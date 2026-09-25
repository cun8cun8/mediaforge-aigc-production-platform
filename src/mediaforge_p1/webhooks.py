from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from threading import Lock, Thread
from time import sleep, time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


@dataclass
class WebhookDispatcher:
    """Asynchronous event fan-out with bounded delivery retries."""

    urls: list[str] = field(default_factory=list)
    secret: str = ""
    timeout_seconds: float = 5.0
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.5
    cloud_events: bool = False
    bearer_token: str = ""
    cloud_event_source: str = "urn:mediaforge:production"
    outbox_path: Path | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _sent: int = field(default=0, init=False)
    _failed: int = field(default=0, init=False)
    _last_error: str | None = field(default=None, init=False)
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _inflight: set[str] = field(default_factory=set, init=False, repr=False)

    @classmethod
    def from_env(cls) -> "WebhookDispatcher":
        raw_urls = os.getenv("MEDIAFORGE_WEBHOOK_URLS", "").strip()
        urls = [item.strip() for item in raw_urls.split(",") if item.strip()]
        try:
            timeout = float(os.getenv("MEDIAFORGE_WEBHOOK_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_WEBHOOK_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise ValueError("MEDIAFORGE_WEBHOOK_TIMEOUT_SECONDS must be > 0")
        try:
            retry_attempts = int(os.getenv("MEDIAFORGE_WEBHOOK_RETRY_ATTEMPTS", "2"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_WEBHOOK_RETRY_ATTEMPTS must be an integer") from exc
        if retry_attempts < 0:
            raise ValueError("MEDIAFORGE_WEBHOOK_RETRY_ATTEMPTS must be >= 0")
        try:
            retry_backoff_seconds = float(
                os.getenv("MEDIAFORGE_WEBHOOK_RETRY_BACKOFF_SECONDS", "0.5")
            )
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_WEBHOOK_RETRY_BACKOFF_SECONDS must be a number"
            ) from exc
        if retry_backoff_seconds < 0:
            raise ValueError(
                "MEDIAFORGE_WEBHOOK_RETRY_BACKOFF_SECONDS must be >= 0"
            )
        return cls(
            urls=urls,
            secret=os.getenv("MEDIAFORGE_WEBHOOK_SECRET", "").strip(),
            timeout_seconds=timeout,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    @classmethod
    def from_siem_env(cls) -> "WebhookDispatcher":
        """Build a CloudEvents sink with explicit outbound destination controls."""
        raw_urls = os.getenv("MEDIAFORGE_SIEM_URLS", "").strip()
        urls = [item.strip() for item in raw_urls.split(",") if item.strip()]
        allowed_hosts = {
            item.strip().lower()
            for item in os.getenv("MEDIAFORGE_SIEM_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        }
        allow_http = os.getenv("MEDIAFORGE_SIEM_ALLOW_INSECURE_HTTP", "false").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if urls and not allowed_hosts:
            raise ValueError(
                "MEDIAFORGE_SIEM_ALLOWED_HOSTS is required when MEDIAFORGE_SIEM_URLS is set"
            )
        for url in urls:
            parsed = urlsplit(url)
            scheme_allowed = parsed.scheme == "https" or (
                allow_http and parsed.scheme == "http"
            )
            if not scheme_allowed or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MEDIAFORGE_SIEM_URLS must use an allowed HTTP(S) endpoint")
            if parsed.hostname.lower() not in allowed_hosts:
                raise ValueError("MEDIAFORGE_SIEM_URLS host is not in MEDIAFORGE_SIEM_ALLOWED_HOSTS")
        try:
            timeout = float(os.getenv("MEDIAFORGE_SIEM_TIMEOUT_SECONDS", "5"))
            retries = int(os.getenv("MEDIAFORGE_SIEM_RETRY_ATTEMPTS", "2"))
            backoff = float(os.getenv("MEDIAFORGE_SIEM_RETRY_BACKOFF_SECONDS", "0.5"))
        except ValueError as exc:
            raise ValueError("SIEM timeout, retry attempts and backoff must be numeric") from exc
        if timeout <= 0 or retries < 0 or backoff < 0:
            raise ValueError("SIEM timeout must be positive; retries and backoff cannot be negative")
        source = os.getenv("MEDIAFORGE_SIEM_SOURCE", "urn:mediaforge:production").strip()
        if not source or len(source) > 500 or any(character.isspace() for character in source):
            raise ValueError("MEDIAFORGE_SIEM_SOURCE must be a non-empty identifier without whitespace")
        return cls(
            urls=urls,
            secret=os.getenv("MEDIAFORGE_SIEM_SIGNING_SECRET", "").strip(),
            timeout_seconds=timeout,
            retry_attempts=retries,
            retry_backoff_seconds=backoff,
            cloud_events=True,
            bearer_token=os.getenv("MEDIAFORGE_SIEM_BEARER_TOKEN", "").strip(),
            cloud_event_source=source,
        )

    @property
    def configured(self) -> bool:
        return bool(self.urls)

    def status_view(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": (
                    "mediaforge-siem-cloud-events-v1"
                    if self.cloud_events
                    else "mediaforge-webhook-v1"
                ),
                "configured": self.configured,
                "endpoint_count": len(self.urls),
                "secret_configured": bool(self.secret),
                "bearer_token_configured": bool(self.bearer_token),
                "delivery_format": "cloudevents-1.0" if self.cloud_events else "mediaforge-event-v1",
                "retry_attempts": self.retry_attempts,
                "retry_backoff_seconds": self.retry_backoff_seconds,
                "sent": self._sent,
                "failed": self._failed,
                "last_error": self._last_error,
                "outbox_configured": self.outbox_path is not None,
                "pending": len(self._pending),
            }

    def configure_outbox(self, path: Path) -> None:
        """Attach a durable outbox and replay events left by a prior process."""
        path = path.expanduser()
        pending: list[dict[str, Any]] = []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                pending = [item for item in raw["events"] if isinstance(item, dict)]
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError):
            # A corrupt outbox must not prevent the API from starting; begin
            # with a clean queue and let the next write replace the file.
            pending = []
        with self._lock:
            self.outbox_path = path
            self._pending = {
                str(item["event_id"]): item
                for item in pending
                if item.get("event_id")
            }
            self._persist_outbox_locked()
            replay = list(self._pending.values())
        for payload in replay:
            self._dispatch(payload)

    def emit(self, *, project_id: str, event: dict[str, Any]) -> None:
        if not self.urls:
            return
        event_id = f"evt_{uuid4().hex[:20]}"
        payload = {
            "schema_version": "mediaforge-event-v1",
            "event_id": event_id,
            "project_id": project_id,
            "occurred_at": event.get("occurred_at"),
            "event": event,
        }
        if self.cloud_events:
            payload = {
                "event_id": event_id,
                "wire_payload": self._cloud_event_payload(
                    event_id=event_id,
                    project_id=project_id,
                    event=event,
                ),
            }
        with self._lock:
            self._pending[str(payload["event_id"])] = payload
            self._persist_outbox_locked()
        self._dispatch(payload)

    def retry_pending(self) -> int:
        """Schedule durable failures again without requiring an API restart."""
        with self._lock:
            pending = list(self._pending.values())
        for payload in pending:
            self._dispatch(payload)
        return len(pending)

    def _dispatch(self, payload: dict[str, Any]) -> None:
        if not self.urls:
            return
        event_id = str(payload["event_id"])
        with self._lock:
            if event_id in self._inflight:
                return
            self._inflight.add(event_id)
        Thread(
            target=self._send,
            args=(payload,),
            name="mediaforge-webhook",
            daemon=True,
        ).start()

    def _send(self, payload: dict[str, Any]) -> None:
        wire_payload = payload.get("wire_payload", payload)
        body = json.dumps(wire_payload, ensure_ascii=False).encode("utf-8")
        timestamp = str(int(time()))
        signature = ""
        if self.secret:
            signature = hmac.new(
                self.secret.encode("utf-8"),
                f"{timestamp}.".encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
        failures: list[str] = []
        opener = build_opener(_NoRedirect())
        for url in self.urls:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/cloudevents+json" if self.cloud_events else "application/json",
                "User-Agent": "mediaforge-webhook/0.1",
                "X-MediaForge-Event-Id": str(payload["event_id"]),
                "X-MediaForge-Timestamp": timestamp,
            }
            if signature:
                headers["X-MediaForge-Signature"] = f"sha256={signature}"
            if self.bearer_token:
                headers["Authorization"] = f"Bearer {self.bearer_token}"
            delivered = False
            last_error: Exception | None = None
            for attempt in range(self.retry_attempts + 1):
                request = Request(url, data=body, headers=headers, method="POST")
                retryable = False
                try:
                    with opener.open(request, timeout=self.timeout_seconds) as response:
                        response_status = int(response.status)
                        if 200 <= response_status < 300:
                            delivered = True
                            break
                        last_error = RuntimeError(f"HTTP {response_status}")
                        retryable = response_status == 408 or response_status == 429 or response_status >= 500
                except HTTPError as exc:
                    last_error = exc
                    retryable = exc.code == 408 or exc.code == 429 or exc.code >= 500
                except (URLError, TimeoutError, OSError) as exc:
                    last_error = exc
                    retryable = True
                if not retryable or attempt >= self.retry_attempts:
                    break
                sleep(self.retry_backoff_seconds * (2**attempt))
            if not delivered:
                failures.append(f"{url}: {last_error or 'delivery failed'}")
        with self._lock:
            if failures:
                self._failed += len(failures)
                self._last_error = "; ".join(failures)
            else:
                self._sent += len(self.urls)
                self._last_error = None
                self._pending.pop(str(payload["event_id"]), None)
            self._inflight.discard(str(payload["event_id"]))
            self._persist_outbox_locked()

    def _persist_outbox_locked(self) -> None:
        if self.outbox_path is None:
            return
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.outbox_path.with_suffix(
            f"{self.outbox_path.suffix}.tmp"
        )
        payload = {
            "schema_version": "mediaforge-webhook-outbox-v1",
            "updated_at": time(),
            "events": list(self._pending.values()),
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.outbox_path)

    def _cloud_event_payload(
        self,
        *,
        event_id: str,
        project_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        details_json = json.dumps(
            details,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        action = str(event.get("action") or "unknown").lower()
        event_type = re.sub(r"[^a-z0-9._-]+", "-", action).strip(".-") or "unknown"
        return {
            "specversion": "1.0",
            "id": event_id,
            "source": self.cloud_event_source,
            "type": f"com.mediaforge.audit.{event_type}",
            "subject": f"projects/{project_id}",
            "time": event.get("occurred_at"),
            "datacontenttype": "application/json",
            "data": {
                "project_id": project_id,
                "audit": {
                    "sequence": event.get("sequence"),
                    "action": event.get("action"),
                    "actor": event.get("actor"),
                    "shot_id": event.get("shot_id"),
                    "trace_id": event.get("trace_id"),
                    "occurred_at": event.get("occurred_at"),
                    "previous_hash": event.get("previous_hash"),
                    "event_hash": event.get("event_hash"),
                    "details_sha256": hashlib.sha256(details_json.encode("utf-8")).hexdigest(),
                    "detail_keys": sorted(str(key) for key in details)[:50],
                },
            },
        }
