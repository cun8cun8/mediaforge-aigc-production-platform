from __future__ import annotations

"""Small, durable LLMOps primitives kept independent of a vendor SDK.

The Studio stores prompt versions alongside the project that used them.  This
keeps a release reproducible even when a hosted tracing product is unavailable.
"""

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z][a-z0-9_.-]{0,119})\s*\}\}")


class PromptValidationError(ValueError):
    """Raised when a prompt definition cannot be made reproducible."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_prompt_key(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if not _KEY_PATTERN.fullmatch(normalized):
        raise PromptValidationError(
            "prompt key must start with a lowercase letter and contain only lowercase letters, digits, ., _ or -"
        )
    return normalized


def validate_template(template: str) -> tuple[str, list[str]]:
    normalized = str(template or "").strip()
    if not normalized or len(normalized) > 16_000:
        raise PromptValidationError("prompt template must contain 1 to 16000 characters")
    # A dangling delimiter is nearly always a production configuration mistake.
    if normalized.count("{{") != normalized.count("}}"):
        raise PromptValidationError("prompt template contains an unmatched variable delimiter")
    variables = sorted(set(_VARIABLE_PATTERN.findall(normalized)))
    for fragment in re.findall(r"\{\{(.*?)\}\}", normalized, flags=re.DOTALL):
        if not _KEY_PATTERN.fullmatch(fragment.strip()):
            raise PromptValidationError("prompt variables must use lowercase dotted names")
    return normalized, variables


def default_prompt_versions() -> list[dict[str, Any]]:
    """Return immutable defaults for a newly created project."""
    defaults = [
        (
            "planning.story",
            "Story planning brief: {{brief.title}}. Premise: {{brief.premise}}. Genre: {{brief.genre}}. Visual direction: {{brief.style}}.",
            "Story bible baseline",
        ),
        (
            "planning.storyboard",
            "Storyboard {{brief.title}} as {{brief.duration_seconds}} seconds with consistent characters: {{brief.characters}}.",
            "Storyboard baseline",
        ),
        (
            "quality.review",
            "Review generated media for {{project.project_id}} against continuity, rights, quality and delivery gates.",
            "Quality evaluation baseline",
        ),
    ]
    records: list[dict[str, Any]] = []
    for key, template, label in defaults:
        records.append(
            build_prompt_version(
                records,
                key=key,
                template=template,
                label=label,
                actor="system",
                activate=True,
            )
        )
    return records


def build_prompt_version(
    records: list[dict[str, Any]],
    *,
    key: str,
    template: str,
    label: str = "",
    actor: str = "studio-user",
    activate: bool = False,
) -> dict[str, Any]:
    normalized_key = validate_prompt_key(key)
    normalized_template, variables = validate_template(template)
    same_key = [item for item in records if item.get("key") == normalized_key]
    revision = max((int(item.get("version", 0)) for item in same_key), default=0) + 1
    payload = {
        "prompt_id": f"prompt_{uuid4().hex[:16]}",
        "key": normalized_key,
        "version": revision,
        "label": str(label or "").strip()[:240],
        "template": normalized_template,
        "variables": variables,
        "status": "ACTIVE" if activate else "DRAFT",
        "created_at": _now(),
        "created_by": str(actor or "studio-user")[:120],
    }
    payload["sha256"] = _digest(
        {name: payload[name] for name in ("key", "version", "template", "variables")}
    )
    return payload


def activate_prompt_version(records: list[dict[str, Any]], prompt_id: str) -> dict[str, Any]:
    target = next((item for item in records if item.get("prompt_id") == prompt_id), None)
    if not target:
        raise PromptValidationError("prompt version was not found")
    for item in records:
        if item.get("key") == target["key"]:
            item["status"] = "ACTIVE" if item is target else "ARCHIVED"
    return target


def prompt_registry_view(records: list[dict[str, Any]]) -> dict[str, Any]:
    entries = sorted(
        (copy.deepcopy(item) for item in records),
        key=lambda item: (str(item.get("key") or ""), -int(item.get("version") or 0)),
    )
    active = {item["key"]: item for item in entries if item.get("status") == "ACTIVE"}
    return {
        "schema_version": "mediaforge-prompt-registry-v1",
        "prompt_count": len(entries),
        "active_count": len(active),
        "active": active,
        "prompts": entries,
    }


def render_prompt(prompt: dict[str, Any], values: dict[str, Any]) -> str:
    """Render a restricted dotted-variable template without evaluating code."""

    template = str(prompt.get("template") or "")

    def lookup(name: str) -> str:
        current: Any = values
        for part in name.split("."):
            if not isinstance(current, dict) or part not in current:
                raise PromptValidationError(f"prompt variable is unavailable: {name}")
            current = current[part]
        if isinstance(current, (list, tuple)):
            return ", ".join(str(item) for item in current)
        if isinstance(current, (dict, set)):
            return json.dumps(current, ensure_ascii=True, sort_keys=True)
        return str(current)

    return _VARIABLE_PATTERN.sub(lambda match: lookup(match.group(1)), template)

