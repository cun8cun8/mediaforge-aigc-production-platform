from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class QuotaConfigurationError(ValueError):
    pass


class QuotaViolation(ValueError):
    def __init__(self, message: str, *, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class TenantQuota:
    max_projects: int | None = None
    max_jobs: int | None = None
    max_budget: float | None = None

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "max_projects": self.max_projects,
            "max_jobs": self.max_jobs,
            "max_budget": self.max_budget,
        }


class QuotaPolicy:
    """Environment-backed tenant quotas with an unlimited-by-default policy."""

    def __init__(
        self,
        *,
        default: TenantQuota,
        tenants: dict[str, TenantQuota],
    ) -> None:
        self.default = default
        self.tenants = tenants

    @classmethod
    def from_env(cls) -> "QuotaPolicy":
        default = TenantQuota(
            max_projects=_optional_int("MEDIAFORGE_TENANT_MAX_PROJECTS"),
            max_jobs=_optional_int("MEDIAFORGE_TENANT_MAX_JOBS"),
            max_budget=_optional_float("MEDIAFORGE_TENANT_BUDGET"),
        )
        raw = os.getenv("MEDIAFORGE_TENANT_QUOTAS", "").strip()
        tenants: dict[str, TenantQuota] = {}
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise QuotaConfigurationError(
                    "MEDIAFORGE_TENANT_QUOTAS must be a JSON object"
                ) from exc
            if not isinstance(payload, dict):
                raise QuotaConfigurationError(
                    "MEDIAFORGE_TENANT_QUOTAS must be a JSON object"
                )
            for tenant_id, values in payload.items():
                if not isinstance(tenant_id, str) or not tenant_id.strip():
                    raise QuotaConfigurationError(
                        "MEDIAFORGE_TENANT_QUOTAS contains an empty tenant id"
                    )
                if not isinstance(values, dict):
                    raise QuotaConfigurationError(
                        "each tenant quota must be a JSON object"
                    )
                tenants[tenant_id.strip()] = TenantQuota(
                    max_projects=_optional_int_value(
                        values.get("max_projects"),
                        f"{tenant_id}.max_projects",
                    ),
                    max_jobs=_optional_int_value(
                        values.get("max_jobs"),
                        f"{tenant_id}.max_jobs",
                    ),
                    max_budget=_optional_float_value(
                        values.get("max_budget"),
                        f"{tenant_id}.max_budget",
                    ),
                )
        return cls(default=default, tenants=tenants)

    @property
    def configured(self) -> bool:
        return bool(
            self.tenants
            or self.default.max_projects is not None
            or self.default.max_jobs is not None
            or self.default.max_budget is not None
        )

    def for_tenant(self, tenant_id: str | None) -> TenantQuota:
        return self.tenants.get(str(tenant_id or "default"), self.default)

    def status_view(self) -> dict[str, Any]:
        return {
            "schema_version": "mediaforge-tenant-quota-v1",
            "configured": self.configured,
            "default": self.default.as_dict(),
            "tenant_overrides": {
                tenant_id: quota.as_dict()
                for tenant_id, quota in sorted(self.tenants.items())
            },
        }


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise QuotaConfigurationError(f"{name} must be a non-negative integer") from exc
    return _validate_int(value, name)


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise QuotaConfigurationError(f"{name} must be a non-negative number") from exc
    return _validate_float(value, name)


def _optional_int_value(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QuotaConfigurationError(f"{name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise QuotaConfigurationError(f"{name} must be a non-negative integer") from exc
    return _validate_int(parsed, name)


def _optional_float_value(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QuotaConfigurationError(f"{name} must be a non-negative number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise QuotaConfigurationError(f"{name} must be a non-negative number") from exc
    return _validate_float(parsed, name)


def _validate_int(value: int, name: str) -> int:
    if value < 0:
        raise QuotaConfigurationError(f"{name} must be a non-negative integer")
    return value


def _validate_float(value: float, name: str) -> float:
    if value < 0:
        raise QuotaConfigurationError(f"{name} must be a non-negative number")
    return value
