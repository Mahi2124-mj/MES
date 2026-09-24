#!/usr/bin/env bash
# ============================================================
#   EOL UNIFIED LAUNCHER  —  Linux port of start_everything.bat
#
#   Ported 2026-07-20.  Same 4-phase structure as the .bat, but:
#     - paths are derived from this script's location, not D:\
#     - services run as background processes with logs under ./logs
#       (no cmd windows on Linux)
#     - the collector phase is OPT-IN behind --with-collectors,
#       and refuses to run if another host already holds live
#       collector locks.  See "WHY THE GATE" below.
#
#   Usage:
#     ./start_everything.sh                    API + dashboard only
#     ./start_everything.sh --with-collectors  full parity (see gate)
#     ./start_everything.sh --force            override the lock gate
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MES_DIR="$ROOT/Phase2"
MES_FE="$ROOT/mes-frontend"
CMS_DIR="$(cd "$ROOT/../.." && pwd)/New folder (2)/New folder (2)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# --- LOCAL-DB repoint (switch_to_local_db.sh) ------------------
# App now uses THIS box's PostgreSQL so a .210 outage cannot stop
# login/dashboard.  Remove this block + restart to go back to .210.
export DB_HOST="127.0.0.1"
export DB_PORT="5432"
export DB_NAME="energydb"
export DB_USER="postgres"
export DB_PASS="tbdi@123"

# --- Video encoder: force GPU (NVENC) deterministically ---------
# 2026-09-16 — After a power-cut reboot this stack auto-starts here (via
# eol.service -> eol_boot.sh -> start_everything.sh), NOT via restart_cms.py.
# The CMS live-recorder encoder is otherwise chosen by a lazy NVENC probe that,
# if the box is loaded at boot, times out and caches libx264 for the whole
# process life — pinning ~19 live transcodes on the CPU (load ~46+) until
# someone manually restarts onto GPU.  That was the recurring "after every
# power-cut / restart the video goes to CPU and I have to fix it by hand"
# problem.  NVENC is proven-good on this box's RTX A2000, so force it at boot and
# skip the flaky probe.  If the GPU/driver ever breaks, set this to libx264 here
# (or `VIDEO_LIVE_ENCODER=libx264 ./start_everything.sh`) to fall back to CPU.
# 2026-09-24 — clip archive catch-up.  The window was 42 min, a leftover from
# when the .ts files were kept ~50 min; since 21-Sep TS_KEEP_HOURS=48, so every
# cycle of the last two days can still be cut.  At 42 min anything older was
# abandoned unarchived — measured 24-Sep: 120,605 cycles, 37,542 clips (31%),
# 46,151 of the misses logged as "camera was recording but no clip was cut in
# time".  The CPU lane goes wider because the single NVENC engine sits at 100%
# while ~45 cores are idle; CLIP_ARCHIVE_CPU_MIN_IDLE still throttles it.
export CLIP_ARCHIVE_WINDOW_MIN="${CLIP_ARCHIVE_WINDOW_MIN:-1440}"
export CLIP_ARCHIVE_CPU_PARALLEL="${CLIP_ARCHIVE_CPU_PARALLEL:-6}"
export VIDEO_LIVE_ENCODER="${VIDEO_LIVE_ENCODER:-h264_nvenc}"
echo "  video encoder: VIDEO_LIVE_ENCODER=$VIDEO_LIVE_ENCODER (recorders forced to GPU at boot)"

# --- File-descriptor limit ------------------------------------
# 2026-07-24 — MES-API (8080) was choking with "OSError: [Errno 24] Too
# many open files" after ~6h: it hit the default soft limit of 1024 FDs
# (many camera/PLC sockets + dashboard connections) and stopped accepting
# new connections -> dashboard "nothing loads".  Raise the soft limit for
# every child we launch (hard limit is 524288, so 65536 is safe).  Children
# inherit this rlimit across setsid/fork/exec.
ulimit -n 65536 2>/dev/null || true

# --- Durable data disk (22 TB) ---------------------------------
# DB-down write buffer + videos live here so they survive on dedicated
# storage.  DATA_DISK is the mount point; collector_engine._cache_dir()
# only uses COLLECTOR_CACHE_DIR when its parent is a real mount, so if the
# disk is absent the collectors fall back to their local cache safely.
DATA_DISK="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data"
export COLLECTOR_CACHE_DIR="$DATA_DISK/buffer"
if [[ -d "$DATA_DISK" ]]; then
  echo "  data disk: $DATA_DISK  (buffer -> $COLLECTOR_CACHE_DIR)"
else
  echo "  [WARN] data disk not mounted ($DATA_DISK) — collectors will buffer to their LOCAL cache until it is."
fi

# --- ffmpeg for CMS video (recording + clip extraction) --------
# imageio_ffmpeg's bundled static ffmpeg-7.0.2 SEGFAULTs while READING our
# MPEG-TS recordings, so recording worked but every clip extraction crashed
# → all cycle videos 404/500.  imageio_ffmpeg honours IMAGEIO_FFMPEG_EXE, so
# point it at the apt-installed system ffmpeg (install: sudo apt install ffmpeg).
if [[ -x /usr/bin/ffmpeg ]]; then
  export IMAGEIO_FFMPEG_EXE="/usr/bin/ffmpeg"
  echo "  ffmpeg: system /usr/bin/ffmpeg (clip extraction safe)"
else
  echo "  [WARN] system ffmpeg missing — CMS will use imageio_ffmpeg's static build, which SEGFAULTs on clip extraction. Run: sudo apt install -y ffmpeg"
fi

# Linux venv kept SEPARATE from the Windows .venv so we never clobber
# the Windows box's interpreter if this tree is ever copied back.
PY="$MES_DIR/.venv-linux/bin/python"
# CMS has its own dependency set (opencv, dash, flask-limiter, PyJWT)
# and its own requirements.txt — it must NOT share the MES venv.
PY_CMS="$CMS_DIR/backend/.venv-linux/bin/python"

# 2026-07-28 — collectors ON BY DEFAULT.  Operator spec: "start_everything.sh se
# sab on ho".  Plain `./start_everything.sh` now brings up EVERYTHING (services +
# collectors + tunnel).  Phase 1 always kills the collector tree, so running
# WITHOUT starting them (the old default) left 0 collectors = no data collection —
# a footgun.  --with-collectors is kept as a no-op for backward compat; use
# --no-collectors to deliberately bring up services only.
WITH_COLLECTORS=1
FORCE=0
# 2026-07-28 — Cloudflare tunnel is OPT-IN, default OFF.  Operator: "abhi ke liye
# LAN pe rakho".  Plain `./start_everything.sh` = LAN only.  Pass --tunnel to also
# expose the dashboard on the internet via cloudflared.
TUNNEL=0
for arg in "$@"; do
  case "$arg" in
    --with-collectors) WITH_COLLECTORS=1 ;;
    --no-collectors)   WITH_COLLECTORS=0 ;;
    --tunnel)          TUNNEL=1 ;;
    --force)           FORCE=1 ;;
    -h|--help)         sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "  [WARN] unknown arg: $arg" ;;
  esac
done

echo
echo " ==============================================================="
echo "   EOL Unified Launcher  (Linux port)"
echo " ==============================================================="
echo "   MES Backend   http://127.0.0.1:8080     uvicorn"
echo "   MES Frontend  http://127.0.0.1:5656     static dist"
echo "   CMS API       http://127.0.0.1:5555     Flask + plc_edge"
echo "   CMS Frontend  http://127.0.0.1:5575     Vite"
echo " ==============================================================="
echo

# --- Preflight: interpreter -------------------------------------
if [[ ! -x "$PY" ]]; then
  cat <<EOF
 [FATAL] Linux venv missing at:
           $MES_DIR/.venv-linux

 The existing Phase2/.venv is a WINDOWS venv (Scripts/, Lib/, no bin/)
 and cannot be used here.  Build the Linux one:

   sudo apt install -y python3.12 python3.12-venv python3-pip libpq-dev
   python3.12 -m venv "$MES_DIR/.venv-linux"
   "$MES_DIR/.venv-linux/bin/pip" install -r "$MES_DIR/requirements.txt"

 NOTE: requirements.txt pins fastapi==0.110.0 / pydantic==2.6.0 /
 psycopg2-binary==2.9.9.  Those have no wheels for this box's
 python3.14 — use 3.12 as above, or unpin them.
EOF
  exit 1
fi

# --- PHASE 1: cleanup stale services ----------------------------
# Port 5656 is EXCLUDED, same as the .bat (2026-06-18 hardening):
# it is owned by the static-dist server / reverse proxy, and killing
# it would also drop :443/:8443.
echo " [PHASE 1/4]  Cleaning up stale services..."
echo "               - port-bound (8080, 5555, 5575, 5000, 8050, 5173)   [5656 EXCLUDED]"
for port in 8080 5555 5575 5000 8050 5173; do
  # fuser is the closest Linux equivalent of the .bat's netstat -ano | Stop-Process
  fuser -k -TERM "${port}/tcp" >/dev/null 2>&1
done
sleep 1
for port in 8080 5555 5575 5000 8050 5173; do
  fuser -k -KILL "${port}/tcp" >/dev/null 2>&1
done

echo "               - collector python tree (parent + children)"
# pkill -f matches the full command line, so this catches the venv
# python running collector_*.py without needing the shim-vs-child
# distinction the Windows version had to work around.
pkill -f "$MES_DIR/.venv-linux/bin/python.*collector_" >/dev/null 2>&1
echo "               Done."
echo

# --- PHASE 2 + collectors gate ----------------------------------
if [[ $WITH_COLLECTORS -eq 1 ]]; then
  # WHY THE GATE:
  #   The .bat does an unconditional  DELETE FROM mes_collector_locks
  #   then starts one never-die loop per collector_*.py.  If the Windows
  #   production box is still up, that wipes the REAL collectors'
  #   singleton locks and brings up a second writer per line —
  #   duplicate cycle counts in energydb, plus MC-protocol slot
  #   contention on all 13 Q-series CPUs (the WinError 10061 the .bat's
  #   own comments describe).  So: look before deleting.
  echo " [PHASE 2/4]  Checking for live collector locks..."
  LOCKHOLDERS="$("$PY" - <<'PYEOF' 2>/dev/null
import os, socket, sys
try:
    import psycopg2
except ImportError:
    sys.exit(3)
try:
    c = psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.30.10"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "tbdi@123"),
        dbname=os.getenv("DB_NAME", "energydb"),
        connect_timeout=5,
        options="-c lock_timeout=3000 -c statement_timeout=3000",
    )
    cur = c.cursor()
    cur.execute("SELECT * FROM mes_collector_locks")
    rows = cur.fetchall()
    c.close()
    me = socket.gethostname()
    for r in rows:
        print(" | ".join(str(x) for x in r))
except Exception as e:
    print("ERR:%s" % e)
    sys.exit(4)
PYEOF
)"
  RC=$?
  if [[ $RC -eq 3 ]]; then
    echo " [FATAL] psycopg2 not installed in the Linux venv."; exit 1
  elif [[ $RC -eq 4 ]]; then
    echo " [FATAL] could not reach energydb: $LOCKHOLDERS"; exit 1
  fi

  if [[ -n "$LOCKHOLDERS" && $FORCE -eq 0 ]]; then
    cat <<EOF

 ==============================================================
  REFUSING TO START COLLECTORS
 ==============================================================
  mes_collector_locks is NOT empty — another host is actively
  collecting.  Rows currently held:

$LOCKHOLDERS

  Starting here would DELETE those locks and run a second
  collector per line against the same PLCs and the same
  energydb.  That double-counts cycles on a LIVE line.

  If the Windows box is genuinely down and these are stale,
  re-run with:   ./start_everything.sh --with-collectors --force
 ==============================================================
EOF
    exit 1
  fi

  echo "               Releasing collector locks..."
  "$PY" -c "
import os, psycopg2
c = psycopg2.connect(host=os.getenv('DB_HOST','192.168.30.10'), port=5432,
                     user='postgres', password='tbdi@123', dbname='energydb',
                     connect_timeout=5,
                     options='-c lock_timeout=3000 -c statement_timeout=3000')
cur = c.cursor(); cur.execute('DELETE FROM mes_collector_locks'); c.commit(); c.close()
" 2>/dev/null
  echo "               Done."
  echo

  # --- PHASE 3: PLC TCP-slot release window ---------------------
  # Mitsubishi Q-series CPUs hold MC-protocol slots for a few seconds
  # after the socket closes.  Same 5s pause as the .bat.
  echo " [PHASE 3/4]  Waiting 5s for PLC TCP slots to release..."
  sleep 5
  echo "               Done."
  echo
else
  echo " [PHASE 2/4]  SKIPPED — collectors not requested (--with-collectors)"
  echo " [PHASE 3/4]  SKIPPED — no PLC sockets to release"
  echo
fi

# --- PHASE 4: launch services -----------------------------------
echo " [PHASE 4/4]  Launching services..."
echo

launch() {  # launch <name> <workdir> <cmd...>
  local name="$1" wd="$2"; shift 2
  echo "   -> $name"
  # setsid makes the child a process-group leader so stop_everything.sh
  # can kill -TERM -PID and take its whole tree down.  The PID is
  # captured OUTSIDE a subshell — the earlier "( cd && nohup ... & )"
  # form recorded the subshell's PID, not the service's, which made
  # every pidfile point at a process that was already gone.
  ( cd "$wd" && exec env PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
      setsid "$@" >>"$LOG_DIR/${name}.log" 2>&1 ) &
  echo $! >"$LOG_DIR/${name}.pid"
  sleep 1
}

# Poll a port until something listens, or time out.
wait_port() {  # wait_port <port> <seconds>
  local port="$1" secs="$2" i=0
  while (( i < secs )); do
    ss -lnt 2>/dev/null | grep -q ":${port} " && return 0
    sleep 1; ((i++))
  done
  return 1
}

# Report a service as UP only if its port is actually listening.
# Anything else prints the tail of its log, because "launched" and
# "running" are not the same thing.
check() {  # check <name> <port>
  local name="$1" port="$2"
  if wait_port "$port" 12; then
    echo "   [ UP ]   $name  (:$port)"
    return 0
  fi
  echo "   [DOWN]   $name  (:$port)  — last lines of ${name}.log:"
  tail -6 "$LOG_DIR/${name}.log" 2>/dev/null | sed 's/^/            /'
  FAILED=$((FAILED + 1))
  return 1
}
FAILED=0

echo "  [1/4] MES-API ..."
# WELD_TEST_GEN defaults to "1" in routers/weld.py — that worker INSERTs
# synthetic robot-weld rows into mes_weld_log every 3s (and prunes old
# ones).  Harmless on a dev DB, NOT harmless against production energydb.
# Pin it off here; load_dotenv() does not override an existing env var,
# so this wins over .env.  Set WELD_TEST_GEN=1 explicitly if you ever
# genuinely want the synthetic feed.
export WELD_TEST_GEN="${WELD_TEST_GEN:-0}"
echo "        WELD_TEST_GEN=$WELD_TEST_GEN (synthetic weld feed)"
# 2026-09-17 — MES-API runs on several uvicorn workers.  A single worker is
# GIL-capped near one core: with 30 supervisors on the page even /api/auth/me
# took seconds while 31 of the 64 cores sat idle.  4 workers took video-open
# p50 from 3115 ms to 72 ms and "under 2 s" from 19% to 99%.
# This is safe ONLY because Phase2/bg_leader.py elects one process to run the
# background work (clip archiver, alarm sweeps, PM + breakdown mail, pollers) —
# otherwise every worker would run all of it.  DB_POOL_MAX is per process, so
# it is divided across the workers to stay under postgres max_connections.
# restart_api.py carries the SAME two settings; they must move together.
export MES_API_WORKERS="${MES_API_WORKERS:-4}"
export DB_POOL_MAX="${DB_POOL_MAX:-40}"
echo "        MES_API_WORKERS=$MES_API_WORKERS  DB_POOL_MAX=$DB_POOL_MAX (per worker)"
# 2026-09-20 — BOOT PARITY.  After a power cut the box must come back in the
# SAME state it was running in, so the settings the restart scripts pin are
# pinned here too.  Anything already in the environment still wins, so a
# deliberate one-off (VIDEO_LIVE_ENCODER=libx264 ./start_everything.sh) works.
#   VIDEO_LIVE_ENCODER — without it the CMS/API fall back to a probe for NVENC.
#     If that probe misses, every camera recorder and every clip encodes on the
#     CPU (5.3 cores per clip vs 0.7) and the whole box crawls.  Pinned to GPU,
#     exactly as Phase2/restart_cms.py does.  Safety valve: set it to libx264.
#   CLIP_PREWARM — the code default is OFF since 2026-09-19; pinned so a future
#     default flip cannot bring prewarm back on its own after a reboot.
export VIDEO_LIVE_ENCODER="${VIDEO_LIVE_ENCODER:-h264_nvenc}"
export CLIP_PREWARM="${CLIP_PREWARM:-0}"
echo "        VIDEO_LIVE_ENCODER=$VIDEO_LIVE_ENCODER  CLIP_PREWARM=$CLIP_PREWARM"
launch MES-API "$MES_DIR" "$PY" -u -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers "$MES_API_WORKERS"

echo "  [2/4] MES-Frontend (static dist + API proxy on :5656) ..."
# The .bat used Caddy to serve mes-frontend\dist AND reverse-proxy /api
# to the backend.  A plain http.server can't proxy, so login POSTs to
# /api/auth/login returned 501.  serve_prod.py replaces it: serves dist
# and proxies /api -> :8080, /cms-api -> :5555 (mirrors vite.config.js).
if ss -lnt 2>/dev/null | grep -q ':5656 '; then
  echo "        already listening on 5656 — left alone (idempotent)"
elif [[ -f "$MES_FE/serve_prod.py" && -d "$MES_FE/dist" ]]; then
  launch MES-Frontend "$MES_FE" "$PY" serve_prod.py 5656 "$MES_FE/dist"
  # 2026-08-11 — tbdi.in landing page (links out to MES / EMS / Die Health /
  # Maintenance / Attendance / Robot).  Reuses serve_prod.py, just a different
  # docroot and port; the cloudflared tunnel maps tbdi.in + www.tbdi.in here.
  if [[ -d "$ROOT/landing" ]]; then
    launch Landing "$MES_FE" "$PY" serve_prod.py 5700 "$ROOT/landing"
  fi
else
  echo "        [WARN] $MES_FE/serve_prod.py or dist missing — skipped"
fi

echo "  [3/4] CMS-API ..."
if [[ ! -x "$PY_CMS" ]]; then
  echo "        [WARN] CMS venv missing at $CMS_DIR/backend/.venv-linux — skipped"
  echo "               build it:  python3.12 -m venv <that path> && pip install -r backend/requirements.txt"
elif [[ -f "$CMS_DIR/backend/api_server.py" ]]; then
  # 2026-08-10 — this box has 64 cores; the clip endpoint defaulted to only
  # 4 concurrent renders, so several operators clicking video at once
  # queued behind each other (measured 18-21 s waits).  16 keeps plenty of
  # cores free for the ~68 camera recorders, which matter more.
  # 2026-09-20 — 24, the same value Phase2/restart_cms.py pins, so a reboot
  # lands on the state the box actually runs in.  It was 16 here while the
  # renderer was on the CPU; on NVENC a wider gate keeps clips inside the 15 s
  # upstream timeout (no 502 retry storm).  Override: CLIP_RENDER_PARALLEL=N.
  # 2026-09-24 — TS_ROTATE_QUIET_S=90 (code default 20).  At every shift start
  # the CMS rotates each camera's TS file: kill the recorder, hold the respawn
  # this long, next camera 10 s later.  These cameras allow ONE RTSP session,
  # and 20 s was not enough to release it: the 08:30 rotation on 24-Sep put 137
  # of 141 cameras into "hung" (ping OK, no video) and 72 were still dead two
  # hours later.  A 90 s hold is what the by-hand recovery uses
  # (CMS_QUIET_SECONDS=90 restart_cms.py), which brought 31 -> 73 cameras back.
  # 2026-09-24 — the shift rotation is what hangs these single-session cameras
  # (the 08:30 rotation put 137 into "hung", the 18:30 one another 153, each by
  # killing every recorder and reconnecting it).  TS_SEGMENT_MIN=N makes ffmpeg
  # roll the .ts itself every N minutes with the RTSP session kept open, so the
  # boundary stops killing anything; 0 = old behaviour.  Until that is switched
  # on, TS_ROTATE_MIN_AGE_S stops the rotation from undoing cameras that only
  # just came back (e.g. right after a power cut).
  CLIP_RENDER_PARALLEL="${CLIP_RENDER_PARALLEL:-24}" \
  TS_ROTATE_QUIET_S="${TS_ROTATE_QUIET_S:-90}" \
  TS_SEGMENT_MIN="${TS_SEGMENT_MIN:-0}" \
  TS_ROTATE_MIN_AGE_S="${TS_ROTATE_MIN_AGE_S:-1800}" \
  VIDEO_ALLOW_UDP="${VIDEO_ALLOW_UDP:-0}" \
  launch CMS-API "$CMS_DIR/backend" "$PY_CMS" api_server.py
else
  echo "        [WARN] $CMS_DIR/backend/api_server.py not found — skipped"
fi

echo "  [4/4] CMS-Frontend ..."
# Vite dev server — needs node/npm, which are not installed here.
if command -v npm >/dev/null 2>&1; then
  launch CMS-Frontend "$CMS_DIR/frontend" npm run dev -- --port 5575 --host
else
  echo "        [WARN] npm not installed — skipped"
fi

# --- Collectors + watchdog (only past the gate) -----------------
if [[ $WITH_COLLECTORS -eq 1 ]]; then
  echo
  echo "  [+]   MES-Collectors (all provisioned lines) ..."
  rm -f "$MES_DIR/collectors/STOP.flag"
  shopt -s nullglob
  for f in "$MES_DIR/collectors"/collector_*.py; do
    cname="$(basename "$f" .py)"
    echo "        -> $cname"
    # never-die loop, mirroring _run_one_collector.bat
    ( cd "$MES_DIR/collectors" && nohup bash -c '
        while true; do
          [[ -f STOP.flag ]] && { echo "STOP.flag present — not restarting"; break; }
          "$1" _clear_stale_lock.py "$2"
          PYTHONUNBUFFERED=1 "$1" -u "$2"
          echo "=== $2 exited rc=$? — restarting in 5s ==="
          sleep 5
        done' _ "$PY" "$(basename "$f")" \
        >>"$LOG_DIR/collector_${cname}.log" 2>&1 &
      echo $! >"$LOG_DIR/collector_${cname}.pid" )
    sleep 1
  done
  shopt -u nullglob

  echo "  [+]   MES-Collector-Watchdog ..."
  launch MES-Collector-Watchdog "$MES_DIR" "$PY" _collector_watchdog.py --loop 60 --email
fi

# --- Health check -----------------------------------------------
# The .bat just printed "SYSTEM IS UP" after spawning windows, which
# says nothing about whether anything survived import.  Verify.
echo
echo " ---------------------------------------------------------------"
echo "   Health check"
echo " ---------------------------------------------------------------"
check MES-API      8080
check MES-Frontend 5656
[[ -x "$PY_CMS" ]]             && check CMS-API      5555
command -v npm >/dev/null 2>&1 && check CMS-Frontend 5575

# --- Cloudflare tunnel (public internet access) -----------------
# 2026-07-28 — start the outbound quick tunnel so the dashboard is reachable
# over the internet (ports the old Windows Caddy+cloudflared setup).  It's a
# CLIENT of :5656 — binds NO port, never touches the LAN NIC — so it's safe and
# leaves LAN access intact.  --protocol http2 because QUIC/UDP is blocked on this
# network.  Idempotent: skipped if already running.  URL changes each start and
# is printed in the summary (also in $CF_LOG).  Stop via stop_everything.sh.
CF_BIN="/home/server/cloudflared"
CF_LOG="/home/server/cf-tunnel.log"
CF_URL=""
if [[ $TUNNEL -eq 1 && -x "$CF_BIN" ]]; then
  if pgrep -x cloudflared >/dev/null 2>&1; then
    echo "   Cloudflare tunnel already running — left alone"
    CF_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)"
  else
    : > "$CF_LOG"
    setsid "$CF_BIN" tunnel --url http://localhost:5656 --no-autoupdate \
      --protocol http2 >>"$CF_LOG" 2>&1 &
    echo "   Cloudflare tunnel starting ..."
    for _i in $(seq 1 15); do
      CF_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)"
      [[ -n "$CF_URL" ]] && break
      sleep 2
    done
  fi
fi

LAN_IP="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
: "${LAN_IP:=127.0.0.1}"

echo
if [[ $FAILED -eq 0 ]]; then
  echo " ==============================================================="
  echo "   SYSTEM IS UP"
  echo " ==============================================================="
else
  echo " ==============================================================="
  echo "   PARTIALLY UP — $FAILED service(s) failed to bind"
  echo "   Full logs in: $LOG_DIR"
  echo " ==============================================================="
fi
echo "   MES dashboard  http://$LAN_IP:5656     ( admin / admin123 )"
echo "   MES API        http://$LAN_IP:8080"
echo "   CMS portal     http://$LAN_IP:5575     ( admin / TbAdmin@2024! )"
[[ -n "$CF_URL" ]] && echo "   PUBLIC (tunnel) $CF_URL"
echo " ==============================================================="
echo "   Stop:  ./stop_everything.sh"
echo
exit $(( FAILED > 0 ? 1 : 0 ))
