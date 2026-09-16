from __future__ import annotations

from dataclasses import dataclass

from .contracts import Capability, GenerationSpec
from .providers import GenerationProvider


class ProviderRoutingError(RuntimeError):
    """Raised when no registered Provider can safely accept a request."""


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

    def __init__(self, registrations: list[ProviderRegistration]) -> None:
        self.registrations = registrations

    def select(
        self,
        spec: GenerationSpec,
        *,
        exclude_provider_names: set[str] | None = None,
    ) -> RouteDecision:
        excluded = exclude_provider_names or set()
        supported = [
            registration
            for registration in self.registrations
            if registration.enabled
            and registration.provider.name not in excluded
            and registration.provider.supports(
                spec.provider_constraints.capability
            )
        ]
        if not supported:
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
