from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


AUDIT_CHAIN_SCHEMA = "mediaforge-audit-chain-v1"


def _json_value(value: Any) -> Any:
    """Normalize values before hashing so equivalent audit records hash identically."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def event_commitment(project_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact event fields protected by one hash-chain link."""
    return {
        "schema_version": AUDIT_CHAIN_SCHEMA,
        "project_id": project_id,
        "sequence": event.get("sequence"),
        "previous_hash": event.get("previous_hash"),
        "action": event.get("action"),
        "actor": event.get("actor"),
        "message": event.get("message"),
        "shot_id": event.get("shot_id"),
        "details": event.get("details", {}),
        "trace_id": event.get("trace_id"),
        "occurred_at": event.get("occurred_at"),
    }


def event_hash(project_id: str, event: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(event_commitment(project_id, event)).encode("utf-8")
    ).hexdigest()


def verify_event_chain(project_id: str, events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify ordering, linkage and digest integrity without mutating project state."""
    checked_at = datetime.now().astimezone().isoformat()
    if not events:
        return {
            "schema_version": AUDIT_CHAIN_SCHEMA,
            "project_id": project_id,
            "checked_at": checked_at,
            "event_count": 0,
            "sealed_event_count": 0,
            "head_hash": None,
            "verified": True,
            "integrity_status": "EMPTY",
            "issues": [],
        }

    required = ("sequence", "event_hash")
    sealed = [
        event
        for event in events
        if all(event.get(field) is not None for field in required)
    ]
    if len(sealed) != len(events):
        return {
            "schema_version": AUDIT_CHAIN_SCHEMA,
            "project_id": project_id,
            "checked_at": checked_at,
            "event_count": len(events),
            "sealed_event_count": len(sealed),
            "head_hash": None,
            "verified": False,
            "integrity_status": "LEGACY_UNSEALED",
            "issues": [
                {
                    "code": "unsealed_event",
                    "message": "One or more historical audit events have no hash-chain commitment.",
                }
            ],
        }

    issues: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for index, event in enumerate(events, start=1):
        sequence = event.get("sequence")
        if sequence != index:
            issues.append(
                {
                    "code": "unexpected_sequence",
                    "sequence": sequence,
                    "message": f"Expected sequence {index}.",
                }
            )
        if event.get("previous_hash") != previous_hash:
            issues.append(
                {
                    "code": "broken_link",
                    "sequence": sequence,
                    "message": "Previous hash does not match the preceding event.",
                }
            )
        expected_hash = event_hash(project_id, event)
        actual_hash = str(event.get("event_hash") or "")
        if actual_hash != expected_hash:
            issues.append(
                {
                    "code": "digest_mismatch",
                    "sequence": sequence,
                    "message": "Event content does not match its recorded digest.",
                }
            )
        previous_hash = actual_hash

    return {
        "schema_version": AUDIT_CHAIN_SCHEMA,
        "project_id": project_id,
        "checked_at": checked_at,
        "event_count": len(events),
        "sealed_event_count": len(events),
        "head_hash": previous_hash,
        "verified": not issues,
        "integrity_status": "VERIFIED" if not issues else "INVALID",
        "issues": issues,
    }
