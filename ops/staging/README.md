# Staging Compose Baseline

`docker-compose.staging.yml` is a single-control-plane pre-production environment. It validates the durable dependencies used by a real deployment without implying high availability or bundling model weights.

It starts:

- PostgreSQL for durable project state.
- Redis for the recoverable ready-job index.
- MinIO plus a one-shot private-bucket initializer for verified delivery archive tests.
- One authenticated MediaForge API with strict six-stage locks enabled.
- An optional remote Worker profile for queue/lease validation.

## Bootstrap And Start

Run from the repository root:

```powershell
.\ops\staging\bootstrap.ps1
docker compose -f docker-compose.staging.yml up --build -d
docker compose -f docker-compose.staging.yml ps
.\ops\staging\verify.ps1
```

需要覆盖项目创建、阶段锁定、生成、审核、合成、交付、发布、归档及导入时，在 API 容器内运行完整闭环，避免将容器内制品路径误判为宿主机文件：

```powershell
.\ops\staging\verify.ps1 -FullWorkflow
```

The bootstrap command writes generated non-production credentials to `ops/staging/.env.staging`. That file and all real Provider configuration are ignored by Git. The Studio listens on [http://127.0.0.1:8021](http://127.0.0.1:8021); MinIO's local console listens on [http://127.0.0.1:19001](http://127.0.0.1:19001).

Use the `admin` token inside the generated `MEDIAFORGE_API_KEYS` JSON when the Studio asks for an access token. Do not paste that token into Issues, chat transcripts, commits or CI logs.

To exercise queue registration and leases in Mock mode:

```powershell
docker compose -f docker-compose.staging.yml --profile worker up -d mediaforge-worker
.\ops\staging\verify.ps1 -RequireWorker
docker compose -f docker-compose.staging.yml logs -f mediaforge-worker
```

## ComfyUI Staging Profile

The base stack intentionally uses `mock`. After assigning a cost owner, collecting a reviewed API-format workflow and verifying model rights:

1. Place the workflow registry and referenced workflow JSON files under `ops/staging/provider-config/`.
2. Copy `.env.provider.comfyui.example` to the ignored `.env.provider` file and set the reachable `COMFYUI_BASE_URL`.
3. Pin every registry entry with a version and SHA-256; retain its model inventory.
4. Restart the API and run diagnostics before submitting media:

```powershell
Copy-Item ops/staging/.env.provider.comfyui.example ops/staging/.env.provider
docker compose -f docker-compose.staging.yml up -d --build mediaforge
Invoke-RestMethod http://127.0.0.1:8021/providers/diagnostics
```

The Compose stack mounts `provider-config/` read-only at `/app/provider-config`; it never copies reviewed workflows or model files into the image. For a host-local ComfyUI instance on Docker Desktop, `host.docker.internal` is usually appropriate. For a remote GPU host, use its private network address and require network policy/TLS appropriate to that environment.

## Verify A Closed Loop

1. Sign into Studio with the local staging admin token.
2. Create a test project and lock script, storyboard and asset stages.
3. In Mock mode, generate a small number of shots and approve one artifact.
4. Lock video, create dialogue/subtitles, export and build a delivery package.
5. Verify package, rights, provenance, continuity and audit reports.
6. Change a story/shot/asset input and confirm affected downstream stage locks invalidate.
7. Repeat with one approved real ComfyUI image only after Provider diagnostics are `READY`.

## Stop Or Reset

```powershell
docker compose -f docker-compose.staging.yml down

# Deletes staging database, Redis, MinIO and artifacts. Never run against production.
docker compose -f docker-compose.staging.yml down -v
```

Use [production deployment](../../docs/production-deployment.md) for HA, OIDC, observability, object-lock policy and Kubernetes. This Compose baseline is deliberately not a production deployment.
