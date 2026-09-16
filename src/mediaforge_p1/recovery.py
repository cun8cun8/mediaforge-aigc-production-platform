from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


BACKUP_MANIFEST_NAME = "mediaforge-backup-manifest.json"
BACKUP_MANIFEST_SCHEMA = "mediaforge-backup-manifest-v1"


class BackupVerificationError(RuntimeError):
    """Raised when a backup bundle is incomplete, altered, or unsafe to use."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_root(bundle: Path) -> Path:
    if bundle.is_symlink():
        raise BackupVerificationError("backup bundle root must not be a symlink")
    try:
        root = bundle.resolve(strict=True)
    except OSError as exc:
        raise BackupVerificationError("backup bundle does not exist") from exc
    if not root.is_dir():
        raise BackupVerificationError("backup bundle must be a directory")
    return root


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise BackupVerificationError("backup bundle contains a path outside its root") from exc


def _bundle_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BackupVerificationError(
                f"backup bundle must not contain symlinks: {_relative_path(root, path)}"
            )
        if path.is_file() and _relative_path(root, path) != BACKUP_MANIFEST_NAME:
            files.append(path)
    return sorted(files, key=lambda item: _relative_path(root, item))


def build_backup_manifest(bundle: Path) -> dict[str, Any]:
    """Build a deterministic inventory for a quiesced backup bundle."""
    root = _bundle_root(bundle)
    files = _bundle_files(root)
    entries = [
        {
            "path": _relative_path(root, path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]
    return {
        "schema_version": BACKUP_MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
        "file_count": len(entries),
        "total_size_bytes": sum(item["size_bytes"] for item in entries),
    }


def write_backup_manifest(bundle: Path) -> dict[str, Any]:
    manifest = build_backup_manifest(bundle)
    root = _bundle_root(bundle)
    target = root / BACKUP_MANIFEST_NAME
    temporary = root / f".{BACKUP_MANIFEST_NAME}.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return manifest


def _validated_manifest_entries(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise BackupVerificationError("backup manifest files must be a list")
    entries: dict[str, dict[str, Any]] = {}
    for entry in value:
        if not isinstance(entry, dict):
            raise BackupVerificationError("backup manifest contains an invalid file entry")
        relative = entry.get("path")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not relative:
            raise BackupVerificationError("backup manifest file path is invalid")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or str(path) != relative:
            raise BackupVerificationError("backup manifest contains an unsafe file path")
        if relative == BACKUP_MANIFEST_NAME or relative in entries:
            raise BackupVerificationError("backup manifest contains duplicate or reserved paths")
        if not isinstance(size, int) or size < 0:
            raise BackupVerificationError("backup manifest file size is invalid")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise BackupVerificationError("backup manifest file checksum is invalid")
        entries[relative] = {"size_bytes": size, "sha256": digest}
    return entries


def _verify_artifact_archive(root: Path) -> dict[str, int]:
    """Reject archive members that could escape an isolated restore directory."""
    archive_path = root / "artifacts.tar.gz"
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise BackupVerificationError("artifact archive cannot be read") from exc
    total_size = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise BackupVerificationError("artifact archive contains an unsafe member path")
        if member.issym() or member.islnk() or member.isdev():
            raise BackupVerificationError("artifact archive contains a link or device member")
        if not member.isfile() and not member.isdir():
            raise BackupVerificationError("artifact archive contains an unsupported member")
        if member.isfile():
            total_size += member.size
    return {"member_count": len(members), "uncompressed_size_bytes": total_size}


def verify_backup_manifest(bundle: Path) -> dict[str, Any]:
    """Verify every bundle member before a database or artifact restore."""
    root = _bundle_root(bundle)
    manifest_path = root / BACKUP_MANIFEST_NAME
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupVerificationError("backup manifest cannot be read") from exc
    if not isinstance(raw_manifest, dict):
        raise BackupVerificationError("backup manifest must be a JSON object")
    if raw_manifest.get("schema_version") != BACKUP_MANIFEST_SCHEMA:
        raise BackupVerificationError("backup manifest schema is unsupported")
    expected = _validated_manifest_entries(raw_manifest.get("files"))
    actual = {
        _relative_path(root, path): path
        for path in _bundle_files(root)
    }
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        detail = []
        if missing:
            detail.append(f"missing: {', '.join(missing)}")
        if unexpected:
            detail.append(f"unexpected: {', '.join(unexpected)}")
        raise BackupVerificationError("backup bundle inventory does not match manifest (" + "; ".join(detail) + ")")
    for relative, path in actual.items():
        expected_entry = expected[relative]
        if path.stat().st_size != expected_entry["size_bytes"]:
            raise BackupVerificationError(f"backup file size mismatch: {relative}")
        if _sha256(path) != expected_entry["sha256"]:
            raise BackupVerificationError(f"backup file checksum mismatch: {relative}")
    archive = _verify_artifact_archive(root)
    return {
        "schema_version": "mediaforge-backup-verification-v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "bundle": str(root),
        "file_count": len(actual),
        "total_size_bytes": sum(path.stat().st_size for path in actual.values()),
        "artifact_archive": archive,
        "verified": True,
    }


def backup_freshness_report(
    bundle: Path,
    *,
    max_age_hours: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a bundle and report whether it meets an operator-defined RPO."""
    if max_age_hours <= 0:
        raise BackupVerificationError("max backup age must be greater than zero")
    verification = verify_backup_manifest(bundle)
    root = _bundle_root(bundle)
    try:
        manifest = json.loads((root / BACKUP_MANIFEST_NAME).read_text(encoding="utf-8"))
        raw_created_at = manifest["created_at"]
        created_at = datetime.fromisoformat(str(raw_created_at).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise BackupVerificationError("backup manifest created_at is invalid") from exc
    if created_at.tzinfo is None:
        raise BackupVerificationError("backup manifest created_at must include a timezone")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise BackupVerificationError("freshness check time must include a timezone")
    age_seconds = max(0.0, (checked_at - created_at.astimezone(timezone.utc)).total_seconds())
    max_age_seconds = max_age_hours * 3600
    return {
        "schema_version": "mediaforge-backup-freshness-v1",
        "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "age_seconds": round(age_seconds, 3),
        "max_age_hours": max_age_hours,
        "fresh": age_seconds <= max_age_seconds,
        "verification": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or verify a MediaForge offline backup inventory."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("manifest", "verify"):
        subcommand = subcommands.add_parser(command)
        subcommand.add_argument("--bundle", type=Path, required=True)
    report = subcommands.add_parser("report")
    report.add_argument("--bundle", type=Path, required=True)
    report.add_argument("--max-age-hours", type=float, required=True)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            result = write_backup_manifest(args.bundle)
        elif args.command == "verify":
            result = verify_backup_manifest(args.bundle)
        else:
            result = backup_freshness_report(
                args.bundle,
                max_age_hours=args.max_age_hours,
            )
    except BackupVerificationError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command != "report" or result["fresh"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
