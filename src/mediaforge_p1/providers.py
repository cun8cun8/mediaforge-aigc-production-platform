from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from time import perf_counter
from uuid import uuid4

from .contracts import Artifact, Capability, GenerationSpec
from .media import (
    create_preview_image,
    create_preview_video,
    probe_image,
    probe_video,
    sha256_file,
)


class GenerationProvider(Protocol):
    name: str

    def supports(self, capability: Capability) -> bool:
        ...

    def estimate_cost(self, spec: GenerationSpec) -> float:
        ...

    def generate(
        self,
        spec: GenerationSpec,
        *,
        job_id: str,
        output_dir: Path,
    ) -> Artifact:
        ...


def _command_args(command: str) -> list[str]:
    try:
        args = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ValueError(f"invalid local Provider command: {exc}") from exc
    normalized = [item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in {'"', "'"} else item for item in args]
    if not normalized:
        raise ValueError("local Provider command is empty")
    return normalized


class LocalCommandProvider:
    """Run a reviewed local GPU pipeline through a narrow JSON/stdout contract."""

    name = "local-gpu-command"

    def __init__(self, *, command: str, capabilities: set[Capability], timeout_seconds: float, estimated_cost: float = 0.0, health_command: str = "", warmup_command: str = "", request_schema: str = "mediaforge-local-provider-request-v1", execution_name: str = "local-gpu-command") -> None:
        _command_args(command)
        self.command = command
        self._capabilities = capabilities
        self.timeout_seconds = timeout_seconds
        self.estimated_cost = estimated_cost
        self.health_command = health_command
        self.warmup_command = warmup_command
        self.request_schema = request_schema
        self.execution_name = execution_name

    def supports(self, capability: Capability) -> bool:
        return capability in self._capabilities

    def estimate_cost(self, spec: GenerationSpec) -> float:
        return self.estimated_cost

    def _run_probe(self, command: str, *, action: str) -> dict[str, object]:
        started = perf_counter()
        try:
            result = subprocess.run(_command_args(command), text=True, capture_output=True, timeout=self.timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"reachable": False, "message": f"local Provider {action} failed: {exc}", "latency_ms": round((perf_counter() - started) * 1000, 2)}
        if result.returncode != 0:
            return {"reachable": False, "message": (result.stderr or result.stdout or f"local Provider {action} returned {result.returncode}").strip()[-2000:], "latency_ms": round((perf_counter() - started) * 1000, 2)}
        return {"reachable": True, "message": f"local Provider {action} completed.", "latency_ms": round((perf_counter() - started) * 1000, 2), "details": {"stdout": result.stdout.strip()[-1000:]}}

    def health_check(self) -> dict[str, object]:
        if not self.health_command:
            return {"reachable": True, "message": "Local Provider command is configured; active health command is not set.", "latency_ms": 0.0, "details": {"execution": "local-gpu-command", "probe": "configuration-only"}}
        return self._run_probe(self.health_command, action="health check")

    def warmup(self) -> dict[str, object]:
        if not self.warmup_command:
            return {"ready": True, "message": "Local Provider is configured; no warmup command is set.", "details": {"warmup": "configuration-only"}}
        result = self._run_probe(self.warmup_command, action="warmup")
        return {"ready": bool(result.get("reachable")), "message": result.get("message"), "latency_ms": result.get("latency_ms"), "details": result.get("details", {})}

    def generate(self, spec: GenerationSpec, *, job_id: str, output_dir: Path) -> Artifact:
        capability = spec.provider_constraints.capability
        if not self.supports(capability):
            raise ValueError(f"unsupported capability: {capability}")
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = f"artifact_{uuid4().hex[:12]}"
        suffix = ".png" if capability == Capability.IMAGE_GENERATION else ".mp4"
        path = output_dir / f"{artifact_id}{suffix}"
        payload = {"schema_version": self.request_schema, "job_id": job_id, "output_path": str(path), "spec": spec.model_dump(mode="json")}
        env = os.environ.copy()
        env.update({"MEDIAFORGE_OUTPUT_PATH": str(path), "MEDIAFORGE_JOB_ID": job_id, "MEDIAFORGE_REQUEST_CAPABILITY": capability.value})
        try:
            result = subprocess.run(_command_args(self.command), input=json.dumps(payload, ensure_ascii=True), text=True, capture_output=True, timeout=self.timeout_seconds, env=env, cwd=str(output_dir), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"local Provider execution failed: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "local Provider returned a non-zero exit code").strip()[-2000:])
        if not path.is_file():
            raise RuntimeError("local Provider completed without creating MEDIAFORGE_OUTPUT_PATH")
        probe = probe_image(path) if capability == Capability.IMAGE_GENERATION else probe_video(path)
        if not probe.valid:
            raise RuntimeError(f"local Provider output is not readable media: {probe.error or 'invalid media'}")
        metadata_path = output_dir / f"{artifact_id}.json"
        metadata_path.write_text(json.dumps({"schema_version": "mediaforge-artifact-metadata-v1", "provider": self.name, "job_id": job_id, "spec": spec.model_dump(mode="json"), "execution": self.execution_name}, ensure_ascii=True, indent=2), encoding="utf-8")
        return Artifact(artifact_id=artifact_id, job_id=job_id, kind="image" if capability == Capability.IMAGE_GENERATION else "video", uri=str(path), mime_type="image/png" if capability == Capability.IMAGE_GENERATION else "video/mp4", sha256=sha256_file(path), size_bytes=path.stat().st_size, duration_seconds=probe.duration_seconds, metadata_uri=str(metadata_path), created_at=datetime.now(timezone.utc))


class DiffSynthProvider(LocalCommandProvider):
    """Run a reviewed DiffSynth-Studio launcher as an isolated GPU Provider.

    DiffSynth changes model entry points frequently, so MediaForge owns only a
    stable JSON/stdout boundary. The launcher can use DiffSynth directly,
    ComfyUI nodes, or a site-specific wrapper without coupling the control
    plane to model internals.
    """

    name = "diffsynth"

    def __init__(self, **kwargs) -> None:
        super().__init__(
            request_schema="mediaforge-diffsynth-provider-request-v1",
            execution_name="diffsynth-studio-command",
            **kwargs,
        )

class MockProvider:
    """Deterministic provider used to validate platform contracts."""

    name = "mock-provider"

    def supports(self, capability: Capability) -> bool:
        return capability in {
            Capability.IMAGE_GENERATION,
            Capability.IMAGE_TO_VIDEO,
        }

    def estimate_cost(self, spec: GenerationSpec) -> float:
        return 0.01 if spec.provider_constraints.capability == Capability.IMAGE_GENERATION else 0.02

    def health_check(self) -> dict:
        return {
            "reachable": True,
            "message": "Mock Provider is ready.",
            "latency_ms": 0.0,
            "details": {"execution": "local-deterministic"},
        }

    def generate(
        self,
        spec: GenerationSpec,
        *,
        job_id: str,
        output_dir: Path,
    ) -> Artifact:
        capability = spec.provider_constraints.capability
        if not self.supports(capability):
            raise ValueError(f"unsupported capability: {capability}")

        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = f"artifact_{uuid4().hex[:12]}"
        if capability == Capability.IMAGE_GENERATION:
            path = output_dir / f"{artifact_id}.png"
            create_preview_image(
                path,
                color=self._color_for(spec.shot_id),
                title=f"{spec.project_id} / {spec.shot_id}",
                subtitle=(
                    f"{spec.intent.shot_type} / {spec.intent.camera_motion} / "
                    f"{spec.intent.mood}"
                ),
            )
            kind = "image"
            mime_type = "image/png"
            duration = None
        else:
            path = output_dir / f"{artifact_id}.mp4"
            color = self._color_for(spec.shot_id)
            create_preview_video(
                path,
                duration_seconds=spec.intent.duration_seconds,
                color=color,
                title=f"{spec.project_id} / {spec.shot_id}",
                subtitle=(
                    f"{spec.intent.shot_type} / {spec.intent.camera_motion} / "
                    f"{spec.intent.mood}"
                ),
            )
            kind = "video"
            mime_type = "video/mp4"
            duration = spec.intent.duration_seconds

        metadata_path = output_dir / f"{artifact_id}.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": "mediaforge-artifact-metadata-v1",
                    "provider": self.name,
                    "job_id": job_id,
                    "spec": spec.model_dump(mode="json"),
                    "simulation": {
                        "mode": "mock",
                        "visible_preview": True,
                        "message": "Workflow validation only; not real model output.",
                    },
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return Artifact(
            artifact_id=artifact_id,
            job_id=job_id,
            kind=kind,
            uri=str(path),
            mime_type=mime_type,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            duration_seconds=duration,
            metadata_uri=str(metadata_path),
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _color_for(shot_id: str) -> str:
        colors = ["#1d3557", "#457b9d", "#e76f51", "#2a9d8f", "#6d597a"]
        return colors[sum(ord(char) for char in shot_id) % len(colors)]
