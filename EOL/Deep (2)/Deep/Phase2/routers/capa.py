"""
routers/capa.py
===============
CAPA (Corrective Action / Preventive Action) for Maintenance.

Two trigger kinds — both detected on-the-fly by `/pending` (no background
worker needed):

  • SINGLE_LIMIT  : a single closed breakdown's downtime exceeded the
                    `single_breakdown_minutes_limit` for that machine.
  • MONTHLY_LIMIT : the month-to-date sum of breakdown minutes for a
                    (line, machine_no) pair exceeded the
                    `monthly_sum_minutes_limit`.

Threshold lookup picks the most specific row:
    Machine (line_id + machine_no)  →  Line (line_id)  →  Global

CAPA payload is stored in `capa_data` JSONB.  We ship a sensible 8D-ish
default (problem description / root cause / containment / corrective
action / preventive action / verification / responsible / target date /
sign-offs) — admin can refine when the actual plant template is shared.

Endpoints
---------
GET    /api/capa/thresholds           List threshold rows
POST   /api/capa/thresholds           Create / upsert (admin)
PUT    /api/capa/thresholds/{id}      Update (admin)
DELETE /api/capa/thresholds/{id}      Delete (admin)

GET    /api/capa/pending              Auto-detected pending CAPAs (no row yet)
GET    /api/capa/                     List CAPA filings (filterable)
GET    /api/capa/{id}                 Single CAPA (full payload)
POST   /api/capa/                     Create new CAPA (Maintenance opens it)
PUT    /api/capa/{id}                 Update payload / status
POST   /api/capa/{id}/close           Mark CLOSED
"""
from datetime import datetime, date, timedelta
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from psycopg2.extras import Json

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/capa", tags=["capa"])


# ── Models ────────────────────────────────────────────────────────────
class ThresholdUpsert(BaseModel):
    scope:                          str   # 'GLOBAL' | 'LINE' | 'MACHINE'
    line_id:                        Optional[int] = None
    machine_no:                     Optional[str] = None
    label:                          Optional[str] = None
    monthly_sum_minutes_limit:      int   = 120
    single_breakdown_minutes_limit: int   = 60
    is_active:                      bool  = True


class CapaCreate(BaseModel):
    trigger_kind:           str   # 'SINGLE_LIMIT' | 'MONTHLY_LIMIT'
    breakdown_id:           Optional[int] = None
    trigger_value_minutes:  Optional[int] = None
    threshold_minutes:      Optional[int] = None
    line_id:                Optional[int] = None
    line_name:              Optional[str] = None
    zone_id:                Optional[int] = None
    zone_name:              Optional[str] = None
    machine_no:             Optional[str] = None
    machine_name:           Optional[str] = None
    month_year:             Optional[str] = None
    capa_data:              Optional[Dict[str, Any]] = None


class CapaUpdate(BaseModel):
    status:    Optional[str] = None       # OPEN | IN_PROGRESS | CLOSED
    capa_data: Optional[Dict[str, Any]] = None


# ── Helpers ───────────────────────────────────────────────────────────
def _resolve_threshold(line_id: Optional[int], machine_no: Optional[str],
                       conn) -> Dict[str, int]:
    """Walk Machine → Line → Global to find the most specific active row.
    Returns {monthly, single} (minutes).  Falls back to (120, 60) if
    nothing is configured (defensive)."""
    cur = dict_cursor(conn)
    if line_id is not None and machine_no:
        cur.execute("""
            SELECT monthly_sum_minutes_limit, single_breakdown_minutes_limit
              FROM mes_capa_thresholds
             WHERE scope='MACHINE' AND line_id=%s AND machine_no=%s AND is_active=TRUE
             LIMIT 1
        """, (line_id, machine_no))
        r = cur.fetchone()
        if r: return {"monthly": r["monthly_sum_minutes_limit"], "single": r["single_breakdown_minutes_limit"]}
    if line_id is not None:
        cur.execute("""
            SELECT monthly_sum_minutes_limit, single_breakdown_minutes_limit
              FROM mes_capa_thresholds
             WHERE scope='LINE' AND line_id=%s AND is_active=TRUE
             LIMIT 1
        """, (line_id,))
        r = cur.fetchone()
        if r: return {"monthly": r["monthly_sum_minutes_limit"], "single": r["single_breakdown_minutes_limit"]}
    cur.execute("""
        SELECT monthly_sum_minutes_limit, single_breakdown_minutes_limit
          FROM mes_capa_thresholds
         WHERE scope='GLOBAL' AND is_active=TRUE
         LIMIT 1
    """)
    r = cur.fetchone()
    if r: return {"monthly": r["monthly_sum_minutes_limit"], "single": r["single_breakdown_minutes_limit"]}
    return {"monthly": 120, "single": 60}


# ── Threshold CRUD ────────────────────────────────────────────────────
@router.get("/thresholds")
def list_thresholds(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT t.id, t.scope, t.line_id, l.line_name,
                   t.machine_no, t.label,
                   t.monthly_sum_minutes_limit,
                   t.single_breakdown_minutes_limit,
                   t.is_active, t.created_at, t.updated_at
              FROM mes_capa_thresholds t
              LEFT JOIN mes_lines l ON l.id = t.line_id
             ORDER BY (CASE scope WHEN 'GLOBAL' THEN 0
                                  WHEN 'LINE'   THEN 1
                                  WHEN 'MACHINE' THEN 2 END),
                      t.line_id, t.machine_no
        """)
        return cur.fetchall()


@router.post("/thresholds", status_code=201)
def create_threshold(body: ThresholdUpsert, admin=Depends(require_admin)):
    if body.scope not in ("GLOBAL", "LINE", "MACHINE"):
        raise HTTPException(400, "scope must be GLOBAL / LINE / MACHINE")
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO mes_capa_thresholds
                    (scope, line_id, machine_no, label,
                     monthly_sum_minutes_limit, single_breakdown_minutes_limit, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (body.scope, body.line_id, body.machine_no, body.label,
                  body.monthly_sum_minutes_limit, body.single_breakdown_minutes_limit,
                  body.is_active))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Create failed (duplicate scope?): {e}")
    return {"id": new_id}


@router.put("/thresholds/{tid}")
def update_threshold(tid: int, body: ThresholdUpsert, admin=Depends(require_admin)):
    if body.scope not in ("GLOBAL", "LINE", "MACHINE"):
        raise HTTPException(400, "scope must be GLOBAL / LINE / MACHINE")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_capa_thresholds
               SET scope=%s, line_id=%s, machine_no=%s, label=%s,
                   monthly_sum_minutes_limit=%s, single_breakdown_minutes_limit=%s,
                   is_active=%s, updated_at=NOW()
             WHERE id=%s
        """, (body.scope, body.line_id, body.machine_no, body.label,
              body.monthly_sum_minutes_limit, body.single_breakdown_minutes_limit,
              body.is_active, tid))
        if cur.rowcount == 0:
            raise HTTPException(404, "Threshold not found")
        conn.commit()
    return {"ok": True}


# ── Global CAPA configuration ────────────────────────────────────────
# All three knobs that drive the Pareto-CAPA pipeline live on the
# GLOBAL row of mes_capa_thresholds:
#
#   • monthly_sum_minutes_limit       — per-machine breakdown ceiling
#                                        for the calendar month.  When a
#                                        machine's MTD sum exceeds this,
#                                        it joins the "breached" cohort.
#   • single_breakdown_minutes_limit  — per-event ceiling that triggers
#                                        an immediate SINGLE_LIMIT CAPA.
#   • pareto_pct                      — of the breached cohort, the top
#                                        N % (cumulative time) MUST file.
#
# Per-line / per-machine overrides still come from POST /thresholds with
# scope='LINE' or 'MACHINE' (resolver picks most specific).  This
# endpoint only updates the GLOBAL row so admin has one single command
# centre.  Everything in the Maintenance dashboard recomputes against
# the new numbers immediately (frontend polls + ap-config-changed
# event).
class GlobalCapaConfig(BaseModel):
    pareto_pct:                     int = 80
    monthly_sum_minutes_limit:      int = 120
    single_breakdown_minutes_limit: int = 60


@router.get("/pareto-config")
def get_pareto_config(user=Depends(get_current_user)):
    """Return all three GLOBAL knobs.  Endpoint name kept as
    `/pareto-config` for backward compat — original payload was just
    `{pareto_pct}` but is now a superset, so old callers still work."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT pareto_pct, monthly_sum_minutes_limit, single_breakdown_minutes_limit
              FROM mes_capa_thresholds
             WHERE scope='GLOBAL' AND is_active=TRUE LIMIT 1
        """)
        r = cur.fetchone()
        if r:
            return {
                "pareto_pct":                     int(r["pareto_pct"]),
                "monthly_sum_minutes_limit":      int(r["monthly_sum_minutes_limit"]),
                "single_breakdown_minutes_limit": int(r["single_breakdown_minutes_limit"]),
            }
        return {"pareto_pct": 80, "monthly_sum_minutes_limit": 120,
                "single_breakdown_minutes_limit": 60}


@router.put("/pareto-config")
def set_pareto_config(body: GlobalCapaConfig, admin=Depends(require_admin)):
    pct      = max(1,  min(100,    int(body.pareto_pct)))
    monthly  = max(1,  min(99_999, int(body.monthly_sum_minutes_limit)))
    single   = max(1,  min(99_999, int(body.single_breakdown_minutes_limit)))
    with get_conn() as conn:
        cur = conn.cursor()
        # Upsert onto the GLOBAL row.  ON CONFLICT bound to the partial
        # unique index uq_capa_thresh_global, which only fires when
        # scope='GLOBAL'.
        cur.execute("""
            INSERT INTO mes_capa_thresholds
                (scope, monthly_sum_minutes_limit, single_breakdown_minutes_limit,
                 pareto_pct, is_active)
            VALUES ('GLOBAL', %s, %s, %s, TRUE)
            ON CONFLICT (scope) WHERE scope='GLOBAL'
            DO UPDATE SET pareto_pct                     = EXCLUDED.pareto_pct,
                          monthly_sum_minutes_limit      = EXCLUDED.monthly_sum_minutes_limit,
                          single_breakdown_minutes_limit = EXCLUDED.single_breakdown_minutes_limit,
                          updated_at                     = NOW()
        """, (monthly, single, pct))
        conn.commit()
    return {"pareto_pct": pct, "monthly_sum_minutes_limit": monthly,
            "single_breakdown_minutes_limit": single}


# ── Pareto: zone-wise breakdown chart data ────────────────────────────
# Returns one bar per (line_id, machine_no) for the chosen month, sorted
# descending by total breakdown minutes.  Computes cumulative % so the
# frontend can highlight the top `pareto_pct` cohort that's mandated to
# file CAPA.  When `zone_id` is omitted, returns every zone's machines
# combined (admin-wide view).
@router.get("/pareto")
def pareto_data(
    zone_id:    Optional[int] = Query(None),
    line_id:    Optional[int] = Query(None),
    month_year: Optional[str] = Query(None,
                  description="YYYY-MM; defaults to current month"),
    user=Depends(get_current_user),
):
    # Resolve month window (calendar month — variables reset on the 1st)
    today = date.today()
    if month_year:
        try:
            y, m = month_year.split("-")
            lo = date(int(y), int(m), 1)
        except Exception:
            raise HTTPException(400, "month_year must be YYYY-MM")
    else:
        lo = today.replace(day=1)
    nm = (lo.replace(day=28) + timedelta(days=4)).replace(day=1)
    hi = nm  # exclusive upper bound

    where = ["b.started_at >= %s", "b.started_at < %s",
             "(b.production_data->>'machine_no') IS NOT NULL"]
    params: list = [lo, hi]
    if zone_id is not None:
        where.append("b.zone_id = %s"); params.append(zone_id)
    if line_id is not None:
        where.append("b.line_id = %s"); params.append(line_id)

    with get_conn() as conn:
        cur = dict_cursor(conn)

        # 1. Aggregate breakdown minutes per (line, machine_no)
        cur.execute(f"""
            SELECT b.line_id,
                   l.line_name,
                   b.zone_id,
                   z.zone_name,
                   b.production_data->>'machine_no'   AS machine_no,
                   b.production_data->>'machine_name' AS machine_name,
                   COUNT(*) AS event_count,
                   COALESCE(SUM(EXTRACT(EPOCH FROM
                       (COALESCE(b.ended_at, NOW()) - b.started_at))/60.0), 0)::INT
                       AS breakdown_minutes
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE {' AND '.join(where)}
             GROUP BY b.line_id, l.line_name, b.zone_id, z.zone_name,
                      b.production_data->>'machine_no',
                      b.production_data->>'machine_name'
        """, params)
        rows = [dict(r) for r in cur.fetchall()]

        # 2. Pareto-cutoff config + per-machine threshold
        cur.execute("""
            SELECT pareto_pct FROM mes_capa_thresholds
             WHERE scope='GLOBAL' AND is_active=TRUE LIMIT 1
        """)
        gp = cur.fetchone()
        pareto_pct = int(gp["pareto_pct"]) if gp else 80

        # 3. Mark each row with whether it breached its monthly threshold
        for r in rows:
            t = _resolve_threshold(r["line_id"], r["machine_no"], conn)
            r["threshold_minutes"] = int(t["monthly"])
            r["breached"] = bool(r["breakdown_minutes"] > t["monthly"])

        # 4. Sort desc by breakdown_minutes
        rows.sort(key=lambda x: -int(x["breakdown_minutes"]))

        # 5. Compute cumulative % across BREACHED machines only — Pareto
        #    cutoff is applied to the at-risk cohort, not to clean
        #    machines that happened to be #1 by minutes but were within
        #    their limit.
        breached_total = sum(r["breakdown_minutes"] for r in rows if r["breached"])
        running = 0.0
        for r in rows:
            if r["breached"] and breached_total > 0:
                running += r["breakdown_minutes"]
                cum_pct = round(running / breached_total * 100.0, 2)
                r["cumulative_pct"]  = cum_pct
                # Top-N % by cumulative coverage MUST file CAPA.  We use
                # the "include the row that crosses the threshold" rule
                # (≤ pareto_pct OR running was below before this row) so
                # the cohort always covers at least pareto_pct% of total.
                r["must_file_capa"] = (running - r["breakdown_minutes"]) < (breached_total * pareto_pct / 100.0)
            else:
                r["cumulative_pct"]  = None
                r["must_file_capa"] = False

        # 6. Existing CAPA filings index for the month (so we can label
        #    "Already filed" vs "Pending").  Match by (line_id, machine_no, month).
        month_str = lo.strftime("%Y-%m")
        cur.execute("""
            SELECT line_id, machine_no, status
              FROM mes_capa
             WHERE line_id = ANY(%s::INT[])
               AND machine_no = ANY(%s::TEXT[])
               AND (month_year = %s OR (created_at >= %s AND created_at < %s))
        """, ([r["line_id"] for r in rows] or [None],
              [r["machine_no"] for r in rows] or [None],
              month_str, lo, hi))
        filed = {}
        for f in cur.fetchall():
            key = (f["line_id"], f["machine_no"])
            # Latest non-cancelled wins
            if key not in filed or f["status"] != "CANCELLED":
                filed[key] = f["status"]
        for r in rows:
            r["capa_status"] = filed.get((r["line_id"], r["machine_no"]))

    return {
        "month_year":      lo.strftime("%Y-%m"),
        "from_date":       lo.isoformat(),
        "to_date":         (hi - timedelta(days=1)).isoformat(),
        "pareto_pct":      pareto_pct,
        "breached_total":  breached_total,
        "machines":        rows,
    }


@router.delete("/thresholds/{tid}")
def delete_threshold(tid: int, admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_capa_thresholds WHERE id=%s", (tid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Threshold not found")
        conn.commit()
    return {"ok": True}


# ── Pending CAPA detection (no row yet) ───────────────────────────────
@router.get("/pending")
def pending(user=Depends(get_current_user)):
    """Return every breakdown / machine combo that's crossed a threshold
    but doesn't have a CAPA row yet.  Two streams in the response:

      single_limit_breaches : list of CLOSED breakdowns whose duration
                              exceeded the single-breakdown limit, with
                              no SINGLE_LIMIT capa already opened.
      monthly_limit_breaches: list of (line, machine_no, month) tuples
                              whose month-to-date sum exceeded the
                              monthly limit, with no MONTHLY_LIMIT capa
                              already opened for that month.
    """
    today = date.today()
    month_start = today.replace(day=1)
    month_year = today.strftime("%Y-%m")

    with get_conn() as conn:
        cur = dict_cursor(conn)

        # ── Single-limit breaches ─────────────────────────────────────
        # Pull every CLOSED breakdown ≥ 30 days back that has machine_no
        # filled, compute duration_minutes, compare to per-machine limit.
        cur.execute("""
            SELECT b.id AS breakdown_id,
                   b.line_id, l.line_name,
                   b.zone_id, z.zone_name,
                   b.production_data->>'machine_no'   AS machine_no,
                   b.production_data->>'machine_name' AS machine_name,
                   b.started_at, b.ended_at,
                   ROUND(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0)::int AS dur_min
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.ended_at IS NOT NULL
               AND b.started_at >= %s
               AND NULLIF(b.production_data->>'machine_no', '') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM mes_capa c
                    WHERE c.trigger_kind='SINGLE_LIMIT'
                      AND c.breakdown_id = b.id
               )
             ORDER BY b.started_at DESC
        """, (today - timedelta(days=60),))
        single_breaches = []
        for r in cur.fetchall():
            r = dict(r)
            t = _resolve_threshold(r["line_id"], r["machine_no"], conn)
            if r["dur_min"] and r["dur_min"] >= t["single"]:
                r["threshold_minutes"]     = t["single"]
                r["trigger_value_minutes"] = r["dur_min"]
                single_breaches.append(r)

        # ── Monthly-limit breaches ────────────────────────────────────
        # Sum month-to-date downtime per (line, machine_no), compare to
        # per-machine monthly limit.
        cur.execute("""
            SELECT b.line_id, l.line_name,
                   b.zone_id, z.zone_name,
                   b.production_data->>'machine_no'   AS machine_no,
                   b.production_data->>'machine_name' AS machine_name,
                   COUNT(*)                                                  AS event_count,
                   COALESCE(SUM(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0), 0)::int AS sum_min
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.ended_at IS NOT NULL
               AND b.started_at >= %s
               AND NULLIF(b.production_data->>'machine_no', '') IS NOT NULL
             GROUP BY b.line_id, l.line_name, b.zone_id, z.zone_name,
                      machine_no, machine_name
        """, (month_start,))
        monthly_breaches = []
        for r in cur.fetchall():
            r = dict(r)
            t = _resolve_threshold(r["line_id"], r["machine_no"], conn)
            if r["sum_min"] >= t["monthly"]:
                # Skip if a MONTHLY_LIMIT capa already exists this month.
                cur.execute("""
                    SELECT 1 FROM mes_capa
                     WHERE trigger_kind='MONTHLY_LIMIT'
                       AND line_id = %s AND machine_no = %s AND month_year = %s
                     LIMIT 1
                """, (r["line_id"], r["machine_no"], month_year))
                if cur.fetchone():
                    continue
                r["threshold_minutes"]     = t["monthly"]
                r["trigger_value_minutes"] = r["sum_min"]
                r["month_year"]            = month_year
                monthly_breaches.append(r)

    return {
        "single_limit_breaches":  single_breaches,
        "monthly_limit_breaches": monthly_breaches,
        "month_year":             month_year,
    }


# ── CAPA CRUD ─────────────────────────────────────────────────────────
@router.get("/")
def list_capa(status:    Optional[str] = Query(None),
              line_id:   Optional[int] = Query(None),
              machine_no:Optional[str] = Query(None),
              days:      int = Query(180, ge=1, le=730),
              user=Depends(get_current_user)):
    where  = ["c.opened_at >= %s"]
    params = [datetime.utcnow() - timedelta(days=days)]
    if status:
        where.append("c.status = %s"); params.append(status.upper())
    if line_id is not None:
        where.append("c.line_id = %s"); params.append(line_id)
    if machine_no:
        where.append("c.machine_no = %s"); params.append(machine_no)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT c.id, c.breakdown_id, c.trigger_kind,
                   c.trigger_value_minutes, c.threshold_minutes,
                   c.line_id, c.line_name, c.zone_id, c.zone_name,
                   c.machine_no, c.machine_name, c.month_year,
                   c.status, c.capa_data,
                   c.opened_at, c.closed_at,
                   ou.username AS opened_by_username,
                   cu.username AS closed_by_username
              FROM mes_capa c
              LEFT JOIN mes_admin ou ON ou.id = c.opened_by_user_id
              LEFT JOIN mes_admin cu ON cu.id = c.closed_by_user_id
             WHERE {' AND '.join(where)}
             ORDER BY c.opened_at DESC
        """, params)
        return cur.fetchall()


@router.get("/{capa_id}")
def get_capa(capa_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT c.*, ou.username AS opened_by_username,
                        cu.username AS closed_by_username
              FROM mes_capa c
              LEFT JOIN mes_admin ou ON ou.id = c.opened_by_user_id
              LEFT JOIN mes_admin cu ON cu.id = c.closed_by_user_id
             WHERE c.id = %s
        """, (capa_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "CAPA not found")
        return row


@router.post("/", status_code=201)
def create_capa(body: CapaCreate, user=Depends(get_current_user)):
    if body.trigger_kind not in ("SINGLE_LIMIT", "MONTHLY_LIMIT"):
        raise HTTPException(400, "trigger_kind must be SINGLE_LIMIT or MONTHLY_LIMIT")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_capa
                (breakdown_id, trigger_kind, trigger_value_minutes, threshold_minutes,
                 line_id, line_name, zone_id, zone_name,
                 machine_no, machine_name, month_year,
                 status, capa_data, opened_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'OPEN', %s, %s)
            RETURNING id
        """, (body.breakdown_id, body.trigger_kind,
              body.trigger_value_minutes, body.threshold_minutes,
              body.line_id, body.line_name, body.zone_id, body.zone_name,
              body.machine_no, body.machine_name, body.month_year,
              Json(body.capa_data) if body.capa_data is not None else None,
              user["id"]))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id}


@router.put("/{capa_id}")
def update_capa(capa_id: int, body: CapaUpdate, user=Depends(get_current_user)):
    upd, params = [], []
    if body.status is not None:
        if body.status not in ("OPEN", "IN_PROGRESS", "CLOSED"):
            raise HTTPException(400, "status must be OPEN / IN_PROGRESS / CLOSED")
        upd.append("status = %s"); params.append(body.status)
        if body.status == "CLOSED":
            upd.append("closed_at = NOW()")
            upd.append("closed_by_user_id = %s"); params.append(user["id"])
    if body.capa_data is not None:
        upd.append("capa_data = %s"); params.append(Json(body.capa_data))
    if not upd:
        return {"ok": True, "updated": False}
    upd.append("updated_at = NOW()")
    params.append(capa_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE mes_capa SET {', '.join(upd)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "CAPA not found")
        conn.commit()
    return {"ok": True, "updated": True}


@router.post("/{capa_id}/close")
def close_capa(capa_id: int, user=Depends(get_current_user)):
    """Shorthand for setting status=CLOSED + stamping closer."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_capa
               SET status='CLOSED',
                   closed_at = NOW(),
                   closed_by_user_id = %s,
                   updated_at = NOW()
             WHERE id = %s AND status <> 'CLOSED'
        """, (user["id"], capa_id))
        if cur.rowcount == 0:
            raise HTTPException(409, "CAPA already closed or not found")
        conn.commit()
    return {"ok": True}
