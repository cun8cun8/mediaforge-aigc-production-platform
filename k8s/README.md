# MediaForge Kubernetes 清单

`base` 部署两个主动/备用 API 副本、一个只指向就绪副本的 ClusterIP 服务、一个
RWX 数据卷和 PDB。`overlays/gpu-worker` 在同一控制面加入本地 Provider 回调式 GPU
Worker。清单使用占位镜像 `ghcr.io/replace-me/mediaforge:replace-me` 和存储类
`mediaforge-rwx`，应用前必须替换成已发布镜像和已验证的 RWX 存储类。

`overlays/locked-down` 以 default-deny NetworkPolicy 启动，只放行 DNS 与 GPU Worker
到 API 的内部控制流。它故意不会猜测 PostgreSQL、Redis、对象存储、OIDC、Provider、
Prometheus 或 Ingress 的地址。复制 `allowed-egress.example.yaml` 与
`api-ingress.example.yaml` 到环境专用 overlay，填写精确 CIDR、端口、域名和 Ingress
控制器规则后再引用；未完成这些规则时不要在生产应用严格 overlay。

`base/secret.example.yaml` 只是所需密钥键名的示例，未被 Kustomization 引用。应由
External Secrets、Sealed Secrets 或组织的密钥管理流程创建同名 `mediaforge-runtime`
Secret，不能将真实密钥提交到仓库。Stripe 原生结算入口使用
`stripe-settlement-webhook-secret`，通用财务回调使用 `settlement-callback-secret`；两者
均为可选注入，未配置时入口保持关闭。

API 和 GPU Worker 都可选读取 `mediaforge-provider` ConfigMap。先依据
`overlays/gpu-worker/provider-config.example.yaml` 创建真实 Provider 配置，再部署；
API 与 Worker 必须配置相同的 Provider 名称和能力，Worker 才会领取该 Provider 已路由的
任务。

`operations/backup-verifier-cronjob.example.yaml` 是一个未被默认 Kustomization 引用的
只读校验样例。它每天检查外部备份系统写入的最新 `mediaforge-backup-*` 目录的清单、归档安全性
和 RPO 新鲜度，不创建、删除或恢复备份。应用前必须替换镜像、备份 PVC、计划和
`MEDIAFORGE_BACKUP_MAX_AGE_HOURS`，并为 CronJob 失败配置组织现有的告警。恢复演练必须在
隔离数据库和空白工件目录中使用 `ops/backup/restore-drill.sh`，不能由这个只读 Job 执行。

```sh
kubectl kustomize k8s/base > /dev/null
kubectl apply -f k8s/base/secret.example.yaml  # 仅在替换所有示例值后；生产建议用外部密钥控制器
kubectl apply -f k8s/overlays/gpu-worker/provider-config.example.yaml
kubectl apply -k k8s/overlays/gpu-worker
kubectl -n mediaforge get pods
```

`/livez` 只表示进程可存活，`/health` 只有持有 PostgreSQL 控制面租约的 API 副本会返回
成功。Service 的 ready endpoint 因此不会把写请求发送给备用副本。GPU 节点需要已安装
NVIDIA device plugin；真实模型、驱动、Provider 命令及其权重必须烘焙进指定镜像或通过受控
卷挂载，不能依赖这个通用应用镜像凭空提供。

发布流程由 `.github/workflows/deploy.yml` 的手动触发入口执行。选择 GitHub Environment、
overlay 和不可变镜像引用后，它先运行 cluster-side dry-run；只有把 `mode` 明确设为
`apply` 才会执行 server-side apply。所选 Environment 必须保存 base64 编码的
`KUBE_CONFIG_DATA`，并用 GitHub Environment 审批保护生产环境。CI 同时渲染三个 overlay、
构建镜像，并运行 Python、中文工作台、叙事和分步规划浏览器验收。
