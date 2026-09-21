#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# cms_watchdog.sh  (2026-09-16)
# Interim safety-net for the CMS-API (:5555) thread-pileup wedge.
#
# ROOT CAUSE it guards: the CMS runs on Flask's dev server with threaded=True
# (unbounded thread-per-request).  After a restart, older cycles' footage is not
# in the fresh rolling .ts, so panels/prefetch get 416 and RETRY; each in-flight
# clip request holds a CMS thread, threads pile up (seen: 650 → health times out,
# every /cycle-video 502s → "video slow / pages take forever"), and the box has
# to be restarted by hand.  This watchdog restarts the CMS *cleanly and on GPU*
# BEFORE it fully wedges, so video keeps working without manual intervention.
#
# It fires on EITHER:  CMS thread count > THRESH   OR   /health not answering.
# A COOLDOWN prevents restart loops (and keeps the 90 s camera-quiet gap rare).
# Runs from cron every 2 min (survives a power-cut reboot; see install note).
#
# This is an INTERIM guard.  The real fix is to bound the CMS server threads
# (waitress) + fast-fail out-of-window clips — a planned CMS change.
# ─────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────
# 2026-09-19 — RESTART REMOVED.  OBSERVE-ONLY.
# Operator: "kisi ko bhi restart ki permission mt de … CMS sirf hard coded hi
# band ho sakta hai, chahe kuch bhi ho."  This watchdog restarted the CMS 12
# times on 19-Sep and 28 times on 18-Sep; every restart is ~96 s with NO camera
# recording on any line (the "#1004 -> #996" jumps in the video archive), and
# in 23 of 39 unhealthy episodes the CMS recovered on its own anyway.
# It now only WRITES what it sees to the log.  There is no restart path left in
# this file — do not add one back.  The CMS is stopped only by a person
# (cms.sh / stop_everything.sh / restart_cms.py run by hand).
# ─────────────────────────────────────────────────────────────────────────
set -u

ROOT="/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep"
LOG="/home/server/cms_watchdog.log"

THRESH=560          # report (only) when CMS threads exceed this (normal ~250-300; wedge ~650)
HEALTH_TIMEOUT=12   # seconds; no answer = wedged

log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }

# --- locate the live CMS pid on :5555 ---
CMS_PID=$(ss -ltnp 2>/dev/null | grep ':5555 ' | grep -oP 'pid=\K[0-9]+' | head -1)

reason=""
if [ -z "$CMS_PID" ]; then
  reason="CMS not listening on :5555"
else
  THREADS=$(ls "/proc/$CMS_PID/task" 2>/dev/null | wc -l)
  # health probe (401 = up-and-answering; 000/timeout = wedged)
  HCODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$HEALTH_TIMEOUT" \
            http://127.0.0.1:5555/api/cameras/health 2>/dev/null)
  if [ "$THREADS" -gt "$THRESH" ]; then
    reason="threads=$THREADS > $THRESH"
  elif [ "$HCODE" = "000" ]; then
    reason="health not answering (code=$HCODE, threads=$THREADS)"
  else
    # healthy — record a heartbeat at most once per 10 min to keep the log small
    exit 0
  fi
fi

# --- observe only: record it, touch nothing ---
log "CMS UNHEALTHY ($reason) — NOT restarting (auto-restart removed by operator 2026-09-19)"
exit 0
