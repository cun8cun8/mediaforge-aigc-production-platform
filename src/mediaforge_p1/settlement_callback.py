from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Mapping


class SettlementCallbackError(ValueError):
    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SettlementCallbackSecurity:
    """Verify a finance-system callback without granting it an API user key."""

    secret: str
    allowed_providers: tuple[str, ...]
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls) -> "SettlementCallbackSecurity":
        raw_providers = os.getenv("MEDIAFORGE_SETTLEMENT_CALLBACK_ALLOWED_PROVIDERS", "")
        providers = tuple(
            dict.fromkeys(
                value.strip().lower()
                for value in raw_providers.split(",")
                if value.strip()
            )
        )
        try:
            max_age = int(os.getenv("MEDIAFORGE_SETTLEMENT_CALLBACK_MAX_AGE_SECONDS", "300"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_SETTLEMENT_CALLBACK_MAX_AGE_SECONDS must be an integer") from exc
        if not 30 <= max_age <= 3600:
            raise ValueError("MEDIAFORGE_SETTLEMENT_CALLBACK_MAX_AGE_SECONDS must be 30..3600")
        return cls(
            secret=os.getenv("MEDIAFORGE_SETTLEMENT_CALLBACK_SECRET", "").strip(),
            allowed_providers=providers,
            max_age_seconds=max_age,
        )

    @property
    def configured(self) -> bool:
        return bool(self.secret and self.allowed_providers)

    def verify(self, headers: Mapping[str, str], body: bytes, *, now: float | None = None) -> None:
        if not self.configured:
            raise SettlementCallbackError("settlement callback is not configured", status_code=503)
        raw_timestamp = str(headers.get("X-MediaForge-Timestamp") or "").strip()
        signature = str(headers.get("X-MediaForge-Signature") or "").strip()
        try:
            timestamp = int(raw_timestamp)
        except ValueError as exc:
            raise SettlementCallbackError("settlement callback timestamp is invalid") from exc
        current = time.time() if now is None else now
        if abs(current - timestamp) > self.max_age_seconds:
            raise SettlementCallbackError("settlement callback timestamp is outside the allowed window")
        expected = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, f"sha256={expected}"):
            raise SettlementCallbackError("settlement callback signature is invalid")

    def permits_provider(self, provider: str) -> bool:
        return str(provider).strip().lower() in self.allowed_providers

    def status_view(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "secret_configured": bool(self.secret),
            "allowed_providers": list(self.allowed_providers),
            "max_age_seconds": self.max_age_seconds,
            "signature_scheme": "hmac-sha256(timestamp + '.' + raw_body)",
        }
