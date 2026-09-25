# Open-Source Adoption Record

MediaForge is a governed media-production control plane, not a replacement for
every specialist tool. This record keeps platform choices explicit so future
work adopts mature components at their boundary instead of rebuilding them
inside the project state machine.

## Current Decisions

| Community project | Role in MediaForge | Decision | Boundary |
| --- | --- | --- | --- |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | Private image/video inference workflows | Integrated | Reviewed, versioned and SHA-256-pinned workflow registry through the Provider adapter |
| [RAGFlow](https://github.com/infiniflow/ragflow) | Complex document retrieval | Integrated | Explicit tenant/project dataset mapping; read-only retrieval by default; approved story snapshot upload is opt-in |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM tracing, prompt/model experiment analysis | Integrated as optional observability | Langfuse v4 OpenTelemetry SDK; default export is redacted and cannot interrupt media generation |
| [OpenTelemetry](https://github.com/open-telemetry/opentelemetry-python) | Portable tracing standard | Adopted through Langfuse integration | Preserve MediaForge audit/event chain as the governance record |
| [c2pa-rs](https://github.com/contentauth/c2pa-rs) / C2PA Tool | Content credentials | Compatible external signer/verifier boundary | Isolated signing service and independent verification; do not invent a proprietary credential format |
| [Temporal](https://github.com/temporalio/temporal) | Long-running distributed orchestration | Integrated as an optional orchestration boundary | API starts durable generation, rendering, packaging or dispatch workflows; Activities call the existing control plane, while native leases remain the fallback |
| [Yjs](https://github.com/yjs/yjs) | Browser text/timeline collaboration | Deferred, deliberate migration candidate | Replace only editor document synchronization; preserve MediaForge authorization, locks, approvals and audit events |
| [DiffSynth-Studio](https://github.com/modelscope/DiffSynth-Studio) | Low-VRAM image/video/audio model execution | Integrated as an optional GPU Provider boundary | Reviewed launcher receives a versioned JSON request; MediaForge retains routing, quality, cost and governance receipts |
| [Diffusers](https://github.com/huggingface/diffusers) | Local model execution | Provider implementation option | Expose it only through the `GenerationProvider` protocol with fixed model/version receipts |

## Why The Control Plane Remains Native

The project-specific state machine owns business facts that generic workflow or
agent platforms do not: project tenancy, review gates, asset rights, provider
budget reservations, audit chaining, C2PA release checks and delivery evidence.
These records stay local and durable even when an external tool is unavailable.

External systems receive a normalized, least-privilege view:

1. Providers receive a validated generation specification and return a
   normalized artifact receipt.
2. RAGFlow receives only explicitly mapped retrieval requests, and only
   approved snapshots when write synchronization is authorized.
3. Langfuse receives identifiers, version fingerprints, cost, duration,
   artifact hashes and evaluation outcomes by default. Prompt/source/media
   content requires an explicit export approval.
4. The optional Temporal Worker executes Activities through the existing control
   plane API. MediaForge remains the business fact source; the Worker never
   writes project state directly.
5. A future Yjs provider synchronizes editable text only; server-side commits
   still create MediaForge collaboration and audit events.

## Adoption Gate

Before adopting another open-source component:

1. Confirm its license, security maintenance and deployment model against the
   intended commercial use.
2. Define an adapter interface, persisted receipt and failure semantics before
   changing any UI or workflow state.
3. Add integration tests with a local fake/server fixture and a real staging
   smoke test where data leaves the MediaForge boundary.
4. Keep a feature flag and a safe local fallback until production acceptance,
   recovery drill and rollback procedure are documented.

See [Architecture](architecture.md), [Configuration](configuration.md) and the
[Release Checklist](release-checklist.md) for the operational controls.
