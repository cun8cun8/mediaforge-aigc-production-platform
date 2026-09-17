"""Signed, local evidence for an explicitly approved Provider probe.

The probe itself may consume external API budget or GPU capacity.  This module
keeps the evidence boundary intentionally small: the probe writes a receipt,
and a later release acceptance command independently validates its freshness,
signature and local artifact integrity without submitting new work.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROBE_RECEIPT_SCHEMA = "mediaforge-provider-probe-receipt-v1"
SIGNATURE_ALGORITHM = "hmac-sha256"
MAX_RECEIPT_BYTES = 1_048_576


class ProviderProbeReceiptError(ValueError):
    """Raised when a probe receipt cannot be trusted as release evidence."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_receipt(receipt: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_signature"
    }
    return json.dumps(
        unsigned,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_provider_probe_receipt(
    receipt: dict[str, Any],
    secret: str,
) -> dict[str, str]:
    """Return a detached signature object without mutating ``receipt``."""
    secret = secret.strip()
    if len(secret) < 32:
        raise ProviderProbeReceiptError(
            "Provider probe receipt secret must be at least 32 characters"
        )
    signature = hmac.new(
        secret.encode("utf-8"),
        _canonical_receipt(receipt),
        hashlib.sha256,
    ).hexdigest()
    return {"algorithm": SIGNATURE_ALGORITHM, "value": signature}


def _parse_created_at(value: Any, *, now: datetime, max_age_hours: float) -> float:
    if not isinstance(value, str) or not value.strip():
        raise ProviderProbeReceiptError("Provider probe receipt creation time is missing")
    try:
        created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderProbeReceiptError(
            "Provider probe receipt creation time is invalid"
        ) from exc
    if created_at.tzinfo is None:
        raise ProviderProbeReceiptError(
            "Provider probe receipt creation time must include a timezone"
        )
    age_hours = (now - created_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < -5 / 60:
        raise ProviderProbeReceiptError("Provider probe receipt creation time is in the future")
    if age_hours > max_age_hours:
        raise ProviderProbeReceiptError("Provider probe receipt is older than the allowed age")
    return max(age_hours, 0.0)


def _validate_artifact(receipt: dict[str, Any]) -> tuple[str, str]:
    artifact = receipt.get("artifact")
    if not isinstance(artifact, dict):
        raise ProviderProbeReceiptError("Provider probe receipt artifact is missing")
    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise ProviderProbeReceiptError("Provider probe receipt artifact URI is missing")
    path = Path(uri).expanduser()
    if not path.is_file():
        raise ProviderProbeReceiptError("Provider probe artifact is not available locally")
    expected_hash = str(artifact.get("sha256") or "").lower()
    if len(expected_hash) != 64 or any(item not in "0123456789abcdef" for item in expected_hash):
        raise ProviderProbeReceiptError("Provider probe receipt artifact hash is invalid")
    if not hmac.compare_digest(sha256_file(path), expected_hash):
        raise ProviderProbeReceiptError("Provider probe artifact hash does not match its receipt")
    try:
        expected_size = int(artifact.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise ProviderProbeReceiptError("Provider probe receipt artifact size is invalid") from exc
    if expected_size < 0 or path.stat().st_size != expected_size:
        raise ProviderProbeReceiptError("Provider probe artifact size does not match its receipt")
    provider = str(receipt.get("provider") or "").strip().lower()
    if not provider:
        raise ProviderProbeReceiptError("Provider probe receipt provider is missing")
    spec = receipt.get("spec")
    constraints = spec.get("provider_constraints") if isinstance(spec, dict) else None
    capability = str(constraints.get("capability") or "").strip().lower() if isinstance(constraints, dict) else ""
    if not capability:
        raise ProviderProbeReceiptError("Provider probe receipt capability is missing")
    quality = receipt.get("quality")
    if not isinstance(quality, dict) or quality.get("valid") is not True:
        raise ProviderProbeReceiptError("Provider probe receipt does not contain a passing quality result")
    return provider, capability


def validate_provider_probe_receipt(
    path: Path,
    *,
    secret: str,
    max_age_hours: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one receipt and return only safe, release-relevant metadata."""
    if max_age_hours <= 0:
        raise ProviderProbeReceiptError("Provider probe maximum age must be greater than zero")
    secret = secret.strip()
    if len(secret) < 32:
        raise ProviderProbeReceiptError(
            "Provider probe receipt signature secret is missing or too short"
        )
    try:
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            raise ProviderProbeReceiptError("Provider probe receipt exceeds the size limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ProviderProbeReceiptError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProbeReceiptError("Provider probe receipt cannot be read") from exc
    if not isinstance(raw, dict):
        raise ProviderProbeReceiptError("Provider probe receipt must be a JSON object")
    if raw.get("schema_version") != PROBE_RECEIPT_SCHEMA:
        raise ProviderProbeReceiptError("Provider probe receipt schema is unsupported")
    if raw.get("status") != "SUCCEEDED":
        raise ProviderProbeReceiptError("Provider probe receipt is not successful")
    signature = raw.get("receipt_signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ProviderProbeReceiptError("Provider probe receipt signature is missing")
    value = str(signature.get("value") or "").lower()
    if len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise ProviderProbeReceiptError("Provider probe receipt signature is invalid")
    expected = sign_provider_probe_receipt(
        raw,
        secret,
    )["value"]
    if not hmac.compare_digest(value, expected):
        raise ProviderProbeReceiptError("Provider probe receipt signature does not match")
    created_at = _parse_created_at(
        raw.get("created_at"),
        now=now or datetime.now(timezone.utc),
        max_age_hours=max_age_hours,
    )
    provider, capability = _validate_artifact(raw)
    return {
        "provider": provider,
        "capability": capability,
        "age_hours": round(created_at, 3),
        "signed": True,
    }
