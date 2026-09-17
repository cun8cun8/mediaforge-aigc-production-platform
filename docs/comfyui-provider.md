# ComfyUI Provider Adapter

`src/mediaforge_p1/comfyui.py` implements the first real execution adapter
against a self-hosted ComfyUI HTTP server.

The API can load this adapter with:

```powershell
$env:MEDIAFORGE_PROVIDER = "comfyui"
$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
$env:COMFYUI_WORKFLOW_PATH = "D:\secure-config\reviewed-comfyui-image-workflow.json"
uvicorn mediaforge_p1.api:app --host 127.0.0.1 --port 8020
```

Verify configuration before submitting work:

```powershell
Invoke-RestMethod http://127.0.0.1:8020/providers/status
Invoke-RestMethod http://127.0.0.1:8020/providers/health
```

The status response includes the workflow path, content SHA-256, optional
version, whether the graph was loaded, the timeout, polling interval, and
estimated cost. A missing, invalid, or hash-drifted workflow leaves the API
available but marks the Provider as unconfigured with an actionable message.
The health response probes `/system_stats` after the workflow is configured.
When a reviewed model manifest is present, it also checks `/models/{folder}`
for every declared model name and reports `reachable=true, healthy=false` when
the ComfyUI server is available but its approved model inventory is incomplete.

## Reviewed workflow registry

One static workflow remains supported through `COMFYUI_WORKFLOW_PATH`. For a
production template pool, set `COMFYUI_WORKFLOW_REGISTRY_PATH` to a registry:

```json
{
  "schema_version": "mediaforge-comfyui-workflow-registry-v1",
  "workflows": [
    {
      "template_id": "template:cinematic:v2",
      "path": "cinematic-v2.json",
      "version": "2026.09.15",
      "sha256": "a-64-character-lowercase-sha256-digest",
      "capabilities": ["image_generation"],
      "model_requirements": [
        {"folder": "checkpoints", "name": "cinematic-v2.safetensors"},
        {"folder": "vae", "name": "cinematic-vae.safetensors"}
      ]
    }
  ]
}
```

Relative workflow paths resolve from the registry directory. Each `template_id`
must be unique. With `COMFYUI_REQUIRE_WORKFLOW_PIN=true`, a non-empty version
and matching SHA-256 are mandatory. Model availability is a folder/name check
against ComfyUI, not a cryptographic verification of weight bytes; keep the
model inventory and image digest in deployment configuration as well. Workflow
files must resolve inside the registry directory; parent-directory traversal,
absolute paths outside that directory and symlink escapes are rejected.

`capabilities` is optional for backward compatibility and defaults to
`["image_generation"]`. A reviewed `image_to_video` graph must declare
`["image_to_video"]` and produce a downloadable `video/*` file, such as MP4
or WebM. A still image, GIF, or unknown file is rejected instead of being
mislabelled as a video artifact.

For platform-created image jobs, the registry must resolve one reviewed default
template. A single-entry registry is selected automatically. For more than one
entry, set `MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID` to an exact registry
`template_id`; startup rejects an omitted or unknown value instead of waiting
for a billable job to fail at the Provider. Shot revisions and A/B candidates
retain that approved graph identity and record `generation_revision` or
`ab_variant` in the immutable generation spec.

Image and video defaults are selected independently. If a registry contains
multiple `image_to_video` graphs, set
`MEDIAFORGE_VIDEO_WORKFLOW_TEMPLATE_ID` to its exact reviewed `template_id`.
A registry with one image graph and one video graph needs neither selector.

Registry membership is an execution approval, not a rights grant. A configured
reviewed template root is allowed through the workflow gate, but a release
still requires a matching `workflow` entry in the MediaForge license registry.
For example, the sample `template:cinematic:v2` requires an approved
`{"kind":"workflow","identifier":"template",...}` record before release.
Use the built-in `comfyui_image:*` namespace when that standard governance
record is appropriate, or import a reviewed license-registry record for a
custom namespace.

Validate a registry locally before any network call:

```powershell
mediaforge-comfyui-preflight --registry D:\secure-config\comfyui-workflow-registry.json
```

The preflight validates the JSON graph structure, unique template names,
workflow SHA-256 pins, versions and declared model requirement format. It does
not contact ComfyUI or submit a prompt. Use `--allow-unpinned` only for a local
development experiment.

## Workflow template

The adapter accepts an API-format workflow directly, or a wrapper with
bindings:

```json
{
  "prompt": {
    "1": {
      "class_type": "SomeNode",
      "inputs": {
        "text": "placeholder"
      }
    }
  },
  "bindings": {
    "shot_id": {
      "node_id": "1",
      "input": "text",
      "source": "shot_id"
    }
  }
}
```

Supported binding sources in this spike:

- `project_id`
- `shot_id`
- `mood`
- `duration_seconds`
- `template_id`
- `reference_image_uri`（已登记参考图的第一个本地 URI）

使用 `reference_image_uri` 时，镜头必须已经登记可读取的参考图，否则提交会
被适配器拒绝。对于本地参考图，适配器会先调用 ComfyUI 的
`POST /upload/image`，再把返回的文件名注入绑定节点；远程 HTTP(S) 或 Data URL
会原样传递。参考图由 MediaForge 负责保存、哈希和交付归档，工作流只声明
输入节点绑定。

## Execution flow

1. Copy the workflow graph.
2. Upload the first local reference image when the workflow declares a
   `reference_image_uri` binding.
3. Apply only declared bindings.
4. Submit the graph to `/prompt`.
5. Poll `/history/{prompt_id}` until completion or error.
6. Select the first image, GIF, or video output.
7. Download it from `/view`.
8. Store a local artifact with a SHA-256 hash and a
   `mediaforge-artifact-metadata-v1` sidecar containing the ComfyUI prompt ID,
   selected output, reference upload receipt, selected workflow version/hash,
   upstream queue/execution timeline, and generation spec.

The adapter deliberately does not expose arbitrary node mutation to the Agent.
Bindings are part of a reviewed workflow template and should be versioned with
the model and custom-node inventory.

The artifact `metadata_uri` is included in the asset inventory, trace,
Provenance report, and delivery ZIP. It provides the local execution receipt
without exposing credentials.

The adapter exposes the capabilities declared by the reviewed registry. Image
artifacts can be normalized into the final MP4 export; an `image_to_video`
workflow returns a video artifact and proceeds through the shared video quality
gate. An unreviewed graph cannot enable either capability.

## Approved cost-bearing probe

After the non-generating preflight and health diagnostics pass, an operator can
submit one explicitly approved test through the same registry used by staging:

```powershell
mediaforge-provider-probe `
  --provider comfyui `
  --registry D:\secure-config\comfyui-workflow-registry.json `
  --template-id comfyui_image:reviewed:v1 `
  --require-workflow-pin `
  --output artifacts/provider-probe
```

This command sends `POST /prompt` and can consume GPU capacity. Its manifest
records the selected workflow version, SHA-256, output artifact and quality
gate result. Do not run it from CI or before a cost owner has approved the
workflow and model inventory.

For a reviewed video graph, record the tested capability explicitly:

```powershell
mediaforge-provider-probe `
  --provider comfyui `
  --registry D:\secure-config\comfyui-workflow-registry.json `
  --template-id comfyui_video:reviewed:v1 `
  --capability image_to_video `
  --require-workflow-pin `
  --output artifacts/provider-probe-video
```
