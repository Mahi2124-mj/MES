"""
meeting_videos.py — Local Meeting Video (2026-11-13).

Operator: a supervisor picks cycle videos on the Supervisor wall ("Save to
meeting"); a separate page shows them, zone-wise and line-wise, with the chosen
one playing large at the top, so they can be shown in the line meeting on the
65-inch panel without downloading anything.  Each video stays 10 days.

WHY A COPY
A cycle clip is cut on demand from a rolling ~50-minute recording, and the clip
archive only keeps some clips (30 days).  So the moment a video is saved, the
bytes the supervisor is watching are fetched through the same /cycle-video
route the wall plays (identical file) and kept in MEETING_VIDEO_ROOT, next to
the clip archive on the video volume.  After KEEP_DAYS the row and the file are
deleted.

Endpoints (login required):
  POST   /api/meeting-videos                 save a cycle video (copy runs in the background)
  GET    /api/meeting-videos                 list (zone / line filter), newest first
  GET    /api/meeting-videos/{id}            one row (status while saving)
  GET    /api/meeting-videos/{id}/video      the file (Range-aware; ?token= for <video src>)
  DELETE /api/meeting-videos/{id}            remove (who saved it, or incharge / admin)
"""
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth import get_current_user, get_user_from_db, username_from_token
from database import get_conn, dict_cursor
from routers import clip_archive

router = APIRouter(prefix="/api/meeting-videos", tags=["meeting-videos"])

KEEP_DAYS = 10
ROOT = os.environ.get("MEETING_VIDEO_ROOT",
                      os.path.join(os.path.dirname(clip_archive.ARCHIVE_ROOT.rstrip("/")), "meeting_videos"))
SELF_BASE = clip_archive.SELF_BASE
FETCH_TIMEOUT = 180            # an Alarm clip that is not archived renders on demand
MAX_BYTES = 400 * 1024 * 1024  # a cycle clip is a few MB; anything this big is wrong
MIN_BYTES = 2048
STALE_SAVING_MIN = 10          # a copy still "saving" after this died with its process
FFMPEG = os.environ.get("MEETING_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("MEETING_FFPROBE", "ffprobe")
REMOVE_ROLES = ("admin", "plant_head", "section_incharge", "shift_incharge", "production_incharge")

_ready = False
_ready_lock = threading.Lock()
_last_sweep = 0.0


def _ensure(cur) -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        cur.execute("SET LOCAL lock_timeout = '3s'")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_meeting_video (
                id            SERIAL PRIMARY KEY,
                line_id       INTEGER NOT NULL,
                kind          TEXT NOT NULL,          -- 'line' (Final Inspection) | 'sub' (machine)
                owner_id      INTEGER NOT NULL,       -- line id or sub-machine (mes_plc_configs) id
                machine_name  TEXT,
                machine_label TEXT,
                cycle_seq     INTEGER NOT NULL,
                is_ng         BOOLEAN NOT NULL DEFAULT FALSE,
                rec_date      TEXT,                   -- only for a cycle from another shift
                shift         TEXT,
                cycle_ts      TIMESTAMPTZ,
                ct            NUMERIC,
                ideal_ct      NUMERIC,
                status        TEXT NOT NULL DEFAULT 'saving',   -- saving | ready | failed
                error         TEXT,
                file_name     TEXT,
                size_bytes    BIGINT,
                added_by      TEXT,
                added_by_id   INTEGER,
                added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at    TIMESTAMPTZ NOT NULL
            )""")
        # 2026-11-13 — name typed when saving (shown after the default name) and
        # the cycle number the wall shows (can differ from cycle_seq)
        cur.execute("""SELECT attname FROM pg_attribute WHERE attrelid = 'mes_meeting_video'::regclass
                        AND attname IN ('title', 'cycle_no') AND NOT attisdropped""")
        have = {r["attname"] if isinstance(r, dict) else r[0] for r in cur.fetchall()}
        if not {"title", "cycle_no"} <= have:
            cur.execute("ALTER TABLE mes_meeting_video ADD COLUMN IF NOT EXISTS title TEXT, "
                        "ADD COLUMN IF NOT EXISTS cycle_no INTEGER")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_meeting_video_line ON mes_meeting_video (line_id, added_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_meeting_video_exp ON mes_meeting_video (expires_at)")
        _ready = True


def _allowed_lines(user) -> Optional[set]:
    """None = every line; else the line ids this login may see (as /api/lines/)."""
    if not user or user.get("role") == "admin":
        return None
    from routers.lines import list_lines
    return {int(l["id"]) for l in (list_lines(plant_id=None, user=user) or [])}


def _can_remove(user, row) -> bool:
    return (user.get("role") in REMOVE_ROLES) or (row.get("added_by_id") and row["added_by_id"] == user.get("id"))


def _path(file_name: str) -> str:
    return os.path.join(ROOT, file_name)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _out(r: dict, user) -> dict:
    return {
        "id": r["id"], "line_id": r["line_id"], "line_name": r.get("line_name"),
        "zone_id": r.get("zone_id"), "zone_name": r.get("zone_name") or "Other",
        "kind": r["kind"], "owner_id": r["owner_id"],
        "machine_name": r.get("machine_name"), "machine_label": r.get("machine_label"),
        "cycle_seq": r["cycle_seq"], "cycle_no": r.get("cycle_no") or r["cycle_seq"],
        "title": r.get("title"), "is_ng": bool(r["is_ng"]),
        "rec_date": r.get("rec_date"), "shift": r.get("shift"),
        "cycle_ts": _iso(r.get("cycle_ts")),
        "ct": float(r["ct"]) if r.get("ct") is not None else None,
        "ideal_ct": float(r["ideal_ct"]) if r.get("ideal_ct") is not None else None,
        "status": r["status"], "error": r.get("error"),
        "size_bytes": r.get("size_bytes"),
        "added_by": r.get("added_by"), "added_at": _iso(r.get("added_at")),
        "expires_at": _iso(r.get("expires_at")),
        "can_remove": bool(user and _can_remove(user, r)),
    }


_SELECT = """SELECT v.*, l.line_name, l.zone_id, z.zone_name
               FROM mes_meeting_video v
               LEFT JOIN mes_lines l ON l.id = v.line_id
               LEFT JOIN mes_zones z ON z.id = l.zone_id"""


# ── housekeeping ─────────────────────────────────────────────────────────
def _sweep(force: bool = False) -> None:
    """Delete expired videos (row + file) and close copies that died mid-save.
    DELETE ... RETURNING hands each row to exactly one process."""
    global _last_sweep
    if not force and time.time() - _last_sweep < 600:
        return
    _last_sweep = time.time()
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            _ensure(cur)
            cur.execute("DELETE FROM mes_meeting_video WHERE expires_at < now() RETURNING id, file_name")
            gone = cur.fetchall()
            cur.execute("""UPDATE mes_meeting_video SET status = 'failed',
                                  error = 'The copy was interrupted. Save the video again.'
                            WHERE status = 'saving' AND added_at < now() - make_interval(mins => %s)""",
                        (STALE_SAVING_MIN,))
            conn.commit()
        for g in gone:
            if g.get("file_name"):
                try:
                    os.remove(_path(g["file_name"]))
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"[MEETING] could not delete {g['file_name']}: {e}", flush=True)
        if gone:
            print(f"[MEETING] removed {len(gone)} video(s) older than {KEEP_DAYS} days", flush=True)
        # empty day folders
        if os.path.isdir(ROOT):
            for d in os.listdir(ROOT):
                p = os.path.join(ROOT, d)
                try:
                    if os.path.isdir(p) and not os.listdir(p):
                        os.rmdir(p)
                except Exception:
                    pass
    except Exception as e:
        print(f"[MEETING] sweep failed: {type(e).__name__}: {e}", flush=True)


def start_meeting_sweeper() -> None:
    """Background leader only: sweep once an hour even if nobody opens the page."""
    def _loop():
        time.sleep(120)
        while True:
            _sweep(force=True)
            time.sleep(3600)
    threading.Thread(target=_loop, name="meeting-video-sweep", daemon=True).start()
    print(f"[MEETING] sweeper started (keep {KEEP_DAYS} days, folder {ROOT})", flush=True)


# ── the copy ─────────────────────────────────────────────────────────────
def _codec(path: str) -> str:
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout or "").strip().lower()
    except Exception:
        return ""


def _to_h264(src: str, dst: str) -> bool:
    """Machine cameras record HEVC and the clip is a stream copy; HEVC plays
    only where the PC / panel happens to decode it (see routers/sixsigma.py).
    The meeting copy is made once, so convert it to the H.264 every browser
    plays: same settings as 6 Sigma, 2 threads at nice 10."""
    tmp = dst + ".h264.part"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src,
           "-vf", "scale=in_range=full:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-profile:v", "baseline", "-level", "3.1", "-color_range", "tv",
           "-an", "-movflags", "+faststart", "-threads", "2", "-f", "mp4", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, preexec_fn=lambda: os.nice(10))
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < MIN_BYTES:
            print(f"[MEETING] H.264 conversion failed rc={r.returncode}: {r.stderr[:200]!r}", flush=True)
            return False
        os.replace(tmp, dst)
        return True
    except Exception as e:
        print(f"[MEETING] H.264 conversion error: {e}", flush=True)
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _copy(vid: int, kind: str, owner: int, seq: int, ng: bool, date: Optional[str], shift: Optional[str]) -> None:
    base = "lines" if kind == "line" else "submachines"
    url = f"{SELF_BASE}/api/{base}/{owner}/cycle-video?cycle_seq={seq}&ng={1 if ng else 0}"
    if date:
        url += f"&date={requests.utils.quote(date)}&shift={requests.utils.quote(shift or '')}"
    day = datetime.now().strftime("%Y-%m-%d")
    rel = os.path.join(day, f"meeting_{vid}.mp4")
    dest = _path(rel)
    tmp = dest + ".part"
    status, err, size = "failed", None, None
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with requests.get(url, timeout=FETCH_TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                err = ("No video was found for this cycle." if r.status_code in (404, 416)
                       else f"The video server answered {r.status_code}. Try again.")
            else:
                n = 0
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(256 * 1024):
                        if not chunk:
                            continue
                        n += len(chunk)
                        if n > MAX_BYTES:
                            raise ValueError("clip larger than the limit")
                        fh.write(chunk)
                if n < MIN_BYTES:
                    err = "No video was found for this cycle."
                else:
                    codec = _codec(tmp)
                    if codec and codec != "h264" and _to_h264(tmp, dest):
                        status, size = "ready", os.path.getsize(dest)
                    else:
                        os.replace(tmp, dest)
                        status, size = "ready", n
    except Exception as e:
        err = f"The video could not be copied ({type(e).__name__})."
        print(f"[MEETING] copy {vid} failed: {type(e).__name__}: {e}", flush=True)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE mes_meeting_video SET status = %s, error = %s, file_name = %s, size_bytes = %s
                            WHERE id = %s""",
                        (status, err, rel if status == "ready" else None, size, vid))
            gone = cur.rowcount == 0
            conn.commit()
        if gone and status == "ready":          # removed while copying
            os.remove(dest)
    except Exception as e:
        print(f"[MEETING] status update {vid} failed: {e}", flush=True)
    print(f"[MEETING] video {vid} ({kind} {owner} cycle {seq}{' NG' if ng else ''}): {status}"
          + (f", {size} bytes" if size else f", {err}"), flush=True)


# ── API ──────────────────────────────────────────────────────────────────
class SaveIn(BaseModel):
    kind: str
    owner_id: int
    line_id: Optional[int] = None
    cycle_seq: int
    ng: bool = False
    date: Optional[str] = None
    shift: Optional[str] = None
    machine_name: Optional[str] = None
    machine_label: Optional[str] = None
    cycle_ts: Optional[str] = None
    ct: Optional[float] = None
    ideal_ct: Optional[float] = None
    cycle_no: Optional[int] = None
    title: Optional[str] = None


def _clean_title(t: Optional[str]) -> Optional[str]:
    t = " ".join(str(t or "").split())
    return t[:120] or None


@router.post("")
def save_video(body: SaveIn, user=Depends(get_current_user)):
    if body.kind not in ("line", "sub"):
        raise HTTPException(400, "kind must be 'line' or 'sub'")
    if body.date and (len(body.date) != 10 or body.date[4] != "-" or body.date[7] != "-"):
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        if body.kind == "line":
            cur.execute("SELECT id FROM mes_lines WHERE id = %s", (body.owner_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Line not found")
            line_id = body.owner_id
        else:
            cur.execute("SELECT line_id, machine_name FROM mes_plc_configs WHERE id = %s", (body.owner_id,))
            m = cur.fetchone()
            if not m or not m.get("line_id"):
                raise HTTPException(404, "Machine not found")
            line_id = int(m["line_id"])
        ok = _allowed_lines(user)
        if ok is not None and line_id not in ok:
            raise HTTPException(403, "This line is not assigned to your login")
        ts = None
        if body.cycle_ts:
            try:
                ts = datetime.fromisoformat(body.cycle_ts.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        # the same cycle saved twice -> the existing copy
        cur.execute("""SELECT id FROM mes_meeting_video
                        WHERE kind = %s AND owner_id = %s AND cycle_seq = %s AND is_ng = %s
                          AND COALESCE(rec_date, '') = %s AND COALESCE(shift, '') = %s
                          AND (%s::timestamptz IS NULL OR cycle_ts IS NULL
                               OR abs(extract(epoch FROM cycle_ts - %s::timestamptz)) < 120)
                          AND status <> 'failed' AND expires_at > now()
                        ORDER BY id DESC LIMIT 1""",
                    (body.kind, body.owner_id, body.cycle_seq, bool(body.ng),
                     body.date or "", (body.shift or "") if body.date else "", ts, ts))
        dup = cur.fetchone()
        title = _clean_title(body.title)
        if dup:
            if title:      # saved before without a name: take this one
                cur.execute("UPDATE mes_meeting_video SET title = %s WHERE id = %s AND title IS NULL", (title, dup["id"]))
                conn.commit()
            cur.execute(_SELECT + " WHERE v.id = %s", (dup["id"],))
            return {"already": True, **_out(cur.fetchone(), user)}
        cur.execute("""INSERT INTO mes_meeting_video
                         (line_id, kind, owner_id, machine_name, machine_label, cycle_seq, is_ng,
                          rec_date, shift, cycle_ts, ct, ideal_ct, added_by, added_by_id, expires_at,
                          title, cycle_no)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now() + make_interval(days => %s), %s, %s)
                       RETURNING id""",
                    (line_id, body.kind, body.owner_id, (body.machine_name or "")[:120] or None,
                     (body.machine_label or "")[:60] or None, body.cycle_seq, bool(body.ng),
                     body.date, body.shift if body.date else None, ts, body.ct, body.ideal_ct,
                     user.get("username"), user.get("id"), KEEP_DAYS,
                     title, body.cycle_no if body.cycle_no and body.cycle_no > 0 else None))
        vid = cur.fetchone()["id"]
        conn.commit()
        cur.execute(_SELECT + " WHERE v.id = %s", (vid,))
        row = cur.fetchone()
    print(f"[MEETING] {user.get('username')} saved {body.kind} {body.owner_id} cycle {body.cycle_seq}"
          f"{' NG' if body.ng else ''} (line {line_id}) as video {vid}"
          + (f" named {title!r}" if title else ""), flush=True)
    threading.Thread(target=_copy, name=f"meeting-copy-{vid}", daemon=True,
                     args=(vid, body.kind, body.owner_id, body.cycle_seq, bool(body.ng),
                           body.date, body.shift)).start()
    return {"already": False, **_out(row, user)}


@router.get("")
def list_videos(zone_id: Optional[int] = Query(None), line_id: Optional[int] = Query(None),
                user=Depends(get_current_user)):
    _sweep()
    ok = _allowed_lines(user)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute(_SELECT + " WHERE v.expires_at > now() ORDER BY v.added_at DESC")
        rows = [r for r in cur.fetchall() if ok is None or r["line_id"] in ok]
    # counts for the zone / line pickers (before the filter, so every chip shows its count)
    zones = {}
    for r in rows:
        zk = r.get("zone_id") or 0
        z = zones.setdefault(zk, {"zone_id": r.get("zone_id"), "zone_name": r.get("zone_name") or "Other",
                                  "count": 0, "lines": {}})
        z["count"] += 1
        ln = z["lines"].setdefault(r["line_id"], {"line_id": r["line_id"], "line_name": r.get("line_name"), "count": 0})
        ln["count"] += 1
    zone_list = sorted(({**z, "lines": sorted(z["lines"].values(), key=lambda x: (x["line_name"] or ""))}
                        for z in zones.values()), key=lambda z: z["zone_name"])
    if zone_id is not None:
        rows = [r for r in rows if r.get("zone_id") == zone_id]
    if line_id is not None:
        rows = [r for r in rows if r["line_id"] == line_id]
    return {"keep_days": KEEP_DAYS, "zones": zone_list, "items": [_out(r, user) for r in rows]}


@router.get("/{vid}")
def get_video_row(vid: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute(_SELECT + " WHERE v.id = %s", (vid,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "Video not found")
    ok = _allowed_lines(user)
    if ok is not None and r["line_id"] not in ok:
        raise HTTPException(403, "This line is not assigned to your login")
    return _out(r, user)


@router.get("/{vid}/video")
def get_video_file(vid: int, request: Request, token: Optional[str] = Query(None)):
    """The saved file.  A <video src> cannot send a header, so ?token= is
    accepted (validated the same way as the header)."""
    raw = token
    if not raw:
        h = request.headers.get("authorization") or ""
        raw = h[7:] if h.lower().startswith("bearer ") else None
    name = username_from_token(raw)
    user = get_user_from_db(name) if name else None
    if not user:
        raise HTTPException(401, "Log in to watch this video")
    user = dict(user)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SELECT line_id, status, file_name FROM mes_meeting_video WHERE id = %s AND expires_at > now()", (vid,))
        r = cur.fetchone()
    if not r or r["status"] != "ready" or not r.get("file_name"):
        raise HTTPException(404, "Video not found")
    ok = _allowed_lines(user)
    if ok is not None and r["line_id"] not in ok:
        raise HTTPException(403, "This line is not assigned to your login")
    p = _path(r["file_name"])
    if not os.path.exists(p):
        raise HTTPException(404, "Video file is missing")
    resp = clip_archive.serve(p, request)
    resp.headers["Content-Disposition"] = f'inline; filename="meeting_video_{vid}.mp4"'
    return resp


@router.delete("/{vid}")
def remove_video(vid: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SELECT * FROM mes_meeting_video WHERE id = %s", (vid,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Video not found")
        if not _can_remove(user, r):
            raise HTTPException(403, "Only the person who saved it or a shift incharge and above can remove it")
        cur.execute("DELETE FROM mes_meeting_video WHERE id = %s", (vid,))
        conn.commit()
    if r.get("file_name"):
        try:
            os.remove(_path(r["file_name"]))
        except FileNotFoundError:
            pass
    print(f"[MEETING] {user.get('username')} removed video {vid} (saved by {r.get('added_by')})", flush=True)
    return {"ok": True}
