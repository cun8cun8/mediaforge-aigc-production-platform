from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hmac
import os
from pathlib import Path
from threading import Lock
from time import monotonic


class MetricsAccessConfigurationError(ValueError):
    """Raised when protected metrics access is configured unsafely."""


@dataclass(frozen=True)
class MetricsAccessPolicy:
    """Access policy for scrape endpoints without exposing the token in status views."""

    mode: str
    _token: str = ""

    @classmethod
    def from_env(cls) -> "MetricsAccessPolicy":
        mode = os.getenv("MEDIAFORGE_METRICS_AUTH_MODE", "public").strip().lower()
        if mode not in {"public", "token"}:
            raise MetricsAccessConfigurationError(
                "MEDIAFORGE_METRICS_AUTH_MODE must be public or token"
            )
        if mode == "public":
            return cls(mode="public")
        token_file = os.getenv("MEDIAFORGE_METRICS_TOKEN_FILE", "").strip()
        if not token_file:
            raise MetricsAccessConfigurationError(
                "MEDIAFORGE_METRICS_TOKEN_FILE is required when metrics auth mode is token"
            )
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MetricsAccessConfigurationError(
                "MEDIAFORGE_METRICS_TOKEN_FILE cannot be read"
            ) from exc
        if len(token) < 16:
            raise MetricsAccessConfigurationError(
                "MEDIAFORGE_METRICS_TOKEN_FILE must contain at least 16 characters"
            )
        return cls(mode="token", _token=token)

    @property
    def protected(self) -> bool:
        return self.mode == "token"

    def permits(self, authorization: str | None) -> bool:
        if not self.protected:
            return True
        scheme, _, token = (authorization or "").partition(" ")
        return bool(
            scheme.lower() == "bearer"
            and token.strip()
            and hmac.compare_digest(token.strip(), self._token)
        )

    def status_view(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "protected": self.protected,
            "token_configured": bool(self._token),
        }


class HttpMetrics:
    def __init__(self) -> None:
        self.started_at = monotonic()
        self._lock = Lock()
        self.requests = Counter()
        self.duration_seconds = Counter()

    def observe(self, *, method: str, route: str, status_code: int, duration: float) -> None:
        key = (method.upper(), route, str(status_code))
        with self._lock:
            self.requests[key] += 1
            self.duration_seconds[(method.upper(), route)] += duration

    def prometheus(self) -> str:
        lines = [
            "# HELP mediaforge_http_requests_total Total HTTP requests handled by MediaForge.",
            "# TYPE mediaforge_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), value in sorted(self.requests.items()):
                lines.append(
                    "mediaforge_http_requests_total{"
                    f'method="{self._label(method)}",'
                    f'route="{self._label(route)}",'
                    f'status="{self._label(status)}"'
                    f"}} {value}"
                )
            lines.extend(
                [
                    "# HELP mediaforge_http_request_duration_seconds_sum HTTP request duration sum.",
                    "# TYPE mediaforge_http_request_duration_seconds_sum counter",
                ]
            )
            for (method, route), value in sorted(self.duration_seconds.items()):
                lines.append(
                    "mediaforge_http_request_duration_seconds_sum{"
                    f'method="{self._label(method)}",'
                    f'route="{self._label(route)}"'
                    f"}} {value:.6f}"
                )
        lines.extend(
            [
                "# HELP mediaforge_process_uptime_seconds Process uptime in seconds.",
                "# TYPE mediaforge_process_uptime_seconds gauge",
                f"mediaforge_process_uptime_seconds {monotonic() - self.started_at:.3f}",
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _label(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
        )

class RuntimeMetrics:
    '''Process-local metrics for Provider execution and estimated spend.'''

    def __init__(self) -> None:
        self._lock = Lock()
        self.attempts = Counter()
        self.duration_seconds = Counter()
        self.estimated_cost = Counter()

    def observe_provider(
        self,
        *,
        provider: str,
        outcome: str,
        duration: float,
        estimated_cost: float,
    ) -> None:
        provider_name = str(provider or 'unknown')
        result = str(outcome or 'unknown')
        with self._lock:
            self.attempts[(provider_name, result)] += 1
            self.duration_seconds[provider_name] += max(float(duration), 0.0)
            self.estimated_cost[provider_name] += max(float(estimated_cost), 0.0)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            providers = sorted(
                {
                    provider
                    for provider, _outcome in self.attempts
                }
                | set(self.duration_seconds)
                | set(self.estimated_cost)
            )
            rows = []
            for provider in providers:
                outcomes = {
                    outcome: count
                    for (name, outcome), count in self.attempts.items()
                    if name == provider
                }
                rows.append(
                    {
                        'provider': provider,
                        'attempts': sum(outcomes.values()),
                        'outcomes': outcomes,
                        'duration_seconds': round(
                            self.duration_seconds[provider],
                            6,
                        ),
                        'estimated_cost': round(
                            self.estimated_cost[provider],
                            6,
                        ),
                    }
                )
            return {
                'schema_version': 'mediaforge-runtime-metrics-v1',
                'providers': rows,
                'total_attempts': sum(self.attempts.values()),
                'total_estimated_cost': round(
                    sum(self.estimated_cost.values()),
                    6,
                ),
            }

    def prometheus(self) -> str:
        lines = [
            '# HELP mediaforge_provider_attempts_total Provider generation attempts by outcome.',
            '# TYPE mediaforge_provider_attempts_total counter',
            '# HELP mediaforge_provider_duration_seconds_sum Provider generation duration sum.',
            '# TYPE mediaforge_provider_duration_seconds_sum counter',
            '# HELP mediaforge_provider_estimated_cost_total Estimated Provider spend.',
            '# TYPE mediaforge_provider_estimated_cost_total counter',
        ]
        with self._lock:
            for (provider, outcome), value in sorted(self.attempts.items()):
                lines.append(
                    'mediaforge_provider_attempts_total{'
                    f'provider="{HttpMetrics._label(provider)}",'
                    f'outcome="{HttpMetrics._label(outcome)}"'
                    f'}} {value}'
                )
            for provider, value in sorted(self.duration_seconds.items()):
                lines.append(
                    'mediaforge_provider_duration_seconds_sum{'
                    f'provider="{HttpMetrics._label(provider)}"'
                    f'}} {value:.6f}'
                )
            for provider, value in sorted(self.estimated_cost.items()):
                lines.append(
                    'mediaforge_provider_estimated_cost_total{'
                    f'provider="{HttpMetrics._label(provider)}"'
                    f'}} {value:.6f}'
                )
        return '\n'.join(lines) + '\n'
