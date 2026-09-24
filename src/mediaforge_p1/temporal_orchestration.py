from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse


class TemporalOrchestrationError(RuntimeError):
    """Raised when an optional Temporal operation cannot be started or inspected."""


class TemporalConfigurationError(ValueError):
    """Raised for invalid Temporal configuration or workflow input."""


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_OPERATIONS = frozenset({"generation", "render", "package", "dispatch"})


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise TemporalConfigurationError(f"{name} must be a boolean")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise TemporalConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise TemporalConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _read_secret_file(path_value: str | None, *, label: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TemporalConfigurationError(f"cannot read {label} file") from exc
    if not value:
        raise TemporalConfigurationError(f"{label} file is empty")
    return value


def _validate_address(address: str) -> str:
    address = address.strip()
    if not address or "/" in address or " " in address:
        raise TemporalConfigurationError("MEDIAFORGE_TEMPORAL_ADDRESS must be host:port")
    if ":" not in address:
        raise TemporalConfigurationError("MEDIAFORGE_TEMPORAL_ADDRESS must include a port")
    host, port = address.rsplit(":", 1)
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise TemporalConfigurationError(
            "MEDIAFORGE_TEMPORAL_ADDRESS must use a valid host:port"
        )
    return address


def _validate_url(value: str | None, *, name: str) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TemporalConfigurationError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TemporalConfigurationError(f"{name} must not contain credentials or query data")
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class TemporalOrchestrationSettings:
    enabled: bool = False
    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "mediaforge-orchestration"
    control_plane_url: str | None = None
    control_plane_token_file: str | None = None
    tls: bool = False
    client_cert_file: str | None = None
    client_key_file: str | None = None
    server_ca_file: str | None = None
    activity_timeout_seconds: int = 3600
    retry_max_attempts: int = 3
    worker_heartbeat_seconds: int = 20
    worker_stale_after_seconds: int = 75
    worker_registry_retention_seconds: int = 604800
    worker_registry_max_per_tenant: int = 500

    @classmethod
    def from_env(cls) -> "TemporalOrchestrationSettings":
        settings = cls(
            enabled=_env_bool("MEDIAFORGE_TEMPORAL_ENABLED"),
            address=_validate_address(
                os.getenv("MEDIAFORGE_TEMPORAL_ADDRESS", "localhost:7233")
            ),
            namespace=os.getenv("MEDIAFORGE_TEMPORAL_NAMESPACE", "default").strip(),
            task_queue=os.getenv(
                "MEDIAFORGE_TEMPORAL_TASK_QUEUE", "mediaforge-orchestration"
            ).strip(),
            control_plane_url=_validate_url(
                os.getenv("MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL"),
                name="MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL",
            ),
            control_plane_token_file=os.getenv(
                "MEDIAFORGE_TEMPORAL_CONTROL_PLANE_TOKEN_FILE"
            ),
            tls=_env_bool("MEDIAFORGE_TEMPORAL_TLS"),
            client_cert_file=os.getenv("MEDIAFORGE_TEMPORAL_CLIENT_CERT_FILE"),
            client_key_file=os.getenv("MEDIAFORGE_TEMPORAL_CLIENT_KEY_FILE"),
            server_ca_file=os.getenv("MEDIAFORGE_TEMPORAL_SERVER_CA_FILE"),
            activity_timeout_seconds=_env_int(
                "MEDIAFORGE_TEMPORAL_ACTIVITY_TIMEOUT_SECONDS",
                3600,
                minimum=1,
                maximum=86400,
            ),
            retry_max_attempts=_env_int(
                "MEDIAFORGE_TEMPORAL_RETRY_MAX_ATTEMPTS",
                3,
                minimum=1,
                maximum=10,
            ),
            worker_heartbeat_seconds=_env_int(
                "MEDIAFORGE_TEMPORAL_WORKER_HEARTBEAT_SECONDS",
                20,
                minimum=5,
                maximum=300,
            ),
            worker_stale_after_seconds=_env_int(
                "MEDIAFORGE_TEMPORAL_WORKER_STALE_AFTER_SECONDS",
                75,
                minimum=15,
                maximum=1800,
            ),
            worker_registry_retention_seconds=_env_int(
                "MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_RETENTION_SECONDS",
                604800,
                minimum=3600,
                maximum=7776000,
            ),
            worker_registry_max_per_tenant=_env_int(
                "MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_MAX_PER_TENANT",
                500,
                minimum=1,
                maximum=10000,
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        _validate_address(self.address)
        if not self.namespace or len(self.namespace) > 120 or not _QUEUE_PATTERN.fullmatch(self.namespace):
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_NAMESPACE must contain only letters, numbers, _, ., or -"
            )
        if not _QUEUE_PATTERN.fullmatch(self.task_queue):
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_TASK_QUEUE must contain only letters, numbers, _, ., or -"
            )
        _validate_url(self.control_plane_url, name="MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL")
        has_cert = bool(self.client_cert_file)
        has_key = bool(self.client_key_file)
        has_ca = bool(self.server_ca_file)
        if any((has_cert, has_key, has_ca)) and not all((has_cert, has_key, has_ca)):
            raise TemporalConfigurationError(
                "Temporal mTLS requires client cert, client key and server CA together"
            )
        if any((has_cert, has_key, has_ca)) and not self.tls:
            raise TemporalConfigurationError("Temporal mTLS requires MEDIAFORGE_TEMPORAL_TLS=true")
        if not 5 <= self.worker_heartbeat_seconds <= 300:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_HEARTBEAT_SECONDS must be between 5 and 300"
            )
        if not 15 <= self.worker_stale_after_seconds <= 1800:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_STALE_AFTER_SECONDS must be between 15 and 1800"
            )
        if self.worker_stale_after_seconds <= self.worker_heartbeat_seconds:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_STALE_AFTER_SECONDS must exceed the heartbeat interval"
            )
        if not 3600 <= self.worker_registry_retention_seconds <= 7776000:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_RETENTION_SECONDS must be between 3600 and 7776000"
            )
        if self.worker_registry_retention_seconds <= self.worker_stale_after_seconds:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_RETENTION_SECONDS must exceed the stale threshold"
            )
        if not 1 <= self.worker_registry_max_per_tenant <= 10000:
            raise TemporalConfigurationError(
                "MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_MAX_PER_TENANT must be between 1 and 10000"
            )

    def tls_config(self) -> Any:
        """Build the SDK TLS value lazily so the base install stays dependency-free."""
        if not self.tls:
            return None
        cert = _read_secret_file(self.client_cert_file, label="Temporal client certificate")
        key = _read_secret_file(self.client_key_file, label="Temporal client key")
        ca = _read_secret_file(self.server_ca_file, label="Temporal server CA")
        if not all((cert, key, ca)):
            return True
        try:
            from temporalio.client import TLSConfig
        except ImportError as exc:  # pragma: no cover - optional dependency boundary
            raise TemporalOrchestrationError(
                "Temporal SDK is required for TLS orchestration"
            ) from exc
        return TLSConfig(
            client_cert=cert.encode("utf-8"),
            client_private_key=key.encode("utf-8"),
            server_root_ca_cert=ca.encode("utf-8"),
        )


@dataclass(frozen=True)
class TemporalOperationRequest:
    project_id: str
    operation: str
    request_id: str
    shot_id: str | None = None
    delivery: dict[str, Any] | None = None
    actor: str = "temporal-worker"
    activity_timeout_seconds: int = 3600
    retry_max_attempts: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in {
            "project_id": self.project_id,
            "request_id": self.request_id,
        }.items():
            if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
                raise TemporalConfigurationError(f"{name} contains unsupported characters")
        if self.shot_id is not None and not _ID_PATTERN.fullmatch(self.shot_id):
            raise TemporalConfigurationError("shot_id contains unsupported characters")
        if self.operation not in _OPERATIONS:
            raise TemporalConfigurationError(
                f"operation must be one of {', '.join(sorted(_OPERATIONS))}"
            )
        if self.operation == "generation" and not self.shot_id:
            raise TemporalConfigurationError("generation requires shot_id")
        if self.operation == "dispatch" and not isinstance(self.delivery, dict):
            raise TemporalConfigurationError("dispatch requires delivery details")
        if not isinstance(self.actor, str) or not self.actor.strip() or len(self.actor) > 120:
            raise TemporalConfigurationError("actor must be a non-empty string")
        if not 1 <= self.activity_timeout_seconds <= 86400:
            raise TemporalConfigurationError("activity_timeout_seconds is out of range")
        if not 1 <= self.retry_max_attempts <= 10:
            raise TemporalConfigurationError("retry_max_attempts is out of range")
        if self.delivery is not None:
            allowed = {"channel", "recipient", "destination_uri", "note"}
            if set(self.delivery) - allowed:
                raise TemporalConfigurationError("delivery contains unsupported fields")
            for key, value in self.delivery.items():
                if value is not None and (not isinstance(value, str) or len(value) > 2000):
                    raise TemporalConfigurationError(f"delivery.{key} is invalid")

    @property
    def workflow_id(self) -> str:
        return f"mediaforge:{self.project_id}:{self.operation}:{self.request_id}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "operation": self.operation,
            "request_id": self.request_id,
            "shot_id": self.shot_id,
            "delivery": dict(self.delivery) if self.delivery else None,
            "actor": self.actor,
            "activity_timeout_seconds": self.activity_timeout_seconds,
            "retry_max_attempts": self.retry_max_attempts,
            "metadata": dict(self.metadata),
        }


class TemporalOrchestrator:
    """Small sync facade around the optional async Temporal Python SDK."""

    def __init__(
        self,
        settings: TemporalOrchestrationSettings,
        *,
        client_factory: Callable[[TemporalOrchestrationSettings], Any] | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self._client_factory = client_factory
        self._last_error: str | None = None
        self._client_attempted = False

    @classmethod
    def from_env(cls) -> "TemporalOrchestrator":
        return cls(TemporalOrchestrationSettings.from_env())

    def status_view(self) -> dict[str, Any]:
        sdk_available = importlib.util.find_spec("temporalio") is not None
        return {
            "enabled": self.settings.enabled,
            "configured": bool(self.settings.enabled and sdk_available),
            "address": self.settings.address,
            "namespace": self.settings.namespace,
            "task_queue": self.settings.task_queue,
            "tls": self.settings.tls,
            "worker_heartbeat_seconds": self.settings.worker_heartbeat_seconds,
            "worker_stale_after_seconds": self.settings.worker_stale_after_seconds,
            "worker_registry_retention_seconds": self.settings.worker_registry_retention_seconds,
            "worker_registry_max_per_tenant": self.settings.worker_registry_max_per_tenant,
            "control_plane_url_configured": bool(self.settings.control_plane_url),
            "control_plane_token_configured": bool(self.settings.control_plane_token_file),
            "worker_connection_configured": bool(self.settings.control_plane_url),
            "sdk_available": sdk_available,
            "client_ready": bool(self._client_attempted and not self._last_error),
            "last_error": self._last_error,
        }

    @staticmethod
    def _workflow_type() -> Any:
        from .temporal_workflows import MediaForgeProductionWorkflow

        return getattr(MediaForgeProductionWorkflow, "run", "MediaForgeProductionWorkflow")

    async def _connect(self) -> Any:
        if not self.settings.enabled:
            raise TemporalOrchestrationError("Temporal orchestration is disabled")
        if self._client_factory is not None:
            result = self._client_factory(self.settings)
            if hasattr(result, "__await__"):
                return await result
            return result
        try:
            from temporalio.client import Client
        except ImportError as exc:
            raise TemporalOrchestrationError(
                "install the optional Temporal dependency with pip install -e .[temporal]"
            ) from exc
        return await Client.connect(
            self.settings.address,
            namespace=self.settings.namespace,
            tls=self.settings.tls_config(),
        )

    def _run(self, coroutine: Any) -> Any:
        try:
            result = asyncio.run(coroutine)
        except TemporalOrchestrationError as exc:
            self._last_error = str(exc)
            raise
        except Exception as exc:
            self._last_error = str(exc)[:500]
            raise TemporalOrchestrationError(self._last_error) from exc
        self._last_error = None
        self._client_attempted = True
        return result

    async def _start(self, request: TemporalOperationRequest) -> dict[str, Any]:
        client = await self._connect()
        try:
            start_options: dict[str, Any] = {}
            if self._client_factory is None:
                try:
                    from temporalio.common import WorkflowIDReusePolicy
                except ImportError as exc:  # pragma: no cover - optional dependency boundary
                    raise TemporalOrchestrationError(
                        "install the optional Temporal dependency with pip install -e .[temporal]"
                    ) from exc
                start_options["id_reuse_policy"] = WorkflowIDReusePolicy.REJECT_DUPLICATE
            handle = await client.start_workflow(
                self._workflow_type(),
                request.to_payload(),
                id=request.workflow_id,
                task_queue=self.settings.task_queue,
                request_id=request.request_id,
                execution_timeout=timedelta(
                    seconds=request.activity_timeout_seconds * request.retry_max_attempts
                ),
                **start_options,
            )
            return {
                "workflow_id": getattr(handle, "id", request.workflow_id),
                "run_id": getattr(handle, "result_run_id", None),
                "operation": request.operation,
                "request_id": request.request_id,
                "started": True,
                "already_started": False,
                "task_queue": self.settings.task_queue,
            }
        except Exception as exc:
            if exc.__class__.__name__ == "WorkflowAlreadyStartedError":
                return {
                    "workflow_id": request.workflow_id,
                    "run_id": None,
                    "operation": request.operation,
                    "request_id": request.request_id,
                    "started": False,
                    "already_started": True,
                    "task_queue": self.settings.task_queue,
                }
            raise

    def start(self, request: TemporalOperationRequest) -> dict[str, Any]:
        return self._run(self._start(request))

    async def _describe(self, workflow_id: str) -> dict[str, Any]:
        client = await self._connect()
        description = await client.get_workflow_handle(workflow_id).describe()
        return {
            "workflow_id": workflow_id,
            "run_id": getattr(description, "run_id", None),
            "status": str(getattr(description, "status", "UNKNOWN")),
            "workflow_type": getattr(description, "workflow_type", None),
            "start_time": getattr(description, "start_time", None),
            "close_time": getattr(description, "close_time", None),
        }

    def describe(self, workflow_id: str) -> dict[str, Any]:
        parts = workflow_id.split(":")
        if len(parts) != 4 or parts[0] != "mediaforge" or not all(
            _ID_PATTERN.fullmatch(value) for value in parts[1:]
        ):
            raise TemporalConfigurationError("workflow_id contains unsupported characters")
        return self._run(self._describe(workflow_id))

    async def _cancel(self, workflow_id: str) -> dict[str, Any]:
        client = await self._connect()
        await client.get_workflow_handle(workflow_id).cancel()
        return {"workflow_id": workflow_id, "canceled": True}

    def cancel(self, workflow_id: str) -> dict[str, Any]:
        if not workflow_id.startswith("mediaforge:"):
            raise TemporalConfigurationError("workflow_id must belong to MediaForge")
        return self._run(self._cancel(workflow_id))

    async def _probe(self) -> dict[str, Any]:
        await self._connect()
        return {"connected": True, **self.status_view()}

    def probe(self) -> dict[str, Any]:
        return self._run(self._probe())
