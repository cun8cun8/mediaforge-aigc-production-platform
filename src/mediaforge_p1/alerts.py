from __future__ import annotations

import os
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class AlertConfigurationError(ValueError):
    """Raised when an operations-alert environment variable is invalid."""


def _integer(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise AlertConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise AlertConfigurationError(f"{name} must be >= {minimum}")
    return value


def _number(name: str, default: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise AlertConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise AlertConfigurationError(f"{name} must be >= {minimum}{suffix}")
    return value


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise AlertConfigurationError(f"{name} must be a boolean")


@dataclass(frozen=True)
class OperationsAlertSettings:
    queue_depth: int
    queue_wait_seconds: float
    failure_rate: float
    minimum_terminal_jobs: int
    spend_ratio: float
    stale_worker_seconds: float
    require_production_ready: bool

    @classmethod
    def from_env(cls) -> "OperationsAlertSettings":
        return cls(
            queue_depth=_integer("MEDIAFORGE_ALERT_QUEUE_DEPTH", 25, minimum=1),
            queue_wait_seconds=_number("MEDIAFORGE_ALERT_QUEUE_WAIT_SECONDS", 300, minimum=1),
            failure_rate=_number("MEDIAFORGE_ALERT_FAILURE_RATE", 0.25, minimum=0.0, maximum=1.0),
            minimum_terminal_jobs=_integer("MEDIAFORGE_ALERT_MIN_TERMINAL_JOBS", 5, minimum=1),
            spend_ratio=_number("MEDIAFORGE_ALERT_SPEND_RATIO", 0.9, minimum=0.0, maximum=1.0),
            stale_worker_seconds=_number("MEDIAFORGE_ALERT_STALE_WORKER_SECONDS", 900, minimum=1),
            require_production_ready=_enabled("MEDIAFORGE_ALERT_REQUIRE_PRODUCTION_READY"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "queue_depth": self.queue_depth,
            "queue_wait_seconds": self.queue_wait_seconds,
            "failure_rate": self.failure_rate,
            "minimum_terminal_jobs": self.minimum_terminal_jobs,
            "spend_ratio": self.spend_ratio,
            "stale_worker_seconds": self.stale_worker_seconds,
            "require_production_ready": self.require_production_ready,
        }


@dataclass(frozen=True)
class OperationsAlert:
    code: str
    severity: str
    summary: str
    observed: float | int | str | bool
    threshold: float | int | str | bool
    source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "observed": self.observed,
            "threshold": self.threshold,
            "source": self.source,
        }


def evaluate_operations_alerts(
    *,
    settings: OperationsAlertSettings,
    studio_metrics: dict[str, Any],
    runtime_metrics: dict[str, Any],
    workers: dict[str, Any],
    production_readiness: dict[str, Any],
) -> dict[str, object]:
    """Evaluate deterministic operations thresholds without external side effects."""
    alerts: list[OperationsAlert] = []
    jobs = studio_metrics.get("jobs") if isinstance(studio_metrics.get("jobs"), dict) else {}
    cost = studio_metrics.get("cost") if isinstance(studio_metrics.get("cost"), dict) else {}
    queue_depth = int(jobs.get("queue_depth") or 0)
    queue_wait = float(jobs.get("average_queue_wait_seconds") or 0.0)
    if queue_depth >= settings.queue_depth:
        alerts.append(OperationsAlert(
            code="QUEUE_DEPTH_HIGH",
            severity="warning",
            summary="Queued generation work exceeds the configured capacity threshold.",
            observed=queue_depth,
            threshold=settings.queue_depth,
            source="studio_metrics.jobs.queue_depth",
        ))
    if queue_wait >= settings.queue_wait_seconds:
        alerts.append(OperationsAlert(
            code="QUEUE_WAIT_HIGH",
            severity="warning",
            summary="Average queue wait exceeds the configured service-level threshold.",
            observed=round(queue_wait, 4),
            threshold=settings.queue_wait_seconds,
            source="studio_metrics.jobs.average_queue_wait_seconds",
        ))

    statuses = jobs.get("status_counts") if isinstance(jobs.get("status_counts"), dict) else {}
    terminal = int(jobs.get("terminal") or 0)
    failed = int(statuses.get("FAILED") or 0)
    if terminal >= settings.minimum_terminal_jobs:
        failure_rate = failed / terminal
        if failure_rate >= settings.failure_rate:
            alerts.append(OperationsAlert(
                code="JOB_FAILURE_RATE_HIGH",
                severity="critical",
                summary="Terminal generation failures exceed the configured error budget.",
                observed=round(failure_rate, 4),
                threshold=settings.failure_rate,
                source="studio_metrics.jobs.status_counts.FAILED",
            ))

    spent_ratio = float(cost.get("spent_ratio") or 0.0)
    if spent_ratio >= settings.spend_ratio:
        alerts.append(OperationsAlert(
            code="STUDIO_SPEND_HIGH",
            severity="critical" if spent_ratio >= 1.0 else "warning",
            summary="Studio spend reached the configured budget protection threshold.",
            observed=round(spent_ratio, 4),
            threshold=settings.spend_ratio,
            source="studio_metrics.cost.spent_ratio",
        ))

    stale_workers = [
        row for row in (workers.get("workers") or [])
        if isinstance(row, dict)
        and (
            row.get("status") == "STALE"
            or float(row.get("heartbeat_age_seconds") or 0.0) >= settings.stale_worker_seconds
        )
    ]
    if stale_workers:
        alerts.append(OperationsAlert(
            code="WORKER_HEARTBEAT_STALE",
            severity="warning",
            summary="One or more remote Workers have stale heartbeats.",
            observed=len(stale_workers),
            threshold=0,
            source="worker_control.heartbeat_age_seconds",
        ))

    provider_attempts = int(runtime_metrics.get("total_attempts") or 0)
    provider_failures = sum(
        int((row.get("outcomes") or {}).get("failed") or 0)
        for row in (runtime_metrics.get("providers") or [])
        if isinstance(row, dict)
    )
    if provider_attempts >= settings.minimum_terminal_jobs:
        provider_failure_rate = provider_failures / provider_attempts
        if provider_failure_rate >= settings.failure_rate:
            alerts.append(OperationsAlert(
                code="PROVIDER_FAILURE_RATE_HIGH",
                severity="critical",
                summary="Provider execution failures exceed the configured error budget.",
                observed=round(provider_failure_rate, 4),
                threshold=settings.failure_rate,
                source="runtime_metrics.providers.outcomes.failed",
            ))

    if settings.require_production_ready and not production_readiness.get("production_ready"):
        alerts.append(OperationsAlert(
            code="PRODUCTION_READINESS_BLOCKED",
            severity="critical",
            summary="Strict production readiness is required but the runtime is not production ready.",
            observed=False,
            threshold=True,
            source="production_readiness.production_ready",
        ))

    severity_order = {"critical": 2, "warning": 1}
    alerts.sort(key=lambda alert: (-severity_order[alert.severity], alert.code))
    critical_count = sum(alert.severity == "critical" for alert in alerts)
    warning_count = sum(alert.severity == "warning" for alert in alerts)
    return {
        "schema_version": "mediaforge-operations-alerts-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grade": "CRITICAL" if critical_count else "WARNING" if warning_count else "HEALTHY",
        "critical_count": critical_count,
        "warning_count": warning_count,
        "settings": settings.as_dict(),
        "alerts": [alert.as_dict() for alert in alerts],
    }


def operations_alerts_prometheus(report: dict[str, object]) -> str:
    alerts = report.get("alerts") if isinstance(report.get("alerts"), list) else []
    critical = int(report.get("critical_count") or 0)
    warning = int(report.get("warning_count") or 0)
    lines = [
        "# HELP mediaforge_operations_alerts_active Active MediaForge operations alerts by severity.",
        "# TYPE mediaforge_operations_alerts_active gauge",
        f'mediaforge_operations_alerts_active{{severity="critical"}} {critical}',
        f'mediaforge_operations_alerts_active{{severity="warning"}} {warning}',
        "# HELP mediaforge_operations_alert_active Active MediaForge operations alert state by code.",
        "# TYPE mediaforge_operations_alert_active gauge",
    ]
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        code = str(alert.get("code") or "unknown").replace("\\", "\\\\").replace('"', '\\"')
        severity = str(alert.get("severity") or "warning").replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'mediaforge_operations_alert_active{{code="{code}",severity="{severity}"}} 1')
    return "\n".join(lines) + "\n"
