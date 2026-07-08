#!/bin/sh
# JobScout — snapshot quotidien de jobs.db + rotation.
# À lancer en root si la DB du volume Docker est root-owned (cas Synology),
# via cron / le Planificateur de tâches DSM.
#
# Snapshot cohérent via `sqlite3 .backup` (sûr même pendant une écriture WAL),
# contrairement à un simple cp.

set -eu

DB="${JOBSCOUT_DB:-/volume1/@docker/volumes/jobscout_jobscout-data/_data/jobs.db}"
DEST="${JOBSCOUT_BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backups}"
KEEP=7

SQLITE3="$(command -v sqlite3 || echo /usr/bin/sqlite3)"

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d_%H%M%S)"
"$SQLITE3" "$DB" ".backup ${DEST}/jobs.db.${STAMP}"

# Rotation : ne garde que les $KEEP snapshots les plus récents.
ls -1t "$DEST"/jobs.db.* 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r f; do
    rm -f "$f"
done

echo "$(date '+%F %T') backup OK -> ${DEST}/jobs.db.${STAMP}"
