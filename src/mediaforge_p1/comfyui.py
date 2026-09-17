from __future__ import annotations

import copy
import json
import mimetypes
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .contracts import Artifact, Capability, GenerationSpec
from .media import sha256_file


class ComfyUIProviderError(RuntimeError):
    """Raised when the ComfyUI HTTP contract cannot be completed."""


@dataclass(frozen=True)
class ComfyWorkflowDefinition:
    """A reviewed ComfyUI graph pinned to a MediaForge template identifier."""

    template_id: str
    workflow: dict[str, Any]
    source_path: str | None = None
    version: str | None = None
    sha256: str | None = None
    model_requirements: tuple[dict[str, str], ...] = ()
    capabilities: tuple[Capability, ...] = (Capability.IMAGE_GENERATION,)

    def provenance_view(self, *, requested_template_id: str) -> dict[str, Any]:
        return {
            "requested_template_id": requested_template_id,
            "registry_template_id": self.template_id,
            "version": self.version,
            "sha256": self.sha256,
            "source_path": self.source_path,
            "model_requirements": [dict(item) for item in self.model_requirements],
            "capabilities": [capability.value for capability in self.capabilities],
        }


@dataclass
class ComfyUIProvider:
    """Adapter for a self-hosted ComfyUI HTTP server."""

    base_url: str
    workflow: dict[str, Any]
    timeout_seconds: float = 60.0
    poll_interval_seconds: float = 0.25
    estimated_cost: float = 0.05
    client_id: str = field(default_factory=lambda: f"mediaforge-{uuid4().hex}")
    workflows: dict[str, ComfyWorkflowDefinition] = field(default_factory=dict)
    default_workflow: ComfyWorkflowDefinition | None = None
    capabilities: set[Capability] = field(
        default_factory=lambda: {Capability.IMAGE_GENERATION}
    )
    name: str = "comfyui"

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def estimate_cost(self, spec: GenerationSpec) -> float:
        return self.estimated_cost

    def health_check(self) -> dict[str, Any]:
        started = perf_counter()
        try:
            payload = self._request_json("GET", "/system_stats")
        except ComfyUIProviderError as exc:
            return {
                "reachable": False,
                "message": str(exc),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "details": {"endpoint": "/system_stats"},
            }
        model_validation = self._validate_model_requirements()
        healthy = not model_validation["missing"] and not model_validation["errors"]
        message = (
            "ComfyUI server and reviewed model requirements are ready."
            if healthy
            else "ComfyUI server is reachable but reviewed model requirements are incomplete."
        )
        return {
            "reachable": True,
            "healthy": healthy,
            "message": message,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "details": {
                "endpoint": "/system_stats",
                "response_keys": sorted(payload.keys()),
                "device_count": len(payload.get("devices", []))
                if isinstance(payload.get("devices"), list)
                else None,
                "model_validation": model_validation,
            },
        }

    def _validate_model_requirements(self) -> dict[str, Any]:
        definitions = list(self.workflows.values())
        if self.default_workflow is not None:
            definitions.append(self.default_workflow)
        requirements = [
            dict(requirement)
            for definition in definitions
            for requirement in definition.model_requirements
        ]
        if not requirements:
            return {
                "configured": False,
                "checked": [],
                "missing": [],
                "errors": [],
                "message": "No reviewed model requirements are declared.",
            }

        available_by_folder: dict[str, set[str]] = {}
        errors: list[dict[str, str]] = []
        for folder in sorted({item["folder"] for item in requirements}):
            try:
                response = self._request_json(
                    "GET",
                    f"/models/{folder}",
                    allow_list=True,
                )
                values = response.get("models", response) if isinstance(response, dict) else response
                if isinstance(values, list):
                    available_by_folder[folder] = {str(item) for item in values}
                elif isinstance(values, dict):
                    available_by_folder[folder] = {str(item) for item in values}
                else:
                    raise ComfyUIProviderError("response is not a model list")
            except ComfyUIProviderError as exc:
                available_by_folder[folder] = set()
                errors.append({"folder": folder, "message": str(exc)})

        checked = [
            {
                "folder": requirement["folder"],
                "name": requirement["name"],
                "available": requirement["name"]
                in available_by_folder.get(requirement["folder"], set()),
            }
            for requirement in requirements
        ]
        return {
            "configured": True,
            "checked": checked,
            "missing": [item for item in checked if not item["available"]],
            "errors": errors,
            "message": "Model availability is checked by folder/name through the ComfyUI API.",
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
            raise ComfyUIProviderError(
                "ComfyUI adapter does not support "
                f"{capability}; configured: {', '.join(sorted(item.value for item in self.capabilities))}"
            )

        definition = self._workflow_for(spec)
        if capability not in definition.capabilities:
            declared = ", ".join(item.value for item in definition.capabilities)
            raise ComfyUIProviderError(
                "reviewed ComfyUI workflow does not declare "
                f"{capability.value}: {definition.template_id} (declared: {declared})"
            )
        uploaded_reference = self._prepare_reference_image(spec, definition.workflow)
        prompt_graph = self._build_prompt_graph(
            spec,
            workflow=definition.workflow,
            reference_image_uri=uploaded_reference["provider_file"]
            if uploaded_reference
            else None,
        )
        response = self._request_json(
            "POST",
            "/prompt",
            body={"prompt": prompt_graph, "client_id": self.client_id},
        )
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise ComfyUIProviderError(
                f"ComfyUI did not return prompt_id: {response!r}"
            )

        history, execution_timeline = self._poll_history(
            str(prompt_id),
            queue_position=response.get("number"),
        )
        file_info = self._find_output_file(history)
        content = self._request_bytes(
            "/view",
            query={
                "filename": str(file_info["filename"]),
                "subfolder": str(file_info.get("subfolder", "")),
                "type": str(file_info.get("type", "output")),
            },
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(str(file_info["filename"])).name
        output_path = output_dir / f"{job_id}_{filename}"
        output_path.write_bytes(content)
        metadata_path = output_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": "mediaforge-artifact-metadata-v1",
                    "provider": self.name,
                    "job_id": job_id,
                    "prompt_id": str(prompt_id),
                    "workflow_template": spec.workflow.template_id,
                    "workflow": definition.provenance_view(
                        requested_template_id=spec.workflow.template_id
                    ),
                    "provider_execution": {
                        "submission": {
                            "prompt_id": str(prompt_id),
                            "queue_position": response.get("number"),
                        },
                        "timeline": execution_timeline,
                        "final_status": (history.get("status") or {}).get("status_str"),
                    },
                    "source_output": file_info,
                    "reference_upload": uploaded_reference,
                    "spec": spec.model_dump(mode="json"),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        artifact_kind = "image"
        if capability == Capability.IMAGE_TO_VIDEO:
            if not mime_type.startswith("video/"):
                output_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                raise ComfyUIProviderError(
                    "ComfyUI image_to_video workflow did not produce a video output: "
                    f"{filename} ({mime_type})"
                )
            artifact_kind = "video"
        return Artifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            job_id=job_id,
            kind=artifact_kind,
            uri=str(output_path),
            mime_type=mime_type,
            sha256=sha256_file(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=None,
            metadata_uri=str(metadata_path),
            created_at=datetime.now(timezone.utc),
        )

    def warmup(self) -> dict[str, Any]:
        """Use the same non-mutating probe as health checks for ComfyUI."""
        result = self.health_check()
        return {
            "ready": bool(result.get("healthy", result.get("reachable"))),
            "message": result.get("message"),
            "latency_ms": result.get("latency_ms"),
            "details": result.get("details") or {},
        }

    def _workflow_for(self, spec: GenerationSpec) -> ComfyWorkflowDefinition:
        if self.workflows:
            definition = self.workflows.get(spec.workflow.template_id)
            if definition is None:
                available = ", ".join(sorted(self.workflows))
                raise ComfyUIProviderError(
                    "no reviewed ComfyUI workflow is registered for "
                    f"{spec.workflow.template_id!r}; available: {available}"
                )
            return definition
        return self.default_workflow or ComfyWorkflowDefinition(
            template_id="legacy-default",
            workflow=self.workflow,
            capabilities=tuple(sorted(self.capabilities, key=lambda item: item.value)),
        )

    def _build_prompt_graph(
        self,
        spec: GenerationSpec,
        *,
        workflow: dict[str, Any],
        reference_image_uri: str | None = None,
    ) -> dict[str, Any]:
        if "prompt" in workflow:
            prompt_graph = copy.deepcopy(workflow["prompt"])
            bindings = workflow.get("bindings", {})
        else:
            prompt_graph = copy.deepcopy(workflow)
            bindings = {}

        for binding_name, binding in bindings.items():
            node_id = str(binding["node_id"])
            input_name = str(binding["input"])
            prompt_graph[node_id]["inputs"][input_name] = self._resolve_binding(
                binding_name,
                binding,
                spec,
                reference_image_uri=reference_image_uri,
            )
        return prompt_graph

    @staticmethod
    def _resolve_binding(
        binding_name: str,
        binding: dict[str, Any],
        spec: GenerationSpec,
        *,
        reference_image_uri: str | None = None,
    ) -> Any:
        if "literal" in binding:
            return binding["literal"]
        source = binding.get("source", binding_name)
        values = {
            "project_id": spec.project_id,
            "shot_id": spec.shot_id,
            "mood": spec.intent.mood,
            "duration_seconds": spec.intent.duration_seconds,
            "template_id": spec.workflow.template_id,
            "reference_image_uri": reference_image_uri
            or next(
                (
                    reference.uri
                    for reference in spec.reference_assets
                    if reference.uri
                ),
                None,
            ),
        }
        if source not in values:
            raise ComfyUIProviderError(f"unsupported workflow binding source: {source}")
        if source == "reference_image_uri" and not values[source]:
            raise ComfyUIProviderError(
                "reference_image_uri binding requires a registered image reference"
            )
        return values[source]

    def _prepare_reference_image(
        self,
        spec: GenerationSpec,
        workflow: dict[str, Any],
    ) -> dict[str, Any] | None:
        reference_uri = next(
            (
                reference.uri
                for reference in spec.reference_assets
                if reference.uri
            ),
            None,
        )
        if not reference_uri or not self._workflow_uses_reference_binding(workflow):
            return None
        if reference_uri.startswith(("http://", "https://", "data:")):
            return {
                "source_uri": reference_uri,
                "provider_file": reference_uri,
                "uploaded": False,
            }

        source_path = Path(reference_uri)
        if not source_path.is_file():
            raise ComfyUIProviderError(
                f"ComfyUI reference image not found: {source_path}"
            )
        uploaded = self._upload_image(source_path)
        provider_file = uploaded["name"]
        subfolder = str(uploaded.get("subfolder") or "").strip("/")
        if subfolder:
            provider_file = f"{subfolder}/{provider_file}"
        return {
            "source_uri": str(source_path),
            "provider_file": provider_file,
            "uploaded": True,
            "endpoint": "/upload/image",
            "subfolder": uploaded.get("subfolder") or "",
            "type": uploaded.get("type") or "input",
        }

    @staticmethod
    def _workflow_uses_reference_binding(workflow: dict[str, Any]) -> bool:
        bindings = workflow.get("bindings", {})
        if not isinstance(bindings, dict):
            return False
        return any(
            binding.get("source", binding_name) == "reference_image_uri"
            for binding_name, binding in bindings.items()
            if isinstance(binding, dict)
        )

    def _upload_image(self, path: Path) -> dict[str, Any]:
        boundary = f"----MediaForge{uuid4().hex}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        image_bytes = path.read_bytes()
        body = b"".join(
            [
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"),
                image_bytes,
                b"\r\n",
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
                    "true\r\n"
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="type"\r\n\r\n'
                    "input\r\n"
                    f"--{boundary}--\r\n"
                ).encode("utf-8"),
            ]
        )
        raw = self._request(
            "POST",
            "/upload/image",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            response = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ComfyUIProviderError(
                "ComfyUI image upload returned invalid JSON"
            ) from exc
        if not isinstance(response, dict) or not str(response.get("name") or ""):
            raise ComfyUIProviderError(
                f"ComfyUI image upload did not return a file name: {response!r}"
            )
        return response

    def _poll_history(
        self,
        prompt_id: str,
        *,
        queue_position: Any = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        deadline = time.monotonic() + self.timeout_seconds
        timeline: list[dict[str, Any]] = []

        def record(state: str, *, status: str | None = None) -> None:
            entry = {
                "state": state,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            if status:
                entry["provider_status"] = status
            if queue_position is not None:
                entry["queue_position"] = queue_position
            if not timeline or any(timeline[-1].get(key) != entry.get(key) for key in ("state", "provider_status", "queue_position")):
                timeline.append(entry)

        record("QUEUED")
        while time.monotonic() < deadline:
            history = self._request_json("GET", f"/history/{prompt_id}")
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if entry is not None:
                status = entry.get("status", {})
                status_name = str(status.get("status_str") or "").strip()
                if status_name == "error":
                    record("FAILED", status=status_name)
                    raise ComfyUIProviderError(
                        f"ComfyUI execution failed: {status!r}"
                    )
                if status.get("completed") or entry.get("outputs"):
                    record("SUCCEEDED", status=status_name or "success")
                    return entry, timeline
                record("RUNNING", status=status_name or None)
            time.sleep(self.poll_interval_seconds)
        record("TIMED_OUT")
        raise ComfyUIProviderError(
            f"timed out waiting for ComfyUI prompt {prompt_id}"
        )

    @staticmethod
    def _find_output_file(history: dict[str, Any]) -> dict[str, Any]:
        outputs = history.get("outputs", {})
        for node_output in outputs.values():
            for key in ("images", "gifs", "videos"):
                files = node_output.get(key, [])
                if files:
                    return files[0]
        raise ComfyUIProviderError(
            f"ComfyUI completed without a downloadable output: {history!r}"
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        allow_list: bool = False,
    ) -> dict[str, Any] | list[Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        raw = self._request(method, path, data=data, headers=headers, query=query)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ComfyUIProviderError(
                f"ComfyUI returned invalid JSON for {method} {path}"
            ) from exc
        if not isinstance(decoded, dict):
            if allow_list and isinstance(decoded, list):
                return decoded
            raise ComfyUIProviderError(
                f"ComfyUI returned a non-object JSON response for {method} {path}"
            )
        return decoded

    def _request_bytes(
        self,
        path: str,
        *,
        query: dict[str, str],
    ) -> bytes:
        return self._request("GET", path, query=query)

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> bytes:
        url = f"{self.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        request = Request(
            url,
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", str(exc))
            raise ComfyUIProviderError(
                f"ComfyUI request failed: {method} {url}: {detail}"
            ) from exc
