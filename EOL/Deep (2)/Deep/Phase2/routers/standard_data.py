"""
routers/standard_data.py — Admin Panel → Production → Standard Data.  2026-11-05.

Per-line standard values for the Control Board sheets.  First set: Track
Record — the Track Record Sheet's "Cycle Time / Effective Cycle Time (sec.)"
and "Target" boxes (operator: every line with cycle time, target, Edit and
Save).  This module only stores the values; nothing reads them yet.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user, require_admin
from database import get_conn, dict_cursor
from ddl_once import once

router = APIRouter(prefix="/api/standard-data", tags=["standard-data"])


@once
def _ensure_table(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_std_track_record (
                     line_id     INTEGER PRIMARY KEY,
                     cycle_time  NUMERIC(10,2),
                     target      NUMERIC(10,2),
                     updated_by  TEXT,
                     updated_at  TIMESTAMPTZ NOT NULL DEFAULT now())""")


class TrackRecordIn(BaseModel):
    cycle_time: Optional[float] = None
    target: Optional[float] = None


def _row(r: dict) -> dict:
    num = lambda v: float(v) if isinstance(v, Decimal) else v
    return {"line_id": r["line_id"], "line_name": r.get("line_name"), "line_code": r.get("line_code"),
            "zone_name": r.get("zone_name"), "cycle_time": num(r.get("cycle_time")),
            "target": num(r.get("target")), "updated_by": r.get("updated_by"),
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None}


@router.get("/track-record")
def list_track_record(user=Depends(get_current_user)):
    """Every active line with its saved Track Record values (null when not set)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_table(cur)
        conn.commit()
        cur.execute("""SELECT l.id AS line_id, l.line_name, l.line_code, z.zone_name,
                              t.cycle_time, t.target, t.updated_by, t.updated_at
                         FROM mes_lines l
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                         LEFT JOIN mes_std_track_record t ON t.line_id = l.id
                        WHERE COALESCE(l.is_active, TRUE)
                        ORDER BY z.zone_name NULLS LAST, l.line_name""")
        return [_row(r) for r in cur.fetchall()]


@router.put("/track-record/{line_id}")
def save_track_record(line_id: int, body: TrackRecordIn, user=Depends(require_admin)):
    for name, v in (("Cycle time", body.cycle_time), ("Target", body.target)):
        if v is not None and not (0 <= v < 100000):
            raise HTTPException(400, f"{name} must be between 0 and 99999 sec.")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_table(cur)
        cur.execute("SELECT 1 FROM mes_lines WHERE id = %s", (line_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Line not found")
        cur.execute("""INSERT INTO mes_std_track_record (line_id, cycle_time, target, updated_by, updated_at)
                       VALUES (%s, %s, %s, %s, now())
                       ON CONFLICT (line_id) DO UPDATE SET cycle_time = EXCLUDED.cycle_time,
                              target = EXCLUDED.target, updated_by = EXCLUDED.updated_by,
                              updated_at = now()
                       RETURNING line_id, cycle_time, target, updated_by, updated_at""",
                    (line_id, body.cycle_time, body.target, (user or {}).get("username")))
        row = cur.fetchone()
        conn.commit()
    return {"ok": True, **_row(row)}
