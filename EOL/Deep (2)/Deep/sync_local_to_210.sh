#!/usr/bin/env bash
# ============================================================
#   SYNC LOCAL DB  ->  .210   (run as the normal 'server' user — NO sudo)
#       ./sync_local_to_210.sh
#
#   The app now runs on THIS box's local PostgreSQL (127.0.0.1) — see
#   switch_to_local_db.sh.  .210 is the OFF-BOX BACKUP.  This script keeps
#   .210 current: whenever .210 is reachable it pushes a full copy of the
#   local energydb up to it.  When .210 is down it SKIPS cleanly (exit 0),
#   so it is safe to run on a schedule — it self-heals the moment .210
#   comes back.
#
#   Direction is LOCAL -> .210 (opposite of refresh_local_backup.sh, which
#   is .210 -> local and was only used during the initial cut-over).  Local
#   is a SUPERSET of .210 (it was seeded from .210 then kept collecting), so
#   overwriting .210 loses nothing.
#
#   Safety: the local dump is taken and size-checked BEFORE .210 is touched;
#   if the dump fails, .210 is left exactly as-is.
#
#   Schedule (user crontab, NO sudo) — every 30 min:
#     crontab -e
#     */30 * * * *  "/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/sync_local_to_210.sh" >/dev/null 2>&1
# ============================================================
set -uo pipefail

# 2026-07-28 — PREVENT OVERLAPPING RUNS.  Over the degraded WiFi-failover link a full
# dump+restore of the 1.8 GB energydb takes LONGER than the 30-min cron interval, so
# runs STACKED: several concurrent pg_dumps held ACCESS-SHARE locks on the LOCAL
# energydb and starved the collectors' schema-ensure (CREATE/ALTER needs ACCESS
# EXCLUSIVE) -> "canceling statement due to lock timeout" -> collectors crashed and
# stopped collecting.  Serialise with a non-blocking flock: a new cron tick SKIPS
# cleanly while a prior sync is still in progress, instead of piling on.
exec 9>"/tmp/sync_local_to_main.lock"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] [SKIP] a previous sync is still running — exiting (no overlap)"
  exit 0
fi

SRC_HOST="127.0.0.1"          # local = source of truth now
DST_HOST="192.168.30.10"     # off-box backup
DB="energydb"
PGUSER="postgres"
export PGPASSWORD="tbdi@123"

BK_DIR="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/database"
KEEP_DUMPS=5
LOG="$BK_DIR/sync_local_to_210.log"
mkdir -p "$BK_DIR"
log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

STAMP="$(date '+%Y%m%d_%H%M%S')"
DUMP="$BK_DIR/local_push_${STAMP}.dump"

log "=== sync start (local -> .210) ==="

# 0. .210 reachable?  If not, skip cleanly (the whole point of "jab reachable ho")
if ! timeout 5 bash -c "</dev/tcp/$DST_HOST/5432" 2>/dev/null; then
  log "[SKIP] .210 not reachable — will try again next run"
  exit 0
fi

# 1. dump LOCAL first (do NOT touch .210 until this succeeds) -----
log "[1/3] dumping local $DB -> $(basename "$DUMP")"
if ! pg_dump -h "$SRC_HOST" -U "$PGUSER" -Fc -d "$DB" -f "$DUMP" 2>>"$LOG"; then
  log "[FATAL] local pg_dump failed — .210 left untouched"; rm -f "$DUMP"; exit 1
fi
DSZ=$(stat -c %s "$DUMP" 2>/dev/null || echo 0)
if [[ "$DSZ" -lt 1000000 ]]; then
  log "[FATAL] local dump suspiciously small (${DSZ}B) — .210 left untouched"; rm -f "$DUMP"; exit 1
fi
log "      dump ok ($(du -h "$DUMP" | cut -f1))"

# 2. push into .210 (drop + recreate energydb, restore) -----------
log "[2/3] restoring into $DST_HOST/$DB"
psql -h "$DST_HOST" -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 >>"$LOG" 2>&1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname='$DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DB;
CREATE DATABASE $DB OWNER $PGUSER;
SQL
if [[ $? -ne 0 ]]; then log "[FATAL] could not recreate $DB on .210 — aborting"; exit 1; fi
pg_restore --no-owner --no-privileges -h "$DST_HOST" -U "$PGUSER" -j 4 -d "$DB" "$DUMP" 2>>"$LOG"
log "      restore rc=$?"

# 3. verify + prune ----------------------------------------------
TBLS=$(psql -h "$DST_HOST" -U "$PGUSER" -d "$DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null)
log "[3/3] .210 backup now has ${TBLS:-?} tables"
ls -1t "$BK_DIR"/local_push_*.dump 2>/dev/null | tail -n +$((KEEP_DUMPS+1)) | while read -r old; do
  log "      pruning old push dump: $(basename "$old")"; rm -f "$old"
done
log "=== sync done ==="
