"""
clip_archive.py — keep the clips operators actually open, on disk, forever.

WHY
There is no per-cycle video file anywhere.  Each camera writes ONE rolling
MPEG-TS (~1 GB, ~50 min of footage) and every click cuts its window out of that
file — a full transcode (HEVC -> H.264, rescaled, resampled to CFR 25 because
the cameras deliver ~13 fps against a nominal 25).  Measured on this box: a
first click costs 2.7 s, the same clip again costs 0.68 s once CMS has it
cached.  There is no GPU here, so that transcode is CPU and it IS the wait.

So render the ones that get clicked BEFORE anyone clicks, and keep them.

WHICH ONES, AND WHY NOT ALL
Measured over a full day: 175,354 cycles.  All of them would be 312 GB/day
(fine — 21 TB free) but ~13 CPU cores running 24/7 (not fine — 67 camera
recorders already hold this box at load ~35).  The ones operators actually open
are the over-target dots and the Alarms; the ordinary green cycles are never
touched.  That subset is ~34% of cycles: ~79 GB and ~3.7 cores a day, and it is
exactly the set behind the yellow OVER TARGET badge and the Alarm markers.

Everything else is untouched and still renders on demand, exactly as before.

THE 50-MINUTE DEADLINE
The rolling .ts holds only ~50 minutes.  A clip that is not rendered inside that
window can never be rendered — the footage is gone.  So the worker walks the
NEWEST unarchived cycles first and ignores anything already past the window;
falling behind loses clips rather than queueing work that would fail anyway.

HOW IT RENDERS
Through the very same /cycle-video routes the browser uses, so the archived file
is byte-identical to what a click would have produced — including the Alarm
clip's extension through the following cycle.  Rebuilding the window arithmetic
here would risk archiving something subtly different from what gets served.

SERVING
On a hit the route streams the file straight off disk with real HTTP Range
support.  Starlette 0.36's FileResponse does NOT implement Range, and without it
the player cannot seek or scrub, so the byte-range handling below is written out
rather than inherited.
"""

import math
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from database import get_conn, dict_cursor


def _int_env(name: str, default: int, low: int = 0) -> int:
    try:
        return max(low, int(os.environ.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


# Sits on the 21 TB video volume next to the rolling .ts files, NOT in /tmp —
# /tmp here is tmpfs (RAM) and is swept every 10 minutes, which is precisely
# what this module exists to outlive.
ARCHIVE_ROOT = os.environ.get(
    "CLIP_ARCHIVE_ROOT",
    "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips")

ENABLED      = os.environ.get("CLIP_ARCHIVE", "1") == "1"
# 2026-08-12 — sized to KEEP UP, now that the chatter-extraction fix returned
# ~40 cores.  The plant produces ~2 cycles/s; one archive pass costs ~2.8 s of
# wall (render + validate + proxy) but only ~6 core-seconds, so ~6 in flight
# tracks production at roughly 13 cores.  Serialised at 1 it fell permanently
# behind and clips aged out of the .ts window unarchived.
# Sized from measurement, not guesswork: one clip costs ~14 core-seconds and
# ~9.7 s of wall (most of it waiting on CMS, not burning CPU).  63-77 clips/min
# therefore needs ~12 in flight and ~15-22 cores — and this box genuinely has
# ~39 free once you measure real CPU instead of loadavg.
# The fast lane below now handles the clips that matter most, so the bulk
# round no longer needs to run wide — 18 + 8 together pushed the box to load 76.
# 2026-08-12 — raised 8 -> 16 once _drain() removed the lockstep barrier.  With
# the barrier in place extra workers were pointless (they just waited on the
# slowest clip of each chunk); without it a slot is refilled the instant it
# frees, so the width is real.  A clip is latency-bound, not CPU-bound — most of
# its ~6 s is spent seeking a growing 1 GB .ts, not encoding — so 16 in flight
# costs far less than 16 cores.  MIN_IDLE_CORES is still the hard ceiling.
# 2026-08-12 (later) — 16 -> 10 once rendering moved in-process.  Through the
# CMS the number was free: extra workers only queued.  Now every worker really
# does start an ffmpeg (~3.6 cores, ~2.5 s), so 16 would ask for ~57 cores and
# only ~38 are free — it would starve the recorders instead of the CMS queue.
# 10 costs ~36 at peak and still yields ~240 clips/min against ~169 produced,
# so the archive keeps up with headroom.  MIN_IDLE_CORES remains the backstop.
# 2026-09-16 — 10 → 14.  The core math above assumed every render was libx264
# (~3.6 cores each).  Renders now take the GPU lane first (NVENC ≈ 0.7 core, and
# GPU_WAIT_S makes them wait for it rather than spill), and a 20-user emulator
# measured the box at ~78% idle CPU while clips were still being cut on demand
# at click time.  14 buys backfill throughput without crowding the recorders;
# MIN_IDLE_CORES is still the hard backstop.
PARALLEL     = _int_env("CLIP_ARCHIVE_PARALLEL", 8, low=1)
# 2026-08-11 — LOAD CEILING.  The first run went at the backlog with 2 workers
# and pushed this box from ~35 to load 99: each render's libx264 takes ~8 cores,
# and the clip pre-warmer and 67 camera recorders are already competing for the
# same 64.  Speculative work must never crowd out the live system, so a round is
# skipped whenever the 1-minute load is already above this.  Missing a few
# clips is fine — they simply render on demand like before.
# 2026-08-12 — THROTTLE ON REAL CPU, NOT LOADAVG.  The ceiling used to compare
# os.getloadavg() against the core count, but loadavg counts processes stuck in
# uninterruptible I/O as well as ones actually running.  With 73 recorders
# streaming to a spinning disk this box sits at load ~50 while only ~25 cores
# are truly busy — so the worker kept pausing itself with ~39 cores idle and
# coverage stalled around 30%.  Measured directly from /proc/stat instead:
# pause only when genuinely little CPU is left.
# 2026-09-16 — 8 → 5.  Measured during the 20-user emulator: 78% of the 64
# cores idle, GPU ~20%, yet clips were still being cut at click time.  The
# gate was pausing speculative work while the box had ample headroom; 5 keeps
# a real reserve for the recorders without stalling the archive.
MIN_IDLE_CORES = float(os.environ.get("CLIP_ARCHIVE_MIN_IDLE_CORES", "8") or 8)
LOAD_CEILING = float(os.environ.get("CLIP_ARCHIVE_LOAD_CEILING", "0") or 0)  # 0 = off
# Deprecated 2026-08-12: no longer read anywhere.  Pacing between chunks only
# existed to soften the lockstep rounds; _drain() feeds continuously and the
# load ceiling is the throttle, so an artificial sleep would just lose
# throughput.  Kept only so an existing CLIP_ARCHIVE_PACE in the environment
# does not read as a setting that still works.
PACE_S       = 0.0
RETAIN_DAYS  = _int_env("CLIP_ARCHIVE_RETAIN_DAYS", 30, low=1)
# Stay clear of the ~50 min .ts window; past this a render would just 404.
WINDOW_MIN   = _int_env("CLIP_ARCHIVE_WINDOW_MIN", 42, low=5)
BATCH        = _int_env("CLIP_ARCHIVE_BATCH", 120, low=1)
# 2026-08-11 — operator: archive EVERY cycle, not just the over-target/Alarm
# ones, so every dot opens instantly.  Kept as a switch because it is the
# expensive mode: ~175k cycles/day is ~312 GB and ~13 CPU-cores of transcode a
# day, against ~79 GB and ~3.7 cores for the interesting subset.  The load
# ceiling below is what keeps that affordable — it simply archives less on a
# busy box instead of fighting the live system for cores.
# 2026-08-12 — back to the interesting subset by measurement, not preference.
# The plant produces ~205 cycles/min; archiving ALL of them needs ~40 cores
# sustained, which do not exist next to 73 camera recorders — so most cycles
# aged out of the 50-minute .ts window unarchived anyway and the result was
# unpredictable (some dots instant, most not).  Alarm + over-target is ~70/min,
# which this worker CAN keep up with, and it is exactly the set operators open.
# Ordinary green cycles keep rendering on demand, as they always did.
#
# 2026-08-12 (later) — ON again, because that measurement was taken through the
# lockstep barrier now fixed in _drain(): the worker was managing ~51 clips/min
# with 40 of 64 cores idle, so "not enough CPU" was really "not enough clips in
# flight".  Operators open ordinary green dots too, and every one of those was
# a 4-8 s wait.  Degradation stays graceful — _pending() orders NG first, then
# over-target, then ordinary, so if the box ever cannot keep up it is the green
# cycles that go unarchived, exactly as in the subset mode.
ARCHIVE_ALL  = os.environ.get("CLIP_ARCHIVE_ALL", "1") != "0"
# Idle gap between rounds.  Was 20 s, which was pure dead time — a round of 40
# clips takes ~19 s, so the worker was asleep for half its life and throughput
# halved.  The load ceiling is the throttle; this only stops a hot spin when
# there is nothing to archive.
SLEEP_S      = _int_env("CLIP_ARCHIVE_SLEEP", 1, low=1)
# Dedicated fast lane for the newest dot per machine (see _newest_loop).
# 2026-09-16 — 8 → 20 workers, poll every 1 s instead of 2.  THIS is the lane
# that decides what the operator experiences: they almost always click the
# newest dot, and a 20-user emulator showed only 5% of opens finding a
# pre-rendered clip — 77% were still being cut on demand (4-10 s) because the
# newest cycle had not been archived yet when it was clicked.  Widening the
# fast lane and halving its poll gets the clip written within seconds of the
# cycle closing, which is what turns a click into a sub-second file serve.
# 2026-09-17 — 20 → 12.  Sizing the two lanes independently was the mistake:
# NEWEST(20) + PARALLEL(14) = 34 possible concurrent renders against only 20
# GPU slots, so 14 of them spilled to the libx264 CPU lane at ~5 cores each
# and drove this 64-core box to load 70 (measured: 21 libx264 alive, archive
# counters cpu=4681 vs gpu=2596).  NEWEST(12) + PARALLEL(8) = 20 now matches
# GPU_PARALLEL exactly, so renders stay on the cheap GPU lane.
NEWEST_PARALLEL = _int_env("CLIP_ARCHIVE_NEWEST_PARALLEL", 12, low=1)
NEWEST_SLEEP    = _int_env("CLIP_ARCHIVE_NEWEST_SLEEP", 1, low=1)
TIMEOUT      = _int_env("CLIP_ARCHIVE_TIMEOUT", 90, low=10)
SELF_BASE    = os.environ.get("MES_SELF_BASE", "http://127.0.0.1:8080").rstrip("/")

def _idle_cores(sample: float = 0.25) -> float:
    """Cores genuinely idle right now, from /proc/stat.

    loadavg is the wrong signal here (it includes I/O wait); this is the actual
    unused CPU, which is what a transcode needs.
    """
    def snap():
        with open("/proc/stat") as fh:
            p = fh.readline().split()[1:]
        v = [int(x) for x in p[:8]]
        return sum(v), v[3] + v[4]          # total, idle+iowait
    t0, i0 = snap()
    time.sleep(sample)
    t1, i1 = snap()
    dt = max(1, t1 - t0)
    return (i1 - i0) / dt * (os.cpu_count() or 1)


# 2026-09-22 — AVERAGED idle.  The feeder used a 0.1 s /proc/stat sample, and
# one libx264 clip bursts across many cores for a moment, so the sample kept
# dipping under the ceiling and every round stopped after its first PARALLEL
# clips (measured: rounds of rows=118 ok=8) while the box averaged ~47 idle
# cores.  A background sampler keeps a ~2 s average instead; processes that do
# not run the sampler (request workers) fall back to the direct probe.
_IDLE = {"v": None, "t": 0.0}


def _idle_sampler() -> None:
    hist = []
    ncpu = os.cpu_count() or 1
    while True:
        try:
            with open("/proc/stat") as fh:
                p = fh.readline().split()[1:]
            v = [int(x) for x in p[:8]]
            now = time.time()
            hist.append((now, sum(v), v[3] + v[4]))
            while len(hist) > 2 and now - hist[1][0] >= 2.0:
                hist.pop(0)
            t0, tot0, idl0 = hist[0]
            if now - t0 >= 1.0:
                _IDLE["v"] = (v[3] + v[4] - idl0) / max(1, sum(v) - tot0) * ncpu
                _IDLE["t"] = now
        except Exception:
            pass
        time.sleep(0.25)


def _idle_avg() -> float:
    if _IDLE["v"] is not None and time.time() - _IDLE["t"] < 5.0:
        return _IDLE["v"]
    return _idle_cores()


_MIN_BYTES = 40 * 1024          # smaller than this is a frameless stub, not a clip

# ── DIRECT RENDER — cut the clip here, not through the CMS ────────────────
# 2026-08-12 — WHY THIS EXISTS, measured rather than guessed.
#
# Every archive job used to be an HTTP GET to this app's own clip endpoint,
# which proxied to the CMS, which ran ffmpeg.  The CMS caps concurrent renders
# at CLIP_RENDER_PARALLEL (16 here) — so 16 archive workers occupied ALL of it,
# and an operator's click had to queue behind them.  Measured during B shift:
# a direct CMS call for a settled 3-15 min old cycle took 33-44 s, and even
# clips already ON DISK came back in 22-33 s because the request still had to
# traverse the same saturated path.  The box meanwhile sat with 38 of 64 cores
# idle, the disk at 5% and ffprobe at 0.14 s: nothing was actually busy.  The
# archive was not competing for CPU, it was competing for the CMS's queue slots
# — with the very operators it exists to serve.
#
# So the bulk archiver stops asking anyone.  It reads the same .ts the CMS
# would have read and runs the same ffmpeg, in this process, against the idle
# cores.  The CMS's 16 slots then belong entirely to interactive clicks.
#
# The window maths below is the CMS's, deliberately copied rather than
# approximated (api_server.py, "CONTENT-ANCHORED .ts TIMELINE"): a recording's
# filename holds the time the RECORDER started, not the time of its first
# frame, and a struggling camera drifts 37-46 s behind wall-clock.  Anchoring
# at the end — content_start = mtime - probed_duration — is exact at the tail,
# which is where live cycles live.  Get this wrong and clips are silently
# offset, so it must stay in step with the CMS.
VIDEO_ROOT = os.environ.get(
    "CLIP_ARCHIVE_VIDEO_ROOT",
    "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/videos")
FFMPEG_BIN  = os.environ.get("CLIP_ARCHIVE_FFMPEG",  "/usr/bin/ffmpeg")
FFPROBE_BIN = os.environ.get("CLIP_ARCHIVE_FFPROBE", "/usr/bin/ffprobe")
DIRECT_RENDER = os.environ.get("CLIP_ARCHIVE_DIRECT", "1") != "0"
# Same clamp the clip endpoint applies, so a direct render and an on-demand one
# produce the same clip for a very long cycle instead of two different lengths.
_CLIP_MAX_SECONDS = float(os.environ.get("CLIP_MAX_SECONDS", "120") or 120)
# Main-line clips still go through the app/CMS (their window is derived there),
# so they keep a SMALL allowance — enough to make progress, never enough to
# refill the queue that operators are waiting in.
HTTP_PARALLEL = _int_env("CLIP_ARCHIVE_HTTP_PARALLEL", 3, low=1)
_HTTP_SEM = threading.Semaphore(HTTP_PARALLEL)
# NVENC slots.  4 was sized for CLIP LATENCY: the A2000 has ONE encode engine
# and saturates almost at once (measured: 4 streams 36x realtime, 16 streams
# 41x), so extra lanes only make each individual clip slower.
# 2026-08-14 — raised to 12, sized for CPU COST instead, because that is the
# constraint that actually bites here.  Measured on live footage with 77
# recorders up: one libx264 clip costs 5.3 cores (peak 7), the SAME clip on
# NVENC costs 0.7 — ~7.5x.  At 4 slots the lane sat permanently pegged (4 on
# the GPU, every other render spilling to libx264 at ~5 cores each) while the
# encode engine still had headroom.  12 trades a slower individual clip for
# cores the 77 camera recorders need more.  Set to 0 to disable the lane.
# 2026-09-16 — 12 → 20.  With the fast lane widened to 20 there must be enough
# GPU slots for those renders to actually land on NVENC; otherwise they wait
# GPU_WAIT_S and then spill to the 5-core CPU lane, which is what used to peg
# the box.  The A2000 sat ~20% busy with 17 encoder sessions during the test,
# so it has room for these.
GPU_PARALLEL = _int_env("CLIP_ARCHIVE_GPU_PARALLEL", 20, low=0)
_GPU_SEM = threading.Semaphore(GPU_PARALLEL) if GPU_PARALLEL > 0 else None
# How long a render waits for a GPU slot before giving up and burning cores.
# This is what produced "CPU pegged while the GPU sits idle": the acquire below
# used to be non-blocking, so a job that arrived a moment before a slot freed
# went straight to a 5-core libx264 encode and held those cores for seconds,
# while the 0.7-core NVENC slot it just missed went unused.  A short wait costs
# at most this many seconds, and only when every slot is genuinely busy.
# 0 restores the old instant-fallback behaviour.  MIN_IDLE_CORES stays the hard
# backstop — this only decides WHICH lane a render uses, never whether it runs.
# 2026-09-16 — default 2.0 → 5.0.  Post-wedge/backlog bursts briefly queue more
# than GPU_PARALLEL renders at once; at 2 s a queued render gave up and spilled to
# the 5-core libx264 lane (measured libx264 up to ~11 → load ~48) even though the
# A2000 sat ~20 % idle.  5 s lets a render wait out the burst for the cheap GPU
# lane instead of pegging the CPU; archive renders are background (not the
# latency-critical click), so the extra wait is free.  Raised in code (not just
# env) so it PERSISTS across restarts — the env resets to the default every boot.
GPU_WAIT_S = float(os.environ.get("CLIP_ARCHIVE_GPU_WAIT_S", "5.0") or 5.0)

# 2026-09-22 — CONTROLLED CPU LANE (operator-approved).  Measured: the A2000's
# single encode engine is 99-100 % busy, mostly with CMS work (15 live
# transcodes + per-cycle Final Inspection extracts), so an archive clip on NVENC
# took 6.6 s against 1.0 s on libx264 while ~47 cores sat idle.  The CPU lane is
# now used FIRST while at least CPU_MIN_IDLE cores are idle (averaged), with a
# hard cap of CPU_PARALLEL concurrent libx264 renders of CPU_THREADS encoder
# threads each; otherwise the GPU lane as before.  A render that finds neither
# lane free waits for a CPU slot — it no longer spills to an unlimited number of
# libx264 processes, which is what pegged the box in August/September.
CPU_PARALLEL = _int_env("CLIP_ARCHIVE_CPU_PARALLEL", 6, low=0)
CPU_THREADS  = _int_env("CLIP_ARCHIVE_CPU_THREADS", 4, low=1)
CPU_MIN_IDLE = float(os.environ.get("CLIP_ARCHIVE_CPU_MIN_IDLE", "16") or 16)
_CPU_SEM = threading.Semaphore(CPU_PARALLEL) if CPU_PARALLEL > 0 else None

_TS_META: dict = {}                 # path -> (size, mtime, duration, probed_at)
_TS_META_LOCK = threading.Lock()
_TS_META_TTL = 20.0                 # a growing file is re-probed at most this often


def _ts_window(path: str):
    """(content_start, content_end, duration) for a .ts, or None.

    Cached: a rotated file never changes (size+mtime match => reuse forever);
    a growing one is re-probed at most every _TS_META_TTL seconds.
    """
    try:
        st = os.stat(path)
    except Exception:
        return None
    now = time.time()
    with _TS_META_LOCK:
        c = _TS_META.get(path)
    if c:
        c_size, c_mtime, c_dur, c_at = c
        settled = (c_size == st.st_size and abs(c_mtime - st.st_mtime) < 0.001)
        if settled or (now - c_at) < _TS_META_TTL:
            return (datetime.fromtimestamp(c_mtime - c_dur),
                    datetime.fromtimestamp(st.st_mtime), c_dur)
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        dur = float((r.stdout or b"").decode("utf-8", "ignore").strip() or 0)
    except Exception:
        return None
    if dur <= 0:
        return None
    with _TS_META_LOCK:
        _TS_META[path] = (st.st_size, st.st_mtime, dur, now)
        if len(_TS_META) > 4096:
            _TS_META.clear()
    return (datetime.fromtimestamp(st.st_mtime - dur),
            datetime.fromtimestamp(st.st_mtime), dur)


def _naive(dt):
    """Drop the tz, keeping the wall-clock reading.

    The cycle log hands back aware timestamps while file mtimes are naive local
    time.  The CMS does exactly this (`ts_start.replace(tzinfo=None)`) on the
    same values, so matching it keeps both paths on one clock — mixing them
    would offset every clip by the UTC difference.
    """
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def _pick_ts(camera_id: str, ts_start: datetime, ts_end: datetime):
    """The rotated .ts that overlaps this cycle most, and its content start.

    Overlap-scored rather than "the newest file": a cycle from earlier in the
    shift lives in a file the recorder has since rotated away from.
    """
    best, best_overlap = None, 0.0
    try:
        names = os.listdir(VIDEO_ROOT)
    except Exception:
        return None
    prefix = f"cam_{camera_id}_"
    for name in names:
        if not (name.startswith(prefix) and name.endswith(".ts")):
            continue
        p = os.path.join(VIDEO_ROOT, name)
        w = _ts_window(p)
        if not w:
            continue
        cs, ce, _dur = w
        overlap = (min(ts_end, ce) - max(ts_start, cs)).total_seconds()
        if overlap > best_overlap:
            best_overlap, best = overlap, (p, cs)
    return best


def _render_direct(camera_id: str, ts_start: datetime, ts_end: datetime,
                   dest: str) -> bool:
    """Cut one clip straight out of the .ts.  True only on a real, sized file."""
    if not (camera_id and ts_start and ts_end):
        return False
    ts_start, ts_end = _naive(ts_start), _naive(ts_end)
    pick = _pick_ts(camera_id, ts_start, ts_end)
    if not pick:
        return False
    ts_file, content_start = pick

    off = (ts_start - content_start).total_seconds()
    dur = (ts_end - ts_start).total_seconds()
    if off < 0:                       # cycle began before this file did
        dur += off
        off = 0.0
    if dur <= 0:
        return False
    if _CLIP_MAX_SECONDS > 0 and dur > _CLIP_MAX_SECONDS:
        off += dur - _CLIP_MAX_SECONDS      # keep the END of a long cycle
        dur = float(_CLIP_MAX_SECONDS)
    trim = max(1.0, float(math.ceil(dur)))
    # Input seek lands on a keyframe BEFORE the cycle; the output seek trims
    # back to the exact start.  1.5 s of slack is the CMS's value.
    in_ss  = max(0.0, off - 1.5)
    out_ss = max(0.0, off - in_ss)

    tmp = f"{dest}.d{os.getpid()}_{threading.get_ident()}"

    # The GPU is a SECOND lane, not a replacement.  Measured on this box: at
    # 8-way concurrency the CPU does 0.19 s/clip against NVENC's 0.63 s, because
    # 64 cores beat one encode engine — so swapping CPU for GPU made clips
    # slower and was reverted.  But the two run on different silicon, and NVENC
    # sustains ~41x realtime that the cores are not using.  A few slots here add
    # that capacity on top of the CPU lane instead of competing with it.
    #
    # 2026-08-14 — PREFER the GPU lane rather than merely trying it.  This used
    # to be acquire(blocking=False), which is why the box could sit with libx264
    # eating 5 cores a clip while NVENC idled: slots free constantly, and a
    # non-blocking miss threw the job onto the expensive lane for the next
    # several seconds regardless.  Waiting GPU_WAIT_S first costs at most that
    # long and only when all GPU_PARALLEL slots are truly busy; the CPU lane is
    # still there as the fallback, so nothing stalls, it just stops paying 7.5x
    # for a clip when the cheap lane was about to open.
    cpu_slot = bool(_CPU_SEM and _idle_avg() >= CPU_MIN_IDLE
                    and _CPU_SEM.acquire(blocking=False))
    on_gpu = (not cpu_slot) and GPU_PARALLEL > 0 and (
        _GPU_SEM.acquire(timeout=GPU_WAIT_S) if GPU_WAIT_S > 0
        else _GPU_SEM.acquire(blocking=False))
    if not on_gpu and not cpu_slot and _CPU_SEM:
        _CPU_SEM.acquire()             # wait for a capped CPU slot, never spill
        cpu_slot = True
    if on_gpu:
        cmd = [
            FFMPEG_BIN, "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-ss", f"{in_ss:.3f}", "-i", ts_file,
            "-ss", f"{out_ss:.3f}", "-t", f"{trim:.3f}",
            "-vf", "scale_cuda=w='min(iw,854)':h=-2",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26",
            "-maxrate", "900k", "-bufsize", "1800k", "-an",
            "-vsync", "cfr", "-r", "25",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", "-f", "mp4", tmp,
        ]
    else:
        # nice 10: a background archive clip always yields the CPU to the camera
        # recorders, the collectors and the API.
        cmd = [
            "nice", "-n", "10", FFMPEG_BIN, "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-ec", "favor_inter",
            "-threads", "2",
            "-ss", f"{in_ss:.3f}", "-i", ts_file,
            "-ss", f"{out_ss:.3f}", "-t", f"{trim:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-threads", str(CPU_THREADS),
            "-vf", "scale='min(iw,854)':'-2':flags=bicubic,format=yuv420p",
            "-color_range", "tv", "-level", "4.0",
            "-maxrate", "900k", "-bufsize", "1800k", "-an",
            "-vsync", "cfr", "-r", "25",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", "-f", "mp4", tmp,
        ]
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=TIMEOUT)
        if os.path.getsize(tmp) < _MIN_BYTES:
            os.remove(tmp)
            # A GPU miss is not a verdict on the footage — the CPU lane may well
            # manage it — so say nothing here and let the next round retry.
            return False
        os.replace(tmp, dest)         # atomic: a reader never sees a half file
        _stats["gpu" if on_gpu else "cpu"] = _stats.get("gpu" if on_gpu else "cpu", 0) + 1
        return True
    except Exception as exc:
        _stats["last_error"] = f"direct: {type(exc).__name__}: {exc}"
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False
    finally:
        if on_gpu:
            _GPU_SEM.release()
        if cpu_slot:
            _CPU_SEM.release()
_started   = False
_start_lock = threading.Lock()
_stats = {"archived": 0, "failed": 0, "skipped": 0, "last_run": None, "last_error": ""}


# ── paths ────────────────────────────────────────────────────────────────
def _safe(part) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))


def _shift_tok(shift) -> str:
    """Filename-safe shift token.  None/'' -> 'NA' so a row that carries no
    shift still gets a stable, collision-free name."""
    s = ("" if shift is None else str(shift)).strip()
    return _safe(s) if s else "NA"


def clip_path(kind: str, owner_id, record_date, cycle_seq, ng: bool,
              shift=None, line_id=None) -> str:
    """Deterministic location for one cycle's clip.

    Layout (operator asked for a browseable tree, 2026-08-13):

        {ARCHIVE_ROOT}/{date}/line_{line}/{shift}/{machine}/cycle_{seq}[_ng].mp4

    date first, then LINE, then SHIFT, then MACHINE, then the clip — so the
    folder is navigable instead of one flat date directory with every line,
    shift and machine mixed together.  `date` stays the TOP level so the
    retention sweep is still a single directory removal.

      * line    — the production line; a "line" clip IS its own line, a "sub"
                  clip sits under its parent line (mes_plc_configs.line_id).
      * shift   — cycle_seq RESTARTS at 1 every shift while the date is shared,
                  so the shift MUST be in the path or A-shift and B-shift clips
                  collide (the "B shift shows A shift's video" bug).
      * machine — "main" for the line's own Final-Inspection clip, "sub_<id>"
                  for each sub-machine.
      * ng is in the leaf because an Alarm and an OK row can share a cycle_seq
        and their clips differ (the Alarm one runs on through the next cycle).
    """
    d = record_date.isoformat() if hasattr(record_date, "isoformat") else str(record_date)
    ln = line_id if line_id is not None else (owner_id if kind == "line" else "unknown")
    line_folder    = f"line_{_safe(ln)}"
    shift_folder   = _shift_tok(shift)
    machine_folder = "main" if kind == "line" else f"sub_{_safe(owner_id)}"
    leaf = f"cycle_{_safe(cycle_seq)}{'_ng' if ng else ''}.mp4"
    return os.path.join(ARCHIVE_ROOT, _safe(d),
                        line_folder, shift_folder, machine_folder, leaf)


def _archived_path(kind: str, owner_id, record_date, cycle_seq, ng: bool,
                   shift, line_id=None) -> Optional[str]:
    """On-disk clip for this EXACT (…, shift, line), or None.  Pure filesystem —
    the archiver uses it for dedup because it already knows each row's shift and
    parent line."""
    try:
        p = clip_path(kind, owner_id, record_date, cycle_seq, ng, shift, line_id)
        if os.path.exists(p) and os.path.getsize(p) >= _MIN_BYTES:
            return p
    except Exception:
        pass
    return None


def _resolve_ctx(kind: str, owner_id, record_date, cycle_seq, ng: bool):
    """The (shift, line_id) that (owner, date, cycle_seq, ng) refers to.

    cycle_seq is unique only WITHIN a shift, so this picks the same row the
    serving route would — the newest one for this cycle — and returns its
    shift plus parent line.  Because the archived clip is keyed by that shift
    (and foldered under that line), a hit always matches what an on-demand
    render of the very same click would produce.

    Returns (shift_name_or_'', line_id_or_None), or None when there is no such
    row — the caller then reports a miss and renders on demand.
    """
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            if kind == "line":
                cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s",
                            (owner_id,))
                t = cur.fetchone()
                if not t or not t.get("db_table_name"):
                    return None
                tbl = f'{t["db_table_name"]}_ct_log'
                cur.execute(
                    f"SELECT shift_name FROM {tbl} "
                    f"WHERE record_date = %s AND cycle_seq = %s "
                    f"  AND COALESCE(is_ng, FALSE) = %s "
                    f"ORDER BY ts DESC LIMIT 1",
                    (record_date, cycle_seq, bool(ng)))
                r = cur.fetchone()
                if not r:
                    return None
                return (r.get("shift_name") or "", owner_id)  # a line IS its own line
            cur.execute(
                "SELECT l.shift_name, p.line_id "
                "FROM mes_submachine_ct_log l "
                "JOIN mes_plc_configs p ON p.id = l.sub_plc_id "
                "WHERE l.sub_plc_id = %s AND l.record_date = %s "
                "  AND l.cycle_seq = %s AND COALESCE(l.is_ng, FALSE) = %s "
                "ORDER BY l.ts_end DESC LIMIT 1",
                (owner_id, record_date, cycle_seq, bool(ng)))
            r = cur.fetchone()
            if not r:
                return None
            return (r.get("shift_name") or "", r.get("line_id"))
    except Exception:
        return None


def find(kind: str, owner_id, record_date, cycle_seq, ng: bool) -> Optional[str]:
    """Archived clip for this cycle, or None.  Never raises.

    Signature is unchanged so the serving routes keep calling it exactly as
    before; the shift and parent line are resolved here (see _resolve_ctx)
    instead of being asked of the caller, so nothing outside this module
    needs to change.
    """
    if not ENABLED:
        return None
    try:
        ctx = _resolve_ctx(kind, owner_id, record_date, cycle_seq, ng)
        if ctx is None:
            return None
        shift, line_id = ctx
        return _archived_path(kind, owner_id, record_date, cycle_seq, ng,
                              shift, line_id)
    except Exception:
        return None


# ── serving (Range-aware; Starlette 0.36 FileResponse is not) ────────────
def serve(path: str, request: Optional[Request]):
    """Stream an archived clip, honouring a byte-range request.

    Without 206 support the player cannot seek and the scrub bar is dead, so
    the Range parsing is explicit here.
    """
    size = os.path.getsize(path)
    rng = (request.headers.get("range") if request is not None else None) or ""
    start, end = 0, size - 1
    partial = False

    m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
    if m:
        s, e = m.group(1), m.group(2)
        if s:
            start = int(s)
            if e:
                end = min(int(e), size - 1)
        elif e:                        # suffix form: last N bytes
            start = max(0, size - int(e))
        if start > end or start >= size:
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{size}"})
        partial = True

    length = end - start + 1

    def _body(chunk=256 * 1024):
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                buf = fh.read(min(chunk, left))
                if not buf:
                    break
                left -= len(buf)
                yield buf

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        # 2026-08-18 — NO browser cache.  The clip URL is keyed only by
        # cycle_seq, but cycle_seq REPEATS across days (esp. register-mirror
        # machines like Semi-Auto whose counter resets) — so max-age=86400 made
        # the browser serve a PREVIOUS day's clip for TODAY's dot ("purani video
        # aa rahi hai").  The on-demand path already sends no-cache for exactly
        # this reason (see routers/submachines.py); match it.  The server still
        # serves the archived file fast off disk — only the stale browser copy
        # was the bug.
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Clip-Source": "archive",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_body(), status_code=206 if partial else 200,
                             media_type="video/mp4", headers=headers)


# ── "there is no footage for this one" memory ────────────────────────────
# 2026-08-12 — THE OTHER HALF OF THE THROUGHPUT BUG.  _fetch_and_store already
# documented that a 404/416 means the camera simply has no footage for that
# window and should not be retried — but nothing ever recorded the fact, so
# every failing cycle came back in the very next round, forever.  Measured on a
# live sample: 24 of 24 candidates failed (15 main-line 404, 5 gateway 502, 4
# sub 416), which is why the worker managed 42 clips/min with 42 cores idle and
# a single ffmpeg running.  It was not rendering — it was re-failing.
#
# The main-line lane suffered worst: it gets a reserved third of every round,
# and lines with no camera mapped occupied that share permanently.
#
# Entries expire after the .ts window, by which time the cycle has aged out of
# _pending() anyway; the TTL only matters if a camera comes back.
_NO_FOOTAGE: dict = {}             # key -> (parked_at, ttl_seconds)
_NO_FOOTAGE_TTL = WINDOW_MIN * 60  # a 404/416: no footage, and there never will be
# 2026-08-12 — TWO TTLs, learned the hard way.  Originally every failure parked
# a cycle for the full window.  Then the CMS got saturated (by this archiver,
# see DIRECT RENDER above) and thousands of perfectly recorded cycles timed out
# — so they were parked for 42 minutes each, _pending() ran dry, and coverage
# sat at 33% while the box was idle.  A timeout says the SYSTEM was busy; it
# says nothing about the footage, so it may only ever park briefly.
_NO_FOOTAGE_SOFT_TTL = _int_env("CLIP_ARCHIVE_SOFT_PARK_S", 180, low=10)
_NO_FOOTAGE_MAX = 200_000          # bounded so a long uptime cannot leak
_SOFT_FAIL_LIMIT = 3
_soft_fails: dict = {}


def _nf_key(kind: str, owner, seq, ng) -> str:
    return f"{kind}:{owner}:{seq}:{1 if ng else 0}"


def _nf_blocked(kind: str, owner, seq, ng) -> bool:
    ent = _NO_FOOTAGE.get(_nf_key(kind, owner, seq, ng))
    if not ent:
        return False
    at, ttl = ent
    return (time.time() - at) < ttl


def _nf_mark(kind: str, owner, seq, ng, hard: bool = True) -> None:
    """Park a candidate.  hard=True for 404/416, else after _SOFT_FAIL_LIMIT."""
    k = _nf_key(kind, owner, seq, ng)
    if not hard:
        n = _soft_fails.get(k, 0) + 1
        _soft_fails[k] = n
        if n < _SOFT_FAIL_LIMIT:
            return
        _soft_fails.pop(k, None)
    if len(_NO_FOOTAGE) >= _NO_FOOTAGE_MAX:
        now = time.time()
        for kk, (at, ttl) in list(_NO_FOOTAGE.items()):
            if now - at >= ttl:
                _NO_FOOTAGE.pop(kk, None)
        if len(_NO_FOOTAGE) >= _NO_FOOTAGE_MAX:
            _NO_FOOTAGE.clear()        # pathological: start over rather than grow
    _NO_FOOTAGE[k] = (time.time(),
                      _NO_FOOTAGE_TTL if hard else _NO_FOOTAGE_SOFT_TTL)


# ── background worker ────────────────────────────────────────────────────
# ── fair share + dead-camera skip (2026-09-19) ───────────────────────────
# Both lanes used to walk mes_lines in TABLE order and stop the moment the
# budget filled, so a line's share depended on where its row happened to sit
# in the heap.  Y17-SS sat last (#31) and lost 32 of its last 60 cycles while
# lines near the top lost none; lines at the top whose camera has no footage
# (YRA-SA-4WAY: no camera bound; 2UA RECLINER: bound but not recording) spent
# the budget failing every round.  Now every source gets its newest cycle in
# before any source gets a second, and a cycle is only queued if its camera
# actually has a recording file spanning that moment.
_TS_NAME_RE = re.compile(r"^cam_(.+)_(\d{13})\.ts$")


def _footage_index():
    """camera_id -> [(file_start, last_write)] epoch spans of its .ts files.

    Cheap on purpose (a listdir and a stat per file, no ffprobe): the file name
    carries the recorder's start time and the mtime is its last write.  None
    when the folder cannot be read — callers then skip nothing.
    """
    idx = {}
    try:
        names = os.listdir(VIDEO_ROOT)
    except Exception:
        return None
    for name in names:
        m = _TS_NAME_RE.match(name)
        if not m:
            continue
        try:
            mt = os.path.getmtime(os.path.join(VIDEO_ROOT, name))
        except OSError:
            continue
        idx.setdefault(m.group(1), []).append((int(m.group(2)) / 1000.0, mt))
    return idx


def _has_footage(idx, camera_id, ts_start, ts_end) -> bool:
    """False only when we KNOW no file of this camera covers the cycle."""
    if idx is None or not camera_id or not ts_start or not ts_end:
        return True
    spans = idx.get(str(camera_id).strip())
    if not spans:
        return False
    try:
        a = _naive(ts_start).timestamp() - 30     # 30 s slack: recorder start-up
        b = _naive(ts_end).timestamp() + 30
    except Exception:
        return True
    return any(s0 <= b and s1 >= a for s0, s1 in spans)


def _fair_merge(per_source, limit):
    """Interleave candidate lists: every source's 1st, then every 2nd, ...

    Within a pass the sources with the most waiting go first, so when the
    budget ends mid-pass it is the most-behind lines that got the slots.
    """
    if limit <= 0:
        return []
    queues = sorted((q for q in per_source if q), key=len, reverse=True)
    out, depth = [], 0
    while len(out) < limit:
        added = False
        for q in queues:
            if depth < len(q):
                out.append(q[depth])
                added = True
                if len(out) >= limit:
                    break
        if not added:
            break
        depth += 1
    return out


# ── Clip priority (Admin → Production → Clip Priority) — 2026-09-21 ─────────
# The GPU cuts ~110k clips/day against ~217k cycles, so sub-machine clips go in
# the admin's order: NG on every zone first (P1), then every cycle of the top
# `p2_zones` zones by zone rank → machine-type rank → newest (P2).  The other
# zones' OK cycles are left for on-click cutting from the 48 h footage (P3).
# Inert until the admin SAVES the page: with nothing saved, or if the config
# cannot be read, everything behaves exactly as before (newest first, all).
import re as _re_prio
_PRIO = {"t": 0.0, "cfg": None, "zone_of_line": {}}
_PRIO_LOCK = threading.Lock()
_MT_RULES = [
    ("Final Inspection", r"final\s*insp"),
    ("Welding (MAG / PJW / projection)", r"weld|pjw|projection|\bmag\b"),
    ("Checking (bolt strength, inspection)", r"check|strength|inspect|test"),
    ("Insert / press (ball guide, lock bar, hinge pin)",
     r"insert|press|ball\s*guide|lock\s*bar|hinge|\bpin\b|stak|caulk|rivet|assy|assembl"),
    ("Greasing / bending / supply", r"greas|bend|supply|karakuri|squeez|slit|cut"),
]


def _machine_type(name) -> str:
    n = str(name or "").lower()
    for label, rx in _MT_RULES:
        if _re_prio.search(rx, n):
            return label
    return "Other"


def _prio():
    """The saved clip-priority config + line→zone map, refreshed every 60 s.
    None = nothing saved / unreadable → old behaviour."""
    with _PRIO_LOCK:
        if time.time() - _PRIO["t"] < 60:
            return _PRIO if _PRIO["cfg"] else None
    cfg, zmap = None, {}
    try:
        from routers.clip_priority import _merged
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT to_regclass('mes_clip_priority') AS t")
            if (cur.fetchone() or {}).get("t"):
                cur.execute("SELECT config FROM mes_clip_priority WHERE id = 1")
                row = cur.fetchone()
                if row and row["config"]:
                    cur.execute("SELECT zone_name FROM mes_zones WHERE COALESCE(is_active, TRUE) "
                                "AND COALESCE(zone_name,'') <> '' ORDER BY zone_name")
                    cfg = _merged(row["config"], [r["zone_name"] for r in cur.fetchall()])
                    cur.execute("SELECT l.id, z.zone_name FROM mes_lines l "
                                "LEFT JOIN mes_zones z ON z.id = l.zone_id")
                    zmap = {r["id"]: r["zone_name"] for r in cur.fetchall()}
    except Exception as exc:
        print(f"[CLIP-ARCHIVE] clip priority not read ({exc}) — archiving as before")
        cfg = None
    with _PRIO_LOCK:
        _PRIO.update(t=time.time(), cfg=cfg, zone_of_line=zmap)
        return _PRIO if cfg else None


def _sub_rank(line_id, machine_name, is_ng):
    """Sort key for a sub-machine cycle, or None when it is P3 (on click only).
    With no saved priority every cycle ranks equal (old newest-first order)."""
    p = _prio()
    if not p:
        return (0, 0, 0)
    cfg = p["cfg"]
    zones = cfg.get("zone_order") or []
    z = p["zone_of_line"].get(line_id)
    zr = zones.index(z) if z in zones else len(zones)
    mo = cfg.get("machine_order") or []
    mt = _machine_type(machine_name)
    mr = mo.index(mt) if mt in mo else len(mo)
    if is_ng and (cfg.get("p1") or {}).get("ng_cycles", True):
        return (0, zr, mr)
    if zr < int(cfg.get("p2_zones") or 0):
        return (1, zr, mr)
    _stats["priority_p3_skip"] = _stats.get("priority_p3_skip", 0) + 1
    return None


def _pending(limit: int):
    """Newest over-target / Alarm cycles that are not archived yet.

    Newest first: everything here is racing the .ts window, and a cycle that
    ages out is lost, so the freshest ones must win.
    """
    out = []
    cutoff_min = WINDOW_MIN
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # PRIORITY, then recency.  Under the load ceiling the worker may only
        # get through part of a round, so the clips people actually open
        # (Alarms, then over-target) must be the ones that get done — the
        # ordinary green cycles fill in with whatever headroom is left.
        interesting_only = "" if ARCHIVE_ALL else """
              AND (COALESCE(l.is_ng, FALSE)
                   OR (p.ideal_cycle_time IS NOT NULL
                       AND l.ct_seconds > p.ideal_cycle_time))"""

        # MAIN LINE FIRST.  The sub-machine pass below always has candidates
        # and would fill the whole round on its own, so Final Inspection — the
        # one machine the Management dashboard shows — never got a slot and
        # stayed slow.  Give it a reserved share of each round.
        # 2026-08-12 — MAIN LINE IS THE SLOW LANE NOW.  Its window is derived in
        # the clip endpoint, so these rows still go out over HTTP to the CMS —
        # ~20 s each, through _HTTP_SEM's few slots.  At limit//3 (40 rows of a
        # 120 round) they filled every _drain worker and the direct lane, which
        # is ~2 s a clip, never got to run: measured 3 clips/min with the box
        # idle.  Give the slow lane exactly the width it can actually use.
        # 2026-09-16 — limit//4 → limit//2.  The quarter-share above was set when
        # every Final-Inspection clip had to go through the slow CMS HTTP lane
        # (~20 s each) and would fill every worker.  Main-line rows that carry a
        # camera + window now render on the DIRECT lane (~2 s), and main line is
        # what the dashboard video modal actually opens — a 20-user emulator
        # measured main-line archive coverage at ~7% while sub-machines were fully
        # covered.  Half the round keeps sub-machines progressing while letting
        # the main line actually catch up.
        main_budget = max(8, limit // 2)
        # Sub-machines whose camera binding is missing from mes_plc_configs are
        # in the same boat — resolvable only by the endpoint, via plc_ip.  Let a
        # couple through per round so they still progress, never enough to stall
        # the round.
        nocam_budget = 2 if DIRECT_RENDER else limit
        nocam_taken = 0

        # 2026-08-12 — THE SUPPLY BUG.  This query fetches limit*6 rows and the
        # already-archived ones are then filtered out in Python.  Ordered by
        # PRIORITY that is fatal when archiving everything: the NG and
        # over-target rows in a 42-minute window number in the thousands, so the
        # fetch never reached an ordinary green cycle — and since those priority
        # rows were long since archived, the round came back with 2-9 candidates
        # while tens of thousands of green cycles sat unarchived.  Measured:
        # rounds finishing in 2 s with 46 cores idle and coverage stuck at 33%.
        #
        # When ARCHIVE_ALL is on, everything is wanted, so newest-first is both
        # simpler and correct — it races the .ts window, which is the only
        # deadline here.  Priority ordering still applies in subset mode, where
        # it is doing real work: choosing WHICH cycles are worth a slot.
        order_by = ("ORDER BY l.ts_end DESC" if ARCHIVE_ALL
                    else "ORDER BY prio ASC, l.ts_end DESC")

        # 2026-08-12 — MAIN-LINE cycles too.  Final Inspection is what the
        # Management dashboard shows, so leaving it out meant that screen never
        # got the archive at all.  Its cycles live in one table per line
        # (mes_lines.db_table_name + '_ct_log') rather than the shared
        # sub-machine log, so they need their own pass.
        try:
            # 2026-08-12 — resolve each line's OWN main camera here, exactly as
            # routers/lines.py does when it builds the upstream request.  With
            # it the main-line clip can be cut locally like a sub-machine one;
            # without it every Final Inspection clip had to go to the CMS, and
            # once the CMS clogged (875 threads) they all 502'd at the 30 s
            # proxy timeout while sub-machine clips served from disk in 0.02 s.
            cur.execute("""
                SELECT l.id, l.db_table_name, l.ideal_cycle_time,
                       (SELECT NULLIF(TRIM(pc.nf2_camera_id), '')
                          FROM mes_plc_configs pc
                         WHERE pc.line_id = l.id
                           AND ((l.dashboard_plc_id IS NOT NULL
                                 AND pc.id = l.dashboard_plc_id)
                             OR (l.dashboard_plc_id IS NULL
                                 AND pc.parent_plc_id IS NULL))
                         ORDER BY (pc.parent_plc_id IS NULL) DESC
                         LIMIT 1) AS cam
                FROM mes_lines l
                WHERE l.is_active AND l.db_table_name IS NOT NULL
            """)
            _lines_all = cur.fetchall()
            _fidx = _footage_index()
            _per_line, _nocam_per_line = [], []
            for ln in _lines_all:
                _cand, _nocam = [], []
                tbl = f'{ln["db_table_name"]}_ct_log'
                try:
                    _main_filter = ("" if ARCHIVE_ALL else """
                          AND (COALESCE(is_ng, FALSE)
                               OR (%(ict)s IS NOT NULL AND ct_value > %(ict)s))""")
                    cur.execute(f"""
                        SELECT cycle_seq, record_date, ts, ct_value, shift_name,
                               COALESCE(is_ng, FALSE) AS is_ng
                        FROM {tbl}
                        WHERE ts > now() - interval '{cutoff_min} minutes'
                          AND ts < now() - interval '5 seconds'
                          {_main_filter}
                        ORDER BY ts DESC
                        LIMIT 60
                    """, {"ict": ln["ideal_cycle_time"]})
                except Exception:
                    # A few lines have no _ct_log table at all (never
                    # provisioned).  psycopg2 aborts the whole transaction on a
                    # failed statement, so without this rollback the FIRST
                    # missing table silently killed every line after it — which
                    # is why the main line never got archived at all.
                    conn.rollback()
                    continue
                _main_rows = cur.fetchall()
                for r in _main_rows:
                    if _archived_path("line", ln["id"], r["record_date"],
                                      r["cycle_seq"], r["is_ng"],
                                      r.get("shift_name"), ln["id"]):
                        continue
                    if _nf_blocked("line", ln["id"], r["cycle_seq"], r["is_ng"]):
                        continue       # already answered "no footage" — don't burn the slot
                    row = {"kind": "line", "line_id": ln["id"],
                           "cycle_seq": r["cycle_seq"],
                           "record_date": r["record_date"],
                           "is_ng": r["is_ng"],
                           "shift_name": r.get("shift_name")}
                    # Same window arithmetic as routers/lines.py: this log holds
                    # only the cycle END, so the start is derived from ct_value,
                    # and an Alarm clip runs on to the NEXT row so the operator
                    # sees the alarm and its recovery as one video.
                    if ln["cam"] and r.get("ts"):
                        _end = r["ts"]
                        _dur = max(3.0, (float(r["ct_value"] or 0) or 10.0) + 1.0)
                        if r["is_ng"]:
                            _nx = next((x["ts"] for x in _main_rows
                                        if x["ts"] and x["ts"] > _end), None)
                            if _nx:
                                _dur += (_nx - _end).total_seconds()
                                _end = _nx
                        if _CLIP_MAX_SECONDS > 0 and _dur > _CLIP_MAX_SECONDS:
                            _dur = _CLIP_MAX_SECONDS
                        row["nf2_camera_id"] = ln["cam"]
                        row["ts_end"]   = _end
                        row["ts_start"] = _end - timedelta(seconds=_dur)
                    if row.get("nf2_camera_id"):
                        if not _has_footage(_fidx, row["nf2_camera_id"],
                                            row.get("ts_start"), row.get("ts_end")):
                            _stats["nofootage_skip"] = _stats.get("nofootage_skip", 0) + 1
                            continue
                        _cand.append(row)
                    else:
                        _nocam.append(row)      # window derived in the endpoint (HTTP)
                if _cand:
                    _per_line.append(_cand)
                if _nocam:
                    _nocam_per_line.append(_nocam)
            # No-camera rows can only go the slow HTTP way and mostly come back
            # 404; like the sub-machine pass, let only a couple through a round.
            out.extend(_fair_merge(_nocam_per_line, min(nocam_budget, main_budget)))
            out.extend(_fair_merge(_per_line, main_budget - len(out)))
        except Exception as exc:
            print(f"[CLIP-ARCHIVE] main-line scan skipped: {exc}")

        cur.execute(f"""
            SELECT l.sub_plc_id, l.cycle_seq, l.record_date,
                   COALESCE(l.is_ng, FALSE) AS is_ng,
                   l.ts_start, l.ts_end, l.shift_name, p.line_id, p.nf2_camera_id,
                   p.machine_name,
                   CASE WHEN COALESCE(l.is_ng, FALSE) THEN 0
                        WHEN p.ideal_cycle_time IS NOT NULL
                             AND l.ct_seconds > p.ideal_cycle_time THEN 1
                        ELSE 2 END AS prio
            FROM mes_submachine_ct_log l
            JOIN mes_plc_configs p ON p.id = l.sub_plc_id
            WHERE l.ts_end > now() - interval '{cutoff_min} minutes'
              AND l.ts_end < now() - interval '5 seconds'
              {interesting_only}
            {order_by}
            LIMIT %s
        """, (limit * (10 if _prio() else 6),))
        _fidx_sub = _footage_index()
        _subs = []
        for r in cur.fetchall():
            rk = _sub_rank(r.get("line_id"), r.get("machine_name"), r["is_ng"])
            if rk is not None:
                _subs.append((rk, r))
        # stable sort: rank first, the SQL's newest-first order within a rank
        _subs.sort(key=lambda x: x[0])
        for _rk, r in _subs:
            if _archived_path("sub", r["sub_plc_id"], r["record_date"],
                              r["cycle_seq"], r["is_ng"],
                              r.get("shift_name"), r.get("line_id")):
                continue
            if _nf_blocked("sub", r["sub_plc_id"], r["cycle_seq"], r["is_ng"]):
                continue
            if not (r.get("nf2_camera_id") or "").strip():
                if nocam_taken >= nocam_budget:
                    continue
                nocam_taken += 1
            elif not _has_footage(_fidx_sub, r["nf2_camera_id"], r.get("ts_start"), r.get("ts_end")):
                _stats["nofootage_skip"] = _stats.get("nofootage_skip", 0) + 1
                continue
            r["kind"] = "sub"
            out.append(r)
            if len(out) >= limit:
                break

    return out


def _pending_newest_main(limit: int):
    """The newest MAIN-LINE cycles — the Final-Inspection dot the operator opens
    from the Management / Supervisor dashboard.

    2026-09-16 — THE COVERAGE BUG.  `_pending_newest` (the fast lane whose whole
    purpose is "the dot an operator actually clicks") only ever queried
    mes_submachine_ct_log and tagged every row kind="sub".  Main-line cycles were
    therefore left to the slow backfill round, and the numbers showed it:
    measured over 10 minutes the archive wrote 1055 sub clips but only 45 main
    ones, against ~62 main cycles/min produced — roughly 7% coverage.  A 20-user
    emulator then found only 5% of video opens hitting a pre-rendered clip while
    77% were cut on demand at click time (4-10 s).  The fast lane was
    pre-rendering the wrong thing.  This adds the main line to it.
    """
    out = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        try:
            cur.execute("""
                SELECT l.id, l.db_table_name,
                       (SELECT NULLIF(TRIM(pc.nf2_camera_id), '')
                          FROM mes_plc_configs pc
                         WHERE pc.line_id = l.id
                           AND ((l.dashboard_plc_id IS NOT NULL
                                 AND pc.id = l.dashboard_plc_id)
                             OR (l.dashboard_plc_id IS NULL
                                 AND pc.parent_plc_id IS NULL))
                         ORDER BY (pc.parent_plc_id IS NULL) DESC
                         LIMIT 1) AS cam
                FROM mes_lines l
                WHERE l.is_active AND l.db_table_name IS NOT NULL
                  AND l.db_table_name <> ''
            """)
            lines = cur.fetchall()
        except Exception as exc:
            conn.rollback()
            print(f"[CLIP-ARCHIVE] newest-main scan skipped: {exc}")
            return out

        _fidx = _footage_index()
        _per_line = []
        for ln in lines:
            _cand = []
            cam = (ln.get("cam") or "").strip()
            if not cam:
                continue            # no camera bound → no footage to cut
            tbl = f'{ln["db_table_name"]}_ct_log'
            try:
                cur.execute(f"""
                    SELECT cycle_seq, record_date, ts, ct_value, shift_name,
                           COALESCE(is_ng, FALSE) AS is_ng
                      FROM {tbl}
                     WHERE ts > now() - interval '6 minutes'
                       AND ts < now() - interval '4 seconds'
                     ORDER BY ts DESC
                     LIMIT 4
                """)
                rows = cur.fetchall()
            except Exception:
                conn.rollback()     # line never provisioned a ct_log
                continue
            for r in rows:
                if _archived_path("line", ln["id"], r["record_date"],
                                  r["cycle_seq"], r["is_ng"],
                                  r.get("shift_name"), ln["id"]):
                    continue
                if _nf_blocked("line", ln["id"], r["cycle_seq"], r["is_ng"]):
                    continue
                row = {"kind": "line", "line_id": ln["id"],
                       "cycle_seq": r["cycle_seq"],
                       "record_date": r["record_date"],
                       "is_ng": r["is_ng"],
                       "shift_name": r.get("shift_name"),
                       "nf2_camera_id": cam}
                # Same window arithmetic as _pending()/routers/lines.py: the log
                # stores only the cycle END, so derive the start from ct_value,
                # and an Alarm clip runs on into the NEXT cycle.
                if r.get("ts"):
                    _end = r["ts"]
                    _dur = max(3.0, (float(r["ct_value"] or 0) or 10.0) + 1.0)
                    if r["is_ng"]:
                        _nx = next((x["ts"] for x in rows
                                    if x["ts"] and x["ts"] > _end), None)
                        if _nx:
                            _dur += (_nx - _end).total_seconds()
                            _end = _nx
                    if _CLIP_MAX_SECONDS > 0 and _dur > _CLIP_MAX_SECONDS:
                        _dur = _CLIP_MAX_SECONDS
                    row["ts_end"]   = _end
                    row["ts_start"] = _end - timedelta(seconds=_dur)
                if not _has_footage(_fidx, cam, row.get("ts_start"), row.get("ts_end")):
                    _stats["nofootage_skip"] = _stats.get("nofootage_skip", 0) + 1
                    continue
                _cand.append(row)
            if _cand:
                _per_line.append(_cand)
    out.extend(_fair_merge(_per_line, limit))
    return out


def _pending_newest(limit: int):
    """The newest cycle on each machine — the dot an operator actually clicks.

    Kept OUT of the main round on purpose.  That round walks up to 120 older
    candidates and takes ~20 s, so a cycle that finished 15 s ago was still
    queued behind it when the operator clicked — measured 2-5 s on a dot that
    should have been ready.  This list is tiny (one per machine) and its own
    loop re-runs every couple of seconds, so a fresh clip is on disk within a
    few seconds of the cycle closing.

    Not filtered to over-target/Alarm: the newest dot is worth having whatever
    its cycle time was.

    2026-09-16 — MAIN LINE FIRST.  This lane used to fetch sub-machine rows only,
    so the Final-Inspection clip the dashboard actually opens was never in it.
    Main now gets the larger share of the budget and sub-machines fill the rest,
    so neither starves.
    """
    main_budget = max(1, int(limit * 0.6))
    out = _pending_newest_main(main_budget)
    if len(out) >= limit:
        return out
    with get_conn() as conn:
        cur = dict_cursor(conn)
        try:
            cur.execute(f"""
                SELECT DISTINCT ON (l.sub_plc_id)
                       l.sub_plc_id, l.cycle_seq, l.record_date,
                       COALESCE(l.is_ng, FALSE) AS is_ng,
                       l.ts_start, l.ts_end, l.shift_name, p.line_id, p.nf2_camera_id,
                       p.machine_name
                FROM mes_submachine_ct_log l
                JOIN mes_plc_configs p ON p.id = l.sub_plc_id
                WHERE l.ts_end > now() - interval '6 minutes'
                  AND l.ts_end < now() - interval '4 seconds'
                ORDER BY l.sub_plc_id, l.ts_end DESC
            """)
            rows = cur.fetchall()
        except Exception as exc:
            conn.rollback()
            print(f"[CLIP-ARCHIVE] newest scan skipped: {exc}")
            return out
    # Newest first.  This used to sort by cycle_seq ACROSS machines, which is
    # meaningless between machines and let the ones whose counters never reset
    # (29,000-43,000 on the SA-4WAY / Semi-Auto machines) take every slot.
    rows.sort(key=lambda r: r["ts_end"], reverse=True)
    ranked = []
    for r in rows:
        rk = _sub_rank(r.get("line_id"), r.get("machine_name"), r["is_ng"])
        if rk is not None:
            ranked.append((rk, r))
    ranked.sort(key=lambda x: x[0])        # stable: newest first within a rank
    rows = [r for _rk, r in ranked]
    _fidx_sub = _footage_index()
    for r in rows:
        if len(out) >= limit:
            break
        if (r.get("nf2_camera_id") or "").strip() and not _has_footage(
                _fidx_sub, r["nf2_camera_id"], r.get("ts_start"), r.get("ts_end")):
            _stats["nofootage_skip"] = _stats.get("nofootage_skip", 0) + 1
            continue
        if _archived_path("sub", r["sub_plc_id"], r["record_date"],
                          r["cycle_seq"], r["is_ng"],
                          r.get("shift_name"), r.get("line_id")):
            continue
        if _nf_blocked("sub", r["sub_plc_id"], r["cycle_seq"], r["is_ng"]):
            continue
        r["kind"] = "sub"
        out.append(r)
    return out


_http_inflight = 0
_http_guard = threading.Lock()


def _http_sem_try() -> bool:
    """Claim a slow-lane slot, or report that the lane is full."""
    global _http_inflight
    with _http_guard:
        if _http_inflight >= HTTP_PARALLEL:
            return False
        _http_inflight += 1
        return True


def _http_worker(row) -> None:
    """Run one slow row and free its slot.  Nobody waits on the result."""
    global _http_inflight
    try:
        if _fetch_and_store(row, allow_http=True):
            _stats["archived"] += 1
        else:
            _stats["failed"] += 1
    except Exception as exc:
        _stats["last_error"] = f"slow lane: {type(exc).__name__}: {exc}"
    finally:
        with _http_guard:
            _http_inflight -= 1


def _drain(pool, rows, workers: int, load_aware: bool = True):
    """Push `rows` through `pool`, keeping `workers` renders in flight at all times.

    2026-08-12 — THE THROUGHPUT BUG.  Both loops used to run in lockstep:
    submit N, wait for ALL N to finish, submit the next N.  Clip cost is wildly
    uneven — one seeking near the live edge of a 1 GB .ts takes 12 s while its
    neighbours take 1 s — so every chunk ran at the speed of its slowest member
    and the other threads sat idle.  Measured: 8 configured workers, but only
    ~2 ffmpegs actually running and 40 of 64 cores idle, i.e. a quarter of the
    intended rate.  That, not CPU and not the encoder, is why coverage stalled.

    Here a slot is refilled the moment it frees, so the pool stays saturated and
    a slow clip costs one thread instead of all of them.
    """
    from concurrent.futures import wait, FIRST_COMPLETED

    ok = failed = 0
    it = iter(rows)
    inflight = set()
    feeding = True

    def _feed(n: int) -> None:
        nonlocal feeding
        for _ in range(n):
            if not feeding:
                return
            try:
                inflight.add(pool.submit(_fetch_and_store, next(it), False))
            except StopIteration:
                feeding = False
                return

    _feed(workers)
    next_check = 0.0
    while inflight:
        done, pending = wait(inflight, return_when=FIRST_COMPLETED)
        inflight = set(pending)
        for fut in done:
            try:
                if fut.result():
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        # Re-check the load ceiling at most once a second.  The probe costs real
        # wall time, so running it per completion would throttle the feeder
        # itself — the very stall this function exists to remove.  On overshoot
        # stop feeding but let what is in flight finish, so nothing is wasted.
        if load_aware and feeding and time.time() >= next_check:
            next_check = time.time() + 1.0
            try:
                if _idle_avg() < MIN_IDLE_CORES * 0.6:
                    feeding = False
            except Exception:
                pass
        _feed(len(done))
    return ok, failed


def _newest_loop():
    """Tight loop for the freshest clips.  Small batches, runs constantly."""
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=NEWEST_PARALLEL,
                              thread_name_prefix="clipnew")
    while True:
        try:
            if _idle_avg() >= MIN_IDLE_CORES:
                rows = _pending_newest(NEWEST_PARALLEL * 3)
                if rows:
                    # This lane is the latency-critical one and its batch is
                    # small, so it feeds straight through without re-probing
                    # load mid-batch; the check above already gated it.
                    _drain(pool, rows, NEWEST_PARALLEL, load_aware=False)
        except Exception as exc:
            print(f"[CLIP-ARCHIVE] newest loop: {exc}")
        time.sleep(NEWEST_SLEEP)


def _needs_http(row) -> bool:
    """True when this row can only be done the slow way (via the clip endpoint).

    Main-line rows carry no camera or window — those are derived in the
    endpoint — and a handful of sub-machines have no camera binding, so they
    are resolved there by plc_ip.  Everything else is cut locally.
    """
    if not DIRECT_RENDER:
        return True
    return not ((row.get("nf2_camera_id") or "").strip() and row.get("ts_start"))


def _fetch_and_store(row, allow_http: bool = True) -> bool:
    """Render one clip and save it.  Best-effort.

    allow_http=False keeps a worker in the fast lane: a direct miss returns
    immediately instead of falling back to a ~20-90 s round trip.  That
    fallback is what made rounds take 130-152 s for work worth ~15 s — the
    handful of slow rows held every worker while ~97 finished clips waited on
    them.  The slow rows now run in their own pool (see _loop).
    """
    kind = row.get("kind", "sub")
    seq  = row["cycle_seq"]
    ng   = bool(row["is_ng"])
    owner = row["line_id"] if kind == "line" else row["sub_plc_id"]
    dest = clip_path(kind, owner, row["record_date"], seq, ng,
                     row.get("shift_name"), row.get("line_id"))

    # Sub-machine clips are cut here, against the idle cores, so the CMS's
    # render queue stays free for the operator who is waiting on a click.
    # Main-line rows have no camera/window in this row, so they still go the
    # long way — but under _HTTP_SEM, which keeps that traffic to a trickle.
    if DIRECT_RENDER and (row.get("nf2_camera_id") or "").strip() and row.get("ts_start"):
        if _render_direct(str(row["nf2_camera_id"]).strip(),
                          row.get("ts_start"), row.get("ts_end"), dest):
            return True
        if not allow_http:
            # No overlapping .ts — usually the camera was down for that window.
            # Park briefly rather than pay for a round trip that would almost
            # certainly 404 too; if the file shows up, the retry will find it.
            _nf_mark(kind, owner, seq, ng, hard=False)
            return False
        # Fall through: the app path knows tricks this does not — the
        # multi-file stitch and the live-edge wait — so give it one chance.

    tmp = dest + f".part{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if kind == "line":
            url = (f"{SELF_BASE}/api/lines/{owner}/cycle-video"
                   f"?cycle_seq={seq}&ng={1 if ng else 0}")
        else:
            url = (f"{SELF_BASE}/api/submachines/{owner}/cycle-video"
                   f"?cycle_seq={seq}&ng={1 if ng else 0}")
        _HTTP_SEM.acquire()
        try:
            r = requests.get(url, timeout=TIMEOUT, stream=True)
        finally:
            # Released as soon as the upstream has answered: the body is a
            # local read, and holding the slot through it would throttle the
            # archive without protecting anyone.
            _HTTP_SEM.release()
        try:
            if r.status_code != 200:
                # 404/416 = the camera has no footage for this window.  That is
                # a real answer, so park it: without this the same doomed rows
                # came back every round and crowded out ones that would have
                # succeeded.  Anything else (502/504/...) is the CMS having a
                # bad moment, so allow a few tries before parking.
                _nf_mark(kind, owner, seq, ng,
                         hard=r.status_code in (404, 416))
                return False
            n = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(256 * 1024):
                    if chunk:
                        fh.write(chunk)
                        n += len(chunk)
        finally:
            r.close()
        if n < _MIN_BYTES:
            os.remove(tmp)
            # A 200 that is too small to be a clip means the same thing as a
            # 404 here — there was nothing to render.  Park it too, or it comes
            # straight back next round.
            _nf_mark(kind, owner, seq, ng)
            return False
        os.replace(tmp, dest)          # atomic: a reader never sees a half file
        return True
    except Exception as exc:
        _stats["last_error"] = f"{type(exc).__name__}: {exc}"
        # A timeout burns the full TIMEOUT seconds of a worker slot, so a row
        # that keeps timing out is the most expensive thing here — park it after
        # a few attempts rather than paying for it every round.
        _nf_mark(kind, owner, seq, ng, hard=False)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _sweep():
    """Drop whole date folders past the retention window."""
    try:
        keep_from = date.today() - timedelta(days=RETAIN_DAYS)
        for name in os.listdir(ARCHIVE_ROOT):
            p = os.path.join(ARCHIVE_ROOT, name)
            if not os.path.isdir(p):
                continue
            try:
                d = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue               # not a date folder — leave it alone
            if d < keep_from:
                shutil.rmtree(p, ignore_errors=True)
                print(f"[CLIP-ARCHIVE] retention: dropped {name}")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[CLIP-ARCHIVE] sweep failed: {exc}")


def _loop():
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="cliparch")
    # Separate pool for rows that must go through the clip endpoint, so their
    # latency never becomes the round's latency.
    http_pool = ThreadPoolExecutor(max_workers=HTTP_PARALLEL,
                                   thread_name_prefix="clipslow")
    last_sweep = 0.0
    while True:
        try:
            try:
                idle = _idle_avg()
            except Exception:
                idle = 999.0
            if idle < MIN_IDLE_CORES:
                _stats["skipped"] += 1
                _stats["last_run"] = f"{time.strftime('%H:%M:%S')} (idle {idle:.0f} cores, paused)"
                time.sleep(SLEEP_S)
                continue
            _t_q = time.time()
            rows = _pending(BATCH)
            _q_ms = (time.time() - _t_q) * 1000

            # Slow rows leave the round here.  They are dispatched to their own
            # small pool and nobody waits for them, so one 90-second upstream
            # can no longer hold ~97 finished clips hostage.  Submitted only
            # while that pool has a free slot; anything skipped simply comes
            # back next round.
            slow = [r for r in rows if _needs_http(r)]
            rows = [r for r in rows if not _needs_http(r)]
            for r in slow:
                if not _http_sem_try():
                    break
                http_pool.submit(_http_worker, r)

            if rows:
                # Concurrent and still load-aware, but no longer in lockstep —
                # see _drain().  The ceiling is re-checked while the round runs,
                # so a busy box stops feeding instead of ploughing on.
                _t0 = time.time()
                ok, failed = _drain(pool, rows, PARALLEL)
                _stats["archived"] += ok
                _stats["failed"] += failed
                # One line per round.  Without it "the archive is slow" is a
                # guess: this says whether the round was short of CANDIDATES,
                # short of TIME, or just failing — three very different faults
                # that all look identical from the clip count alone.
                _el = time.time() - _t0
                print(f"[CLIP-ARCHIVE] round rows={len(rows)} ok={ok} fail={failed} "
                      f"in {_el:.1f}s ({ok / max(_el, .001) * 60:.0f}/min) "
                      f"query={_q_ms:.0f}ms parked={len(_NO_FOOTAGE)} "
                      f"cpu={_stats.get('cpu',0)} gpu={_stats.get('gpu',0)} "
                      f"nofootage={_stats.get('nofootage_skip',0)} "
                      f"idle={_idle_avg():.0f}",
                      flush=True)
            _stats["last_run"] = time.strftime("%H:%M:%S")
            if time.time() - last_sweep > 3600:
                _sweep()
                last_sweep = time.time()
        except Exception as exc:
            _stats["last_error"] = f"{type(exc).__name__}: {exc}"
            print(f"[CLIP-ARCHIVE] loop error: {exc}")
        time.sleep(SLEEP_S)


def start() -> None:
    """Start the worker once.  Safe to call repeatedly."""
    global _started
    if not ENABLED or _started:
        return
    with _start_lock:
        if _started:
            return
        try:
            os.makedirs(ARCHIVE_ROOT, exist_ok=True)
        except Exception as exc:
            print(f"[CLIP-ARCHIVE] disabled — cannot create {ARCHIVE_ROOT}: {exc}")
            return
        threading.Thread(target=_idle_sampler, daemon=True, name="clip-idle").start()
        threading.Thread(target=_loop, daemon=True, name="clip-archive").start()
        threading.Thread(target=_newest_loop, daemon=True,
                         name="clip-archive-newest").start()
        _started = True
        print(f"[CLIP-ARCHIVE] on — root={ARCHIVE_ROOT} parallel={PARALLEL} "
              f"retain={RETAIN_DAYS}d window={WINDOW_MIN}min "
              f"cpu_lane={CPU_PARALLEL}x{CPU_THREADS}t min_idle={CPU_MIN_IDLE:.0f}")


def stats() -> dict:
    d = dict(_stats)
    d.update({"enabled": ENABLED, "root": ARCHIVE_ROOT,
              "retain_days": RETAIN_DAYS, "started": _started})
    try:
        total = files = 0
        for dirpath, _, names in os.walk(ARCHIVE_ROOT):
            for n in names:
                if n.endswith(".mp4"):
                    files += 1
                    total += os.path.getsize(os.path.join(dirpath, n))
        d["files"] = files
        d["bytes"] = total
    except Exception:
        pass
    return d
