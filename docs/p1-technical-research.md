# P-1 技术预研记录

## 当前结论

截至 2026 年 9 月 12 日，MediaForge 的最短端到端链路已经用 Mock Provider 跑通，
并补齐了本地生产交付、运行就绪度和外部质量评估适配入口：

```text
GenerationSpec
  -> Job 状态机
  -> Mock 视频工件
  -> 媒体质量探针
  -> FFmpeg 拼接
  -> manifest / Trace 记录
```

当前结论是 **本地生产闭环通过，真实 Provider 仍待验证**。因此可以进入现场联调，
但不能把真实视频生成能力标记为已经验收。

## 已验证项

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| Python 运行时 | 通过 | Python 3.12.4 |
| Pydantic 契约 | 通过 | `GenerationSpec`、`ShotCard` 和 Brief 校验 |
| Job 幂等与租约恢复 | 通过 | 相同幂等键返回同一个 Job；服务重启或运维接口可恢复超时的 `ADMITTED/RUNNING` 任务 |
| Job 状态保护 | 通过 | 非法状态转换会抛出 `InvalidTransition` |
| Provider 路由 | 通过 | vertical slice 实际经过 Router，按能力、预算、优先级选择 Provider，超预算会阻断；Mock、ComfyUI、Replicate 配置状态可查询 |
| Replicate 请求可靠性 | 通过 | 预测创建使用稳定的 `Idempotency-Key`，瞬时 HTTP/网络错误按边界重试；非幂等 `POST` 不自动重放，策略在 Provider 状态和工件 sidecar 中可审计 |
| 字幕轨导出 | 通过 | 按已通过镜头的时间轴生成 UTF-8 SRT，项目清单、交付包和归档包均携带字幕文件 |
| 音频轨导入与混音 | 通过 | 支持常见音频格式、时长探针、许可证/来源登记、SHA-256 和发布锁定；导出时自动循环或截断并纳入交付包与归档包 |
| 连续性审查 | 通过 | 对镜头顺序、角色来源、场景字段、时间轴、Brief 目标时长和字幕覆盖执行确定性检查；报告可导出，并作为评估、发布、验收和归档证据 |
| P0 应用 API | 通过 | 工作台总览、运营指标、项目列表、项目恢复、归档/恢复、计划、单镜头提交、批量提交、入队执行、项目队列 Drain、全局队列 Drain、Worker CLI、Worker 注册/心跳/租约领取、审核、批量审批、返工、失败重试、Job 取消、异步 Provider 回调幂等、运营摘要、生产报告、项目/Job/资产 Trace 查询与导出、项目复盘查询与导出、连续性审查查询与导出、Provider 路由预览、镜头级 A/B 候选、候选提升、镜头筛选、Job 筛选、策略查询、许可证/来源合规报告、资产清单、审计 JSON/CSV 导出、评估报告、Provider 基准、项目快照、复制/导入分支、导出、交付打包、交付包验收、发布门禁、分发回执/分发确认/分发报告、验收证书、验收证书导出、项目封档、成本台账和审计查询已串成 FastAPI 闭环 |
| Mock 视频生成 | 通过 | P-1 生成 3 个 1 秒 MP4 片段；P0 支持 30/45/60 秒 Brief 的 5 秒粒度镜头规划 |
| 媒体质量探针 | 通过 | 图片/视频均可解码，质量报告包含 decode、format 或 duration、resolution 检查 |
| FFmpeg | 通过 | 使用 `imageio-ffmpeg` bundled binary |
| 视频拼接 | 通过 | P0 导出最终 MP4，30 秒、640x360 |
| 内容安全策略 | 通过 | 本地策略可阻断提示注入/绕过审批文本，并记录非阻断警告 |
| 自动化测试 | 通过 | `117 passed`（Linux 容器，含真实 PostgreSQL、Redis、MinIO 联合验收） |
| OpenAI-compatible 规划器 | 通过 | 本地 HTTP 服务验证结构化请求、JSON fenced response 和 ShotCard 解析 |
| Webhook 事件通知 | 通过 | 本地 HTTP 服务验证事件封装、异步投递、HMAC-SHA256 签名和持久化 outbox 恢复 |
| 认证 SSE 实时进度 | 通过 | Bearer Token + Fetch 流式读取 + 游标续传 + 断线重连 |
| 项目/镜头在线编辑 | 通过 | API 与工作台编辑入口验证，镜头 revision 和审计事件持久化 |
| 多服务商路由池 | 通过 | `MEDIAFORGE_PROVIDERS` 按优先级注册多个后端，逐项状态/健康诊断和回调安全可用 |
| 服务商故障转移 | 通过 | 同一个 Job 在首选服务商失败后切换剩余候选，记录尝试链和累计估算成本 |
| 生产交付派发 | 通过 | 已验证本地文件派发、ZIP 哈希/大小、派发清单和失败记录；HTTP(S) 适配器支持重试与可选 HMAC |
| 外部质量评估适配 | 通过 | 默认禁止数据外发；显式配置 URL 与导出许可后验证请求、响应合并和 fail-open/fail-closed |
| 生产就绪度与部署入口 | 通过 | `/ops/readiness`、`mediaforge-readiness` CLI、Compose Worker profile 和部署检查已验证 |

## 产物

运行：

```powershell
python -m mediaforge_p1 --output artifacts/p1-probe
```

会生成：

- `artifacts/p1-probe/manifest.json`
- `artifacts/p1-probe/final_sample.mp4`
- `artifacts/p1-probe/shots/*.mp4`
- `artifacts/p1-probe/shots/*.json`

## 尚未验证项

1. ComfyUI 服务是否能通过固定工作流稳定返回图片和运行元数据。
2. 真实图生视频 Provider 的成本、成功率、时延和内容限制。
3. 真实 Provider 是否按其服务端文档兑现幂等语义；平台已为 Replicate 请求发送稳定的 `Idempotency-Key` 并完成本地重试契约测试，但仍需用真实账号验证超时后重复提交时的服务端行为。
4. 真实参考图片上传到视频 Provider 的模型专用输入格式；平台已经支持参考图登记、哈希、许可证、镜头规格传播和交付归档，ComfyUI 本地参考图自动上传，Replicate 小文件自动转换 Data URL，较大文件和模型专用字段仍需自定义 `input_builder` 或托管 URL。
5. 真实集中式许可证服务的权限、可用性、变更通知和商业条款仍需现场核验；平台侧同步机制已支持按需拉取、条件请求、主机白名单、Token、哈希留痕和失败保护。

## P-1 结论

基础设施层面的最短链路可行，当前主要未知数已经收敛到真实媒体 Provider、集中式许可证服务、
真实交付接收端和外部质量服务的现场条件。项目内已经提供可审计的本地许可证登记表与同步入口，
发布门禁会核验参考资产、Provider、工作流和 LoRA；交付派发和质量评估默认保持本地/不外发，
只有显式配置现场目标和数据导出许可才会调用外部系统。下一步应接入一个真实视频 Provider，
并用同一批 ShotCard 对比 Mock 和真实 Provider 的成本、时延、成功率与输出质量。

真实 Provider 运行入口已经提供：

- `python -m mediaforge_p1.provider_probe --provider comfyui --workflow <workflow.json>`
- `python -m mediaforge_p1.provider_probe --provider replicate`

当前本机检查结果：`127.0.0.1:8188` 没有运行 ComfyUI，环境中也没有配置云端 API Token，因此真实服务调用尚未执行。

P0 API 入口见 `src/mediaforge_p1/api.py`，默认使用 Mock Provider 和磁盘工件目录，完整验证仍需将 Provider 替换为真实服务并运行同一组流程。

## API 级闭环验证

本机 MediaForge API 已在 `http://127.0.0.1:8020` 启动并完成一次真实 HTTP 流程：

```text
创建项目
  -> 生成计划
  -> 第 1 个镜头提交
  -> 第 1 个镜头审核退回
  -> 返工并重跑
  -> 批量提交剩余 5 个镜头
  -> 批量审批待审镜头
  -> 导出样片
  -> 打包交付 ZIP
  -> 交付包验收
  -> Provider 基准 / 发布评估
  -> 许可证/来源合规检查
  -> 故事与时间轴连续性检查
  -> 真实交付派发
 -> 分发回执
  -> 分发确认
  -> 项目封档
  -> 交付包导入复验
```

验证结果：

- 项目状态：`EXPORTED`
- 最终视频：30 秒、640x360
- 审核退回和返工重跑：通过
- 批量生成：5 个镜头
- 批量审批：5 个镜头
- 导出产物：`artifacts/api/smoke_callback_security_20260909/final_sample.mp4`
- 项目清单：`artifacts/api/smoke_callback_security_20260909/project-manifest.json`
- 交付包：`artifacts/api/smoke_callback_security_20260909/smoke_callback_security_20260909-delivery.zip`，包含样片、项目清单、镜头资产、Provider 元数据、资产清单、策略报告、合规报告、连续性报告、分发报告、评估报告、Provenance 报告、Provider 基准、路由预览、A/B 对比报告、生产报告、审计日志、发布记录和交付摘要

最新干净验证确认返工后的 Job 和 Artifact 都发生变化：

- 项目：`smoke_callback_security_20260909`
- 状态：`EXPORTED`
- 返工后 Job 变化：通过
- 返工后 Artifact 变化：通过
- Artifact 历史记录数：2
- 批量生成数：5
- 批量审批数：5
- Provider 路由预览导出：通过
- 镜头级 A/B 候选生成、推荐和候选提升：通过
- 全局队列 Drain：通过，最终剩余队列数 0
- Worker CLI：通过，可读取本地状态并 drain 队列
- 交付包文件数：38
- 交付包验收：通过，`delivery-verification.json` 已生成
- 交付包导入复验：通过，`smoke_callback_security_20260909_imported` 导入后 `delivery_verified=true`，发布记录和分发历史已重置
- Provenance / chain-of-custody 报告：通过，包含 6 个镜头和 9 个 Artifact 的逐镜头 lineage
- 许可证/来源合规报告：通过，11 项检查覆盖参考素材授权、音频轨许可证、生成资产哈希、资产文件存在、工件元数据、Provider 归因、Provider 配置、工作流白名单、LoRA 白名单、许可证台账和安全策略
- 故事与时间轴连续性报告：通过，镜头顺序、角色/场景连续性、目标时长和字幕覆盖均通过，并纳入评估、发布、验收和归档门禁
- 分发回执与确认：通过，`smoke-distribution` 渠道生成 receipt，随后完成确认并写入 `distribution-report.json`
- 生产交付派发：通过，调用 `/projects/{id}/deliveries/dispatch` 复制 ZIP 并生成派发清单，派发结果写入 delivery receipt
- 项目封档：通过，`closeout-report.json` 已生成，项目已归档
- 验收证书：通过，`acceptance-report.json` 已生成，可独立下载与复核
- 最终归档包：通过，封档后可生成 `*-archive.zip` 并输出 `archive-verification.json`
- 发布评估：100/100，通过
- Provider 基准推荐：`mock-provider`
- 快照与审计 CSV：通过
- 生产报告导出：通过，包含路由预览和 A/B 对比摘要
- 字幕轨导出：通过，最终导出生成 `final-subtitles.srt`，并纳入交付与归档校验
- 音频轨导入与混音：通过，音频探针、许可证/来源元数据、循环混音、交付与归档校验均已覆盖
- 工作室运营指标：通过，统一输出项目、镜头、任务、队列等待、执行时延、预算、单位成本、质量和分发指标
- 许可证台账运行时校验/导入/导出：通过，合法版本可持久化跨重启使用，非法记录不会替换当前台账
- Provider 回调安全：通过，HMAC-SHA256 覆盖时间戳、方法、路径和原始请求体；路径篡改、过期请求和未签名生产请求均被阻断
- 集中式许可证台账同步：通过，支持主机白名单、Bearer Token、ETag/Last-Modified 条件请求、内容哈希、坏数据失败保护和后台定时同步
- 生产基础设施：通过，支持可选 Bearer 鉴权、角色/租户隔离、SQLite 状态后端、Prometheus HTTP 指标和前端令牌登录
- 复制分支：`smoke_callback_security_20260909_branch`，6 个镜头模板
- 分支队列入队 / Drain：通过
- 项目列表、工作台总览、资产清单、Job 摘要、成本台账、策略报告、评估报告、Provider 基准、项目快照、复制分支、导入模板、审计 JSON/CSV 导出、审计轨迹、发布记录和交付包治理摘要：通过
- 项目归档/恢复、归档后写操作阻断、非终态 Job 取消：通过
- 项目预算守门：通过，超预算生成请求返回 422

可复现命令：

```powershell
python -m mediaforge_p1.api_smoke --base-url http://127.0.0.1:8020
```

## Provider 配置化验证

API 已支持通过环境变量切换 Provider：

- `MEDIAFORGE_PROVIDER=mock`
- `MEDIAFORGE_PROVIDER=comfyui`
- `MEDIAFORGE_PROVIDER=replicate`

多服务商路由池：

- `MEDIAFORGE_PROVIDERS=comfyui,replicate,mock`
- 列表顺序决定同能力服务商优先级，未配置后端保留在诊断结果但不会执行任务
- `/providers/status`、`/providers/health`、`/providers/diagnostics` 返回完整服务商池

状态接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8020/providers/status
Invoke-RestMethod http://127.0.0.1:8020/providers/health
```

当前验证结果：

- 默认 Provider：`mock-provider`
- Provider 状态接口：通过
- Provider 运行态健康检查：通过，未配置时不发起外部请求，已配置时返回可达性、延迟和探测端点
- Provider 失败退避重试：通过，持久化最后错误、指数退避、最大尝试次数，到期后由队列/Worker 自动恢复
- ComfyUI 配置工厂：通过，可报告工作流缺失、JSON 错误和运行参数错误
- Replicate 缺少 Token / 模型版本时：服务可启动，生成任务会被 422 清晰阻断
- 最新自动化测试：`117 passed`，其中企业验收覆盖真实 PostgreSQL、Redis、MinIO，以及使用普通 Provider 令牌的 Mock 生成、交付和确认闭环。验收详情见 `enterprise-acceptance.md`。
- 最新 `8020` live smoke 项目：`smoke_callback_security_20260909`，使用 HMAC-SHA256 签名回调，包含失败重试与重复事件幂等验证

新增管理接口：

```text
GET /projects?include_archived=
POST /projects/import
POST /projects/import-package
GET /studio/overview?include_archived=
GET /studio/metrics?include_archived=
GET /metrics/runtime
GET /delivery/status
GET /quality/status
GET /ops/readiness
GET /studio/metrics/export?format=json|csv&include_archived=
GET /providers/callback-security
GET /auth/status
GET /auth/me
GET /tenants/me/quota
GET /tenants/me/cost?include_archived=
GET /tenants/me/cost/export?format=json|csv&include_archived=
GET /tenants/quota/status
GET /metrics
GET /governance/license-registry
POST /governance/license-registry/validate
POST /governance/license-registry/import
POST /governance/license-registry/sync
GET /governance/license-registry/sync/status
GET /governance/license-registry/export
POST /projects/{id}/jobs/{job_id}/callback
POST /projects/{id}/clone
GET /projects/{id}/collaboration
POST /projects/{id}/collaboration/members
DELETE /projects/{id}/collaboration/members/{subject}
GET /projects/{id}/comments
POST /projects/{id}/comments
DELETE /projects/{id}/comments/{comment_id}
POST /projects/{id}/snapshot/export
POST /projects/{id}/archive
POST /projects/{id}/restore
GET /projects/{id}/operations
GET /projects/{id}/reports/production
POST /projects/{id}/reports/export
GET /projects/{id}/provenance
POST /projects/{id}/provenance/export
GET /projects/{id}/compliance
POST /projects/{id}/compliance/export
GET /projects/{id}/continuity
POST /projects/{id}/continuity/export
GET /projects/{id}/distribution
POST /projects/{id}/distribution/export
POST /projects/{id}/deliveries/{delivery_id}/acknowledge
POST /projects/{id}/acceptance/export
GET /projects/{id}/acceptance
POST /projects/{id}/closeout
POST /projects/{id}/archive-package
POST /projects/{id}/archive-package/verify
GET /projects/{id}/routes
GET /projects/{id}/shots?review_status=&query=&limit=
GET /projects/{id}/cost
GET /projects/{id}/policy
GET /projects/{id}/assets
GET /projects/{id}/jobs?status=&shot_id=&limit=
GET /projects/{id}/audit
POST /projects/{id}/audit/export
POST /projects/{id}/audit/export-csv
GET /projects/{id}/evaluations/latest
POST /projects/{id}/evaluations/run
GET /projects/{id}/providers/benchmark
POST /projects/{id}/providers/benchmark
POST /projects/{id}/shots/{shot_id}/retry
POST /projects/{id}/shots/{shot_id}/retry/schedule
POST /projects/{id}/shots/{shot_id}/enqueue
GET /projects/{id}/shots/{shot_id}/route
POST /projects/{id}/shots/{shot_id}/route/export
GET /projects/{id}/shots/{shot_id}/variants/comparison
POST /projects/{id}/shots/{shot_id}/variants/compare
POST /projects/{id}/shots/{shot_id}/variants/export
POST /projects/{id}/shots/{shot_id}/variants/{variant_id}/promote
POST /projects/{id}/shots/enqueue-all
POST /projects/{id}/jobs/{job_id}/process
POST /projects/{id}/jobs/recover-stale
POST /projects/{id}/queue/drain
POST /queue/drain-all
POST /jobs/recover-stale
POST /projects/{id}/jobs/{job_id}/cancel
POST /projects/{id}/package/verify
POST /projects/{id}/release
POST /projects/{id}/deliveries
POST /projects/{id}/deliveries/dispatch
```

Studio 页面已支持从项目列表恢复项目，并展示工作台总览、运营下一步、镜头筛选、资产清单、Job 队列、批量入队、项目队列 Drain、全局队列 Drain、单 Job 处理、失败重试、Job 取消、项目归档/恢复、策略门禁、许可证/来源合规门禁、连续性审查、评估分数、Provider 基准、路由预览、镜头级 A/B 候选、候选提升、项目复制分支、快照导出、生产报告导出、项目/Job/资产 Trace、Provenance / chain-of-custody 报告、交付包验收、分发回执/分发报告/分发确认、验收证书、项目封档、预算、已花费、Job 数量、审计 JSON/CSV 导出、发布和审计事件。

Worker CLI 入口：

```powershell
python -m mediaforge_p1.worker --output-root artifacts/api --limit 50
```

注意：如果浏览器打开 `127.0.0.1:8010` 仍显示旧的 Mini RAG 页面，后端检查显示这是浏览器缓存或 service worker 接管旧端口导致。MediaForge 已改用干净端口 `http://127.0.0.1:8020/` 进行人工查看和后续联调。
