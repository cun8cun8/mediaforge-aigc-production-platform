from __future__ import annotations

from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.service import MediaForgeService, WorkflowError


def brief(project_id: str = "workflow_project") -> CreativeBrief:
    return CreativeBrief(
        project_id=project_id,
        title="Workflow project",
        premise="A caller receives a warning from the future.",
        genre="suspense",
        style="cinematic",
        characters=["Alice", "Bob"],
        duration_seconds=30,
        budget=2,
    )


def stage(workflow: dict, key: str) -> dict:
    return next(item for item in workflow["stages"] if item["key"] == key)


def test_workflow_locks_stage_evidence_and_cascades_rework(tmp_path):
    service = MediaForgeService(tmp_path)
    service.create_project(brief())

    initial = service.production_workflow("workflow_project")
    assert stage(initial, "script")["status"] == "IN_PROGRESS"

    plan = service.generate_plan("workflow_project")
    assert stage(service.production_workflow("workflow_project"), "script")["status"] == "READY"

    service.lock_production_stage("workflow_project", "script", actor="writer")
    assert stage(service.production_workflow("workflow_project"), "storyboard")["status"] == "READY"

    service.lock_production_stage("workflow_project", "storyboard", actor="director")
    locked = service.production_workflow("workflow_project")
    assert stage(locked, "script")["status"] == "LOCKED"
    assert stage(locked, "storyboard")["status"] == "LOCKED"

    shot_id = plan["shots"][0]["shot"]["shot_id"]
    service.update_shot_card(
        "workflow_project",
        shot_id,
        changes={"mood": "urgent"},
        actor="director",
    )
    reworked = service.production_workflow("workflow_project")
    assert stage(reworked, "script")["status"] == "LOCKED"
    assert stage(reworked, "storyboard")["status"] == "REWORK"
    assert reworked["latest_rework"]["from_stage"] == "storyboard"
    assert reworked["latest_rework"]["invalidated_stages"] == ["storyboard"]

    restarted = MediaForgeService(tmp_path)
    restored = restarted.production_workflow("workflow_project")
    assert stage(restored, "storyboard")["status"] == "REWORK"
    assert restored["latest_rework"]["reason"] == "shot_card_updated"

    restarted.update_project_brief(
        "workflow_project",
        changes={"title": "Workflow project revised"},
        actor="producer",
    )
    brief_rework = restarted.production_workflow("workflow_project")
    assert stage(brief_rework, "script")["status"] == "REWORK"
    assert brief_rework["latest_rework"]["reason"] == "project_brief_updated"


def test_required_stage_locks_guard_actual_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_REQUIRE_STAGE_LOCKS", "true")
    service = MediaForgeService(tmp_path)
    service.create_project(brief("workflow_enforced"))
    plan = service.generate_plan("workflow_enforced")
    shot_id = plan["shots"][0]["shot"]["shot_id"]

    try:
        service.submit_shot("workflow_enforced", shot_id)
    except WorkflowError as exc:
        assert "production stage must be locked" in str(exc)
    else:  # pragma: no cover - documents the required guard
        raise AssertionError("generation should require locked upstream stages")

    service.lock_production_stage("workflow_enforced", "script")
    service.lock_production_stage("workflow_enforced", "storyboard")
    service.lock_production_stage("workflow_enforced", "assets")
    result = service.submit_shot("workflow_enforced", shot_id)
    assert result["artifact"]
