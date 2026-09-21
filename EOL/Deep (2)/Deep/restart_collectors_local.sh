#!/usr/bin/env bash
set -uo pipefail
DEEP="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep"
COLL="$DEEP/Phase2/collectors"; PY="$DEEP/Phase2/.venv-linux/bin/python"; LOG="$DEEP/logs"
for pass in 1 2; do
  : > "$COLL/STOP.flag"
  for pid in $(pgrep -x bash 2>/dev/null); do fl="$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)" || continue; case "$fl" in *"while true"*collector_*|*_clear_stale_lock*) kill -9 "$pid" 2>/dev/null;; esac; done
  for pid in $(pgrep -f 'collector_[a-z0-9_]*\.py|_clear_stale_lock\.py' 2>/dev/null); do fl="$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)" || continue; case "$fl" in *.venv-linux/bin/python*) kill -9 "$pid" 2>/dev/null;; esac; done
  sleep 2
done
rm -f "$COLL/STOP.flag"
shopt -s nullglob; n=0
for f in "$COLL"/collector_*.py; do base="$(basename "$f")"; cn="${base%.py}"
  setsid bash -c 'cd "$0"||exit 1; export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=energydb DB_USER=postgres DB_PASS="tbdi@123"; while true; do [[ -f STOP.flag ]]&&break; "$1" _clear_stale_lock.py "$2"; PYTHONUNBUFFERED=1 "$1" -u "$2"; sleep 5; done' "$COLL" "$PY" "$base" </dev/null >>"$LOG/collector_${cn}.log" 2>&1 &
  n=$((n+1))
done
echo "launched $n collectors on local"
