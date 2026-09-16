from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass


class CallbackSecurityError(ValueError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def callback_signature(
    secret: str | bytes,
    *,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    canonical = (
        f"{timestamp}\n{method.upper()}\n{path}\n".encode("utf-8") + body
    )
    digest = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass(frozen=True)
class CallbackSecurity:
    secret: bytes | None
    provider_mode: str
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls, *, provider_mode: str) -> "CallbackSecurity":
        secret_text = os.getenv("MEDIAFORGE_CALLBACK_SECRET", "").strip()
        max_age_raw = os.getenv(
            "MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS",
            "300",
        ).strip()
        try:
            max_age_seconds = int(max_age_raw)
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS must be an integer"
            ) from exc
        if not 30 <= max_age_seconds <= 86400:
            raise ValueError(
                "MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS must be between 30 and 86400"
            )
        return cls(
            secret=secret_text.encode("utf-8") if secret_text else None,
            provider_mode=provider_mode.strip().lower(),
            max_age_seconds=max_age_seconds,
        )

    @property
    def configured(self) -> bool:
        return self.secret is not None

    @property
    def required(self) -> bool:
        return self.provider_mode not in {"mock", "unknown", ""}

    @property
    def accepting_callbacks(self) -> bool:
        return self.configured or not self.required

    def status_view(self) -> dict:
        if self.configured:
            mode = "signed"
        elif self.required:
            mode = "disabled"
        else:
            mode = "development_unsigned"
        return {
            "schema_version": "mediaforge-callback-security-v1",
            "configured": self.configured,
            "required": self.required,
            "accepting_callbacks": self.accepting_callbacks,
            "mode": mode,
            "algorithm": "HMAC-SHA256",
            "signed_components": ["timestamp", "method", "path", "body"],
            "timestamp_header": "X-MediaForge-Timestamp",
            "signature_header": "X-MediaForge-Signature",
            "max_age_seconds": self.max_age_seconds,
        }

    def verify(
        self,
        *,
        timestamp: str | None,
        signature: str | None,
        method: str,
        path: str,
        body: bytes,
        now: int | None = None,
    ) -> dict:
        if not self.configured:
            if self.required:
                raise CallbackSecurityError(
                    "Provider callbacks are disabled until MEDIAFORGE_CALLBACK_SECRET is configured.",
                    status_code=503,
                )
            return {"verified": False, "mode": "development_unsigned"}

        if not timestamp or not signature:
            raise CallbackSecurityError(
                "Provider callback signature headers are required.",
                status_code=401,
            )
        try:
            timestamp_value = int(timestamp)
        except ValueError as exc:
            raise CallbackSecurityError(
                "Provider callback timestamp is invalid.",
                status_code=401,
            ) from exc

        current_time = int(time.time()) if now is None else now
        if abs(current_time - timestamp_value) > self.max_age_seconds:
            raise CallbackSecurityError(
                "Provider callback timestamp is outside the accepted window.",
                status_code=401,
            )

        expected = callback_signature(
            self.secret,
            timestamp=timestamp,
            method=method,
            path=path,
            body=body,
        )
        normalized = signature.strip().lower()
        if not normalized.startswith("sha256="):
            normalized = f"sha256={normalized}"
        if not hmac.compare_digest(expected, normalized):
            raise CallbackSecurityError(
                "Provider callback signature is invalid.",
                status_code=401,
            )
        return {"verified": True, "mode": "signed"}
