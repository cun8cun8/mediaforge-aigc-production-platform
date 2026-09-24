from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthManager,
)
from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.delivery import DeliveryDispatcher
from mediaforge_p1.service import MediaForgeService, ProjectRuntime
from mediaforge_p1.temporal_orchestration import (
    TemporalConfigurationError,
    TemporalOperationRequest,
    TemporalOrchestrator,
    TemporalOrchestrationSettings,
)
from mediaforge_p1.temporal_worker import TemporalControlPlaneClient


def test_temporal_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEDIAFORGE_TEMPORAL_ENABLED", raising=False)
    settings = TemporalOrchestrationSettings.from_env()
    assert settings.enabled is False
    assert TemporalOrchestrator(settings).status_view()["configured"] is False


def test_temporal_status_reports_worker_control_plane_configuration():
    disabled_worker = TemporalOrchestrator(TemporalOrchestrationSettings(enabled=True))
    configured_worker = TemporalOrchestrator(
        TemporalOrchestrationSettings(
            enabled=True,
            control_plane_url="http://127.0.0.1:8020",
        )
    )
    assert disabled_worker.status_view()["worker_connection_configured"] is False
    assert configured_worker.status_view()["worker_connection_configured"] is True


def test_temporal_settings_reject_partial_mtls():
    with pytest.raises(TemporalConfigurationError, match="mTLS"):
        TemporalOrchestrationSettings(
            enabled=True,
            tls=True,
            client_cert_file="client.crt",
        ).validate()


def test_temporal_request_has_stable_workflow_id_and_payload():
    request = TemporalOperationRequest(
        project_id="demo",
        operation="dispatch",
        request_id="delivery-v1",
        delivery={"channel": "review", "recipient": "producer"},
    )
    assert request.workflow_id == "mediaforge:demo:dispatch:delivery-v1"
    assert request.to_payload()["delivery"] == {
        "channel": "review",
        "recipient": "producer",
    }


def test_temporal_request_rejects_unsafe_or_incomplete_operations():
    with pytest.raises(TemporalConfigurationError):
        TemporalOperationRequest(project_id="demo/../other", operation="render", request_id="r1")
    with pytest.raises(TemporalConfigurationError, match="shot_id"):
        TemporalOperationRequest(project_id="demo", operation="generation", request_id="r1")
    with pytest.raises(TemporalConfigurationError, match="delivery"):
        TemporalOperationRequest(project_id="demo", operation="dispatch", request_id="r1")


class _FakeHandle:
    id = "mediaforge:demo:render:render-v1"
    result_run_id = "run-1"

    async def describe(self):
        return type(
            "Description",
            (),
            {
                "run_id": "run-1",
                "status": "RUNNING",
                "workflow_type": "MediaForgeProductionWorkflow",
                "start_time": None,
                "close_time": None,
            },
        )()

    async def cancel(self):
        return None


class _FakeTemporalClient:
    def __init__(self):
        self.handle = _FakeHandle()
        self.starts = []

    async def start_workflow(self, workflow, payload, **kwargs):
        self.starts.append((workflow, payload, kwargs))
        self.handle.id = kwargs["id"]
        return self.handle

    def get_workflow_handle(self, workflow_id):
        assert workflow_id == self.handle.id
        return self.handle


def test_temporal_orchestrator_starts_describes_and_cancels_with_fake_client():
    client = _FakeTemporalClient()
    settings = TemporalOrchestrationSettings(enabled=True)
    orchestrator = TemporalOrchestrator(settings, client_factory=lambda _settings: client)
    request = TemporalOperationRequest(
        project_id="demo",
        operation="render",
        request_id="render-v1",
    )

    started = orchestrator.start(request)
    assert started["started"] is True
    assert started["workflow_id"] == request.workflow_id
    assert client.starts[0][1]["request_id"] == "render-v1"
    assert orchestrator.describe(request.workflow_id)["status"] == "RUNNING"
    assert orchestrator.cancel(request.workflow_id)["canceled"] is True
    assert orchestrator.status_view()["client_ready"] is True


def test_worker_maps_operation_to_existing_control_plane(monkeypatch):
    settings = TemporalOrchestrationSettings(
        enabled=True,
        control_plane_url="http://127.0.0.1:8020",
    )
    client = TemporalControlPlaneClient(settings)
    calls = []

    def fake_request(method, path, body=None, extra_headers=None):
        calls.append((method, path, body, extra_headers))
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.invoke(
        TemporalOperationRequest(
            project_id="demo",
            operation="dispatch",
            request_id="delivery-v1",
            delivery={"channel": "review", "recipient": "producer"},
            actor="worker",
        ).to_payload()
    )
    assert calls == [
        (
            "POST",
            "/projects/demo/deliveries/dispatch",
            {"channel": "review", "recipient": "producer", "actor": "worker"},
            {"Idempotency-Key": "temporal-delivery-v1"},
        )
    ]
    assert result["temporal_operation"] == "dispatch"


def test_dispatcher_uses_stable_target_for_an_idempotency_key(tmp_path):
    package = tmp_path / "delivery.zip"
    package.write_bytes(b"delivery")
    dispatcher = DeliveryDispatcher(tmp_path / "artifacts")

    first = dispatcher.dispatch(
        package,
        project_id="demo",
        release_id="release-v1",
        channel="review",
        recipient="producer",
        idempotency_key="temporal-delivery-v1",
    )
    second = dispatcher.dispatch(
        package,
        project_id="demo",
        release_id="release-v1",
        channel="review",
        recipient="producer",
        idempotency_key="temporal-delivery-v1",
    )

    assert first.delivery_id == second.delivery_id
    assert first.destination_uri == second.destination_uri


def test_service_returns_existing_delivery_for_the_same_idempotency_key(tmp_path):
    service = MediaForgeService(tmp_path)
    project = ProjectRuntime(brief=CreativeBrief.model_validate(_brief("delivery_idempotent")))
    project.deliveries.append(
        {
            "delivery_id": "delivery_existing",
            "status": "DELIVERED",
            "idempotency_key": "temporal-delivery-v1",
            "receipt_path": str(tmp_path / "receipt.json"),
            "dispatch": {"delivery_id": "dispatch_existing"},
        }
    )
    service.projects[project.brief.project_id] = project

    result = service.dispatch_delivery(
        project.brief.project_id,
        idempotency_key="temporal-delivery-v1",
    )

    assert result["idempotent"] is True
    assert result["delivery"]["delivery_id"] == "delivery_existing"


def _brief(project_id: str) -> dict[str, object]:
    return {
        "project_id": project_id,
        "title": "午夜来电",
        "premise": "女主在深夜接到一个来自自己未来的电话。",
        "genre": "都市悬疑",
        "style": "cinematic blue hour",
        "duration_seconds": 30,
        "budget": 2.0,
        "characters": ["林夏", "周启"],
    }


def test_temporal_api_returns_clear_error_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "disabled")
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "mock")
    monkeypatch.setenv("MEDIAFORGE_TEMPORAL_ENABLED", "false")
    app = create_app(output_root=tmp_path)

    with TestClient(app) as client:
        assert client.post("/projects", json=_brief("temporal_disabled")).status_code == 201
        response = client.post(
            "/projects/temporal_disabled/orchestration/temporal",
            json={"operation": "render", "request_id": "render-v1"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Temporal orchestration is disabled"


def test_temporal_api_starts_and_inspects_with_injected_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "disabled")
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "mock")
    monkeypatch.setenv("MEDIAFORGE_TEMPORAL_ENABLED", "false")
    app = create_app(output_root=tmp_path)
    fake_client = _FakeTemporalClient()
    app.state.mediaforge.temporal = TemporalOrchestrator(
        TemporalOrchestrationSettings(enabled=True),
        client_factory=lambda _settings: fake_client,
    )

    with TestClient(app) as client:
        assert client.post("/projects", json=_brief("temporal_api")).status_code == 201
        started = client.post(
            "/projects/temporal_api/orchestration/temporal",
            json={"operation": "render", "request_id": "render-v1"},
        )
        detail = client.get(
            "/projects/temporal_api/orchestration/temporal/mediaforge:temporal_api:render:render-v1"
        )
        listing = client.get("/projects/temporal_api/orchestration/temporal")
        canceled = client.post(
            "/projects/temporal_api/orchestration/temporal/mediaforge:temporal_api:render:render-v1/cancel",
            json={},
        )
        dispatched = client.post(
            "/projects/temporal_api/orchestration/temporal",
            json={
                "operation": "dispatch",
                "request_id": "dispatch-v1",
                "delivery": {
                    "channel": "review",
                    "recipient": "producer",
                    "actor": "ignored-client-actor",
                },
            },
        )

    assert started.status_code == 200
    assert started.json()["workflow_id"] == "mediaforge:temporal_api:render:render-v1"
    assert dispatched.status_code == 200
    assert fake_client.starts[1][1]["delivery"]["channel"] == "review"
    assert fake_client.starts[1][1]["delivery"]["recipient"] == "producer"
    assert "actor" not in fake_client.starts[1][1]["delivery"]
    assert detail.status_code == 200
    assert detail.json()["status"] == "RUNNING"
    assert listing.status_code == 200
    assert listing.json()["workflows"][0]["status"] == "RUNNING"
    assert "mediaforge_temporal_workflows{state=\"RUNNING\"} 1" in client.get("/metrics").text
    assert canceled.status_code == 200
    assert canceled.json()["canceled"] is True
    assert canceled.json()["record"]["status"] == "CANCEL_REQUESTED"


def test_temporal_workflow_index_persists_and_refreshes(tmp_path):
    service = MediaForgeService(tmp_path)
    project = ProjectRuntime(brief=CreativeBrief.model_validate(_brief("temporal_index")))
    service.projects[project.brief.project_id] = project
    request = TemporalOperationRequest(
        project_id=project.brief.project_id,
        operation="render",
        request_id="render-v1",
    )

    service.record_temporal_event(request, state="REQUESTED", actor="studio")
    service.record_temporal_event(
        request,
        state="STARTED",
        actor="studio",
        details={"task_queue": "mediaforge-orchestration", "run_id": "run-1"},
    )
    report = service.temporal_workflows(project.brief.project_id)

    assert report["workflows"][0]["status"] == "RUNNING"
    assert report["workflows"][0]["run_id"] == "run-1"
    assert "mediaforge_temporal_workflows{state=\"RUNNING\"} 1" in service.temporal_orchestration_prometheus()

    reloaded = MediaForgeService(tmp_path)
    reloaded_report = reloaded.temporal_workflows(project.brief.project_id)
    assert reloaded_report["workflows"][0]["workflow_id"] == request.workflow_id
    assert reloaded_report["workflows"][0]["task_queue"] == "mediaforge-orchestration"


def test_orchestrator_identity_is_tenant_bound_and_activity_limited(monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv(
        "MEDIAFORGE_API_KEYS",
        '{"temporal":{"subject":"temporal-worker","role":"orchestrator","tenant_id":"tenant_a"}}',
    )
    auth = AuthManager.from_env()
    principal = auth.authenticate("Bearer temporal")

    auth.authorize(
        principal,
        method="POST",
        path="/projects/project_a/export",
    )
    with pytest.raises(AuthenticationError, match="restricted"):
        auth.authorize(
            principal,
            method="POST",
            path="/projects/project_a/orchestration/temporal",
        )

    monkeypatch.setenv(
        "MEDIAFORGE_API_KEYS",
        '{"temporal":{"subject":"temporal-worker","role":"orchestrator"}}',
    )
    with pytest.raises(AuthConfigurationError, match="tenant_id"):
        AuthManager.from_env()
