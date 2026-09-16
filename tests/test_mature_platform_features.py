from __future__ import annotations

import base64
import json
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient
import pytest

from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.registry import DEFAULT_RECORDS
from mediaforge_p1.service import MediaForgeService


def brief(project_id: str = "mature_features") -> CreativeBrief:
    return CreativeBrief(
        project_id=project_id,
        title="Prompt governed production",
        premise="A producer reviews a generated scene before its release.",
        genre="drama",
        duration_seconds=30,
        style="natural daylight",
        characters=["Avery", "Blake"],
        budget=2.0,
    )


def test_prompt_registry_annotations_experiments_and_timeline_are_durable(tmp_path: Path) -> None:
    service = MediaForgeService(tmp_path)
    service.create_project(brief())

    registry = service.prompt_registry("mature_features")
    assert registry["active_count"] == 3
    baseline = registry["active"]["planning.story"]
    created = service.create_prompt_version(
        "mature_features",
        key="planning.story",
        template="Create a concise plan for {{brief.title}}: {{brief.premise}}.",
        label="Concise planner",
        actor="prompt-editor",
    )
    assert created["prompt"]["version"] == baseline["version"] + 1
    activated = service.activate_prompt_version(
        "mature_features", created["prompt"]["prompt_id"], actor="prompt-editor"
    )
    assert activated["prompt"]["status"] == "ACTIVE"
    assert service.prompt_registry("mature_features")["active"]["planning.story"]["prompt_id"] == created["prompt"]["prompt_id"]

    annotation = service.record_evaluation_annotation(
        "mature_features",
        target_type="project",
        target_id="mature_features",
        verdict="PASS",
        rating=0.9,
        note="Narrative is ready for a storyboard review.",
        actor="human-reviewer",
    )
    assert annotation["annotation"]["verdict"] == "PASS"
    experiment = service.register_prompt_experiment(
        "mature_features",
        name="Concise story planning",
        prompt_key="planning.story",
        control_prompt_id=baseline["prompt_id"],
        treatment_prompt_id=created["prompt"]["prompt_id"],
        objective="Reduce planning revisions without lowering approval rate.",
    )
    assert experiment["experiment"]["status"] == "REGISTERED"

    plan = service.generate_plan("mature_features")
    assert plan["story_bible"]["prompt_snapshots"]
    timeline = service.edit_timeline("mature_features")
    assert len(timeline["video_clips"]) == 6
    updated = service.update_edit_timeline(
        "mature_features",
        clips=[
            {
                "shot_id": clip["shot_id"],
                "start_seconds": clip["start_seconds"],
                "duration_seconds": clip["duration_seconds"],
                "enabled": clip["enabled"],
            }
            for clip in timeline["video_clips"]
        ],
    )
    assert updated["revision"] == 1
    exported = service.export_edit_timeline("mature_features", format="otio_json")
    assert Path(exported["path"]).is_file()

    restored = MediaForgeService(tmp_path)
    summary = restored.llmops_summary("mature_features")
    assert summary["annotation_count"] == 1
    assert summary["experiment_count"] == 1
    assert restored.edit_timeline("mature_features")["revision"] == 1

    snapshot = service.export_project_snapshot("mature_features")
    imported = service.import_project_snapshot(
        json.loads(Path(snapshot["snapshot"]).read_text(encoding="utf-8")),
        target_project_id="mature_features_imported",
    )
    assert imported["edit_timeline"]["revision"] == 1


def test_media_derivatives_are_catalogued_and_downloadable(tmp_path: Path) -> None:
    service = MediaForgeService(tmp_path)
    service.create_project(brief("derivatives"))
    source = tmp_path / "reference.png"
    Image.new("RGB", (960, 540), "#126782").save(source)
    reference = service.register_reference_asset(
        "derivatives",
        name="Character reference",
        content_b64=base64.b64encode(source.read_bytes()).decode("ascii"),
        license="user_supplied_or_project_owned",
        kind="character_reference",
        character="Avery",
    )["asset"]

    result = service.asset_derivatives("derivatives", reference["asset_id"])
    assert result["cached"] is False
    assert result["derivatives"][0]["kind"] == "thumbnail"
    assert result["derivatives"][0]["available"] is True
    inventory = service.asset_inventory("derivatives")
    asset = next(item for item in inventory["reference_assets"] if item["asset_id"] == reference["asset_id"])
    derivative = asset["derivatives"][0]
    downloaded = service.asset_derivative_download(
        "derivatives", reference["asset_id"], derivative["derivative_id"]
    )
    assert downloaded["path"].is_file()
    assert service.asset_derivatives("derivatives", reference["asset_id"])["cached"] is True


def test_mature_platform_api_routes_are_exposed(tmp_path: Path) -> None:
    with TestClient(create_app(output_root=tmp_path)) as client:
        payload = brief("api_mature").model_dump(mode="json")
        assert client.post("/projects", json=payload).status_code == 201
        prompts = client.get("/projects/api_mature/prompts")
        assert prompts.status_code == 200
        created = client.post(
            "/projects/api_mature/prompts",
            json={
                "key": "planning.story",
                "template": "Plan {{brief.title}} using {{brief.genre}}.",
                "activate": True,
            },
        )
        assert created.status_code == 201
        annotation = client.post(
            "/projects/api_mature/evaluations/annotations",
            json={
                "target_type": "project",
                "target_id": "api_mature",
                "verdict": "PASS",
            },
        )
        assert annotation.status_code == 201
        assert client.get("/projects/api_mature/llmops").json()["annotation_count"] == 1
        assert client.get("/projects/api_mature/timeline").status_code == 200


def test_collaboration_locks_provider_contracts_and_credentials_are_durable(tmp_path: Path) -> None:
    service = MediaForgeService(tmp_path)
    service.create_project(brief("collaboration_foundations"))
    presence = service.update_collaboration_presence(
        "collaboration_foundations",
        subject="editor-a",
        status="BUSY",
        section="timeline",
    )
    assert presence["active_member_count"] == 1
    lock = service.acquire_edit_lock(
        "collaboration_foundations",
        subject="editor-a",
        target_type="timeline",
        target_id="main",
    )["lock"]
    assert lock["subject"] == "editor-a"
    try:
        service.acquire_edit_lock(
            "collaboration_foundations",
            subject="editor-b",
            target_type="timeline",
            target_id="main",
        )
    except ValueError as exc:
        assert "held by editor-a" in str(exc)
    else:  # pragma: no cover - the lock must be exclusive
        raise AssertionError("second editor acquired an active edit lock")
    assert service.release_edit_lock(
        "collaboration_foundations", lock["lock_id"], subject="editor-a"
    )["active_lock_count"] == 0

    service.generate_plan("collaboration_foundations")
    contracts = service.validate_provider_contracts("collaboration_foundations")
    assert contracts["summary"]["protocol_passed"] is True
    assert contracts["summary"]["routable_shot_count"] == 6
    assert service.project_jobs("collaboration_foundations")["total_count"] == 0

    source = tmp_path / "credential-source.png"
    Image.new("RGB", (640, 360), "#237b65").save(source)
    asset = service.register_reference_asset(
        "collaboration_foundations",
        name="Credential reference",
        content_b64=base64.b64encode(source.read_bytes()).decode("ascii"),
        license="user_supplied_or_project_owned",
        kind="character_reference",
        character="Avery",
    )["asset"]
    credential = service.create_content_credential(
        "collaboration_foundations", asset_id=asset["asset_id"]
    )["credential"]
    assert credential["c2pa"]["status"] == "UNSIGNED"
    assert Path(credential["manifest_path"]).is_file()
    verification = service.verify_content_credential(
        "collaboration_foundations", credential["credential_id"]
    )["verification"]
    assert verification["status"] == "INTEGRITY_VERIFIED"
    assert verification["passed"] is True
    production_report = service.production_report("collaboration_foundations")
    assert production_report["provider_contract"]["summary"]["protocol_passed"] is True
    provenance = service.project_provenance("collaboration_foundations")
    assert provenance["governance"]["provider_contract"]["summary"]["planned_shot_count"] == 6
    governance_paths = service._write_governance_files("collaboration_foundations")
    assert any(path.name == "provider-contract.json" for path in governance_paths)
    restored = MediaForgeService(tmp_path)
    assert restored.project_content_credentials("collaboration_foundations")["summary"]["count"] == 1


def test_collaboration_contract_and_credential_api_routes(tmp_path: Path) -> None:
    with TestClient(create_app(output_root=tmp_path)) as client:
        payload = brief("api_foundations").model_dump(mode="json")
        assert client.post("/projects", json=payload).status_code == 201
        assert client.post(
            "/projects/api_foundations/collaboration/presence",
            json={"status": "ACTIVE", "section": "brief"},
        ).status_code == 200
        lock = client.post(
            "/projects/api_foundations/collaboration/locks",
            json={"target_type": "brief", "target_id": "api_foundations"},
        )
        assert lock.status_code == 200
        lock_id = lock.json()["lock"]["lock_id"]
        assert client.delete(
            f"/projects/api_foundations/collaboration/locks/{lock_id}"
        ).status_code == 200
        assert client.get("/providers/contracts").status_code == 200
        assert client.post(
            "/projects/api_foundations/providers/contracts/validate", json={}
        ).status_code == 200
        assert client.get(
            "/projects/api_foundations/evaluations/baselines"
        ).status_code == 200
        assert client.post(
            "/projects/api_foundations/evaluations/baselines",
            json={"name": "cannot-baseline-incomplete-project"},
        ).status_code == 422


def test_collaboration_documents_replay_merge_and_snapshot_import(tmp_path: Path) -> None:
    service = MediaForgeService(tmp_path)
    service.create_project(brief("collaboration_documents"))
    applied = service.apply_collaboration_text_operations(
        "collaboration_documents",
        document_id="production-notes",
        actor="editor-a",
        operations=[
            {"kind": "insert", "op_id": "editor-a:1", "after_id": "root", "value": "A"},
            {"kind": "insert", "op_id": "editor-a:2", "after_id": "editor-a:1", "value": "B"},
        ],
    )
    assert applied["document"]["text"] == "AB"
    # Repeating a causal operation is idempotent and never duplicates text.
    replayed = service.apply_collaboration_text_operations(
        "collaboration_documents",
        document_id="production-notes",
        actor="editor-a",
        operations=[{"kind": "insert", "op_id": "editor-a:2", "after_id": "editor-a:1", "value": "B"}],
    )
    assert replayed["changed_count"] == 0
    replaced = service.replace_collaboration_document(
        "collaboration_documents",
        document_id="production-notes",
        text="已确认镜头节奏",
        actor="editor-b",
    )
    assert replaced["document"]["text"] == "已确认镜头节奏"
    events = service.collaboration_events("collaboration_documents")
    assert events["events"][-1]["type"] == "document.updated"

    snapshot = service.export_project_snapshot("collaboration_documents")
    imported = service.import_project_snapshot(
        json.loads(Path(snapshot["snapshot"]).read_text(encoding="utf-8")),
        target_project_id="collaboration_documents_imported",
    )
    assert imported["project_id"] == "collaboration_documents_imported"
    assert service.collaboration_documents("collaboration_documents_imported")["documents"][0]["text"] == "已确认镜头节奏"

    restored = MediaForgeService(tmp_path)
    restored_documents = restored.collaboration_documents("collaboration_documents")
    assert restored_documents["documents"][0]["text"] == "已确认镜头节奏"


def test_collaboration_document_api_and_websocket_snapshot(tmp_path: Path) -> None:
    with TestClient(create_app(output_root=tmp_path)) as client:
        payload = brief("api_collaboration_documents").model_dump(mode="json")
        assert client.post("/projects", json=payload).status_code == 201
        saved = client.put(
            "/projects/api_collaboration_documents/collaboration/documents/production-notes",
            json={"text": "镜头一通过"},
        )
        assert saved.status_code == 200
        assert saved.json()["document"]["text"] == "镜头一通过"
        operations = client.post(
            "/projects/api_collaboration_documents/collaboration/documents/production-notes/operations",
            json={
                "operations": [
                    {"kind": "insert", "op_id": "api-editor:1", "after_id": "root", "value": "新"}
                ]
            },
        )
        assert operations.status_code == 200
        assert "新" in operations.json()["document"]["text"]
        events = client.get("/projects/api_collaboration_documents/collaboration/events")
        assert events.status_code == 200
        assert any(event["type"] == "document.updated" for event in events.json()["events"])
        with client.websocket_connect(
            "/projects/api_collaboration_documents/collaboration/events/ws"
        ) as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["data"]["documents"]["documents"][0]["document_id"] == "production-notes"


def test_asset_rights_expiry_becomes_a_blocking_compliance_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFORGE_REQUIRE_ASSET_RIGHTS_RECORD", "true")
    service = MediaForgeService(tmp_path)
    service.create_project(brief("asset_rights"))
    source = tmp_path / "rights-reference.png"
    Image.new("RGB", (640, 360), "#654b9d").save(source)
    asset = service.register_reference_asset(
        "asset_rights",
        name="Licensed character reference",
        content_b64=base64.b64encode(source.read_bytes()).decode("ascii"),
        license="licensed",
        kind="character_reference",
        character="Avery",
    )["asset"]
    expired = {
        "registry_id": "asset:expired-reference",
        "kind": "asset",
        "match": asset["sha256"],
        "match_type": "exact",
        "license": "licensed",
        "status": "approved",
        "evidence": "expired contract fixture",
        "valid_until": "2020-01-01T00:00:00+00:00",
    }
    service.import_license_registry([*DEFAULT_RECORDS, expired], actor="rights-owner")
    expired_compliance = service.project_compliance("asset_rights")
    rights_gate = next(
        check for check in expired_compliance["checks"] if check["name"] == "asset_rights_registry"
    )
    assert rights_gate["passed"] is False
    assert expired_compliance["passed"] is False
    assert rights_gate["observed"]["unregistered"][0]["reason"] == "expired"

    active = {**expired, "registry_id": "asset:active-reference", "valid_until": "2099-01-01T00:00:00+00:00"}
    service.import_license_registry([*DEFAULT_RECORDS, active], actor="rights-owner")
    assert service.project_compliance("asset_rights")["passed"] is True


def test_evaluation_baseline_detects_prompt_or_provider_context_drift(tmp_path: Path) -> None:
    service = MediaForgeService(tmp_path)
    project_id = "evaluation_baseline"
    service.create_project(brief(project_id))
    source = tmp_path / "baseline-reference.png"
    Image.new("RGB", (640, 360), "#156f68").save(source)
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    for name, character in (("Avery reference", "Avery"), ("Blake reference", "Blake")):
        service.register_reference_asset(
            project_id,
            name=name,
            content_b64=encoded,
            license="user_supplied_or_project_owned",
            kind="character_reference",
            character=character,
        )
    service.generate_plan(project_id)
    service.submit_pending_shots(project_id)
    approved = service.approve_ready_shots(project_id, actor="quality-owner")
    assert len(approved["approved"]) == 6
    service.export_project(project_id)
    baseline = service.create_evaluation_baseline(
        project_id,
        name="Mock provider release candidate",
        actor="quality-owner",
    )["baseline"]
    current = service.latest_evaluation_report(project_id)
    assert current["regression"]["passed"] is True
    assert baseline["active"] is True
    restored = MediaForgeService(tmp_path)
    assert restored.latest_evaluation_report(project_id)["baselines"][0]["baseline_id"] == baseline["baseline_id"]
    service.build_delivery_package(project_id)

    active = service.prompt_registry(project_id)["active"]["planning.story"]
    service.create_prompt_version(
        project_id,
        key="planning.story",
        template="Create a revised story plan for {{brief.title}} using {{brief.genre}}.",
        label="Changed admission context",
        activate=True,
        actor="prompt-owner",
    )
    drifted = service.latest_evaluation_report(project_id)
    assert drifted["regression"]["passed"] is False
    result = drifted["regression"]["results"][0]
    assert result["context_passed"] is False
    assert active["prompt_id"] != service.prompt_registry(project_id)["active"]["planning.story"]["prompt_id"]
    with pytest.raises(ValueError, match="model admission baseline did not pass"):
        service.release_project(project_id)

    service.activate_prompt_version(project_id, active["prompt_id"], actor="prompt-owner")
    assert service.latest_evaluation_report(project_id)["regression"]["passed"] is True
    service.release_project(project_id)
    with pytest.raises(ValueError, match="released project evaluation baseline cannot be changed"):
        service.create_evaluation_baseline(project_id, name="Post-release mutation")
