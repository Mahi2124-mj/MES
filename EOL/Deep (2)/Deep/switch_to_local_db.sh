#!/usr/bin/env bash
# ============================================================
#   SWITCH THE APP TO THE LOCAL DB   (run as the normal user)
#       ./switch_to_local_db.sh
#
#   Points the MES stack (API + collectors) at the local PostgreSQL
#   (127.0.0.1) instead of 192.168.30.10, by exporting DB_HOST in
#   start_everything.sh, then does a clean restart.  The app uses
#   os.getenv("DB_HOST","192.168.30.10") everywhere, so this env
#   export repoints it with no code edits.
#
#   After this the app runs entirely on THIS box's DB — so .210 being
#   down no longer stops login/dashboard.  .210 stays as a backup
#   (see the optional local->.210 sync cron).
#
#   To revert to .210: remove the DB_HOST block from start_everything.sh
#   (marked LOCAL-DB below) and restart.
# ============================================================
set -uo pipefail

DEEP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START="$DEEP/start_everything.sh"
DBPASS="tbdi@123"

echo " [1/4] Verifying local DB (127.0.0.1) has energydb..."
if ! PGPASSWORD="$DBPASS" psql -h 127.0.0.1 -U postgres -d energydb -tAc "SELECT 1 FROM mes_admin LIMIT 1" >/dev/null 2>&1; then
  echo "   [FATAL] local energydb not ready.  Run first:  sudo ./setup_local_db.sh"
  exit 1
fi
echo "   local energydb OK"

echo " [2/4] Pointing start_everything.sh at 127.0.0.1..."
if ! grep -q 'LOCAL-DB repoint' "$START"; then
  # insert the export right after the LOG_DIR mkdir line
  awk '
    /mkdir -p "\$LOG_DIR"/ && !done {
      print
      print ""
      print "# --- LOCAL-DB repoint (switch_to_local_db.sh) ------------------"
      print "# App now uses THIS box'\''s PostgreSQL so a .210 outage cannot stop"
      print "# login/dashboard.  Remove this block + restart to go back to .210."
      print "export DB_HOST=\"127.0.0.1\""
      print "export DB_PORT=\"5432\""
      print "export DB_NAME=\"energydb\""
      print "export DB_USER=\"postgres\""
      print "export DB_PASS=\"tbdi@123\""
      done=1
      next
    }
    { print }
  ' "$START" > "$START.tmp" && mv "$START.tmp" "$START"
  chmod +x "$START"
  echo "   added DB_HOST=127.0.0.1 export"
else
  echo "   already pointed at local (LOCAL-DB block present)"
fi

echo " [3/4] Clean restart on the local DB..."
"$DEEP/stop_everything.sh" --keep-locks >/dev/null 2>&1
sleep 3
# stale locks in the LOCAL db (from the restore) — clear so --force starts clean
PGPASSWORD="$DBPASS" psql -h 127.0.0.1 -U postgres -d energydb -c "DELETE FROM mes_collector_locks;" >/dev/null 2>&1
rm -f "$DEEP/Phase2/collectors/STOP.flag"
DB_HOST=127.0.0.1 nohup "$START" --with-collectors --force > "$DEEP/logs/_switch_local.out" 2>&1 &

echo " [4/4] Waiting for the stack..."
for i in $(seq 1 30); do
  ss -lnt 2>/dev/null | grep -qE ':8080 ' && break; sleep 2
done
sleep 15
echo
echo " ==============================================================="
echo "   Now running on LOCAL DB (127.0.0.1)."
CONN=$(PGPASSWORD="$DBPASS" psql -h 127.0.0.1 -U postgres -d energydb -tAc "SELECT count(*) FROM pg_stat_activity" 2>/dev/null)
LOGIN=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:5656/api/auth/login -d 'username=admin&password=admin' 2>/dev/null)
echo "   local DB connections: ${CONN:-?}/100   |   login HTTP: ${LOGIN:-?}"
echo " ==============================================================="
echo "   .210 being down will no longer stop the app."
echo "   (Optional: set up a local->.210 backup sync — ask to add it.)"
echo " ==============================================================="
