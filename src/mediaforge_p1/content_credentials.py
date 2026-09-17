from __future__ import annotations

"""Content Credentials adapter with an explicit external C2PA signing boundary.

The platform can always create a deterministic credential claim.  It is only
reported as signed when an operator-configured C2PA command produces the
declared output; a local JSON claim is deliberately labelled UNSIGNED.
"""

import json
import mimetypes
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
    test_signer_mode: bool = False
    production_signer_attested: bool = False
    trusted_validation_configured: bool = False

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
        test_signer_mode = os.getenv("MEDIAFORGE_C2PA_SIGNER_TEST_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        production_signer_attested = os.getenv(
            "MEDIAFORGE_C2PA_PRODUCTION_SIGNER_ATTESTED", ""
        ).strip().lower() in {"1", "true", "yes"}
        trusted_validation_configured = any(
            os.getenv(variable, "").strip()
            for variable in (
                "MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS",
                "MEDIAFORGE_C2PA_VERIFIER_ALLOWED_LIST",
            )
        )
        return cls(
            signer_command=signer_command,
            verifier_command=verifier_command,
            timeout_seconds=timeout_seconds,
            test_signer_mode=test_signer_mode,
            production_signer_attested=production_signer_attested,
            trusted_validation_configured=trusted_validation_configured,
        )


class ContentCredentials:
    def __init__(self, settings: ContentCredentialSettings | None = None) -> None:
        self.settings = settings or ContentCredentialSettings.from_env()

    def status_view(self) -> dict[str, Any]:
        signer_configured = self.settings.signer_command is not None
        verifier_configured = self.settings.verifier_command is not None
        return {
            "schema_version": "mediaforge-content-credentials-adapter-v1",
            "configured": signer_configured,
            "mode": "external-c2pa-signer" if signer_configured else "claim-only",
            "verifier_configured": verifier_configured,
            "production_ready": signer_configured
            and verifier_configured
            and not self.settings.test_signer_mode
            and self.settings.trusted_validation_configured
            and self.settings.production_signer_attested,
            "production_signer_attested": self.settings.production_signer_attested,
            "trusted_validation_configured": self.settings.trusted_validation_configured,
            "signer_identity_mode": (
                "builtin-test"
                if self.settings.test_signer_mode
                else "operator-configured"
            ),
            "message": (
                "A C2PA signer and verifier are configured in explicit test mode; production acceptance remains blocked."
                if signer_configured and self.settings.test_signer_mode
                else "A C2PA signer and verifier are configured, but production acceptance remains blocked until a C2PA trust anchor or allowed signing certificate list is configured."
                if signer_configured
                and verifier_configured
                and not self.settings.trusted_validation_configured
                else "A C2PA signer and verifier are configured, but production acceptance remains blocked until the operator attests a successful trusted C2PA validation."
                if signer_configured
                and verifier_configured
                and not self.settings.production_signer_attested
                else "An external C2PA signer command is configured."
                if signer_configured
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
        c2pa_manifest_path: Path | None,
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
            "{c2pa_manifest}": str(c2pa_manifest_path)
            if c2pa_manifest_path
            else "",
        }
        command = [
            argument.replace("{input}", substitutions["{input}"])
            .replace("{output}", substitutions["{output}"])
            .replace("{manifest}", substitutions["{manifest}"])
            .replace("{c2pa_manifest}", substitutions["{c2pa_manifest}"])
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

    @staticmethod
    def _c2pa_manifest(
        *,
        credential_id: str,
        project_id: str,
        source: Path,
        source_sha256: str,
        created_at: str,
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the manifest-definition JSON consumed by C2PA Tool.

        This is deliberately separate from MediaForge's evidence claim. The
        latter remains an application record, while this document follows the
        C2PA Tool manifest-definition format and is safe to hand to an
        isolated signer. Signing credentials are never included here.
        """

        media_type, _ = mimetypes.guess_type(source.name)
        return {
            "claim_generator": "MediaForge/0.1.0",
            "title": source.name,
            "format": media_type or "application/octet-stream",
            "assertions": [
                {
                    "label": "c2pa.actions.v2",
                    "data": {
                        "actions": [
                            {
                                "action": "c2pa.created",
                                "when": created_at,
                                "digitalSourceType": os.getenv(
                                    "MEDIAFORGE_C2PA_DIGITAL_SOURCE_TYPE",
                                    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
                                ),
                                "softwareAgent": {
                                    "name": "MediaForge",
                                    "version": "0.1.0",
                                },
                            }
                        ]
                    },
                },
                {
                    "label": "org.mediaforge.provenance.v1",
                    "data": {
                        "credential_id": credential_id,
                        "project_id": project_id,
                        "asset_sha256": source_sha256,
                        "source_filename": source.name,
                        "claim": claim,
                    },
                },
            ],
        }

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
        c2pa_manifest_path = output_root / f"{credential_id}.c2pa.json"
        signed_output = output_root / f"{source.stem}.c2pa{source.suffix}"
        created_at = datetime.now(timezone.utc).isoformat()
        source_sha256 = sha256_file(source)
        payload = {
            "schema_version": "mediaforge-content-credential-claim-v1",
            "credential_id": credential_id,
            "project_id": project_id,
            "created_at": created_at,
            "asset": {
                "filename": source.name,
                "sha256": source_sha256,
                "size_bytes": source.stat().st_size,
            },
            "claim": claim,
            "c2pa_manifest_path": str(c2pa_manifest_path),
            "c2pa": {
                "status": "UNSIGNED",
                "signed_output": None,
                "verification": "not-run",
            },
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        c2pa_manifest_path.write_text(
            json.dumps(
                self._c2pa_manifest(
                    credential_id=credential_id,
                    project_id=project_id,
                    source=source,
                    source_sha256=source_sha256,
                    created_at=created_at,
                    claim=claim,
                ),
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        if not self.settings.signer_command:
            return {
                **payload,
                "manifest_path": str(manifest_path),
                "c2pa_manifest_path": str(c2pa_manifest_path),
                "signed_output": None,
            }

        completed = self._external_command(
            self.settings.signer_command,
            source=source,
            signed_output=signed_output,
            manifest_path=manifest_path,
            c2pa_manifest_path=c2pa_manifest_path,
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
            "c2pa_manifest_path": str(c2pa_manifest_path),
            "signed_output": str(signed_output),
        }

    def verify(self, credential: dict[str, Any]) -> dict[str, Any]:
        """Verify claim integrity and optionally delegate signed media validation.

        The built-in checks prove that the persisted claim still describes the
        local source bytes.  A C2PA signature is only reported as independently
        verified after an explicitly configured verifier command succeeds.
        """
        manifest_path = Path(str(credential.get("manifest_path") or "")).resolve()
        c2pa_manifest_value = credential.get("c2pa_manifest_path")
        c2pa_manifest_path = (
            Path(str(c2pa_manifest_value)).resolve()
            if c2pa_manifest_value
            else None
        )
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
        if c2pa_manifest_path is not None:
            c2pa_manifest: dict[str, Any] | None = None
            try:
                parsed_c2pa_manifest = json.loads(
                    c2pa_manifest_path.read_text(encoding="utf-8")
                )
                c2pa_manifest = (
                    parsed_c2pa_manifest
                    if isinstance(parsed_c2pa_manifest, dict)
                    else None
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                c2pa_manifest = None
            add_check(
                "c2pa_manifest",
                bool(c2pa_manifest)
                and c2pa_manifest.get("claim_generator") == "MediaForge/0.1.0"
                and isinstance(c2pa_manifest.get("assertions"), list),
                str(c2pa_manifest_path) if c2pa_manifest_path.is_file() else None,
                "c2patool manifest definition",
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
                c2pa_manifest_path=c2pa_manifest_path,
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
