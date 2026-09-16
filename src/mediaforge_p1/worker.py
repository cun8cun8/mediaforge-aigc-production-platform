from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import platform
import subprocess
from pathlib import Path
from socket import gethostname
from threading import Event, Lock, Thread
from time import sleep, time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .callback_security import callback_signature
from .config import build_provider_bundles_from_env
from .contracts import Capability, GenerationSpec
from .router import ProviderRegistration
from .service import MediaForgeService


class RemoteWorkerError(RuntimeError):
    """Raised when the remote Worker control API cannot be reached."""


RequestFn = Callable[..., dict[str, Any]]
CallbackFn = Callable[..., dict[str, Any]]


def probe_gpu_resources(
    *,
    command_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Return best-effort, vendor-neutral-enough GPU telemetry from nvidia-smi."""
    runner = command_runner or subprocess.run
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = runner(command, capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "gpu_probe": {"status": "unavailable", "source": "nvidia-smi", "message": str(exc)},
            "gpus": [],
        }
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "nvidia-smi failed").strip()[:500]
        return {
            "gpu_probe": {"status": "unavailable", "source": "nvidia-smi", "message": message},
            "gpus": [],
        }
    gpus: list[dict[str, Any]] = []
    for row in (result.stdout or "").splitlines():
        parts = [item.strip() for item in row.split(",")]
        if len(parts) != 6:
            continue
        try:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "driver_version": parts[2],
                    "memory_total_mib": int(float(parts[3])),
                    "memory_free_mib": int(float(parts[4])),
                    "utilization_percent": int(float(parts[5])),
                }
            )
        except ValueError:
            continue
    return {
        "gpu_probe": {
            "status": "available" if gpus else "unavailable",
            "source": "nvidia-smi",
            "message": None if gpus else "nvidia-smi returned no parseable GPU rows",
        },
        "gpus": gpus,
    }


def worker_resource_snapshot(
    *,
    execution_mode: str = "api-provider-dispatch",
) -> dict[str, Any]:
    """Build the resource contract advertised to the control plane."""
    return {
        "hostname": gethostname(),
        "os": platform.system(),
        "cpu_count": os.cpu_count(),
        "execution_mode": execution_mode,
        **probe_gpu_resources(),
    }


def remote_request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call one JSON endpoint without adding another HTTP dependency."""
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "mediaforge-worker/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RemoteWorkerError(
            f"{method.upper()} {path} failed: HTTP {exc.code} {detail}"
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RemoteWorkerError(f"{method.upper()} {path} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RemoteWorkerError(f"{method.upper()} {path} returned a non-object JSON payload")
    return payload


def remote_provider_callback_json(
    base_url: str,
    path: str,
    body: dict[str, Any],
    *,
    token: str | None,
    callback_secret: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Send a Worker-produced result through the normal signed callback API."""
    data = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time()))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "mediaforge-worker/0.1",
        "X-MediaForge-Timestamp": timestamp,
        "X-MediaForge-Signature": callback_signature(
            callback_secret,
            timestamp=timestamp,
            method="POST",
            path=path,
            body=data,
        ),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RemoteWorkerError(
            f"POST {path} failed: HTTP {exc.code} {detail}"
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RemoteWorkerError(f"POST {path} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RemoteWorkerError(f"POST {path} returned a non-object JSON payload")
    return payload


def run_worker(
    *,
    output_root: Path,
    limit: int = 50,
    include_archived: bool = False,
) -> dict:
    provider_bundles = build_provider_bundles_from_env()
    provider_bundle = next(
        (bundle for bundle in provider_bundles if bundle.configured),
        provider_bundles[0],
    )
    service = MediaForgeService(
        output_root,
        provider=provider_bundle.provider,
        provider_registrations=[
            ProviderRegistration(
                provider=bundle.provider,
                priority=len(provider_bundles) - index,
                enabled=bundle.configured,
            )
            for index, bundle in enumerate(provider_bundles)
        ],
    )
    service.set_provider_status(
        provider_bundle.status_view(),
        statuses=[bundle.status_view() for bundle in provider_bundles],
    )
    return service.drain_all_queues(
        limit=limit,
        actor="worker-cli",
        include_archived=include_archived,
    )


def run_remote_worker(
    *,
    base_url: str,
    worker_id: str,
    capabilities: list[str] | None = None,
    concurrency: int = 1,
    limit: int = 50,
    project_id: str | None = None,
    poll_interval: float = 5.0,
    heartbeat_interval: float | None = None,
    minimum_gpu_memory_mib: int = 0,
    token: str | None = None,
    once: bool = False,
    request_fn: RequestFn | None = None,
    execution_mode: str | None = None,
    artifact_root: Path | None = None,
    callback_secret: str | None = None,
    callback_fn: CallbackFn | None = None,
    local_provider: Any | None = None,
    local_provider_name: str | None = None,
) -> dict[str, Any]:
    """Run a restart-safe Worker against the HTTP control plane."""
    if not base_url.strip():
        raise ValueError("base_url is required for remote worker mode")
    if not worker_id.strip():
        raise ValueError("worker_id is required")
    if concurrency < 1 or concurrency > 64:
        raise ValueError("concurrency must be between 1 and 64")
    if limit < 1 or limit > 64:
        raise ValueError("limit must be between 1 and 64")
    if poll_interval < 0:
        raise ValueError("poll_interval must be >= 0")
    if minimum_gpu_memory_mib < 0 or minimum_gpu_memory_mib > 1_048_576:
        raise ValueError("minimum_gpu_memory_mib must be between 0 and 1048576")
    selected_mode = (
        execution_mode
        or os.getenv("MEDIAFORGE_WORKER_EXECUTION_MODE", "api-provider-dispatch")
    ).strip().lower()
    if selected_mode not in {"api-provider-dispatch", "local-provider-callback"}:
        raise ValueError(
            "execution_mode must be api-provider-dispatch or local-provider-callback"
        )
    request = request_fn or remote_request_json
    clean_capabilities = sorted({item.strip() for item in (capabilities or []) if item.strip()})
    provider = None
    local_artifact_root = None
    resolved_callback_secret = ""
    if selected_mode == "local-provider-callback":
        provider = local_provider
        if provider is None:
            bundles = build_provider_bundles_from_env()
            requested_provider = (
                local_provider_name
                or os.getenv("MEDIAFORGE_WORKER_PROVIDER", "")
            ).strip()
            bundle = next(
                (
                    item
                    for item in bundles
                    if requested_provider
                    and (
                        item.provider.name == requested_provider
                        or item.mode == requested_provider
                    )
                ),
                None,
            )
            if bundle is None:
                bundle = next((item for item in bundles if item.configured), bundles[0])
            if requested_provider and (
                bundle.provider.name != requested_provider
                and bundle.mode != requested_provider
            ):
                raise ValueError(
                    f"Worker Provider is not configured: {requested_provider}"
                )
            if not bundle.configured:
                raise ValueError(f"Worker Provider is not configured: {bundle.message}")
            provider = bundle.provider
        configured_root = artifact_root or os.getenv(
            "MEDIAFORGE_WORKER_ARTIFACT_ROOT", ""
        ).strip()
        if not configured_root:
            raise ValueError(
                "MEDIAFORGE_WORKER_ARTIFACT_ROOT is required for local-provider-callback"
            )
        local_artifact_root = Path(configured_root).expanduser().resolve()
        local_artifact_root.mkdir(parents=True, exist_ok=True)
        resolved_callback_secret = (
            callback_secret or os.getenv("MEDIAFORGE_CALLBACK_SECRET", "")
        ).strip()
        if not resolved_callback_secret:
            raise ValueError(
                "MEDIAFORGE_CALLBACK_SECRET is required for local-provider-callback"
            )
        provider_capabilities = {
            capability.value
            for capability in Capability
            if provider.supports(capability)
        }
        if clean_capabilities:
            clean_capabilities = sorted(set(clean_capabilities) & provider_capabilities)
        else:
            clean_capabilities = sorted(provider_capabilities)
        if not clean_capabilities:
            raise ValueError("local Worker Provider does not support a known capability")
    def current_resources() -> dict[str, Any]:
        resources = worker_resource_snapshot(execution_mode=selected_mode)
        if provider is not None:
            resources["provider_name"] = str(provider.name)
            resources["provider_capabilities"] = clean_capabilities
        return resources

    initial_resources = current_resources()
    request(
        base_url,
        "POST",
        "/workers/register",
        {
            "worker_id": worker_id.strip(),
            "capabilities": clean_capabilities,
            "concurrency": concurrency,
            "resources": initial_resources,
        },
        token=token,
    )
    status = request(base_url, "GET", "/workers", token=token)
    lease_seconds = float(status.get("lease_seconds") or 900)
    interval = heartbeat_interval
    if interval is None:
        interval = max(min(lease_seconds / 3, 30.0), 1.0)
    if interval <= 0:
        raise ValueError("heartbeat_interval must be > 0")

    stop_event = Event()
    active_lock = Lock()
    active_job_ids: set[str] = set()
    result: dict[str, Any] = {
        "mode": "remote",
        "worker_id": worker_id.strip(),
        "base_url": base_url.rstrip("/"),
        "registered": True,
        "cycles": 0,
        "claimed": 0,
        "processed": 0,
        "failed": 0,
        "heartbeat_errors": [],
        "gpu": initial_resources,
        "execution_mode": selected_mode,
    }

    def heartbeat_loop() -> None:
        while not stop_event.wait(interval):
            with active_lock:
                job_ids = sorted(active_job_ids)
            try:
                request(
                    base_url,
                    "POST",
                    f"/workers/{worker_id}/heartbeat",
                    {
                        "job_ids": job_ids,
                        "resources": current_resources(),
                    },
                    token=token,
                )
            except Exception as exc:  # keep processing while control plane recovers
                result["heartbeat_errors"].append(str(exc))

    heartbeat_thread = Thread(
        target=heartbeat_loop,
        name=f"mediaforge-heartbeat-{worker_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        while True:
            result["cycles"] += 1
            claim_body: dict[str, Any] = {
                "limit": min(limit, concurrency),
                "project_id": project_id,
                "minimum_gpu_memory_mib": minimum_gpu_memory_mib,
            }
            if provider is not None:
                claim_body["provider_name"] = str(provider.name)
            claim = request(
                base_url,
                "POST",
                f"/workers/{worker_id}/claim",
                claim_body,
                token=token,
            )
            jobs = [job for job in claim.get("jobs", []) if isinstance(job, dict)]
            result["claimed"] += len(jobs)
            if not jobs:
                if once:
                    break
                stop_event.wait(poll_interval)
                continue

            def process_claimed(job: dict[str, Any]) -> tuple[bool, str | None]:
                job_id = str(job.get("job_id") or "")
                spec = job.get("spec") or {}
                current_project_id = str(spec.get("project_id") or project_id or "")
                if not job_id or not current_project_id:
                    return False, "claimed job is missing job_id or project_id"
                with active_lock:
                    active_job_ids.add(job_id)
                try:
                    if selected_mode == "local-provider-callback":
                        assert provider is not None
                        assert local_artifact_root is not None
                        callback_path = (
                            f"/projects/{current_project_id}/jobs/{job_id}/callback"
                        )
                        expected_provider = str(job.get("routed_provider") or "").strip()
                        active_provider = str(provider.name).strip()

                        def send_callback(payload: dict[str, Any]) -> dict[str, Any]:
                            if callback_fn is not None:
                                return callback_fn(
                                    base_url,
                                    callback_path,
                                    payload,
                                    token=token,
                                    callback_secret=resolved_callback_secret,
                                )
                            return remote_provider_callback_json(
                                base_url,
                                callback_path,
                                payload,
                                token=token,
                                callback_secret=resolved_callback_secret,
                            )

                        if expected_provider and expected_provider != active_provider:
                            send_callback(
                                {
                                    "event_id": f"worker:{worker_id}:{job_id}:route-mismatch",
                                    "provider": expected_provider,
                                    "status": "FAILED",
                                    "reason": (
                                        "leased Worker Provider does not match routed Provider: "
                                        f"expected {expected_provider}, got {active_provider}"
                                    ),
                                    "actor": f"worker:{worker_id}",
                                    "worker_id": worker_id,
                                }
                            )
                            return False, "leased Worker Provider does not match the routed Provider"

                        send_callback(
                            {
                                "event_id": f"worker:{worker_id}:{job_id}:running",
                                "provider": active_provider,
                                "status": "RUNNING",
                                "actor": f"worker:{worker_id}",
                                "worker_id": worker_id,
                            }
                        )
                        generation_spec: GenerationSpec | None = None
                        try:
                            generation_spec = GenerationSpec.model_validate(spec)
                            if not provider.supports(
                                generation_spec.provider_constraints.capability
                            ):
                                raise ValueError(
                                    "local Worker Provider does not support "
                                    f"{generation_spec.provider_constraints.capability.value}"
                                )
                            artifact = provider.generate(
                                generation_spec,
                                job_id=job_id,
                                output_dir=(
                                    local_artifact_root
                                    / current_project_id
                                    / "shots"
                                ),
                            )
                        except Exception as exc:
                            estimated_cost = (
                                provider.estimate_cost(generation_spec)
                                if generation_spec is not None
                                else None
                            )
                            send_callback(
                                {
                                    "event_id": f"worker:{worker_id}:{job_id}:failed",
                                    "provider": active_provider,
                                    "status": "FAILED",
                                    "reason": str(exc)[:2000],
                                    "estimated_cost": estimated_cost,
                                    "actor": f"worker:{worker_id}",
                                    "worker_id": worker_id,
                                }
                            )
                            return False, str(exc)
                        send_callback(
                            {
                                "event_id": (
                                    f"worker:{worker_id}:{job_id}:succeeded:"
                                    f"{artifact.sha256[:16]}"
                                ),
                                "provider": active_provider,
                                "status": "SUCCEEDED",
                                "artifact_uri": artifact.uri,
                                "artifact_kind": artifact.kind,
                                "mime_type": artifact.mime_type,
                                "estimated_cost": provider.estimate_cost(generation_spec),
                                "actor": f"worker:{worker_id}",
                                "worker_id": worker_id,
                            }
                        )
                        return True, None
                    request(
                        base_url,
                        "POST",
                        (
                            f"/projects/{current_project_id}/jobs/{job_id}/process"
                            f"?worker_id={worker_id}"
                        ),
                        token=token,
                    )
                    return True, None
                except Exception as exc:
                    return False, str(exc)
                finally:
                    with active_lock:
                        active_job_ids.discard(job_id)

            with ThreadPoolExecutor(max_workers=min(concurrency, len(jobs))) as pool:
                futures = [pool.submit(process_claimed, job) for job in jobs]
                for future in as_completed(futures):
                    succeeded, error = future.result()
                    if succeeded:
                        result["processed"] += 1
                    else:
                        result["failed"] += 1
                        if error:
                            result.setdefault("errors", []).append(error)
            if once:
                break
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=max(interval, 1.0) + 1.0)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drain queued MediaForge jobs from a local workspace."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.getenv("MEDIAFORGE_ARTIFACT_ROOT", "artifacts/api")),
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument(
        "--base-url",
        default=os.getenv("MEDIAFORGE_API_BASE_URL", "").strip(),
        help="Use the HTTP Worker control plane instead of local state (for example http://127.0.0.1:8020).",
    )
    parser.add_argument(
        "--worker-id",
        default=os.getenv("MEDIAFORGE_WORKER_ID", f"worker-{gethostname()}"),
    )
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        default=[],
        help="Worker capability; repeat for multiple capabilities.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("MEDIAFORGE_WORKER_CONCURRENCY", "1")),
    )
    parser.add_argument(
        "--min-gpu-memory-mib",
        type=int,
        default=int(os.getenv("MEDIAFORGE_WORKER_MIN_GPU_MEMORY_MIB", "0")),
        help="Require this much free GPU memory before claiming remote jobs; 0 disables the admission check.",
    )
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--token", default=os.getenv("MEDIAFORGE_WORKER_TOKEN", "").strip())
    parser.add_argument("--once", action="store_true", help="Claim once and exit in remote mode.")
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=None,
        help="Remote heartbeat interval in seconds; defaults to one third of the lease.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=("api-provider-dispatch", "local-provider-callback"),
        default=os.getenv(
            "MEDIAFORGE_WORKER_EXECUTION_MODE", "api-provider-dispatch"
        ),
        help="Execute through the API or run the configured Provider on this Worker and return a signed callback.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=(
            Path(os.environ["MEDIAFORGE_WORKER_ARTIFACT_ROOT"])
            if os.getenv("MEDIAFORGE_WORKER_ARTIFACT_ROOT", "").strip()
            else None
        ),
        help="Shared API artifact root required by local-provider-callback.",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("MEDIAFORGE_WORKER_PROVIDER", "").strip() or None,
        help="Provider name or configured mode for local-provider-callback.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.0,
        help="If greater than zero, keep draining in a loop and sleep between passes.",
    )
    args = parser.parse_args()

    if args.base_url:
        try:
            result = run_remote_worker(
                base_url=args.base_url,
                worker_id=args.worker_id,
                capabilities=args.capabilities,
                concurrency=args.concurrency,
                limit=args.limit,
                project_id=args.project_id,
                poll_interval=args.poll_interval if args.poll_interval > 0 else 5.0,
                heartbeat_interval=args.heartbeat_interval,
                minimum_gpu_memory_mib=args.min_gpu_memory_mib,
                token=args.token or None,
                once=args.once,
                execution_mode=args.execution_mode,
                artifact_root=args.artifact_root,
                local_provider_name=args.provider,
            )
        except (RemoteWorkerError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.poll_interval <= 0:
        result = run_worker(
            output_root=args.output_root,
            limit=args.limit,
            include_archived=args.include_archived,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        while True:
            result = run_worker(
                output_root=args.output_root,
                limit=args.limit,
                include_archived=args.include_archived,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sleep(args.poll_interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
