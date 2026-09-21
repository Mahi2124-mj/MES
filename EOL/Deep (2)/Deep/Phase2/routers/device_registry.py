# ───────────────────────────────────────────────────────────────────────
# device_registry.py   (/api/devices)   2026-09-02
# ───────────────────────────────────────────────────────────────────────
# Tracks which physical devices (phones / tablets / browsers) are running
# the MES, whether via the Android APK (TWA), an installed PWA, or a plain
# browser — plus the app version each is on and when it was last seen.
#
# The web app calls POST /api/devices/checkin on login and every ~10 min
# while open.  It sends a stable per-device id (localStorage), the detected
# source (apk/pwa/browser), the app version, and the user-agent; the server
# parses model/OS from the UA and upserts one row per device.
#
# Purely ADDITIVE telemetry — its own table, no counting/OEE/collector
# column is ever touched.  Listing is admin/plant_head only.
# ───────────────────────────────────────────────────────────────────────
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/devices", tags=["devices"])

_HEAD_ROLES = ("admin", "plant_head")   # who may view the registry


# ── schema ──────────────────────────────────────────────────────────────
@once
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_device_registry (
                device_id     TEXT PRIMARY KEY,
                first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
                checkin_count INTEGER     NOT NULL DEFAULT 1,
                app_source    TEXT,          -- 'apk' | 'pwa' | 'browser'
                app_version   TEXT,          -- APK versionName if known, else web build
                web_version   TEXT,          -- frontend build tag
                user_agent    TEXT,
                model         TEXT,          -- parsed from UA
                os            TEXT,          -- e.g. 'Android 14'
                screen        TEXT,          -- 'WxH'
                last_user     TEXT,
                last_ip       TEXT
            )""")


# ── user-agent parsing (best-effort) ─────────────────────────────────────
def _parse_ua(ua: str):
    ua = ua or ""
    os_ = None
    model = None
    m = re.search(r"Android\s+([\d.]+)", ua)
    if m:
        os_ = f"Android {m.group(1)}"
        # model sits after the Android version: "...; Android 14; SM-A155F Build/..."
        m2 = re.search(r"Android[^;]*;\s*([^;)]+?)(?:\s+Build/|;|\))", ua)
        if m2:
            model = m2.group(1).strip()
    elif "iPhone" in ua:
        os_, model = "iOS", "iPhone"
    elif "iPad" in ua:
        os_, model = "iPadOS", "iPad"
    elif "Windows NT" in ua:
        os_ = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_ = "macOS"
    elif "Linux" in ua:
        os_ = "Linux"
    if model and model.lower() in ("wv", "k"):   # WebView noise tokens
        model = None
    return os_, model


class CheckinBody(BaseModel):
    device_id:   str
    app_source:  Optional[str] = None
    app_version: Optional[str] = None
    web_version: Optional[str] = None
    user_agent:  Optional[str] = None
    screen:      Optional[str] = None


@router.post("/checkin")
def checkin(body: CheckinBody, request: Request, user=Depends(get_current_user)):
    _ensure_tables()
    did = (body.device_id or "").strip()[:80]
    if not did:
        raise HTTPException(status_code=400, detail="device_id required")
    ua = (body.user_agent or request.headers.get("user-agent") or "")[:400]
    os_, model = _parse_ua(ua)
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else ""))[:60]
    src = (body.app_source or "browser")[:16]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_device_registry
                (device_id, app_source, app_version, web_version,
                 user_agent, model, os, screen, last_user, last_ip)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (device_id) DO UPDATE SET
                last_seen     = now(),
                checkin_count = mes_device_registry.checkin_count + 1,
                app_source    = EXCLUDED.app_source,
                app_version   = EXCLUDED.app_version,
                web_version   = EXCLUDED.web_version,
                user_agent    = EXCLUDED.user_agent,
                model         = EXCLUDED.model,
                os            = EXCLUDED.os,
                screen        = EXCLUDED.screen,
                last_user     = EXCLUDED.last_user,
                last_ip       = EXCLUDED.last_ip
        """, (did, src, body.app_version, body.web_version,
              ua, model, os_, (body.screen or "")[:24],
              user.get("username"), ip))
    return {"ok": True}


@router.get("/list")
def list_devices(user=Depends(get_current_user)):
    if user.get("role") not in _HEAD_ROLES:
        raise HTTPException(status_code=403, detail="admin only")
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT device_id, first_seen, last_seen, checkin_count,
                   app_source, app_version, web_version,
                   model, os, screen, last_user, last_ip, user_agent
            FROM mes_device_registry
            ORDER BY last_seen DESC
        """)
        rows = cur.fetchall()
    # counts by source for a quick header summary
    summary = {}
    for r in rows:
        summary[r["app_source"] or "browser"] = summary.get(r["app_source"] or "browser", 0) + 1
    return {"devices": rows, "total": len(rows), "by_source": summary}
