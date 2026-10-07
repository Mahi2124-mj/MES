"""
Historical → Cycle Time Study  (2026-10-07)

Part-to-part cycle time of one machine over any time window (hour precision),
optionally for one model, so the average CT can be read off and output
predicted.  Values are the REAL recorded cycle times — nothing is capped
(operator: "cap kuch nahi, jo real hai wahi").  Only rows with no measurable
time (ct <= 0: backfill / phantom placeholders) are left out and counted.

Sources
    main machine (Final / line machine)  <mes_lines.db_table_name>_ct_log
    sub machine                           mes_submachine_ct_log (has model)
The main machine's log carries no model, so its model is taken from the
line's first sub machine at that moment (the model running on the line).

GET /api/ct-study/machines?line_id=
GET /api/ct-study?line_id=&machine=main|<plc id>&dt_from=&dt_to=&model=&part_code=
                 &serial_from=&serial_to=
Serial range: the cycles from the part `serial_from` up to the part
`serial_to` on that machine (full part code or the serial part of it),
searched inside dt_from..dt_to; either end may be left blank.
"""
import bisect
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_conn, dict_cursor
from routers.andon import _user_line_scope, _norm

router = APIRouter(prefix="/api/ct-study", tags=["ct-study"])

MAX_DAYS = 31
IST = timezone(timedelta(hours=5, minutes=30))


def _naive(dt):
    """Plant-local naive datetime (the main ct_log is naive local time, the
    sub-machine log is timestamptz)."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(IST).replace(tzinfo=None)
    return dt
MAX_CYCLES_RETURNED = 5000


def _line(cur, line_id, user):
    cur.execute("SELECT id, line_name, db_table_name, ideal_cycle_time "
                "FROM mes_lines WHERE id = %s", (line_id,))
    ln = cur.fetchone()
    if not ln:
        raise HTTPException(404, "Line not found")
    kind, names = _user_line_scope(user)
    if kind == "none" or (kind == "some" and _norm(ln["line_name"]) not in names):
        raise HTTPException(403, "This line is not assigned to you")
    return ln


def _machines(cur, line_id):
    cur.execute("""SELECT id, machine_name, parent_plc_id, machine_seq, ideal_cycle_time
                     FROM mes_plc_configs WHERE line_id = %s
                    ORDER BY (parent_plc_id IS NOT NULL), machine_seq NULLS LAST, id""",
                (line_id,))
    rows = cur.fetchall()
    main = next((r for r in rows if r["parent_plc_id"] is None), None)
    out = []
    if main:
        out.append({"key": "main", "plc_id": main["id"], "name": main["machine_name"],
                    "kind": "main", "ideal_ct": float(main["ideal_cycle_time"] or 0) or None})
    for r in rows:
        if r["parent_plc_id"] is not None:
            out.append({"key": str(r["id"]), "plc_id": r["id"], "name": r["machine_name"],
                        "kind": "sub", "ideal_ct": float(r["ideal_cycle_time"] or 0) or None})
    return out


@router.get("/machines")
def machines(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _line(cur, line_id, user)
        # 2026-10-07 — the line's production shifts, so the page can offer
        # "date + shift" and fill From/To with that shift's own times.
        cur.execute("""SELECT shift_name, start_time, end_time,
                              COALESCE(crosses_midnight, false) AS xm
                         FROM mes_shift_configs
                        WHERE line_id = %s AND COALESCE(is_production, TRUE) = TRUE
                          AND shift_name NOT ILIKE 'GAP%%'
                        ORDER BY start_time""", (line_id,))
        shifts = [{"name": r["shift_name"],
                   "start": r["start_time"].strftime("%H:%M") if r["start_time"] else None,
                   "end": r["end_time"].strftime("%H:%M") if r["end_time"] else None,
                   "crosses_midnight": bool(r["xm"]) or (r["start_time"] and r["end_time"]
                                                         and r["end_time"] <= r["start_time"])}
                  for r in cur.fetchall()]
        return {"machines": _machines(cur, line_id), "shifts": shifts}


def _parse_dt(s, name):
    try:
        return datetime.fromisoformat(s.replace("T", " ").strip())
    except Exception:
        raise HTTPException(400, f"{name} must be YYYY-MM-DD HH:MM")


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _nice(x):
    if x <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * e:
            return m * e
    return 10 * e


@router.get("")
def ct_study(
    line_id:   int,
    machine:   str = Query("main"),
    dt_from:   str = Query(...),
    dt_to:     str = Query(...),
    model:     Optional[str] = Query(None),
    part_code: Optional[str] = Query(None),
    serial_from: Optional[str] = Query(None),
    serial_to:   Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    t0, t1 = _parse_dt(dt_from, "From"), _parse_dt(dt_to, "To")
    if t1 <= t0:
        raise HTTPException(400, "To must be after From")
    if t1 - t0 > timedelta(days=MAX_DAYS):
        raise HTTPException(400, f"Pick at most {MAX_DAYS} days")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SET LOCAL statement_timeout = '60s'")
        ln = _line(cur, line_id, user)
        mlist = _machines(cur, line_id)
        mach = next((m for m in mlist if m["key"] == machine), None)
        if not mach:
            raise HTTPException(404, "Machine not found on this line")

        if mach["kind"] == "main":
            tbl = (ln["db_table_name"] or "").strip()
            if not tbl:
                raise HTTPException(404, "This line has no cycle log")
            cur.execute(f"""SELECT ts, ct_value AS ct, is_ng, part_code
                              FROM {tbl}_ct_log
                             WHERE ts >= %s AND ts < %s ORDER BY ts""", (t0, t1))
            cyc = [dict(r) for r in cur.fetchall()]
            # model running on the line at each moment, from the first sub
            first_sub = next((m for m in mlist if m["kind"] == "sub"), None)
            tl_ts, tl_model = [], []
            if first_sub:
                cur.execute("""SELECT ts_end, model_name FROM mes_submachine_ct_log
                                WHERE sub_plc_id = %s AND ts_end >= %s AND ts_end < %s
                                  AND model_name IS NOT NULL
                                ORDER BY ts_end""",
                            (first_sub["plc_id"], t0 - timedelta(hours=2), t1))
                for r in cur.fetchall():
                    tl_ts.append(_naive(r["ts_end"])); tl_model.append(r["model_name"])
            for c in cyc:
                i = bisect.bisect_right(tl_ts, c["ts"]) - 1
                c["model"] = tl_model[i] if i >= 0 else None
        else:
            cur.execute("""SELECT ts_end AS ts, ct_seconds AS ct, is_ng, part_code,
                                  model_name AS model
                             FROM mes_submachine_ct_log
                            WHERE sub_plc_id = %s AND ts_end >= %s AND ts_end < %s
                            ORDER BY ts_end""", (mach["plc_id"], t0, t1))
            cyc = [dict(r) for r in cur.fetchall()]
            for c in cyc:
                c["ts"] = _naive(c["ts"])

    # Serial range: from the part serial_from to the part serial_to (by when
    # each was made on this machine).  Exact code first, else partial match;
    # the FIRST match opens the range and the LAST match closes it.
    rng = None
    sf, st = (serial_from or "").strip().upper(), (serial_to or "").strip().upper()
    if sf or st:
        codes = [(c.get("part_code") or "").strip().upper() for c in cyc]
        if not any(codes):
            raise HTTPException(400, "This machine does not record part codes in this "
                                     "window; pick the Final machine for a serial range")

        def _find(q, last):
            idx = [i for i, cd in enumerate(codes) if cd == q] or \
                  [i for i, cd in enumerate(codes) if q in cd]
            if not idx:
                return None
            return idx[-1] if last else idx[0]
        i0, i1 = 0, len(cyc) - 1
        if sf:
            i0 = _find(sf, False)
            if i0 is None:
                raise HTTPException(400, f"Part '{serial_from.strip()}' not found on this "
                                         f"machine between From and To")
        if st:
            i1 = _find(st, True)
            if i1 is None:
                raise HTTPException(400, f"Part '{serial_to.strip()}' not found on this "
                                         f"machine between From and To")
        if i0 > i1:
            i0, i1 = i1, i0
        cyc = cyc[i0:i1 + 1]
        rng = {"from_part": cyc[0].get("part_code"), "from_ts": cyc[0]["ts"].strftime("%Y-%m-%d %H:%M:%S"),
               "to_part": cyc[-1].get("part_code"), "to_ts": cyc[-1]["ts"].strftime("%Y-%m-%d %H:%M:%S"),
               "cycles": len(cyc)}

    models = {}
    for c in cyc:
        models[c.get("model") or "Unknown"] = models.get(c.get("model") or "Unknown", 0) + 1
    if model:
        cyc = [c for c in cyc if (c.get("model") or "Unknown") == model]
    if part_code and part_code.strip():
        pc = part_code.strip().upper()
        cyc = [c for c in cyc if pc in (c.get("part_code") or "").upper()]

    zero = sum(1 for c in cyc if c["ct"] is None or float(c["ct"]) <= 0)
    cyc = [c for c in cyc if c["ct"] is not None and float(c["ct"]) > 0]
    vals = [float(c["ct"]) for c in cyc]
    ok_vals = [float(c["ct"]) for c in cyc if not c["is_ng"]]
    hours = (t1 - t0).total_seconds() / 3600.0
    if rng and len(cyc) > 1:          # rate over the serial range itself
        hours = max((cyc[-1]["ts"] - cyc[0]["ts"]).total_seconds() / 3600.0, 1 / 3600.0)
    sv = sorted(vals)
    stats = {
        "cycles": len(vals), "ok": len(ok_vals), "ng": len(vals) - len(ok_vals),
        "avg": round(sum(vals) / len(vals), 2) if vals else None,
        "avg_ok": round(sum(ok_vals) / len(ok_vals), 2) if ok_vals else None,
        "median": round(_pct(sv, 0.5), 2) if sv else None,
        "min": round(sv[0], 2) if sv else None,
        "max": round(sv[-1], 2) if sv else None,
        "std": round(statistics.pstdev(vals), 2) if len(vals) > 1 else None,
        "p10": round(_pct(sv, 0.10), 2) if sv else None,
        "p90": round(_pct(sv, 0.90), 2) if sv else None,
        "total_ct_hours": round(sum(vals) / 3600.0, 2),
        "rate_per_hour": round(len(vals) / hours, 1) if hours > 0 else None,
        "excluded_zero": zero,
    }

    hourly = {}
    for c in cyc:
        h = c["ts"].replace(minute=0, second=0, microsecond=0)
        b = hourly.setdefault(h, {"cycles": 0, "ok": 0, "ng": 0, "sum": 0.0,
                                  "min": None, "max": None})
        v = float(c["ct"])
        b["cycles"] += 1
        b["ng" if c["is_ng"] else "ok"] += 1
        b["sum"] += v
        b["min"] = v if b["min"] is None else min(b["min"], v)
        b["max"] = v if b["max"] is None else max(b["max"], v)
    hourly_out = [{"hour": h.strftime("%Y-%m-%d %H:00"), "cycles": b["cycles"],
                   "ok": b["ok"], "ng": b["ng"], "avg": round(b["sum"] / b["cycles"], 2),
                   "min": round(b["min"], 2), "max": round(b["max"], 2)}
                  for h, b in sorted(hourly.items())]

    hist = []
    if sv:
        top = _pct(sv, 0.95) or sv[-1]
        w = _nice(max(top, 1.0) / 20.0)
        nb = int(math.ceil(top / w)) or 1
        counts = [0] * nb
        over = 0
        for v in vals:
            i = int(v // w)
            if i >= nb:
                over += 1
            else:
                counts[i] += 1
        hist = [{"lo": round(i * w, 2), "hi": round((i + 1) * w, 2), "n": n}
                for i, n in enumerate(counts)]
        if over:
            hist.append({"lo": round(nb * w, 2), "hi": None, "n": over})

    tail = cyc[-MAX_CYCLES_RETURNED:]
    return {
        "line": {"id": ln["id"], "name": ln["line_name"]},
        "machine": mach,
        "window": {"from": t0.strftime("%Y-%m-%d %H:%M"), "to": t1.strftime("%Y-%m-%d %H:%M"),
                   "hours": round(hours, 2)},
        "models": [{"model": k, "cycles": v} for k, v in sorted(models.items(), key=lambda x: -x[1])],
        "serial_range": rng,
        "stats": stats,
        "hourly": hourly_out,
        "hist": hist,
        "cycles": [{"ts": c["ts"].strftime("%Y-%m-%d %H:%M:%S"), "ct": round(float(c["ct"]), 2),
                    "ok": not c["is_ng"], "part_code": c.get("part_code"),
                    "model": c.get("model")} for c in tail],
        "cycles_truncated": len(cyc) > len(tail),
    }
