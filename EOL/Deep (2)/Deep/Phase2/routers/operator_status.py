"""
routers/operator_status.py — MES data for the Control Board's Operator Status
sheets ("Visualization Sheet of Operator Status").  2026-11-06.

GET /api/control-board/operator-status?line_id=<id>&month=YYYY-MM
  -> {line_id, line_name, month, days, sheets: [{first_day, operators: [...]}, {...}]}

Operator rules (6-Nov):
  * a line has TWO sheets a month, one per shift crew;
  * each sheet's operators are decided at the start of the month: the crew of
    each shift on the first day it has one;
  * no shift is written on the sheets: a crew's shift changes every week
    (operator, 6-Nov) — Sheet 1 = the crew on A shift at the start, Sheet 2 =
    the other crew;
  * when an operator changes later, the new one is added below and the earlier
    one stays — only their mark for that day changes;
  * marks follow the sheet's own legend only; for now attendance = ○ (the
    operator will say later which legend mark applies when).

Source = Shift Allocation (mes_manpower_allocations, active rows).  The crews
swap A / B shift every week, so a sheet follows its CREW: the A sheet is the
crew that was on A shift at the start of the month, also in the weeks it works
B shift.  A crew seen on a shift that does not match that sheet's crew is the
other crew (rotated).  A newcomer joins the sheet of the crew they first work
with.  Only SAVED shifts count: a shift with no saved allocation shows no
operator and no mark, even if the crew could be carried from an earlier day
(operator, 10-Nov — the week carry of 6-Nov was removed).

Day mark of an operator: ○ when on this line that day (either shift), else
blank.  Read-only.

2026-11-12: each sheet also says which shift its crew worked each day
(`shifts` {day: "A"/"B"}, the shift where most of the crew was) — the Operator
Visualization Sheet's supervisor signature is that shift's line leader.

2026-11-13 (operator): the PROCESS column stays the process of the operator's
first day in the month; on a day the operator worked another process (Shift
Allocation), that day's PROCESS NUMBER is shown inside the attendance mark.
Number = the operator station: on a Seat Slider line 6 operators run 6
process groups (Upper Rail + Lock Bar = 1 ... Final Inspection = 6); a process
belongs to the group it is most often allocated with in the month (active
processes only), groups numbered by their first process in the line's order.
  operators[].pno      the number of the PROCESS column's process ("6")
  operators[].day_pno  {day: number} only for days with a group the operator
                       did not have on the first day: ONE number (operator,
                       13-Nov) - the lowest such group
  process_nos          {number: process names} for the legend / tooltips
"""
from __future__ import annotations

import bisect
import calendar
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/control-board", tags=["control-board"])

SHEETS = ("A", "B")
PRESENT = "○"      # ○ — attendance mark for now (operator, 6-Nov)
NEW_MIN_DAYS = 10       # start day only once the line has 10+ saved days before it
                        # (allocation history starts Sep-Oct 2026, saved only now and then at first)


def _ddmon(d: date) -> str:
    return d.strftime("%d-%b")


def process_numbers(cur, line_id: int, d0: date, d1: date):
    """{process_id: number} and {number: "name + name"} from the saved
    allocations of [d0, d1] — see the module notes."""
    cur.execute("""SELECT a.shift_date, a.shift_name, a.operator_id, p.id, p.process_name, p.display_order
                     FROM mes_manpower_allocations a JOIN mes_processes p ON p.id = a.process_id
                    WHERE a.line_id = %s AND a.removed_at IS NULL AND p.is_active
                      AND a.shift_date BETWEEN %s AND %s""",
                (line_id, d0, d1))
    groups, order, names = {}, {}, {}
    for r in cur.fetchall():
        groups.setdefault((r["shift_date"], r["shift_name"], r["operator_id"]), set()).add(r["id"])
        order[r["id"]] = r["display_order"] or 0
        names[r["id"]] = r["process_name"] or ""
    seen = Counter(frozenset(g) for g in groups.values())
    home = {}
    for pid in order:                      # the group this process is most often part of
        best = max((g for g in seen if pid in g), key=lambda g: (seen[g], len(g), -min(order[x] for x in g)))
        home[pid] = best
    firsts = sorted({min(order[x] for x in g) for g in home.values()})
    rank = {o: i + 1 for i, o in enumerate(firsts)}
    pno = {pid: rank[min(order[x] for x in g)] for pid, g in home.items()}
    label = {}
    for pid in sorted(pno, key=lambda x: order[x]):
        label.setdefault(pno[pid], []).append(names[pid])
    return pno, {str(n): " + ".join(v) for n, v in sorted(label.items())}


def _same_crew(crew, members: set) -> bool:
    """At least half of `crew` already belongs to `members`."""
    return bool(members) and len(set(crew) & members) * 2 >= max(1, len(crew))


@router.get("/operator-status")
def operator_status(line_id: int = Query(...), month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                    user=Depends(get_current_user)):
    y, m = int(month[:4]), int(month[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(400, "month must be YYYY-MM")
    ndays = calendar.monthrange(y, m)[1]
    m0, m1 = date(y, m, 1), date(y, m, ndays)
    end = min(m1, date.today())
    so = {s: {"first_day": None, "operators": [], "shifts": {}} for s in SHEETS}
    out = {"line_id": line_id, "month": month, "days": ndays, "sheets": [so[s] for s in SHEETS]}

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, line_name, db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")
        out["line_name"] = line["line_name"]
        if end < m0:
            return out                                   # a future month

        # saved crews of the month
        cur.execute("""
            SELECT a.shift_date, a.shift_name, a.operator_id, p.process_name, p.display_order, p.id AS pid
              FROM mes_manpower_allocations a
              JOIN mes_processes p ON p.id = a.process_id
             WHERE a.line_id = %s AND a.removed_at IS NULL
               AND a.shift_date BETWEEN %s AND %s AND a.shift_name = ANY(%s)
             ORDER BY a.shift_date, a.shift_name, p.display_order, a.allocated_at""",
                    (line_id, m0, end, list(SHEETS)))
        saved: dict = {}
        day_pids: dict = {}                              # (operator, date) -> process ids of that day
        row_pid: dict = {}                               # (operator, date, shift) -> the process shown
        for r in cur.fetchall():
            crew = saved.setdefault((r["shift_date"], r["shift_name"]), {})
            crew.setdefault(r["operator_id"], (r["process_name"], r["display_order"] or 0))
            day_pids.setdefault((r["operator_id"], r["shift_date"]), set()).add(r["pid"])
            row_pid.setdefault((r["operator_id"], r["shift_date"], r["shift_name"]), r["pid"])
        try:
            pno, out["process_nos"] = process_numbers(cur, line_id, m0, end)
        except Exception as e:                           # never break the sheet over the numbers
            print(f"[OPS] process numbers failed: {str(e)[:120]}", flush=True)
            pno, out["process_nos"] = {}, {}

        cur.execute("SELECT DISTINCT shift_date AS d FROM mes_manpower_allocations WHERE line_id = %s AND removed_at IS NULL ORDER BY 1",
                    (line_id,))
        line_days = [r["d"] for r in cur.fetchall()]

        # 2026-11-10 (operator): "operator name tab tak show na ho jab tak shift
        # allocation save na ho, bhale hi last day se crew carry forward hua ho".
        # Only SAVED shifts count — no carry from an earlier day of the week.
        crews: dict = {k: (v, None) for k, v in saved.items() if m0 <= k[0] <= end}

        # presence: op -> {day: [(shift, process, carried_from)]}
        pres: dict = {}
        for (d, sh), (members, src) in crews.items():
            for op, (proc, _o) in members.items():
                pres.setdefault(op, {}).setdefault(d, []).append((sh, proc, src))

        # sheet of every operator, day by day: the first crew seen on each shift
        # starts that shift's sheet; a crew that is mostly the other sheet's is
        # that crew rotated; anyone new joins the sheet their crew-mates are on
        rows = {s: [] for s in SHEETS}
        sheet_of: dict = {}
        crew_size: dict = {}
        d = m0
        while d <= end:
            for sh in SHEETS:
                c = crews.get((d, sh))
                if not c:
                    continue
                members = c[0]
                order = sorted(members, key=lambda o: members[o][1])
                other = SHEETS[1 - SHEETS.index(sh)]
                mine, theirs = set(rows[sh]), set(rows[other])
                target = sh
                if _same_crew(order, theirs) and not _same_crew(order, mine):
                    target = other                       # the other crew, rotated onto this shift
                elif mine and not _same_crew(order, mine) and not theirs:
                    target = other                       # a second crew before the other sheet started
                for op in order:
                    if op in sheet_of:
                        continue
                    a = len(set(order) & set(rows[sh])), len(set(order) & set(rows[other]))
                    s = target if a[0] == a[1] else (sh if a[0] > a[1] else other)
                    sheet_of[op] = s
                    rows[s].append(op)
                    if so[s]["first_day"] is None:
                        so[s]["first_day"] = d.day
                # the shift this crew worked today, for the sheet most of it is on
                n = {x: sum(1 for op in order if sheet_of.get(op) == x) for x in SHEETS}
                best = max(SHEETS, key=lambda x: n[x])
                if n[best] and n[best] > crew_size.get((best, d), 0):
                    crew_size[(best, d)] = n[best]
                    so[best]["shifts"][str(d.day)] = sh
            d += timedelta(days=1)
        roster = [op for s in SHEETS for op in rows[s]]
        if not roster:
            return out

        cur.execute("SELECT id, full_name, employee_id, badge_code FROM mes_operators WHERE id = ANY(%s)", (roster,))
        info = {r["id"]: r for r in cur.fetchall()}
        cur.execute("""SELECT operator_id, MIN(shift_date) AS first FROM mes_manpower_allocations
                        WHERE line_id = %s AND removed_at IS NULL AND operator_id = ANY(%s) GROUP BY 1""",
                    (line_id, roster))
        first_ever = {r["operator_id"]: r["first"] for r in cur.fetchall()}

    def established(d: date) -> bool:
        """Enough saved history before `d` to trust it as the operator's start day."""
        return bisect.bisect_left(line_days, d) >= NEW_MIN_DAYS

    for s in SHEETS:
        for op in rows[s]:
            days_p = pres.get(op, {})
            marks: dict = {}
            for d, here in sorted(days_p.items()):
                if d < m0:
                    continue
                tip = " + ".join(f"{sh} shift · {p}" for sh, p, _ in here)
                carried = all(x[2] for x in here)
                if carried:
                    tip += f" · Shift Allocation not saved, crew carried from {_ddmon(here[0][2])}"
                marks[str(d.day)] = [PRESENT, tip, 1 if carried else 0]
            first = min(dd for dd in days_p if dd >= m0)
            o = info.get(op) or {}
            fe = first_ever.get(op)
            # 2026-11-13: the number of the process in the PROCESS column; a day
            # on another group shows that group's number (one number only)
            # (a group the operator did not have on the first day; an operator who
            # always covers two groups shows none)
            rp = row_pid.get((op, first, days_p[first][0][0]))
            row_no = pno.get(rp)
            first_set = {pno[p] for p in day_pids.get((op, first), ()) if p in pno}
            day_no = {}
            for d in days_p:
                if d >= m0:
                    new = sorted({pno[p] for p in day_pids.get((op, d), ()) if p in pno} - first_set)
                    if new:
                        day_no[str(d.day)] = str(new[0])
            so[s]["operators"].append({
                "id": op,
                "code": o.get("employee_id") or o.get("badge_code") or "",
                "name": o.get("full_name") or f"Operator {op}",
                "process": days_p[first][0][1],
                "pno": str(row_no) if row_no else "", "day_pno": day_no,
                "start_day": fe.strftime("%d.%m.%y") if fe and established(fe) else "",
                "days": marks,
            })
    return out
