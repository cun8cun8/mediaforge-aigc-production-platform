from __future__ import annotations

from mediaforge_p1.contracts import CreativeBrief, ReviewStatus
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


def test_required_stage_locks_prepare_revision_for_relock(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFORGE_REQUIRE_STAGE_LOCKS", "true")
    service = MediaForgeService(tmp_path)
    service.create_project(brief("workflow_revision_lock"))
    plan = service.generate_plan("workflow_revision_lock")
    shot_id = plan["shots"][0]["shot"]["shot_id"]

    for stage_key in ("script", "storyboard", "assets"):
        service.lock_production_stage("workflow_revision_lock", stage_key)
    service.submit_shot("workflow_revision_lock", shot_id)
    service.review_shot(
        "workflow_revision_lock",
        shot_id,
        status=ReviewStatus.CHANGES_REQUESTED,
    )

    prepared = service.revise_shot("workflow_revision_lock", shot_id, comment="Rework")
    assert prepared["requires_stage_lock"] is True
    assert prepared["required_stage_locks"] == ["storyboard", "assets"]
    assert prepared["artifact"] is None
    assert stage(service.production_workflow("workflow_revision_lock"), "storyboard")["status"] == "REWORK"
    assert stage(service.production_workflow("workflow_revision_lock"), "assets")["status"] == "REWORK"

    service.lock_production_stage("workflow_revision_lock", "storyboard")
    service.lock_production_stage("workflow_revision_lock", "assets")
    regenerated = service.submit_shot("workflow_revision_lock", shot_id)
    assert regenerated["artifact"]


def test_required_stage_locks_prepare_all_batch_revisions_before_resubmitting(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEDIAFORGE_REQUIRE_STAGE_LOCKS", "true")
    service = MediaForgeService(tmp_path)
    service.create_project(brief("workflow_batch_revision_lock"))
    plan = service.generate_plan("workflow_batch_revision_lock")
    shot_ids = [item["shot"]["shot_id"] for item in plan["shots"][:2]]

    for stage_key in ("script", "storyboard", "assets"):
        service.lock_production_stage("workflow_batch_revision_lock", stage_key)
    for shot_id in shot_ids:
        service.submit_shot("workflow_batch_revision_lock", shot_id)
        service.review_shot(
            "workflow_batch_revision_lock",
            shot_id,
            status=ReviewStatus.CHANGES_REQUESTED,
        )

    prepared = service.submit_pending_shots("workflow_batch_revision_lock")
    assert prepared["submitted"] == 0
    assert prepared["prepared_for_stage_lock"] == shot_ids
    assert prepared["required_stage_locks"] == ["storyboard", "assets"]

    service.lock_production_stage("workflow_batch_revision_lock", "storyboard")
    service.lock_production_stage("workflow_batch_revision_lock", "assets")
    resumed = service.submit_pending_shots("workflow_batch_revision_lock")
    assert resumed["submitted"] == 6


def test_reviewed_comfy_template_stays_stable_for_revisions_and_variants(
    tmp_path,
):
    service = MediaForgeService(tmp_path)
    service.set_provider_status(
        {
            "mode": "comfyui",
            "provider": "comfyui",
            "configured": True,
            "message": "ComfyUI workflow is configured.",
            "capabilities": ["image_generation"],
            "details": {"default_template_id": "comfyui_image:reviewed:v3"},
        }
    )
    service.create_project(brief("workflow_comfy_template"))
    plan = service.generate_plan("workflow_comfy_template")
    shot_id = plan["shots"][0]["shot"]["shot_id"]
    runtime = service.projects["workflow_comfy_template"].shots[shot_id]

    assert runtime.spec.provider_constraints.capability.value == "image_generation"
    assert runtime.spec.workflow.template_id == "comfyui_image:reviewed:v3"

    service.submit_shot("workflow_comfy_template", shot_id)
    service.review_shot(
        "workflow_comfy_template",
        shot_id,
        status=ReviewStatus.CHANGES_REQUESTED,
    )
    revised = service.revise_shot("workflow_comfy_template", shot_id)
    runtime = service.projects["workflow_comfy_template"].shots[shot_id]
    variant = service._variant_spec(runtime, 1)

    assert revised["revision"] == 1
    assert runtime.spec.workflow.template_id == "comfyui_image:reviewed:v3"
    assert runtime.spec.asset_versions["generation_revision"] == "r1"
    assert variant.workflow.template_id == "comfyui_image:reviewed:v3"
    assert variant.asset_versions["generation_revision"] == "r1"
    assert variant.asset_versions["ab_variant"] == "v1"


def test_reviewed_comfy_registry_root_is_allowed_but_still_requires_license(
    tmp_path,
):
    service = MediaForgeService(tmp_path)
    service.set_provider_status(
        {
            "mode": "comfyui",
            "provider": "comfyui",
            "configured": True,
            "message": "ComfyUI workflow is configured.",
            "capabilities": ["image_generation"],
            "details": {
                "default_template_id": "studio_cinematic:reviewed:v3",
                "workflow_registry": {
                    "workflows": [
                        {
                            "registry_template_id": "studio_cinematic:reviewed:v3",
                        }
                    ]
                },
            },
        }
    )
    service.create_project(brief("workflow_custom_comfy_root"))
    service.generate_plan("workflow_custom_comfy_root")

    report = service.project_compliance("workflow_custom_comfy_root")
    checks = {check["name"]: check for check in report["checks"]}

    assert checks["workflow_allowlist"]["passed"] is True
    assert "studio_cinematic" in checks["workflow_allowlist"]["expected"]
    assert checks["license_registry"]["passed"] is False
    assert any(
        {
            "kind": "workflow",
            "identifier": "studio_cinematic",
            "registered": False,
            "reason": "not_registered",
        }.items()
        <= item.items()
        for item in checks["license_registry"]["observed"]
    )
