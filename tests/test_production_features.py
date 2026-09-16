from __future__ import annotations

import base64
import io
import json
import subprocess
import threading
import wave
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief, ReviewStatus
from mediaforge_p1.enterprise_runtime import BillingLedger, EnterpriseConfigurationError
from mediaforge_p1.media import create_placeholder_video, probe_audio, probe_video, sha256_file
from mediaforge_p1.quality import QualityEvaluator
from mediaforge_p1.service import MediaForgeService, WorkflowError
from mediaforge_p1.speech import SpeechSynthesizer, SpeechSynthesisError
from mediaforge_p1.visual import sample_frames, visual_report
from mediaforge_p1.worker import probe_gpu_resources


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name in ("MEDIAFORGE_PROVIDERS", "MEDIAFORGE_QUALITY_URL", "MEDIAFORGE_WEBHOOK_URLS", "MEDIAFORGE_LLM_MODE", "MEDIAFORGE_TENANT_QUOTAS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"STATE_BACKEND": "sqlite", "QUEUE_BACKEND": "sqlite", "STORAGE_MODE": "local",
                        "PROVIDER": "mock", "AUTH_MODE": "disabled", "RATE_LIMIT_ENABLED": "false",
                        "TTS_MODE": "deterministic", "VISUAL_GATE": "false"}.items():
        monkeypatch.setenv("MEDIAFORGE_" + name, value)


def brief(project_id="production_test"):
    return CreativeBrief(project_id=project_id, title="Future call", premise="A woman receives a call from her future self.",
                         genre="suspense", style="cinematic", characters=["Alice", "Bob"], duration_seconds=30, budget=2)


@contextmanager
def endpoint(response):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 16000)
    return buffer.getvalue()


def test_http_tts_normalizes_wav_and_preserves_previous_on_invalid_response(tmp_path):
    output = tmp_path / "accepted.m4a"
    with endpoint({"audio_b64": base64.b64encode(wav_bytes()).decode()}) as (url, received):
        metadata = SpeechSynthesizer(mode="http", url=url).synthesize("Test voice", output)
    assert received[0]["text"] == "Test voice"
    assert metadata["preview_only"] is False
    assert output.read_bytes()[4:8] == b"ftyp"
    assert probe_audio(output).valid
    digest = sha256_file(output)
    with endpoint({"audio_b64": base64.b64encode(b"not audio").decode()}) as (url, _):
        with pytest.raises(SpeechSynthesisError):
            SpeechSynthesizer(mode="http", url=url).synthesize("Invalid", output)
    assert sha256_file(output) == digest
    assert not list(tmp_path.glob(".tts-*"))


@pytest.mark.parametrize("settings", [{"mode": "unknown"}, {"timeout": float("nan")}, {"timeout": -1}])
def test_invalid_tts_configuration(settings):
    with pytest.raises(SpeechSynthesisError):
        SpeechSynthesizer(**settings)


def test_preview_audio_is_explicitly_blocked_for_release(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    result = service.generate_voiceover("production_test", text="Preview tone", license="owned", source="test fixture")
    assert result["audio_track_metadata"]["preview_only"]
    checks = {check["name"]: check for check in service.project_compliance("production_test")["checks"]}
    assert checks["audio_track_license"]["passed"]
    assert not checks["audio_track_production"]["passed"]
    assert checks["audio_track_production"]["blocking"]


def test_voiceover_failure_does_not_replace_project_audio(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    first = service.generate_voiceover("production_test", text="Existing preview")
    before = Path(first["audio_track"]).read_bytes()
    with endpoint({"audio_b64": "invalid"}) as (url, _):
        service.speech_synthesizer = SpeechSynthesizer(mode="http", url=url)
        with pytest.raises(SpeechSynthesisError):
            service.generate_voiceover("production_test", text="New audio")
    assert service.project_view("production_test")["audio_track"] == first["audio_track"]
    assert Path(first["audio_track"]).read_bytes() == before
    with pytest.raises(WorkflowError, match="evidence"):
        service.generate_voiceover("production_test", text="New audio", license="licensed")


def test_lipsync_promotes_master_and_survives_restart(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    project = service.projects["production_test"]
    source_video = tmp_path / "production_test" / "source.mp4"
    source_audio = tmp_path / "production_test" / "source.wav"
    create_placeholder_video(source_video, duration_seconds=1, color="#334455")
    source_audio.write_bytes(wav_bytes())
    project.final_mp4 = str(source_video)
    project.audio_track = str(source_audio)

    class FakeLipSync:
        def status_view(self):
            return {"mode": "command", "configured": True, "provider": "test-lipsync"}

        def render(self, video, _audio, output, *, metadata):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(Path(video).read_bytes())
            return {"mode": "command", "provider": "test-lipsync", "uri": str(output), "sha256": sha256_file(output), "size_bytes": output.stat().st_size}

    service.lipsync = FakeLipSync()
    result = service.generate_lipsync("production_test", actor="tester")

    assert result["lipsync_artifact"]["provider"] == "test-lipsync"
    assert Path(result["final_mp4"]).is_file()
    assert probe_video(Path(result["final_mp4"])).valid
    assert any(event["action"] == "postproduction.lipsync_completed" for event in service.audit_log("production_test")["events"])
    restarted = MediaForgeService(tmp_path)
    assert restarted.project_view("production_test")["lipsync_artifact"]["uri"] == result["lipsync_artifact"]["uri"]


def test_training_dataset_exports_only_approved_quality_passed_shots(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    plan = service.generate_plan("production_test")
    shot_id = plan["shots"][0]["shot"]["shot_id"]
    service.submit_shot("production_test", shot_id)
    service.review_shot("production_test", shot_id, status=ReviewStatus.APPROVED, comment="good")

    result = service.export_training_dataset("production_test", actor="dataset-test")

    assert result["record_count"] == 1
    assert Path(result["dataset_path"]).read_text(encoding="utf-8").count("\n") == 1
    assert json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))["source"] == "approved-current-artifacts"
    assert service.training_dataset("production_test")["ready"] is True


def test_multi_speaker_dialogue_is_atomic_persistent_and_invalidates_on_shot_change(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    plan = service.generate_plan("production_test")
    first, second = [item["shot"]["shot_id"] for item in plan["shots"][:2]]
    lines = [
        {
            "line_id": "line_01",
            "shot_id": first,
            "speaker": "Alice",
            "text": "I just received a call.",
            "start_seconds": 0,
            "end_seconds": 1.2,
            "voice": "alice",
        },
        {
            "line_id": "line_02",
            "shot_id": second,
            "speaker": "Bob",
            "text": "The call came from the future.",
            "start_seconds": 5,
            "end_seconds": 6.4,
            "voice": "bob",
        },
    ]
    project = service.generate_dialogue_timeline("production_test", lines=lines)
    audio_path = Path(project["audio_track"])
    assert audio_path.is_file()
    assert probe_audio(audio_path).valid
    assert project["dialogue_timeline"]["current"] is True
    assert project["audio_track_metadata"]["line_count"] == 2
    assert project["audio_track_metadata"]["speakers"] == ["Alice", "Bob"]
    subtitles, cue_count, duration = service._write_project_subtitles(service.projects["production_test"])
    assert cue_count == 2
    assert duration == 30
    assert "Alice: I just received a call." in subtitles.read_text(encoding="utf-8")

    clone = service.clone_project("production_test", target_project_id="dialogue_clone")
    assert clone["dialogue_timeline"]["current"] is True
    assert Path(clone["audio_track"]).is_file()
    snapshot = service.export_project_snapshot("production_test")
    snapshot_record = json.loads(Path(snapshot["snapshot"]).read_text(encoding="utf-8"))
    imported = service.import_project_snapshot(snapshot_record, target_project_id="dialogue_import")
    assert imported["dialogue_timeline"]["current"] is True

    before = audio_path.read_bytes()
    with endpoint({"audio_b64": "invalid"}) as (url, _):
        service.speech_synthesizer = SpeechSynthesizer(mode="http", url=url)
        with pytest.raises(SpeechSynthesisError, match="previous audio was kept"):
            service.generate_dialogue_timeline("production_test", lines=lines)
    assert audio_path.read_bytes() == before

    service.update_shot_card(
        "production_test", first, changes={"duration_seconds": 4}, actor="editor"
    )
    stale = service.project_view("production_test")["dialogue_timeline"]
    assert stale["current"] is False
    with pytest.raises(WorkflowError, match="dialogue timeline is stale"):
        service.export_project("production_test")


def test_dialogue_api_validates_timed_lines(tmp_path):
    with TestClient(create_app(output_root=tmp_path)) as client:
        payload = brief("dialogue_api").model_dump(mode="json")
        assert client.post("/projects", json=payload).status_code == 201
        plan = client.post("/projects/dialogue_api/plan")
        assert plan.status_code == 200
        shot = plan.json()["shots"][0]["shot"]
        response = client.post(
            "/projects/dialogue_api/dialogue",
            json={
                "lines": [{
                    "line_id": "line_01",
                    "shot_id": shot["shot_id"],
                    "speaker": "Alice",
                    "text": "API dialogue.",
                    "start_seconds": 0,
                    "end_seconds": 1,
                    "voice": "alice",
                    "language": "zh-CN",
                }],
            },
        )
        assert response.status_code == 201
        assert response.json()["dialogue_timeline"]["current"] is True
        invalid = client.post(
            "/projects/dialogue_api/dialogue",
            json={"lines": [{
                "line_id": "line_02", "shot_id": shot["shot_id"], "speaker": "Mallory",
                "text": "Undeclared speaker", "start_seconds": 0, "end_seconds": 1,
            }]},
        )
        assert invalid.status_code == 422


def test_visual_exposure_and_contrast_use_real_pixels(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (800, 400), "black").save(path)
    report = visual_report(path, "image", blocking=True)
    assert report["available"] and not report["passed"]
    assert report["frames"][0]["black_ratio"] == 1
    image = Image.new("RGB", (800, 400), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 399, 399), fill="black")
    image.save(path)
    assert visual_report(path, "image")["passed"]
    frame = sample_frames(path, "image")[0]
    with Image.open(io.BytesIO(base64.b64decode(frame["data_b64"]))) as decoded:
        assert decoded.width == 512
        assert decoded.format == "JPEG"
    assert "data_b64" not in visual_report(path, "image")["frames"][0]


def test_remote_quality_receives_frames_and_scoped_references(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    service.generate_plan("production_test")
    spec = next(iter(service.projects["production_test"].shots.values())).spec
    root = tmp_path / "production_test"
    root.mkdir(exist_ok=True)
    image = root / "sample.png"
    Image.new("RGB", (640, 360), "green").save(image)
    reference = spec.reference_assets[0].model_copy(update={"uri": str(image), "sha256": sha256_file(image)})
    outside = tmp_path / "other.png"
    Image.new("RGB", (10, 10)).save(outside)
    foreign = reference.model_copy(update={"uri": str(outside)})
    spec = spec.model_copy(update={"reference_assets": [reference, foreign]})
    with endpoint({"passed": True, "score": 0.9, "checks": [{"name": "identity", "passed": True}]}) as (url, received):
        result = QualityEvaluator(url=url, allow_data_export=True, retries=0).evaluate(
            image, artifact_id="a", media_kind="image", local_quality={}, spec=spec, reference_root=root)
    assert result.passed
    assert len(received[0]["reference_frames"]) == 1
    assert received[0]["reference_frames"][0]["frames"][0]["data_b64"]
    assert str(tmp_path) not in json.dumps(received[0])


@pytest.mark.parametrize("response", [{}, {"passed": "false"}, {"passed": True, "checks": [{"passed": "false"}]}])
def test_incomplete_external_quality_fails_closed(tmp_path, response):
    path = tmp_path / "frame.png"
    Image.new("RGB", (32, 32)).save(path)
    with endpoint(response) as (url, _):
        result = QualityEvaluator(url=url, allow_data_export=True, fail_open=False, retries=0).evaluate(
            path, artifact_id="a", media_kind="image", local_quality={}, spec={})
    assert result.passed is False
    assert result.error


def test_recheck_failure_blocks_approval_and_invalidates_delivery(tmp_path, monkeypatch):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    plan = service.generate_plan("production_test")
    shot_id = plan["shots"][0]["shot"]["shot_id"]
    service.submit_shot("production_test", shot_id)
    service.review_shot("production_test", shot_id, status=ReviewStatus.APPROVED)
    project = service.projects["production_test"]
    project.final_mp4 = "previous-export.mp4"
    project.delivery_package = "previous-delivery.zip"
    monkeypatch.setenv("MEDIAFORGE_VISUAL_GATE", "true")
    result = service.recheck_shot_quality("production_test", shot_id)
    assert result["quality"]["visual_evaluation"]["frames"]
    assert not result["quality"]["passed"]
    assert result["review_status"] == "CHANGES_REQUESTED"
    assert project.final_mp4 is None and project.delivery_package is None
    with pytest.raises(WorkflowError, match="quality must pass"):
        service.review_shot("production_test", shot_id, status=ReviewStatus.APPROVED)
    restored = MediaForgeService(tmp_path)
    assert restored.projects["production_test"].shots[shot_id].quality == result["quality"]
    monkeypatch.setenv("MEDIAFORGE_VISUAL_GATE", "false")
    revised = service.revise_shot("production_test", shot_id, comment="Use advisory checks for intentional flat-color Mock footage")
    assert revised["revision"] == result["revision"] + 1
    assert revised["quality"]["passed"]
    assert service.review_shot("production_test", shot_id, status=ReviewStatus.APPROVED)["review_status"] == "APPROVED"
    project.release = {"release_id": "locked"}
    with pytest.raises(WorkflowError, match="released"):
        service.recheck_shot_quality("production_test", shot_id)


def test_billing_filters_pagination_and_currency_isolation(tmp_path):
    ledger = BillingLedger(tmp_path / "usage.sqlite3")
    for index in range(5):
        ledger.record(event_id=str(index), tenant_id="a", project_id="one", category="generation", quantity=1, unit_price=0.2, currency="USD")
    ledger.record(event_id="other", tenant_id="b", category="generation", quantity=1, unit_price=1)
    ledger.record(event_id="eur", tenant_id="a", category="storage", quantity=1, unit_price=1, currency="EUR")
    first = ledger.event_page("a", project_id="one", category="generation", currency="usd", limit=2)
    second = ledger.event_page("a", project_id="one", limit=2, offset=2)
    assert first["total"] == 5 and first["has_more"]
    assert not ({event["event_id"] for event in first["events"]} & {event["event_id"] for event in second["events"]})
    timestamp = first["events"][0]["occurred_at"]
    assert ledger.event_page("a", since=timestamp + 100)["total"] == 0
    assert ledger.summary("a")["currency"] is None
    with pytest.raises(EnterpriseConfigurationError):
        ledger.event_page("a", since=2, until=1)


def test_new_api_validation_and_worker_health(tmp_path):
    with TestClient(create_app(output_root=tmp_path)) as client:
        assert client.get("/speech/status").json()["preview_only"]
        assert client.get("/billing/events?limit=501").status_code == 422
        assert client.get("/billing/events?since=nan").status_code == 422
        assert client.get("/billing/events?since=2&until=1").status_code == 422
        assert client.get("/billing/events?currency=invalid").status_code == 422
        assert client.post("/projects/missing/shots/missing/review-quality", json={}).status_code == 404
    service = MediaForgeService(tmp_path)
    service.register_worker("live", resources={"cpu_count": 4})
    service.register_worker("stale")
    service.workers["stale"]["last_heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    status = service.worker_status()
    assert status["worker_count"] == 2 and status["online_count"] == 1 and status["stale_count"] == 1
    assert status["execution_mode"] == "api-provider-dispatch"


def test_gpu_probe_and_memory_admission_refresh(tmp_path):
    parsed = probe_gpu_resources(
        command_runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="0, Test GPU, 555.1, 8192, 6144, 17\n", stderr="",
        )
    )
    assert parsed["gpu_probe"]["status"] == "available"
    assert parsed["gpus"] == [{
        "index": 0, "name": "Test GPU", "driver_version": "555.1",
        "memory_total_mib": 8192, "memory_free_mib": 6144,
        "utilization_percent": 17,
    }]

    service = MediaForgeService(tmp_path)
    service.create_project(brief("gpu_admission"))
    plan = service.generate_plan("gpu_admission")
    service.enqueue_shot("gpu_admission", plan["shots"][0]["shot"]["shot_id"])
    service.register_worker(
        "gpu-worker",
        capabilities=["image_to_video"],
        resources={
            "execution_mode": "api-provider-dispatch",
            "gpu_probe": {"status": "available", "source": "fixture"},
            "gpus": [{"index": 0, "name": "Test GPU", "memory_total_mib": 8192, "memory_free_mib": 1024}],
        },
    )
    rejected = service.claim_worker_jobs("gpu-worker", minimum_gpu_memory_mib=2048)
    assert rejected["claimed_count"] == 0
    assert rejected["admission"]["eligible"] is False
    assert rejected["admission"]["reason"] == "insufficient free GPU memory"
    refreshed = service.worker_heartbeat(
        "gpu-worker",
        resources={
            "execution_mode": "api-provider-dispatch",
            "gpu_probe": {"status": "available", "source": "fixture"},
            "gpus": [{"index": 0, "name": "Test GPU", "memory_total_mib": 8192, "memory_free_mib": 4096}],
        },
    )
    assert refreshed["worker"]["gpu"]["memory_free_mib"] == 4096
    claimed = service.claim_worker_jobs("gpu-worker", minimum_gpu_memory_mib=2048)
    assert claimed["claimed_count"] == 1
    assert claimed["admission"]["eligible"] is True
