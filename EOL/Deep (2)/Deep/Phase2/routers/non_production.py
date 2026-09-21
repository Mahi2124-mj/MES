"""
routers/non_production.py
=========================
Non-Production Day management — supports day-level, shift-level,
and hourly-slot-level NPD marking.

Admin and Zone roles can mark/unmark any line as non-production.
When a line's shift is marked NPD, its target is treated as 0.

GET  /api/npd/                 → list NPD entries (optional ?line_id=X&date=YYYY-MM-DD)
GET  /api/npd/today            → all lines that are NPD today
GET  /api/npd/check            → ?line_id=X&date=YYYY-MM-DD → entries for that day
POST /api/npd/                 → mark a line/shift as NPD for a date
DELETE /api/npd/{id}           → remove an NPD entry
"""

from datetime import date as dt_date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/npd", tags=["non-production-days"])


# ── Auth: admin or zone role ───────────────────────────────────

def require_admin_or_zone(user: dict = Depends(get_current_user)):
    if user["role"] not in ("admin", "zone"):
        raise HTTPException(403, "Admin or Zone role required")
    return user


# ── Schemas ────────────────────────────────────────────────────

class NPDCreate(BaseModel):
    line_id:      int
    date:         str                        # "YYYY-MM-DD"
    shift_name:   Optional[str]  = None      # None = whole-day NPD
    hourly_slots: Optional[List[str]] = None # None/empty = full shift NPD
    reason:       Optional[str]  = None


# ── Routes ─────────────────────────────────────────────────────

@router.get("/today")
def get_today_npd(user=Depends(get_current_user)):
    """Return all NPD entries for today (day-level and shift-level)."""
    today = dt_date.today().isoformat()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT n.id, n.line_id, n.date, n.shift_name, n.hourly_slots,
                   n.reason, n.created_by, n.created_at,
                   l.line_code, l.line_name
            FROM mes_non_production_days n
            JOIN mes_lines l ON l.id = n.line_id
            WHERE n.date = %s
            ORDER BY l.line_code, n.shift_name NULLS FIRST
        """, (today,))
        return cur.fetchall()


@router.get("/check")
def check_npd(
    line_id: int  = Query(...),
    date:    str  = Query(..., description="YYYY-MM-DD"),
    user=Depends(get_current_user)
):
    """Return all NPD entries for a specific line + date."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT * FROM mes_non_production_days
            WHERE line_id = %s AND date = %s
            ORDER BY shift_name NULLS FIRST
        """, (line_id, date))
        entries = cur.fetchall()
        has_day_npd = any(e["shift_name"] is None for e in entries)
        return {
            "is_npd": len(entries) > 0,
            "has_day_npd": has_day_npd,
            "entries": entries,
            # backward-compat: first entry (or day-level if present)
            "entry": next((e for e in entries if e["shift_name"] is None),
                          entries[0] if entries else None),
        }


@router.get("/")
def list_npd(
    line_id: Optional[int] = Query(None),
    date:    Optional[str] = Query(None),
    user=Depends(get_current_user)
):
    """List NPD entries, optionally filtered by line or date."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        clauses, params = [], []
        if line_id:
            clauses.append("n.line_id = %s"); params.append(line_id)
        if date:
            clauses.append("n.date = %s"); params.append(date)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(f"""
            SELECT n.id, n.line_id, n.date, n.shift_name, n.hourly_slots,
                   n.reason, n.created_by, n.created_at,
                   l.line_code, l.line_name
            FROM mes_non_production_days n
            JOIN mes_lines l ON l.id = n.line_id
            {where}
            ORDER BY n.date DESC, l.line_code, n.shift_name NULLS FIRST
        """, params)
        return cur.fetchall()


@router.post("/", status_code=201)
def mark_npd(body: NPDCreate, user=Depends(require_admin_or_zone)):
    """Mark a line/shift as non-production for a date. Admin or Zone only."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Verify line exists
        cur.execute("SELECT id, line_name FROM mes_lines WHERE id = %s", (body.line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")

        # Normalise hourly_slots — empty list treated as None (full shift/day NPD)
        slots = body.hourly_slots if body.hourly_slots else None

        # Check if an entry already exists for this (line, date, shift_name)
        cur.execute("""
            SELECT id FROM mes_non_production_days
            WHERE line_id = %s AND date = %s
            AND shift_name IS NOT DISTINCT FROM %s
        """, (body.line_id, body.date, body.shift_name))
        existing = cur.fetchone()

        try:
            if existing:
                conn.cursor().execute("""
                    UPDATE mes_non_production_days
                    SET reason=%s, hourly_slots=%s, created_by=%s, created_at=NOW()
                    WHERE id=%s
                """, (body.reason, slots, user["username"], existing["id"]))
            else:
                conn.cursor().execute("""
                    INSERT INTO mes_non_production_days
                        (line_id, date, shift_name, hourly_slots, reason, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (body.line_id, body.date, body.shift_name,
                      slots, body.reason, user["username"]))
        except Exception as e:
            raise HTTPException(400, str(e))

        shift_info = f" Shift={body.shift_name}" if body.shift_name else " (whole day)"
        slots_info = f" slots={slots}" if slots else ""
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('NPD_MARKED', 'line', %s, %s)
        """, (body.line_id,
              f"date={body.date}{shift_info}{slots_info} by={user['username']} reason={body.reason}"))

    return {"ok": True, "message": f"Marked as non-production for {body.date}{shift_info}"}


@router.delete("/{npd_id}")
def unmark_npd(npd_id: int, user=Depends(require_admin_or_zone)):
    """Remove a non-production day entry. Admin or Zone only."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_non_production_days WHERE id = %s", (npd_id,))
        entry = cur.fetchone()
        if not entry:
            raise HTTPException(404, "NPD entry not found")

        conn.cursor().execute(
            "DELETE FROM mes_non_production_days WHERE id = %s", (npd_id,)
        )
        shift_info = f" Shift={entry['shift_name']}" if entry.get("shift_name") else " (whole day)"
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('NPD_REMOVED', 'line', %s, %s)
        """, (entry["line_id"],
              f"date={entry['date']}{shift_info} by={user['username']}"))

    return {"ok": True, "message": "Non-production day entry removed"}
