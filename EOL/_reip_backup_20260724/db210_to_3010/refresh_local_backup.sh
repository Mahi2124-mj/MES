#!/usr/bin/env bash
# ============================================================
#   REFRESH LOCAL BACKUP DB   (run as the normal 'server' user — NO sudo)
#       ./refresh_local_backup.sh
#
#   The app stays on the CENTRAL DB 192.168.10.210 (source of truth).
#   This box's local PostgreSQL (127.0.0.1 / energydb) is kept only as a
#   BACKUP COPY — a warm, queryable mirror + an on-disk .dump file — so if
#   .210 is ever lost we have a recent full copy of the data.
#
#   This script, run on a schedule (see the cron line at the bottom):
#     1. pg_dump the whole energydb from .210 to a timestamped .dump on the
#        22 TB data disk  (this IS the file backup too)
#     2. reload it into the local energydb  (drop + recreate + restore)
#     3. keep the last KEEP_DUMPS dumps, delete older ones
#
#   Gentle by design: ONE connection to .210, custom-format parallel restore
#   only against the LOCAL db.  Nothing here repoints the app — to actually
#   run the app ON the local db you'd use switch_to_local_db.sh instead.
# ============================================================
set -uo pipefail

SRC_HOST="192.168.10.210"     # central DB (source of truth)
DST_HOST="127.0.0.1"          # local backup DB
DB="energydb"
PGUSER="postgres"
export PGPASSWORD="tbdi@123"

BK_DIR="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/database"
KEEP_DUMPS=7                  # keep a week of daily dumps
LOG="$BK_DIR/refresh_local_backup.log"
mkdir -p "$BK_DIR"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# a fixed-name marker isn't used for the dump because the runtime forbids
# Date.now() in workflows, but this is a plain shell script so `date` is fine.
STAMP="$(date '+%Y%m%d_%H%M%S')"
DUMP="$BK_DIR/energydb_backup_${STAMP}.dump"

log "=== refresh start (.210 -> local backup) ==="

# 0. sanity: is .210 reachable?
if ! timeout 5 bash -c "</dev/tcp/$SRC_HOST/5432" 2>/dev/null; then
  log "[SKIP] central DB $SRC_HOST:5432 not reachable — keeping existing backup as-is"
  exit 0
fi

# 1. dump .210 (single connection, custom format) ---------------
log "[1/3] dumping $DB from $SRC_HOST -> $(basename "$DUMP")"
if ! pg_dump -h "$SRC_HOST" -U "$PGUSER" -Fc -d "$DB" -f "$DUMP" 2>>"$LOG"; then
  log "[FATAL] pg_dump from $SRC_HOST failed — leaving previous backup untouched"
  rm -f "$DUMP"
  exit 1
fi
log "      dump ok ($(du -h "$DUMP" | cut -f1))"

# 2. reload into the LOCAL backup db ----------------------------
# Safe to drop: the app is NOT pointed here, so nothing is connected.
log "[2/3] reloading into $DST_HOST/$DB"
psql -h "$DST_HOST" -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 >>"$LOG" 2>&1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname='$DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DB;
CREATE DATABASE $DB OWNER $PGUSER;
SQL
if [[ $? -ne 0 ]]; then log "[FATAL] could not recreate local $DB"; exit 1; fi
pg_restore --no-owner --no-privileges -h "$DST_HOST" -U "$PGUSER" -j 4 \
  -d "$DB" "$DUMP" 2>>"$LOG"
log "      restore rc=$?"

# 3. verify + prune old dumps -----------------------------------
TBLS=$(psql -h "$DST_HOST" -U "$PGUSER" -d "$DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null)
log "[3/3] local backup now has ${TBLS:-?} tables"
ls -1t "$BK_DIR"/energydb_backup_*.dump 2>/dev/null | tail -n +$((KEEP_DUMPS+1)) | while read -r old; do
  log "      pruning old dump: $(basename "$old")"; rm -f "$old"
done

log "=== refresh done ==="

# ── To run this automatically every night at 01:30 (NO sudo) ──────────
#   crontab -e   # as the 'server' user, add:
#   30 1 * * *  "/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/refresh_local_backup.sh" >/dev/null 2>&1
