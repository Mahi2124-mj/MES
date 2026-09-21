from __future__ import annotations

import csv
import json
import os
import contextlib
import threading
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List

import cv2
from flask import Flask, jsonify, request, send_file, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from auth import authenticate, create_token, require_auth, require_role
from camera_config import add_camera, update_camera, delete_camera, list_cameras, get_camera_rtsp_url
from cycle_state import (
    end_cycle, get_all_states, get_machine_state,
    get_next_cycle_num, start_cycle,
)
from plc_config import list_plcs, add_plc, update_plc, delete_plc, load_plc_config, update_plc_config
from shifts_config import list_shifts, add_or_update_shift, delete_shift
from camera_bindings import list_bindings, add_binding, delete_binding
import mes_client
from plc_monitor import PlcMonitor
from recorder import DEFAULT_METADATA_CSV, ensure_metadata_file, open_rtsp_capture
from camera_stream import get_stream, stop_all as stop_all_streams
from zone_config import (
    list_zones, get_lines_for_zone, get_machines_for_line,
    add_zone, add_line, add_machine,
    delete_zone, delete_line, delete_machine,
    rename_zone, rename_line, rename_machine,
    assign_camera, all_machines_flat, list_all_lines_flat
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, DEFAULT_METADATA_CSV)

app = Flask(__name__)
import os as _os_cors
_DEFAULT_ORIGINS = (
    "http://localhost:5575,http://127.0.0.1:5575,"
    "http://localhost:5656,http://127.0.0.1:5656,"
    "http://localhost:3000,http://127.0.0.1:3000"
)
_ALLOWED_ORIGINS = [
    o.strip() for o in _os_cors.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]
CORS(app, resources={
    # Videos are read-only media streams — accept any origin so ngrok /
    # devtunnel / TV browsers can load them without CORS trouble. The
    # filename is the only secret, and /api/video requires JWT anyway.
    r"/api/video":         {"origins": "*"},
    r"/api/video/*":       {"origins": "*"},
    r"/api/submachine/*":  {"origins": "*"},
    r"/api/*":             {"origins": _ALLOWED_ORIGINS},
    r"/live_feed/*":       {"origins": "*"},   # MJPEG img src — allow all origins
    r"/camera_frame/*":    {"origins": "*"},   # JPEG snapshot  — allow all origins
})

# ─── Rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],           # no global limit — per-route only
    storage_uri="memory://",
)

plc_monitor = PlcMonitor(BASE_DIR, CSV_PATH)
plc_monitor.start()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _json_ok(data: Any = None, message: str = "OK", status: int = 200):
    return jsonify({"ok": True, "message": message, "data": data}), status


def _json_err(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message}), status


# mtime-based cycles cache — dashboard polls /api/overview every few
# seconds; re-parsing an 11 k-row CSV on every hit was the single biggest
# slow-down as the file grew. We now parse only when the file actually
# changes on disk (collector append).
_CYCLES_CACHE: Dict[str, Any] = {"mtime": -1.0, "rows": None}

# 60-second hit-cache for /api/video/by-part's structured-path glob.
# Key = sanitized part code; value = {"path": resolved_mp4_or_None, "exp": ts}
_SEARCH_CACHE: Dict[str, Dict[str, Any]] = {}


def _attempt_repair(safe_part: str, videos_root: str) -> str:
    """Auto-repair: when /api/video/by-part has no MP4 on disk, try to
    re-extract one on the spot from the most-recent matching live TS file.

    2026-05-18 — Solution D.  Combined with the source-pipeline keyframe
    fix in plc_monitor.py, this gives us per-shift self-healing: if a
    cycle's MP4 was never written or got deleted, a CSV lookup tells us
    which TS owns it and we re-encode the slice once on demand.

    Heuristic: scan `<videos_root>/cycles.csv` (the recorder's
    metadata) for the most-recent row whose part_code matches.  If found,
    locate its TS file (live-recorder writes them under videos_root/
    cam_*.ts) and re-extract using the same hybrid-seek + libx264
    parameters that the live extractor uses.  Returns the new MP4 path
    on success, None on any failure (caller falls back to 404)."""
    import csv as _csv, glob as _glob, re as _re
    try:
        # 2026-05-21 — Path bug fix.  cycles.csv actually lives at
        # BASE_DIR/cycles.csv (parent of videos_root), not inside
        # videos_root.  The earlier `os.path.join(videos_root,
        # "cycles.csv")` always silently returned "" → auto-repair
        # never actually fired → 404 was the only outcome whenever the
        # primary extract path failed (storm-guard / phantom-cycle cap
        # / TS file missing).  Try both locations to be safe.
        csv_path = os.path.join(videos_root, "cycles.csv")
        if not os.path.exists(csv_path):
            csv_path = os.path.join(os.path.dirname(videos_root), "cycles.csv")
        if not os.path.exists(csv_path):
            return ""
        matches = []
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                pc = _re.sub(r"[^A-Za-z0-9._-]", "_",
                             (row.get("part_code") or "")).strip("_")
                if pc == safe_part:
                    matches.append(row)
        if not matches:
            return ""
        latest = matches[-1]
        start_s = latest.get("start_time") or ""
        end_s   = latest.get("end_time")   or ""
        if not start_s or not end_s:
            return ""

        from datetime import datetime as _dt
        try:
            start_dt = _dt.fromisoformat(start_s)
            end_dt   = _dt.fromisoformat(end_s)
        except Exception:
            return ""

        # 2026-05-26 — CROSS-MACHINE BUG FIX.
        # Operator saw Upper Rail's camera feed inside a Final Inspection
        # MP4.  Root cause: this loop scanned ALL `cam_*.ts` files (every
        # camera, all 7 machines) and kept overwriting `best_ts` without
        # filtering by which camera actually owns the cycle row.  When
        # Final's MP4 was missing, the auto-repair re-encoded a slice from
        # whatever camera's TS happened to be processed LAST (alphabetical
        # glob order → `cam_upper_rail_greasing_*` beats
        # `cam_panasonic_default_*`).  Result: a video file named after
        # Final's part_code but containing Upper Rail's view.
        #
        # Fix: look up the binding for `latest['machine_id']` and only
        # consider TS files whose name starts with `cam_{camera_id}_`.
        # Bindings are tiny + cached; cost is negligible.
        camera_id_filter = ""
        try:
            mid = str(latest.get("machine_id") or "").strip()
            if mid:
                for _b in list_bindings(os.path.dirname(videos_root)):
                    if str(_b.get("machine_id", "")).strip() == mid:
                        camera_id_filter = str(_b.get("camera_id", "")).strip()
                        break
        except Exception:
            camera_id_filter = ""

        # Find the TS file that contains this time window.  TS files are
        # named cam_{camera_id}_{epoch_ms}.ts so the epoch tells us the
        # record_start.  Pick the one whose start <= cycle_start and
        # whose mtime (last frame) >= cycle_end (or live now).
        # 2026-05-21 — TS now lives at video_config.save_path (D:\MES_Videos)
        # not just videos_root.  Search BOTH locations so auto-repair
        # works after the save-path migration.
        ts_candidates = _glob.glob(os.path.join(videos_root, "cam_*.ts"))
        try:
            _custom = _load_video_cfg().get("save_path", "")
            if _custom and os.path.isdir(_custom):
                ts_candidates += _glob.glob(os.path.join(_custom, "cam_*.ts"))
        except Exception:
            pass
        best_ts = None
        for ts_path in ts_candidates:
            base = os.path.basename(ts_path)
            # 2026-05-26 — STRICT camera filter (see comment above).
            # Without this, any camera's TS that covered the time window
            # could win.  We require the basename to start with the exact
            # `cam_{camera_id}_` prefix derived from the cycle's binding.
            if camera_id_filter and not base.startswith(f"cam_{camera_id_filter}_"):
                continue
            m = _re.search(r"_(\d{10,})\.ts$", base)
            if not m:
                continue
            try:
                rec_start = _dt.fromtimestamp(int(m.group(1)) / 1000.0)
            except Exception:
                continue
            if rec_start > start_dt:
                continue                       # TS started AFTER cycle began
            # Need ts to cover cycle_end (file growing = mtime ≈ now)
            try:
                ts_mtime = _dt.fromtimestamp(os.path.getmtime(ts_path))
            except Exception:
                continue
            if ts_mtime < end_dt:
                continue                       # TS doesn't extend past cycle
            # Prefer the TS whose rec_start is CLOSEST to (but not after)
            # the cycle start — that minimises the seek offset and avoids
            # picking up an older TS from a previous shift.
            if best_ts is None or rec_start > best_ts[1]:
                best_ts = (ts_path, rec_start)
        if best_ts is None:
            return ""

        ts_path, rec_start = best_ts
        dur_raw   = (end_dt - start_dt).total_seconds()
        if dur_raw <= 0:
            return ""
        # 2026-05-23 — NO CAP.  Operator: "video me koii cap nahi
        # rahegi, jitni der ki cycle utni ki video".  Trust the
        # (start_dt, end_dt) window exactly as recorded — if the cycle
        # was 109s, the video is 109s.  No "phantom merge" clipping.
        ss        = max(0.0, (start_dt - rec_start).total_seconds())
        import math as _math
        duration  = max(1.0, float(_math.ceil(dur_raw)))

        # Hybrid seek — same as source path
        input_ss  = max(0.0, ss - 1.5)
        output_ss = max(0.0, ss - input_ss)

        try:
            from plc_monitor import _get_ffmpeg, _pick_hw_encoder  # type: ignore
            ffmpeg = _get_ffmpeg()
            _hw_codec, _hw_flags = _pick_hw_encoder()
        except Exception:
            ffmpeg = "ffmpeg"
            _hw_codec, _hw_flags = "libx264", ["-preset", "ultrafast", "-crf", "23"]

        out_path = os.path.join(videos_root, f"{safe_part}.mp4")
        # 2026-05-19 — HW encode (NVENC > QSV > libx264) on rescue/repair too.
        # Same probed encoder as the main cycle extractor in plc_monitor.
        cmd = [
            ffmpeg, "-y",
            "-fflags",     "+genpts+discardcorrupt",
            "-err_detect", "ignore_err",
            # 2026-08-02 — conceal lost macroblocks from the previous frame
            # instead of rendering them as flat GREEN.  The camera network
            # drops RTP packets, so a -c copy recording genuinely has holes;
            # favor_inter makes what survives watchable rather than a green wall.
            "-ec", "favor_inter",
            "-ss", f"{input_ss:.3f}",
            "-i", ts_path,
            "-ss", f"{output_ss:.3f}",
            "-t", f"{duration:.3f}",
            "-c:v", _hw_codec,
            *_hw_flags,
            "-pix_fmt", "yuv420p",
            "-an",
            "-vsync", "cfr",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            out_path,
        ]
        try:
            r = subprocess.run(cmd,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=max(30, int(duration * 3)))
            if r.returncode == 0 and os.path.exists(out_path):
                # bust the search cache so the new file is found
                _SEARCH_CACHE.pop(safe_part, None)
                print(f"[REPAIR] regenerated {safe_part}.mp4 "
                      f"from {os.path.basename(ts_path)} ss={ss:.1f} "
                      f"dur={duration:.0f}s")
                return out_path
        except subprocess.TimeoutExpired:
            print(f"[REPAIR] ffmpeg timeout for {safe_part}")
        except Exception as e:
            print(f"[REPAIR] ffmpeg error for {safe_part}: {e}")
    except Exception as e:
        print(f"[REPAIR] unexpected error: {e}")
    return ""

def _read_cycles(limit: int | None = None) -> List[Dict[str, Any]]:
    ensure_metadata_file(CSV_PATH)
    try:
        mtime = os.path.getmtime(CSV_PATH)
    except OSError:
        mtime = 0.0
    cached_rows = _CYCLES_CACHE.get("rows")
    if cached_rows is not None and _CYCLES_CACHE["mtime"] == mtime:
        return cached_rows[:limit] if limit else cached_rows

    binding_map = {
        str(binding.get("machine_id", "")): int(binding.get("target_time", 0) or 0)
        for binding in list_bindings(BASE_DIR)
        if str(binding.get("machine_id", "")).strip()
    }

    def _clean(s: Any) -> str:
        """Strip NUL bytes + control chars — occasional power-loss mid-write
        can leave a partial row with \\x00 padding that crashes int()."""
        if s is None:
            return ""
        return str(s).replace("\x00", "").strip()

    def _safe_int(v: Any, default: int = 0) -> int:
        try:
            return int(_clean(v) or default)
        except (ValueError, TypeError):
            return default

    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(_clean(v) or default)
        except (ValueError, TypeError):
            return default

    rows: List[Dict[str, Any]] = []
    skipped = 0
    # errors="replace" so a single corrupt byte never blows up the whole
    # reader — pair with NUL stripping below.
    with open(CSV_PATH, "r", newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            try:
                machine_id = _clean(row.get("machine_id", ""))
                cn = _safe_int(row.get("cycle_number", 0))
                # Rows without a valid cycle_number are garbage (partial writes)
                if cn <= 0 and not row.get("start_time"):
                    skipped += 1
                    continue
                rows.append({
                    "cycle_number": cn,
                    "start_time":   _clean(row.get("start_time", "")),
                    "end_time":     _clean(row.get("end_time", "")),
                    "duration":     int(_safe_float(row.get("duration", 0))),
                    "file_path":    _clean(row.get("file_path", "")),
                    "machine_id":   machine_id,
                    "machine_name": _clean(row.get("machine_name", "")),
                    "line_name":    _clean(row.get("line_name", "")),
                    "zone_name":    _clean(row.get("zone_name", "")),
                    "shift":        _clean(row.get("shift", "")),
                    "tag":          _clean(row.get("tag", "")),
                    "part_code":    _clean(row.get("part_code", "")),
                    "target_time":  binding_map.get(machine_id),
                })
            except Exception:
                skipped += 1
                continue
    if skipped:
        print(f"[CYCLES] Skipped {skipped} corrupt/partial row(s) in {CSV_PATH}")
    rows.sort(key=lambda item: item["cycle_number"], reverse=True)
    _CYCLES_CACHE["mtime"] = mtime
    _CYCLES_CACHE["rows"]  = rows
    return rows[:limit] if limit else rows


def _camera_name_map() -> Dict[str, str]:
    return {str(cam.get("id", "")): str(cam.get("name", "")) for cam in list_cameras(BASE_DIR)}


def _binding_camera_map() -> Dict[str, str]:
    return {
        str(binding.get("machine_id", "")): str(binding.get("camera_id", "")).strip()
        for binding in list_bindings(BASE_DIR)
        if str(binding.get("machine_id", "")).strip()
    }


def _flat_zones() -> List[Dict[str, Any]]:
    zones = []
    for z in list_zones(BASE_DIR):
        zones.append({
            "zone_id": z.get("id", ""),
            "zone_name": z.get("name", ""),
            "line_count": len(z.get("lines", []))
        })
    return zones

def _flat_machines() -> List[Dict[str, str]]:
    camera_names = _camera_name_map()
    binding_camera_map = _binding_camera_map()
    rows = []
    for machine in all_machines_flat(BASE_DIR):
        camera_id = str(machine.get("camera_id") or binding_camera_map.get(machine.get("machine_id", ""), "")).strip()
        rows.append({
            **machine,
            "camera_id": camera_id,
            "camera_name": camera_names.get(camera_id, "Unassigned"),
        })
    return rows

def _hierarchy() -> List[Dict[str, Any]]:
    camera_names = _camera_name_map()
    result = []
    for zone in list_zones(BASE_DIR):
        zone_payload = {"id": zone.get("id", ""), "name": zone.get("name", ""), "lines": []}
        for line in zone.get("lines", []):
            line_payload = {"id": line.get("id", ""), "name": line.get("name", ""), "machines": []}
            for machine in line.get("machines", []):
                line_payload["machines"].append({
                    "id": machine.get("id", ""),
                    "name": machine.get("name", ""),
                    "camera_id": machine.get("camera_id"),
                    "camera_name": camera_names.get(str(machine.get("camera_id", "")), "Unassigned"),
                })
            zone_payload["lines"].append(line_payload)
        result.append(zone_payload)
    return result

def _camera_grid() -> List[Dict[str, Any]]:
    """Return flat list of machines enriched with camera + cycle state.

    Two sources merged:
      1. MES Postgres via `/api/cms-sync/machines` — source of truth.
         Every machine added through the MES Admin Panel lives here,
         joined with zone_name + line_name + nf2_camera_id from
         `mes_plc_configs`.
      2. Local zones.json (`all_machines_flat`) — legacy fallback for
         zones MES doesn't manage yet (e.g. plants that haven't been
         migrated).

    2026-05-14 dedup rule: MES wins per ZONE.  If MES has even one
    machine in a zone, ALL legacy zones.json entries for that zone are
    dropped — fixes the "same line appearing twice" System Map bug
    where 'YNC Seat Slider' (legacy) and 'YNC-SS' (MES) both showed
    under SEAT SLIDER because machine_id dedup didn't catch them
    (`machine_273` vs `mes:2` were different keys).
    """
    camera_names = _camera_name_map()
    cameras_map: Dict[str, Dict] = {str(c.get("id", "")): c for c in list_cameras(BASE_DIR)}
    binding_camera_map = _binding_camera_map()
    cycle_states = get_all_states(BASE_DIR)
    seen_ids: set = set()
    rows: List[Dict[str, Any]] = []

    # ── Source 1 (PRIMARY): MES Postgres ─────────────────────────────
    # Best-effort — if MES is down we fall through and serve only the
    # legacy rows so the Camera CMS doesn't black out during a restart.
    try:
        ok, mes_machines = mes_client.list_machines()
    except Exception:
        ok, mes_machines = False, []

    # Track which zones MES manages so we can suppress legacy duplicates
    # for the same zone.  Case-insensitive + trim — "SEAT SLIDER",
    # "Seat Slider", "seat slider" all collapse to one key.
    mes_zone_keys: set = set()

    if ok and isinstance(mes_machines, list):
        for m in mes_machines:
            try:
                plc_id = int(m.get("id", 0))
            except (TypeError, ValueError):
                continue
            if plc_id <= 0:
                continue
            mid = f"mes:{plc_id}"
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            zname = (m.get("zone_name") or "").strip()
            if zname:
                mes_zone_keys.add(zname.lower())
            cam_id = str(m.get("nf2_camera_id") or "").strip()
            cam = cameras_map.get(cam_id, {})
            cstate = cycle_states.get(mid, {})
            rows.append({
                "zone_id":      zname,           # MES zones use name as the stable key
                "zone_name":    zname,
                "line_id":      m.get("line_id"),
                "line_name":    m.get("line_name") or "",
                "machine_id":   mid,
                "machine_name": m.get("machine_name") or "",
                "camera_id":    cam_id,
                "camera_name":  camera_names.get(cam_id, "Unassigned"),
                "camera_ip":    cam.get("ip", ""),
                "camera_port":  cam.get("port", 554),
                "has_camera":   bool(cam_id and cam),
                "recording":    cstate.get("recording", False),
                "cycle_number": cstate.get("cycle_number"),
                "cycle_start":  cstate.get("start_time"),
            })

    # ── Source 2 (FALLBACK): local zones.json ────────────────────────
    # Iterate AFTER MES so we know which zones to suppress.  A legacy
    # row only survives if its zone has zero MES-managed machines.
    for machine in all_machines_flat(BASE_DIR):
        mid = str(machine.get("machine_id", ""))
        if not mid:
            continue
        zname = (machine.get("zone_name") or "").strip()
        if zname and zname.lower() in mes_zone_keys:
            # MES already owns this zone — skip the legacy duplicate so
            # the System Map shows one canonical line per zone.
            continue
        cam_id = str(machine.get("camera_id") or binding_camera_map.get(mid, "")).strip()
        cam = cameras_map.get(cam_id, {})
        cstate = cycle_states.get(mid, {})
        seen_ids.add(mid)
        rows.append({
            "zone_id":      machine["zone_id"],
            "zone_name":    machine["zone_name"],
            "line_id":      machine["line_id"],
            "line_name":    machine["line_name"],
            "machine_id":   mid,
            "machine_name": machine["machine_name"],
            "camera_id":    cam_id,
            "camera_name":  camera_names.get(cam_id, "Unassigned"),
            "camera_ip":    cam.get("ip", ""),
            "camera_port":  cam.get("port", 554),
            "has_camera":   bool(cam_id and cam),
            "recording":    cstate.get("recording", False),
            "cycle_number": cstate.get("cycle_number"),
            "cycle_start":  cstate.get("start_time"),
        })

    return rows


def _validate_name(val: str, field: str = "Name", max_len: int = 80) -> tuple[bool, str]:
    """Validate a name field — non-empty and within max length."""
    val = val.strip()
    if not val:
        return False, f"{field} is required"
    if len(val) > max_len:
        return False, f"{field} must be {max_len} characters or fewer"
    return True, val


# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def healthcheck():
    return _json_ok({"status": "healthy"})


# ─── Corrupted-clip sweep (admin) ─────────────────────────────────────────────
# Solution B (2026-05-18) — for cycles whose MP4 was produced by the pre-fix
# ffmpeg path (with the +igndts bug), this scans all MP4s under videos/ and
# removes any that ffprobe rejects.  Next cycle of the same part code
# auto-regenerates a clean MP4 via the new extractor.

def _ffprobe_corrupt(file_path: str, ffmpeg_bin: str) -> bool:
    """Return True if ffmpeg can't decode the first second of this MP4.

    2026-05-18 — Augmented with VISUAL-corruption detection.  The
    +igndts encoder bug produced MP4s that are syntactically valid (rc=0,
    no stderr warnings) but contain gray-garbage pixel data — H.264
    re-encoded the corrupt decoder output verbatim, so it "decodes
    cleanly" to a uniform gray frame with a few smudges.
    Approach: dump a single mid-clip frame to YUV and inspect the
    luma variance.  A real machine-floor frame has stddev > 30 typically;
    a corrupt gray-blob frame has stddev < 12.  Threshold at 18 catches
    the broken clips without false-positiving on legit dark / empty
    shop-floor scenes.
    """
    try:
        # ── Step 1: classic syntax check ────────────────────────────
        r = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-i", file_path,
             "-t", "1", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=8,
        )
        if r.returncode != 0:
            return True
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        bad_signs = [
            "Invalid data found",
            "non-existing PPS",
            "non-existing SPS",
            "decode_slice_header error",
            "no frame!",
            "concealing",
            "error while decoding",
        ]
        if any(s in err for s in bad_signs):
            return True

        # ── Step 2: VISUAL check — dump a mid-clip frame as raw gray
        # to stdout and measure its standard deviation in Python.  No
        # ffprobe / filter-metadata fragility — works on any ffmpeg
        # build.  A real shop-floor frame: stddev typically 30-80.
        # A "+igndts-corrupted" frame: uniform mid-gray, stddev < 12.
        # Threshold 18 catches the broken clips without false-positive
        # on legit dark / clean-background scenes.
        # Scale-down to 320x180 first so we move at most ~58 KB through
        # the pipe — fast and statistically equivalent for variance.
        r = subprocess.run(
            [ffmpeg_bin, "-v", "quiet",
             "-ss", "4",
             "-i", file_path,
             "-vframes", "1",
             "-vf", "scale=320:180,format=gray",
             "-f", "rawvideo",
             "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        raw = r.stdout or b""
        if len(raw) < 320 * 180:
            # Couldn't decode the frame at all — be safe, don't delete
            return False
        # Variance on the gray channel — no numpy import for portability
        n = len(raw)
        s  = 0
        s2 = 0
        for byte in raw[:n]:
            s  += byte
            s2 += byte * byte
        mean = s / n
        var  = (s2 / n) - (mean * mean)
        stddev = var ** 0.5
        return stddev < 18.0
    except subprocess.TimeoutExpired:
        return True       # hung on decode = treat as broken
    except Exception:
        return False      # be conservative on tool errors


@app.post("/api/admin/sweep-corrupted-clips")
def sweep_corrupted_clips():
    """Walk videos/ tree and delete every MP4 ffmpeg can't decode.
    Returns the list of deleted files + counts.  Safe to call repeatedly
    — non-corrupt files are left alone."""
    dry_run = (request.args.get("dry_run", "0") in ("1", "true", "yes"))
    videos_root = os.path.normpath(os.path.join(BASE_DIR, "videos"))
    if not os.path.isdir(videos_root):
        return _json_err("videos folder missing")

    try:
        from plc_monitor import _get_ffmpeg  # type: ignore
        ffmpeg = _get_ffmpeg()
    except Exception:
        ffmpeg = "ffmpeg"

    scanned = 0
    deleted = []
    skipped = []
    for root_dir, _dirs, files in os.walk(videos_root):
        for fname in files:
            if not fname.lower().endswith(".mp4"):
                continue
            if fname.startswith("_pending_"):
                continue       # active extraction in-flight — leave alone
            full = os.path.join(root_dir, fname)
            scanned += 1
            try:
                size = os.path.getsize(full)
            except Exception:
                continue
            # Skip empty or stub files — those are separately handled
            # by the cycle extractor's own size_actual gate.
            if size < 8 * 1024:
                continue
            if _ffprobe_corrupt(full, ffmpeg):
                if dry_run:
                    deleted.append(os.path.relpath(full, videos_root))
                else:
                    try:
                        os.remove(full)
                        # bust by-part search cache so the next hit
                        # tries auto-repair instead of stale 404
                        base = os.path.splitext(fname)[0]
                        _SEARCH_CACHE.pop(base, None)
                        deleted.append(os.path.relpath(full, videos_root))
                    except OSError as e:
                        skipped.append({"file": fname, "err": str(e)})

    return _json_ok({
        "scanned":     scanned,
        "deleted":     deleted,
        "deleted_n":   len(deleted),
        "skipped":     skipped,
        "dry_run":     dry_run,
    })


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
@limiter.limit("10 per minute")   # brute-force protection
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        return _json_err("Username and password are required")
    ok, user = authenticate(username, password, BASE_DIR)
    if not ok or not user:
        return _json_err("Invalid username or password", 401)
    token = create_token(user)
    return _json_ok({
        "token": token,
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role"),
        "display_name": user.get("display_name", user.get("username")),
    }, "Login successful")


@app.get("/api/auth/me")
@require_auth
def auth_me():
    """Return current user info from JWT payload."""
    from flask import g
    return _json_ok(g.current_user)


# ─── Overview ─────────────────────────────────────────────────────────────────

@app.get("/api/overview")
@require_auth
def overview():
    zones = _flat_zones()
    machines = _flat_machines()
    cameras = list_cameras(BASE_DIR)
    all_cycles = _read_cycles()          # single read
    recent_cycles = all_cycles[:8]
    return _json_ok({
        "counts": {
            "zones": len(zones),
            "lines": len(list_all_lines_flat(BASE_DIR)),
            "machines": len(machines),
            "cameras": len(cameras),
            "cycles": len(all_cycles),   # reuse — no second CSV read
        },
        "recent_cycles": recent_cycles,
        "hierarchy": _hierarchy(),
    })


@app.get("/api/hierarchy")
@require_auth
def hierarchy():
    return _json_ok(_hierarchy())


# ─── Masters ──────────────────────────────────────────────────────────────────

@app.get("/api/masters/zones")
@require_auth
def get_zones():
    return _json_ok(_flat_zones())


@app.post("/api/masters/zones")
@require_auth
@require_role("admin", "supervisor")
def create_zone():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Zone name")
    if not valid:
        return _json_err(name_or_err)
    ok, message, zone_id = add_zone(name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message)
    return _json_ok({"id": zone_id}, message, 201)


@app.delete("/api/masters/zones/<zone_id>")
@require_auth
@require_role("admin")
def remove_zone(zone_id: str):
    ok, message = delete_zone(zone_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/zones/<zone_id>")
@require_auth
@require_role("admin", "supervisor")
def update_zone(zone_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Zone name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_zone(zone_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/lines")
@require_auth
def get_lines():
    zone_id = request.args.get("zone_id")
    if zone_id:
        lines = [
            {
                "zone_id": zone_id,
                "zone_name": next((z.get("name", "") for z in list_zones(BASE_DIR) if z.get("id") == zone_id), ""),
                "id": line.get("id", ""),
                "name": line.get("name", ""),
                "machine_count": len(line.get("machines", [])),
            }
            for line in get_lines_for_zone(zone_id, BASE_DIR)
        ]
        return _json_ok(lines)
    return _json_ok(list_all_lines_flat(BASE_DIR))


@app.post("/api/masters/lines")
@require_auth
@require_role("admin", "supervisor")
def create_line():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Line name")
    if not valid:
        return _json_err(name_or_err)
    zone_id = str(payload.get("zone_id", "")).strip()
    if not zone_id:
        return _json_err("zone_id is required")
    ok, message, line_id = add_line(zone_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message)
    return _json_ok({"id": line_id}, message, 201)


@app.delete("/api/masters/lines/<zone_id>/<line_id>")
@require_auth
@require_role("admin")
def remove_line(zone_id: str, line_id: str):
    ok, message = delete_line(zone_id, line_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/lines/<zone_id>/<line_id>")
@require_auth
@require_role("admin", "supervisor")
def update_line(zone_id: str, line_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Line name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_line(zone_id, line_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/machines")
@require_auth
def get_machines():
    return _json_ok(_flat_machines())


@app.post("/api/masters/machines")
@require_auth
@require_role("admin", "supervisor")
def create_machine():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Machine name")
    if not valid:
        return _json_err(name_or_err)
    zone_id = str(payload.get("zone_id", "")).strip()
    line_id = str(payload.get("line_id", "")).strip()
    if not zone_id or not line_id:
        return _json_err("zone_id and line_id are required")
    ok, message, machine_id = add_machine(
        zone_id, line_id, name_or_err,
        camera_id=payload.get("camera_id") or None,
        base_dir=BASE_DIR,
    )
    if not ok:
        return _json_err(message)
    return _json_ok({"id": machine_id}, message, 201)


@app.delete("/api/masters/machines/<zone_id>/<line_id>/<machine_id>")
@require_auth
@require_role("admin")
def remove_machine(zone_id: str, line_id: str, machine_id: str):
    ok, message = delete_machine(zone_id, line_id, machine_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/machines/<zone_id>/<line_id>/<machine_id>")
@require_auth
@require_role("admin", "supervisor")
def update_machine(zone_id: str, line_id: str, machine_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Machine name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_machine(zone_id, line_id, machine_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/machines/<zone_id>/<line_id>/<machine_id>/camera")
@require_auth
@require_role("admin", "supervisor")
def update_machine_camera(zone_id: str, line_id: str, machine_id: str):
    payload = request.get_json(silent=True) or {}
    camera_id = str(payload.get("camera_id", "")).strip()
    if not camera_id:
        return _json_err("camera_id is required")
    ok, message = assign_camera(zone_id, line_id, machine_id, camera_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/cameras")
@require_auth
def get_cameras():
    return _json_ok(list_cameras(BASE_DIR))


@app.post("/api/masters/cameras")
@require_auth
@require_role("admin", "supervisor")
def create_camera():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    ip = str(payload.get("ip", "")).strip()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", "")).strip()
    if not name or not ip or not username or not password:
        return _json_err("Name, IP, username, and password are required")
    if len(name) > 80 or len(ip) > 64:
        return _json_err("Name or IP too long (max 80 / 64 chars)")
    ok, message, camera_id = add_camera(
        name=name,
        ip=ip,
        username=username,
        password=password,
        path=str(payload.get("path", "")),
        port=int(payload.get("port", 554) or 554),
        base_dir=BASE_DIR,
    )
    if not ok:
        return _json_err(message)
    return _json_ok({"id": camera_id}, message, 201)


@app.put("/api/masters/cameras/<camera_id>")
@require_auth
@require_role("admin", "supervisor")
def edit_camera(camera_id: str):
    payload = request.get_json(silent=True) or {}
    ok, message = update_camera(
        camera_id,
        name=payload.get("name"),
        ip=payload.get("ip"),
        username=payload.get("username"),
        password=payload.get("password"),
        path=payload.get("path"),
        port=payload.get("port"),
        base_dir=BASE_DIR,
    )
    if not ok:
        return _json_err(message, 400)
    return _json_ok(message=message)


@app.delete("/api/masters/cameras/<camera_id>")
@require_auth
@require_role("admin")
def remove_camera(camera_id: str):
    ok, message = delete_camera(camera_id, BASE_DIR)
    if not ok:
        return _json_err(message, 400)
    return _json_ok(message=message)


# ─── Camera Grid ──────────────────────────────────────────────────────────────

@app.get("/api/camera-grid")
@require_auth
def camera_grid():
    """Return all machines with camera + cycle status for grid view."""
    return _json_ok(_camera_grid())


# ─── Camera Health (for offline bar) ─────────────────────────────────────────
#
# Polled from the frontend OfflineCameraBar every 30 s.  Returns an array
# of {id, ip, port, name, online} — `online` is True iff a TCP connect
# attempt to ip:port returns within 1.5 s (typical RTSP / ONVIF port).
#
# Implementation notes:
#  - Cached for 25 s so a fast user reload doesn't slam every camera
#    again — the bar polls every 30 s anyway.
#  - Each camera probed with a 1.5 s socket timeout in parallel via
#    a small ThreadPoolExecutor so 10+ cameras don't serialise.
#  - We use a TCP connect probe (not RTSP DESCRIBE) because connect
#    is enough to detect "camera reachable" — RTSP handshake adds
#    auth complexity for zero diagnostic gain.

import socket as _socket
from concurrent.futures import ThreadPoolExecutor as _TPE

_CAM_HEALTH_CACHE = {"ts": 0.0, "data": []}
_CAM_HEALTH_TTL_S = 25
_CAM_PROBE_TIMEOUT_S = 1.5

def _probe_camera_tcp(cam: dict) -> dict:
    ip   = str(cam.get("ip", "")).strip()
    port = int(cam.get("port", 554) or 554)
    online = False
    if ip:
        try:
            with _socket.create_connection((ip, port), timeout=_CAM_PROBE_TIMEOUT_S):
                online = True
        except (OSError, _socket.timeout):
            online = False
    return {
        "id":     cam.get("id"),
        "name":   cam.get("name"),
        "ip":     ip,
        "port":   port,
        "online": online,
    }

@app.get("/api/cameras/health")
@require_auth
def cameras_health():
    """Live online/offline status for every configured camera."""
    now = time.time()
    if (now - _CAM_HEALTH_CACHE["ts"]) < _CAM_HEALTH_TTL_S and _CAM_HEALTH_CACHE["data"]:
        return _json_ok(_CAM_HEALTH_CACHE["data"])
    cams = list_cameras(BASE_DIR)
    if not cams:
        _CAM_HEALTH_CACHE["ts"]   = now
        _CAM_HEALTH_CACHE["data"] = []
        return _json_ok([])
    # Probe up to 8 cameras in parallel; more than that on a single line
    # is rare and serialising the tail is fine.
    with _TPE(max_workers=min(8, len(cams))) as pool:
        rows = list(pool.map(_probe_camera_tcp, cams))
    _CAM_HEALTH_CACHE["ts"]   = now
    _CAM_HEALTH_CACHE["data"] = rows
    return _json_ok(rows)


# ─── MES proxy: zones / lines / machines from MES Postgres ───────────────────
#
# The CMS Machine Detail page treats MES (`mes_plc_configs`) as the source of
# truth for plant/zone/line/machine config — that's where the collectors and
# dashboards already read from.  These endpoints proxy the MES `/api/cms-sync`
# loopback surface so the CMS frontend doesn't need a MES JWT.
#
# When the operator saves a machine here, two things happen atomically:
#   1. CMS pushes the row to MES (`mes_plc_configs` upsert)
#   2. CMS upserts the local `camera_config_bindings.json` so plc_monitor's
#      recorder + clip-extractor picks up the camera↔machine mapping on the
#      next poll tick.  The binding `id` is reused if the same MES plc_id
#      already has one.

def _sync_binding_from_machine(mes_plc_id: int, machine_payload: dict) -> None:
    """Mirror the machine row into camera_config_bindings.json.

    plc_monitor reads bindings.json on every poll to decide whether to
    extract per-cycle MP4 (MAIN) or just keep the rolling TS (SUB).  We
    rebuild the binding entry from the canonical MES row so admins never
    edit it directly — the Machine Detail form is the only write path.
    """
    from camera_bindings import load_bindings, save_bindings
    data = load_bindings(BASE_DIR)
    bindings = data.get("bindings", [])
    # Filter out any existing binding for this MES machine + drop the
    # legacy plc_id-only matching by using machine_id == "mes:<id>".
    machine_key = f"mes:{mes_plc_id}"
    bindings = [b for b in bindings if str(b.get("machine_id", "")) != machine_key]

    camera_id   = (machine_payload.get("nf2_camera_id") or "").strip()
    is_sub      = machine_payload.get("parent_plc_id") is not None
    trigger     = "SUB" if is_sub else "MAIN"
    # SUB-machine bit lives in process_seq_address (per Phase2 convention);
    # MAIN-machine uses ok/ng_bit_address pair.  We store both so the binding
    # is self-describing.
    m_bit = (machine_payload.get("process_seq_address") or "").strip().upper()
    ok_bit = (machine_payload.get("ok_bit_address") or "").strip().upper()
    ng_bit = (machine_payload.get("ng_bit_address") or "").strip().upper()
    target = machine_payload.get("ideal_cycle_time")
    try:
        target_s = int(round(float(target))) if target is not None else 30
    except (TypeError, ValueError):
        target_s = 30

    if camera_id:
        # Only emit a binding if a camera is actually assigned.  Otherwise
        # the row would never produce video and just clutter the JSON.
        # `plc_ip` is included so the /api/plc-edge handler can match
        # incoming webhook edges against this binding directly, without
        # needing a parallel plcs.json entry — that file is now legacy.
        #
        # Resolve zone+line names from MES so plc_monitor writes videos
        # under videos/<line_name>/ instead of videos/Unknown_Line/.
        # The MES `/api/cms-sync/machines` endpoint already joins these.
        zone_name = ""
        line_name = ""
        try:
            ok_m, machines = mes_client.list_machines()
            if ok_m and isinstance(machines, list):
                for m in machines:
                    try:
                        if int(m.get("id", 0)) == int(mes_plc_id):
                            zone_name = (m.get("zone_name") or "").strip()
                            line_name = (m.get("line_name") or "").strip()
                            break
                    except (TypeError, ValueError):
                        continue
        except Exception:
            # Non-fatal — folder just falls back to Unknown_Line.
            pass

        bindings.append({
            "id":                f"bind_mes_{mes_plc_id}",
            "machine_id":        machine_key,
            "camera_id":         camera_id,
            "plc_id":            f"mes_plc_{mes_plc_id}",   # synthetic id used by plc-edge match
            "plc_ip":            (machine_payload.get("plc_ip") or "").strip(),
            "trigger_type":      trigger,
            "ok_bit":            ok_bit,
            "ng_bit":            ng_bit,
            "m_bit_address":     m_bit,
            "target_time":       target_s,
            "extract_per_cycle": (trigger == "MAIN"),
            "machine_name":      machine_payload.get("machine_name", ""),
            "zone_name":         zone_name,
            "line_name":         line_name,
        })
    data["bindings"] = bindings
    save_bindings(data, BASE_DIR)


@app.get("/api/mes/state")
@require_auth
def mes_state():
    """One-shot snapshot for the CMS frontend's dropdowns."""
    ok, body = mes_client.get_state()
    if not ok:
        return _json_err(body.get("error", "MES unreachable"), 502)
    return _json_ok(body)


@app.get("/api/mes/machines")
@require_auth
def mes_machines():
    """Flat list of every PLC machine with zone+line names joined."""
    ok, body = mes_client.list_machines()
    if not ok:
        return _json_err(body.get("error", "MES unreachable"), 502)
    return _json_ok(body)


@app.post("/api/mes/machine")
@require_auth
@require_role("admin", "supervisor")
def mes_machine_upsert():
    """Save a machine row to MES + mirror the binding locally.

    Body shape matches `routers.cms_sync.MachineUpsert` — id=null to
    create, id=<int> to update.  On success, also rewrites the entry
    in camera_config_bindings.json so plc_monitor picks up the
    camera↔trigger mapping without a Flask restart.
    """
    payload = request.get_json(silent=True) or {}
    ok, body = mes_client.upsert_machine(payload)
    if not ok:
        return _json_err(body.get("error", "MES upsert failed"), 502)

    mes_id = body.get("id")
    if mes_id:
        # Merge the assigned id back into the payload for the binding
        merged = dict(payload)
        merged["id"] = mes_id
        try:
            _sync_binding_from_machine(int(mes_id), merged)
        except Exception as exc:
            # Log but don't fail — MES side already accepted the write.
            print(f"[MES-SYNC] binding sync error for mes_id={mes_id}: {exc}")

    return _json_ok(body, "Machine saved")


@app.delete("/api/mes/machine/<int:plc_id>")
@require_auth
@require_role("admin")
def mes_machine_delete(plc_id: int):
    """Delete a machine row in MES + drop its local binding."""
    ok, body = mes_client.delete_machine(plc_id)
    if not ok:
        return _json_err(body.get("error", "MES delete failed"), 502)
    # Drop the mirrored binding entry too
    try:
        from camera_bindings import load_bindings, save_bindings
        data = load_bindings(BASE_DIR)
        bindings = data.get("bindings", [])
        key = f"mes:{plc_id}"
        data["bindings"] = [b for b in bindings if str(b.get("machine_id", "")) != key]
        save_bindings(data, BASE_DIR)
    except Exception as exc:
        print(f"[MES-SYNC] binding cleanup error for plc_id={plc_id}: {exc}")
    return _json_ok({"id": plc_id}, "Machine deleted")


# ─── Cycle Trigger ────────────────────────────────────────────────────────────

import threading as _threading
import queue as _queuemod

# ── PLC-edge async queue (2026-07-31) ────────────────────────────────
# The webhook used to run the binding match + _trigger_binding SYNCHRONOUSLY
# in the Flask request thread.  Under load (collectors push every cycle, plus
# retries whenever a response lagged) the threaded dev server spawned hundreds
# of request threads all contending on the cycle/spawn locks -> 300-500 threads
# stuck on futexes, every GET timing out, dashboard video "band ho jaati".
# Now the webhook only ENQUEUES the edge and returns 200 instantly; ONE daemon
# worker drains the queue, so edge processing is serial (no lock contention,
# correct per-machine cycle ordering) and the HTTP layer never blocks.

# ── clip render gate (2026-08-03) ────────────────────────────────────────
# Two problems this solves, both reported as "video lags when I open one":
#   • the SAME clip clicked repeatedly → one render, the rest wait and then
#     find it cached (a per-key lock);
#   • MANY different clips opened at once → they queue instead of launching
#     a transcode each and pegging the CPU, which is what made the clip
#     already playing stutter (a global semaphore).
# Sized at 4: comfortably parallel on this box while leaving cores for the
# camera recorders, which matter more than shaving a second off a clip.
_CLIP_LOCKS: dict = {}
_CLIP_LOCKS_GUARD = threading.Lock()
_CLIP_RENDER_SEM = threading.Semaphore(int(os.environ.get("CLIP_RENDER_PARALLEL", "4")))


@contextlib.contextmanager
def _clip_gate(key: str):
    """Serialise renders of `key`, and cap total concurrent renders."""
    with _CLIP_LOCKS_GUARD:
        lock = _CLIP_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CLIP_LOCKS[key] = lock
        if len(_CLIP_LOCKS) > 512:          # bound the dict on a long-lived server
            for k in [k for k in _CLIP_LOCKS if k != key][:256]:
                _CLIP_LOCKS.pop(k, None)
    lock.acquire()
    try:
        _CLIP_RENDER_SEM.acquire()
        try:
            yield
        finally:
            _CLIP_RENDER_SEM.release()
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# 2026-08-03 — CONTENT-ANCHORED .ts TIMELINE  (the "video nahi aa rahi" root cause)
#
# A recording's filename carries the wall-clock ms at which the recorder PROCESS
# was spawned — NOT the wall-clock of its first video frame.  Between those two
# moments ffmpeg has to open the RTSP session, negotiate, probe the stream and
# wait for the first keyframe; and once running, a camera that delivers below
# its nominal fps makes the file's content-time fall further behind wall-clock
# every minute.  Measured on this box: the gap is 1 s on a healthy camera and
# 37-46 s on a struggling one.
#
# Every seek offset used to be computed as (cycle_start - filename_timestamp),
# so it was that same 40-odd seconds TOO LARGE.  Near the tail of a long
# recording the offset then landed PAST end-of-file and ffmpeg emitted nothing
#   -> 404 "camera may have been offline", or a stub of whatever tail remained
#      (measured: 0.32 s / 8 frames for a 13.5 s cycle).
# That is why clips were fine right after a rotation and vanished as the file
# aged, and why the one camera with ~0 s drift always played perfectly.
#
# Anchoring at the END instead is drift-proof: the last write (mtime) is the
# wall-clock of the newest frame, so
#       content_start = mtime - probed_duration
# is exact at the file's tail — precisely where the live cycles being requested
# live.  Same window, corrected offset: 13.52 s / 338 frames.
#
# ffprobe costs ~100 ms on a 15-minute TS, so results are cached: a rotated file
# never changes (size+mtime match => reuse forever) and a growing file is
# re-probed at most every _TS_META_TTL seconds.  For a cached growing file the
# start is derived from the CACHED mtime/duration pair (they belong together);
# only the end is refreshed from the live mtime.
# ---------------------------------------------------------------------------
_TS_META_CACHE: dict = {}          # path -> (size, mtime, duration, probed_at)
_TS_META_GUARD = threading.Lock()
_TS_META_TTL   = 8.0


def _probe_ffprobe_bin() -> str:
    try:
        from plc_monitor import _get_ffmpeg as _gf  # type: ignore
        cand = os.path.join(os.path.dirname(_gf()), "ffprobe")
        if os.path.exists(cand) or os.path.exists(cand + ".exe"):
            return cand
    except Exception:
        pass
    return "ffprobe"


def _ts_content_window(path: str, force: bool = False):
    """(content_start, content_end, duration_s) in wall-clock, or None.

    See the block comment above — this is what makes a seek offset land on the
    frame the operator actually asked for.  Returns None when the file cannot be
    probed so callers keep their old filename-anchored behaviour.

    `force` skips the cache — needed by the live-edge wait, which re-probes every
    few hundred ms and must see the file actually growing.
    """
    try:
        st = os.stat(path)
    except Exception:
        return None
    now = time.time()
    with _TS_META_GUARD:
        c = None if force else _TS_META_CACHE.get(path)
    if c:
        c_size, c_mtime, c_dur, c_at = c
        fresh_closed = (c_size == st.st_size and abs(c_mtime - st.st_mtime) < 0.001)
        if fresh_closed or (now - c_at) < _TS_META_TTL:
            return (datetime.fromtimestamp(c_mtime - c_dur),
                    datetime.fromtimestamp(st.st_mtime), c_dur)
    try:
        r = subprocess.run(
            [_probe_ffprobe_bin(), "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        dur = float((r.stdout or b"").decode("utf-8", "ignore").strip() or 0)
    except Exception:
        return None
    if dur <= 0:
        return None
    with _TS_META_GUARD:
        if len(_TS_META_CACHE) > 4000:
            _TS_META_CACHE.clear()
        _TS_META_CACHE[path] = (st.st_size, st.st_mtime, dur, now)
    return (datetime.fromtimestamp(st.st_mtime - dur),
            datetime.fromtimestamp(st.st_mtime), dur)


_EDGE_Q = _queuemod.Queue(maxsize=5000)

def _process_edge(payload, edge_dt, plc_ip, bit):
    bindings = list_bindings(BASE_DIR)
    legacy_plc_id = None
    for p in list_plcs(BASE_DIR):
        if str(p.get("ip", "")).strip() == plc_ip:
            legacy_plc_id = str(p.get("id", "")).strip()
            break
    if not hasattr(plc_monitor, "_next_part_code"):
        plc_monitor._next_part_code = {}
    incoming_part = str(payload.get("part_code", "") or "").strip()
    if incoming_part:
        if legacy_plc_id:
            plc_monitor._next_part_code[legacy_plc_id] = incoming_part
        plc_monitor._next_part_code[f"ip:{plc_ip}"] = incoming_part
    for b in bindings:
        b_ip     = str(b.get("plc_ip", "")).strip()
        b_plc_id = str(b.get("plc_id", "")).strip()
        if (b_ip and b_ip == plc_ip) or (legacy_plc_id and b_plc_id == legacy_plc_id):
            try:
                plc_monitor._trigger_binding(b, edge_dt=edge_dt)
            except Exception as exc:
                print(f"[EDGE-WORKER] _trigger_binding failed for {b.get('id')}: {exc}")
    state_key = f"{legacy_plc_id or ('ip:' + plc_ip)}|{bit}"
    plc_monitor._last_values[state_key]       = True
    plc_monitor._last_change_times[state_key] = payload.get("iso", "")

def _edge_worker():
    while True:
        item = _EDGE_Q.get()
        try:
            _process_edge(*item)
        except Exception as exc:
            print(f"[EDGE-WORKER] error: {exc}")
        finally:
            _EDGE_Q.task_done()

_threading.Thread(target=_edge_worker, daemon=True, name="edge-worker").start()


@app.post("/api/plc-edge")
def plc_edge():
    """Receive a rising-edge notification from the MES collector.

    Earlier this CMS polled the PLC directly which competed with the
    collector for the only TCP slot Mitsubishi gives us — about half
    the L108/L109 rising edges silently dropped.  Now the collector
    is the sole PLC client and pushes us each edge over loopback
    HTTP.  We translate the edge into the same cycle-rotate flow the
    old poller used, so clip extraction logic is unchanged.

    Body shape (sent by Phase2/collector_engine.py:_emit_edge_webhook):
        {
          "line_id":     int,
          "line_name":   str,
          "plc_ip":      "192.168.30.150",
          "plc_port":    5002,
          "bit":         "L108" | "L109",
          "status":      "OK" | "NG",
          "epoch":       1778210000.123,
          "epoch_ms":    1778210000123,
          "iso":         "2026-05-08T08:53:20.123",
          "model_number":1,
          "model_name":  "TRACK ASSY ...",
          "ok_total":    47,
          "ng_total":    2
        }

    No auth — this is a localhost-only loopback sink.  CORS / firewall
    keeps it off the LAN.
    """
    payload = request.get_json(silent=True) or {}
    plc_ip   = str(payload.get("plc_ip", "")).strip()
    bit      = str(payload.get("bit", "")).strip()
    if not plc_ip or not bit:
        return _json_err("plc_ip and bit are required", 400)

    # Pull the PLC-accurate timestamp out of the payload — this is the
    # instant MES detected the rising edge on the PLC's bit, captured
    # at the collector's 30 ms fast-poll cadence.  Webhook arrival at
    # CMS can lag by 10-30 s under load, which was making
    # (datetime.now() - prev_now) shorter than the real cycle and
    # producing 32 s clips for 52 s cycles.  We pass this trustworthy
    # timestamp through to _trigger_binding so cycle start/end math
    # matches the chart's CT exactly.
    edge_dt = None
    edge_source = "now()"      # diagnostic — which path produced edge_dt
    epoch_ms = payload.get("epoch_ms")
    if epoch_ms is not None:
        try:
            edge_dt = datetime.fromtimestamp(int(epoch_ms) / 1000.0)
            edge_source = "epoch_ms"
        except (ValueError, TypeError, OSError):
            edge_dt = None
    if edge_dt is None:
        # Fall back to ISO field if epoch_ms missing/malformed
        iso = str(payload.get("iso", "")).strip()
        if iso:
            try:
                edge_dt = datetime.fromisoformat(iso)
                edge_source = "iso"
            except ValueError:
                edge_dt = None
    if edge_dt is None:
        edge_dt = datetime.now()
        edge_source = "now()-fallback"
    # 2026-05-14 — surface the source so we can tell from a log line
    # whether the new PLC-timestamp path is actually active.
    print(f"[EDGE-WEBHOOK] {plc_ip}|{bit} epoch_ms={epoch_ms} "
          f"-> edge_dt={edge_dt.isoformat()} src={edge_source}")

    # 2026-07-31 — enqueue and return immediately; the edge-worker does the
    # binding match + _trigger_binding off the request thread (see queue above).
    try:
        _EDGE_Q.put_nowait((payload, edge_dt, plc_ip, bit))
        _queued = True
    except _queuemod.Full:
        _queued = False
        print(f"[EDGE-WEBHOOK] queue FULL ({_EDGE_Q.qsize()}) — dropping {plc_ip}|{bit}")
    return _json_ok({"queued": _queued, "bit": bit, "status": payload.get("status")})


@app.post("/api/cycle/trigger")
@require_auth
@require_role("admin", "supervisor", "operator")
def cycle_trigger():
    """
    Toggle cycle recording for a machine.
    Body: { machine_id, action: 'start'|'stop'|'toggle' }
    """
    payload = request.get_json(silent=True) or {}
    machine_id = str(payload.get("machine_id", "")).strip()
    action = str(payload.get("action", "toggle")).strip()

    if not machine_id:
        return _json_err("machine_id is required")

    current = get_machine_state(machine_id, BASE_DIR)
    is_recording = current.get("recording", False)

    if action == "toggle":
        action = "stop" if is_recording else "start"

    if action == "start":
        if is_recording:
            return _json_err("Machine is already recording")
        next_num = get_next_cycle_num(CSV_PATH)
        state = start_cycle(machine_id, next_num, BASE_DIR)
        return _json_ok(state, f"Cycle {next_num} started for {machine_id}")

    elif action == "stop":
        if not is_recording:
            return _json_err("No active cycle to stop")
        state = end_cycle(machine_id, BASE_DIR)
        return _json_ok(state, f"Cycle stopped for {machine_id}")

    return _json_err(f"Unknown action: {action}")


@app.get("/api/cycle/status")
@require_auth
def cycle_status():
    """Return current cycle recording state for all machines."""
    all_states = get_all_states(BASE_DIR)
    all_machines = all_machines_flat(BASE_DIR)
    result = []
    for machine in all_machines:
        mid = machine.get("machine_id", "")
        state = all_states.get(mid, {})
        result.append({
            "machine_id": mid,
            "machine_name": machine.get("machine_name", ""),
            "zone_id": machine.get("zone_id", ""),
            "zone_name": machine.get("zone_name", ""),
            "line_id": machine.get("line_id", ""),
            "line_name": machine.get("line_name", ""),
            "recording": state.get("recording", False),
            "cycle_number": state.get("cycle_number"),
            "start_time": state.get("start_time"),
        })
    return _json_ok(result)


@app.get("/api/cycle/history")
@require_auth
def cycle_history():
    """Return cycle history with optional filters."""
    limit      = request.args.get("limit", type=int)
    machine_id = request.args.get("machine_id", "").strip()
    zone_name  = request.args.get("zone_name", "").strip()
    line_name  = request.args.get("line_name", "").strip()
    shift      = request.args.get("shift", "").strip()
    date_from  = request.args.get("date_from", "").strip()   # YYYY-MM-DD
    date_to    = request.args.get("date_to", "").strip()     # YYYY-MM-DD

    rows = _read_cycles()   # read all, filter below

    if machine_id:
        rows = [r for r in rows if r.get("machine_id") == machine_id]
    if zone_name:
        rows = [r for r in rows if r.get("zone_name","").lower() == zone_name.lower()]
    if line_name:
        rows = [r for r in rows if r.get("line_name","").lower() == line_name.lower()]
    if shift:
        rows = [r for r in rows if r.get("shift","").lower() == shift.lower()]
    if date_from:
        rows = [r for r in rows if r.get("start_time","") >= date_from]
    if date_to:
        rows = [r for r in rows if r.get("start_time","") <= date_to + "T23:59:59"]

    # Apply limit after filtering — rows are descending, so [:limit] = newest N
    if limit:
        rows = rows[:limit]

    # Attach camera_id so frontend can show live feed per cycle
    cam_map = _binding_camera_map()
    for r in rows:
        r["camera_id"] = cam_map.get(r.get("machine_id", ""), "")

    return _json_ok(rows)


@app.get("/api/cycle/part-log")
@require_auth
def download_part_log():
    """Download part_log.csv — clean CSV with part_name, part_code, cycle_time, video_path."""
    from recorder import PART_LOG_CSV, _ensure_part_log
    part_log = _ensure_part_log(BASE_DIR)
    return send_file(part_log, mimetype="text/csv", as_attachment=True,
                     download_name="part_log.csv")


# ─── PLC Config (Multiple) ───────────────────────────────────────────────────

@app.get("/api/masters/plcs")
@require_auth
def get_plcs_master():
    return _json_ok(list_plcs(BASE_DIR))

@app.post("/api/masters/plcs")
@require_auth
@require_role("admin")
def create_plc_master():
    payload = request.get_json(silent=True) or {}
    ok, msg, pid = add_plc(payload, BASE_DIR)
    return _json_ok({"id": pid}, msg) if ok else _json_err(msg)

@app.patch("/api/masters/plcs/<plc_id>")
@require_auth
@require_role("admin")
def patch_plc_master(plc_id):
    payload = request.get_json(silent=True) or {}
    ok, msg = update_plc(plc_id, payload, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)

@app.delete("/api/masters/plcs/<plc_id>")
@require_auth
@require_role("admin")
def delete_plc_master(plc_id):
    ok, msg = delete_plc(plc_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)


# ─── PLC Live Bit Status ─────────────────────────────────────────────────────

@app.get("/api/plc-live-status")
@require_auth
def get_plc_live_status():
    """Return current live bit values from the running PlcMonitor thread."""
    plcs = list_plcs(BASE_DIR)
    result = []
    for plc in plcs:
        plc_id = str(plc.get("id", ""))
        bit_raw = str(plc.get("bit_address", ""))
        bits = [b.strip() for b in bit_raw.split(",") if b.strip()]
        bit_states = []
        for bit in bits:
            key = f"{plc_id}|{bit}"
            val = plc_monitor._last_values.get(key)
            last_change = plc_monitor._last_change_times.get(key)
            bit_states.append({
                "bit": bit,
                "value": val,           # True/False/None(unknown)
                "last_change": last_change,
            })
        result.append({
            "id": plc_id,
            "ip": plc.get("ip"),
            "port": plc.get("port"),
            "description": plc.get("description", ""),
            "enabled": plc.get("enabled", False),
            "bits": bit_states,
            "connected": plc_monitor._connected_plcs.get(plc_id, False),
        })
    return _json_ok(result)


# ─── PLC Webhook (Node-RED → Flask bridge) ───────────────────────────────────

@app.post("/api/plc-webhook")
def plc_webhook():
    """
    Receives rising-edge events from Node-RED (or any external source).
    Node-RED detects the bit change and POSTs here — no auth needed
    since it's localhost-only and carries no sensitive data.

    Body: { "plc_id": "plc_xxx", "bit": "L108", "value": true }
    """
    payload = request.get_json(silent=True) or {}
    plc_id  = str(payload.get("plc_id",  "")).strip()
    bit     = str(payload.get("bit",     "")).strip()
    value   = payload.get("value", True)

    if not plc_id and not bit:
        return _json_err("plc_id or bit required")

    # Update the live-status cache so the UI shows the correct bit state
    if plc_id and bit:
        key = f"{plc_id}|{bit}"
        import time as _time
        plc_monitor._last_values[key]       = bool(value)
        plc_monitor._last_change_times[key] = _time.strftime("%Y-%m-%dT%H:%M:%S")
        plc_monitor._connected_plcs[plc_id] = True

    if not bool(value):
        # Falling edge — just update state, no cycle action
        return _json_ok({"triggered": 0}, "Falling edge noted")

    # Rising edge — find all bindings for this PLC and trigger them
    from camera_bindings import list_bindings as _lb
    bindings = _lb(BASE_DIR)
    triggered = 0
    for binding in bindings:
        bid = str(binding.get("plc_id", "")).strip()
        if plc_id and bid != plc_id:
            continue
        plc_monitor._trigger_binding(binding)
        triggered += 1

    print(f"[WEBHOOK] Rising edge plc={plc_id} bit={bit} -> triggered {triggered} binding(s)")
    return _json_ok({"triggered": triggered}, f"Triggered {triggered} binding(s)")


# ─── Legacy PLC Config (Singular fallback) ───────────────────────────────────

@app.get("/api/plc-config")
@require_auth
def get_plc_config():
    return _json_ok(load_plc_config(BASE_DIR))

@app.post("/api/plc-config")
@require_auth
@require_role("admin")
def save_plc_config():
    payload = request.get_json(silent=True) or {}
    allowed = {"ip", "port", "bit_address", "enabled", "description"}
    updates = {k: v for k, v in payload.items() if k in allowed}
    if "port" in updates:
        updates["port"] = int(updates["port"] or 502)
    cfg = update_plc_config(updates, BASE_DIR)
    return _json_ok(cfg, "PLC configuration saved")


# ─── Shifts Config ──────────────────────────────────────────────────────────

@app.get("/api/masters/shifts")
@require_auth
def get_shifts():
    return _json_ok(list_shifts(BASE_DIR))

@app.post("/api/masters/shifts")
@require_auth
@require_role("admin")
def save_shift():
    payload = request.get_json(silent=True) or {}
    ok, msg, sid = add_or_update_shift(payload, BASE_DIR)
    return _json_ok({"id": sid}, msg) if ok else _json_err(msg)

@app.delete("/api/masters/shifts/<shift_id>")
@require_auth
@require_role("admin")
def remove_shift(shift_id: str):
    ok, msg = delete_shift(shift_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg, 404)


# ─── Camera Config Bindings ──────────────────────────────────────────────────

@app.get("/api/camera-configs")
@require_auth
def get_camera_bindings():
    return _json_ok(list_bindings(BASE_DIR))

@app.post("/api/camera-configs")
@require_auth
@require_role("admin")
def add_camera_binding():
    payload = request.get_json(silent=True) or {}
    ok, msg, bid = add_binding(payload, BASE_DIR)
    return _json_ok({"id": bid}, msg) if ok else _json_err(msg)

@app.delete("/api/camera-configs/<binding_id>")
@require_auth
@require_role("admin")
def delete_camera_binding(binding_id):
    ok, msg = delete_binding(binding_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)


# ─── Video Playback ──────────────────────────────────────────────────────────

def _get_ffmpeg_exe() -> str:
    """Return path to ffmpeg binary (imageio-ffmpeg bundled or system)."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"   # fallback to system ffmpeg if installed


def _mjpeg_from_ts(ts_file: str):
    """
    Serve an MJPEG stream by reading the last ~3 seconds of the live TS file.
    No new RTSP connection needed — reuses the already-recording TS file.
    Keyframes are every 2 s (forced by plc_monitor), so decoding starts quickly.
    """
    ffmpeg_exe = _get_ffmpeg_exe()

    def generate():
        cmd = [
            ffmpeg_exe,
            "-loglevel", "quiet",
            "-fflags", "+nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-sseof", "-2",          # seek to 2 s from end — keyframes every 1 s (-g 20), so a
                                     # 2 s offset keeps a smooth jitter buffer at lower latency
            "-i", ts_file,
            "-vf", "fps=10",         # 10 fps is plenty for monitoring
            "-q:v", "4",
            "-f", "mjpeg",
            "-flush_packets", "1",
            "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            buf = b""
            while True:
                chunk = proc.stdout.read(32768)
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(b"\xff\xd8")
                    if s == -1:
                        buf = b""
                        break
                    e = buf.find(b"\xff\xd9", s + 2)
                    if e == -1:
                        buf = buf[s:]
                        break
                    jpeg = buf[s:e + 2]
                    buf  = buf[e + 2:]
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" +
                        jpeg +
                        b"\r\n"
                    )
        finally:
            try:
                proc.terminate()
            except Exception:
                pass

    return generate


@app.get("/live_feed/<camera_id>")
def live_feed(camera_id: str):
    """
    MJPEG live stream.  Reads from the already-recording TS file so no second
    RTSP connection is needed (camera typically allows only one connection).
    """
    # Video OFF for this camera's line (or master) → don't spawn any ffmpeg.
    if not plc_monitor.camera_recording_allowed(camera_id):
        return "Video recording is OFF for this line", 503
    ts_file = plc_monitor.get_ts_file(camera_id)

    if ts_file and os.path.exists(ts_file) and os.path.getsize(ts_file) > 0:
        # Serve from the live TS file — no RTSP conflict
        gen = _mjpeg_from_ts(ts_file)
    else:
        # TS recorder not running yet — fall back to direct RTSP (sub-stream)
        rtsp_url = get_camera_rtsp_url(camera_id, BASE_DIR)
        if not rtsp_url:
            return "Camera not found", 404
        ffmpeg_exe = _get_ffmpeg_exe()
        from urllib.parse import urlparse, urlunparse
        parsed  = urlparse(rtsp_url)
        sub_url = urlunparse(parsed._replace(path="/h264/ch1/sub/av_stream"))

        def gen():
            cmd = [
                ffmpeg_exe, "-loglevel", "quiet",
                "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay",
                "-rtsp_transport", "tcp", "-probesize", "32", "-analyzeduration", "0",
                "-i", sub_url,
                "-vf", "fps=10", "-q:v", "4", "-f", "mjpeg", "-flush_packets", "1",
                "pipe:1",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                buf = b""
                while True:
                    chunk = proc.stdout.read(32768)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        s = buf.find(b"\xff\xd8")
                        if s == -1:
                            buf = b""
                            break
                        e = buf.find(b"\xff\xd9", s + 2)
                        if e == -1:
                            buf = buf[s:]
                            break
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +
                               buf[s:e + 2] + b"\r\n")
                        buf = buf[e + 2:]
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass

    resp = app.response_class(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"]               = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]                      = "no-cache"
    resp.headers["X-Accel-Buffering"]           = "no"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/camera_frame/<camera_id>")
def camera_frame(camera_id: str):
    """
    Single JPEG snapshot.  Extracted from the live TS file (no extra RTSP connection).
    """
    if not plc_monitor.camera_recording_allowed(camera_id):
        return "Video recording is OFF for this line", 503
    ts_file = plc_monitor.get_ts_file(camera_id)

    if not ts_file or not os.path.exists(ts_file) or os.path.getsize(ts_file) == 0:
        return "Stream offline", 503

    ffmpeg_exe = _get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-loglevel", "quiet",
        "-sseof", "-2",          # 2 s from end of live file (fresher fallback snapshot)
        "-i", ts_file,
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-q:v", "3",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6)
        if result.returncode == 0 and result.stdout:
            resp = Response(result.stdout, mimetype="image/jpeg")
            resp.headers["Cache-Control"]               = "no-cache, no-store, must-revalidate"
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
    except Exception:
        pass
    return "Stream offline", 503


@app.get("/api/video")
def serve_video():
    path = request.args.get("path")
    if not path:
        return "Video not found", 404
    file_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    file_path = os.path.normpath(file_path)
    if not file_path.startswith(os.path.normpath(BASE_DIR)):
        return "Forbidden", 403
    if not os.path.exists(file_path):
        return "Video not found", 404

    # conditional=True enables Range request support (required for HTML5 <video> seeking)
    resp = send_file(file_path, mimetype="video/mp4", conditional=True)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


def _mp4_has_video(_p):
    """True iff the MP4 at _p has >=1 decodable video packet.  Rejects
    frameless ftyp+moov stubs (camera stalled/offline during the cycle) that
    would otherwise stream with HTTP 200 and crash the browser demuxer
    ("NS_ERROR_DOM_MEDIA_DEMUXER_ERR: No MP4 video tracks")."""
    try:
        if not (os.path.exists(_p) and os.path.getsize(_p) > 0):
            return False
        _ff = os.environ.get("IMAGEIO_FFMPEG_EXE", "/usr/bin/ffmpeg")
        _probe = os.path.join(os.path.dirname(_ff), "ffprobe")
        if not os.path.exists(_probe):
            _probe = "ffprobe"
        _r = subprocess.run(
            [_probe, "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", _p],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
        _n = (_r.stdout or b"").decode("utf-8", "ignore").strip()
        return _n.isdigit() and int(_n) > 0
    except Exception:
        try:
            return os.path.getsize(_p) > 40 * 1024   # stub is only moov/ftyp
        except Exception:
            return False


@app.get("/api/video/by-part")
def serve_video_by_part():
    """Serve the latest cycle video for a given part code.
    Supports byte-range (206) and always streams in 1 MB chunks so the Flask
    dev server doesn't buffer the whole file before sending — essential for
    remote clients over slow links."""
    import re as _re
    code = (request.args.get("code") or "").strip()
    if not code:
        return "Missing code", 400
    safe = _re.sub(r"[^A-Za-z0-9._-]", "_", code).strip("_")
    if not safe:
        return "Invalid code", 400
    videos_root = os.path.normpath(os.path.join(BASE_DIR, "videos"))
    # Legacy flat path (pre-per-line layout): videos/<part>.mp4
    file_path   = os.path.normpath(os.path.join(videos_root, f"{safe}.mp4"))
    if not file_path.startswith(os.path.normpath(BASE_DIR)):
        return "Forbidden", 403

    # New per-line layout: videos/<Line_Name>/<part>.mp4  — check every
    # immediate subfolder (cheap, one-level glob) before giving up.
    if not os.path.exists(file_path):
        try:
            # 2026-08-02 — pick the NEWEST match, not the first one found.
            # Part codes REPEAT across days, and this lookup keys on the part
            # code alone (no date/shift filter), so "break on first hit" served
            # a clip from a PREVIOUS day for today's cycle — the operator saw a
            # 2026-08-01 15:13 timestamp burned into a video opened for an
            # 2026-08-02 18:52 cycle.  Sorting by mtime makes today's clip win.
            _cands = []
            for entry in os.listdir(videos_root):
                cand = os.path.join(videos_root, entry, f"{safe}.mp4")
                if os.path.isfile(cand):
                    _cands.append(cand)
            if _cands:
                file_path = max(_cands, key=lambda p: os.path.getmtime(p))
        except Exception:
            pass

    # Structured custom_root fallback (D:\videos\Zone\Line\... deep tree).
    # Cache hits for 5 min (file won't move), misses for 5 s (a cycle
    # extracting RIGHT now may finish any second — don't make the user
    # wait a minute for the 404 to flip to 200).
    if not os.path.exists(file_path):
        hit = _SEARCH_CACHE.get(safe)
        now = time.time()
        cached = None
        if hit and now < hit["exp"]:
            cached = hit.get("path")
        if cached and os.path.exists(cached):
            file_path = cached
        elif not hit or now >= hit["exp"]:
            try:
                custom_root = _load_video_cfg().get("save_path", "")
                found = None
                if custom_root and os.path.isdir(custom_root):
                    import glob as _glob
                    hits = _glob.glob(
                        os.path.join(custom_root, "**", f"{safe}.mp4"),
                        recursive=True,
                    )
                    found = hits[0] if hits else None
                # Positive hit → long TTL; negative hit → short TTL
                ttl = 300 if found else 5
                _SEARCH_CACHE[safe] = {"path": found, "exp": now + ttl}
                if found:
                    file_path = found
            except Exception:
                _SEARCH_CACHE[safe] = {"path": None, "exp": now + 5}
    if not os.path.exists(file_path):
        # 2026-05-18 — AUTO-REPAIR.
        # MP4 missing.  Before giving up, scan in-progress TS files and
        # attempt a one-shot re-extract.  Most useful when a cycle
        # extraction failed or the original MP4 was deleted as part of
        # a cleanup — if the TS for that cycle is still live (within
        # the current shift, typically), we can rebuild a clean clip
        # on the spot.  We don't have cycle metadata here (just the
        # part code), so we use a heuristic: pick the most-recent TS
        # file that contains the part_code in its CSV companion.
        repaired = _attempt_repair(safe, videos_root)
        if repaired and os.path.exists(repaired):
            file_path = repaired
        else:
            return "Video not found", 404

    # 2026-07-23 — reject a frameless <part>.mp4 (camera stalled during the
    # cycle) so it returns a clean 404 instead of a 200 stub the browser can't
    # demux ("No MP4 video tracks" console spam).
    if not _mp4_has_video(file_path):
        return "Video not found (no frames)", 404

    file_size    = os.path.getsize(file_path)
    range_header = request.headers.get("Range", "")
    CHUNK        = 1024 * 1024  # 1 MB — 16× larger than before for fewer syscalls

    def _stream(path, start, length):
        with open(path, "rb") as f:
            if start:
                f.seek(start)
            left = length
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                left -= len(data)
                yield data

    base_headers = {
        "Content-Type":   "video/mp4",
        "Accept-Ranges":  "bytes",
        "Cache-Control":  "public, max-age=86400",   # browser cache for 1 day
        "Access-Control-Allow-Origin": "*",
    }

    # ── Byte-range request (browser seeking / progressive buffering) ──────────
    m = _re.search(r"bytes=(\d+)-(\d*)", range_header) if range_header else None
    if m:
        byte1  = int(m.group(1))
        byte2  = int(m.group(2)) if m.group(2) else file_size - 1
        byte2  = min(byte2, file_size - 1)
        length = byte2 - byte1 + 1
        headers = {
            **base_headers,
            "Content-Range":  f"bytes {byte1}-{byte2}/{file_size}",
            "Content-Length": str(length),
        }
        return Response(_stream(file_path, byte1, length), 206,
                        headers=headers, direct_passthrough=True)

    # ── Full file — still stream it, never load the whole file into memory ───
    headers = {
        **base_headers,
        "Content-Length": str(file_size),
    }
    return Response(_stream(file_path, 0, file_size), 200,
                    headers=headers, direct_passthrough=True)


# ─── Video save path config ───────────────────────────────────────────────────
_VIDEO_CFG_PATH = os.path.join(BASE_DIR, "video_config.json")

def _load_video_cfg():
    try:
        with open(_VIDEO_CFG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"save_path": ""}

def _save_video_cfg(cfg):
    with open(_VIDEO_CFG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def _retention_hours(cfg=None):
    """Effective video retention in HOURS.  Prefers the new `retention_hours`
    key; falls back to the legacy `retention_days` (×24); default 48 h (2 days).
    2026-06-19 — retention is now hours-granular so an admin can keep clips for,
    e.g., 12 h instead of being forced to whole days."""
    cfg = cfg if cfg is not None else _load_video_cfg()
    rh = cfg.get("retention_hours")
    if rh is not None:
        try:
            return max(1, int(rh))
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(cfg.get("retention_days", 2))) * 24
    except (TypeError, ValueError):
        return 48

def _retention_seconds(cfg=None):
    return _retention_hours(cfg) * 3600

def _video_enabled(cfg=None):
    """Master VIDEO ON/OFF switch (default ON).  When False the admin has turned
    recording OFF (Storage page toggle) to shed PC load — plc_monitor stops the
    recorders + skips extraction and live MJPEG is disabled.  Counting unaffected."""
    cfg = cfg if cfg is not None else _load_video_cfg()
    return cfg.get("video_enabled", True) is not False

@app.get("/api/config/video-enabled")
@require_auth
def get_video_enabled():
    return _json_ok({"video_enabled": _video_enabled()})

@app.post("/api/config/video-enabled")
@require_auth
@require_role("admin")
def set_video_enabled():
    """Master toggle for camera recording + live view.  OFF stops every camera
    recorder immediately (PC load drops); ON lets the plc_monitor watchdog
    respawn them within ~3 s.  Line counting is MES-side, untouched."""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    cfg = _load_video_cfg()
    cfg["video_enabled"] = enabled
    _save_video_cfg(cfg)
    killed = 0
    if not enabled:
        # Stop recorders NOW (the watchdog would also do it on its next tick).
        try:
            for cam in list(plc_monitor._camera_workers.values()):
                try:
                    plc_monitor._kill_cam(cam); killed += 1
                except Exception:
                    pass
            plc_monitor._camera_workers.clear()
        except Exception as e:
            print(f"[VIDEO] kill-on-disable failed: {e}")
    print(f"[VIDEO] recording {'ENABLED' if enabled else 'DISABLED'} by admin"
          + (f" — stopped {killed} recorder(s)" if killed else ""))
    return _json_ok({"video_enabled": enabled, "stopped_recorders": killed},
                    "Video recording " + ("enabled" if enabled else "disabled"))


# ─── Per-line video toggle (called by the MES backend over loopback) ───────────
# The MES Production Panel owns the per-line Video ON/OFF UI; it proxies the
# toggle to the MES backend, which calls these loopback-only endpoints.  We
# accept ONLY direct server-to-server loopback calls (no X-Forwarded-For), so a
# request proxied in through Caddy /cms-api from the internet is rejected.
def _internal_only():
    ra = request.remote_addr or ""
    if ra not in ("127.0.0.1", "::1") or request.headers.get("X-Forwarded-For"):
        return _json_err("internal endpoint — loopback only", 403)
    return None

@app.get("/api/internal/video-lines")
def internal_get_video_lines():
    guard = _internal_only()
    if guard:
        return guard
    cfg = _load_video_cfg()
    return _json_ok({
        "disabled_lines": sorted({str(x).strip() for x in (cfg.get("video_disabled_lines") or []) if str(x).strip()}),
        "video_enabled":  _video_enabled(cfg),
    })

@app.post("/api/internal/video-line")
def internal_set_video_line():
    guard = _internal_only()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    line    = str(data.get("line", "")).strip()
    enabled = bool(data.get("enabled", True))
    if not line:
        return _json_err("line is required")
    cfg = _load_video_cfg()
    disabled = {str(x).strip() for x in (cfg.get("video_disabled_lines") or []) if str(x).strip()}
    if enabled:
        disabled.discard(line)
    else:
        disabled.add(line)
    cfg["video_disabled_lines"] = sorted(disabled)
    _save_video_cfg(cfg)
    killed = 0
    if not enabled:
        # Stop this line's cameras NOW (watchdog would also do it next tick).
        try:
            cam_ids = {str(b.get("camera_id", "")).strip() for b in list_bindings(BASE_DIR)
                       if str(b.get("line_name", "")).strip() == line}
            for cid in cam_ids:
                cam = plc_monitor._camera_workers.pop(cid, None)
                if cam:
                    try:
                        plc_monitor._kill_cam(cam); killed += 1
                    except Exception:
                        pass
        except Exception as e:
            print(f"[VIDEO] line-disable kill failed: {e}")
    print(f"[VIDEO] line {line!r} recording {'ENABLED' if enabled else 'DISABLED'}"
          + (f" — stopped {killed} recorder(s)" if killed else ""))
    return _json_ok({"line": line, "enabled": enabled,
                     "disabled_lines": sorted(disabled), "stopped_recorders": killed},
                    f"Video {'enabled' if enabled else 'disabled'} for {line}")


@app.get("/api/config/video-path")
@require_auth
def get_video_path():
    cfg = _load_video_cfg()
    path = cfg.get("save_path") or os.path.join(BASE_DIR, "videos")
    # Disk free space
    import shutil
    try:
        usage = shutil.disk_usage(path if os.path.exists(path) else BASE_DIR)
        free_gb = round(usage.free / (1024**3), 1)
    except Exception:
        free_gb = None
    rh = _retention_hours(cfg)
    return _json_ok({"save_path": cfg.get("save_path", ""), "effective_path": path,
                     "free_gb": free_gb,
                     "retention_hours": rh,
                     # retention_days kept for any legacy reader (rounded).
                     "retention_days": max(1, round(rh / 24))})

@app.post("/api/config/video-path")
@require_auth
def set_video_path():
    data = request.get_json(silent=True) or {}
    path = str(data.get("save_path", "")).strip()
    if path:
        # 1. Make sure the directory exists (or can be created).
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            return _json_err(f"Cannot create directory: {e}")
        # 2. Real write-permission check — try touching a tiny file in it.
        # Errno 13 (Permission denied) on a read-only / no-ACL drive is
        # silent until the recorder tries to write later, so we surface it
        # NOW with a clear message so the admin can pick another folder.
        probe = os.path.join(path, ".write_test_eol")
        try:
            with open(probe, "w") as _f:
                _f.write("ok")
            os.remove(probe)
        except PermissionError:
            return _json_err(
                f"No write permission on {path!r}. "
                "Pick a folder you can write to (e.g. a sub-folder you create yourself) "
                "or grant Modify rights to this user on that drive."
            )
        except OSError as e:
            return _json_err(f"Cannot write to {path!r}: {e}")
    # retention (optional) — hours-granular.  Accept `retention_hours` (new,
    # preferred) or legacy `retention_days` (×24).  Range 1 h .. 8760 h (365 d).
    new_rh = None
    rh_in = data.get("retention_hours", None)
    rd_in = data.get("retention_days", None)
    if rh_in is not None:
        try:
            new_rh = int(rh_in)
        except (TypeError, ValueError):
            return _json_err("retention_hours must be an integer")
    elif rd_in is not None:
        try:
            new_rh = int(rd_in) * 24
        except (TypeError, ValueError):
            return _json_err("retention_days must be an integer")
    if new_rh is not None and (new_rh < 1 or new_rh > 8760):
        return _json_err("retention must be 1..8760 hours (max 365 days)")
    cfg = _load_video_cfg()
    old_path = cfg.get("save_path", "")
    cfg["save_path"] = path
    if new_rh is not None:
        cfg["retention_hours"] = new_rh
        cfg["retention_days"]  = max(1, round(new_rh / 24))  # legacy mirror
    _save_video_cfg(cfg)

    # ── Hot-reload running recorders ───────────────────────────────────
    # FFmpeg subprocesses are spawned with their write target baked in,
    # so an in-flight recorder keeps writing to the OLD path until the
    # process dies.  When the admin changes the save path we proactively
    # kill every running camera recorder — the watchdog (~3 s tick)
    # restarts each one, this time pointing at the freshly resolved path.
    # Same treatment for any active per-cycle write workers.
    killed_cams = 0
    killed_workers = 0
    if path != old_path:
        try:
            for cam in list(plc_monitor._camera_workers.values()):
                try: plc_monitor._kill_cam(cam); killed_cams += 1
                except Exception: pass
            plc_monitor._camera_workers.clear()
            for w in list(plc_monitor._video_workers.values()):
                proc = w.get("proc")
                if proc and proc.poll() is None:
                    try: proc.kill(); killed_workers += 1
                    except Exception: pass
            plc_monitor._video_workers.clear()
            # Wipe by-part lookup cache: stale POSITIVE hits would keep
            # serving the OLD save_path's mp4 until the 5-min TTL expired,
            # even though new recordings now go to the NEW path.
            try:
                _SEARCH_CACHE.clear()
            except Exception:
                pass
            print(f"[CFG] save_path changed: {old_path!r} → {path!r} | "
                  f"killed {killed_cams} camera recorder(s), {killed_workers} cycle worker(s), "
                  f"cleared video lookup cache")
        except Exception as e:
            print(f"[CFG] hot-reload kill failed: {e}")

    return _json_ok({
        "save_path":         path,
        "killed_recorders":  killed_cams,
        "killed_workers":    killed_workers,
        "message":           "Video save path updated"
                              + (f" — restarted {killed_cams + killed_workers} recorder(s)"
                                 if (killed_cams + killed_workers) else ""),
    })


# ─── Folder browser (drives + directory listing) ──────────────────────
# Powers the "Browse…" button next to Video Save Path.  Browsers can't
# expose absolute filesystem paths via <input type="file"> (security
# restriction), so we serve a directory tree from the backend instead
# and let the user click through drives → folders.  Files are NOT
# listed — only sub-directories.
@app.get("/api/config/list-drives")
@require_auth
def list_drives():
    """Return a list of available drive roots on this machine.
    On Windows: every assigned drive letter (C:, D:, E:, …).
    On POSIX:    just '/' as a single root."""
    drives = []
    try:
        if os.name == "nt":
            import string as _str
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(_str.ascii_uppercase):
                if bitmask & (1 << i):
                    root = f"{letter}:\\"
                    free_gb = None
                    try:
                        import shutil as _sh
                        free_gb = round(_sh.disk_usage(root).free / (1024 ** 3), 1)
                    except Exception:
                        pass
                    drives.append({
                        "path":   root,
                        "label":  f"Drive {letter}:",
                        "free_gb": free_gb,
                    })
        else:
            drives.append({"path": "/", "label": "/", "free_gb": None})
    except Exception as e:
        return _json_err(f"Drive enumeration failed: {e}")
    return _json_ok({"drives": drives})


@app.get("/api/config/list-dir")
@require_auth
def list_dir():
    """List immediate sub-directories of `?path=...`.  Returns folders
    only — files are filtered out.  Hidden / system folders are skipped
    so the picker stays clean.
    Response shape:
        { ok: true, path, parent, folders: [{name, path}] }
    """
    raw = (request.args.get("path") or "").strip()
    if not raw:
        return _json_err("path is required")
    try:
        # Normalise + resolve so the picker can navigate `..` cleanly.
        path = os.path.abspath(os.path.expanduser(raw))
    except Exception as e:
        return _json_err(f"Bad path: {e}")
    if not os.path.isdir(path):
        return _json_err(f"Not a directory: {path}")

    folders = []
    try:
        for entry in os.scandir(path):
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name
                # Skip hidden / system / junk on Windows + POSIX
                if name.startswith(".") or name.startswith("$"):
                    continue
                if name in ("System Volume Information", "Recycle.Bin", "$RECYCLE.BIN"):
                    continue
                folders.append({"name": name, "path": entry.path})
            except OSError:
                # Permission denied / device not ready — skip silently
                continue
    except PermissionError:
        return _json_err(f"Permission denied: {path}")
    except OSError as e:
        return _json_err(f"Cannot read directory: {e}")

    folders.sort(key=lambda f: f["name"].lower())

    parent = os.path.dirname(path)
    # On Windows, dirname("C:\\") returns "C:\\" — flatten to None so the
    # frontend knows it's at a drive root.
    if parent == path:
        parent = None

    return _json_ok({
        "path":    path,
        "parent":  parent,
        "folders": folders,
    })


@app.post("/api/config/create-dir")
@require_auth
def create_dir():
    """Create a new sub-directory under `parent` named `name`.  Used by
    the picker's "+ New Folder" button so users can carve out a fresh
    target directory on the external drive without leaving the UI."""
    data = request.get_json(silent=True) or {}
    parent = str(data.get("parent", "")).strip()
    name   = str(data.get("name", "")).strip()
    if not parent or not name:
        return _json_err("parent and name are required")
    # Reject any path-traversal attempts in the name.
    if "/" in name or "\\" in name or name in (".", ".."):
        return _json_err("Invalid folder name")
    if not os.path.isdir(parent):
        return _json_err(f"Parent is not a directory: {parent}")
    target = os.path.join(parent, name)
    try:
        os.makedirs(target, exist_ok=True)
    except Exception as e:
        return _json_err(f"Could not create folder: {e}")
    return _json_ok({"path": target})


# ─── Storage health ───────────────────────────────────────────────────────────
# 2026-06-19 — Live probe of the configured video save_path so the admin UI can
# warn when an external/USB drive has been removed / gone read-only AFTER it was
# saved.  The recorder already silently falls back to the local default
# (<base>/videos) in that case (_resolve_videos_root) — this endpoint makes that
# state visible instead of it being buried in console logs.
@app.get("/api/config/storage-health")
@require_auth
def storage_health():
    import shutil
    cfg          = _load_video_cfg()
    configured   = (cfg.get("save_path") or "").strip()
    default_root = os.path.join(BASE_DIR, "videos")
    exists       = bool(configured) and os.path.isdir(configured)
    writable     = False
    if exists:
        probe = os.path.join(configured, ".write_test_eol")
        try:
            with open(probe, "w") as _f:
                _f.write("ok")
            os.remove(probe)
            writable = True
        except Exception:
            writable = False
    # Effective path = what the recorders will actually use (mirrors
    # plc_monitor._resolve_videos_root: custom path only if writable, else default).
    effective      = configured if (configured and writable) else default_root
    using_fallback = bool(configured) and (effective != configured)
    try:
        usage    = shutil.disk_usage(effective if os.path.exists(effective) else BASE_DIR)
        free_gb  = round(usage.free  / (1024**3), 1)
        total_gb = round(usage.total / (1024**3), 1)
    except Exception:
        free_gb = total_gb = None
    if not configured:
        status = "default"      # no custom path — using local default
    elif using_fallback:
        status = "fallback"     # custom path set but NOT writable → fell back
    elif free_gb is not None and free_gb < 5:
        status = "low_space"
    else:
        status = "ok"
    return _json_ok({
        "configured_path": configured,
        "default_path":    default_root,
        "effective_path":  effective,
        "configured_set":  bool(configured),
        "exists":          exists,
        "writable":        writable,
        "using_fallback":  using_fallback,
        "status":          status,
        "free_gb":         free_gb,
        "total_gb":        total_gb,
        "retention_hours": _retention_hours(cfg),
        "video_enabled":   _video_enabled(cfg),
    })


# ─── Ping check ──────────────────────────────────────────────────────────────
@app.get("/api/ping")
def ping_host():
    import socket
    ip = request.args.get("ip", "")
    port = int(request.args.get("port", 554))
    if not ip:
        return _json_err("Missing ip")
    try:
        t0 = time.time()
        s = socket.create_connection((ip, port), timeout=3)
        ms = round((time.time() - t0) * 1000)
        s.close()
        return _json_ok({"ok": True, "ms": ms})
    except Exception:
        return _json_ok({"ok": False, "ms": 0})


# ─── MES shifts proxy ────────────────────────────────────────────────────────
import requests as _requests
_MES_BASE = os.environ.get("MES_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

@app.get("/api/config/shifts-from-mes")
@require_auth
def shifts_from_mes():
    """Fetch shift + hourly slot config from MES backend for all lines."""
    try:
        # Get lines from MES
        r = _requests.get(f"{_MES_BASE}/api/lines/", timeout=5)
        if r.status_code != 200:
            return _json_err("MES unreachable")
        lines = r.json()
        if not isinstance(lines, list):
            lines = lines.get("data", []) if isinstance(lines, dict) else []
        result = []
        for ln in lines[:10]:  # cap at 10 lines
            lid = ln.get("id")
            try:
                r2 = _requests.get(f"{_MES_BASE}/api/lines/{lid}", timeout=5)
                if r2.status_code == 200:
                    detail = r2.json()
                    if isinstance(detail, dict) and "data" in detail:
                        detail = detail["data"]
                    result.append({
                        "line_id": lid,
                        "line_name": ln.get("line_name", ""),
                        "shifts": detail.get("shifts", []),
                        "hourly_slots": detail.get("hourly_slots", []),
                        "breaks": detail.get("breaks", []),
                    })
            except Exception:
                pass
        return _json_ok(result)
    except Exception as e:
        return _json_err(f"MES error: {e}")


# ══════════════════════════════════════════════════════════════════════════
# VIDEO AUTO-CLEANUP — at every shift boundary, wipe the flat videos folder
# so old shift's recordings don't pile up on the local disk.
#
# Triggers fire at the START of each production shift (Shift A = 08:30,
# Shift B = 18:30 — defaults; overridable via VIDEO_CLEAN_TIMES env var
# as a comma-separated "HH:MM" list).  Only the flat-copy folder is
# cleaned; the structured copy on D:\videos is preserved for long-term
# archival.  Cycle records in the DB stay intact — only the .mp4 files go.
# ══════════════════════════════════════════════════════════════════════════

_VIDEO_CLEAN_STATE = {"last_fire": None}  # (date, "HH:MM") of last cleanup

def _run_video_cleanup(reason: str = "manual") -> dict:
    """Delete every *.mp4 under the effective videos save root.
    Reads the path from `video_config.json` (admin-set via Camera Master
    UI) on every call so a path change is honoured immediately, even
    across already-running cleanup schedules.
    Also sweeps stale .ts recordings that no live worker is using —
    each stale TS can be hundreds of MB.
    Returns a small summary dict for logging / manual trigger replies."""
    cfg_save = _load_video_cfg().get("save_path", "").strip()
    videos_root = cfg_save if (cfg_save and os.path.isdir(cfg_save)) \
                  else os.path.join(BASE_DIR, "videos")
    if not os.path.isdir(videos_root):
        return {"deleted": 0, "freed_mb": 0, "reason": reason}

    # 2026-06-14 / 2026-06-19 — RETENTION: keep the last N hours of mp4 clips
    # (admin-set in video_config.json → retention_hours, default 48 h).  Only
    # clips OLDER than the cutoff are wiped (replaces the old "wipe-all every
    # shift").  Hours-granular so e.g. a 12 h retention is honoured.
    cutoff = time.time() - _retention_seconds()

    # TS files currently in use by live recorders — don't delete these.
    live_ts: set = set()
    try:
        for cam in getattr(plc_monitor, "_camera_workers", {}).values():
            tsf = cam.get("ts_file")
            if tsf: live_ts.add(os.path.abspath(tsf))
        for w in getattr(plc_monitor, "_video_workers", {}).values():
            tsf = w.get("ts_file")
            if tsf: live_ts.add(os.path.abspath(tsf))
    except Exception:
        pass

    # 2026-05-14 — Wipe-all at shift start, per operator decision.
    # plc_monitor's _check_shift_boundary is now the canonical cleaner
    # (rotates TS + wipes mp4s in one pass).  This api_server worker
    # is kept as a safety-net duplicate so a clean state is enforced
    # even if the PLC shift-boundary check misses a fire (e.g. PLC
    # offline during the exact minute).  Orphan TS files (recorder
    # artefacts from died sub-camera retries) get nuked unconditionally.
    deleted_mp4 = 0
    kept_mp4    = 0
    deleted_ts  = 0
    freed       = 0
    ts_cutoff = time.time() - float(os.environ.get("TS_KEEP_HOURS", "48")) * 3600
    kept_ts: list = []
    for root, _dirs, files in os.walk(videos_root):
        for f in files:
            low = f.lower()
            p   = os.path.join(root, f)
            ap  = os.path.abspath(p)
            try:
                if low.endswith(".mp4"):
                    if os.path.getmtime(p) >= cutoff:
                        kept_mp4 += 1          # within retention window — keep
                        continue
                    sz = os.path.getsize(p)
                    os.remove(p)
                    deleted_mp4 += 1
                    freed       += sz
                elif low.endswith(".ts") and ap not in live_ts:
                    # Finished TS from an earlier recorder session.
                    # 2026-09-21 — 48 h FOOTAGE HOLD: kept until it is older
                    # than TS_KEEP_HOURS so clips can still be cut later (was:
                    # deleted on sight, every hour and at every shift start).
                    if os.path.getmtime(p) >= ts_cutoff:
                        kept_ts.append((os.path.getmtime(p), p))
                        continue
                    sz = os.path.getsize(p)
                    os.remove(p)
                    deleted_ts  += 1
                    freed       += sz
            except OSError:
                pass
    # DISK GUARD: if free space drops below TS_MIN_FREE_GB, delete the oldest
    # kept TS files first until it is back above.  Recording never stops for
    # lack of disk because of the hold.
    try:
        import shutil as _sh
        need = float(os.environ.get("TS_MIN_FREE_GB", "2000")) * 1e9
        guard_n = 0
        for _mt, _p in sorted(kept_ts):
            if _sh.disk_usage(videos_root).free >= need:
                break
            try:
                _sz = os.path.getsize(_p)
                os.remove(_p)
                deleted_ts += 1
                freed += _sz
                guard_n += 1
            except OSError:
                pass
        if guard_n:
            print(f"[VIDEO-CLEAN] disk guard: free space below "
                  f"{need / 1e9:.0f} GB — removed {guard_n} oldest TS file(s)")
    except Exception as _exc:
        print(f"[VIDEO-CLEAN] disk guard skipped: {_exc}")
    # Prune empty sub-directories so the folder tree stays tidy.
    for root, dirs, files in os.walk(videos_root, topdown=False):
        if root == videos_root:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass
    mb = round(freed / 1024 / 1024, 1)
    print(f"[VIDEO-CLEAN] {reason}: removed {deleted_mp4} mp4 + "
          f"{deleted_ts} old ts ({mb} MB) · kept {len(kept_ts)} ts within the "
          f"48 h hold")
    return {"deleted": deleted_mp4 + deleted_ts,
            "deleted_mp4": deleted_mp4,
            "deleted_ts":  deleted_ts,
            "freed_mb":    mb,
            "reason":      reason}


def _video_cleanup_worker():
    """Background thread: once a minute, check if 'now' matches one of the
    configured shift-start times (within a 90 s window) and trigger the
    cleanup exactly once per boundary per day.

    Times come from VIDEO_CLEAN_TIMES env var (e.g. "08:30,18:30").
    Default matches the line's Shift A / Shift B start times."""
    import time as _t
    from datetime import datetime as _dt

    raw    = os.environ.get("VIDEO_CLEAN_TIMES", "08:30,18:30")
    # Accept "HH:MM" only; silently skip malformed entries.
    targets = []
    for t in raw.split(","):
        t = t.strip()
        if len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit():
            targets.append(t)
    if targets:
        print(f"[VIDEO-CLEAN] Worker started — shift-start cleanups at "
              f"{', '.join(targets)} + hourly retention pass")
    else:
        print("[VIDEO-CLEAN] Worker started — hourly retention pass only "
              "(no shift-start times configured)")

    while True:
        try:
            _t.sleep(30)
            now = _dt.now()
            hhmm = now.strftime("%H:%M")
            today = now.date()
            # (1) Shift-start cleanup at the configured times (once per boundary).
            if hhmm in targets:
                key = (today.isoformat(), hhmm)
                if _VIDEO_CLEAN_STATE.get("last_fire") != key:
                    _VIDEO_CLEAN_STATE["last_fire"] = key
                    _run_video_cleanup(reason=f"shift-start {hhmm}")
            # (2) Hourly retention pass — so an hours-granular retention (e.g.
            # 12 h) is actually enforced, not only at the 2 daily shift starts.
            hour_key = (today.isoformat(), now.hour)
            if _VIDEO_CLEAN_STATE.get("last_hour_fire") != hour_key:
                _VIDEO_CLEAN_STATE["last_hour_fire"] = hour_key
                _run_video_cleanup(reason=f"hourly retention {now.strftime('%H:00')}")
        except Exception as exc:
            print(f"[VIDEO-CLEAN] worker error: {exc}")


def _start_video_cleanup_worker():
    import threading
    threading.Thread(target=_video_cleanup_worker,
                     name="video-cleanup",
                     daemon=True).start()


@app.post("/api/admin/video-cleanup")
@require_auth
@require_role("admin")
def trigger_video_cleanup():
    """Manual trigger — admin can hit this to apply retention NOW.
    2026-06-19 — locked behind admin auth (was previously unauthenticated)."""
    summary = _run_video_cleanup(reason="manual API trigger")
    return _json_ok(summary, "Retention cleanup applied")


# ─── Sub-machine clip trim ────────────────────────────────────────────────────
#
# Phase2 calls this to slice a sub-cycle out of the camera's continuous
# shift-long MPEG-TS recording. No cycle-level .mp4 is pre-extracted for
# sub-machines — the shift video is kept whole and trimmed on demand, which
# is exactly how the user described the flow.
#
#   GET /api/submachine/clip?plc_ip=192.168.30.190
#       &ts_start=2026-04-22T13:45:11+05:30
#       &ts_end  =2026-04-22T13:45:27+05:30
#
# Response: an H.264/MP4 stream of that time range.  Range headers are
# respected so HTML5 <video> seeking works.
@app.get("/api/submachine/clip")
def submachine_clip():
    from datetime import datetime as _dt, timedelta as _td

    plc_ip     = (request.args.get("plc_ip") or "").strip()
    ts_start_s = (request.args.get("ts_start") or "").strip()
    ts_end_s   = (request.args.get("ts_end")   or "").strip()
    # Explicit camera_id bypasses binding lookup — useful for sub-machines
    # whose sub-PLC doesn't have a dedicated camera binding. Phase2 sends
    # this from mes_plc_configs.nf2_camera_id when admin has configured it.
    camera_id_q = (request.args.get("camera_id") or "").strip()

    if not (ts_start_s and ts_end_s):
        return "Missing ts_start or ts_end", 400
    if not (plc_ip or camera_id_q):
        return "Missing plc_ip or camera_id", 400

    # Defensive: if a caller forgot to URL-encode the "+" in a tz offset
    # (e.g. "+05:30") it arrives as a space — restore it so fromisoformat
    # doesn't choke and 400 the request.
    def _fix_tz(s: str) -> str:
        # Common malformed pattern: "...sssss 05:30" → "...sssss+05:30"
        if " 0" in s and ":" in s.split(" 0", 1)[1][:5]:
            return s.replace(" 0", "+0", 1)
        return s
    ts_start_s = _fix_tz(ts_start_s)
    ts_end_s   = _fix_tz(ts_end_s)

    try:
        ts_start = _dt.fromisoformat(ts_start_s)
        ts_end   = _dt.fromisoformat(ts_end_s)
    except ValueError as e:
        return f"Bad ISO timestamp ({e}): start={ts_start_s!r} end={ts_end_s!r}", 400
    if ts_end <= ts_start:
        return "ts_end must be > ts_start", 400

    camera_id = camera_id_q
    if not camera_id:
        # Fallback: plc_ip → NF2 plc_id → binding → camera_id
        plcs = list_plcs(BASE_DIR) or []
        plc_row = next((p for p in plcs if str(p.get("ip", "")).strip() == plc_ip), None)
        if not plc_row:
            return f"No PLC in plcs.json for IP {plc_ip}", 404
        nf2_plc_id = str(plc_row.get("id", "")).strip()
        binding = next(
            (b for b in list_bindings(BASE_DIR)
             if str(b.get("plc_id", "")).strip() == nf2_plc_id),
            None,
        )
        if not binding:
            return (f"No camera bound to plc_id={nf2_plc_id}. "
                    f"Pass ?camera_id= explicitly or add binding."), 404
        camera_id = str(binding.get("camera_id", "")).strip()
        if not camera_id:
            return "Binding has empty camera_id", 500

    # 3) Active camera worker (shift-long TS recorder)
    cam_worker = None
    try:
        cam_worker = plc_monitor._camera_workers.get(camera_id)  # type: ignore[attr-defined]
    except Exception:
        cam_worker = None

    # 2026-05-29 — DON'T 503 IMMEDIATELY when cam_worker is missing.
    # Operator complaint: a historical cycle clip (Final Inspection,
    # earlier today) returned 503 because the live Panasonic recorder
    # was in a brief death/respawn gap at the moment of the request,
    # even though a perfectly good rotated TS file covering the cycle
    # existed on disk.  Fix: when cam_worker is unavailable, fall
    # through to the file-based TS selector below — it will find the
    # right historical TS by overlap-scoring the cycle's time window.
    # Still kick off a lazy-launch so future requests find a live
    # worker.
    if not cam_worker:
        try:
            plc_monitor._ensure_camera_recording(camera_id)  # type: ignore[attr-defined]
        except Exception:
            pass

    if cam_worker:
        ts_file     = cam_worker.get("ts_file")
        write_start = cam_worker.get("write_start") or cam_worker.get("record_start")
        if not (ts_file and write_start and os.path.exists(ts_file)):
            ts_file     = None
            write_start = None
    else:
        ts_file     = None
        write_start = None

    # 2026-05-18 — TS FILE SELECTOR.
    # cam_worker holds the CURRENTLY-active recorder.  But the user may
    # click a cycle from earlier in the shift that lives in an older
    # rotated TS file (e.g. after a CMS restart spawned a new recorder
    # at 09:08 while a zombie ffmpeg from 08:00 was still writing).
    # If the requested cycle is BEFORE the active recorder's write_start
    # OR AFTER the active recorder's last write (mtime), scan all
    # cam_{camera_id}_*.ts files and pick the one whose [embed_start,
    # mtime] window contains the cycle.  Falls back to cam_worker if
    # no better candidate is found.
    # 2026-05-29 — When cam_worker is None (Panasonic in respawn gap),
    # write_start/ts_file are None; force the file-based selector by
    # treating "active" as not covering the requested window so the
    # glob+overlap path runs unconditionally.
    ts_naive_start = ts_start.replace(tzinfo=None)
    ts_naive_end   = ts_end.replace(tzinfo=None)
    if write_start is not None:
        # 2026-08-03 — decide "does the LIVE file hold this cycle?" from the
        # file's real content window, not from write_start.  A recorder that
        # rotated mid-cycle leaves the worker pointing at a brand-new .ts while
        # write_start still describes the previous one, so this test used to say
        # "yes, it's in the active file" and the extract then seeked into a file
        # that begins AFTER the cycle ended — 416 "empty duration", or a seek
        # past EOF and a 404, for a cycle that WAS recorded in the file the
        # rotation just closed.  Comparing against the content window makes the
        # rotated file get found by the glob-selector below instead.
        _cw_live = _ts_content_window(ts_file)
        if _cw_live:
            active_naive, active_mtime = _cw_live[0], _cw_live[1]
        else:
            active_naive = write_start.replace(tzinfo=None)
            try:
                active_mtime = _dt.fromtimestamp(os.path.getmtime(ts_file))
            except Exception:
                active_mtime = _dt.now()
        # `ts_naive_start` (not _end): if the cycle BEGINS before this file's
        # first frame the live file can only ever yield a truncated clip, so an
        # older fragment must be considered even though the windows overlap.
        needs_other_ts = (ts_naive_start < active_naive
                           or ts_naive_start > active_mtime + _td(seconds=30))
    else:
        # No live worker — must hunt rotated TS files.
        active_naive   = ts_naive_start  # placeholder, ignored below
        active_mtime   = _dt.now()
        needs_other_ts = True
    if needs_other_ts:
        import glob as _glob, re as _re
        # 2026-05-29 — When ts_file is None (no live cam_worker), fall
        # back to the resolved videos root so we can still glob rotated
        # TS files from history.
        if ts_file:
            videos_dir = os.path.dirname(ts_file)
        else:
            try:
                from plc_monitor import _resolve_videos_root as _rv  # type: ignore
                videos_dir = _rv(BASE_DIR)
            except Exception:
                videos_dir = r"D:\MES_Videos"
        chosen     = None
        chosen_start = None
        chosen_score = -1
        cand_glob  = os.path.join(videos_dir, f"cam_{camera_id}_*.ts")
        for cand in _glob.glob(cand_glob):
            base = os.path.basename(cand)
            m = _re.search(r"_(\d{13})\.ts$", base)
            if not m:
                continue
            try:
                cand_start = _dt.fromtimestamp(int(m.group(1)) / 1000.0)
            except Exception:
                continue
            try:
                cand_mtime = _dt.fromtimestamp(os.path.getmtime(cand))
            except Exception:
                continue
            # 2026-05-29 - SCORED selector for smoother video extraction.
            # Old logic required strict CONTAINMENT (TS fully covers
            # cycle); cycles at boundaries returned no match -> empty
            # extract -> "video kabhi aati kabhi nhi" complaint.
            # New: any TS whose window OVERLAPS the cycle gets scored
            # by overlap-in-seconds; highest overlap wins.  Falls back
            # to nearest-in-time if zero overlap.
            # 2026-08-03 — score against the file's REAL content window, not its
            # filename stamp + a 60 s guess.  cand_start was the recorder's spawn
            # time (up to ~46 s before the first frame) and cand_end overshot by a
            # flat minute, so a file that does not actually reach the cycle could
            # out-score the one that does.  _ts_content_window() derives both ends
            # from the probed duration; on failure we keep the old estimate.
            _cw = _ts_content_window(cand)
            if _cw:
                cand_start, cand_end = _cw[0], _cw[1]
            else:
                cand_end = cand_mtime + _td(seconds=60)   # generous tail buffer
            overlap_start = max(cand_start, ts_naive_start)
            overlap_end   = min(cand_end,   ts_naive_end)
            overlap_sec   = max(0.0, (overlap_end - overlap_start).total_seconds())
            score = overlap_sec
            if score <= 0:
                # No overlap; score negatively by distance (smaller distance = higher score)
                if cand_end < ts_naive_start:
                    dist = (ts_naive_start - cand_end).total_seconds()
                else:
                    dist = (cand_start - ts_naive_end).total_seconds()
                score = -dist  # closer = larger (less negative) score
            if score > chosen_score:
                chosen, chosen_start, chosen_score = cand, cand_start, score
        if chosen:
            _active_repr = os.path.basename(ts_file) if ts_file else "(no live worker)"
            print(f"[CLIP] cycle {ts_naive_start}->{ts_naive_end} not in "
                  f"active TS ({_active_repr} from "
                  f"{active_naive}); using {os.path.basename(chosen)} "
                  f"from {chosen_start} (score={chosen_score:.1f}s)")
            ts_file       = chosen
            active_naive  = chosen_start
            write_start   = chosen_start

    # 2026-05-29 — Final guard: if we still have no ts_file (neither
    # live worker nor any rotated TS file matched the requested time)
    # return a clean 404 instead of crashing on a None path below.
    if not ts_file or not os.path.exists(ts_file):
        return (f"No TS file available for {camera_id} in window "
                f"{ts_naive_start} → {ts_naive_end}.  Camera may have "
                f"been offline for this cycle."), 404

    # 2026-08-03 — CONTENT-ANCHOR the seek origin (see _ts_content_window).
    # `active_naive` is a SPAWN time: either the live worker's write_start or the
    # chosen file's filename stamp.  The first frame lands 1-46 s after it, and a
    # camera running under its nominal fps widens that gap as the file grows, so
    # every offset below inherited the error and the extract drifted past the
    # requested moment — past EOF entirely on an aged file (-> 404 / 3 s stub).
    # Re-anchoring on (mtime - probed duration) is exact at the file's tail,
    # which is where the requested cycles are.  Applied to BOTH paths: the live
    # worker's file suffers the same drift as a rotated one.
    _cw_active = _ts_content_window(ts_file)

    # 2026-08-03 — LIVE-EDGE WAIT.  The dashboard fires this request within a
    # second or two of the PLC reporting cycle-end, but ffmpeg buffers its TS
    # output, so the last few seconds of that very cycle are still in memory and
    # not yet on disk.  Extracting immediately returned a clip that stopped
    # short (measured 15.0 s for a 16.8 s cycle) — and a short clip was then
    # thrown away by the length check further down, so the operator got a 404
    # for a cycle that WAS recorded.  Wait, briefly and boundedly, for the
    # writer to catch up past the requested end.  Costs nothing on historical
    # windows (the loop's condition is already false) and at most ~5 s on the
    # newest cycle, which is the one still being written.
    # 2026-08-04 — margin 0.5 s → 2.0 s, deadline 5 s → 8 s.  mtime says when the
    # muxer last wrote, which runs slightly AHEAD of the last frame ffprobe can
    # index, so "content_end just reached the cycle end" still left the final
    # second or two unreadable and the clip came back 1.5-3 s short.  Measured
    # over 103 cycles: 12 of the 27 short clips were this tail, all on the newest
    # cycle.  Costs nothing on any older window — the loop condition is already
    # false there and it never runs.
    if _cw_active:
        _edge_deadline = time.time() + 8.0
        _want_end = ts_naive_end.timestamp()
        while _cw_active[1].timestamp() < _want_end + 2.0:
            if time.time() >= _edge_deadline:
                break
            time.sleep(0.4)
            _cw_probe = _ts_content_window(ts_file, force=True)
            if not _cw_probe:
                break
            if _cw_probe[1] <= _cw_active[1]:
                # writer is not advancing (stalled camera) — waiting longer
                # cannot help, take what is on disk.
                _cw_active = _cw_probe
                break
            _cw_active = _cw_probe

    if _cw_active:
        active_naive = _cw_active[0]
    # One line per clip request: the whole seek decision in a form that can be
    # checked against the file on disk when an operator reports a missing clip.
    try:
        if _cw_active:
            _cov = (f"content {_cw_active[0]:%H:%M:%S}->{_cw_active[1]:%H:%M:%S} "
                    f"dur={_cw_active[2]:.0f}s")
        else:
            _cov = "content ?? (probe failed, using filename anchor)"
        print(f"[CLIP] {camera_id} want {ts_naive_start:%H:%M:%S}->{ts_naive_end:%H:%M:%S}"
              f" | ts={os.path.basename(ts_file)} | {_cov}", flush=True)
    except Exception:
        pass

    # 4) Map wall-clock range → seconds into the TS file (with pre-roll)
    ts_start_off = (ts_naive_start - active_naive).total_seconds()
    duration_s   = (ts_end   - ts_start).total_seconds()
    if ts_start_off < 0:
        # Cycle started before this TS file did — best-effort: start from 0.
        duration_s  = max(0.0, duration_s + ts_start_off)
        ts_start_off = 0.0
    if duration_s <= 0:
        return "Empty duration after TS-file alignment", 416

    # 2026-05-18 — operator spec: video must match EXACT cycle duration.
    # Earlier this added 2 s pre-roll + 2 s post-roll for review context,
    # so a 15.76 s cycle came back as a 19.76 s clip and the operator
    # saw a confusing gap between the labelled CT and the video length.
    # No padding now — clip duration == cycle duration to the second.
    seek_off = max(0.0, ts_start_off)
    # 2026-06-30 — operator spec: clip length rounds UP to the cycle's ceil
    # (e.g. 13.32 s cycle → 14 s clip), matching the main-machine MP4 extractor
    # (_extract_cycle) and the repair path, which both math.ceil() the duration.
    import math as _math
    trim_dur = max(1.0, float(_math.ceil(duration_s)))

    # 5) Transcode slice to MP4 on the fly (streams out via stdout)
    # 2026-05-21 — Switched to _pick_hw_encoder() (NVENC > QSV > libx264).
    # Same probed encoder the cycle extractor + auto-repair use, so the
    # on-demand sub-machine clip pulls 8-10× less CPU and starts 2-3×
    # faster on this box's RTX A2000.  Falls back to libx264 if NVENC
    # isn't installed.
    try:
        from plc_monitor import _get_ffmpeg, _pick_hw_encoder  # type: ignore
        ffmpeg = _get_ffmpeg()
        _hw_codec, _hw_flags = _pick_hw_encoder()
    except Exception:
        ffmpeg = "ffmpeg"
        _hw_codec, _hw_flags = "libx264", ["-preset", "ultrafast", "-crf", "23"]

    # 2026-05-27 — Operator: "video render thik kr".  Switched back to
    # STREAM COPY (no re-encoding) for on-demand clips.  Earlier we
    # forced a libx264 re-encode to dodge the "≤1 s of macroblocks at
    # the start" artefact, but at the cost of 2-3× extra latency.  The
    # live RTSP recorder now produces TS with `-bf 0` + `-g 60`
    # (every-2-second keyframes) via _pick_live_encoder, so input-side
    # seek lands on a keyframe within 2 s of the request and the
    # leading-blocks problem is gone.  Seek slack tightened from 1.5 s
    # → 0.5 s (still safe against the 2 s keyframe interval since seek
    # rounds up to the nearest keyframe anyway).  Result: ~10× faster,
    # near-zero CPU; the typical 10-15 s clip arrives in under 500 ms
    # end-to-end on this hardware.
    # 2026-08-02 — backoff 0.5s → 4s.  The 0.5 s above was justified by the
    # recorder writing `-g 60` (2 s keyframes) via _pick_live_encoder — but SUB
    # cameras now record with `-c copy`, so the keyframe interval is whatever the
    # CAMERA emits (measured 1-2 s on some, far longer on others) and we have no
    # control over it.  With only 0.5 s of slack the input seek could land mid-GOP,
    # the decoder then had no reference frame, and the clip opened as a flat GREEN
    # rectangle.  4 s of slack lands before a keyframe on every camera measured
    # here; `-ss _output_ss` still trims back to the exact cycle start, so the
    # clip content is unchanged — only a few extra frames get decoded (~ms).
    _input_ss  = max(0.0, seek_off - 1.5)
    _output_ss = max(0.0, seek_off - _input_ss)

    # 2026-06-01 — render to a FINALIZED temp .mp4 (real non-fragmented moov
    # + +faststart), NOT a fragmented pipe.  Unique name → safe under
    # concurrent clicks; lives in the system temp dir so the video-pipeline
    # sweeper/archiver never touch it.  (See the response note below for the
    # "0:00 / 0:02 stuck duration" bug this fixes.)
    import tempfile as _tempfile, hashlib as _hashlib, glob as _glob2
    _tmp_root = _tempfile.gettempdir()

    # 2026-06-01 — render to a FINALIZED .mp4 (real non-fragmented moov +
    # +faststart), NOT a fragmented pipe, so the browser gets a true
    # Content-Length + duration + 206 Range (fixes "0:00 / 0:02" stuck
    # duration + dead seek-bar over the LAN / in a 2nd browser; the OLD pipe
    # had +empty_moov, no Content-Length, only a cosmetic Accept-Ranges, so a
    # strict/LAN browser saw an unbounded "live" stream and the bar never grew).
    #
    # CACHE THE CLIP BY (camera, window):  a video player issues the first GET
    # *plus* many Range/seek GETs for the SAME clip.  A fresh per-request temp
    # re-ran ffmpeg every time AND leaked a file per range (a close-time delete
    # can't remove a clip Windows/AV still holds open -> hundreds of GB-scale
    # orphans).  Keyed on (camera_id, ts_start, ts_end): the first request
    # renders it, every later Range request for the same window reuses the
    # finalized file -- no re-encode, OS file-cache, ONE file.  Past-footage
    # windows are immutable (deterministic -c copy) so reuse is always correct.
    # Zero impact on the live recorder/counting: we still only READ the .ts.
    _key = _hashlib.md5(f"{camera_id}|{ts_start_s}|{ts_end_s}".encode()).hexdigest()
    _out_path = os.path.join(_tmp_root, f"cycclip_{_key}.mp4")

    # 2026-09-12 — CACHE FAST-PATH.  A clip for a window that finished recording
    # (>20 s ago) is IMMUTABLE and was published to _out_path only after passing
    # the frame check, so a repeat click — or the video player's own second
    # (video-load) request that follows its bytes=0-0 preflight — can serve it
    # straight off disk with NO ffprobe and NO render gate.  That removed the
    # ~1.5 s the cache-hit path used to spend re-probing the same file every time.
    # Near-live windows (<=20 s old) still take the full path (recording may still
    # be growing, so the cached clip could be short).
    try:
        _win_age = (_dt.now() - ts_naive_end).total_seconds()
    except Exception:
        _win_age = 0.0
    if _win_age > 20 and os.path.exists(_out_path) and os.path.getsize(_out_path) > 40 * 1024:
        _resp = send_file(_out_path, mimetype="video/mp4", conditional=True)
        _resp.headers["Accept-Ranges"]  = "bytes"
        _resp.headers["Cache-Control"]  = "public, max-age=3600"
        _resp.headers["X-Clip-Source"]  = "cache"
        return _resp

    # 2026-07-23 — VALIDATE the render actually contains VIDEO FRAMES, not just
    # an MP4 container.  When the seek lands past the end of a stalled/fragmented
    # .ts, ffmpeg (-c:v copy) still writes ftyp+moov (size > 0) but ZERO frames.
    # The old `size > 0` success check then published + served that stub with
    # HTTP 200, and the browser choked with "NS_ERROR_DOM_MEDIA_DEMUXER_ERR:
    # No MP4 video tracks" (console spam + broken player).  ffprobe for >=1 video
    # packet; a frameless stub is rejected so the endpoint 404s and the UI shows
    # a clean "no video for this cycle".
    _ffprobe_bin = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    def _clip_has_video(_p):
        try:
            if not (os.path.exists(_p) and os.path.getsize(_p) > 40 * 1024):
                return False   # empty/frameless stubs are only a few KB
            # 2026-09-12 — FAST validity check.  The old `-count_packets` read the
            # WHOLE file to count video packets (~0.4-0.8 s/clip) — once render
            # became a -c copy, THAT probe was the bulk of the click latency.  A
            # real clip has a non-zero container duration; a frameless ftyp+moov
            # stub does not.  `format=duration` reads only the moov (~0.03 s) and
            # is enough to tell them apart (size>40KB already rules out stubs).
            _r = subprocess.run(
                [_ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", _p],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
            _d = float((_r.stdout or b"0").decode("utf-8", "ignore").strip() or 0)
            return _d > 0.3
        except Exception:
            # ffprobe missing/errored -> size heuristic: a frameless stub is only
            # the moov/ftyp (~a few KB); real 1 s+ clips are much larger.
            try:
                return os.path.getsize(_p) > 40 * 1024
            except Exception:
                return False

    # Stale sweep -- keep the temp dir bounded no matter how clients behave:
    # drop any cycclip_*.mp4 older than 10 min.  A clip currently being served
    # stays open -> remove() fails -> reclaimed on the next pass (harmless).
    try:
        _cut = time.time() - 600
        for _old in _glob2.glob(os.path.join(_tmp_root, "cycclip_*.mp4")):
            try:
                if os.path.getmtime(_old) < _cut:
                    os.remove(_old)
            except Exception:
                pass
    except Exception:
        pass

    # 2026-07-31 — a cached clip is reusable ONLY if it's ~as long as the
    # requested cycle window.  The earlier "immutable past window" assumption
    # broke when the FIRST click landed while the recording of that window was
    # still incomplete (cycle just ended, or the recorder had respawned
    # mid-window): ffmpeg trimmed a SHORT clip, cached it, and every replay
    # then served that stale 20s clip for a 62s cycle.  ffprobe the cached
    # duration; if it's short, re-extract — by replay time the .ts holds the
    # full cycle, so the operator now gets the FULL cycle-length video.
    def _cached_full(_p):
        try:
            if not (os.path.exists(_p) and os.path.getsize(_p) > 0):
                return False
            _r = subprocess.run(
                [_ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", _p],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
            _d = float((_r.stdout or b"0").decode("utf-8", "ignore").strip() or 0)
            return _d >= max(1.0, trim_dur - 2.0)
        except Exception:
            # can't probe -> fall back to the old size heuristic, don't thrash
            try: return os.path.getsize(_p) > 40 * 1024
            except Exception: return False

    # Render only if this exact window isn't already cached AT FULL LENGTH.
    # Render to a unique work file, then os.replace() onto the keyed path so a
    # concurrent reader never sees a half-written clip.
    if not _cached_full(_out_path):
        _wfd, _work = _tempfile.mkstemp(prefix="cycclip_", suffix=".part.mp4", dir=_tmp_root)
        os.close(_wfd)

        # 2026-09-12 — "PICK AND SHOW" — STREAM COPY, no re-encode (operator:
        # "encode/decode mat kar, jo record hui hai wahi utha ke dikha").  The
        # cameras stream H.264 (rtsp .../h264/ch1/sub/av_stream) and the recorder
        # stores it with `-c copy` at a clean CONSTANT 15 fps (verified: 150
        # frames in a 10 s window, r_frame_rate=avg_frame_rate=15/1, codec=h264,
        # 1280x720).  So the on-disk .ts is ALREADY a browser-playable, smooth
        # H.264 stream — the old libx264 re-encode was re-compressing H.264 into
        # H.264 for nothing.  That per-click encode + its latency is what let slow
        # renders pile up under Flask threaded=True until the CMS wedged (1300+
        # threads, 476 CLOSE-WAIT, every endpoint timing out — "video load hi nahi
        # ho rahi").  Copy is ~0.03 s and ~0 CPU, so nothing queues behind it.
        #
        # `-c copy` can only cut on keyframes, so input -ss lands on the keyframe
        # AT/BEFORE the cycle start (a real keyframe -> no green corruption) and we
        # KEEP that short lead-in rather than a frame-accurate output -ss (copy
        # can't drop leading frames cleanly).  Duration is grown by the lead-in so
        # the FULL cycle is always covered.  The prior VFR "freeze while the bar
        # moves" bug that forced -vsync cfr no longer applies — the stream is now
        # true CFR, so copy plays smoothly.  Multi-fragment cycles (cross a
        # recorder restart) still re-encode via the stitch fallback below.
        _copy_dur = trim_dur + max(0.0, _output_ss)
        _single_cmd = [
            ffmpeg, "-y",
            "-fflags",     "+genpts+discardcorrupt",
            "-err_detect", "ignore_err",
            "-ss", f"{_input_ss:.3f}",         # input seek -> keyframe BEFORE cycle
            "-i", ts_file,
            "-t", f"{_copy_dur:.3f}",          # cover lead-in + full cycle
            "-c", "copy",                      # PICK AND SHOW — no re-encode
            "-an",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",         # real moov + Content-Length + seekable
            "-f", "mp4",
            _work,
        ]

        # 2026-06-04 — MULTI-TS STITCH (anti-truncation).  A long cycle can span
        # a mid-cycle recorder restart, so the single chosen ts_file holds only
        # the post-restart portion -> the clip came back shorter than the cycle
        # (110 s CT -> 70-80 s video) and gap-cycles 404'd.  Build a concat of
        # EVERY rotated TS for this camera overlapping the cycle window and
        # seek/trim across the whole stream so the clip == full CT.  Tried
        # FIRST; on empty output we fall through to the single-file extract
        # above, so a concat failure is never worse than the old behaviour.
        _cmds     = []
        _listfile = None
        try:
            import glob as _glob3, re as _re3
            _vdir = os.path.dirname(ts_file)
            _cyc  = []
            for _cf in _glob3.glob(os.path.join(_vdir, f"cam_{camera_id}_*.ts")):
                _mm = _re3.search(r"_(\d{13})\.ts$", os.path.basename(_cf))
                if not _mm:
                    continue
                try:
                    # 2026-08-03 — content window, not filename stamp + mtime.
                    # A fragment's frames begin 1-46 s after its filename time,
                    # so the old bounds both mis-selected fragments and made the
                    # offset below wrong (see _ts_content_window).
                    _cwf = _ts_content_window(_cf)
                    if _cwf:
                        _cs, _ce, _cd = _cwf[0], _cwf[1], _cwf[2]
                    else:
                        _cs = _dt.fromtimestamp(int(_mm.group(1)) / 1000.0)
                        # content-end ~= last-write time (+3s flush slack).  The old
                        # +60s pulled in fragments that ended long before the cycle,
                        # pushing the seek offset past the concat content -> empty.
                        _ce = _dt.fromtimestamp(os.path.getmtime(_cf)) + _td(seconds=3)
                        _cd = max(0.0, (_ce - _cs).total_seconds())
                except Exception:
                    continue
                if (_cs <= ts_naive_end and _ce >= ts_naive_start
                        and os.path.getsize(_cf) > 0):
                    _cyc.append((_cs, _ce, _cd, _cf))
            _cyc.sort(key=lambda x: x[0])
            if len(_cyc) >= 2:                  # cycle spans >1 TS file -> stitch
                _lfd, _listfile = _tempfile.mkstemp(prefix="cyclist_", suffix=".txt",
                                                    dir=_tmp_root)
                with os.fdopen(_lfd, "w", encoding="utf-8") as _lf:
                    for _e in _cyc:
                        _lf.write("file '%s'\n"
                                  % _e[3].replace("\\", "/").replace("'", "'\\''"))
                # 2026-08-03 — offset must be measured on the CONCAT timeline, not
                # on wall-clock.  concat splices fragments end-to-end, so any dead
                # air between a recorder dying and restarting simply does not exist
                # in the stitched stream: a wall-clock delta from the first
                # fragment's start overshoots by the size of every gap before the
                # target.  Walk the fragments instead, accumulating real durations
                # until the one that actually contains the cycle start.
                _soff = None
                _acc  = 0.0
                for _cs_i, _ce_i, _cd_i, _ in _cyc:
                    if _cs_i <= ts_naive_start <= _ce_i:
                        _soff = _acc + max(0.0, (ts_naive_start - _cs_i).total_seconds())
                        break
                    _acc += _cd_i
                if _soff is None:               # cycle starts in a gap -> nearest frame
                    _soff = max(0.0, (ts_naive_start - _cyc[0][0]).total_seconds())
                _sdur = max(1.0, (ts_end - ts_start).total_seconds())   # ceil(CT)
                # RE-ENCODE (libx264) the concat: rebuilds the fragments' PTS
                # discontinuities into ONE clean monotonic timeline -> a PLAYABLE
                # ~CT clip.  (-c:v copy carried each fragment's broken PTS
                # verbatim -> unplayable, why the prior stitch was disabled.)
                # Output-side -ss (after -i) is frame-accurate on the re-encoded
                # stream; an overshoot yields empty -> single-file fallback below.
                _cmds.append([
                    ffmpeg, "-y",
                    "-fflags",     "+genpts+discardcorrupt",
                    "-err_detect", "ignore_err",
            # 2026-08-02 — conceal lost macroblocks from the previous frame
            # instead of rendering them as flat GREEN.  The camera network
            # drops RTP packets, so a -c copy recording genuinely has holes;
            # favor_inter makes what survives watchable rather than a green wall.
            "-ec", "favor_inter",
                    "-f", "concat", "-safe", "0",
                    "-i", _listfile,
                    "-ss", f"{_soff:.3f}",
                    "-t", f"{_sdur:.3f}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                    "-vf", "scale='min(iw,854)':'-2':flags=bicubic,format=yuv420p",
                    "-maxrate", "900k", "-bufsize", "1800k",
                    "-pix_fmt", "yuv420p", "-an", "-vsync", "cfr", "-r", "25",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    _work,
                ])
        except Exception:
            _cmds = []
        # 2026-07-23 — STITCH RE-ENABLED with RE-ENCODE (the 2026-06-05 disable
        # was because of -c:v copy, now fixed above).  Stitch FIRST for
        # multi-fragment cycles, then the single-file extract as the guaranteed
        # fallback; _clip_has_video() rejects a frameless stitch so we still fall
        # through to single-file (and finally a clean 404) — never worse.
        # 2026-07-27 — SINGLE-FILE COPY FIRST (instant-playback fix).
        # The 2026-06-04 order tried the STITCH first, which RE-ENCODES (libx264)
        # every cycle whose 15s window the glob matched to >=2 .ts files.  Under
        # this box's 61-camera software-encode load (load avg ~50) that re-encode
        # took 26-34 s per clip -> operator: "video bhot time le rahi / buffer".
        # A single-file `-c copy` extract of the SAME cycle is ~0.03 s (measured).
        # So try the zero-encode copy FIRST; accept it unless it came back
        # TRUNCATED (cycle genuinely spanned a mid-recording restart), and only
        # then fall back to the (rare, slow) stitch re-encode.
        _stitch_cmds = _cmds                      # 0 or 1 stitch candidate
        # 2026-09-12 — clip extraction stays on libx264 (CPU) BY DESIGN.  The
        # scarce resource on this box is the A2000's single NVENC engine, which the
        # ~23 live MAIN recorders now keep ~87% busy (they moved to NVENC to free
        # the CPU).  Measured 6-concurrent renders of a 15 s clip: GPU = 5-8 s each
        # (NVENC-contended), libx264 = 1.3-1.5 s each (the 64-core CPU is 40-70%
        # idle now that recorders are off it).  So for the LATENCY-CRITICAL on-click
        # clip the CPU path is the fast one — every open transcodes out of the .ts,
        # and 1.5 s on free CPU beats 6 s on a contended GPU.  (An earlier GPU-first
        # attempt here made clicks SLOWER; reverted.)
        _ordered = [_single_cmd] + _stitch_cmds   # copy first, stitch after

        def _clip_duration(_p):
            """Seconds of video in _p, or -1.0 when it cannot be probed."""
            try:
                _r = subprocess.run(
                    [_ffprobe_bin, "-v", "error", "-show_entries",
                     "format=duration", "-of", "csv=p=0", _p],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
                return float((_r.stdout or b"0").decode("utf-8", "ignore").strip() or 0)
            except Exception:
                return -1.0

        def _clip_long_enough(_p, _want):
            """True if the extracted clip covers ~the full cycle (not truncated)."""
            _d = _clip_duration(_p)
            if _d < 0:
                return True   # can't probe -> don't block a valid clip
            return _d >= max(1.0, _want - 1.5)

        # ffmpeg can exit non-zero on a benign trailing-packet warning yet still
        # write a playable clip, so success = "non-empty file with >=1 frame".
        #
        # 2026-08-03 — CONCURRENT PLAY REQUESTS.  Operators open several dots,
        # and re-click the same one, in quick succession.  Every request used to
        # start its own ffmpeg, so N clicks meant N transcodes: the box spiked
        # and the clip already playing began to stutter.  `_clip_gate` fixes both
        # halves — it serialises per clip key (so twins of the SAME window wait
        # and then find it cached instead of re-rendering) and caps how many
        # DIFFERENT clips may transcode at once.  It is a context manager, so a
        # timeout or a crash inside the loop always releases it.
        with _clip_gate(_out_path):
            # A twin finished it while we queued.  For a finished window trust the
            # published file cheaply (size only) instead of the ~1.5 s _cached_full
            # ffprobe — this is the concurrent second request after the first
            # render publishes.
            if ((_win_age > 20 and os.path.exists(_out_path)
                 and os.path.getsize(_out_path) > 40 * 1024)
                    or _cached_full(_out_path)):
                _ordered = []
            # 2026-08-03 — KEEP THE BEST ATTEMPT.  The loop used to leave whatever
            # the LAST command wrote in _work: a slightly-short but perfectly
            # watchable single-file extract was rejected by the length check,
            # the stitch fallback then ran, produced nothing, and clobbered
            # _work — so the endpoint 404'd a cycle it had already successfully
            # extracted.  (Measured: a 16.8 s cycle yielded a 15.0 s copy, 0.3 s
            # under the threshold, and came back "no video recorded".)  Now every
            # candidate is measured and the LONGEST valid one is preserved, so a
            # failed fallback can only ever leave us where we already were.
            _best_dur  = -1.0
            _best_path = _work + ".best"
            for _idx, _try_cmd in enumerate(_ordered):
                try:
                    _rr = subprocess.run(_try_cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, timeout=30)
                except Exception as _rexc:
                    print(f"[CLIP] ffmpeg spawn/timeout ({_idx}): {_rexc}", flush=True)
                    continue   # timeout / spawn error -- try next candidate
                if not _clip_has_video(_work):
                    _tail = (_rr.stderr or b"").decode("utf-8", "ignore").strip()
                    _tail = " / ".join(_tail.splitlines()[-3:])[:300]
                    print(f"[CLIP] empty render ({_idx}) rc={_rr.returncode}: {_tail}",
                          flush=True)
                    continue
                _this_dur = _clip_duration(_work)
                if _this_dur > _best_dur:
                    _best_dur = _this_dur
                    try:
                        os.replace(_work, _best_path)   # stash; next cmd rewrites _work
                    except Exception:
                        _best_dur = -1.0                # stash failed -> leave _work be
                        break
                if _clip_long_enough(_best_path if _best_dur >= 0 else _work, trim_dur):
                    break                               # full-length: stop trying
            if _best_dur >= 0 and os.path.exists(_best_path):
                try:
                    os.replace(_best_path, _work)       # publish the longest attempt
                except Exception:
                    pass
            try:
                if os.path.exists(_best_path):
                    os.remove(_best_path)
            except Exception:
                pass
        try:
            if _listfile and os.path.exists(_listfile):
                os.remove(_listfile)
        except Exception:
            pass

        if _clip_has_video(_work):
            try:
                os.replace(_work, _out_path)   # atomic publish
            except Exception:
                # Lost a race (another request published the same key / file
                # already open) -- drop our copy and serve the published one.
                try: os.remove(_work)
                except Exception: pass
        else:
            try: os.remove(_work)
            except Exception: pass

    if not _clip_has_video(_out_path):
        # No real footage for this window — camera stalled/offline for the cycle.
        # 404 (not a 200 empty stub) so the player shows a clean "no video for
        # this cycle" instead of a NS_ERROR_DOM_MEDIA_DEMUXER_ERR decode error.
        try:
            if os.path.exists(_out_path):
                os.remove(_out_path)
        except Exception:
            pass
        return "No video recorded for this cycle (camera stalled/offline)", 404

    resp = send_file(_out_path, mimetype="video/mp4", conditional=True)
    resp.headers["Accept-Ranges"] = "bytes"
    # 2026-07-31 — CACHEABLE.  A cycle clip is immutable: (camera, ts_start,
    # ts_end) always renders the same video, so `no-store` was forcing every
    # replay — and every remote viewer — to re-pull the whole file across the
    # site's single (weak, WiFi) internet uplink.  1 h of public caching lets
    # the browser reuse a clip instantly on replay, and lets a Cloudflare cache
    # rule serve other viewers from the POP nearest them instead of the origin.
    resp.headers["Cache-Control"] = "public, max-age=3600"
    # No close-time delete: the file is intentionally reused by the player's
    # follow-up Range requests for this same clip; the stale sweep above
    # reclaims it within ~10 min.
    return resp


if __name__ == "__main__":
    _start_video_cleanup_worker()
    app.run(host="0.0.0.0", port=5555, debug=False, threaded=True)
