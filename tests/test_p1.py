from __future__ import annotations

import json
import base64
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from PIL import Image
import mediaforge_p1.replicate as replicate_module

from mediaforge_p1.comfyui import (
    ComfyUIProvider,
    ComfyUIProviderError,
    ComfyWorkflowDefinition,
)
from mediaforge_p1.comfyui_preflight import preflight_comfyui_registry
from mediaforge_p1.delivery import DeliveryDispatchError, DeliveryDispatcher
from mediaforge_p1.enterprise_runtime import (
    BillingLedger,
    ObjectStorage,
    SlidingWindowRateLimiter,
)
from mediaforge_p1.quality import QualityEvaluator
from mediaforge_p1.config import (
    build_provider_bundles_from_env,
    build_provider_from_env,
)
from mediaforge_p1.llm import OpenAICompatibleStoryPlanner
from mediaforge_p1.media import create_placeholder_audio, create_placeholder_video
from mediaforge_p1.replicate import ReplicateProviderError, ReplicateVideoProvider
from mediaforge_p1.contracts import (
    Artifact,
    Capability,
    CreativeBrief,
    GenerationSpec,
    Intent,
    JobStatus,
    ProviderConstraints,
    QualityRequirements,
    ReferenceAssetRef,
    WorkflowSpec,
    ControlNet,
)
from mediaforge_p1.jobs import InvalidTransition, JobLeasePolicy, JobStore, RetryPolicy
from mediaforge_p1.media import (
    SubtitleCue,
    find_ffmpeg,
    mix_audio,
    probe_audio,
    probe_image,
    probe_video,
    sha256_file,
    write_srt,
)
from mediaforge_p1.providers import MockProvider
from mediaforge_p1.providers import LocalCommandProvider
from mediaforge_p1.lipsync import LipSyncAdapter, LipSyncSettings, LipSyncError
from mediaforge_p1.service import MediaForgeService
from mediaforge_p1.router import (
    ProviderRegistration,
    ProviderRouter,
    ProviderRoutingError,
)
from mediaforge_p1.vertical_slice import run_vertical_slice


def test_enterprise_runtime_adapters_are_durable_and_idempotent(tmp_path: Path) -> None:
    limiter_path = tmp_path / "rate-limit.sqlite3"
    first = SlidingWindowRateLimiter(limiter_path, limit=2, window_seconds=60)
    second = SlidingWindowRateLimiter(limiter_path, limit=2, window_seconds=60)
    assert first.allow("tenant_a", now=100)[0] is True
    assert second.allow("tenant_a", now=101)[0] is True
    assert first.allow("tenant_a", now=102)[0] is False

    ledger = BillingLedger(tmp_path / "billing.sqlite3")
    event = ledger.record(
        event_id="generation_001",
        tenant_id="tenant_a",
        category="video_seconds",
        quantity=5,
        unit_price=0.02,
    )
    duplicate = ledger.record(
        event_id="generation_001",
        tenant_id="tenant_a",
        category="video_seconds",
        quantity=999,
        unit_price=999,
    )
    assert duplicate["amount"] == event["amount"] == 0.1
    assert ledger.summary("tenant_a")["event_count"] == 1

    source = tmp_path / "source.bin"
    source.write_bytes(b"enterprise-artifact")
    stored = ObjectStorage(tmp_path / "objects").put(source, "tenant_a/project_001/file.bin")
    assert (tmp_path / "objects" / "objects" / "tenant_a" / "project_001" / "file.bin").is_file()
    with pytest.raises(ValueError):
        ObjectStorage(tmp_path / "objects").put(source, "../escape.bin")


def test_rate_limiter_rejects_invalid_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="between"):
        SlidingWindowRateLimiter(tmp_path / "rate-limit.sqlite3", limit=0)
    with pytest.raises(ValueError, match="between"):
        SlidingWindowRateLimiter(tmp_path / "rate-limit.sqlite3", window_seconds=0)


def test_sqlite_rate_limit_is_atomic_under_concurrency(tmp_path: Path) -> None:
    limiter = SlidingWindowRateLimiter(tmp_path / "rate-limit.sqlite3", limit=3, window_seconds=60)

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: limiter.allow("tenant_a", now=100), range(12)))

    assert sum(allowed for allowed, _ in results) == 3
    assert all(remaining == 0 for allowed, remaining in results if not allowed)


def test_rate_limit_decision_exposes_the_earliest_recovery_time(tmp_path: Path) -> None:
    limiter = SlidingWindowRateLimiter(
        tmp_path / "rate-limit.sqlite3",
        limit=2,
        window_seconds=10,
    )

    first = limiter.check("tenant_a", now=100)
    second = limiter.check("tenant_a", now=101)
    rejected = limiter.check("tenant_a", now=102)
    almost_ready = limiter.check("tenant_a", now=109.25)
    recovered = limiter.check("tenant_a", now=110)

    assert first.allowed is True
    assert first.remaining == 1
    assert first.reset_at_epoch == 110
    assert second.allowed is True
    assert second.remaining == 0
    assert rejected.allowed is False
    assert rejected.remaining == 0
    assert rejected.retry_after_seconds == 8
    assert rejected.reset_at_epoch == 110
    assert almost_ready.retry_after_seconds == 1
    assert recovered.allowed is True
    assert recovered.remaining == 0


def make_spec(
    shot_id: str = "shot_001",
    capability: Capability = Capability.IMAGE_TO_VIDEO,
) -> GenerationSpec:
    return GenerationSpec(
        project_id="project_001",
        shot_id=shot_id,
        asset_versions={"character_a": "character_a:v1"},
        intent=Intent(
            shot_type="medium",
            camera_motion="static",
            duration_seconds=1,
            mood="tense",
        ),
        provider_constraints=ProviderConstraints(
            capability=capability,
            max_cost=0.4,
            deadline_seconds=180,
        ),
        workflow=WorkflowSpec(
            template_id="template:v1",
            controlnet=ControlNet(enabled=True, strength=0.5),
        ),
        quality_requirements=QualityRequirements(
            minimum_character_similarity=0.8,
        ),
    )


def test_generation_spec_rejects_out_of_range_controlnet() -> None:
    with pytest.raises(ValueError):
        make_spec().model_copy(
            update={
                "workflow": WorkflowSpec(
                    template_id="template:v1",
                    controlnet=ControlNet(enabled=True, strength=1.5),
                )
            }
        )


def test_job_creation_is_idempotent_and_transitions_are_guarded() -> None:
    store = JobStore()
    spec = make_spec()
    first = store.create(spec, "same-request")
    second = store.create(spec, "same-request")

    assert first.job_id == second.job_id
    assert first.status == JobStatus.CREATED

    store.transition(first.job_id, JobStatus.VALIDATED)
    store.transition(first.job_id, JobStatus.QUEUED)
    with pytest.raises(InvalidTransition):
        store.transition(first.job_id, JobStatus.SUCCEEDED)


def test_failed_job_can_return_to_queue_for_retry() -> None:
    store = JobStore()
    job = store.create(make_spec(), "retry-request")

    for status in (
        JobStatus.VALIDATED,
        JobStatus.QUEUED,
        JobStatus.ADMITTED,
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.RETRY_WAIT,
        JobStatus.QUEUED,
    ):
        store.transition(job.job_id, status)

    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1


def test_retry_policy_uses_bounded_exponential_backoff(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_RETRY_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("MEDIAFORGE_RETRY_BASE_DELAY_SECONDS", "2")

    policy = RetryPolicy.from_env()

    assert policy.max_attempts == 4
    assert policy.delay_for_attempt(1) == 2
    assert policy.delay_for_attempt(2) == 4
    assert policy.delay_for_attempt(4) == 16


def test_scheduled_retry_persists_and_promotes_when_due() -> None:
    store = JobStore(max_attempts=3)
    job = store.create(make_spec(), "scheduled-retry")
    for status in (
        JobStatus.VALIDATED,
        JobStatus.QUEUED,
        JobStatus.ADMITTED,
        JobStatus.RUNNING,
        JobStatus.FAILED,
    ):
        store.transition(job.job_id, status, reason="provider outage")

    retry_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    store.schedule_retry(
        job.job_id,
        retry_at=retry_at,
        reason="provider outage",
    )

    assert job.status == JobStatus.RETRY_WAIT
    assert job.retry_at == retry_at
    assert job.last_error == "provider outage"
    assert store.due_retries(now=retry_at) == [job]

    store.promote_retry(job.job_id, reason="retry backoff elapsed")

    assert job.status == JobStatus.QUEUED
    assert job.retry_at is None


def test_job_lease_policy_reads_and_validates_environment(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_JOB_LEASE_SECONDS", "12.5")
    monkeypatch.setenv("MEDIAFORGE_WORKER_REGISTRY_RETENTION_SECONDS", "3600")
    monkeypatch.setenv("MEDIAFORGE_WORKER_REGISTRY_MAX_PER_TENANT", "7")

    policy = JobLeasePolicy.from_env()

    assert policy.stale_after_seconds == 12.5
    assert policy.worker_registry_retention_seconds == 3600
    assert policy.worker_registry_max_per_tenant == 7
    with pytest.raises(ValueError):
        JobLeasePolicy(stale_after_seconds=0)
    with pytest.raises(ValueError, match="retention"):
        JobLeasePolicy(stale_after_seconds=900, worker_registry_retention_seconds=900)


def test_mock_provider_creates_a_probeable_video(tmp_path: Path) -> None:
    provider = MockProvider()
    artifact = provider.generate(
        make_spec(),
        job_id="job_test",
        output_dir=tmp_path,
    )

    result = probe_video(Path(artifact.uri))
    assert artifact.kind == "video"
    assert artifact.sha256
    assert artifact.metadata_uri
    metadata_path = Path(artifact.metadata_uri)
    assert metadata_path.is_file()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "mediaforge-artifact-metadata-v1"
    assert metadata["provider"] == "mock-provider"
    assert metadata["job_id"] == "job_test"
    assert metadata["simulation"]["visible_preview"] is True
    assert result.valid is True
    assert result.width == 640
    assert result.height == 360
    ffmpeg, _ = find_ffmpeg()
    frame_path = tmp_path / "mock-preview-frame.png"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.2", "-i", artifact.uri, "-frames:v", "1", str(frame_path)],
        check=True,
    )
    with Image.open(frame_path) as frame:
        colors = frame.convert("RGB").getcolors(maxcolors=640 * 360)
    assert colors is not None and len(colors) > 8


def test_mock_provider_creates_a_visible_preview_image(tmp_path: Path) -> None:
    artifact = MockProvider().generate(
        make_spec(capability=Capability.IMAGE_GENERATION),
        job_id="job_image",
        output_dir=tmp_path,
    )

    assert artifact.kind == "image"
    assert probe_image(Path(artifact.uri)).valid is True
    with Image.open(artifact.uri) as image:
        colors = image.convert("RGB").getcolors(maxcolors=640 * 360)
    assert colors is not None and len(colors) > 8
    metadata = json.loads(Path(artifact.metadata_uri).read_text(encoding="utf-8"))
    assert metadata["simulation"]["mode"] == "mock"


def test_probe_image_and_image_quality_report(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (640, 360), "#1d3557").save(image_path, format="PNG")

    result = probe_image(image_path)
    quality = MediaForgeService._quality_report(
        result,
        make_spec(capability=Capability.IMAGE_GENERATION),
        media_kind="image",
    )

    assert result.valid is True
    assert result.width == 640
    assert result.height == 360
    assert result.format == "PNG"
    assert result.duration_seconds is None
    assert quality["media_kind"] == "image"
    assert quality["passed"] is True
    assert {check["name"] for check in quality["checks"]} == {
        "decode",
        "format",
        "resolution",
    }


def test_quality_evaluator_requires_explicit_data_export_opt_in(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.mp4"
    artifact.write_bytes(b"local-artifact")
    evaluator = QualityEvaluator(
        url="https://quality.example.invalid/evaluate",
        allow_data_export=False,
    )
    result = evaluator.evaluate(
        artifact,
        artifact_id="artifact_001",
        media_kind="video",
        local_quality={"passed": True},
        spec=make_spec(),
    )
    assert result.configured is True
    assert result.passed is None
    assert "data export is disabled" in (result.error or "")


def test_quality_evaluator_posts_and_merges_external_decision(tmp_path: Path) -> None:
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            received.append(json.loads(self.rfile.read(size)))
            body = json.dumps(
                {
                    "provider": "quality-gateway",
                    "passed": True,
                    "score": 0.94,
                    "checks": [
                        {
                            "name": "semantic_consistency",
                            "passed": True,
                            "observed": 0.94,
                            "expected": ">=0.80",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        artifact = tmp_path / "artifact.mp4"
        create_placeholder_video(artifact, duration_seconds=1, color="blue")
        evaluator = QualityEvaluator(
            url=f"http://127.0.0.1:{server.server_port}/evaluate",
            allow_data_export=True,
            retries=0,
        )
        result = evaluator.evaluate(
            artifact,
            artifact_id="artifact_002",
            media_kind="video",
            local_quality={"passed": True},
            spec=make_spec(),
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.passed is True
    assert result.score == 0.94
    assert received[0]["artifact_id"] == "artifact_002"
    assert received[0]["artifact"]["sha256"]
    assert "path" not in received[0]["artifact"]
    assert len(received[0]["frames"]) == 3
    assert all(frame["data_b64"] for frame in received[0]["frames"])


def test_http_delivery_dispatch_sends_signed_package(tmp_path: Path) -> None:
    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            received.append(
                {
                    "body": self.rfile.read(size),
                    "signature": self.headers.get("X-MediaForge-Signature"),
                    "sha256": self.headers.get("X-MediaForge-Package-SHA256"),
                }
            )
            response = b'{"accepted":true}'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        package = tmp_path / "delivery.zip"
        package.write_bytes(b"http-package")
        result = DeliveryDispatcher(
            tmp_path / "artifacts",
            mode="http",
            secret="delivery-secret",
            retries=0,
        ).dispatch(
            package,
            project_id="http_project",
            release_id="release_002",
            channel="remote",
            recipient="ops",
            destination_uri=f"http://127.0.0.1:{server.server_port}/upload",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.status == "DISPATCHED"
    assert result.response_status == 202
    assert received[0]["body"] == b"http-package"
    assert received[0]["signature"]
    assert received[0]["sha256"] == result.package_sha256


def test_http_delivery_does_not_follow_redirects(tmp_path: Path) -> None:
    requested_paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            requested_paths.append(self.path)
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{server.server_port}/redirected")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        package = tmp_path / "delivery.zip"
        package.write_bytes(b"redirect-package")
        with pytest.raises(DeliveryDispatchError, match="HTTP delivery failed"):
            DeliveryDispatcher(
                tmp_path / "artifacts",
                mode="http",
                secret="delivery-secret",
                retries=0,
            ).dispatch(
                package,
                project_id="redirect_project",
                release_id="release_003",
                channel="remote",
                recipient="ops",
                destination_uri=f"http://127.0.0.1:{server.server_port}/upload",
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requested_paths == ["/upload"]


def test_delivery_targets_are_bounded_by_explicit_policy(tmp_path: Path) -> None:
    package = tmp_path / "delivery.zip"
    package.write_bytes(b"bounded-package")
    dispatcher = DeliveryDispatcher(
        tmp_path / "artifacts",
        mode="file",
        file_root=tmp_path / "allowed",
    )
    dispatcher.dispatch(
        package,
        project_id="bounded",
        release_id="release",
        channel="archive",
        recipient="qa",
        destination_uri=(tmp_path / "allowed" / "out.zip").resolve().as_uri(),
    )
    assert (tmp_path / "allowed" / "out.zip").is_file()
    with pytest.raises(DeliveryDispatchError, match="inside"):
        dispatcher.dispatch(
            package,
            project_id="bounded",
            release_id="release",
            channel="archive",
            recipient="qa",
            destination_uri=(tmp_path / "outside.zip").resolve().as_uri(),
        )


def test_service_prefers_provider_supported_capability(tmp_path: Path) -> None:
    class ImageOnlyProvider:
        name = "image-only-provider"

        def supports(self, capability: Capability) -> bool:
            return capability == Capability.IMAGE_GENERATION

        def estimate_cost(self, spec: GenerationSpec) -> float:
            return 0.01

        def generate(self, spec: GenerationSpec, *, job_id: str, output_dir: Path):
            raise NotImplementedError

    service = MediaForgeService(tmp_path, provider=ImageOnlyProvider())

    assert service._preferred_capability() == Capability.IMAGE_GENERATION


def test_openai_compatible_story_planner_posts_and_parses_structured_plan() -> None:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer planner-token"
            length = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(length)))
            if len(requests) == 1:
                body = b'{"error":"temporary planner outage"}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            payload = {
                "choices": [{
                    "message": {
                        "content": "```json\n"
                        + json.dumps({
                            "story_bible": {"theme": "未来来电"},
                            "shots": [
                                {
                                    "shot_id": f"ep01_sc01_{index:02d}",
                                    "scene": "公寓客厅",
                                    "description": "林夏接起电话",
                                    "characters": ["林夏"],
                                    "duration_seconds": 5,
                                    "mood": "紧张",
                                    "subtitle_text": "喂？",
                                }
                                for index in range(1, 7)
                            ],
                        }, ensure_ascii=False)
                        + "\n```",
                    }
                }],
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        planner = OpenAICompatibleStoryPlanner(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="planner-token",
            model="test-model",
            timeout_seconds=2,
        )
        plan = planner.plan(CreativeBrief(
            project_id="llm_contract",
            title="午夜来电",
            premise="女主接到未来的电话",
            genre="悬疑",
            style="蓝调",
            duration_seconds=30,
            budget=2,
            characters=["林夏", "周启"],
        ))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests[0]["model"] == "test-model"
    assert len(requests) == 2
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert plan.story_bible["theme"] == "未来来电"
    assert len(plan.shots) == 6
    assert plan.shots[0].project_id == "llm_contract"


def test_comfyui_provider_submits_polls_and_downloads_output(tmp_path: Path) -> None:
    png_path = tmp_path / "server-output.png"
    Image.new("RGB", (8, 8), "#e76f51").save(png_path, format="PNG")
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            assert self.path == "/prompt"
            length = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(length)))
            body = json.dumps({"prompt_id": "prompt-test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/history/prompt-test":
                body = json.dumps(
                    {
                        "prompt-test": {
                            "status": {"completed": True, "status_str": "success"},
                            "outputs": {
                                "9": {
                                    "images": [
                                        {
                                            "filename": "result.png",
                                            "subfolder": "",
                                            "type": "output",
                                        }
                                    ]
                                }
                            },
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            parsed = urlparse(self.path)
            if parsed.path == "/view":
                assert parse_qs(parsed.query)["filename"] == ["result.png"]
                content = png_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ComfyUIProvider(
            base_url=f"http://127.0.0.1:{server.server_port}",
            workflow={
                "prompt": {
                    "1": {
                        "class_type": "TestNode",
                        "inputs": {"value": "original"},
                    }
                },
                "bindings": {
                    "shot_id": {
                        "node_id": "1",
                        "input": "value",
                        "source": "shot_id",
                    }
                },
            },
            poll_interval_seconds=0.01,
        )
        artifact = provider.generate(
            make_spec(capability=Capability.IMAGE_GENERATION),
            job_id="job_comfyui",
            output_dir=tmp_path / "downloaded",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests[0]["prompt"]["1"]["inputs"]["value"] == "shot_001"
    assert artifact.kind == "image"
    assert artifact.metadata_uri
    comfy_metadata = json.loads(
        Path(artifact.metadata_uri).read_text(encoding="utf-8")
    )
    assert comfy_metadata["schema_version"] == "mediaforge-artifact-metadata-v1"
    assert comfy_metadata["prompt_id"] == "prompt-test"
    assert [item["state"] for item in comfy_metadata["provider_execution"]["timeline"]] == [
        "QUEUED",
        "SUCCEEDED",
    ]
    assert comfy_metadata["workflow"]["requested_template_id"] == "template:v1"
    assert Path(artifact.uri).read_bytes() == png_path.read_bytes()


def test_comfyui_provider_marks_reviewed_video_output_as_video(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider = ComfyUIProvider(
        base_url="http://127.0.0.1:8188",
        workflow={"1": {"class_type": "TestNode", "inputs": {}}},
        capabilities={Capability.IMAGE_TO_VIDEO},
    )
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *_args, **_kwargs: {"prompt_id": "video-prompt"},
    )
    monkeypatch.setattr(
        provider,
        "_poll_history",
        lambda *_args, **_kwargs: (
            {
                "outputs": {
                    "7": {
                        "videos": [
                            {
                                "filename": "reviewed-shot.mp4",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                }
            },
            [{"state": "SUCCEEDED"}],
        ),
    )
    monkeypatch.setattr(
        provider,
        "_request_bytes",
        lambda *_args, **_kwargs: b"video-bytes",
    )

    artifact = provider.generate(
        make_spec(capability=Capability.IMAGE_TO_VIDEO),
        job_id="job_comfyui_video",
        output_dir=tmp_path / "downloaded",
    )

    assert artifact.kind == "video"
    assert artifact.mime_type == "video/mp4"
    assert Path(artifact.uri).read_bytes() == b"video-bytes"


def test_comfyui_provider_rejects_a_template_for_the_wrong_capability(
    tmp_path: Path,
) -> None:
    provider = ComfyUIProvider(
        base_url="http://127.0.0.1:8188",
        workflow={},
        workflows={
            "comfyui_video:reviewed:v1": ComfyWorkflowDefinition(
                template_id="comfyui_video:reviewed:v1",
                workflow={"1": {"class_type": "TestNode", "inputs": {}}},
                capabilities=(Capability.IMAGE_TO_VIDEO,),
            )
        },
        capabilities={Capability.IMAGE_GENERATION, Capability.IMAGE_TO_VIDEO},
    )
    image_spec = make_spec(capability=Capability.IMAGE_GENERATION).model_copy(
        update={
            "workflow": WorkflowSpec(
                template_id="comfyui_video:reviewed:v1",
                controlnet=ControlNet(enabled=False, strength=0),
            )
        }
    )

    with pytest.raises(ComfyUIProviderError, match="does not declare image_generation"):
        provider.generate(
            image_spec,
            job_id="job_wrong_comfyui_capability",
            output_dir=tmp_path / "downloaded",
        )


def test_comfyui_provider_uploads_local_reference_before_prompt(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "#264653").save(reference_path, format="PNG")
    output_path = tmp_path / "server-output.png"
    Image.new("RGB", (8, 8), "#e76f51").save(output_path, format="PNG")
    uploaded: list[bytes] = []
    prompts: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            if self.path == "/upload/image":
                assert self.headers["Content-Type"].startswith(
                    "multipart/form-data; boundary="
                )
                uploaded.append(body)
                response = json.dumps(
                    {"name": "reference.png", "subfolder": "", "type": "input"}
                ).encode()
            else:
                assert self.path == "/prompt"
                prompts.append(json.loads(body))
                response = json.dumps({"prompt_id": "prompt-reference"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_GET(self) -> None:
            if self.path == "/history/prompt-reference":
                response = json.dumps(
                    {
                        "prompt-reference": {
                            "status": {"completed": True, "status_str": "success"},
                            "outputs": {
                                "9": {
                                    "images": [
                                        {
                                            "filename": "result.png",
                                            "subfolder": "",
                                            "type": "output",
                                        }
                                    ]
                                }
                            },
                        }
                    }
                ).encode()
            elif self.path == "/view?filename=result.png&subfolder=&type=output":
                response = output_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ComfyUIProvider(
            base_url=f"http://127.0.0.1:{server.server_port}",
            workflow={
                "prompt": {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "placeholder.png"},
                    }
                },
                "bindings": {
                    "reference_image_uri": {
                        "node_id": "1",
                        "input": "image",
                        "source": "reference_image_uri",
                    }
                },
            },
            poll_interval_seconds=0.01,
        )
        artifact = provider.generate(
            make_spec(capability=Capability.IMAGE_GENERATION).model_copy(
                update={
                    "reference_assets": [
                        ReferenceAssetRef(
                            asset_id="project_001:reference:one",
                            name="reference",
                            version="v1",
                            kind="character_reference",
                            uri=str(reference_path),
                            mime_type="image/png",
                            license="project-owned",
                            source="test",
                        )
                    ]
                }
            ),
            job_id="job_comfyui_reference",
            output_dir=tmp_path / "downloaded",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert uploaded
    assert reference_path.name.encode() in uploaded[0]
    assert prompts[0]["prompt"]["1"]["inputs"]["image"] == "reference.png"
    metadata = json.loads(Path(artifact.metadata_uri).read_text(encoding="utf-8"))
    assert metadata["reference_upload"] == {
        "source_uri": str(reference_path),
        "provider_file": "reference.png",
        "uploaded": True,
        "endpoint": "/upload/image",
        "subfolder": "",
        "type": "input",
    }


def test_comfyui_provider_can_be_built_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "prompt": {
                    "1": {
                        "class_type": "TestNode",
                        "inputs": {"text": "placeholder"},
                    }
                },
                "bindings": {
                    "shot_id": {
                        "node_id": "1",
                        "input": "text",
                        "source": "shot_id",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:9123/")
    monkeypatch.setenv("COMFYUI_WORKFLOW_PATH", str(workflow_path))
    monkeypatch.setenv("COMFYUI_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("COMFYUI_POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("COMFYUI_ESTIMATED_COST", "0.07")

    bundle = build_provider_from_env()

    assert bundle.configured is True
    assert bundle.provider.name == "comfyui"
    assert isinstance(bundle.provider, ComfyUIProvider)
    assert bundle.capabilities == ["image_generation"]
    assert bundle.details["base_url"] == "http://127.0.0.1:9123"
    assert bundle.details["workflow_path"] == str(workflow_path)
    assert bundle.details["workflow_loaded"] is True
    assert bundle.details["timeout_seconds"] == 12.0
    assert bundle.details["poll_interval_seconds"] == 0.05
    assert bundle.details["estimated_cost"] == 0.07
    assert bundle.details["workflow_pin_required"] is False
    assert bundle.details["workflow_registry_path"] is None
    assert bundle.details["workflow"]["registry_template_id"] == "legacy-default"
    assert len(bundle.details["workflow"]["sha256"]) == 64
    assert bundle.status_view()["details"]["workflow_loaded"] is True


def test_comfyui_workflow_registry_selects_pinned_template_and_models(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow_path = tmp_path / "cinematic.json"
    workflow_path.write_text(
        json.dumps({"1": {"class_type": "TestNode", "inputs": {"text": "cinematic"}}}),
        encoding="utf-8",
    )
    digest = sha256_file(workflow_path)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "template:cinematic:v2",
                        "path": workflow_path.name,
                        "version": "2026.09.15",
                        "sha256": digest,
                        "model_requirements": [
                            {"folder": "checkpoints", "name": "cinematic-v2.safetensors"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("COMFYUI_WORKFLOW_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("COMFYUI_REQUIRE_WORKFLOW_PIN", "true")

    bundle = build_provider_from_env()

    assert bundle.configured is True
    provider = bundle.provider
    assert isinstance(provider, ComfyUIProvider)
    definition = provider._workflow_for(
        make_spec(capability=Capability.IMAGE_GENERATION).model_copy(
                update={
                    "workflow": WorkflowSpec(
                        template_id="template:cinematic:v2",
                        controlnet=ControlNet(enabled=True, strength=0.5),
                    )
                }
        )
    )
    assert definition.version == "2026.09.15"
    assert definition.sha256 == digest
    assert definition.model_requirements == (
        {"folder": "checkpoints", "name": "cinematic-v2.safetensors"},
    )
    assert bundle.details["workflow_registry"]["workflow_count"] == 1
    assert bundle.details["default_template_id"] == "template:cinematic:v2"


def test_comfyui_registry_requires_an_explicit_default_when_multiple(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_workflow = tmp_path / "first.json"
    second_workflow = tmp_path / "second.json"
    first_workflow.write_text(
        json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
        encoding="utf-8",
    )
    second_workflow.write_text(
        json.dumps({"2": {"class_type": "TestNode", "inputs": {}}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "comfyui_image:portrait:v1",
                        "path": first_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(first_workflow),
                    },
                    {
                        "template_id": "comfyui_image:wide:v1",
                        "path": second_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(second_workflow),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("COMFYUI_WORKFLOW_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("COMFYUI_REQUIRE_WORKFLOW_PIN", "true")
    monkeypatch.delenv("MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID", raising=False)

    missing_default = build_provider_from_env()

    assert missing_default.configured is False
    assert "MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID" in missing_default.message

    monkeypatch.setenv(
        "MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID",
        "comfyui_image:wide:v1",
    )
    selected = build_provider_from_env()

    assert selected.configured is True
    assert selected.details["default_template_id"] == "comfyui_image:wide:v1"


def test_comfyui_registry_selects_reviewed_video_template_separately(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image_workflow = tmp_path / "image.json"
    video_workflow = tmp_path / "video.json"
    for path in (image_workflow, video_workflow):
        path.write_text(
            json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
            encoding="utf-8",
        )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "comfyui_image:reviewed:v1",
                        "path": image_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(image_workflow),
                        "capabilities": ["image_generation"],
                    },
                    {
                        "template_id": "comfyui_video:reviewed:v1",
                        "path": video_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(video_workflow),
                        "capabilities": ["image_to_video"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("COMFYUI_WORKFLOW_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("COMFYUI_REQUIRE_WORKFLOW_PIN", "true")

    bundle = build_provider_from_env()
    report = preflight_comfyui_registry(registry_path)

    assert bundle.configured is True
    assert bundle.capabilities == ["image_generation", "image_to_video"]
    assert bundle.details["default_template_id"] == "comfyui_image:reviewed:v1"
    assert bundle.details["default_video_template_id"] == "comfyui_video:reviewed:v1"
    assert bundle.provider.supports(Capability.IMAGE_TO_VIDEO) is True
    assert report["default_template_id"] == "comfyui_image:reviewed:v1"
    assert report["default_video_template_id"] == "comfyui_video:reviewed:v1"
    assert report["capabilities"] == ["image_generation", "image_to_video"]


def test_comfyui_preflight_reports_pinned_registry_without_provider_call(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "reviewed.json"
    workflow_path.write_text(
        json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "comfyui_image:reviewed:v1",
                        "path": workflow_path.name,
                        "version": "2026.09.17",
                        "sha256": sha256_file(workflow_path),
                        "model_requirements": [
                            {"folder": "checkpoints", "name": "reviewed.safetensors"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = preflight_comfyui_registry(registry_path)

    assert report["require_pin"] is True
    assert report["default_template_id"] == "comfyui_image:reviewed:v1"
    assert report["workflow_count"] == 1
    assert report["workflows"] == [
        {
            "template_id": "comfyui_image:reviewed:v1",
            "version": "2026.09.17",
            "sha256": sha256_file(workflow_path),
            "source_file": "reviewed.json",
            "model_requirements": [
                {"folder": "checkpoints", "name": "reviewed.safetensors"}
            ],
            "capabilities": ["image_generation"],
        }
    ]


def test_comfyui_registry_rejects_workflow_outside_reviewed_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    reviewed_dir = tmp_path / "reviewed"
    reviewed_dir.mkdir()
    external_workflow = tmp_path / "outside.json"
    external_workflow.write_text(
        json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
        encoding="utf-8",
    )
    registry_path = reviewed_dir / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "comfyui_image:outside:v1",
                        "path": "../outside.json",
                        "version": "1",
                        "sha256": sha256_file(external_workflow),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("COMFYUI_WORKFLOW_REGISTRY_PATH", str(registry_path))

    bundle = build_provider_from_env()

    assert bundle.configured is False
    assert "must resolve within the registry directory" in bundle.message


def test_comfyui_preflight_requires_a_default_for_multiple_workflows(
    tmp_path: Path,
) -> None:
    first_workflow = tmp_path / "first.json"
    second_workflow = tmp_path / "second.json"
    for path in (first_workflow, second_workflow):
        path.write_text(
            json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
            encoding="utf-8",
        )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "comfyui_image:first:v1",
                        "path": first_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(first_workflow),
                    },
                    {
                        "template_id": "comfyui_image:second:v1",
                        "path": second_workflow.name,
                        "version": "1",
                        "sha256": sha256_file(second_workflow),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID"):
        preflight_comfyui_registry(registry_path)

    report = preflight_comfyui_registry(
        registry_path,
        default_template_id="comfyui_image:second:v1",
    )
    assert report["default_template_id"] == "comfyui_image:second:v1"


def test_comfyui_workflow_registry_rejects_hash_drift(tmp_path: Path, monkeypatch) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps({"1": {"class_type": "TestNode", "inputs": {}}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                "workflows": [
                    {
                        "template_id": "template:drift:v1",
                        "path": workflow_path.name,
                        "version": "1",
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("COMFYUI_WORKFLOW_REGISTRY_PATH", str(registry_path))

    bundle = build_provider_from_env()

    assert bundle.configured is False
    assert "SHA-256 mismatch" in bundle.message


def test_comfyui_configuration_reports_missing_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow_path = tmp_path / "missing-workflow.json"
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.setenv("COMFYUI_WORKFLOW_PATH", str(workflow_path))

    bundle = build_provider_from_env()

    assert bundle.configured is False
    assert bundle.provider.name == "comfyui"
    assert bundle.capabilities == ["image_generation"]
    assert "workflow file not found" in bundle.message
    assert bundle.details["workflow_loaded"] is False


def test_comfyui_configuration_rejects_invalid_runtime_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "1": {
                    "class_type": "TestNode",
                    "inputs": {"text": "placeholder"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "comfyui")
    monkeypatch.setenv("COMFYUI_WORKFLOW_PATH", str(workflow_path))
    monkeypatch.setenv("COMFYUI_TIMEOUT_SECONDS", "0")

    bundle = build_provider_from_env()

    assert bundle.configured is False
    assert "COMFYUI_TIMEOUT_SECONDS must be > 0" in bundle.message


def test_multiple_provider_configuration_preserves_priority_and_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "1": {
                    "class_type": "TestNode",
                    "inputs": {"text": "placeholder"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAFORGE_PROVIDERS", "mock,comfyui,mock")
    monkeypatch.setenv("COMFYUI_WORKFLOW_PATH", str(workflow_path))

    bundles = build_provider_bundles_from_env()

    assert [bundle.provider.name for bundle in bundles] == [
        "mock-provider",
        "comfyui",
    ]
    assert all(bundle.configured for bundle in bundles)
    assert bundles[0].capabilities == ["image_generation", "image_to_video"]
    assert bundles[1].capabilities == ["image_generation"]


def test_mock_provider_health_check_is_ready() -> None:
    health = MockProvider().health_check()

    assert health == {
        "reachable": True,
        "message": "Mock Provider is ready.",
        "latency_ms": 0.0,
        "details": {"execution": "local-deterministic"},
    }


def test_write_srt_formats_timeline_and_multiline_text(tmp_path: Path) -> None:
    path = write_srt(
        tmp_path / "captions.srt",
        [
            SubtitleCue(0, 5, "第一句\n第二行"),
            SubtitleCue(5, 10.125, "下一句"),
        ],
    )

    assert path.read_text(encoding="utf-8") == (
        "1\n"
        "00:00:00,000 --> 00:00:05,000\n"
        "第一句\n"
        "第二行\n"
        "\n"
        "2\n"
        "00:00:05,000 --> 00:00:10,125\n"
        "下一句\n"
        ""
    )

    with pytest.raises(ValueError, match="at least one subtitle cue"):
        write_srt(tmp_path / "empty.srt", [])


def test_comfyui_provider_health_checks_system_stats(monkeypatch) -> None:
    provider = ComfyUIProvider(
        base_url="http://127.0.0.1:8188",
        workflow={"1": {"class_type": "TestNode", "inputs": {}}},
    )
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **_kwargs) -> object:
        calls.append((method, path))
        return {"devices": [{"name": "cpu"}], "queue_running": []}

    monkeypatch.setattr(provider, "_request_json", fake_request)

    health = provider.health_check()

    assert calls == [("GET", "/system_stats")]
    assert health["reachable"] is True
    assert health["details"]["endpoint"] == "/system_stats"
    assert health["details"]["device_count"] == 1


def test_comfyui_provider_health_fails_when_reviewed_model_is_missing(monkeypatch) -> None:
    provider = ComfyUIProvider(
        base_url="http://127.0.0.1:8188",
        workflow={},
        workflows={
            "template:cinematic:v1": ComfyWorkflowDefinition(
                template_id="template:cinematic:v1",
                workflow={"1": {"class_type": "TestNode", "inputs": {}}},
                model_requirements=(
                    {"folder": "checkpoints", "name": "missing.safetensors"},
                ),
            )
        },
    )
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **_kwargs) -> object:
        calls.append((method, path))
        if path == "/system_stats":
            return {"devices": [{"name": "gpu"}]}
        assert path == "/models/checkpoints"
        return ["available.safetensors"]

    monkeypatch.setattr(provider, "_request_json", fake_request)

    health = provider.health_check()

    assert calls == [("GET", "/system_stats"), ("GET", "/models/checkpoints")]
    assert health["reachable"] is True
    assert health["healthy"] is False
    assert health["details"]["model_validation"]["missing"] == [
        {"folder": "checkpoints", "name": "missing.safetensors", "available": False}
    ]


def test_replicate_provider_health_checks_models(monkeypatch) -> None:
    provider = ReplicateVideoProvider(api_token="token", version="version")
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, target: str) -> dict:
        calls.append((method, target))
        return {"results": []}

    monkeypatch.setattr(provider, "_request_json", fake_request)

    health = provider.health_check()

    assert calls == [("GET", "/models?limit=1")]
    assert health["reachable"] is True
    assert health["details"]["endpoint"] == "/models?limit=1"


def test_local_provider_reports_missing_command(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "local")
    monkeypatch.delenv("MEDIAFORGE_LOCAL_PROVIDER_COMMAND", raising=False)

    bundle = build_provider_from_env()

    assert bundle.configured is False
    assert bundle.provider.name == "local-gpu-command"
    assert "MEDIAFORGE_LOCAL_PROVIDER_COMMAND" in bundle.message


def test_local_provider_generates_contract_artifact(tmp_path: Path, monkeypatch) -> None:
    provider = LocalCommandProvider(
        command="python worker.py",
        capabilities={Capability.IMAGE_GENERATION},
        timeout_seconds=5,
    )

    def fake_run(*_args, **kwargs):
        output = Path(kwargs["env"]["MEDIAFORGE_OUTPUT_PATH"])
        Image.new("RGB", (32, 18), "#345").save(output, format="PNG")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mediaforge_p1.providers.subprocess.run", fake_run)
    spec = make_spec("shot_01", Capability.IMAGE_GENERATION)
    spec = spec.model_copy(update={"project_id": "local_project"})

    artifact = provider.generate(spec, job_id="job_01", output_dir=tmp_path)

    assert artifact.kind == "image"
    assert probe_image(Path(artifact.uri)).valid
    assert Path(artifact.metadata_uri).is_file()


def test_http_lipsync_requires_opt_in_and_accepts_valid_output(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "source.mp4"
    audio = tmp_path / "source.wav"
    output = tmp_path / "synced.mp4"
    create_placeholder_video(video, duration_seconds=1, color="#334455")
    create_placeholder_audio(audio, duration_seconds=1)
    response_body = json.dumps({"output_base64": base64.b64encode(video.read_bytes()).decode("ascii")}).encode("utf-8")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return response_body
    monkeypatch.setattr("mediaforge_p1.lipsync.urlopen", lambda *_args, **_kwargs: Response())
    adapter = LipSyncAdapter(LipSyncSettings(mode="http", command="", url="http://lipsync.test", timeout_seconds=5, allow_data_export=True))
    result = adapter.render(video, audio, output, metadata={"project_id": "p1"})

    assert result["mode"] == "http"
    assert probe_video(output).valid


def test_http_lipsync_blocks_data_export_without_opt_in(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    audio = tmp_path / "source.wav"
    create_placeholder_video(video, duration_seconds=1, color="#334455")
    create_placeholder_audio(audio, duration_seconds=1)
    adapter = LipSyncAdapter(LipSyncSettings(mode="http", command="", url="http://127.0.0.1:1", timeout_seconds=5, allow_data_export=False))

    with pytest.raises(LipSyncError, match="ALLOW_DATA_EXPORT"):
        adapter.render(video, audio, tmp_path / "synced.mp4", metadata={})


def test_replicate_provider_retries_idempotent_transient_requests() -> None:
    calls: list[dict[str, str | int]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            calls.append(
                {
                    "path": self.path,
                    "idempotency_key": self.headers.get("Idempotency-Key", ""),
                }
            )
            if len(calls) == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ReplicateVideoProvider(
            api_token="token",
            version="version",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            http_retry_attempts=1,
            http_retry_backoff_seconds=0,
        )
        payload = provider._request_json(
            "POST",
            "/retry",
            body={"prompt": "test"},
            idempotency_key="mediaforge-job_retry",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert payload == {"ok": True}
    assert calls == [
        {
            "path": "/v1/retry",
            "idempotency_key": "mediaforge-job_retry",
        },
        {
            "path": "/v1/retry",
            "idempotency_key": "mediaforge-job_retry",
        },
    ]


def test_replicate_provider_rejects_negative_http_retry_settings() -> None:
    with pytest.raises(ValueError, match="http_retry_attempts must be >= 0"):
        ReplicateVideoProvider(
            api_token="token",
            version="version",
            http_retry_attempts=-1,
        )

    with pytest.raises(ValueError, match="http_retry_backoff_seconds must be >= 0"):
        ReplicateVideoProvider(
            api_token="token",
            version="version",
            http_retry_backoff_seconds=-1,
        )

    with pytest.raises(ValueError, match="http_retry_max_delay_seconds must be between"):
        ReplicateVideoProvider(
            api_token="token",
            version="version",
            http_retry_max_delay_seconds=301,
        )

    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        ReplicateVideoProvider(
            api_token="token",
            version="version",
            timeout_seconds=0,
        )

    with pytest.raises(ValueError, match="cancel_after_seconds must be between"):
        ReplicateVideoProvider(
            api_token="token",
            version="version",
            cancel_after_seconds=4,
        )


def test_replicate_provider_submits_polls_and_downloads_video(tmp_path: Path) -> None:
    source_video = tmp_path / "source.mp4"
    create_placeholder_video(
        source_video,
        duration_seconds=1,
        color="#2a9d8f",
    )
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            assert self.path == "/v1/predictions"
            assert self.headers["Authorization"] == "Bearer test-token"
            assert self.headers["Idempotency-Key"] == "mediaforge-job_replicate"
            assert self.headers["Cancel-After"] == "180s"
            length = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(length)))
            body = json.dumps(
                {
                    "id": "prediction-test",
                    "status": "starting",
                    "urls": {
                        "get": (
                            f"http://127.0.0.1:{self.server.server_port}"
                            "/v1/predictions/prediction-test"
                        )
                    },
                }
            ).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/v1/predictions/prediction-test":
                body = json.dumps(
                    {
                        "id": "prediction-test",
                        "status": "succeeded",
                        "output": [
                            f"http://127.0.0.1:{self.server.server_port}/files/result.mp4"
                        ],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/files/result.mp4":
                assert "Authorization" not in self.headers
                content = source_video.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            poll_interval_seconds=0.01,
        )
        artifact = provider.generate(
            make_spec(),
            job_id="job_replicate",
            output_dir=tmp_path / "downloaded",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests[0]["version"] == "model-version:v1"
    assert requests[0]["input"]["duration"] == 1
    assert artifact.kind == "video"
    assert artifact.metadata_uri
    replicate_metadata = json.loads(
        Path(artifact.metadata_uri).read_text(encoding="utf-8")
    )
    assert replicate_metadata["schema_version"] == "mediaforge-artifact-metadata-v1"
    assert replicate_metadata["job_id"] == "job_replicate"
    assert replicate_metadata["request"]["idempotency_key"] == "mediaforge-job_replicate"
    assert replicate_metadata["request"]["cancel_after"] == "180s"
    assert replicate_metadata["request"]["remote_idempotency"] == "provider-header"
    assert "test-token" not in json.dumps(replicate_metadata)
    assert probe_video(Path(artifact.uri)).valid is True


def test_replicate_provider_submits_terminal_webhook_prediction() -> None:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            assert self.path == "/v1/predictions"
            assert self.headers["Authorization"] == "Bearer test-token"
            assert self.headers["Idempotency-Key"] == "mediaforge-job_webhook"
            assert self.headers["Cancel-After"] == "5s"
            length = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(length)))
            body = json.dumps(
                {"id": "prediction-webhook", "status": "starting"}
            ).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            cancel_after_seconds=5,
            webhook_url_template=(
                "https://studio.example.test/providers/replicate/webhook?"
                "project_id={project_id}&job_id={job_id}"
            ),
        )
        submission = provider.submit_webhook_prediction(
            make_spec(),
            job_id="job_webhook",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert submission.prediction_id == "prediction-webhook"
    assert submission.status == "starting"
    assert submission.webhook_url.endswith(
        "project_id=project_001&job_id=job_webhook"
    )
    assert requests == [
        {
            "version": "model-version:v1",
            "input": {"prompt": "tense cinematic shot shot_001", "duration": 1},
            "webhook": submission.webhook_url,
            "webhook_events_filter": ["completed"],
        }
    ]


def test_replicate_provider_cancels_remote_prediction_after_local_timeout(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def _reply(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            calls.append(
                {
                    "path": self.path,
                    "idempotency_key": self.headers.get("Idempotency-Key", ""),
                    "cancel_after": self.headers.get("Cancel-After", ""),
                }
            )
            base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
            if self.path == "/v1/predictions":
                self._reply(
                    201,
                    {
                        "id": "prediction-timeout",
                        "status": "starting",
                        "urls": {
                            "get": f"{base_url}/predictions/prediction-timeout",
                            "cancel": (
                                f"{base_url}/predictions/prediction-timeout/cancel"
                            ),
                        },
                    },
                )
                return
            if self.path == "/v1/predictions/prediction-timeout/cancel":
                self.send_response(204)
                self.end_headers()
                return
            self._reply(404, {"error": "not found"})

        def do_GET(self) -> None:
            if self.path != "/v1/predictions/prediction-timeout":
                self._reply(404, {"error": "not found"})
                return
            base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
            self._reply(
                200,
                {
                    "id": "prediction-timeout",
                    "status": "processing",
                    "urls": {
                        "cancel": f"{base_url}/predictions/prediction-timeout/cancel"
                    },
                },
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            timeout_seconds=0.03,
            poll_interval_seconds=0.001,
            cancel_after_seconds=5,
            http_retry_attempts=0,
        )
        with pytest.raises(ReplicateProviderError, match="remote cancellation requested"):
            provider.generate(
                make_spec(),
                job_id="job_timeout",
                output_dir=tmp_path / "timed-out",
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert calls == [
        {
            "path": "/v1/predictions",
            "idempotency_key": "mediaforge-job_timeout",
            "cancel_after": "5s",
        },
        {
            "path": "/v1/predictions/prediction-timeout/cancel",
            "idempotency_key": "mediaforge-cancel-job_timeout",
            "cancel_after": "",
        },
    ]


def test_replicate_provider_honors_a_short_retry_after_header(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests = 0
    delays: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:
            nonlocal requests
            requests += 1
            assert self.path == "/v1/models?limit=1"
            if requests == 1:
                self.send_response(429)
                self.send_header("Retry-After", "3")
                self.end_headers()
                return
            body = json.dumps({"results": []}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(replicate_module.time, "sleep", delays.append)
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            http_retry_attempts=1,
            http_retry_backoff_seconds=0,
            http_retry_max_delay_seconds=5,
        )
        assert provider._request_json("GET", "/models?limit=1") == {"results": []}
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests == 2
    assert delays == [3]


def test_replicate_provider_surfaces_a_long_retry_after_for_job_scheduling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:
            nonlocal requests
            requests += 1
            self.send_response(429)
            self.send_header("Retry-After", "45")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(replicate_module.time, "sleep", lambda _delay: None)
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            http_retry_attempts=2,
            http_retry_backoff_seconds=0,
            http_retry_max_delay_seconds=5,
        )
        with pytest.raises(ReplicateProviderError, match="retry after 45s") as error:
            provider._request_json("GET", "/models?limit=1")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert error.value.retry_after_seconds == 45
    assert requests == 1


def test_replicate_provider_cancels_a_persisted_prediction_by_id() -> None:
    calls: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            calls.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "idempotency_key": self.headers.get("Idempotency-Key", ""),
                }
            )
            assert self.path == "/v1/predictions/prediction-bound/cancel"
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = ReplicateVideoProvider(
            api_token="test-token",
            version="model-version:v1",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            timeout_seconds=180,
            cancel_request_timeout_seconds=7,
            http_retry_attempts=0,
        )
        result = provider.cancel_prediction(
            "prediction-bound",
            job_id="job-bound",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result == {"requested": True, "detail": "provider status accepted"}
    assert calls == [
        {
            "path": "/v1/predictions/prediction-bound/cancel",
            "authorization": "Bearer test-token",
            "idempotency_key": "mediaforge-cancel-job-bound",
        }
    ]


def test_replicate_provider_can_be_built_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "replicate")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "token")
    monkeypatch.setenv("REPLICATE_MODEL_VERSION", "version")
    monkeypatch.setenv("REPLICATE_API_BASE_URL", "http://127.0.0.1:9124/v1/")
    monkeypatch.setenv("REPLICATE_HTTP_RETRY_ATTEMPTS", "4")
    monkeypatch.setenv("REPLICATE_HTTP_RETRY_BACKOFF_SECONDS", "0.25")
    monkeypatch.setenv("REPLICATE_HTTP_RETRY_MAX_DELAY_SECONDS", "11")
    monkeypatch.setenv("REPLICATE_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("REPLICATE_CANCEL_REQUEST_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("REPLICATE_POLL_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("REPLICATE_CANCEL_AFTER_SECONDS", "300")
    monkeypatch.setenv(
        "REPLICATE_WEBHOOK_URL_TEMPLATE",
        "https://studio.example.test/providers/replicate/webhook?project_id={project_id}&job_id={job_id}",
    )

    bundle = build_provider_from_env()

    assert bundle.configured is True
    assert isinstance(bundle.provider, ReplicateVideoProvider)
    assert bundle.provider.base_url == "http://127.0.0.1:9124/v1"
    assert bundle.provider.http_retry_attempts == 4
    assert bundle.provider.http_retry_backoff_seconds == 0.25
    assert bundle.provider.http_retry_max_delay_seconds == 11
    assert bundle.provider.timeout_seconds == 240
    assert bundle.provider.cancel_request_timeout_seconds == 12
    assert bundle.provider.poll_interval_seconds == 0.5
    assert bundle.provider.cancel_after_seconds == 300
    assert bundle.provider.webhook_url_template
    assert bundle.details["http_retry_attempts"] == 4
    assert bundle.details["http_retry_backoff_seconds"] == 0.25
    assert bundle.details["http_retry_max_delay_seconds"] == 11
    assert bundle.details["timeout_seconds"] == 240
    assert bundle.details["cancel_request_timeout_seconds"] == 12
    assert bundle.details["poll_interval_seconds"] == 0.5
    assert bundle.details["cancel_after_seconds"] == 300
    assert bundle.details["cancel_after_header"] == "300s"
    assert bundle.details["webhook_url_template_configured"] is True
    assert bundle.details["post_retry_requires_idempotency_key"] is True


def test_replicate_provider_allows_short_local_timeout_without_cancel_header(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "replicate")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "token")
    monkeypatch.setenv("REPLICATE_MODEL_VERSION", "version")
    monkeypatch.setenv("REPLICATE_TIMEOUT_SECONDS", "2")
    monkeypatch.delenv("REPLICATE_CANCEL_AFTER_SECONDS", raising=False)

    bundle = build_provider_from_env()

    assert bundle.configured is True
    assert isinstance(bundle.provider, ReplicateVideoProvider)
    assert bundle.provider.timeout_seconds == 2
    assert bundle.provider.cancel_after_seconds is None
    assert bundle.details["cancel_after_header"] is None


def test_replicate_input_includes_registered_reference_images(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (640, 360), "#264653").save(reference, format="PNG")
    spec = make_spec().model_copy(
        update={
            "reference_assets": [
                ReferenceAssetRef(
                    asset_id="project_001:reference:linxia",
                    name="林夏定妆照",
                    version="v1",
                    kind="character_reference",
                    character="character_a",
                    uri=str(reference),
                    mime_type="image/png",
                    sha256=sha256_file(reference),
                    size_bytes=reference.stat().st_size,
                    license="user_supplied_or_project_owned",
                    source="studio-upload",
                )
            ]
        }
    )
    provider = ReplicateVideoProvider(
        api_token="token",
        version="version",
    )

    payload = provider._build_input(spec)

    data_url = payload["reference_images"][0]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == reference.read_bytes()


def test_replicate_default_input_rejects_large_local_reference(tmp_path: Path) -> None:
    reference = tmp_path / "large-reference.png"
    reference.write_bytes(b"x" * (256 * 1024 + 1))
    spec = make_spec().model_copy(
        update={
            "reference_assets": [
                ReferenceAssetRef(
                    asset_id="project_001:reference:large",
                    name="large-reference",
                    version="v1",
                    kind="character_reference",
                    uri=str(reference),
                    mime_type="image/png",
                    license="project-owned",
                    source="test",
                )
            ]
        }
    )

    with pytest.raises(ReplicateProviderError, match="exceeds 256 KiB"):
        ReplicateVideoProvider(api_token="token", version="version")._build_input(spec)


def test_provider_router_uses_priority_and_budget() -> None:
    spec = make_spec()
    cheap = MockProvider()
    expensive = MockProvider()
    expensive.name = "expensive-provider"

    router = ProviderRouter(
        [
            ProviderRegistration(provider=cheap, priority=1),
            ProviderRegistration(provider=expensive, priority=10),
        ]
    )
    decision = router.select(spec)
    assert decision.provider.name == "expensive-provider"
    assert decision.estimated_cost == 0.02

    constrained = spec.model_copy(
        update={
            "provider_constraints": ProviderConstraints(
                capability=Capability.IMAGE_TO_VIDEO,
                max_cost=0.01,
                deadline_seconds=180,
            )
        }
    )
    with pytest.raises(ProviderRoutingError):
        router.select(constrained)


def test_provider_router_skips_open_circuit_and_recovers() -> None:
    from mediaforge_p1.router import (
        ProviderCircuitBreaker,
        ProviderCircuitBreakerSettings,
    )

    spec = make_spec()
    primary = MockProvider()
    primary.name = "primary-provider"
    fallback = MockProvider()
    fallback.name = "fallback-provider"
    now = datetime.now(timezone.utc)
    circuit = ProviderCircuitBreaker(
        ProviderCircuitBreakerSettings(failure_threshold=2, open_seconds=30)
    )
    router = ProviderRouter(
        [
            ProviderRegistration(provider=primary, priority=10),
            ProviderRegistration(provider=fallback, priority=1),
        ],
        circuit_breaker=circuit,
    )

    circuit.record_failure(primary.name, error="timeout", now=now)
    assert router.select(spec).provider.name == primary.name
    opened = circuit.record_failure(
        primary.name,
        error="rate limited",
        retry_after_seconds=45,
        now=now,
    )
    assert opened["opened"] is True
    assert opened["state"] == "OPEN"
    assert router.select(spec).provider.name == fallback.name

    assert circuit.is_available(primary.name, now=now + timedelta(seconds=44)) is False
    assert circuit.is_available(primary.name, now=now + timedelta(seconds=45)) is True
    assert circuit.snapshot(primary.name, now=now + timedelta(seconds=45))["state"] == "HALF_OPEN"
    recovered = circuit.record_success(primary.name, now=now + timedelta(seconds=46))
    assert recovered["recovered"] is True
    assert router.select(spec).provider.name == primary.name


def test_provider_circuit_state_survives_service_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("MEDIAFORGE_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("MEDIAFORGE_PROVIDER_CIRCUIT_OPEN_SECONDS", "60")
    primary = MockProvider()
    primary.name = "persistent-primary"
    fallback = MockProvider()
    fallback.name = "persistent-fallback"
    registrations = [
        ProviderRegistration(provider=primary, priority=10),
        ProviderRegistration(provider=fallback, priority=1),
    ]
    service = MediaForgeService(
        tmp_path,
        provider=primary,
        provider_registrations=registrations,
    )
    service.router.circuit_breaker.record_failure(
        primary.name,
        error="upstream unavailable",
    )
    service._persist()

    restored = MediaForgeService(
        tmp_path,
        provider=primary,
        provider_registrations=registrations,
    )
    circuit = restored.router.circuit_breaker.snapshot(primary.name)
    assert circuit["state"] == "OPEN"
    assert restored.router.select(make_spec()).provider.name == fallback.name
    assert restored.provider_circuit_status()["persistence"] == {
        "backend": "sqlite",
        "survives_restart": True,
        "shared_on_control_plane_failover": False,
        "topology": "single-control-plane-snapshot",
    }


def test_vertical_slice_exports_manifest_and_final_mp4(tmp_path: Path) -> None:
    result = run_vertical_slice(tmp_path)

    assert result["status"] == "SUCCEEDED"
    assert Path(result["final_mp4"]).exists()
    assert Path(result["manifest"]).exists()
    assert result["final_probe"]["valid"] is True
    assert len(result["jobs"]) == 3
    assert all(job["route"]["provider"] == "mock-provider" for job in result["jobs"])


def test_audio_probe_and_mix_loops_soundtrack_to_video_duration(
    tmp_path: Path,
) -> None:
    video = create_placeholder_video(
        tmp_path / "silent.mp4",
        duration_seconds=2,
        color="#264653",
    )
    audio = create_placeholder_audio(
        tmp_path / "soundtrack.m4a",
        duration_seconds=0.4,
    )
    output = mix_audio(video, audio, tmp_path / "mixed.mp4")

    assert probe_audio(audio).valid is True
    mixed_audio = probe_audio(output)
    assert mixed_audio.valid is True
    assert mixed_audio.duration_seconds is not None
    assert mixed_audio.duration_seconds >= 1.9
    assert probe_video(output).valid is True


def test_vertical_slice_supports_image_providers(tmp_path: Path) -> None:
    class StillImageProvider:
        name = "still-image-provider"

        def supports(self, capability: Capability) -> bool:
            return capability == Capability.IMAGE_GENERATION

        def estimate_cost(self, spec: GenerationSpec) -> float:
            return 0.02

        def generate(self, spec: GenerationSpec, *, job_id: str, output_dir: Path):
            output_dir.mkdir(parents=True, exist_ok=True)
            artifact_id = f"artifact_{uuid4().hex[:12]}"
            path = output_dir / f"{artifact_id}.png"
            Image.new("RGB", (640, 360), "#457b9d").save(path, format="PNG")
            return Artifact(
                artifact_id=artifact_id,
                job_id=job_id,
                kind="image",
                uri=str(path),
                mime_type="image/png",
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                duration_seconds=None,
                created_at=datetime.now(timezone.utc),
            )

    result = run_vertical_slice(tmp_path, provider=StillImageProvider())

    assert result["status"] == "SUCCEEDED"
    assert Path(result["final_mp4"]).exists()
    assert result["final_probe"]["valid"] is True
    assert len(result["jobs"]) == 3
    assert all(job["route"]["provider"] == "still-image-provider" for job in result["jobs"])
