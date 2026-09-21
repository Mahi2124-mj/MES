#!/usr/bin/env bash
# ============================================================
#   ENERGYDB DAILY BACKUP  ->  22 TB data disk
#
#   Dumps the whole energydb (custom/compressed format, restorable
#   with pg_restore) into  <disk>/eol-data/database/  once a day.
#   Keeps RETENTION_DAYS of dumps, deletes older ones.
#
#   Runs from cron (see db_backup.cron).  Safe to run by hand too.
#   Exits 0 = success, 1 = failure (cron mail / log shows why).
# ============================================================
set -uo pipefail

# --- config -----------------------------------------------------
DB_HOST="${DB_HOST:-192.168.10.210}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASS="${DB_PASS:-tbdi@123}"
DB_NAME="${DB_NAME:-energydb}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

DISK="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data"
DEST="$DISK/database"
MARKER="$DISK/.disk_ok"
LOG="$DEST/_backup.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG" 2>/dev/null; }

# --- preflight --------------------------------------------------
# Only write to the data disk when it is actually mounted (same
# .disk_ok marker the collector buffer uses).  Never dump onto the
# root fs by mistake if the disk is absent.
if [[ ! -f "$MARKER" ]]; then
  echo "[$(date '+%F %T')] [SKIP] data disk not mounted ($MARKER missing) — backup skipped" >&2
  exit 1
fi
mkdir -p "$DEST"

if ! command -v pg_dump >/dev/null 2>&1; then
  log "[FATAL] pg_dump not installed.  Run:  sudo apt install -y postgresql-client"
  exit 1
fi

# --- connection-headroom guard ----------------------------------
# pg_dump itself needs a slot; if the DB is already near max_connections
# (the app baseline is ~45/100), adding the dump could tip it over and the
# LIVE app would get "too many clients".  Refuse to start unless there is
# comfortable headroom, so the backup never disrupts production.
CONN_INFO="$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
             -tAc "SELECT count(*)||'/'||setting FROM pg_stat_activity, pg_settings WHERE name='max_connections'" 2>/dev/null)"
if [[ -n "$CONN_INFO" ]]; then
  CUR="${CONN_INFO%%/*}"; MAXC="${CONN_INFO##*/}"
  if [[ "$CUR" =~ ^[0-9]+$ && "$MAXC" =~ ^[0-9]+$ ]]; then
    HEADROOM=$(( MAXC - CUR ))
    if (( HEADROOM < 20 )); then
      log "[SKIP] only $HEADROOM connection slots free ($CUR/$MAXC) — backup deferred to avoid starving the live app"
      exit 1
    fi
    log "connection headroom OK ($CUR/$MAXC used)"
  fi
fi

STAMP="$(date '+%Y-%m-%d_%H%M%S')"
OUT="$DEST/${DB_NAME}_${STAMP}.dump"

# --- dump -------------------------------------------------------
log "starting pg_dump of $DB_NAME from $DB_HOST -> $OUT"
if PGPASSWORD="$DB_PASS" pg_dump \
      -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
      -d "$DB_NAME" -Fc --no-owner --no-privileges \
      -f "$OUT" 2>>"$LOG"; then
  SZ="$(du -h "$OUT" | cut -f1)"
  log "OK  backup written: $(basename "$OUT")  ($SZ)"
else
  rc=$?
  log "[FATAL] pg_dump failed (rc=$rc) — removing partial file"
  rm -f "$OUT"
  exit 1
fi

# --- retention (delete dumps older than RETENTION_DAYS) ---------
DELN="$(find "$DEST" -maxdepth 1 -name "${DB_NAME}_*.dump" -type f -mtime "+${RETENTION_DAYS}" -print -delete 2>/dev/null | wc -l)"
[[ "$DELN" -gt 0 ]] && log "retention: deleted $DELN dump(s) older than ${RETENTION_DAYS} days"

TOTAL="$(find "$DEST" -maxdepth 1 -name "${DB_NAME}_*.dump" -type f | wc -l)"
log "done.  $TOTAL backup(s) on disk, total $(du -sh "$DEST" 2>/dev/null | cut -f1)"
exit 0
