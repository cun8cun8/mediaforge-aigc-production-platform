from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.observability import (
    MetricsAccessConfigurationError,
    MetricsAccessPolicy,
)


def test_metrics_access_policy_requires_a_readable_nontrivial_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MEDIAFORGE_METRICS_AUTH_MODE", "token")
    with pytest.raises(MetricsAccessConfigurationError, match="TOKEN_FILE"):
        MetricsAccessPolicy.from_env()

    token_file = tmp_path / "metrics-token"
    token_file.write_text("short", encoding="utf-8")
    monkeypatch.setenv("MEDIAFORGE_METRICS_TOKEN_FILE", str(token_file))
    with pytest.raises(MetricsAccessConfigurationError, match="at least 16"):
        MetricsAccessPolicy.from_env()


def test_protected_metrics_accept_only_the_dedicated_file_token(monkeypatch, tmp_path) -> None:
    token = "test-only-metrics-token-123456"
    token_file = tmp_path / "metrics-token"
    token_file.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("MEDIAFORGE_METRICS_AUTH_MODE", "token")
    monkeypatch.setenv("MEDIAFORGE_METRICS_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv(
        "MEDIAFORGE_API_KEYS",
        '{"ordinary-api-key":{"subject":"operator","role":"admin"}}',
    )
    policy = MetricsAccessPolicy.from_env()
    assert token not in str(policy.status_view())

    with TestClient(create_app(output_root=tmp_path / "artifacts")) as client:
        assert client.get("/metrics").status_code == 401
        assert client.get(
            "/metrics", headers={"Authorization": "Bearer ordinary-api-key"}
        ).status_code == 401
        response = client.get(
            "/metrics", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert "mediaforge_process_uptime_seconds" in response.text
        assert client.get(
            "/metrics/runtime", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200
