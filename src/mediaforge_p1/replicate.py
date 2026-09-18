from __future__ import annotations

import base64
import json
import math
import mimetypes
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .contracts import Artifact, Capability, GenerationSpec
from .media import sha256_file


class ReplicateProviderError(RuntimeError):
    """Raised when the cloud prediction contract cannot be completed."""


InputBuilder = Callable[[GenerationSpec], dict[str, Any]]
_MAX_DATA_URI_BYTES = 256 * 1024


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
    cancel_after_seconds: float | None = None
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
        if self.cancel_after_seconds is not None and not (
            5 <= self.cancel_after_seconds <= 24 * 60 * 60
        ):
            raise ValueError(
                "cancel_after_seconds must be between 5 and 86400 seconds"
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
                    "request": {
                        "idempotency_key": self._idempotency_key(job_id),
                        "http_retry_attempts": self.http_retry_attempts,
                        "http_retry_backoff_seconds": self.http_retry_backoff_seconds,
                        "cancel_after": cancel_after,
                        "remote_idempotency": "provider-header",
                    },
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
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read(), response.headers.get("Content-Type")
            except HTTPError as exc:
                if (
                    not self._is_retryable_http_error(
                        method,
                        exc,
                        idempotency_key=idempotency_key,
                    )
                    or attempt >= attempts - 1
                ):
                    detail = getattr(exc, "reason", str(exc))
                    raise ReplicateProviderError(
                        f"Replicate request failed: {method} {url}: {detail}"
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
            time.sleep(self.http_retry_backoff_seconds * (2**attempt))
        raise ReplicateProviderError(
            f"Replicate request failed: {method} {url}: retry limit reached"
        )

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
