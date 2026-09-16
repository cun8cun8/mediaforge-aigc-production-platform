from __future__ import annotations

import json
import io
import tarfile
from datetime import datetime, timezone

import pytest

from mediaforge_p1.recovery import (
    BACKUP_MANIFEST_NAME,
    BackupVerificationError,
    backup_freshness_report,
    verify_backup_manifest,
    write_backup_manifest,
)


def test_backup_manifest_detects_tampering_and_inventory_drift(tmp_path) -> None:
    bundle = tmp_path / "backup"
    bundle.mkdir(parents=True)
    (bundle / "state.dump").write_bytes(b"postgres-backup")
    with tarfile.open(bundle / "artifacts.tar.gz", "w:gz") as archive:
        payload = b"artifact-archive"
        member = tarfile.TarInfo("preview.mp4")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    manifest = write_backup_manifest(bundle)
    assert manifest["file_count"] == 2
    assert (bundle / BACKUP_MANIFEST_NAME).is_file()
    assert verify_backup_manifest(bundle)["verified"] is True

    (bundle / "state.dump").write_bytes(b"changed")
    with pytest.raises(BackupVerificationError, match="checksum|size"):
        verify_backup_manifest(bundle)

    write_backup_manifest(bundle)
    (bundle / "unexpected.txt").write_text("not in manifest", encoding="utf-8")
    with pytest.raises(BackupVerificationError, match="inventory"):
        verify_backup_manifest(bundle)


def test_backup_manifest_rejects_unsafe_artifact_archive(tmp_path) -> None:
    bundle = tmp_path / "backup"
    bundle.mkdir()
    (bundle / "state.dump").write_bytes(b"postgres-backup")
    with tarfile.open(bundle / "artifacts.tar.gz", "w:gz") as archive:
        payload = b"unsafe"
        member = tarfile.TarInfo("../outside")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    write_backup_manifest(bundle)

    with pytest.raises(BackupVerificationError, match="unsafe member path"):
        verify_backup_manifest(bundle)


def test_backup_manifest_rejects_unsafe_paths(tmp_path) -> None:
    bundle = tmp_path / "backup"
    bundle.mkdir()
    (bundle / BACKUP_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": "mediaforge-backup-manifest-v1",
                "files": [
                    {
                        "path": "../state.dump",
                        "size_bytes": 1,
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BackupVerificationError, match="unsafe"):
        verify_backup_manifest(bundle)


def test_backup_freshness_report_enforces_rpo_without_replacing_verification(tmp_path) -> None:
    bundle = tmp_path / "backup"
    bundle.mkdir()
    (bundle / "state.dump").write_bytes(b"postgres-backup")
    with tarfile.open(bundle / "artifacts.tar.gz", "w:gz") as archive:
        payload = b"artifact-archive"
        member = tarfile.TarInfo("preview.mp4")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    write_backup_manifest(bundle)
    manifest_path = bundle / BACKUP_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2026-09-15T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = backup_freshness_report(
        bundle,
        max_age_hours=24,
        now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert report["fresh"] is True
    assert report["verification"]["verified"] is True

    stale = backup_freshness_report(
        bundle,
        max_age_hours=1,
        now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert stale["fresh"] is False

    with pytest.raises(BackupVerificationError, match="greater than zero"):
        backup_freshness_report(bundle, max_age_hours=0)
