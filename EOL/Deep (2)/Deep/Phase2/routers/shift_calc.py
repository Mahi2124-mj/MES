"""
routers/shift_calc.py
=====================
Production-department shift / OT calculator.

Two modes:
  forward  — "I need to make N parts. How many shifts + OT do I need?"
  reverse  — "I have N days. How many parts can I produce?"

Rate source options:
  historical   — avg parts/shift from last 7-day production
  theoretical  — line's planned capacity (working_minutes / ideal_ct)

Quality factor: configurable %.  Target is divided by quality% so the
NG/rework loss is built in.  e.g. target=5000 OK, quality=98% →
effective_target = 5102 parts must come off the line.

OT capacity is read from mes_shift_configs.ot_start_time / ot_end_time —
operator already set those per-line.  If allow_ot=False, OT slots are
skipped and only normal shift capacity counts.

Output includes:
  plain      — "3 shifts + 1.5 hrs OT" summary string
  schedule   — day-wise [{day, shift, ot_used, parts}] breakdown
  bottleneck — note if target is unreachable in any reasonable window
"""
from __future__ import annotations

from datetime import datetime, date, timedelta, time as dt_time
from math import ceil
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user

router = APIRouter(prefix="/api/shift-calc", tags=["shift-calc"])


# ════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════

def _ot_minutes(scfg: dict) -> int:
    """Return the OT window length in minutes for a shift config row.
    Empty / zero if OT isn't configured for this shift."""
    s = scfg.get("ot_start_time")
    e = scfg.get("ot_end_time")
    if not s or not e:
        return 0
    s_dt = datetime.combine(date.today(), s)
    e_dt = datetime.combine(date.today(), e)
    if e_dt <= s_dt:
        # Crosses midnight
        e_dt += timedelta(days=1)
    return int((e_dt - s_dt).total_seconds() / 60)


def _avg_historical_per_shift(table: str, days: int = 7) -> Optional[float]:
    """Average OK+NG parts per production shift in last N days (production
    shifts only — A/B/C, not GAP*).  Returns None if no data."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        try:
            cur.execute(f"""
                SELECT AVG(GREATEST(COALESCE(ok_count,0) + COALESCE(ng_count,0), 0))::float AS avg_per_shift,
                       COUNT(*)::int                                                       AS n_shifts
                  FROM {table}
                 WHERE record_date >= CURRENT_DATE - INTERVAL '{days} days'
                   AND shift_name NOT LIKE 'GAP%%'
                   AND COALESCE(is_gap_time, false) = false
                   AND (COALESCE(ok_count,0) + COALESCE(ng_count,0)) > 0
            """)
            r = cur.fetchone()
            if not r or not r.get("avg_per_shift") or r["n_shifts"] == 0:
                return None
            return float(r["avg_per_shift"])
        except Exception as exc:
            print(f"[SHIFT-CALC] historical avg failed: {exc}")
            return None


def _theoretical_per_shift(working_minutes: int, ideal_ct: float, quality_pct: float = 100.0) -> float:
    """Pure capacity: working_minutes × 60 / ideal_ct.
    quality_pct is NOT applied here — caller applies it once at the end
    by inflating the target."""
    if not working_minutes or not ideal_ct or ideal_ct <= 0:
        return 0.0
    return (working_minutes * 60.0) / ideal_ct


def _load_line(line_id: int) -> dict:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.id, l.line_name, l.db_table_name,
                   pc.ideal_cycle_time
              FROM mes_lines l
              JOIN mes_plc_configs pc ON pc.line_id = l.id AND pc.parent_plc_id IS NULL
             WHERE l.id = %s
        """, (line_id,))
        ln = cur.fetchone()
        if not ln:
            raise HTTPException(404, "line not found")
        return dict(ln)


def _load_production_shifts(line_id: int) -> List[dict]:
    """Return ordered list of production shifts (A, B, C — not GAP).
    Each entry has shift_name, working_minutes, total_plan, ot_minutes."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT shift_name, start_time, end_time, working_minutes,
                   total_plan, startup_delay_min,
                   ot_start_time, ot_end_time
              FROM mes_shift_configs
             WHERE line_id = %s
               AND COALESCE(is_production, true) = true
               AND shift_name NOT LIKE 'GAP%%'
             ORDER BY start_time
        """, (line_id,))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["ot_minutes"] = _ot_minutes(d)
            rows.append(d)
        return rows


# ════════════════════════════════════════════════════════════════════
#  Endpoint
# ════════════════════════════════════════════════════════════════════

class ComputeBody(BaseModel):
    line_id:      int
    mode:         str    = "forward"      # "forward" | "reverse"
    target_qty:   Optional[int]   = None  # forward mode input
    days_avail:   Optional[int]   = None  # reverse mode input
    allow_ot:     bool   = True
    quality_pct:  float  = 98.0           # 0-100
    rate_source:  str    = "historical"   # "historical" | "theoretical"


@router.post("/compute")
def compute(body: ComputeBody, user=Depends(get_current_user)):
    ln = _load_line(body.line_id)
    shifts = _load_production_shifts(body.line_id)
    if not shifts:
        raise HTTPException(400, "No production shifts configured for this line")
    ideal_ct = float(ln.get("ideal_cycle_time") or 15.0)

    # ── Decide per-shift production rate ────────────────────────────
    rate_label = body.rate_source
    avg_hist = None
    if body.rate_source == "historical":
        avg_hist = _avg_historical_per_shift(ln["db_table_name"], days=7)
        if avg_hist and avg_hist > 0:
            parts_per_shift = avg_hist
            rate_label = "historical (last 7 days)"
        else:
            # Fall back to theoretical with a warning
            parts_per_shift = _theoretical_per_shift(
                shifts[0].get("working_minutes") or 0, ideal_ct)
            rate_label = "theoretical (no historical data yet)"
    else:
        parts_per_shift = _theoretical_per_shift(
            shifts[0].get("working_minutes") or 0, ideal_ct)
        rate_label = "theoretical (working_minutes / ideal_ct)"

    parts_per_shift = max(1.0, parts_per_shift)
    parts_per_ot_min = 60.0 / ideal_ct  # per minute of OT
    shifts_per_day = len(shifts)        # typically 2 (A, B)

    # OT capacity per shift's OT window (in parts)
    ot_caps = {s["shift_name"]: int(s["ot_minutes"] * parts_per_ot_min)
               for s in shifts}
    total_ot_per_day = sum(ot_caps.values()) if body.allow_ot else 0
    parts_per_day_normal = parts_per_shift * shifts_per_day
    parts_per_day_max    = parts_per_day_normal + total_ot_per_day

    quality_pct = max(1.0, min(100.0, body.quality_pct))

    # ════════════════════════════════════════════════════════════════
    #  Forward mode
    # ════════════════════════════════════════════════════════════════
    if body.mode == "forward":
        if not body.target_qty or body.target_qty <= 0:
            raise HTTPException(400, "target_qty required for forward mode")
        effective_target = ceil(body.target_qty * 100.0 / quality_pct)

        schedule: List[dict] = []
        remaining = effective_target
        produced  = 0
        ot_total_min = 0
        ot_total_parts = 0
        day = 1

        # Hard cap so a misconfigured line can't lock up the API
        MAX_DAYS = 365
        while remaining > 0 and day <= MAX_DAYS:
            for s in shifts:
                if remaining <= 0:
                    break
                sname = s["shift_name"]
                # Normal shift production
                produce_normal = min(int(parts_per_shift), remaining)
                remaining -= produce_normal
                produced  += produce_normal
                ot_used    = 0
                ot_minutes = 0

                # OT used only if still short AND allow_ot
                if remaining > 0 and body.allow_ot and ot_caps.get(sname, 0) > 0:
                    cap = ot_caps[sname]
                    ot_used = min(cap, remaining)
                    # OT minutes actually used
                    ot_minutes = ceil(ot_used / parts_per_ot_min) if parts_per_ot_min > 0 else 0
                    ot_minutes = min(ot_minutes, s["ot_minutes"])
                    remaining -= ot_used
                    produced  += ot_used
                    ot_total_min  += ot_minutes
                    ot_total_parts += ot_used

                schedule.append({
                    "day": day,
                    "shift": sname,
                    "parts_normal": produce_normal,
                    "parts_ot": ot_used,
                    "ot_minutes": ot_minutes,
                    "parts_total": produce_normal + ot_used,
                })

                if remaining <= 0:
                    break
            day += 1

        achievable = remaining <= 0
        days_used = schedule[-1]["day"] if schedule else 0
        normal_shifts = sum(1 for r in schedule if r["parts_normal"] > 0)
        ot_shifts     = sum(1 for r in schedule if r["parts_ot"] > 0)

        plain_parts = []
        plain_parts.append(f"{normal_shifts} shift{'s' if normal_shifts != 1 else ''}")
        if ot_total_min > 0:
            plain_parts.append(f"{round(ot_total_min/60, 1)} hr{'s' if ot_total_min >= 60 else ''} OT")
        plain = " + ".join(plain_parts) + f"  · spans {days_used} day{'s' if days_used != 1 else ''}"
        if not achievable:
            plain += f"  ⚠ short by {remaining} parts after {MAX_DAYS} days"

        return {
            "mode": "forward",
            "target_qty":        body.target_qty,
            "effective_target":  effective_target,
            "quality_pct":       quality_pct,
            "rate_source":       rate_label,
            "parts_per_shift":   round(parts_per_shift, 1),
            "parts_per_ot_min":  round(parts_per_ot_min, 2),
            "shifts_per_day":    shifts_per_day,
            "achievable":        achievable,
            "shifts_used":       normal_shifts,
            "ot_shifts_used":    ot_shifts,
            "ot_minutes_total":  ot_total_min,
            "ot_hours_total":    round(ot_total_min / 60.0, 2),
            "ot_parts_total":    ot_total_parts,
            "days_needed":       days_used,
            "produced_total":    produced,
            "shortage":          max(0, remaining),
            "plain":             plain,
            "schedule":          schedule,
            "history_avg_hint":  round(avg_hist, 1) if avg_hist else None,
            "line_name":         ln["line_name"],
        }

    # ════════════════════════════════════════════════════════════════
    #  Reverse mode
    # ════════════════════════════════════════════════════════════════
    elif body.mode == "reverse":
        if not body.days_avail or body.days_avail <= 0:
            raise HTTPException(400, "days_avail required for reverse mode")
        per_day = parts_per_day_normal + (total_ot_per_day if body.allow_ot else 0)
        max_output_raw = int(per_day * body.days_avail)
        # Quality factor — operator wants OK count after NG; produced × q%
        max_output_ok  = int(max_output_raw * quality_pct / 100.0)

        schedule = []
        produced = 0
        for d in range(1, body.days_avail + 1):
            for s in shifts:
                sname = s["shift_name"]
                normal = int(parts_per_shift)
                ot = ot_caps.get(sname, 0) if body.allow_ot else 0
                ot_min = s["ot_minutes"] if body.allow_ot else 0
                schedule.append({
                    "day": d,
                    "shift": sname,
                    "parts_normal": normal,
                    "parts_ot": ot,
                    "ot_minutes": ot_min,
                    "parts_total": normal + ot,
                })
                produced += normal + ot

        plain = (f"In {body.days_avail} day{'s' if body.days_avail!=1 else ''} "
                 f"→ up to {max_output_raw:,} parts produced "
                 f"(~{max_output_ok:,} OK at {quality_pct:.0f}% quality)")

        return {
            "mode": "reverse",
            "days_avail":         body.days_avail,
            "quality_pct":        quality_pct,
            "rate_source":        rate_label,
            "parts_per_shift":    round(parts_per_shift, 1),
            "shifts_per_day":     shifts_per_day,
            "parts_per_day_max":  per_day,
            "max_output_raw":     max_output_raw,
            "max_output_ok":      max_output_ok,
            "plain":              plain,
            "schedule":           schedule,
            "history_avg_hint":   round(avg_hist, 1) if avg_hist else None,
            "line_name":          ln["line_name"],
        }

    else:
        raise HTTPException(400, "mode must be 'forward' or 'reverse'")


@router.get("/lines")
def list_lines_for_calc(user=Depends(get_current_user)):
    """Compact line picker — name + ideal CT + whether OT is configured."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.id, l.line_name,
                   pc.ideal_cycle_time,
                   (SELECT COUNT(*) FROM mes_shift_configs s
                     WHERE s.line_id = l.id
                       AND COALESCE(is_production, true) = true
                       AND shift_name NOT LIKE 'GAP%%') AS shift_count,
                   (SELECT BOOL_OR(ot_start_time IS NOT NULL AND ot_end_time IS NOT NULL)
                      FROM mes_shift_configs s2
                     WHERE s2.line_id = l.id) AS has_ot
              FROM mes_lines l
         LEFT JOIN mes_plc_configs pc
                ON pc.line_id = l.id AND pc.parent_plc_id IS NULL
             ORDER BY l.line_name
        """)
        return cur.fetchall()
