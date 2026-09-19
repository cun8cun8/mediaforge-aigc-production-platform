from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mediaforge_p1.c2pa_signer import (
    C2paSignerError,
    C2paVerifierSettings,
    _path_within,
    _trust_reference,
    _verify,
)
from mediaforge_p1.content_credentials import ContentCredentialSettings, ContentCredentials
from mediaforge_p1.service import MediaForgeService


def test_production_status_requires_a_trusted_signer_attestation() -> None:
    unverified = ContentCredentials(
        ContentCredentialSettings(
            signer_command="sign {input} {c2pa_manifest} {output}",
            verifier_command="verify {input} {c2pa_manifest} {output}",
            timeout_seconds=30,
        )
    ).status_view()
    assert unverified["production_ready"] is False
    assert unverified["production_signer_attested"] is False

    verified = ContentCredentials(
        ContentCredentialSettings(
            signer_command="sign {input} {c2pa_manifest} {output}",
            verifier_command="verify {input} {c2pa_manifest} {output}",
            timeout_seconds=30,
            trusted_validation_configured=True,
            production_signer_attested=True,
        )
    ).status_view()
    assert verified["production_ready"] is True


def test_service_persists_admin_c2pa_attestation_and_invalidates_on_config_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFORGE_C2PA_SIGNER_COMMAND",
        "signer {input} {c2pa_manifest} {output}",
    )
    monkeypatch.setenv(
        "MEDIAFORGE_C2PA_VERIFIER_COMMAND",
        "verifier {input} {c2pa_manifest} {output}",
    )
    monkeypatch.setenv(
        "MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS",
        "https://trust.example.test/c2pa.pem",
    )
    service = MediaForgeService(tmp_path)
    assert service.content_credentials_status()["production_ready"] is False

    attested = service.attest_content_credentials(
        actor="release-admin",
        evidence_reference="https://evidence.example.test/c2pa/run-17",
        evidence_sha256="a" * 64,
        note="独立验证器已通过生产证书链检查。",
    )
    assert attested["production_ready"] is True
    assert attested["operator_attestation"]["status"] == "VALID"
    assert attested["operator_attestation"]["attested_by"] == "release-admin"

    restored = MediaForgeService(tmp_path)
    restored_status = restored.content_credentials_status()
    assert restored_status["production_ready"] is True
    assert restored_status["operator_attestation"]["status"] == "VALID"

    restored.content_credentials.settings = ContentCredentialSettings(
        signer_command="changed-signer {input} {c2pa_manifest} {output}",
        verifier_command="verifier {input} {c2pa_manifest} {output}",
        timeout_seconds=120,
        trusted_validation_configured=True,
    )
    stale = restored.content_credentials_status()
    assert stale["production_ready"] is False
    assert stale["operator_attestation"]["status"] == "STALE"


def test_content_credential_uses_dedicated_c2patool_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"source-media")
    output_root = tmp_path / "credentials"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[0] == "sign":
            Path(command[-1]).write_bytes(b"signed-c2pa-media")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("mediaforge_p1.content_credentials.subprocess.run", fake_run)
    credentials = ContentCredentials(
        ContentCredentialSettings(
            signer_command="sign {input} {c2pa_manifest} {output}",
            verifier_command="verify {input} {c2pa_manifest} {output}",
            timeout_seconds=30,
        )
    )

    created = credentials.create(
        project_id="project-c2pa",
        asset_path=source,
        output_dir=output_root,
        claim={"asset_id": "final_mp4", "source": "approved-current-artifacts"},
    )

    c2pa_manifest_path = Path(created["c2pa_manifest_path"])
    c2pa_manifest = json.loads(c2pa_manifest_path.read_text(encoding="utf-8"))
    assert created["c2pa"]["status"] == "SIGNED_UNVERIFIED"
    assert c2pa_manifest["claim_generator"] == "MediaForge/0.1.0"
    assert c2pa_manifest["format"] == "image/png"
    assert c2pa_manifest["assertions"][0]["label"] == "c2pa.actions.v2"
    assert c2pa_manifest["assertions"][0]["data"]["actions"][0][
        "digitalSourceType"
    ] == "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
    assert c2pa_manifest["assertions"][1]["label"] == "org.mediaforge.provenance.v1"
    assert commands[0] == [
        "sign",
        str(source.resolve()),
        str(c2pa_manifest_path.resolve()),
        str(Path(created["signed_output"]).resolve()),
    ]

    verified = credentials.verify({**created, "asset_path": str(source)})
    assert verified["status"] == "SIGNED_VERIFIED"
    assert commands[1][0] == "verify"
    assert commands[1][2] == str(c2pa_manifest_path.resolve())


def test_isolated_signer_rejects_paths_outside_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    assert _path_within(root.resolve(), str(root / "project" / "asset.mp4"), label="input") == (
        root / "project" / "asset.mp4"
    ).resolve()
    with pytest.raises(C2paSignerError, match="artifact root"):
        _path_within(root.resolve(), str(tmp_path / "outside.mp4"), label="input")


def test_verifier_uses_trust_profile_and_rejects_invalid_validation_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    source = root / "source.mp4"
    manifest = root / "manifest.json"
    output = root / "signed.mp4"
    for path in (source, manifest, output):
        path.write_bytes(b"media")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"validation_state":"Invalid","validation_status":'
                '[{"code":"signingCredential.untrusted"}]}'
            ),
            stderr="",
        )

    monkeypatch.setattr("mediaforge_p1.c2pa_signer.subprocess.run", fake_run)
    settings = C2paVerifierSettings(
        artifact_root=root,
        auth_token="test-token",
        c2patool_path="c2patool",
        timeout_seconds=30,
        trust_anchors="/trust/anchors.pem",
        allowed_list=None,
        trust_config="/trust/store.cfg",
    )

    with pytest.raises(C2paSignerError, match="signingCredential.untrusted"):
        _verify(
            settings,
            source=str(source),
            manifest=str(manifest),
            output=str(output),
        )
    assert commands == [
        [
            "c2patool",
            str(output.resolve()),
            "trust",
            "--trust_anchors",
            "/trust/anchors.pem",
            "--trust_config",
            "/trust/store.cfg",
        ]
    ]


def test_trust_reference_requires_https_or_an_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variable = "MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS"
    trust_file = tmp_path / "anchors.pem"
    trust_file.write_text("public trust material", encoding="utf-8")

    monkeypatch.setenv(variable, str(trust_file))
    assert _trust_reference(variable) == str(trust_file.resolve())

    monkeypatch.setenv(variable, "http://example.invalid/anchors.pem")
    with pytest.raises(C2paSignerError, match="HTTPS"):
        _trust_reference(variable)

    monkeypatch.setenv(variable, str(tmp_path / "missing.pem"))
    with pytest.raises(C2paSignerError, match="unavailable"):
        _trust_reference(variable)
