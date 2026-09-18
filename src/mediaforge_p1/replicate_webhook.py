from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Mapping


class ReplicateWebhookSecurityError(ValueError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReplicateWebhookSecurity:
    """Verify Replicate's native Svix-compatible webhook signature."""

    signing_key: bytes | None
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls) -> "ReplicateWebhookSecurity":
        secret = os.getenv("REPLICATE_WEBHOOK_SIGNING_SECRET", "").strip()
        max_age_raw = os.getenv(
            "REPLICATE_WEBHOOK_MAX_AGE_SECONDS",
            "300",
        ).strip()
        try:
            max_age_seconds = int(max_age_raw)
        except ValueError as exc:
            raise ValueError(
                "REPLICATE_WEBHOOK_MAX_AGE_SECONDS must be an integer"
            ) from exc
        if not 30 <= max_age_seconds <= 86400:
            raise ValueError(
                "REPLICATE_WEBHOOK_MAX_AGE_SECONDS must be between 30 and 86400"
            )
        if not secret:
            return cls(signing_key=None, max_age_seconds=max_age_seconds)
        if not secret.startswith("whsec_"):
            raise ValueError(
                "REPLICATE_WEBHOOK_SIGNING_SECRET must start with whsec_"
            )
        try:
            signing_key = base64.b64decode(secret[6:], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(
                "REPLICATE_WEBHOOK_SIGNING_SECRET has invalid base64 key data"
            ) from exc
        if not signing_key:
            raise ValueError("REPLICATE_WEBHOOK_SIGNING_SECRET must not be empty")
        return cls(signing_key=signing_key, max_age_seconds=max_age_seconds)

    @property
    def configured(self) -> bool:
        return self.signing_key is not None

    def status_view(self) -> dict:
        return {
            "schema_version": "mediaforge-replicate-webhook-security-v1",
            "configured": self.configured,
            "algorithm": "HMAC-SHA256",
            "signature_version": "v1",
            "signed_components": ["webhook-id", "webhook-timestamp", "body"],
            "max_age_seconds": self.max_age_seconds,
        }

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: int | None = None,
    ) -> dict[str, str | bool]:
        if self.signing_key is None:
            raise ReplicateWebhookSecurityError(
                "Replicate webhooks are disabled until "
                "REPLICATE_WEBHOOK_SIGNING_SECRET is configured.",
                status_code=503,
            )
        webhook_id = str(headers.get("webhook-id") or "").strip()
        timestamp = str(headers.get("webhook-timestamp") or "").strip()
        signature = str(headers.get("webhook-signature") or "").strip()
        if not webhook_id or not timestamp or not signature:
            raise ReplicateWebhookSecurityError(
                "Replicate webhook signature headers are required.",
                status_code=401,
            )
        try:
            timestamp_value = int(timestamp)
        except ValueError as exc:
            raise ReplicateWebhookSecurityError(
                "Replicate webhook timestamp is invalid.",
                status_code=401,
            ) from exc
        current_time = int(time.time()) if now is None else now
        if abs(current_time - timestamp_value) > self.max_age_seconds:
            raise ReplicateWebhookSecurityError(
                "Replicate webhook timestamp is outside the accepted window.",
                status_code=401,
            )
        signed_content = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
        expected = hmac.new(
            self.signing_key,
            signed_content,
            hashlib.sha256,
        ).digest()
        candidates = []
        for value in signature.split():
            version, separator, encoded = value.partition(",")
            if version != "v1" or not separator or not encoded:
                continue
            try:
                candidates.append(base64.b64decode(encoded, validate=True))
            except (ValueError, binascii.Error):
                continue
        if not candidates or not any(
            hmac.compare_digest(expected, candidate) for candidate in candidates
        ):
            raise ReplicateWebhookSecurityError(
                "Replicate webhook signature is invalid.",
                status_code=401,
            )
        return {
            "verified": True,
            "webhook_id": webhook_id,
            "timestamp": timestamp,
            "signature_version": "v1",
        }
