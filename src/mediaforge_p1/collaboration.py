from __future__ import annotations

"""Small, dependency-free RGA document used for collaborative project notes.

Operations are idempotent and commute when their causal predecessors arrive.
The service keeps the document state durable; WebSocket delivery is only an
optimization, so reconnecting clients can request operations after a cursor.
"""

from copy import deepcopy
from typing import Any


ROOT_ID = "root"
SCHEMA_VERSION = "mediaforge-rga-document-v1"


class CollaborationDocumentError(ValueError):
    pass


def empty_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": {},
        "tombstones": [],
        "operation_count": 0,
    }


def _clean_identifier(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 240:
        raise CollaborationDocumentError(f"{field} is required and must be <= 240 characters")
    return clean


def _document_state(document: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(document or empty_document())
    if state.get("schema_version") != SCHEMA_VERSION:
        raise CollaborationDocumentError("collaboration document has an unsupported schema")
    if not isinstance(state.get("nodes"), dict) or not isinstance(state.get("tombstones"), list):
        raise CollaborationDocumentError("collaboration document state is malformed")
    return state


def visible_node_ids(document: dict[str, Any]) -> list[str]:
    state = _document_state(document)
    nodes = state["nodes"]
    children: dict[str, list[str]] = {ROOT_ID: []}
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        after_id = str(node.get("after_id") or ROOT_ID)
        children.setdefault(after_id, []).append(node_id)
    for values in children.values():
        values.sort()

    result: list[str] = []
    visiting: set[str] = set()

    def visit(parent_id: str) -> None:
        for node_id in children.get(parent_id, []):
            if node_id in visiting:
                continue
            visiting.add(node_id)
            result.append(node_id)
            visit(node_id)

    visit(ROOT_ID)
    return result


def document_text(document: dict[str, Any]) -> str:
    state = _document_state(document)
    tombstones = {str(item) for item in state["tombstones"]}
    return "".join(
        str(state["nodes"][node_id].get("value") or "")
        for node_id in visible_node_ids(state)
        if node_id not in tombstones
    )


def apply_operations(
    document: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    *,
    max_nodes: int = 32_000,
    max_text_length: int = 16_000,
) -> tuple[dict[str, Any], int]:
    state = _document_state(document)
    if not isinstance(operations, list) or not operations:
        raise CollaborationDocumentError("at least one collaboration operation is required")
    if len(operations) > 2_000:
        raise CollaborationDocumentError("at most 2000 collaboration operations may be submitted at once")
    nodes: dict[str, dict[str, Any]] = state["nodes"]
    tombstones = {str(item) for item in state["tombstones"]}
    changed = 0

    for raw in operations:
        if not isinstance(raw, dict):
            raise CollaborationDocumentError("collaboration operations must be objects")
        kind = str(raw.get("kind") or "").strip().lower()
        op_id = _clean_identifier(raw.get("op_id"), "operation op_id")
        if kind == "insert":
            value = raw.get("value")
            if not isinstance(value, str) or len(value) != 1:
                raise CollaborationDocumentError("insert operations must contain exactly one character")
            after_id = str(raw.get("after_id") or ROOT_ID).strip() or ROOT_ID
            existing = nodes.get(op_id)
            candidate = {"after_id": after_id, "value": value}
            if existing is not None:
                if {
                    "after_id": str(existing.get("after_id") or ROOT_ID),
                    "value": str(existing.get("value") or ""),
                } != candidate:
                    raise CollaborationDocumentError("operation id conflicts with an existing insert")
                continue
            if len(nodes) >= max_nodes:
                raise CollaborationDocumentError("collaboration document reached its node limit")
            nodes[op_id] = candidate
            changed += 1
        elif kind == "delete":
            target_ids = raw.get("target_ids")
            if not isinstance(target_ids, list) or not target_ids or len(target_ids) > 2_000:
                raise CollaborationDocumentError("delete operations require 1 to 2000 target_ids")
            for target_id in target_ids:
                clean_target = _clean_identifier(target_id, "delete target_id")
                if clean_target not in tombstones:
                    tombstones.add(clean_target)
                    changed += 1
        else:
            raise CollaborationDocumentError("operation kind must be insert or delete")

    state["tombstones"] = sorted(tombstones)
    state["operation_count"] = int(state.get("operation_count") or 0) + len(operations)
    if len(document_text(state)) > max_text_length:
        raise CollaborationDocumentError("collaboration document text exceeds 16000 characters")
    return state, changed


def replacement_operations(document: dict[str, Any] | None, text: str, *, actor: str) -> list[dict[str, Any]]:
    if len(text) > 16_000:
        raise CollaborationDocumentError("collaboration document text must be <= 16000 characters")
    clean_actor = _clean_identifier(actor, "document actor")
    state = _document_state(document)
    previous = visible_node_ids(state)
    operations: list[dict[str, Any]] = []
    if previous:
        operations.append(
            {
                "kind": "delete",
                "op_id": f"{clean_actor}:replace:delete:{int(state.get('operation_count') or 0) + 1}",
                "target_ids": previous,
            }
        )
    after_id = ROOT_ID
    offset = int(state.get("operation_count") or 0) + 2
    for index, character in enumerate(text):
        op_id = f"{clean_actor}:replace:{offset + index}"
        operations.append(
            {"kind": "insert", "op_id": op_id, "after_id": after_id, "value": character}
        )
        after_id = op_id
    return operations or [
        {
            "kind": "delete",
            "op_id": f"{clean_actor}:replace:empty:{offset}",
            "target_ids": previous or [ROOT_ID],
        }
    ]
