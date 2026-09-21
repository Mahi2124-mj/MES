"""
routers/pm_status.py
====================
Per-line PM status for the Fullscreen dashboard's "PM Check" tile.

Reads the maintenance team's yearly plan (`maintenance_yearly_pm_shedule`) and
answers one question per machine, as of TODAY:  is a PM due, is one overdue, or
is the machine fine — and when is the next one?

READ-ONLY.  This never writes to the maintenance tables; the maintenance app
owns them.

WEEK MODEL (decoded from the maintenance app's own grid, 2026-08-07)
--------------------------------------------------------------------
`plan_weeks` / `actual_weeks` are JSONB maps keyed by a **0-based week index
0..47** over a FINANCIAL year that starts in APRIL, with 4 fixed weeks per
month (not ISO weeks):

    week_index = month_offset * 4 + (week_of_month - 1)
    month_offset: APR=0, MAY=1, ... MAR=11

So "4" = MAY 1W, "22" = SEP 3W, "29" = NOV 2W.  Cross-checked against a 4M
(four-monthly) machine whose weeks are 6/22/38 = MAY/SEP/JAN — exactly four
months apart, which only holds under this model.

NAME MATCHING
-------------
The maintenance plan and the MES machine list are maintained separately and the
names differ ("MAG Welding of Arm x Recliner" vs "... Station 1"/"Station 2",
"Final Inspection Machine" vs "Final Inspection").  We therefore normalise and
prefix-match rather than requiring equality, and one plan row may legitimately
cover several MES machines (the two welding stations share a plan row).
"""
from datetime import date, timedelta
from typing import Optional
import json
import re

from fastapi import APIRouter, Depends, Query

from auth import get_current_user_optional
from database import get_conn, dict_cursor

pm_status_router = APIRouter(prefix="/api/pm-status", tags=["pm-status"])

MONTHS = ["APR", "MAY", "JUN", "JUL", "AUG", "SEP",
          "OCT", "NOV", "DEC", "JAN", "FEB", "MAR"]


def _fy_label(d: date) -> str:
    """Financial year label for a date — April..March, e.g. '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def _week_index(d: date) -> int:
    """0-based week index (0..47) of a date in its financial year."""
    month_offset = (d.month - 4) % 12
    # Weeks are fixed 7-day blocks inside the month; day 29+ still counts as 4W.
    week_of_month = min(3, (d.day - 1) // 7)
    return month_offset * 4 + week_of_month


def _week_label(wi: int) -> str:
    return f"{MONTHS[wi // 4]} {wi % 4 + 1}W"


def _week_start_date(wi: int, fy_start_year: int) -> date:
    """First calendar day of a week index within the given financial year."""
    month_offset = wi // 4
    cal_month = 4 + month_offset
    year = fy_start_year
    if cal_month > 12:
        cal_month -= 12
        year += 1
    return date(year, cal_month, (wi % 4) * 7 + 1)


def _norm(s: str) -> str:
    """Lowercase, alphanumerics only — so spacing/punctuation drift can't break
    a match between the two separately-maintained name lists."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ── Machine-name matching ────────────────────────────────────────────────
# The two lists are typed by different teams and drift in every possible way:
#   "Upper Rail Greasing Machine"  vs  "Upper Rail Greasing m/c"
#   "LOWER RAIL GREASING"          vs  "Lower Rail Grease & Bar Coding M/c"
#   "Upr Rail x Lock Mecha Assy"   vs  "Upr Rail×Lock Mecha Assy　M/C"   (U+00D7, U+3000)
#   "Guide S/Ab Insert #1 M/c"     vs  "Guide S/A Insert　#1 M/C"
# So compare TOKEN SETS with the noise words dropped and the common spelling
# variants folded together, instead of demanding a prefix.
_NOISE = {"mc", "m", "c", "machine", "and", "the", "of", "x", "with", "in"}
_SYN = {
    "greasing": "grease", "greese": "grease",
    "upper": "upr", "lower": "lwr",
    "automatic": "auto", "automatric": "auto",
    "sab": "sa", "assembly": "assy",
    "inspn": "inspection", "insp": "inspection",
    "bkt": "bracket",
}


def _tokens(s: str) -> set:
    """Comparable token set for a machine name."""
    t = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()
    out = set()
    for w in t:
        if w.isdigit():
            out.add(str(int(w)))          # "01" and "1" are the same station
            continue
        w = _SYN.get(w, w)
        if w in _NOISE or len(w) < 2:
            continue
        out.add(w)
    return out


def _score(a: set, b: set) -> float:
    """Overlap relative to the smaller set, 0..1."""
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    # Station/line numbers must agree — "#1" must never match "#2".
    da, db = {x for x in a if x.isdigit()}, {x for x in b if x.isdigit()}
    if da and db and not (da & db):
        return 0.0
    return len(inter) / min(len(a), len(b))


def _keys_as_ints(raw) -> list:
    """plan_weeks/actual_weeks arrive as dict (psycopg2 JSONB) or str."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, dict):
        return []
    out = []
    for k in raw.keys():
        try:
            out.append(int(k))
        except Exception:
            pass
    return sorted(out)


@pm_status_router.get("/line/{line_id}")
def pm_status_for_line(
    line_id: int,
    on_date: Optional[str] = Query(None, description="YYYY-MM-DD; omit = today"),
    user=Depends(get_current_user_optional),
):
    """Every machine on the line + its PM status as of `on_date`.

    status is one of:
      OVERDUE — a planned week has passed with no actual marked against it
      DUE     — this very week is a planned week, not yet marked done
      OK      — nothing pending; `next` says when the next PM falls
      NO PLAN — the machine has no row in the yearly plan
    """
    try:
        today = date.fromisoformat(on_date) if on_date else date.today()
    except Exception:
        today = date.today()

    fy      = _fy_label(today)
    fy0     = _fy_start_year(today)
    cur_wi  = _week_index(today)

    with get_conn() as conn:
        cur = dict_cursor(conn)

        cur.execute("SELECT line_name FROM mes_lines WHERE id = %s", (line_id,))
        lrow = cur.fetchone()
        line_name = (lrow or {}).get("line_name") or "Unknown line"

        cur.execute(
            "SELECT id, machine_name, machine_seq FROM mes_plc_configs "
            "WHERE line_id = %s ORDER BY COALESCE(machine_seq, 9999), id",
            (line_id,))
        machines = [dict(r) for r in (cur.fetchall() or [])]

        # The plan's `line` column uses its own codes (YMC_RC, YHB_RC ...).  Take
        # the MES line's first token (YMC from "YMC Recliner") and keep the plan
        # rows whose line code starts with it — that is what distinguishes the
        # per-line copies of an otherwise identical machine list.
        token = _norm((line_name.split() or [""])[0])
        cur.execute(
            "SELECT line, machine_code, machine_name, pm_frequency, "
            "       plan_weeks, actual_weeks, actual_dates "
            "FROM maintenance_yearly_pm_shedule WHERE year_label = %s",
            (fy,))
        all_plan = [dict(r) for r in (cur.fetchall() or [])]

    plan_rows = [p for p in all_plan
                 if token and _norm(p.get("line")).startswith(token)] or []

    # Pre-tokenise the plan side once.
    for p in plan_rows:
        p["_tok"] = _tokens(p.get("machine_name"))

    def _match(machine_name: str):
        """Best plan row for a MES machine name.

        Exact/prefix first (cheap and unambiguous), then token overlap for the
        spelling drift.  0.6 is the lowest threshold that still keeps
        "Guide S/A Insert #1" away from "#2" and "Rail Assy #01" from "#02" —
        those are separated by the digit guard in _score anyway.
        """
        mn = _norm(machine_name)
        if not mn:
            return None
        for p in plan_rows:                       # exact / prefix
            pn = _norm(p.get("machine_name"))
            if pn and (mn == pn or mn.startswith(pn) or pn.startswith(mn)):
                return p
        mt = _tokens(machine_name)
        best, best_sc = None, 0.0
        for p in plan_rows:
            sc = _score(mt, p["_tok"])
            if sc > best_sc:
                best, best_sc = p, sc
        return best if best_sc >= 0.6 else None

    out = []
    for m in machines:
        p = _match(m["machine_name"])
        if not p:
            out.append({
                "machine_id":   m["id"],
                "machine_name": m["machine_name"],
                "machine_seq":  m["machine_seq"],
                "status":       "NO PLAN",
                "frequency":    None,
                "plan":         [],
                "overdue":      [],
                "next":         None,
                "plan_machine": None,
            })
            continue

        planned = _keys_as_ints(p.get("plan_weeks"))
        done    = set(_keys_as_ints(p.get("actual_weeks")))
        overdue = [w for w in planned if w < cur_wi and w not in done]
        due_now = cur_wi in planned and cur_wi not in done
        nxt     = next((w for w in planned if w > cur_wi), None)

        status = "DUE" if due_now else ("OVERDUE" if overdue else "OK")
        out.append({
            "machine_id":   m["id"],
            "machine_name": m["machine_name"],
            "machine_seq":  m["machine_seq"],
            "status":       status,
            "frequency":    p.get("pm_frequency") or None,
            "machine_code": p.get("machine_code"),
            "plan_machine": p.get("machine_name"),
            "plan":         [{"week": w, "label": _week_label(w),
                              "date": _week_start_date(w, fy0).isoformat(),
                              "done": w in done} for w in planned],
            "overdue":      [{"week": w, "label": _week_label(w),
                              "date": _week_start_date(w, fy0).isoformat()}
                             for w in overdue],
            "next":         ({"week": nxt, "label": _week_label(nxt),
                              "date": _week_start_date(nxt, fy0).isoformat()}
                             if nxt is not None else None),
        })

    return {
        "line_id":      line_id,
        "line_name":    line_name,
        "financial_year": fy,
        "as_of":        today.isoformat(),
        "current_week": {"index": cur_wi, "label": _week_label(cur_wi)},
        "counts": {
            "overdue": sum(1 for r in out if r["status"] == "OVERDUE"),
            "due":     sum(1 for r in out if r["status"] == "DUE"),
            "ok":      sum(1 for r in out if r["status"] == "OK"),
            "no_plan": sum(1 for r in out if r["status"] == "NO PLAN"),
        },
        "machines": out,
    }
