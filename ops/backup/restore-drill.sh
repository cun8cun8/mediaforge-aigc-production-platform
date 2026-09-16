#!/bin/sh
# Restore a verified backup only into an explicitly named isolated drill target.
set -eu

: "${MEDIAFORGE_BACKUP_BUNDLE:?MEDIAFORGE_BACKUP_BUNDLE is required}"
: "${MEDIAFORGE_DRILL_DATABASE_URL:?MEDIAFORGE_DRILL_DATABASE_URL is required}"
: "${MEDIAFORGE_DRILL_DATABASE_NAME:?MEDIAFORGE_DRILL_DATABASE_NAME is required}"
: "${MEDIAFORGE_DRILL_ARTIFACT_ROOT:?MEDIAFORGE_DRILL_ARTIFACT_ROOT is required}"

if [ "${MEDIAFORGE_DRILL_ACKNOWLEDGEMENT:-}" != "ISOLATED-RECOVERY-DRILL" ]; then
  echo "Refusing restore: set MEDIAFORGE_DRILL_ACKNOWLEDGEMENT=ISOLATED-RECOVERY-DRILL." >&2
  exit 64
fi

case "$MEDIAFORGE_DRILL_DATABASE_NAME" in
  *drill*|*restore*|*recovery*) ;;
  *)
    echo "Refusing restore: MEDIAFORGE_DRILL_DATABASE_NAME must include drill, restore, or recovery." >&2
    exit 64
    ;;
esac

case "$MEDIAFORGE_DRILL_ARTIFACT_ROOT" in
  /|.|..|../*|*/..|*/../*)
    echo "Refusing restore: MEDIAFORGE_DRILL_ARTIFACT_ROOT is unsafe." >&2
    exit 64
    ;;
esac

if [ -e "$MEDIAFORGE_DRILL_ARTIFACT_ROOT" ] && [ ! -d "$MEDIAFORGE_DRILL_ARTIFACT_ROOT" ]; then
  echo "Refusing restore: MEDIAFORGE_DRILL_ARTIFACT_ROOT must be a directory." >&2
  exit 64
fi

if [ ! -f "$MEDIAFORGE_BACKUP_BUNDLE/state.dump" ] || [ ! -f "$MEDIAFORGE_BACKUP_BUNDLE/artifacts.tar.gz" ]; then
  echo "Refusing restore: bundle lacks state.dump or artifacts.tar.gz." >&2
  exit 64
fi

actual_database=$(psql "$MEDIAFORGE_DRILL_DATABASE_URL" -Atqc 'SELECT current_database()')
if [ "$actual_database" != "$MEDIAFORGE_DRILL_DATABASE_NAME" ]; then
  echo "Refusing restore: connected database does not match MEDIAFORGE_DRILL_DATABASE_NAME." >&2
  exit 64
fi

if [ -e "$MEDIAFORGE_DRILL_ARTIFACT_ROOT" ] && [ "$(find "$MEDIAFORGE_DRILL_ARTIFACT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "Refusing restore: MEDIAFORGE_DRILL_ARTIFACT_ROOT must be empty." >&2
  exit 64
fi
mkdir -p "$MEDIAFORGE_DRILL_ARTIFACT_ROOT"

mediaforge-backup report --bundle "$MEDIAFORGE_BACKUP_BUNDLE" \
  --max-age-hours "${MEDIAFORGE_DRILL_MAX_BACKUP_AGE_HOURS:-48}"

pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "$MEDIAFORGE_DRILL_DATABASE_URL" \
  "$MEDIAFORGE_BACKUP_BUNDLE/state.dump"
tar -xzf "$MEDIAFORGE_BACKUP_BUNDLE/artifacts.tar.gz" -C "$MEDIAFORGE_DRILL_ARTIFACT_ROOT"

printf '%s\n' "Recovery drill restored verified bundle into $MEDIAFORGE_DRILL_DATABASE_NAME and $MEDIAFORGE_DRILL_ARTIFACT_ROOT."
