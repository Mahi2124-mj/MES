#!/usr/bin/env bash
# PM audit runner (read-only agent).  Skips the run if the box is already
# saturated, so the audit never adds load during an incident (and never reports
# overload as app bugs).  Runs at low CPU/IO priority, never two at once.
#   run_audit.sh               full audit (hourly)
#   run_audit.sh --video-only  video checks only
ROOT="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep"
LOAD=$(cut -d' ' -f1 /proc/loadavg); CORES=$(nproc)
if awk "BEGIN{exit !($LOAD > $CORES)}"; then
  echo "$(date '+%F %T') SKIP — load $LOAD > $CORES cores" >> /home/server/pm_agent.log
  exit 0
fi
cd "$ROOT" || exit 1
export MES_BACKGROUND=0
exec flock -n "$ROOT/pmagent/.run.lock" \
  nice -n 10 ionice -c2 -n7 \
  "$ROOT/Phase2/.venv-linux/bin/python" pmagent/pm_agent.py --once "$@" >> /home/server/pm_agent.log 2>&1
