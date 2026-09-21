"""
routers/maintenance_logbook.py
==============================
Maintenance Daily Log Book (TBDI / MAINT. / F / 008) — DB-backed.

One sheet per (record_date, shift): the header fields + the FULL table of
rows (stored as JSONB).  The LogBook page loads the sheet for the chosen
date+shift and upserts it on Save, so the log is shared across every
station and kept permanently in the DB (no localStorage).

Endpoints:
  GET  /api/maintenance-logbook/sheet?date=YYYY-MM-DD&shift=A   → load
  POST /api/maintenance-logbook/sheet                           → upsert
"""
from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/maintenance-logbook", tags=["maintenance-logbook"])


@once
def _ensure_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_maintenance_logbook (
                id                  SERIAL PRIMARY KEY,
                record_date         DATE        NOT NULL,
                shift               VARCHAR(8)  NOT NULL,
                employees_present   TEXT,
                employees_on_leave  TEXT,
                total_working_hours TEXT,
                total_over_time     TEXT,
                total_down_time     TEXT,
                rows                JSONB       NOT NULL DEFAULT '[]'::jsonb,
                updated_by          VARCHAR(120),
                updated_at          TIMESTAMP   DEFAULT NOW(),
                UNIQUE (record_date, shift)
            )
        """)
        conn.commit()


def _author(user) -> str:
    return (getattr(user, "username", None) or getattr(user, "name", None)
            or getattr(user, "email", None) or "operator")


class LogBookIn(BaseModel):
    date:  str
    shift: str
    hdr:   dict = {}
    rows:  List[dict] = []


def _empty(date: str, shift: str) -> dict:
    return {
        "date": date, "shift": shift,
        "hdr": {"present": "", "onLeave": "", "workHours": "",
                "overTime": "", "downTime": ""},
        "rows": [], "updated_by": None, "updated_at": None,
    }


@router.get("/sheet")
def get_sheet(
    date:  str = Query(..., description="record_date YYYY-MM-DD"),
    shift: str = Query(..., description="shift label"),
    user=Depends(get_current_user),
):
    _ensure_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT employees_present, employees_on_leave, total_working_hours, "
            "       total_over_time, total_down_time, rows, updated_by, updated_at "
            "FROM mes_maintenance_logbook WHERE record_date=%s AND shift=%s",
            (date, shift),
        )
        r = cur.fetchone()
    if not r:
        return _empty(date, shift)
    _rows = r["rows"]
    if not isinstance(_rows, list):
        try:
            _rows = json.loads(_rows) if _rows else []
        except Exception:
            _rows = []
    return {
        "date": date, "shift": shift,
        "hdr": {
            "present":   r["employees_present"]   or "",
            "onLeave":   r["employees_on_leave"]  or "",
            "workHours": r["total_working_hours"] or "",
            "overTime":  r["total_over_time"]     or "",
            "downTime":  r["total_down_time"]     or "",
        },
        "rows": _rows,
        "updated_by": r["updated_by"],
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


@router.post("/sheet")
def save_sheet(body: LogBookIn, user=Depends(get_current_user)):
    if not (body.date or "").strip() or not (body.shift or "").strip():
        raise HTTPException(400, "date and shift are required")
    h = body.hdr or {}
    _ensure_table()
    author = _author(user)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO mes_maintenance_logbook
                (record_date, shift, employees_present, employees_on_leave,
                 total_working_hours, total_over_time, total_down_time,
                 rows, updated_by, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,NOW())
            ON CONFLICT (record_date, shift) DO UPDATE SET
                employees_present   = EXCLUDED.employees_present,
                employees_on_leave  = EXCLUDED.employees_on_leave,
                total_working_hours = EXCLUDED.total_working_hours,
                total_over_time     = EXCLUDED.total_over_time,
                total_down_time     = EXCLUDED.total_down_time,
                rows                = EXCLUDED.rows,
                updated_by          = EXCLUDED.updated_by,
                updated_at          = NOW()
            """,
            (body.date.strip(), body.shift.strip(),
             str(h.get("present", "")   or ""), str(h.get("onLeave", "")  or ""),
             str(h.get("workHours", "") or ""), str(h.get("overTime", "") or ""),
             str(h.get("downTime", "")  or ""),
             json.dumps(body.rows or []), author),
        )
        conn.commit()
    return {"ok": True, "updated_by": author}
