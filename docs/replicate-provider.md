# Cloud Video Provider Adapter

`src/mediaforge_p1/replicate.py` implements a version-pinned asynchronous
video adapter using a Replicate-compatible HTTP API.

## Configuration

The adapter requires:

- an API token supplied by the runtime secret manager;
- a pinned model version;
- an optional input builder for the selected model's input schema;
- a deadline and polling interval.

The HTTP retry controls are configured with:

- `REPLICATE_HTTP_RETRY_ATTEMPTS` (default `2`);
- `REPLICATE_HTTP_RETRY_BACKOFF_SECONDS` (default `0.5`).
- `REPLICATE_TIMEOUT_SECONDS` (default `180`);
- `REPLICATE_CANCEL_REQUEST_TIMEOUT_SECONDS` (default `15`, valid range `1` to `300`);
- `REPLICATE_POLL_INTERVAL_SECONDS` (default `1`);
- `REPLICATE_CANCEL_AFTER_SECONDS` (default follows the local timeout; valid range `5` to `86400`).

Prediction creation sends `Idempotency-Key: mediaforge-{job_id}`. Only
transient responses are retried, and a `POST` is retried only when that stable
key is present. Network failures on polling and output downloads can be
retried as `GET` requests; a non-idempotent `POST` is failed immediately.
These controls are separate from the MediaForge Job retry policy, which
creates a new execution attempt after the Provider call has failed.

The default input builder is only a smoke-test shape. Every production model
must provide a reviewed input builder because model input names and output
formats are model-specific.

Prediction creation includes Replicate's `Cancel-After` header. On a local
polling timeout, the adapter also calls the response's `urls.cancel` endpoint
with a separate stable cancellation idempotency key. This limits orphaned
remote jobs and records whether the terminal Provider request was accepted in
the job failure reason. A failure to cancel does not hide the original timeout.
The same bounded cancellation request is issued when an operator cancels a
Replicate native-webhook Job after its prediction ID has been bound to the Job.
The control plane records the attempt and result in `job.canceled`; an outage
in the Provider cancellation endpoint does not prevent the local Job from
entering `CANCELED`.

已登记的参考图会进入 `GenerationSpec.reference_assets`，每项包含资产 ID、版本、
本地 URI、SHA-256、许可证和来源。默认输入构造器会把参考图放入
`reference_images`：HTTP(S) 或 Data URL 原样传递，本地文件会编码为 Data URL。
Data URL 适用于不超过 256 KiB 的小文件；更大的文件必须提供可访问的 HTTP(S)
URL，或由自定义 `input_builder` 按模型要求映射为 `image`、`images` 等字段。

## Execution flow

1. Submit a version-pinned prediction.
2. Poll the prediction URL until `succeeded`, `failed`, or `canceled`.
3. Select the first downloadable output URL.
4. Download the output immediately into the MediaForge artifact store.
5. Persist the raw prediction response in a
   `mediaforge-artifact-metadata-v1` sidecar beside the artifact.

The sidecar also records the idempotency strategy and retry settings. It never
stores the API token.

## Native Webhook Mode

Polling remains the default. For long-running production jobs, a dedicated
Worker can submit an asynchronous prediction and let Replicate deliver only the
terminal event. Configure a public HTTPS URL template and the signing secret
retrieved from Replicate's webhook settings:

```text
REPLICATE_WEBHOOK_URL_TEMPLATE=https://studio.example.com/providers/replicate/webhook?project_id={project_id}&job_id={job_id}
REPLICATE_WEBHOOK_SIGNING_SECRET=whsec_<base64-key>
REPLICATE_WEBHOOK_MAX_AGE_SECONDS=300
MEDIAFORGE_WORKER_EXECUTION_MODE=replicate-webhook
MEDIAFORGE_WORKER_PROVIDER=replicate
MEDIAFORGE_CALLBACK_SECRET=<worker-to-control-plane-secret>
```

The template must retain both placeholders. The Worker submits the prediction,
then records the returned prediction ID through the signed MediaForge callback.
`POST /providers/replicate/webhook` verifies Replicate's `webhook-id`,
`webhook-timestamp`, and `webhook-signature` headers against the raw body;
only a delivery whose prediction ID matches the recorded Job can advance it.
For successful terminal events, the API downloads the output into the project
artifact directory before it applies normal quality, audit, billing, and retry
rules. Duplicate or late terminal deliveries are acknowledged without
downloading the output again.

The adapter does not store the API token in a job record, prompt, trace payload,
or artifact metadata. The artifact `metadata_uri` is carried into the asset
inventory, trace, Provenance report, and delivery ZIP so operators can audit
the prediction ID and pinned model version after the remote URL expires.
