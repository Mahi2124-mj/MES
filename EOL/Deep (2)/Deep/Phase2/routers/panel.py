# ───────────────────────────────────────────────────────────────────────
# panel.py   (/api/panel)   2026-09-13
# ───────────────────────────────────────────────────────────────────────
# Panel-IP → auto-open mapping.  Each physical LAN panel (by its IP) can be
# mapped to one line + view (Management / Supervisor / Fullscreen / Dashboard);
# when the LAN app opens on that panel it jumps straight there.
# The real panel IP reaches MES-API via the X-Forwarded-For header that
# serve_prod (:5656) adds when it proxies /api.
# ───────────────────────────────────────────────────────────────────────
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, create_token, get_user_from_db
from ddl_once import once

router = APIRouter(prefix="/api/panel", tags=["panel"])

_VIEWS = {
    "management": lambda code: f"/{code}/MANAGEMENT",
    "supervisor": lambda code: f"/{code}/SUPERVISOR",
    "fullscreen": lambda code: f"/fullscreen/{code}",
    "dashboard":  lambda code: "/dashboard",
}


@once
def _ensure(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_panel_map (
                     panel_ip   TEXT PRIMARY KEY,
                     line_id    INTEGER,
                     view       TEXT NOT NULL DEFAULT 'management',
                     updated_by TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # 2026-09-13 — optional auto-login user for a kiosk panel: the wall opens
    # with NO manual login by minting a token for this user, keyed by panel IP.
    # Only the username is stored (never a password).
    cur.execute("ALTER TABLE mes_panel_map ADD COLUMN IF NOT EXISTS auto_user TEXT")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") if request else None
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request and request.client else "") or ""


@router.get("/whoami")
def whoami(request: Request, user=Depends(get_current_user)):
    """The IP MES-API sees for this device — shown in the admin mapping UI."""
    return {"ip": _client_ip(request)}


@router.get("/auto-open")
def auto_open(request: Request, user=Depends(get_current_user)):
    """The route this panel (by IP) should jump to, or null if unmapped."""
    ip = _client_ip(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT m.line_id, m.view, l.line_code, l.line_name
                         FROM mes_panel_map m LEFT JOIN mes_lines l ON l.id = m.line_id
                        WHERE m.panel_ip = %s""", (ip,))
        r = cur.fetchone()
        conn.commit()
    if not r:
        return {"ip": ip, "path": None}
    code = r["line_code"] or r["line_id"]
    view = r["view"] if r["view"] in _VIEWS else "management"
    path = _VIEWS[view](code) if (view == "dashboard" or code) else None
    return {"ip": ip, "path": path, "line_id": r["line_id"],
            "line_name": r["line_name"], "view": view}


@router.get("/auto-login")
def auto_login(request: Request):
    """No-auth: for a panel mapped with an auto-login user, mint a token for that
    user (keyed by the panel's trusted IP) so the wall opens with NO manual
    login. The password is never stored or sent — the admin just picks the
    display user in the mapping and the server issues the token by panel IP."""
    ip = _client_ip(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT m.line_id, m.view, m.auto_user, l.line_code
                         FROM mes_panel_map m LEFT JOIN mes_lines l ON l.id = m.line_id
                        WHERE m.panel_ip = %s""", (ip,))
        r = cur.fetchone()
        conn.commit()
    if not r:
        return {"ip": ip, "token": None, "path": None}
    code = r["line_code"] or r["line_id"]
    view = r["view"] if r["view"] in _VIEWS else "management"
    path = _VIEWS[view](code) if (view == "dashboard" or code) else None
    au = (r.get("auto_user") or "").strip()
    if not au:
        return {"ip": ip, "token": None, "path": path}
    u = get_user_from_db(au)
    if not u:
        return {"ip": ip, "token": None, "path": path}
    token = create_token(u["username"], u.get("role") or "operator", u["id"])
    return {"ip": ip, "token": token, "path": path,
            "username": u["username"], "role": u.get("role")}


@router.get("/maps")
def list_maps(user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "plant_head"):
        raise HTTPException(403, "Admin only")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT m.panel_ip, m.line_id, m.view, m.auto_user, m.updated_by, m.updated_at,
                              l.line_name, l.line_code
                         FROM mes_panel_map m LEFT JOIN mes_lines l ON l.id = m.line_id
                        ORDER BY m.panel_ip""")
        return [{
            "panel_ip": r["panel_ip"], "line_id": r["line_id"], "view": r["view"],
            "auto_user": r.get("auto_user"),
            "line_name": r["line_name"], "line_code": r["line_code"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in cur.fetchall()]


class MapBody(BaseModel):
    panel_ip: str
    line_id: int
    view: str = "management"
    auto_user: Optional[str] = None


@router.post("/maps")
def set_map(body: MapBody, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "plant_head"):
        raise HTTPException(403, "Admin only")
    ip = (body.panel_ip or "").strip()
    if not ip:
        raise HTTPException(400, "panel_ip required")
    view = body.view if body.view in _VIEWS else "management"
    au = (body.auto_user or "").strip() or None
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute("""INSERT INTO mes_panel_map (panel_ip, line_id, view, auto_user, updated_by, updated_at)
                       VALUES (%s,%s,%s,%s,%s,now())
                       ON CONFLICT (panel_ip) DO UPDATE SET
                         line_id=EXCLUDED.line_id, view=EXCLUDED.view,
                         auto_user=EXCLUDED.auto_user,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (ip, body.line_id, view, au, user.get("username")))
        conn.commit()
    return {"ok": True}


@router.delete("/maps")
def del_map(panel_ip: str, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "plant_head"):
        raise HTTPException(403, "Admin only")
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute("DELETE FROM mes_panel_map WHERE panel_ip=%s", ((panel_ip or "").strip(),))
        conn.commit()
    return {"ok": True}
