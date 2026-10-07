"""
Historical → Breakdown History  (2026-10-07)

One list of every breakdown slip, from the moment it is raised until it is
closed, merged from all places a slip can live:

    maintenance_db (Maintenance_DX, port 9965 flow)
        maintenance_auto_breakdown_slip   AUTO-MAINT    (andon Maintenance call)
        toolroom_auto_breakdown_slip      AUTO-TOOLROOM (andon Toolroom call)
        maintenance_breakdown_data        MANUAL        (filled on 9965)
    energydb (MES)
        mes_breakdown_log                 MES-LOG       (Historical "+ Add Breakdown")
        mes_breakdowns                    MES-SLIP      (old Breakdown Slips tab)

Stage of an auto slip follows its prod_stage:
    PENDING_PRODUCTION -> PENDING_MAINTENANCE -> COMPLETED
A manual 9965 slip is filled by maintenance in one go, so it is CLOSED.

Read-only; scoped to the user's assigned lines (same rule as Andon History).
GET /api/breakdown-history          merged list + summary + zone/line options
GET /api/breakdown-history/slip     one slip, every field (full slip view)
"""
from datetime import date, datetime, time as dtime
from decimal import Decimal
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_conn, dict_cursor
from routers.andon import _maint_conn, _user_line_scope, _norm

router = APIRouter(prefix="/api/breakdown-history", tags=["breakdown-history"])

MAINT_TABLES = {
    "AUTO-MAINT":    ("maintenance_auto_breakdown_slip", "Maintenance", True),
    "AUTO-TOOLROOM": ("toolroom_auto_breakdown_slip",    "Toolroom",    True),
    "MANUAL":        ("maintenance_breakdown_data",      "Maintenance", False),
}
STAGE_LABEL = {
    "PENDING_PRODUCTION":  "Production pending",
    "PENDING_MAINTENANCE": "Maintenance pending",
    "COMPLETED":           "Closed",
    "CLOSED":              "Closed",
    "OPEN":                "Open",
    "RESOLVED":            "Resolved",
}
_COMMON = ("id, zone, line, machine_no, machine_name, slip_date, shift, model_no, "
           "category, bd_start_date, bd_start_time, bd_end_date, bd_ok_time, "
           "bd_received_time, mc_down_time_minutes, response_time_minutes, "
           "problem_reported_by_production, problem_observed_by_maintenance, "
           "action_taken_on_problem, spares_used, spares, bd_attended_by, "
           "line_leader_name, machine_operator_name, submitted_at")


def _s(v):
    """JSON-safe scalar."""
    if v is None:
        return None
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _txt(v):
    v = _s(v)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _in_scope(scope, line):
    kind, names = scope
    if kind == "all":
        return True
    if kind == "none":
        return False
    return _norm(line) in names


def _maint_rows(scope):
    out = []
    with _maint_conn() as mc:
        cur = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for src, (table, dept, is_auto) in MAINT_TABLES.items():
            extra = ", prod_stage, andon_event_id" if is_auto else ""
            cur.execute(f"SELECT {_COMMON}{extra} FROM {table}")
            for r in cur.fetchall():
                if not _in_scope(scope, r.get("line")):
                    continue
                stage = (r.get("prod_stage") or "PENDING_PRODUCTION") if is_auto else "COMPLETED"
                spares = _txt(r.get("spares_used")) or _txt(r.get("spares"))
                d = r.get("slip_date") or r.get("bd_start_date")
                out.append({
                    "key": f"{src}:{r['id']}", "src": src, "id": r["id"], "dept": dept,
                    "date": _s(d), "shift": _txt(r.get("shift")),
                    "zone": _txt(r.get("zone")), "line": _txt(r.get("line")),
                    "machine_no": _txt(r.get("machine_no")),
                    "machine_name": _txt(r.get("machine_name")),
                    "model": _txt(r.get("model_no")), "category": _txt(r.get("category")),
                    "bd_start": " ".join(x for x in (_txt(r.get("bd_start_date")),
                                                     _txt(r.get("bd_start_time"))) if x) or None,
                    "bd_ok": " ".join(x for x in (_txt(r.get("bd_end_date")),
                                                  _txt(r.get("bd_ok_time"))) if x) or None,
                    "down_min": _s(r.get("mc_down_time_minutes")),
                    "response_min": _s(r.get("response_time_minutes")),
                    "problem": _txt(r.get("problem_reported_by_production")),
                    "observed": _txt(r.get("problem_observed_by_maintenance")),
                    "action": _txt(r.get("action_taken_on_problem")),
                    "spares": spares,
                    "attended_by": _txt(r.get("bd_attended_by")),
                    "line_leader": _txt(r.get("line_leader_name")),
                    "stage": stage, "stage_label": STAGE_LABEL.get(stage, stage),
                    "closed": stage == "COMPLETED",
                    "andon_event_id": r.get("andon_event_id"),
                })
    return out


def _mes_rows(scope):
    out = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, bd_date, shift, zone_code, line_code, machine_no,
                              machine_name, model_no, problem_production,
                              problem_maintenance, action_taken, spares_detail,
                              attended_by, dept, category, solve_time_min,
                              solve_time_hours, line_leader_name
                         FROM mes_breakdown_log""")
        for r in cur.fetchall():
            if not _in_scope(scope, r.get("line_code")):
                continue
            mins = r.get("solve_time_min")
            if mins is None and r.get("solve_time_hours") is not None:
                mins = float(r["solve_time_hours"]) * 60
            out.append({
                "key": f"MES-LOG:{r['id']}", "src": "MES-LOG", "id": r["id"],
                "dept": _txt(r.get("dept")), "date": _s(r.get("bd_date")),
                "shift": _txt(r.get("shift")), "zone": _txt(r.get("zone_code")),
                "line": _txt(r.get("line_code")), "machine_no": _txt(r.get("machine_no")),
                "machine_name": _txt(r.get("machine_name")), "model": _txt(r.get("model_no")),
                "category": _txt(r.get("category")), "bd_start": None, "bd_ok": None,
                "down_min": round(float(mins), 1) if mins is not None else None,
                "response_min": None, "problem": _txt(r.get("problem_production")),
                "observed": _txt(r.get("problem_maintenance")),
                "action": _txt(r.get("action_taken")), "spares": _txt(r.get("spares_detail")),
                "attended_by": _txt(r.get("attended_by")),
                "line_leader": _txt(r.get("line_leader_name")),
                "stage": "CLOSED", "stage_label": "Closed", "closed": True,
                "andon_event_id": None,
            })
        cur.execute("""SELECT b.id, b.started_at, b.ended_at, b.shift_name, b.state,
                              b.reason, l.line_name, z.zone_name
                         FROM mes_breakdowns b
                         LEFT JOIN mes_lines l ON l.id = b.line_id
                         LEFT JOIN mes_zones z ON z.id = b.zone_id""")
        for r in cur.fetchall():
            if not _in_scope(scope, r.get("line_name")):
                continue
            st, en = r.get("started_at"), r.get("ended_at")
            state = (r.get("state") or "").strip().upper()
            out.append({
                "key": f"MES-SLIP:{r['id']}", "src": "MES-SLIP", "id": r["id"],
                "dept": None, "date": _s(st.date()) if st else None,
                "shift": _txt(r.get("shift_name")), "zone": _txt(r.get("zone_name")),
                "line": _txt(r.get("line_name")), "machine_no": None, "machine_name": None,
                "model": None, "category": None,
                "bd_start": _s(st)[:19] if st else None, "bd_ok": _s(en)[:19] if en else None,
                "down_min": round((en - st).total_seconds() / 60, 1) if (st and en) else None,
                "response_min": None, "problem": _txt(r.get("reason")), "observed": None,
                "action": None, "spares": None, "attended_by": None, "line_leader": None,
                "stage": state, "stage_label": STAGE_LABEL.get(state, state.title()),
                "closed": state in ("CLOSED", "RESOLVED"), "andon_event_id": None,
            })
    return out


@router.get("")
def breakdown_history(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    zone:      Optional[str] = Query(None),
    line:      Optional[str] = Query(None),
    stage:     Optional[str] = Query(None, description="open | closed"),
    src:       Optional[str] = Query(None),
    q:         Optional[str] = Query(None),
    limit:     int = Query(3000, ge=1, le=10000),
    user=Depends(get_current_user),
):
    scope = _user_line_scope(user)
    rows, warn = [], None
    try:
        rows += _maint_rows(scope)
    except HTTPException as e:
        warn = f"Maintenance slips not available: {e.detail}"
    rows += _mes_rows(scope)

    # zone -> lines options from everything the user may see (before filters)
    opts = {}                      # norm(zone) -> [display, {norm(line): display}]
    for r in rows:
        if r["zone"]:
            z = opts.setdefault(_norm(r["zone"]), [r["zone"], {}])
            if r["line"]:
                z[1].setdefault(_norm(r["line"]), r["line"])

    def keep(r):
        d = (r["date"] or "")[:10]
        if date_from and (not d or d < date_from):
            return False
        if date_to and (not d or d > date_to):
            return False
        if zone and _norm(r["zone"]) != _norm(zone):
            return False
        if line and _norm(r["line"]) != _norm(line):
            return False
        if stage == "open" and r["closed"]:
            return False
        if stage == "closed" and not r["closed"]:
            return False
        if src and r["src"] != src:
            return False
        if q:
            ql = q.strip().lower()
            hay = " ".join(str(r.get(k) or "") for k in (
                "machine_no", "machine_name", "problem", "observed", "action",
                "spares", "attended_by", "model", "line_leader")).lower()
            if ql not in hay:
                return False
        return True

    rows = [r for r in rows if keep(r)]
    rows.sort(key=lambda r: ((r["date"] or ""), (r["bd_start"] or "")), reverse=True)
    total_min = sum(float(r["down_min"] or 0) for r in rows)
    summary = {
        "total": len(rows),
        "open": sum(1 for r in rows if not r["closed"]),
        "closed": sum(1 for r in rows if r["closed"]),
        "production_pending": sum(1 for r in rows if r["stage"] == "PENDING_PRODUCTION"),
        "maintenance_pending": sum(1 for r in rows if r["stage"] == "PENDING_MAINTENANCE"),
        "downtime_hours": round(total_min / 60, 1),
    }
    return {
        "rows": rows[:limit], "summary": summary, "warning": warn,
        "options": [{"zone": zd, "lines": sorted(ls.values())}
                    for zd, ls in sorted(opts.values(), key=lambda v: v[0])],
    }


@router.get("/slip")
def slip(src: str, id: int, user=Depends(get_current_user)):
    """Every field of one Maintenance_DX slip, for the full-slip view."""
    if src not in MAINT_TABLES:
        raise HTTPException(400, "Full slip view is available for Maintenance slips only")
    table, dept, is_auto = MAINT_TABLES[src]
    with _maint_conn() as mc:
        cur = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {table} WHERE id = %s", (id,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "Slip not found")
    if not _in_scope(_user_line_scope(user), r.get("line")):
        raise HTTPException(403, "This line is not assigned to you")
    out = {k: _s(v) for k, v in r.items()}
    stage = (r.get("prod_stage") or "PENDING_PRODUCTION") if is_auto else "COMPLETED"
    out.update({"src": src, "dept": dept, "stage": stage,
                "stage_label": STAGE_LABEL.get(stage, stage)})
    return out
