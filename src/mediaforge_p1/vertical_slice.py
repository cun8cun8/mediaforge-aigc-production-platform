from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .contracts import (
    Capability,
    CreativeBrief,
    GenerationSpec,
    Intent,
    JobStatus,
    ProviderConstraints,
    QualityRequirements,
    ShotCard,
    WorkflowSpec,
    ControlNet,
)
from .jobs import JobStore
from .media import (
    concat_videos,
    create_video_from_image,
    find_ffmpeg,
    probe_image,
    probe_video,
)
from .providers import GenerationProvider, MockProvider
from .router import ProviderRegistration, ProviderRouter


def build_probe_brief() -> CreativeBrief:
    return CreativeBrief(
        project_id="drama_p1_001",
        title="午夜来电",
        premise="女主在深夜接到一个来自自己未来的电话。",
        genre="都市悬疑",
        style="cinematic blue hour",
        duration_seconds=30,
        budget=2.0,
        characters=["林夏", "周启"],
    )


def build_probe_shots(brief: CreativeBrief) -> list[ShotCard]:
    return [
        ShotCard(
            project_id=brief.project_id,
            shot_id="ep01_sc01_sh01",
            scene="公寓客厅",
            description="林夏在昏暗客厅接起陌生电话。",
            characters=["林夏"],
            duration_seconds=1,
            mood="tense",
        ),
        ShotCard(
            project_id=brief.project_id,
            shot_id="ep01_sc01_sh02",
            scene="公寓客厅",
            description="周启的身影出现在窗外，林夏回头。",
            characters=["林夏", "周启"],
            duration_seconds=1,
            mood="surprised",
        ),
        ShotCard(
            project_id=brief.project_id,
            shot_id="ep01_sc01_sh03",
            scene="公寓客厅",
            description="电话再次响起，林夏看向桌上的旧照片。",
            characters=["林夏"],
            duration_seconds=1,
            mood="fearful",
        ),
    ]


def _preferred_capability(provider: GenerationProvider) -> Capability:
    if provider.supports(Capability.IMAGE_TO_VIDEO):
        return Capability.IMAGE_TO_VIDEO
    if provider.supports(Capability.IMAGE_GENERATION):
        return Capability.IMAGE_GENERATION
    return Capability.IMAGE_TO_VIDEO


def compile_generation_spec(
    shot: ShotCard,
    *,
    capability: Capability,
) -> GenerationSpec:
    return GenerationSpec(
        project_id=shot.project_id,
        shot_id=shot.shot_id,
        asset_versions={
            character: "reference:v1" for character in shot.characters
        },
        intent=Intent(
            shot_type="medium_close_up",
            camera_motion="slow_push_in",
            duration_seconds=float(shot.duration_seconds),
                mood=shot.mood,
            ),
            provider_constraints=ProviderConstraints(
                capability=capability,
                resolution="720p",
                max_cost=0.40,
                deadline_seconds=180,
            ),
        workflow=WorkflowSpec(
            template_id="p1_mock_i2v:v1",
            allowed_lora_ids=["cinematic_style:v1"],
            controlnet=ControlNet(enabled=True, strength=0.65),
        ),
        quality_requirements=QualityRequirements(
            minimum_character_similarity=0.80,
            must_not_include=["watermark", "extra_face"],
        ),
    )


def _run_job(
    store: JobStore,
    provider: GenerationProvider,
    spec: GenerationSpec,
    output_dir: Path,
):
    job = store.create(spec, f"{spec.project_id}:{spec.shot_id}:v1")
    if job.status == JobStatus.CREATED:
        store.transition(job.job_id, JobStatus.VALIDATED)
        store.transition(job.job_id, JobStatus.QUEUED)
        store.transition(job.job_id, JobStatus.ADMITTED)
        store.transition(job.job_id, JobStatus.RUNNING)

    artifact = provider.generate(
        spec,
        job_id=job.job_id,
        output_dir=output_dir / "shots",
    )
    job.add_artifact(artifact)
    quality = (
        probe_image(Path(artifact.uri))
        if artifact.kind == "image"
        else probe_video(Path(artifact.uri))
    )
    if not quality.valid:
        store.transition(
            job.job_id,
            JobStatus.QUALITY_REJECTED,
            reason=quality.error or "media probe failed",
        )
        raise RuntimeError(f"quality gate failed for {spec.shot_id}: {quality.error}")

    store.transition(job.job_id, JobStatus.SUCCEEDED)
    return job, artifact, quality


def _artifact_for_export(artifact: dict[str, object], output_dir: Path) -> Path:
    path = Path(str(artifact["uri"]))
    if artifact.get("kind") != "image":
        return path
    normalized_dir = output_dir / "normalized"
    normalized_path = normalized_dir / f"{artifact['artifact_id']}.mp4"
    if normalized_path.exists():
        return normalized_path
    return create_video_from_image(
        path,
        normalized_path,
        duration_seconds=float(artifact.get("duration_seconds") or 1.0),
    )


def run_vertical_slice(
    output_dir: Path,
    provider: GenerationProvider | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    router = (
        ProviderRouter(
            [ProviderRegistration(provider=MockProvider(), priority=1)]
        )
        if provider is None
        else None
    )
    ffmpeg_executable, ffmpeg_source = find_ffmpeg()
    brief = build_probe_brief()
    shots = build_probe_shots(brief)
    selected_provider = provider or router.registrations[0].provider
    capability = _preferred_capability(selected_provider)
    specs = [
        compile_generation_spec(shot, capability=capability)
        for shot in shots
    ]
    store = JobStore()
    completed = []

    for spec in specs:
        if router is not None:
            decision = router.select(spec)
            selected_provider = decision.provider
            route = {
                "provider": selected_provider.name,
                "estimated_cost": decision.estimated_cost,
                "reason": decision.reason,
            }
        else:
            selected_provider = provider
            route = {
                "provider": selected_provider.name,
                "estimated_cost": selected_provider.estimate_cost(spec),
                "reason": "provider explicitly supplied by caller",
            }

        job, artifact, quality = _run_job(
            store,
            selected_provider,
            spec,
            output_dir,
        )
        completed.append(
            {
                "job_id": job.job_id,
                "shot_id": spec.shot_id,
                "status": job.status,
                "attempts": job.attempts,
                "route": route,
                "artifact": artifact.model_dump(mode="json"),
                "quality": {
                    "valid": quality.valid,
                    "duration_seconds": quality.duration_seconds,
                    "width": quality.width,
                    "height": quality.height,
                    "format": quality.format,
                },
                "events": [
                    {
                        "status": event.status,
                        "occurred_at": event.occurred_at.isoformat(),
                        "reason": event.reason,
                    }
                    for event in job.events
                ],
            }
        )

    final_mp4 = output_dir / "final_sample.mp4"
    concat_videos(
        [
            _artifact_for_export(item["artifact"], output_dir)
            for item in completed
        ],
        final_mp4,
    )
    final_probe = probe_video(final_mp4)
    if not final_probe.valid:
        raise RuntimeError(f"final sample quality gate failed: {final_probe.error}")

    manifest = {
        "status": "SUCCEEDED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "brief": brief.model_dump(mode="json"),
        "shots": [shot.model_dump(mode="json") for shot in shots],
        "jobs": completed,
        "final_mp4": str(final_mp4),
        "final_probe": {
            "valid": final_probe.valid,
            "duration_seconds": final_probe.duration_seconds,
            "width": final_probe.width,
            "height": final_probe.height,
        },
        "ffmpeg": {
            "executable": ffmpeg_executable,
            "source": ffmpeg_source,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    manifest["manifest"] = str(manifest_path)
    return manifest
