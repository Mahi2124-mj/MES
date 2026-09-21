#!/usr/bin/env bash
COLL="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/Phase2/collectors"
PY="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/Phase2/.venv-linux/bin/python"
LOG="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/logs/collector_collector_ync_l6.log"
# reap ALL ync_l6 loops + pythons
for pid in $(pgrep -x bash); do fl="$(tr '\0' ' ' </proc/$pid/cmdline 2>/dev/null)"; [[ "$fl" == *collector_ync_l6* ]] && kill -9 "$pid" 2>/dev/null; done
for pid in $(pgrep -f 'collector_ync_l6\.py'); do fl="$(tr '\0' ' ' </proc/$pid/cmdline 2>/dev/null)"; [[ "$fl" == *.venv-linux/bin/python* ]] && kill -9 "$pid" 2>/dev/null; done
sleep 3
# start ONE fresh loop
setsid bash -c 'cd "$0"; export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=energydb DB_USER=postgres DB_PASS="tbdi@123"; while true; do [[ -f STOP.flag ]]&&break; "$1" _clear_stale_lock.py "$2"; PYTHONUNBUFFERED=1 "$1" -u "$2"; sleep 5; done' "$COLL" "$PY" "collector_ync_l6.py" </dev/null >>"$LOG" 2>&1 &
disown
