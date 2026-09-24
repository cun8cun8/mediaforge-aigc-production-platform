# Temporal 编排

MediaForge 已提供一个可选的 Temporal 边界，用于承载耗时、可重试、需要
恢复的生产操作。它不是第二套业务状态机：项目状态、审计链、租约、预算、
质量门禁和交付证据仍然由 MediaForge API 负责。

## 组成

```text
Studio/API -> Temporal Server -> MediaForge Temporal Worker -> MediaForge API
                                      |
                                      +-- generation: submit shot
                                      +-- render: export project
                                      +-- package: build delivery package
                                      +-- dispatch: send delivery
```

API 只提交 Workflow。Worker 注册一个确定性的 Workflow 和一个通用
`mediaforge.execute_operation` Activity，Activity 通过控制面 API 调用现有
业务服务。这样 Temporal 的重试不会绕开现有权限、租约和审计逻辑。

Worker 启动后会立即、并按 `MEDIAFORGE_TEMPORAL_WORKER_HEARTBEAT_SECONDS`
向控制面登记。控制面按 Worker API key 的租户保存队列、命名空间、版本、活动
Activity 数和累计成功/失败数；超过
`MEDIAFORGE_TEMPORAL_WORKER_STALE_AFTER_SECONDS` 未上报即标为超时。Studio 的
“编排”页和 `GET /orchestration/temporal/workers` 都展示该状态。这个心跳接口是
`orchestrator` 身份唯一额外允许的非项目路径，不能用于读取或修改其他控制面资源。

交付 Activity 还会以 `temporal-{request_id}` 发送 `Idempotency-Key`。服务端
会复用同一项目下已记录的交付；HTTP 交付目标同时收到标准
`Idempotency-Key` 和 `X-MediaForge-Idempotency-Key`，应按该键实现去重。

## 本地启动

```powershell
pip install -e ".[dev,temporal]"
$env:MEDIAFORGE_TEMPORAL_ENABLED = "true"
$env:MEDIAFORGE_TEMPORAL_ADDRESS = "localhost:7233"
$env:MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL = "http://127.0.0.1:8020"
$env:MEDIAFORGE_TEMPORAL_WORKER_ID = "temporal-dev-1"
$env:MEDIAFORGE_TEMPORAL_WORKER_HEARTBEAT_SECONDS = "20"
$env:MEDIAFORGE_TEMPORAL_WORKER_STALE_AFTER_SECONDS = "75"
$env:MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_RETENTION_SECONDS = "604800"
$env:MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_MAX_PER_TENANT = "500"
temporal server start-dev
mediaforge-temporal-worker
```

然后提交一个稳定请求：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8020/projects/demo/orchestration/temporal `
  -ContentType 'application/json' `
  -Body '{"operation":"render","request_id":"render-v1","actor":"studio-user"}'
```

`request_id` 参与 Workflow ID：
`mediaforge:{project_id}:{operation}:{request_id}`。客户端超时后可以安全重试同一个
请求，Temporal 会返回 `already_started=true`，而不是创建第二个 Workflow。MediaForge
会拒绝复用一个已经关闭的 Workflow ID，因此该键在其保留期内也是一次性业务请求键。

## 生产边界

- Temporal API/Worker 仅传递项目 ID、镜头 ID、交付字段和版本元数据，不传递密钥或原始媒体。
- Worker 需要一个最小权限的 MediaForge API 身份；推荐通过 secret file 注入 bearer token。
  使用 `role=orchestrator` 并显式设置 `tenant_id`。该身份只能调用生成提交、导出、
  打包和分发四条 Activity 路径，以及自己的 Worker 心跳，不能访问 Studio 或任意项目管理接口。
- 监控应告警 `mediaforge_temporal_workers{status="STALE"} > 0`，并同时观察
  `mediaforge_temporal_worker_active_operations` 与
  `mediaforge_temporal_worker_oldest_heartbeat_seconds`。这些指标不带 Worker ID，避免
  因 Pod 更替产生高基数标签。
- 当 `mediaforge_temporal_configured > 0` 但没有 `ONLINE` Worker 时，应在三分钟后
  告警。Worker 登记按租户保留七天（可通过
  `MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_RETENTION_SECONDS` 调整），并由
  `MEDIAFORGE_TEMPORAL_WORKER_REGISTRY_MAX_PER_TENANT` 限制总量。
- Temporal Server/Cloud 的 TLS、命名空间、保留期、可用区和备份由部署侧负责。
- 先在 staging 使用 `POST /orchestration/temporal/probe`、真实 Worker 和故障恢复演练，再切换生产流量。
- 未启用 Temporal 时，原生 lease Worker 仍是完整可用的执行路径。
