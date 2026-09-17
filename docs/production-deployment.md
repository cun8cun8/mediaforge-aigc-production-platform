# MediaForge 生产部署

## 启动

```powershell
Copy-Item .env.example .env
# 编辑 .env，至少替换 API keys、回调密钥和 Provider 配置
docker compose up --build -d
# 可选：启动同一控制面的远程 Worker
docker compose --profile worker up --build -d
```

服务地址为 `http://127.0.0.1:8020/`。部署后检查：

```powershell
$headers = @{ Authorization = "Bearer $env:MEDIAFORGE_READINESS_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8020/health
Invoke-RestMethod http://127.0.0.1:8020/auth/status
Invoke-RestMethod http://127.0.0.1:8020/providers/diagnostics
Invoke-RestMethod http://127.0.0.1:8020/llm/status
Invoke-RestMethod http://127.0.0.1:8020/webhooks/status -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/delivery/status -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/quality/status -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/enterprise/probe -Method Post -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/ops/readiness -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/ops/alerts -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/enterprise/status -Headers $headers
Invoke-RestMethod http://127.0.0.1:8020/billing/summary -Headers $headers
Invoke-WebRequest http://127.0.0.1:8020/metrics
mediaforge-readiness --base-url http://127.0.0.1:8020 --token $env:MEDIAFORGE_READINESS_TOKEN
mediaforge-production-acceptance --base-url http://127.0.0.1:8020 --token $env:MEDIAFORGE_READINESS_TOKEN --probe-enterprise --probe-planning --require-production
```

`mediaforge-production-acceptance` writes a sanitized report to
`artifacts/production-acceptance.json`. It checks the API, Provider diagnostics,
OCR configuration, planning state, enterprise runtime and readiness. It does not
submit a generation request; run `mediaforge-provider-probe` explicitly after
approving any real Provider cost. In strict mode, it also validates one recent,
HMAC-signed probe receipt per enabled real Provider, including the local artifact
hash and quality result. Configure the receipt signing value only in the secret
manager as `MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET`; give acceptance the same
value through `--provider-probe-secret-file` or
`MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET_FILE`. `--require-production` fails
when Mock mode, identity, shared queue, persistent storage, callback protection
or Provider probe evidence leave the deployment below production requirements.

## GPU Worker Local Execution

默认远程 Worker 的 `api-provider-dispatch` 模式只负责领取和心跳，实际 Provider 仍由
API 容器调用。需要让具备模型和 GPU 的 Worker 独立推理时，启用
`local-provider-callback`：Worker 在共享 artifact 根目录生成媒体，并以 HMAC 签名的
`RUNNING`/终态回调提交结果。控制面会校验 Worker 持有该 Job 的未过期租约，并只把与
该 Worker Provider 名称相同的已路由任务分配给它。

```powershell
# .env 必须同时供 API 和 Worker 使用；Worker token 对应 role=provider 的 API key。
# MEDIAFORGE_PROVIDER=local
# MEDIAFORGE_LOCAL_PROVIDER_COMMAND=/opt/mediaforge-provider/run.sh
# MEDIAFORGE_WORKER_PROVIDER=local
# MEDIAFORGE_CALLBACK_SECRET=<long-random-secret>
# MEDIAFORGE_WORKER_TOKEN=<provider-role-api-key>
docker compose --profile gpu-worker up --build -d
```

GPU Worker 与 API 必须挂载同一个可写的 `/var/lib/mediaforge/artifacts`，并配置相同的
Provider 池/工作流版本。Worker 会自动申报自己实际支持的能力和 Provider 名称，无法匹配
的任务保留在队列中，不会被错误 Worker 领取后消耗一次重试。`mediaforge-gpu-worker`
Compose profile 请求全部 NVIDIA GPU；按节点和显存策略调整 `MEDIAFORGE_GPU_WORKER_CONCURRENCY`
或在编排层限制设备。真实模型、驱动、权重和 `MEDIAFORGE_LOCAL_PROVIDER_COMMAND` 本身仍由
Worker 镜像或受控挂载提供，通用应用镜像不会包含商业模型。

当 API 配置多个 Provider 时，为每个 GPU Worker 设置
`MEDIAFORGE_WORKER_PROVIDER` 为 API 诊断中对应的 Provider 名称或配置模式
（例如 `comfyui`、`replicate`、`local`）。这样各 Worker 只领取自己能执行的路由任务；
未配置或拼写错误的名称会在 Worker 启动时失败，而不会错误消耗队列任务。

先通过不产生工作流任务的诊断，再在批准成本后运行一次 Provider Probe：

```powershell
$env:MEDIAFORGE_PROVIDER_PROBE_RECEIPT_SECRET = Get-Content ops/secrets/provider-probe-receipt-secret -Raw
mediaforge-provider-probe --provider local --output artifacts/provider-probe
mediaforge-production-acceptance `
  --base-url http://127.0.0.1:8020 `
  --token $env:MEDIAFORGE_READINESS_TOKEN `
  --provider-probe-receipt artifacts/provider-probe/manifest.json `
  --provider-probe-secret-file ops/secrets/provider-probe-receipt-secret `
  --require-production
```

## Kubernetes

仓库中的 [Kubernetes 清单](../k8s/README.md) 包含两个 API 副本、PostgreSQL 租约就绪
探针、RWX artifact PVC、PDB 和可选 GPU Worker overlay。它不是托管数据库、对象存储、
密钥管理或 GPU 模型镜像的替代品。部署前必须：替换镜像和 `mediaforge-rwx` 存储类；通过
组织密钥系统创建 `mediaforge-runtime`；为 API 和 Worker 创建相同的 `mediaforge-provider`
配置；确认 PostgreSQL、Redis、S3/MinIO 和 NVIDIA device plugin 已可用。

`/livez` 只用于 startup/liveness；`/health` 用于 readiness。启用 leased 控制面时，备用
副本故意在 `/health` 返回 503，Kubernetes Service 只会向当前租约持有者发送流量。先做
渲染验证再 apply：

```powershell
kubectl kustomize k8s/base | Out-Null
kubectl kustomize k8s/overlays/gpu-worker | Out-Null
kubectl apply -k k8s/overlays/gpu-worker
kubectl -n mediaforge get pods
```

## CI/CD 与网络边界

`.github/workflows/ci.yml` 在每个提交和 PR 上安装完整运行依赖，执行 Python 回归、
Compose 标准/HA/GPU 配置渲染、所有 Kubernetes overlay 渲染、生产镜像构建，以及中文
工作台、叙事、分步规划浏览器验收。UI 截图、报告和 API 日志会作为失败或成功证据上传。

`.github/workflows/deploy.yml` 不会在推送时自动发布。它只能手动执行，要求提供 GitHub
Environment、镜像不可变引用和目标 overlay，并从该 Environment 读取 base64 编码的
`KUBE_CONFIG_DATA`。工作流总是先做 `kubectl apply --server-side --dry-run=server`；只有
明确选择 `apply` 才会真正提交。生产 Environment 应启用必需审批者和受限分支规则。

需要最小网络权限时，使用 `k8s/overlays/locked-down`。该 overlay 默认拒绝全部入/出站，
只允许 DNS 和 GPU Worker 到 API 的控制流。部署方必须从示例创建环境专用 NetworkPolicy，
明确放行数据库、Redis、对象存储、身份服务、审核/Provider 服务、监控抓取和受控入口。
不要把通配公网 egress 当作严格隔离，也不要在未添加这些规则前把该 overlay 应用于生产。

## Active/Passive Control Plane

`docker-compose.ha.yml` provides a two-or-more replica deployment topology with
PostgreSQL state, Redis queue indexing, MinIO-compatible object storage, a
shared artifact volume, and an Nginx gateway. Start it with a real `.env` that
contains `MEDIAFORGE_POSTGRES_PASSWORD`, `MEDIAFORGE_MINIO_ROOT_USER`,
`MEDIAFORGE_MINIO_ROOT_PASSWORD`, API authentication, callback and Provider
settings, then scale the API service:

```powershell
docker compose -f docker-compose.ha.yml up --build -d --scale mediaforge=2
```

The leased control plane uses `MEDIAFORGE_CONTROL_PLANE_MODE=leased` with a
PostgreSQL row lease. Only its current holder returns `200` from `/health` and
serves application traffic; standby instances return `503` and reload the
durable state snapshot after lease acquisition. The gateway retries a standby
`503` against another replica. This is active/passive high availability, not
unsafe multi-writer replication.

All replicas must mount the same read-write artifact root. For a multi-node
deployment use a vetted RWX persistent volume or another shared filesystem at
`/var/lib/mediaforge`; PostgreSQL retains state snapshots but generated-media
paths and OIDC/session artifacts also need shared durable storage. Run
`/enterprise/probe`, `/ops/readiness`, `/ops/alerts` and
`mediaforge-production-acceptance --require-production` through the gateway
before admitting production traffic.

## Operations Alerts

`GET /ops/alerts` derives a deterministic `HEALTHY`, `WARNING`, or `CRITICAL`
grade from queue depth and wait time, terminal-job and Provider failure rates,
studio spend, stale Worker heartbeats, and optional strict production readiness.
The same active-alert gauges are exposed through `/metrics` for Prometheus or
another collector. The API does not transmit telemetry or invoke webhooks on its
own, so alert routing remains under the deployment team's control.

Set the `MEDIAFORGE_ALERT_*` variables in `.env.example` to match capacity and
budget policy. Enable `MEDIAFORGE_ALERT_REQUIRE_PRODUCTION_READY=true` only in a
real production control plane; it intentionally treats Mock mode and missing
enterprise dependencies as critical.

## Monitoring And Alert Routing

`docker-compose.observability.yml` is a production-facing Compose overlay that
starts Prometheus 3.13 LTS, Alertmanager, and Grafana with a pre-provisioned
MediaForge dashboard. It scrapes the existing `/metrics` endpoint every 15
seconds and evaluates availability, critical/warning operations alerts, HTTP 5xx
ratio, and Provider failure ratio. The stack binds ports only to `127.0.0.1`;
publish them through an authenticated TLS reverse proxy when remote access is
needed.

Create two non-versioned one-line secrets before starting it. The metrics secret
is mounted into both MediaForge and Prometheus, and is never placed in the
Prometheus configuration or environment variables:

```powershell
New-Item -ItemType Directory -Force ops/secrets | Out-Null
$metricsToken = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
$grafanaPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
Set-Content -NoNewline -Encoding ascii ops/secrets/mediaforge-metrics-token $metricsToken
Set-Content -NoNewline -Encoding ascii ops/secrets/grafana-admin-password $grafanaPassword
$env:MEDIAFORGE_METRICS_TOKEN_FILE = (Resolve-Path ops/secrets/mediaforge-metrics-token)
$env:MEDIAFORGE_GRAFANA_ADMIN_PASSWORD_FILE = (Resolve-Path ops/secrets/grafana-admin-password)
```

For the standard single-instance Compose deployment:

```powershell
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
mediaforge-production-acceptance --base-url http://127.0.0.1:8020 --metrics-token-file $env:MEDIAFORGE_METRICS_TOKEN_FILE
```

For active/passive HA, point Prometheus at the Nginx gateway so an inactive
lease holder is retried transparently:

```powershell
$env:MEDIAFORGE_METRICS_TARGET = "gateway:8080"
docker compose -f docker-compose.ha.yml -f docker-compose.observability.yml config --quiet
docker compose -f docker-compose.ha.yml -f docker-compose.observability.yml up --build -d --scale mediaforge=2
```

Open Grafana at `http://127.0.0.1:3000/`, Prometheus at
`http://127.0.0.1:9090/targets`, and Alertmanager at `http://127.0.0.1:9093/`.
The initial Grafana username is `admin` unless
`MEDIAFORGE_GRAFANA_ADMIN_USER` is set; its password is the generated secret
file. Verify a scrape without exposing the token in a shell history:

```powershell
$headers = @{ Authorization = "Bearer $(Get-Content -Raw $env:MEDIAFORGE_METRICS_TOKEN_FILE)" }
Invoke-WebRequest http://127.0.0.1:8020/metrics -Headers $headers
```

The bundled Alertmanager route is deliberately `dashboard-only`: it groups and
shows alerts locally but has no external receiver. This makes an unconfigured
install safe, but it is not a paging system. To enable an approved generic
Webhook, copy `ops/observability/alertmanager/alertmanager.webhook.example.yml`
to a non-versioned location, replace the example URL, create the one-line token
file referenced by `credentials_file`, then start the extra overlay:

```powershell
$env:MEDIAFORGE_ALERTMANAGER_CONFIG_FILE = (Resolve-Path ops/secrets/alertmanager.yml)
$env:MEDIAFORGE_ALERTMANAGER_WEBHOOK_TOKEN_FILE = (Resolve-Path ops/secrets/alertmanager-webhook-token)
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.webhook.yml up -d
```

Use the notification system's own endpoint, TLS policy, allow-list, retry
policy, and on-call escalation before treating this as a production paging
control. A third-party notification URL or token cannot be provisioned by this
repository.

Compose 使用持久化卷 `/var/lib/mediaforge`，SQLite 状态库位于
`/var/lib/mediaforge/artifacts/mediaforge-state.sqlite3`，媒体产物位于同一卷的
`artifacts/` 目录。备份时需要同时备份 SQLite 文件和产物目录，恢复后先执行
`/health`、`/providers/diagnostics`，再执行交付包验收。

## 安全配置

生产环境必须使用 `MEDIAFORGE_AUTH_MODE=required` 或 `oidc`。API Token 通过
`MEDIAFORGE_API_KEYS` 注入，响应不会回显 Token。生产 Provider 模式还必须设置
`MEDIAFORGE_CALLBACK_SECRET`，Provider 回调需要 HMAC-SHA256 签名；请将回调 URL
置于反向代理或内网入口之后，并限制 `MEDIAFORGE_LICENSE_REGISTRY_SYNC_ALLOWED_HOSTS`。

推荐将 `.env` 放在密钥管理系统生成后注入，不要提交到版本库。轮换 API Token 或
回调密钥后重启服务，并用 `/auth/status`、`/providers/callback-security` 验证生效。

企业身份可使用 OIDC RFC 7662 introspection：

```text
MEDIAFORGE_AUTH_MODE=oidc
MEDIAFORGE_OIDC_INTROSPECTION_URL=https://login.example.com/oauth2/introspect
MEDIAFORGE_OIDC_CLIENT_ID=mediaforge
MEDIAFORGE_OIDC_CLIENT_SECRET=replace-with-client-secret
```

OIDC 模式不允许匿名请求。身份服务必须返回有效主体以及 `tenant_id`、`tenant` 或
`org_id` 中至少一个非空租户字段；角色取自 `roles` 或 `realm_access.roles`。
身份模式以 `MEDIAFORGE_AUTH_MODE` 为准，不再使用独立的身份展示开关。

如企业的用户生命周期由 SAML、LDAP/AD 或 SCIM 管理，应由 IdP 将其联邦为 OIDC Claim，
而不是让应用保存目录密码或手写 SAML XML 解析。`MEDIAFORGE_OIDC_ROLE_CLAIM` 和
`MEDIAFORGE_OIDC_TENANT_CLAIM` 支持点分 Claim 路径，
`MEDIAFORGE_OIDC_ROLE_MAPPING` 将经审批的目录组映射为 MediaForge 角色。例如
`groups` 中的 `mediaforge-publishers` 可映射为 `publisher`。该映射只参与服务端授权，
目录成员变更在 IdP 签发新 Token 后生效。

浏览器单点登录在 introspection 之外配置授权码 + PKCE：
`MEDIAFORGE_OIDC_AUTHORIZATION_URL`、`MEDIAFORGE_OIDC_TOKEN_URL`、
`MEDIAFORGE_OIDC_REDIRECT_URI`。服务在 `/auth/login` 生成一次性 state 和 S256 challenge，
`/auth/callback` 交换授权码并写入 HttpOnly、SameSite=Lax 会话 Cookie。默认单控制面将
PKCE state 和会话保存在当前进程内存；`MEDIAFORGE_CONTROL_PLANE_MODE=leased` 下，
`MEDIAFORGE_OIDC_SESSION_BACKEND=auto` 自动改用 PostgreSQL，因此主备切换后仍可消费
登录回调和读取会话。生产环境必须使用 HTTPS 并设置
`MEDIAFORGE_OIDC_COOKIE_SECURE=true`。

对象存储支持本地文件系统和 S3/MinIO。S3/MinIO 运行镜像安装企业依赖：
`python -m pip install -e ".[enterprise]"`，然后配置
`MEDIAFORGE_STORAGE_MODE=s3`、`MEDIAFORGE_STORAGE_ENDPOINT` 和
`MEDIAFORGE_STORAGE_BUCKET`。共享队列可配置 Redis：
`MEDIAFORGE_QUEUE_BACKEND=redis`、`MEDIAFORGE_REDIS_URL`；`/enterprise/status`
会检查驱动和关键配置是否齐全。生产镜像已安装企业依赖。
交付接口 `/projects/{id}/deliveries/dispatch` 在配置 S3/MinIO 后会先归档经过校验的
交付包，再分发交付。对象以租户、项目和发布版本分目录；对象地址、SHA-256 和大小
进入交付回执并持久化。上传失败不会产生成功交付回执。媒体制作仍使用本地工作目录，
对象存储不是所有媒体接口的透明替代品。

使用 PostgreSQL 状态后端时配置：

```text
MEDIAFORGE_STATE_BACKEND=postgres
MEDIAFORGE_DATABASE_URL=postgresql://mediaforge:password@postgres:5432/mediaforge
MEDIAFORGE_STATE_TABLE=mediaforge_state
MEDIAFORGE_QUEUE_BACKEND=redis
MEDIAFORGE_REDIS_URL=redis://redis:6379/0
```

PostgreSQL 会初始化状态表，并使用版本比较阻止旧进程覆盖新状态。当前支持一个 API
控制面和多个具有唯一 `worker_id` 的远程 Worker，不支持多个 API 进程同时修改同一
工作区；不要使用 `uvicorn --workers` 扩容控制面。Redis 保存可恢复的就绪任务索引，
数据库保存任务和租约。入队失败后的任务会保留，领取前补建缺失索引；领取落库后才
确认 Redis 记录。Worker 轮询同时推进到期重试、回收过期租约，不回收仍在正常心跳的任务。

分步规划可以单独将 Checkpoint 切换到官方 PostgreSQL Saver，并使用数据库 advisory lock
阻止两个进程同时推进同一项目草稿：

```text
MEDIAFORGE_PLANNING_MODE=langgraph
MEDIAFORGE_PLANNING_CHECKPOINT_BACKEND=postgres
MEDIAFORGE_PLANNING_DATABASE_URL=postgresql://mediaforge_planning:replace-me@postgres:5432/mediaforge_planning
```

该数据库应为专用实例或专用角色，首次使用会创建 LangGraph Checkpoint 表和
`mediaforge_planning_run` 表。它支持受控故障切换时读取/恢复同一草稿，不会把项目状态的
单控制面快照语义变成多主写入；完整多活控制面仍需要对项目状态、计费、限流和审计存储进行
统一的事务设计。部署后由管理员调用 `POST /planning/probe` 验证数据库和 Checkpoint
建表权限；`GET /planning/status` 与 `GET /ops/readiness` 只返回最近一次脱敏探测状态。
详细备份与升级边界见 [可恢复分步规划](staged-planning.md)。

`POST /enterprise/probe` 需要管理员权限，会执行 Redis PING、PostgreSQL SELECT 和
对象存储 HEAD 检查。`connectivity_verified` 表示本进程最近一次探测或操作结果，不是
持续健康监控；HEAD 成功也不代替真实上传权限验收。`GET /enterprise/status` 本身不
发起外部探测。更换后端不会自动迁移原 JSON/SQLite 工作区，迁移前应导出项目并备份。

Compose 从 `.env` 插值；使用其他环境文件时同时传入 `--env-file`，并设置
`MEDIAFORGE_ENV_FILE` 为同一路径。PostgreSQL URL、Redis URL 和存储凭证必须由部署
环境提供。备份还需覆盖媒体工作目录、计费 SQLite 和 Webhook outbox。

请求限流使用 SQLite 持久化窗口，当前按单控制面部署：
`MEDIAFORGE_RATE_LIMIT_BACKEND=sqlite`、`MEDIAFORGE_RATE_LIMIT_REQUESTS=120`、
`MEDIAFORGE_RATE_LIMIT_WINDOW_SECONDS=60`。工作台只读请求使用独立桶，默认是写入阈值的五倍；
可通过 `MEDIAFORGE_RATE_LIMIT_READ_REQUESTS=600` 明确设置。这样项目详情的并行读取和协作刷新
不会消耗生成、审核、发布等写操作配额。每个响应包含实际生效桶的剩余配额响应头。

用量计费台账写入 `MEDIAFORGE_BILLING_DB`，事件以 `(tenant_id, event_id)` 幂等；旧表会
在事务中迁移并保留记录。Provider 成功
生成会自动登记用量，也可通过 `POST /billing/events` 接入外部计费系统，使用
`GET /billing/summary` 和 `GET /billing/events` 对账。写入要求管理员或 Provider 角色，
项目必须属于当前租户。混合币种按 `by_currency` 分组，不生成跨币种合计。
同步生成自动登记估算成本；异步成功回调提供 `actual_cost` 时优先登记实际成本，
否则登记估算成本，并以 `cost_basis` 区分。回调重放不会重复记账。这是用量台账，不是
支付扣款或税务开票。外部支付/财务系统可将回执通过 `POST /billing/settlements` 写入
按租户和 `settlement_id` 幂等的结算账本，使用 `GET /billing/settlements` 与
`/billing/settlements/summary` 对账；平台不保存支付凭据，也不替代收单服务。

对于不能持有管理员 API Key 的支付网关或 ERP 适配器，可配置
`MEDIAFORGE_SETTLEMENT_CALLBACK_SECRET` 和允许的 Provider 名称，向
`POST /billing/settlements/callback` 发送规范化结算 JSON。该回调包含 `tenant_id`，
并使用 `X-MediaForge-Timestamp` 与 `X-MediaForge-Signature` 验证
`HMAC-SHA256(secret, timestamp + '.' + raw_body)`；默认关闭，重放窗口可配置。
回调仍以 `(tenant_id, settlement_id)` 幂等写入。支付宝、微信支付等各自的签名格式必须先由
受控网关或专用适配器转换为该规范化格式，平台不会接受未验证的第三方 Webhook。

Stripe payout 可以不经中间网关直接接入 `POST /billing/settlements/stripe`。设置
`MEDIAFORGE_STRIPE_SETTLEMENT_WEBHOOK_SECRET` 后，服务会在解析 JSON 前用原始请求体验证
`Stripe-Signature` 的 `t` 和一个 `v1` HMAC-SHA256 签名，并拒绝超过
`MEDIAFORGE_STRIPE_SETTLEMENT_MAX_AGE_SECONDS` 的重放。只接受
`payout.paid`、`payout.failed` 和 `payout.canceled`（可通过事件白名单收窄）；payout 必须携带
`metadata.mediaforge_tenant_id`，金额从 Stripe 最小货币单位转换后写入已有的租户幂等结算台账。
该入口不接收客户付款、银行卡数据或税务信息。支付宝和微信支付需要由持证网关验证其 RSA/证书
协议后使用通用规范化回调，不能把它们的签名格式错误地当成 Stripe HMAC。

文本配音通过 `POST /projects/{id}/voiceover` 生成并自动应用为项目音频轨。
`GET /speech/status` 查看配音模式；Windows 默认使用系统 SAPI，必须安装匹配语言和音色。
非 Windows 默认 `deterministic` 只生成测试音，不是人声，发布门禁明确拒绝测试音。
HTTP 模式配置 `MEDIAFORGE_TTS_MODE=http` 和 `MEDIAFORGE_TTS_URL`，请求包含
`text`、`voice`、`language`，响应为 JSON `{ "audio_b64": "..." }`，上限 50 MiB。
所有模式均在临时目录校验并统一转码为 AAC/M4A，失败保留原音轨。
请求可带 `license`（`unverified`/`owned`/`licensed`/`commercial_use_allowed`）和
`source` 授权来源；默认授权未验证，不能发布，指定授权必须提供来源凭证。
音色商业使用权由部署方确认，不会因自动生成而自动赋予商用权。支持通过
`POST /projects/{id}/dialogue` 生成多角色逐句时间轴：每句绑定镜头、角色、起止秒数、音色和
语言，服务会在临时目录逐句合成、混音为单条 AAC/M4A 并生成带角色前缀的 SRT，失败时保留
旧音轨，镜头变更会阻断过期时间轴导出。口型同步通过独立适配器接入，HTTP 服务费用需通过计费事件
或结算回执接入。

工作台“运行与账单”页展示运行配置、管理员连接探测和各币种用量，支持筛选及本页 JSON 导出。
`GET /billing/events` 支持 `project_id`、`category`、`currency`、`since`/`until`
（Unix 秒）、`limit`（1..500，默认 50）和 `offset`，返回 `total`、`has_more`。
分页按发生时间降序排列；并发新增用量时偏移分页可能移动，对账应指定固定结束时间。

## 规划器与事件通知

默认使用本地确定性故事规划器，适合开发和离线验收。生产环境可以配置任意
OpenAI-compatible Chat Completions 服务：

```text
MEDIAFORGE_LLM_MODE=openai_compatible
MEDIAFORGE_LLM_BASE_URL=https://llm.example.com/v1
MEDIAFORGE_LLM_API_KEY=replace-with-llm-key
MEDIAFORGE_LLM_MODEL=your-structured-output-model
MEDIAFORGE_LLM_RETRY_ATTEMPTS=2
MEDIAFORGE_LLM_RETRY_BACKOFF_SECONDS=0.5
```

`POST /projects/{id}/plan` 会校验外部返回的 StoryBible、ShotCard、角色和总时长，
不符合约束的响应不会进入生产队列；网络超时、429 和 5xx 会按有限次数退避重试，
最终失败返回 502，不会污染项目计划。`GET /llm/status` 可查看脱敏配置状态。

安装 `.[agents]` 并设置 `MEDIAFORGE_PLANNING_MODE=langgraph` 可另外启用五阶段、
独立草稿、人工审核与失败恢复。旧 `/plan` 保持原行为；新流程入口为工作台“分步规划”。
部署范围、Checkpoint 备份、权限与模型重试计费边界见 [可恢复分步规划](staged-planning.md)。

规划检索默认使用 SQLite FTS5 BM25 和中文双字分词；设置 `MEDIAFORGE_RAG_BACKEND=pgvector`
可启用 PostgreSQL 精确余弦语义检索，使用 TEI 嵌入服务与强制行级安全。设置
`MEDIAFORGE_RAG_BACKEND=ragflow` 可查询显式映射的外部 RAGFlow 数据集，适合复用已经治理的
企业知识库；该模式只读，不会将项目内容上传、同步或改写到 RAGFlow，且必须显式授权查询外发。
数据库迁移、模型配置、RAGFlow 映射、文本发送授权与验收命令见 [语义检索部署](semantic-memory.md)。
检索后端不会将现有单阶段规划器变成多智能体图执行器。
`MEDIAFORGE_RAG_ENABLED` 默认 true，`MEDIAFORGE_RAG_TOP_K` 默认 8（1..50），
`MEDIAFORGE_RAG_MAX_CONTEXT_CHARS` 默认 8000（256..32000）。检索内容作为不可信参考
传入外部规划器的 `retrieved_memory`，本地确定性规划器不使用上下文。
默认只检索当前项目；工作台“故事记忆”页可显式选择当前主体有权读取的同租户项目。
`GET /projects/{id}/memory/sources` 返回可选来源；检索接口为
`GET /projects/{id}/memory?query=关键词&source_project_ids=来源项目`，多个来源重复参数。
`POST /projects/{id}/plan` 可传 `{ "memory_project_ids": ["来源项目"] }`；省略请求体兼容旧用法。
越权来源统一 404；引用记录包含来源项目、镜头版本和 SHA256，不包含密钥。
SQLite/pgvector 的 `story-memory.sqlite3` 或向量索引从当前通过策略检查的项目状态延迟刷新，
拒绝计划不写入。SQLite 缓存丢失可在下一次检索时重建；旧版无权限来源记录不会直接导入。
RAGFlow 数据不属于项目备份或本地记忆缓存，须由其独立治理。备份仍应以项目状态和媒体为准。

## 视觉质检

生成完成后对图像或视频实际解码；视频在 0%、40%、80% 位置抽取三帧，最长边 512 像素。
本地检查曝光和对比度，默认仅供审核参考；设置 `MEDIAFORGE_VISUAL_GATE=true` 后失败阻断。
低对比度或纯色也可能是创作意图，启用前应以本项目样本校准；这不是语义或身份识别模型。
已生成镜头可在详情页“重新质检”，对应 `POST /projects/{id}/shots/{shot_id}/review-quality`，
需要审核权限。失败转为要求修改，撤销旧导出和交付状态，不能再次批准，须返修再生成。
质检结果、证据哈希、时间与版本进入持久化状态和审计；已发布项目禁止修改质检。

远端评估需同时配置 `MEDIAFORGE_QUALITY_URL` 和显式
`MEDIAFORGE_QUALITY_ALLOW_DATA_EXPORT=true`。v2 请求发送实际 JPEG base64 抽帧、
最多六张项目内参考图、镜头规格和 SHA256，不再发送本地媒体路径。开启即允许向该服务
发送项目画面及参考素材；部署方应确认数据授权。响应必须包含布尔 `passed`，
可带 0..1 的 `score`、`provider` 和 `checks`（每项有布尔 `passed`）；任一检查失败不会
被顶层 true 覆盖。缺字段/坏响应按 `MEDIAFORGE_QUALITY_FAIL_OPEN` 处理；正式门禁建议 false。
未配置模型不会报告已完成语义评估。真实角色一致性和内容安全效果需目标视觉模型现场验收。

审计事件可通过 Webhook 推送到运维系统。多个地址使用逗号分隔，配置密钥后每个请求
会带 `X-MediaForge-Signature: sha256=...`，签名原文为
`timestamp + "." + 原始请求体`：

```text
MEDIAFORGE_WEBHOOK_URLS=https://ops.example.com/mediaforge/events
MEDIAFORGE_WEBHOOK_SECRET=replace-with-webhook-secret
MEDIAFORGE_WEBHOOK_TIMEOUT_SECONDS=5
MEDIAFORGE_WEBHOOK_RETRY_ATTEMPTS=2
MEDIAFORGE_WEBHOOK_RETRY_BACKOFF_SECONDS=0.5
```

前端实时进度使用 `GET /projects/{id}/events`。认证模式下客户端必须携带 Bearer
Token；工作台已使用带认证的 Fetch 流式读取、游标续传和断线重连。事件推送失败不会
回滚已经持久化的项目操作，失败事件会保留在 artifact 根目录的
`webhook-outbox.json`，服务重启后自动重放；失败次数、待投递数和最近错误可通过
`/webhooks/status` 查看。

审计记录采用项目内顺序哈希链：每条新事件承诺前一事件摘要、操作者、动作、消息、
详情、镜头、追踪标识和时间。`GET /projects/{id}/audit` 返回 `integrity`，
`GET /projects/{id}/audit/integrity` 可独立校验，`POST /projects/{id}/audit/export-integrity`
导出 `audit-integrity.json`。首次继续写入旧版未封存项目时会为已有记录补封，并标注
`chain_origin=legacy_resealed`；这只能保证补封后的连续性，不能倒推出旧记录在补封前
未被修改。链校验失败时系统会拒绝继续写入该项目，应从已验证的快照或归档恢复。
从交付包导入新项目时，源项目的审计 JSON 和完整性摘要作为导入证据保留，目标项目则
从 `project.imported_package` 事件开始自己的原生链，不会把不同项目的链伪装为连续。

可选 SIEM 连接复用耐久出站队列，但使用 CloudEvents 1.0。外发载荷仅含项目、动作、
操作者、镜头/追踪标识、哈希链和详情字段名及摘要，不含审计消息文本、详情值、素材或
密钥。配置 URL 时必须同时配置精确主机白名单；生产只允许 HTTPS。Bearer Token 和
HMAC 密钥应放入 Secret，网络策略还必须允许该 SIEM 地址的 TCP 443 出站：

```text
MEDIAFORGE_SIEM_URLS=https://siem.example.com/ingest/mediaforge
MEDIAFORGE_SIEM_ALLOWED_HOSTS=siem.example.com
MEDIAFORGE_SIEM_BEARER_TOKEN=replace-with-siem-token
MEDIAFORGE_SIEM_SIGNING_SECRET=replace-with-siem-signing-secret
MEDIAFORGE_SIEM_SOURCE=urn:mediaforge:production
MEDIAFORGE_SIEM_TIMEOUT_SECONDS=5
MEDIAFORGE_SIEM_RETRY_ATTEMPTS=2
MEDIAFORGE_SIEM_RETRY_BACKOFF_SECONDS=0.5
```

SIEM 失败不会回滚已持久化操作，待投递事件保存在 artifact 根目录的
`siem-outbox.json` 并会在重启时重放。工作台“运行与账单”显示启用状态、端点数量和
待投递数；运行环境可用 `/enterprise/status` 中的 `siem` 字段做监控。

哈希链能发现单条记录被修改，但若攻击者能同时改写全部业务状态和哈希值，仍需要把链头
提交到独立管理的不可变系统。MediaForge 提供可选外部锚定：`POST /projects/{id}/audit/anchor`
只接受已验证且非空的链头，成功后将回执写入原生审计链、`audit-anchors.json`、交付包和
归档包。锚定的是当时的链头快照；后续操作会形成新的链头，应在发布、结项或归档前再次
锚定。`GET /projects/{id}/audit/anchors` 返回本地保存的回执，`/enterprise/status` 的
`audit_anchor` 仅报告配置与就绪状态，不会泄漏公证服务地址或凭据。
每份保存的回执还包含重算 `anchor_hash` 所需的项目、链模式、来源、事件数、封存数、
链头和时间；接口的 `verification` 字段可离线检测本地回执是否被篡改，但不会把“可验证的
回执”误表述为“已在线查询外部存储”。

优先使用已在不同权限域中管理、已启用 Object Lock 的 S3/MinIO bucket。对象写入会附带
`COMPLIANCE`（或明确选择的 `GOVERNANCE`）保留期，并立即读回 retention；任何写入或
读回校验失败都不会生成成功回执。本地文件系统不能作为不可变锚定。该模式复用对象存储
凭据：

```text
MEDIAFORGE_AUDIT_ANCHOR_MODE=object_lock
MEDIAFORGE_AUDIT_ANCHOR_RETENTION_DAYS=3650
MEDIAFORGE_AUDIT_ANCHOR_OBJECT_LOCK_MODE=COMPLIANCE
MEDIAFORGE_AUDIT_ANCHOR_KEY_PREFIX=audit-anchors
```

也可以接入内部或第三方签名公证服务。服务必须返回 JSON，并回显请求中的 `anchor_hash`
及其自己的非空 `receipt_id`；系统会拒绝不匹配或非 2xx 回执。HTTP 锚定要求精确主机
白名单和 HTTPS，Bearer Token、HMAC 密钥应仅通过 Secret 注入：

```text
MEDIAFORGE_AUDIT_ANCHOR_MODE=http
MEDIAFORGE_AUDIT_ANCHOR_URL=https://notary.example.com/v1/mediaforge/anchors
MEDIAFORGE_AUDIT_ANCHOR_ALLOWED_HOSTS=notary.example.com
MEDIAFORGE_AUDIT_ANCHOR_BEARER_TOKEN=replace-with-notary-token
MEDIAFORGE_AUDIT_ANCHOR_SIGNING_SECRET=replace-with-notary-signing-secret
MEDIAFORGE_AUDIT_ANCHOR_TIMEOUT_SECONDS=10
```

签名格式为 `X-MediaForge-Signature: sha256=...`，原文为 `timestamp + "." + 原始 JSON`
请求体。外部锚定所保护的是 `project_id`、链模式、来源、事件数、链头 hash、锚定时间和
锚定 hash，不发送审计消息、详情、素材或密钥。生产上线前要由存储管理员确认 bucket 在
创建时已启用 Object Lock、保留策略不能由应用身份缩短，并完成一次写入、读回和恢复演练。

## 租户配额与协作

可以用 `MEDIAFORGE_TENANT_MAX_PROJECTS`、`MEDIAFORGE_TENANT_MAX_JOBS`、
`MEDIAFORGE_TENANT_BUDGET` 设置默认配额，也可以用
`MEDIAFORGE_TENANT_QUOTAS` 配置租户级 JSON 覆盖。`/tenants/me/quota` 提供当前租户
用量，超限会返回 `429`，避免生成任务继续扩大成本。项目的成员、角色、项目级评论和
镜头级评论通过 `/projects/{id}/collaboration` 与 `/projects/{id}/comments` 管理，
并进入项目状态、快照和审计记录。项目角色会在请求级别执行：查看者只能读，审核者可
评论和审核，编辑者可修改内容，发布者可交付/发布，成员管理仅限项目所有者。
租户成本台账可从 `/tenants/me/cost` 查看，也可通过 `/tenants/me/cost/export?format=json`
或 `format=csv` 下载，方便财务对账和预算审计。

## 权利与模型准入

许可证台账记录支持可选的 `valid_from`、`valid_until`、`rights_uri` 与 `evidence` 字段。
其中 `kind: asset` 的 `match` 必须是上传参考素材或原著文件的 SHA-256。启用
`MEDIAFORGE_REQUIRE_ASSET_RIGHTS_RECORD=true` 后，每项有实体文件的参考素材与原著文件都必须
有处于有效期的资产权利记录；台账中的 `expired`、未生效或撤销记录会使合规和发布门禁失败。
合同正文、供应商版权检索结果和指纹服务仍由外部治理系统保存，MediaForge 只保存可审计链接和
不可变哈希，不上传合同机密。

交付评估页可将一次完整评估固化为模型准入基线。基线记录当前服务商路由、工作流模板、启用
提示词快照及最低模型质量分；`GET/POST /projects/{id}/evaluations/baselines` 可供自动化系统读取
和创建。已激活基线会在每次评估和发布前重新检查，Provider、工作流或提示词变更，以及质量
门禁退化都会阻断发布，直至新的候选通过评估并由质量负责人固化为新基线。

协作在线状态和编辑锁属于短租约协调机制：`POST /projects/{id}/collaboration/presence`
心跳默认有效 90 秒，`POST /projects/{id}/collaboration/locks` 在指定的简报、场次、镜头、
时间线或提示词目标上创建 15--900 秒的软锁。共享制作笔记通过
`PUT /projects/{id}/collaboration/documents/{document_id}` 保存，或用
`POST .../operations` 提交幂等 RGA 操作；`GET .../events?after=` 与
`WS .../events/ws?after=` 提供可补发的事件游标。文档状态和最近 1000 条协作事件会随项目
持久化，断线客户端应从最后游标重放。浏览器 WebSocket 使用 OIDC Cookie，或在连接首帧
提交 API 令牌，令牌不会进入 URL；反向代理场景可通过
`MEDIAFORGE_ALLOWED_WEBSOCKET_ORIGINS` 限制允许的 Studio Origin。

## 内容凭证与 Provider 契约

工作台可以为本地资产创建内容凭证声明，记录资产 SHA-256、项目来源、参考资产许可证、
审计链状态和锚定数量。未配置签名器时，声明的 `c2pa.status` 始终是 `UNSIGNED`，只能作为
待签名证据，不能宣称已嵌入 C2PA 或通过独立验证。配置经过安全评审的本机签名器后，使用
无 shell 的参数模板调用，并要求它产生 `{output}`：

```text
MEDIAFORGE_C2PA_SIGNER_COMMAND=c2patool {input} --manifest {manifest} --output {output}
MEDIAFORGE_C2PA_VERIFIER_COMMAND=c2patool {output} --validate
MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS=120
```

`{input}` 是原始资产，`{manifest}` 是 MediaForge 的可审计声明，`{output}` 是签名器必须
创建的输出路径。命令成功且输出存在时状态为 `SIGNED_UNVERIFIED`。配置
`MEDIAFORGE_C2PA_VERIFIER_COMMAND` 后，工作台或
`POST /projects/{id}/content-credentials/{credential_id}/verify` 会验证声明哈希、输出哈希并运行
独立验证器；只有全部通过才标为 `SIGNED_VERIFIED`。未配置签名器的声明可验证本地来源哈希，
状态为 `INTEGRITY_VERIFIED`，但不代表 C2PA 签名。声明、签名器输出（存在时）、
`content-credentials.json`、交付包和归档包都会一并保存。签名私钥及 HSM/云 KMS 凭据必须只由
签名器运行环境持有，不能传给 API 进程。

`GET /providers/contracts` 执行不产生生成任务的 Provider 协议检查；
`POST /projects/{id}/providers/contracts/validate` 还会验证当前镜头是否能由已启用服务商在
预算范围内路由。该检查不代表目标模型的质量、价格和法律合规验收，生产切换仍应在隔离
账号中进行实测和回调幂等演练。

## 运行模式

- `MEDIAFORGE_PROVIDER=mock`：仅用于流程验收，诊断等级为 `SIMULATION`。
- `MEDIAFORGE_PROVIDER=comfyui`：使用固定工作流和本地服务，诊断需达到 `READY`。
- `MEDIAFORGE_PROVIDER=replicate`：必须设置 Token 和锁定的模型版本。
- `MEDIAFORGE_PROVIDER=local`：本机 GPU 命令 Provider，必须设置
  `MEDIAFORGE_LOCAL_PROVIDER_COMMAND`；命令通过 stdin 接收 JSON，并在
  `MEDIAFORGE_OUTPUT_PATH` 创建可读 PNG/MP4。健康检查和模型预热分别由
  `MEDIAFORGE_LOCAL_PROVIDER_HEALTH_COMMAND` 与
  `MEDIAFORGE_LOCAL_PROVIDER_WARMUP_COMMAND` 配置。

需要同时接入多个后端时使用 `MEDIAFORGE_PROVIDERS`，例如
`MEDIAFORGE_PROVIDERS=comfyui,replicate,mock`。列表顺序决定同能力服务商的
优先级；路由器仍会按能力、预算和 `configured` 状态过滤。生成失败时，同一个 Job
会按剩余优先级切换到符合条件的备用服务商，所有尝试会写入路由、成本和审计记录。
未配置的后端会保留在诊断结果中，但不会领取任务。`/providers/status`、`/providers/health` 和
`/providers/diagnostics` 会返回完整服务商池及逐项健康状态；不设置该变量时继续
使用 `MEDIAFORGE_PROVIDER` 的单服务商兼容模式。

真实 Provider 仍需用目标账号、工作流和模型完成成本、延迟、成功率、内容限制
以及服务端幂等行为验收；平台内的 Mock、HTTP 适配器、失败重试、回调对账、质量
门禁、合规、交付和归档链路已具备本地自动化验证。

口型同步使用 `MEDIAFORGE_LIPSYNC_MODE=command|http`。command 模式将本地视频、
音频和输出路径传给 GPU Worker；http 模式默认拒绝发送媒体，只有显式设置
`MEDIAFORGE_LIPSYNC_ALLOW_DATA_EXPORT=true` 才会发送 base64，远端必须返回
`{output_base64}`。`POST /projects/{id}/lipsync` 成功后会替换项目主样片并刷新交付、
归档和审计状态。

## Worker 控制面

Worker 启动后先调用 `POST /workers/register`，定期调用心跳接口维持 Job 租约，
再通过 `POST /workers/{worker_id}/claim` 按并发容量领取任务。领取的任务必须带相同
`worker_id` 调用处理接口；服务重启后 Worker、Job 租约和完成统计仍从 JSON/SQLite/PostgreSQL
状态恢复。`MEDIAFORGE_JOB_LEASE_SECONDS` 控制租约时长，运维可通过
`POST /jobs/recover-stale` 回收失联 Worker 的任务。
Worker 注册时可声明 `capabilities` 和 `resources`，领取接口会优先匹配生成能力。
工作台显示在线/超时状态、心跳年龄、主机、系统、CPU、并发、活动任务和 GPU 遥测；CLI 自动
通过 `nvidia-smi` 上报 GPU 型号、驱动、总显存、空闲显存和利用率。领取接口的
`minimum_gpu_memory_mib` 会在领取前执行显存准入并返回结构化 admission 结果。目前
`execution_mode=api-provider-dispatch` 仍表示 Worker 通过 API 调用 Provider，GPU 遥测和
准入不等于本机独立 GPU 推理。`execution_mode=local-provider-callback` 则在 Worker 本地执行
Provider；其领取请求携带 Provider 名称，控制面仅返回相同路由 Provider 的任务，所有回调
还绑定 Worker Job 租约。这样同一队列可混合不同模型 Worker，而不会让错误模型领取任务。
Worker 注册、查看、心跳和领取按租户隔离。使用 `provider` 角色令牌执行任务时必须
带本租户的 `worker_id`，任务必须已被该 Worker 领取且租约未过期，不需要授予管理员
或项目编辑权限。Worker 不能借处理任务接口修改项目计划。
资产下载使用 `/projects/{id}/assets/{asset_id}/download`，服务端不会接受任意磁盘路径，
只允许项目资产清单中的对象，并返回 `X-MediaForge-SHA256` 供下游校验。

## 验证边界

企业适配器真实服务验收使用独立的临时 PostgreSQL、Redis 和 MinIO，仅绑定本机端口：

```powershell
docker compose -p mediaforge-acceptance -f docker-compose.enterprise-test.yml up -d --wait
$env:MEDIAFORGE_TEST_POSTGRES_URL='postgresql://mediaforge_test:local-test-only@127.0.0.1:15492/mediaforge_test'
$env:MEDIAFORGE_TEST_REDIS_URL='redis://127.0.0.1:16392/0'
$env:MEDIAFORGE_TEST_S3_ENDPOINT='http://127.0.0.1:19092'
python -m pytest -q tests/test_enterprise.py tests/test_enterprise_integration.py
docker compose -p mediaforge-acceptance -f docker-compose.enterprise-test.yml down
```

这些凭证仅供本地临时验收，不能用于生产。测试为每次执行创建独立表、队列和桶，
结束时清理；未设置测试连接变量时，真实服务测试会明确跳过，而不是伪装通过。
联合验收覆盖任务入队、HTTP Worker、PostgreSQL 重启恢复、MinIO 交付和确认回执，
但媒体生成使用 Mock Provider，不代表真实商业模型的质量和成本验收。

建议在隔离虚拟环境或容器内安装依赖并运行测试，避免共享 Python 环境中的第三方
依赖冲突。生产镜像使用 `imageio-ffmpeg` 包内的 FFmpeg，健康检查使用 Python 标准库。
当前自动化验收记录见 `enterprise-acceptance.md`。`/ops/readiness` 的 `ready` 表示当前
运行模式的闭环可用；商业发布须同时检查 `production_ready`，Mock 模式该字段为 false。

本地自动化闭环已覆盖 Mock Provider、LLM 规划器 HTTP 契约、Webhook HMAC 签名、认证
SSE、项目/镜头编辑、任务租约、质量门禁、交付、归档和恢复。真实 ComfyUI、云视频
Provider、模型服务和集中式许可证服务仍必须使用目标环境凭证完成一次现场验收，尤其是
成本、延迟、限流、服务端幂等和内容安全策略。

## 备份与恢复演练

生产恢复单元由三部分组成：PostgreSQL 状态、共享 artifact 卷，以及已配置 S3/MinIO
中的交付/归档对象。`ops/backup/create-postgres-backup.sh` 只处理前两项：它以 custom
format 导出 PostgreSQL、打包 artifact 根目录，并写入逐文件 SHA-256 清单。为避免数据库
状态和媒体文件跨时点混合，脚本拒绝在线运行；必须先停止所有 API 与 Worker 写入，显式设置
`MEDIAFORGE_BACKUP_QUIESCED=true`，再执行。

```sh
export MEDIAFORGE_DATABASE_URL='postgresql://...'
export MEDIAFORGE_ARTIFACT_ROOT=/srv/mediaforge/artifacts
export MEDIAFORGE_BACKUP_ROOT=/srv/mediaforge-backups
export MEDIAFORGE_BACKUP_QUIESCED=true
sh ops/backup/create-postgres-backup.sh
mediaforge-backup verify --bundle /srv/mediaforge-backups/mediaforge-backup-YYYYMMDDTHHMMSSZ
mediaforge-backup report --bundle /srv/mediaforge-backups/mediaforge-backup-YYYYMMDDTHHMMSSZ --max-age-hours 24
```

`report` 会先完成完整性校验，再以指定的最大备份年龄判断 RPO；超出阈值时仍输出 JSON，
但返回非零状态，适合接入计划任务或监控。

恢复演练必须在隔离环境执行。`ops/backup/restore-drill.sh` 会在恢复前校验备份、新鲜度、
确认短语、目标数据库名和空 artifact 目录，并拒绝名称不包含 `drill`、`restore` 或
`recovery` 的数据库：

```sh
export MEDIAFORGE_BACKUP_BUNDLE=/srv/mediaforge-backups/mediaforge-backup-YYYYMMDDTHHMMSSZ
export MEDIAFORGE_DRILL_DATABASE_URL='postgresql://mediaforge:password@drill-db:5432/mediaforge_recovery_drill'
export MEDIAFORGE_DRILL_DATABASE_NAME=mediaforge_recovery_drill
export MEDIAFORGE_DRILL_ARTIFACT_ROOT=/srv/mediaforge-drill/artifacts
export MEDIAFORGE_DRILL_ACKNOWLEDGEMENT=ISOLATED-RECOVERY-DRILL
sh ops/backup/restore-drill.sh
```

配置相同或经过明确迁移的 Provider、密钥与对象存储；最后以
`mediaforge-production-acceptance`、抽样项目的 artifact SHA-256 和一次审批/交付读操作验收。
S3/MinIO 对象不应只依赖本地 tar 包，应启用其版本化、跨域复制或平台快照，并将其恢复点与
 PostgreSQL 备份记录在同一演练工单中。不要把恢复命令直接指向现网数据库或现网共享卷。

## 容量与稳定性验收

`ops/load/studio-readonly.mjs` 是一个只读 k6 基线，覆盖 `/livez`、`/health`、
Provider 诊断、生产就绪度和可选的已批准项目读取。它不创建项目、不提交生成任务，也不触发
模型成本。应在拓扑等同于生产的预发布环境执行，而不是将压测直接对准本地 Mock：

```sh
export MEDIAFORGE_LOAD_BASE_URL=https://staging.mediaforge.example
export MEDIAFORGE_LOAD_TOKEN='secret-readiness-token'
export MEDIAFORGE_LOAD_PROJECT_ID=approved-staging-project
k6 run ops/load/studio-readonly.mjs
```

默认以 10 VU 保持两分钟并要求成功率大于 99%、P95 小于 1000 ms。实际发布应为每个
部署规格记录批准的并发、错误率、P95/P99、数据库/Redis 连接池、Worker/GPU 饱和度与
Provider 429 比例；这些目标由业务 SLO 决定，不能由仓库中的默认阈值替代。
