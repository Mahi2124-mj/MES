"""
bd_clip.py — the camera clip of an AUTO breakdown slip, for the MES slip views
(2026-10-17, operator: "9965 jaisa slip ke saath Video + ID").

The clips are cut by Phase2/breakdown_clipper.py (user unit breakdown-clipper):
    <BDCLIP_MAINT_ROOT>/YYYY/MM/DD/<slip id>__<machine>__<start>__<end>[__cam2].mp4
    <BDCLIP_TOOLROOM_ROOT>/YYYY/MM/DD/<slip id>__...
A slip can have a second camera (`__cam2`).  Same rules as Maintenance_DX's
routers/bd_clip.py (9965):
  * nothing loads until "▶ Video" is pressed: `/info` is a small JSON;
  * <video> cannot send an Authorization header, so `/info` (behind login and the
    user's line scope) hands out a 30-min signed link; the link carries only the
    slip identity + camera number — the file path is resolved on the server
    every time, never taken from the request;
  * `/file` streams in 64 KB pieces with HTTP Range, so the browser buffers a few
    seconds at a time and seeking fetches only that part.  Never a partial
    (`.part`) file.

  GET /api/bd-clip/{src}/{sid}/info  -> {available, reason?, clips: [{label, url, size_mb, name}]}
  GET /api/bd-clip/file?t=<signed>   -> video/mp4 (206 with Range)
"""
import glob
import os
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt

import auth
from auth import get_current_user

router = APIRouter(prefix="/api/bd-clip", tags=["bd-clip"])

_DISK = os.environ.get("BDCLIP_DISK", "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735")
ROOTS = {
    "maintenance": os.environ.get("BDCLIP_MAINT_ROOT", os.path.join(_DISK, "Maintenance_breakdown_video")),
    "toolroom": os.environ.get("BDCLIP_TOOLROOM_ROOT", os.path.join(_DISK, "Toolroom_breakdown_video")),
}
# slip `src` spellings used across the MES pages
_SRC_OF = {"maintenance": "maintenance", "auto-maint": "maintenance", "automaint": "maintenance",
           "toolroom": "toolroom", "auto-toolroom": "toolroom", "autotoolroom": "toolroom"}
_TABLE_OF = {"maintenance": "maintenance_auto_breakdown_slip", "toolroom": "toolroom_auto_breakdown_slip"}
LINK_MIN = 30
CHUNK = 64 * 1024


def _src(s: str) -> str:
    v = _SRC_OF.get((s or "").strip().lower())
    if not v:
        raise HTTPException(400, "src must be maintenance or toolroom")
    return v


def _clips(src: str, sid: int) -> list:
    """This slip's finished clips, camera 1 first (newest cut wins per camera)."""
    root = ROOTS.get(src) or ""
    if not root or not os.path.isdir(root):
        return []
    found = []
    for pat in (os.path.join(root, "*", "*", "*", f"{int(sid)}__*"), os.path.join(root, f"{int(sid)}__*")):
        found += [p for p in glob.glob(pat) if p.lower().endswith(".mp4") and os.path.isfile(p)]
    by_cam = {}
    for p in found:
        m = re.search(r"__cam(\d+)\.mp4$", os.path.basename(p), re.I)
        cam = int(m.group(1)) if m else 1
        if cam not in by_cam or os.path.getmtime(p) > os.path.getmtime(by_cam[cam]):
            by_cam[cam] = p
    return [(cam, by_cam[cam]) for cam in sorted(by_cam)]


def _check_scope(src: str, sid: int, user) -> None:
    """The slip must exist and its line be in the user's scope (same rule as the
    Breakdown History slip view)."""
    try:
        from routers.breakdown_history import _in_scope
        from routers.andon import _maint_conn, _user_line_scope
        import psycopg2.extras
        with _maint_conn() as mc:
            cur = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT line FROM {_TABLE_OF[src]} WHERE id = %s", (int(sid),))
            r = cur.fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"slip lookup failed: {exc}")
    if not r:
        raise HTTPException(404, "Slip not found")
    if not _in_scope(_user_line_scope(user), r.get("line")):
        raise HTTPException(403, "This line is not assigned to you")


@router.get("/{src}/{sid}/info")
def clip_info(src: str, sid: int, user=Depends(get_current_user)):
    s = _src(src)
    _check_scope(s, sid, user)
    clips = _clips(s, sid)
    if not clips:
        return {"available": False, "clips": [],
                "reason": "No video for this slip yet. The clip is made after the breakdown ends."}
    out = []
    for cam, p in clips:
        tok = jwt.encode({"purpose": "bdclip", "src": s, "sid": int(sid), "cam": cam,
                          "exp": datetime.utcnow() + timedelta(minutes=LINK_MIN)},
                         auth.SECRET_KEY, algorithm=auth.ALGORITHM)
        out.append({"label": f"Camera {cam}", "url": f"/api/bd-clip/file?t={tok}",
                    "size_mb": round(os.path.getsize(p) / 1048576, 1), "name": os.path.basename(p)})
    return {"available": True, "clips": out}


def _pieces(path: str, start: int, end: int):
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            b = f.read(min(CHUNK, left))
            if not b:
                break
            left -= len(b)
            yield b


@router.get("/file")
def clip_file(request: Request, t: str = Query("")):
    try:
        d = jwt.decode(t, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        if d.get("purpose") != "bdclip":
            raise JWTError()
    except JWTError:
        raise HTTPException(401, "Video link expired - open the video again.")
    s = _SRC_OF.get(str(d.get("src") or ""))
    if not s:
        raise HTTPException(400, "bad link")
    path = dict(_clips(s, int(d.get("sid") or 0))).get(int(d.get("cam") or 1))
    if not path:
        raise HTTPException(404, "Video not found")
    size = os.path.getsize(path)
    start, end, status = 0, size - 1, 200
    m = re.match(r"bytes=(\d*)-(\d*)$", (request.headers.get("range") or "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                       # "bytes=-N": the last N bytes
            start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            raise HTTPException(416, "Range not satisfiable")
        status = 206
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
               "Cache-Control": "private, max-age=600"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_pieces(path, start, end), status_code=status,
                             media_type="video/mp4", headers=headers)
