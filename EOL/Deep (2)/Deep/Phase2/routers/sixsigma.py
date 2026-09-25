# ════════════════════════════════════════════════════════════════
# routers/sixsigma.py   →  /api/sixsigma/...
# ════════════════════════════════════════════════════════════════
"""6 Sigma page — Ball Guide station dual-camera clip review (Seat Slider only).
#
The Ball Guide machine (Seat Slider lines) has TWO cameras. Both cut a clip on
the SAME production cycle, so every cycle produces two synchronized clips (one
per camera). Clips are retained for 40 days.

This router is purely ADDITIVE (new table, no collector / seat-slider changes):
    mes_sixsigma_config  — per-line ball-guide 2-camera + retention config

It also mirrors the two cameras into `mes_process_cameras` (role='external', the
same table the recliner/CMS clip pipeline reads) so the existing per-cycle clip
recorder records them — i.e. same-cycle capture reuses the proven pipeline.
Actual RTSP capture + per-camera clip files are produced CMS-side; the camera
RTSP URLs entered here are stored so the CMS can be pointed at them.
"""
import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, get_current_user_optional, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/sixsigma", tags=["sixsigma"])

_TBL_RE = re.compile(r"^[a-z0-9_]+$")   # per-line table names are plain idents

# The CMS cuts a clip for ONE camera between two timestamps; the sub-machine
# page already uses this endpoint, and it is the only path that can give the two
# Ball Guide cameras their own footage instead of the line's Final-Inspection
# clip.  Same default as routers/submachines.py.
CYCLE_VIDEO_BASE_URL = os.environ.get("CYCLE_VIDEO_BASE_URL", "http://127.0.0.1:5555")
CLIP_PROXY_TIMEOUT = float(os.environ.get("SIXSIGMA_CLIP_TIMEOUT", "20") or 20)

# ── browser-playable clips (2026-09-25) ─────────────────────────────────────
# "video kuch PC me render ho rahi, kuch me nahi."  The cameras on this station
# record HEVC, and the CMS cuts the clip by stream copy, so what reached the
# browser was H.265 in an MP4 tagged `hev1`.  That plays only where the machine
# happens to have HEVC support — on Windows that means the paid HEVC Video
# Extension plus a GPU that decodes it, which is why it worked on some PCs and
# showed a black box on others; `hev1` (rather than `hvc1`) is refused even by
# several players that do support HEVC.
#
# So this page — and only this page — re-encodes what the CMS returns to plain
# H.264 Constrained Baseline / yuv420p / faststart before it reaches the
# browser.  That is the profile every Edge, Chrome, Firefox and Safari has
# decoded for a decade, with no extension installed.  Measured on a real clip:
# 704x576, 16.8 s, HEVC 1.32 MB -> H.264 616 KB in 1.4 s on the CPU.
#
# libx264, not NVENC, is deliberate: the GPU encoder is already saturated by
# the CMS's live transcodes, and for a cut this small the CPU finishes sooner
# (measured elsewhere in this stack: 1.0 s CPU vs 6.6 s GPU) on a box that
# otherwise has idle cores.  The CMS is not touched.
#
# Results are cached on disk, so a clip is converted once and every later view
# (and every Range request the player makes while seeking) is served straight
# off the file.
CLIP_CACHE_DIR = os.environ.get(
    "SIXSIGMA_CLIP_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "cache", "sixsigma_clips"))
CLIP_CACHE_HOURS = float(os.environ.get("SIXSIGMA_CLIP_CACHE_HOURS", "24") or 24)
FFMPEG = os.environ.get("SIXSIGMA_FFMPEG", "ffmpeg")

_clip_locks: dict = {}
_clip_locks_guard = threading.Lock()


def _clip_lock(key: str):
    """One in-flight conversion per clip, so a double-click converts once."""
    with _clip_locks_guard:
        if len(_clip_locks) > 256:
            _clip_locks.clear()
        return _clip_locks.setdefault(key, threading.Lock())


def _prune_clip_cache():
    """Drop cached clips older than CLIP_CACHE_HOURS.  Cheap: one scandir."""
    cutoff = time.time() - CLIP_CACHE_HOURS * 3600
    try:
        with os.scandir(CLIP_CACHE_DIR) as it:
            for e in it:
                try:
                    if e.is_file() and e.stat().st_mtime < cutoff:
                        os.unlink(e.path)
                except OSError:
                    pass
    except OSError:
        pass


def _to_h264(src: str, dst: str) -> bool:
    """Re-encode `src` into a browser-safe MP4 at `dst`.  True if it worked.

    Constrained Baseline + yuv420p + limited range is the lowest common
    denominator every browser decodes; +faststart puts the moov atom first so
    playback can begin before the whole file has arrived.  The cameras tag
    their stream full-range (yuvj420p), so the range conversion is explicit
    rather than left to the encoder's guess.
    """
    tmp = dst + ".part"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", src,
           "-vf", "scale=in_range=full:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-profile:v", "baseline", "-level", "3.1", "-color_range", "tv",
           "-an", "-movflags", "+faststart",
           # `-f mp4` explicitly: the temp name ends in .part, and ffmpeg picks
           # its muxer from the extension, so without this it just refuses
           # ("Unable to choose an output format").
           "-f", "mp4", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=CLIP_PROXY_TIMEOUT * 3)
        if r.returncode != 0 or not os.path.getsize(tmp):
            print(f"[6SIGMA] transcode failed rc={r.returncode}: "
                  f"{r.stderr[:200]!r}", flush=True)
            raise RuntimeError("ffmpeg")
        os.replace(tmp, dst)
        return True
    except Exception as exc:
        print(f"[6SIGMA] transcode error: {exc}", flush=True)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False

DEFAULT_MACHINE = "Ball Guide"
DEFAULT_RETENTION = 40


@once
def _ensure(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_sixsigma_config (
                     line_id        INTEGER PRIMARY KEY,
                     machine_name   TEXT NOT NULL DEFAULT 'Ball Guide',
                     cam1_name      TEXT,
                     cam1_url       TEXT,
                     cam2_name      TEXT,
                     cam2_url       TEXT,
                     retention_days INTEGER NOT NULL DEFAULT 40,
                     updated_by     TEXT,
                     updated_at     TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # 2026-09-25 — the CMS camera id for each of the two cameras.  The config
    # stores the RTSP URL the operator typed; the CMS knows the camera by its
    # own id, and the per-camera clip endpoint needs THAT.  Filled when the
    # camera is registered with the CMS (see camera_config.add_camera).
    # 2026-09-25 — best-effort ONLY.  A plain ALTER here ran inside a request and
    # died with "canceling statement due to lock timeout" (the per-request DDL
    # trap this codebase has hit before), turning the clip call into a 500.  The
    # columns are created once; if the lock is busy we simply carry on.
    for _sql in ("ALTER TABLE mes_sixsigma_config ADD COLUMN IF NOT EXISTS cam1_cid TEXT",
                 "ALTER TABLE mes_sixsigma_config ADD COLUMN IF NOT EXISTS cam2_cid TEXT"):
        try:
            cur.execute(_sql)
        except Exception:
            try: cur.connection.rollback()
            except Exception: pass


def _ss_lines(cur):
    """Seat Slider lines only (that's where the Ball Guide machine lives)."""
    cur.execute("""
        SELECT l.id, l.line_name, l.line_code, l.db_table_name, z.zone_name
          FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
         WHERE COALESCE(l.is_active, TRUE)
           AND (z.zone_name ILIKE '%seat%slider%' OR z.zone_name ILIKE '%seat_slider%'
                OR l.line_name ILIKE '%-SS' OR l.line_name ILIKE '%_SS')
         ORDER BY l.line_name""")
    return cur.fetchall()


@router.get("/config")
def get_config(user=Depends(get_current_user)):
    """All saved ball-guide configs + the Seat Slider lines to choose from."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT c.*, l.line_name, l.line_code
                         FROM mes_sixsigma_config c
                         JOIN mes_lines l ON l.id = c.line_id
                        ORDER BY l.line_name""")
        configs = cur.fetchall()
        for c in configs:
            if c.get("updated_at"):
                c["updated_at"] = c["updated_at"].isoformat()
        lines = _ss_lines(cur)
        conn.commit()
        return {"configs": configs, "lines": lines,
                "default_machine": DEFAULT_MACHINE, "default_retention": DEFAULT_RETENTION}


class ConfigBody(BaseModel):
    line_id: int
    machine_name: str = DEFAULT_MACHINE
    cam1_name: Optional[str] = "Camera 1"
    cam1_url:  Optional[str] = None
    cam2_name: Optional[str] = "Camera 2"
    cam2_url:  Optional[str] = None
    retention_days: int = DEFAULT_RETENTION


@router.post("/config")
def save_config(body: ConfigBody, admin=Depends(require_admin)):
    if not body.line_id:
        raise HTTPException(400, "line_id required")
    rd = body.retention_days if (body.retention_days and body.retention_days > 0) else DEFAULT_RETENTION
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""INSERT INTO mes_sixsigma_config
                         (line_id, machine_name, cam1_name, cam1_url, cam2_name, cam2_url,
                          retention_days, updated_by, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
                       ON CONFLICT (line_id) DO UPDATE SET
                         machine_name=EXCLUDED.machine_name,
                         cam1_name=EXCLUDED.cam1_name, cam1_url=EXCLUDED.cam1_url,
                         cam2_name=EXCLUDED.cam2_name, cam2_url=EXCLUDED.cam2_url,
                         retention_days=EXCLUDED.retention_days,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (body.line_id, (body.machine_name or DEFAULT_MACHINE).strip(),
                     (body.cam1_name or "Camera 1").strip(), (body.cam1_url or "").strip() or None,
                     (body.cam2_name or "Camera 2").strip(), (body.cam2_url or "").strip() or None,
                     rd, admin.get("username")))
        # Mirror the two cameras into mes_process_cameras (external role) so the
        # existing per-cycle clip pipeline records them. Best-effort — never fail
        # the save if that table's shape differs.
        try:
            cur.execute("DELETE FROM mes_process_cameras WHERE line_id=%s AND role='external' AND camera_id IN (%s,%s)",
                        (body.line_id, (body.cam1_url or "").strip(), (body.cam2_url or "").strip()))
            for url in ((body.cam1_url or "").strip(), (body.cam2_url or "").strip()):
                if url:
                    cur.execute("""INSERT INTO mes_process_cameras
                                     (line_id, machine_id, process_id, camera_id, role, is_active)
                                   VALUES (%s, NULL, NULL, %s, 'external', true)""",
                                (body.line_id, url))
        except Exception:
            pass
        conn.commit()
        return {"ok": True, "retention_days": rd}


@router.delete("/config")
def delete_config(line_id: int = Query(...), admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("DELETE FROM mes_sixsigma_config WHERE line_id=%s", (line_id,))
        conn.commit()
        return {"ok": True}


@router.get("/clips")
def get_clips(line_id: int = Query(...),
              date: Optional[str] = Query(None),
              shift: Optional[str] = Query(None),
              limit: int = Query(30, ge=1, le=100),
              user=Depends(get_current_user)):
    """Recent cycles for the line's Ball Guide station, each with the two camera
    clip URLs. Reuses the existing /cycle-video proxy; the second camera is
    requested with cam=2 (honoured once the CMS emits per-camera files)."""
    rec_date = date or datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT c.*, l.db_table_name FROM mes_sixsigma_config c
                         JOIN mes_lines l ON l.id=c.line_id WHERE c.line_id=%s""", (line_id,))
        cfg = cur.fetchone()
        if not cfg:
            raise HTTPException(404, "This line has no Ball Guide 6-Sigma config yet")
        tbl = (cfg["db_table_name"] or "") + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS r", (tbl,))
        if not cur.fetchone()["r"]:
            return {"config": _cfg_public(cfg), "cycles": []}
        cols = "cycle_seq, ts, COALESCE(is_ng,false) AS is_ng"
        cur.execute(f"""SELECT column_name FROM information_schema.columns
                         WHERE table_name=%s AND column_name='part_code'""", (tbl,))
        has_part = cur.fetchone() is not None
        if has_part:
            cols += ", part_code"
        q = f"SELECT {cols} FROM {tbl} WHERE ts::date = %s"
        params = [rec_date]
        if shift:
            cur.execute(f"""SELECT column_name FROM information_schema.columns
                             WHERE table_name=%s AND column_name='shift_name'""", (tbl,))
            if cur.fetchone():
                q += " AND shift_name = %s"; params.append(shift)
        q += " ORDER BY ts DESC LIMIT %s"; params.append(limit)
        cur.execute(q, params)
        rows = cur.fetchall()
        cycles = []
        for r in rows:
            seq = r["cycle_seq"]
            base = f"/api/lines/{line_id}/cycle-video?cycle_seq={seq}&date={rec_date}"
            if shift:
                base += f"&shift={shift}"
            ng = 1 if r.get("is_ng") else 0
            # 2026-09-24 — DO NOT hand back the line's ordinary cycle clip as
            # if it were Ball Guide footage.  `/api/lines/{id}/cycle-video` has
            # no `cam` parameter (see routers/lines.py get_cycle_video), so the
            # old "&cam=1" / "&cam=2" URLs both resolved to the SAME Final
            # Inspection clip: the page played FI video under the two Ball Guide
            # camera headings, which is what the operator caught on 24-Sep
            # ("camera abhi configure kiye hain, clips kahan se aayi?").
            # Nothing records these two cameras yet — the CMS has no Ball Guide
            # recorder at all — so the honest answer is "no clip".  When the CMS
            # starts emitting per-camera files, fill these from THOSE files.
            _b = f"/api/sixsigma/clip?line_id={line_id}&cycle_seq={seq}&date={rec_date}"
            if shift:
                _b += f"&shift={shift}"
            cycles.append({
                "cycle_seq": seq,
                "ts": r["ts"].isoformat() if r.get("ts") else None,
                "is_ng": bool(r.get("is_ng")),
                "part_code": r.get("part_code"),
                # A camera only gets a URL once its CMS camera id is known —
                # otherwise the page correctly says "no clip from this camera".
                "cam1_url": (f"{_b}&cam=1" if (cfg.get("cam1_cid") or "").strip() else None),
                "cam2_url": (f"{_b}&cam=2" if (cfg.get("cam2_cid") or "").strip() else None),
            })
        conn.commit()
        note = ""
        if not ((cfg.get("cam1_cid") or "").strip() or (cfg.get("cam2_cid") or "").strip()):
            note = ("These cameras are not registered with the CMS yet, so no "
                    "footage is being recorded for this station.")
        return {"config": _cfg_public(cfg), "cycles": cycles, "clips_note": note}


@router.get("/clip")
def sixsigma_clip(line_id: int = Query(...),
                  cycle_seq: int = Query(...),
                  cam: int = Query(1, ge=1, le=2),
                  date: Optional[str] = Query(None),
                  shift: Optional[str] = Query(None),
                  token: Optional[str] = Query(None,
                      description="JWT fallback for <video src=...>"),
                  request: Request = None,
                  user=Depends(get_current_user_optional)):
    """One Ball Guide camera's footage for one cycle of this line.

    The cycle window comes from the line's own ct_log (ts is the cycle END and
    ct_value its length), and the cut itself is done by the CMS for THAT camera
    — so camera 1 and camera 2 return different video, which is the whole point
    of this page.
    """
    import requests
    from fastapi.responses import StreamingResponse

    rec_date = date or datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # no DDL on this path — the table exists; a lock wait here would only
        # turn a video request into a 500.
        cur.execute("""SELECT c.*, l.db_table_name FROM mes_sixsigma_config c
                         JOIN mes_lines l ON l.id=c.line_id WHERE c.line_id=%s""",
                    (line_id,))
        cfg = cur.fetchone()
        if not cfg:
            raise HTTPException(404, "This line has no Ball Guide 6-Sigma config")
        cid = (cfg.get("cam1_cid") if cam == 1 else cfg.get("cam2_cid")) or ""
        cid = cid.strip()
        if not cid:
            raise HTTPException(404, "This camera is not registered with the CMS yet")
        tbl = (cfg["db_table_name"] or "") + "_ct_log"
        if not _TBL_RE.match(tbl):
            raise HTTPException(400, "bad table")
        cur.execute("SELECT to_regclass(%s) AS r", (tbl,))
        if not cur.fetchone()["r"]:
            raise HTTPException(404, "no cycle log for this line")
        q = f"SELECT ts, ct_value FROM {tbl} WHERE record_date=%s AND cycle_seq=%s"
        params = [rec_date, cycle_seq]
        if shift:
            q += " AND shift_name=%s"; params.append(shift)
        q += " ORDER BY ts DESC LIMIT 1"
        cur.execute(q, params)
        row = cur.fetchone()
        conn.rollback()
    if not row or not row.get("ts"):
        raise HTTPException(404, "Cycle not found")
    ts_end = row["ts"]
    try:
        ct = float(row.get("ct_value") or 0)
    except Exception:
        ct = 0.0
    ct = min(max(ct, 3.0), 120.0)          # sane window even on a junk ct
    ts_start = ts_end - timedelta(seconds=ct)

    # ── serve a browser-playable copy ───────────────────────────────────
    # The CMS hands back the camera's own HEVC, which only some machines can
    # decode (see the note at CLIP_CACHE_DIR).  Convert once, cache, and let
    # the range-aware server from clip_archive do the rest so seeking works.
    from routers.clip_archive import serve as _serve_file

    key = hashlib.sha1(
        f"{cid}|{ts_start.isoformat()}|{ts_end.isoformat()}".encode()
    ).hexdigest()[:24]
    try:
        os.makedirs(CLIP_CACHE_DIR, exist_ok=True)
    except OSError:
        pass
    cached = os.path.join(CLIP_CACHE_DIR, f"{key}.mp4")

    if os.path.exists(cached) and os.path.getsize(cached) > 0:
        return _serve_file(cached, request)

    with _clip_lock(key):
        # Another request may have finished it while we waited on the lock.
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return _serve_file(cached, request)

        # Always fetch the WHOLE cut — a Range would give us a fragment that
        # cannot be re-encoded.  The player's Range is answered off the cached
        # file instead.
        try:
            r = requests.get(f"{CYCLE_VIDEO_BASE_URL}/api/submachine/clip",
                             params={"camera_id": cid,
                                     "ts_start": ts_start.isoformat(),
                                     "ts_end": ts_end.isoformat()},
                             stream=True, timeout=CLIP_PROXY_TIMEOUT)
        except Exception as exc:
            raise HTTPException(502, f"Upstream unreachable: {exc}")
        if r.status_code >= 400:
            detail, code = f"Upstream: {r.text[:200]}", r.status_code
            try: r.close()
            except Exception: pass
            raise HTTPException(code, detail)

        raw = os.path.join(CLIP_CACHE_DIR, f"{key}.src")
        try:
            with open(raw, "wb") as fh:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        fh.write(chunk)
        except Exception as exc:
            raise HTTPException(502, f"Upstream read failed: {exc}")
        finally:
            try: r.close()
            except Exception: pass

        ok = _to_h264(raw, cached)
        try:
            os.unlink(raw)
        except OSError:
            pass
        _prune_clip_cache()

        if not ok:
            # Never leave the page blank because a conversion failed: hand the
            # original through, which still plays wherever HEVC is supported.
            def _passthrough():
                rr = requests.get(f"{CYCLE_VIDEO_BASE_URL}/api/submachine/clip",
                                  params={"camera_id": cid,
                                          "ts_start": ts_start.isoformat(),
                                          "ts_end": ts_end.isoformat()},
                                  stream=True, timeout=CLIP_PROXY_TIMEOUT)
                try:
                    for chunk in rr.iter_content(chunk_size=64 * 1024):
                        yield chunk
                finally:
                    try: rr.close()
                    except Exception: pass
            return StreamingResponse(
                _passthrough(), media_type="video/mp4",
                headers={"Accept-Ranges": "bytes",
                         "Cache-Control": "no-cache, no-store, must-revalidate",
                         "X-Clip-Codec": "source-passthrough"})

    return _serve_file(cached, request)


def _cfg_public(cfg):
    return {
        "line_id": cfg["line_id"], "machine_name": cfg.get("machine_name"),
        "cam1_name": cfg.get("cam1_name"), "cam2_name": cfg.get("cam2_name"),
        "retention_days": cfg.get("retention_days", DEFAULT_RETENTION),
    }
