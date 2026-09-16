from __future__ import annotations

import json
import sqlite3
import base64
import hashlib
import hmac
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.auth import AuthManager, AuthenticationError
from mediaforge_p1.contracts import CreativeBrief, JobStatus
from mediaforge_p1.enterprise_runtime import (
    EnterpriseConfigurationError,
    BillingLedger,
    EnterpriseRuntime,
    ObjectStorage,
    PostgresStateAdapter,
    RedisQueueAdapter,
)
from mediaforge_p1.oidc import OIDCIntrospector
from mediaforge_p1.service import MediaForgeService, WorkflowError
from mediaforge_p1.media import probe_audio


class MemoryRedis:
    def __init__(self):
        self.entries = {}
        self.unavailable = False
        self.fail_ack = False
        self.on_ack = lambda job_id: None

    def zadd(self, key, mapping, nx=False):
        if self.unavailable:
            raise ConnectionError("offline")
        bucket = self.entries.setdefault(key, {})
        for member, score in mapping.items():
            if not nx or member not in bucket:
                bucket[member] = score

    def zrange(self, key, start, end):
        return sorted(self.entries.get(key, {}), key=self.entries.get(key, {}).get)

    def zrem(self, key, member):
        self.on_ack(member)
        if self.fail_ack:
            raise ConnectionError("offline")
        self.entries.get(key, {}).pop(member, None)

    def ping(self):
        if self.unavailable:
            raise ConnectionError("offline")
        return True


def brief(project_id="enterprise_test", tenant_id="tenant_a"):
    return CreativeBrief(
        project_id=project_id, tenant_id=tenant_id, title="Future call",
        premise="A woman receives a call from her future self.",
        genre="suspense", style="cinematic", duration_seconds=30,
        budget=2.0, characters=["Alice", "Bob"],
    )


def test_voiceover_generation_registers_probeable_project_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_TTS_MODE", "deterministic")
    service = MediaForgeService(tmp_path)
    service.create_project(brief("voiceover_test"))

    project = service.generate_voiceover(
        "voiceover_test",
        text="这是一个可重复验收的中文配音片段。",
        voice="default",
    )

    audio_path = Path(project["audio_track"])
    assert audio_path.is_file()
    assert probe_audio(audio_path).valid is True
    assert project["audio_track_metadata"]["source"] == "deterministic-voice-preview"
    assert project["audio_track_metadata"]["preview_only"] is True


def test_redis_index_deduplicates_and_recovers_missing_entries(monkeypatch):
    redis = MemoryRedis()
    monkeypatch.setattr(RedisQueueAdapter, "_client", lambda self: redis)
    adapter = RedisQueueAdapter("redis://localhost")
    adapter.enqueue({"job_id": "a"})
    adapter.enqueue({"job_id": "a"})
    adapter.enqueue({"job_id": "stale"})
    assert adapter.ordered_pending(["a", "b"]) == ["a", "b"]
    adapter.acknowledge("a")
    assert adapter.ordered_pending(["b"]) == ["b"]
    redis.entries.clear()
    assert adapter.ordered_pending(["b"]) == ["b"]
    redis.unavailable = True
    with pytest.raises(EnterpriseConfigurationError, match="no jobs were claimed"):
        adapter.ordered_pending(["b"])


def test_redis_service_recovers_enqueue_and_serializes_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("MEDIAFORGE_QUEUE_BACKEND", "redis")
    redis = MemoryRedis()
    monkeypatch.setattr(RedisQueueAdapter, "_client", lambda self: redis)
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    plan = service.generate_plan("enterprise_test")
    shot_id = plan["shots"][0]["shot"]["shot_id"]
    redis.unavailable = True
    with pytest.raises(WorkflowError, match="durable job state is retained"):
        service.enqueue_shot("enterprise_test", shot_id)
    service = MediaForgeService(tmp_path)
    assert service.jobs.all()[0].status == JobStatus.QUEUED
    redis.unavailable = False
    service.enqueue_shot("enterprise_test", shot_id)
    service.register_worker("worker-a")
    service.register_worker("worker-b")

    def check_persisted_lease(job_id):
        restored = MediaForgeService(tmp_path)
        assert restored.jobs.get(job_id).status == JobStatus.ADMITTED

    redis.on_ack = check_persisted_lease
    redis.fail_ack = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.claim_worker_jobs, ["worker-a", "worker-b"]))
    claimed = [item for result in results for item in result["jobs"]]
    assert len(claimed) == 1
    assert any(result["queue_warnings"] for result in results)
    restored = MediaForgeService(tmp_path)
    assert restored.jobs.get(claimed[0]["job_id"]).worker_id == claimed[0]["worker_id"]


def test_postgres_service_uses_snapshot_backend_on_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "postgres")
    stored = {}
    monkeypatch.setattr(PostgresStateAdapter, "load", lambda self: stored.get("state"))
    monkeypatch.setattr(PostgresStateAdapter, "save", lambda self, payload: stored.update(state=payload))
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    service.generate_plan("enterprise_test")
    restored = MediaForgeService(tmp_path)
    assert restored.project_view("enterprise_test")["status"] == "PLANNED"
    assert len(json.loads(stored["state"])["projects"]) == 1
    assert not (tmp_path / "mediaforge-state.json").exists()
    assert not (tmp_path / "mediaforge-state.sqlite3").exists()


def test_worker_renews_live_leases_and_recovers_expired_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_RETRY_BASE_DELAY_SECONDS", "0")
    service = MediaForgeService(tmp_path)
    service.create_project(brief())
    plan = service.generate_plan("enterprise_test")
    service.enqueue_shot("enterprise_test", plan["shots"][0]["shot"]["shot_id"])
    service.register_worker("worker-a")
    service.register_worker("worker-b")
    claimed = service.claim_worker_jobs("worker-a")["jobs"][0]
    job = service.jobs.get(claimed["job_id"])
    job.events[-1] = replace(job.events[-1], occurred_at=datetime.now(timezone.utc) - timedelta(days=1))
    service.worker_heartbeat("worker-a", job_ids=[job.job_id])
    assert service.claim_worker_jobs("worker-b")["claimed_count"] == 0
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    recovered = service.claim_worker_jobs("worker-b")
    assert recovered["claimed_count"] == 1
    assert recovered["jobs"][0]["job_id"] == job.job_id
    assert job.worker_id == "worker-b"


def test_oidc_requires_token_and_tenant(monkeypatch):
    introspector = OIDCIntrospector("https://identity.example.test/introspect")
    auth = AuthManager(mode="oidc", credentials={}, oidc=introspector)
    with pytest.raises(AuthenticationError, match="required"):
        auth.authenticate(None)
    payload = {"sub": "producer", "roles": ["editor"], "tenant_id": "tenant_a"}
    monkeypatch.setattr(introspector, "introspect", lambda token: payload)
    principal = auth.authenticate("Bearer test-token")
    assert principal.authenticated and principal.tenant_id == "tenant_a"
    assert principal.role == "editor"
    payload.pop("tenant_id")
    with pytest.raises(AuthenticationError, match="tenant identifier"):
        auth.authenticate("Bearer test-token")
    payload["realm_access"] = []
    payload["roles"] = 123
    with pytest.raises(AuthenticationError, match="roles"):
        auth.authenticate("Bearer test-token")


def test_oidc_directory_group_mapping_supports_federated_claims(monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "oidc")
    monkeypatch.setenv("MEDIAFORGE_OIDC_INTROSPECTION_URL", "https://identity.example.test/introspect")
    monkeypatch.setenv("MEDIAFORGE_OIDC_ROLE_CLAIM", "groups")
    monkeypatch.setenv("MEDIAFORGE_OIDC_TENANT_CLAIM", "organization.id")
    monkeypatch.setenv(
        "MEDIAFORGE_OIDC_ROLE_MAPPING",
        json.dumps({"ad-mediaforge-publishers": "publisher"}),
    )
    auth = AuthManager.from_env()
    assert auth._oidc is not None
    monkeypatch.setattr(
        auth._oidc,
        "introspect",
        lambda _token: {
            "sub": "directory-user",
            "groups": ["ad-mediaforge-publishers"],
            "organization": {"id": "tenant_directory"},
        },
    )

    principal = auth.authenticate("Bearer federated-token")

    assert principal.subject == "directory-user"
    assert principal.role == "publisher"
    assert principal.tenant_id == "tenant_directory"
    assert auth.status_view()["directory_mapping"] == {
        "role_claim": "groups",
        "tenant_claim": "organization.id",
        "role_mapping_count": 1,
    }


def test_billing_migrates_legacy_ledger_and_scopes_event_ids(tmp_path):
    path = tmp_path / "billing.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE usage_event (event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT, category TEXT NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL, currency TEXT NOT NULL, occurred_at REAL NOT NULL, metadata TEXT NOT NULL)")
        connection.execute("INSERT INTO usage_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("shared-id", "tenant_a", None, "generation", 1, 0.2, "USD", 100, "{}"))
    ledger = BillingLedger(path)
    ledger.record(event_id="shared-id", tenant_id="tenant_b", category="generation", quantity=1, unit_price=0.4)
    duplicate = ledger.record(event_id="shared-id", tenant_id="tenant_a", category="generation", quantity=999, unit_price=999)
    assert duplicate["tenant_id"] == "tenant_a" and duplicate["amount"] == 0.2
    assert ledger.summary("tenant_b")["amount"] == 0.4
    assert len(BillingLedger(path).events()) == 2


def test_billing_does_not_add_different_currencies(tmp_path):
    ledger = BillingLedger(tmp_path / "billing.sqlite3")
    for currency in ("USD", "CNY"):
        ledger.record(event_id=currency, tenant_id="tenant_a", category="generation", quantity=1, unit_price=10, currency=currency)
    report = ledger.summary("tenant_a")
    assert report["amount"] is None and report["currency"] is None
    assert report["by_currency"]["USD"]["amount"] == 10
    assert report["by_currency"]["CNY"]["amount"] == 10
    with pytest.raises(EnterpriseConfigurationError, match="finite"):
        ledger.record(event_id="bad", tenant_id="tenant_a", category="generation", quantity=float("inf"), unit_price=1)


def test_object_storage_rejects_windows_and_posix_escape_keys(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"safe")
    storage = ObjectStorage(tmp_path)
    for key in ("../escape", "/absolute", "C:/escape", "C:relative", "a/./b", "a//b", "a/file:stream"):
        with pytest.raises(EnterpriseConfigurationError):
            storage.put(source, key)


def test_object_storage_immutable_write_reads_back_object_lock_retention(tmp_path, monkeypatch):
    source = tmp_path / "anchor.json"
    source.write_text('{"anchor":"verified"}', encoding="utf-8")
    requested: dict[str, object] = {}

    class FakeS3:
        def put_object(self, **kwargs):
            requested.update(kwargs)
            return {"VersionId": "version-001"}

        def get_object_retention(self, **_kwargs):
            return {
                "Retention": {
                    "Mode": requested["ObjectLockMode"],
                    "RetainUntilDate": requested["ObjectLockRetainUntilDate"],
                }
            }

    fake_s3 = FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_args, **_kwargs: fake_s3))
    storage = ObjectStorage(tmp_path, mode="s3", bucket="immutable-audit")
    retained_until = datetime.now(timezone.utc) + timedelta(days=30)

    stored = storage.put_immutable(
        source,
        "audit-anchors/project/anchor.json",
        retention_until=retained_until,
        object_lock_mode="COMPLIANCE",
    )

    assert requested["ObjectLockMode"] == "COMPLIANCE"
    assert requested["Bucket"] == "immutable-audit"
    assert stored["object_lock_verified"] is True
    assert stored["version_id"] == "version-001"
    assert stored["retention_mode"] == "COMPLIANCE"


def test_enterprise_probe_distinguishes_configuration_from_connectivity(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("MEDIAFORGE_REDIS_URL", "redis://localhost")
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "disabled")
    redis = MemoryRedis()
    monkeypatch.setattr(RedisQueueAdapter, "_client", lambda self: redis)
    runtime = EnterpriseRuntime(tmp_path)
    assert runtime.status_view()["queue"]["connectivity_verified"] is False
    assert runtime.status_view()["identity"]["production_ready"] is False
    assert runtime.probe()["reachable"] is True
    assert runtime.status_view()["queue"]["connectivity_verified"] is True
    redis.unavailable = True
    assert runtime.probe()["reachable"] is False
    assert runtime.status_view()["queue"]["production_ready"] is False


def test_billing_api_isolates_tenants_and_requires_ingestion_role(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({
        "key-a": {"subject": "a", "role": "admin", "tenant_id": "tenant_a"},
        "key-b": {"subject": "b", "role": "admin", "tenant_id": "tenant_b"},
        "key-editor": {"subject": "editor", "role": "editor", "tenant_id": "tenant_a"},
        "key-provider": {"subject": "provider", "role": "provider", "tenant_id": "tenant_a"},
    }))
    with TestClient(create_app(output_root=tmp_path)) as client:
        payload = {"event_id": "shared-id", "category": "usage", "quantity": 1, "unit_price": 0.1}
        assert client.post("/billing/events", json=payload).status_code == 401
        assert client.post("/providers/test-mutation", json={}).status_code == 401
        for token, tenant in (("key-a", "tenant_a"), ("key-b", "tenant_b")):
            response = client.post("/billing/events", json=payload, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            assert response.json()["event"]["tenant_id"] == tenant
            assert response.json()["summary"]["event_count"] == 1
        assert client.post("/billing/events", json=payload, headers={"Authorization": "Bearer key-editor"}).status_code == 403
        assert client.post("/billing/events", json=payload, headers={"Authorization": "Bearer key-provider"}).status_code == 200
        assert client.post("/enterprise/probe", headers={"Authorization": "Bearer key-editor"}).status_code == 403
        assert client.post("/enterprise/probe", headers={"Authorization": "Bearer key-a"}).status_code == 200
        payload["project_id"] = "missing"
        assert client.post("/billing/events", json=payload, headers={"Authorization": "Bearer key-a"}).status_code == 404


def test_settlement_api_is_idempotent_and_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({
        "admin-a": {"subject": "a", "role": "admin", "tenant_id": "tenant_a"},
        "viewer-a": {"subject": "viewer", "role": "viewer", "tenant_id": "tenant_a"},
        "admin-b": {"subject": "b", "role": "admin", "tenant_id": "tenant_b"},
    }))
    with TestClient(create_app(output_root=tmp_path)) as client:
        admin_a = {"Authorization": "Bearer admin-a"}
        admin_b = {"Authorization": "Bearer admin-b"}
        viewer_a = {"Authorization": "Bearer viewer-a"}
        payload = {"settlement_id": "checkout-001", "provider": "stripe", "status": "pending", "amount": 12.5, "currency": "USD", "external_id": "pi_001"}
        first = client.post("/billing/settlements", json=payload, headers=admin_a)
        duplicate = client.post("/billing/settlements", json={**payload, "metadata": {"replayed": True}}, headers=admin_a)
        assert first.status_code == duplicate.status_code == 200
        assert duplicate.json()["settlement"]["amount"] == 12.5
        paid = client.post("/billing/settlements", json={**payload, "status": "paid"}, headers=admin_a)
        assert paid.status_code == 200 and paid.json()["settlement"]["status"] == "paid"
        assert client.post("/billing/settlements", json=payload, headers=viewer_a).status_code == 403
        assert client.get("/billing/settlements", headers=admin_b).json()["total"] == 0
        assert client.get("/billing/settlements", headers=admin_a).json()["settlements"][0]["tenant_id"] == "tenant_a"
        assert client.get("/billing/settlements/summary", headers=admin_a).json()["by_currency"]["USD"]["by_status"]["paid"] == 12.5
        assert client.get("/billing/settlements?status=bad", headers=admin_a).status_code == 422


def test_signed_settlement_callback_is_idempotent_and_does_not_require_an_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({
        "admin-a": {"subject": "a", "role": "admin", "tenant_id": "tenant_a"},
    }))
    monkeypatch.setenv("MEDIAFORGE_SETTLEMENT_CALLBACK_SECRET", "callback-secret")
    monkeypatch.setenv("MEDIAFORGE_SETTLEMENT_CALLBACK_ALLOWED_PROVIDERS", "stripe,erp")
    payload = {
        "tenant_id": "tenant_a",
        "settlement_id": "gateway-001",
        "provider": "stripe",
        "status": "paid",
        "amount": 12.5,
        "currency": "USD",
        "external_id": "payment-001",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"callback-secret",
        f"{timestamp}.".encode("utf-8") + raw,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-MediaForge-Timestamp": timestamp,
        "X-MediaForge-Signature": f"sha256={signature}",
    }
    with TestClient(create_app(output_root=tmp_path)) as client:
        assert client.post("/billing/settlements/callback", content=raw).status_code == 401
        first = client.post("/billing/settlements/callback", content=raw, headers=headers)
        duplicate = client.post("/billing/settlements/callback", content=raw, headers=headers)
        assert first.status_code == duplicate.status_code == 200
        assert first.json()["settlement"]["tenant_id"] == "tenant_a"
        assert duplicate.json()["summary"]["by_currency"]["USD"]["by_status"]["paid"] == 12.5

        invalid_provider = {**payload, "settlement_id": "gateway-002", "provider": "unknown"}
        invalid_raw = json.dumps(invalid_provider, separators=(",", ":")).encode("utf-8")
        invalid_signature = hmac.new(
            b"callback-secret",
            f"{timestamp}.".encode("utf-8") + invalid_raw,
            hashlib.sha256,
        ).hexdigest()
        assert client.post(
            "/billing/settlements/callback",
            content=invalid_raw,
            headers={**headers, "X-MediaForge-Signature": f"sha256={invalid_signature}"},
        ).status_code == 422

        status = client.get(
            "/billing/settlements/callback/status",
            headers={"Authorization": "Bearer admin-a"},
        )
        assert status.status_code == 200
        assert status.json()["allowed_providers"] == ["stripe", "erp"]


def test_native_stripe_settlement_webhook_verifies_raw_body_and_payout_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({
        "admin-a": {"subject": "a", "role": "admin", "tenant_id": "tenant_a"},
    }))
    monkeypatch.setenv("MEDIAFORGE_STRIPE_SETTLEMENT_WEBHOOK_SECRET", "whsec-test-secret")
    timestamp = int(time.time())
    payload = {
        "id": "evt_payout_paid",
        "type": "payout.paid",
        "created": timestamp,
        "livemode": False,
        "data": {"object": {
            "id": "po_123",
            "object": "payout",
            "amount": 1234,
            "currency": "usd",
            "arrival_date": timestamp + 3600,
            "metadata": {"mediaforge_tenant_id": "tenant_a"},
        }},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        b"whsec-test-secret",
        f"{timestamp}.".encode("ascii") + raw,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Stripe-Signature": f"t={timestamp},v1={signature}",
    }
    with TestClient(create_app(output_root=tmp_path)) as client:
        assert client.post("/billing/settlements/stripe", content=raw).status_code == 401
        first = client.post("/billing/settlements/stripe", content=raw, headers=headers)
        duplicate = client.post("/billing/settlements/stripe", content=raw, headers=headers)
        assert first.status_code == duplicate.status_code == 200
        assert first.json()["settlement"] == {
            "settlement_id": "stripe:po_123",
            "tenant_id": "tenant_a",
            "provider": "stripe",
            "status": "paid",
            "amount": 12.34,
            "currency": "USD",
            "external_id": "evt_payout_paid",
            "occurred_at": first.json()["settlement"]["occurred_at"],
            "metadata": {
                "stripe_event_id": "evt_payout_paid",
                "stripe_event_type": "payout.paid",
                "stripe_payout_id": "po_123",
                "stripe_livemode": False,
                "stripe_event_created": timestamp,
                "stripe_arrival_date": timestamp + 3600,
            },
        }
        assert client.post(
            "/billing/settlements/stripe",
            content=raw,
            headers={**headers, "Stripe-Signature": f"t={timestamp},v1={'0' * 64}"},
        ).status_code == 401
        missing_tenant = json.dumps({
            **payload,
            "id": "evt_payout_missing_tenant",
            "data": {"object": {**payload["data"]["object"], "id": "po_124", "metadata": {}}},
        }, separators=(",", ":")).encode("utf-8")
        missing_signature = hmac.new(
            b"whsec-test-secret",
            f"{timestamp}.".encode("ascii") + missing_tenant,
            hashlib.sha256,
        ).hexdigest()
        assert client.post(
            "/billing/settlements/stripe",
            content=missing_tenant,
            headers={**headers, "Stripe-Signature": f"t={timestamp},v1={missing_signature}"},
        ).status_code == 422
        status = client.get(
            "/billing/settlements/stripe/status",
            headers={"Authorization": "Bearer admin-a"},
        )
        assert status.status_code == 200
        assert status.json()["endpoint_secret_configured"] is True
        assert status.json()["allowed_event_types"] == ["payout.paid", "payout.failed", "payout.canceled"]


def test_oidc_browser_login_uses_pkce_session_and_logout(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "oidc")
    monkeypatch.delenv("MEDIAFORGE_API_KEYS", raising=False)
    monkeypatch.setenv("MEDIAFORGE_OIDC_INTROSPECTION_URL", "https://identity.example.test/introspect")
    monkeypatch.setenv("MEDIAFORGE_OIDC_CLIENT_ID", "mediaforge-client")
    monkeypatch.setenv("MEDIAFORGE_OIDC_AUTHORIZATION_URL", "https://identity.example.test/authorize")
    monkeypatch.setenv("MEDIAFORGE_OIDC_TOKEN_URL", "https://identity.example.test/token")
    monkeypatch.setenv("MEDIAFORGE_OIDC_REDIRECT_URI", "http://localhost/auth/callback")
    monkeypatch.setenv("MEDIAFORGE_OIDC_SCOPE", "openid profile email")
    app = create_app(output_root=tmp_path)
    auth = app.state.auth_manager
    monkeypatch.setattr(auth._oidc, "exchange_authorization_code", lambda *args, **kwargs: {"access_token": "session-token", "expires_in": 600})
    monkeypatch.setattr(auth._oidc, "introspect", lambda token: {"active": True, "sub": "browser-user", "tenant_id": "tenant_a", "roles": ["editor"]})
    with TestClient(app) as client:
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 303
        query = parse_qs(urlparse(login.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["client_id"] == ["mediaforge-client"]
        callback = client.get(f"/auth/callback?code=one-time-code&state={query['state'][0]}", follow_redirects=False)
        assert callback.status_code == 303
        assert "mediaforge_session=" in callback.headers["set-cookie"]
        assert client.get("/auth/me").json()["subject"] == "browser-user"
        assert client.post("/auth/logout").json() == {"logged_out": True}
        assert client.get("/auth/me").status_code == 401


def test_worker_registration_heartbeat_and_listing_are_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({
        "tenant-a": {"subject": "worker-a", "role": "provider", "tenant_id": "tenant_a"},
        "tenant-b": {"subject": "worker-b", "role": "provider", "tenant_id": "tenant_b"},
    }))
    a, b = {"Authorization": "Bearer tenant-a"}, {"Authorization": "Bearer tenant-b"}
    with TestClient(create_app(output_root=tmp_path)) as client:
        assert client.post("/workers/register", json={"worker_id": "owned-worker"}, headers=a).status_code == 200
        assert client.get("/workers", headers=a).json()["worker_count"] == 1
        assert client.get("/workers", headers=b).json()["worker_count"] == 0
        assert client.post("/workers/register", json={"worker_id": "owned-worker"}, headers=b).status_code == 422
        assert client.post("/workers/owned-worker/heartbeat", json={}, headers=b).status_code == 422
        assert client.post("/workers/owned-worker/claim", json={}, headers=b).status_code == 422
        assert client.post("/workers/owned-worker/heartbeat", json={}, headers=a).status_code == 200
        assert client.app.state.mediaforge.workers["owned-worker"]["tenant_id"] == "tenant_a"


def test_provider_process_requires_its_worker_and_valid_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("MEDIAFORGE_API_KEYS", json.dumps({"provider": {"subject": "worker", "role": "provider", "tenant_id": "tenant_a"}}))
    with TestClient(create_app(output_root=tmp_path), headers={"Authorization": "Bearer provider"}) as client:
        service = client.app.state.mediaforge
        service.create_project(brief())
        plan = service.generate_plan("enterprise_test")
        queued = service.enqueue_shot("enterprise_test", plan["shots"][0]["shot"]["shot_id"])
        path = f"/projects/enterprise_test/jobs/{queued['current_job_id']}/process"
        assert client.post(path).status_code == 403
        assert client.post("/workers/register", json={"worker_id": "worker-a"}).status_code == 200
        assert client.post(path + "?worker_id=worker-a").status_code == 422
        claim = client.post("/workers/worker-a/claim", json={})
        assert claim.json()["claimed_count"] == 1
        processed = client.post(path + "?worker_id=worker-a")
        assert processed.status_code == 200
        assert processed.json()["job"]["status"] == "SUCCEEDED"
        assert client.post("/projects/enterprise_test/plan").status_code == 403


def test_oidc_http_introspection_checks_active_and_client_credentials(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            token = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())["token"][0]
            calls.append(self.headers.get("Authorization"))
            body = json.dumps({"active": token == "active-token", "sub": "producer", "tenant_id": "tenant_a", "roles": ["editor"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "oidc")
        monkeypatch.delenv("MEDIAFORGE_API_KEYS", raising=False)
        monkeypatch.setenv("MEDIAFORGE_OIDC_INTROSPECTION_URL", f"http://127.0.0.1:{server.server_port}/introspect")
        monkeypatch.setenv("MEDIAFORGE_OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("MEDIAFORGE_OIDC_CLIENT_SECRET", "test-secret")
        auth = AuthManager.from_env()
        assert auth.authenticate("Bearer active-token").tenant_id == "tenant_a"
        with pytest.raises(AuthenticationError, match="inactive"):
            auth.authenticate("Bearer expired-token")
        assert calls == ["Basic " + base64.b64encode(b"test-client:test-secret").decode()] * 2
        assert "test-secret" not in json.dumps(auth.status_view())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_rate_limit_returns_retry_headers_and_isolates_anonymous_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "disabled")
    monkeypatch.setenv("MEDIAFORGE_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("MEDIAFORGE_RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("MEDIAFORGE_RATE_LIMIT_READ_REQUESTS", "3")
    app = create_app(output_root=tmp_path)
    with TestClient(app, client=("client-a", 1000)) as first:
        for _ in range(3):
            assert first.get("/providers/status").status_code == 200
        read_rejected = first.get("/providers/status")
        assert read_rejected.status_code == 429
        assert read_rejected.headers["X-RateLimit-Limit"] == "3"
        assert first.get("/health").status_code == 200
        assert first.get("/health").headers["X-RateLimit-Remaining"] == "0"
        rejected = first.get("/health")
        assert rejected.status_code == 429
        assert int(rejected.headers["Retry-After"]) > 0
    with TestClient(app, client=("client-b", 1001)) as second:
        assert second.get("/health").status_code == 200
