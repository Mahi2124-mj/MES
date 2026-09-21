#!/usr/bin/env bash
# cms.sh — start / stop / restart ONLY the CMS-API, nothing else.
#
# Operator: "ek sh file … for only cms start stop".
#
# start_everything.sh brings up the whole stack (MES-API, frontend, CMS,
# collectors).  When only the camera side is sick, cycling all of it is far more
# disruption than the problem deserves — and every extra restart costs recorder
# sessions.  This touches the CMS and nothing else.
#
#   ./cms.sh status     what is running, is it answering, how many recorders
#   ./cms.sh stop       stop the CMS (whole process group, recorders included)
#   ./cms.sh start      start it exactly the way start_everything.sh does
#   ./cms.sh restart    stop, wait out the quiet window, start
#
# WHY THE QUIET WINDOW: these cameras accept ONE RTSP session.  Kill a recorder
# and the camera still believes the old session is open; a recorder that
# reconnects immediately is refused and the camera ends up with no recording at
# all.  Waiting lets the camera drop the session on its own.  90 s is the value
# that has worked here; override with CMS_QUIET=nn.
#
# Deliberately NOT a systemd unit: start_everything.sh owns the CMS pidfile
# (logs/CMS-API.pid) and this stays compatible with it, so stop_everything.sh
# still works afterwards.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMS_DIR="$(cd "$ROOT/../.." && pwd)/New folder (2)/New folder (2)"
BACKEND="$CMS_DIR/backend"
PY_CMS="$BACKEND/.venv-linux/bin/python"
LOG_DIR="$ROOT/logs"
PIDFILE="$LOG_DIR/CMS-API.pid"
LOGFILE="$LOG_DIR/CMS-API.log"
PORT=5555

# Same value start_everything.sh uses — 64 cores here, and a narrow gate made
# several operators clicking video queue behind each other.
CLIP_RENDER_PARALLEL="${CLIP_RENDER_PARALLEL:-16}"
QUIET="${CMS_QUIET:-90}"

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_off=$'\033[0m'
ok()   { echo "  ${c_grn}✓${c_off} $*"; }
warn() { echo "  ${c_yel}!${c_off} $*"; }
bad()  { echo "  ${c_red}✗${c_off} $*"; }

cms_pids() {   # every pid listening on the CMS port
  ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | sort -u
}

recorders() { pgrep -fc "ffmpeg.*rtsp" 2>/dev/null || echo 0; }

health() {     # prints "CODE SECONDS"
  # curl exits non-zero on timeout but has ALREADY printed "000 <secs>", so a
  # `|| echo` fallback appends a SECOND answer and the caller reads both.
  local out
  out="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time 10 \
         "http://127.0.0.1:$PORT/api/cameras/health" 2>/dev/null)"
  echo "${out:-000 -}"
}

status() {
  local pids; pids="$(cms_pids)"
  echo "CMS-API status"
  if [[ -z "$pids" ]]; then
    bad "not running (nothing listening on :$PORT)"
  else
    for p in $pids; do
      ok "pid $p  up $(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')s  threads $(ls /proc/"$p"/task 2>/dev/null | wc -l)"
    done
    read -r code secs <<<"$(health)"
    # The CMS answers 401 on this endpoint without a token — that is healthy.
    if [[ "$code" == "200" || "$code" == "401" ]]; then
      ok "health HTTP $code in ${secs}s"
    else
      bad "health HTTP $code in ${secs}s — wedged or still starting"
    fi
  fi
  echo "  recorders running : $(recorders)"
  echo "  fresh .ts (3 min) : $(find /run/media/server/*/eol-data/videos -maxdepth 1 -name '*.ts' -mmin -3 2>/dev/null | wc -l)"
  echo "  log               : $LOGFILE"
}

stop() {
  echo "Stopping CMS-API"
  local pids; pids="$(cms_pids)"
  if [[ -z "$pids" ]]; then
    warn "already stopped"
  else
    # Negative pid = the whole process group, so the recorders it spawned go
    # with it instead of being left behind holding camera sessions.
    for p in $pids; do
      kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
      ok "TERM sent to $p"
    done
    for _ in $(seq 1 20); do
      [[ -z "$(cms_pids)" ]] && break
      sleep 1
    done
    if [[ -n "$(cms_pids)" ]]; then
      warn "still up after 20 s — sending KILL"
      for p in $(cms_pids); do kill -9 -"$p" 2>/dev/null || kill -9 "$p" 2>/dev/null; done
      sleep 2
    fi
  fi
  # Recorders are children of the CMS; anything still alive is an orphan that
  # would keep a camera session open and block the next recorder.
  local orph; orph="$(pgrep -f 'ffmpeg.*rtsp' 2>/dev/null | wc -l)"
  if (( orph > 0 )); then
    pkill -TERM -f 'ffmpeg.*rtsp' 2>/dev/null
    sleep 2
    pkill -9 -f 'ffmpeg.*rtsp' 2>/dev/null
    ok "cleared $orph orphan recorder(s)"
  fi
  rm -f "$PIDFILE"
  ok "stopped"
}

start() {
  echo "Starting CMS-API"
  if [[ -n "$(cms_pids)" ]]; then
    warn "already running on :$PORT — use restart"
    return 0
  fi
  [[ -x "$PY_CMS" ]]            || { bad "CMS venv missing: $PY_CMS"; return 1; }
  [[ -f "$BACKEND/api_server.py" ]] || { bad "api_server.py missing in $BACKEND"; return 1; }
  mkdir -p "$LOG_DIR"
  # setsid so it leads its own process group and stop can take the whole tree.
  ( cd "$BACKEND" && exec env PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
      CLIP_RENDER_PARALLEL="$CLIP_RENDER_PARALLEL" \
      setsid "$PY_CMS" api_server.py >>"$LOGFILE" 2>&1 ) &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 60); do
    [[ -n "$(cms_pids)" ]] && break
    sleep 1
  done
  if [[ -n "$(cms_pids)" ]]; then
    ok "listening on :$PORT (CLIP_RENDER_PARALLEL=$CLIP_RENDER_PARALLEL)"
    echo "  recorders reconnect over the next minute or two — check with: $0 status"
  else
    bad "did not come up in 60 s — see $LOGFILE"
    return 1
  fi
}

restart() {
  stop
  echo "Quiet window ${QUIET}s — letting the cameras drop their old RTSP sessions"
  for i in $(seq "$QUIET" -10 1); do printf "\r  %3ds remaining " "$i"; sleep 10; done
  printf "\r                     \r"
  start
}

case "${1:-status}" in
  start)   start   ;;
  stop)    stop    ;;
  restart) restart ;;
  status)  status  ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
