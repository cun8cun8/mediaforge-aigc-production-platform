import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "receipt", Path(__file__).parents[1] / "src/mediaforge_p1/provider_probe_receipt.py")
receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt)


@pytest.mark.parametrize("maximum_age", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_receipt_age_is_rejected_before_reading_file(tmp_path, maximum_age):
    with pytest.raises(receipt.ProviderProbeReceiptError, match="(?i)age|finite"):
        receipt.validate_provider_probe_receipt(
            tmp_path / "missing.json", secret="test-secret-that-is-more-than-32-characters",
            max_age_hours=maximum_age)


@pytest.mark.parametrize("maximum_age", [float("nan"), float("inf")])
def test_nonfinite_age_cannot_accept_a_stale_signed_receipt(maximum_age):
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=365)).isoformat()
    with pytest.raises(receipt.ProviderProbeReceiptError):
        receipt._parse_created_at(stale, now=now, max_age_hours=maximum_age)
