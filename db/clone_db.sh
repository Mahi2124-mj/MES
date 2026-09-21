#!/usr/bin/env bash
# ============================================================
#  DB CLONE DUMP  (structure + functions + config data)
#
#  ONE importable SQL file that recreates the whole database:
#  EVERY table, function, view, index, trigger + the DATA of
#  small config/master tables.
#
#  Row DATA of LARGE tables (anything over DATA_MAX_BYTES) is
#  skipped -- their STRUCTURE is still included -- so the file
#  stays small enough for GitHub (<100 MB/file). The big tables
#  are cycle/transactional logs that are regenerated live; full
#  production data lives in db_backup.sh dumps, not in git.
#
#  Which tables get skipped is decided LIVE from actual sizes,
#  so it keeps working as tables grow / new lines are added.
#
#  Re-create elsewhere:
#     createdb -h HOST -U postgres energydb        # if it doesn't exist
#     psql -h HOST -U postgres -d energydb -f db/energydb_clone.sql
# ============================================================
set -uo pipefail

DB_HOST="${DB_HOST:-127.0.0.1}"   # local = source of truth (see Phase2/database.py); override via env
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASS="${DB_PASS:-tbdi@123}"
DB_NAME="${DB_NAME:-energydb}"

# Tables bigger than this keep STRUCTURE but SKIP row data. Lower it if the
# dump is still too big; raise it to include more data.
DATA_MAX_BYTES="${DATA_MAX_BYTES:-262144}"   # 256 KB

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/${DB_NAME}_clone.sql"

command -v pg_dump >/dev/null 2>&1 || { echo "[FATAL] install: sudo apt install -y postgresql-client" >&2; exit 1; }
command -v psql    >/dev/null 2>&1 || { echo "[FATAL] install: sudo apt install -y postgresql-client" >&2; exit 1; }
export PGPASSWORD="$DB_PASS"

# Data-heavy ordinary tables (incl. partitions) over the threshold.
mapfile -t BIG < <(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "
  SELECT n.nspname||'.'||c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'r'
    AND n.nspname NOT IN ('pg_catalog','information_schema')
    AND pg_total_relation_size(c.oid) > ${DATA_MAX_BYTES}
  ORDER BY 1;") || { echo "[FATAL] cannot reach DB $DB_NAME@$DB_HOST" >&2; exit 1; }

args=(--no-owner --no-privileges --clean --if-exists)
for t in "${BIG[@]}"; do [ -n "$t" ] && args+=(--exclude-table-data="$t"); done

echo "[clone_db] $DB_NAME @ $DB_HOST : full structure + config data, skipping row-data of ${#BIG[@]} large tables"
if pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "${args[@]}" -f "$OUT"; then
  echo "[clone_db] OK -> $OUT ($(du -h "$OUT" | cut -f1))"
else
  echo "[clone_db] [FATAL] pg_dump failed" >&2; exit 1
fi

SZB="$(stat -c%s "$OUT" 2>/dev/null || echo 0)"
if [ "$SZB" -gt 104857600 ]; then
  echo "[clone_db] [WARN] $OUT is over 100 MB -- GitHub will reject it." >&2
  echo "           Re-run smaller, e.g.:  DATA_MAX_BYTES=65536 bash db/clone_db.sh" >&2
fi
