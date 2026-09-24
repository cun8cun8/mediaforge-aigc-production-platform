# MediaForge Helm Chart

This chart is the Helm equivalent of the repository Kubernetes manifests. It
deploys an active/passive API control plane, an optional GPU Worker, a shared
artifact PVC, probes, a PDB, optional HPA and optional Ingress. It does not
create a database, Redis, object storage, identity provider, provider account,
certificate, GPU model image, or runtime Secret.

## Prerequisites

1. Publish an immutable application image.
2. Provision PostgreSQL, Redis, private S3/MinIO storage and an RWX storage
   class or an existing RWX PVC.
3. Create the runtime Secret through the organization's secret manager. Do not
   put credentials in Helm values files or command-line value overrides.
4. Configure an OIDC application and a real Provider separately.

The default mediaforge-runtime Secret needs these keys:

| Required key | Purpose |
| --- | --- |
| database-url | PostgreSQL application DSN |
| redis-url | Authenticated Redis DSN |
| storage-endpoint | S3/MinIO endpoint |
| api-keys | Break-glass API-key policy or Worker token policy |
| worker-token | GPU Worker API token |
| temporal-worker-token | Tenant-bound `orchestrator` API token for the optional Temporal Worker |
| callback-secret | MediaForge Provider callback HMAC secret |
| metrics-token | Metrics bearer token mounted as a file |

Optional keys include storage-access-key-id, storage-secret-access-key and
oidc-client-secret. For C2PA, mount only the signing service's own Secret or
HSM/KMS credentials and configure its non-secret file references in
runtime.config; do not mount a signing private key into the API container.

## Install

Create a non-secret values file:

~~~yaml
image:
  reference: ghcr.io/example/mediaforge@sha256:replace-with-immutable-digest

runtime:
  existingSecret: mediaforge-runtime
  providerConfigMap:
    name: mediaforge-provider
  config:
    MEDIAFORGE_AUTH_MODE: oidc
    MEDIAFORGE_OIDC_INTROSPECTION_URL: https://identity.example.com/oauth2/introspect
    MEDIAFORGE_OIDC_CLIENT_ID: mediaforge
    MEDIAFORGE_OIDC_AUTHORIZATION_URL: https://identity.example.com/oauth2/authorize
    MEDIAFORGE_OIDC_TOKEN_URL: https://identity.example.com/oauth2/token
    MEDIAFORGE_OIDC_REDIRECT_URI: https://studio.example.com/auth/callback
    MEDIAFORGE_STORAGE_BUCKET: production-mediaforge

persistence:
  existingClaim: mediaforge-rwx

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: studio.example.com
      paths:
        - path: /
          pathType: Prefix
~~~

Render first, then apply:

~~~sh
helm lint helm/mediaforge
helm template mediaforge helm/mediaforge \
  --namespace mediaforge \
  --values production-values.yaml > mediaforge-rendered.yaml
kubectl apply --server-side --dry-run=server -f mediaforge-rendered.yaml
helm upgrade --install mediaforge helm/mediaforge \
  --namespace mediaforge --create-namespace \
  --values production-values.yaml
~~~

To enable the GPU Worker, set worker.enabled=true only after the Provider
ConfigMap, NVIDIA device plugin, worker image and GPU scheduling policy are
ready. The Worker and API intentionally share the artifact claim.

To enable durable orchestration, set `temporal.enabled=true` after Temporal
Server or Temporal Cloud, namespace, and a separate tenant-bound Worker token
are ready. The `api-keys` Secret value must map that token to
`{"subject":"temporal-worker","role":"orchestrator","tenant_id":"<tenant>"}`.
The `orchestrator` identity cannot access Studio or general project APIs: it is
restricted to generation submission, export, packaging, and dispatch Activity
paths for its tenant. The Worker readiness probe checks Temporal and the active
MediaForge control plane without submitting media work.

## Guardrails

- Two API replicas require a RWX claim. Template rendering fails when the
  access mode is not ReadWriteMany.
- The chart requires runtime.existingSecret; it never synthesizes credentials.
- Runtime ConfigMap values matching secret, password, API key, token or access
  key are rejected unless they are a mounted file path ending in _FILE.
- The default control plane is leased, the default browser session is expected
  to use PostgreSQL in this topology. The default authentication mode is
  required API keys; set OIDC mode only with the complete provider configuration.
- Ingress and HPA are opt-in. Network policies are cluster-specific and should
  be added by a reviewed environment overlay with exact database, identity,
  Provider, object-storage and monitoring destinations.
- A successful Helm install is not production acceptance. Run enterprise probe,
  readiness, operations alert, real Provider probe and strict production
  acceptance through the public gateway before routing commercial traffic.
