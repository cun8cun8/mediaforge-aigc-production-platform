from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .audit_integrity import AUDIT_CHAIN_SCHEMA, canonical_json

if TYPE_CHECKING:
    from .enterprise_runtime import ObjectStorage


AUDIT_ANCHOR_SCHEMA = "mediaforge-audit-anchor-v1"


class AuditAnchorError(ValueError):
    """Raised when an external audit-chain anchor cannot be safely verified."""


def _environment_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuditAnchorSettings:
    mode: str
    retention_days: int
    object_lock_mode: str
    key_prefix: str
    url: str
    allowed_hosts: frozenset[str]
    allow_insecure_http: bool
    timeout_seconds: float
    bearer_token: str
    signing_secret: str

    @classmethod
    def from_env(cls) -> "AuditAnchorSettings":
        mode = os.getenv("MEDIAFORGE_AUDIT_ANCHOR_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "object_lock", "http"}:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_MODE must be disabled, object_lock or http"
            )
        try:
            retention_days = int(os.getenv("MEDIAFORGE_AUDIT_ANCHOR_RETENTION_DAYS", "3650"))
        except ValueError as exc:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_RETENTION_DAYS must be an integer"
            ) from exc
        if not 1 <= retention_days <= 36500:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_RETENTION_DAYS must be between 1 and 36500"
            )
        object_lock_mode = os.getenv(
            "MEDIAFORGE_AUDIT_ANCHOR_OBJECT_LOCK_MODE", "COMPLIANCE"
        ).strip().upper()
        if object_lock_mode not in {"COMPLIANCE", "GOVERNANCE"}:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_OBJECT_LOCK_MODE must be COMPLIANCE or GOVERNANCE"
            )
        key_prefix = os.getenv("MEDIAFORGE_AUDIT_ANCHOR_KEY_PREFIX", "audit-anchors").strip().strip("/")
        if not key_prefix or any(part in {"", ".", ".."} or ":" in part for part in key_prefix.split("/")):
            raise AuditAnchorError("MEDIAFORGE_AUDIT_ANCHOR_KEY_PREFIX is invalid")
        url = os.getenv("MEDIAFORGE_AUDIT_ANCHOR_URL", "").strip()
        allowed_hosts = frozenset(
            host.strip().lower()
            for host in os.getenv("MEDIAFORGE_AUDIT_ANCHOR_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        allow_insecure_http = _environment_flag(
            "MEDIAFORGE_AUDIT_ANCHOR_ALLOW_INSECURE_HTTP"
        )
        try:
            timeout_seconds = float(
                os.getenv("MEDIAFORGE_AUDIT_ANCHOR_TIMEOUT_SECONDS", "10")
            )
        except ValueError as exc:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_TIMEOUT_SECONDS must be a number"
            ) from exc
        if not 0.1 <= timeout_seconds <= 120:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_TIMEOUT_SECONDS must be between 0.1 and 120"
            )
        settings = cls(
            mode=mode,
            retention_days=retention_days,
            object_lock_mode=object_lock_mode,
            key_prefix=key_prefix,
            url=url,
            allowed_hosts=allowed_hosts,
            allow_insecure_http=allow_insecure_http,
            timeout_seconds=timeout_seconds,
            bearer_token=os.getenv("MEDIAFORGE_AUDIT_ANCHOR_BEARER_TOKEN", "").strip(),
            signing_secret=os.getenv("MEDIAFORGE_AUDIT_ANCHOR_SIGNING_SECRET", "").strip(),
        )
        if settings.mode == "http":
            settings._validate_http_destination()
        return settings

    def _validate_http_destination(self) -> None:
        parsed = urlsplit(self.url)
        if not parsed.hostname or parsed.scheme not in {"https", "http"}:
            raise AuditAnchorError("MEDIAFORGE_AUDIT_ANCHOR_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise AuditAnchorError("MEDIAFORGE_AUDIT_ANCHOR_URL must not contain credentials or a fragment")
        if parsed.scheme != "https" and not self.allow_insecure_http:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_URL must use HTTPS unless MEDIAFORGE_AUDIT_ANCHOR_ALLOW_INSECURE_HTTP=true"
            )
        if not self.allowed_hosts:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_ALLOWED_HOSTS is required when HTTP anchoring is enabled"
            )
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise AuditAnchorError(
                "MEDIAFORGE_AUDIT_ANCHOR_URL host is not in MEDIAFORGE_AUDIT_ANCHOR_ALLOWED_HOSTS"
            )


class AuditAnchorStore:
    """Commit verified audit-chain heads to a separately administered immutable sink."""

    def __init__(
        self,
        output_root: Path,
        storage: "ObjectStorage",
        settings: AuditAnchorSettings | None = None,
    ) -> None:
        self.output_root = output_root
        self.storage = storage
        self.settings = settings or AuditAnchorSettings.from_env()

    @property
    def configured(self) -> bool:
        return self.settings.mode != "disabled"

    def status_view(self) -> dict[str, Any]:
        storage_status = self.storage.status_view()
        object_lock_ready = (
            storage_status.get("mode") in {"s3", "minio", "object"}
            and bool(storage_status.get("configured"))
            and bool(storage_status.get("driver_ready"))
        )
        http_ready = bool(self.settings.url and self.settings.allowed_hosts)
        ready = (
            self.settings.mode == "disabled"
            or (self.settings.mode == "object_lock" and object_lock_ready)
            or (self.settings.mode == "http" and http_ready)
        )
        return {
            "schema_version": AUDIT_ANCHOR_SCHEMA,
            "configured": self.configured,
            "ready": ready,
            "mode": self.settings.mode,
            "retention_days": self.settings.retention_days,
            "object_lock_mode": self.settings.object_lock_mode if self.settings.mode == "object_lock" else None,
            "signing_configured": bool(self.settings.signing_secret),
            "bearer_token_configured": bool(self.settings.bearer_token),
            "destination_allowlist_configured": bool(self.settings.allowed_hosts),
            "object_storage_ready": object_lock_ready if self.settings.mode == "object_lock" else None,
        }

    def anchor(self, project_id: str, integrity: Mapping[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise AuditAnchorError("audit anchoring is disabled")
        if not integrity.get("verified") or not integrity.get("head_hash"):
            raise AuditAnchorError("only a verified, non-empty audit chain can be externally anchored")
        anchor_id = f"anchor_{uuid4().hex}"
        anchored_at = datetime.now(timezone.utc)
        commitment = {
            "schema_version": AUDIT_ANCHOR_SCHEMA,
            "anchor_id": anchor_id,
            "project_id": project_id,
            "anchored_at": anchored_at.isoformat(),
            "audit_chain": {
                "schema_version": integrity.get("schema_version", AUDIT_CHAIN_SCHEMA),
                "chain_origin": integrity.get("chain_origin"),
                "event_count": integrity.get("event_count"),
                "sealed_event_count": integrity.get("sealed_event_count"),
                "head_hash": integrity.get("head_hash"),
                "integrity_status": integrity.get("integrity_status"),
            },
        }
        anchor_hash = hashlib.sha256(canonical_json(commitment).encode("utf-8")).hexdigest()
        payload = {**commitment, "anchor_hash": anchor_hash}
        if self.settings.mode == "object_lock":
            return self._anchor_object_lock(project_id, payload, anchored_at)
        return self._anchor_http(payload)

    def _anchor_object_lock(
        self,
        project_id: str,
        payload: Mapping[str, Any],
        anchored_at: datetime,
    ) -> dict[str, Any]:
        local_dir = self.output_root / project_id / "audit-anchor-payloads"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_payload = local_dir / f"{payload['anchor_id']}.json"
        local_payload.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        retention_until = anchored_at + timedelta(days=self.settings.retention_days)
        object_key = f"{self.settings.key_prefix}/{project_id}/{payload['anchor_id']}.json"
        try:
            stored = self.storage.put_immutable(
                local_payload,
                object_key,
                retention_until=retention_until,
                object_lock_mode=self.settings.object_lock_mode,
            )
        except Exception as exc:
            raise AuditAnchorError("object-lock audit anchoring failed") from exc
        if not stored.get("object_lock_verified"):
            raise AuditAnchorError("object-lock retention could not be verified")
        return {
            **self._receipt_base(payload),
            "mode": "object_lock",
            "verified": True,
            "retention_mode": stored.get("retention_mode"),
            "retention_until": stored.get("retention_until"),
            "object": {
                "key": stored.get("key"),
                "uri": stored.get("uri"),
                "sha256": stored.get("sha256"),
                "size": stored.get("size"),
                "version_id": stored.get("version_id"),
            },
        }

    def _anchor_http(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = canonical_json(payload).encode("utf-8")
        timestamp = datetime.now(timezone.utc).isoformat()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-MediaForge-Anchor-Id": str(payload["anchor_id"]),
            "X-MediaForge-Timestamp": timestamp,
        }
        if self.settings.bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.bearer_token}"
        if self.settings.signing_secret:
            signature = hmac.new(
                self.settings.signing_secret.encode("utf-8"),
                f"{timestamp}.".encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-MediaForge-Signature"] = f"sha256={signature}"
        request = Request(self.settings.url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw_response = response.read(64 * 1024)
                status_code = int(response.status)
        except (HTTPError, URLError, OSError) as exc:
            raise AuditAnchorError("HTTP audit anchoring request failed") from exc
        if not 200 <= status_code < 300:
            raise AuditAnchorError("HTTP audit anchoring endpoint rejected the commitment")
        try:
            receipt = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditAnchorError("HTTP audit anchoring endpoint returned an invalid receipt") from exc
        if not isinstance(receipt, dict) or receipt.get("anchor_hash") != payload["anchor_hash"]:
            raise AuditAnchorError("HTTP audit anchoring receipt does not match the committed chain head")
        receipt_id = str(receipt.get("receipt_id") or "").strip()
        if not receipt_id or len(receipt_id) > 256:
            raise AuditAnchorError("HTTP audit anchoring receipt is missing receipt_id")
        return {
            **self._receipt_base(payload),
            "mode": "http",
            "verified": True,
            "receipt_id": receipt_id,
        }

    @staticmethod
    def _receipt_base(payload: Mapping[str, Any]) -> dict[str, Any]:
        chain = payload["audit_chain"]
        return {
            "anchor_id": payload["anchor_id"],
            "project_id": payload["project_id"],
            "anchored_at": payload["anchored_at"],
            "anchor_hash": payload["anchor_hash"],
            "chain_schema": chain["schema_version"],
            "chain_origin": chain["chain_origin"],
            "event_count": chain["event_count"],
            "sealed_event_count": chain["sealed_event_count"],
            "head_hash": chain["head_hash"],
            "integrity_status": chain["integrity_status"],
        }

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Verify the commitment encoded in a saved receipt without contacting its sink."""
        anchor_id = str(receipt.get("anchor_id") or "")
        try:
            commitment = {
                "schema_version": AUDIT_ANCHOR_SCHEMA,
                "anchor_id": anchor_id,
                "project_id": receipt["project_id"],
                "anchored_at": receipt["anchored_at"],
                "audit_chain": {
                    "schema_version": receipt["chain_schema"],
                    "chain_origin": receipt.get("chain_origin"),
                    "event_count": receipt["event_count"],
                    "sealed_event_count": receipt["sealed_event_count"],
                    "head_hash": receipt["head_hash"],
                    "integrity_status": receipt["integrity_status"],
                },
            }
            expected_hash = hashlib.sha256(
                canonical_json(commitment).encode("utf-8")
            ).hexdigest()
            actual_hash = str(receipt.get("anchor_hash") or "")
            verified = bool(anchor_id and actual_hash == expected_hash)
        except (KeyError, TypeError, ValueError):
            expected_hash = None
            actual_hash = str(receipt.get("anchor_hash") or "")
            verified = False
        return {
            "anchor_id": anchor_id or None,
            "verified": verified,
            "expected_anchor_hash": expected_hash,
            "actual_anchor_hash": actual_hash or None,
            "issue": None if verified else "saved receipt does not reproduce its anchor commitment",
        }
