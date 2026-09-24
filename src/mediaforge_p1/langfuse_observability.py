from __future__ import annotations

"""Optional Langfuse adapter for LLM and generation observability.

MediaForge keeps its audit trail as the system of record. This adapter only
exports a redacted operational view and must never make media generation fail.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import random
import re
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Iterator
from urllib.parse import urlparse


class LangfuseConfigurationError(ValueError):
    """Raised when opt-in Langfuse settings are incomplete or unsafe."""


def _enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    raise LangfuseConfigurationError(
        "MEDIAFORGE_LANGFUSE_ENABLED must be true or false"
    )


def _secret_from_file(variable: str) -> str:
    raw_path = os.getenv(variable, "").strip()
    if not raw_path:
        raise LangfuseConfigurationError(f"{variable} is required when Langfuse is enabled")
    try:
        value = Path(raw_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LangfuseConfigurationError(f"{variable} must point to a readable secret file") from exc
    if len(value) < 8:
        raise LangfuseConfigurationError(f"{variable} must contain a non-trivial credential")
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class LangfuseSettings:
    enabled: bool
    base_url: str | None = None
    environment: str = "default"
    sample_rate: float = 1.0
    include_prompt_content: bool = False
    public_key: str = ""
    secret_key: str = ""

    @classmethod
    def from_env(cls) -> "LangfuseSettings":
        if not _enabled(os.getenv("MEDIAFORGE_LANGFUSE_ENABLED", "false")):
            return cls(enabled=False)

        base_url = os.getenv(
            "MEDIAFORGE_LANGFUSE_BASE_URL", "https://cloud.langfuse.com"
        ).strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise LangfuseConfigurationError(
                "MEDIAFORGE_LANGFUSE_BASE_URL must be an absolute http(s) URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LangfuseConfigurationError(
                "MEDIAFORGE_LANGFUSE_BASE_URL must not contain credentials, a query, or a fragment"
            )

        environment = os.getenv(
            "MEDIAFORGE_LANGFUSE_ENVIRONMENT", "default"
        ).strip().lower()
        if not re.fullmatch(r"(?!langfuse)[a-z0-9_-]{1,40}", environment):
            raise LangfuseConfigurationError(
                "MEDIAFORGE_LANGFUSE_ENVIRONMENT must be 1..40 lowercase letters, numbers, _ or - and cannot start with langfuse"
            )
        try:
            sample_rate = float(os.getenv("MEDIAFORGE_LANGFUSE_SAMPLE_RATE", "1"))
        except ValueError as exc:
            raise LangfuseConfigurationError(
                "MEDIAFORGE_LANGFUSE_SAMPLE_RATE must be a number between 0 and 1"
            ) from exc
        if not 0 <= sample_rate <= 1:
            raise LangfuseConfigurationError(
                "MEDIAFORGE_LANGFUSE_SAMPLE_RATE must be between 0 and 1"
            )
        return cls(
            enabled=True,
            base_url=base_url,
            environment=environment,
            sample_rate=sample_rate,
            include_prompt_content=_enabled(
                os.getenv("MEDIAFORGE_LANGFUSE_INCLUDE_PROMPT_CONTENT", "false")
            ),
            public_key=_secret_from_file("MEDIAFORGE_LANGFUSE_PUBLIC_KEY_FILE"),
            secret_key=_secret_from_file("MEDIAFORGE_LANGFUSE_SECRET_KEY_FILE"),
        )

    def status_view(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "base_url_configured": bool(self.base_url),
            "environment": self.environment if self.enabled else None,
            "sample_rate": self.sample_rate if self.enabled else None,
            "include_prompt_content": self.include_prompt_content,
            "credentials_configured": bool(self.public_key and self.secret_key),
        }


class _GenerationObservation:
    def __init__(self) -> None:
        self.output: dict[str, object] = {"status": "succeeded"}

    def succeed(self, *, artifact_id: str, artifact_sha256: str, kind: str) -> None:
        self.output = {
            "status": "succeeded",
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            "kind": kind,
        }


class LangfuseObservability:
    """Best-effort client built around Langfuse Python SDK v4's OTel API."""

    def __init__(
        self,
        settings: LangfuseSettings,
        *,
        client_factory: Callable[[LangfuseSettings], Any] | None = None,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or self._default_client
        self._random_value = random_value
        self._client: Any | None = None
        self._initialization_error: str | None = None
        self._lock = Lock()
        self._emitted = 0
        self._failed = 0
        self._sampled_out = 0
        if settings.enabled:
            self._initialize()

    @classmethod
    def from_env(cls) -> "LangfuseObservability":
        return cls(LangfuseSettings.from_env())

    @staticmethod
    def _default_client(settings: LangfuseSettings) -> Any:
        try:
            from langfuse import Langfuse
        except ImportError as exc:
            raise RuntimeError(
                "Langfuse SDK is not installed; install mediaforge-p1-spike[observability]"
            ) from exc
        try:
            installed_version = version("langfuse")
        except PackageNotFoundError as exc:  # pragma: no cover - import metadata invariant
            raise RuntimeError(
                "Langfuse SDK metadata is unavailable; install mediaforge-p1-spike[observability]"
            ) from exc
        if not LangfuseObservability._sdk_version_supported(installed_version):
            raise RuntimeError(
                "Langfuse SDK 4.7 or newer is required for OpenTelemetry ingestion; "
                f"found {installed_version}"
            )
        return Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.base_url,
            environment=settings.environment,
        )

    @staticmethod
    def _sdk_version_supported(installed_version: str) -> bool:
        """Avoid legacy ingestion, which Langfuse has announced for removal."""
        match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", installed_version)
        if match is None:
            return False
        major, minor, patch = (int(value or 0) for value in match.groups())
        return (major, minor, patch) >= (4, 7, 0)

    def _initialize(self) -> None:
        try:
            self._client = self._client_factory(self.settings)
        except Exception as exc:  # Observability must not prevent control-plane startup.
            self._initialization_error = self._safe_error(exc)
            self._client = None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return f"{type(exc).__name__}: {str(exc)[:240]}"

    def status_view(self) -> dict[str, object]:
        with self._lock:
            return {
                **self.settings.status_view(),
                "client_ready": self._client is not None,
                "initialization_error": self._initialization_error,
                "emitted_count": self._emitted,
                "failed_count": self._failed,
                "sampled_out_count": self._sampled_out,
            }

    def _sample(self) -> bool:
        if not self.settings.enabled or self._client is None:
            return False
        if self.settings.sample_rate >= 1:
            return True
        selected = self._random_value() < self.settings.sample_rate
        if not selected:
            with self._lock:
                self._sampled_out += 1
        return selected

    @staticmethod
    def _safe_update(observation: Any, **kwargs: Any) -> None:
        update = getattr(observation, "update", None)
        if callable(update):
            update(**kwargs)

    def _record_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failed += 1
            self._initialization_error = self._safe_error(exc)

    def _record_emit(self) -> None:
        with self._lock:
            self._emitted += 1

    @contextmanager
    def provider_generation(
        self,
        *,
        project_id: str | None,
        trace_id: str | None,
        job_id: str,
        provider: str,
        capability: str,
        estimated_cost: float,
        prompt_versions: list[dict[str, Any]],
        spec: Any,
    ) -> Iterator[_GenerationObservation]:
        run = _GenerationObservation()
        if not self._sample():
            yield run
            return

        context_manager: Any | None = None
        observation: Any | None = None
        started = perf_counter()
        metadata = {
            "mediaforge.project_id": project_id,
            "mediaforge.trace_id": trace_id,
            "mediaforge.job_id": job_id,
            "mediaforge.provider": provider,
            "mediaforge.capability": capability,
            "mediaforge.estimated_cost": round(max(float(estimated_cost), 0.0), 6),
            "mediaforge.prompt_versions": [
                {
                    "prompt_id": item.get("prompt_id"),
                    "key": item.get("key"),
                    "version": item.get("version"),
                    "sha256": item.get("sha256"),
                }
                for item in prompt_versions
            ],
        }
        spec_view = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
        input_view: dict[str, Any] = {
            "spec_sha256": _fingerprint(spec_view),
            "prompt_content_included": self.settings.include_prompt_content,
        }
        if self.settings.include_prompt_content:
            input_view["spec"] = spec_view
        try:
            context_manager = self._client.start_as_current_observation(
                as_type="generation",
                name="mediaforge.provider.generate",
                model=provider,
            )
            observation = context_manager.__enter__()
            self._safe_update(observation, input=input_view, metadata=metadata)
        except Exception as exc:
            self._record_failure(exc)
            context_manager = None
            observation = None
        try:
            yield run
        except BaseException as exc:
            if observation is not None:
                try:
                    self._safe_update(
                        observation,
                        output={
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "duration_seconds": round(perf_counter() - started, 6),
                        },
                    )
                    context_manager.__exit__(type(exc), exc, exc.__traceback__)
                    self._record_emit()
                except Exception as export_error:
                    self._record_failure(export_error)
            raise
        else:
            if observation is not None:
                try:
                    output = {**run.output, "duration_seconds": round(perf_counter() - started, 6)}
                    self._safe_update(observation, output=output)
                    context_manager.__exit__(None, None, None)
                    self._record_emit()
                except Exception as exc:
                    self._record_failure(exc)

    def record_evaluation(
        self,
        *,
        project_id: str,
        trace_id: str,
        evaluation_id: str,
        score: float,
        passed: bool,
        prompt_versions: list[dict[str, Any]],
    ) -> None:
        if not self._sample():
            return
        context_manager: Any | None = None
        try:
            context_manager = self._client.start_as_current_observation(
                as_type="span", name="mediaforge.project.evaluation"
            )
            observation = context_manager.__enter__()
            self._safe_update(
                observation,
                input={
                    "evaluation_id": evaluation_id,
                    "prompt_versions": [
                        {
                            "prompt_id": item.get("prompt_id"),
                            "sha256": item.get("sha256"),
                        }
                        for item in prompt_versions
                    ],
                },
                output={"score": round(float(score), 6), "passed": bool(passed)},
                metadata={
                    "mediaforge.project_id": project_id,
                    "mediaforge.trace_id": trace_id,
                    "mediaforge.evaluation_id": evaluation_id,
                },
            )
            context_manager.__exit__(None, None, None)
            self._record_emit()
        except Exception as exc:
            self._record_failure(exc)
            if context_manager is not None:
                try:
                    context_manager.__exit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass

    def flush(self) -> None:
        client = self._client
        flush = getattr(client, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:
                self._record_failure(exc)
