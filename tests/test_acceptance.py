from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mediaforge_p1.acceptance import run_production_acceptance, write_report


def test_production_acceptance_is_non_destructive_and_can_require_production(
    tmp_path,
) -> None:
    requests: list[tuple[str, str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def _reply(self, value: dict) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reply_text(self, value: str) -> None:
            body = value.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            requests.append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/metrics":
                self._reply_text("mediaforge_process_uptime_seconds 12\n")
                return
            payloads = {
                "/health": {"status": "ok"},
                "/providers/diagnostics": {
                    "grade": "SIMULATION",
                    "ready": True,
                    "production_ready": False,
                    "api_key": "must-not-appear-in-report",
                },
                "/source-ingest/status": {
                    "configured": False,
                    "mode": "disabled",
                    "configuration_error": None,
                },
                "/planning/status": {
                    "engine": "langgraph",
                    "configured": True,
                    "version": "mediaforge-planning-graph-v3",
                },
                "/enterprise/status": {"topology": "single-control-plane"},
                "/ops/readiness": {
                    "ready": True,
                    "production_ready": False,
                    "grade": "READY",
                    "blocking_failures": [],
                    "warnings": ["provider"],
                },
                "/ops/alerts": {
                    "grade": "HEALTHY",
                    "critical_count": 0,
                    "warning_count": 0,
                },
            }
            self._reply(payloads[self.path])

        def do_POST(self) -> None:
            requests.append(("POST", self.path, self.headers.get("Authorization")))
            self._reply({"reachable": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        report = run_production_acceptance(
            base_url,
            token="acceptance-token",
            metrics_token="metrics-acceptance-token",
            probe_enterprise=True,
            probe_planning=True,
        )
        assert report["passed"] is True
        assert report["grade"] == "READY"
        assert {item["code"] for item in report["checks"]} >= {
            "health",
            "provider_diagnostics",
            "source_ingest",
            "planning_status",
            "enterprise_status",
            "readiness",
            "operations_alerts",
            "metrics",
            "enterprise_probe",
            "planning_probe",
        }
        assert "must-not-appear-in-report" not in json.dumps(report)
        assert "metrics-acceptance-token" not in json.dumps(report)
        assert all(
            header == "Bearer acceptance-token"
            for _method, path, header in requests
            if path != "/metrics"
        )
        assert next(
            header for _method, path, header in requests if path == "/metrics"
        ) == "Bearer metrics-acceptance-token"

        strict = run_production_acceptance(base_url, require_production=True)
        assert strict["passed"] is False
        assert strict["blocking_failures"] == ["production_readiness"]
        output = tmp_path / "acceptance.json"
        write_report(output, report)
        assert output.is_file()
        assert "must-not-appear-in-report" not in output.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
