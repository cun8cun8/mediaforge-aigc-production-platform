# MediaForge Runtime Reference

> 本文保留实现级运行时说明、完整接口索引和适配器边界。首次使用请从仓库根目录的 [README](../README.md) 开始；部署和配置请分别查阅[生产部署](production-deployment.md)和[配置参考](configuration.md)。

This directory contains the first executable vertical slice for MediaForge.
It validates the narrowest path from a structured shot specification to
generated media, a persisted job state, a quality probe, and a final MP4.

## Setup

```powershell
python -m pip install -e ".[dev]"
```

## Run

```powershell
python -m mediaforge_p1
```

The command writes a reproducible probe run to
`artifacts/p1-probe/manifest.json`.

To probe a configured real Provider:

```powershell
python -m mediaforge_p1.provider_probe --provider comfyui --workflow D:\secure-config\reviewed-comfyui-image-workflow.json
python -m mediaforge_p1.provider_probe --provider replicate
```

The ComfyUI command requires a valid API-format workflow. The Replicate
command reads `REPLICATE_API_TOKEN` and `REPLICATE_MODEL_VERSION` from the
environment and never accepts secrets as command-line arguments.

Run the non-destructive deployment acceptance report after configuring an API
instance. It checks API, Provider diagnostics, OCR, planning, enterprise
runtime and readiness without submitting a paid media job. `--require-production`
returns a non-zero status unless real Provider and enterprise requirements are met.
Add `--release-project <project-id>` for a content delivery decision: it requires
at least one `SIGNED_VERIFIED` content credential on every supplied project.

```powershell
mediaforge-production-acceptance --base-url http://127.0.0.1:8020 --token $env:MEDIAFORGE_READINESS_TOKEN --probe-enterprise --probe-planning --require-production
```

The report is written to `artifacts/production-acceptance.json`. Execute the
separate `mediaforge-provider-probe` command only after approving real Provider
costs.

Browser acceptance uses a locked Node development dependency:

```powershell
npm install
npm run install:browsers
$env:MEDIAFORGE_UI_URL='http://127.0.0.1:8020'
npm run test:ui
npm run test:ui:narrative
```

When a managed Chromium download is unavailable on a developer workstation,
set `PLAYWRIGHT_CHANNEL=chrome` to use the installed Google Chrome channel.

Optional resumable story/storyboard drafts, human review, and recovery use the
`agents` extra with `MEDIAFORGE_PLANNING_MODE=langgraph`. See
[staged planning](staged-planning.md) for setup and deployment boundaries.

For a self-hosted Prometheus, Alertmanager, and Grafana operations stack, layer
`docker-compose.observability.yml` on the normal or HA Compose file. It protects
the scrape endpoint with a Docker secret and provisions the MediaForge dashboard;
the deployment and notification boundary are documented in
[production deployment](production-deployment.md#monitoring-and-alert-routing).

For active/passive production control-plane replicas backed by PostgreSQL, Redis
and shared artifacts, use `docker-compose.ha.yml`; the lease and gateway model
are documented in [production deployment](production-deployment.md).

For GPU-local Provider execution, run the `gpu-worker` Compose profile. The Worker
generates into the shared artifact root and completes Jobs through signed callbacks;
it claims only Jobs routed to its configured Provider. Kubernetes API/HA/GPU Worker
manifests and required secret/provider configuration are in [k8s](../k8s/README.md).

Prompt versions, human review annotations, experiments, media derivatives, edit
timelines, Provider contract checks, collaboration presence/soft locks, durable
CRDT-backed shared production notes, and content credential claims are persisted with
a project and included in delivery governance files. Each credential includes an
application audit claim and a C2PA Tool manifest-definition JSON. Content credential claims are
`UNSIGNED` unless `MEDIAFORGE_C2PA_SIGNER_COMMAND` is configured and its signer
creates the declared output. A signed credential remains `SIGNED_UNVERIFIED` until
`MEDIAFORGE_C2PA_VERIFIER_COMMAND` independently validates it; see
[production deployment](production-deployment.md).

项目工作台还提供从“需求与剧本”到“交付”的六阶段编排。每个阶段根据真实产物计算门禁，
锁定会保存对应证据指纹；剧本、分镜、参考资产、视频或后期输入变更会使受影响阶段及其下游
锁定失效，并将返工范围写入审计日志。默认是建议门禁，便于渐进迁移；生产环境可设置
`MEDIAFORGE_REQUIRE_STAGE_LOCKS=true`，要求在提交镜头、导出成片、构建交付包和发布前完成
对应阶段锁定。配置 `MEDIAFORGE_REQUIRE_RELEASE_CONTENT_CREDENTIALS=true` 后，发布 API
还必须确认当前 `final_mp4` 本身具有已签名并由独立验证器验证的 C2PA 内容凭证；参考图或
历史成片的凭证不能替代该门禁。

许可证台账可按资产 SHA-256 保存合同证据与有效期，并在
`MEDIAFORGE_REQUIRE_ASSET_RIGHTS_RECORD=true` 时把到期或缺失的资产权利记录变成发布
阻断项。交付评估还支持固化服务商、工作流和提示词版本的模型准入基线；任一上下文或质量
门禁漂移后，发布会被阻断直到重新准入。

Offline PostgreSQL plus artifact backup bundles can be created only during a
quiesced window with `sh ops/backup/create-postgres-backup.sh`; validate one with
`mediaforge-backup verify --bundle <path>` and enforce an RPO with
`mediaforge-backup report --bundle <path> --max-age-hours 24`. The isolated restore
drill and read-only k6 capacity baseline are in
[production deployment](production-deployment.md#备份与恢复演练).

## Run the P0 API

```powershell
uvicorn mediaforge_p1.api:app --host 127.0.0.1 --port 8020 --reload
```

The API exposes the complete first-loop flow:

```text
GET  /studio/overview?include_archived=
GET  /studio/metrics?include_archived=
GET  /studio/metrics/export?format=json|csv&include_archived=
GET  /ops/alerts
GET  /providers/callback-security
GET  /llm/status
GET  /planning/status
GET  /webhooks/status
GET  /auth/status
GET  /auth/login
GET  /auth/callback
POST /auth/logout
GET  /auth/me
GET  /tenants/me/quota
GET  /tenants/me/cost?include_archived=
GET  /tenants/me/cost/export?format=json|csv&include_archived=
GET  /tenants/quota/status
GET  /metrics
GET  /metrics/runtime
GET  /observability/langfuse/status
GET  /delivery/status
GET  /quality/status
GET  /speech/status
GET  /ops/readiness
GET  /enterprise/status
POST /enterprise/probe
GET  /billing/summary
GET  /billing/events
POST /billing/events
GET  /billing/settlements
GET  /billing/settlements/summary
POST /billing/settlements
GET  /billing/settlements/callback/status
POST /billing/settlements/callback
GET  /billing/settlements/stripe/status
POST /billing/settlements/stripe
GET  /workers
POST /workers/register
POST /workers/{worker_id}/heartbeat
POST /workers/{worker_id}/claim
POST /governance/license-registry/sync
GET  /governance/license-registry/sync/status
GET  /governance/license-registry
POST /governance/license-registry/validate
POST /governance/license-registry/import
GET  /governance/license-registry/export
POST /projects/{id}/jobs/{job_id}/callback
POST /projects
POST /projects/import
POST /projects/import-package
POST /projects/import-archive
GET  /projects?include_archived=
GET  /projects/{id}/collaboration
GET  /projects/{id}/collaboration/documents
GET  /projects/{id}/collaboration/events?after=
POST /projects/{id}/collaboration/documents/{document_id}/operations
PUT  /projects/{id}/collaboration/documents/{document_id}
WS   /projects/{id}/collaboration/events/ws?after=
GET  /projects/{id}/memory?query=
GET  /projects/{id}/memory/sources
POST /projects/{id}/memory/ragflow-sync
POST /enterprise/memory/probe
PATCH /projects/{id}
GET  /projects/{id}/events
GET  /projects/{id}/narrative-events
POST /projects/{id}/narrative-events
PATCH /projects/{id}/narrative-events/{event_id}
POST /projects/{id}/narrative-events/{event_id}/review
GET  /projects/{id}/adaptation-scenes
POST /projects/{id}/adaptation-scenes
POST /projects/{id}/adaptation-scenes/derive
PATCH /projects/{id}/adaptation-scenes/{scene_id}
POST /projects/{id}/adaptation-scenes/{scene_id}/review
GET  /projects/{id}/source-chapters
POST /projects/{id}/source-chapters
PATCH /projects/{id}/source-chapters/{chapter_id}
POST /projects/{id}/source-documents/import
GET  /projects/{id}/source-documents/{document_id}/download
GET  /source-ingest/status
POST /projects/{id}/source-chapters/{chapter_id}/event-candidates
POST /projects/{id}/narrative-candidates/{candidate_id}/adopt
POST /projects/{id}/narrative-candidates/{candidate_id}/discard
POST /projects/{id}/collaboration/members
DELETE /projects/{id}/collaboration/members/{subject}
GET  /projects/{id}/comments
POST /projects/{id}/comments
DELETE /projects/{id}/comments/{comment_id}
POST /projects/{id}/clone
POST /projects/{id}/snapshot/export
POST /projects/{id}/plan
GET  /projects/{id}/planning
POST /projects/{id}/planning
GET  /projects/{id}/planning/{run_id}
POST /projects/{id}/planning/{run_id}/resume
POST /projects/{id}/planning/{run_id}/migrate
POST /projects/{id}/planning/{run_id}/review
POST /projects/{id}/planning/{run_id}/cancel
POST /projects/{id}/archive
POST /projects/{id}/restore
GET  /projects/{id}/operations
GET  /projects/{id}/reports/production
POST /projects/{id}/reports/export
GET  /projects/{id}/trace
POST /projects/{id}/trace/export
GET  /projects/{id}/retrospective
POST /projects/{id}/retrospective/export
GET  /projects/{id}/provenance
POST /projects/{id}/provenance/export
GET  /projects/{id}/compliance
POST /projects/{id}/compliance/export
GET  /projects/{id}/continuity
POST /projects/{id}/continuity/export
GET  /projects/{id}/distribution
POST /projects/{id}/distribution/export
GET  /projects/{id}/delivery-feedback?delivery_id=
POST /projects/{id}/delivery-feedback
PATCH /projects/{id}/delivery-feedback/{feedback_id}
POST /projects/{id}/deliveries/{delivery_id}/acknowledge
GET  /projects/{id}/acceptance
POST /projects/{id}/acceptance/export
POST /projects/{id}/closeout
POST /projects/{id}/archive-package
POST /projects/{id}/archive-package/verify
GET  /projects/{id}/routes
GET  /projects/{id}/cost
GET  /projects/{id}/policy
GET  /projects/{id}/assets
GET  /projects/{id}/assets/{asset_id}/download
GET  /projects/{id}/references
POST /projects/{id}/audio
DELETE /projects/{id}/audio
POST /projects/{id}/voiceover
POST /projects/{id}/dialogue
GET  /projects/{id}/training-dataset
POST /projects/{id}/training-dataset/export
POST /projects/{id}/lipsync
GET  /lipsync/status
POST /providers/warmup
POST /projects/{id}/references
GET  /projects/{id}/shots?review_status=&query=&limit=
PATCH /projects/{id}/shots/{shot_id}
GET  /projects/{id}/jobs?status=&shot_id=&limit=
GET  /projects/{id}/audit
POST /projects/{id}/audit/export
POST /projects/{id}/audit/export-csv
GET  /projects/{id}/evaluations/latest
POST /projects/{id}/evaluations/run
GET  /projects/{id}/evaluations/baselines
POST /projects/{id}/evaluations/baselines
GET  /projects/{id}/providers/benchmark
POST /projects/{id}/providers/benchmark
POST /projects/{id}/shots/submit-all
POST /projects/{id}/shots/enqueue-all
POST /projects/{id}/shots/{shot_id}/submit
POST /projects/{id}/shots/{shot_id}/enqueue
POST /projects/{id}/shots/{shot_id}/retry
GET  /projects/{id}/shots/{shot_id}/route
POST /projects/{id}/shots/{shot_id}/route/export
GET  /projects/{id}/shots/{shot_id}/variants/comparison
POST /projects/{id}/shots/{shot_id}/variants/compare
POST /projects/{id}/shots/{shot_id}/variants/export
POST /projects/{id}/shots/{shot_id}/variants/{variant_id}/promote
POST /projects/{id}/jobs/{job_id}/process
POST /projects/{id}/jobs/recover-stale
POST /projects/{id}/queue/drain
POST /queue/drain-all
POST /jobs/recover-stale
POST /projects/{id}/jobs/{job_id}/cancel
POST /projects/{id}/shots/approve-ready
POST /projects/{id}/shots/{shot_id}/review
POST /projects/{id}/shots/{shot_id}/review-quality
POST /projects/{id}/shots/{shot_id}/revise
POST /projects/{id}/export
POST /projects/{id}/package
POST /projects/{id}/package/verify
POST /projects/{id}/release
POST /projects/{id}/deliveries
POST /projects/{id}/deliveries/dispatch
GET /providers/status
GET /providers/health
GET /providers/diagnostics
GET /governance/license-registry
POST /projects/{id}/shots/{shot_id}/retry/schedule
```

故事事件通过 `POST /projects/{id}/narrative-events` 录入，包含章节/顺序、场景、摘要、角色、
叙事权重、情绪和可选原文定位或摘录。事件初始为待审核；通过
`POST /projects/{id}/narrative-events/{event_id}/review` 明确确认后，才会成为下一次故事与分镜规划
的输入。Story Bible 只保留选用事件、版本与原文 SHA-256，不复制原文摘录到生成提示词。事件被修订
或退回时，尚未进入媒体生成的旧计划会自动失效；一旦创建生成任务、出现媒体产物或项目已发布，事件将
锁定，需通过项目分支继续改编。

审核通过的故事事件可用 `POST /projects/{id}/adaptation-scenes/derive` 生成可编辑的剧本场次，或通过
`POST /projects/{id}/adaptation-scenes` 手工创建。场次必须绑定已确认事件，修改后会回到待审核；只有已确认
且来源事件版本、审核状态和哈希仍一致的场次才会优先进入下一次分镜规划。事件或章节修订会将关联场次
自动退回修改，避免使用已过期剧本继续生产。

原著可通过 `POST /projects/{id}/source-chapters` 以章节形式导入，记录权利依据、原文 SHA-256、
版本与是否允许外部模型处理。`POST /projects/{id}/source-chapters/{chapter_id}/event-candidates`
默认只在本地按段落结构提取候选事件；仅当该章节显式设置 `allow_external_processing=true` 且外部 LLM
已配置时，才会将本章原文发送给模型做结构化提取。候选不会自动进入规划，必须通过
`POST /projects/{id}/narrative-candidates/{candidate_id}/adopt` 采纳为待审核故事事件。章节修订会使未采纳
候选失效、将关联事件退回修改并使尚未进入媒体生产的计划失效；媒体生产开始后，章节与候选同样冻结。

`POST /projects/{id}/source-documents/import` 支持 TXT、Markdown、DOCX 和 PDF，单文件上限 20 MB。服务将
原始文件保存在项目受控目录，记录文件 SHA-256、解析器版本、格式、权利依据、提取方式和自动分出的章节 ID；
原文件可由 `GET /projects/{id}/source-documents/{document_id}/download` 校验哈希后下载。PDF 优先提取内嵌文字。
扫描件只有在导入请求显式设置 `allow_external_processing=true`，并由部署方配置受控 HTTP OCR 或本地 OCR 命令时
才会处理；默认不会发送文件，OCR 的配置状态可由 `GET /source-ingest/status` 查询。HTTP OCR 接口接收
`filename`、`mime_type` 和 `content_b64`，返回 `{"text":"..."}`。OCR 调用结果会以 `ocr_http` 或
`ocr_command` 写入来源记录，便于审计。
项目分支会复制受控原著文件；快照会内嵌经 SHA-256 校验的原始文件内容，交付包也会携带 `sources/` 中的
来源文件，因此恢复、导入和审计不会依赖原项目目录。

参考图通过 `POST /projects/{id}/references` 登记。请求体包含
`name`、`content_b64`、`license`，以及可选的 `kind`（角色、风格或场景）和
`character`。服务会校验真实图片格式、写入项目 `references/` 目录、计算
SHA-256，并把最新版本传播到已规划镜头的 `GenerationSpec.reference_assets`。
交付 ZIP 会同时携带这些参考图；ComfyUI 工作流绑定
`reference_image_uri` 时会自动上传本地参考图，Replicate 默认会把不超过
256 KiB 的本地参考图编码为 Data URL。模型专用字段、较大文件和远程存储仍通过
自定义 `input_builder` 或可访问的 HTTP(S) URL 接入。

项目音频轨通过 `POST /projects/{id}/audio` 登记，支持 AAC、FLAC、M4A、MP3、OGG、
Opus、WAV 和 WebM，单文件上限为 50 MB。服务会校验音频可解码性、记录时长、SHA-256、
许可证和来源，并在最终导出时循环或截断音频以匹配样片时长；交付 ZIP、归档 ZIP、项目
清单和资产清单均会携带音频轨及其治理元数据。可通过 `DELETE /projects/{id}/audio`
移除音频；项目发布后音频轨会被锁定。

多角色对白通过 `POST /projects/{id}/dialogue` 生成。每句对白绑定镜头、角色、起止秒数、
音色和语言；服务会校验角色与镜头时间窗，在临时目录逐句合成并原子替换最终 AAC/M4A
轨道，同时生成带角色前缀的 SRT。任一逐句合成、混音或探测失败都会保留旧音轨；镜头或
对白变更后旧时间轴会被标记为过期，导出必须重新合成。口型同步通过独立适配器接入，
不会默认发送本地媒体数据。

Provider mode is selected with `MEDIAFORGE_PROVIDER`:

```powershell
$env:MEDIAFORGE_PROVIDER = "mock"
$env:MEDIAFORGE_PROVIDER = "comfyui"
$env:MEDIAFORGE_PROVIDER = "replicate"
$env:MEDIAFORGE_PROVIDER = "local"
```

For capability-aware routing across more than one backend, set
`MEDIAFORGE_PROVIDERS` to a comma-separated list. The first capable provider
has the highest priority; when a generation attempt fails, the same Job can
fail over once to each remaining eligible provider, while every attempt is
still filtered by capability, budget, and configuration readiness:

```powershell
$env:MEDIAFORGE_PROVIDERS = "comfyui,replicate,mock"
```

The singular setting remains supported and is used when the pool setting is
absent. `/providers/status`, `/providers/health`, and `/providers/diagnostics`
return both the effective provider and the complete routing pool.

When using `comfyui`, also set `COMFYUI_BASE_URL` and
`COMFYUI_WORKFLOW_PATH`. The workflow must be a reviewed ComfyUI API-format
JSON graph. `COMFYUI_TIMEOUT_SECONDS`, `COMFYUI_POLL_INTERVAL_SECONDS`, and
`COMFYUI_ESTIMATED_COST` are optional runtime controls. For a production
template pool, use `COMFYUI_WORKFLOW_REGISTRY_PATH` and set
`COMFYUI_REQUIRE_WORKFLOW_PIN=true`; every registry entry then needs a version
and an expected SHA-256. MediaForge validates the graph bytes during startup,
requires `MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID` when a registry has more than
one image graph or `MEDIAFORGE_VIDEO_WORKFLOW_TEMPLATE_ID` when it has more
than one `image_to_video` graph, routes platform-created shots to that reviewed
`template_id`, and includes the selected graph identity and
ComfyUI queue/execution timeline in each artifact sidecar. See
`docs/comfyui-provider.md` for the registry format.

Use `mediaforge-comfyui-preflight --registry <path>` to validate pinned
registry contents without opening a network connection or submitting media.

When using `replicate`, set `REPLICATE_API_TOKEN` and the pinned
`REPLICATE_MODEL_VERSION`. `REPLICATE_API_BASE_URL` is optional. The HTTP
adapter defaults to two bounded retries with a `0.5` second exponential base
backoff; override these with `REPLICATE_HTTP_RETRY_ATTEMPTS` and
`REPLICATE_HTTP_RETRY_BACKOFF_SECONDS` when needed. Prediction creation uses a
stable per-job `Idempotency-Key` (`mediaforge-{job_id}`), so a transient
response or timeout can be retried without intentionally creating a second
prediction. `REPLICATE_TIMEOUT_SECONDS`, `REPLICATE_POLL_INTERVAL_SECONDS` and
`REPLICATE_CANCEL_AFTER_SECONDS` control local polling and the remote deadline.
`REPLICATE_CANCEL_REQUEST_TIMEOUT_SECONDS` separately bounds the cloud
cancellation request (default `15`) so a manual cancellation does not inherit a
long generation timeout. On timeout, or when an operator cancels a bound
native-webhook Job, MediaForge requests Replicate's prediction-specific
cancellation URL before recording the terminal local state. Non-idempotent
`POST` requests are never retried automatically.

Replicate `429` responses honor `Retry-After`: waits up to
`REPLICATE_HTTP_RETRY_MAX_DELAY_SECONDS` are retried inside the existing
attempt; longer waits become a durable Job retry plan. The audit event records
the policy delay, the Provider delay, and the chosen scheduled delay so a
production operator can distinguish a service quota from an internal failure.

Set `MEDIAFORGE_WORKER_EXECUTION_MODE=replicate-webhook` together with
`REPLICATE_WEBHOOK_URL_TEMPLATE` when a public HTTPS control plane should
receive native Replicate completion events. The URL template must contain both
`{project_id}` and `{job_id}`. `REPLICATE_WEBHOOK_SIGNING_SECRET` verifies the
provider's webhook headers independently of `MEDIAFORGE_CALLBACK_SECRET`,
which still protects the Worker-to-control-plane submission binding. Check the
non-sensitive receiver configuration at
`GET /providers/replicate/webhook-security`.

When using `local`, set `MEDIAFORGE_LOCAL_PROVIDER_COMMAND`. The reviewed
command receives a JSON generation request on stdin and through
`MEDIAFORGE_REQUEST_PATH`; it must create the exact `MEDIAFORGE_OUTPUT_PATH`
as a readable PNG or MP4. Configure capabilities, timeout, health and warmup
with the `MEDIAFORGE_LOCAL_PROVIDER_*` variables. `POST /providers/warmup`
runs explicit warmup hooks and requires an admin role.

Lip-sync is an explicit post-production adapter. Configure
`MEDIAFORGE_LIPSYNC_MODE=command` with `MEDIAFORGE_LIPSYNC_COMMAND` for a local
GPU worker, or use `http` only with `MEDIAFORGE_LIPSYNC_ALLOW_DATA_EXPORT=true`.
Command mode must create a readable MP4 at `MEDIAFORGE_OUTPUT_PATH`; HTTP mode
returns JSON `{ "output_base64": "..." }`. `POST /projects/{id}/lipsync`
promotes the result to the project master and includes it in assets, manifests,
delivery packages and audit events.

Check the active Provider:

```powershell
Invoke-RestMethod http://127.0.0.1:8020/providers/status
Invoke-RestMethod http://127.0.0.1:8020/providers/health
Invoke-RestMethod http://127.0.0.1:8020/providers/diagnostics
```

`/providers/status` reports configuration readiness. `/providers/health`
performs an explicit runtime probe: Mock is local-ready, ComfyUI checks
`/system_stats` and, when a reviewed model manifest is declared, validates
model names through `/models/{folder}`; Replicate checks API reachability
without starting a generation job. `/providers/diagnostics` combines configuration, connectivity,
capabilities, production-mode, and Provider-specific checks into a readiness
grade (`READY`, `SIMULATION`, `DEGRADED`, or `BLOCKED`) and never returns API
credentials. The Studio `服务商` panel and top-bar check use this diagnostic
contract.

异步 Provider 回调支持 HMAC-SHA256 鉴权。生产 Provider 模式下必须设置
`MEDIAFORGE_CALLBACK_SECRET`，否则回调入口返回 `503`；本地 Mock 模式可不设置。
签名原文依次为 `timestamp`、HTTP 方法、请求路径和原始 JSON 请求体，各字段以换行分隔。
请求使用 `X-MediaForge-Timestamp` 和 `X-MediaForge-Signature` 头，签名格式为
`sha256=<hex>`。默认只接受 300 秒内的请求，可通过
`MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS` 调整（30 至 86400 秒）。状态可从
`GET /providers/callback-security` 或 Provider 诊断中查看，响应不会泄露密钥。

`/governance/license-registry` exposes the auditable license registry used by
the release gate. By default it uses built-in records for reference licenses,
Providers, workflow families, and LoRA prefixes. Set
`MEDIAFORGE_LICENSE_REGISTRY_PATH` to a JSON file containing a `records` array
to replace those records, for example:

```json
{
  "records": [
    {
      "registry_id": "workflow:reviewed_i2v",
      "kind": "workflow",
      "match": "reviewed_i2v",
      "match_type": "exact",
      "license": "operator-managed",
      "status": "approved",
      "evidence": "internal review ticket"
    }
  ]
}
```

Project compliance reports include the registry source, registered counts, and
unregistered dependencies. Any unregistered dependency blocks release and
archive packaging until the registry is updated.

集中式台账同步使用 `MEDIAFORGE_LICENSE_REGISTRY_SYNC_URL`，通过
`POST /governance/license-registry/sync` 按需拉取并校验。可用
`MEDIAFORGE_LICENSE_REGISTRY_SYNC_ALLOWED_HOSTS`（逗号分隔）限制目标主机，
`MEDIAFORGE_LICENSE_REGISTRY_SYNC_TOKEN` 提供可选的 Bearer Token。同步支持
ETag/Last-Modified 条件请求、5 MB 响应上限、内容 SHA-256 和失败保护；坏数据或
上游不可用时不会替换当前台账，结果会写入 `management.sync`。设置
`MEDIAFORGE_LICENSE_REGISTRY_SYNC_INTERVAL_SECONDS`（至少 30 秒）后，服务启动时
会开启后台定时同步；`GET /governance/license-registry/sync/status` 可查看调度状态。

生产部署可启用 `MEDIAFORGE_AUTH_MODE=required`，并通过
`MEDIAFORGE_API_KEYS` 配置 JSON 格式的 Bearer Token、角色和租户，例如：

```powershell
$env:MEDIAFORGE_AUTH_MODE = "required"
$env:MEDIAFORGE_API_KEYS = '{"studio-editor":{"subject":"producer","role":"editor","tenant_id":"tenant_a"},"studio-admin":{"subject":"ops","role":"admin"}}'
```

支持 `viewer`、`provider`、`reviewer`、`editor`、`publisher` 和 `admin` 角色。
项目读取、统计和写操作会按 `tenant_id` 隔离；默认开发模式为 `disabled`，不要求令牌。
`/metrics` 输出 Prometheus 文本格式的 HTTP 请求计数、状态码和耗时指标。默认开发
模式可匿名读取；设置 `MEDIAFORGE_METRICS_AUTH_MODE=token` 后，`/metrics` 与
`/metrics/runtime` 仅接受来自 `MEDIAFORGE_METRICS_TOKEN_FILE` 的专用 Bearer
Token，适用于随附的监控 Compose 覆盖层。

租户配额默认不限；生产环境可以设置 `MEDIAFORGE_TENANT_MAX_PROJECTS`、
`MEDIAFORGE_TENANT_MAX_JOBS`、`MEDIAFORGE_TENANT_BUDGET`，或用
`MEDIAFORGE_TENANT_QUOTAS` 为不同租户配置 JSON 覆盖。创建项目、提交生成任务、
批量 A/B 候选以及复制/导入项目都会经过配额门禁，超限返回 `429` 并附带当前用量。
项目协作支持成员角色和项目级/镜头级评论，数据随快照、SQLite/JSON 状态和审计日志持久化。
项目级权限会在每次请求时重新校验：查看者只能读取，审核者可审核和评论，编辑者可修改，
发布者可执行交付/发布，成员管理仅限项目所有者。租户成本台账可通过
`/tenants/me/cost` 查询，并导出 JSON/CSV，包含项目预算、实际支出、镜头尝试次数和候选版本成本。

Provider failures are persisted with the last error and an exponential retry
schedule. Configure `MEDIAFORGE_RETRY_MAX_ATTEMPTS` and
`MEDIAFORGE_RETRY_BASE_DELAY_SECONDS`; queue drain and the Worker CLI promote
due retries automatically. Manual `Retry` remains available as an explicit
operator override, while a scheduled retry can be canceled.

The Provider HTTP retry policy is separate from the Job retry policy. Provider
HTTP retries cover transient network/HTTP failures within one execution
attempt; Job retries create a new execution attempt after a failed job. The
Replicate request strategy is visible in `/providers/status`,
`/providers/health`, and the artifact metadata sidecar. ComfyUI `POST /prompt`
is not automatically replayed because its API does not provide a portable
idempotency contract.

Provider jobs also use a restart-safe execution lease. Configure
`MEDIAFORGE_JOB_LEASE_SECONDS` (default `900`). On service startup, or through
`POST /projects/{id}/jobs/recover-stale` and `POST /jobs/recover-stale`, jobs
left in `ADMITTED` or `RUNNING` beyond the lease are marked failed, audited,
and placed into the normal bounded retry schedule. This prevents a crashed
worker from leaving a project permanently stuck in execution.

Worker 控制面支持注册、心跳、并发容量和持久化租约领取。Provider 角色可以调用
`/workers/{worker_id}/claim` 领取排队 Job，随后使用
`/projects/{id}/jobs/{job_id}/process?worker_id=...` 执行；租约过期后由 stale recovery
回收，避免多个 Worker 重复处理同一个任务。旧版状态文件没有 Worker 节点时会自动兼容。
Worker 注册时可声明生成能力和资源标签，领取时会按能力过滤 Job；工作台展示主机、CPU 和
GPU 遥测，CLI 自动上报 `nvidia-smi` 的型号、驱动、总显存、空闲显存与利用率；领取接口
支持 `minimum_gpu_memory_mib` 显存准入并返回结构化原因。当前仍是 API 服务商调度，不是
Worker 本机独立 GPU 推理。资产清单中的生成资产、
参考资产和最终输出均可通过资产 ID 下载，下载网关会校验项目归属、artifact root 和哈希。

Run a live API smoke flow:

```powershell
python -m mediaforge_p1.api_smoke --base-url http://127.0.0.1:8020
```

Drain all queued jobs from the local workspace without the web UI:

```powershell
python -m mediaforge_p1.worker --output-root artifacts/api --limit 50
```

Run a real remote Worker against the HTTP control plane. The process registers
itself, claims only jobs matching its declared capabilities, renews active
leases in the background, and executes jobs through the same API used by the
Studio UI:

```powershell
python -m mediaforge_p1.worker `
  --base-url http://127.0.0.1:8020 `
  --worker-id local-gpu-01 `
  --capability image_to_video `
  --concurrency 1
```

Use `--once` for a single poll-and-drain pass, `--project-id` to constrain
claims, and `--token` when API authentication is enabled. `MEDIAFORGE_WORKER_*`
environment variables provide equivalent defaults for container deployment.

After `pip install -e .`, the same entry points are also available as
`mediaforge`, `mediaforge-api-smoke`, `mediaforge-worker`, and
`mediaforge-provider-probe`.

If `127.0.0.1:8010` shows another old local app, use `8020`. Browsers can keep
stale single-page apps or service workers for a reused localhost port.

## Test

```powershell
python -m pytest
```

The spike intentionally uses a deterministic `MockProvider`. Real image or
video Providers can use the same contract without changing the job state
machine or artifact record format.

## Scope

- `GenerationSpec` validation with Pydantic
- Idempotent job creation and guarded state transitions
- Mock image/video generation
- ComfyUI HTTP Provider adapter with workflow bindings
- Version-pinned async cloud video Provider adapter
- Deterministic Provider Router with capability and budget checks
- FastAPI Studio flow with project recovery, operations summary, job filters,
  shot filters, retry, cancellation, archive/restore, policy gate, cost ledger,
  studio throughput/quality/cost metrics,
  runtime license registry validation, import, export, and restart persistence,
  signed asynchronous Provider callback reconciliation with replay protection and event idempotency,
  audit JSON/CSV export, asset inventory, release gate, evaluation report,
  Provider benchmark, route preview, shot-level A/B variants, project snapshot,
  verified archive package import
  export/import with closed-loop evidence, delivery package import/verification and re-verification,
  project/job/asset trace query and export,
  provenance/chain-of-custody report, license/source compliance gate,
  deterministic story/timeline continuity audit with release and archive gates,
  provider artifact metadata sidecars with package inclusion,
  reference asset upload, licensing, hashing, Provider input propagation,
  restart-safe job lease recovery and stale execution auditing,
  deterministic SRT subtitle timeline export, audio-track probing/mixing/licensing, distribution receipt/acknowledgement/report, acceptance certificate, project closeout,
  final archive bundle and verification, clone/import branch, queued execution, global queue
  drain, worker CLI, production report export, export, delivery ZIP, editable project/shot
  cards, OpenAI-compatible story planning, authenticated SSE progress, HMAC Webhook events
- Deterministic local safety policy and explainable media quality checks
- FFmpeg discovery, image/video probing, normalization, and concatenation
- A small end-to-end vertical slice with a manifest

This is a research harness, not the final product architecture.
