# 故事语义检索

## 能力与边界

`MEDIAFORGE_RAG_BACKEND=sqlite` 是默认的本地 BM25 关键词检索，不发送文本。
`pgvector` 模式将已接受的故事设定、镜头卡和参考资产描述通过 TEI `/embed` 编码，
在 PostgreSQL 内按余弦相似度检索。图片本身不进入这个文本索引。
`ragflow` 模式默认只调用已部署 RAGFlow 的检索 API，查询预先策展的数据集；它不会在项目
创建、检索或规划时自动上传、解析、删除或写入数据。经部署级和请求级双重确认后，编辑者可
手动将已批准的故事设定、镜头卡与参考资产描述同步为一个内容寻址的 Markdown 快照。
默认仅查询当前项目；额外来源必须由用户选择并通过项目成员权限与租户检查。

RAGFlow 来源必须按本租户和 MediaForge 项目 ID 显式映射到数据集 ID。应用在发起远端查询前
先执行本地权限检查，远端响应中不属于本次映射范围的数据集片段会被丢弃。它适合复用已有的
企业文档库和 RAGFlow 解析能力，但不替代 MediaForge 项目状态的本地可重建记忆。

向量模式采用精确检索和范围 B-tree 索引，尚无 HNSW/IVFFlat 近似索引或大规模性能保证。
单项目最多 4000 个片段，每批 16 个。向量模式默认片段 384 字符、重叠 48 字符，
可通过 `MEDIAFORGE_RAG_CHUNK_CHARS`（64..3000）和 `MEDIAFORGE_RAG_CHUNK_OVERLAP`
（0 到片段长度减 1）调整，改变后会更新索引指纹并重建。
字符不等同于模型 token；不允许服务端静默截断，若模型最大输入长度不足，索引操作明确失败，
需要缩小分片或部署支持更长输入的模型。检索问题和查询前缀也受模型长度约束。
相似度阈值不是概率，需要按目标模型和业务数据标定。

## 初始化数据库

安装 Python 3.12+ 和 `pip install -e ".[enterprise]"`，数据库需支持 pgvector 扩展。
使用专用迁移账号执行包内迁移，不将它交给 API：

```powershell
$env:MEDIAFORGE_RAG_MIGRATION_URL='postgresql://migration_user:replace-me@localhost:5432/mediaforge'
python -m mediaforge_p1.vector_memory --init-schema
Remove-Item Env:MEDIAFORGE_RAG_MIGRATION_URL
```

由数据库管理员创建专用应用账号，并授权：

```sql
CREATE ROLE mediaforge_rag LOGIN PASSWORD 'replace-with-a-unique-secret' NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO mediaforge_rag;
GRANT SELECT, INSERT, UPDATE, DELETE ON mediaforge_memory_project, mediaforge_memory_chunk TO mediaforge_rag;
GRANT USAGE, SELECT ON SEQUENCE mediaforge_memory_chunk_memory_id_seq TO mediaforge_rag;
```

不要给应用账号 CREATE、表所有权、迁移角色成员资格或 BYPASSRLS 权限。
两张表启用并强制执行行级安全；缺少事务内工作区、租户、项目范围时默认不可读。
API 每次数据库连接检查角色权限和策略启用状态，拒绝超级用户或绕过 RLS 的账号。
RLS 是可信 API 设置范围后的纵深防御，不是将数据库凭证分发给租户的安全方案：
持有数据库凭证的人可以设置自定义会话变量，因此凭证必须只保存在服务端。
策略修改仍属于受信任的数据库管理员权限范围。

## 配置模型

部署固定版本的 [Hugging Face Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference/quick_tour)
兼容服务。实际模型需按其许可证、语言覆盖、输入限制和硬件要求选定。
以下字段全部与已部署实例保持一致：

```text
MEDIAFORGE_RAG_BACKEND=pgvector
MEDIAFORGE_RAG_DATABASE_URL=postgresql://mediaforge_rag:replace-me@localhost:5432/mediaforge
MEDIAFORGE_RAG_NAMESPACE=studio-production
MEDIAFORGE_RAG_MIN_SIMILARITY=0.25
MEDIAFORGE_RAG_CHUNK_CHARS=384
MEDIAFORGE_RAG_CHUNK_OVERLAP=48
MEDIAFORGE_EMBEDDING_URL=http://127.0.0.1:8080/embed
MEDIAFORGE_EMBEDDING_MODEL=your-deployed-model-id
MEDIAFORGE_EMBEDDING_REVISION=your-pinned-model-commit
MEDIAFORGE_EMBEDDING_DIMENSIONS=768
MEDIAFORGE_EMBEDDING_TIMEOUT_SECONDS=30
MEDIAFORGE_EMBEDDING_ALLOW_DATA_EXPORT=true
```

外网服务应使用 HTTPS；访问令牌通过 `MEDIAFORGE_EMBEDDING_TOKEN` 配置。
工作区标识必须在同库不同部署间唯一，且重启后保持稳定。嵌入发送授权默认关闭，
启用意味着允许将项目文本、检索问题发送到配置的服务，必须先确认数据授权。
HTTP 重定向被拒绝，避免文本或 Bearer Token 被转发到另一地址。

需要前缀的模型使用 `MEDIAFORGE_EMBEDDING_QUERY_PREFIX` 和
`MEDIAFORGE_EMBEDDING_DOCUMENT_PREFIX`，例如模型明确要求时分别配置 `query: ` 和 `passage: `。
尾部空格有意义，不会被删除。服务返回的向量必须数量匹配、维度匹配、非零且全为有限数值，
应用会执行 L2 归一化。输入上限、响应上限、连接和查询超时均有限制。

模型名、固定修订、维度、前缀和分片规则共同决定索引指纹。变更后旧空间不会用于新检索，
下一次读取授权来源时会重新构建。这里的模型修订是部署方声明的版本，并非远程模型身份认证；
必须固定服务端模型，不能在同一声明下偷偷替换模型。

## 配置 RAGFlow 检索与显式同步

RAGFlow 服务和数据集由部署方独立维护。仅在已完成数据授权、数据集内容审查以及网络边界配置后，
启用以下配置：

```text
MEDIAFORGE_RAG_BACKEND=ragflow
MEDIAFORGE_RAGFLOW_BASE_URL=https://ragflow.example.com
MEDIAFORGE_RAGFLOW_API_KEY=replace-with-ragflow-api-key
MEDIAFORGE_RAGFLOW_DATASET_MAP={"tenant_a":{"brand-library":["ragflow-dataset-id"]}}
MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT=true
MEDIAFORGE_RAGFLOW_TIMEOUT_SECONDS=15
MEDIAFORGE_RAGFLOW_SIMILARITY_THRESHOLD=0.2
MEDIAFORGE_RAGFLOW_VECTOR_SIMILARITY_WEIGHT=0.3
```

`MEDIAFORGE_RAGFLOW_DATASET_MAP` 的键是 MediaForge 的 `tenant_id` 和可作为来源选择的
`project_id`，值是该来源获准查询的 RAGFlow 数据集 ID 数组。不要按名称模糊匹配，也不要将
通用数据集映射给未经内容审查的项目。`MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT` 默认关闭；
设置为 `true` 明确表示允许把检索问题发送到此 RAGFlow 实例。API Key 仅存在服务端配置，
不进入客户端、审计详情或规划引用。HTTP 重定向会被拒绝，避免查询内容或 Bearer Token 被转发。

默认适配只使用 RAGFlow `/api/v1/retrieval` 查询和 `/api/v1/datasets` 连通性探测。需要将
已批准的项目内容作为独立快照导入时，额外开启写回开关：

```text
MEDIAFORGE_RAGFLOW_ALLOW_WRITE_SYNC=true
MEDIAFORGE_RAGFLOW_MAX_SYNC_BYTES=5000000
```

随后由拥有编辑权限的项目成员显式调用：

```text
POST /projects/{project_id}/memory/ragflow-sync
{"confirm_data_export": true}
```

该调用会根据快照 SHA-256 命名 Markdown 文件，先查询同名文档再通过 RAGFlow
`/api/v1/datasets/{dataset_id}/documents` 上传缺失快照。同步不会上传原始章节、媒体文件或
未批准的规划尝试；每次调用都会写入 MediaForge 审计记录。删除、重新分块与 RAGFlow 数据集
生命周期仍由知识库管理员负责。

## 使用与故障保护

工作台“故事记忆”显示检索引擎、模型、维度、阈值和分数类型。RAGFlow 写回已配置时，
该页会展示“同步已批准快照”命令；操作人必须二次确认，服务端仍会验证项目编辑权限、
租户/项目数据集映射、快照大小和幂等摘要。
“运行与账单”中的“检查检索连接”仅管理员可执行，对当前后端执行连通性探测。
`POST /enterprise/memory/probe` 使用固定的无敏感探测文本，返回 `reachable` 和诊断状态。
状态是最近一次操作结果，不是持续健康监控。

`GET /projects/{id}/memory` 和 `POST /projects/{id}/plan` 沿用原有来源选择协议。
生成计划持久化检索后端、源片段 SHA256、实际截取文本 SHA256、源镜头修订和嵌入指纹。
SQLite/pgvector 索引来自已接受状态，不从失败规划中学习。向量计算在替换事务前完成，任一批次
失败不删除旧索引；并发更新使用范围锁和版本比较，旧任务不能覆盖较新结果。RAGFlow 没有本地索引
同步或回写，其数据新鲜度、解析质量和保留策略由外部数据集管理员负责。
数据库、模型或 RAGFlow 故障返回 503，保留原计划；不会静默降级为关键词结果或忽略检索上下文。
模型未验证，或向 TEI/RAGFlow 外发文本未授权时，启用语义上下文的规划模式不能报告生产就绪。

## 自动化验收

```powershell
docker compose -p mediaforge-vector-test -f docker-compose.semantic-test.yml up -d --wait
$env:MEDIAFORGE_TEST_VECTOR_URL='postgresql://mediaforge_vector_test:local-test-only@127.0.0.1:15493/mediaforge_vector_test'
python -m pytest -q tests/test_memory.py tests/test_vector_memory.py
docker compose -p mediaforge-vector-test -f docker-compose.semantic-test.yml down
Remove-Item Env:MEDIAFORGE_TEST_VECTOR_URL
```

临时容器仅绑定回环地址、使用内存数据目录；这些测试凭证不能用于生产。
测试创建独立普通账号和工作区，结束时清理自身数据与权限。
验证真实 pgvector 排序、数据库行级隔离、模型空间隔离、幂等、并发保护、重启读取、
规划引用、权限拒绝与服务故障。RAGFlow 仅支持上述显式、人工触发的内容寻址快照上传，
不会自动同步、删除或管理外部数据集生命周期。嵌入 HTTP 测试使用固定向量，只证明协议和存储正确性，
不证明真实中文语义召回效果。正式验收还应使用部署模型和标注问答集评估召回率及阈值。

如需运行语义检索页面验收，额外安装 Playwright 与 Chromium，并设置 `PLAYWRIGHT_MODULE`
为可导入的 Playwright 模块名或绝对路径，再运行 `tests/test_vector_memory.py` 中的
`test_pgvector_browser_controls`。该测试自动启动独立临时 API，以真实 pgvector 和固定向量
验证 1440、768、390 像素页面、查询分数和连接诊断；不会改动已打开的浏览器或用户项目。
截图与结构化报告在 `artifacts/ui-acceptance/semantic-*`。

实现依据：[pgvector](https://github.com/pgvector/pgvector)、
[pgvector Psycopg 适配](https://github.com/pgvector/pgvector-python)、
[PostgreSQL 行级安全](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)。
