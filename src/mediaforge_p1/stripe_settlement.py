from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from .settlement_callback import SettlementCallbackError


_EVENT_STATUS = {
    "payout.paid": "paid",
    "payout.failed": "failed",
    "payout.canceled": "void",
}
_ZERO_DECIMAL_CURRENCIES = frozenset({
    "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg",
    "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
})


@dataclass(frozen=True)
class StripeSettlementWebhook:
    """Verify and normalize signed Stripe payout events for the settlement ledger."""

    endpoint_secret: str = field(repr=False)
    allowed_event_types: tuple[str, ...]
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls) -> "StripeSettlementWebhook":
        raw_types = os.getenv(
            "MEDIAFORGE_STRIPE_SETTLEMENT_EVENT_TYPES",
            "payout.paid,payout.failed,payout.canceled",
        )
        event_types = tuple(dict.fromkeys(
            value.strip() for value in raw_types.split(",") if value.strip()
        ))
        if any(value not in _EVENT_STATUS for value in event_types):
            raise ValueError(
                "MEDIAFORGE_STRIPE_SETTLEMENT_EVENT_TYPES supports only "
                "payout.paid, payout.failed, and payout.canceled"
            )
        try:
            max_age = int(os.getenv("MEDIAFORGE_STRIPE_SETTLEMENT_MAX_AGE_SECONDS", "300"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_STRIPE_SETTLEMENT_MAX_AGE_SECONDS must be an integer") from exc
        if not 30 <= max_age <= 3600:
            raise ValueError("MEDIAFORGE_STRIPE_SETTLEMENT_MAX_AGE_SECONDS must be 30..3600")
        return cls(
            endpoint_secret=os.getenv("MEDIAFORGE_STRIPE_SETTLEMENT_WEBHOOK_SECRET", "").strip(),
            allowed_event_types=event_types,
            max_age_seconds=max_age,
        )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint_secret and self.allowed_event_types)

    def verify_and_normalize(
        self,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise SettlementCallbackError("Stripe settlement webhook is not configured", status_code=503)
        timestamp, signatures = self._signature_components(headers)
        current = time.time() if now is None else now
        if abs(current - timestamp) > self.max_age_seconds:
            raise SettlementCallbackError("Stripe webhook timestamp is outside the allowed window")
        expected = hmac.new(
            self.endpoint_secret.encode("utf-8"),
            f"{timestamp}.".encode("ascii") + body,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(signature, expected) for signature in signatures):
            raise SettlementCallbackError("Stripe webhook signature is invalid")
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettlementCallbackError("Stripe webhook payload is invalid", status_code=422) from exc
        if not isinstance(event, dict):
            raise SettlementCallbackError("Stripe webhook payload is invalid", status_code=422)
        return self._normalize_event(event)

    def _signature_components(self, headers: Mapping[str, str]) -> tuple[int, tuple[str, ...]]:
        raw = str(headers.get("Stripe-Signature") or "").strip()
        timestamp: int | None = None
        signatures: list[str] = []
        for entry in raw.split(","):
            key, separator, value = entry.strip().partition("=")
            if not separator:
                continue
            if key == "t":
                try:
                    parsed = int(value)
                except ValueError as exc:
                    raise SettlementCallbackError("Stripe webhook timestamp is invalid") from exc
                if timestamp is not None and timestamp != parsed:
                    raise SettlementCallbackError("Stripe webhook contains conflicting timestamps")
                timestamp = parsed
            elif key == "v1" and len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
                signatures.append(value.lower())
        if timestamp is None or not signatures:
            raise SettlementCallbackError("Stripe webhook signature is invalid")
        return timestamp, tuple(signatures)

    def _normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        data = event.get("data")
        payout = data.get("object") if isinstance(data, dict) else None
        if not event_id or len(event_id) > 240 or event_type not in self.allowed_event_types:
            raise SettlementCallbackError("Stripe event type is not accepted", status_code=422)
        if not isinstance(payout, dict) or str(payout.get("object") or "") != "payout":
            raise SettlementCallbackError("Stripe webhook does not contain a payout", status_code=422)
        payout_id = str(payout.get("id") or "").strip()
        currency = str(payout.get("currency") or "").strip().lower()
        metadata = payout.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        tenant_id = str(
            metadata.get("mediaforge_tenant_id") or metadata.get("tenant_id") or ""
        ).strip()
        amount = payout.get("amount")
        if (
            not payout_id
            or len(payout_id) > 180
            or not tenant_id
            or len(tenant_id) > 160
            or not isinstance(amount, int)
            or amount < 0
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
        ):
            raise SettlementCallbackError("Stripe payout is missing required settlement metadata", status_code=422)
        divisor = Decimal(1 if currency in _ZERO_DECIMAL_CURRENCIES else 100)
        normalized_metadata = {
            "stripe_event_id": event_id,
            "stripe_event_type": event_type,
            "stripe_payout_id": payout_id,
            "stripe_livemode": bool(event.get("livemode")),
            "stripe_event_created": event.get("created"),
            "stripe_arrival_date": payout.get("arrival_date"),
        }
        return {
            "settlement_id": f"stripe:{payout_id}",
            "tenant_id": tenant_id,
            "provider": "stripe",
            "status": _EVENT_STATUS[event_type],
            "amount": float(Decimal(amount) / divisor),
            "currency": currency.upper(),
            "external_id": event_id,
            "metadata": normalized_metadata,
        }

    def status_view(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "endpoint_secret_configured": bool(self.endpoint_secret),
            "allowed_event_types": list(self.allowed_event_types),
            "max_age_seconds": self.max_age_seconds,
            "signature_scheme": "stripe-v1-hmac-sha256(timestamp + '.' + raw_body)",
            "tenant_metadata_key": "mediaforge_tenant_id",
        }
