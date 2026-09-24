from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


class DeliveryDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeliveryDispatchResult:
    delivery_id: str
    mode: str
    destination_uri: str
    status: str
    package_sha256: str
    package_size_bytes: int
    response_status: int | None = None
    response_body: str | None = None
    manifest_path: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "mode": self.mode,
            "destination_uri": self.destination_uri,
            "status": self.status,
            "package_sha256": self.package_sha256,
            "package_size_bytes": self.package_size_bytes,
            "response_status": self.response_status,
            "response_body": self.response_body,
            "manifest_path": self.manifest_path,
        }


class DeliveryDispatcher:
    """Dispatch verified delivery packages to local, file, or HTTP targets."""

    def __init__(
        self,
        output_root: Path,
        *,
        mode: str = "local",
        timeout_seconds: float = 30.0,
        retries: int = 2,
        secret: str = "",
    ) -> None:
        self.output_root = output_root
        self.mode = mode.strip().lower() or "local"
        self.timeout_seconds = max(float(timeout_seconds), 1.0)
        self.retries = max(int(retries), 0)
        self.secret = secret

    @classmethod
    def from_env(cls, output_root: Path) -> "DeliveryDispatcher":
        try:
            timeout = float(os.getenv("MEDIAFORGE_DELIVERY_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_DELIVERY_TIMEOUT_SECONDS must be a number"
            ) from exc
        try:
            retries = int(os.getenv("MEDIAFORGE_DELIVERY_RETRIES", "2"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_DELIVERY_RETRIES must be an integer") from exc
        return cls(
            output_root,
            mode=os.getenv("MEDIAFORGE_DELIVERY_MODE", "local"),
            timeout_seconds=timeout,
            retries=retries,
            secret=os.getenv("MEDIAFORGE_DELIVERY_SECRET", ""),
        )

    def status_view(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "configured": self.mode != "disabled",
            "http_signature": bool(self.secret),
            "supported_targets": ["local", "file", "http", "https"],
        }

    def dispatch(
        self,
        package_path: Path,
        *,
        project_id: str,
        release_id: str,
        channel: str,
        recipient: str,
        destination_uri: str | None = None,
        note: str = "",
        idempotency_key: str | None = None,
    ) -> DeliveryDispatchResult:
        if self.mode == "disabled":
            raise DeliveryDispatchError("delivery dispatcher is disabled")
        package = Path(package_path)
        if not package.is_file():
            raise DeliveryDispatchError(f"delivery package not found: {package}")
        package_sha256 = self._sha256(package)
        package_size = package.stat().st_size
        delivery_id = (
            "dispatch_"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
            if idempotency_key
            else f"dispatch_{time.strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        )
        destination = (destination_uri or "").strip()
        parsed = urlparse(destination) if destination else None
        scheme = (parsed.scheme if parsed else "").lower()
        if self.mode == "local" or not destination or scheme == "mediaforge":
            return self._dispatch_local(
                package,
                delivery_id=delivery_id,
                project_id=project_id,
                release_id=release_id,
                channel=channel,
                recipient=recipient,
                note=note,
                package_sha256=package_sha256,
                package_size=package_size,
                idempotency_key=idempotency_key,
            )
        if scheme == "file":
            return self._dispatch_file(
                package,
                delivery_id=delivery_id,
                destination=destination,
                package_sha256=package_sha256,
                package_size=package_size,
            )
        if scheme in {"http", "https"}:
            return self._dispatch_http(
                package,
                delivery_id=delivery_id,
                destination=destination,
                project_id=project_id,
                release_id=release_id,
                channel=channel,
                recipient=recipient,
                note=note,
                package_sha256=package_sha256,
                package_size=package_size,
                idempotency_key=idempotency_key,
            )
        raise DeliveryDispatchError(
            "destination_uri must use file://, http://, https://, "
            "or mediaforge://"
        )

    def _dispatch_local(
        self,
        package: Path,
        *,
        delivery_id: str,
        project_id: str,
        release_id: str,
        channel: str,
        recipient: str,
        note: str,
        package_sha256: str,
        package_size: int,
        idempotency_key: str | None,
    ) -> DeliveryDispatchResult:
        destination_dir = self.output_root / "delivery-out" / project_id / delivery_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_package = destination_dir / package.name
        shutil.copy2(package, destination_package)
        manifest_path = destination_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "mediaforge-delivery-dispatch-v1",
                    "delivery_id": delivery_id,
                    "project_id": project_id,
                    "release_id": release_id,
                    "channel": channel,
                    "recipient": recipient,
                    "note": note,
                    "package": str(destination_package),
                    "package_sha256": package_sha256,
                    "package_size_bytes": package_size,
                    "idempotency_key": idempotency_key,
                    "dispatched_at": time.time(),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return DeliveryDispatchResult(
            delivery_id=delivery_id,
            mode="local",
            destination_uri=destination_package.resolve().as_uri(),
            status="DISPATCHED",
            package_sha256=package_sha256,
            package_size_bytes=package_size,
            manifest_path=str(manifest_path),
        )

    def _dispatch_file(
        self,
        package: Path,
        *,
        delivery_id: str,
        destination: str,
        package_sha256: str,
        package_size: int,
    ) -> DeliveryDispatchResult:
        parsed = urlparse(destination)
        raw_path = unquote(parsed.path)
        if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        target = Path(raw_path)
        if parsed.netloc:
            target = Path(f"//{parsed.netloc}{raw_path}")
        if target.suffix.lower() != ".zip":
            target = target / package.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package, target)
        return DeliveryDispatchResult(
            delivery_id=delivery_id,
            mode="file",
            destination_uri=target.as_uri(),
            status="DISPATCHED",
            package_sha256=package_sha256,
            package_size_bytes=package_size,
        )

    def _dispatch_http(
        self,
        package: Path,
        *,
        delivery_id: str,
        destination: str,
        project_id: str,
        release_id: str,
        channel: str,
        recipient: str,
        note: str,
        package_sha256: str,
        package_size: int,
        idempotency_key: str | None,
    ) -> DeliveryDispatchResult:
        body = package.read_bytes()
        headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(package_size),
            "X-MediaForge-Delivery-Id": delivery_id,
            "X-MediaForge-Project-Id": project_id,
            "X-MediaForge-Release-Id": release_id,
            "X-MediaForge-Channel": channel,
            "X-MediaForge-Recipient": recipient,
            "X-MediaForge-Package-SHA256": package_sha256,
        }
        if note:
            headers["X-MediaForge-Note"] = note[:1000]
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
            headers["X-MediaForge-Idempotency-Key"] = idempotency_key
        if self.secret:
            signing = f"{delivery_id}.{package_sha256}.{package_size}".encode()
            headers["X-MediaForge-Signature"] = hmac.new(
                self.secret.encode("utf-8"),
                signing,
                hashlib.sha256,
            ).hexdigest()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(destination, data=body, headers=headers, method="POST")
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    response_body = response.read(4000).decode("utf-8", errors="replace")
                    return DeliveryDispatchResult(
                        delivery_id=delivery_id,
                        mode="http",
                        destination_uri=destination,
                        status="DISPATCHED",
                        package_sha256=package_sha256,
                        package_size_bytes=package_size,
                        response_status=response.status,
                        response_body=response_body,
                    )
            except (HTTPError, URLError, OSError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2.0 ** attempt, 5.0))
        raise DeliveryDispatchError(
            f"HTTP delivery failed after {self.retries + 1} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
