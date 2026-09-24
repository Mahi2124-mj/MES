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
  • losses    — per hourly slot: breakdown / speed / material / others minutes,
                operator loss remarks and the loss time windows (2026-09-22)

Read-only.  Adds no tables, touches no counting.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, time as dt_time
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
             WHERE line_id=%s AND shift_name=%s
             ORDER BY slot_order
        """, (line_id, shift))
        all_slots = cur.fetchall()
        slots = [r for r in all_slots if r["slot_order"] < 900]
        # 2026-09-22 — the OT bucket (slot_order >= 900, e.g. "03:30-06:20 OT")
        # becomes an extra hour column when OT actually ran (below).
        ot_slots = [r for r in all_slots if r["slot_order"] >= 900]
        ot_plan = 0
        try:
            cur.execute("SELECT ot_plan FROM mes_shift_configs WHERE line_id=%s AND shift_name=%s",
                        (line_id, shift))
            _otr = cur.fetchone()
            ot_plan = int((_otr or {}).get("ot_plan") or 0)
        except Exception:
            conn.rollback()
            ot_plan = 0

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
                # mno = the same model-number rule as the product rows below
                # (3rd-4th part-code chars), NULL for unscanned / junk codes.
                cur.execute(
                    f"SELECT ts::time AS t, is_ng, "
                    f"       CASE WHEN part_code ~ '^[0-9]{{4}}' "
                    f"            THEN substring(part_code from 3 for 2)::int END AS mno "
                    f"  FROM {ct} WHERE record_date=%s AND shift_name=%s", (d, shift))
                cycles = cur.fetchall()
                ot_slots, gap_cycles = _gap_ot(cur, ct, d, slots, ot_slots, cycles)
                cycles = cycles + gap_cycles
                # Per model: the part-code's 3rd-4th chars are the model number
                # (e.g. 00_09_6D6 -> 9, 00_15_6D6 -> 15). Require 4 leading
                # digits so the slice is safe and bad scans (blanks/'ERROR') and
                # 1-off junk are dropped by the HAVING count.
                # 2026-09-22 — counted from the cycles above (same rule: model
                # number, >= 5 cycles) so GAP-labelled OT cycles are included.
                _agg = {}
                for c in cycles:
                    if c.get("mno") is None:
                        continue
                    a = _agg.setdefault(int(c["mno"]), {"mno": int(c["mno"]), "ok": 0, "ng": 0})
                    a["ng" if c["is_ng"] else "ok"] += 1
                model_rows = sorted((a for a in _agg.values() if a["ok"] + a["ng"] >= 5),
                                    key=lambda a: -a["ok"])

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

    # OT: the shift's own cycles (same record_date + shift_name) that fall in
    # the OT bucket, or a manual OT target, make it a real hour column — same
    # plan / actual / cumm / model / loss / man-hour treatment as any hour.
    # Cycles the collector labelled GAP (OT never activated) are not the
    # shift's, exactly as the dashboard and the totals treat them.
    for s in ot_slots:
        ran = any(_in_slot(c["t"], s["start_time"], s["end_time"]) for c in cycles)
        if ran or ot_plan:
            s = dict(s)
            s["_ot"] = True
            if ot_plan:
                s["plan_pieces"] = ot_plan
            slots.append(s)

    # product models (the rows the sheet prints) and when each first ran
    prod_mnos = {int(m["mno"]) for m in model_rows}
    first_min = {}
    for c in cycles:
        mno = c.get("mno")
        if mno is None or int(mno) not in prod_mnos:
            continue
        x = _mins(c["t"])
        if x is not None and (int(mno) not in first_min or x < first_min[int(mno)]):
            first_min[int(mno)] = x
    order = sorted(prod_mnos, key=lambda n: (first_min.get(n, 1e9), n))
    sno_by_mno = {n: i + 1 for i, n in enumerate(order)}

    latest = max((_mins(c["t"]) for c in cycles), default=None)
    hours, cum, tot_plan, slot_models = [], 0, 0, []
    for s in slots:
        ss, se = s["start_time"], s["end_time"]
        ok = ng = 0
        seen = set()
        for c in cycles:
            if _in_slot(c["t"], ss, se):
                if c["is_ng"]:
                    ng += 1
                else:
                    ok += 1
                if c.get("mno") is not None and int(c["mno"]) in sno_by_mno:
                    seen.add(sno_by_mno[int(c["mno"])])
                else:
                    # 2026-09-23 — cycles with no part code (or a code no model
                    # owns) are the sheet's "Not scanned (no part code)" row, which
                    # the leader renames by hand.  It is a model like any other, so
                    # the hours it ran must show its S.No in Model S. N. — the one
                    # after the scanned models (the sheet builds the same number).
                    seen.add(len(order) + 1)
        plan = int(s["plan_pieces"] or 0)
        elapsed = latest is not None and _mins(ss) <= latest   # slot already started
        if elapsed:
            cum += ok + ng
        h = {
            "label": s["slot_label"],
            "plan": plan or None,
            "ok": ok if elapsed else None,
            "ng": ng if elapsed else None,
            "cum": cum if elapsed else None,
        }
        if s.get("_ot"):
            h["ot"] = True
        hours.append(h)
        slot_models.append(sorted(seen))
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
        fm = first_min.get(mno)
        first_seen = None
        if fm is not None and shift_start is not None:
            _t = (shift_start.hour * 60 + shift_start.minute + int(fm)) % 1440
            first_seen = f"{_t // 60:02d}:{_t % 60:02d}"
        products.append({"model": name_by_no.get(mno) or f"Model {mno}",
                         "model_no": mno, "plan": None, "total": total,
                         "ok": int(m["ok"]), "ng": int(m["ng"]), "balance": None,
                         # 2026-09-22 — S.No = order the models first ran in
                         "sno": sno_by_mno.get(mno), "first_seen": first_seen})
    if len(products) == 1:
        products[0]["plan"] = int(tot_plan)
        products[0]["balance"] = products[0]["total"] - int(tot_plan)

    # ── per-slot losses (never raises; zeros when the data is missing) ──
    losses = _peff_losses(line_id, line["line_name"], d, shift, slots)

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
        "losses": losses,
        # per hour column (aligned with `hours`): S.No of each model that ran in it
        "slot_models": slot_models,
    }


# ════════════════════════════════════════════════════════════════════
#  OT the collector did not see (2026-09-22)
# ════════════════════════════════════════════════════════════════════
# When a line keeps running after the shift end but OT was never switched on,
# the collector files those cycles under shift_name 'GAP' (YNC-SS A 21-Sep:
# 188 cycles 17:15-18:00).  For the PEFF sheet only, such a run counts as the
# shift's OT: it must START within OT_RUN_GAP_MIN of the OT window's start
# (the configured OT bucket, else the shift end), CONTINUE with no pause longer
# than OT_RUN_GAP_MIN, and hold at least OT_RUN_MIN_CYCLES cycles — a handful
# of stray cycles is ignored.  Collectors and the dashboard are untouched.
OT_RUN_GAP_MIN = 15
OT_RUN_MIN_CYCLES = 20
OT_MAX_MIN = 240          # a window synthesised when no OT bucket is configured


def _gap_ot(cur, ct, d, slots, ot_slots, cycles):
    """-> (ot_slots, gap_cycles).  Never raises."""
    try:
        if not slots:
            return ot_slots, []
        sec = lambda t: t.hour * 3600 + t.minute * 60 + t.second
        base_t = slots[0]["start_time"]
        start0 = datetime.combine(d, base_t)
        at = lambda t: start0 + timedelta(seconds=(sec(t) - sec(base_t)) % 86400)
        last = slots[-1]
        shift_end = at(last["start_time"]) + timedelta(seconds=(sec(last["end_time"]) - sec(last["start_time"])) % 86400)
        wins = []
        for s in ot_slots:
            a = at(s["start_time"])
            wins.append((s, a, a + timedelta(seconds=(sec(s["end_time"]) - sec(s["start_time"])) % 86400)))
        if not wins:
            wins.append((None, shift_end, shift_end + timedelta(minutes=OT_MAX_MIN)))
        own_ts = [at(c["t"]) for c in cycles]
        out_slots, gap_all = list(ot_slots), []
        for s, a, b in wins:
            cur.execute(f"SELECT ts, ts::time AS t, is_ng, "
                        f"       CASE WHEN part_code ~ '^[0-9]{{4}}' "
                        f"            THEN substring(part_code from 3 for 2)::int END AS mno "
                        f"  FROM {ct} WHERE ts >= %s AND ts < %s AND shift_name = 'GAP' ORDER BY ts",
                        (a, b))
            gap = cur.fetchall()
            if not gap:
                continue
            pts = sorted([(r["ts"], r) for r in gap] + [(t, None) for t in own_ts if a <= t < b],
                         key=lambda x: x[0])
            if (pts[0][0] - a).total_seconds() > OT_RUN_GAP_MIN * 60:
                continue
            run = [pts[0]]
            for p in pts[1:]:
                if (p[0] - run[-1][0]).total_seconds() > OT_RUN_GAP_MIN * 60:
                    break
                run.append(p)
            if len(run) < OT_RUN_MIN_CYCLES:
                continue
            gap_run = [r for _, r in run if r is not None]
            gap_all.extend({"t": r["t"], "is_ng": r["is_ng"], "mno": r["mno"]} for r in gap_run)
            if s is None:                     # no OT bucket configured: shift end -> run end
                end = run[-1][0]
                end = end + timedelta(minutes=(5 - end.minute % 5) % 5 or 5, seconds=-end.second)
                out_slots.append({"slot_order": 999, "start_time": shift_end.time(), "end_time": end.time().replace(microsecond=0),
                                  "slot_label": f"{shift_end:%H:%M}-{end:%H:%M} OT", "plan_pieces": 0})
        return out_slots, gap_all
    except Exception as exc:
        print(f"[PEFF] GAP OT check skipped: {exc}")
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return ot_slots, []


# ════════════════════════════════════════════════════════════════════
#  PER-SLOT LOSSES  (2026-09-22) — feeds the sheet's CORE JOB MAN HOUR rows
#  23 / 24, SEMI CORE JOB row 33, the hourly loss summary and the
#  "Summary Line Stop" box.
# ════════════════════════════════════════════════════════════════════
# breakdown / speed : the Hourly Report engine (routers.reports._slot_losses —
#                     andon-aware breakdown, SUM(ct - ideal) speed), so the
#                     sheet reads the same as that report.
# material / others : mes_status_log (one row per PLC status transition),
#                     each status held until the next row, mapped to a loss
#                     type through mes_status_mappings — the same mapping the
#                     collector's _update_status uses (status_map[old]["loss"]).
#
# Midnight: a B-shift slot after 00:00 happens on the NEXT calendar day while
# the shift keeps the previous record_date.  Slot times are therefore laid out
# in minutes since the shift start.  _slot_losses builds its windows as
# record_date + slot clock time, so it is called with the next day for the
# after-midnight slots (with the shift's own date those windows would land on
# the morning BEFORE the shift and read the previous night's losses).
# mes_status_log.record_date is the calendar date of each row (ts.date()), so
# it is read by ts window, not by record_date.
_LOSS_KEYS = ("breakdown", "speed", "material", "others",
              "setup", "change_over", "quality")
_MAX_WINDOWS = 10          # per slot, longest kept (a flapping status can log hundreds)


def _slot_datetimes(d, slots):
    """[(start_dt, end_dt)] for each slot, laid out from the shift's first slot."""
    if not slots:
        return []
    sec = lambda t: t.hour * 3600 + t.minute * 60 + t.second
    base = sec(slots[0]["start_time"])
    start0 = datetime.combine(d, slots[0]["start_time"])
    out = []
    for s in slots:
        a_off = (sec(s["start_time"]) - base) % 86400
        dur = (sec(s["end_time"]) - sec(s["start_time"])) % 86400
        a = start0 + timedelta(seconds=a_off)
        out.append((a, a + timedelta(seconds=dur)))
    return out


def _peff_losses(line_id, line_name, d, shift, slots):
    """One entry per slot: {label, breakdown_min, speed_min, material_min,
    others_min, setup_min, change_over_min, quality_min, remarks,
    windows:[{start, end, category, min}]}.  Read-only; never raises."""
    out = [{"label": s["slot_label"], **{f"{k}_min": 0.0 for k in _LOSS_KEYS},
            "remarks": "", "windows": []} for s in slots]
    if not slots:
        return out
    try:
        bounds = _slot_datetimes(d, slots)
    except Exception as exc:
        print(f"[PEFF] loss slots skipped: {exc}")
        return out
    wins = [[] for _ in slots]

    def _clip(i, a, b, cat):
        """Add [a, b) clipped to slot i as a window; returns the seconds added."""
        sa, sb = bounds[i]
        x, y = max(a, sa), min(b, sb)
        if y <= x:
            return 0.0
        wins[i].append((x, y, cat))
        return (y - x).total_seconds()

    # ── breakdown + speed (+ remarks): the Hourly Report engine ──────────
    try:
        from routers.reports import _slot_losses, _shift_remarks, _norm_slot
        groups = {}
        for i, (a, b) in enumerate(bounds):
            if b > a:          # a zero-length slot would read as 24 h in _slot_bounds
                groups.setdefault(a.date(), []).append(i)
        for day, idxs in groups.items():
            rs = [{"label": f"{bounds[i][0]:%H:%M}-{bounds[i][1]:%H:%M}"} for i in idxs]
            _slot_losses(line_id, day, shift, rs)
            for i, r in zip(idxs, rs):
                out[i]["breakdown_min"] = float(r.get("breakdown_loss") or 0.0) / 60.0
                out[i]["speed_min"] = float(r.get("speed_loss") or 0.0) / 60.0
        # _slot_losses matches shift_name = shift / NULL only, but the remark
        # screen saves shift_name '' — _shift_remarks (the Excel export's
        # reader) takes all three, keyed by the shift's own record_date.
        rem = {_norm_slot(k): v for k, v in (_shift_remarks(line_id, d, shift) or {}).items()}
        for i, s in enumerate(slots):
            out[i]["remarks"] = (rem.get(_norm_slot(s["slot_label"])) or "")[:300]
    except Exception as exc:
        print(f"[PEFF] breakdown/speed skipped: {exc}")

    start_dt = bounds[0][0]
    end_dt = max(b for _, b in bounds)
    stop = min(end_dt, datetime.now())

    # ── breakdown windows (same source _slot_losses reads) ───────────────
    try:
        ivs = []
        andon = False
        try:
            from routers.andon import andon_line_set, andon_breakdown_intervals, _norm
            andon = bool(line_name) and _norm(line_name) in andon_line_set()
        except Exception:
            andon = False
        if andon:
            ivs = andon_breakdown_intervals(line_name, start_dt, end_dt) or []
        else:
            with get_conn() as conn:
                cur = dict_cursor(conn)
                cur.execute("""
                    SELECT GREATEST(started_at, %s) AS a,
                           LEAST(COALESCE(ended_at, LOCALTIMESTAMP), %s) AS b
                      FROM mes_breakdowns
                     WHERE line_id=%s AND started_at < %s
                       AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                     ORDER BY 1""", (start_dt, end_dt, line_id, end_dt, start_dt))
                ivs = [(r["a"], r["b"]) for r in cur.fetchall() if r["a"] and r["b"]]
        merged = []
        for a, b in sorted(ivs):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            elif b > a:
                merged.append((a, b))
        for a, b in merged:
            for i in range(len(slots)):
                _clip(i, a, b, "breakdown")
    except Exception as exc:
        print(f"[PEFF] breakdown windows skipped: {exc}")

    # ── material / others (+ setup / change-over / quality) from the PLC
    #    status log ───────────────────────────────────────────────────────
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT status_name, loss_type FROM mes_status_mappings WHERE line_id=%s",
                        (line_id,))
            lmap = {(r["status_name"] or "").strip().upper(): r["loss_type"]
                    for r in cur.fetchall() if r["loss_type"]}
            rows = []
            if lmap and stop > start_dt:
                cur.execute("""
                    SELECT status, ts::timestamp AS t FROM mes_status_log
                     WHERE line_id=%s AND record_date BETWEEN %s AND %s
                       AND ts >= %s AND ts < %s
                     ORDER BY ts""",
                            (line_id, start_dt.date(), stop.date(), start_dt, stop))
                rows = cur.fetchall()
        for k, r in enumerate(rows):
            cat = lmap.get((r["status"] or "").strip().upper())
            if cat not in _LOSS_KEYS or cat in ("breakdown", "speed"):
                continue       # breakdown comes from the andon / breakdown log above
            a = r["t"]
            b = rows[k + 1]["t"] if k + 1 < len(rows) else stop
            if b <= a:
                continue
            for i in range(len(slots)):
                out[i][f"{cat}_min"] += _clip(i, a, b, cat) / 60.0
    except Exception as exc:
        print(f"[PEFF] status-log losses skipped: {exc}")

    # ── tidy: rounding + windows (merge touching pieces, keep the longest) ──
    for i, o in enumerate(out):
        for k in _LOSS_KEYS:
            # 3 decimals so the sheet's 1-decimal minutes are not rounded twice;
            # a negative speed "loss" (cycles faster than ideal) is no loss.
            o[f"{k}_min"] = round(max(0.0, o[f"{k}_min"]), 3)
        ws = []
        for x, y, cat in sorted(wins[i]):
            if ws and ws[-1][2] == cat and (x - ws[-1][1]).total_seconds() <= 1:
                ws[-1] = (ws[-1][0], max(y, ws[-1][1]), cat)
            else:
                ws.append((x, y, cat))
        ws = sorted(ws, key=lambda w: (w[1] - w[0]), reverse=True)[:_MAX_WINDOWS]
        o["windows"] = [{"start": f"{x:%H:%M}", "end": f"{y:%H:%M}", "category": cat,
                         "min": round((y - x).total_seconds() / 60.0, 2)}
                        for x, y, cat in sorted(ws)]
    return out


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
