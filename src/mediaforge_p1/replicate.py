from __future__ import annotations

import base64
import json
import math
import mimetypes
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .contracts import Artifact, Capability, GenerationSpec
from .media import sha256_file


class ReplicateProviderError(RuntimeError):
    """Raised when the cloud prediction contract cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


InputBuilder = Callable[[GenerationSpec], dict[str, Any]]
_MAX_DATA_URI_BYTES = 256 * 1024


@dataclass(frozen=True)
class ReplicateSubmission:
    prediction_id: str
    status: str
    webhook_url: str
    prediction: dict[str, Any]


@dataclass
class ReplicateVideoProvider:
    """Version-pinned async video adapter using Replicate's HTTP API."""

    api_token: str
    version: str
    input_builder: InputBuilder | None = None
    base_url: str = "https://api.replicate.com/v1"
    timeout_seconds: float = 180.0
    poll_interval_seconds: float = 1.0
    estimated_cost: float = 0.20
    http_retry_attempts: int = 2
    http_retry_backoff_seconds: float = 0.5
    http_retry_max_delay_seconds: float = 15.0
    cancel_after_seconds: float | None = None
    cancel_request_timeout_seconds: float = 15.0
    webhook_url_template: str | None = None
    name: str = "replicate-video"

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        if self.http_retry_attempts < 0:
            raise ValueError("http_retry_attempts must be >= 0")
        if self.http_retry_backoff_seconds < 0:
            raise ValueError("http_retry_backoff_seconds must be >= 0")
        if not 0 <= self.http_retry_max_delay_seconds <= 300:
            raise ValueError(
                "http_retry_max_delay_seconds must be between 0 and 300 seconds"
            )
        if self.cancel_after_seconds is not None and not (
            5 <= self.cancel_after_seconds <= 24 * 60 * 60
        ):
            raise ValueError(
                "cancel_after_seconds must be between 5 and 86400 seconds"
            )
        if not 1 <= self.cancel_request_timeout_seconds <= 300:
            raise ValueError(
                "cancel_request_timeout_seconds must be between 1 and 300 seconds"
            )
        if self.webhook_url_template is not None:
            template = self.webhook_url_template.strip()
            if not template:
                raise ValueError("webhook_url_template must not be blank")
            missing = {
                placeholder
                for placeholder in {"{project_id}", "{job_id}"}
                if placeholder not in template
            }
            if missing:
                raise ValueError(
                    "webhook_url_template must include "
                    f"{', '.join(sorted(missing))}"
                )

    def supports(self, capability: Capability) -> bool:
        return capability == Capability.IMAGE_TO_VIDEO

    def estimate_cost(self, spec: GenerationSpec) -> float:
        return self.estimated_cost

    def health_check(self) -> dict[str, Any]:
        started = perf_counter()
        try:
            payload = self._request_json("GET", "/models?limit=1")
        except ReplicateProviderError as exc:
            return {
                "reachable": False,
                "message": str(exc),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "details": {"endpoint": "/models?limit=1"},
            }
        return {
            "reachable": True,
            "message": "Replicate API is reachable.",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "details": {
                "endpoint": "/models?limit=1",
                "response_keys": sorted(payload.keys()),
            },
        }

    def generate(
        self,
        spec: GenerationSpec,
        *,
        job_id: str,
        output_dir: Path,
    ) -> Artifact:
        if not self.supports(spec.provider_constraints.capability):
            raise ReplicateProviderError(
                "ReplicateVideoProvider only supports image_to_video"
            )
        payload = {
            "version": self.version,
            "input": self._build_input(spec),
        }
        prediction_headers = {"Prefer": "wait=1"}
        cancel_after = self._cancel_after_header()
        if cancel_after:
            prediction_headers["Cancel-After"] = cancel_after
        prediction = self._request_json(
            "POST",
            "/predictions",
            body=payload,
            headers=prediction_headers,
            idempotency_key=self._idempotency_key(job_id),
        )
        prediction = self._poll_prediction(prediction, job_id=job_id)
        return self._archive_prediction_output(
            prediction,
            spec=spec,
            job_id=job_id,
            output_dir=output_dir,
            request_metadata={
                "idempotency_key": self._idempotency_key(job_id),
                "http_retry_attempts": self.http_retry_attempts,
                "http_retry_backoff_seconds": self.http_retry_backoff_seconds,
                "cancel_after": cancel_after,
                "remote_idempotency": "provider-header",
                "completion_mode": "polling",
            },
        )

    def submit_webhook_prediction(
        self,
        spec: GenerationSpec,
        *,
        job_id: str,
    ) -> ReplicateSubmission:
        """Start an async prediction and ask Replicate for terminal webhooks."""
        if not self.supports(spec.provider_constraints.capability):
            raise ReplicateProviderError(
                "ReplicateVideoProvider only supports image_to_video"
            )
        webhook_url = self._render_webhook_url(spec.project_id, job_id)
        payload = {
            "version": self.version,
            "input": self._build_input(spec),
            "webhook": webhook_url,
            "webhook_events_filter": ["completed"],
        }
        headers: dict[str, str] = {}
        cancel_after = self._cancel_after_header()
        if cancel_after:
            headers["Cancel-After"] = cancel_after
        prediction = self._request_json(
            "POST",
            "/predictions",
            body=payload,
            headers=headers or None,
            idempotency_key=self._idempotency_key(job_id),
        )
        prediction_id = str(prediction.get("id") or "").strip()
        if not prediction_id:
            raise ReplicateProviderError(
                "prediction response has no id for webhook reconciliation"
            )
        return ReplicateSubmission(
            prediction_id=prediction_id,
            status=str(prediction.get("status") or "starting"),
            webhook_url=webhook_url,
            prediction=prediction,
        )

    def archive_webhook_prediction(
        self,
        prediction: dict[str, Any],
        *,
        spec: GenerationSpec,
        job_id: str,
        output_dir: Path,
    ) -> Artifact:
        """Persist a verified terminal webhook output before its remote URL expires."""
        return self._archive_prediction_output(
            prediction,
            spec=spec,
            job_id=job_id,
            output_dir=output_dir,
            request_metadata={
                "idempotency_key": self._idempotency_key(job_id),
                "cancel_after": self._cancel_after_header(),
                "remote_idempotency": "provider-webhook",
                "completion_mode": "webhook",
            },
        )

    def cancel_webhook_submission(
        self,
        submission: ReplicateSubmission,
        *,
        job_id: str,
    ) -> dict[str, Any]:
        """Best-effort cleanup if MediaForge cannot persist a submission binding."""
        return self._cancel_prediction(
            submission.prediction,
            prediction_id=submission.prediction_id,
            job_id=job_id,
        )

    def cancel_prediction(
        self,
        prediction_id: str,
        *,
        job_id: str,
    ) -> dict[str, Any]:
        """Request cancellation for a persisted asynchronous prediction."""
        clean_prediction_id = prediction_id.strip()
        if not clean_prediction_id:
            return {"requested": False, "detail": "prediction id is blank"}
        return self._cancel_prediction(
            {"id": clean_prediction_id},
            prediction_id=clean_prediction_id,
            job_id=job_id,
        )

    def _archive_prediction_output(
        self,
        prediction: dict[str, Any],
        *,
        spec: GenerationSpec,
        job_id: str,
        output_dir: Path,
        request_metadata: dict[str, Any],
    ) -> Artifact:
        output_url = self._first_output_url(prediction.get("output"))
        # Output URLs may be hosted on a different domain. Never forward the
        # Provider API token to that file host.
        content, content_type = self._request_bytes(output_url)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = Path(urlparse(output_url).path).name or "output.mp4"
        if "." not in output_name:
            output_name = f"{output_name}.mp4"
        output_path = output_dir / f"{job_id}_{output_name}"
        output_path.write_bytes(content)
        metadata_path = output_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": "mediaforge-artifact-metadata-v1",
                    "provider": self.name,
                    "job_id": job_id,
                    "prediction": prediction,
                    "version": self.version,
                    "request": request_metadata,
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return Artifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            job_id=job_id,
            kind="video",
            uri=str(output_path),
            mime_type=content_type
            or mimetypes.guess_type(output_name)[0]
            or "video/mp4",
            sha256=sha256_file(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=spec.intent.duration_seconds,
            metadata_uri=str(metadata_path),
            created_at=datetime.now(timezone.utc),
        )

    def _render_webhook_url(self, project_id: str, job_id: str) -> str:
        template = (self.webhook_url_template or "").strip()
        if not template:
            raise ReplicateProviderError(
                "REPLICATE_WEBHOOK_URL_TEMPLATE is required for webhook mode"
            )
        try:
            url = template.format(
                project_id=quote(project_id, safe=""),
                job_id=quote(job_id, safe=""),
            )
        except (KeyError, ValueError) as exc:
            raise ReplicateProviderError(
                "REPLICATE_WEBHOOK_URL_TEMPLATE is invalid"
            ) from exc
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ReplicateProviderError(
                "REPLICATE_WEBHOOK_URL_TEMPLATE must render an absolute HTTP(S) URL"
            )
        return url

    def _build_input(self, spec: GenerationSpec) -> dict[str, Any]:
        if self.input_builder is not None:
            return self.input_builder(spec)
        payload = {
            "prompt": f"{spec.intent.mood} cinematic shot {spec.shot_id}",
            "duration": int(spec.intent.duration_seconds),
        }
        reference_images = [
            self._reference_input(reference)
            for reference in spec.reference_assets
            if reference.uri
        ]
        if reference_images:
            payload["reference_images"] = reference_images
        return payload

    @staticmethod
    def _reference_input(reference: Any) -> str:
        uri = str(reference.uri)
        if uri.startswith(("http://", "https://", "data:")):
            return uri

        path = Path(uri)
        if not path.is_file():
            raise ReplicateProviderError(
                f"Replicate reference image not found: {path}"
            )
        content = path.read_bytes()
        if len(content) > _MAX_DATA_URI_BYTES:
            raise ReplicateProviderError(
                "Replicate reference image exceeds 256 KiB; provide a hosted "
                "HTTP(S) URL or a custom input_builder"
            )
        mime_type = (
            str(getattr(reference, "mime_type", None) or "").strip()
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _poll_prediction(
        self,
        prediction: dict[str, Any],
        *,
        job_id: str,
    ) -> dict[str, Any]:
        prediction_url = prediction.get("urls", {}).get("get")
        prediction_id = prediction.get("id")
        if not prediction_url and prediction_id:
            prediction_url = f"{self.base_url.rstrip('/')}/predictions/{prediction_id}"
        if not prediction_url:
            raise ReplicateProviderError(
                f"prediction response has no polling URL: {prediction!r}"
            )

        deadline = time.monotonic() + self.timeout_seconds
        current = prediction
        while time.monotonic() < deadline:
            status = current.get("status")
            if status == "succeeded":
                return current
            if status in {"failed", "canceled"}:
                raise ReplicateProviderError(
                    f"prediction ended with status {status}: {current.get('error')}"
                )
            time.sleep(self.poll_interval_seconds)
            current = self._request_json("GET", prediction_url)
        cancellation = self._cancel_prediction(
            current,
            prediction_id=str(prediction_id or ""),
            job_id=job_id,
        )
        cancellation_detail = (
            "remote cancellation requested"
            if cancellation["requested"]
            else f"remote cancellation unavailable: {cancellation['detail']}"
        )
        raise ReplicateProviderError(
            "timed out waiting for prediction "
            f"{prediction_id or prediction_url}; {cancellation_detail}"
        )

    def _cancel_prediction(
        self,
        prediction: dict[str, Any],
        *,
        prediction_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        urls = prediction.get("urls")
        cancel_url = urls.get("cancel") if isinstance(urls, dict) else None
        if not cancel_url and prediction_id:
            cancel_url = (
                f"{self.base_url.rstrip('/')}/predictions/{prediction_id}/cancel"
            )
        if not isinstance(cancel_url, str) or not cancel_url.startswith(
            ("http://", "https://")
        ):
            return {"requested": False, "detail": "prediction response has no cancel URL"}
        try:
            raw, _ = self._request(
                "POST",
                cancel_url,
                idempotency_key=self._cancel_idempotency_key(job_id),
                timeout_seconds=self.cancel_request_timeout_seconds,
            )
        except ReplicateProviderError as exc:
            return {"requested": False, "detail": str(exc)}
        try:
            response = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {}
        provider_status = (
            response.get("status") if isinstance(response, dict) else None
        )
        return {
            "requested": True,
            "detail": f"provider status {provider_status or 'accepted'}",
        }

    def _cancel_after_header(self) -> str | None:
        seconds = (
            self.cancel_after_seconds
            if self.cancel_after_seconds is not None
            else self.timeout_seconds
        )
        if not 5 <= seconds <= 24 * 60 * 60:
            return None
        return f"{math.ceil(seconds)}s"

    @staticmethod
    def _first_output_url(output: Any) -> str:
        candidates: list[Any]
        if isinstance(output, list):
            candidates = output
        else:
            candidates = [output]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate
            if isinstance(candidate, dict):
                url = candidate.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
        raise ReplicateProviderError(f"prediction has no downloadable output: {output!r}")

    def _request_json(
        self,
        method: str,
        target: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        raw, _ = self._request(
            method,
            target,
            body=body,
            headers=headers,
            idempotency_key=idempotency_key,
        )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ReplicateProviderError(
                f"Replicate returned invalid JSON for {method} {target}"
            ) from exc
        if not isinstance(decoded, dict):
            raise ReplicateProviderError(
                f"Replicate returned a non-object response for {method} {target}"
            )
        return decoded

    def _request_bytes(self, target: str) -> tuple[bytes, str | None]:
        return self._request("GET", target, include_auth=False)

    def _request(
        self,
        method: str,
        target: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        include_auth: bool = True,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[bytes, str | None]:
        url = target if target.startswith(("http://", "https://")) else (
            f"{self.base_url.rstrip('/')}/{target.lstrip('/')}"
        )
        request_headers = {
            "Accept": "application/json",
        }
        if include_auth:
            request_headers["Authorization"] = f"Bearer {self.api_token}"
        if headers:
            request_headers.update(headers)
        if idempotency_key:
            request_headers["Idempotency-Key"] = idempotency_key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_timeout_seconds = timeout_seconds or self.timeout_seconds
        attempts = self.http_retry_attempts + 1
        for attempt in range(attempts):
            # Rebuild the request for every attempt so urllib can safely
            # replay requests with a JSON body after a transient response.
            request = Request(
                url,
                data=data,
                headers=request_headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=request_timeout_seconds) as response:
                    return response.read(), response.headers.get("Content-Type")
            except HTTPError as exc:
                retry_after = self._retry_after_seconds(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                if (
                    not self._is_retryable_http_error(
                        method,
                        exc,
                        idempotency_key=idempotency_key,
                    )
                    or attempt >= attempts - 1
                ):
                    raise self._request_error(
                        method,
                        url,
                        exc,
                        retry_after_seconds=retry_after,
                    ) from exc
                retry_delay = max(
                    self.http_retry_backoff_seconds * (2**attempt),
                    retry_after or 0,
                )
                if retry_delay > self.http_retry_max_delay_seconds:
                    raise self._request_error(
                        method,
                        url,
                        exc,
                        retry_after_seconds=retry_after,
                    ) from exc
            except (URLError, TimeoutError) as exc:
                retryable_network_error = method.upper() in {
                    "GET",
                    "HEAD",
                    "OPTIONS",
                } or (method.upper() == "POST" and bool(idempotency_key))
                if not retryable_network_error or attempt >= attempts - 1:
                    detail = getattr(exc, "reason", str(exc))
                    raise ReplicateProviderError(
                        f"Replicate request failed: {method} {url}: {detail}"
                    ) from exc
                retry_delay = self.http_retry_backoff_seconds * (2**attempt)
            time.sleep(retry_delay)
        raise ReplicateProviderError(
            f"Replicate request failed: {method} {url}: retry limit reached"
        )

    @staticmethod
    def _request_error(
        method: str,
        url: str,
        error: HTTPError,
        *,
        retry_after_seconds: float | None,
    ) -> ReplicateProviderError:
        detail = getattr(error, "reason", str(error))
        retry_detail = (
            f"; retry after {math.ceil(retry_after_seconds)}s"
            if retry_after_seconds is not None
            else ""
        )
        return ReplicateProviderError(
            f"Replicate request failed: {method} {url}: {detail}{retry_detail}",
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        """Read an RFC 7231 Retry-After value without trusting unbounded delays."""
        raw = (value or "").strip()
        if not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        return min(math.ceil(seconds), 24 * 60 * 60)

    @staticmethod
    def _idempotency_key(job_id: str) -> str:
        return f"mediaforge-{job_id}"

    @staticmethod
    def _cancel_idempotency_key(job_id: str) -> str:
        return f"mediaforge-cancel-{job_id}"

    @staticmethod
    def _is_retryable_http_error(
        method: str,
        error: HTTPError,
        *,
        idempotency_key: str | None = None,
    ) -> bool:
        method_name = method.upper()
        if method_name == "POST" and not idempotency_key:
            return False
        return method_name in {"GET", "HEAD", "OPTIONS", "POST"} and error.code in {
            408,
            409,
            425,
            429,
            500,
            502,
            503,
            504,
        }
