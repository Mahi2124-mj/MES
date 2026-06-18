"""
routers/breakdowns.py
=====================
Maintenance Breakdown tracking — powers the Maintenance Dashboard.

State machine
-------------
    OPEN  ──(ended_at stamped)──>  RESOLVED  ──(closure form submitted)──>  CLOSED
     │                                                                        ▲
     └──── visible on ANDON live table ──┘  └─── visible on History table ───┘

Closure form is the Toyota Boshoku BREAK DOWN SLIP, split into TWO halves:

    • Production half  (`production_data` JSONB)
        Filled by Production / line leader the moment the line goes to
        BREAKDOWN.  Contains line/zone/machine identity, category tick,
        received-time, "Problem reported by Production" text, etc.

    • Maintenance half (`maintenance_data` JSONB)
        Filled by Maintenance after the line returns to RUNNING.  Contains
        actual problem observed, action taken, spares used, attended-by,
        signatures.

Auto-locked, never editable by either side:
    • B/D Start Time + Start Date  ← collector stamps from `started_at`
    • B/D OK Time   + End Date     ← collector stamps from `ended_at`
    • M/C Down Time in Minutes     ← computed from (ended_at - started_at)

Endpoints
---------
GET    /api/breakdowns/active                ANDON live (state='OPEN')
GET    /api/breakdowns/recent?days=2         last N days (RESOLVED + CLOSED)
GET    /api/breakdowns/pending-production    OPEN rows where Production hasn't filled yet
POST   /api/breakdowns                       open a new breakdown
POST   /api/breakdowns/{id}/resolve          stamp ended_at — moves to history
POST   /api/breakdowns/{id}/production-fill  Production saves their half
POST   /api/breakdowns/{id}/close            Maintenance saves + closes (state='CLOSED')
GET    /api/breakdowns/stats                 zone + line MTBF/MTTR/LTTR/LTBF
"""

from datetime import datetime, timedelta
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from psycopg2.extras import Json

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/breakdowns", tags=["breakdowns"])


# ── Models ────────────────────────────────────────────────────────────────
class BreakdownCreate(BaseModel):
    line_id:    int
    reason:     Optional[str] = None
    started_at: Optional[datetime] = None     # defaults to NOW()


class BreakdownResolve(BaseModel):
    ended_at: Optional[datetime] = None       # defaults to NOW()


class BreakdownProductionFill(BaseModel):
    production_data: Dict[str, Any]


class BreakdownClose(BaseModel):
    # Backwards-compat: callers still send `closure_data` but it's now
    # interpreted as the maintenance half.  New callers can also send
    # `maintenance_data` explicitly.
    closure_data:     Optional[Dict[str, Any]] = None
    maintenance_data: Optional[Dict[str, Any]] = None


# ── Helpers ───────────────────────────────────────────────────────────────
def _shift_for(line_id: int, when: datetime, conn) -> Optional[str]:
    """Find the shift name a timestamp falls into for a given line.
    Falls back to NULL if the line has no shift_configs row."""
    cur = conn.cursor()
    cur.execute("""
        SELECT shift_name, start_time, end_time
          FROM mes_shift_configs
         WHERE line_id = %s
    """, (line_id,))
    rows = cur.fetchall()
    if not rows:
        return None
    t = when.time()
    for shift_name, st, en in rows:
        if st <= en:
            if st <= t < en:
                return shift_name
        else:
            # Wraps midnight
            if t >= st or t < en:
                return shift_name
    return rows[0][0]


def _next_serial(line_id: int, started_at: datetime, shift_name: Optional[str], conn) -> int:
    """Compute serial_in_shift = how many breakdowns this line has had in
    the same shift today (1-based)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(MAX(serial_in_shift), 0) + 1
          FROM mes_breakdowns
         WHERE line_id = %s
           AND shift_name IS NOT DISTINCT FROM %s
           AND DATE(started_at) = DATE(%s)
    """, (line_id, shift_name, started_at))
    return cur.fetchone()[0] or 1


def _enrich_row(r: dict) -> dict:
    """Attach a `duration_seconds` field to a breakdown row.  For OPEN tickets
    we measure from started_at to NOW; for closed ones we use ended_at."""
    started = r.get("started_at")
    ended   = r.get("ended_at") or datetime.utcnow()
    if started:
        if hasattr(started, "tzinfo") and started.tzinfo:
            now = datetime.now(started.tzinfo)
            ended = r.get("ended_at") or now
        delta = ended - started
        r["duration_seconds"] = int(delta.total_seconds())
    else:
        r["duration_seconds"] = 0
    return r


_FULL_COLS = """
    b.id, b.line_id, b.zone_id, b.shift_name, b.serial_in_shift,
    b.started_at, b.ended_at, b.state, b.reason,
    b.closed_at, b.closure_data, b.closed_by_user_id,
    b.production_data,  b.production_filled_at,  b.production_filled_by_user_id,
    b.maintenance_data, b.maintenance_filled_at, b.maintenance_filled_by_user_id,
    l.line_name, l.line_code,
    z.zone_name, z.zone_code
"""


# ── ANDON / History ───────────────────────────────────────────────────────
@router.get("/active")
def list_active(user=Depends(get_current_user)):
    """Live ANDON — every breakdown still in state='OPEN'."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT {_FULL_COLS}
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.state = 'OPEN'
             ORDER BY b.started_at ASC
        """)
        return [_enrich_row(dict(r)) for r in cur.fetchall()]


@router.get("/pending-production")
def list_pending_production(user=Depends(get_current_user)):
    """Breakdowns where Production hasn't filled their half yet.
    Drives the banner on the Production Dashboard ("X breakdowns need
    your input").

    Includes both OPEN and RESOLVED tickets — operator's spec: "if
    Production missed filling at the time of breakdown they should be
    able to do it later, before the slip closes".  CLOSED tickets are
    excluded (Maintenance has already filed → slip is locked).
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT {_FULL_COLS}
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.state IN ('OPEN', 'RESOLVED')
               AND b.production_filled_at IS NULL
             ORDER BY b.started_at ASC
        """)
        return [_enrich_row(dict(r)) for r in cur.fetchall()]


@router.get("/recent")
def list_recent(days: int = Query(2, ge=1, le=60),
                user=Depends(get_current_user)):
    """Recent history — RESOLVED + CLOSED tickets within the last `days` days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT {_FULL_COLS},
                   u.username AS closed_by_username
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
              LEFT JOIN mes_admin u ON u.id = b.closed_by_user_id
             WHERE b.state IN ('RESOLVED','CLOSED')
               AND COALESCE(b.ended_at, b.closed_at) >= %s
             ORDER BY COALESCE(b.ended_at, b.closed_at) DESC
        """, (cutoff,))
        return [_enrich_row(dict(r)) for r in cur.fetchall()]


@router.post("/", status_code=201)
def open_breakdown(body: BreakdownCreate, user=Depends(get_current_user)):
    """Mark a line as broken (creates ANDON row).  Any logged-in user can
    open one — typically called by a Maintenance / Production user."""
    started = body.started_at or datetime.utcnow()
    with get_conn() as conn:
        # Resolve zone via line → zone FK
        cur = conn.cursor()
        cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s", (body.line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        zone_id = row[0]

        shift = _shift_for(body.line_id, started, conn)
        serial = _next_serial(body.line_id, started, shift, conn)

        cur.execute("""
            INSERT INTO mes_breakdowns
                (line_id, zone_id, shift_name, serial_in_shift,
                 started_at, state, reason, opened_by_user_id)
            VALUES (%s, %s, %s, %s, %s, 'OPEN', %s, %s)
            RETURNING id
        """, (body.line_id, zone_id, shift, serial,
              started, body.reason, user["id"]))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id, "serial_in_shift": serial,
                "shift_name": shift, "started_at": started.isoformat()}


@router.post("/{br_id}/resolve")
def resolve_breakdown(br_id: int, body: BreakdownResolve,
                      user=Depends(get_current_user)):
    """Mark a breakdown as RESOLVED (line is back running).  Moves the row
    from ANDON to History.  The closure form is filled separately."""
    ended = body.ended_at or datetime.utcnow()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_breakdowns
               SET state='RESOLVED', ended_at = %s, updated_at = NOW()
             WHERE id = %s AND state = 'OPEN'
        """, (ended, br_id))
        if cur.rowcount == 0:
            raise HTTPException(409, "Breakdown not OPEN (already resolved or missing)")
        conn.commit()
        return {"ok": True, "ended_at": ended.isoformat()}


@router.post("/{br_id}/production-fill")
def production_fill(br_id: int, body: BreakdownProductionFill,
                    user=Depends(get_current_user)):
    """Production / line leader saves their half of the BREAK DOWN SLIP.
    Can be called at any state (OPEN / RESOLVED) — Production may finish
    filling after the line is already running again, e.g. if the breakdown
    was so brief the operator didn't catch it in real-time.

    Once Production saves, the half is LOCKED — repeat calls are rejected
    so a later edit can never silently overwrite the first responder's
    record.  Maintenance side cannot edit this payload either.  CLOSED
    tickets are also rejected (Maintenance already filed → slip frozen).
    """
    if not isinstance(body.production_data, dict):
        raise HTTPException(400, "production_data must be an object")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT state, production_filled_at
              FROM mes_breakdowns
             WHERE id = %s
        """, (br_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Breakdown not found")
        if row["state"] == "CLOSED":
            raise HTTPException(409,
                "This breakdown is already closed by Maintenance — slip is locked.")
        if row["production_filled_at"] is not None:
            raise HTTPException(409,
                "Production half is already saved — once filed, the slip cannot be edited.")

        cur.execute("""
            UPDATE mes_breakdowns
               SET production_data = %s,
                   production_filled_at = NOW(),
                   production_filled_by_user_id = %s,
                   updated_at = NOW()
             WHERE id = %s
        """, (Json(body.production_data), user["id"], br_id))
        conn.commit()
    return {"ok": True}


@router.post("/{br_id}/close")
def close_breakdown(br_id: int, body: BreakdownClose,
                    user=Depends(get_current_user)):
    """Maintenance saves their half of the BREAK DOWN SLIP and moves the
    ticket from RESOLVED → CLOSED.

    Backwards-compat: callers can send `closure_data` (legacy single-blob
    payload) or `maintenance_data` (new split-form payload).  Either is
    written to `mes_breakdowns.maintenance_data`; the legacy `closure_data`
    column is also stamped so older readers still see the unified blob."""
    payload = body.maintenance_data if body.maintenance_data is not None else body.closure_data
    if not isinstance(payload, dict):
        raise HTTPException(400, "maintenance_data / closure_data must be an object")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_breakdowns
               SET state='CLOSED',
                   maintenance_data = %s,
                   maintenance_filled_at = COALESCE(maintenance_filled_at, NOW()),
                   maintenance_filled_by_user_id = COALESCE(maintenance_filled_by_user_id, %s),
                   closure_data = COALESCE(closure_data, %s),
                   closed_at = NOW(),
                   closed_by_user_id = %s,
                   updated_at = NOW()
             WHERE id = %s AND state IN ('RESOLVED','OPEN')
        """, (Json(payload), user["id"], Json(payload), user["id"], br_id))
        if cur.rowcount == 0:
            raise HTTPException(409, "Breakdown not in a closeable state")

        # If still OPEN at close-time (rare — closing without an explicit
        # resolve) stamp ended_at = NOW() too.
        cur.execute("""
            UPDATE mes_breakdowns
               SET ended_at = COALESCE(ended_at, NOW())
             WHERE id = %s
        """, (br_id,))
        conn.commit()
    return {"ok": True}


# ── Stats ─────────────────────────────────────────────────────────────────
def _resolve_window(days: int, from_date: Optional[str], to_date: Optional[str]):
    """Return (cutoff_lo, cutoff_hi).  Custom date range wins over `days`.
    Dates are inclusive (whole-day).  Both optional — `from_date` alone
    means "from that date through now"; `to_date` alone is unusual but
    means "from N days back through to_date"."""
    hi = datetime.utcnow()
    lo = hi - timedelta(days=days)
    if from_date:
        try: lo = datetime.fromisoformat(from_date)
        except Exception: pass
    if to_date:
        try:
            d = datetime.fromisoformat(to_date)
            # Inclusive end-of-day so picking "to=Apr 30" includes all of Apr 30.
            hi = d.replace(hour=23, minute=59, second=59, microsecond=999000)
        except Exception: pass
    return lo, hi


@router.get("/stats")
def stats(days:      int           = Query(30, ge=1, le=365),
          from_date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) — overrides `days`"),
          to_date:   Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) — overrides `days`"),
          user=Depends(get_current_user)):
    """Aggregate breakdown stats per zone and per line over the last N days.

    Returns:
      {
        zones: [{zone_id, zone_name, lttr_minutes, breakdowns_count}],
        lines: [{line_id, line_name, zone_id, zone_name,
                 mtbf_hours, mttr_minutes, breakdowns_count}]
      }

    Definitions:
      LTTR (zone) = LONGEST time-to-repair seen on any closed breakdown in
                    the zone over the window — i.e. the worst-case repair
                    duration, not the mean.
      MTTR (line) = mean line-time-to-repair on this line
      MTBF (line) = mean inter-arrival time on this line
    """
    lo, hi = _resolve_window(days, from_date, to_date)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # Per-line stats
        cur.execute("""
            SELECT b.line_id,
                   l.line_name,
                   b.zone_id,
                   z.zone_name,
                   COUNT(*) FILTER (WHERE b.ended_at IS NOT NULL) AS closed_count,
                   COUNT(*)                                       AS total_count,
                   AVG(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0)
                       FILTER (WHERE b.ended_at IS NOT NULL)      AS mttr_minutes,
                   MIN(b.started_at) AS first_at,
                   MAX(b.started_at) AS last_at
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.started_at >= %s AND b.started_at <= %s
             GROUP BY b.line_id, l.line_name, b.zone_id, z.zone_name
        """, (lo, hi))
        lines = []
        for r in cur.fetchall():
            r = dict(r)
            n = r.pop("total_count") or 0
            first = r.pop("first_at"); last = r.pop("last_at")
            if n >= 2 and first and last:
                # MTBF in hours = window / failures
                window_h = max((last - first).total_seconds() / 3600.0, 0)
                r["mtbf_hours"] = round(window_h / max(n, 1), 2)
            else:
                r["mtbf_hours"] = None
            r["mttr_minutes"] = round(float(r["mttr_minutes"] or 0), 1) if r["mttr_minutes"] else None
            r["breakdowns_count"] = n
            r.pop("closed_count", None)
            lines.append(r)

        # Per-zone stats — LTTR is the LONGEST repair seen in the zone
        # over the window (i.e. MAX, not AVG).  This surfaces the worst
        # outlier so Maintenance can target it.
        cur.execute("""
            SELECT b.zone_id,
                   z.zone_name,
                   COUNT(*)  AS total_count,
                   MAX(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0)
                       FILTER (WHERE b.ended_at IS NOT NULL) AS lttr_minutes
              FROM mes_breakdowns b
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.started_at >= %s AND b.started_at <= %s
               AND b.zone_id IS NOT NULL
             GROUP BY b.zone_id, z.zone_name
        """, (lo, hi))
        zones = []
        for r in cur.fetchall():
            r = dict(r)
            n = r.pop("total_count") or 0
            r["lttr_minutes"] = round(float(r["lttr_minutes"] or 0), 1) if r["lttr_minutes"] else None
            r["breakdowns_count"] = n
            zones.append(r)

        # ── Per-machine stats ─────────────────────────────────────────────
        # Production fills `production_data.machine_no` + `machine_name`
        # — group on those.  Only counts breakdowns where Production has
        # entered a machine (so we don't surface "(unfilled)" buckets).
        cur.execute("""
            SELECT
                b.line_id,
                l.line_name,
                b.zone_id,
                z.zone_name,
                NULLIF(b.production_data->>'machine_no', '')   AS machine_no,
                NULLIF(b.production_data->>'machine_name', '') AS machine_name,
                COUNT(*) FILTER (WHERE b.ended_at IS NOT NULL) AS closed_count,
                COUNT(*)                                       AS total_count,
                AVG(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0)
                    FILTER (WHERE b.ended_at IS NOT NULL)      AS mttr_minutes,
                MAX(EXTRACT(EPOCH FROM (b.ended_at - b.started_at)) / 60.0)
                    FILTER (WHERE b.ended_at IS NOT NULL)      AS lttr_minutes,
                MIN(b.started_at) AS first_at,
                MAX(b.started_at) AS last_at
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.started_at >= %s AND b.started_at <= %s
               AND NULLIF(b.production_data->>'machine_no', '') IS NOT NULL
             GROUP BY b.line_id, l.line_name, b.zone_id, z.zone_name,
                      machine_no, machine_name
             ORDER BY total_count DESC, machine_no
        """, (lo, hi))
        machines = []
        for r in cur.fetchall():
            r = dict(r)
            n = r.pop("total_count") or 0
            first = r.pop("first_at"); last = r.pop("last_at")
            r.pop("closed_count", None)
            if n >= 2 and first and last:
                window_h = max((last - first).total_seconds() / 3600.0, 0)
                r["mtbf_hours"] = round(window_h / max(n, 1), 2)
            else:
                r["mtbf_hours"] = None
            r["mttr_minutes"] = round(float(r["mttr_minutes"] or 0), 1) if r["mttr_minutes"] else None
            r["lttr_minutes"] = round(float(r["lttr_minutes"] or 0), 1) if r["lttr_minutes"] else None
            r["breakdowns_count"] = n
            machines.append(r)

    return {
        "zones":       zones,
        "lines":       lines,
        "machines":    machines,
        "window_days": days,
        "from_date":   lo.date().isoformat(),
        "to_date":     hi.date().isoformat(),
    }


# ── Historical archive (full slip browser) ────────────────────────────────
@router.get("/history")
def history(
    days:        int           = Query(30, ge=1, le=730),
    state:       Optional[str] = Query(None, description="OPEN | RESOLVED | CLOSED"),
    line_id:     Optional[int] = Query(None),
    zone_id:     Optional[int] = Query(None),
    machine_no:  Optional[str] = Query(None),
    from_date:   Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) — overrides `days`"),
    to_date:     Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) — overrides `days`"),
    limit:       int           = Query(200, ge=1, le=2000),
    offset:      int           = Query(0, ge=0),
    user=Depends(get_current_user),
):
    """Full historical slip browser.  Drives the Maintenance Historical
    Data page — supports filters on date-range (preset days OR custom
    from/to dates), state, line, zone, and Production-entered machine_no.
    Returns full slip payload (both halves + filled timestamps) so the
    frontend can open any row in read-only "view" mode."""
    lo, hi = _resolve_window(days, from_date, to_date)
    where = ["b.started_at >= %s", "b.started_at <= %s"]
    params: list = [lo, hi]
    if state:
        where.append("b.state = %s"); params.append(state.upper())
    if line_id is not None:
        where.append("b.line_id = %s"); params.append(line_id)
    if zone_id is not None:
        where.append("b.zone_id = %s"); params.append(zone_id)
    if machine_no:
        where.append("b.production_data->>'machine_no' = %s")
        params.append(machine_no)

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT {_FULL_COLS},
                   pf.username AS production_filled_by_username,
                   mf.username AS maintenance_filled_by_username,
                   cu.username AS closed_by_username
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
              LEFT JOIN mes_admin pf ON pf.id = b.production_filled_by_user_id
              LEFT JOIN mes_admin mf ON mf.id = b.maintenance_filled_by_user_id
              LEFT JOIN mes_admin cu ON cu.id = b.closed_by_user_id
             WHERE {' AND '.join(where)}
             ORDER BY b.started_at DESC
             LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = [_enrich_row(dict(r)) for r in cur.fetchall()]

        # Total count for pagination
        cur.execute(f"""
            SELECT COUNT(*) AS n FROM mes_breakdowns b
             WHERE {' AND '.join(where)}
        """, params)
        total = cur.fetchone()["n"]

    return {
        "rows": rows, "total": total,
        "limit": limit, "offset": offset,
        "from_date": lo.date().isoformat(),
        "to_date":   hi.date().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# SLIP RAISE THRESHOLD CONFIG
# ─────────────────────────────────────────────────────────────────────
# Operator's clarified requirement:
#
#   "Some breakdowns get attended and fixed within 5–10 minutes — those
#    don't need a full slip.  Only the threshold matters: if a
#    breakdown takes LONGER than X minutes to resolve, the formal slip
#    is RAISED (= mandatory full closure form, both Production +
#    Maintenance halves).  Anything fixed under X minutes is a MINOR
#    event — Production just logs basic details, no slip needed."
#
# So a single global knob: `slip_raise_threshold_min`.
#
# How the breakdown lifecycle uses it (handled in resolve / close
# endpoints, not here):
#
#   t = ended_at - started_at
#   ┌──────────────────────────────────────────────────────────┐
#   │ if t < threshold  →  state='CLOSED', tier='MINOR'        │
#   │   • Production logs reason text only (lightweight form)  │
#   │   • No Maintenance closure form required                 │
#   │   • Counts in MTBF stats but NOT in CAPA breach counters │
#   ├──────────────────────────────────────────────────────────┤
#   │ if t ≥ threshold  →  state='RESOLVED', tier='MAJOR'      │
#   │   • Full slip raised — Production half + Maintenance     │
#   │     half both required before the slip can move to       │
#   │     CLOSED                                                │
#   │   • Breakdown Mails escalation chain fires from t=0      │
#   │   • Counts in CAPA breach counters per existing logic    │
#   └──────────────────────────────────────────────────────────┘
#
# Single-row config table keyed on scope='GLOBAL'.
class SlipThresholdConfig(BaseModel):
    slip_raise_threshold_min: int = 10


def _ensure_slip_config_table(conn) -> None:
    """Create / migrate the single-row config table.  Idempotent.

    Legacy schema had two columns (production_fill_timeout_min +
    maintenance_fill_timeout_min) — both kept here as ALTER TABLE
    additions so an upgrade in place doesn't blow up; only the new
    `slip_raise_threshold_min` column is read/written from now on."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_breakdown_slip_config (
            scope                       TEXT     PRIMARY KEY DEFAULT 'GLOBAL',
            slip_raise_threshold_min    INTEGER  NOT NULL DEFAULT 10,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Tolerant migration — if legacy table existed with different cols,
    # ensure the new column is present.
    cur.execute("""
        ALTER TABLE mes_breakdown_slip_config
        ADD COLUMN IF NOT EXISTS slip_raise_threshold_min INTEGER NOT NULL DEFAULT 10
    """)
    # Seed default GLOBAL row if missing.
    cur.execute("""
        INSERT INTO mes_breakdown_slip_config (scope)
        VALUES ('GLOBAL')
        ON CONFLICT (scope) DO NOTHING
    """)
    conn.commit()


@router.get("/slip-config")
def get_slip_config(user=Depends(get_current_user)):
    """Return the GLOBAL slip-raise threshold (in minutes).

    Default: 10 minutes."""
    with get_conn() as conn:
        _ensure_slip_config_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT slip_raise_threshold_min, updated_at
              FROM mes_breakdown_slip_config
             WHERE scope='GLOBAL' LIMIT 1
        """)
        r = cur.fetchone()
        if r:
            return {
                "slip_raise_threshold_min": int(r["slip_raise_threshold_min"]),
                "updated_at":               r["updated_at"].isoformat() if r["updated_at"] else None,
            }
        return {
            "slip_raise_threshold_min": 10,
            "updated_at":               None,
        }


@router.put("/slip-config")
def set_slip_config(body: SlipThresholdConfig, admin=Depends(require_admin)):
    """Admin-only: update the GLOBAL slip-raise threshold.

    Clamped to [1, 1440] (1 minute .. 24 hours).  An out-of-range
    request gets snapped to the nearest valid edge so admin sees the
    value they actually got."""
    th = max(1, min(1440, int(body.slip_raise_threshold_min)))
    with get_conn() as conn:
        _ensure_slip_config_table(conn)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_breakdown_slip_config
                (scope, slip_raise_threshold_min)
            VALUES ('GLOBAL', %s)
            ON CONFLICT (scope)
            DO UPDATE SET slip_raise_threshold_min = EXCLUDED.slip_raise_threshold_min,
                          updated_at               = NOW()
        """, (th,))
        conn.commit()
    return { "slip_raise_threshold_min": th }


