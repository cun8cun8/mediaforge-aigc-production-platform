from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


def read_secret_file(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AcceptanceError("metrics token file cannot be read") from exc
    if not value:
        raise AcceptanceError("metrics token file is empty")
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
) -> dict[str, Any]:
    """Run non-destructive API acceptance checks without exposing credentials.

    Provider media generation is intentionally excluded: it may incur cost and is
    exercised separately by ``mediaforge-provider-probe`` after operator approval.
    """
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
        "--output",
        type=Path,
        default=Path("artifacts/production-acceptance.json"),
    )
    args = parser.parse_args()
    try:
        metrics_token = (
            read_secret_file(args.metrics_token_file)
            if args.metrics_token_file is not None
            else ""
        )
        report = run_production_acceptance(
            args.base_url,
            token=args.token,
            metrics_token=metrics_token,
            require_production=args.require_production,
            probe_enterprise=args.probe_enterprise,
            probe_planning=args.probe_planning,
        )
    except AcceptanceError as exc:
        parser.error(str(exc))
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
