# ════════════════════════════════════════════════════════════════
# routers/binfilling.py   →  /api/binfilling/...
# ════════════════════════════════════════════════════════════════
"""Bin Filling (BinVision) integration — PURELY ADDITIVE.

BinVision is a standalone AI bin-filling verification app (its own FastAPI on
127.0.0.1:8090, its own SQLite DB + vision pipeline). Nothing in BinVision or in
serve_prod is modified. This router simply reverse-proxies BinVision under
/api/binfilling/* so its dashboard shows up as a native MES page that rides the
SAME origin, tunnel and login as the rest of MES (any /api/* → serve_prod → this
backend → BinVision), exactly like the 6 Sigma page pattern.

How the proxy works
-------------------
* GET /api/binfilling/            → fetches BinVision's index.html and rewrites
                                     its absolute '/api/...' calls to
                                     '/api/binfilling/bv/api/...' so they come
                                     back through this proxy instead of colliding
                                     with the real MES API. (Rewrite is on the
                                     fly — BinVision's file on disk is untouched.)
* ANY /api/binfilling/bv/{path}   → streamed reverse-proxy to 127.0.0.1:8090/{path}
                                     (JSON, images and MJPEG previews all pass
                                     through).

Auth: reuses the MES login. Every request must carry a valid MES token via the
`mes_bv` cookie (set by the MES Bin Filling page), an Authorization: Bearer
header, or a `?t=` query param — so exposing this on the tunnel does NOT expose
BinVision unauthenticated. BinVision itself has no login; this is the gate.

BinVision's live-push WebSocket (/ws) is intentionally NOT proxied: serve_prod
does not forward WebSockets, and the dashboard already refreshes every 3-20s by
polling, so it stays fully functional (only the optional live tile-push is idle).
"""
import os

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from auth import username_from_token

router = APIRouter(prefix="/api/binfilling", tags=["binfilling"])

# BinVision runs on this host:port (see Bin-Filling/app/run_local.py, default
# BINVISION_PORT=8090). Override with BINVISION_BASE if it moves.
BINVISION_BASE = os.environ.get("BINVISION_BASE", "http://127.0.0.1:8090").rstrip("/")
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Hop-by-hop / connection headers we must not forward in either direction.
_STRIP_REQ  = {"host", "content-length", "connection", "keep-alive",
               "transfer-encoding", "cookie", "authorization", "accept-encoding"}
_STRIP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length",
               "content-encoding"}

_OFFLINE_HTML = (
    "<!doctype html><meta charset='utf-8'>"
    "<body style='margin:0;height:100vh;display:flex;flex-direction:column;"
    "align-items:center;justify-content:center;gap:12px;font-family:system-ui,"
    "-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0'>"
    "<div style='font-size:44px'>&#128230;</div>"
    "<div style='font-weight:800;font-size:18px'>Bin Filling service is offline</div>"
    "<div style='color:#8092af;max-width:520px;text-align:center;font-size:14px'>"
    "The BinVision service (port 8090) is not running on the server, so there is "
    "nothing to display yet.</div></body>"
)


def _token(request: Request):
    tok = request.cookies.get("mes_bv")
    if not tok:
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            tok = h[7:]
    if not tok:
        tok = request.query_params.get("t")
    return tok


def _require(request: Request):
    if not username_from_token(_token(request)):
        raise HTTPException(401, "MES login required for Bin Filling")


@router.get("")
@router.get("/")
async def bv_index(request: Request):
    """Serve BinVision's dashboard HTML with its /api paths rewritten to route
    back through this proxy."""
    _require(request)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{BINVISION_BASE}/")
    except Exception:
        return HTMLResponse(_OFFLINE_HTML, status_code=503)
    # Only the API calls are absolute ('/api/...'); the page has no /static or
    # external assets. The WebSocket ('/ws') is left as-is (harmless 404 → the
    # dashboard falls back to its polling, which carries every number anyway).
    html = r.text.replace("/api/", "/api/binfilling/bv/api/")
    return HTMLResponse(html, status_code=r.status_code)


@router.api_route("/bv/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def bv_proxy(path: str, request: Request):
    """Streamed reverse-proxy for every BinVision API call the dashboard makes."""
    _require(request)
    url = f"{BINVISION_BASE}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQ}

    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        req = client.build_request(request.method, url, headers=fwd, content=body)
        upstream = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise HTTPException(503, "Bin Filling service offline")

    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in _STRIP_RESP}

    async def _body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _body(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
