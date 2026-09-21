"""
clip_prewarm.py — render a cycle's video clip BEFORE the operator clicks it.

WHY THIS EXISTS
There is no per-cycle video file on disk.  Each camera writes ONE rolling
MPEG-TS (HEVC, ~1 GB, ~50 min of footage) and the CMS /api/submachine/clip
endpoint cuts the cycle's window out of it on demand.  That cut is a full
TRANSCODE, not a stream copy: HEVC -> H.264, scaled, and resampled to CFR 25
because the cameras deliver a sparse variable-rate stream (nominal 25 fps,
~13 fps actual) that plays as a frozen picture otherwise.

Measured on this box: 1.7-2.5 s of libx264 for a 20-30 s cycle, against 0.17 s
to read the .ts and 0.10 s to probe it.  There is no GPU here (ASPEED BMC
only, no /dev/dri/renderD128), so that transcode is CPU-bound and IS the 2-4 s
the operator waits on the first click.

CMS already caches the finished clip by (camera, ts_start, ts_end) in tmpfs for
~10 min — which is exactly why the SECOND click on the same dot is instant.
This module makes the FIRST click instant too, by paying the render before
anyone clicks.

HOW
While a wallboard is open, its 8 s /wallboard-cycles poll already lists every
cycle on screen.  From that we re-request the newest few cycles through the
very same MES cycle-video routes the browser uses, with `Range: bytes=0-0`.

Going through our own route — instead of rebuilding the window arithmetic here
— is deliberate: it guarantees the CMS cache key (an md5 of camera_id +
ts_start + ts_end) matches byte-for-byte what the click will ask for.  Rebuilt
arithmetic that drifted by a millisecond would render a second clip and warm
nothing.  The Range header means CMS renders and caches the clip but ships one
byte back, so a clip nobody opens costs no network.

WHY ONLY THE NEWEST FEW, AND ONLY OPEN WALLBOARDS
Transcoding every cycle on every line would be roughly 4 cores of continuous
work (0.29 cycles/s across the plant x ~14 core-seconds each) spent mostly on
clips nobody looks at.  Demand-driven + newest-N keeps it to a trickle and puts
the work where operators actually click: the cycle that just finished.

Cycles older than the newest-N window are NOT warmed.  Opening one of those
costs a render, same as before this module existed — no regression, just no
improvement there.

SAFETY
- Best-effort throughout: every failure is swallowed.  A cold click still
  works, it just waits like it used to.
- Never blocks the poll — submissions are fire-and-forget.
- CLIP_PREWARM_PARALLEL is deliberately far below the CMS renderer's own 16
  slots (CLIP_RENDER_PARALLEL), so a real click never queues behind a
  speculative render.
- Bounded queue + de-dup, so a slow or down CMS cannot pile up work or grow
  memory without limit.

Tunables (all env, all optional):
    CLIP_PREWARM=1                 turn it on (default OFF since 2026-09-19)
    CLIP_PREWARM_PARALLEL=1        background (poll-driven) renders
    CLIP_PREWARM_HINT_PARALLEL=2   priority renders for hover / list-open
    CLIP_PREWARM_PER_MACHINE=2     newest cycles warmed per machine
    CLIP_PREWARM_TIMEOUT=45        seconds per warm request
    CLIP_PREWARM_SEEN_TTL=900      how long a warmed cycle is remembered
    MES_SELF_BASE=http://127.0.0.1:8080
"""

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("clip_prewarm")


def _int_env(name: str, default: int, low: int = 0) -> int:
    try:
        return max(low, int(os.environ.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


# 2026-09-19 — default is now OFF.  It was on unless CLIP_PREWARM=0 was set,
# and that setting lived only in the running process: every full start
# (start_everything.sh, and so every reboot via eol_boot.sh) brought the
# prewarm storm back, the CMS wedged within ~20 min, and the CMS got restarted
# (12 times on 19-Sep).  Set CLIP_PREWARM=1 explicitly to turn it on.
ENABLED     = os.environ.get("CLIP_PREWARM", "0") == "1"
PARALLEL    = _int_env("CLIP_PREWARM_PARALLEL", 1, low=1)
PER_MACHINE = _int_env("CLIP_PREWARM_PER_MACHINE", 2, low=0)
SEEN_TTL    = float(_int_env("CLIP_PREWARM_SEEN_TTL", 900, low=60))
TIMEOUT     = float(_int_env("CLIP_PREWARM_TIMEOUT", 45, low=5))
SELF_BASE   = os.environ.get("MES_SELF_BASE", "http://127.0.0.1:8080").rstrip("/")

PARALLEL_HINT = _int_env("CLIP_PREWARM_HINT_PARALLEL", 2, low=1)

# 2026-08-10 — CONCURRENCY IS SET BY CORES PER RENDER, NOT BY SPARE CORES.
# First cut ran 3 background + 3 hint renders.  Measured: each clip's libx264
# grabs 4-7 cores (ffmpeg threads default to "all"), and its validation pass
# (ffprobe -count_packets) peaked at 700% on its own — so six speculative
# renders added ~30 cores on top of the 67 recorders.  Load went 32 -> 71 and
# real clicks started timing out at 30 s with a 502, i.e. strictly worse than
# the 2-4 s wait this was meant to remove.  1 + 2 keeps the speculative work to
# roughly a tenth of the box while still covering hover and list-open, which is
# where the clicks actually are.

# TWO LANES, and they must not share workers.
#
# "bg"   — the /wallboard-cycles poll guessing at the newest cycles.
# "hint" — the front-end saying the operator is hovering / just opened the
#          over-target list, i.e. a click is imminent.
#
# The first version used one pool with one cap, and the very first poll after a
# restart filled it with 12 background guesses; the hint that arrived two
# seconds later was refused outright.  Exactly backwards — a real click must
# never queue behind speculation.  So the hint lane gets its own workers and its
# own budget.  Combined ceiling stays well under the CMS renderer's 16 slots.
#
# Refuse work rather than let it pile up: if CMS is slow, extra submissions are
# dropped and the next poll / hover offers them again.
QUEUE_CAP = {"bg": PARALLEL * 2, "hint": PARALLEL_HINT * 4}
MAX_SEEN  = 8000

_pools: dict = {}
_pool_lock = threading.Lock()

# Guards both _seen and _pending.
_lock = threading.Lock()
_seen: dict = {}      # md5(path) -> monotonic time we submitted it
_pending = {"bg": 0, "hint": 0}


def _get_pool(lane: str) -> ThreadPoolExecutor:
    pool = _pools.get(lane)
    if pool is None:
        with _pool_lock:
            pool = _pools.get(lane)
            if pool is None:
                pool = ThreadPoolExecutor(
                    max_workers=(PARALLEL_HINT if lane == "hint" else PARALLEL),
                    thread_name_prefix=f"clipwarm-{lane}")
                _pools[lane] = pool
    return pool


def _claim(key: str, lane: str) -> bool:
    """True if this cycle is ours to render right now.

    False when it is already warm, already queued, or this lane is full.
    """
    now = time.monotonic()
    with _lock:
        if _pending[lane] >= QUEUE_CAP[lane]:
            return False
        prev = _seen.get(key)
        if prev is not None and now - prev < SEEN_TTL:
            return False
        if len(_seen) >= MAX_SEEN:
            for k in [k for k, v in _seen.items() if now - v >= SEEN_TTL]:
                _seen.pop(k, None)
            if len(_seen) >= MAX_SEEN:
                # Pathological (SEEN_TTL far larger than the churn rate).
                # Start over rather than grow without bound.
                _seen.clear()
        _seen[key] = now
        _pending[lane] += 1
        return True


def _release(key: str, lane: str, keep: bool) -> None:
    """Mark the slot free.  keep=False lets a later poll retry this cycle."""
    with _lock:
        _pending[lane] = max(0, _pending[lane] - 1)
        if not keep:
            _seen.pop(key, None)


def _run(key: str, lane: str, path: str) -> None:
    keep = False
    try:
        r = requests.get(SELF_BASE + path,
                         headers={"Range": "bytes=0-0"},
                         timeout=TIMEOUT)
        try:
            # 206/200 — rendered and cached, which is the whole point.
            # 404     — the camera has no footage for this window (stalled,
            #           offline, or the .ts already rotated past it).  That is
            #           a real answer, so don't keep retrying it.
            keep = r.status_code in (200, 206, 404)
        finally:
            # Body is a single byte, but close explicitly so the connection
            # goes back to the pool either way.
            r.close()
    except Exception as exc:
        log.debug("prewarm %s failed: %s", path, exc)
    _release(key, lane, keep)


def submit(paths, hint: bool = False) -> int:
    """Queue MES-relative cycle-video paths for speculative rendering.

    hint=True means the operator is about to click these (hover / list opened),
    so they go down the priority lane with its own workers.
    """
    if not ENABLED:
        return 0
    lane = "hint" if hint else "bg"
    queued = 0
    for path in paths:
        key = hashlib.md5(path.encode()).hexdigest()
        if not _claim(key, lane):
            continue
        try:
            _get_pool(lane).submit(_run, key, lane, path)
            queued += 1
        except Exception:
            _release(key, lane, False)
            break        # pool is gone (shutdown) — stop trying
    return queued


def _newest_seqs(cycles) -> list:
    """The last PER_MACHINE cycle_seq values, newest first.

    /wallboard-cycles orders its cycles by cycle_seq ASC, so the newest sit at
    the end of the list.
    """
    if not cycles:
        return []
    seqs = [c.get("cycle_seq") for c in cycles
            if isinstance(c, dict) and c.get("cycle_seq") is not None]
    if not seqs:
        return []
    return seqs[-PER_MACHINE:][::-1] if PER_MACHINE else []


def submit_wallboard(line_id: int, payload: dict) -> int:
    """Warm the newest cycles of every machine on an open wallboard.

    `payload` is exactly what /wallboard-cycles returns — main row plus the
    per-sub-machine list — so this keeps working as that endpoint grows fields.
    """
    if not ENABLED or PER_MACHINE <= 0 or not isinstance(payload, dict):
        return 0

    paths = []

    main = payload.get("main") or {}
    for seq in _newest_seqs(main.get("cycles")):
        paths.append(f"/api/lines/{line_id}/cycle-video?cycle_seq={seq}")

    for m in (payload.get("machines") or []):
        sub_id = m.get("sub_id")
        if not sub_id:          # 0 / None = the main-line marker, done above
            continue
        for seq in _newest_seqs(m.get("cycles")):
            paths.append(
                f"/api/submachines/{sub_id}/cycle-video?cycle_seq={seq}")

    return submit(paths)


def stats() -> dict:
    """Small snapshot for /health-style debugging."""
    with _lock:
        return {
            "enabled":     ENABLED,
            "parallel":    PARALLEL,
            "per_machine": PER_MACHINE,
            "pending":     dict(_pending),
            "remembered":  len(_seen),
        }
