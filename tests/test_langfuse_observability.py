from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.langfuse_observability import (
    LangfuseConfigurationError,
    LangfuseObservability,
    LangfuseSettings,
)
from mediaforge_p1.service import MediaForgeService


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class FakeContext:
    def __init__(self, observation: FakeObservation) -> None:
        self.observation = observation
        self.exits: list[tuple] = []

    def __enter__(self) -> FakeObservation:
        return self.observation

    def __exit__(self, *args) -> None:
        self.exits.append(args)


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.contexts: list[FakeContext] = []
        self.flushed = False

    def start_as_current_observation(self, **kwargs) -> FakeContext:
        self.calls.append(kwargs)
        context = FakeContext(FakeObservation())
        self.contexts.append(context)
        return context

    def flush(self) -> None:
        self.flushed = True


class CapturingTelemetry:
    def __init__(self) -> None:
        self.generation_calls: list[dict] = []

    @contextmanager
    def provider_generation(self, **kwargs):
        self.generation_calls.append(kwargs)
        yield type(
            "Run",
            (),
            {"succeed": lambda _self, **_kwargs: None},
        )()

    def record_evaluation(self, **_kwargs) -> None:
        pass

    def status_view(self) -> dict:
        return {"enabled": False, "client_ready": False}

    def flush(self) -> None:
        pass


def test_langfuse_is_disabled_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("MEDIAFORGE_LANGFUSE_ENABLED", raising=False)
    settings = LangfuseSettings.from_env()
    status = LangfuseObservability(settings).status_view()
    assert status["enabled"] is False
    assert status["credentials_configured"] is False
    assert status["client_ready"] is False


def test_langfuse_settings_require_file_backed_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MEDIAFORGE_LANGFUSE_ENABLED", "true")
    with pytest.raises(LangfuseConfigurationError, match="PUBLIC_KEY_FILE"):
        LangfuseSettings.from_env()

    public_key = tmp_path / "public-key"
    secret_key = tmp_path / "secret-key"
    public_key.write_text("pk-lf-test-public", encoding="utf-8")
    secret_key.write_text("sk-lf-test-secret", encoding="utf-8")
    monkeypatch.setenv("MEDIAFORGE_LANGFUSE_PUBLIC_KEY_FILE", str(public_key))
    monkeypatch.setenv("MEDIAFORGE_LANGFUSE_SECRET_KEY_FILE", str(secret_key))
    monkeypatch.setenv("MEDIAFORGE_LANGFUSE_ENVIRONMENT", "staging")
    settings = LangfuseSettings.from_env()

    status = settings.status_view()
    assert status["credentials_configured"] is True
    assert "pk-lf-test-public" not in str(status)
    assert "sk-lf-test-secret" not in str(status)


def test_provider_export_is_redacted_and_records_result() -> None:
    client = FakeLangfuseClient()
    settings = LangfuseSettings(
        enabled=True,
        base_url="https://langfuse.example.test",
        environment="staging",
        public_key="pk-lf-test-public",
        secret_key="sk-lf-test-secret",
    )
    telemetry = LangfuseObservability(settings, client_factory=lambda _settings: client)
    secret_prompt = "do not export this prompt body"

    with telemetry.provider_generation(
        project_id="project_1",
        trace_id="trace_1",
        job_id="job_1",
        provider="comfyui",
        capability="image_generation",
        estimated_cost=0.12,
        prompt_versions=[
            {
                "prompt_id": "prompt_1",
                "key": "planning.story",
                "version": 3,
                "sha256": "a" * 64,
                "rendered": secret_prompt,
            }
        ],
        spec={"prompt": secret_prompt, "local_path": "D:/private/source.png"},
    ) as run:
        run.succeed(artifact_id="asset_1", artifact_sha256="b" * 64, kind="image")

    first_update = client.contexts[0].observation.updates[0]
    assert first_update["input"]["prompt_content_included"] is False
    assert "spec" not in first_update["input"]
    assert secret_prompt not in str(first_update)
    assert "D:/private/source.png" not in str(first_update)
    assert first_update["metadata"]["mediaforge.estimated_cost"] == 0.12
    assert client.contexts[0].observation.updates[-1]["output"]["artifact_id"] == "asset_1"
    assert telemetry.status_view()["emitted_count"] == 1


def test_export_failure_is_non_blocking_and_sampling_is_counted() -> None:
    settings = LangfuseSettings(
        enabled=True,
        base_url="https://langfuse.example.test",
        public_key="pk-lf-test-public",
        secret_key="sk-lf-test-secret",
        sample_rate=0.5,
    )
    sampled_out = LangfuseObservability(
        settings,
        client_factory=lambda _settings: FakeLangfuseClient(),
        random_value=lambda: 0.9,
    )
    with sampled_out.provider_generation(
        project_id="project_1",
        trace_id="trace_1",
        job_id="job_1",
        provider="local",
        capability="image_generation",
        estimated_cost=0,
        prompt_versions=[],
        spec={},
    ):
        pass
    assert sampled_out.status_view()["sampled_out_count"] == 1

    telemetry = LangfuseObservability(
        settings,
        client_factory=lambda _settings: (_ for _ in ()).throw(RuntimeError("collector unreachable")),
    )
    with telemetry.provider_generation(
        project_id="project_1",
        trace_id="trace_1",
        job_id="job_1",
        provider="local",
        capability="image_generation",
        estimated_cost=0,
        prompt_versions=[],
        spec={},
    ):
        pass
    assert telemetry.status_view()["client_ready"] is False
    assert telemetry.status_view()["initialization_error"] == "RuntimeError: collector unreachable"


def test_old_langfuse_sdk_is_rejected_for_otel_ingestion() -> None:
    assert LangfuseObservability._sdk_version_supported("4.7.0") is True
    assert LangfuseObservability._sdk_version_supported("4.8.1") is True
    assert LangfuseObservability._sdk_version_supported("3.15.0") is False
    assert LangfuseObservability._sdk_version_supported("unknown") is False


def test_langfuse_status_endpoint_is_safe_and_shutdown_flushes(monkeypatch, tmp_path) -> None:
    client = FakeLangfuseClient()
    telemetry = LangfuseObservability(
        LangfuseSettings(
            enabled=True,
            base_url="https://langfuse.example.test",
            public_key="pk-lf-test-public",
            secret_key="sk-lf-test-secret",
        ),
        client_factory=lambda _settings: client,
    )
    monkeypatch.setattr(
        "mediaforge_p1.service.LangfuseObservability.from_env",
        classmethod(lambda _cls: telemetry),
    )
    with TestClient(create_app(output_root=tmp_path)) as api:
        response = api.get("/observability/langfuse/status")
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert "pk-lf-test-public" not in response.text
        assert "sk-lf-test-secret" not in response.text
    assert client.flushed is True


def test_service_generation_path_emits_provider_observation(tmp_path) -> None:
    service = MediaForgeService(tmp_path)
    service.create_project(
        CreativeBrief(
            project_id="observed_project",
            title="Observed generation",
            premise="A reviewed shot is generated.",
            genre="drama",
            duration_seconds=30,
            style="natural daylight",
            characters=["Avery", "Blake"],
            budget=2.0,
        )
    )
    telemetry = CapturingTelemetry()
    service.langfuse = telemetry

    service.generate_plan("observed_project")
    shot_id = service.project_shots("observed_project")["shots"][0]["shot"]["shot_id"]
    service.submit_shot("observed_project", shot_id)

    assert len(telemetry.generation_calls) == 1
    call = telemetry.generation_calls[0]
    assert call["project_id"] == "observed_project"
    assert call["provider"] == "mock-provider"
    assert call["capability"] in {"image_generation", "image_to_video"}
