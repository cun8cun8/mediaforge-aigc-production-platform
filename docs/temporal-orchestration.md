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

交付 Activity 还会以 `temporal-{request_id}` 发送 `Idempotency-Key`。服务端
会复用同一项目下已记录的交付；HTTP 交付目标同时收到标准
`Idempotency-Key` 和 `X-MediaForge-Idempotency-Key`，应按该键实现去重。

## 本地启动

```powershell
pip install -e ".[dev,temporal]"
$env:MEDIAFORGE_TEMPORAL_ENABLED = "true"
$env:MEDIAFORGE_TEMPORAL_ADDRESS = "localhost:7233"
$env:MEDIAFORGE_TEMPORAL_CONTROL_PLANE_URL = "http://127.0.0.1:8020"
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
- Temporal Server/Cloud 的 TLS、命名空间、保留期、可用区和备份由部署侧负责。
- 先在 staging 使用 `POST /orchestration/temporal/probe`、真实 Worker 和故障恢复演练，再切换生产流量。
- 未启用 Temporal 时，原生 lease Worker 仍是完整可用的执行路径。
