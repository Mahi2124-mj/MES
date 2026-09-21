"""
routers/prod_breakdown_slips.py  (MES / :8080 side)
===================================================
PRODUCTION half of the Maintenance_DX (:8892) breakdown slip, brought into the
MES so the PRODUCTION operator fills it here on :5656.

Flow (owned by Maintenance_DX, we only drive the PRODUCTION step):
  ANDON Maintenance/Toolroom call open > threshold
    → an AUTO half-slip is created in maintenance_db (prod_stage='PENDING_PRODUCTION')
      pre-filled with zone/line/machine/MODEL/times/downtime.
    → PRODUCTION fills its half + submits (HERE) → prod_stage='PENDING_MAINTENANCE'
      → then maintenance completes it on :8892 → 'COMPLETED'.

This router talks DIRECTLY to `maintenance_db` (the same DB the :8892 backend
owns) and REPLICATES the exact production-phase logic of
`Maintenance_DX/Phase2/routers/breakdown_slips.py::fill_auto_slip` — same flat
column mapping, same forward-only stage guard (race-safe), same `sync_status`
upsert into `breakdown_status` — so the 9965 maintenance dashboard sees the slip
exactly as if it had been submitted there.  SELECT + a single guarded UPDATE;
never touches the MES energydb or the MES counting/OEE.
"""

import os
import re
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_user, FLOOR_SCOPED_ROLES
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/prod-breakdown-slips", tags=["prod-breakdown-slips"])

# ── maintenance_db (same host/creds as energydb, different db) ────────────
_MAINT_DSN = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432") or 5432),
    "dbname":          os.getenv("MAINT_DB_NAME", "maintenance_db"),
    "user":            os.getenv("DB_USER", "postgres"),
    "password":        os.getenv("DB_PASS", "tbdi@123"),
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5") or 5),
}


@contextmanager
def _maint_conn(write: bool = False):
    """maintenance_db connection.  write=True commits on clean exit / rolls back
    on error; read path never writes.  503 if the DB is unreachable."""
    try:
        conn = psycopg2.connect(**_MAINT_DSN)
    except Exception as exc:
        raise HTTPException(503, f"maintenance_db unavailable: {exc}")
    try:
        yield conn
        if write:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── constants — MUST match Maintenance_DX/routers/breakdown_slips.py ───────
AUTO_SLIP_TABLE     = "maintenance_auto_breakdown_slip"
TOOLROOM_SLIP_TABLE = "toolroom_auto_breakdown_slip"
STATUS_TABLE        = "breakdown_status"
SRC_TABLES = {"maintenance": AUTO_SLIP_TABLE, "toolroom": TOOLROOM_SLIP_TABLE}


def _src_table(src: str) -> str:
    t = SRC_TABLES.get((src or "maintenance").strip().lower())
    if not t:
        raise HTTPException(400, "src must be 'maintenance' or 'toolroom'")
    return t


def _blank_to_none(v):
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _to_int(v):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── breakdown_status upsert (copied verbatim from :8892) ──────────────────
_STATUS_COLS = ("slip_id, bd_for, andon_event_id, bd_start_date, bd_start_time, zone, line, "
                "machine_no, shift, total_downtime_min, state, prod, maint, toolroom, "
                "production_at, submitted_at")

_STATUS_SELECT = """
    SELECT s.id, %(bd_for)s, s.andon_event_id,
           s.bd_start_date, s.bd_start_time, s.zone, s.line, s.machine_no, s.shift,
           s.mc_down_time_minutes,
           CASE WHEN s.bd_ok_time IS NOT NULL THEN 'RESOLVED' ELSE 'OPEN' END,
           CASE WHEN COALESCE(s.prod_stage,'PENDING_MAINTENANCE') = 'PENDING_PRODUCTION'
                THEN 'PENDING' ELSE 'SUBMITTED' END,
           CASE WHEN %(bd_for)s <> 'maintenance' THEN '-'
                WHEN COALESCE(s.prod_stage,'PENDING_MAINTENANCE') = 'COMPLETED'
                THEN 'SUBMITTED' ELSE 'PENDING' END,
           CASE WHEN %(bd_for)s <> 'toolroom' THEN '-'
                WHEN COALESCE(s.prod_stage,'PENDING_MAINTENANCE') = 'COMPLETED'
                THEN 'SUBMITTED' ELSE 'PENDING' END,
           s.production_at, s.submitted_at
      FROM {tbl} s
"""

_STATUS_UPSERT = """
    INSERT INTO {status} ({cols})
    {select} WHERE s.id = %(sid)s
    ON CONFLICT (bd_for, slip_id) DO UPDATE SET
        andon_event_id=EXCLUDED.andon_event_id, bd_start_date=EXCLUDED.bd_start_date,
        bd_start_time=EXCLUDED.bd_start_time, zone=EXCLUDED.zone, line=EXCLUDED.line,
        machine_no=EXCLUDED.machine_no, shift=EXCLUDED.shift,
        total_downtime_min=EXCLUDED.total_downtime_min, state=EXCLUDED.state,
        prod=EXCLUDED.prod, maint=EXCLUDED.maint, toolroom=EXCLUDED.toolroom,
        production_at=EXCLUDED.production_at, submitted_at=EXCLUDED.submitted_at,
        updated_at=NOW()
"""


def _sync_status(cur, tbl: str, slip_id: int):
    """Mirror the slip into breakdown_status (what the 9965 Status tab reads).
    Runs in a SAVEPOINT so a status hiccup never rolls back the slip update."""
    bd_for = "toolroom" if tbl == TOOLROOM_SLIP_TABLE else "maintenance"
    try:
        cur.execute("SAVEPOINT sp_status")
        cur.execute(_STATUS_UPSERT.format(status=STATUS_TABLE, cols=_STATUS_COLS,
                                          select=_STATUS_SELECT.format(tbl=tbl)),
                    {"bd_for": bd_for, "sid": slip_id})
        cur.execute("RELEASE SAVEPOINT sp_status")
    except Exception as ex:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT sp_status")
        except Exception:
            pass
        print(f"[PROD-SLIP] status row for slip #{slip_id} ({bd_for}) not synced: {ex}")


# ── production-half → flat columns (only the PROD_FIELDS) ──────────────────
# Mirrors breakdown_slips._halves_to_flat's production side.
def _prod_to_flat(prod: dict) -> dict:
    prod = prod or {}
    return {
        "machine_no":            prod.get("machine_no"),
        "machine_name":          prod.get("machine_name"),
        "line_leader_name":      prod.get("line_leader_name"),
        "machine_operator_name": prod.get("machine_operator_name"),
        "category":              prod.get("category"),
        "model_no":              prod.get("model_no"),
        "bd_received_time":      prod.get("bd_received_time"),
        "response_time_minutes": _to_int(prod.get("response_time_minutes")),
        "frequency":             _to_int(prod.get("frequency")) or 1,
        "problem_reported_by_production": prod.get("problem_reported_by_production"),
    }


# ── ticket shape (production_data subset of :8892 _slip_to_ticket) ─────────
def _row_to_ticket(r: dict) -> dict:
    return {
        "id": r["id"],
        "src": r.get("_src"),
        "zone": r.get("zone"), "line": r.get("line"),
        "machine_no": r.get("machine_no"), "machine_name": r.get("machine_name"),
        "slip_date": str(r["slip_date"]) if r.get("slip_date") else None,
        "shift": r.get("shift"),
        "model_no": r.get("model_no"),
        "bd_start_date": str(r["bd_start_date"]) if r.get("bd_start_date") else None,
        "bd_start_time": r.get("bd_start_time"),
        "bd_received_time": r.get("bd_received_time"),
        "bd_ok_time": r.get("bd_ok_time"),
        "bd_end_date": str(r["bd_end_date"]) if r.get("bd_end_date") else None,
        "mc_down_time_minutes": r.get("mc_down_time_minutes"),
        "response_time_minutes": r.get("response_time_minutes"),
        # production-editable (may already be partly filled)
        "line_leader_name": r.get("line_leader_name"),
        "machine_operator_name": r.get("machine_operator_name"),
        "category": r.get("category"),
        "frequency": r.get("frequency"),
        "problem_reported_by_production": r.get("problem_reported_by_production"),
        "andon_event_id": r.get("andon_event_id"),
    }


# ── per-line scope (reuse the andon scoping so production sees own lines) ──
def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _user_scope(user):
    """('all', None) | ('none', set()) | ('some', {normalised line names})."""
    if user.get("role") == "admin":
        return ("all", None)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT l.line_name FROM mes_operator_lines ol
                         JOIN mes_lines l ON l.id = ol.line_id
                        WHERE ol.admin_id = %s""", (user["id"],))
        names = {_norm(r["line_name"]) for r in cur.fetchall() if r.get("line_name")}
    if names:
        return ("some", names)
    if user.get("role") in FLOOR_SCOPED_ROLES:
        return ("none", set())
    return ("all", None)


# ── endpoints ─────────────────────────────────────────────────────────────
@router.get("/pending")
def pending(user=Depends(get_current_user)):
    """PENDING_PRODUCTION slips (both maintenance + toolroom), newest first,
    scoped to the user's assigned lines."""
    mode, names = _user_scope(user)
    if mode == "none":
        return []
    out = []
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for src, tbl in SRC_TABLES.items():
            cur.execute(f"""
                SELECT id, zone, line, machine_no, machine_name, shift, slip_date,
                       model_no, bd_start_date, bd_start_time, bd_received_time,
                       bd_ok_time, bd_end_date, mc_down_time_minutes, response_time_minutes,
                       category, frequency, line_leader_name, machine_operator_name,
                       problem_reported_by_production, andon_event_id
                  FROM {tbl}
                 WHERE COALESCE(prod_stage,'PENDING_MAINTENANCE') = 'PENDING_PRODUCTION'
            """)
            for r in cur.fetchall():
                d = dict(r)
                d["_src"] = src
                if mode == "some" and _norm(d.get("line")) not in names:
                    continue
                out.append(_row_to_ticket(d))
    out.sort(key=lambda d: (str(d.get("bd_start_date") or ""),
                            str(d.get("bd_start_time") or ""), d.get("id") or 0),
             reverse=True)
    return out


@router.get("/machines")
def machines(zone: str = Query(...), line: str = Query(...),
             user=Depends(get_current_user)):
    """Machines for a (zone, line) from the machine master (maintenance_machines)
    — feeds the slip's MACHINE NO. dropdown, same source the 9965 form uses.
    Matched on NORMALISED zone/line names."""
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT machine_no, machine_name
              FROM maintenance_machines
             WHERE COALESCE(is_active, TRUE)
               AND UPPER(REGEXP_REPLACE(COALESCE(zone_name,''),'[^A-Za-z0-9]','','g')) = %s
               AND UPPER(REGEXP_REPLACE(COALESCE(line_name,''),'[^A-Za-z0-9]','','g')) = %s
             ORDER BY serial_no NULLS LAST, machine_no
        """, (_norm(zone), _norm(line)))
        return [dict(r) for r in cur.fetchall()]


@router.get("/{sid}")
def get_one(sid: int, src: str = Query("maintenance"), user=Depends(get_current_user)):
    tbl = _src_table(src)
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {tbl} WHERE id = %s", (sid,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "slip not found")
    d = dict(r)
    d["_src"] = "toolroom" if tbl == TOOLROOM_SLIP_TABLE else "maintenance"
    return _row_to_ticket(d)


class ProdSubmit(BaseModel):
    production_data: dict
    src: str = "maintenance"


@router.post("/{sid}/submit")
def submit(sid: int, body: ProdSubmit, user=Depends(get_current_user)):
    """Production submits its half → prod_stage PENDING_PRODUCTION → PENDING_MAINTENANCE.
    Replicates the :8892 production-phase fill EXACTLY (forward-only, race-safe)."""
    tbl = _src_table(body.src)
    flat = _prod_to_flat(body.production_data)
    sent = set((body.production_data or {}).keys())

    sets, vals = [], []
    for col, value in flat.items():
        # only write a column the form actually sent (leave ANDON's values intact)
        form_key = "date" if col == "slip_date" else col
        if form_key not in sent and col not in sent:
            # 'response_time_minutes'/'frequency' may be derived; include if sent
            continue
        sets.append(f"{col} = %s")
        vals.append(_blank_to_none(value))

    _uid = user.get("id") if isinstance(user, dict) else None
    with _maint_conn(write=True) as conn:
        cur = conn.cursor()
        # forward-only stage guard: must still be PENDING_PRODUCTION
        cur.execute(f"SELECT COALESCE(prod_stage,'PENDING_MAINTENANCE') FROM {tbl} WHERE id=%s",
                    (sid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "slip not found")
        cur_stage = row[0]
        if cur_stage == "PENDING_MAINTENANCE":
            raise HTTPException(409, "Ye slip pehle hi submit ho chuki hai (refresh karein).")
        if cur_stage != "PENDING_PRODUCTION":
            raise HTTPException(409,
                f"Abhi ye slip '{cur_stage}' par hai — production step yahin se nahi ho sakta.")

        sets.append("prod_stage = %s");            vals.append("PENDING_MAINTENANCE")
        sets.append("production_by_user_id = %s"); vals.append(_uid)
        sets.append("production_at = NOW()")
        sets.append("submitted_by_user_id = %s");  vals.append(_uid)
        sets.append("submitted_at = NOW()")
        vals.append(sid)

        cur.execute(
            f"""UPDATE {tbl} SET {', '.join(sets)}
                 WHERE id = %s
                   AND COALESCE(prod_stage,'PENDING_MAINTENANCE') = 'PENDING_PRODUCTION'""",
            vals)
        if cur.rowcount == 0:
            raise HTTPException(409, "Slip ka stage abhi-abhi badal gaya — refresh karein.")
        _sync_status(cur, tbl, sid)
    return {"ok": True, "id": sid, "stage": "PENDING_MAINTENANCE"}
