# Configuration Reference

This document separates safe local defaults from production settings. Start with [`.env.example`](../.env.example), place real values in a deployment secret manager or an ignored `.env` file, and never commit credentials, provider tokens, webhook secrets, database passwords or real media.

## Configuration Layers

| Layer | Use | Typical settings |
| --- | --- | --- |
| Local development | Run the Studio without paid services | Mock Provider, SQLite, local artifact root, disabled auth |
| Integration / staging | Verify one real Provider and callback | ComfyUI or Replicate, callback secret, test tenant, isolated storage |
| Team production | Durable collaborative operation | PostgreSQL, Redis, MinIO/S3, OIDC, metrics token, quotas |
| HA / Kubernetes | Failure tolerance and worker scale | leased control plane, RWX storage, GPU Worker, NetworkPolicy, secret manager |

## Local Development Profile

Use this profile for UI and closed-loop workflow development. It has no external model or cloud dependency.

```powershell
python -m pip install -e ".[dev,agents]"

$env:MEDIAFORGE_AUTH_MODE = "disabled"
$env:MEDIAFORGE_PROVIDER = "mock"
$env:MEDIAFORGE_LLM_MODE = "disabled"
$env:MEDIAFORGE_RATE_LIMIT_ENABLED = "false"
$env:MEDIAFORGE_ARTIFACT_ROOT = "artifacts/local"

python -m uvicorn mediaforge_p1.api:create_app --factory --host 127.0.0.1 --port 8020
```

Do not enable `MEDIAFORGE_REQUIRE_STAGE_LOCKS` on the first local walkthrough. Enable it when the team is ready to test the full review process.

## Provider Profiles

### ComfyUI Image Generation

```powershell
$env:MEDIAFORGE_PROVIDER = "comfyui"
$env:COMFYUI_BASE_URL = "http://comfyui.internal:8188"
$env:COMFYUI_WORKFLOW_REGISTRY_PATH = "/app/config/comfyui-workflow-registry.json"
$env:COMFYUI_REQUIRE_WORKFLOW_PIN = "true"
$env:MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID = "comfyui_image:reviewed:v1"
$env:COMFYUI_TIMEOUT_SECONDS = "180"
$env:COMFYUI_POLL_INTERVAL_SECONDS = "0.5"
```

The registry must map each `template_id` to a reviewed API-format workflow, version and SHA-256. A single-entry registry is selected automatically; a multi-workflow registry must set `MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID` to an exact reviewed ID. Startup validates the workflow bytes; health checks also inspect the ComfyUI service and declared model inventory. See [ComfyUI Provider Adapter](comfyui-provider.md).

### Replicate Image-To-Video

```powershell
$env:MEDIAFORGE_PROVIDER = "replicate"
$env:REPLICATE_API_TOKEN = "<from-secret-manager>"
$env:REPLICATE_MODEL_VERSION = "<approved-immutable-version>"
$env:REPLICATE_HTTP_RETRY_ATTEMPTS = "2"
$env:REPLICATE_HTTP_RETRY_BACKOFF_SECONDS = "0.5"
```

Keep the model version immutable. The adapter sends a per-Job idempotency key and uses bounded retries for transient failures; it does not expose the token in diagnostics. See [Replicate Provider Adapter](replicate-provider.md).

### Multi-Provider Routing

```powershell
$env:MEDIAFORGE_PROVIDERS = "comfyui,replicate"
```

The first ready Provider able to perform the requested capability has priority. A failed execution may fail over to other eligible Providers, subject to capability, budget and policy checks. Use `GET /providers/diagnostics` to inspect readiness without exposing credentials.

### Local GPU Command And Remote Worker

```powershell
$env:MEDIAFORGE_PROVIDER = "local"
$env:MEDIAFORGE_LOCAL_PROVIDER_COMMAND = "/opt/mediaforge-provider/run.sh"
$env:MEDIAFORGE_LOCAL_PROVIDER_CAPABILITIES = "image_generation,image_to_video"
$env:MEDIAFORGE_LOCAL_PROVIDER_TIMEOUT_SECONDS = "900"

$env:MEDIAFORGE_WORKER_EXECUTION_MODE = "local-provider-callback"
$env:MEDIAFORGE_WORKER_PROVIDER = "local"
$env:MEDIAFORGE_WORKER_ARTIFACT_ROOT = "/var/lib/mediaforge/artifacts"
$env:MEDIAFORGE_CALLBACK_SECRET = "<long-random-secret>"
```

The command receives request JSON and must create the exact file declared by `MEDIAFORGE_OUTPUT_PATH`. In `local-provider-callback` mode, API and Worker need the same writable artifact root. The Worker must authenticate as role `provider`, hold a live job lease and send the shared callback secret as HMAC-SHA256 signed headers.

## Callback Protection

Every non-Mock asynchronous callback requires:

```text
MEDIAFORGE_CALLBACK_SECRET=<long-random-secret>
MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS=300
```

The callback endpoint is:

```text
POST /projects/{project_id}/jobs/{job_id}/callback
```

The signature covers timestamp, HTTP method, request path and raw JSON body. Workers can use the built-in callback implementation in `mediaforge_p1.worker`; custom integrations must use the same canonical algorithm. Check configuration with `GET /providers/callback-security`.

## Production Runtime Profile

The following is a minimum target, not a copy-and-paste credentials file:

```text
MEDIAFORGE_STATE_BACKEND=postgres
MEDIAFORGE_DATABASE_URL=postgresql://mediaforge:<password>@postgres:5432/mediaforge
MEDIAFORGE_STATE_TABLE=mediaforge_state

MEDIAFORGE_QUEUE_BACKEND=redis
MEDIAFORGE_REDIS_URL=redis://redis:6379/0
MEDIAFORGE_REDIS_QUEUE=mediaforge:jobs

MEDIAFORGE_STORAGE_MODE=s3
MEDIAFORGE_STORAGE_ENDPOINT=https://minio.internal
MEDIAFORGE_STORAGE_BUCKET=mediaforge-artifacts

MEDIAFORGE_ARTIFACT_ROOT=/var/lib/mediaforge/artifacts
MEDIAFORGE_CONTROL_PLANE_MODE=leased
MEDIAFORGE_CONTROL_PLANE_LEASE_SECONDS=30

MEDIAFORGE_AUTH_MODE=oidc
MEDIAFORGE_REQUIRE_STAGE_LOCKS=true
MEDIAFORGE_RATE_LIMIT_ENABLED=true
```

For S3 or MinIO, inject `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` only through the deployment secret mechanism. The artifact root remains a shared editable volume; object storage is used for verified delivery/archive objects and integrity receipts.

For an executable pre-production baseline, use the repository's [staging Compose guide](../ops/staging/README.md). It generates ignored local credentials, creates a private MinIO bucket before API startup, and exercises PostgreSQL, Redis and S3-compatible probes without requiring a real Provider.

## Identity And Tenant Mapping

For an enterprise identity provider, configure OIDC introspection at minimum:

```text
MEDIAFORGE_AUTH_MODE=oidc
MEDIAFORGE_OIDC_INTROSPECTION_URL=https://login.example.com/oauth2/introspect
MEDIAFORGE_OIDC_CLIENT_ID=mediaforge
MEDIAFORGE_OIDC_CLIENT_SECRET=<from-secret-manager>
MEDIAFORGE_OIDC_ISSUER=https://login.example.com/realms/mediaforge
MEDIAFORGE_OIDC_ROLE_CLAIM=groups
MEDIAFORGE_OIDC_TENANT_CLAIM=organization.id
MEDIAFORGE_OIDC_ROLE_MAPPING={"mediaforge-editors":"editor","mediaforge-publishers":"publisher"}
```

Browser SSO additionally needs authorization code + PKCE endpoints and a public HTTPS callback URL. Set `MEDIAFORGE_OIDC_COOKIE_SECURE=true` outside local development. Keep directory credentials in the identity provider; MediaForge only consumes approved identity claims.

## Storage, Observability And Governance

| Concern | Essential setting | Operational check |
| --- | --- | --- |
| Metrics | `MEDIAFORGE_METRICS_AUTH_MODE=token` and token file | `/metrics`, Prometheus scrape |
| Alerting | `MEDIAFORGE_ALERT_*` thresholds | `/ops/alerts`, Grafana dashboard |
| Asset rights | `MEDIAFORGE_REQUIRE_ASSET_RIGHTS_RECORD=true` | Project compliance report blocks unregistered asset |
| Workflow rights | `MEDIAFORGE_LICENSE_REGISTRY_PATH` or registry sync | License registry validation |
| Audit anchor | `MEDIAFORGE_AUDIT_ANCHOR_MODE=object_lock` or `http` | External receipt and read-back verification |
| C2PA | signer and verifier command variables | Signed and independently verified state |
| Delivery | `MEDIAFORGE_DELIVERY_MODE` and optional secret | Dispatch receipt and delivery package verification |

The Prometheus, Alertmanager and Grafana overlay is `docker-compose.observability.yml`. Keep monitoring tokens in `ops/secrets/` or a proper secret store, never in a Compose file or GitHub Actions log.

## Configuration Validation

Run these checks in increasing order of cost and impact:

```powershell
# No paid generation: configuration and connectivity only.
Invoke-RestMethod http://127.0.0.1:8020/providers/status
Invoke-RestMethod http://127.0.0.1:8020/providers/health
Invoke-RestMethod http://127.0.0.1:8020/providers/diagnostics
Invoke-RestMethod http://127.0.0.1:8020/ops/readiness

# Explicitly approved Provider test only.
mediaforge-provider-probe --provider comfyui --workflow D:\secure-config\reviewed-comfyui-image-workflow.json

# Release readiness.
mediaforge-production-acceptance --base-url https://staging.example.com --token $env:MEDIAFORGE_READINESS_TOKEN --probe-enterprise --probe-planning --require-production
```

Use the [release checklist](release-checklist.md) to decide when a configuration is allowed to move from staging to production. Detailed variable comments remain in [`.env.example`](../.env.example).
