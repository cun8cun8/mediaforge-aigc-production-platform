from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from typing import Any, Mapping

from .temporal_orchestration import (
    TemporalConfigurationError,
    TemporalOperationRequest,
    TemporalOrchestrator,
    TemporalOrchestrationError,
    TemporalOrchestrationSettings,
)
from .temporal_workflows import MediaForgeProductionWorkflow


class ControlPlaneHttpError(TemporalOrchestrationError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class TemporalControlPlaneClient:
    def __init__(self, settings: TemporalOrchestrationSettings) -> None:
        if not settings.control_plane_url:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL is required by the worker"
            )
        self.settings = settings
        self.base_url = settings.control_plane_url
        self.token = ""
        if settings.control_plane_token_file:
            try:
                self.token = Path(settings.control_plane_token_file).read_text(
                    encoding="utf-8"
                ).strip()
            except OSError as exc:
                raise TemporalConfigurationError(
                    "cannot read MEDIAFORGE_TEMPORAL_CONTROL_PLANE_TOKEN_FILE"
                ) from exc

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        headers.update(extra_headers or {})
        request = UrlRequest(
            f"{self.base_url}{path}",
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(
                request, timeout=self.settings.activity_timeout_seconds
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise ControlPlaneHttpError(
                exc.code, f"MediaForge API returned {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise TemporalOrchestrationError(f"MediaForge API connection failed: {exc.reason}") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TemporalOrchestrationError("MediaForge API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise TemporalOrchestrationError("MediaForge API returned a non-object response")
        return result

    def invoke(self, request: Mapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation", ""))
        project_id = str(request.get("project_id", ""))
        actor = str(request.get("actor", "temporal-worker"))
        if operation == "generation":
            shot_id = str(request.get("shot_id", ""))
            result = self._request(
                "POST",
                f"/projects/{project_id}/shots/{shot_id}/submit",
            )
        elif operation == "render":
            result = self._request("POST", f"/projects/{project_id}/export")
        elif operation == "package":
            result = self._request("POST", f"/projects/{project_id}/package")
        elif operation == "dispatch":
            delivery = dict(request.get("delivery") or {})
            delivery["actor"] = actor
            result = self._request(
                "POST",
                f"/projects/{project_id}/deliveries/dispatch",
                delivery,
                {"Idempotency-Key": f"temporal-{request['request_id']}"},
            )
        else:
            raise TemporalConfigurationError(
                f"unsupported Temporal operation: {operation}"
            )
        result["temporal_request_id"] = request.get("request_id")
        result["temporal_operation"] = operation
        return result

    def heartbeat(
        self,
        *,
        worker_id: str,
        active_operations: int,
        completed_operations: int,
        failed_operations: int,
        version: str,
    ) -> dict[str, Any]:
        """Report Worker liveness without granting general control-plane access."""
        return self._request(
            "POST",
            "/orchestration/temporal/workers/heartbeat",
            {
                "worker_id": worker_id,
                "task_queue": self.settings.task_queue,
                "namespace": self.settings.namespace,
                "version": version,
                "active_operations": active_operations,
                "completed_operations": completed_operations,
                "failed_operations": failed_operations,
            },
        )


def build_worker_settings(args: argparse.Namespace) -> TemporalOrchestrationSettings:
    settings = TemporalOrchestrationSettings.from_env()
    overrides: dict[str, Any] = {}
    for field_name, value in {
        "address": args.address,
        "namespace": args.namespace,
        "task_queue": args.task_queue,
        "control_plane_url": args.control_plane_url,
        "control_plane_token_file": args.token_file,
    }.items():
        if value:
            overrides[field_name] = value
    if args.tls:
        overrides["tls"] = True
    if overrides:
        settings = replace(settings, enabled=True, **overrides)
        settings.validate()
    if not settings.enabled:
        raise TemporalConfigurationError(
            "set MEDIAFORGE_TEMPORAL_ENABLED=true before starting the worker"
        )
    return settings


async def run_temporal_worker(settings: TemporalOrchestrationSettings) -> None:
    try:
        from temporalio import activity
        from temporalio.client import Client
        from temporalio.exceptions import ApplicationError
        from temporalio.worker import Worker
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise TemporalOrchestrationError(
            "install the optional Temporal dependency with pip install -e .[temporal]"
        ) from exc

    control_plane = TemporalControlPlaneClient(settings)
    worker_id = (
        os.getenv("MEDIAFORGE_TEMPORAL_WORKER_ID", "").strip()
        or socket.gethostname().strip()
        or "temporal-worker"
    )
    worker_version = (
        os.getenv("MEDIAFORGE_BUILD_VERSION", "").strip()
        or os.getenv("MEDIAFORGE_VERSION", "").strip()
        or "unknown"
    )
    active_operations = 0
    completed_operations = 0
    failed_operations = 0

    async def send_heartbeat() -> dict[str, Any]:
        return await asyncio.to_thread(
            control_plane.heartbeat,
            worker_id=worker_id,
            active_operations=active_operations,
            completed_operations=completed_operations,
            failed_operations=failed_operations,
            version=worker_version,
        )

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(settings.worker_heartbeat_seconds)
            try:
                await send_heartbeat()
            except TemporalOrchestrationError:
                # A temporary API outage must not interrupt active Temporal Activities.
                # The control plane will expose this Worker as stale until it recovers.
                continue

    # Startup is deliberately fail-fast: a Worker without a reachable, authenticated
    # control plane cannot safely run MediaForge Activities.
    await send_heartbeat()
    client = await Client.connect(
        settings.address,
        namespace=settings.namespace,
        tls=settings.tls_config(),
    )

    @activity.defn(name="mediaforge.execute_operation")
    async def execute_operation(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal active_operations, completed_operations, failed_operations
        active_operations += 1
        activity.heartbeat({"operation": request.get("operation"), "phase": "api_request"})
        try:
            validated = TemporalOperationRequest(
                project_id=str(request["project_id"]),
                operation=str(request["operation"]),
                request_id=str(request["request_id"]),
                shot_id=request.get("shot_id"),
                delivery=request.get("delivery"),
                actor=str(request.get("actor", "temporal-worker")),
                activity_timeout_seconds=int(request.get("activity_timeout_seconds", 3600)),
                retry_max_attempts=int(request.get("retry_max_attempts", 3)),
                metadata=request.get("metadata") or {},
            )
            result = await asyncio.to_thread(control_plane.invoke, validated.to_payload())
        except ControlPlaneHttpError as exc:
            failed_operations += 1
            if 400 <= exc.status_code < 500:
                raise ApplicationError(str(exc), non_retryable=True) from exc
            raise
        except Exception:
            failed_operations += 1
            raise
        finally:
            active_operations = max(active_operations - 1, 0)
        completed_operations += 1
        activity.heartbeat({"operation": request.get("operation"), "phase": "completed"})
        return result

    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[MediaForgeProductionWorkflow],
        activities=[execute_operation],
    )
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        await worker.run()
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


def check_temporal_worker(settings: TemporalOrchestrationSettings) -> dict[str, Any]:
    """Perform the readiness checks used by the Worker deployment.

    A readiness check deliberately validates both ends of the activity boundary:
    Temporal must accept a client connection and the Worker must have a usable
    control-plane endpoint. It does not submit a production operation.
    """
    temporal = TemporalOrchestrator(settings).probe()
    control_plane = TemporalControlPlaneClient(settings)._request("GET", "/health")
    return {
        "ready": bool(temporal.get("connected")),
        "temporal": temporal,
        "control_plane": control_plane,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MediaForge Temporal Worker")
    parser.add_argument("--address")
    parser.add_argument("--namespace")
    parser.add_argument("--task-queue")
    parser.add_argument("--control-plane-url")
    parser.add_argument("--token-file")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify Temporal and control-plane connectivity, then exit",
    )
    args = parser.parse_args()
    try:
        settings = build_worker_settings(args)
        if args.check:
            print(json.dumps(check_temporal_worker(settings), ensure_ascii=True))
            return
        asyncio.run(run_temporal_worker(settings))
    except (TemporalConfigurationError, TemporalOrchestrationError) as exc:
        parser.exit(1, f"Temporal Worker failed: {exc}\n")


if __name__ == "__main__":  # pragma: no cover
    main()
