"""Control Board → Save PDF hand-off for the Android app.  2026-11-06.

The app's WebView cannot save a file the page builds itself (blob download),
but it hands any download from the server to the phone's browser
(MainActivity DownloadListener → ACTION_VIEW), which saves it to Downloads —
the same path the in-app "Update now" uses.  So inside the app the Control
Board uploads the finished A3 PDF here and opens the returned link.

POST /api/control-board/pdf?name=<file>.pdf     body = the PDF bytes (logged-in user)
     -> {"url": "/api/control-board/pdf/<token>/<file>.pdf", "expires_in": 600}
GET  /api/control-board/pdf/<token>/<file>.pdf  -> the PDF as an attachment.
     No login (the phone's browser has none): the random 32-hex token is the
     key, and the file is deleted after 10 minutes.

Files live in Phase2/cache/cb_pdf (shared by the API workers); old ones are
removed on every upload and on every download.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from auth import get_current_user

router = APIRouter(prefix="/api/control-board", tags=["control-board"])

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "cb_pdf")
_TTL_S = 600
_MAX_BYTES = 25 * 1024 * 1024
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def _clean_name(name: str) -> str:
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(name or "")).strip(" .")
    n = re.sub(r"\s+", " ", n)[:150] or "control-board"
    return n if n.lower().endswith(".pdf") else n + ".pdf"


def _prune() -> None:
    try:
        now = time.time()
        for f in os.listdir(_DIR):
            p = os.path.join(_DIR, f)
            try:
                if now - os.path.getmtime(p) > _TTL_S:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


@router.post("/pdf")
async def upload_pdf(request: Request, name: str = "control-board.pdf", user=Depends(get_current_user)):
    body = await request.body()
    if len(body) > _MAX_BYTES:
        raise HTTPException(413, "PDF too large")
    if not body.startswith(b"%PDF-"):
        raise HTTPException(400, "not a PDF")
    os.makedirs(_DIR, exist_ok=True)
    _prune()
    token = secrets.token_hex(16)
    tmp = os.path.join(_DIR, token + ".part")
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, os.path.join(_DIR, token + ".pdf"))
    fname = _clean_name(name)
    return {"url": f"/api/control-board/pdf/{token}/{quote(fname)}", "expires_in": _TTL_S}


@router.get("/pdf/{token}/{fname}")
def download_pdf(token: str, fname: str):
    if not _TOKEN.match(token):
        raise HTTPException(404, "not found")
    _prune()
    path = os.path.join(_DIR, token + ".pdf")
    if not os.path.isfile(path):
        raise HTTPException(404, "This PDF link has expired. Save the PDF again.")
    return FileResponse(path, media_type="application/pdf", filename=_clean_name(fname))
