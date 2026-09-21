#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────
# restart_cms.py — restart ONLY the CMS-API (:5555).
#
# The CMS-API is the parent of every camera-capture + clip-encoder ffmpeg.
# When the clip queue backs up (e.g. after repeated MES-API restarts wedge
# the EDGE-WEBHOOK queue), clips spill from the GPU (NVENC) lane to libx264
# (CPU) and pile up — 15-20 encoders at ~5 cores each peg the box and the
# whole system (dashboards, API) goes slow. Restarting the CMS-API kills the
# stuck encoders + its children and relaunches the pipeline clean (cameras
# come back, clips go back to NVENC).
#
#   Run:   python3 Phase2/restart_cms.py
# ───────────────────────────────────────────────────────────────────────
import os, re, time, signal, subprocess

PORT    = 5555
BACKEND = ("/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/"
           "D DRIVE/EOL/EOL/New folder (2)/New folder (2)/backend")
VENV_PY = os.path.join(BACKEND, ".venv-linux", "bin", "python")
SCRIPT  = "api_server.py"
LOG     = ("/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/"
           "D DRIVE/EOL/EOL/Deep (2)/Deep/logs/CMS-API.log")


def pid_on_port(port):
    try:
        out = subprocess.check_output(["ss", "-ltnp"], text=True)
        for line in out.splitlines():
            if f":{port} " in line and "pid=" in line:
                m = re.search(r"pid=(\d+)", line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None


def children_of(pid):
    try:
        return [int(x) for x in subprocess.check_output(
            ["pgrep", "-P", str(pid)], text=True).split()]
    except Exception:
        return []


pid = pid_on_port(PORT)
env = dict(os.environ)
if pid:
    print(f"Found CMS-API pid {pid} on :{PORT}")
    # carry the running process's env (DB creds, RTSP settings, etc.)
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for kv in f.read().split(b"\0"):
                if b"=" in kv:
                    k, v = kv.split(b"=", 1)
                    env[k.decode(errors="ignore")] = v.decode(errors="ignore")
        print("  carried env from running process")
    except Exception as e:
        print(f"  env carry skipped: {e}")

    kids = children_of(pid)
    print(f"  killing {len(kids)} child ffmpeg (cameras + clip encoders)…")
    for c in kids:
        try: os.kill(c, signal.SIGKILL)
        except Exception: pass
    try: os.kill(pid, signal.SIGTERM)
    except Exception: pass
    for _ in range(12):
        time.sleep(1)
        if pid_on_port(PORT) is None:
            break
    if pid_on_port(PORT) is not None:
        try: os.kill(pid, signal.SIGKILL); print("  SIGKILL CMS-API")
        except Exception: pass
    # sweep any leftover ffmpeg it orphaned
    time.sleep(2)
    for c in children_of(pid):
        try: os.kill(c, signal.SIGKILL)
        except Exception: pass
else:
    print(f"No CMS-API on :{PORT} — launching fresh")

# Sweep ORPHANED libx264 clip encoders from a prior runaway. When the CMS was
# killed/restarted earlier, its clip ffmpeg children were reparented to
# `systemd --user` (not killed) and kept burning CPU — a CMS restart alone can't
# reach them because they are no longer its children. Kill any libx264 running
# >120s (a healthy clip finishes in seconds; RTSP camera captures don't use
# libx264, so this only hits stuck clip encoders).
# Kill ALL libx264 clip encoders (not just old ones): the CMS is being
# restarted so every in-flight clip dies anyway, and a clean slate (zero CPU
# encoders) lets the NVENC probe at startup run on a free CPU and PASS —
# otherwise 20+ libx264 contend with the probe, it times out, and the CMS caches
# libx264 for its whole life (the vicious cycle that kept the box pegged).
# RTSP camera captures do NOT use libx264, so this only hits clip encoders.
try:
    _out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True)
    _n = 0
    for _ln in _out.splitlines():
        if "libx264" in _ln and "grep" not in _ln:
            _p = _ln.split(None, 1)
            if _p and _p[0].isdigit():
                try:
                    os.kill(int(_p[0]), signal.SIGKILL); _n += 1
                except Exception:
                    pass
    print(f"  swept {_n} libx264 clip encoder(s) — clean slate for the NVENC probe")
except Exception as _e:
    print(f"  libx264 sweep skipped: {_e}")
time.sleep(2)   # let the CPU settle so the startup NVENC probe passes

# 2026-09-12 — SAFE QUIET WINDOW (opt-in via CMS_QUIET_SECONDS).
# The plant cameras accept only ONE RTSP session on :554.  After we SIGKILL a
# recorder the camera keeps that (now dead) session until its own timeout; an
# immediate relaunch then hits "connection refused" and records NOTHING until it
# gives up and retries — the 70→35 camera storm.  Waiting quiet (no recorder
# running) lets every camera drop its ghost session before we reconnect, so the
# fresh recorders bind first try.  Default 0 keeps the old rapid behaviour;
# a safe restart runs with CMS_QUIET_SECONDS=90.
_quiet = int(os.environ.get("CMS_QUIET_SECONDS", "0") or 0)
if _quiet > 0:
    print(f"  quiet window: waiting {_quiet}s for single-session cameras to "
          f"release their RTSP sessions before reconnecting…", flush=True)
    time.sleep(_quiet)

# 2026-09-12 — clip render parallelism = 8.  History: 16 flooded the box when
# clips AND the ~18 live MAIN transcodes were BOTH on libx264 (load 68); capping
# to 4 helped then.  Now the live recorders run on NVENC (h264_nvenc, GPU) so the
# 64-core CPU sits 40-70% idle, and on-click clips render on libx264 there at
# ~1.5 s each even 6-concurrent (GPU is the contended lane now — 23 recorders keep
# NVENC ~87%).  At 4 slots the panels' ~2 clip-req/s queued behind slow renders =
# 30 s buffering; 8 slots on the free CPU drain the queue (8 × 1/1.5s ≈ 5 req/s
# throughput) without CPU saturation.  Raise further only if clicks still queue.
# 2026-09-16 — raised 8 → 24.  The 8 was tuned when on-click clips re-encoded on
# libx264 (~5 cores each).  They now serve as `-c copy` (recent windows, near-zero
# cost) or NVENC (GPU), and the CPU sits ~78% idle, so 8 was throttling throughput:
# under the plant's clip-request volume the 8-slot gate queued requests past the
# MES-API 15 s upstream timeout → 502 → panels retried → the retry storm piled up
# CMS threads until it wedged.  24 lets the gate drain the queue on the free CPU so
# requests finish well under 15 s, MES-API stops 502-ing, and the retry storm (the
# real thread-pileup driver) never starts.  Override with CLIP_RENDER_PARALLEL=N.
env["CLIP_RENDER_PARALLEL"] = os.environ.get("CLIP_RENDER_PARALLEL") or "24"
print(f"  set CLIP_RENDER_PARALLEL={env['CLIP_RENDER_PARALLEL']} — wider gate so clips finish under the 15s upstream timeout (no 502 retry-storm)")

# 2026-09-16 — DETERMINISTIC GPU FOR THE LIVE RECORDERS.  _pick_live_encoder()
# otherwise PROBES NVENC lazily on the first transcode-needed camera; if the box
# is loaded at that instant (e.g. right after a power-cut reboot, or while clips
# are draining) the probe times out and libx264 gets cached for the WHOLE process
# life — pinning ~19 live transcodes on the CPU (load ~46+) until someone
# restarts again.  That is the recurring "after a restart it goes to CPU and I
# have to fix it every time" problem.  NVENC is proven-good on this A2000 (the
# recorders + clip pipeline run on it), so force it here and skip the flaky probe.
# An invocation-time value still wins, so if the GPU/driver ever breaks you can
# recover with:  VIDEO_LIVE_ENCODER=libx264 python3 Phase2/restart_cms.py
env["VIDEO_LIVE_ENCODER"] = os.environ.get("VIDEO_LIVE_ENCODER") or env.get("VIDEO_LIVE_ENCODER") or "h264_nvenc"
print(f"  set VIDEO_LIVE_ENCODER={env['VIDEO_LIVE_ENCODER']} — recorders forced to GPU across restarts (no flaky probe)")

# relaunch detached
logf = open(LOG, "ab")
p = subprocess.Popen([VENV_PY, SCRIPT], cwd=BACKEND, env=env,
                     stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                     start_new_session=True)
print(f"Relaunched CMS-API pid {p.pid}")
print("Waiting for :5555 to come up…")
for _ in range(30):
    time.sleep(1)
    if pid_on_port(PORT) is not None:
        print("✓ CMS-API up on :5555 — cameras + clip pipeline restarting clean")
        break
else:
    print("⚠ :5555 not up yet — check logs/CMS-API.log")
