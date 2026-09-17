from __future__ import annotations

"""Isolated C2PA Tool signing service and its API-side client.

The API process never receives a certificate or private key. It sends paths for
already persisted artifacts to a network-local signing service that owns the
credential mount. The same module is intentionally usable outside Docker for a
reviewed HSM/KMS subprocess signer.
"""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class C2paSignerError(RuntimeError):
    pass


def _environment_flag(variable: str) -> bool:
    return os.getenv(variable, "").strip().lower() in {"1", "true", "yes"}


def _required_secret_file(variable: str) -> str:
    value = os.getenv(variable, "").strip()
    if not value:
        raise C2paSignerError(f"{variable} must point to a readable secret file")
    path = Path(value)
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise C2paSignerError(f"{variable} cannot be read") from exc
    if not secret:
        raise C2paSignerError(f"{variable} is empty")
    return secret


def _path_within(root: Path, raw_path: str, *, label: str) -> Path:
    try:
        resolved = Path(raw_path).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise C2paSignerError(f"{label} must be inside the configured artifact root") from exc
    return resolved


@dataclass(frozen=True)
class C2paSignerSettings:
    artifact_root: Path
    auth_token: str
    c2patool_path: str
    settings_path: Path | None
    signer_path: str | None
    private_key_path: Path | None
    certificate_path: Path | None
    algorithm: str
    timestamp_authority_url: str | None
    timeout_seconds: int
    allow_builtin_test_signer: bool

    @classmethod
    def from_env(cls) -> "C2paSignerSettings":
        artifact_root = Path(
            os.getenv("MEDIAFORGE_C2PA_ARTIFACT_ROOT", "/var/lib/mediaforge/artifacts")
        ).resolve()
        settings_value = os.getenv("MEDIAFORGE_C2PA_SIGNER_SETTINGS_PATH", "").strip()
        signer_path = os.getenv("MEDIAFORGE_C2PA_SIGNER_PATH", "").strip() or None
        private_key_value = os.getenv("MEDIAFORGE_C2PA_SIGNER_PRIVATE_KEY_FILE", "").strip()
        certificate_value = os.getenv("MEDIAFORGE_C2PA_SIGNER_CERTIFICATE_FILE", "").strip()
        allow_builtin_test_signer = _environment_flag(
            "MEDIAFORGE_C2PA_SIGNER_ALLOW_BUILTIN_TEST_SIGNER"
        )
        if bool(private_key_value) != bool(certificate_value):
            raise C2paSignerError(
                "MEDIAFORGE_C2PA_SIGNER_PRIVATE_KEY_FILE and "
                "MEDIAFORGE_C2PA_SIGNER_CERTIFICATE_FILE must be configured together"
            )
        if settings_value and (private_key_value or signer_path):
            raise C2paSignerError(
                "configure exactly one C2PA signer mode: settings, local PEM, or subprocess"
            )
        if signer_path and private_key_value:
            raise C2paSignerError(
                "configure exactly one C2PA signer mode: settings, local PEM, or subprocess"
            )
        if not (settings_value or signer_path or private_key_value or allow_builtin_test_signer):
            raise C2paSignerError(
                "a C2PA settings file, local PEM credential, subprocess signer, or explicit builtin test signer is required"
            )
        raw_timeout = os.getenv("MEDIAFORGE_C2PA_SIGNER_SERVICE_TIMEOUT_SECONDS", "180")
        try:
            timeout_seconds = int(raw_timeout)
        except ValueError as exc:
            raise C2paSignerError(
                "MEDIAFORGE_C2PA_SIGNER_SERVICE_TIMEOUT_SECONDS must be an integer"
            ) from exc
        if not 1 <= timeout_seconds <= 1800:
            raise C2paSignerError(
                "MEDIAFORGE_C2PA_SIGNER_SERVICE_TIMEOUT_SECONDS must be 1..1800"
            )
        algorithm = os.getenv("MEDIAFORGE_C2PA_SIGNER_ALGORITHM", "ps256").strip().lower()
        if algorithm not in {"ps256", "ps384", "ps512", "es256", "es384", "es512", "ed25519"}:
            raise C2paSignerError("MEDIAFORGE_C2PA_SIGNER_ALGORITHM is not supported")
        timestamp_authority_url = os.getenv("MEDIAFORGE_C2PA_TSA_URL", "").strip() or None
        return cls(
            artifact_root=artifact_root,
            auth_token=_required_secret_file("MEDIAFORGE_C2PA_SIGNER_AUTH_TOKEN_FILE"),
            c2patool_path=os.getenv("MEDIAFORGE_C2PATOOL_PATH", "c2patool").strip()
            or "c2patool",
            settings_path=Path(settings_value).resolve() if settings_value else None,
            signer_path=signer_path,
            private_key_path=Path(private_key_value).resolve()
            if private_key_value
            else None,
            certificate_path=Path(certificate_value).resolve()
            if certificate_value
            else None,
            algorithm=algorithm,
            timestamp_authority_url=timestamp_authority_url,
            timeout_seconds=timeout_seconds,
            allow_builtin_test_signer=allow_builtin_test_signer,
        )

    @property
    def signing_mode(self) -> str:
        if self.signer_path:
            return "subprocess"
        if self.settings_path:
            return "settings"
        if self.allow_builtin_test_signer:
            return "builtin_test"
        return "local_pem"


@contextmanager
def _tool_settings(settings: C2paSignerSettings) -> Iterator[Path | None]:
    """Provide C2PA Tool settings without ever storing private key text in artifacts."""

    if settings.settings_path is not None:
        if not settings.settings_path.is_file():
            raise C2paSignerError("configured C2PA settings file is unavailable")
        yield settings.settings_path
        return
    if settings.private_key_path is None or settings.certificate_path is None:
        yield None
        return
    try:
        private_key = settings.private_key_path.read_text(encoding="utf-8").strip()
        certificate = settings.certificate_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise C2paSignerError("local C2PA PEM credential is unavailable") from exc
    if not private_key or not certificate or '"""' in private_key or '"""' in certificate:
        raise C2paSignerError("local C2PA PEM credential has an unsupported format")
    lines = [
        "[signer.local]",
        f'alg = "{settings.algorithm}"',
        'sign_cert = """' + certificate + '"""',
        'private_key = """' + private_key + '"""',
    ]
    if settings.timestamp_authority_url:
        lines.append(f'tsa_url = "{settings.timestamp_authority_url}"')
    descriptor, raw_path = tempfile.mkstemp(prefix="mediaforge-c2pa-", suffix=".toml")
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        path.unlink(missing_ok=True)


def _sign(
    settings: C2paSignerSettings,
    *,
    source: str,
    manifest: str,
    output: str,
) -> dict[str, Any]:
    root = settings.artifact_root
    if not root.is_dir():
        raise C2paSignerError("configured C2PA artifact root is unavailable")
    source_path = _path_within(root, source, label="input")
    manifest_path = _path_within(root, manifest, label="manifest")
    output_path = _path_within(root, output, label="output")
    if not source_path.is_file() or not manifest_path.is_file():
        raise C2paSignerError("C2PA source and manifest must exist")
    if source_path == output_path:
        raise C2paSignerError("C2PA output must not overwrite the source asset")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with _tool_settings(settings) as tool_settings:
        command = [settings.c2patool_path]
        if tool_settings is not None:
            command.extend(["--settings", str(tool_settings)])
        if settings.signer_path:
            command.extend(["--signer-path", settings.signer_path])
        command.extend(
            [str(source_path), "-m", str(manifest_path), "-f", "-o", str(output_path)]
        )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=settings.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise C2paSignerError(f"C2PA Tool could not sign the asset: {exc}") from exc
    if completed.returncode != 0 or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "C2PA Tool failed").strip()
        raise C2paSignerError(f"C2PA Tool signing failed: {detail[:1000]}")
    return {
        "output": str(output_path),
        "signing_mode": settings.signing_mode,
        "tool_output": (completed.stderr or completed.stdout or "signed").strip()[:1000],
    }


class _SignerServer(ThreadingHTTPServer):
    settings: C2paSignerSettings


class _SignerHandler(BaseHTTPRequestHandler):
    server: _SignerServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.settings.auth_token}"
        supplied = self.headers.get("Authorization", "")
        return secrets.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        tool_ready = False
        try:
            tool_ready = subprocess.run(
                [self.server.settings.c2patool_path, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            ).returncode == 0
        except OSError:
            tool_ready = False
        self._json(
            HTTPStatus.OK if tool_ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "ok" if tool_ready else "unavailable",
                "signing_mode": self.server.settings.signing_mode,
                "c2patool_ready": tool_ready,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/sign":
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"detail": "unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= content_length <= 16_384:
                raise C2paSignerError("invalid signing request size")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise C2paSignerError("signing request must be an object")
            result = _sign(
                self.server.settings,
                source=str(payload.get("input") or ""),
                manifest=str(payload.get("manifest") or ""),
                output=str(payload.get("output") or ""),
            )
        except (C2paSignerError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        self._json(HTTPStatus.CREATED, result)


def serve(host: str, port: int) -> None:
    settings = C2paSignerSettings.from_env()
    server = _SignerServer((host, port), _SignerHandler)
    server.settings = settings
    server.serve_forever()


def _trust_reference(variable: str) -> str | None:
    """Accept an HTTPS trust source or an existing, operator-mounted local file."""

    value = os.getenv(variable, "").strip()
    if not value:
        return None
    if value.startswith("https://"):
        return value
    if value.startswith("http://"):
        raise C2paSignerError(f"{variable} must use HTTPS when it is a URL")
    path = Path(value).resolve()
    if not path.is_file():
        raise C2paSignerError(f"{variable} local file is unavailable")
    return str(path)


@dataclass(frozen=True)
class C2paVerifierSettings:
    artifact_root: Path
    auth_token: str
    c2patool_path: str
    timeout_seconds: int
    trust_anchors: str | None
    allowed_list: str | None
    trust_config: str | None

    @classmethod
    def from_env(cls) -> "C2paVerifierSettings":
        raw_timeout = os.getenv("MEDIAFORGE_C2PA_VERIFIER_SERVICE_TIMEOUT_SECONDS", "90")
        try:
            timeout_seconds = int(raw_timeout)
        except ValueError as exc:
            raise C2paSignerError(
                "MEDIAFORGE_C2PA_VERIFIER_SERVICE_TIMEOUT_SECONDS must be an integer"
            ) from exc
        if not 1 <= timeout_seconds <= 1800:
            raise C2paSignerError(
                "MEDIAFORGE_C2PA_VERIFIER_SERVICE_TIMEOUT_SECONDS must be 1..1800"
            )
        trust_anchors = _trust_reference("MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS")
        allowed_list = _trust_reference("MEDIAFORGE_C2PA_VERIFIER_ALLOWED_LIST")
        trust_config = _trust_reference("MEDIAFORGE_C2PA_VERIFIER_TRUST_CONFIG")
        return cls(
            artifact_root=Path(
                os.getenv("MEDIAFORGE_C2PA_ARTIFACT_ROOT", "/var/lib/mediaforge/artifacts")
            ).resolve(),
            auth_token=_required_secret_file("MEDIAFORGE_C2PA_VERIFIER_AUTH_TOKEN_FILE"),
            c2patool_path=os.getenv("MEDIAFORGE_C2PATOOL_PATH", "c2patool").strip()
            or "c2patool",
            timeout_seconds=timeout_seconds,
            trust_anchors=trust_anchors,
            allowed_list=allowed_list,
            trust_config=trust_config,
        )


def _verify(
    settings: C2paVerifierSettings,
    *,
    source: str,
    manifest: str,
    output: str,
) -> dict[str, Any]:
    root = settings.artifact_root
    if not root.is_dir():
        raise C2paSignerError("configured C2PA artifact root is unavailable")
    source_path = _path_within(root, source, label="input")
    manifest_path = _path_within(root, manifest, label="manifest")
    output_path = _path_within(root, output, label="output")
    if not source_path.is_file() or not manifest_path.is_file() or not output_path.is_file():
        raise C2paSignerError("C2PA source, manifest, and signed output must exist")
    try:
        command = [settings.c2patool_path, str(output_path)]
        if settings.trust_anchors or settings.allowed_list:
            command.append("trust")
            if settings.trust_anchors:
                command.extend(["--trust_anchors", settings.trust_anchors])
            if settings.allowed_list:
                command.extend(["--allowed_list", settings.allowed_list])
            if settings.trust_config:
                command.extend(["--trust_config", settings.trust_config])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise C2paSignerError(f"C2PA Tool could not verify the asset: {exc}") from exc
    tool_output = completed.stdout or completed.stderr or ""
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "C2PA Tool verification failed").strip()
        raise C2paSignerError(f"C2PA Tool verification failed: {detail[:1000]}")
    json_start = tool_output.find("{")
    try:
        report = json.loads(tool_output[json_start:]) if json_start >= 0 else None
    except json.JSONDecodeError:
        report = None
    validation_state = report.get("validation_state") if isinstance(report, dict) else None
    if validation_state != "Valid":
        statuses = report.get("validation_status") if isinstance(report, dict) else None
        codes = [
            str(item.get("code"))
            for item in statuses
            if isinstance(item, dict) and item.get("code")
        ] if isinstance(statuses, list) else []
        detail = ", ".join(codes) or str(validation_state or "missing validation state")
        raise C2paSignerError(f"C2PA Tool validation state is not valid: {detail[:1000]}")
    return {
        "output": str(output_path),
        "validation_state": validation_state,
        "tool_output": tool_output.strip()[:1000],
    }


class _VerifierServer(ThreadingHTTPServer):
    settings: C2paVerifierSettings


class _VerifierHandler(BaseHTTPRequestHandler):
    server: _VerifierServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.settings.auth_token}"
        supplied = self.headers.get("Authorization", "")
        return secrets.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        tool_ready = False
        try:
            tool_ready = subprocess.run(
                [self.server.settings.c2patool_path, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            ).returncode == 0
        except OSError:
            tool_ready = False
        self._json(
            HTTPStatus.OK if tool_ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "ok" if tool_ready else "unavailable",
                "c2patool_ready": tool_ready,
                "trusted_validation_configured": bool(
                    self.server.settings.trust_anchors
                    or self.server.settings.allowed_list
                ),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/verify":
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"detail": "unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= content_length <= 16_384:
                raise C2paSignerError("invalid verification request size")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise C2paSignerError("verification request must be an object")
            result = _verify(
                self.server.settings,
                source=str(payload.get("input") or ""),
                manifest=str(payload.get("manifest") or ""),
                output=str(payload.get("output") or ""),
            )
        except (C2paSignerError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        self._json(HTTPStatus.OK, result)


def serve_verifier(host: str, port: int) -> None:
    settings = C2paVerifierSettings.from_env()
    server = _VerifierServer((host, port), _VerifierHandler)
    server.settings = settings
    server.serve_forever()


def _client_token() -> str:
    return _required_secret_file("MEDIAFORGE_C2PA_SIGNER_TOKEN_FILE")


def request_signature(*, source: str, manifest: str, output: str) -> dict[str, Any]:
    base_url = os.getenv("MEDIAFORGE_C2PA_SIGNER_URL", "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise C2paSignerError("MEDIAFORGE_C2PA_SIGNER_URL must be an HTTP(S) URL")
    token = _client_token()
    payload = json.dumps(
        {"input": source, "manifest": manifest, "output": output}, ensure_ascii=True
    ).encode("utf-8")
    request = Request(
        f"{base_url}/v1/sign",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    raw_timeout = os.getenv("MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS", "180")
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise C2paSignerError("MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS must be an integer") from exc
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise C2paSignerError(f"isolated C2PA signer request failed: {exc}") from exc
    if not isinstance(body, dict) or str(body.get("output") or "") != output:
        raise C2paSignerError("isolated C2PA signer returned an invalid response")
    return body


def request_verification(*, source: str, manifest: str, output: str) -> dict[str, Any]:
    base_url = os.getenv("MEDIAFORGE_C2PA_VERIFIER_URL", "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise C2paSignerError("MEDIAFORGE_C2PA_VERIFIER_URL must be an HTTP(S) URL")
    token = _required_secret_file("MEDIAFORGE_C2PA_VERIFIER_TOKEN_FILE")
    payload = json.dumps(
        {"input": source, "manifest": manifest, "output": output}, ensure_ascii=True
    ).encode("utf-8")
    request = Request(
        f"{base_url}/v1/verify",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    raw_timeout = os.getenv("MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS", "90")
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise C2paSignerError("MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS must be an integer") from exc
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise C2paSignerError(f"independent C2PA verifier request failed: {exc}") from exc
    if not isinstance(body, dict) or str(body.get("output") or "") != output:
        raise C2paSignerError("independent C2PA verifier returned an invalid response")
    return body


def signer_main() -> None:
    parser = argparse.ArgumentParser(description="run the isolated MediaForge C2PA signing service")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--host", default=os.getenv("MEDIAFORGE_C2PA_SIGNER_BIND_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MEDIAFORGE_C2PA_SIGNER_PORT", "8030")),
    )
    arguments = parser.parse_args()
    serve(arguments.host, arguments.port)


def client_main() -> None:
    parser = argparse.ArgumentParser(description="request a C2PA signature from MediaForge signer")
    parser.add_argument("command", choices=["sign"])
    parser.add_argument("input")
    parser.add_argument("manifest")
    parser.add_argument("output")
    arguments = parser.parse_args()
    try:
        request_signature(
            source=arguments.input,
            manifest=arguments.manifest,
            output=arguments.output,
        )
    except C2paSignerError as exc:
        parser.exit(1, f"mediaforge C2PA signer client: {exc}\n")


def verifier_main() -> None:
    parser = argparse.ArgumentParser(description="run the independent MediaForge C2PA verifier")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--host", default=os.getenv("MEDIAFORGE_C2PA_VERIFIER_BIND_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MEDIAFORGE_C2PA_VERIFIER_PORT", "8031")),
    )
    arguments = parser.parse_args()
    serve_verifier(arguments.host, arguments.port)


def verifier_client_main() -> None:
    parser = argparse.ArgumentParser(description="request independent MediaForge C2PA verification")
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("input")
    parser.add_argument("manifest")
    parser.add_argument("output")
    arguments = parser.parse_args()
    try:
        request_verification(
            source=arguments.input,
            manifest=arguments.manifest,
            output=arguments.output,
        )
    except C2paSignerError as exc:
        parser.exit(1, f"mediaforge C2PA verifier client: {exc}\n")


if __name__ == "__main__":  # pragma: no cover
    signer_main()
