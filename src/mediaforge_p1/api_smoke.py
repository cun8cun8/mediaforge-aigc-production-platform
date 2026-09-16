from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .callback_security import callback_signature
from .media import create_placeholder_audio, probe_audio


class ApiSmokeError(RuntimeError):
    pass


def request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    api_token = os.getenv("MEDIAFORGE_SMOKE_TOKEN", "").strip()
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    callback_secret = os.getenv("MEDIAFORGE_CALLBACK_SECRET", "").strip()
    if callback_secret and path.endswith("/callback") and data is not None:
        timestamp = str(int(time.time()))
        headers["X-MediaForge-Timestamp"] = timestamp
        headers["X-MediaForge-Signature"] = callback_signature(
            callback_secret,
            timestamp=timestamp,
            method=method,
            path=path,
            body=data,
        )
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiSmokeError(f"{method} {path} failed: {exc.code} {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiSmokeError(f"{method} {path} failed: {exc}") from exc


def run_smoke(base_url: str, project_id: str) -> dict[str, Any]:
    brief = {
        "project_id": project_id,
        "title": "午夜来电 Smoke 验证",
        "premise": "女主在深夜接到一个来自自己未来的电话。",
        "genre": "都市悬疑",
        "style": "cinematic blue hour",
        "duration_seconds": 30,
        "budget": 2.0,
        "characters": ["林夏", "周启"],
    }
    request_json(base_url, "POST", "/projects", brief)
    studio_metrics_before = request_json(base_url, "GET", "/studio/metrics")
    if studio_metrics_before["schema_version"] != "mediaforge-studio-metrics-v1":
        raise ApiSmokeError("unexpected studio metrics schema")
    readiness = request_json(base_url, "GET", "/ops/readiness")
    if readiness["schema_version"] != "mediaforge-production-readiness-v1":
        raise ApiSmokeError("unexpected production readiness schema")
    runtime_metrics = request_json(base_url, "GET", "/metrics/runtime")
    if runtime_metrics["schema_version"] != "mediaforge-runtime-metrics-v1":
        raise ApiSmokeError("unexpected runtime metrics schema")
    registry = request_json(base_url, "GET", "/governance/license-registry")
    if registry["schema_version"] != "mediaforge-license-registry-v1":
        raise ApiSmokeError("unexpected license registry schema")
    registry_validation = request_json(
        base_url,
        "POST",
        "/governance/license-registry/validate",
        {"records": registry["records"], "source": "api-smoke"},
    )
    if not registry_validation["valid"]:
        raise ApiSmokeError("license registry validation failed")
    callback_security = request_json(
        base_url,
        "GET",
        "/providers/callback-security",
    )
    registry_export = request_json(
        base_url,
        "GET",
        "/governance/license-registry/export",
    )
    if registry_export["schema_version"] != registry["schema_version"]:
        raise ApiSmokeError("license registry export mismatch")
    callback_project_id = f"{project_id}_callback"
    callback_brief = {**brief, "project_id": callback_project_id}
    request_json(base_url, "POST", "/projects", callback_brief)
    callback_plan = request_json(base_url, "POST", f"/projects/{callback_project_id}/plan")
    callback_shot_id = callback_plan["shots"][0]["shot"]["shot_id"]
    callback_queued = request_json(
        base_url,
        "POST",
        f"/projects/{callback_project_id}/shots/{callback_shot_id}/enqueue",
    )
    callback_job_id = callback_queued["job"]["job_id"]
    callback_failed = request_json(
        base_url,
        "POST",
        f"/projects/{callback_project_id}/jobs/{callback_job_id}/callback",
        {
            "event_id": "smoke-callback-failure-1",
            "provider": "mock-provider",
            "status": "FAILED",
            "reason": "smoke callback failure path",
        },
    )
    if callback_failed["job"]["status"] != "RETRY_WAIT":
        raise ApiSmokeError("provider callback failure was not scheduled for retry")
    callback_duplicate = request_json(
        base_url,
        "POST",
        f"/projects/{callback_project_id}/jobs/{callback_job_id}/callback",
        {
            "event_id": "smoke-callback-failure-1",
            "provider": "mock-provider",
            "status": "FAILED",
            "reason": "duplicate smoke callback",
        },
    )
    if callback_duplicate["idempotent"] is not True:
        raise ApiSmokeError("provider callback duplicate was not idempotent")
    with tempfile.TemporaryDirectory(prefix="mediaforge-smoke-audio-") as temp_dir:
        audio_path = create_placeholder_audio(
            Path(temp_dir) / "smoke-tone.m4a",
            duration_seconds=1,
            frequency_hz=220,
        )
        audio_registered = request_json(
            base_url,
            "POST",
            f"/projects/{project_id}/audio",
            {
                "name": audio_path.name,
                "content_b64": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                "license": "mock_generated",
                "source": "api-smoke-tone",
                "actor": "smoke-runner",
            },
        )
    registered_audio = Path(audio_registered["audio_track"])
    if not registered_audio.is_file() or not probe_audio(registered_audio).valid:
        raise ApiSmokeError("audio track registration did not produce readable media")
    if audio_registered.get("audio_track_metadata", {}).get("license") != "mock_generated":
        raise ApiSmokeError("audio track governance metadata was not persisted")
    plan = request_json(base_url, "POST", f"/projects/{project_id}/plan")
    shots = plan["shots"]
    continuity = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/continuity",
    )
    if continuity["schema_version"] != "mediaforge-continuity-v1":
        raise ApiSmokeError("unexpected continuity report schema")
    if not continuity["passed"] or continuity["summary"]["duration_seconds"] != 30:
        raise ApiSmokeError("continuity report did not pass the planned timeline")
    first_shot_id = shots[0]["shot"]["shot_id"]
    first_run = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/submit",
    )
    route_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/route/export",
        {"actor": "smoke-runner"},
    )
    request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/review",
        {
            "status": "CHANGES_REQUESTED",
            "comment": "Smoke test requests one revision.",
        },
    )
    revision = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/revise",
        {"comment": "Smoke test revision run."},
    )
    comparison = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/variants/compare",
        {"candidate_count": 2, "actor": "smoke-runner"},
    )
    promoted_variant_id = comparison["comparison"]["recommended_variant_id"]
    promoted = request_json(
        base_url,
        "POST",
        (
            f"/projects/{project_id}/shots/{first_shot_id}/variants/"
            f"{promoted_variant_id}/promote"
        ),
        {
            "comment": "Smoke test promotes the recommended A/B candidate.",
            "actor": "smoke-runner",
        },
    )
    comparison_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/variants/export",
        {"actor": "smoke-runner"},
    )
    request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/{first_shot_id}/review",
        {"status": "APPROVED"},
    )

    batch_generation = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/submit-all",
    )
    batch_approval = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/shots/approve-ready",
        {"comment": "Smoke test batch approval."},
    )

    exported = request_json(base_url, "POST", f"/projects/{project_id}/export")
    final_audio = Path(exported.get("final_mp4", ""))
    if not final_audio.is_file() or not probe_audio(final_audio).valid:
        raise ApiSmokeError("final sample does not contain a readable audio stream")
    if exported.get("audio_track") != str(registered_audio):
        raise ApiSmokeError("final export lost the configured audio track")
    packaged = request_json(base_url, "POST", f"/projects/{project_id}/package")
    with zipfile.ZipFile(packaged["package_zip"]) as package_archive:
        if not any(
            name.startswith("final_audio.") for name in package_archive.namelist()
        ):
            raise ApiSmokeError("delivery package is missing the final audio track")
    benchmark_run = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/providers/benchmark",
        {"actor": "smoke-runner"},
    )
    package_verification = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/package/verify",
        {"actor": "smoke-runner"},
    )
    provenance = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/provenance",
    )
    provenance_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/provenance/export",
        {"actor": "smoke-runner"},
    )
    compliance = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/compliance",
    )
    compliance_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/compliance/export",
        {"actor": "smoke-runner"},
    )
    continuity_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/continuity/export",
        {"actor": "smoke-runner"},
    )
    if provenance["schema_version"] != "mediaforge-provenance-v1":
        raise ApiSmokeError("unexpected provenance report schema")
    if provenance["summary"]["artifact_count"] < provenance["summary"]["shot_count"]:
        raise ApiSmokeError("provenance report is missing shot artifacts")
    if compliance["schema_version"] != "mediaforge-compliance-v1":
        raise ApiSmokeError("unexpected compliance report schema")
    if not compliance["passed"]:
        raise ApiSmokeError("compliance report blocked release")
    if not continuity_export["report"]["passed"]:
        raise ApiSmokeError("continuity report blocked release")
    released = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/release",
        {
            "channel": "smoke-test",
            "comment": "Release gate smoke verification.",
            "actor": "smoke-runner",
        },
    )
    delivered = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/deliveries/dispatch",
        {
            "channel": "smoke-distribution",
            "recipient": "smoke-archive",
            "destination_uri": f"mediaforge://smoke-distribution/{project_id}",
            "note": "Smoke test delivery receipt.",
            "actor": "smoke-runner",
        },
    )
    operations_after_delivery = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/operations",
    )
    if operations_after_delivery["next_action"]["code"] != "ACKNOWLEDGE_DELIVERY":
        raise ApiSmokeError("delivery acknowledgement was not requested")
    delivery_ack = request_json(
        base_url,
        "POST",
        (
            f"/projects/{project_id}/deliveries/"
            f"{delivered['delivery']['delivery_id']}/acknowledge"
        ),
        {
            "accepted": True,
            "note": "Smoke test recipient confirms receipt.",
            "actor": "smoke-recipient",
        },
    )
    distribution = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/distribution",
    )
    if distribution["schema_version"] != "mediaforge-distribution-v1":
        raise ApiSmokeError("unexpected distribution report schema")
    if distribution["count"] != 1:
        raise ApiSmokeError("distribution report did not record delivery")
    if distribution["summary"]["accepted_count"] != 1:
        raise ApiSmokeError("distribution report did not record delivery acknowledgement")
    operations_after_ack = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/operations",
    )
    studio_metrics = request_json(base_url, "GET", "/studio/metrics")
    if studio_metrics["shots"]["approved"] < len(shots):
        raise ApiSmokeError("studio metrics did not record approved shots")
    if not Path(delivered["receipt_path"]).is_file():
        raise ApiSmokeError("delivery receipt file is missing")
    if delivery_ack["delivery"]["status"] != "ACCEPTED":
        raise ApiSmokeError("delivery acknowledgement did not update status")
    evaluation = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/evaluations/latest",
    )
    benchmark = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/providers/benchmark",
    )
    audit_csv = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/audit/export-csv",
        {"actor": "smoke-runner"},
    )
    snapshot = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/snapshot/export",
        {"actor": "smoke-runner"},
    )
    production_report = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/reports/export",
        {"actor": "smoke-runner"},
    )
    trace = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/trace",
    )
    trace_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/trace/export",
        {"actor": "smoke-runner"},
    )
    retrospective = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/retrospective",
    )
    retrospective_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/retrospective/export",
        {"actor": "smoke-runner"},
    )
    if trace["schema_version"] != "mediaforge-trace-v1":
        raise ApiSmokeError("unexpected trace report schema")
    if trace["summary"]["job_count"] < 6:
        raise ApiSmokeError("trace report is missing generation jobs")
    if not trace["summary"]["artifact_count"]:
        raise ApiSmokeError("trace report is missing generated artifacts")
    if not Path(trace_export["trace_report"]).is_file():
        raise ApiSmokeError("trace report file is missing")
    if retrospective["schema_version"] != "mediaforge-retrospective-v1":
        raise ApiSmokeError("unexpected retrospective report schema")
    if retrospective["metrics"]["production"]["approved_shots"] != 6:
        raise ApiSmokeError("retrospective report is missing approved shots")
    if not Path(retrospective_export["retrospective_report"]).is_file():
        raise ApiSmokeError("retrospective report file is missing")
    closeout = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/closeout",
        {"actor": "smoke-runner"},
    )
    if closeout["report"]["schema_version"] != "mediaforge-closeout-v1":
        raise ApiSmokeError("unexpected closeout report schema")
    if closeout["report"]["summary"]["delivery_accepted_count"] != 1:
        raise ApiSmokeError("closeout report did not record accepted delivery")
    if closeout["report"]["project"]["archived"] is not True:
        raise ApiSmokeError("closeout report did not archive project")
    acceptance = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/acceptance",
    )
    acceptance_export = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/acceptance/export",
        {"actor": "smoke-runner"},
    )
    if acceptance["schema_version"] != "mediaforge-acceptance-certificate-v1":
        raise ApiSmokeError("unexpected acceptance certificate schema")
    if acceptance["summary"]["archived"] is not True:
        raise ApiSmokeError("acceptance certificate did not archive project")
    if acceptance["signoff"]["status"] != "ACCEPTED":
        raise ApiSmokeError("acceptance certificate did not record signoff")
    if not Path(acceptance["report_path"]).is_file():
        raise ApiSmokeError("acceptance certificate file is missing")
    if acceptance_export["report"]["schema_version"] != "mediaforge-acceptance-certificate-v1":
        raise ApiSmokeError("acceptance export did not preserve schema")
    if not Path(acceptance_export["acceptance_report"]).is_file():
        raise ApiSmokeError("acceptance export file is missing")
    closed_snapshot = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/snapshot/export",
        {"actor": "smoke-runner"},
    )
    closed_snapshot_payload = json.loads(
        Path(closed_snapshot["snapshot"]).read_text(encoding="utf-8")
    )
    if closed_snapshot_payload["project"]["closeout_report"] != closeout["closeout_report"]:
        raise ApiSmokeError("closed snapshot did not include closeout evidence")
    if closed_snapshot_payload["project"]["acceptance_report"] != acceptance_export["acceptance_report"]:
        raise ApiSmokeError("closed snapshot did not include acceptance evidence")
    if not Path(closed_snapshot_payload["project"]["delivery_verification_report"]).is_file():
        raise ApiSmokeError("closed snapshot did not include delivery verification")
    operations_after_closeout = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/operations",
    )
    if operations_after_closeout["next_action"]["code"] != "ARCHIVE_PACKAGE":
        raise ApiSmokeError("closed project did not request archive package")
    archive_package = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/archive-package",
        {"actor": "smoke-runner"},
    )
    archive_verify = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/archive-package/verify",
        {"actor": "smoke-runner"},
    )
    operations_after_archive = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/operations",
    )
    if archive_package["summary"]["schema_version"] != "mediaforge-archive-package-v1":
        raise ApiSmokeError("unexpected archive package schema")
    if archive_package["verification"]["schema_version"] != "mediaforge-archive-verification-v1":
        raise ApiSmokeError("unexpected archive verification schema")
    if not archive_package["verification"]["passed"]:
        raise ApiSmokeError("archive package verification failed")
    if not archive_verify["verification"]["passed"]:
        raise ApiSmokeError("archive package re-verification failed")
    if operations_after_archive["next_action"]["code"] != "CLOSED":
        raise ApiSmokeError("verified archive package did not close operations")
    required_archive_files = {
        "archive-summary.json",
        "deliverables/final_sample.mp4",
        "deliverables/delivery-package.zip",
        "governance/acceptance-report.json",
        "governance/closeout-report.json",
        "governance/delivery-verification.json",
        "governance/project-snapshot.json",
        "governance/audit-log.csv",
        "governance/trace-report.json",
        "governance/continuity-report.json",
    }
    with zipfile.ZipFile(archive_package["archive_package"]) as archive:
        archive_names = set(archive.namelist())
    if not any(
        name.startswith("deliverables/final_audio.") for name in archive_names
    ):
        raise ApiSmokeError("archive package is missing the final audio track")
    missing_archive_files = sorted(required_archive_files - archive_names)
    if missing_archive_files:
        raise ApiSmokeError(
            "archive package is missing files: "
            + ", ".join(missing_archive_files)
        )
    imported_archive = request_json(
        base_url,
        "POST",
        "/projects/import-archive",
        {
            "archive_zip": archive_package["archive_package"],
            "project_id": f"{project_id}_archive_imported",
            "actor": "smoke-runner",
        },
    )
    if imported_archive["project_id"] != f"{project_id}_archive_imported":
        raise ApiSmokeError("archive import returned the wrong project")
    if not imported_archive.get("delivery_verified"):
        raise ApiSmokeError("archive import did not preserve delivery verification")
    if not imported_archive.get("archive_verified"):
        raise ApiSmokeError("archive import did not preserve archive verification")
    imported_archive_audio = Path(imported_archive.get("audio_track", ""))
    if not imported_archive_audio.is_file() or not probe_audio(imported_archive_audio).valid:
        raise ApiSmokeError("archive import did not preserve a readable audio track")
    if imported_archive.get("release") is not None:
        raise ApiSmokeError("archive import should reset release state")
    if imported_archive["archive_import"]["source_project_id"] != project_id:
        raise ApiSmokeError("archive import lost source project identity")
    if not Path(imported_archive["archive_verification_report"]).is_file():
        raise ApiSmokeError("archive import verification report is missing")
    imported = request_json(
        base_url,
        "POST",
        "/projects/import-package",
        {
            "package_zip": released["package"]["package_zip"],
            "project_id": f"{project_id}_imported",
            "actor": "smoke-runner",
        },
    )
    if not imported.get("delivery_verified"):
        raise ApiSmokeError("imported delivery package was not verified")
    if imported.get("release") is not None:
        raise ApiSmokeError("imported delivery package should reset release state")
    imported_package_audio = Path(imported.get("audio_track", ""))
    if not imported_package_audio.is_file() or not probe_audio(imported_package_audio).valid:
        raise ApiSmokeError("delivery package import did not preserve a readable audio track")
    if not Path(imported["delivery_verification_report"]).is_file():
        raise ApiSmokeError("imported delivery verification report is missing")
    cloned = request_json(
        base_url,
        "POST",
        f"/projects/{project_id}/clone",
        {
            "project_id": f"{project_id}_branch",
            "title_suffix": "Branch",
            "actor": "smoke-runner",
        },
    )
    branch_id = cloned["project_id"]
    cloned_audio = Path(cloned.get("audio_track", ""))
    if not cloned_audio.is_file() or not probe_audio(cloned_audio).valid:
        raise ApiSmokeError("cloned project did not preserve a readable audio track")
    branch_queue = request_json(
        base_url,
        "POST",
        f"/projects/{branch_id}/shots/enqueue-all",
    )
    branch_drain = request_json(
        base_url,
        "POST",
        f"/projects/{branch_id}/queue/drain",
        {"limit": 10, "actor": "smoke-runner"},
    )
    global_drain = request_json(
        base_url,
        "POST",
        "/queue/drain-all",
        {
            "limit": 10,
            "actor": "smoke-runner",
            "include_archived": False,
        },
    )
    policy = request_json(base_url, "GET", f"/projects/{project_id}/policy")
    succeeded_jobs = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/jobs?status=SUCCEEDED",
    )
    approved_shots = request_json(
        base_url,
        "GET",
        f"/projects/{project_id}/shots?review_status=APPROVED&limit=2",
    )
    return {
        "project_id": project_id,
        "status": exported["status"],
        "final_mp4": exported["final_mp4"],
        "audio_track": exported["audio_track"],
        "audio_track_metadata": exported["audio_track_metadata"],
        "manifest": exported["manifest"],
        "package_zip": released["package"]["package_zip"],
        "package_file_count": released["package"].get("file_count", packaged["file_count"]),
        "package_verified": package_verification["verification"]["passed"],
        "package_verification_report": package_verification["verification_report"],
        "release_status": released["release"]["status"],
        "evaluation_score": evaluation["latest"]["score_percent"],
        "evaluation_passed": evaluation["latest"]["passed"],
        "benchmark_report": benchmark_run["benchmark_report"],
        "benchmark_recommended_provider": benchmark["latest"]["recommended_provider"],
        "audit_csv": audit_csv["audit_csv"],
        "snapshot": snapshot["snapshot"],
        "closed_snapshot": closed_snapshot["snapshot"],
        "production_report": production_report["production_report"],
        "trace_report": trace_export["trace_report"],
        "trace_id": trace["trace_id"],
        "trace_span_count": trace_export["span_count"],
        "retrospective_report": retrospective_export["retrospective_report"],
        "retrospective_maturity_percent": retrospective["maturity_percent"],
        "provenance_report": provenance_export["provenance_report"],
        "provenance_artifact_count": provenance_export["report"]["summary"]["artifact_count"],
        "provenance_shot_count": provenance_export["report"]["summary"]["shot_count"],
        "compliance_report": compliance_export["compliance_report"],
        "compliance_passed": compliance_export["report"]["passed"],
        "compliance_check_count": compliance_export["report"]["summary"]["check_count"],
        "continuity_report": continuity_export["continuity_report"],
        "continuity_passed": continuity_export["report"]["passed"],
        "continuity_check_count": continuity_export["report"]["summary"]["check_count"],
        "delivery_receipt": delivered["receipt_path"],
        "delivery_ack_status": delivery_ack["delivery"]["status"],
        "delivery_ack_time": delivery_ack["delivery"]["acknowledged_at"],
        "closeout_report": closeout["closeout_report"],
        "closeout_archived": closeout["archived"],
        "acceptance_report": acceptance["report_path"],
        "acceptance_export_report": acceptance_export["acceptance_report"],
        "archive_package": archive_package["archive_package"],
        "archive_verified": archive_verify["verification"]["passed"],
        "archive_file_count": archive_package["file_count"],
        "imported_archive_project_id": imported_archive["project_id"],
        "imported_archive_verified": imported_archive["archive_verified"],
        "imported_archive_source_project_id": imported_archive["archive_import"]["source_project_id"],
        "distribution_report": delivered["distribution_report"],
        "distribution_count": distribution["count"],
        "distribution_pending_count": distribution["summary"]["pending_count"],
        "distribution_accepted_count": distribution["summary"]["accepted_count"],
        "distribution_channel": distribution["latest"]["channel"],
        "imported_project_id": imported["project_id"],
        "imported_delivery_verified": imported["delivery_verified"],
        "imported_verification_report": imported["delivery_verification_report"],
        "imported_shots": len(imported["shots"]),
        "imported_release_reset": imported["release"] is None,
        "route_report": route_export["route_report"],
        "comparison_report": comparison_export["comparison_report"],
        "promoted_variant_id": promoted_variant_id,
        "variant_count": len(promoted["shot"]["variants"]),
        "cloned_project_id": cloned["project_id"],
        "cloned_shots": len(cloned["shots"]),
        "branch_queued": branch_queue["queued"],
        "branch_processed": branch_drain["processed"],
        "global_queue_processed": global_drain["processed"],
        "global_queue_remaining": global_drain["remaining"],
        "duration_seconds": exported["final_quality"]["duration_seconds"],
        "width": exported["final_quality"]["width"],
        "height": exported["final_quality"]["height"],
        "first_job_changed": (
            first_run["current_job_id"] != revision["current_job_id"]
        ),
        "first_artifact_changed": (
            first_run["artifact"]["artifact_id"]
            != revision["artifact"]["artifact_id"]
        ),
        "first_history_count": len(revision["artifact_history"]),
        "batch_submitted": batch_generation["submitted"],
        "batch_approved": len(batch_approval["approved"]),
        "operations_after_delivery_next_action": operations_after_delivery["next_action"]["code"],
        "operations_next_action": operations_after_ack["next_action"]["code"],
        "succeeded_jobs": succeeded_jobs["total_count"],
        "approved_shot_filter_count": approved_shots["total_count"],
        "policy_passed": policy["passed"],
        "policy_reports": policy["count"],
        "delivery_policy_passed": released["package"]["summary"]["policy"]["passed"],
        "delivery_compliance_passed": released["package"]["summary"]["compliance"]["passed"],
        "delivery_audit_count": released["package"]["summary"]["audit_count"],
        "studio_metrics_schema": studio_metrics["schema_version"],
        "studio_metrics_approval_rate": studio_metrics["shots"]["approval_rate"],
        "studio_metrics_success_rate": studio_metrics["jobs"]["success_rate"],
        "license_registry_schema": registry["schema_version"],
        "license_registry_validation": registry_validation["valid"],
        "callback_security_mode": callback_security["mode"],
        "provider_callback_status": callback_failed["job"]["status"],
        "provider_callback_idempotent": callback_duplicate["idempotent"],
        "production_readiness": readiness["grade"],
        "runtime_attempts": runtime_metrics["total_attempts"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MediaForge API smoke flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument(
        "--token",
        default=os.getenv("MEDIAFORGE_SMOKE_TOKEN", "").strip(),
        help="Bearer token for required-auth deployments.",
    )
    parser.add_argument(
        "--project-id",
        default=f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    args = parser.parse_args()
    if args.token:
        os.environ["MEDIAFORGE_SMOKE_TOKEN"] = args.token
    try:
        result = run_smoke(args.base_url, args.project_id)
    except ApiSmokeError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
