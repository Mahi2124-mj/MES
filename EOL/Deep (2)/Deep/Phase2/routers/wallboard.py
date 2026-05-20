"""
routers/wallboard.py
====================
65" portrait wallboard endpoints — one per LEFT and RIGHT dashboard
that sits on the shop-floor TV wall (per ASSY LINE-2 reference image).

Two physical screens per line:
  LEFT   — multi-machine cycle-time graphs stacked, with hover-panel
           showing per-machine hourly slot counts.
  RIGHT  — line summary (target/actual/KPIs from final machine) +
           hourly slots + daily/weekly/monthly Plan vs Actual +
           realtime cycle-time histogram (0.1s buckets, monthly) +
           per-model monthly production count.

Endpoints (all GET, all auth-optional so the wallboard TV can poll
anonymously after first boot):
  /api/lines/{id}/wallboard-cycles    — full-shift CT data per sub-machine
  /api/lines/{id}/wallboard-summary   — KPI tiles + per-machine hourly slots
  /api/lines/{id}/wallboard-history   — daily / weekly / monthly Plan vs Actual
  /api/lines/{id}/ct-histogram        — month-long CT density (0.1s buckets)
  /api/lines/{id}/model-counts        — per-model production count, monthly
"""

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user_optional
from database import get_conn, dict_cursor


router = APIRouter(prefix="/api/lines", tags=["wallboard"])


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════
def _resolve_line(line_id: int, conn):
    """Return (db_table_name, current_shift_row_id, ideal_ct) or raise 404."""
    cur = dict_cursor(conn)
    cur.execute(
        "SELECT id, db_table_name, current_shift_row_id, ideal_cycle_time "
        "FROM mes_lines WHERE id = %s",
        (line_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Line not found")
    return row


def _current_shift_row(table: str, row_id: Optional[int], conn):
    """Return the live shift row dict (or None if collector hasn't pinned one)."""
    if not row_id:
        return None
    cur = dict_cursor(conn)
    cur.execute(
        f"SELECT * FROM {table} WHERE id = %s AND is_shift_completed = false",
        (row_id,),
    )
    return cur.fetchone()


# ══════════════════════════════════════════════════════════════════
# 1. LEFT dashboard — multi-machine CT (full shift)
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/wallboard-cycles")
def wallboard_cycles(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """For each sub-machine on this line, return EVERY cycle of the
    currently-running shift.  Frontend stacks these into N small line
    charts (1 per machine).

    Shape:
    [
        {
          "sub_id":         12,
          "machine_name":   "Semi-Auto",
          "machine_seq":    3,
          "ideal_ct":       15.0,
          "cycles": [
              {"cycle_seq": 1, "ts": "2026-05-18T08:31:12+05:30",
               "ct": 14.32, "is_ng": false},
              ...
          ]
        },
        ...
    ]
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        shift_row = _current_shift_row(
            line["db_table_name"], line["current_shift_row_id"], conn)
        cur = dict_cursor(conn)

        # Main PLC for this line so we can find its sub-machines
        cur.execute(
            "SELECT id FROM mes_plc_configs "
            "WHERE line_id = %s AND parent_plc_id IS NULL LIMIT 1",
            (line_id,))
        main_row = cur.fetchone()
        if not main_row:
            return []
        main_plc_id = main_row["id"]

        # Pull sub-machines + each one's cycles in ONE round-trip via LATERAL.
        # Each sub-machine's cycles are scoped to today + current shift so
        # the dashboard shows ONLY the live shift.
        today  = date.today()
        shift  = shift_row.get("shift_name") if shift_row else None
        if not shift:
            return []          # no shift active → empty wallboard

        # mes_submachine_ct_log doesn't have is_ng (sub-machines don't
        # report NG via their own bit; they're upstream stations).  Just
        # build cycle dots with ct + cycle_seq; the LEFT dashboard's
        # color coding uses "ct > ideal_ct" as the spike threshold.
        cur.execute("""
            SELECT p.id                  AS sub_id,
                   p.machine_name,
                   p.machine_seq,
                   p.ideal_cycle_time    AS ideal_ct,
                   COALESCE(jsonb_agg(
                       jsonb_build_object(
                           'cycle_seq', l.cycle_seq,
                           'ts',        l.ts_end,
                           'ct',        l.ct_seconds
                       ) ORDER BY l.cycle_seq
                   ) FILTER (WHERE l.id IS NOT NULL), '[]'::jsonb) AS cycles
            FROM mes_plc_configs p
            LEFT JOIN mes_submachine_ct_log l
                   ON l.sub_plc_id  = p.id
                  AND l.record_date = %s
                  AND l.shift_name  = %s
            WHERE p.parent_plc_id = %s
            GROUP BY p.id
            ORDER BY COALESCE(p.machine_seq, 9999), p.id
        """, (today, shift, main_plc_id))

        out = []
        for r in cur.fetchall():
            d = dict(r)
            d["ideal_ct"] = float(d["ideal_ct"]) if d["ideal_ct"] is not None else None
            # jsonb_agg already gives us the cycles list as JSON; psycopg2
            # parses it to a Python list of dicts automatically.
            out.append(d)

        # ── MAIN-LINE CT chart ────────────────────────────────────────
        # 2026-05-18 — Operator wants the final-machine (line aggregate)
        # CT graph as the FIRST row above all sub-machines.  It comes
        # from the line's own *_ct_log table (NOT mes_submachine_ct_log)
        # because the main line counts L108 from the head PLC.
        main_table = line["db_table_name"] + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS t", (main_table,))
        if cur.fetchone()["t"]:
            cur.execute(f"""
                SELECT cycle_seq, ts, ct_value AS ct,
                       COALESCE(is_ng, FALSE) AS is_ng
                FROM {main_table}
                WHERE record_date = %s AND shift_name = %s
                ORDER BY cycle_seq
            """, (today, shift))
            main_cycles = [
                {"cycle_seq": r["cycle_seq"],
                 "ts":        r["ts"].isoformat() if r["ts"] else None,
                 "ct":        float(r["ct"]) if r["ct"] is not None else 0.0,
                 "is_ng":     bool(r["is_ng"])}
                for r in cur.fetchall()
            ]
        else:
            main_cycles = []

        # 2026-05-18-r13 — Renamed per operator spec ("main machine
        # koi nhi h, final inspection h").  This row IS the line
        # aggregate, but its semantic name on the floor is just
        # "Final Inspection" — there's no separate machine, the final
        # inspection station IS what counts the line's output.
        main_row = {
            "sub_id":       0,                       # 0 = main line marker
            "machine_name": "Final Inspection",
            "machine_seq":  0,
            "ideal_ct":     float(line["ideal_cycle_time"] or 15.0),
            "cycles":       main_cycles,
            "is_main":      True,
        }

        return {
            "shift_name":   shift,
            "record_date":  str(today),
            "main":         main_row,
            "machines":     out,
        }


# ══════════════════════════════════════════════════════════════════
# 2. RIGHT dashboard — line summary + per-machine hourly slots
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/wallboard-summary")
def wallboard_summary(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """Return the headline KPI tiles + per-machine hourly slot counts.

    KPI tiles come from the FINAL machine of the line (the line's own
    shift row, which is the last station's output → matches the wall
    display's "ASSY LINE-2 → final inspection" semantics).

    Hourly slots: for each sub-machine, build a [slot_label, count]
    pair list that mirrors the line's slot config.
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"]
        cur = dict_cursor(conn)

        # 2026-05-18-r14 — Pull the human-readable line name + model
        # number too so the wallboard header chip can show "YNC-SS"
        # instead of "Line 2" (operator: "meri line ka naam glt h").
        cur.execute("""SELECT line_name FROM mes_lines WHERE id = %s""",
                    (line_id,))
        _row = cur.fetchone()
        line_name = (_row and _row.get("line_name")) or f"Line {line_id}"

        shift_row = _current_shift_row(
            table, line["current_shift_row_id"], conn)
        if not shift_row:
            return {"shift_row": None, "machines_hourly": [],
                    "kpi": {"line_name": line_name}}

        shift_name  = shift_row["shift_name"]
        record_date = shift_row["record_date"]

        # ── Pull hourly slot config (label + start time) ──────────────
        # mes_hourly_slots schema: start_time, end_time, plan_pieces
        cur.execute("""
            SELECT slot_label, start_time, end_time, plan_pieces, slot_order,
                   db_column_prefix
            FROM mes_hourly_slots
            WHERE line_id = %s AND shift_name = %s
            ORDER BY slot_order, start_time
        """, (line_id, shift_name))
        slots = [dict(r) for r in cur.fetchall()]

        # ── For each sub-machine, count cycles per slot ───────────────
        cur.execute("""
            SELECT id, machine_name, machine_seq
            FROM mes_plc_configs
            WHERE parent_plc_id = (
                SELECT id FROM mes_plc_configs
                WHERE line_id = %s AND parent_plc_id IS NULL LIMIT 1
            )
            ORDER BY COALESCE(machine_seq, 9999), id
        """, (line_id,))
        subs = [dict(r) for r in cur.fetchall()]

        machines_hourly = []
        for s in subs:
            row = {"sub_id": s["id"], "machine_name": s["machine_name"],
                   "machine_seq": s["machine_seq"], "slots": []}
            for slot in slots:
                cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM mes_submachine_ct_log
                    WHERE sub_plc_id  = %s
                      AND record_date = %s
                      AND shift_name  = %s
                      AND ts_end::time >= %s
                      AND ts_end::time <  %s
                """, (s["id"], record_date, shift_name,
                      slot["start_time"], slot["end_time"]))
                cnt = cur.fetchone()["cnt"] or 0
                row["slots"].append({
                    "label": slot["slot_label"],
                    "start": str(slot["start_time"])[:5],
                    "end":   str(slot["end_time"])[:5],
                    "plan":  slot["plan_pieces"],
                    "count": cnt,
                })
            machines_hourly.append(row)

        # ── KPI tiles from final machine (shift row already pulled) ──
        # Provide a clean serialisable subset — frontend can extract
        # whatever it wants from this.
        kpi = {
            "line_name":           line_name,        # r14 — added
            "shift_name":          shift_name,
            "record_date":         str(record_date),
            "operating_status":    shift_row.get("operating_status"),
            "shift_plan":          shift_row.get("shift_plan"),
            "shift_plan_completed":shift_row.get("shift_plan_completed"),
            "shift_plan_remaining":shift_row.get("shift_plan_remaining"),
            "ok_count":            shift_row.get("ok_count"),
            "ng_count":            shift_row.get("ng_count"),
            "current_model_name":  shift_row.get("current_model_name"),
            "current_model_number":shift_row.get("current_model"),
            "overall_oee":         shift_row.get("overall_oee"),
            "availability":        shift_row.get("availability"),
            "performance":         shift_row.get("performance"),
            "quality_oee":         shift_row.get("quality_oee"),
            "cycle_time_actual":   shift_row.get("cycle_time_actual"),
            "cycle_time_plan":     shift_row.get("cycle_time_plan"),
        }

        # ── MAIN LINE per-slot breakdown (from shift row columns) ────
        # The collector writes hour_HHMM_HHMM_plan / _actual / _ok / _ng
        # for each slot directly to the shift-row.  Read them by the
        # slot's db_column_prefix.
        line_hourly = []
        for slot in slots:
            p = slot.get("db_column_prefix") or ""
            plan   = shift_row.get(f"{p}_plan")   if p else None
            actual = shift_row.get(f"{p}_actual") if p else None
            ok     = shift_row.get(f"{p}_ok")     if p else None
            ng     = shift_row.get(f"{p}_ng")     if p else None
            line_hourly.append({
                "label":  slot["slot_label"],
                "start":  str(slot["start_time"])[:5],
                "end":    str(slot["end_time"])[:5],
                "plan":   int(plan or 0),
                "actual": int(actual or 0),
                "ok":     int(ok or 0),
                "ng":     int(ng or 0),
            })

        return {
            "kpi":             kpi,
            "machines_hourly": machines_hourly,
            "line_hourly":     line_hourly,
        }


# ══════════════════════════════════════════════════════════════════
# 3. Plan vs Actual — daily / weekly / monthly
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/wallboard-history")
def wallboard_history(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """Return three bar-chart series for the RIGHT dashboard's body:
       - daily  : last 30 days  (one bar per day)
       - weekly : last 12 weeks (one bar per ISO week)
       - monthly: last 12 months
    Each bar carries `plan`, `actual`, `date` (or week/month label).
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"]
        cur = dict_cursor(conn)

        # Sum across BOTH shifts each day so totals match what the
        # operator sees on the dashboard's main number.
        cur.execute(f"""
            SELECT record_date,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY record_date
            ORDER BY record_date
        """)
        daily = [{"date": str(r["record_date"]),
                  "plan": int(r["plan"] or 0),
                  "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        cur.execute(f"""
            SELECT date_trunc('week', record_date)::date AS wk,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '12 weeks'
            GROUP BY wk
            ORDER BY wk
        """)
        weekly = [{"week": str(r["wk"]),
                   "plan": int(r["plan"] or 0),
                   "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        cur.execute(f"""
            SELECT date_trunc('month', record_date)::date AS mo,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY mo
            ORDER BY mo
        """)
        monthly = [{"month": str(r["mo"]),
                    "plan": int(r["plan"] or 0),
                    "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        return {"daily": daily, "weekly": weekly, "monthly": monthly}


# ══════════════════════════════════════════════════════════════════
# 4. Cycle-time histogram — 0.1s buckets, current month
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/ct-histogram")
def ct_histogram(
    line_id: int,
    days: int = Query(30, ge=1, le=120,
                      description="Window in days (default 30 = current month)"),
    user=Depends(get_current_user_optional),
):
    """Return cycle-time density buckets for `days` days ending today.
    Bucket width = 0.1 s (e.g. 14.3, 14.4, 14.5 ...).

    Output:
    {
      "total_cycles": 13042,
      "peak_bucket":  15.0,
      "peak_count":   1842,
      "buckets":      [{"ct": 14.0, "count": 23}, ...]
    }
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"] + "_ct_log"
        cur = dict_cursor(conn)

        # Make sure the line's ct_log table exists; otherwise return empty.
        cur.execute("SELECT to_regclass(%s) AS t", (table,))
        if not cur.fetchone()["t"]:
            return {"total_cycles": 0, "peak_bucket": None,
                    "peak_count": 0, "buckets": []}

        # Bucket via floor(ct * 10) / 10.  We clamp to a sane operating
        # range (0.5 → 60 s) so a single 600 s outlier doesn't stretch
        # the X-axis flat.
        cur.execute(f"""
            SELECT ROUND((ct_value * 10)::numeric) / 10.0 AS bucket,
                   COUNT(*) AS cnt
            FROM {table}
            WHERE record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
              AND ct_value > 0
              AND ct_value < 60
            GROUP BY bucket
            ORDER BY bucket
        """, (days,))
        buckets = []
        peak_bucket, peak_count = None, 0
        total = 0
        for r in cur.fetchall():
            ct  = float(r["bucket"])
            cnt = int(r["cnt"])
            buckets.append({"ct": ct, "count": cnt})
            total += cnt
            if cnt > peak_count:
                peak_count  = cnt
                peak_bucket = ct
        return {
            "total_cycles": total,
            "peak_bucket":  peak_bucket,
            "peak_count":   peak_count,
            "buckets":      buckets,
            "window_days":  days,
        }


# ══════════════════════════════════════════════════════════════════
# 5. Per-model production count — monthly
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/model-counts")
def model_counts(
    line_id: int,
    days: int = Query(30, ge=1, le=120,
                      description="Window in days (default 30)"),
    user=Depends(get_current_user_optional),
):
    """Return per-model production count for the last `days` days.

    The ct_log table doesn't carry model_name directly, so we infer
    via the shift row's `current_model_name` recorded at cycle time —
    fall back to counting EVERY cycle under "—" when no model info
    is available so totals still match the main dashboard count.
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table   = line["db_table_name"]
        ct_log  = table + "_ct_log"
        cur = dict_cursor(conn)

        # Quick existence check
        cur.execute("SELECT to_regclass(%s) AS t", (ct_log,))
        if not cur.fetchone()["t"]:
            return []

        # We use the FINAL machine's per-shift row to learn what model
        # was running on a given date+shift, then attribute its cycles
        # to that model.  This is a best-effort grouping; for lines
        # that don't track model_name we collapse everything under
        # "Unknown".
        cur.execute(f"""
            WITH model_window AS (
                SELECT record_date,
                       shift_name,
                       MAX(current_model_name) AS model
                  FROM {table}
                 WHERE record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
                 GROUP BY record_date, shift_name
            )
            SELECT COALESCE(m.model, 'Unknown') AS model_name,
                   COUNT(*)                    AS cnt
            FROM {ct_log} l
            LEFT JOIN model_window m
                   ON m.record_date = l.record_date
                  AND m.shift_name  = l.shift_name
            WHERE l.record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
              AND l.ct_value > 0
            GROUP BY model_name
            ORDER BY cnt DESC
        """, (days, days))
        return [{"model_name": r["model_name"], "count": int(r["cnt"])}
                for r in cur.fetchall()]
