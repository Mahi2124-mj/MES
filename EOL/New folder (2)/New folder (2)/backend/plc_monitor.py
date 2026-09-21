from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pymcprotocol

from camera_bindings import list_bindings
from camera_config import get_camera_rtsp_url, list_cameras
from cycle_state import end_cycle, get_machine_state, get_next_cycle_num, load_state, rotate_cycle, start_cycle
from plc_config import list_plcs
from recorder import DEFAULT_VIDEOS_DIR, append_cycle_metadata, ensure_metadata_file
from shift_boundary import current_shift_label, is_at_boundary
from zone_config import all_machines_flat


# Persistent state file — survives CMS restart so a service bounce
# during the boundary minute doesn't fire the wipe twice.
SHIFT_STATE_FILE = "shift_state.json"


# ── Video save-root resolver ──────────────────────────────────────────────
# Reads `video_config.json` on EVERY call so admin changes from the
# Camera Master UI ("Video Save Path") take effect immediately for new
# recordings — no service restart needed.  Resolution order:
#   1. video_config.json → save_path (set via /api/config/video-path)
#      → use it if non-empty AND the directory exists or can be created.
#   2. Else fall back to `<base_dir>/videos` (the original default).
# The returned path is guaranteed to exist on return (mkdir -p semantics).
def _resolve_videos_root(base_dir: str) -> str:
    import json as _json
    cfg_path = os.path.join(base_dir, "video_config.json")
    fallback = os.path.join(base_dir, DEFAULT_VIDEOS_DIR)
    try:
        with open(cfg_path, "r") as f:
            sp = (_json.load(f) or {}).get("save_path", "").strip()
        if sp:
            try:
                os.makedirs(sp, exist_ok=True)
            except Exception as e:
                print(f"[VIDEO-PATH] custom path mkdir failed ({sp!r}: {e}) "
                      f"— falling back to {fallback}")
                sp = ""
            if sp:
                # Runtime writability probe — drives can become read-only
                # (USB removed, ACL change) AFTER an admin saved the path.
                # Better to fall back to local default than spam permission
                # errors on every cycle.
                probe = os.path.join(sp, ".write_test_eol")
                try:
                    with open(probe, "w") as _f:
                        _f.write("ok")
                    os.remove(probe)
                    return sp
                except Exception as e:
                    print(f"[VIDEO-PATH] custom path NOT writable ({sp!r}: {e}) "
                          f"— falling back to {fallback}.  "
                          f"Update the Video Save Path from Camera Master to a writable folder.")
    except Exception:
        pass
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _resolve_retention_seconds(base_dir: str) -> int:
    """Video retention window in SECONDS, read live from video_config.json.
    Prefers `retention_hours`; falls back to legacy `retention_days` (×24);
    default 48 h (2 days).  Mirrors api_server._retention_seconds so both the
    shift-boundary cleaner here and the api_server worker agree on one value."""
    import json as _json
    cfg_path = os.path.join(base_dir, "video_config.json")
    try:
        with open(cfg_path, "r") as f:
            cfg = _json.load(f) or {}
        rh = cfg.get("retention_hours")
        if rh is not None:
            return max(1, int(rh)) * 3600
        return max(1, int(cfg.get("retention_days", 2))) * 24 * 3600
    except Exception:
        return 2 * 24 * 3600


def _video_enabled(base_dir: str) -> bool:
    """Master VIDEO ON/OFF switch from video_config.json (default ON).
    2026-06-19 — admin can turn recording OFF (Storage page toggle) to shed PC
    load: when False, camera recorders are stopped + not respawned, no clips are
    extracted, and live MJPEG is disabled.  Line counting is the MES collector's
    job (not this) so it is completely unaffected."""
    import json as _json
    cfg_path = os.path.join(base_dir, "video_config.json")
    try:
        with open(cfg_path, "r") as f:
            cfg = _json.load(f) or {}
        return cfg.get("video_enabled", True) is not False
    except Exception:
        return True


def _disabled_lines(base_dir: str) -> set:
    """Set of line_names whose camera recording is turned OFF (per-line video
    toggle from the MES Production Panel).  Stored in video_config.json →
    video_disabled_lines.  Empty = every line records."""
    import json as _json
    cfg_path = os.path.join(base_dir, "video_config.json")
    try:
        with open(cfg_path, "r") as f:
            cfg = _json.load(f) or {}
        return {str(x).strip() for x in (cfg.get("video_disabled_lines") or []) if str(x).strip()}
    except Exception:
        return set()


def _line_for_camera(camera_id: str, base_dir: str, bindings=None) -> str:
    """Resolve a camera's line_name via its binding (denormalised on each
    binding by api_server._sync_binding_from_machine).  '' if unknown."""
    cid = str(camera_id or "").strip()
    if not cid:
        return ""
    try:
        binds = bindings if bindings is not None else list_bindings(base_dir)
        for b in binds:
            if str(b.get("camera_id", "")).strip() == cid:
                return str(b.get("line_name", "") or "").strip()
    except Exception:
        pass
    return ""


# ── DEMAND-DRIVEN RECORDING (2026-08-03) ─────────────────────────────────
# Recording every bound camera around the clock was the root of the picture
# quality complaints.  Measured: 31 continuous RTSP recorders while only TWO
# lines were producing.  A single camera pulled on its own is flawless, but
# ~30 at once do not all get served cleanly — each loses packets, which shows
# up as pixel break-up and, because the recorder timestamps by real arrival
# time, as a clip whose progress bar keeps moving while the picture is frozen
# (the timeline is honest; the frames simply never arrived).
#
# So: record a camera only while its LINE is actually producing.  The MES
# knows this (it logs every cycle), so we ask it once a minute.  Cameras on
# idle lines stop, and the handful that matter get the bandwidth.
#
# Fail-OPEN everywhere: if the MES is unreachable, the answer is empty, or the
# camera's line is unknown, the camera keeps recording.  A problem with this
# feature must never be able to silence the cameras.
# 2026-09-21 — 48 h FOOTAGE HOLD.  Raw camera TS files are kept this long so
# clips can still be cut later (catch-up / on click).  Was 1 h, and every
# shift boundary deleted the files outright.
TS_KEEP_HOURS = float(os.environ.get("TS_KEEP_HOURS", "48"))
TS_KEEP_SEC = int(TS_KEEP_HOURS * 3600)
# Shift rotation: one camera at a time, respawn held so the camera can free
# its single RTSP session before the new recorder connects.
ROTATE_STAGGER_S = float(os.environ.get("TS_ROTATE_STAGGER_S", "10"))
ROTATE_QUIET_S = float(os.environ.get("TS_ROTATE_QUIET_S", "20"))
_PRODUCING: set = set()
_PRODUCING_AT: float = 0.0
_PRODUCING_OK: bool = False          # have we ever got a usable answer?
_PRODUCING_LOCK = threading.Lock()
_PRODUCING_TTL = 60.0
# 2026-09-21 — 45 -> 1440 min.  With 45 min every break / shift gap stopped a
# line's cameras and restarted them later; the single-session cameras kept
# the old session and hung (67 hung on 21-Sep).  A line that produced in the
# last 24 h now keeps recording straight through.
_PRODUCING_WINDOW_MIN = int(os.environ.get("VIDEO_ACTIVE_MINUTES", "1440"))


def _refresh_producing(base_dir: str = "") -> None:
    """Pull the producing-line set from the MES (cheap, cached both sides)."""
    global _PRODUCING, _PRODUCING_AT, _PRODUCING_OK
    now = time.time()
    with _PRODUCING_LOCK:
        if now - _PRODUCING_AT < _PRODUCING_TTL:
            return
        _PRODUCING_AT = now
    try:
        import json as _json
        import urllib.request as _u
        url = (f"http://127.0.0.1:8080/api/lines/producing"
               f"?minutes={_PRODUCING_WINDOW_MIN}")
        with _u.urlopen(url, timeout=6) as r:
            data = _json.loads(r.read().decode("utf-8", "ignore"))
        names = {str(l.get("line_name") or "").strip()
                 for l in (data.get("lines") or []) if l.get("line_name")}
        # SAFETY: only trust the answer if some bound camera actually belongs to
        # one of those lines.  Without this check a naming mismatch (or every
        # producing line simply having no camera) silences ALL recording — which
        # is exactly what happened the first time this shipped: 4 producing
        # lines came back, none of them had a working camera, and the box went
        # from 31 recorders to 0.  If nothing matches we fall back to recording
        # everything, so the worst case is the old behaviour, never a blackout.
        if names:
            try:
                bound = {str(b.get("line_name") or "").strip()
                         for b in (list_bindings(base_dir) or [])}
            except Exception:
                bound = set()
            if bound and not (names & bound):
                print(f"[VIDEO] producing lines {sorted(names)} match no camera "
                      f"binding — recording ALL cameras instead", flush=True)
                names = set()
        if names:
            with _PRODUCING_LOCK:
                prev, _PRODUCING, _PRODUCING_OK = _PRODUCING, names, True
            if prev != names:
                print(f"[VIDEO] recording lines ({len(names)}): "
                      f"{', '.join(sorted(names))}", flush=True)
        else:
            with _PRODUCING_LOCK:
                _PRODUCING_OK = False        # unknown -> everything records
    except Exception as exc:
        # leave the previous answer in place; never flip to "record nothing"
        print(f"[VIDEO] producing-lines lookup failed ({exc}) — keeping "
              f"previous set", flush=True)


# How many cameras may record at once.  This is the single most important
# number for picture quality.  Measured on this plant network:
#     camera alone .................. 25.0 fps  (full rate)
#     same camera, ~50 recorders ....  3.5-18 fps
# The cameras are fine one at a time; run fifty and each one loses frames.
# A clip built from a frame-starved recording plays as a still picture over a
# correctly-advancing progress bar — the "video freeze" operators report.
# Capping the recorders keeps every stream near full rate.
# 2026-08-03 — cap raised to cover every bound camera.  It was introduced at
# 20 on the theory that concurrency was starving the streams, but measuring it
# both ways settled that: cap 12 gave 12.0 fps average, cap 20 gave 13.3 — no
# real gain, while the cap DID silently stop recording cameras operators needed
# (YNC's .140 and .145 among them, so those cycles had no video at all).  The
# real frame loss turned out to be the arrival-time stamping, fixed separately.
# Keep the knob for emergencies; default it high enough to exclude nobody.
_MAX_RECORDERS = int(os.environ.get("VIDEO_MAX_RECORDERS", "200"))
_ALLOWED_CAMS: set = set()
_ALLOWED_AT: float = 0.0


def _refresh_allowed(base_dir: str) -> None:
    """Pick which cameras get to record, newest decision every 60 s.

    Priority: cameras on lines that are producing right now, then the rest, up
    to `_MAX_RECORDERS`.  Sorted by camera id inside each group so the choice
    is stable — a camera doesn't get dropped and re-added every cycle, which
    would fragment its recording."""
    global _ALLOWED_CAMS, _ALLOWED_AT
    now = time.time()
    with _PRODUCING_LOCK:
        if now - _ALLOWED_AT < 60.0 and _ALLOWED_CAMS:
            return
        _ALLOWED_AT = now
    try:
        binds = list_bindings(base_dir) or []
    except Exception:
        return
    _refresh_producing(base_dir)
    with _PRODUCING_LOCK:
        producing = set(_PRODUCING)

    hot, cold = [], []
    seen = set()
    for b in binds:
        cid = str(b.get("camera_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ln = str(b.get("line_name") or "").strip()
        (hot if (ln and ln in producing) else cold).append(cid)
    chosen = sorted(hot)[:_MAX_RECORDERS]
    if len(chosen) < _MAX_RECORDERS:
        chosen += sorted(cold)[:_MAX_RECORDERS - len(chosen)]
    new = set(chosen)
    with _PRODUCING_LOCK:
        prev, _ALLOWED_CAMS = _ALLOWED_CAMS, new
    if prev != new:
        print(f"[VIDEO] recording {len(new)}/{len(seen)} cameras "
              f"(cap {_MAX_RECORDERS}; {len(hot)} on producing lines)", flush=True)


def _camera_recording_allowed(camera_id: str, base_dir: str, bindings=None) -> bool:
    """True if this camera should be recording: master video ON, its line not
    disabled, and it is inside the concurrent-recorder budget."""
    if not _video_enabled(base_dir):
        return False
    ln = _line_for_camera(camera_id, base_dir, bindings)
    if ln and ln in _disabled_lines(base_dir):
        return False
    if os.environ.get("VIDEO_ALWAYS_ON", "0") == "1":
        return True                       # escape hatch: record everything
    _refresh_allowed(base_dir)
    with _PRODUCING_LOCK:
        allowed = set(_ALLOWED_CAMS)
    if not allowed:
        return True                       # never worked out a set -> fail open
    return str(camera_id).strip() in allowed


_FFMPEG_CACHE: list = []


def _get_ffmpeg() -> str:
    """Path to the ffmpeg used for every recording, extract and clip render.

    2026-08-03 — PREFER THE SYSTEM BUILD.  This used to return imageio_ffmpeg's
    bundled static binary unconditionally.  That build (v7.0.2 here) SEGFAULTS
    decoding the HEVC the cameras produce: it dies at startup with SIGSEGV and
    writes nothing, so the clip endpoint saw a zero-frame output and answered
    "No video recorded for this cycle (camera stalled/offline)" — the operator's
    "video nahi aa rahi" — for cycles whose footage was sitting on disk, intact.
    Measured back-to-back on one .ts, identical arguments:
        imageio ffmpeg 7.0.2  -> Segmentation fault (rc 139), 0-byte output
        /usr/bin/ffmpeg 8.0.1 -> rc 0, 13.56 s clip
    Recording itself is `-c copy` (no decode) so it survived on either build,
    which is why only playback looked broken.
    A second, quieter win: the bundled binary ships WITHOUT ffprobe, and callers
    derive the ffprobe path from this one's directory — so every "does this clip
    contain video?" probe was failing open.  The system prefix has both.
    Falls back to the bundled binary when no system ffmpeg is installed.
    """
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE[0]
    import shutil as _shutil
    chosen = _shutil.which("ffmpeg")
    if not chosen:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            chosen = get_ffmpeg_exe()
        except Exception:
            chosen = "ffmpeg"
    _FFMPEG_CACHE.append(chosen)
    print(f"[PLC] ffmpeg: {chosen}")
    return chosen


# ── Hardware-accelerated H.264 encoder picker ─────────────────────────────
# 2026-05-19 — Offload H.264 re-encoding from CPU (libx264) to whatever
# hardware encoder is actually available on this box.  Probed ONCE at
# process start by encoding a tiny synthetic clip and checking the
# encoder didn't error out; result cached for the life of the process.
#
# Priority order is deliberate:
#   1. h264_nvenc  — NVIDIA NVENC (we have an RTX A2000 12 GB; once the
#                    driver is updated to >= 551.76 the API 12.2 mismatch
#                    goes away and this becomes the fastest path).
#   2. h264_qsv    — Intel Quick Sync on the iGPU (UHD 770 here).  Works
#                    on current driver, ~4-5× realtime, ~5% CPU per stream.
#   3. libx264     — CPU fallback (the current default).  Always works
#                    but pegs cores.
#
# Each encoder's "preset / quality" flag-set is bundled in the cache
# entry so call sites just splice it into the existing ffmpeg cmd list.
# Quality target is preserved across all three (~CRF 23 equivalent).
_HW_ENCODER_CACHE: list = []   # [(codec, [flags...])]

# 2026-08-02 (final) — RTSP transport policy: TCP first, UDP only as a rescue.
# The two transports fail in OPPOSITE ways on this plant network, and which one
# is better depends on how degraded the network is at that moment:
#   TCP  — lossless (retransmits), so the recording DECODES CLEANLY.  But when
#          the network is badly degraded the cameras accept the session and then
#          deliver almost nothing (measured: 59 ESTABLISHED sessions, ~0 Mbps at
#          the NIC, recorders at 0.1-0.5x realtime, fragmented .ts with gaps).
#   UDP  — always keeps flowing (0.9x realtime even when degraded), but lost RTP
#          packets damage the HEVC bitstream in place.  The .ts then looks fine
#          (right duration, right rate) yet the DECODER dies on it:
#          "CABAC_MAX_BIN", "cu_qp_delta out of range", "Could not find ref with
#          POC" — which is exactly the GREEN half-frame the operator reported,
#          and why a 26 s cycle played back as 1 s of video.
# Measured on cam .162 once the network recovered: TCP = 20.0s captured, 0 decode
# errors, 20.0s usable; UDP = 20.0s captured, 782 decode errors, corrupt.
# So: default TCP (clean), and let the starvation watchdog below drop an
# individual camera to UDP only when TCP genuinely stops delivering for it —
# lossy video beats no video, but only where TCP has actually failed.
# 2026-08-03 — OFF by default.  The UDP fallback was added because a starved TCP
# session captures below realtime, and UDP "keeps flowing".  It does — but what it
# writes is not usable video.  Measured back-to-back on cam_lock_bar_y17, same
# camera, same 40 s, `-c copy` straight off the wire:
#     TCP : 39.8 s captured (1.00x realtime),  821 decodable frames, 102 errors
#     UDP : 39.0 s captured (0.97x realtime),   43 decodable frames, 794 errors
# Both "keep up" on paper; only one contains video.  Lost RTP packets corrupt the
# HEVC bitstream in place, so the .ts has the right duration and rate while the
# decoder can only reconstruct a couple of frames per second — which is exactly
# the operator's "16.8 s ki cycle, 4 second ki video" and the green/blocky frames.
# The starvation readings that triggered these demotions (0.17-0.22x) were all
# taken while every recorder was re-connecting at once after a restart; in steady
# state the same cameras hold TCP at 1.00x.  So: stay on TCP, which is lossless.
# So UDP must be a LAST RESORT, not the response to a mild dip.  Turning it off
# entirely was measured too and is also wrong: the handful of cameras that truly
# cannot hold TCP then die and reconnect forever, and their gaps cost more than
# their corruption did.  Acceptance over 159 real cycles:
#     UDP as last resort : 121/159 clips full-length
#     UDP disabled       :  97/159 (recorder death gaps -> 416 / short)
# So: keep the fallback, but only for a camera that is severely starved (see
# _STARVE_RATIO), and re-probe TCP with an exponential backoff.
# Set VIDEO_ALLOW_UDP=0 to disable the fallback entirely.
# 2026-09-21 — default OFF: a UDP session that is not torn down cleanly keeps
# a single-session camera busy (the proven recovery recipe runs with 0).
_ALLOW_UDP_FALLBACK = os.environ.get("VIDEO_ALLOW_UDP", "0") == "1"

# Fraction of realtime below which a TCP session is considered hopeless.  Was
# 0.6, which demoted cameras measured at 0.54-0.58x — those were capturing CLEAN
# video at better than half rate, and `-vsync cfr` fills the rest on playback,
# so moving them to a corrupt UDP stream made their clips strictly worse.  0.35
# keeps them on TCP and reserves UDP for links that are genuinely collapsing.
_STARVE_RATIO = float(os.environ.get("VIDEO_STARVE_RATIO", "0.35"))


def _probe_duration(path: Optional[str]) -> Optional[float]:
    """Seconds of video actually inside a .ts/.mp4, or None if unreadable.
    Used by the recorder watchdog's starvation check (2026-08-02)."""
    if not path or not os.path.exists(path):
        return None
    ff = _get_ffmpeg()
    probe = os.path.join(os.path.dirname(ff), "ffprobe") if os.path.dirname(ff) else "ffprobe"
    if not os.path.exists(probe):
        probe = "ffprobe"
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=8)
        return float((r.stdout or "").strip().splitlines()[0])
    except Exception:
        return None


def _probe_encoder(ffmpeg: str, codec: str, extra: list) -> bool:
    """Encode 1 s of colour bars and verify the file actually has FRAMES.

    2026-08-12 — this used to accept `returncode == 0 and size > 0`, and that is
    exactly how a broken NVENC slipped through once before: with libcuda in a
    bad state ffmpeg still exited 0 and still wrote a 261-byte ftyp+moov stub
    with zero frames, so every per-cycle clip came out empty and nothing was
    logged.  A byte count cannot tell those apart — count the video packets.
    """
    import subprocess as _sp, tempfile as _tf, os as _os
    out = _tf.NamedTemporaryFile(suffix=".mp4", delete=False)
    out.close()
    try:
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", "smptebars=size=320x240:rate=10",
            "-t", "1",
            "-c:v", codec,
            *extra,
            "-pix_fmt", "yuv420p",
            "-an", out.name,
        ]
        r = _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=15)
        if r.returncode != 0 or _os.path.getsize(out.name) < 1024:
            return False
        probe = _os.path.join(_os.path.dirname(ffmpeg), "ffprobe")
        pr = _sp.run([probe, "-v", "error", "-select_streams", "v:0",
                      "-count_packets", "-show_entries", "stream=nb_read_packets",
                      "-of", "csv=p=0", out.name],
                     stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=15)
        n = (pr.stdout or b"").decode("utf-8", "ignore").strip()
        return n.isdigit() and int(n) > 0
    except Exception:
        return False
    finally:
        try: _os.remove(out.name)
        except Exception: pass


def _pick_hw_encoder() -> tuple:
    """Return (codec, [flags]) of the fastest available H.264 encoder.
    Result cached so probe runs only once per process."""
    if _HW_ENCODER_CACHE:
        return _HW_ENCODER_CACHE[0]

    ffmpeg = _get_ffmpeg()
    # (codec, probe_flags, runtime_flags)  — keep probe & runtime separate
    # because some encoders accept different parameter sets in headless probe
    # vs real RTSP pipeline.
    # 2026-05-27 — Operator: "video rendering speed tej kar".
    # Switched post-process clip encoding to FASTEST presets across all
    # encoders.  Visual quality drop is negligible at 720p RTSP source;
    # operator only watches each clip once for visual QA, and the cycle
    # extraction window is tight so faster encode = clip ready sooner.
    #   NVENC:   p1 (~2-3× faster than p4)
    #   QSV:     veryfast → faster
    #   libx264: superfast → ultrafast (already there)
    # 2026-08-01 — NVENC/QSV DISABLED on this box; extraction uses libx264
    # UNCONDITIONALLY.  Root cause of "video aa hi nahi rahi": h264_nvenc failed
    # at runtime with "Cannot load libcuda.so.1" (the NVIDIA CUDA runtime became
    # unavailable — the documented driver/API-mismatch issue noted above).  The
    # encoder is probed ONCE at process start and cached for the process life;
    # NVENC passed that early probe but libcuda later dropped, so EVERY per-cycle
    # extract silently produced an empty 261-byte MP4 (0 frames, stderr is
    # DEVNULL so nothing was logged).  libx264 (software) is proven-working here,
    # is already what the LIVE recorder picks on this GPU-less box, and per-cycle
    # extraction is capped at 8 parallel (_extract_sem) on 64 cores — trivial
    # CPU.  So there is no GPU dependency and no silent empty-clip failure mode.
    # Re-enable NVENC/QSV here ONLY after the NVIDIA driver is fixed (>=551.76)
    # and libcuda.so.1 loads — and validate with a REAL extract, not just the
    # synthetic probe, before trusting it (the probe passed while real encodes
    # were failing).
    # 2026-08-12 — an RTX A2000 12GB is now in this box, so NVENC is back on the
    # list — but PROBED, never assumed.  The note above records why it was
    # removed: the probe passed while libcuda was actually broken, and every
    # clip came out as a 261-byte empty MP4 with stderr swallowed.  So the probe
    # below encodes real frames and the result is only accepted if the file has
    # them; anything short of that falls through to libx264, which is what the
    # box ran on before and still works with no GPU at all.
    #
    # Worth it: a clip costs ~14 CPU-core-seconds on libx264, which is what has
    # kept the pre-render archive from covering more than ~60% of cycles.  NVENC
    # does it on dedicated silicon and leaves those cores to the 74 recorders.
    #
    # p4 (not p1): NVENC presets are not libx264 presets — p4 is the balanced
    # one and is still far faster than any CPU preset here.  -cq 26 keeps the
    # size in the same band as the CRF 26 the CPU path uses, so the tunnel
    # budget is unchanged.
    candidates = [
        ("h264_nvenc", ["-preset", "p4", "-cq", "26"],
                       ["-preset", "p4", "-cq", "26", "-tune", "ll"]),
        # CPU encoder (always works — no GPU/driver dependency)
        ("libx264",    ["-preset", "ultrafast", "-crf", "26"],
                       ["-preset", "ultrafast", "-crf", "26",
                        "-tune", "zerolatency"]),
    ]
    for codec, probe_flags, runtime_flags in candidates:
        if codec == "libx264" or _probe_encoder(ffmpeg, codec, probe_flags):
            print(f"[ENCODER] picked {codec!r} for H.264 re-encoding (FAST mode)")
            _HW_ENCODER_CACHE.append((codec, runtime_flags))
            return _HW_ENCODER_CACHE[0]
    # Truly unreachable — libx264 always passes — but be defensive.
    _HW_ENCODER_CACHE.append(("libx264", ["-preset", "ultrafast", "-crf", "26"]))
    return _HW_ENCODER_CACHE[0]


# ── Live-recorder encoder picker ─────────────────────────────────────────
# Same priority order as _pick_hw_encoder (NVENC > QSV > libx264) but
# returns flags tuned for LIVE continuous RTSP recording instead of
# post-process clip extraction:
#   • -tune ll / zerolatency          → no internal frame queue
#   • -bf 0                           → zero B-frames (every frame is
#                                       I- or P-, so `-c copy` cycle
#                                       extraction lands on a clean
#                                       boundary; B-frames would break
#                                       the keyframe-only seek math)
#   • -rc-lookahead 0 (NVENC)         → no encode-side buffering
#   • -rc vbr -cq 23 (NVENC)          → CRF-equivalent VBR quality
# Cached for the life of the process.
_LIVE_ENCODER_CACHE: list = []


def _pick_live_encoder() -> tuple:
    """Return (codec, [flags]) of the fastest H.264 encoder, with
    flags tuned for a low-latency RTSP→MPEG-TS recorder."""
    if _LIVE_ENCODER_CACHE:
        return _LIVE_ENCODER_CACHE[0]

    # 2026-09-12 — OPERATOR OVERRIDE.  The auto-probe below runs lazily on the
    # first transcode-needed camera; if the box is loaded at that instant the
    # NVENC probe times out and libx264 gets cached for the WHOLE process life —
    # pinning ~18 live HEVC/H.264 transcodes on the CPU (load ~46) which makes
    # every clip/video buffer for ~30 s.  NVENC is proven-good on this A2000
    # (clip pipeline uses it), so allow forcing it and skip the flaky probe.
    #   VIDEO_LIVE_ENCODER=h264_nvenc   → GPU (recommended here)
    #   VIDEO_LIVE_ENCODER=libx264      → CPU (old behaviour)
    _force = os.environ.get("VIDEO_LIVE_ENCODER", "").strip()
    _forced = {
        "h264_nvenc": ("h264_nvenc",
                       ["-preset", "p4", "-tune", "ll", "-rc", "vbr",
                        "-cq", "23", "-bf", "0", "-rc-lookahead", "0"]),
        "libx264":    ("libx264",
                       ["-preset", "ultrafast", "-tune", "zerolatency", "-crf", "23"]),
    }
    if _force in _forced:
        print(f"[ENCODER-LIVE] FORCED {_force!r} via VIDEO_LIVE_ENCODER "
              f"(probe skipped)")
        _LIVE_ENCODER_CACHE.append(_forced[_force])
        return _LIVE_ENCODER_CACHE[0]

    ffmpeg = _get_ffmpeg()
    candidates = [
        # NVENC live: p4 preset, ll tune, VBR with CQ23, no B-frames,
        # no encode-side lookahead.  Equivalent visual quality to the
        # libx264 superfast + zerolatency + crf23 we used before, but
        # ~8-10× cheaper on CPU and 2-3× lower encode latency.
        ("h264_nvenc",
         ["-preset", "p4", "-cq", "23"],     # probe — minimal
         ["-preset", "p4", "-tune", "ll",
          "-rc", "vbr", "-cq", "23",
          "-bf", "0", "-rc-lookahead", "0"]),
        # Intel QSV — fast preset, global_quality 23, no B-frames
        ("h264_qsv",
         ["-preset", "veryfast", "-global_quality", "23"],
         ["-preset", "veryfast", "-global_quality", "23",
          "-bf", "0", "-look_ahead", "0"]),
        # libx264 fallback — used on THIS box because there is no GPU.
        # 2026-07-22 — preset superfast -> ultrafast as part of the
        # CPU-saturation fix: on a GPU-less 64-core box running 45+ live
        # HEVC->H.264 transcodes, ultrafast cuts encoder CPU ~30 % with
        # near-identical file size (measured) — the headroom that stops the
        # stall-watchdog from fragmenting recordings into 2-3 s clips.
        ("libx264",
         ["-preset", "ultrafast", "-crf", "23"],
         ["-preset", "ultrafast", "-tune", "zerolatency", "-crf", "23"]),
    ]
    for codec, probe_flags, runtime_flags in candidates:
        if codec == "libx264" or _probe_encoder(ffmpeg, codec, probe_flags):
            print(f"[ENCODER-LIVE] picked {codec!r} for live RTSP recording")
            _LIVE_ENCODER_CACHE.append((codec, runtime_flags))
            return _LIVE_ENCODER_CACHE[0]
    _LIVE_ENCODER_CACHE.append(
        ("libx264", ["-preset", "ultrafast", "-tune", "zerolatency", "-crf", "23"]))
    return _LIVE_ENCODER_CACHE[0]


# Maximum realistic cycle duration (seconds).  Any cycle longer than this
# is almost certainly a stale-state artefact from a crashed previous session.
_MAX_CYCLE_SECONDS = 300   # 5 minutes


class PlcMonitor:
    def __init__(self, base_dir: str, csv_path: str, poll_interval: float = 0.03):
        # poll_interval was 0.3s — way too slow.  L108 / L109 OK/NG pulses
        # on the Mitsubishi PLC are typically 50-100 ms wide, so a 300 ms
        # poll missed roughly half the rising edges.  Symptom: the dashboard
        # showed a clean 52 s cycle, but plc_monitor recorded only 17 s
        # because it saw an intermediate (false) edge.  30 ms matches the
        # MES collector's poll rate and catches every pulse.
        self.base_dir      = base_dir
        self.csv_path      = csv_path
        self.poll_interval = poll_interval

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Live status — read by /api/plc-live-status
        self._last_values:       Dict[str, bool] = {}
        self._last_change_times: Dict[str, str]  = {}
        self._connected_plcs:    Dict[str, bool] = {}

        # Persistent pymcprotocol connections per PLC id
        self._plc_conns: Dict[str, pymcprotocol.Type4E] = {}
        self._plc_lock  = threading.Lock()
        # Protects _ensure_camera_recording from spawning two ffmpegs on
        # the same camera when the startup pre-starter and the watchdog
        # tick both call it within ~200 ms.  Symptom of the race was a
        # garbled / corrupted MP4 — both ffmpegs decoded the same RTSP
        # stream and produced corrupt H.264 frames.
        self._cam_spawn_lock = threading.Lock()
        # Back-off table — PLC id → unix ts before which we don't retry.
        # Mitsubishi PLCs allow a small number of simultaneous MC slots;
        # if another process (e.g. Phase2 collector) is already holding
        # the slot, we stop spamming "Cannot connect..." every 300 ms
        # and only retry every 60 s.
        self._plc_next_retry: Dict[str, float] = {}

        # Continuous per-camera MPEG-TS recorders:
        #   camera_id -> {"proc": Popen, "ts_file": str, "record_start": datetime}
        self._camera_workers: Dict[str, Dict] = {}

        # Per-cycle markers:
        #   machine_id -> {"camera_id", "ts_file", "record_start", "start_dt", "cycle_number"}
        self._video_workers: Dict[str, Dict] = {}

        # Machine metadata cache (zones/lines)
        self._machine_meta_cache: Optional[Dict[str, Dict]] = None
        self._machine_meta_ts: float = 0.0

        # Cap parallel ffmpeg cycle extractions.  Each extraction is a
        # full x264 re-encode that pegs one CPU core.  On a multi-machine
        # line (e.g. YNC-SS has machine_17 + machine_24 firing together)
        # unbounded threads were piling up and cascading into timeouts
        # where no video made it to disk.  2 in flight keeps the encoder
        # responsive and the Flask / RTSP feeds smooth.
        self._extract_sem = threading.Semaphore(8)   # 2026-07-31: 2->8 (64-core box)

        # Auto-discovered set of camera_ids that belong to a sub-machine
        # in the EOL admin (Phase2 mes_plc_configs.nf2_camera_id).
        # Populated by a background thread that polls Phase2 every 60 s
        # so admin can add a new sub-machine via UI alone — no JSON
        # edits to camera_config_bindings.json are needed.
        self._sub_cameras: set = set()
        self._sub_camera_lock = threading.Lock()
        threading.Thread(
            target=self._refresh_sub_cameras_loop,
            name="sub-cam-refresh", daemon=True,
        ).start()

        # 2026-08-03 — camera reachability prober (see the gate in
        # _ensure_camera_recording).  Cheap TCP connect to :554, never spawns
        # ffmpeg, runs off the poll loop so it can never stall recording.
        self._cam_unreachable: set = set()
        threading.Thread(
            target=self._probe_camera_reachability_loop,
            name="cam-reachability", daemon=True,
        ).start()

        # Pre-create the videos root (custom path from UI if set, else default).
        _resolve_videos_root(base_dir)

    def _probe_camera_reachability_loop(self) -> None:
        """Keep `self._cam_unreachable` current: a camera lands in the set when a
        plain TCP connect to its RTSP port fails, and leaves it the moment the
        port answers again.  Deliberately does NOT use ffmpeg — a doomed ffmpeg
        spawn costs orders of magnitude more than a 2 s socket probe, and it is
        those spawns (one per offline camera every 3 s) that were stealing CPU and
        camera-network bandwidth from the cameras that actually work."""
        import socket as _sock
        while not self._stop.is_set():
            try:
                cams = list_cameras(self.base_dir) or []
                # 2026-09-19 — Final Inspection cameras moved to a 2 s connect
                # timeout (operator).  Measured on the plant network: a camera
                # that has been idle takes ~1 s to accept its FIRST connection,
                # so the 0.4 s probe marked working cameras "unreachable" and
                # recording was refused for them.
                # 2026-09-20 — every camera now gets 2 s (operator).  Offline
                # cameras fail in ~1 s (host unreachable) and hung ones are
                # refused at once, so a full round still takes ~30 s.
                unreachable = set()
                for c in cams:
                    if self._stop.is_set():
                        break
                    cid = str(c.get("id") or "").strip()
                    ip  = str(c.get("ip") or "").strip()
                    if not cid or not ip:
                        continue
                    port = int(c.get("port") or 554)
                    s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                    s.settimeout(2.0)
                    try:
                        s.connect((ip, port))
                    except Exception:
                        unreachable.add(cid)
                    finally:
                        try: s.close()
                        except Exception: pass
                prev = getattr(self, "_cam_unreachable", set())
                self._cam_unreachable = unreachable
                came_back = prev - unreachable
                went_away = unreachable - prev
                if came_back:
                    print(f"[PLC] Reachability: {len(came_back)} camera(s) back online "
                          f"— recording resumes immediately.")
                if went_away:
                    print(f"[PLC] Reachability: {len(went_away)} camera(s) unreachable "
                          f"— recorder spawns paused for them ({len(unreachable)} total "
                          f"of {len(cams)}); the live cameras keep the bandwidth.")
            except Exception as exc:
                print(f"[PLC] reachability probe error: {exc}", flush=True)
            self._stop.wait(30)

    # ─── Thread lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._reset_stale_machine_states()
        # Prestart used to run synchronously at import time: every bound
        # camera spawned a blocking FFmpeg → RTSP handshake (2–10 s each)
        # so Flask couldn't accept requests for ~10–50 s on boot.
        # Move it to its own thread — recorders warm up in the background
        # while Flask is already serving. First few PLC triggers before
        # warm-up will fall through the existing "TS not ready" guard.
        threading.Thread(
            target=self._prestart_camera_recorders,
            name="cam-prestart", daemon=True,
        ).start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plc-monitor", daemon=True)
        self._thread.start()

    def _prestart_camera_recorders(self) -> None:
        """Start continuous TS recorders for all cameras bound in config.
        This ensures the TS file already has content when the first PLC cycle fires,
        avoiding the short-video problem caused by RTSP connection startup delay."""
        import glob as _glob
        try:
            bindings = list_bindings(self.base_dir)
        except Exception:
            return
        # 2026-05-18 — KILL ZOMBIE FFMPEG.
        # Previous CMS process may have left orphan ffmpeg recorders alive
        # (Windows doesn't kill child processes when parent dies unless
        # job-objects are configured).  Those zombies keep writing to old
        # TS files, double-recording from each camera and blocking the
        # OS file lock so subsequent os.remove() fails silently.  Net
        # result: stale TS files for cycle-clip extraction + cam_worker
        # pointing at the WRONG TS file.
        # Cleanup: enumerate all ffmpeg.exe processes whose command line
        # references our videos folder, kill them, then remove TS files.
        try:
            import psutil as _ps
            videos_abs_for_kill = _resolve_videos_root(self.base_dir)
            videos_marker = os.path.normcase(os.path.abspath(videos_abs_for_kill))
            killed_n = 0
            for p in _ps.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (p.info.get("name") or "").lower()
                    if not name.startswith("ffmpeg"):
                        continue
                    cmd = p.info.get("cmdline") or []
                    joined = os.path.normcase(" ".join(cmd))
                    if videos_marker in joined:
                        p.kill()
                        killed_n += 1
                except Exception:
                    continue
            if killed_n:
                print(f"[PRESTART] killed {killed_n} zombie ffmpeg "
                      f"processes from previous session")
                import time as _time
                _time.sleep(1.0)   # give OS a moment to release file locks
        except ImportError:
            # psutil missing → best effort; still try to remove TS files,
            # any locked ones will simply skip below.
            pass
        except Exception as _exc:
            print(f"[PRESTART] zombie-kill skipped: {_exc}")

        # Clean up orphaned TS files left by previous sessions.
        # 2026-05-29 — CRITICAL FIX.  The previous `os.remove(old_ts)`
        # unconditionally nuked EVERY cam_*.ts at startup, including
        # rotated files containing historical cycle clips the
        # operator still wants to scrub.  After an api_server restart,
        # clicking any earlier cycle returned 503/404 because its TS
        # file no longer existed.  New rule: only delete TS files
        # older than TS_KEEP_GRACE_SEC (matches _cleanup_old_ts) so
        # rotated files within the retention window are preserved.
        # Empty/zero-byte ffmpeg-launch-fail stubs are still removed.
        import time as _time
        TS_KEEP_GRACE_SEC = TS_KEEP_SEC      # 48 h footage hold (was 3600)
        videos_abs = _resolve_videos_root(self.base_dir)
        _now_clean = _time.time()
        for old_ts in _glob.glob(os.path.join(videos_abs, "cam_*.ts")):
            try:
                _sz = os.path.getsize(old_ts)
                _age = _now_clean - os.path.getmtime(old_ts)
            except OSError:
                continue
            # Always remove tiny stubs (failed RTSP connect leaves <64KB).
            if _sz < 65536:
                try: os.remove(old_ts)
                except OSError: pass
                continue
            # Keep recent rotated TS files so historical clip lookups work.
            if _age < TS_KEEP_GRACE_SEC:
                continue
            try:
                os.remove(old_ts)
            except OSError:
                pass
        # Clean up orphaned _pending_*.mp4 files — these are stale
        # extraction temp files that never got renamed because their
        # ffmpeg was killed by a timeout and the subsequent os.remove()
        # was blocked by a Windows file lock.  They waste disk and show
        # up confusingly in the videos/ tree.
        pending_removed = 0
        for old_pending in _glob.glob(
                os.path.join(videos_abs, "**", "_pending_*.mp4"),
                recursive=True):
            try:
                os.remove(old_pending)
                pending_removed += 1
            except OSError:
                pass
        if pending_removed:
            print(f"[PLC] Startup cleanup: removed {pending_removed} orphan _pending_ files")
        started: set = set()
        for b in bindings:
            cid = str(b.get("camera_id", "")).strip()
            if cid and cid not in started:
                self._ensure_camera_recording(cid)
                started.add(cid)
                print(f"[PLC] Pre-started TS recorder for camera {cid}")

    def _reset_stale_machine_states(self) -> None:
        """
        On startup: if any machine's JSON state shows recording=True but the
        start_time is older than _MAX_CYCLE_SECONDS, the previous Flask session
        crashed mid-cycle.  Reset to avoid computing a huge duration when the
        next PLC trigger fires.
        """
        now = datetime.now()
        try:
            all_states = load_state(self.base_dir)
        except Exception:
            return
        for machine_id, state in all_states.items():
            if not state.get("recording"):
                continue
            start_str = state.get("start_time", "")
            if not start_str:
                end_cycle(machine_id, self.base_dir)
                print(f"[PLC] Reset stale recording state for {machine_id} (no start_time)")
                continue
            try:
                start_dt = datetime.fromisoformat(start_str)
                elapsed  = (now - start_dt).total_seconds()
                if elapsed > _MAX_CYCLE_SECONDS:
                    end_cycle(machine_id, self.base_dir)
                    print(
                        f"[PLC] Reset stale recording state for {machine_id} "
                        f"(was recording for {elapsed:.0f}s since {start_str})"
                    )
            except (ValueError, TypeError):
                end_cycle(machine_id, self.base_dir)
                print(f"[PLC] Reset stale recording state for {machine_id} (bad start_time)")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._plc_lock:
            for conn in self._plc_conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._plc_conns.clear()
        # Stop continuous camera recorders
        for cam in list(self._camera_workers.values()):
            self._kill_cam(cam)
        self._camera_workers.clear()
        self._video_workers.clear()

    # ─── Main poll loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                print(f"[PLC] Poll loop error: {exc}")
            self._stop.wait(self.poll_interval)

    def _poll_once(self) -> None:
        # ──────────────────────────────────────────────────────────────────────
        # PLC bit-polling has moved to the MES collector
        # (Phase2/collector_engine.py).  This monitor used to open its OWN
        # MC4E TCP connection to 192.168.30.150:5002 and read the same
        # L108/L109 bits the collector reads; Mitsubishi Q-series only
        # accept a couple of simultaneous TCP clients on that port, so
        # the two services fought for the slot and ~half the rising edges
        # silently dropped on whichever side got squeezed.
        #
        # The collector is now the only PLC client, and on every L108/L109
        # rising edge it POSTs us /api/plc-edge.  That handler in
        # api_server.py calls _trigger_binding() with the SAME flow this
        # loop used to trigger directly.  Net effect: zero PLC packets
        # from CMS, zero contention, no missed edges.
        #
        # We KEEP the camera-recorder watchdog (below) running, because it's
        # responsible for spawning ffmpeg per camera and rolling .ts files —
        # nothing to do with PLC polling.
        # ──────────────────────────────────────────────────────────────────────

        bindings = list_bindings(self.base_dir)

        # ── Per-line + master VIDEO gate ──────────────────────────────────────
        # Stop recorders for any camera whose LINE has video turned OFF (per-line
        # toggle from the MES Production Panel → video_config.json
        # video_disabled_lines) or when the master switch is OFF.  This sheds the
        # ffmpeg/NVENC/disk load for those lines.  _ensure_camera_recording()
        # also refuses to (re)spawn disabled cameras so the watchdog below won't
        # bring them back.  Line counting is the MES collector's job — untouched.
        for cid in list(self._camera_workers.keys()):
            if not _camera_recording_allowed(cid, self.base_dir, bindings):
                cam = self._camera_workers.pop(cid, None)
                if cam:
                    try:
                        self._kill_cam(cam)
                    except Exception:
                        pass
                    print(f"[VIDEO] recorder {cid} stopped (line/master OFF)")

        # ── Camera recorder watchdog ──────────────────────────────────────────
        # Restart any dead recorders every ~10 polls (≈ 3 seconds).
        # If a recorder dies within 10 s of starting (i.e. the camera is
        # offline / RTSP unreachable) we exponentially back off retries
        # up to 5 min, and log only on state transitions — otherwise the
        # console was flooded with "died — restarting" every 3 s forever
        # and every attempt spawned a new orphan TS file.
        self._watchdog_tick = getattr(self, "_watchdog_tick", 0) + 1
        if not hasattr(self, "_cam_fail_state"):
            self._cam_fail_state: Dict[str, Dict] = {}
        if not hasattr(self, "_cam_transport"):
            # 2026-08-02 — per-camera RTSP transport ("tcp" default → "udp"
            # fallback), driven by the adaptive-fallback logic below.
            self._cam_transport: Dict[str, str] = {}
        if not hasattr(self, "_cam_live_probe_at"):
            # cid -> last epoch we ran the live starvation probe (throttle 60 s)
            self._cam_live_probe_at: Dict[str, float] = {}
        st_live_last = self._cam_live_probe_at
        # 2026-08-02 — periodically give a UDP-demoted camera another shot at TCP.
        # TCP is the only transport that decodes cleanly here, but the camera
        # network can only carry ~14 concurrent TCP streams before the rest
        # starve, so most cameras get demoted to (lossy) UDP.  Capacity frees up
        # whenever cameras go offline / lines stop, so retry every 10 min: a
        # camera that can now hold TCP gets clean video back on its own, and one
        # that still can't is demoted again within ~50 s by the checks below.
        if not hasattr(self, "_cam_tcp_retry_at"):
            self._cam_tcp_retry_at: Dict[str, float] = {}
        # 2026-08-03 — how many times TCP has been tried and failed for a camera.
        # Drives the backoff below so a link that is persistently too thin stops
        # being re-probed every 10 minutes.
        if not hasattr(self, "_cam_tcp_fails"):
            self._cam_tcp_fails: Dict[str, int] = {}
        # 2026-08-03 — `now_ts` MUST be read here, before the TCP-retry sweep
        # below uses it.  It used to be assigned only inside the watchdog block
        # further down, so this loop raised NameError on every single poll:
        #   [PLC] Poll loop error: cannot access local variable 'now_ts'
        # The exception aborted the poll before the watchdog ran, so recorders
        # that died were never respawned — the box drifted from 65 recorders to
        # 24 and every cycle on the missing cameras returned 404 "no video".
        now_ts = time.time()
        for _cid, _tr in list(self._cam_transport.items()):
            if _tr != "udp":
                continue
            # 2026-08-03 — BACK OFF, AND DON'T KILL A WORKING RECORDER.
            # This used to re-probe TCP every 10 minutes and KILL the running
            # (healthy, ~1.0x realtime) UDP recorder to do it.  On a link that
            # is persistently too thin the probe always failed, so each camera
            # paid two reconnect gaps every 10 minutes — once for the retry,
            # once for the re-demotion — and the reconnect costs 20-90 s of
            # recording.  Measured: 21 demotions in 9 minutes, and the cycles
            # landing in those gaps came back short or missing.
            # Now: the retry interval doubles with each failed attempt (10 min →
            # 4 h), and instead of killing we just restore the TCP default so it
            # takes effect on the recorder's NEXT natural restart.  A link that
            # genuinely recovers is still picked up; one that cannot hold TCP
            # settles on UDP and stops churning.
            _fails = self._cam_tcp_fails.get(_cid, 1)
            _wait  = min(600 * (2 ** max(0, _fails - 1)), 14400)   # cap 4 h
            if now_ts - self._cam_tcp_retry_at.get(_cid, 0) < _wait:
                continue
            self._cam_tcp_retry_at[_cid] = now_ts
            self._cam_transport.pop(_cid, None)          # back to TCP default
            print(f"[PLC] Camera {_cid}: will retry TCP on next recorder restart "
                  f"(attempt {_fails + 1}, next re-check in {_wait / 60:.0f} min) — "
                  f"clean video if the link can now hold it.")
        if self._watchdog_tick >= 10:
            self._watchdog_tick = 0
            camera_ids = {str(b.get("camera_id", "")).strip() for b in bindings}
            for cid in camera_ids:
                if not cid:
                    continue
                cam = self._camera_workers.get(cid)
                if cam and cam["proc"].poll() is not None:
                    # Measure how long this recorder survived before dying.
                    started_at = cam.get("record_start")
                    alive_s = 0.0
                    if started_at:
                        try:
                            alive_s = (datetime.now() - started_at).total_seconds()
                        except Exception:
                            pass
                    del self._camera_workers[cid]

                    st = self._cam_fail_state.setdefault(
                        cid, {"fails": 0, "next_try": 0.0, "announced": False})

                    # 2026-08-02 — STARVATION → UDP fallback.  The quick-death
                    # rule below only catches a camera whose TCP session dies
                    # outright.  The nastier failure is a session that STAYS UP
                    # but is starved: measured on cam .162 the TCP recorder ran
                    # at 0.22-0.27x realtime (36 s of wall clock produced 8 s of
                    # video) while UDP on the same camera ran 0.91x.  Because it
                    # survived >30 s it was treated as "a real recording that
                    # dropped" and was restarted on TCP forever — so the .ts files
                    # were tiny fragments with GAPS between them, and any cycle
                    # landing in a gap returned 404 ("No video with supported
                    # format" on the wallboard).  Detect it: compare the video
                    # actually captured against how long the recorder was alive;
                    # below 60 % the transport can't keep up → switch to UDP.
                    # Only ffprobes on a death event, so the cost is negligible.
                    # 2026-08-03 — MEASURE AGAINST STREAMING TIME, NOT UPTIME.
                    # `alive_s` counts from the moment ffmpeg was SPAWNED, but the
                    # RTSP open (connect, negotiate, wait for the first keyframe)
                    # takes 20-90 s on these cameras and captures nothing.  Divided
                    # by that, even a recorder streaming at a clean 1.0x scored
                    # 0.3-0.5x and was demoted to UDP — which is lossy by design, so
                    # the "fix" damaged the HEVC bitstream and cost another kill +
                    # reconnect gap.  `write_start` is stamped by _detect_write_start
                    # the moment real bytes appear, so it is the honest denominator.
                    # No write_start at all means the session never delivered a
                    # frame: that is an unreachable/refused camera, not a starved
                    # transport, and switching it to UDP would help nothing.
                    _ws = cam.get("write_start")
                    _stream_s = 0.0
                    if _ws:
                        try:
                            _stream_s = (datetime.now() - _ws).total_seconds()
                        except Exception:
                            _stream_s = 0.0
                    if (_ALLOW_UDP_FALLBACK and _stream_s >= 20
                            and self._cam_transport.get(cid, "tcp") == "tcp"):
                        _cap = _probe_duration(cam.get("ts_file"))
                        if _cap is not None and _cap < _stream_s * _STARVE_RATIO:
                            self._cam_transport[cid] = "udp"
                            st["fails"] = 0
                            # Feeds the TCP re-check backoff above.
                            self._cam_tcp_fails[cid] = \
                                self._cam_tcp_fails.get(cid, 0) + 1
                        elif _cap is not None:
                            # TCP held up this time — forget the past failures so
                            # a camera whose link recovered goes back to the fast
                            # 10-minute re-check instead of staying on the long
                            # backoff forever.
                            self._cam_tcp_fails.pop(cid, None)
                            print(f"[PLC] Camera {cid}: TCP starved "
                                  f"({_cap:.0f}s captured in {_stream_s:.0f}s streaming = "
                                  f"{_cap / max(_stream_s, 1):.2f}x realtime) — "
                                  f"switching to UDP transport (sustained but lossy).")

                    if alive_s >= 30:
                        # Real recording that dropped → reset back-off, retry now.
                        st["fails"]    = 0
                        st["next_try"] = 0
                        st["announced"] = False
                        print(f"[PLC] Camera recorder {cid} died — restarting "
                              f"(was up {alive_s:.0f}s)")
                        self._ensure_camera_recording(cid)
                    else:
                        # Died almost immediately → camera probably offline.
                        # 2026-05-29 — Operator instruction: NO exponential
                        # backoff.  Hammer RTSP every 3 s so recording
                        # resumes the moment the camera responds again.
                        # Announce once on first failure, then every 60 s
                        # while still offline so the console stays readable
                        # without going silent.
                        st["fails"] += 1
                        # 2026-08-02 — adaptive transport fallback.  UDP is the
                        # default here (see _ensure_camera_recording).  If a
                        # camera's UDP session keeps dying in <30 s, give TCP a
                        # try for that one camera before writing it off as
                        # offline — a few cameras do prefer TCP.  A genuinely
                        # offline camera fails on both, so this is harmless.
                        if (_ALLOW_UDP_FALLBACK and st["fails"] >= 3
                                and self._cam_transport.get(cid, "tcp") == "tcp"):
                            self._cam_transport[cid] = "udp"
                            st["fails"] = 0          # give UDP a fresh set of tries
                            print(f"[PLC] Camera {cid}: TCP wouldn't hold — "
                                  f"trying UDP transport.")
                        cool_down = 3   # fixed retry interval — no backoff
                        st["next_try"] = now_ts + cool_down
                        if not st["announced"]:
                            print(f"[PLC] Camera {cid} appears OFFLINE "
                                  f"(died after {alive_s:.0f}s). "
                                  f"Retrying every {cool_down}s — recording "
                                  f"will resume as soon as camera responds.")
                            st["announced"]      = True
                            st["last_announce"]  = now_ts
                        elif now_ts - st.get("last_announce", 0) >= 60:
                            print(f"[PLC] Camera {cid} still OFFLINE after "
                                  f"{st['fails']} attempts — still retrying every {cool_down}s.")
                            st["last_announce"] = now_ts
                elif cam is not None:
                    # 2026-08-02 — the LIVE starvation probe that used to sit here
                    # was removed: it shelled out to ffprobe (up to 8 s) from
                    # inside the PLC polling thread, once per camera per minute.
                    # With ~60 cameras that can block the poll loop for tens of
                    # seconds, which DELAYS respawning dead recorders — the exact
                    # gap-in-recording problem it was meant to help.  The
                    # on-death starvation check (above) gives the same signal and
                    # only runs when a recorder actually exits, so it is cheap.
                    pass
                elif cam is None:
                    # No recorder active — either just never started, or
                    # waiting in back-off. Retry if cool-down elapsed.
                    st = self._cam_fail_state.get(cid)
                    if st and now_ts < st.get("next_try", 0):
                        continue
                    if self._ensure_camera_recording(cid):
                        # Successful spawn (doesn't yet mean RTSP connected)
                        if st:
                            st["announced"] = False

            # ── ZOMBIE REAPER (2026-05-18, fixed 2026-05-19) ──────────
            # Walk every ffmpeg process on the box; any whose command
            # line references "cam_<id>" but does NOT match the PID we
            # currently believe is its recorder gets killed.  Without
            # this, a missed `_kill_cam` (timeout, ignored SIGTERM, etc.)
            # leaves multiple ffmpegs writing to the same TS file,
            # corrupting both and producing 0-byte cycle clips.
            #
            # 2026-05-19 — CRITICAL BUG FIX.
            # The previous match condition (`cam_<cid>_` in cmdline) was
            # too greedy.  Cycle EXTRACTOR ffmpegs ALSO contain
            # `cam_<cid>_` because they read from `cam_<cid>_<ts>.ts`
            # as INPUT.  The reaper was killing every cycle extraction
            # mid-flight, producing:
            #   [PLC] ZOMBIE-REAPER killing extra ffmpeg PID=X for cam_Y
            #   [PLC] Extraction error: [WinError 5] Access is denied
            #   [PLC] Cycle #NNN done ... video=none
            # Result: ZERO cycle clips generated for 6+ hours.
            #
            # Fix: only reap ffmpegs that are RECORDERS — they have
            # `-i rtsp://` in their cmdline.  Extractors have `-i
            # <ts_file>` (a file path, not an rtsp URL).  This single
            # extra check makes the reaper kill ONLY real duplicate
            # recorders and leaves cycle extractors alone.
            try:
                import psutil as _ps
                live_pids = {cid: w["proc"].pid for cid, w
                              in self._camera_workers.items()}
                for p in _ps.process_iter(["pid", "name", "cmdline"]):
                    try:
                        name = (p.info.get("name") or "").lower()
                        if not name.startswith("ffmpeg"):
                            continue
                        cmd = " ".join(p.info.get("cmdline") or [])
                        # CRITICAL: only consider RECORDERS (rtsp input).
                        # Extractors read from a .ts file, not rtsp.
                        if "-i rtsp://" not in cmd:
                            continue
                        # Find which camera this ffmpeg is for
                        for cid in camera_ids:
                            if not cid:
                                continue
                            # Match "cam_<cid>_" in the cmdline (the TS
                            # filename our recorder always writes)
                            if f"cam_{cid}_" not in cmd:
                                continue
                            live_pid = live_pids.get(cid)
                            # 2026-05-21 — Don't kill when no live recorder
                            # is tracked.  Reaper was killing the very
                            # ffmpeg we just spawned (during recovery
                            # attempts after a launch error), creating
                            # an infinite kill-respawn loop.  Earlier
                            # log:  "ZOMBIE-REAPER killing extra ffmpeg
                            #         PID=37416 ... (live PID=None)".
                            # If live_pid is None we have no recorder
                            # to defend — leave any orphans alone, the
                            # next launch attempt will pick its own
                            # process up as the canonical live one.
                            if live_pid is None:
                                break
                            if live_pid != p.info["pid"]:
                                print(f"[PLC] ZOMBIE-REAPER killing extra "
                                      f"ffmpeg PID={p.info['pid']} for "
                                      f"{cid} (live PID={live_pid})")
                                p.kill()
                            break
                    except Exception:
                        continue
            except ImportError:
                pass        # psutil missing → reaper offline (best-effort)
            except Exception as _zexc:
                print(f"[PLC] zombie reaper warning: {_zexc}")
        # ─────────────────────────────────────────────────────────────────────

        # ── Shift-boundary cleanup for SUB cameras ───────────────────────────
        # User requirement: continuous TS for SUB cameras is scoped to one
        # shift only.  At each shift start (from shifts.json) we stop the
        # ffmpeg, delete the .ts file, and let the watchdog above respawn
        # a fresh recorder on the next tick.  MAIN cameras are skipped
        # entirely — their per-cycle MP4 (barcode-named) is the long-term
        # artefact and the rolling TS must keep flowing across the boundary
        # so we don't lose an in-flight cycle.
        try:
            self._check_shift_boundary(bindings)
        except Exception as exc:
            print(f"[PLC] shift-boundary check error: {exc}")
        # ─────────────────────────────────────────────────────────────────────

        # PLC bit polling intentionally removed — see big comment at the top
        # of this method.  Edges now arrive via the /api/plc-edge webhook
        # from the MES collector, which calls _trigger_binding() the same
        # way this loop used to.  Mark every CMS PLC as "connected" so
        # the admin dashboard's bit-watch widget keeps showing OK status
        # — connectivity is now a property of the collector, not us.
        for plc in (list_plcs(self.base_dir) or []):
            self._connected_plcs[str(plc.get("id", "")).strip()] = True

        # `bindings` is loaded above so the camera-recorder watchdog has
        # the up-to-date binding list.  We don't iterate over it here —
        # nothing to do until a webhook fires.
        return

    # ─── Part code from PLC word registers ──────────────────────────────────

    # Node-RED flow reads D5004 with 13 registers.  Each 16-bit register holds
    # two ASCII characters in byte-reversed order (low byte first, high byte second).
    _PART_CODE_ADDR = "D5004"
    _PART_CODE_LEN  = 13          # 13 registers → up to 26 ASCII chars

    def _read_part_code(self, plc_id: str) -> str:
        """Read part-code string from PLC word registers, matching the Node-RED
        byte-reversed ASCII conversion."""
        conn = self._plc_conns.get(plc_id)
        if conn is None:
            return ""
        try:
            regs = conn.batchread_wordunits(
                headdevice=self._PART_CODE_ADDR,
                readsize=self._PART_CODE_LEN,
            )
        except Exception as exc:
            print(f"[PLC] Part code read error ({plc_id}): {exc}")
            return ""
        # Byte-reversed ASCII: low byte first, high byte second (same as Node-RED)
        chars = []
        for reg in regs:
            high_byte = reg & 0xFF           # low byte of register → first char
            low_byte  = (reg >> 8) & 0xFF    # high byte of register → second char
            if high_byte > 0:
                chars.append(chr(high_byte))
            if low_byte > 0:
                chars.append(chr(low_byte))
        part_code = "".join(chars).strip().strip("\x00")
        if part_code:
            print(f"[PLC] Part code: {part_code!r}")
        return part_code

    # ─── Trigger binding (rising edge handler) ────────────────────────────────

    def _trigger_binding(self, binding: Dict,
                          edge_dt: Optional[datetime] = None) -> None:
        """When MES sends an edge webhook it carries the PLC-accurate
        timestamp (`epoch_ms`/`iso`).  The /api/plc-edge handler converts
        that to a datetime and passes it in here so cycle duration math
        uses the true PLC pulse times instead of HTTP-arrival-at-CMS,
        which can jitter by tens of seconds under load and was producing
        videos shorter than the chart's CT (52 s cycle → 32 s clip).
        Manual / legacy callers without an edge timestamp fall back to
        wall clock."""
        edge_dt = edge_dt or datetime.now()

        # Video gate — when this line's video (or the master switch) is OFF,
        # skip all cycle recording/extraction.  /api/plc-edge still returns 200
        # to the MES collector; only the video side is skipped here, so line
        # counting (MES-side) is unaffected.
        _bind_line = str(binding.get("line_name", "") or "").strip()
        if not _video_enabled(self.base_dir) or (_bind_line and _bind_line in _disabled_lines(self.base_dir)):
            return

        machine_id = str(binding.get("machine_id", "")).strip()
        camera_id  = str(binding.get("camera_id",  "")).strip()
        if not machine_id:
            return

        ensure_metadata_file(self.csv_path)
        current       = get_machine_state(machine_id, self.base_dir)
        next_cycle    = get_next_cycle_num(self.csv_path)
        current_cycle = int(current.get("cycle_number") or 0)
        if current_cycle >= next_cycle:
            next_cycle = current_cycle + 1

        meta = self._get_machine_meta(machine_id)
        plc_id = str(binding.get("plc_id", "")).strip()

        if current.get("recording"):
            # ── Cycle ENDED ───────────────────────────────────────────────────
            start_iso = current.get("start_time", "")
            end_dt    = edge_dt
            try:
                start_dt = datetime.fromisoformat(start_iso) if start_iso else end_dt
            except ValueError:
                start_dt = end_dt
            duration_s = max(0, int((end_dt - start_dt).total_seconds()))
            # 2026-05-14 diagnostic — operator reports 2 s videos for
            # 28 s cycles.  Log the exact PLC times we used so we can
            # see whether duration_s is wrong (start_dt/end_dt mismatch)
            # or downstream extraction is misreading the clip.
            print(f"[PLC-DURATION] {machine_id} #{current_cycle}: "
                  f"start_iso={start_iso!r} -> start_dt={start_dt.isoformat()} "
                  f"end_dt={end_dt.isoformat()} delta={duration_s}s "
                  f"edge_dt_was={edge_dt.isoformat()}")

            # Read part code from PLC at cycle-end (freshest value).
            # When called from /api/plc-edge, the webhook handler stuffs
            # the part_code MES already read into `self._next_part_code`
            # for the matching plc_id — we use that and skip a redundant
            # PLC read (CMS no longer holds a TCP slot, so a direct read
            # here would fail anyway).
            #
            # Two key shapes: legacy plc_id (from plcs.json) and the new
            # `ip:<addr>` key written by the IP-match path in /api/plc-edge.
            # Try both before falling back to a PLC read.
            ppc = getattr(self, "_next_part_code", {}) or {}
            override = ppc.pop(plc_id, None)
            if override is None:
                # New MES-driven binding carries plc_ip — try the IP key
                plc_ip_key = f"ip:{str(binding.get('plc_ip', '')).strip()}"
                if plc_ip_key != "ip:":
                    override = ppc.pop(plc_ip_key, None)
            if override is not None:
                part_code = override
            else:
                part_code = self._read_part_code(plc_id) if plc_id else ""

            # Pop cycle marker immediately (non-blocking)
            worker = self._video_workers.pop(machine_id, None)

            # Start next cycle + new marker right away.  Use the PLC-side
            # edge_dt so the next cycle's start_time matches the actual
            # pulse moment (this becomes the *start* of the upcoming cycle
            # whose duration we'll compute on the NEXT edge).
            state = rotate_cycle(machine_id, next_cycle, self.base_dir,
                                  start_time=edge_dt)
            print(f"[PLC] Started next cycle #{state.get('cycle_number')} for {machine_id}")
            self._start_video(machine_id, camera_id, next_cycle, marker_dt=edge_dt)

            # Per-binding extract policy.
            #   1) explicit binding flag wins (admin override)
            #   2) else auto-detect: if the camera is configured as
            #      `nf2_camera_id` for any Phase2 sub-machine, skip MP4
            #      and let /api/submachine/clip serve on-demand TS slices.
            #   3) else default = per-cycle MP4 (main-machine behaviour)
            if "extract_per_cycle" in binding:
                extract_per_cycle = bool(binding["extract_per_cycle"])
            else:
                with self._sub_camera_lock:
                    is_sub_cam = camera_id in self._sub_cameras
                extract_per_cycle = not is_sub_cam

            # Extract cycle video + write CSV in background
            t = threading.Thread(
                target=self._finalize_cycle,
                args=(worker, current_cycle, start_dt, end_dt,
                      duration_s, machine_id, meta, part_code,
                      extract_per_cycle),
                daemon=True,
            )
            t.start()

        else:
            # ── First trigger — mark cycle start ──────────────────────────────
            state = start_cycle(machine_id, next_cycle, self.base_dir,
                                 start_time=edge_dt)
            print(f"[PLC] Started cycle #{state.get('cycle_number')} for {machine_id} (first trigger)")
            self._start_video(machine_id, camera_id, next_cycle, marker_dt=edge_dt)

    # ─── Finalize cycle (background thread) ──────────────────────────────────

    def _finalize_cycle(
        self,
        worker: Optional[Dict],
        cycle_number: int,
        start_dt: datetime,
        end_dt: datetime,
        duration_s: int,
        machine_id: str,
        meta: Dict,
        part_code: str = "",
        extract_per_cycle: bool = True,
    ) -> None:
        """Extract cycle clip from the MPEG-TS rolling file, then write CSV row.
        Cycle = previous OK/NG pulse → next OK/NG pulse, full duration."""
        file_rel = ""
        cycle_duration_s = (end_dt - start_dt).total_seconds()
        # 2026-05-23 — ALL CAPS REMOVED PER OPERATOR DEMAND.
        # "video me koii cap nahi rahegi, jitni der ki cycle utni ki
        # video — phir tu sabse pehle badi cycles ke liye cap kyu lagata
        # hai".  Every prior clip rule (60s, smart 4×ideal_ct, etc.)
        # caused short videos for legitimate long cycles.  No more
        # length tampering: cycle is whatever (end_dt - start_dt) says.
        # If the duration looks insane that's a count/edge problem to
        # fix upstream — NOT something to mask by cutting the clip.
        if not extract_per_cycle:
            # Sub-machine binding — keep the shift-long TS rolling but
            # don't write a per-cycle MP4. The Phase2 sub-machine UI
            # trims a slice from the TS via /api/submachine/clip on click.
            print(f"[PLC] Cycle #{cycle_number} ({machine_id}) "
                  f"extract_per_cycle=false → skipping MP4 (TS continues)")
        elif worker and worker.get("ts_file"):
            # Wait for the encoder to flush end_dt content into the TS file.
            # 2026-07-31 — cut 25s -> 6s.  The recorders now use -flush_packets
            # (real-time flush) and this runs on Linux (coherent FS cache, not
            # Windows), so the .ts already holds everything up to end_dt within
            # <1s; 6s is an ample margin for the final GOP.  The old 25s held
            # EVERY main-cycle finalize thread alive for 25s (hundreds of sleeping
            # threads) AND added 25s of latency before the MP4 was ready — the
            # exact "video 5-15 min baad band ho jaati" + latency regression.
            time.sleep(6)
            # Throttle concurrent ffmpeg extractions. Raised 2 -> 8 (2026-07-31):
            # this 64-core / 232 GB box was bottlenecked at 2 slots, so main-cycle
            # (final-inspection) MP4 extractions queued up, the finalize threads
            # piled on the semaphore, and final-inspection video stopped after a
            # few minutes.  8 concurrent h264 704x576 extracts (~1-2 cores each)
            # is well within budget and drains the backlog.
            with self._extract_sem:
                file_rel = self._extract_cycle(
                    ts_file=worker["ts_file"],
                    record_start=worker["record_start"],
                    start_dt=start_dt,
                    end_dt=end_dt,
                    cycle_number=cycle_number,
                    machine_id=machine_id,
                    ts_cycle_start=worker.get("ts_cycle_start"),
                    part_code=part_code,
                )

        try:
            append_cycle_metadata(
                csv_path=self.csv_path,
                cycle_number=cycle_number,
                start_dt=start_dt,
                end_dt=end_dt,
                relative_file_path=file_rel,
                machine_id=machine_id,
                machine_name=meta.get("machine_name", ""),
                line_name=meta.get("line_name", ""),
                zone_name=meta.get("zone_name", ""),
                tag="",
                part_code=part_code,
            )
            # 2026-05-19 — Operator-readable structured summary.
            # Earlier compact one-line form ("video=none" vs
            # "video=<path>") was hard to scan during shift review.  Now
            # each cycle prints a 4-line block with clear OK/FAIL status
            # so the operator can grep / eyeball "VIDEO MISSING" cases
            # without parsing the whole log.
            _video_ok = bool(file_rel) and not (file_rel or "").lower().startswith("none")
            _video_status = "OK    saved to " + (file_rel or "?") if _video_ok else "FAIL  no MP4 generated"
            _video_mark   = "[OK]" if _video_ok else "[--]"
            print(
                f"\n"
                f"+--- CYCLE #{cycle_number} -------------------------------------------\n"
                f"|  machine    : {meta.get('machine_name', '?')}\n"
                f"|  part_code  : {part_code or '(none)'}\n"
                f"|  duration   : {duration_s}s\n"
                f"|  video      : {_video_mark}  {_video_status}\n"
                f"+--------------------------------------------------------------"
            )
        except Exception as exc:
            print(f"[PLC] CSV write error: {exc}")

        # Clean up the TS file if nothing else needs it anymore
        if worker and worker.get("ts_file"):
            self._cleanup_old_ts(worker["ts_file"])

    # ─── Sub-camera auto-discovery ────────────────────────────────────────
    def _refresh_sub_cameras_loop(self) -> None:
        """Poll Phase2's /api/sub-cameras every 60 s and update the local
        set. New sub-machines added via Phase2 admin become 'known' here
        within a minute — no Flask restart, no JSON file edit."""
        import urllib.request, json as _json
        url = "http://127.0.0.1:8080/api/sub-cameras"
        while True:
            try:
                with urllib.request.urlopen(url, timeout=4) as resp:
                    body = _json.loads(resp.read().decode("utf-8"))
                ids = set(body.get("camera_ids") or [])
                with self._sub_camera_lock:
                    changed = (ids != self._sub_cameras)
                    if changed:
                        added   = ids - self._sub_cameras
                        removed = self._sub_cameras - ids
                        self._sub_cameras = ids
                if changed and (added or removed):
                    print(f"[SUB-CAM] auto-detected change "
                          f"+{sorted(added)} -{sorted(removed)} "
                          f"(now: {sorted(ids)})")
            except Exception:
                pass  # Phase2 not up yet — try again in 60 s
            time.sleep(60)

    # ─── Continuous per-camera MPEG-TS recorder ───────────────────────────────

    def _ensure_camera_recording(self, camera_id: str) -> Optional[Dict]:
        """
        Ensure a continuous H.264/MPEG-TS recording is running for this camera.
        Returns the camera worker dict, or None if unavailable.
        Each keyframe is forced every 1 second so we can seek accurately.

        Concurrency:  guarded by `_cam_spawn_lock` so the pre-starter on
        boot and the watchdog tick can't both Popen ffmpeg in the
        ~150 ms gap between the alive-check and the dict-write.  The
        old race produced two ffmpegs feeding the same RTSP stream,
        which corrupted both .ts files and made every clip render with
        FFT-style scrambled frames.
        """
        # Video gate — never spawn a recorder when this camera's line (or the
        # master switch) has video turned OFF.
        if not _camera_recording_allowed(camera_id, self.base_dir):
            return None
        with self._cam_spawn_lock:
            # Check if still alive
            if camera_id in self._camera_workers:
                cam = self._camera_workers[camera_id]
                if cam["proc"].poll() is None:
                    return cam
                print(f"[PLC] Camera recorder for {camera_id} died, restarting...")
                del self._camera_workers[camera_id]

            # 2026-05-21 — SPAWN-STORM GUARD.
            # _start_video (every cycle) and /api/submachine/clip (every
            # retry) call this function with NO cool-down check, so a
            # broken camera (RTSP 451 etc.) would spawn-die-spawn-die
            # every few seconds.  Each spawn creates a fresh TS filename,
            # leaving the cycle's marker pointing at a stale file that
            # the next spawn already deleted → "TS file missing" → no
            # MP4 for ANY cycle until the camera revives.  Honour the
            # watchdog's exponential back-off here too — if next_try is
            # still in the future, refuse to spawn.
            if not hasattr(self, "_cam_fail_state"):
                self._cam_fail_state: Dict[str, Dict] = {}
            _st = self._cam_fail_state.get(camera_id)
            if _st and time.time() < _st.get("next_try", 0):
                return None

            # 2026-08-03 — REACHABILITY GATE.  Roughly half the bound cameras are
            # physically offline at any time (measured: 32 of 73).  Each of them
            # was still being retried every 3 s, and every retry spawns a real
            # ffmpeg that sits in RTSP connect for seconds before dying.  Those
            # doomed processes compete for CPU, sockets and — most importantly —
            # camera-network bandwidth with the cameras that ARE live, which is
            # part of why live cameras were only getting a fraction of realtime
            # and their clips came out corrupt.  A background prober keeps a set
            # of currently-unreachable cameras (refreshed every 30 s, TCP connect
            # to :554, no ffmpeg involved); we simply refuse to spawn for those.
            # The moment a camera answers again the prober drops it from the set
            # and the normal 3 s retry resumes — so recovery stays instant.
            if camera_id in getattr(self, "_cam_unreachable", ()):
                return None

            rtsp_url = get_camera_rtsp_url(camera_id, self.base_dir)
            if not rtsp_url:
                return None

            videos_abs = _resolve_videos_root(self.base_dir)
            os.makedirs(videos_abs, exist_ok=True)
            ts_file = os.path.join(videos_abs, f"cam_{camera_id}_{int(time.time()*1000)}.ts")

            ffmpeg = _get_ffmpeg()
            # Capture stderr to a per-camera log file so silent RTSP/encoder failures
            # are visible. Without this, the recorder dies and we have no idea why.
            cam_log_path = os.path.join(videos_abs, f"_cam_{camera_id}.log")
            # 2026-07-31 — HYBRID codec.  SUB cameras (on-demand clip
            # extraction) record RAW HEVC with -c copy (0% CPU).  MAIN cameras
            # (extract_per_cycle: a per-cycle MP4 is re-encoded from the .ts at
            # every cycle end) MUST stay H.264 so that extraction stays a cheap
            # h264 decode -- decoding raw HEVC for 100s+ final-inspection cycles
            # in the /api/plc-edge webhook thread was slow enough to pile up 500
            # threads and hang the CMS.  H.264 recording also restores the
            # yuv420p/tv-range normalization those MP4s need for browser/TV.
            try:
                from camera_bindings import list_bindings as _lb
                _is_main = any(
                    str(b.get("camera_id")) == camera_id
                    and str(b.get("trigger_type", "")).upper() == "MAIN"
                    for b in _lb(self.base_dir))
            except Exception:
                _is_main = False
            if _is_main:
                # 2026-07-31 — RIGHT-SIZED to what actually gets served.  At
                # 1280-wide / 3000k this software HEVC->H.264 transcode ran at
                # speed=0.84x with drop=80+ on the busier MAIN cams, so ffmpeg
                # fell behind, died, and respawned MID-CYCLE — which is why a
                # 53 s cycle came back as an 18 s clip.  Both serving paths
                # (api_server _single_cmd and _extract_cycle) now output
                # 854-wide @ 900k, so recording any larger is pure waste:
                # 854 @ 1500k keeps encode comfortably above realtime, leaves
                # headroom above the served bitrate, and lets every cycle be
                # cut at its FULL length from one continuous .ts.
                # 2026-08-02 — RECORD AT 720p, not 854x480.  This transcode is the
                # real quality ceiling for every dashboard clip: whatever it writes
                # is all the extractor has to work with, so recording 854-wide made
                # even a perfect extract look blocky ("pixel phat rahe hain").  The
                # 854 cap was set on 2026-07-31 when ~61 cameras were being software
                # HEVC->H.264 transcoded at once and the busy MAIN cams ran at 0.84x
                # (falling behind -> recorder died mid-cycle -> short clips).  That
                # is no longer the shape: only the 15 MAIN (extract_per_cycle)
                # cameras transcode, the other ~60 are `-c copy`.  Re-measured on
                # real 2304x1296 HEVC footage on this box: 854=4.4x, 1152=4.3x,
                # 1280=4.1x realtime — 1280 keeps a ~4x safety margin over the 1.3x
                # needed to never fall behind. bicubic (not fast_bilinear) + crf 24
                # for a sharper downscale. Served clips are 1024-wide, so recording
                # 1280 leaves real detail for the encoder instead of upscaling mush.
                # 2026-09-12 — MOVE THIS TRANSCODE TO THE GPU.  Measured on this
                # box's A2000: the old libx264 path burned ~5.7 CPU-cores per MAIN
                # cam; 18 of them pinned load ~46 so every clip/live view buffered
                # ~30 s.  cuda-decode + scale_cuda + h264_nvenc does the identical
                # job at ~0.3 cores/cam (30× less) while the GPU was sitting idle.
                # NVENC is proven on this box (the clip pipeline already uses it).
                # SAFETY VALVE: VIDEO_LIVE_ENCODER=libx264 forces the old CPU path
                # (restart only), in case a camera's stream ever won't cuda-decode.
                if os.environ.get("VIDEO_LIVE_ENCODER", "").strip() == "libx264":
                    _hwin = []
                    _codec = [
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-tune", "zerolatency", "-crf", "24",
                        "-vf", "scale='min(iw,1280)':'-2':flags=bicubic,format=yuv420p",
                        "-color_range", "tv", "-level", "4.0",
                        "-maxrate", "3000k", "-bufsize", "6000k",
                        "-r", "15", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
                    ]
                else:
                    _hwin = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
                    _codec = [
                        "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll",
                        "-rc", "vbr", "-cq", "24", "-bf", "0", "-rc-lookahead", "0",
                        "-vf", "scale_cuda=1280:-2",
                        "-maxrate", "3000k", "-bufsize", "6000k",
                        "-r", "15", "-g", "15", "-keyint_min", "15",
                    ]
            else:
                _hwin = []
                _codec = ["-c", "copy"]     # raw HEVC, ~0% CPU
            # 2026-08-02 — ADAPTIVE transport: TCP first, UDP fallback per camera.
            # Why: on this camera network TCP-interleaved RTSP gives PERFECT,
            # loss-free recordings where the path can carry it (e.g. cam .86 =
            # 15s, 0 errors — TCP retransmits lost packets).  But on the more
            # lossy paths TCP hard-fails ("Input/output error", ~0.04s).  UDP is
            # the mirror image: it ALWAYS sustains, but on a lossy path the
            # missing RTP packets show up as MACROBLOCK CORRUPTION (the glitchy
            # frames operators reported).  So: start every camera on TCP (clean
            # video); the watchdog flips a camera to UDP only after TCP proves it
            # can't hold (3 quick deaths).  Net: clean where TCP works, sustained-
            # but-imperfect only where TCP genuinely can't run.  buffer_size +
            # reorder_queue_size cut UDP corruption while UDP is in use; both are
            # harmless on TCP.  (Earlier this was hard-pinned to UDP during the
            # Aug-1 network crisis when TCP failed everywhere; the crisis passed.)
            # 2026-08-02 (final) — DEFAULT IS UDP on this plant network.
            # Measured with 59 recorders live: every one had an ESTABLISHED TCP
            # session to :554 yet the NIC was receiving ~0 Mbps — the cameras
            # accept the TCP-interleaved RTSP session and then deliver almost no
            # data, so recorders limped at 0.1-0.5x realtime and wrote fragmented
            # .ts with gaps (any cycle landing in a gap = 404 "No video with
            # supported format" on the wallboard).  The same cameras over UDP
            # deliver 0.71-0.91x realtime.  A brief TCP-first experiment looked
            # fine on one camera (.86) but does NOT generalise here.
            # NOTE the transport map is in-memory, so it resets on every CMS
            # restart — which is exactly why the default must be the transport
            # that actually works, not one we hope to fall back from.
            _transport = getattr(self, "_cam_transport", {}).get(camera_id, "tcp")
            # 2026-08-02 — the UDP receive tuning must be applied ONLY on UDP.
            # Measured on cam .162: TCP plain = 0.27x realtime, TCP *with* these
            # flags = 0.04x (they make an already-starved TCP session far worse);
            # UDP plain = 0.91x.  So they are not the "harmless on TCP" no-op the
            # earlier revision assumed.
            _udp_tune = (["-buffer_size", "26214400",     # 25 MB UDP recv buffer
                          "-reorder_queue_size", "2000"]  # tolerate RTP reorder
                         if _transport == "udp" else [])
            cmd = [
                ffmpeg, "-y",
                *_hwin,                              # GPU decode for MAIN cams (else empty)
                "-rtsp_transport", _transport,
                *_udp_tune,
                # 2026-08-03 — 10 s → 45 s, to match STALL_TIMEOUT.  This is the
                # socket read timeout: with 10 s, ffmpeg exited on its OWN the
                # moment a camera went quiet for ten seconds.  But the stall
                # watchdog was deliberately set to 45 s because a healthy camera
                # on this network does go quiet for 15-30 s and then resumes — so
                # the two disagreed, and ffmpeg won: recorders were dying every
                # minute or two on silences the watchdog was written to ride out,
                # and each death cost a 20-90 s RTSP reconnect during which the
                # camera recorded NOTHING.  Cycles landing in those gaps are the
                # missing/short clips.  Now the socket tolerates exactly what the
                # watchdog tolerates, and the watchdog stays the one authority on
                # when a feed is actually dead.
                "-timeout", "45000000",             # 45 s socket I/O timeout (us)
                # 2026-08-03 — KEEP THE CAMERA'S OWN TIMESTAMPS.
                # A previous revision stamped every packet with its ARRIVAL time
                # (`-use_wallclock_as_timestamps 1`) to make the .ts duration match
                # wall-clock.  It did — and it wrecked playback, because arrival
                # time is not capture time: the network hands frames over in
                # bursts, so the clip inherited that jitter.  Measured on the same
                # camera over the same 20 s:
                #     camera timestamps : 500 frames, 25.1 fps, median gap  40 ms,
                #                         zero gaps over 0.5 s
                #     arrival timestamps: 274 frames, 13.8 fps, median gap   1 ms,
                #                         12 gaps over 0.5 s, worst 1.86 s
                # That second row is exactly what operators described — "a second
                # of video, then it freezes for two or three".  The camera paces
                # its RTP timestamps properly, so we simply pass them through:
                # smooth playback AND an honest timeline.  `+discardcorrupt` stays
                # (drop damaged packets); no genpts, no wallclock rewriting.
                "-fflags", "+discardcorrupt",
                "-i", rtsp_url,
            ] + _codec + [
                "-an",
                # 2026-07-31 — flush every packet to disk immediately.  With -c copy
                # of low-bitrate HEVC the mpegts muxer buffered its output, so the
                # .ts file SIZE sat "frozen" for >5s during quiet scenes even though
                # the camera was streaming fine -> the stall watchdog FALSE-killed a
                # healthy recorder -> 600+ respawns / 20 min -> fragmented recordings
                # with gaps -> dashboard cycle video "chhoot"ing (live stream was
                # always fine because it reads the camera directly).  Real-time flush
                # makes file growth track the live feed, so ONLY a true freeze (no
                # packets at all) trips the watchdog.
                "-flush_packets", "1",
                "-f", "mpegts",
                ts_file,
            ]
            record_start = datetime.now()
            try:
                cam_log = open(cam_log_path, "wb", buffering=0)   # overwrite, not append
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=cam_log,
                )
                cam = {
                    "proc":         proc,
                    "ts_file":      ts_file,
                    "record_start": record_start,
                    "write_start":  None,   # set by background thread once ffmpeg starts writing
                }
                self._camera_workers[camera_id] = cam
                # Background thread: detect when ffmpeg actually starts writing frames
                t = threading.Thread(
                    target=self._detect_write_start,
                    args=(cam, ts_file),
                    daemon=True,
                )
                t.start()
                print(f"[PLC] Continuous TS recording started: {ts_file}")
                # 2026-05-21 — IMMEDIATE-DEATH DETECTOR (storm-guard companion).
                # RTSP 451 / unreachable / auth-fail causes ffmpeg to exit in
                # well under a second.  The watchdog only runs every ~3 s, so
                # in that window the same camera can be spawned 3-6 more times
                # by _start_video (per-cycle) and submachine_clip (per-retry).
                # Verify the process is still alive after 0.6 s — if not,
                # arm the storm-guard cool-down RIGHT NOW so the next
                # _ensure_camera_recording call returns None instead of
                # spawning another disposable ffmpeg.
                t_guard = threading.Thread(
                    target=self._arm_storm_guard_if_dead,
                    args=(camera_id, proc, ts_file),
                    daemon=True,
                )
                t_guard.start()
                return cam
            except Exception as exc:
                print(f"[PLC] Camera ffmpeg launch error: {exc}")
                return None

    def _arm_storm_guard_if_dead(self, camera_id: str, proc, ts_file: str) -> None:
        """Watch a newly-spawned ffmpeg for 0.6 s.  If it has already
        exited, arm `_cam_fail_state[camera_id]` with an exponential
        back-off cool-down so subsequent spawn attempts (from any
        caller) bail out via the storm-guard at the top of
        `_ensure_camera_recording`.  Cleans up the (likely 0-byte)
        TS file the dead ffmpeg left behind so the videos folder
        doesn't accumulate orphan stubs.
        2026-05-21 — fixes the spawn-storm where a broken camera
        produced 6-10 zombie TS files per minute and every cycle
        marker pointed at a file the next spawn had already deleted."""
        time.sleep(0.6)
        if proc.poll() is None:
            return                                   # alive — let watchdog handle it
        # Process died within 0.6 s — almost certainly RTSP-side fail
        # (451, host unreachable, auth refused).  Arm the storm-guard.
        if not hasattr(self, "_cam_fail_state"):
            self._cam_fail_state: Dict[str, Dict] = {}
        st = self._cam_fail_state.setdefault(
            camera_id, {"fails": 0, "next_try": 0.0, "announced": False})
        st["fails"] += 1
        # 2026-05-29 — Per operator instruction, NO exponential backoff.
        # Storm-guard still applies (we don't want 10 spawn attempts per
        # second on a refused RTSP) but waits a fixed 3 s so reconnect
        # happens the instant the camera is back online.
        cool_down = 3
        st["next_try"] = time.time() + cool_down
        if not st["announced"]:
            print(f"[PLC] Camera {camera_id} died instantly "
                  f"(RTSP refused) — retrying every {cool_down}s "
                  f"until camera responds.")
            st["announced"] = True
        # Remove the worker entry so the next spawn attempt at least
        # tries afresh once the cool-down expires.
        try:
            cur = self._camera_workers.get(camera_id)
            if cur and cur.get("proc") is proc:
                del self._camera_workers[camera_id]
        except Exception:
            pass
        # Nuke the empty TS stub the dead ffmpeg left behind.
        try:
            if os.path.exists(ts_file) and os.path.getsize(ts_file) < 65536:
                os.remove(ts_file)
        except OSError:
            pass

    def _detect_write_start(self, cam: Dict, ts_file: str) -> None:
        """Two-phase monitor for a live recorder:

        Phase 1 — DETECT WRITE START.  Poll the TS file until ffmpeg has
        written at least 64 KB, then stamp `cam["write_start"]`.  This
        captures the exact wall-clock time when real video content starts
        flowing, eliminating the need to guess the RTSP startup delay
        when seeking later.

        Phase 2 — STALL WATCHDOG (2026-05-29).  After write_start is
        detected, keep polling file size every 1 s.  If the TS file
        stops growing for `STALL_TIMEOUT` seconds while ffmpeg is STILL
        ALIVE, the RTSP source has stalled mid-stream (classic Panasonic
        sub-stream symptom: camera stops sending packets but doesn't
        close the TCP socket, so ffmpeg's own `-timeout` flag never
        fires because that only covers connect-time, not in-flight
        reads).  Kill ffmpeg so the watchdog tick (~3 s later) spawns a
        fresh recorder and recording resumes the instant RTSP responds
        again.  Without this, the recorder would freeze for 60+ s
        producing duplicate frames into the TS and corrupting any
        cycle MP4 extracted from that file."""
        STALL_TIMEOUT = 45.0  # seconds with no file growth → stalled.
                              # 2026-07-31: 5s → 15s.  2026-08-02: 15s → 45s.
                              # On this (lossy) camera network a healthy UDP
                              # recorder can go quiet for 15-30 s and then resume;
                              # at 15 s the watchdog was KILLING those recorders,
                              # and every kill+respawn loses the RTSP re-connect
                              # time.  Measured effect: recorders were capturing at
                              # ~realtime while alive, yet per-camera coverage of
                              # the last 5 minutes was only 45-60 % — the missing
                              # half was restart churn (44 "appears OFFLINE" + 16
                              # stall-kills per 400 log lines).  A cycle landing in
                              # one of those gaps is exactly the 404 / "No video
                              # with supported format" the operator sees.  45 s
                              # still catches a genuinely dead feed quickly enough
                              # (the OFFLINE retry path handles hard failures).
        deadline = time.monotonic() + 60  # give up DETECT phase after 60 s
        write_started = False
        last_size = 0
        last_grow_ts = time.monotonic()

        while True:
            try:
                cur_size = os.path.getsize(ts_file) if os.path.exists(ts_file) else 0
            except OSError:
                cur_size = 0
            now = time.monotonic()

            # ── Phase 1: detect write_start ─────────────────────────────
            if not write_started:
                if cur_size >= 65536:
                    cam["write_start"] = datetime.now()
                    print(f"[PLC] write_start detected for {ts_file}")
                    write_started = True
                    last_size    = cur_size
                    last_grow_ts = now
                elif now >= deadline:
                    print(f"[PLC] write_start timeout for {ts_file} "
                          f"— will use elapsed fallback")
                    return
                time.sleep(0.2)
                continue

            # ── Phase 2: stall watchdog ─────────────────────────────────
            # Exit cleanly if ffmpeg already died on its own — the
            # camera-recorder watchdog (3-s tick) will respawn it.
            proc = cam.get("proc")
            if proc is None or proc.poll() is not None:
                return

            if cur_size > last_size:
                last_size    = cur_size
                last_grow_ts = now
            elif now - last_grow_ts >= STALL_TIMEOUT:
                print(f"[PLC] Stall detected on {os.path.basename(ts_file)} "
                      f"(no growth for {STALL_TIMEOUT:.0f}s, size frozen at "
                      f"{cur_size//1024}KB) — killing ffmpeg to force RTSP reconnect")
                try:
                    self._kill_cam(cam)
                except Exception as exc:
                    print(f"[PLC] Stall-kill error: {exc}")
                return

            time.sleep(1.0)

    def _kill_cam(self, cam: Dict) -> None:
        proc = cam.get("proc")
        if not proc:
            return
        try:
            proc.stdin.write(b'q\n')
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # ─── Shift-end cleanup for SUB cameras ────────────────────────────────────

    def _shift_state_path(self) -> str:
        return os.path.join(self.base_dir, SHIFT_STATE_FILE)

    def _load_shift_state(self) -> Dict:
        """Persisted record of which shift we've already cleaned up for.
        Survives CMS restarts so a bounce during the boundary minute
        doesn't fire the wipe twice."""
        import json as _json
        p = self._shift_state_path()
        try:
            with open(p, "r", encoding="utf-8") as f:
                return _json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def _save_shift_state(self, data: Dict) -> None:
        import json as _json
        try:
            with open(self._shift_state_path(), "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2)
        except OSError as exc:
            print(f"[PLC] shift_state save failed: {exc}")

    def _check_shift_boundary(self, bindings: List[Dict]) -> None:
        """Called once per poll.  Throttled internally to fire at most
        once per shift, identified by current_shift_label()."""
        now = datetime.now()

        # Cheap minute-level throttle so we don't load JSON 30 times/sec.
        last_minute_checked = getattr(self, "_shift_check_minute", -1)
        cur_minute = now.hour * 60 + now.minute
        if cur_minute == last_minute_checked:
            return
        self._shift_check_minute = cur_minute

        if not is_at_boundary(now, self.base_dir):
            return

        label = current_shift_label(now, self.base_dir)
        state = self._load_shift_state()
        if state.get("last_fired_shift_label") == label:
            return    # already cleaned for this shift

        # 2026-05-14 — extended from "SUB cameras only" to "ALL cameras".
        # Operator requirement: at every shift start, rotate every camera's
        # TS file (MAIN + SUB) AND wipe all past-shift mp4s.  A TS file
        # that's been recording for 10+ hours accumulates PCR drift which
        # makes ffmpeg deep-seek emit empty / unplayable clips (the bug
        # that was producing 200-600 KB MP4s with 0:00 duration in the UI).
        # Rotating per shift keeps each TS file under ~8 hours = clean
        # keyframe alignment for every extraction.
        all_cam_ids: List[str] = []
        for b in bindings:
            cid = str(b.get("camera_id", "")).strip()
            if cid and cid not in all_cam_ids:
                all_cam_ids.append(cid)

        if not all_cam_ids:
            # Nothing to wipe — still record the label so we don't keep
            # checking the binding list every minute for the whole shift.
            state["last_fired_shift_label"] = label
            self._save_shift_state(state)
            return

        print(f"[PLC] Shift boundary reached ({label}) — rotating "
              f"{len(all_cam_ids)} camera TS file(s) + wiping past-shift mp4s")
        # _wipe_sub_camera_state name is now misleading — it really just
        # stops the recorder + deletes its TS for any camera ID passed in.
        # Works identically for MAIN and SUB; the watchdog tick respawns.
        self._wipe_sub_camera_state(all_cam_ids)
        self._wipe_past_shift_mp4s()

        state["last_fired_shift_label"] = label
        state["last_fired_at"] = now.isoformat(timespec="seconds")
        state["wiped_cameras"] = all_cam_ids
        self._save_shift_state(state)

    def _wipe_sub_camera_state(self, camera_ids: List[str]) -> None:
        """Rotate the recorders of the given cameras at a shift boundary.

        2026-09-21 — 48 h footage hold + gentle rotation.  This used to stop
        EVERY recorder at the same instant, DELETE its TS and let the 3 s
        watchdog respawn them all together.  Deleting threw away footage the
        clip archiver had not cut yet, and the mass stop/start left the
        single-session cameras hung: they keep the old session and ignore the
        new connection (67 cameras on 21-Sep).  Now the TS file is KEPT (the
        retention pass deletes it after TS_KEEP_HOURS) and cameras rotate one
        at a time in the background: stop, hold the respawn ROTATE_QUIET_S so
        the camera can release its session, next camera ROTATE_STAGGER_S later.
        Each file still covers about one shift, so clip seeks stay short."""
        ids = [c for c in camera_ids if c]
        if not ids:
            return
        if not hasattr(self, "_cam_fail_state"):
            self._cam_fail_state = {}

        def _rotate() -> None:
            done = 0
            for cid in ids:
                cam = self._camera_workers.pop(cid, None)
                st = self._cam_fail_state.setdefault(
                    cid, {"fails": 0, "next_try": 0.0, "announced": False})
                # Hold before the stop too, so nothing respawns mid-kill.
                st["next_try"] = max(st.get("next_try", 0.0),
                                     time.time() + ROTATE_QUIET_S + 10)
                if cam:
                    try:
                        self._kill_cam(cam)
                    except Exception as exc:
                        print(f"[PLC] shift-rotate kill failed for {cid}: {exc}")
                    st["next_try"] = max(st.get("next_try", 0.0),
                                         time.time() + ROTATE_QUIET_S)
                    done += 1
                # In-flight cycle markers started on the old recorder session.
                stale_markers = [
                    mid for mid, vw in list(self._video_workers.items())
                    if vw.get("camera_id") == cid
                ]
                for mid in stale_markers:
                    self._video_workers.pop(mid, None)
                time.sleep(ROTATE_STAGGER_S)
            print(f"[PLC] shift rotation done: {done} recorder(s) rotated one at a "
                  f"time, old TS files kept for {TS_KEEP_HOURS:g} h", flush=True)

        print(f"[PLC] shift rotation: {len(ids)} camera(s), one every "
              f"{ROTATE_STAGGER_S:g}s, respawn held {ROTATE_QUIET_S:g}s, "
              f"TS kept {TS_KEEP_HOURS:g} h", flush=True)
        threading.Thread(target=_rotate, name="shift-rotate", daemon=True).start()

    def _wipe_past_shift_mp4s(self) -> None:
        """Apply video RETENTION at the shift boundary: delete cycle .mp4 clips
        OLDER than the configured retention window; keep the rest.
        2026-06-19 — was a wipe-ALL-every-shift, which silently overrode the
        admin's retention setting (a '3 day' choice still died every shift).
        Now it honours retention_hours/_days from video_config.json AND targets
        the configured save_path (e.g. an external drive) instead of the
        hardcoded <base>/videos.  TS rotation (in _wipe_sub_camera_state) still
        keeps each TS file young for clean keyframe seeks — that's separate."""
        videos_root = _resolve_videos_root(self.base_dir)
        if not os.path.isdir(videos_root):
            return
        cutoff = time.time() - _resolve_retention_seconds(self.base_dir)
        deleted = 0
        kept    = 0
        freed   = 0
        for root, _dirs, files in os.walk(videos_root):
            for f in files:
                if not f.lower().endswith(".mp4"):
                    continue
                p = os.path.join(root, f)
                try:
                    if os.path.getmtime(p) >= cutoff:
                        kept += 1            # within retention window — keep
                        continue
                    sz = os.path.getsize(p)
                    os.remove(p)
                    deleted += 1
                    freed   += sz
                except OSError:
                    pass
        # Prune now-empty subdirectories so the tree stays tidy
        for root, _dirs, _files in os.walk(videos_root, topdown=False):
            if root == videos_root:
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
        mb = freed / 1024 / 1024
        print(f"[PLC] shift retention: removed {deleted} old mp4 ({mb:.1f} MB), "
              f"kept {kept} within window")

    # ─── Per-cycle video marker ────────────────────────────────────────────────

    def _start_video(self, machine_id: str, camera_id: str, cycle_number: int,
                      marker_dt: Optional[datetime] = None) -> None:
        """Mark cycle start — the camera TS recorder is already running.
        `marker_dt`, when supplied, is the PLC-accurate edge time from the
        MES webhook (epoch_ms).  It anchors both the per-machine cycle
        marker AND the ts_cycle_start offset into the TS file, so the
        extracted clip starts exactly at the PLC pulse moment instead of
        whenever the HTTP webhook happened to reach CMS."""
        marker_dt = marker_dt or datetime.now()
        self._video_workers.pop(machine_id, None)

        if not camera_id:
            print(f"[PLC] No camera bound for {machine_id} — video skipped")
            return

        cam = self._ensure_camera_recording(camera_id)
        if not cam:
            print(f"[PLC] No RTSP URL for camera={camera_id!r} — video skipped")
            return

        write_start = cam.get("write_start")
        if write_start:
            # How many seconds of TS content exist at the moment this cycle
            # starts.  Both `marker_dt` (PLC pulse time) and `write_start`
            # (CMS-side recorder start) are wall-clock datetimes — they're
            # comparable as long as MES and CMS clocks are aligned (they
            # are: same machine, localhost webhook).
            ts_cycle_start: Optional[float] = max(0.0, (marker_dt - write_start).total_seconds())
        else:
            # write_start not yet known (camera just launched) — use None so
            # _extract_cycle falls back to the elapsed-14 heuristic
            ts_cycle_start = None

        self._video_workers[machine_id] = {
            "camera_id":      camera_id,
            "ts_file":        cam["ts_file"],
            "record_start":   cam["record_start"],
            "start_dt":       marker_dt,
            "cycle_number":   cycle_number,
            "ts_cycle_start": ts_cycle_start,   # seconds into TS file at cycle start
        }
        print(
            f"[PLC] Cycle #{cycle_number} marker set for {machine_id} "
            f"(ts_cycle_start={ts_cycle_start:.1f}s)" if ts_cycle_start is not None
            else f"[PLC] Cycle #{cycle_number} marker set for {machine_id} (ts_cycle_start=unknown)"
        )

    # ─── Cycle clip extraction ────────────────────────────────────────────────

    def _extract_cycle(
        self,
        ts_file: str,
        record_start: datetime,
        start_dt: datetime,
        end_dt: datetime,
        cycle_number: int,
        machine_id: str,
        ts_cycle_start: Optional[float] = None,
        part_code: str = "",
    ) -> str:
        """
        Cut [start_dt, end_dt] out of the rolling MPEG-TS file and write
        it as an MP4 named after the part code.  Operator spec is verbatim:
        from one OK/NG bit to the next OK/NG bit, no walls.
        """
        if not os.path.exists(ts_file):
            print(f"[PLC] TS file missing: {ts_file}")
            return ""

        # Seconds into the TS where the cycle starts.
        if ts_cycle_start is not None:
            ss = max(0.0, ts_cycle_start)
        else:
            ss = max(0.0, (start_dt - record_start).total_seconds())
        raw_duration = (end_dt - start_dt).total_seconds()

        # 2026-05-13 — operator spec change:
        #   "jo PLC se aayega voh as it is run hoga"
        # Clip length must match the PLC cycle duration EXACTLY, rounded
        # UP to the next whole second (15.7 s → 16 s, 200.0 s → 200 s).
        # No pre/post padding, no upper cap.
        #
        # Decoder safety note (rolled back the 2026-05-12 padding hack):
        #   The recorder forces a keyframe every 1 s (-g 20 @ 20 fps),
        #   and we're using input-side `-ss` with `-c copy`, so ffmpeg
        #   auto-snaps to the nearest preceding keyframe.  Worst-case
        #   the clip begins ~0–1 s before `ss`, which keeps HTML5/TV
        #   decoders happy without us padding explicitly.  The cycle
        #   END is set by `-t duration`; ffmpeg writes whole frames so
        #   the file may overshoot by a fraction of a GOP (≤1 s).
        import math as _math
        # Floor at 1 s: if two PLC pulses arrive within the same second
        # (fast machine, clock drift), ceil(0) = 0 would tell ffmpeg
        # `-t 0` and produce an empty file.  1 s is the smallest cycle
        # the operator would ever ask for and matches the TS keyframe
        # interval, so an off-by-one frame is harmless.
        #
        # 2026-05-14 — no upper cap: operator wants the clip length to
        # ALWAYS match cycle duration exactly, even 200 s cycles during
        # model change.  The earlier 19,710 s "stuck cycle" disaster is
        # now prevented structurally by per-shift TS rotation (a marker
        # that survives a shift boundary gets dropped automatically), so
        # raw_duration can't exceed one shift (~8 h) and in practice
        # never exceeds a few hundred seconds.
        duration = max(1.0, float(_math.ceil(raw_duration)))
        print(f"[PLC] #{cycle_number} extract: ss={ss:.1f}s "
              f"cycle={raw_duration:.1f}s -> clip={duration:.0f}s "
              f"(tight, no padding)  "
              f"ts={os.path.basename(ts_file)}")

        videos_abs = _resolve_videos_root(self.base_dir)
        safe_part = re.sub(r"[^A-Za-z0-9._-]", "_", part_code).strip("_") if part_code else ""
        if safe_part:
            file_name = f"{safe_part}.mp4"
        else:
            # 2026-05-27 — Sanitize machine_id for Windows filename.
            # machine_id is "mes:2" / "mes:13" etc., and Windows rejects
            # ':' in filenames (WinError 87).  Strip every char that
            # isn't safe for both Windows and Linux.
            _safe_mid = re.sub(r"[^A-Za-z0-9._-]", "_", str(machine_id))
            file_name = f"cycle_{cycle_number}_{_safe_mid}.mp4"

        # ── Structured folder path ──────────────────────────────────
        # Check video_config.json for custom save_path. If set, build:
        #   save_path / Zone / Line / Machine / Date / Shift / Slot / part.mp4
        # Also keep a flat copy in videos_abs for /api/video/by-part lookups.
        structured_abs = None
        try:
            _vcfg_path = os.path.join(self.base_dir, "video_config.json")
            custom_root = ""
            if os.path.exists(_vcfg_path) and os.path.getsize(_vcfg_path) > 0:
                import json as _json
                try:
                    with open(_vcfg_path) as _f:
                        _vcfg = _json.load(_f)
                    custom_root = (_vcfg.get("save_path", "") or "").strip()
                except (ValueError, OSError):
                    custom_root = ""  # corrupt/missing → skip structured save
                if custom_root:
                    os.makedirs(custom_root, exist_ok=True)
                    meta = self._get_machine_meta(machine_id)
                    # Sanitize every path segment: strip any separator / drive-unsafe chars
                    # so values like "Final Inspection M/c" don't accidentally spawn a
                    # nested "M\c" directory on Windows.
                    import re as _re_path
                    def _safe(seg: str) -> str:
                        s = _re_path.sub(r"[^A-Za-z0-9._-]+", "_",
                                         (seg or "").strip().replace(" ", "_"))
                        return s.strip("_") or "X"
                    zone_name  = _safe(meta.get("zone_name")    or "Unknown_Zone")
                    line_name  = _safe(meta.get("line_name")    or "Unknown_Line")
                    mach_name  = _safe(meta.get("machine_name") or machine_id)
                    date_str   = start_dt.strftime("%Y-%m-%d")
                    shift_name = _safe(self._get_current_shift_name(start_dt))
                    slot_label = _safe(self._get_slot_label(start_dt))
                    # Build: root/Zone/Line/Machine/Date/Shift/Slot/
                    sub_dir = os.path.join(
                        custom_root, zone_name, line_name, mach_name,
                        date_str, shift_name, slot_label
                    )
                    os.makedirs(sub_dir, exist_ok=True)
                    structured_abs = os.path.join(sub_dir, file_name)
        except Exception as exc:
            print(f"[PLC] Structured path error: {exc}")

        # ── Per-line folder on flat videos path ──────────────────────────
        # Organise flat copy as videos/<Line_Name>/<part>.mp4 instead of
        # videos/<part>.mp4 so admins can browse recordings by line.
        # Falls back to the root videos_abs if meta is missing.
        try:
            _meta = self._get_machine_meta(machine_id)
            _line_dir = (_meta.get("line_name") or "").strip().replace(" ", "_") or "Unknown_Line"
            # Strip any path-unsafe chars
            import re as _re_line
            _line_dir = _re_line.sub(r"[^A-Za-z0-9._-]", "_", _line_dir).strip("_") or "Unknown_Line"
        except Exception:
            _line_dir = "Unknown_Line"
        line_videos_dir = os.path.join(videos_abs, _line_dir)
        os.makedirs(line_videos_dir, exist_ok=True)

        file_abs = os.path.join(line_videos_dir, file_name)
        file_rel = f"{DEFAULT_VIDEOS_DIR}/{_line_dir}/{file_name}"
        tmp_name = f"_pending_cyc{cycle_number}_{int(time.time()*1000)}_{file_name}"
        tmp_abs = os.path.join(line_videos_dir, tmp_name)

        # 2026-05-18 — OVERWRITE GUARD.
        # PLC L108 occasionally chatters: one real 115 s cycle is followed
        # by a phantom 9 s pulse a few seconds later, with the SAME
        # part_code (scanner hasn't rescanned).  Without this guard, the
        # phantom's MP4 (named `{part_code}.mp4`) overwrites the real
        # cycle's clip, and the operator sees a 9 s video when the
        # dashboard shows 115 s.
        #
        # Rule: if an MP4 already exists for this part_code and its
        # duration is BIGGER than what we're about to extract, skip the
        # new write.  The first/longest video for a part wins.  Also
        # rename the new attempt to `{safe_part}_chatter_{cycle}.mp4`
        # so it's still on disk for diagnostics but doesn't clobber the
        # primary clip.
        try:
            if os.path.exists(file_abs):
                existing_size = os.path.getsize(file_abs)
                # Quick probe of existing duration via ffprobe header
                _probe_r = subprocess.run(
                    [ _get_ffmpeg(), "-hide_banner", "-i", file_abs ],
                    capture_output=True, timeout=5,
                )
                _existing_dur = 0.0
                import re as _re_dur2
                _m = _re_dur2.search(
                    r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)",
                    (_probe_r.stderr or b"").decode("utf-8", errors="replace"),
                )
                if _m:
                    _existing_dur = (int(_m.group(1))*3600
                                      + int(_m.group(2))*60
                                      + float(_m.group(3)))
                # If the existing clip is clearly longer than the new one
                # we're about to extract, treat the new one as chatter.
                if _existing_dur >= duration + 2.0:
                    chatter_name = f"{safe_part}_chatter_{cycle_number}.mp4"
                    file_abs = os.path.join(line_videos_dir, chatter_name)
                    file_rel = f"{DEFAULT_VIDEOS_DIR}/{_line_dir}/{chatter_name}"
                    tmp_name = f"_pending_chatter{cycle_number}_{int(time.time()*1000)}_{chatter_name}"
                    tmp_abs  = os.path.join(line_videos_dir, tmp_name)
                    print(f"[CHATTER-GUARD] {safe_part}: existing clip "
                          f"{_existing_dur:.1f}s >= new {duration:.0f}s+2 — "
                          f"new cycle #{cycle_number} saved as {chatter_name} "
                          f"(primary {safe_part}.mp4 kept intact)")
        except Exception as _cg_exc:
            # Best effort — if probe fails, fall through to normal write
            print(f"[CHATTER-GUARD] probe failed: {_cg_exc}")

        ffmpeg = _get_ffmpeg()
        # 2026-05-16 — Switched first-pass from `-c copy` to RE-ENCODE.
        # Operator reported "video plays 1 sec then stops" for cycles
        # extracted from long-running TS files (1000+ s into the file).
        # Symptom: 69 MB MP4 for 50 s cycle, ffprobe shows duration=50 s,
        # but HTML5 <video> stops at the first PTS discontinuity after
        # the initial keyframe.  Root cause: stream-copy preserves the
        # source TS's PCR drift verbatim, so the MP4 has non-monotonic
        # PTS that browsers refuse to decode past.  Re-encoding with
        # libx264 rebuilds clean monotonic timestamps + a fresh moov
        # atom — guaranteed playable.  Cost: ~real-time-on-ultrafast
        # (50 s clip ≈ 12-15 s of CPU); fine because the collector
        # already waits 25 s for the TS flush before kicking extraction.
        #
        # 2026-05-18 — CORRUPTED-VIDEO FIX.
        # Operator reported macroblocked / smeared playback (gray patches
        # with hint of structure — classic "decoded P-frame without I-frame
        # reference" pattern).  Two changes pin the cause:
        #
        # 1.  +igndts removed from fflags.  This was telling the demuxer
        #     to discard DTS on packets that had both DTS & PTS, which
        #     caused out-of-order frame delivery to libx264 — the encoder
        #     then re-encoded the visual garbage verbatim.  +genpts alone
        #     is sufficient to rebuild monotonic timestamps; igndts is
        #     only needed for sources that lie about DTS (our recorder
        #     produces clean DTS so it's harmful here).
        #
        # 2.  Hybrid seek: input-side `-ss` is rounded DOWN by 1.5 s so
        #     ffmpeg lands on the keyframe BEFORE the cycle start (TS
        #     keyframes are every 1 s, so 1.5 s of slack guarantees a
        #     valid I-frame is in the stream).  Then output-side `-ss`
        #     skips the pre-roll to the exact cycle boundary.  This way
        #     the encoder always sees a fully-decodable GOP at the start.
        #
        # 3.  -err_detect ignore_err keeps decoding on bit errors so a
        #     single corrupt packet in the TS doesn't cascade into a
        #     dropped section of the clip.
        # -------------------------------------------------------------
        # Seek hybrid:
        #   input_ss  = max(0, ss - 1.5)   → keyframe-aligned anchor
        #   output_ss = ss - input_ss      → exact-frame skip-forward
        # If ss is already < 1.5 s into the TS, just use input_ss=0 and
        # output_ss=ss (the file head is its own keyframe).
        # 2026-07-22 — MULTI-TS STITCH tried here but reverted: seeking a
        # concat of rotated TS by WALL-CLOCK offset overshoots whenever the
        # recording has gaps (HEVC cams drop packets) -> ffmpeg rc=0 but 0 KB
        # output -> worse than the single-file partial.  Single file + the
        # permissive duration gate below (save whatever valid footage exists)
        # gives the operator SOME video instead of nothing.
        _eff_src = ts_file
        _eff_ss  = ss
        _concat_in = []
        _concat_lst = None
        _input_ss  = max(0.0, ss - 4.0)   # 2026-08-02: 1.5→4.0, keyframe slack (see api_server)
        _output_ss = max(0.0, ss - _input_ss)
        # 2026-05-19 — Hardware-accelerated H.264 re-encode (NVENC > QSV > libx264).
        # Probe runs once at process start; result cached.  Auto-falls back
        # to libx264 if neither GPU encoder is available — never breaks recording.
        _hw_codec, _hw_flags = _pick_hw_encoder()
        cmd = [
            ffmpeg, "-y",
            "-fflags",     "+genpts+discardcorrupt",   # NO +igndts
            "-err_detect", "ignore_err",               # don't bail on bit errors
            "-ec", "favor_inter",                      # conceal lost MBs, not green
            *_concat_in,                               # concat demuxer when stitching TS
            "-ss", f"{_input_ss:.3f}",                 # input seek → keyframe before cycle
            "-i", _eff_src,                            # single TS or concat listfile
            "-ss", f"{_output_ss:.3f}",                # output seek → exact cycle start
            "-t", f"{duration:.3f}",
            "-c:v", _hw_codec,
            *_hw_flags,                  # picked codec's preset/quality bundle
            # 2026-07-31 — TUNNEL-SIZED, same budget as the on-demand clip path
            # in api_server (_single_cmd).  These per-cycle MP4s are what
            # /api/video/by-part serves when the time-window path can't cover a
            # cycle, and at the old ~2000 kbps a 50 s cycle was 12 MB => ~15 s
            # over the ~620 KB/s Cloudflare tunnel.  854-wide @ ~900 kbps keeps
            # the clip readable for shop-floor review, plays several times
            # faster than realtime through the tunnel, and shrinks the 48 h
            # archive on disk as a bonus.
            # 2026-08-02 — SHARPNESS FIX.  Operator: "video ki quality sahi nahi
            # aa rahi, pixel phat rahe hain."  Two causes, both fixed:
            #   1. the 15 clip-producing cameras were recording the 704x576 SUB
            #      stream — a D1 source blown up on a wallboard TV is inherently
            #      blocky.  They now record the 2304x1296 MAIN stream (cameras.json),
            #      giving the encoder ~10x the pixels to work from.
            #   2. this encode was 854-wide @ 900 kbps with fast_bilinear — a
            #      soft, bitrate-starved downscale that ADDED mush on top.
            # Now: 1024-wide, bicubic (sharper kernel), crf 24 quality-driven with
            # a 1800k cap.  Measured on real 2304 footage: 60 s clip ≈ 2.4 MB (~4 s
            # over the ~620 KB/s tunnel) — visibly sharper AND still inside the
            # same tunnel budget the 3-4 s click-to-play target needs (the old
            # blocky SUB-sourced clips were ~12 s).  Do not raise past ~1280 wide:
            # 1280 measured ~30 s over the tunnel and breaks that target.
            # Operator choice 2026-08-02: keep the 854-wide / ~900k tunnel budget
            # (fastest click-to-play, ~6 s for a 60 s clip) — the sharpness win
            # comes from the SOURCE now being a 1280x720 recording of the 2304
            # MAIN stream instead of a 704x576 SUB stream, plus bicubic instead of
            # fast_bilinear. Measured on the same footage: 60 s clip ≈ 3.6 MB.
            # Raising this to 1024-wide/1100k was measured at ~6 MB (~10 s tunnel)
            # if sharper is ever wanted over speed.
            "-vf", "scale='min(iw,854)':'-2':flags=bicubic,format=yuv420p",
            "-crf", "27",
            "-maxrate", "900k", "-bufsize", "1800k",
            "-pix_fmt", "yuv420p",       # max compatibility (browser HW decode)
            "-an",
            "-vsync", "cfr",             # constant frame rate → clean playback timing
            "-avoid_negative_ts", "make_zero",
            # +faststart relocates the MP4 moov atom to the start of the
            # file after writing.  Without it, an HTML5 <video> tag has
            # to download the entire file (100+ MB on long cycles) before
            # finding the metadata box at the end and starting playback.
            # With +faststart, the TV browser can start rendering from
            # the first keyframe as soon as moov + a few seconds of frames
            # arrive.  Cost: ~200 ms post-processing pass per clip.
            "-movflags", "+faststart",
            tmp_abs,
        ]
        snap_ts = ts_file   # nothing to clean up — extraction is single-stage
        # Subprocess timeout scales with clip length.  First pass is
        # stream-copy at input-seek (~150 ms regardless of duration).
        # The retry RE-ENCODES with output-seek though, which is much
        # heavier: ~real-time on libx264 ultrafast for the clip itself
        # plus a head-to-seek scan of the TS file.  Floor at 120 s with
        # 4× clip-length headroom: 16 s clip → 124 s, 200 s clip → 860 s.
        max_timeout = max(120, int(duration * 4) + 60)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=max_timeout)
                result_rc = proc.returncode
            except subprocess.TimeoutExpired:
                print(f"[PLC] Extraction timeout for #{cycle_number} — killing ffmpeg")
                proc.kill()
                proc.wait()
                # Windows sometimes keeps the handle briefly after kill —
                # retry a few times with a short sleep so the tmp file
                # doesn't linger as "_pending_..." taking up disk.
                for _ in range(5):
                    try:
                        os.remove(tmp_abs)
                        break
                    except OSError:
                        time.sleep(0.3)
                # Two-stage snapshot leftover — best-effort cleanup.
                if snap_ts != ts_file:
                    try:
                        if os.path.exists(snap_ts): os.remove(snap_ts)
                    except OSError: pass
                return ""
        except Exception as exc:
            print(f"[PLC] Extraction error: {exc}")
            for _ in range(5):
                try:
                    os.remove(tmp_abs)
                    break
                except OSError:
                    time.sleep(0.3)
            try:
                if os.path.exists(snap_ts): os.remove(snap_ts)
            except OSError: pass
            return ""

        # Snapshot served its purpose — ditch it (Windows file-lock retry).
        # Skip if we fell back to using ts_file directly (snap_ts == ts_file).
        if snap_ts != ts_file:
            for _ in range(3):
                try:
                    if os.path.exists(snap_ts): os.remove(snap_ts)
                    break
                except OSError:
                    time.sleep(0.2)

        # Success criteria are checked on the temp file — no other writer can
        # have touched it because the name is unique per call.
        # 2026-05-14 — tightened threshold + ffprobe duration check.  The
        # OLD `> 1024 bytes` check was passing tiny 200–600 KB files that
        # contained only a single keyframe header (TS-deep-seek bug), so
        # the UI played them and showed 0:00 duration.  The follow-up
        # `30 KB/sec` floor still let through PCR-drift clips that were
        # 2 s long but 2 MB in size (passing the size gate, failing the
        # duration we asked for).  Now we ALSO ffprobe the output and
        # require actual_duration ≥ 0.6 × requested.
        # 2026-07-22 — SIZE GATE RELAXED to a flat 60 KB floor (was scaled by
        # the whole cycle duration, which rejected a valid 3 s partial of a
        # 28 s cycle just because the camera stalled and only 3 s got
        # recorded).  60 KB still rejects 0-byte / single-keyframe stubs; the
        # ffprobe duration gate below is what enforces "real playable clip".
        MIN_BYTES_PER_SEC = 30 * 1024
        min_required = 60 * 1024
        size_actual = os.path.getsize(tmp_abs) if os.path.exists(tmp_abs) else 0

        def _probe_duration(path: str):
            """Return (duration_seconds, probe_succeeded).
            `imageio_ffmpeg` ships ffmpeg only — ffprobe sibling never
            exists, so the old `ffmpeg.replace("ffmpeg","ffprobe")` trick
            always failed and we treated every clip as 0 s long.  Now we
            use `ffmpeg -i <file>` (always present) and parse the
            "Duration: HH:MM:SS.ms" line from stderr — same info ffprobe
            would have given us.

            Returns probe_succeeded=False when the duration can't be read
            (corrupt MP4 metadata, ffmpeg crash) so the caller can decide
            whether to fall back to a size-only check rather than reject
            a perfectly playable 20 MB clip just because its moov atom
            is missing."""
            if not path or not os.path.exists(path):
                return 0.0, False
            try:
                result = subprocess.run(
                    [ffmpeg, "-hide_banner", "-i", path],
                    capture_output=True, text=True, timeout=10,
                )
                # ffmpeg -i prints "Duration: 00:00:15.12, start: ..." to
                # stderr and exits with rc=1 (no output specified).  That
                # rc is expected — we only care about the header parse.
                import re as _re_dur
                m = _re_dur.search(
                    r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)",
                    result.stderr or "",
                )
                if m:
                    sec = (int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + float(m.group(3)))
                    return sec, True
                return 0.0, False
            except Exception:
                return 0.0, False

        actual_dur, probe_ok = (_probe_duration(tmp_abs)
                                  if size_actual > 0 else (0.0, False))
        # 2026-05-18 — Acceptance rule, two regimes:
        #   • Short cycles (≤ 20 s, normal production) keep the strict 60%
        #     threshold so a 2-s-clip-for-15-s-cycle (PCR drift / bad seek)
        #     still gets discarded.
        #   • Long cycles (> 20 s, typically caused by break / setup /
        #     recorder crash mid-cycle) accept ≥ 30%.  Operator would
        #     rather have a 67-s clip of a 187-s cycle than NOTHING at all
        #     just because the recorder restarted mid-cycle.  Both regimes
        #     enforce a 5-s minimum so we never publish an empty stub.
        def _min_acceptable(d):
            # 2026-07-22 — PERMISSIVE: operator would rather see a short clip
            # of the part than a "no video" box when a stalled camera only
            # recorded part of the cycle.  Accept any clip that reaches
            # min(cycle, 2 s).  The ffprobe check still rejects 0 s / corrupt
            # output, so whatever gets saved is a real, browser-playable H.264
            # clip — just possibly shorter than the full cycle.
            return min(float(d), 2.0)
        if probe_ok:
            dur_ok = actual_dur >= _min_acceptable(duration)
        else:
            dur_ok = True   # no signal → don't double-fail on top of size

        # If the first attempt produced a too-small file OR a too-short
        # clip, retry once with *output-side* seek + decoding flags.
        # Output-side seek forces ffmpeg to walk the file from the head
        # and emit frames properly; slower (~1–2 s vs 150 ms) but
        # reliable for the corrupted-PCR case that's currently producing
        # 2-second clips for 28-second cycles.
        if (result_rc == 0 and (0 < size_actual < min_required or not dur_ok)) or result_rc != 0:
            print(f"[PLC] #{cycle_number} first pass off-target "
                  f"(size={size_actual//1024}KB need={min_required//1024}KB, "
                  f"duration={actual_dur:.1f}s/{duration:.0f}s), retrying with re-encode")
            try: os.remove(tmp_abs)
            except OSError: pass
            # 2026-05-14 — retry now RE-ENCODES instead of stream-copy.
            # The first pass already tried stream-copy; if that produced a
            # 0 s / corrupted output it's almost always because the source
            # TS has PCR jumps that confuse `-c copy`.  Decoding to YUV and
            # re-encoding with libx264 rebuilds proper timestamps + moov
            # atom, so the resulting MP4 plays in HTML5 with correct
            # duration even when the input is mid-file corrupted.
            # `-preset ultrafast` keeps the cost ~real-time per second of
            # clip on a typical box; combined with output-side seek the
            # retry takes ~5-10 s for a 15 s cycle.
            # 2026-08-11 — HYBRID SEEK.  This retry used output-side -ss alone,
            # which makes ffmpeg decode the file from byte 0 and throw all of it
            # away to reach the cycle.  Fine when the offset is small; these TS
            # files run ~50 minutes, so a cycle near the end meant decoding
            # ~4.4 HOURS of video for a 10 s clip — measured at 40+ s and ~5.7
            # cores EACH, with 7-8 running at once (2,961 of these on the day it
            # was found).  That was ~40 of the box's 64 cores, and it is why the
            # clip archive could never get any headroom.
            #
            # Coarse input-side seek to _SEEK_PAD seconds before the target
            # (instant — keyframe index, no decoding), then the SAME output-side
            # -ss for the last few seconds.  The frame-accurate positioning is
            # unchanged because the output seek still does the fine work; it
            # just no longer has the whole file to chew through first.
            _SEEK_PAD = 6.0
            if _eff_ss > _SEEK_PAD * 2:
                _pre_ss, _post_ss = _eff_ss - _SEEK_PAD, _SEEK_PAD
            else:
                # Already near the head — the old single-seek form is cheap here
                # and avoids any edge case with a tiny pre-roll.
                _pre_ss, _post_ss = None, _eff_ss
            retry_cmd = [
                ffmpeg, "-y",
                # 2026-05-18 — dropped +igndts (causes out-of-order
                # frames → garbled re-encode).
                "-fflags",     "+genpts+discardcorrupt",
                "-err_detect", "ignore_err",
                *_concat_in,                 # concat demuxer when stitching TS
                *(["-ss", f"{_pre_ss:.3f}"] if _pre_ss is not None else []),
                "-i", _eff_src,              # single TS or concat listfile
                "-ss", f"{_post_ss:.3f}",    # output seek = frame-accurate
                "-t", f"{duration:.3f}",
                # 2026-05-19 — HW encode (NVENC→QSV→libx264) on retry path too.
                "-c:v", _hw_codec,
                *_hw_flags,
                "-pix_fmt", "yuv420p",
                "-an",
                "-vsync", "cfr",             # stable timing
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                tmp_abs,
            ]
            try:
                proc2 = subprocess.Popen(retry_cmd,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
                try:
                    proc2.wait(timeout=max_timeout)
                    result_rc = proc2.returncode
                except subprocess.TimeoutExpired:
                    proc2.kill(); proc2.wait()
                    print(f"[PLC] #{cycle_number} retry timed out")
                    for _ in range(5):
                        try: os.remove(tmp_abs); break
                        except OSError: time.sleep(0.3)
                    return ""
            except Exception as exc:
                print(f"[PLC] #{cycle_number} retry error: {exc}")
                for _ in range(5):
                    try: os.remove(tmp_abs); break
                    except OSError: time.sleep(0.3)
                return ""
            size_actual = os.path.getsize(tmp_abs) if os.path.exists(tmp_abs) else 0
            actual_dur, probe_ok = (_probe_duration(tmp_abs)
                                      if size_actual > 0 else (0.0, False))
            if probe_ok:
                dur_ok = actual_dur >= _min_acceptable(duration)
            else:
                dur_ok = True
            print(f"[PLC] #{cycle_number} retry produced "
                  f"{size_actual//1024}KB / "
                  f"{actual_dur:.1f}s{'' if probe_ok else ' (probe failed)'} "
                  f"(target {duration:.0f}s, "
                  f"min_acceptable={_min_acceptable(duration):.1f}s, "
                  f"dur_ok={dur_ok})")

        # 2026-07-22 — stitch temp listfile is no longer needed once both the
        # primary and retry ffmpeg passes have run; remove it so the videos
        # dir doesn't fill with _concat_*.txt.
        if _concat_lst:
            try: os.remove(_concat_lst)
            except OSError: pass

        if (result_rc == 0 and os.path.exists(tmp_abs)
                and size_actual >= min_required and dur_ok):
            size_kb = size_actual // 1024
            try:
                # Atomic on the same filesystem; replaces any existing file.
                # Retry on Windows file-lock errors — ffmpeg sometimes holds
                # its output handle for a split second after exiting, which
                # was leaving files stuck as "_pending_..." on disk.
                last_exc = None
                for _ in range(5):
                    try:
                        os.replace(tmp_abs, file_abs)
                        last_exc = None
                        break
                    except OSError as _e:
                        last_exc = _e
                        time.sleep(0.3)
                if last_exc is not None:
                    raise last_exc
            except OSError as exc:
                print(f"[PLC] Rename {tmp_abs} -> {file_abs} failed: {exc}")
                for _ in range(5):
                    try:
                        os.remove(tmp_abs)
                        break
                    except OSError:
                        time.sleep(0.3)
                return ""
            # Copy to structured folder if configured
            if structured_abs:
                try:
                    import shutil
                    shutil.copy2(file_abs, structured_abs)
                    print(f"[PLC] Structured copy: {structured_abs}")
                except Exception as exc:
                    print(f"[PLC] Structured copy failed: {exc}")
            print(f"[PLC] Cycle video extracted: {file_rel} ({size_kb} KB)")
            return file_rel

        # Failed — clean up the temp file.  Also nuke any prior file at the
        # final path: if an old broken-tiny / wrong-duration clip is sitting
        # there from a previous failed extraction, by-part lookup would
        # still return it and play 0:00 (or 2 s for a 28 s cycle) in the
        # browser.  Deleting it forces a clean 404 (UI shows "no video"
        # instead of a broken player).
        try: os.remove(tmp_abs)
        except OSError: pass
        try:
            if os.path.exists(file_abs):
                stale_size = os.path.getsize(file_abs)
                stale_dur, stale_probe_ok = _probe_duration(file_abs)
                stale_bad = stale_size < min_required or (
                    stale_probe_ok and stale_dur < _min_acceptable(duration))
                if stale_bad:
                    os.remove(file_abs)
                    print(f"[PLC] Removed stale clip at {file_abs} "
                          f"(size={stale_size//1024}KB dur={stale_dur:.1f}s)")
        except OSError: pass
        print(f"[PLC] Cycle video extraction failed for #{cycle_number} "
              f"(rc={result_rc}, size={size_actual//1024}KB need={min_required//1024}KB, "
              f"dur={actual_dur:.1f}s need>={_min_acceptable(duration):.1f}s)")
        return ""

    # ─── TS file cleanup ─────────────────────────────────────────────────────

    def _cleanup_old_ts(self, ts_file: str) -> None:
        """Delete a TS file once it is no longer referenced by any live recorder
        or pending cycle worker.  Safe to call from a background thread.

        2026-05-28 — GRACE PERIOD.  Operator's camera dies every 10-30 s
        and pumps the TS-rotate path constantly.  Earlier cleanup would
        delete a TS the moment its recorder died — but a cycle that
        ended seconds before death is still trying to finalize using
        that file in a background thread.  The finalize then errors
        out with `[PLC] TS file missing: ...` and the operator loses
        the MP4 even though it could have been extracted.

        Fix: only delete TS files whose mtime is >= TS_KEEP_GRACE_SEC
        old (i.e., recorder has truly stopped writing for that long).
        Fresh-killed TS files get a window for pending cycles to
        finalize.  Files older than the grace period are deleted as
        before (no disk-space regression).

        2026-05-29 - Bumped 60s -> 3600s (1 hour) for Final Inspection
        operator who complained "video thoda issue kr rhi h" — clicking
        any chart cycle older than ~90s gave a 829-byte empty MP4 (TS
        already rotated out).  At ~10 MB per 30-s TS file = ~1.2 GB/hr
        retention.  Acceptable for a workstation; covers a full shift
        of historical clip retrieval."""
        TS_KEEP_GRACE_SEC = TS_KEEP_SEC      # 48 h footage hold (was 3600)
        if not ts_file or not ts_file.endswith(".ts"):
            return
        for cam in self._camera_workers.values():
            if cam.get("ts_file") == ts_file:
                return  # still the live recording
        for w in self._video_workers.values():
            if w.get("ts_file") == ts_file:
                return  # another cycle is still extracting from it
        try:
            if not os.path.exists(ts_file):
                return
            # Grace check — recently-modified TS is preserved for
            # in-flight cycle finalize.
            age = time.time() - os.path.getmtime(ts_file)
            if age < TS_KEEP_GRACE_SEC:
                # Re-schedule cleanup for later via the finalize path;
                # not deleting now is safe (extra MB on disk for ~1 min).
                return
            os.remove(ts_file)
            print(f"[PLC] Cleaned up old TS: {os.path.basename(ts_file)} "
                  f"(age={age:.0f}s)")
        except OSError as exc:
            print(f"[PLC] TS cleanup error: {exc}")

    # ─── Shift/slot helpers for structured video folders ────────────────────

    def _get_current_shift_name(self, dt: datetime) -> str:
        """Determine shift name from shifts.json based on time."""
        try:
            from shifts_config import list_shifts
            shifts = list_shifts(self.base_dir)
            h, m = dt.hour, dt.minute
            t_min = h * 60 + m
            for s in shifts:
                ss = sum(int(x) * (60 if i == 0 else 1) for i, x in enumerate(s["start"].split(":")))
                se = sum(int(x) * (60 if i == 0 else 1) for i, x in enumerate(s["end"].split(":")))
                if se > ss:
                    if ss <= t_min < se:
                        return s.get("name", s.get("id", "Unknown"))
                else:  # crosses midnight
                    if t_min >= ss or t_min < se:
                        return s.get("name", s.get("id", "Unknown"))
        except Exception:
            pass
        # Fallback: simple A/B
        h = dt.hour
        if 6 <= h < 18:
            return "ShiftA"
        return "ShiftB"

    def _get_slot_label(self, dt: datetime) -> str:
        """Return hourly slot label like '08:30-09:30'."""
        h, m = dt.hour, dt.minute
        # Round down to nearest hour slot
        slot_start_h = h
        slot_start_m = 30 if m >= 30 else 0
        slot_end_h = slot_start_h + (1 if slot_start_m == 30 else 0)
        slot_end_m = 30 if slot_start_m == 0 else 0
        if slot_end_h >= 24:
            slot_end_h -= 24
        return f"{slot_start_h:02d}:{slot_start_m:02d}-{slot_end_h:02d}:{slot_end_m:02d}"

    # ─── pymcprotocol connection helpers ─────────────────────────────────────

    def _get_conn(self, plc_id: str, ip: str, port: int) -> Optional[pymcprotocol.Type4E]:
        with self._plc_lock:
            if plc_id in self._plc_conns:
                return self._plc_conns[plc_id]
        # Respect cool-down after previous failure so we don't spam the PLC
        # with connect attempts every 300 ms when another process owns the slot.
        now = time.time()
        next_retry = self._plc_next_retry.get(plc_id, 0.0)
        if now < next_retry:
            return None
        try:
            conn = pymcprotocol.Type4E()
            conn.connect(ip, port)
            with self._plc_lock:
                self._plc_conns[plc_id] = conn
            self._connected_plcs[plc_id] = True
            # Clear back-off on success
            self._plc_next_retry.pop(plc_id, None)
            print(f"[PLC] Connected to {plc_id} at {ip}:{port}")
            return conn
        except Exception as exc:
            # Throttle retries — 60 s cool-down, and only log on transition
            # from "ok" to "down" to keep the log clean.
            was_ok = self._connected_plcs.get(plc_id, True)
            self._plc_next_retry[plc_id] = now + 60
            if was_ok:
                print(f"[PLC] Cannot connect to {plc_id} at {ip}:{port}: {exc} — "
                      f"cool-down 60s (likely another process owns the MC slot)")
            return None

    def _drop_conn(self, plc_id: str) -> None:
        with self._plc_lock:
            conn = self._plc_conns.pop(plc_id, None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # ─── Public helpers for api_server ───────────────────────────────────────

    def get_ts_file(self, camera_id: str) -> Optional[str]:
        """Return the live TS file path for this camera, or None if not recording."""
        cam = self._camera_workers.get(camera_id)
        if cam and cam["proc"].poll() is None:
            return cam["ts_file"]
        return None

    def camera_recording_allowed(self, camera_id: str) -> bool:
        """Public wrapper for the per-line/master video gate (used by the MJPEG
        endpoints in api_server so live view is also off for disabled lines)."""
        return _camera_recording_allowed(camera_id, self.base_dir)

    # ─── Machine metadata helper ──────────────────────────────────────────────

    def _get_machine_meta(self, machine_id: str) -> Dict:
        """Resolve zone_name + line_name + machine_name for a given machine_id.

        Two sources, in order:
          1. zones.json via `all_machines_flat` — covers legacy local-only
             machines (created via the old MachineMaster CRUD).
          2. camera_config_bindings.json — covers MES-driven machines
             that use synthetic `mes:<plc_id>` ids.  The /api/mes/machine
             handler stuffs zone_name / line_name onto the binding at
             save time (see _sync_binding_from_machine in api_server.py)
             so plc_monitor can write videos under videos/<line>/ without
             a per-cycle MES round-trip.
        """
        now = time.time()
        if self._machine_meta_cache is None or (now - self._machine_meta_ts) > 30:
            cache: Dict[str, Dict] = {
                m["machine_id"]: m for m in all_machines_flat(self.base_dir)
            }
            try:
                for b in list_bindings(self.base_dir):
                    mid = str(b.get("machine_id", "")).strip()
                    if not mid or mid in cache:
                        continue
                    cache[mid] = {
                        "machine_id":   mid,
                        "machine_name": b.get("machine_name", ""),
                        "zone_name":    b.get("zone_name", ""),
                        "line_name":    b.get("line_name", ""),
                        "camera_id":    b.get("camera_id", ""),
                    }
            except Exception as exc:
                print(f"[PLC] meta-cache binding merge error: {exc}")
            self._machine_meta_cache = cache
            self._machine_meta_ts = now
        return self._machine_meta_cache.get(machine_id, {})
