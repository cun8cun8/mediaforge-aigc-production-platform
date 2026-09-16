from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class RegistrySyncError(ValueError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RegistryFetchResult:
    url: str
    status_code: int
    payload: dict | None
    content_sha256: str | None
    content_length: int | None
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


def _allowed_hosts() -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv(
            "MEDIAFORGE_LICENSE_REGISTRY_SYNC_ALLOWED_HOSTS",
            "",
        ).split(",")
        if item.strip()
    }


def validate_sync_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    clean_url = str(url).strip()
    parsed = urlsplit(clean_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise RegistrySyncError(
            "license registry sync URL must use HTTP(S) and include a hostname",
            status_code=422,
        )
    if parsed.username or parsed.password:
        raise RegistrySyncError(
            "license registry sync URL must not include embedded credentials",
            status_code=422,
        )
    allowed = _allowed_hosts() if allowed_hosts is None else allowed_hosts
    if allowed and parsed.hostname.lower() not in allowed:
        raise RegistrySyncError(
            f"license registry sync host is not allowed: {parsed.hostname}",
            status_code=422,
        )
    return clean_url


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_sync_url(newurl, allowed_hosts=self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_registry(
    url: str,
    *,
    timeout_seconds: float = 10.0,
    etag: str | None = None,
    last_modified: str | None = None,
    token: str | None = None,
    max_bytes: int = 5_000_000,
) -> RegistryFetchResult:
    allowed_hosts = _allowed_hosts()
    clean_url = validate_sync_url(url, allowed_hosts=allowed_hosts)
    if not 0 < timeout_seconds <= 60:
        raise RegistrySyncError(
            "license registry sync timeout must be greater than 0 and no more than 60 seconds",
            status_code=422,
        )
    headers = {
        "Accept": "application/json",
        "User-Agent": "MediaForge-LicenseRegistrySync/1.0",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    clean_token = (token or "").strip()
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"
    request = Request(clean_url, headers=headers, method="GET")
    opener = build_opener(_RestrictedRedirectHandler(allowed_hosts))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = int(response.getcode() or 200)
            response_etag = response.headers.get("ETag")
            response_last_modified = response.headers.get("Last-Modified")
            chunks: list[bytes] = []
            content_length = response.headers.get("Content-Length")
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > max_bytes:
                raise RegistrySyncError(
                    "license registry response exceeds the 5 MB limit",
                    status_code=502,
                )
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RegistrySyncError(
                        "license registry response exceeds the 5 MB limit",
                        status_code=502,
                    )
                chunks.append(chunk)
    except RegistrySyncError:
        raise
    except HTTPError as exc:
        if exc.code == 304:
            return RegistryFetchResult(
                url=clean_url,
                status_code=304,
                payload=None,
                content_sha256=None,
                content_length=0,
                etag=etag,
                last_modified=last_modified,
                not_modified=True,
            )
        raise RegistrySyncError(
            f"license registry sync upstream returned HTTP {exc.code}",
            status_code=502,
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise RegistrySyncError(
            f"license registry sync request failed: {exc}",
            status_code=502,
        ) from exc

    content = b"".join(chunks)
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistrySyncError(
            "license registry sync response is not valid UTF-8 JSON",
            status_code=502,
        ) from exc
    if not isinstance(payload, dict):
        raise RegistrySyncError(
            "license registry sync response must be a JSON object",
            status_code=502,
        )
    return RegistryFetchResult(
        url=clean_url,
        status_code=status_code,
        payload=payload,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_length=len(content),
        etag=response_etag,
        last_modified=response_last_modified,
    )
