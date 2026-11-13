"""
routers/predictive.py — Maintenance → Predictive Maintenance.  2026-11-12.

Operator: "maine ek aur dashboard banaya tha, usko MES ke format ke hisaab se
merge karna hai; Maintenance me ek tab Predictive Maintenance ke naam se".

The Predictive Module (/run/media/.../predictive-module, systemd service
`predictive-module`, port 1006) keeps doing the work: it is the ONLY reader of
the PPI analog card (192.168.31.222, Press-18 bearing temperature + vibration;
the card accepts one Modbus connection), and it keeps the history, alarms,
baseline and forecast.  MES only shows it: this router passes the module's
read API through the MES login (also from mes.tbdi.in, where port 1006 is not
reachable), and "Set baseline" for users who may change it.  Nothing here
talks to the card, and the module's settings (channels, limits, converter)
stay on its own page.

GET  /api/predictive/state                 → module /api/state
GET  /api/predictive/history?ch=&range=    → module /api/history
GET  /api/predictive/events?limit=         → module /api/events
GET  /api/predictive/export.csv?ch=&range= → module /api/export.csv
POST /api/predictive/baseline {ch, action}  → module /api/baseline
     (admin / plant_head, the maintenance department, or "full" on the
      maintenance-predictive page)
"""
from __future__ import annotations

import os
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from auth import get_current_user
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/predictive", tags=["predictive"])

BASE = os.getenv("PREDICTIVE_URL", "http://127.0.0.1:1006").rstrip("/")
PAGE_KEY = "maintenance-predictive"
RANGES = {"15m", "1h", "8h", "24h", "7d", "30d"}
_session = requests.Session()


def _get(path: str, params: Optional[dict] = None, timeout: float = 4.0) -> requests.Response:
    try:
        r = _session.get(f"{BASE}{path}", params=params, timeout=timeout)
    except requests.RequestException:
        raise HTTPException(503, "Predictive Module is not reachable (service predictive-module, port 1006).")
    return r


def _json(r: requests.Response):
    try:
        data = r.json()
    except ValueError:
        raise HTTPException(502, "Predictive Module sent an unreadable reply.")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, (data or {}).get("error") or f"Predictive Module error {r.status_code}")
    return data


# 2026-11-12 (operator: "kisi ke data ko disturb mat kariyo") — the MES page is
# READ-ONLY: "Set baseline" is switched off here, baselines stay on the module's
# own page.  PREDICTIVE_ALLOW_BASELINE=1 turns the button back on.
ALLOW_BASELINE = os.getenv("PREDICTIVE_ALLOW_BASELINE", "0") == "1"


def can_write(user) -> bool:
    if not ALLOW_BASELINE:
        return False
    if (user or {}).get("role") in ("admin", "plant_head"):
        return True
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT perm_level FROM mes_user_page_permissions WHERE user_id = %s AND page_key = %s",
                        (user.get("id"), PAGE_KEY))
            r = cur.fetchone()
            if r:
                return r["perm_level"] == "full"
            if user.get("role") == "department" and user.get("department_id"):
                cur.execute("SELECT slug FROM mes_departments WHERE id = %s", (user["department_id"],))
                d = cur.fetchone()
                return bool(d and str(d["slug"] or "").lower() == "maintenance")
    except Exception:
        return False
    return False


@router.get("/state")
def state(user=Depends(get_current_user)):
    data = _json(_get("/api/state"))
    data["can_write"] = can_write(user)
    data["module_url"] = os.getenv("PREDICTIVE_PAGE_URL", "http://192.168.30.15:1006")
    return data


@router.get("/history")
def history(ch: int = Query(...), range: str = Query("1h"), user=Depends(get_current_user)):  # noqa: A002
    if range not in RANGES:
        raise HTTPException(400, "unknown range")
    return _json(_get("/api/history", {"ch": ch, "range": range}, timeout=8.0))


@router.get("/events")
def events(limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    return _json(_get("/api/events", {"limit": limit}))


@router.get("/export.csv")
def export_csv(ch: int = Query(...), range: str = Query("7d"), user=Depends(get_current_user)):  # noqa: A002
    if range not in RANGES:
        raise HTTPException(400, "unknown range")
    r = _get("/api/export.csv", {"ch": ch, "range": range}, timeout=15.0)
    if r.status_code >= 400:
        _json(r)
    return Response(content=r.content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": r.headers.get("Content-Disposition",
                                                                  f'attachment; filename="ch{ch}_{range}.csv"')})


class BaselineBody(BaseModel):
    ch: int
    action: str = "set"          # "set" (last `hours` h) or "clear" (back to automatic)
    hours: int = 24


@router.post("/baseline")
def baseline(body: BaselineBody, user=Depends(get_current_user)):
    if not ALLOW_BASELINE:
        raise HTTPException(403, "Baseline changes are switched off in MES; set the baseline on the Predictive Module page.")
    if not can_write(user):
        raise HTTPException(403, "Only the maintenance team or an admin can change the baseline.")
    if body.action not in ("set", "clear"):
        raise HTTPException(400, "action must be set or clear")
    try:
        r = _session.post(f"{BASE}/api/baseline", timeout=8.0,
                          json={"ch": body.ch, "action": body.action, "hours": max(1, min(168, body.hours))})
    except requests.RequestException:
        raise HTTPException(503, "Predictive Module is not reachable.")
    out = _json(r)
    print(f"[PREDICTIVE] baseline {body.action} ch{body.ch} by {user.get('username')}", flush=True)
    return out
