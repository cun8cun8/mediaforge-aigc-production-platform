from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .provider_probe_receipt import (
    ProviderProbeReceiptError,
    validate_provider_probe_receipt,
)


class AcceptanceError(RuntimeError):
    pass


def fetch_json(base_url: str, path: str, *, token: str = "", method: str = "GET") -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AcceptanceError(f"{path} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{path} could not be read") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"{path} returned an invalid JSON object")
    return value


def fetch_text(base_url: str, path: str, *, token: str = "") -> str:
    headers = {"Accept": "text/plain"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")
    except HTTPError as exc:
        raise AcceptanceError(f"{path} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise AcceptanceError(f"{path} could not be read") from exc


def read_secret_file(path: Path, *, label: str = "secret") -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AcceptanceError(f"{label} file cannot be read") from exc
    if not value:
        raise AcceptanceError(f"{label} file is empty")
    return value


def _check(code: str, passed: bool, blocking: bool, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "passed": passed,
        "blocking": blocking,
        "message": message,
        "detail": detail or {},
    }


def run_production_acceptance(
    base_url: str,
    *,
    token: str = "",
    metrics_token: str = "",
    require_production: bool = False,
    probe_enterprise: bool = False,
    probe_planning: bool = False,
    provider_probe_receipts: list[Path] | None = None,
    provider_probe_secret: str = "",
    provider_probe_max_age_hours: float = 168.0,
) -> dict[str, Any]:
    """Run non-destructive API acceptance checks without exposing credentials.

    Provider media generation is intentionally excluded: it may incur cost and is
    exercised separately by ``mediaforge-provider-probe`` after operator approval.
    """
    if provider_probe_max_age_hours <= 0:
        raise AcceptanceError("provider probe maximum age must be greater than zero")

    checks: list[dict[str, Any]] = []

    def get(code: str, path: str) -> dict[str, Any] | None:
        try:
            return fetch_json(base_url, path, token=token)
        except AcceptanceError as exc:
            checks.append(_check(code, False, True, str(exc)))
            return None

    health = get("health", "/health")
    if health is not None:
        checks.append(_check("health", True, True, "API health endpoint responded."))

    diagnostics = get("provider_diagnostics", "/providers/diagnostics")
    if diagnostics is not None:
        production_ready = bool(diagnostics.get("production_ready"))
        checks.append(
            _check(
                "provider_diagnostics",
                bool(diagnostics.get("ready", diagnostics.get("grade") != "BLOCKED")),
                True,
                "Provider diagnostics responded.",
                {
                    "grade": diagnostics.get("grade"),
                    "production_ready": production_ready,
                },
            )
        )
        status_payload = diagnostics.get("status")
        status_rows = (
            status_payload.get("providers") or [status_payload]
            if isinstance(status_payload, dict)
            else []
        )
        real_providers = [
            row
            for row in status_rows
            if isinstance(row, dict)
            and str(row.get("mode") or "").strip().lower()
            not in {"", "mock", "unknown"}
        ]
        callback_security = diagnostics.get("callback_security") or {}
        callback_configured = bool(callback_security.get("configured"))
        checks.append(
            _check(
                "provider_callback_security",
                not real_providers or callback_configured,
                require_production and bool(real_providers),
                "Callback signing is configured for real Providers."
                if not real_providers or callback_configured
                else "Real Providers require signed callback configuration.",
                {
                    "real_provider_count": len(real_providers),
                    "configured": callback_configured,
                },
            )
        )
        comfy_rows = [
            row
            for row in real_providers
            if str(row.get("mode") or "").strip().lower() == "comfyui"
        ]
        if comfy_rows:
            governance_rows = []
            for row in comfy_rows:
                details = row.get("details") or {}
                capabilities = {
                    str(item)
                    for item in row.get("capabilities") or []
                    if str(item).strip()
                }
                registry = details.get("workflow_registry") or {}
                registry_rows = registry.get("workflows") if isinstance(registry, dict) else []
                reviewed_entries = bool(registry_rows) and all(
                    isinstance(item, dict)
                    and bool(item.get("version"))
                    and len(str(item.get("sha256") or "")) == 64
                    for item in registry_rows
                )
                legacy_workflow = details.get("workflow") or {}
                reviewed_legacy = (
                    isinstance(legacy_workflow, dict)
                    and bool(legacy_workflow.get("version"))
                    and len(str(legacy_workflow.get("sha256") or "")) == 64
                )
                missing_defaults = []
                if "image_generation" in capabilities and not details.get("default_template_id"):
                    missing_defaults.append("image_generation")
                if "image_to_video" in capabilities and not details.get("default_video_template_id"):
                    missing_defaults.append("image_to_video")
                governance_rows.append(
                    {
                        "provider": str(row.get("provider") or "comfyui"),
                        "workflow_loaded": bool(details.get("workflow_loaded")),
                        "workflow_pin_required": bool(details.get("workflow_pin_required")),
                        "reviewed_registry": reviewed_entries,
                        "reviewed_legacy_workflow": reviewed_legacy,
                        "capabilities": sorted(capabilities),
                        "missing_default_capabilities": missing_defaults,
                    }
                )
            governance_passed = all(
                item["workflow_loaded"]
                and item["workflow_pin_required"]
                and (item["reviewed_registry"] or item["reviewed_legacy_workflow"])
                and not item["missing_default_capabilities"]
                for item in governance_rows
            )
            checks.append(
                _check(
                    "comfyui_workflow_governance",
                    governance_passed,
                    require_production,
                    "Reviewed ComfyUI workflow governance is complete."
                    if governance_passed
                    else "ComfyUI workflow pins, reviewed entries, or capability defaults are incomplete.",
                    {"providers": governance_rows},
                )
            )

        receipt_paths = provider_probe_receipts or []
        expected_providers: dict[str, dict[str, set[str]]] = {}
        for row in real_providers:
            provider_name = str(row.get("provider") or row.get("mode") or "").strip().lower()
            if not provider_name:
                continue
            aliases = {
                provider_name,
                str(row.get("mode") or "").strip().lower(),
            }
            expected_providers[provider_name] = {
                "aliases": {item for item in aliases if item},
                "capabilities": {
                    str(item).strip().lower()
                    for item in row.get("capabilities") or []
                    if str(item).strip()
                },
            }

        receipt_rows: list[dict[str, Any]] = []
        covered_providers: set[str] = set()
        for receipt_path in receipt_paths:
            try:
                validated = validate_provider_probe_receipt(
                    receipt_path,
                    secret=provider_probe_secret,
                    max_age_hours=provider_probe_max_age_hours,
                )
                matched_provider = next(
                    (
                        provider_name
                        for provider_name, expected in expected_providers.items()
                        if validated["provider"] in expected["aliases"]
                    ),
                    None,
                )
                capability_allowed = bool(
                    matched_provider
                    and validated["capability"]
                    in expected_providers[matched_provider]["capabilities"]
                )
                receipt_rows.append(
                    {
                        "provider": validated["provider"],
                        "capability": validated["capability"],
                        "age_hours": validated["age_hours"],
                        "valid": capability_allowed,
                    }
                )
                if capability_allowed and matched_provider is not None:
                    covered_providers.add(matched_provider)
            except ProviderProbeReceiptError:
                receipt_rows.append({"valid": False})
        missing_providers = sorted(set(expected_providers) - covered_providers)
        receipts_passed = (
            not real_providers
            or (
                bool(provider_probe_secret.strip())
                and bool(receipt_paths)
                and not missing_providers
                and all(item["valid"] for item in receipt_rows)
            )
        )
        if real_providers or receipt_paths:
            checks.append(
                _check(
                    "provider_probe_receipts",
                    receipts_passed,
                    require_production and bool(real_providers),
                    "Recent signed Provider probe receipts cover every enabled real Provider."
                    if receipts_passed
                    else "Provider probe receipts are missing, invalid, stale, or do not cover every enabled real Provider.",
                    {
                        "configured_provider_count": len(expected_providers),
                        "receipt_count": len(receipt_paths),
                        "valid_receipt_count": sum(
                            1 for item in receipt_rows if item["valid"]
                        ),
                        "covered_providers": sorted(covered_providers),
                        "missing_providers": missing_providers,
                        "max_age_hours": provider_probe_max_age_hours,
                    },
                )
            )

    provider_contract = get("provider_contract", "/providers/contracts")
    if provider_contract is not None:
        summary = provider_contract.get("summary") or {}
        protocol_passed = bool(summary.get("protocol_passed"))
        checks.append(
            _check(
                "provider_contract",
                protocol_passed,
                True,
                "Enabled Provider protocol contracts passed."
                if protocol_passed
                else "At least one enabled Provider protocol contract failed.",
                {
                    "provider_count": int(provider_contract.get("provider_count") or 0),
                    "protocol_passed": protocol_passed,
                    "planned_shot_count": int(summary.get("planned_shot_count") or 0),
                    "unroutable_shot_count": int(summary.get("unroutable_shot_count") or 0),
                },
            )
        )

    content_credentials = get("content_credentials", "/content-credentials/status")
    if content_credentials is not None:
        signer_configured = bool(content_credentials.get("configured"))
        verifier_configured = bool(content_credentials.get("verifier_configured"))
        c2pa_ready = signer_configured and verifier_configured
        checks.append(
            _check(
                "content_credentials",
                c2pa_ready,
                require_production,
                "C2PA signer and independent verifier are configured."
                if c2pa_ready
                else "Production C2PA requires both a signer and an independent verifier.",
                {
                    "mode": content_credentials.get("mode"),
                    "signer_configured": signer_configured,
                    "verifier_configured": verifier_configured,
                    "production_ready": bool(content_credentials.get("production_ready")),
                },
            )
        )

    source_ingest = get("source_ingest", "/source-ingest/status")
    if source_ingest is not None:
        valid = source_ingest.get("configuration_error") is None
        checks.append(
            _check(
                "source_ingest",
                valid,
                False,
                "OCR configuration is valid." if valid else "OCR configuration is invalid.",
                {
                    "mode": source_ingest.get("mode"),
                    "configured": bool(source_ingest.get("configured")),
                },
            )
        )

    planning = get("planning_status", "/planning/status")
    if planning is not None:
        checks.append(
            _check(
                "planning_status",
                True,
                False,
                "Planning status responded.",
                {
                    "engine": planning.get("engine"),
                    "configured": bool(planning.get("configured")),
                    "version": planning.get("version"),
                },
            )
        )

    enterprise = get("enterprise_status", "/enterprise/status")
    if enterprise is not None:
        checks.append(
            _check(
                "enterprise_status",
                True,
                False,
                "Enterprise runtime status responded.",
                {"topology": enterprise.get("topology")},
            )
        )

    readiness = get("readiness", "/ops/readiness")
    if readiness is not None:
        operational_ready = bool(readiness.get("ready"))
        production_ready = bool(readiness.get("production_ready"))
        checks.append(
            _check(
                "readiness",
                operational_ready,
                True,
                "Operational readiness passed." if operational_ready else "Operational readiness failed.",
                {
                    "grade": readiness.get("grade"),
                    "production_ready": production_ready,
                    "blocking_failures": readiness.get("blocking_failures", []),
                    "warnings": readiness.get("warnings", []),
                },
            )
        )
        if require_production:
            checks.append(
                _check(
                    "production_readiness",
                    production_ready,
                    True,
                    "Production deployment requirements passed."
                    if production_ready
                    else "Production requirements are not yet satisfied.",
                )
            )

    alerts = get("operations_alerts", "/ops/alerts")
    if alerts is not None:
        critical_count = int(alerts.get("critical_count") or 0)
        checks.append(
            _check(
                "operations_alerts",
                critical_count == 0,
                require_production,
                "No critical operations alerts are active."
                if critical_count == 0
                else "Critical operations alerts are active.",
                {
                    "grade": alerts.get("grade"),
                    "critical_count": critical_count,
                    "warning_count": int(alerts.get("warning_count") or 0),
                },
            )
        )

    if metrics_token:
        try:
            metrics = fetch_text(base_url, "/metrics", token=metrics_token)
            valid = "mediaforge_process_uptime_seconds" in metrics
            checks.append(
                _check(
                    "metrics",
                    valid,
                    True,
                    "Protected Prometheus metrics endpoint responded."
                    if valid
                    else "Metrics response did not contain MediaForge gauges.",
                )
            )
        except AcceptanceError as exc:
            checks.append(_check("metrics", False, True, str(exc)))

    for enabled, code, path in (
        (probe_enterprise, "enterprise_probe", "/enterprise/probe"),
        (probe_planning, "planning_probe", "/planning/probe"),
    ):
        if not enabled:
            continue
        try:
            result = fetch_json(base_url, path, token=token, method="POST")
            checks.append(
                _check(
                    code,
                    bool(result.get("reachable")),
                    True,
                    "Connection probe passed."
                    if result.get("reachable")
                    else "Connection probe failed.",
                    {"reachable": bool(result.get("reachable"))},
                )
            )
        except AcceptanceError as exc:
            checks.append(_check(code, False, True, str(exc)))

    blocking_failures = [item["code"] for item in checks if item["blocking"] and not item["passed"]]
    warnings = [item["code"] for item in checks if not item["blocking"] and not item["passed"]]
    return {
        "schema_version": "mediaforge-production-acceptance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url.rstrip("/"),
        "require_production": require_production,
        "passed": not blocking_failures,
        "grade": "PRODUCTION_READY" if require_production and not blocking_failures else "READY" if not blocking_failures else "BLOCKED",
        "blocking_failures": blocking_failures,
        "warnings": warnings,
        "checks": checks,
        "manual_provider_probe": "Run mediaforge-provider-probe explicitly after approving any external generation cost.",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run non-destructive MediaForge production acceptance checks."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("MEDIAFORGE_API_BASE_URL", "http://127.0.0.1:8020"),
    )
    parser.add_argument("--token", default=os.getenv("MEDIAFORGE_READINESS_TOKEN", ""))
    parser.add_argument(
        "--metrics-token-file",
        type=Path,
        default=(
            Path(os.environ["MEDIAFORGE_METRICS_TOKEN_FILE"])
            if os.getenv("MEDIAFORGE_METRICS_TOKEN_FILE", "").strip()
            else None
        ),
        help="one-line metrics bearer token file; the value is never reported",
    )
    parser.add_argument("--require-production", action="store_true")
    parser.add_argument("--probe-enterprise", action="store_true")
    parser.add_argument("--probe-planning", action="store_true")
    parser.add_argument(
        "--provider-probe-receipt",
        action="append",
        type=Path,
        default=[
            Path(item.strip())
            for item in os.getenv("MEDIAFORGE_PROVIDER_PROBE_RECEIPTS", "").split(",")
            if item.strip()
        ],
        help="signed provider probe manifest; repeat for each enabled real provider",
    )
    parser.add_argument(
        "--provider-probe-secret-file",
        type=Path,
        default=(
            Path(os.environ["MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET_FILE"])
            if os.getenv("MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET_FILE", "").strip()
            else None
        ),
        help="one-line provider probe receipt signature secret; the value is never reported",
    )
    parser.add_argument(
        "--provider-probe-max-age-hours",
        type=float,
        default=float(os.getenv("MEDIAFORGE_PROVIDER_PROBE_MAX_AGE_HOURS", "168")),
        help="maximum permitted age for signed provider probe receipts",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/production-acceptance.json"),
    )
    args = parser.parse_args()
    try:
        metrics_token = (
            read_secret_file(args.metrics_token_file, label="metrics token")
            if args.metrics_token_file is not None
            else ""
        )
        provider_probe_secret = (
            read_secret_file(
                args.provider_probe_secret_file,
                label="provider probe receipt signature secret",
            )
            if args.provider_probe_secret_file is not None
            else ""
        )
        report = run_production_acceptance(
            args.base_url,
            token=args.token,
            metrics_token=metrics_token,
            require_production=args.require_production,
            probe_enterprise=args.probe_enterprise,
            probe_planning=args.probe_planning,
            provider_probe_receipts=args.provider_probe_receipt,
            provider_probe_secret=provider_probe_secret,
            provider_probe_max_age_hours=args.provider_probe_max_age_hours,
        )
    except AcceptanceError as exc:
        parser.error(str(exc))
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
