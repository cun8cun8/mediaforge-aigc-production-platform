from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock

from .contracts import Capability, GenerationSpec
from .providers import GenerationProvider


class ProviderRoutingError(RuntimeError):
    """Raised when no registered Provider can safely accept a request."""


@dataclass(frozen=True)
class ProviderCircuitBreakerSettings:
    """Process-local protection against repeatedly selecting a failing Provider."""

    enabled: bool = True
    failure_threshold: int = 3
    open_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("provider circuit failure_threshold must be >= 1")
        if not math.isfinite(self.open_seconds) or self.open_seconds <= 0:
            raise ValueError("provider circuit open_seconds must be finite and > 0")

    @classmethod
    def from_env(cls) -> "ProviderCircuitBreakerSettings":
        raw_enabled = os.getenv(
            "MEDIAFORGE_PROVIDER_CIRCUIT_BREAKER_ENABLED",
            "true",
        ).strip().lower()
        if raw_enabled not in {"true", "false"}:
            raise ValueError(
                "MEDIAFORGE_PROVIDER_CIRCUIT_BREAKER_ENABLED must be true or false"
            )
        try:
            failure_threshold = int(
                os.getenv(
                    "MEDIAFORGE_PROVIDER_CIRCUIT_FAILURE_THRESHOLD",
                    "3",
                ).strip()
            )
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_PROVIDER_CIRCUIT_FAILURE_THRESHOLD must be an integer"
            ) from exc
        try:
            open_seconds = float(
                os.getenv(
                    "MEDIAFORGE_PROVIDER_CIRCUIT_OPEN_SECONDS",
                    "60",
                ).strip()
            )
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_PROVIDER_CIRCUIT_OPEN_SECONDS must be a number"
            ) from exc
        return cls(
            enabled=raw_enabled == "true",
            failure_threshold=failure_threshold,
            open_seconds=open_seconds,
        )


@dataclass
class _ProviderCircuitState:
    consecutive_failures: int = 0
    state: str = "CLOSED"
    opened_at: datetime | None = None
    open_until: datetime | None = None
    last_error: str | None = None
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None


class ProviderCircuitBreaker:
    """Small, observable circuit breaker used by a single API/Worker process.

    The breaker makes no network calls and protects its local process
    immediately. Its owner can persist ``export_state`` with a control-plane
    snapshot and restore it after a restart or active/passive failover.
    """

    def __init__(
        self,
        settings: ProviderCircuitBreakerSettings | None = None,
    ) -> None:
        self.settings = settings or ProviderCircuitBreakerSettings.from_env()
        self._states: dict[str, _ProviderCircuitState] = {}
        self._lock = RLock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _clean_name(provider_name: str) -> str:
        clean_name = provider_name.strip()
        if not clean_name:
            raise ValueError("provider circuit name is required")
        return clean_name

    @staticmethod
    def _safe_retry_after(retry_after_seconds: float | None) -> float:
        try:
            delay = float(retry_after_seconds)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(delay) or delay <= 0:
            return 0.0
        return min(delay, float(24 * 60 * 60))

    def _state_for(self, provider_name: str) -> _ProviderCircuitState:
        return self._states.setdefault(provider_name, _ProviderCircuitState())

    def is_available(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return whether the Provider may be selected, advancing OPEN to HALF_OPEN."""
        clean_name = self._clean_name(provider_name)
        if not self.settings.enabled:
            return True
        checked_at = now or self._now()
        with self._lock:
            state = self._state_for(clean_name)
            if state.state != "OPEN":
                return True
            if state.open_until is not None and checked_at >= state.open_until:
                state.state = "HALF_OPEN"
                return True
            return False

    def record_failure(
        self,
        provider_name: str,
        *,
        error: str,
        retry_after_seconds: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Record one Provider failure and open the circuit when warranted."""
        clean_name = self._clean_name(provider_name)
        failed_at = now or self._now()
        if not self.settings.enabled:
            return self.snapshot(clean_name, now=failed_at)
        with self._lock:
            state = self._state_for(clean_name)
            was_half_open = state.state == "HALF_OPEN"
            state.consecutive_failures += 1
            state.last_error = error[:1000]
            state.last_failure_at = failed_at
            should_open = (
                was_half_open
                or state.consecutive_failures >= self.settings.failure_threshold
            )
            opened = False
            if should_open:
                cooldown_seconds = max(
                    self.settings.open_seconds,
                    self._safe_retry_after(retry_after_seconds),
                )
                state.state = "OPEN"
                state.opened_at = failed_at
                state.open_until = failed_at + timedelta(seconds=cooldown_seconds)
                opened = True
            result = self._snapshot_locked(clean_name, state, now=failed_at)
            result["opened"] = opened
            result["cooldown_seconds"] = (
                max(
                    self.settings.open_seconds,
                    self._safe_retry_after(retry_after_seconds),
                )
                if opened
                else None
            )
            return result

    def record_success(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Close a recovered circuit after a successful Provider operation."""
        clean_name = self._clean_name(provider_name)
        succeeded_at = now or self._now()
        if not self.settings.enabled:
            return self.snapshot(clean_name, now=succeeded_at)
        with self._lock:
            state = self._state_for(clean_name)
            recovered = state.state in {"OPEN", "HALF_OPEN"}
            state.consecutive_failures = 0
            state.state = "CLOSED"
            state.opened_at = None
            state.open_until = None
            state.last_error = None
            state.last_success_at = succeeded_at
            result = self._snapshot_locked(clean_name, state, now=succeeded_at)
            result["recovered"] = recovered
            return result

    def manual_recover(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Close a circuit after an operator has independently verified health."""
        clean_name = self._clean_name(provider_name)
        recovered_at = now or self._now()
        with self._lock:
            state = self._state_for(clean_name)
            previous_state = state.state
            state.consecutive_failures = 0
            state.state = "CLOSED"
            state.opened_at = None
            state.open_until = None
            state.last_error = None
            state.last_success_at = recovered_at
            result = self._snapshot_locked(clean_name, state, now=recovered_at)
            result["manual_recovery"] = previous_state != "CLOSED"
            result["previous_state"] = previous_state
            return result

    def snapshot(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        clean_name = self._clean_name(provider_name)
        checked_at = now or self._now()
        with self._lock:
            if not self.settings.enabled:
                return {
                    "provider": clean_name,
                    "state": "DISABLED",
                    "available": True,
                    "consecutive_failures": 0,
                    "failure_threshold": self.settings.failure_threshold,
                    "opened_at": None,
                    "open_until": None,
                    "last_error": None,
                    "last_failure_at": None,
                    "last_success_at": None,
                }
            state = self._state_for(clean_name)
            if (
                state.state == "OPEN"
                and state.open_until is not None
                and checked_at >= state.open_until
            ):
                state.state = "HALF_OPEN"
            return self._snapshot_locked(clean_name, state, now=checked_at)

    def status_view(self, provider_names: list[str]) -> dict[str, object]:
        names = sorted({self._clean_name(name) for name in provider_names})
        return {
            "scope": "process",
            "enabled": self.settings.enabled,
            "failure_threshold": self.settings.failure_threshold,
            "open_seconds": self.settings.open_seconds,
            "providers": [self.snapshot(name) for name in names],
        }

    def export_state(self) -> dict[str, object]:
        """Return the non-secret state that a durable control plane can own."""
        with self._lock:
            if not self.settings.enabled:
                return {
                    "schema_version": "mediaforge-provider-circuit-v1",
                    "states": [],
                }
            checked_at = self._now()
            states = []
            for provider_name, state in sorted(self._states.items()):
                if (
                    state.state == "OPEN"
                    and state.open_until is not None
                    and checked_at >= state.open_until
                ):
                    state.state = "HALF_OPEN"
                states.append(
                    {
                        "provider": provider_name,
                        "consecutive_failures": state.consecutive_failures,
                        "state": state.state,
                        "opened_at": (
                            state.opened_at.isoformat() if state.opened_at else None
                        ),
                        "open_until": (
                            state.open_until.isoformat() if state.open_until else None
                        ),
                        "last_error": state.last_error,
                        "last_failure_at": (
                            state.last_failure_at.isoformat()
                            if state.last_failure_at
                            else None
                        ),
                        "last_success_at": (
                            state.last_success_at.isoformat()
                            if state.last_success_at
                            else None
                        ),
                    }
                )
            return {
                "schema_version": "mediaforge-provider-circuit-v1",
                "states": states,
            }

    def restore(self, payload: object) -> dict[str, int]:
        """Replace local state from a previously exported durable snapshot.

        Circuit state is a protective cache, not project truth. Invalid legacy
        rows are ignored so an old or partially malformed snapshot cannot stop
        the control plane from recovering its project and Job state.
        """
        with self._lock:
            self._states = {}
            if not self.settings.enabled:
                return {"restored": 0, "ignored": 0}
            if not isinstance(payload, dict):
                return {"restored": 0, "ignored": 0}
            rows = payload.get("states")
            if not isinstance(rows, list):
                return {"restored": 0, "ignored": 0}
            restored = 0
            ignored = 0
            for row in rows:
                if not isinstance(row, dict):
                    ignored += 1
                    continue
                try:
                    provider_name = self._clean_name(str(row.get("provider") or ""))
                    raw_failures = row.get("consecutive_failures", 0)
                    if isinstance(raw_failures, bool):
                        raise ValueError("failure count must be numeric")
                    failures = int(raw_failures)
                    state_name = str(row.get("state") or "CLOSED").upper()
                    if failures < 0 or state_name not in {
                        "CLOSED",
                        "OPEN",
                        "HALF_OPEN",
                    }:
                        raise ValueError("invalid circuit state")
                    restored_state = _ProviderCircuitState(
                        consecutive_failures=failures,
                        state=state_name,
                        opened_at=self._restore_datetime(row.get("opened_at")),
                        open_until=self._restore_datetime(row.get("open_until")),
                        last_error=(
                            str(row["last_error"])[:1000]
                            if row.get("last_error") is not None
                            else None
                        ),
                        last_failure_at=self._restore_datetime(
                            row.get("last_failure_at")
                        ),
                        last_success_at=self._restore_datetime(
                            row.get("last_success_at")
                        ),
                    )
                    # An OPEN circuit without a recovery timestamp must never
                    # create a permanent routing blackout after restoration.
                    if (
                        restored_state.state == "OPEN"
                        and restored_state.open_until is None
                    ):
                        restored_state.state = "HALF_OPEN"
                    self._states[provider_name] = restored_state
                    restored += 1
                except (TypeError, ValueError):
                    ignored += 1
            return {"restored": restored, "ignored": ignored}

    @staticmethod
    def _restore_datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("circuit timestamp requires a timezone")
        return parsed.astimezone(timezone.utc)

    def _snapshot_locked(
        self,
        provider_name: str,
        state: _ProviderCircuitState,
        *,
        now: datetime,
    ) -> dict[str, object]:
        return {
            "provider": provider_name,
            "state": state.state,
            "available": state.state != "OPEN",
            "consecutive_failures": state.consecutive_failures,
            "failure_threshold": self.settings.failure_threshold,
            "opened_at": state.opened_at.isoformat() if state.opened_at else None,
            "open_until": state.open_until.isoformat() if state.open_until else None,
            "last_error": state.last_error,
            "last_failure_at": (
                state.last_failure_at.isoformat() if state.last_failure_at else None
            ),
            "last_success_at": (
                state.last_success_at.isoformat() if state.last_success_at else None
            ),
        }


@dataclass(frozen=True)
class ProviderRegistration:
    provider: GenerationProvider
    priority: int = 0
    enabled: bool = True


@dataclass(frozen=True)
class RouteDecision:
    provider: GenerationProvider
    estimated_cost: float
    reason: str


class ProviderRouter:
    """Deterministic capability and budget router."""

    def __init__(
        self,
        registrations: list[ProviderRegistration],
        *,
        circuit_breaker: ProviderCircuitBreaker | None = None,
    ) -> None:
        self.registrations = registrations
        self.circuit_breaker = circuit_breaker or ProviderCircuitBreaker()

    def select(
        self,
        spec: GenerationSpec,
        *,
        exclude_provider_names: set[str] | None = None,
    ) -> RouteDecision:
        excluded = exclude_provider_names or set()
        capable = [
            registration
            for registration in self.registrations
            if registration.enabled
            and registration.provider.name not in excluded
            and registration.provider.supports(
                spec.provider_constraints.capability
            )
        ]
        availability = {
            registration.provider.name: self.circuit_breaker.is_available(
                registration.provider.name
            )
            for registration in capable
        }
        supported = [
            registration
            for registration in capable
            if availability[registration.provider.name]
        ]
        if not supported:
            unavailable = [
                registration.provider.name
                for registration in capable
                if not availability[registration.provider.name]
            ]
            if unavailable:
                raise ProviderRoutingError(
                    "all capable Providers are temporarily unavailable: "
                    + ", ".join(sorted(unavailable))
                )
            raise ProviderRoutingError(
                "no enabled Provider supports "
                f"{spec.provider_constraints.capability}"
            )

        candidates = []
        rejected: list[str] = []
        for registration in supported:
            estimated_cost = registration.provider.estimate_cost(spec)
            if estimated_cost <= spec.provider_constraints.max_cost:
                candidates.append((registration, estimated_cost))
            else:
                rejected.append(
                    f"{registration.provider.name}={estimated_cost:.4f}"
                )

        if not candidates:
            raise ProviderRoutingError(
                "all capable Providers exceed budget "
                f"{spec.provider_constraints.max_cost:.4f}: {', '.join(rejected)}"
            )

        candidates.sort(
            key=lambda item: (
                -item[0].priority,
                item[1],
                item[0].provider.name,
            )
        )
        registration, estimated_cost = candidates[0]
        return RouteDecision(
            provider=registration.provider,
            estimated_cost=estimated_cost,
            reason=(
                f"selected {registration.provider.name} by priority="
                f"{registration.priority}, estimated_cost={estimated_cost:.4f}"
            ),
        )
