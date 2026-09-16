from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .comfyui import ComfyUIProvider
from .contracts import (
    Capability,
    GenerationSpec,
    Intent,
    ProviderConstraints,
    QualityRequirements,
    WorkflowSpec,
    ControlNet,
    JobStatus,
)
from .jobs import JobStore
from .media import probe_image, probe_video
from .providers import GenerationProvider
from .providers import LocalCommandProvider
from .replicate import ReplicateVideoProvider
from .router import ProviderRegistration, ProviderRouter


def build_probe_spec(provider_name: str, duration_seconds: int, capability: Capability | None = None) -> GenerationSpec:
    capability = capability or (
        Capability.IMAGE_GENERATION
        if provider_name == "comfyui"
        else Capability.IMAGE_TO_VIDEO
    )
    return GenerationSpec(
        project_id="provider_probe",
        shot_id=f"{provider_name}_probe",
        asset_versions={"character_a": "reference:v1"},
        intent=Intent(
            shot_type="medium_close_up",
            camera_motion="slow_push_in",
            duration_seconds=float(duration_seconds),
            mood="tense",
        ),
        provider_constraints=ProviderConstraints(
            capability=capability,
            resolution="720p",
            max_cost=float(os.getenv("MEDIAFORGE_MAX_COST", "0.40")),
            deadline_seconds=int(os.getenv("MEDIAFORGE_DEADLINE_SECONDS", "180")),
        ),
        workflow=WorkflowSpec(
            template_id="provider_probe:v1",
            allowed_lora_ids=[],
            controlnet=ControlNet(enabled=False, strength=0),
        ),
        quality_requirements=QualityRequirements(
            minimum_character_similarity=0.0,
            must_not_include=["watermark"],
        ),
    )


def load_workflow(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"workflow file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"workflow file is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("workflow file must contain a JSON object")
    return value


def build_provider(provider_name: str, workflow_path: Path | None) -> GenerationProvider:
    if provider_name == "comfyui":
        if workflow_path is None:
            raise ValueError("--workflow is required for --provider comfyui")
        return ComfyUIProvider(
            base_url=os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188"),
            workflow=load_workflow(workflow_path),
        )

    if provider_name == "local":
        command = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_COMMAND", "").strip()
        if not command:
            raise ValueError("MEDIAFORGE_LOCAL_PROVIDER_COMMAND is not configured")
        raw_capabilities = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_CAPABILITIES", "image_generation,image_to_video")
        capabilities = {Capability(value.strip()) for value in raw_capabilities.split(",") if value.strip()}
        return LocalCommandProvider(
            command=command,
            capabilities=capabilities,
            timeout_seconds=float(os.getenv("MEDIAFORGE_LOCAL_PROVIDER_TIMEOUT_SECONDS", "900")),
            estimated_cost=float(os.getenv("MEDIAFORGE_LOCAL_PROVIDER_ESTIMATED_COST", "0")),
            health_command=os.getenv("MEDIAFORGE_LOCAL_PROVIDER_HEALTH_COMMAND", "").strip(),
            warmup_command=os.getenv("MEDIAFORGE_LOCAL_PROVIDER_WARMUP_COMMAND", "").strip(),
        )

    if provider_name == "replicate":
        token = os.getenv("REPLICATE_API_TOKEN")
        version = os.getenv("REPLICATE_MODEL_VERSION")
        if not token:
            raise ValueError("REPLICATE_API_TOKEN is not configured")
        if not version:
            raise ValueError("REPLICATE_MODEL_VERSION is not configured")
        return ReplicateVideoProvider(
            api_token=token,
            version=version,
            base_url=os.getenv(
                "REPLICATE_API_BASE_URL",
                "https://api.replicate.com/v1",
            ),
            http_retry_attempts=int(
                os.getenv("REPLICATE_HTTP_RETRY_ATTEMPTS", "2")
            ),
            http_retry_backoff_seconds=float(
                os.getenv("REPLICATE_HTTP_RETRY_BACKOFF_SECONDS", "0.5")
            ),
        )

    raise ValueError(f"unsupported provider: {provider_name}")
def run_provider_probe(
    provider_name: str,
    output_dir: Path,
    *,
    workflow_path: Path | None = None,
    duration_seconds: int = 3,
    capability: Capability | None = None,
) -> dict[str, Any]:
    provider = build_provider(provider_name, workflow_path)
    if capability is None and provider_name == "local":
        raw = os.getenv("MEDIAFORGE_LOCAL_PROVIDER_CAPABILITIES", "image_generation,image_to_video").split(",")
        capability = Capability(raw[0].strip())
    spec = build_probe_spec(provider_name, duration_seconds, capability)
    router = ProviderRouter([ProviderRegistration(provider=provider, priority=1)])
    decision = router.select(spec)
    store = JobStore()
    job = store.create(spec, f"{spec.project_id}:{spec.shot_id}:v1")
    for status in (
        JobStatus.VALIDATED,
        JobStatus.QUEUED,
        JobStatus.ADMITTED,
        JobStatus.RUNNING,
    ):
        store.transition(job.job_id, status)

    try:
        artifact = provider.generate(
            spec,
            job_id=job.job_id,
            output_dir=output_dir / "artifacts",
        )
        quality = (
            probe_image(Path(artifact.uri)).__dict__
            if artifact.kind == "image"
            else probe_video(Path(artifact.uri)).__dict__
        )
        if not quality["valid"]:
            store.transition(
                job.job_id,
                JobStatus.QUALITY_REJECTED,
                reason=quality.get("error", "quality probe failed"),
            )
            raise RuntimeError(f"quality probe failed: {quality}")
        job.add_artifact(artifact)
        store.transition(job.job_id, JobStatus.SUCCEEDED)
    except Exception as exc:
        if job.status == JobStatus.RUNNING:
            store.transition(job.job_id, JobStatus.FAILED, reason=str(exc))
        raise

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "SUCCEEDED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.name,
        "route": {
            "estimated_cost": decision.estimated_cost,
            "reason": decision.reason,
        },
        "job": {
            "job_id": job.job_id,
            "status": job.status,
            "attempts": job.attempts,
            "events": [
                {
                    "status": event.status,
                    "occurred_at": event.occurred_at.isoformat(),
                    "reason": event.reason,
                }
                for event in job.events
            ],
        },
        "spec": spec.model_dump(mode="json"),
        "artifact": artifact.model_dump(mode="json"),
        "quality": quality,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    manifest["manifest"] = str(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real Provider probe.")
    parser.add_argument(
        "--provider",
        choices=("comfyui", "replicate", "local"),
        required=True,
    )
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--duration", type=int, default=3)
    parser.add_argument("--capability", choices=[item.value for item in Capability])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/provider-probe"),
    )
    args = parser.parse_args()
    try:
        result = run_provider_probe(
            args.provider,
            args.output,
            workflow_path=args.workflow,
            duration_seconds=args.duration,
            capability=Capability(args.capability) if args.capability else None,
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"status={result['status']}")
    print(f"provider={result['provider']}")
    print(f"artifact={result['artifact']['uri']}")
    print(f"manifest={result['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
