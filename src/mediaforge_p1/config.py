from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .comfyui import ComfyUIProvider, ComfyWorkflowDefinition
from .contracts import Capability, GenerationSpec
from .providers import GenerationProvider, LocalCommandProvider, MockProvider
from .replicate import ReplicateVideoProvider


class UnavailableProvider:
    def __init__(
        self,
        *,
        name: str,
        reason: str,
        capabilities: Sequence[Capability],
    ) -> None:
        self.name = name
        self.reason = reason
        self._capabilities = set(capabilities)

    def supports(self, capability: Capability) -> bool:
        return capability in self._capabilities

    def estimate_cost(self, spec: GenerationSpec) -> float:
        return 0

    def generate(self, *args, **kwargs):
        raise RuntimeError(f"Provider {self.name} is not configured: {self.reason}")


@dataclass(frozen=True)
class ProviderBundle:
    provider: GenerationProvider
    mode: str
    configured: bool
    message: str
    capabilities: list[str]
    details: dict[str, Any] = field(default_factory=dict)

    def status_view(self) -> dict:
        payload = {
            "mode": self.mode,
            "provider": self.provider.name,
            "configured": self.configured,
            "message": self.message,
            "capabilities": self.capabilities,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def _load_workflow(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ComfyUI workflow file not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"ComfyUI workflow file cannot be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ComfyUI workflow file is not valid JSON: {path}") from exc

    if not isinstance(payload, dict) or not payload:
        raise ValueError("ComfyUI workflow must contain a non-empty JSON object")

    graph = payload.get("prompt", payload)
    if not isinstance(graph, dict) or not graph:
        raise ValueError("ComfyUI workflow prompt graph must be a non-empty object")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise ValueError(
                f"ComfyUI workflow node {node_id!r} must contain an inputs object"
            )
    return payload


def _workflow_sha256(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"ComfyUI workflow file cannot be hashed: {path}") from exc


def _workflow_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("mediaforge", {})
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValueError("ComfyUI workflow mediaforge metadata must be an object")
    return metadata


def _model_requirements(raw: Any) -> tuple[dict[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("ComfyUI model_requirements must be a list")
    requirements: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"ComfyUI model requirement {index} must be an object")
        folder = str(item.get("folder") or "").strip()
        name = str(item.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", folder):
            raise ValueError(
                f"ComfyUI model requirement {index} folder must contain only letters, numbers, _ or -"
            )
        if not name or len(name) > 255 or "\x00" in name:
            raise ValueError(f"ComfyUI model requirement {index} name is invalid")
        normalized = {"folder": folder, "name": name}
        if normalized not in requirements:
            requirements.append(normalized)
    return tuple(requirements)


def _workflow_capabilities(raw: Any) -> tuple[Capability, ...]:
    if raw is None:
        return (Capability.IMAGE_GENERATION,)
    values = raw.split(",") if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError("ComfyUI workflow capabilities must be a list or comma-separated string")
    capabilities: list[Capability] = []
    for index, value in enumerate(values):
        try:
            capability = Capability(str(value).strip())
        except ValueError as exc:
            supported = ", ".join(item.value for item in Capability)
            raise ValueError(
                f"ComfyUI workflow capability {index} is invalid; supported: {supported}"
            ) from exc
        if capability not in capabilities:
            capabilities.append(capability)
    if not capabilities:
        raise ValueError("ComfyUI workflow capabilities must contain at least one capability")
    return tuple(capabilities)


def _workflow_definition(
    path: Path,
    *,
    template_id: str,
    version: str | None,
    pinned_sha256: str | None,
    model_requirements: Any = None,
    capabilities: Any = None,
    require_pin: bool = False,
) -> ComfyWorkflowDefinition:
    payload = _load_workflow(path)
    metadata = _workflow_metadata(payload)
    resolved_template_id = template_id or str(metadata.get("template_id") or "").strip()
    if not resolved_template_id or len(resolved_template_id) > 160:
        raise ValueError("ComfyUI workflow template_id is required and must be <= 160 characters")
    resolved_version = version or str(metadata.get("version") or "").strip() or None
    actual_sha256 = _workflow_sha256(path)
    expected_sha256 = (pinned_sha256 or "").strip().lower() or None
    if expected_sha256 and not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("ComfyUI workflow sha256 must be a lowercase 64-character hex digest")
    if expected_sha256 and expected_sha256 != actual_sha256:
        raise ValueError(
            f"ComfyUI workflow SHA-256 mismatch for {path}: expected {expected_sha256}, got {actual_sha256}"
        )
    if require_pin and (not expected_sha256 or not resolved_version):
        raise ValueError(
            "COMFYUI_REQUIRE_WORKFLOW_PIN requires a workflow version and pinned SHA-256"
        )
    return ComfyWorkflowDefinition(
        template_id=resolved_template_id,
        workflow=payload,
        source_path=str(path),
        version=resolved_version,
        sha256=actual_sha256,
        model_requirements=_model_requirements(
            model_requirements
            if model_requirements is not None
            else metadata.get("model_requirements")
        ),
        capabilities=_workflow_capabilities(
            capabilities if capabilities is not None else metadata.get("capabilities")
        ),
    )


def load_comfyui_workflow_registry(
    path: Path,
    *,
    require_pin: bool,
) -> dict[str, ComfyWorkflowDefinition]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ComfyUI workflow registry file not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"ComfyUI workflow registry cannot be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ComfyUI workflow registry is not valid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "mediaforge-comfyui-workflow-registry-v1":
        raise ValueError("ComfyUI workflow registry schema_version must be mediaforge-comfyui-workflow-registry-v1")
    entries = payload.get("workflows")
    if not isinstance(entries, list) or not entries:
        raise ValueError("ComfyUI workflow registry must contain a non-empty workflows list")

    registry_root = path.expanduser().resolve().parent
    definitions: dict[str, ComfyWorkflowDefinition] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"ComfyUI workflow registry entry {index} must be an object")
        template_id = str(entry.get("template_id") or "").strip()
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            raise ValueError(f"ComfyUI workflow registry entry {index} path is required")
        workflow_path = Path(raw_path).expanduser()
        if not workflow_path.is_absolute():
            workflow_path = path.parent / workflow_path
        resolved_workflow_path = workflow_path.resolve()
        try:
            resolved_workflow_path.relative_to(registry_root)
        except ValueError as exc:
            raise ValueError(
                "ComfyUI workflow registry entries must resolve within the registry directory: "
                f"{raw_path}"
            ) from exc
        definition = _workflow_definition(
            resolved_workflow_path,
            template_id=template_id,
            version=str(entry.get("version") or "").strip() or None,
            pinned_sha256=str(entry.get("sha256") or "").strip() or None,
            model_requirements=entry.get("model_requirements"),
            capabilities=entry.get("capabilities"),
            require_pin=require_pin,
        )
        if definition.template_id in definitions:
            raise ValueError(f"ComfyUI workflow registry repeats template_id: {definition.template_id}")
        definitions[definition.template_id] = definition
    return definitions


def select_comfyui_template(
    workflows: dict[str, ComfyWorkflowDefinition],
    default_workflow: ComfyWorkflowDefinition | None,
    *,
    capability: Capability,
    default_template_id: str | None = None,
    environment_variable: str,
) -> str:
    """Choose the reviewed graph used by a platform generation capability.

    A registry can hold several reviewed graphs, but the normal production
    workflow does not let an Agent choose arbitrary graph names. Requiring an
    explicit default therefore prevents a healthy-looking ComfyUI deployment
    from failing only after a cost-bearing job reaches the Provider.
    """
    configured = (
        default_template_id
        if default_template_id is not None
        else os.getenv(environment_variable, "")
    ).strip()
    if workflows:
        eligible = {
            template_id: definition
            for template_id, definition in workflows.items()
            if capability in definition.capabilities
        }
        if not eligible:
            raise ValueError(
                "no reviewed ComfyUI workflow declares capability "
                f"{capability.value}"
            )
        if configured:
            if configured not in eligible:
                available = ", ".join(sorted(eligible))
                raise ValueError(
                    f"{environment_variable} is not registered for {capability.value}: "
                    f"{configured!r}; available: {available}"
                )
            return configured
        if len(eligible) == 1:
            return next(iter(eligible))
        available = ", ".join(sorted(eligible))
        raise ValueError(
            f"multiple ComfyUI workflows declare {capability.value}; set "
            f"{environment_variable} to one of: "
            f"{available}"
        )
    if default_workflow is None:  # pragma: no cover - defensive invariant
        raise ValueError("ComfyUI default workflow is not configured")
    if capability not in default_workflow.capabilities:
        raise ValueError(
            "ComfyUI default workflow does not declare capability "
            f"{capability.value}"
        )
    return default_workflow.template_id


def select_comfyui_image_template(
    workflows: dict[str, ComfyWorkflowDefinition],
    default_workflow: ComfyWorkflowDefinition | None,
    *,
    default_template_id: str | None = None,
) -> str:
    return select_comfyui_template(
        workflows,
        default_workflow,
        capability=Capability.IMAGE_GENERATION,
        default_template_id=default_template_id,
        environment_variable="MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID",
    )


def select_comfyui_video_template(
    workflows: dict[str, ComfyWorkflowDefinition],
    default_workflow: ComfyWorkflowDefinition | None,
    *,
    default_template_id: str | None = None,
) -> str:
    return select_comfyui_template(
        workflows,
        default_workflow,
        capability=Capability.IMAGE_TO_VIDEO,
        default_template_id=default_template_id,
        environment_variable="MEDIAFORGE_VIDEO_WORKFLOW_TEMPLATE_ID",
    )


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
    inclusive: bool = True,
) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    valid = value >= minimum if inclusive else value > minimum
    if not valid:
        operator = ">=" if inclusive else ">"
        raise ValueError(f"{name} must be {operator} {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    inclusive: bool = True,
) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    valid = value >= minimum if inclusive else value > minimum
    if not valid:
        operator = ">=" if inclusive else ">"
        raise ValueError(f"{name} must be {operator} {minimum}, got {value}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _build_provider_for_mode(mode: str) -> ProviderBundle:
    if mode == "mock":
        provider = MockProvider()
        return ProviderBundle(
            provider=provider,
            mode=mode,
            configured=True,
            message="Mock Provider is active.",
            capabilities=[
                Capability.IMAGE_GENERATION,
                Capability.IMAGE_TO_VIDEO,
            ],
        )

    if mode == "comfyui":
        base_url = os.getenv(
            "COMFYUI_BASE_URL",
            "http://127.0.0.1:8188",
        ).strip().rstrip("/")
        registry_raw = os.getenv("COMFYUI_WORKFLOW_REGISTRY_PATH", "").strip()
        workflow_path = Path(
            os.getenv(
                "COMFYUI_WORKFLOW_PATH",
                "data/workflows/comfyui_image_workflow.json",
            ).strip()
        ).expanduser()
        require_pin = False
        details = {
            "base_url": base_url,
            "workflow_path": str(workflow_path),
            "workflow_loaded": False,
            "workflow_registry_path": registry_raw or None,
            "workflow_pin_required": None,
        }
        try:
            require_pin = _env_bool("COMFYUI_REQUIRE_WORKFLOW_PIN", False)
            details["workflow_pin_required"] = require_pin
            workflows: dict[str, ComfyWorkflowDefinition] = {}
            workflow: ComfyWorkflowDefinition | None = None
            if registry_raw:
                registry_path = Path(registry_raw).expanduser()
                workflows = load_comfyui_workflow_registry(
                    registry_path,
                    require_pin=require_pin,
                )
                details["workflow_registry"] = {
                    "schema_version": "mediaforge-comfyui-workflow-registry-v1",
                    "workflow_count": len(workflows),
                    "workflows": [
                        definition.provenance_view(
                            requested_template_id=definition.template_id
                        )
                        for definition in workflows.values()
                    ],
                }
            else:
                workflow = _workflow_definition(
                    workflow_path,
                    template_id=os.getenv(
                        "COMFYUI_WORKFLOW_TEMPLATE_ID", "legacy-default"
                    ).strip(),
                    version=os.getenv("COMFYUI_WORKFLOW_VERSION", "").strip() or None,
                    pinned_sha256=os.getenv("COMFYUI_WORKFLOW_SHA256", "").strip() or None,
                    capabilities=os.getenv("COMFYUI_CAPABILITIES", "").strip() or None,
                    require_pin=require_pin,
                )
                details["workflow"] = workflow.provenance_view(
                    requested_template_id=workflow.template_id
                )
            configured_capabilities = set()
            for definition in workflows.values():
                configured_capabilities.update(definition.capabilities)
            if workflow is not None:
                configured_capabilities.update(workflow.capabilities)
            if not configured_capabilities:  # pragma: no cover - registry invariant
                raise ValueError("ComfyUI workflow registry declares no capabilities")
            image_template = (
                select_comfyui_image_template(workflows, workflow)
                if Capability.IMAGE_GENERATION in configured_capabilities
                else None
            )
            video_template = (
                select_comfyui_video_template(workflows, workflow)
                if Capability.IMAGE_TO_VIDEO in configured_capabilities
                else None
            )
            details["default_template_id"] = image_template
            details["default_video_template_id"] = video_template
            details["workflow_capabilities"] = [
                item.value for item in sorted(configured_capabilities, key=lambda item: item.value)
            ]
            timeout_seconds = _env_float(
                "COMFYUI_TIMEOUT_SECONDS",
                60.0,
                minimum=0,
                inclusive=False,
            )
            poll_interval_seconds = _env_float(
                "COMFYUI_POLL_INTERVAL_SECONDS",
                0.25,
                minimum=0,
            )
            estimated_cost = _env_float(
                "COMFYUI_ESTIMATED_COST",
                0.05,
                minimum=0,
            )
        except ValueError as exc:
            reason = str(exc)
            return ProviderBundle(
                provider=UnavailableProvider(
                    name="comfyui",
                    reason=reason,
                    capabilities=[Capability.IMAGE_GENERATION],
                ),
                mode=mode,
                configured=False,
                message=reason,
                capabilities=[Capability.IMAGE_GENERATION],
                details=details,
            )

        details["workflow_loaded"] = True
        details["timeout_seconds"] = timeout_seconds
        details["poll_interval_seconds"] = poll_interval_seconds
        details["estimated_cost"] = estimated_cost
        return ProviderBundle(
            provider=ComfyUIProvider(
                base_url=base_url,
                workflow={} if workflows else workflow.workflow,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                estimated_cost=estimated_cost,
                workflows=workflows,
                default_workflow=None if workflows else workflow,
                capabilities=configured_capabilities,
            ),
            mode=mode,
            configured=True,
            message="ComfyUI workflow is configured.",
            capabilities=[item.value for item in sorted(configured_capabilities, key=lambda item: item.value)],
            details=details,
        )

    if mode in {"local", "local-command"}:
        command = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_COMMAND", "").strip()
        health_command = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_HEALTH_COMMAND", "").strip()
        warmup_command = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_WARMUP_COMMAND", "").strip()
        details = {
            "execution": "local-gpu-command",
            "command_configured": bool(command),
            "health_command_configured": bool(health_command),
            "warmup_command_configured": bool(warmup_command),
        }
        try:
            timeout_seconds = _env_float(
                "MEDIAFORGE_LOCAL_PROVIDER_TIMEOUT_SECONDS",
                900.0,
                minimum=0,
                inclusive=False,
            )
            estimated_cost = _env_float(
                "MEDIAFORGE_LOCAL_PROVIDER_ESTIMATED_COST",
                0.0,
                minimum=0,
            )
        except ValueError as exc:
            reason = str(exc)
            return ProviderBundle(
                provider=UnavailableProvider(name="local-gpu-command", reason=reason, capabilities=[]),
                mode=mode,
                configured=False,
                message=reason,
                capabilities=[],
                details=details,
            )
        raw_capabilities = os.getenv(
            "MEDIAFORGE_LOCAL_PROVIDER_CAPABILITIES",
            "image_generation,image_to_video",
        )
        capabilities: list[Capability] = []
        for value in raw_capabilities.split(","):
            value = value.strip().lower()
            if not value:
                continue
            try:
                capability = Capability(value)
            except ValueError:
                return ProviderBundle(
                    provider=UnavailableProvider(name="local-gpu-command", reason=f"unsupported local Provider capability: {value}", capabilities=[]),
                    mode=mode,
                    configured=False,
                    message=f"unsupported local Provider capability: {value}",
                    capabilities=[],
                    details=details,
                )
            if capability not in capabilities:
                capabilities.append(capability)
        details.update({"timeout_seconds": timeout_seconds, "estimated_cost": estimated_cost, "capability_config": [item.value for item in capabilities]})
        if not command:
            reason = "missing MEDIAFORGE_LOCAL_PROVIDER_COMMAND"
            return ProviderBundle(
                provider=UnavailableProvider(name="local-gpu-command", reason=reason, capabilities=capabilities),
                mode=mode,
                configured=False,
                message=reason,
                capabilities=capabilities,
                details=details,
            )
        try:
            provider = LocalCommandProvider(command=command, capabilities=set(capabilities), timeout_seconds=timeout_seconds, estimated_cost=estimated_cost, health_command=health_command, warmup_command=warmup_command)
        except ValueError as exc:
            return ProviderBundle(
                provider=UnavailableProvider(name="local-gpu-command", reason=str(exc), capabilities=capabilities),
                mode=mode,
                configured=False,
                message=str(exc),
                capabilities=capabilities,
                details=details,
            )
        return ProviderBundle(
            provider=provider,
            mode=mode,
            configured=True,
            message="Local GPU command Provider is configured.",
            capabilities=capabilities,
            details=details,
        )

    if mode == "replicate":
        token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        version = os.getenv("REPLICATE_MODEL_VERSION", "").strip()
        base_url = os.getenv(
            "REPLICATE_API_BASE_URL",
            "https://api.replicate.com/v1",
        ).strip().rstrip("/")
        details = {
            "api_base_url": base_url,
            "credential_configured": bool(token),
            "model_version_configured": bool(version),
        }
        try:
            http_retry_attempts = _env_int(
                "REPLICATE_HTTP_RETRY_ATTEMPTS",
                2,
                minimum=0,
            )
            http_retry_backoff_seconds = _env_float(
                "REPLICATE_HTTP_RETRY_BACKOFF_SECONDS",
                0.5,
                minimum=0,
            )
            http_retry_max_delay_seconds = _env_float(
                "REPLICATE_HTTP_RETRY_MAX_DELAY_SECONDS",
                15.0,
                minimum=0,
                maximum=300,
            )
            timeout_seconds = _env_float(
                "REPLICATE_TIMEOUT_SECONDS",
                180.0,
                minimum=0,
                inclusive=False,
            )
            cancel_request_timeout_seconds = _env_float(
                "REPLICATE_CANCEL_REQUEST_TIMEOUT_SECONDS",
                15.0,
                minimum=1,
                maximum=300,
            )
            poll_interval_seconds = _env_float(
                "REPLICATE_POLL_INTERVAL_SECONDS",
                1.0,
                minimum=0,
            )
            raw_cancel_after = os.getenv("REPLICATE_CANCEL_AFTER_SECONDS", "").strip()
            cancel_after_seconds = (
                _env_float(
                    "REPLICATE_CANCEL_AFTER_SECONDS",
                    5.0,
                    minimum=5,
                    maximum=24 * 60 * 60,
                )
                if raw_cancel_after
                else None
            )
            webhook_url_template = os.getenv(
                "REPLICATE_WEBHOOK_URL_TEMPLATE",
                "",
            ).strip() or None
        except ValueError as exc:
            reason = str(exc)
            return ProviderBundle(
                provider=UnavailableProvider(
                    name="replicate-video",
                    reason=reason,
                    capabilities=[Capability.IMAGE_TO_VIDEO],
                ),
                mode=mode,
                configured=False,
                message=reason,
                capabilities=[Capability.IMAGE_TO_VIDEO],
                details=details,
            )
        details.update(
            {
                "http_retry_attempts": http_retry_attempts,
                "http_retry_backoff_seconds": http_retry_backoff_seconds,
                "http_retry_max_delay_seconds": http_retry_max_delay_seconds,
                "timeout_seconds": timeout_seconds,
                "cancel_request_timeout_seconds": cancel_request_timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
                "cancel_after_seconds": cancel_after_seconds,
                "webhook_url_template_configured": bool(webhook_url_template),
                "post_retry_requires_idempotency_key": True,
            }
        )
        missing = [
            name
            for name, value in {
                "REPLICATE_API_TOKEN": token,
                "REPLICATE_MODEL_VERSION": version,
            }.items()
            if not value
        ]
        if missing:
            reason = f"missing {', '.join(missing)}"
            return ProviderBundle(
                provider=UnavailableProvider(
                    name="replicate-video",
                    reason=reason,
                    capabilities=[Capability.IMAGE_TO_VIDEO],
                ),
                mode=mode,
                configured=False,
                message=reason,
                capabilities=[Capability.IMAGE_TO_VIDEO],
                details=details,
            )

        provider = ReplicateVideoProvider(
            api_token=token,
            version=version,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            http_retry_attempts=http_retry_attempts,
            http_retry_backoff_seconds=http_retry_backoff_seconds,
            http_retry_max_delay_seconds=http_retry_max_delay_seconds,
            cancel_after_seconds=cancel_after_seconds,
            cancel_request_timeout_seconds=cancel_request_timeout_seconds,
            webhook_url_template=webhook_url_template,
        )
        details.update(
            {
                "estimated_cost": provider.estimated_cost,
                "cancel_after_header": provider._cancel_after_header(),
            }
        )
        return ProviderBundle(
            provider=provider,
            mode=mode,
            configured=True,
            message="Replicate video Provider is configured.",
            capabilities=[Capability.IMAGE_TO_VIDEO],
            details=details,
        )

    reason = f"unsupported MEDIAFORGE_PROVIDER={mode}"
    return ProviderBundle(
        provider=UnavailableProvider(
            name=mode or "unknown",
            reason=reason,
            capabilities=[],
        ),
        mode=mode,
        configured=False,
        message=reason,
        capabilities=[],
    )


def build_provider_from_env() -> ProviderBundle:
    """Build the legacy single-provider configuration."""
    mode = os.getenv("MEDIAFORGE_PROVIDER", "mock").strip().lower()
    return _build_provider_for_mode(mode)


def build_provider_bundles_from_env() -> list[ProviderBundle]:
    """Build all configured providers while preserving their priority order.

    ``MEDIAFORGE_PROVIDERS`` is an additive multi-provider setting. The first
    provider has the highest routing priority. The legacy singular setting is
    used when the multi-provider setting is absent.
    """
    raw = os.getenv("MEDIAFORGE_PROVIDERS", "").strip()
    if not raw:
        return [build_provider_from_env()]

    modes: list[str] = []
    for value in raw.split(","):
        mode = value.strip().lower()
        if mode and mode not in modes:
            modes.append(mode)
    if not modes:
        raise ValueError("MEDIAFORGE_PROVIDERS must contain at least one provider")
    return [_build_provider_for_mode(mode) for mode in modes]
