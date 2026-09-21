"""
routers/peff.py — data feed for the PEFF hourly-production check-sheet.

The PEFF sheet (public/peff-sheet.html, hosted at /peff-sheet) is a static
form.  This single endpoint returns everything it needs to auto-fill for a
chosen line + shift + date, pulled live from the same tables the dashboards
use — so the sheet reads "same to same" as the MES:

  • machines  — station no (SS-01..) + name, in machine_no order
  • operators — per machine, from the Shift-Allocation page (mes_manpower_allocations)
  • hours     — per fixed clock-hour: plan / OK / NG / cumulative
  • totals    — shift OK / NG / total / plan
  • model     — dominant part-code family for the shift

Read-only.  Adds no tables, touches no counting.  Losses / man-hour matrix are
intentionally NOT filled here (that needs a separate downtime-reason mapping).
"""
from __future__ import annotations

import re
from datetime import datetime, time as dt_time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/peff", tags=["peff"])

# Work-center base codes (shift letter A/B is appended per shift).  Shown as
# the dropdown choices in the WC-alignment panel.
WC_CHOICES = [
    "Q31S01", "Q31S02", "Q31S03", "Q31S04", "Q31S05", "Q31S06", "Q31S07",
    "Q31S08", "Q31S09",                       # Seat Slider (YRA/YJC/YSD/YHB/YCA/YNC/YFG/YWD/Y17)
    "Q31R01", "Q31R02", "Q31R03", "Q31R04", "Q31R05", "Q31RS1",   # Recliner
    "Q31L01",                                 # Loop Pipe
    "Q31G01",                                 # Gear Lifter
    "Q31SS1", "Q31SS2", "Q31SS3",             # Sub-assembly
    "Q31T01",                                 # Thin recliner
    "Q31P01",                                 # Press shop
]


@once
def _ensure_wc_col():
    """Add mes_lines.peff_wc (base WC code) once; safe to call repeatedly."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE mes_lines ADD COLUMN IF NOT EXISTS peff_wc VARCHAR(16)")
        conn.commit()

_TBL_RE = re.compile(r"^[a-z0-9_]+$")


@router.get("/data")
def peff_data(line_id: int = Query(...),
              date: str = Query(...),
              shift: str = Query(...),
              user=Depends(get_current_user)):
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    with get_conn() as conn:
        cur = dict_cursor(conn)

        # ── line ──────────────────────────────────────────────────────
        try:
            cur.execute("SELECT id, line_name, db_table_name, peff_wc FROM mes_lines WHERE id=%s",
                        (line_id,))
        except Exception:
            conn.rollback()
            _ensure_wc_col()
            cur.execute("SELECT id, line_name, db_table_name, peff_wc FROM mes_lines WHERE id=%s",
                        (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "line not found")
        tbl = (line["db_table_name"] or "").strip()
        wc_base = (line.get("peff_wc") or "").strip()
        wc = (wc_base + shift.upper()) if wc_base else ""

        # ── machines: the line's real (machine-backed) processes, in
        #    physical station order.  We deliberately do NOT join
        #    mes_machines: its ids drift from mes_processes.machine_id on
        #    some lines (stale seed), whereas mes_processes keeps the
        #    correct names and is what the Shift-Allocation page uses.
        #    Rows were first created in machine_no order, so ORDER BY id
        #    reproduces the physical SS-01.. sequence; the manual (NULL-
        #    machine) "Manual Movement" step is excluded here.
        cur.execute("""
            SELECT id, process_name FROM mes_processes
             WHERE line_id=%s AND machine_id IS NOT NULL AND is_active
             ORDER BY id
        """, (line_id,))
        mrows = cur.fetchall()

        # ── operators keyed by process_id (robust — the allocation references
        #    the process, not the drifting machine). Carry-forward: if this exact
        #    day has no allocation, use the most recent same-shift day earlier in
        #    the SAME week (the crew stays put through the week until changed).
        def _fetch_ops(qdate):
            cur.execute("""
                SELECT a.process_id, o.full_name
                  FROM mes_manpower_allocations a
                  JOIN mes_operators o ON o.id = a.operator_id
                 WHERE a.line_id=%s AND a.shift_date=%s AND a.shift_name=%s
                   AND a.removed_at IS NULL
            """, (line_id, qdate, shift))
            m = {}
            for r in cur.fetchall():
                m.setdefault(int(r["process_id"]), []).append(r["full_name"])
            return m

        op_by_pid = _fetch_ops(d)
        if not op_by_pid:
            cur.execute("""
                SELECT MAX(shift_date) AS sd FROM mes_manpower_allocations
                 WHERE line_id=%s AND shift_name=%s AND removed_at IS NULL
                   AND shift_date < %s
                   AND date_trunc('week', shift_date) = date_trunc('week', %s::date)
            """, (line_id, shift, d, d))
            cr = cur.fetchone()
            if cr and cr["sd"]:
                op_by_pid = _fetch_ops(cr["sd"])

        machines = []
        for i, r in enumerate(mrows):
            pid = int(r["id"])
            machines.append({
                "no": f"SS-{i+1:02d}",
                "name": r["process_name"] or "",
                "process_id": pid,
                "operator": ", ".join(op_by_pid.get(pid, [])) or "",
            })

        # ── hourly slots: the REAL break-adjusted MES slots (same as the
        #    dashboard and the paper sheet — e.g. 11:30-13:05 — not fixed
        #    clock-hours) ───────────────────────────────────────────────
        cur.execute("""
            SELECT slot_order, slot_label, start_time, end_time, plan_pieces
              FROM mes_hourly_slots
             WHERE line_id=%s AND shift_name=%s AND slot_order < 900
             ORDER BY slot_order
        """, (line_id, shift))
        slots = cur.fetchall()

        # ── raw cycles for the shift + per-model actuals ──────────────
        cycles, model_rows = [], []
        if tbl and _TBL_RE.match(tbl):
            ct = f"{tbl}_ct_log"
            cur.execute("SELECT to_regclass(%s) t", (f"public.{ct}",))
            if cur.fetchone()["t"] is not None:
                # 2026-09-17 — key on record_date, NOT ts::date.  B shift runs
                # 18:30 -> 03:15, and a cycle at 00:30 carries the PREVIOUS
                # day's record_date while its ts::date is the next day.  With
                # ts::date the whole after-midnight half of the shift vanished:
                # the sheet showed 0 for every slot past midnight and a shift
                # total that did not match its own rows.
                cur.execute(
                    f"SELECT ts::time AS t, is_ng FROM {ct} "
                    f"WHERE record_date=%s AND shift_name=%s", (d, shift))
                cycles = cur.fetchall()
                # Per model: the part-code's 3rd-4th chars are the model number
                # (e.g. 00_09_6D6 -> 9, 00_15_6D6 -> 15). Require 4 leading
                # digits so the slice is safe and bad scans (blanks/'ERROR') and
                # 1-off junk are dropped by the HAVING count.
                cur.execute(
                    f"SELECT substring(part_code from 3 for 2)::int AS mno, "
                    f"       COUNT(*) FILTER (WHERE NOT is_ng) ok, "
                    f"       COUNT(*) FILTER (WHERE is_ng) ng "
                    f"  FROM {ct} WHERE record_date=%s AND shift_name=%s "
                    f"   AND part_code ~ '^[0-9]{{4}}' "
                    f"GROUP BY 1 HAVING COUNT(*) >= 5 ORDER BY 2 DESC",
                    (d, shift))
                model_rows = cur.fetchall()

        # model_number → readable name for this line (deduped)
        cur.execute("""SELECT DISTINCT ON (model_number) model_number, model_name
                         FROM mes_model_mappings WHERE line_id=%s
                        ORDER BY model_number, id""", (line_id,))
        name_by_no = {int(r["model_number"]): (r["model_name"] or "").strip()
                      for r in cur.fetchall()}

    # ── per-slot hourly rows (bucket each cycle by the slot's own window) ──
    # 2026-09-17 — a plain `start <= t < end` cannot express a slot that
    # crosses midnight: "23:05 <= t < 00:05" is never true, so that slot
    # matched ZERO cycles while 559 of them fell inside it.  Compare in
    # minutes-since-shift-start instead, which also makes "has this slot
    # happened yet" correct on a shift that runs past midnight.
    shift_start = slots[0]["start_time"] if slots else None

    def _mins(t):
        """Minutes from the shift's first slot start, wrapping once at midnight."""
        if t is None or shift_start is None:
            return None
        m = t.hour * 60 + t.minute + t.second / 60.0
        base = shift_start.hour * 60 + shift_start.minute + shift_start.second / 60.0
        return m - base if m >= base else m - base + 1440

    def _in_slot(t, ss, se):
        a, b, x = _mins(ss), _mins(se), _mins(t)
        if None in (a, b, x):
            return False
        if b <= a:                       # the slot itself wraps past midnight
            b += 1440
        return a <= x < b

    latest = max((_mins(c["t"]) for c in cycles), default=None)
    hours, cum, tot_plan = [], 0, 0
    for s in slots:
        ss, se = s["start_time"], s["end_time"]
        ok = ng = 0
        for c in cycles:
            if _in_slot(c["t"], ss, se):
                if c["is_ng"]:
                    ng += 1
                else:
                    ok += 1
        plan = int(s["plan_pieces"] or 0)
        elapsed = latest is not None and _mins(ss) <= latest   # slot already started
        if elapsed:
            cum += ok + ng
        hours.append({
            "label": s["slot_label"],
            "plan": plan or None,
            "ok": ok if elapsed else None,
            "ng": ng if elapsed else None,
            "cum": cum if elapsed else None,
        })
        tot_plan += plan

    # grand totals from ALL cycles (so break-gap cycles still count)
    grand_ok = sum(1 for c in cycles if not c["is_ng"])
    grand_ng = sum(1 for c in cycles if c["is_ng"])
    grand_total = grand_ok + grand_ng

    # ── product rows, one per model. Per-model PLAN/Balance is a production
    #    target MES doesn't store, so it auto-fills only on a single-model
    #    shift; split-model days get actuals filled, plan/balance left manual.
    products = []
    for m in model_rows:
        mno = int(m["mno"])
        total = int(m["ok"]) + int(m["ng"])
        products.append({"model": name_by_no.get(mno) or f"Model {mno}",
                         "model_no": mno, "plan": None, "total": total,
                         "ok": int(m["ok"]), "ng": int(m["ng"]), "balance": None})
    if len(products) == 1:
        products[0]["plan"] = int(tot_plan)
        products[0]["balance"] = products[0]["total"] - int(tot_plan)

    return {
        "line_id": line_id,
        "line_name": line["line_name"],
        "shift": shift,
        "date": date,
        "wc": wc,
        "model": products[0]["model"] if products else "",
        "machines": machines,
        "hours": hours,
        "products": products,
        "totals": {"ok": grand_ok, "ng": grand_ng, "total": grand_total,
                   "plan": int(tot_plan), "balance": grand_total - int(tot_plan)},
    }


# ════════════════════════════════════════════════════════════════════
#  Work-Center alignment panel  (line ↔ WC base code)
# ════════════════════════════════════════════════════════════════════
@router.get("/workcenters")
def list_workcenters(user=Depends(get_current_user)):
    """Every line with its saved PEFF work-center base code, plus the list
    of choices for the dropdown."""
    _ensure_wc_col()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, line_name, COALESCE(line_code,'') line_code,
                              COALESCE(peff_wc,'') peff_wc
                         FROM mes_lines
                        WHERE COALESCE(is_active, TRUE)
                        ORDER BY line_name""")
        lines = [{"line_id": r["id"], "line_name": r["line_name"],
                  "line_code": r["line_code"], "wc": r["peff_wc"]}
                 for r in cur.fetchall()]
    return {"choices": WC_CHOICES, "lines": lines}


class WCSave(BaseModel):
    line_id: int
    wc: str = ""


@router.post("/workcenter")
def save_workcenter(body: WCSave, admin=Depends(require_admin)):
    """Set (or clear) a line's PEFF work-center base code."""
    _ensure_wc_col()
    wc = (body.wc or "").strip().upper()
    if wc and wc not in WC_CHOICES:
        raise HTTPException(400, f"unknown work-center: {wc}")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE mes_lines SET peff_wc=%s WHERE id=%s",
                    (wc or None, body.line_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "line not found")
        conn.commit()
    return {"ok": True, "line_id": body.line_id, "wc": wc}
