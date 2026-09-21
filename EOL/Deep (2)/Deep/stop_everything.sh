#!/usr/bin/env bash
# ============================================================
#   EOL UNIFIED STOP  —  Linux port of stop_everything.bat
#
#   Ported 2026-07-20.  Mirrors the .bat, with one ordering fix:
#   STOP.flag is dropped FIRST, before anything is killed, so the
#   never-die collector loops see it on exit and don't relaunch.
#   (The .bat kills ports first, which leaves a small window where
#   a loop can respawn a collector before the flag lands.)
#
#   By default this clears mes_collector_locks, same as the .bat.
#   Pass --keep-locks to leave them alone — use that if collectors
#   are running on ANOTHER host and you're only stopping local
#   services, since the DELETE is global, not per-host.
# ============================================================
set -uo pipefail

# 2026-07-24 — app runs on the LOCAL DB now; default DB_HOST to 127.0.0.1 so
# the lock-clear step reaches it (was defaulting to .210, which is off-box and
# down -> "DB unreachable — locks not cleared").  An explicit DB_HOST still wins.
export DB_HOST="${DB_HOST:-127.0.0.1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES_DIR="$ROOT/Phase2"
LOG_DIR="$ROOT/logs"
PY="$MES_DIR/.venv-linux/bin/python"

KEEP_LOCKS=0
[[ "${1:-}" == "--keep-locks" ]] && KEEP_LOCKS=1

# Reliably reap the collector fleet: the never-die loops are inline
# `bash -c 'while true …'` scripts that reference _clear_stale_lock.py, and
# each collector is `…/.venv-linux/bin/python -u collector_*.py`.  The old
# `pkill -f …collector_` only hit the pythons, leaving the loops alive to
# respawn them — so collectors survived every "stop", their DB connections
# accumulated across restarts, and the DB eventually hit max_connections.
# We match on /proc/PID/cmdline (this script's own cmdline is just
# "bash stop_everything.sh", so it never self-matches), loops FIRST so
# nothing respawns, then the pythons, twice to catch stragglers.
_reap_collectors() {
  local pass pid cmd
  for pass in 1 2; do
    : > "$MES_DIR/collectors/STOP.flag" 2>/dev/null
    for pid in $(pgrep -x bash 2>/dev/null); do
      cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
      case "$cmd" in
        *_clear_stale_lock*|*"while true"*collector_*) kill -9 "$pid" 2>/dev/null ;;
      esac
    done
    for pid in $(pgrep -f 'collector_[a-z0-9_]*\.py' 2>/dev/null); do
      cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
      case "$cmd" in
        *.venv-linux/bin/python*collector_*) kill -9 "$pid" 2>/dev/null ;;
      esac
    done
    sleep 2
  done
}

echo
echo " ==============================================================="
echo "   EOL Unified STOP  (Linux port)"
echo " ==============================================================="
echo

# --- 1. Disarm the never-die loops BEFORE killing anything ------
echo " [1/4]  Dropping collector STOP.flag..."
mkdir -p "$MES_DIR/collectors"
: > "$MES_DIR/collectors/STOP.flag"
echo "        Done."

# --- 2. Kill our own tracked PIDs -------------------------------
echo " [2/4]  Stopping tracked services..."
if [[ -d "$LOG_DIR" ]]; then
  for pidfile in "$LOG_DIR"/*.pid; do
    [[ -e "$pidfile" ]] || continue
    pid="$(cat "$pidfile" 2>/dev/null)"
    name="$(basename "$pidfile" .pid)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "        - $name (pid $pid)"
      # negative pid = whole process group, so the never-die bash
      # wrapper and its python child both go down together
      kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
  done
fi
sleep 2

# --- 3. Sweep by port + process name ----------------------------
# Note: unlike start_everything.sh, 5656 IS included here — the .bat
# does the same.  Stop takes the frontend down deliberately.
echo " [3/4]  Sweeping ports (8080, 5555, 5656, 5575, 5000, 8050, 5173)..."
for port in 8080 5555 5656 5575 5000 8050 5173; do
  fuser -k -TERM "${port}/tcp" >/dev/null 2>&1
done
sleep 1
for port in 8080 5555 5656 5575 5000 8050 5173; do
  fuser -k -KILL "${port}/tcp" >/dev/null 2>&1
done
echo "        - reaping collector fleet (loops + pythons)..."
_reap_collectors
# Backstop: kill anything from THIS box still holding a DB connection to
# the energydb host (collectors are the only such holders once services
# are down) — catches any process the cmdline match missed.
DB_HOST_IP="${DB_HOST:-192.168.30.10}"
for pid in $(ss -tnp 2>/dev/null | grep ":5432" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u); do
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
  case "$cmd" in *collector_*) kill -9 "$pid" 2>/dev/null ;; esac
done
sleep 2
_LEFT="$(pgrep -f 'collector_[a-z0-9_]*\.py' 2>/dev/null | wc -l)"
_HOLD="$(ss -tn 2>/dev/null | grep -c ':5432')"
echo "        Done.  (collector procs left: $_LEFT, local DB connections: $_HOLD)"

# --- 3b. Stop the Cloudflare tunnel + any in-progress DB sync ----
# 2026-07-28 — "stop_everything = kill EVERYTHING".  cloudflared is matched by
# EXACT process name (pkill -x cloudflared) so this script's own cmdline — which
# contains the word "cloudflared" — is NEVER self-matched (a pkill -f would kill
# this shell).  Also halt a running local->.30.10 sync (its pg_dump/pg_restore
# both carry the local_push_ dump path) so a stop doesn't leave a half-restore
# hammering the box + holding local locks that starve a later collector restart.
echo " [3b/4] Stopping Cloudflare tunnel + in-progress DB sync..."
if pkill -9 -x cloudflared 2>/dev/null; then echo "        - cloudflared tunnel stopped"
else echo "        - cloudflared not running"; fi
for _p in $(pgrep -x pg_dump 2>/dev/null) $(pgrep -x pg_restore 2>/dev/null); do
  _c="$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null)"
  case "$_c" in *local_push_*) kill -9 "$_p" 2>/dev/null && echo "        - killed sync pid $_p" ;; esac
done
echo "        Done."

# --- 4. Clear locks + mark lines stopped ------------------------
if [[ $KEEP_LOCKS -eq 1 ]]; then
  echo " [4/4]  SKIPPED — --keep-locks given, mes_collector_locks untouched."
elif [[ -x "$PY" ]]; then
  echo " [4/4]  Clearing mes_collector_locks + marking lines stopped..."
  "$PY" -c "
import os, psycopg2
c = psycopg2.connect(host=os.getenv('DB_HOST','192.168.30.10'), port=5432,
                     user='postgres', password='tbdi@123', dbname='energydb',
                     connect_timeout=5,
                     options='-c lock_timeout=3000 -c statement_timeout=3000')
cur = c.cursor()
cur.execute('DELETE FROM mes_collector_locks')
cur.execute(\"UPDATE mes_lines SET collector_pid=NULL, collector_status='stopped' WHERE collector_status='running'\")
c.commit(); c.close()
" 2>/dev/null && echo "        Done." || echo "        [WARN] DB unreachable — locks not cleared."
else
  echo " [4/4]  SKIPPED — Linux venv missing, cannot reach DB."
fi

echo
echo " All local services stopped."
echo " Re-arm collectors by deleting: $MES_DIR/collectors/STOP.flag"
echo
