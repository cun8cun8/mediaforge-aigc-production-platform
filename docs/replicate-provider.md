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

Prediction creation sends `Idempotency-Key: mediaforge-{job_id}`. Only
transient responses are retried, and a `POST` is retried only when that stable
key is present. Network failures on polling and output downloads can be
retried as `GET` requests; a non-idempotent `POST` is failed immediately.
These controls are separate from the MediaForge Job retry policy, which
creates a new execution attempt after the Provider call has failed.

The default input builder is only a smoke-test shape. Every production model
must provide a reviewed input builder because model input names and output
formats are model-specific.

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

The adapter does not store the API token in a job record, prompt, trace payload,
or artifact metadata. The artifact `metadata_uri` is carried into the asset
inventory, trace, Provenance report, and delivery ZIP so operators can audit
the prediction ID and pinned model version after the remote URL expires.
