from __future__ import annotations

import pytest

from mediaforge_p1.alerts import (
    AlertConfigurationError,
    OperationsAlertSettings,
    evaluate_operations_alerts,
    operations_alerts_prometheus,
)
from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief, JobStatus
from mediaforge_p1.providers import MockProvider
from mediaforge_p1.service import MediaForgeService
from fastapi.testclient import TestClient


def test_operations_alerts_flag_capacity_cost_worker_and_strict_readiness() -> None:
    report = evaluate_operations_alerts(
        settings=OperationsAlertSettings(
            queue_depth=2,
            queue_wait_seconds=10,
            failure_rate=0.25,
            minimum_terminal_jobs=4,
            spend_ratio=0.9,
            stale_worker_seconds=30,
            require_production_ready=True,
        ),
        studio_metrics={
            "jobs": {
                "queue_depth": 3,
                "average_queue_wait_seconds": 11,
                "terminal": 4,
                "status_counts": {JobStatus.FAILED.value: 1},
            },
            "cost": {"spent_ratio": 1.0},
        },
        runtime_metrics={
            "total_attempts": 4,
            "providers": [{"outcomes": {"failed": 1}}],
        },
        workers={"workers": [{"status": "STALE", "heartbeat_age_seconds": 31}]},
        production_readiness={"production_ready": False},
    )

    assert report["grade"] == "CRITICAL"
    codes = {alert["code"] for alert in report["alerts"]}
    assert codes == {
        "QUEUE_DEPTH_HIGH",
        "QUEUE_WAIT_HIGH",
        "JOB_FAILURE_RATE_HIGH",
        "STUDIO_SPEND_HIGH",
        "WORKER_HEARTBEAT_STALE",
        "PROVIDER_FAILURE_RATE_HIGH",
        "PRODUCTION_READINESS_BLOCKED",
    }
    prometheus = operations_alerts_prometheus(report)
    assert 'mediaforge_operations_alerts_active{severity="critical"} 4' in prometheus
    assert 'code="STUDIO_SPEND_HIGH"' in prometheus


def test_operations_alert_settings_validate_environment(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_ALERT_FAILURE_RATE", "1.1")
    with pytest.raises(AlertConfigurationError, match="MEDIAFORGE_ALERT_FAILURE_RATE"):
        OperationsAlertSettings.from_env()


def test_operations_alerts_report_provider_circuit_isolation() -> None:
    settings = OperationsAlertSettings(
        queue_depth=25,
        queue_wait_seconds=300,
        failure_rate=0.25,
        minimum_terminal_jobs=5,
        spend_ratio=0.9,
        stale_worker_seconds=900,
        require_production_ready=False,
    )
    shared = {
        "settings": settings,
        "studio_metrics": {"jobs": {}, "cost": {}},
        "runtime_metrics": {"total_attempts": 0, "providers": []},
        "workers": {"workers": []},
        "production_readiness": {"production_ready": True},
    }
    warning = evaluate_operations_alerts(
        **shared,
        provider_circuits={
            "enabled": True,
            "routing": {
                "temporarily_unavailable_provider_count": 1,
                "all_enabled_providers_temporarily_unavailable": False,
            },
        },
    )
    assert warning["grade"] == "WARNING"
    assert warning["alerts"] == [
        {
            "code": "PROVIDER_CIRCUIT_OPEN",
            "severity": "warning",
            "summary": "One or more Providers are temporarily isolated by the circuit breaker.",
            "observed": 1,
            "threshold": 0,
            "source": "provider_circuit_breaker.routing",
        }
    ]

    critical = evaluate_operations_alerts(
        **shared,
        provider_circuits={
            "enabled": True,
            "routing": {
                "temporarily_unavailable_provider_count": 2,
                "all_enabled_providers_temporarily_unavailable": True,
            },
        },
    )
    assert critical["grade"] == "CRITICAL"
    assert critical["alerts"][0]["code"] == "PROVIDER_CIRCUIT_OPEN"
    assert critical["alerts"][0]["severity"] == "critical"


def test_service_alerts_when_all_enabled_provider_circuits_are_open(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFORGE_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "1")
    service = MediaForgeService(tmp_path)
    service.router.circuit_breaker.record_failure(
        "mock-provider",
        error="upstream outage",
    )

    report = service.operations_alerts()

    assert report["grade"] == "CRITICAL"
    alert = next(
        item
        for item in report["alerts"]
        if item["code"] == "PROVIDER_CIRCUIT_OPEN"
    )
    assert alert["severity"] == "critical"
    assert "PROVIDER_CIRCUIT_OPEN" in service.operations_alerts_prometheus()


def test_service_and_api_expose_operations_alerts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_ALERT_QUEUE_DEPTH", "1")
    monkeypatch.setenv("MEDIAFORGE_ALERT_REQUIRE_PRODUCTION_READY", "true")
    service = MediaForgeService(tmp_path)
    service.create_project(CreativeBrief(
        project_id="operations_alerts",
        title="Operations alerts",
        premise="A production control plane checks its own queue.",
        genre="drama",
        style="cinema",
        duration_seconds=30,
        budget=2,
        characters=["A", "B"],
    ))
    plan = service.generate_plan("operations_alerts")
    service.enqueue_shot("operations_alerts", plan["shots"][0]["shot"]["shot_id"])
    report = service.operations_alerts()
    assert report["grade"] == "CRITICAL"
    assert {alert["code"] for alert in report["alerts"]} == {
        "QUEUE_DEPTH_HIGH",
        "PRODUCTION_READINESS_BLOCKED",
    }

    app = create_app(output_root=tmp_path / "api")
    with TestClient(app) as client:
        response = client.get("/ops/alerts")
        assert response.status_code == 200
        assert response.json()["schema_version"] == "mediaforge-operations-alerts-v1"
        assert "mediaforge_operations_alerts_active" in client.get("/metrics").text


def test_leased_control_plane_without_postgres_refuses_traffic(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_MODE", "leased")
    monkeypatch.delenv("MEDIAFORGE_DATABASE_URL", raising=False)
    monkeypatch.delenv("MEDIAFORGE_STATE_BACKEND", raising=False)
    app = create_app(output_root=tmp_path)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "standby"
        assert client.get("/livez").status_code == 200
        assert client.get("/ops/alerts").status_code == 503
        assert client.post("/projects", json={
            "project_id": "blocked_control_plane",
            "title": "Blocked control plane",
            "premise": "A standby must not accept workspace writes.",
            "genre": "drama",
            "style": "cinema",
            "duration_seconds": 30,
            "budget": 2,
            "characters": ["A", "B"],
        }).status_code == 503


def test_operations_alerts_do_not_probe_providers_during_metrics_collection(tmp_path, monkeypatch) -> None:
    class CountingProvider(MockProvider):
        name = "counting-provider"

        def __init__(self) -> None:
            self.health_checks = 0

        def health_check(self) -> dict:
            self.health_checks += 1
            return super().health_check()

    monkeypatch.setenv("MEDIAFORGE_ALERT_REQUIRE_PRODUCTION_READY", "true")
    provider = CountingProvider()
    service = MediaForgeService(tmp_path, provider=provider)
    service.operations_alerts()
    assert provider.health_checks == 0
    service.production_readiness()
    assert provider.health_checks == 1
    service.operations_alerts()
    assert provider.health_checks == 1
