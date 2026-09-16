# Contributing to MediaForge

感谢参与 MediaForge。这个项目同时涉及创作工作流、媒体处理、模型 Provider、企业运行时和治理，因此提交应保持小而可验证，避免将环境配置和业务素材混入代码。

## Before You Start

1. 阅读根目录 [README](README.md) 与相关专题文档。
2. 在 Issue 中描述问题或设计意图；涉及架构、Provider 或数据模型变更时，先说明兼容性和回滚方式。
3. Fork 或创建个人分支，不直接向 `main` 推送。
4. 不提交 `.env`、Token、数据库地址、真实媒体、用户原文、模型权重或 `artifacts/` 内容。

## Local Environment

```powershell
python -m pip install -e ".[dev,enterprise,agents]"
npm ci
npm run install:browsers

$env:MEDIAFORGE_AUTH_MODE = "disabled"
$env:MEDIAFORGE_PROVIDER = "mock"
$env:MEDIAFORGE_LLM_MODE = "disabled"
$env:MEDIAFORGE_ARTIFACT_ROOT = "artifacts/contributor"
python -m uvicorn mediaforge_p1.api:create_app --factory --host 127.0.0.1 --port 8020
```

Use Mock mode for normal development. A real Provider Probe may incur cost and must never be triggered by a generic test or browser smoke test.

## Branch And Commit Convention

Use short, scoped branches:

```text
feat/workflow-locking
fix/provider-callback-retry
docs/deployment-guide
test/worker-lease-recovery
```

Commit messages use a concise conventional prefix:

```text
feat: add provider route preview
fix: reject expired callback timestamps
docs: clarify production storage boundary
test: cover workflow invalidation
```

Do not mix unrelated refactors with behavioral changes. Keep migrations, generated files and dependency upgrades isolated when possible.

## Required Checks

Run the narrowest relevant test first, then the full check set before requesting review:

```powershell
python -m compileall -q src tests
python -m pytest -q

$env:MEDIAFORGE_UI_URL = "http://127.0.0.1:8020"
npm run test:ui
npm run test:ui:workflow
npm run test:ui:narrative
npm run test:ui:planning
```

Provider, Worker, persistence, authorization, UI workflow and delivery changes need regression coverage in their corresponding `tests/` area. When behavior is user-facing, include the Chinese Studio text and both desktop/mobile rendering implications in the pull request description.

## Integration Requirements

### Provider and model changes

- Declare the exact supported capability; do not infer it from a model name.
- Pin remote model versions and version/hash reviewed workflows.
- Preserve idempotency, job lease, failure recovery and artifact sidecar metadata.
- Never log Provider tokens or raw callback secrets.
- Add successful, failed and retry-path tests using fakes or local fixtures.

### Governance changes

- Do not bypass the audit trail, stage lock or tenant policy to simplify a feature.
- Model/workflow/source rights must be represented as evidence, not free-form claims.
- A signer configuration is not proof of C2PA verification; keep signing and verification states distinct.

### Deployment changes

- Validate Compose and Kubernetes manifests.
- Keep secrets as references or examples only; use GitHub Environments or the target secret manager for values.
- Explain effect on control-plane lease, shared artifact storage, Workers and rollback in the PR.

## Pull Request Standard

A PR should include:

- Problem and user-facing effect.
- Scope, deliberately excluded work and compatibility notes.
- Test commands and results.
- Screenshots for Studio changes, or API request/response examples for contract changes.
- Migration, configuration, cost and security implications where relevant.
- Rollback plan for production-impacting changes.

Maintainers should require at least one review for ordinary changes and explicit owner approval for production configuration, security, identity, license policy and release workflow changes.

## Documentation

Update the appropriate document with every meaningful behavior change:

| Change | Update |
| --- | --- |
| Product workflow or UI | `README.md` and `docs/architecture.md` |
| Environment variable or provider contract | `docs/configuration.md`, `.env.example`, adapter documentation |
| Production operation | `docs/production-deployment.md` and `docs/release-checklist.md` |
| Runtime endpoint or implementation detail | `docs/runtime-reference.md` |

## Reporting Security Issues

Do not open public issues for suspected vulnerabilities, credentials, tenant isolation failures or unpublished media exposure. Follow [SECURITY.md](SECURITY.md).
