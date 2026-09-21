"""
routers/maintenance_kpi.py
==========================
Maintenance KPI dashboard — auto-computed from mes_breakdowns over a
selectable period (today / last 7d / 30d / custom range).  Compares each
KPI to its admin-set target and returns a pass/fail flag.

KPIs computed
-------------
  mtbf_hours          — mean time between failures (hours)
                          = window_hours / breakdowns_count       (higher is better)
  mttr_minutes        — mean time to repair (minutes)
                          = AVG(ended_at - started_at)            (lower is better)
  availability_pct    — Availability %
                          = MTBF / (MTBF + MTTR_h) × 100          (higher is better)
  breakdowns_count    — total breakdowns in window                 (lower is better)
  total_downtime_min  — sum of (ended_at - started_at) minutes     (lower is better)
  pending_closures    — RESOLVED tickets waiting for closure form  (lower is better)

Endpoints
---------
GET    /api/maintenance-kpi/                  Compute KPIs (with target compare)
GET    /api/maintenance-kpi/export.csv        Same data as CSV download
GET    /api/maintenance-kpi/targets           List all targets
POST   /api/maintenance-kpi/targets           Create / upsert target (admin)
PUT    /api/maintenance-kpi/targets/{id}      Update target (admin)
DELETE /api/maintenance-kpi/targets/{id}      Delete target (admin)
"""
import csv
import io
from datetime import datetime, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/maintenance-kpi", tags=["maintenance-kpi"])


# ── KPI metadata ─────────────────────────────────────────────────────
KPI_DEFS = [
    # (key, label, unit, direction, default_target)
    ("mtbf_hours",         "MTBF",                "hours",   "higher", 100.0),
    ("mttr_minutes",       "MTTR",                "minutes", "lower",   30.0),
    ("availability_pct",   "Availability",        "%",       "higher",  95.0),
    ("breakdowns_count",   "Total breakdowns",    "count",   "lower",   10.0),
    ("total_downtime_min", "Total downtime",      "minutes", "lower",  120.0),
    ("pending_closures",   "Pending closures",    "count",   "lower",    0.0),
]


# ── Models ───────────────────────────────────────────────────────────
class TargetUpsert(BaseModel):
    kpi_key:      str
    line_id:      Optional[int] = None
    target_value: float
    unit:         Optional[str] = None
    direction:    str = "higher"
    is_active:    bool = True


# ── Helpers ──────────────────────────────────────────────────────────
def _resolve_window(period: Optional[str],
                    date_from: Optional[str],
                    date_to:   Optional[str]) -> tuple[datetime, datetime, str]:
    """Map (period | from-to) → (start_dt, end_dt, label).
    period can be: today / yesterday / 7d / 30d / 90d / custom"""
    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())

    if period == "custom":
        if not date_from or not date_to:
            raise HTTPException(400, "custom period requires from + to (YYYY-MM-DD)")
        try:
            f = datetime.strptime(date_from, "%Y-%m-%d")
            t = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            raise HTTPException(400, "from/to must be YYYY-MM-DD")
        return f, t, f"{date_from} → {date_to}"
    if period == "today":
        return today_start, now, "Today"
    if period == "yesterday":
        y = today_start - timedelta(days=1)
        return y, today_start, "Yesterday"
    if period == "30d":
        return now - timedelta(days=30), now, "Last 30 days"
    if period == "90d":
        return now - timedelta(days=90), now, "Last 90 days"
    # default = 7d
    return now - timedelta(days=7), now, "Last 7 days"


def _load_targets(conn, line_id: Optional[int]) -> dict:
    """Return {kpi_key: target_dict} — per-line override if exists, else
    plant-wide row, else hard-coded default from KPI_DEFS."""
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT kpi_key, line_id, target_value, unit, direction, is_active
          FROM mes_kpi_targets
         WHERE is_active = TRUE
           AND (line_id IS NULL OR line_id = %s)
    """, (line_id,))
    rows = cur.fetchall()

    # Per-line override wins over plant-wide; build a two-pass map.
    per_line, global_ = {}, {}
    for r in rows:
        (per_line if r["line_id"] is not None else global_)[r["kpi_key"]] = r

    out = {}
    for key, label, unit, direction, default in KPI_DEFS:
        if key in per_line:
            out[key] = per_line[key]
        elif key in global_:
            out[key] = global_[key]
        else:
            out[key] = {"kpi_key": key, "line_id": None,
                        "target_value": default, "unit": unit,
                        "direction": direction, "is_active": True}
    return out


def _compute(conn, start: datetime, end: datetime, line_id: Optional[int]) -> dict:
    """Aggregate raw figures from mes_breakdowns over [start, end)."""
    cur = dict_cursor(conn)
    where = "started_at >= %s AND started_at < %s"
    params: list = [start, end]
    if line_id is not None:
        where += " AND line_id = %s"
        params.append(line_id)

    cur.execute(f"""
        SELECT COUNT(*)                                                  AS bd_count,
               SUM(EXTRACT(EPOCH FROM (ended_at - started_at)))           AS total_down_sec,
               AVG(EXTRACT(EPOCH FROM (ended_at - started_at)))           AS avg_repair_sec,
               COUNT(*) FILTER (WHERE state = 'RESOLVED')                 AS pending_closures
          FROM mes_breakdowns
         WHERE {where}
    """, params)
    row = cur.fetchone() or {}

    bd_count        = int(row.get("bd_count") or 0)
    total_down_sec  = float(row.get("total_down_sec") or 0)
    avg_repair_sec  = float(row.get("avg_repair_sec") or 0)
    pending         = int(row.get("pending_closures") or 0)

    window_hours = max((end - start).total_seconds() / 3600.0, 0.001)

    if bd_count > 0:
        # Uptime = window - total downtime, divided across failures
        uptime_hours = max(window_hours - total_down_sec / 3600.0, 0)
        mtbf_hours   = round(uptime_hours / bd_count, 2)
        mttr_minutes = round(avg_repair_sec / 60.0, 2)
    else:
        mtbf_hours   = round(window_hours, 2)  # zero failures → MTBF = full window
        mttr_minutes = 0.0

    if mtbf_hours > 0 or mttr_minutes > 0:
        denom = mtbf_hours + (mttr_minutes / 60.0)
        availability_pct = round((mtbf_hours / denom) * 100.0, 2) if denom > 0 else 100.0
    else:
        availability_pct = 100.0

    return {
        "mtbf_hours":         mtbf_hours,
        "mttr_minutes":       mttr_minutes,
        "availability_pct":   availability_pct,
        "breakdowns_count":   bd_count,
        "total_downtime_min": round(total_down_sec / 60.0, 1),
        "pending_closures":   pending,
    }


def _verdict(value: float, target: float, direction: str) -> str:
    """'pass' / 'fail' / 'na' depending on direction."""
    if value is None or target is None:
        return "na"
    if direction == "higher":
        return "pass" if value >= target else "fail"
    if direction == "lower":
        return "pass" if value <= target else "fail"
    return "na"


def _build_payload(conn, start: datetime, end: datetime,
                   line_id: Optional[int], window_label: str) -> dict:
    raw     = _compute(conn, start, end, line_id)
    targets = _load_targets(conn, line_id)

    cards = []
    for key, label, unit, direction, _default in KPI_DEFS:
        t   = targets[key]
        val = raw[key]
        cards.append({
            "kpi_key":   key,
            "label":     label,
            "value":     val,
            "unit":      t.get("unit") or unit,
            "target":    t["target_value"],
            "direction": t["direction"],
            "verdict":   _verdict(val, t["target_value"], t["direction"]),
        })

    # Resolve line / zone names so the UI can echo them in the export.
    line_name = None
    if line_id:
        cur = dict_cursor(conn)
        cur.execute("SELECT line_name FROM mes_lines WHERE id = %s", (line_id,))
        r = cur.fetchone()
        line_name = r["line_name"] if r else None

    return {
        "window":   {"label": window_label,
                     "from":  start.isoformat(),
                     "to":    end.isoformat()},
        "line_id":  line_id,
        "line_name": line_name,
        "kpis":     cards,
    }


# ── Endpoints ────────────────────────────────────────────────────────
@router.get("/")
def get_kpis(period:    Optional[str] = Query("7d"),
             date_from: Optional[str] = Query(None),
             date_to:   Optional[str] = Query(None),
             line_id:   Optional[int] = Query(None),
             user=Depends(get_current_user)):
    """Compute KPIs for the requested window + line, with target verdicts."""
    start, end, label = _resolve_window(period, date_from, date_to)
    with get_conn() as conn:
        return _build_payload(conn, start, end, line_id, label)


@router.get("/export.csv")
def export_csv(period:    Optional[str] = Query("7d"),
               date_from: Optional[str] = Query(None),
               date_to:   Optional[str] = Query(None),
               line_id:   Optional[int] = Query(None),
               user=Depends(get_current_user)):
    """Same payload as /  but as a flat CSV file the user can keep."""
    start, end, label = _resolve_window(period, date_from, date_to)
    with get_conn() as conn:
        payload = _build_payload(conn, start, end, line_id, label)

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["Maintenance KPI Report"])
    w.writerow(["Period",  payload["window"]["label"],
                "From", payload["window"]["from"],
                "To",   payload["window"]["to"]])
    w.writerow(["Line",    payload["line_name"] or "(All lines)"])
    w.writerow([])
    w.writerow(["KPI", "Value", "Unit", "Target", "Direction", "Verdict"])
    for c in payload["kpis"]:
        w.writerow([c["label"], c["value"], c["unit"], c["target"],
                    c["direction"], c["verdict"].upper()])

    fname = f"maintenance_kpi_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Targets CRUD (admin) ─────────────────────────────────────────────
@router.get("/targets")
def list_targets(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT t.id, t.kpi_key, t.line_id, t.target_value, t.unit,
                   t.direction, t.is_active, t.created_at, t.updated_at,
                   l.line_name
              FROM mes_kpi_targets t
              LEFT JOIN mes_lines l ON l.id = t.line_id
             ORDER BY t.kpi_key, t.line_id NULLS FIRST
        """)
        return cur.fetchall()


@router.post("/targets", status_code=201)
def create_target(body: TargetUpsert, admin=Depends(require_admin)):
    if body.kpi_key not in {k for k, *_ in KPI_DEFS}:
        raise HTTPException(400, f"Unknown kpi_key. Allowed: {[k for k, *_ in KPI_DEFS]}")
    if body.direction not in ("higher", "lower"):
        raise HTTPException(400, "direction must be 'higher' or 'lower'")
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO mes_kpi_targets
                    (kpi_key, line_id, target_value, unit, direction, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (kpi_key, line_id) DO UPDATE
                    SET target_value = EXCLUDED.target_value,
                        unit         = EXCLUDED.unit,
                        direction    = EXCLUDED.direction,
                        is_active    = EXCLUDED.is_active,
                        updated_at   = NOW()
                RETURNING id
            """, (body.kpi_key, body.line_id, body.target_value,
                  body.unit, body.direction, body.is_active))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Save failed: {e}")
    return {"id": new_id}


@router.put("/targets/{target_id}")
def update_target(target_id: int, body: TargetUpsert,
                  admin=Depends(require_admin)):
    if body.direction not in ("higher", "lower"):
        raise HTTPException(400, "direction must be 'higher' or 'lower'")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_kpi_targets
               SET kpi_key=%s, line_id=%s, target_value=%s, unit=%s,
                   direction=%s, is_active=%s, updated_at=NOW()
             WHERE id=%s
        """, (body.kpi_key, body.line_id, body.target_value, body.unit,
              body.direction, body.is_active, target_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Target not found")
        conn.commit()
    return {"ok": True}


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_kpi_targets WHERE id=%s", (target_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Target not found")
        conn.commit()
    return {"ok": True}
