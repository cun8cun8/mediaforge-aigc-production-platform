# Production Release Checklist

This checklist is the deployment decision record for MediaForge. A green CI run alone is not a production approval: Provider credentials, rights records, identity integration, storage permissions and GPU model output need environment-specific verification.

## 1. Code And Supply Chain

- [ ] Pull request is reviewed and CI is green: Python tests, browser acceptance, Compose/Kubernetes rendering and image build.
- [ ] Release image is pinned by immutable digest, not a mutable tag.
- [ ] Dependency and container findings have an assigned disposition.
- [ ] No `.env`, `ops/secrets/`, generated artifacts, raw customer content or model weights are committed.
- [ ] Production configuration is injected through GitHub Environment secrets or the deployment secret manager.

## 2. Provider And GPU Validation

- [ ] Each enabled Provider is `READY` in `/providers/diagnostics`.
- [ ] `mediaforge-production-acceptance --require-production` reports passing Provider contracts, callback signing and ComfyUI workflow governance.
- [ ] ComfyUI workflow registry entries have version, SHA-256 and approved model inventory.
- [ ] Cloud model versions are explicitly pinned; budget estimates and usage limits are reviewed.
- [ ] `MEDIAFORGE_CALLBACK_SECRET` is present and the callback max-age policy is appropriate.
- [ ] A real Provider Probe was approved for cost and produced a recent signed artifact receipt for every enabled real Provider.
- [ ] At least one Worker per enabled local Provider has healthy heartbeat, capability match and GPU admission.
- [ ] Failed Provider execution, stale lease recovery and manual retry were exercised in staging.

## 3. Data, Storage And Recovery

- [ ] PostgreSQL state is durable, backed up and uses a least-privilege application role.
- [ ] Redis queue access is authenticated and reachable from API and Workers.
- [ ] API replicas and GPU Workers share the required writable artifact root.
- [ ] S3/MinIO bucket is private, encrypted per platform policy, versioned and limited to intended prefixes.
- [ ] Delivery dispatch writes object URI, SHA-256 and size receipt only after upload verification.
- [ ] Backup creation, backup verification and an isolated restore drill passed against the target release shape.

## 4. Identity, Tenant And Network Security

- [ ] `MEDIAFORGE_AUTH_MODE=oidc` or an explicitly approved temporary API-key policy is active.
- [ ] OIDC issuer, client, redirect URI, role claim and tenant claim map to the intended organization.
- [ ] Browser SSO uses HTTPS and `MEDIAFORGE_OIDC_COOKIE_SECURE=true`.
- [ ] Test accounts verify viewer, reviewer, editor, publisher, provider and admin boundaries.
- [ ] Tenant isolation is checked for projects, artifacts, queue claims and cost reports.
- [ ] Reverse proxy and Kubernetes policies allow only required ingress and egress to identity, storage, Provider, monitoring and worker endpoints.

## 5. Governance And Delivery Acceptance

- [ ] Stage locking is enabled: `MEDIAFORGE_REQUIRE_STAGE_LOCKS=true`.
- [ ] A reference project completes the six stages without manual database mutation.
- [ ] Story/shot changes invalidate the expected downstream locks and produce audit events.
- [ ] Quality, continuity, rights and provenance reports are inspected by the designated reviewer.
- [ ] All source assets required for release have active rights evidence and valid dates.
- [ ] Delivery package builds, verifies and can be re-imported or independently inspected.
- [ ] C2PA is only marked verified after a separate verifier command succeeds; unsigned output is not represented as signed.

## 6. Observability And Operations

- [ ] `/metrics` is protected with a dedicated token and scraped by Prometheus.
- [ ] `/ops/alerts` thresholds reflect the actual queue capacity, spend ceiling and failure policy.
- [ ] Alertmanager routes to the responsible on-call destination and was tested with a non-production alert.
- [ ] Grafana dashboard shows API errors, latency, queue age, Worker health, Provider failure rate and spend.
- [ ] Operational runbook identifies owners for Provider outage, GPU failure, database recovery, rights escalation and delivery rollback.

## 7. Final Commands

Run through the staging gateway, not directly against an individual standby API:

```powershell
Invoke-RestMethod https://staging.example.com/enterprise/probe
Invoke-RestMethod https://staging.example.com/ops/readiness
Invoke-RestMethod https://staging.example.com/ops/alerts

mediaforge-production-acceptance `
  --base-url https://staging.example.com `
  --token $env:MEDIAFORGE_READINESS_TOKEN `
  --probe-enterprise `
  --probe-planning `
  --provider-probe-receipt artifacts/provider-probe/manifest.json `
  --provider-probe-secret-file ops/secrets/provider-probe-receipt-secret `
  --require-production
```

Record the image digest, acceptance report location, configuration revision, approver, rollback target and any accepted risks in the release ticket. The GitHub workflow performs server-side dry run by default; selecting `apply` must be protected by a GitHub Environment with required reviewers.
