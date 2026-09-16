from __future__ import annotations

import os
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.enterprise_runtime import (
    ControlPlaneLeaseSettings,
    EnterpriseConfigurationError,
    ObjectStorage,
    PostgresControlPlaneLease,
    PostgresStateAdapter,
    RedisQueueAdapter,
)
from mediaforge_p1.sessions import PostgresBrowserSessionStore
from mediaforge_p1.service import MediaForgeService
from mediaforge_p1.worker import run_remote_worker


def endpoint(name):
    value = os.getenv(name)
    if not value:
        pytest.skip(f"set {name} to run the real-service acceptance test")
    return value


def test_postgres_roundtrip_conflict_and_service_restart(tmp_path, monkeypatch):
    url = endpoint("MEDIAFORGE_TEST_POSTGRES_URL")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    table = "mediaforge_test_" + uuid4().hex
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "postgres")
    monkeypatch.setenv("MEDIAFORGE_DATABASE_URL", url)
    monkeypatch.setenv("MEDIAFORGE_STATE_TABLE", table)
    try:
        service = MediaForgeService(tmp_path)
        service.create_project(CreativeBrief(
            project_id="database_acceptance", title="Future call",
            premise="A woman receives a call from her future self.",
            genre="suspense", style="cinematic", duration_seconds=30,
            budget=2, characters=["Alice", "Bob"],
        ))
        service.generate_plan("database_acceptance")
        restored = MediaForgeService(tmp_path)
        assert restored.project_view("database_acceptance")["status"] == "PLANNED"
        first = PostgresStateAdapter(url, table)
        stale = PostgresStateAdapter(url, table)
        original = first.load()
        assert stale.load() == original
        first.save(original)
        with pytest.raises(EnterpriseConfigurationError, match="another process"):
            stale.save("{}")
        assert PostgresStateAdapter(url, table).load() == original
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))


def test_postgres_leased_control_plane_has_one_active_writer():
    url = endpoint("MEDIAFORGE_TEST_POSTGRES_URL")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    table = "mediaforge_lease_" + uuid4().hex
    first = PostgresControlPlaneLease(
        PostgresStateAdapter(url),
        ControlPlaneLeaseSettings(
            mode="leased", holder_id="control-plane-a", lease_seconds=5, table=table,
        ),
    )
    second = PostgresControlPlaneLease(
        PostgresStateAdapter(url),
        ControlPlaneLeaseSettings(
            mode="leased", holder_id="control-plane-b", lease_seconds=5, table=table,
        ),
    )
    try:
        assert first.acquire_or_renew() is True
        assert second.acquire_or_renew() is False
        assert first.status_view()["ready_for_traffic"] is True
        assert second.status_view()["ready_for_traffic"] is False
        first.release()
        assert second.acquire_or_renew() is True
    finally:
        second.release()
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))


def test_postgres_oidc_session_store_survives_cross_instance_read():
    url = endpoint("MEDIAFORGE_TEST_POSTGRES_URL")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    table = "mediaforge_oidc_session_" + uuid4().hex
    first = PostgresBrowserSessionStore(url, table)
    second = PostgresBrowserSessionStore(url, table)
    try:
        first.create_state("one-time-state", "pkce-verifier", time.time() + 60)
        assert second.consume_state("one-time-state") == "pkce-verifier"
        assert first.consume_state("one-time-state") is None
        first.create_session("session-id", {
            "subject": "browser-user", "role": "editor", "tenant_id": "tenant_a", "authenticated": True,
        }, time.time() + 60)
        assert second.get_session("session-id")["subject"] == "browser-user"
        second.delete_session("session-id")
        assert first.get_session("session-id") is None
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))


def test_postgres_leased_service_failover_reloads_latest_snapshot(tmp_path, monkeypatch):
    url = endpoint("MEDIAFORGE_TEST_POSTGRES_URL")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    suffix = uuid4().hex
    state_table = "mediaforge_state_" + suffix
    lease_table = "mediaforge_lease_" + suffix
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "postgres")
    monkeypatch.setenv("MEDIAFORGE_DATABASE_URL", url)
    monkeypatch.setenv("MEDIAFORGE_STATE_TABLE", state_table)
    monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_MODE", "leased")
    monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_TABLE", lease_table)
    monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_LEASE_SECONDS", "5")
    try:
        monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_ID", "primary-a")
        primary = MediaForgeService(tmp_path / "primary")
        assert primary.control_plane_status(acquire=True)["primary"] is True
        primary.create_project(CreativeBrief(
            project_id="failover_project", title="Failover project",
            premise="A standby should reload the latest durable project state.",
            genre="drama", style="cinema", duration_seconds=30,
            budget=2, characters=["Alice", "Bob"],
        ))
        monkeypatch.setenv("MEDIAFORGE_CONTROL_PLANE_ID", "standby-b")
        standby = MediaForgeService(tmp_path / "standby")
        assert standby.control_plane_status(acquire=True)["primary"] is False
        primary.release_control_plane()
        assert standby.control_plane_status(acquire=True)["primary"] is True
        assert standby.project_view("failover_project")["brief"]["title"] == "Failover project"
    finally:
        try:
            primary.release_control_plane()
        except UnboundLocalError:
            pass
        try:
            standby.release_control_plane()
        except UnboundLocalError:
            pass
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(state_table)))
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(lease_table)))


def test_redis_real_index_recovers_and_acknowledges():
    url = endpoint("MEDIAFORGE_TEST_REDIS_URL")
    pytest.importorskip("redis")
    queue = RedisQueueAdapter(url, "mediaforge-test:" + uuid4().hex)
    try:
        queue.enqueue({"job_id": "first"})
        queue.enqueue({"job_id": "first"})
        assert queue.ordered_pending(["first", "second"]) == ["first", "second"]
        queue.acknowledge("first")
        assert queue._client().zrange(queue.queue_name + ":ready", 0, -1) == ["second"]
        queue._client().delete(queue.queue_name + ":ready")
        assert queue.ordered_pending(["second"]) == ["second"]
    finally:
        queue._client().delete(queue.queue_name + ":ready")


def test_s3_real_upload_preserves_bytes(tmp_path, monkeypatch):
    url = endpoint("MEDIAFORGE_TEST_S3_ENDPOINT")
    boto3 = pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", os.getenv("MEDIAFORGE_TEST_S3_ACCESS_KEY", "mediaforge_test"))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", os.getenv("MEDIAFORGE_TEST_S3_SECRET_KEY", "local-test-only"))
    bucket = "mediaforge-test-" + uuid4().hex
    client = boto3.client("s3", endpoint_url=url)
    client.create_bucket(Bucket=bucket)
    source = tmp_path / "acceptance.bin"
    source.write_bytes(b"mediaforge-object-storage-acceptance")
    try:
        stored = ObjectStorage(tmp_path, mode="s3", endpoint=url, bucket=bucket).put(source, "tenant/project/acceptance.bin")
        assert stored["uri"] == f"s3://{bucket}/tenant/project/acceptance.bin"
        body = client.get_object(Bucket=bucket, Key=stored["key"])["Body"]
        try:
            assert body.read() == source.read_bytes()
        finally:
            body.close()
    finally:
        client.delete_object(Bucket=bucket, Key="tenant/project/acceptance.bin")
        client.delete_bucket(Bucket=bucket)


def test_enterprise_worker_to_delivery_closed_loop(tmp_path, monkeypatch):
    pg_url = endpoint("MEDIAFORGE_TEST_POSTGRES_URL")
    redis_url = endpoint("MEDIAFORGE_TEST_REDIS_URL")
    s3_url = endpoint("MEDIAFORGE_TEST_S3_ENDPOINT")
    psycopg = pytest.importorskip("psycopg")
    boto3 = pytest.importorskip("boto3")
    from psycopg import sql

    suffix = uuid4().hex
    table, bucket, queue_name = "mediaforge_test_" + suffix, "mediaforge-test-" + suffix, "mediaforge-test:" + suffix
    settings = {
        "MEDIAFORGE_PROVIDER": "mock", "MEDIAFORGE_STATE_BACKEND": "postgres",
        "MEDIAFORGE_DATABASE_URL": pg_url, "MEDIAFORGE_STATE_TABLE": table,
        "MEDIAFORGE_QUEUE_BACKEND": "redis", "MEDIAFORGE_REDIS_URL": redis_url,
        "MEDIAFORGE_REDIS_QUEUE": queue_name, "MEDIAFORGE_STORAGE_MODE": "s3",
        "MEDIAFORGE_STORAGE_ENDPOINT": s3_url, "MEDIAFORGE_STORAGE_BUCKET": bucket,
        "MEDIAFORGE_DELIVERY_MODE": "local", "MEDIAFORGE_AUTH_MODE": "required",
        "MEDIAFORGE_API_KEYS": json.dumps({"test-only-token": {"subject": "qa", "role": "admin", "tenant_id": "tenant_a"}, "worker-only-token": {"subject": "worker", "role": "provider", "tenant_id": "tenant_a"}}),
        "AWS_ACCESS_KEY_ID": os.getenv("MEDIAFORGE_TEST_S3_ACCESS_KEY", "mediaforge_test"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("MEDIAFORGE_TEST_S3_SECRET_KEY", "local-test-only"),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MEDIAFORGE_PROVIDERS", raising=False)
    s3 = boto3.client("s3", endpoint_url=s3_url)
    s3.create_bucket(Bucket=bucket)
    project_id = "enterprise_closed_loop"
    try:
        with TestClient(create_app(output_root=tmp_path), headers={"Authorization": "Bearer test-only-token"}) as client:
            def post(path, body=None):
                response = client.post(path, json=body)
                response.raise_for_status()
                return response.json()

            assert post("/enterprise/probe")["reachable"] is True
            readiness = client.get("/ops/readiness").json()
            assert next(check for check in readiness["checks"] if check["code"] == "enterprise_runtime")["passed"] is True
            assert readiness["production_ready"] is False
            post("/projects", {
                "project_id": project_id, "tenant_id": "tenant_a", "title": "Future call",
                "premise": "A woman receives a call from her future self.",
                "genre": "suspense", "style": "cinematic", "duration_seconds": 30,
                "budget": 2, "characters": ["Alice", "Bob"],
            })
            plan = post(f"/projects/{project_id}/plan")
            post(f"/projects/{project_id}/shots/enqueue-all")

            def request(base_url, method, path, body=None, *, token=None):
                response = client.request(method, path, json=body, headers={"Authorization": "Bearer worker-only-token"})
                response.raise_for_status()
                return response.json()

            result = run_remote_worker(base_url="http://testserver", worker_id="acceptance-worker", concurrency=6, limit=6, once=True, request_fn=request)
            assert result["processed"] == 6 and result["failed"] == 0
            for shot in plan["shots"]:
                post(f"/projects/{project_id}/shots/{shot['shot']['shot_id']}/review", {"status": "APPROVED"})
            post(f"/projects/{project_id}/export")
            post(f"/projects/{project_id}/package")
            post(f"/projects/{project_id}/release", {"channel": "acceptance"})
            delivered = post(f"/projects/{project_id}/deliveries/dispatch", {})
            stored = delivered["dispatch"]["storage_object"]
            assert stored["uri"].startswith(f"s3://{bucket}/tenant_a/{project_id}/")
            assert s3.head_object(Bucket=bucket, Key=stored["key"])["ContentLength"] == stored["size"]
            post(f"/projects/{project_id}/deliveries/{delivered['delivery']['delivery_id']}/acknowledge", {"accepted": True})
            assert client.get("/billing/summary").json()["event_count"] == 6
        restored = MediaForgeService(tmp_path)
        delivery = restored.projects[project_id].deliveries[-1]
        assert delivery["status"] == "ACCEPTED"
        assert delivery["dispatch"]["storage_object"] == stored
    finally:
        with psycopg.connect(pg_url) as connection:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))
        RedisQueueAdapter(redis_url, queue_name)._client().delete(queue_name + ":ready")
        for item in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=item["Key"])
        s3.delete_bucket(Bucket=bucket)
