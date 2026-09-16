# 企业适配验收

## 2026-09-15 对白、GPU 准入与企业闭环

本轮补齐多角色对白时间轴、浏览器企业登录和结算对账入口。对白逐句绑定镜头、角色、
起止时间、音色和语言，逐句合成失败时原子保留旧音轨，过期时间轴会阻断导出，并生成
带角色前缀的 SRT。Worker CLI 通过 `nvidia-smi` 上报 GPU 遥测，领取接口支持
`minimum_gpu_memory_mib` 显存准入；当前执行模式仍是 API 服务商调度。

OIDC 浏览器入口采用授权码 + PKCE，服务只在内存保留会话并使用 HttpOnly Cookie；
外部支付或财务系统可通过 `POST /billing/settlements` 写入按租户幂等的回执，工作台
展示汇总和最近记录。平台不保存支付凭据、不直接收单或开票。新增 Stripe payout
原生 Webhook 入口，使用 Stripe-Signature 原始体校验、租户元数据和最小货币单位转换。

- 企业专项测试：`4 passed`，覆盖对白时间轴相关回归、结算租户隔离/幂等、Stripe 原生
  payout 签名和 OIDC PKCE 会话。
- 企业 API 回归：`80 passed`；JavaScript 语法、Python 编译检查通过。
- 本机 Worker GPU 探测已读取 RTX 3050 Ti，显存总量 4096 MiB、空闲约 3962 MiB；
  该结果只证明当前机器遥测链路可用，不代表目标生产 GPU 推理质量。

### 当前边界

真实 OIDC 身份平台、支付渠道、税务系统和 GPU 本地推理 Provider 仍需部署方凭证与目标
环境验收；现有实现提供协议、会话、审计、幂等和准入闭环，不把 API 调度冒充本机推理。

## 2026-09-14 可恢复分步规划验收

新增真实 LangGraph 五阶段规划：检索、故事设定、分镜、参数编译、人工审核。
SQLite Checkpoint 保存阶段结果，独立草稿只有经人工确认后才应用到项目；失败可恢复，
分镜或策略失败可带审核意见定向返工，最多三轮。中文工作台提供草稿、参数、轨迹、修订、
审批与取消入口，主看板同步草稿状态，审批后进入原有镜头生成与交付流程。
部署与复现命令见 [可恢复分步规划](staged-planning.md)。

- 最终生产安装包 Linux 全量回归：`177 passed, 2 skipped, 9 warnings`，245.92 秒。
  报告 `artifacts/planning-linux-regression.xml`。数据库、队列、对象存储和向量后端分别使用
  真实 PostgreSQL 16、Redis 7.4、MinIO、pgvector 0.8.6；仅两项 Playwright 用例按环境跳过。
- Windows 最终规划回归：`15 passed, 1 warning`，34.59 秒，报告
  `artifacts/planning-final-acceptance.xml`，包含真实子进程强制终止、锁释放、断点恢复、
  HTTP 模型协议和浏览器失败返工。已完成的故事阶段在恢复或分镜修订时不会重新调用模型。
- Linux 跳过的两项浏览器用例在 Windows 单独联合复验：`2 passed, 1 warning`，24.50 秒，
  报告 `artifacts/planning-browser-acceptance.xml`。覆盖外部规划失败修订和真实 pgvector 页面。
- 较早 Windows 全量运行结果为 `178 passed, 1 failed, 8 warnings`，722.55 秒，保留于
  `artifacts/planning-full-regression.xml`。唯一失败是页面测试在审批后的刷新结束前断言按钮状态；
  改为等待按钮恢复后，上述 15 项规划回归全部通过，不将旧失败报告改写为通过。
- 规划、记忆与生产功能的中间联合回归 `41 passed, 1 warning`，96.10 秒，
  `artifacts/planning-acceptance.xml`。最后的主看板状态修订由后续浏览器与安装包验收覆盖。
- 工作台、确定性分步规划、外部规划 fixture 和真实 pgvector 页面均完成 Playwright 验证，
  覆盖 1440、768、390 像素，无页面脚本错误或横向溢出；报告分别为
  `artifacts/ui-acceptance/report.json`、`planning-report.json`、`planning-external-report.json`、
  `semantic-report.json`，截图同目录。浏览器为独立 Chromium，不是连接到用户已打开的浏览器。
- 最终镜像 `mediaforge-planning-20260914:local` 构建通过；在 `/tmp` 导入安装包，
  验证 142 个路由、包内静态资源/SQL、草稿、重启、人工确认和实际 Mock 媒体生成。
  可复现脚本 `tests/planning_image_smoke.py`；Compose 配置、JavaScript 语法与 `pip check` 通过。
- 9 项 Linux 警告包含 AnyIO/Pillow 弃用提示及只读测试挂载导致的 pytest 缓存写入警告。
  Docker 的 `MEDIAFORGE_AUTH_MODE` ENV 警告不代表镜像包含凭据，该值仅为认证模式。

### 当前边界

本地预览 `http://127.0.0.1:8023/` 保留既有数据，启用分步规划；Provider 为 Mock，
故事为本地确定性模式，检索为 SQLite。外部模型协议测试使用本地 fixture，不调用收费服务。
本轮没有真实 LLM 叙事、中文嵌入召回、视觉角色一致性或视频 Provider 的目标质量验收。

Checkpoint 仍限定单控制面本地磁盘，不能据此声称完成分布式 Agent/GPU 图执行。
在本节对应的 2026-09-14 规划验收时，独立 GPU 执行、模型预热、浏览器企业单点登录和
支付结算尚未接入；这些能力已在本文顶部的 2026-09-15 增量中补齐协议与验收入口。口型
同步、多控制面高可用和后期集群能力仍属于后续规模化边界。

### 完成检查

真实服务使用独立 Compose 项目 `mediaforge-planning-acceptance-20260914`，数据目录为 tmpfs。
测试结束后移除该项目容器与网络，不涉及其他业务容器；保留工作台预览和验收产物。

## 2026-09-14 语义检索增量验收

已新增可配置的 PostgreSQL/pgvector 后端和 TEI 嵌入接口，保留默认 SQLite 关键词检索。
包含工作区/租户/项目行级权限、普通数据库账号限制、模型空间隔离、可配置分片、
事务替换、并发更新保护、检索故障阻断、计划引用指纹及中文诊断入口。
部署方法见 [语义检索部署](semantic-memory.md)。

- 全量回归：`159 passed, 4 skipped, 9 warnings`，535.05 秒。
  4 项企业真实服务测试在该次执行未配置外部连接，后续单独联合验收通过。
- 最终分片配置调整和新增浏览器用例后，联合运行故事记忆、向量检索、真实企业服务测试：
  `35 passed, 2 warnings`，76.75 秒，无跳过。报告为 `artifacts/semantic-acceptance.xml`。
- 数据库使用真实 `pgvector/pgvector:0.8.6-pg16`，运行账号无超级用户/BYPASSRLS 权限。
  同时验证 PostgreSQL 状态恢复、Redis 队列和 MinIO 交付闭环。
- Playwright 覆盖默认工作台和真实 pgvector 临时 API，1440、768、390 像素无脚本错误或横向溢出。
  两份报告在 `artifacts/ui-acceptance/report.json`、`semantic-report.json`，截图在同目录。
- 最终镜像 `mediaforge-semantic-20260914:local` 构建通过，包内 SQL、pgvector 依赖、默认分片
  和 135 个路由初始化通过；Compose 配置、JavaScript 语法和 `pip check` 通过。
- 第三方弃用警告仍存在。Docker 将 `MEDIAFORGE_AUTH_MODE` 标为疑似敏感 ENV，
  该变量只表示认证模式，不含 Token；未把实际凭据写入镜像。

### 本轮边界

嵌入服务使用固定向量进行协议测试，不能据此声称真实模型召回质量已达标。
本机访问 Hugging Face 模型元数据请求超时，未完成真实中文模型下载和效果验收。
未将 Mock 模型或固定向量服务设置为用户工作台的语义模型。
现有预览 `http://127.0.0.1:8023/` 保持默认关键词模式和已有项目数据；
接入部署模型并授权文本发送后，重启 API 才启用语义模式。

当时原方案仍未全部实现：LangGraph 可恢复多阶段规划（后续实现见本文顶部）、独立 GPU 执行/显存准入/模型预热、
多角色对白时间轴与口型同步、浏览器企业单点登录、支付结算，以及真实视觉模型与生成 Provider
的目标环境验收仍是独立待办。本次语义检索接入不代表这些能力已完成。

## 2026-09-14 增量验收（语义检索接入前）

本轮完成自动配音的音频标准化和失败保护、中文运行与账单面板、项目权限隔离的故事检索、
真实抽帧证据与镜头复检、Worker 资源/心跳展示。预览地址为 `http://127.0.0.1:8023/`。

- Windows 隔离 Python 环境全量回归：`141 passed, 4 skipped, 9 warnings`，585.34 秒。
- 被跳过的真实服务用例单独启动 PostgreSQL 16、Redis 7.4、MinIO 后执行：`4 passed`，42.15 秒。
  测试容器采用独立 Compose 项目 `mediaforge-acceptance-20260914`，已清理，不涉及用户原有容器。
- 14 项新增生产功能测试和 12 项故事记忆测试参加全量回归。HTTP 配音与视觉服务使用本地契约服务，
  未调用付费模型。SAPI 中文人声另行真实生成，M4A/AAC 文件可解码，时长 3.77 秒。
- Playwright 独立浏览器验证 1440、768、390 像素宽度，无页面脚本错误和横向溢出；覆盖故事检索及空状态、
  连接检查、账单筛选和 JSON 下载、等待复检接口成功、实际视频播放、配音输入按钮状态。
  证据在 `artifacts/ui-acceptance/report.json` 和同目录 PNG；复现脚本 `tests/ui_smoke.cjs`。
- Docker 镜像 `mediaforge-features-20260914:local` 构建和包内 API/静态资源/FTS5/抽帧/音频冒烟通过。
  Compose 使用 `.env.example` 并设置 `MEDIAFORGE_ENV_FILE=.env.example` 后配置检查通过。
- 第三方弃用警告仍存在（Starlette/httpx/AnyIO 和 Pillow），不计为功能验证失败。

### 复现命令

项目需要 Python 3.12 或更新版本，本机系统默认 Python 3.8 不适用。

```powershell
& .\artifacts\enterprise-venv\Scripts\python.exe -m pytest -q tests
# 真实服务连接与容器命令见 production-deployment.md
# 页面测试需可导入 playwright；PLAYWRIGHT_MODULE 可指向已安装的模块绝对路径
$env:MEDIAFORGE_UI_URL='http://127.0.0.1:8023'
node tests/ui_smoke.cjs
```

页面测试仅接受 Mock Provider，会创建带 `ui_acceptance_` 前缀的独立验收项目，保留用于查看；
不修改既有项目，不调用真实收费生成。截图和报告是执行产物，不替代目标生产环境验收。

### 范围限制

本次不能据此宣称原提案 P0/P1/P2 路线图“全部完成”：

- 当时规划仅为经过结构化校验的单阶段规划器；后续 LangGraph 草稿与恢复实现见本文顶部。
- 当时故事检索仅为 SQLite FTS5 BM25；后续 pgvector 和数据库行级安全实现见本文顶部增量记录。
- Worker 为远程 API 调度客户端，不是独立 GPU 执行器；尚无 GPU Registry、显存准入、模型预热和多控制面部署。
- 本地视觉指标只覆盖抽样曝光/对比度；远端 JPEG/参考图契约已接通，但真实视觉模型的角色一致性效果尚未现场验收。
- 配音为项目级单音轨，不是多角色对白时间轴、逐句对齐或口型同步；收费配音的真实成本需外部事件接入。
- OIDC introspection 不等于浏览器企业单点登录流程；用量台账不等于支付、税务发票或结算系统。

## 2026-09-13 历史记录

日期：2026-09-13。目标是接通对象存储、状态数据库、队列、身份、限流、通知、计费与部署，
并给出可复现的自动化证据。本次没有调用付费模型或真实企业身份账号。

## 验收证据

完整测试在 Linux 容器内执行：`117 passed, 9 warnings`，耗时 84.46 秒。
PostgreSQL 16、Redis 7.4、MinIO 为独立测试容器；生成 Provider 为 Mock。
警告来自第三方库的废弃接口提示，不影响当前断言。

最终生产镜像 `mediaforge-enterprise-acceptance:local` 构建成功；普通安装包中的
HTML/CSS/JavaScript 资源可读，129 个 API/静态路由初始化成功，包内 FFmpeg 能生成
并校验测试视频。最终 Compose 配置和 JavaScript 语法检查通过。4 个本轮临时验收
容器已移除；本地独立 Mock 预览保留在 `http://127.0.0.1:8021/`，未替换原 8020 服务。

| 能力 | 验证路径 |
| --- | --- |
| PostgreSQL 状态 | 空库建表、项目与计划重启恢复、版本冲突拒绝覆盖 |
| Redis 队列 | 持久化后入队、缺失索引恢复、并发认领去重、租约落库后确认 |
| Worker 恢复 | 到期重试、失联租约回收、有效心跳续租、跨租户隔离 |
| 对象存储 | 真实 S3 上传与字节校验、交付归档、对象地址与哈希进入回执 |
| 企业鉴权 | OIDC HTTP introspection、客户端凭证、失效令牌与匿名拒绝、租户声明 |
| 低权限执行 | 普通 Provider 令牌执行已领取任务；无租约与项目编辑被拒绝 |
| 请求限流 | SQLite 窗口持久化、429 与 Retry-After、匿名客户端隔离 |
| 事件通知 | 现有 Webhook HMAC、失败重试、outbox 持久化与恢复测试 |
| 计费台账 | 租户内幂等、旧表事务迁移、同步用量、异步实际成本、回调去重、币种隔离 |
| 运行就绪度 | 管理员连接探测、缺失或失败连接不报告就绪、Mock 不报告生产就绪 |
| 联合流程 | 建项目、排镜头、入队、Provider Worker 执行、审批、导出、发布、MinIO 归档、确认、重启读取回执 |

主要用例位于 `tests/test_enterprise.py`、`tests/test_enterprise_integration.py`，
原有 `tests/test_api.py` 和 `tests/test_p1.py` 同时参与全量回归。
真实服务测试需要显式设置连接变量，未配置时会跳过。启动与清理命令见
`production-deployment.md`，测试基础设施定义见 `docker-compose.enterprise-test.yml`。

## 部署边界

- 一个活动 API 控制面、多远程 Worker；PostgreSQL 快照不是多活动控制面的事务模型。
- Redis 是可恢复的就绪索引，任务与租约的事实来源仍是持久化状态。
- S3/MinIO 接入交付归档；制作、预览与导出仍需要本地媒体工作目录。
- 台账记录用量与成本；收单支付、税务发票仍由外部系统负责，支付回执可通过结算台账对账。
- 目标身份平台、付费 Provider、云存储权限与真实素材质量仍需部署方凭证做现场验收。
- Windows 曾出现 socket 资源耗尽及共享 Anaconda OpenSSL 冲突；最终通过结果来自隔离 Linux 环境。
