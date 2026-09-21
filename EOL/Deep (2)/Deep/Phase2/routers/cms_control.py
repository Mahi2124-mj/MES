"""
routers/cms_control.py
======================
MES → CMS control proxy.  2026-06-19.

The CMS (camera system) owns the actual camera recorders, but the per-line
Video ON/OFF UI lives in the MES Production Panel (the operator manages it from
there, not from the CMS).  The MES frontend calls these endpoints; we proxy the
toggle over LOOPBACK to the CMS's internal endpoints (http://127.0.0.1:5555),
which are loopback-only (a request proxied in through Caddy is rejected there).

Turning a line's video OFF only stops that line's camera recording/live-view to
shed PC load — line counting (this MES collector) is completely unaffected.
"""
import os
import json as _json
import urllib.request
import urllib.error

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user

router = APIRouter(prefix="/api/cms", tags=["cms-control"])

_CMS_BASE = os.environ.get("CMS_BASE_URL", "http://127.0.0.1:5555")
_TIMEOUT  = 4


class VideoLineBody(BaseModel):
    line:    str
    enabled: bool


def _cms_get(path: str):
    req = urllib.request.Request(_CMS_BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return _json.loads(r.read().decode("utf-8"))


def _cms_post(path: str, body: dict):
    data = _json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _CMS_BASE + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return _json.loads(r.read().decode("utf-8"))


@router.get("/video-lines")
def get_video_lines(user=Depends(get_current_user)):
    """Per-line video state: {disabled_lines:[...], video_enabled:bool}.
    `disabled_lines` are line_names whose camera recording is OFF."""
    try:
        r = _cms_get("/api/internal/video-lines")
        return r.get("data", r)
    except urllib.error.URLError as e:
        raise HTTPException(502, f"CMS not reachable: {e}")
    except Exception as e:
        raise HTTPException(502, f"CMS error: {e}")


@router.post("/video-line")
def set_video_line(body: VideoLineBody, user=Depends(get_current_user)):
    """Turn a line's camera recording ON/OFF (admin/zone).  Proxies to the CMS."""
    if user.get("role") not in ("admin", "zone"):
        raise HTTPException(403, "Zone or Admin role required")
    line = (body.line or "").strip()
    if not line:
        raise HTTPException(400, "line is required")
    try:
        r = _cms_post("/api/internal/video-line", {"line": line, "enabled": bool(body.enabled)})
        return r.get("data", r)
    except urllib.error.URLError as e:
        raise HTTPException(502, f"CMS not reachable: {e}")
    except Exception as e:
        raise HTTPException(502, f"CMS error: {e}")
