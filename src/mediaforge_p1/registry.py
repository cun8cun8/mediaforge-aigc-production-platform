from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "registry_id": "license:cc0",
        "kind": "license",
        "match": "cc0",
        "match_type": "exact",
        "license": "CC0",
        "status": "approved",
        "evidence": "default policy",
    },
    {
        "registry_id": "license:commercial_use_allowed",
        "kind": "license",
        "match": "commercial_use_allowed",
        "match_type": "exact",
        "license": "commercial-use-allowed",
        "status": "approved",
        "evidence": "default policy",
    },
    {
        "registry_id": "license:licensed",
        "kind": "license",
        "match": "licensed",
        "match_type": "exact",
        "license": "licensed",
        "status": "approved",
        "evidence": "default policy",
    },
    {
        "registry_id": "license:mock_generated",
        "kind": "license",
        "match": "mock_generated",
        "match_type": "exact",
        "license": "internal-test",
        "status": "approved",
        "evidence": "deterministic mock output",
    },
    {
        "registry_id": "license:owned",
        "kind": "license",
        "match": "owned",
        "match_type": "exact",
        "license": "owned",
        "status": "approved",
        "evidence": "project owner attestation",
    },
    {
        "registry_id": "license:public_domain",
        "kind": "license",
        "match": "public_domain",
        "match_type": "exact",
        "license": "public-domain",
        "status": "approved",
        "evidence": "public-domain attestation",
    },
    {
        "registry_id": "license:user_supplied_or_project_owned",
        "kind": "license",
        "match": "user_supplied_or_project_owned",
        "match_type": "exact",
        "license": "project-owned",
        "status": "approved",
        "evidence": "creative brief ownership attestation",
    },
    {
        "registry_id": "provider:mock-provider",
        "kind": "provider",
        "match": "mock-provider",
        "match_type": "exact",
        "license": "internal-test",
        "status": "approved",
        "evidence": "built-in provider",
    },
    {
        "registry_id": "provider:comfyui",
        "kind": "provider",
        "match": "comfyui",
        "match_type": "exact",
        "license": "operator-managed",
        "status": "approved",
        "evidence": "self-hosted adapter contract",
    },
    {
        "registry_id": "provider:replicate-video",
        "kind": "provider",
        "match": "replicate-video",
        "match_type": "exact",
        "license": "provider-terms-required",
        "status": "approved",
        "evidence": "version-pinned adapter contract",
    },
    {
        "registry_id": "workflow:p0_mock_i2v",
        "kind": "workflow",
        "match": "p0_mock_i2v",
        "match_type": "exact",
        "license": "internal-test",
        "status": "approved",
        "evidence": "built-in workflow",
    },
    {
        "registry_id": "workflow:p1_mock_i2v",
        "kind": "workflow",
        "match": "p1_mock_i2v",
        "match_type": "exact",
        "license": "internal-test",
        "status": "approved",
        "evidence": "built-in workflow",
    },
    {
        "registry_id": "workflow:comfyui_image",
        "kind": "workflow",
        "match": "comfyui_image",
        "match_type": "exact",
        "license": "operator-managed",
        "status": "approved",
        "evidence": "reviewed ComfyUI workflow family",
    },
    {
        "registry_id": "workflow:provider_probe",
        "kind": "workflow",
        "match": "provider_probe",
        "match_type": "exact",
        "license": "internal-test",
        "status": "approved",
        "evidence": "provider probe workflow",
    },
    {
        "registry_id": "workflow:replicate_i2v",
        "kind": "workflow",
        "match": "replicate_i2v",
        "match_type": "exact",
        "license": "provider-terms-required",
        "status": "approved",
        "evidence": "version-pinned adapter workflow",
    },
    {
        "registry_id": "lora:cinematic_style",
        "kind": "lora",
        "match": "cinematic_style:",
        "match_type": "prefix",
        "license": "operator-managed",
        "status": "approved",
        "evidence": "reviewed style adapter family",
    },
    {
        "registry_id": "lora:mock_lora",
        "kind": "lora",
        "match": "mock_lora:",
        "match_type": "prefix",
        "license": "internal-test",
        "status": "approved",
        "evidence": "built-in test adapter family",
    },
)


@dataclass(frozen=True)
class RegistryMatch:
    kind: str
    identifier: str
    registered: bool
    registry_id: str | None = None
    license: str | None = None
    evidence: str | None = None
    status: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "registered": self.registered,
            "registry_id": self.registry_id,
            "license": self.license,
            "evidence": self.evidence,
            "status": self.status,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "reason": self.reason,
        }


class LicenseRegistry:
    """Small auditable registry adapter for project dependencies."""

    schema_version = "mediaforge-license-registry-v1"
    supported_kinds = {"license", "provider", "workflow", "lora", "asset"}

    def __init__(self, records: Iterable[dict[str, Any]], *, source: str) -> None:
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in records:
            record = dict(raw)
            self._validate_record(record)
            registry_id = str(record["registry_id"])
            if registry_id in seen_ids:
                raise ValueError(f"duplicate license registry id: {registry_id}")
            seen_ids.add(registry_id)
            normalized.append(record)
        self.records = normalized
        self.source = source

    @classmethod
    def from_env(cls) -> "LicenseRegistry":
        raw_path = os.getenv("MEDIAFORGE_LICENSE_REGISTRY_PATH", "").strip()
        if not raw_path:
            return cls(copy.deepcopy(DEFAULT_RECORDS), source="built-in defaults")
        path = Path(raw_path).expanduser()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"license registry file not found: {path}") from exc
        except OSError as exc:
            raise ValueError(f"license registry file cannot be read: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"license registry file is not valid JSON: {path}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("license registry must contain a records array")
        return cls(payload["records"], source=str(path))

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str) -> "LicenseRegistry":
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("license registry must contain a records array")
        return cls(payload["records"], source=source)

    @classmethod
    def _validate_record(cls, record: dict[str, Any]) -> None:
        required = {"registry_id", "kind", "match", "match_type", "license", "status"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(
                f"license registry record is missing fields: {', '.join(missing)}"
            )
        if str(record["kind"]) not in cls.supported_kinds:
            raise ValueError(f"unsupported license registry kind: {record['kind']}")
        if str(record["match_type"]) not in {"exact", "prefix"}:
            raise ValueError(f"unsupported license registry match type: {record['match_type']}")
        if not all(str(record[field]).strip() for field in required):
            raise ValueError("license registry fields must not be empty")

        valid_from = cls._parse_timestamp(record.get("valid_from"), "valid_from")
        valid_until = cls._parse_timestamp(record.get("valid_until"), "valid_until")
        if valid_from and valid_until and valid_from > valid_until:
            raise ValueError("license registry valid_from must not be after valid_until")
        if record.get("rights_uri") and not str(record["rights_uri"]).strip().startswith(("https://", "http://")):
            raise ValueError("license registry rights_uri must be an HTTP(S) URL")

    @staticmethod
    def _parse_timestamp(value: Any, field: str) -> datetime | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError(f"license registry {field} must be an ISO-8601 timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"license registry {field} must be an ISO-8601 timestamp"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(f"license registry {field} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def resolve(
        self,
        kind: str,
        identifier: str,
        *,
        now: datetime | None = None,
    ) -> RegistryMatch:
        normalized_kind = str(kind).strip().lower()
        normalized_identifier = str(identifier).strip().lower()
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        for record in self.records:
            if str(record["kind"]).lower() != normalized_kind:
                continue
            match = str(record["match"]).lower()
            matches = (
                normalized_identifier == match
                if record["match_type"] == "exact"
                else normalized_identifier.startswith(match)
            )
            if matches:
                valid_from = self._parse_timestamp(record.get("valid_from"), "valid_from")
                valid_until = self._parse_timestamp(record.get("valid_until"), "valid_until")
                status = str(record["status"]).lower()
                reason = None
                approved = status == "approved"
                if valid_from and observed_at < valid_from:
                    approved = False
                    reason = "not_yet_effective"
                elif valid_until and observed_at > valid_until:
                    approved = False
                    reason = "expired"
                elif not approved:
                    reason = f"status_{status}"
                return RegistryMatch(
                    kind=normalized_kind,
                    identifier=identifier,
                    registered=approved,
                    registry_id=str(record["registry_id"]),
                    license=str(record["license"]),
                    evidence=str(record.get("evidence") or ""),
                    status=status,
                    valid_from=valid_from.isoformat() if valid_from else None,
                    valid_until=valid_until.isoformat() if valid_until else None,
                    reason=reason,
                )
        return RegistryMatch(
            kind=normalized_kind,
            identifier=identifier,
            registered=False,
            reason="not_registered",
        )

    def assess(self, requirements: Iterable[tuple[str, str]]) -> dict[str, Any]:
        unique_requirements = list(dict.fromkeys(requirements))
        matches = [
            self.resolve(kind, identifier)
            for kind, identifier in unique_requirements
        ]
        unregistered = [match.as_dict() for match in matches if not match.registered]
        return {
            "passed": not unregistered,
            "checked_count": len(matches),
            "registered_count": sum(1 for match in matches if match.registered),
            "unregistered_count": len(unregistered),
            "unregistered": unregistered,
            "matches": [match.as_dict() for match in matches],
        }

    def view(self) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc)
        approved = 0
        expired = 0
        for record in self.records:
            match = self.resolve(str(record["kind"]), str(record["match"]), now=observed_at)
            approved += int(match.registered)
            expired += int(match.reason == "expired")
        public_fields = (
            "registry_id",
            "kind",
            "match",
            "match_type",
            "license",
            "status",
            "evidence",
            "valid_from",
            "valid_until",
            "rights_uri",
        )
        public_records = [
            {field: record[field] for field in public_fields if field in record}
            for record in self.records
        ]
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "record_count": len(self.records),
                "approved_count": approved,
                "revoked_count": len(self.records) - approved,
                "expired_count": expired,
            },
            "records": copy.deepcopy(public_records),
        }
