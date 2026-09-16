# 可恢复分步规划

分步规划使用 LangGraph StateGraph 和官方 SQLite 或 PostgreSQL Checkpointer，执行检索、故事设定、
分镜、参数编译、人工审核五个阶段。草稿独立于正式项目，审核通过后才更新镜头。
已确认的故事事件会在故事设定阶段写入 `narrative_event_context`，并成为确定性分镜的直接输入；
原文摘录仅以 SHA-256 和定位形式留存在上下文中，不复制到分镜提示词。
当存在已确认的剧本场次时，工作流会先写入 `adaptation_scene_context`，并以场次标题、梗概、节拍、对白和
时长作为分镜的优先输入。场次只能引用已确认的故事事件；事件或原著章节版本变化会使关联场次退回修改，
所以不能跳过场次审核沿用旧分镜。
原著章节可先在工作台的“原著章节”页导入，再提取为需要人工采纳的候选事件。章节默认走本地结构化
提取；外部模型处理原文需要针对该章节的显式授权。候选只有采纳为故事事件并审核通过后才会进入此上下文。
章节哈希变化会使关联事件退回修改，因此不能用旧章节版本覆盖新的规划草稿。
原著也可从 TXT、Markdown、DOCX 或 PDF 自动分章导入；原始文件与自动生成的章节保持同一个文件哈希和
解析器版本记录。PDF 优先使用内嵌文字；扫描件只会在导入请求显式设置 `allow_external_processing=true` 且
部署方配置 OCR 后处理。HTTP OCR 会接收整份 PDF，必须使用受控端点；本地命令也要求同一显式授权。
来源记录保留 `native_text`、`ocr_command` 或 `ocr_http` 提取方式。`GET /source-ingest/status` 仅返回
OCR 配置状态，不泄露命令或端点。
旧 `POST /projects/{id}/plan` 和工作台“生成计划”保留原行为，不自动进入该审核流程。

## 启用

需要 Python 3.12 或更新版本：

```powershell
python -m pip install -e ".[agents]"
$env:MEDIAFORGE_PLANNING_MODE='langgraph'
uvicorn mediaforge_p1.api:app --host 127.0.0.1 --port 8023
```

默认 `MEDIAFORGE_PLANNING_MODE=legacy`；生产 Docker 镜像包含 agents 依赖，仍需显式启用。
`GET /planning/status` 返回配置、模型模式、阶段和修订上限。工作台提供中文“分步规划”页。

本地开发默认使用 SQLite。需要让多个受控 API 实例读取同一草稿或进行故障切换时，安装
`.[agents,enterprise]`，并使用独立的 PostgreSQL 数据库：

```powershell
$env:MEDIAFORGE_PLANNING_MODE='langgraph'
$env:MEDIAFORGE_PLANNING_CHECKPOINT_BACKEND='postgres'
$env:MEDIAFORGE_PLANNING_DATABASE_URL='postgresql://mediaforge_planning:replace-me@postgres:5432/mediaforge_planning'
```

首次启动时官方 `PostgresSaver` 会创建自己的 Checkpoint 表，MediaForge 会额外创建
`mediaforge_planning_run` 运行登记表。不要把该 URL 指向项目状态表所在的共享业务数据库，
也不要由 API 使用超级用户；应使用专用数据库或至少专用角色。适配器自动启用
`LANGGRAPH_STRICT_MSGPACK=true`，限制持久化状态反序列化范围。项目 API Key 不写入 Checkpoint。
PostgreSQL 模式使用会话级 advisory lock 排他推进同一租户/项目的草稿；进程异常退出后数据库
会释放锁。
完成配置后由管理员调用 `POST /planning/probe` 验证连接和 Checkpoint 建表权限；常规
`GET /planning/status` 不发起网络探测，只显示最近一次脱敏结果。

未配置外部 LLM 时，使用本地确定性故事和分镜验证流程，不使用检索上下文，也不支持语义修订。
真实故事生成沿用 [部署文档](production-deployment.md) 的 `MEDIAFORGE_LLM_*` 配置。
外部规划器分别请求故事设定与分镜，不把一次模型调用伪装成多阶段执行。
角色必须与项目简报一致，镜头数、总时长、结构与内容策略通过校验后才进入审核。

## 接口与状态

| 操作 | 接口 |
| --- | --- |
| 创建草稿 | `POST /projects/{id}/planning` |
| 最近 30 次记录 | `GET /projects/{id}/planning` |
| 单次详情 | `GET /projects/{id}/planning/{run_id}` |
| 恢复失败或中断阶段 | `POST /projects/{id}/planning/{run_id}/resume` |
| 迁移兼容旧草稿 | `POST /projects/{id}/planning/{run_id}/migrate` |
| 审核 | `POST /projects/{id}/planning/{run_id}/review` |
| 取消非终态草稿 | `POST /projects/{id}/planning/{run_id}/cancel` |

创建请求可传 `{"memory_project_ids":["source_project"]}`，省略时只使用当前项目。
来源必须与项目同租户且当前用户可读；恢复、迁移与审核时重新检查权限。
检索支持 SQLite、pgvector 或只读 RAGFlow 数据集映射，配置与文本发送授权见
[语义检索部署](semantic-memory.md)。

审核请求为 `{"decision":"approve|revise|reject","comment":"审核意见"}`。
创建、恢复、迁移、取消要求编辑权限；审核要求审核权限；审核人与迁移操作者取自认证主体，不能由请求体冒充。

- `RUNNING`：当前正在执行；进程退出并释放锁后，读取时显示 `INTERRUPTED`。
- `FAILED`：阶段失败，返回脱敏错误，已有正式计划不变。恢复只重试未完成阶段。
- `AWAITING_REVIEW`：草稿与 GenerationSpec 可检查，未写入正式计划。
- `READY_TO_APPLY`：图执行已批准，但项目提交尚未完成，可重试确认。
- `APPROVED`、`REJECTED`、`CANCELED`：已应用、已退回、已取消。

模型或策略失败后，若已有有效故事设定，可以提交 `revise` 返回分镜阶段；不重跑故事设定。
修订必须填写意见，最多三轮，且仅外部规划器支持。审核阶段同样可以返工。
接口成功返回运行资源不代表规划成功，客户端必须检查 `status`；权限错误、冲突或前置条件
不满足分别返回对应的 403/404/409。草稿列表和详情不返回原始 Checkpoint 数据库内容。

## 一致性边界

仅支持尚无生成任务、媒体和发布记录的制作前项目。有生产记录时应创建新项目或无生产任务的分支。
每个项目最多一个活跃草稿；项目编辑、直接生成、批量入队与草稿执行共享制作前互斥保护。
已存在任务后的正常 Worker 执行不依赖此规划锁。

恢复或确认时重新检查项目输入和模型配置指纹；草稿生成后项目改变会拒绝覆盖。项目输入还包括
已确认故事事件的章节顺序、版本和来源哈希，以及已确认剧本场次的版本与来源事件哈希，因此修订或重新审核
事件、场次后必须创建新草稿。
确认时再次编译参数并执行内容策略，所应用参数必须与审核的 GenerationSpec 一致。
项目内的 `planning_run_id` 是提交回执，进程在项目提交后退出也不会导致重复应用或重复审批审计。
故事设定保留阶段轨迹、审核意见、来源文档哈希、实际检索片段哈希及嵌入空间指纹。

阶段已经持久化后不会因恢复再次执行；**进程在远程请求完成但 Checkpoint 落盘之前退出，
该请求仍可能重发并再次计费**。这不是对外部模型调用或支付系统的 exactly-once 保证。
规划请求目前同步执行，客户端超时后应读取运行状态，不能直接重复创建。

## 部署与备份

SQLite Checkpoint 和运行目录位于 `MEDIAFORGE_ARTIFACT_ROOT/.planning/`，文件为
`checkpoints.sqlite3`；PostgreSQL 模式的 Checkpoint 和运行登记表位于配置的专用数据库。
两种后端都可能包含项目简报、检索内容、模型输出和审核意见，部署方必须用文件权限或数据库
权限、磁盘加密和备份权限保护；API Key 不写入规划状态。
图调用显式关闭 LangSmith tracing，不因宿主机存在 tracing 环境变量而自动发送项目内容。

SQLite 不支持多控制面共享、网络文件锁或高可用 Checkpointer。PostgreSQL 模式提供共享
Checkpoint 和跨进程草稿锁，可用于受控故障切换，但当前项目状态仍是单活动控制面的乐观并发快照；
它不是多主 API 写入模型。生产上应保持一个活动写控制面，在切换前停止旧实例、等待在途请求完成，
然后由新实例从共享数据库恢复草稿。Worker 的 GPU 信息目前只用于遥测与领取准入，不会被误报为
LangGraph 的 GPU 执行节点。

SQLite 部署在容器重建时必须保留整个 artifact volume；PostgreSQL 部署需要备份其专用数据库。
停写并等待正在执行的请求结束后，将项目状态、媒体和规划 Checkpoint 一起备份；恢复必须使用
同一批快照。部署升级前保留旧镜像和快照。当前 `mediaforge-planning-graph-v2` 可以显式迁移到 v3：
服务会验证旧模型指纹、当前项目输入、来源权限与 Checkpoint 节点集，不会重跑已完成阶段，并把操作者、
版本和时间写入草稿的 `migration_history`。任何不在兼容表中的图版本都会被拒绝，必须创建新草稿。

## 验证

```powershell
python -m pytest -q tests/test_planning.py
# 安装 Playwright 和 Chromium 后，设置 PLAYWRIGHT_MODULE 执行隔离浏览器用例
# 对已启动的本地 Mock 工作台：
$env:MEDIAFORGE_UI_URL='http://127.0.0.1:8023'
node tests/ui_planning_smoke.cjs
```

覆盖真实子进程强制退出后恢复、未完成节点重试、人工修订、权限、并发锁、过期草稿、
提交失败重试、检查点下载隔离和 HTTP 结构化模型契约。浏览器覆盖失败分镜修复、刷新恢复、
人工确认、实际 Mock 媒体生成以及 1440/768/390 像素布局。
外部模型测试使用本地协议 fixture，不代表真实 LLM 的叙事质量已经验收。

生产安装包可独立验证，以下命令在仓库根目录执行。脚本只挂载测试目录，运行目录为 `/tmp`，
因此导入的是镜像内安装包，而不是工作区源码：

```powershell
docker build -t mediaforge-planning-20260914:local .
docker run --rm --workdir /tmp --mount "type=bind,source=${PWD}/tests,target=/checks,readonly" `
  -e MEDIAFORGE_AUTH_MODE=disabled -e MEDIAFORGE_PLANNING_MODE=langgraph `
  -e MEDIAFORGE_LLM_MODE=disabled -e MEDIAFORGE_PROVIDER=mock -e MEDIAFORGE_STATE_BACKEND=sqlite `
  -e MEDIAFORGE_ARTIFACT_ROOT=/tmp/mediaforge-smoke `
  mediaforge-planning-20260914:local python /checks/planning_image_smoke.py
```

该验收不需要或使用真实模型密钥，不能将其中关闭鉴权的参数用于生产环境。

上游机制参考：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、
[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。
