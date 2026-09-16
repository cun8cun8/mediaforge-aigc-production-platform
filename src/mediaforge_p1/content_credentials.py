from __future__ import annotations

"""Content Credentials adapter with an explicit external C2PA signing boundary.

The platform can always create a deterministic credential claim.  It is only
reported as signed when an operator-configured C2PA command produces the
declared output; a local JSON claim is deliberately labelled UNSIGNED.
"""

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .media import sha256_file


class ContentCredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContentCredentialSettings:
    signer_command: str | None
    verifier_command: str | None
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> "ContentCredentialSettings":
        raw_timeout = os.getenv("MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS", "120")
        try:
            timeout_seconds = int(raw_timeout)
        except ValueError as exc:
            raise ContentCredentialError(
                "MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS must be an integer"
            ) from exc
        if not 1 <= timeout_seconds <= 1800:
            raise ContentCredentialError(
                "MEDIAFORGE_C2PA_SIGNER_TIMEOUT_SECONDS must be 1..1800"
            )
        signer_command = os.getenv("MEDIAFORGE_C2PA_SIGNER_COMMAND", "").strip() or None
        verifier_command = os.getenv("MEDIAFORGE_C2PA_VERIFIER_COMMAND", "").strip() or None
        return cls(
            signer_command=signer_command,
            verifier_command=verifier_command,
            timeout_seconds=timeout_seconds,
        )


class ContentCredentials:
    def __init__(self, settings: ContentCredentialSettings | None = None) -> None:
        self.settings = settings or ContentCredentialSettings.from_env()

    def status_view(self) -> dict[str, Any]:
        return {
            "schema_version": "mediaforge-content-credentials-adapter-v1",
            "configured": self.settings.signer_command is not None,
            "mode": "external-c2pa-signer" if self.settings.signer_command else "claim-only",
            "verifier_configured": self.settings.verifier_command is not None,
            "message": (
                "An external C2PA signer command is configured."
                if self.settings.signer_command
                else "No C2PA signer is configured; credentials are emitted as unsigned claims."
            ),
            "timeout_seconds": self.settings.timeout_seconds,
        }

    def _external_command(
        self,
        command_template: str,
        *,
        source: Path,
        signed_output: Path | None,
        manifest_path: Path,
        purpose: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            command = shlex.split(command_template, posix=False)
        except ValueError as exc:
            raise ContentCredentialError(
                f"MEDIAFORGE_C2PA_{purpose}_COMMAND is invalid"
            ) from exc
        if not command:
            raise ContentCredentialError(
                f"MEDIAFORGE_C2PA_{purpose}_COMMAND is empty"
            )
        substitutions = {
            "{input}": str(source),
            "{output}": str(signed_output) if signed_output else "",
            "{manifest}": str(manifest_path),
        }
        command = [
            argument.replace("{input}", substitutions["{input}"])
            .replace("{output}", substitutions["{output}"])
            .replace("{manifest}", substitutions["{manifest}"])
            for argument in command
        ]
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContentCredentialError(
                f"external C2PA {purpose.lower()} failed: {exc}"
            ) from exc

    def create(
        self,
        *,
        project_id: str,
        asset_path: Path,
        output_dir: Path,
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        source = asset_path.resolve()
        if not source.is_file():
            raise ContentCredentialError(f"asset does not exist: {asset_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_root = output_dir.resolve()
        credential_id = f"credential_{source.stem}_{sha256_file(source)[:12]}"
        manifest_path = output_root / f"{credential_id}.json"
        signed_output = output_root / f"{source.stem}.c2pa{source.suffix}"
        payload = {
            "schema_version": "mediaforge-content-credential-claim-v1",
            "credential_id": credential_id,
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "asset": {
                "filename": source.name,
                "sha256": sha256_file(source),
                "size_bytes": source.stat().st_size,
            },
            "claim": claim,
            "c2pa": {
                "status": "UNSIGNED",
                "signed_output": None,
                "verification": "not-run",
            },
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )

        if not self.settings.signer_command:
            return {
                **payload,
                "manifest_path": str(manifest_path),
                "signed_output": None,
            }

        completed = self._external_command(
            self.settings.signer_command,
            source=source,
            signed_output=signed_output,
            manifest_path=manifest_path,
            purpose="SIGNER",
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown signer error").strip()
            raise ContentCredentialError(
                f"external C2PA signer exited {completed.returncode}: {message[:1000]}"
            )
        if not signed_output.is_file():
            raise ContentCredentialError(
                "external C2PA signer succeeded but did not create the declared {output} file"
            )
        payload["c2pa"] = {
            "status": "SIGNED_UNVERIFIED",
            "signed_output": str(signed_output),
            "signed_output_sha256": sha256_file(signed_output),
            "verification": "signer-output-present; independent verifier not configured",
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        return {
            **payload,
            "manifest_path": str(manifest_path),
            "signed_output": str(signed_output),
        }

    def verify(self, credential: dict[str, Any]) -> dict[str, Any]:
        """Verify claim integrity and optionally delegate signed media validation.

        The built-in checks prove that the persisted claim still describes the
        local source bytes.  A C2PA signature is only reported as independently
        verified after an explicitly configured verifier command succeeds.
        """
        manifest_path = Path(str(credential.get("manifest_path") or "")).resolve()
        source = Path(str(credential.get("asset_path") or "")).resolve()
        c2pa = credential.get("c2pa") or {}
        signed_value = c2pa.get("signed_output") or credential.get("signed_output")
        signed_output = Path(str(signed_value)).resolve() if signed_value else None
        checks: list[dict[str, Any]] = []

        def add_check(name: str, passed: bool, observed: Any, expected: Any) -> None:
            checks.append(
                {
                    "name": name,
                    "passed": bool(passed),
                    "observed": observed,
                    "expected": expected,
                }
            )

        payload: dict[str, Any] | None = None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        add_check(
            "manifest",
            isinstance(payload, dict)
            and payload.get("schema_version") == "mediaforge-content-credential-claim-v1",
            str(manifest_path) if manifest_path.is_file() else None,
            "mediaforge-content-credential-claim-v1",
        )
        expected_hash = (
            str((payload or {}).get("asset", {}).get("sha256") or "")
        )
        source_hash = sha256_file(source) if source.is_file() else None
        add_check("source_asset", source_hash == expected_hash and bool(expected_hash), source_hash, expected_hash or None)

        signed_status = str(c2pa.get("status") or "UNSIGNED")
        signed_hash = str(c2pa.get("signed_output_sha256") or "")
        actual_signed_hash = sha256_file(signed_output) if signed_output and signed_output.is_file() else None
        if signed_status.startswith("SIGNED"):
            add_check(
                "signed_output",
                actual_signed_hash == signed_hash and bool(signed_hash),
                actual_signed_hash,
                signed_hash or None,
            )

        external = {
            "configured": self.settings.verifier_command is not None,
            "passed": None,
            "message": "No external C2PA verifier is configured.",
        }
        if self.settings.verifier_command and signed_output and signed_output.is_file():
            completed = self._external_command(
                self.settings.verifier_command,
                source=source,
                signed_output=signed_output,
                manifest_path=manifest_path,
                purpose="VERIFIER",
            )
            external = {
                "configured": True,
                "passed": completed.returncode == 0,
                "message": (completed.stderr or completed.stdout or "verifier completed").strip()[:1000],
            }
            add_check("external_c2pa_verifier", completed.returncode == 0, completed.returncode, 0)
        elif self.settings.verifier_command and signed_status.startswith("SIGNED"):
            external = {
                "configured": True,
                "passed": False,
                "message": "Signed output is unavailable for external verification.",
            }
            add_check("external_c2pa_verifier", False, None, "signed output file")

        integrity_passed = all(check["passed"] for check in checks if check["name"] != "external_c2pa_verifier")
        verification_status = (
            "SIGNED_VERIFIED"
            if integrity_passed and signed_status.startswith("SIGNED") and external["passed"] is True
            else "INTEGRITY_VERIFIED"
            if integrity_passed and signed_status == "UNSIGNED"
            else "SIGNED_UNVERIFIED"
            if integrity_passed and signed_status.startswith("SIGNED")
            else "FAILED"
        )
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": verification_status,
            "passed": verification_status in {"INTEGRITY_VERIFIED", "SIGNED_VERIFIED"},
            "checks": checks,
            "external_verifier": external,
        }
