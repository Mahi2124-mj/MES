"""
Quality → Red Bin Lock  (2026-10-07)

A bar code that the TBDI Red Bin portal has LOCKED must not pass Final
Inspection.  The portal owns the lock list (database `redbin`, table
"PartLock"); MES only READS it.  The collector checks every Final part at the
same moment as the Semi-Auto NG check and, on a locked code, pulses the line's
bit from `mes_redbin_bit_master` (initially L230, the same bit the Semi-Auto NG
trace uses) and logs the restriction in `mes_redbin_final_block`.

This router serves the Quality tab:
    GET  /locks       the portal's lock list (live from redbin, read-only)
    GET  /blocks      parts restricted at Final (which line / machine / when)
    GET  /bit-master  which bit each line's Final machine uses
    POST /bit-master  add / change a line's bit (admin, plant head, quality
                      incharge, or 'full' on the redbin-lock page)

Both MES tables were created once (2026-10-07); nothing here runs DDL.
"""
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor, DB_CONFIG
from auth import get_current_user

router = APIRouter(prefix="/api/quality/redbin", tags=["redbin-lock"])

REDBIN_DB = "redbin"
IST = timedelta(hours=5, minutes=30)
PAGE_KEY = "redbin-lock"
_WRITE_ROLES = ("admin", "plant_head", "quality_incharge")


def _redbin_conn():
    cfg = {k: v for k, v in DB_CONFIG.items()
           if k not in ("database", "dbname", "connect_timeout")}
    return psycopg2.connect(
        **cfg, dbname=REDBIN_DB, connect_timeout=3,
        options="-c default_transaction_read_only=on -c statement_timeout=5000")


def _ist(ts):
    """redbin stores UTC (timestamp without zone) -> IST string."""
    if not ts:
        return None
    return (ts + IST).strftime("%Y-%m-%d %H:%M:%S")


def _can_write(user) -> bool:
    if (user or {}).get("role") in _WRITE_ROLES:
        return True
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT perm_level FROM mes_user_page_permissions "
                        " WHERE user_id = %s AND page_key = %s",
                        (user.get("id"), PAGE_KEY))
            r = cur.fetchone()
            return bool(r and r[0] == "full")
    except Exception:
        return False


@router.get("/locks")
def locks(show: str = Query("locked"), user=Depends(get_current_user)):
    """The portal's lock list.  show=locked (default) or all (the portal's
    older table design keeps unlocked rows with status UNLOCKED)."""
    try:
        c = _redbin_conn()
    except Exception as e:
        return {"ok": False, "error": f"Red Bin database not reachable: {e}",
                "rows": [], "counts": {}}
    try:
        cur = c.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns "
                    " WHERE table_schema='public' AND table_name='PartLock'")
        cols = {r[0] for r in cur.fetchall()}
        if not cols:
            return {"ok": False, "error": 'Table "PartLock" not found in redbin',
                    "rows": [], "counts": {}}
        has_unl = {"unlockedAt", "unlockedByName"} <= cols
        sel = ('"partCode", status, "redBinNumber", "recordStatus", "zoneName", '
               '"lineName", "partName", "lockedAt", "lockedByName", "updatedAt"'
               + (', "unlockedAt", "unlockedByName"' if has_unl else ''))
        where = "" if show == "all" else "WHERE status = 'LOCKED'"
        cur.execute(f'SELECT {sel} FROM "PartLock" {where} '
                    f'ORDER BY "lockedAt" DESC LIMIT 5000')
        rows = []
        for r in cur.fetchall():
            rows.append({
                "part_code":      r[0], "status": r[1],
                "red_bin_number": r[2], "record_status": r[3],
                "zone": r[4], "line": r[5], "part_name": r[6],
                "locked_at": _ist(r[7]), "locked_by": r[8],
                "updated_at": _ist(r[9]),
                "unlocked_at": _ist(r[10]) if has_unl else None,
                "unlocked_by": r[11] if has_unl else None,
            })
        cur.execute('SELECT status, count(*) FROM "PartLock" GROUP BY 1')
        counts = {k: v for k, v in cur.fetchall()}
        return {"ok": True, "error": None, "rows": rows, "counts": counts,
                "fetched_at": (datetime.utcnow() + IST).strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"ok": False, "error": str(e), "rows": [], "counts": {}}
    finally:
        try: c.close()
        except Exception: pass


@router.get("/blocks")
def blocks(date_from: Optional[str] = None, date_to: Optional[str] = None,
           line_id: Optional[int] = None, part_code: Optional[str] = None,
           user=Depends(get_current_user)):
    """Parts the Final machine restricted because they were locked."""
    try:
        d_to = date.fromisoformat(date_to) if date_to else date.today()
        d_from = date.fromisoformat(date_from) if date_from else d_to - timedelta(days=30)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            """SELECT id, ts, record_date, shift_name, line_id, line_name,
                      machine_name, part_code, red_bin_number, record_status,
                      rb_zone, rb_line, locked_by, locked_at, bit_address,
                      bit_written, lookup_source, note
                 FROM mes_redbin_final_block
                WHERE ts >= %s AND ts < %s
                  AND (%s::int IS NULL OR line_id = %s::int)
                  AND (%s::text IS NULL OR part_code ILIKE '%%' || %s::text || '%%')
                ORDER BY ts DESC LIMIT 5000""",
            (d_from, d_to + timedelta(days=1), line_id, line_id,
             part_code or None, part_code or None))
        rows = []
        for r in cur.fetchall():
            r = dict(r)
            r["ts"] = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else None
            r["record_date"] = str(r["record_date"]) if r["record_date"] else None
            r["locked_at"] = _ist(r["locked_at"])
            rows.append(r)
        return {"rows": rows, "date_from": str(d_from), "date_to": str(d_to)}


@router.get("/bit-master")
def bit_master(user=Depends(get_current_user)):
    """Every line that has a Final (main) machine, with its Red Bin bit row."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            """SELECT l.id AS line_id, l.line_name, z.zone_name,
                      fi.id AS final_plc_id, fi.machine_name AS final_machine,
                      fi.plc_ip AS final_ip, fi.fi_sa_ng_bit AS sa_ng_bit,
                      bm.bit_address, bm.hold_sec, bm.enabled,
                      bm.updated_by, bm.updated_at
                 FROM mes_lines l
                 LEFT JOIN mes_zones z ON z.id = l.zone_id
                 LEFT JOIN LATERAL (
                      SELECT p.* FROM mes_plc_configs p
                       WHERE p.line_id = l.id AND p.parent_plc_id IS NULL
                       ORDER BY p.id LIMIT 1) fi ON TRUE
                 LEFT JOIN mes_redbin_bit_master bm ON bm.line_id = l.id
                WHERE fi.id IS NOT NULL
                ORDER BY (bm.line_id IS NULL), z.zone_name, l.line_name""")
        rows = []
        for r in cur.fetchall():
            r = dict(r)
            r["hold_sec"] = float(r["hold_sec"]) if r["hold_sec"] is not None else None
            r["updated_at"] = (r["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
                               if r["updated_at"] else None)
            rows.append(r)
        return {"rows": rows, "can_edit": _can_write(user)}


class BitMasterIn(BaseModel):
    line_id: int
    bit_address: str
    hold_sec: float = 2.0
    enabled: bool = True


@router.post("/bit-master")
def bit_master_save(body: BitMasterIn, user=Depends(get_current_user)):
    if not _can_write(user):
        raise HTTPException(403, "Not allowed to change the Red Bin bit master")
    bit = (body.bit_address or "").strip().upper()
    if not bit or len(bit) > 20 or not bit[0].isalpha() or not bit[1:].replace(".", "").isalnum():
        raise HTTPException(400, "Enter a PLC bit address, e.g. L230 or M500")
    if not (0.5 <= body.hold_sec <= 30):
        raise HTTPException(400, "Hold time must be 0.5 - 30 seconds")
    who = user.get("username") or user.get("name") or "unknown"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id FROM mes_plc_configs
                        WHERE line_id = %s AND parent_plc_id IS NULL
                        ORDER BY id LIMIT 1""", (body.line_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "This line has no Final (main) machine")
        cur.execute(
            """INSERT INTO mes_redbin_bit_master
                   (line_id, plc_config_id, bit_address, hold_sec, enabled,
                    updated_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (line_id) DO UPDATE SET
                   plc_config_id = EXCLUDED.plc_config_id,
                   bit_address   = EXCLUDED.bit_address,
                   hold_sec      = EXCLUDED.hold_sec,
                   enabled       = EXCLUDED.enabled,
                   updated_by    = EXCLUDED.updated_by,
                   updated_at    = now()""",
            (body.line_id, r[0], bit, body.hold_sec, body.enabled, who))
        conn.commit()
    return {"ok": True}
