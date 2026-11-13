"""
routers/track_record.py — MES data for the Control Board's Track Record Sheet.
2026-11-06.

GET /api/standard-data/track-record/sheet?line_id=<id>&month=YYYY-MM

For every day of the month and shift A / B (operator's definitions, 6-Nov):
  A  regular man-hour (min) = regular working minutes × operators
       regular working minutes = shift span − scheduled breaks (470 on SS / Rec)
       operators = distinct operators in Shift Allocation (line leader not
       counted); an unsaved shift borrows the latest saved same-shift crew of
       the same Monday–Sunday week, as the Hourly Sheet does
  B  OT man-hour (min) = OT minutes actually run × operators
       OT = the continuous run of cycles right after the shift end (first
       cycle within 15 min, no gap over 15 min, at least 20 cycles — the PEFF
       rule), up to the next production shift, minus breaks; cycles made
       before OT was switched on (logged GAP) count too
  C  A + B          D  regular OK parts          E  OT OK parts
  F  D + E          G  C / F × 60, one decimal
  M1 machine inspection = the collector's shift-start setup (loss_setup_seconds)
  M2 change over = andon "Model Setup"    M4 quality  = andon "Quality"
  M5 no parts    = andon "Material"       M6 others   = andon "Other Loss" (+ unknown types)
       andon = button time, cut to the shift window
  M3 stop time M/C = maintenance slips (auto + manual, overlaps merged)
                     + tool-room slips, cut to the shift window
  S1 line leader of the shift (signature image from Employee Master)
Only finished shifts with at least 50 OK parts are filled (stray / test cycles
on a holiday gave G = 9400; a running shift paired a full day's man-hours with
half a day's parts).
Read-only: MES DB + maintenance_db, nothing is written.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_conn, dict_cursor
from routers.andon import _maint_conn, _merge, _loss_of, andon_keys, _NORM_SQL

router = APIRouter(prefix="/api/standard-data", tags=["standard-data"])

SHIFTS = ("A", "B")
OT_START_GAP = timedelta(minutes=15)     # first OT cycle within 15 min of the shift end
OT_MAX_GAP = timedelta(minutes=15)       # the run ends at the first gap over 15 min
OT_MIN_CYCLES = 20
BURST_PER_MIN = 10                       # restart backfill writes ~500 rows in 1-2 min
MIN_PARTS = 50                           # fewer = stray / test cycles, not a worked shift


def _t(v) -> time:
    """time / timetz → naive time."""
    return v.replace(tzinfo=None) if isinstance(v, time) else v


def _window(d: date, cfg: dict):
    s0 = datetime.combine(d, _t(cfg["start_time"]))
    s1 = datetime.combine(d, _t(cfg["end_time"]))
    if cfg.get("crosses_midnight") or s1 <= s0:
        s1 += timedelta(days=1)
    return s0, s1


def _break_windows(breaks: list, shift: str | None, around: date):
    """Break intervals near `around` (day before .. day after); shift=None = all."""
    out = []
    for b in breaks:
        app = re.sub(r"\s", "", b.get("applies_to_shifts") or "")
        if shift and app and shift not in app.split(","):
            continue
        for k in (-1, 0, 1):
            dd = around + timedelta(days=k)
            x = datetime.combine(dd, _t(b["start_time"]))
            y = datetime.combine(dd, _t(b["end_time"]))
            if b.get("crosses_midnight") or y <= x:
                y += timedelta(days=1)
            out.append((x, y))
    return _merge(out)


def _overlap_s(a: datetime, b: datetime, ivs) -> float:
    return sum(max(0.0, (min(b, y) - max(a, x)).total_seconds()) for x, y in ivs)


def _clip_minutes(ivs, a: datetime, b: datetime) -> float:
    return sum(max(0.0, (min(b, y) - max(a, x)).total_seconds()) for x, y in ivs) / 60.0


def _table_exists(cur, name: str) -> bool:
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        return False
    cur.execute("SELECT to_regclass(%s) AS t", (name,))
    return bool((cur.fetchone() or {}).get("t"))


def _ot_run(post: list, s1: datetime, n_start: datetime):
    """OT run after the shift end from (ts, is_ng) cycles → (end_ts, ok_count) or None."""
    pts = [p for p in post if s1 <= p[0] < n_start]
    if not pts:
        return None
    per_min: dict = {}
    for ts, _ in pts:
        k = ts.replace(second=0, microsecond=0)
        per_min[k] = per_min.get(k, 0) + 1
    pts = [p for p in pts if per_min[p[0].replace(second=0, microsecond=0)] <= BURST_PER_MIN]
    if not pts or pts[0][0] - s1 > OT_START_GAP:
        return None
    run = [pts[0]]
    for p in pts[1:]:
        if p[0] - run[-1][0] > OT_MAX_GAP:
            break
        run.append(p)
    if len(run) < OT_MIN_CYCLES:
        return None
    return run[-1][0], sum(1 for _, ng in run if not ng)


def _slip_ivs(rows) -> list:
    out = []
    for r in rows:
        try:
            a = datetime.combine(r["bd_start_date"], datetime.strptime(str(r["bd_start_time"]).strip()[:5], "%H:%M").time())
        except Exception:
            continue
        b = None
        if r.get("bd_ok_time"):
            try:
                b = datetime.combine(r.get("bd_end_date") or r["bd_start_date"],
                                     datetime.strptime(str(r["bd_ok_time"]).strip()[:5], "%H:%M").time())
            except Exception:
                b = None
        if b is None and r.get("mc_down_time_minutes"):
            b = a + timedelta(minutes=float(r["mc_down_time_minutes"]))
        if b is not None and b < a:
            b += timedelta(days=1)
        if b is not None and b > a:
            out.append((a, b))
    return out


@router.get("/track-record/sheet")
def track_record_sheet(line_id: int = Query(...), month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                       user=Depends(get_current_user)):
    y, m = int(month[:4]), int(month[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(400, "month must be YYYY-MM")
    ndays = calendar.monthrange(y, m)[1]
    m0, m1 = date(y, m, 1), date(y, m, ndays)
    today = date.today()

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, line_name, db_table_name, zone_id FROM mes_lines WHERE id = %s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")
        cur.execute("""SELECT shift_name, start_time, end_time, crosses_midnight, is_production
                         FROM mes_shift_configs WHERE line_id = %s""", (line_id,))
        cfgs = {r["shift_name"]: r for r in cur.fetchall()}
        prod_cfgs = [c for k, c in cfgs.items()
                     if not k.upper().startswith("GAP") and c.get("is_production") is not False]
        # breaks; a line without its own (YNC / YSD Recliner) borrows its zone's
        cur.execute("SELECT * FROM mes_break_configs WHERE line_id = %s", (line_id,))
        breaks = cur.fetchall()
        if not breaks and line.get("zone_id"):
            cur.execute("""SELECT b.* FROM mes_break_configs b
                             JOIN mes_lines l ON l.id = b.line_id
                            WHERE l.zone_id = %s
                              AND b.line_id = (SELECT b2.line_id FROM mes_break_configs b2
                                                 JOIN mes_lines l2 ON l2.id = b2.line_id
                                                WHERE l2.zone_id = %s ORDER BY b2.line_id LIMIT 1)""",
                        (line["zone_id"], line["zone_id"]))
            breaks = cur.fetchall()

        tbl = line.get("db_table_name") or ""
        ct = f"{tbl}_ct_log"
        reg_cnt: dict = {}
        post: list = []
        if _table_exists(cur, ct):
            # regular OK parts: shift-tagged cycles inside the shift window
            for sh in SHIFTS:
                c = cfgs.get(sh)
                if not c:
                    continue
                cur.execute(f"""
                    SELECT l.record_date AS d,
                           COUNT(*) FILTER (WHERE NOT COALESCE(l.is_ng, FALSE)
                                              AND l.ts >= l.record_date + %(st)s::time
                                              AND l.ts <  l.record_date + %(en)s::time + %(add)s::interval) AS reg
                      FROM {ct} l
                     WHERE l.shift_name = %(sh)s AND l.record_date BETWEEN %(m0)s AND %(m1)s
                     GROUP BY 1""",
                    {"st": _t(c["start_time"]), "en": _t(c["end_time"]), "sh": sh, "m0": m0, "m1": m1,
                     "add": "1 day" if (c.get("crosses_midnight") or _t(c["end_time"]) <= _t(c["start_time"])) else "0"})
                for r in cur.fetchall():
                    reg_cnt[(r["d"], sh)] = int(r["reg"] or 0)
            # cycles after a shift end: shift-tagged outside the window + GAP
            cur.execute(f"""
                SELECT l.ts, COALESCE(l.is_ng, FALSE) AS ng FROM {ct} l
                 WHERE l.record_date BETWEEN %(m0)s AND %(m2)s
                   AND (l.shift_name LIKE 'GAP%%'
                        OR (l.shift_name = 'A' AND %(a)s
                            AND NOT (l.ts >= l.record_date + %(ast)s::time AND l.ts < l.record_date + %(aen)s::time + %(aadd)s::interval))
                        OR (l.shift_name = 'B' AND %(b)s
                            AND NOT (l.ts >= l.record_date + %(bst)s::time AND l.ts < l.record_date + %(ben)s::time + %(badd)s::interval)))
                 ORDER BY l.ts""",
                {"m0": m0, "m2": m1 + timedelta(days=1),
                 "a": "A" in cfgs, "b": "B" in cfgs,
                 "ast": _t(cfgs["A"]["start_time"]) if "A" in cfgs else time(0),
                 "aen": _t(cfgs["A"]["end_time"]) if "A" in cfgs else time(0),
                 "aadd": "1 day" if "A" in cfgs and (cfgs["A"].get("crosses_midnight") or _t(cfgs["A"]["end_time"]) <= _t(cfgs["A"]["start_time"])) else "0",
                 "bst": _t(cfgs["B"]["start_time"]) if "B" in cfgs else time(0),
                 "ben": _t(cfgs["B"]["end_time"]) if "B" in cfgs else time(0),
                 "badd": "1 day" if "B" in cfgs and (cfgs["B"].get("crosses_midnight") or _t(cfgs["B"]["end_time"]) <= _t(cfgs["B"]["start_time"])) else "0"})
            post = [(r["ts"].replace(tzinfo=None), r["ng"]) for r in cur.fetchall()]

        # shift-start setup (machine inspection), last row of each shift
        setup: dict = {}
        if _table_exists(cur, tbl):
            cur.execute("""SELECT 1 FROM information_schema.columns
                            WHERE table_name = %s AND column_name = 'loss_setup_seconds'""", (tbl,))
            if cur.fetchone():
                cur.execute(f"""SELECT DISTINCT ON (record_date, shift_name) record_date, shift_name, loss_setup_seconds
                                  FROM {tbl} WHERE record_date BETWEEN %s AND %s AND shift_name IN ('A','B')
                                 ORDER BY record_date, shift_name, id DESC""", (m0, m1))
                for r in cur.fetchall():
                    setup[(r["record_date"], r["shift_name"])] = float(r["loss_setup_seconds"] or 0)

        # operators (distinct, removed rows excluded); a week back for carry-forward
        cur.execute("""SELECT shift_date, shift_name, COUNT(DISTINCT operator_id) AS n
                         FROM mes_manpower_allocations
                        WHERE line_id = %s AND removed_at IS NULL AND shift_date BETWEEN %s AND %s
                        GROUP BY 1, 2""", (line_id, m0 - timedelta(days=7), m1))
        alloc = {(r["shift_date"], r["shift_name"]): int(r["n"]) for r in cur.fetchall()}

        # line leader per shift + signatures of those leaders
        cur.execute("""SELECT sa.shift_date, sa.shift_name, sa.leader_id, ad.username
                         FROM mes_leader_shift_alloc sa LEFT JOIN mes_admin ad ON ad.id = sa.leader_id
                        WHERE sa.line_id = %s AND sa.shift_date BETWEEN %s AND %s""", (line_id, m0, m1))
        lead = {(r["shift_date"], r["shift_name"]): (r["leader_id"], r["username"]) for r in cur.fetchall()}
        leaders: dict = {}
        ids = sorted({v[0] for v in lead.values() if v[0]})
        if ids:
            cur.execute("""SELECT ad.id, ad.username, m.signature_image FROM mes_admin ad
                             LEFT JOIN mes_leader_meta m ON m.admin_id = ad.id WHERE ad.id = ANY(%s)""", (ids,))
            for r in cur.fetchall():
                sig = r.get("signature_image") or None
                leaders[str(r["id"])] = {"name": r["username"],
                                         "signature": sig if sig and str(sig).startswith("data:image/") else None}

    # andon calls + breakdown slips (maintenance_db, read-only)
    keys = andon_keys(line["line_name"])
    calls, maint_iv, tool_iv = [], [], []
    w0 = datetime.combine(m0, time(0)) - timedelta(days=1)
    w1 = datetime.combine(m1, time(0)) + timedelta(days=2)
    if keys:
        try:
            with _maint_conn() as mc:
                mcur = dict_cursor(mc)
                mcur.execute("SET TRANSACTION READ ONLY")
                mcur.execute(f"""SELECT display_name, started_at, ended_at FROM andon_history
                                  WHERE {_NORM_SQL} = ANY(%s) AND started_at < %s AND ended_at > %s""", (keys, w1, w0))
                calls = [(r["display_name"], r["started_at"], r["ended_at"]) for r in mcur.fetchall()]
                mcur.execute(f"""SELECT display_name, started_at FROM andon_system
                                  WHERE state = 'OPEN' AND {_NORM_SQL} = ANY(%s) AND started_at < %s""", (keys, w1))
                now = datetime.now()
                calls += [(r["display_name"], r["started_at"], now) for r in mcur.fetchall()]
                d0, d1 = m0 - timedelta(days=1), m1 + timedelta(days=1)
                for tname, bucket in (("maintenance_auto_breakdown_slip", maint_iv),
                                      ("maintenance_breakdown_data", maint_iv),
                                      ("toolroom_auto_breakdown_slip", tool_iv)):
                    try:
                        mcur.execute(f"""SELECT bd_start_date, bd_start_time, bd_end_date, bd_ok_time, mc_down_time_minutes
                                           FROM {tname} WHERE {_NORM_SQL} = ANY(%s)
                                            AND bd_start_date BETWEEN %s AND %s""", (keys, d0, d1))
                        bucket.extend(_slip_ivs(mcur.fetchall()))
                    except Exception:
                        mc.rollback()
                mc.rollback()
        except HTTPException:
            pass                          # maintenance_db down: andon / breakdown rows stay empty
    calls = [(n, a.replace(tzinfo=None) if a else a, b.replace(tzinfo=None) if b else b)
             for n, a, b in calls if a and b and b > a]
    by_bucket = {"setup": [], "quality": [], "material": [], "others": []}
    for n, a, b in calls:
        k = _loss_of(n)
        if k in by_bucket:
            by_bucket[k].append((a, b))
    maint_iv, tool_iv = _merge(maint_iv), _merge(tool_iv)

    # next production shift start after a shift end
    def next_start(s1: datetime) -> datetime:
        best = s1 + timedelta(hours=12)
        for c in prod_cfgs:
            t = datetime.combine(s1.date(), _t(c["start_time"]))
            if t <= s1:
                t += timedelta(days=1)
            best = min(best, t)
        return best

    def operators(d: date, sh: str):
        if (d, sh) in alloc:
            return alloc[(d, sh)], None
        wk0 = d - timedelta(days=d.weekday())
        for k in range(1, 7):
            dd = d - timedelta(days=k)
            if dd < wk0:
                break
            if (dd, sh) in alloc:
                return alloc[(dd, sh)], dd.isoformat()
        return None, None

    now = datetime.now()
    shifts_out: dict = {sh: {} for sh in SHIFTS}
    reg_minutes: dict = {}
    for sh in SHIFTS:
        c = cfgs.get(sh)
        if not c:
            continue
        for day in range(1, ndays + 1):
            d = date(y, m, day)
            if d > today:
                break
            s0, s1 = _window(d, c)
            if s1 > now:
                break                      # shift not finished yet
            reg_min = ((s1 - s0).total_seconds() - _overlap_s(s0, s1, _break_windows(breaks, sh, d))) / 60.0
            reg_minutes[sh] = round(reg_min)
            D = reg_cnt.get((d, sh), 0)
            run = _ot_run(post, s1, next_start(s1)) if post else None
            E, ot_min = 0, 0.0
            if run:
                end, E = run
                ot_min = max(0.0, ((end - s1).total_seconds()
                                   - _overlap_s(s1, end, _break_windows(breaks, None, s1.date()))) / 60.0)
            if D + E < MIN_PARTS:
                continue                   # shift did not run
            ops, ops_from = operators(d, sh)
            A = round(reg_min * ops) if ops else None
            B = round(ot_min * ops) if (ops and ot_min) else (0 if ops else None)
            C = (A or 0) + (B or 0) if ops else None
            F = D + E
            G = round(C / F * 60, 1) if (C and F) else None
            lid, lname = lead.get((d, sh), (None, None))
            shifts_out[sh][str(day)] = {
                "A": A, "B": B, "C": C, "D": D, "E": E, "F": F, "G": G,
                "M1": round(setup.get((d, sh), 0) / 60.0),
                "M2": round(_clip_minutes(by_bucket["setup"], s0, s1)),
                "M3": round(_clip_minutes(maint_iv, s0, s1) + _clip_minutes(tool_iv, s0, s1)),
                "M4": round(_clip_minutes(by_bucket["quality"], s0, s1)),
                "M5": round(_clip_minutes(by_bucket["material"], s0, s1)),
                "M6": round(_clip_minutes(by_bucket["others"], s0, s1)),
                "S1": str(lid) if lid else None,
                "S1_name": lname,
                "ops": ops, "ops_from": ops_from, "ot_min": round(ot_min),
            }
    # 2026-11-12: the leader of EVERY allocated shift (also days MES has no
    # values for) — the sheets show a signature only on a day whose column
    # has values, typed ones included, never on an empty day
    leads = {sh: {} for sh in ("A", "B")}
    for (d, sh), (lid, _n) in lead.items():
        if lid and sh in leads and d.year == y and d.month == m:
            leads[sh][str(d.day)] = str(lid)
    return {"line_id": line_id, "line_name": line["line_name"], "month": month, "days": ndays,
            "regular_minutes": reg_minutes, "shifts": shifts_out, "leaders": leaders, "leads": leads}
