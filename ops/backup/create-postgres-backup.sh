#!/bin/sh
# Create a portable offline backup only after API and Worker writes are quiesced.
set -eu

: "${MEDIAFORGE_DATABASE_URL:?MEDIAFORGE_DATABASE_URL is required}"
: "${MEDIAFORGE_ARTIFACT_ROOT:?MEDIAFORGE_ARTIFACT_ROOT is required}"
: "${MEDIAFORGE_BACKUP_ROOT:?MEDIAFORGE_BACKUP_ROOT is required}"

if [ "${MEDIAFORGE_BACKUP_QUIESCED:-false}" != "true" ]; then
  echo "Refusing online backup: set MEDIAFORGE_BACKUP_QUIESCED=true after stopping MediaForge API and Workers." >&2
  exit 64
fi

if [ ! -d "$MEDIAFORGE_ARTIFACT_ROOT" ]; then
  echo "MEDIAFORGE_ARTIFACT_ROOT is not a directory: $MEDIAFORGE_ARTIFACT_ROOT" >&2
  exit 64
fi

umask 077
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$MEDIAFORGE_BACKUP_ROOT"
temporary="$MEDIAFORGE_BACKUP_ROOT/.mediaforge-backup-$timestamp.partial"
destination="$MEDIAFORGE_BACKUP_ROOT/mediaforge-backup-$timestamp"

if [ -e "$temporary" ] || [ -e "$destination" ]; then
  echo "Backup destination already exists for $timestamp; retry in a new second." >&2
  exit 65
fi

mkdir "$temporary"
pg_dump --format=custom --no-owner --no-privileges --file "$temporary/state.dump" "$MEDIAFORGE_DATABASE_URL"
tar -C "$MEDIAFORGE_ARTIFACT_ROOT" -czf "$temporary/artifacts.tar.gz" .
printf '%s\n' "created_at=$timestamp" "artifact_root=$MEDIAFORGE_ARTIFACT_ROOT" > "$temporary/backup-info.txt"
python -m mediaforge_p1.recovery manifest --bundle "$temporary"
mv "$temporary" "$destination"
python -m mediaforge_p1.recovery verify --bundle "$destination"
printf '%s\n' "Backup created and verified: $destination"
