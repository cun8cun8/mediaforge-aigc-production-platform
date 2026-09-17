from __future__ import annotations

import json

from mediaforge_p1 import provider_probe
from mediaforge_p1.contracts import Capability
from mediaforge_p1.provider_probe_receipt import (
    PROBE_RECEIPT_SCHEMA,
    validate_provider_probe_receipt,
)
from mediaforge_p1.providers import MockProvider


def test_provider_probe_writes_a_signed_verifiable_receipt(tmp_path, monkeypatch) -> None:
    secret = "provider-probe-receipt-secret-for-runtime-test"
    monkeypatch.setenv("MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET", secret)
    monkeypatch.setattr(
        provider_probe,
        "build_provider",
        lambda *_args, **_kwargs: MockProvider(),
    )

    result = provider_probe.run_provider_probe(
        "local",
        tmp_path,
        capability=Capability.IMAGE_GENERATION,
    )

    manifest_path = tmp_path / "manifest.json"
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert receipt["schema_version"] == PROBE_RECEIPT_SCHEMA
    assert receipt["receipt_signature"]["algorithm"] == "hmac-sha256"
    assert validate_provider_probe_receipt(
        manifest_path,
        secret=secret,
        max_age_hours=1,
    ) == {
        "provider": "mock-provider",
        "capability": "image_generation",
        "age_hours": 0.0,
        "signed": True,
    }
