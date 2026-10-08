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
from datetime import date as _date, datetime, timedelta
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
        # 2026-10-08 — production now names who attended (picked from the
        # maintenance attendance board); same flat column maintenance used.
        "bd_attended_by":        prod.get("bd_attended_by"),
    }


BD_ATTENDED_MAX = 160          # maintenance_db column is VARCHAR(160)


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
        "bd_attended_by": r.get("bd_attended_by"),
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


# ── 30-minute rule + supervisor reminder (2026-10-08) ─────────────────────
# Operator: "shift ke 30 min baad shift incharge hi bhar payega" and
# "supervisor dashboard pe pending slip ka number, click pe kaun si aur kab se".
# A slip belongs to the shift instance its breakdown started in; once that
# shift ended FILL_GRACE_MIN ago, only these roles may still submit it.
FILL_GRACE_MIN = 30
LATE_FILL_ROLES = {"shift_incharge", "admin"}
_ALIAS_TTL = 300.0
_alias_cache = {"at": 0.0, "map": {}}


def _line_aliases(cur) -> dict:
    """{normalised name: MES line_id} for every name a slip's line can carry:
    MES line_name / line_code / nf2_line_name and the machine-master line
    (YHB Recliner -> YHB_RC).  Cached for 5 min."""
    import time as _t
    if _t.time() - _alias_cache["at"] < _ALIAS_TTL and _alias_cache["map"]:
        return _alias_cache["map"]
    from routers.machines import _resolve_nf2_line
    cur.execute("SELECT id, line_name, line_code, nf2_line_name FROM mes_lines")
    rows = cur.fetchall()
    out = {}
    for r in rows:
        for nm in (r["line_name"], r["line_code"], r["nf2_line_name"]):
            if nm and _norm(nm) not in out:
                out[_norm(nm)] = r["id"]
    # The resolver may roll its connection back, so it gets its own — never
    # the caller's transaction.
    with get_conn() as rconn:
        for r in rows:
            try:
                _z, nf2 = _resolve_nf2_line(rconn, r["id"])
            except Exception:
                rconn.rollback()
                continue
            if nf2 and _norm(nf2) not in out:
                out[_norm(nf2)] = r["id"]
    # "Loop Pipe-Line 1" is LOOP_PIPE_1 on the maintenance side
    for k, v in list(out.items()):
        short = k.replace("LINE", "")
        if short and short != k and short not in out:
            out[short] = v
    _alias_cache.update(at=_t.time(), map=out)
    return out


def _slip_anchor(r: dict) -> Optional[datetime]:
    """When the breakdown started (B/D START), else the slip date."""
    d = r.get("bd_start_date") or r.get("slip_date")
    if not d:
        return None
    if isinstance(d, str):
        try:
            d = _date.fromisoformat(d[:10])
        except ValueError:
            return None
    t = r.get("bd_start_time")
    try:
        hh, mm = (int(x) for x in str(t or "").split(":")[:2]) if t else (0, 0)
    except ValueError:
        hh, mm = 0, 0
    return datetime(d.year, d.month, d.day, hh, mm)


def _fill_deadline(cur, r: dict, shift_cache: dict) -> Optional[datetime]:
    """End of the slip's shift + FILL_GRACE_MIN, or None when it can't be told."""
    line_id = _line_aliases(cur).get(_norm(r.get("line")))
    sh = (r.get("shift") or "").strip().upper()
    anchor = _slip_anchor(r)
    if not line_id or not sh or not anchor:
        return None
    key = (line_id, sh)
    if key not in shift_cache:
        cur.execute("""SELECT start_time, end_time, COALESCE(crosses_midnight, FALSE) AS xm
                         FROM mes_shift_configs
                        WHERE line_id = %s AND UPPER(shift_name) = %s LIMIT 1""", (line_id, sh))
        shift_cache[key] = cur.fetchone()
    c = shift_cache[key]
    if not c or not c["start_time"] or not c["end_time"]:
        return None
    st, en = c["start_time"], c["end_time"]
    crosses = bool(c["xm"]) or en <= st
    start = datetime.combine(anchor.date(), st)
    if crosses and anchor.time() < en:          # after midnight: began the day before
        start -= timedelta(days=1)
    end = datetime.combine(start.date(), en) + (timedelta(days=1) if crosses else timedelta())
    return end + timedelta(minutes=FILL_GRACE_MIN)


def _lock_fields(d: dict, deadline: Optional[datetime], user) -> dict:
    locked = bool(deadline and datetime.now() > deadline)
    role = (user or {}).get("role") if isinstance(user, dict) else None
    d["fill_deadline"] = deadline.isoformat(timespec="minutes") if deadline else None
    d["locked"] = locked
    d["can_fill"] = (not locked) or role in LATE_FILL_ROLES
    return d


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
                       problem_reported_by_production, bd_attended_by, andon_event_id
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
    if out:
        with get_conn() as conn:
            cur, cache = dict_cursor(conn), {}
            for d in out:
                _lock_fields(d, _fill_deadline(cur, d, cache), user)
    return out


@router.get("/line-pending")
def line_pending(line_id: int = Query(...), user=Depends(get_current_user)):
    """Pending (production-side) slips of ONE line — the SUPERVISOR wall's
    reminder badge and its list: which slip, which shift, pending since when,
    and whether it is past the 30-minute fill window."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        aliases = _line_aliases(cur)
        mine = {k for k, v in aliases.items() if v == line_id}
        if not mine:
            return {"line_id": line_id, "count": 0, "slips": [], "by_shift": []}
        slips = []
        with _maint_conn() as mconn:
            mcur = mconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            for src, tbl in SRC_TABLES.items():
                mcur.execute(f"""
                    SELECT id, zone, line, machine_no, machine_name, shift, slip_date,
                           bd_start_date, bd_start_time, bd_ok_time, mc_down_time_minutes,
                           created_at
                      FROM {tbl}
                     WHERE COALESCE(prod_stage,'PENDING_MAINTENANCE') = 'PENDING_PRODUCTION'
                """)
                for r in mcur.fetchall():
                    if _norm(r.get("line")) in mine:
                        d = dict(r); d["src"] = src
                        slips.append(d)
        cache, now = {}, datetime.now()
        out = []
        for d in slips:
            anchor = _slip_anchor(d)
            since = d.get("created_at") or anchor
            item = {
                "id": d["id"], "src": d["src"], "line": d.get("line"),
                "machine_no": d.get("machine_no"), "machine_name": d.get("machine_name"),
                "shift": d.get("shift"),
                "slip_date": str(d["slip_date"]) if d.get("slip_date") else None,
                "bd_start": anchor.isoformat(timespec="minutes") if anchor else None,
                "pending_since": since.isoformat(timespec="minutes") if since else None,
                "pending_min": int((now - since).total_seconds() // 60) if since else None,
                "down_min": d.get("mc_down_time_minutes"),
            }
            out.append(_lock_fields(item, _fill_deadline(cur, d, cache), user))
    out.sort(key=lambda x: x["bd_start"] or "", reverse=True)
    groups = {}
    for x in out:
        k = (x["slip_date"] or (x["bd_start"] or "")[:10], x["shift"] or "—")
        groups[k] = groups.get(k, 0) + 1
    by_shift = [{"date": k[0], "shift": k[1], "count": n}
                for k, n in sorted(groups.items(), reverse=True)]
    return {"line_id": line_id, "count": len(out), "slips": out, "by_shift": by_shift,
            "grace_min": FILL_GRACE_MIN}


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


# ── B/D ATTENDED BY: who is on duty now (2026-10-08) ─────────────────────
# Operator: "maintenance attendance board me current time shift-wise jo person
# allot hain unka dropdown, multiple naam select".  Same rule as Maintenance_DX
# routers/attendance.py on_duty(): 07:00-18:00 = day (G + A rows), otherwise
# night (B); after midnight it is still yesterday's night.  A person's row for
# a day is their latest board row on or before it; unknown rows count as G.
_ATT_SLOTS = ("G", "A", "B", "WO", "LEAVE", "WFH")
_DUTY_DAY_FROM, _DUTY_NIGHT_FROM = 7, 18


@router.get("/attendees")
def attendees(user=Depends(get_current_user)):
    now = datetime.now()
    day = now.date()
    if _DUTY_DAY_FROM <= now.hour < _DUTY_NIGHT_FROM:
        slots, label = ("G", "A"), "G + A"
    else:
        slots, label = ("B",), "B"
        if now.hour < _DUTY_DAY_FROM:
            day = day - timedelta(days=1)
    people = []
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (b.staff_id) b.staff_id, b.slot, b.pos
                      FROM maintenance_attendance_board b
                     WHERE b.day <= %s
                     ORDER BY b.staff_id, b.day DESC)
                SELECT s.name, s.emp_code, l.slot
                  FROM latest l
                  JOIN maintenance_employee s ON s.id = l.staff_id
                 WHERE s.removed_on IS NULL OR s.removed_on > %s
                 ORDER BY l.pos, s.name
            """, (day, day))
            rows = cur.fetchall()
        except psycopg2.Error:
            rows = []                     # board not set up yet -> typed names only
    for r in rows:
        slot = (r.get("slot") or "").strip().upper()
        if (slot if slot in _ATT_SLOTS else "G") in slots and (r.get("name") or "").strip():
            people.append({"name": r["name"].strip(), "emp_code": r.get("emp_code") or ""})
    return {"shift": label, "day": day.isoformat(), "people": people}


# ── MACHINE OPERATOR NAME from Shift Allocation (2026-10-08) ─────────────
# Operator: "shift allocation hai to machine select hote hi operator ka naam".
# The slip's machine comes from maintenance_machines; its machine_name is the
# same text as the MES process (mes_processes.process_name) that Shift
# Allocation assigns people to, so the two are matched on the normalised name
# within the slip's line.  Also returns the MES line_id so the page can load
# the line leader for that shift.
def _alloc_shift(shift, hhmm):
    s = (shift or "").strip().upper()
    if s in ("A", "B"):
        return s
    if "B" in s or "2" in s:
        return "B"
    if "A" in s or "1" in s:
        return "A"
    try:
        h, m = (int(x) for x in str(hhmm or "").split(":")[:2])
        return "A" if 8 * 60 + 30 <= h * 60 + m < 18 * 60 + 30 else "B"
    except ValueError:
        return None


@router.get("/allocated-operator")
def allocated_operator(line: str = Query(...), machine_name: str = Query(""),
                       date: Optional[str] = Query(None), shift: Optional[str] = Query(None),
                       time: Optional[str] = Query(None), user=Depends(get_current_user)):
    out = {"line_id": None, "date": None, "shift": None, "process": None, "operators": []}
    try:
        d = _date.fromisoformat(str(date)[:10]) if date else _date.today()
    except ValueError:
        d = _date.today()
    sh = _alloc_shift(shift, time)
    # B shift runs past midnight but is allocated on the day it started.  With
    # the breakdown time known that is decided exactly; without it, try the
    # slip date first and then the day before.
    days = (d,)
    try:
        if sh == "B" and time:
            if int(str(time).split(":")[0]) < 8:
                days = (d - timedelta(days=1),)
        elif sh == "B":
            days = (d, d - timedelta(days=1))
    except ValueError:
        days = (d, d - timedelta(days=1)) if sh == "B" else (d,)
    out["shift"] = sh
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, line_name FROM mes_lines")
        hit = [r["id"] for r in cur.fetchall() if _norm(r["line_name"]) == _norm(line)]
        if not hit:
            return out
        out["line_id"] = hit[0]
        if not sh:
            return out
        for day in days:
            cur.execute("""SELECT o.full_name, p.process_name
                             FROM mes_manpower_allocations ma
                             JOIN mes_operators o ON o.id = ma.operator_id
                        LEFT JOIN mes_processes p ON p.id = ma.process_id
                            WHERE ma.line_id = %s AND ma.shift_date = %s
                              AND ma.shift_name = %s AND ma.removed_at IS NULL
                         ORDER BY o.full_name""", (out["line_id"], day, sh))
            rows = cur.fetchall()
            if rows:
                out["date"] = day.isoformat()
                break
        else:
            return out
    # Shift Allocation columns carry the MES machine editor's names since
    # 8-Oct, which differ slightly from maintenance_machines ("Upper Rail
    # Greasing Machine" vs "Upper Rail Greasing m/c") — take the closest.
    from routers.manpower import _name_score
    best, best_s = None, 0.0
    for p in {r["process_name"] for r in rows if r["process_name"]}:
        s = _name_score(machine_name, p)
        if s > best_s:
            best, best_s = p, s
    if best and best_s >= 0.75:
        out["process"] = best
        out["operators"] = list(dict.fromkeys(
            r["full_name"].strip() for r in rows
            if r["process_name"] == best and (r["full_name"] or "").strip()))
    return out


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
    t = _row_to_ticket(d)
    with get_conn() as conn:
        _lock_fields(t, _fill_deadline(dict_cursor(conn), t, {}), user)
    return t


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
    if len(str(flat.get("bd_attended_by") or "")) > BD_ATTENDED_MAX:
        raise HTTPException(400, f"B/D ATTENDED BY is too long (max {BD_ATTENDED_MAX} characters).")

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
    # 30-minute rule: past the slip's shift end + FILL_GRACE_MIN only the
    # Shift Incharge (or admin) may still fill it.  Enforced here, not just by
    # a greyed button.
    with _maint_conn() as rconn:
        rcur = rconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        rcur.execute(f"""SELECT line, shift, slip_date, bd_start_date, bd_start_time
                           FROM {tbl} WHERE id = %s""", (sid,))
        srow = rcur.fetchone()
    if srow:
        with get_conn() as econn:
            deadline = _fill_deadline(dict_cursor(econn), dict(srow), {})
        if (deadline and datetime.now() > deadline
                and (user or {}).get("role") not in LATE_FILL_ROLES):
            raise HTTPException(403,
                f"Shift ended more than {FILL_GRACE_MIN} min ago "
                f"(window closed {deadline:%d-%b %H:%M}). Only the Shift Incharge can fill this slip now.")
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
