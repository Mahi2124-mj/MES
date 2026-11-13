"""
routers/cb_history.py — Historical Data: saved copies of the Control Board
sheets (monthly) and of the Line Rejection records (daily).  2026-11-12.

Operator (12-Nov): "historical data page me control board page ke 4M change,
operator visual, track record sheet, defect record sheet ke tab bana de jinme
inka monthly data auto save hota rahe aur pdf download ka button bhi dena and
line rejection daily basis pe save honi chahiye; filter sab tab me (zone, line)".

Saved automatically by a background thread (one API process, @bg_startup):
  * every hour at about :15 (clear of the :00-:07 / :30-:37 pg_dump runs):
      - every active line's CURRENT month of the four sheets;
      - on days 1-3 of a month also the PREVIOUS month (late entries), after
        that a month is no longer touched (frozen);
      - the Red Bin rejection records of the last 3 days (status changes
        on recent records are picked up), older days frozen;
  * first run after a start: any line whose previous month has no copy yet
    gets one, and every Red Bin day since the portal started is saved once.

Each copy is exactly what the live sheet is fed (same functions), with the
"running shift" removed so the saved sheet opens read-only:
  4m      routers.control_board_4m.get_4m
  ops     routers.control_board_4m.get_ops (sheet 1 and 2)
  track   routers.track_record.track_record_sheet + the line's Standard Data
  defect  routers.line_rejection.sheet + the line leader signatures
Typed entries that the live Track Record / Defect sheets do not save are not
in the copies either.

Tables (own, created once): mes_cb_history, mes_cb_rejection_day.
Read-only for every other table; counting / OEE untouched.

GET  /api/cb-history/months?sheet=&line_id=        saved months of a line
GET  /api/cb-history/sheet?sheet=&line_id=&month=  one saved copy
GET  /api/cb-history/rejections?date_from=&date_to=&zone=&line=
GET  /api/cb-history/rejection-filters
POST /api/cb-history/save?month=YYYY-MM&line_id=   (admin) save now
"""
from __future__ import annotations

import json
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_admin
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/cb-history", tags=["cb-history"])

SHEETS = ("4m", "ops", "track", "defect")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
REJ_REFRESH_DAYS = 3          # Red Bin days re-saved every run (status changes)
PREV_MONTH_UNTIL_DAY = 3      # previous month re-saved on days 1..3
_ready = False
_lock = threading.Lock()      # one save run at a time (thread + admin button)


# ── schema ───────────────────────────────────────────────────────────────
def _ensure(cur) -> None:
    global _ready
    if _ready:
        return
    cur.execute("""SELECT count(*) AS n FROM pg_class
                    WHERE relname IN ('mes_cb_history', 'mes_cb_rejection_day') AND relkind = 'r'""")
    if int((cur.fetchone() or {}).get("n") or 0) < 2:
        with get_conn() as c2:                      # own transaction
            k = c2.cursor()
            k.execute("SET LOCAL lock_timeout = '3s'")
            k.execute("""CREATE TABLE IF NOT EXISTS mes_cb_history (
                    sheet     VARCHAR(10) NOT NULL,
                    line_id   INTEGER     NOT NULL,
                    month     CHAR(7)     NOT NULL,
                    data      JSONB       NOT NULL,
                    saved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (sheet, line_id, month))""")
            k.execute("""CREATE TABLE IF NOT EXISTS mes_cb_rejection_day (
                    rec_date   DATE        NOT NULL,
                    record_id  TEXT        NOT NULL,
                    zone_name  TEXT,
                    line_name  TEXT,
                    data       JSONB       NOT NULL,
                    saved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (rec_date, record_id))""")
            k.execute("CREATE INDEX IF NOT EXISTS ix_cb_rej_day_line ON mes_cb_rejection_day (line_name, rec_date)")
    _ready = True


def _jsonable(o):
    """Round-trip through JSON (dates, Decimals → strings / floats)."""
    return json.loads(json.dumps(o, default=lambda x: float(x) if hasattr(x, "as_integer_ratio") else str(x)))


# ── building one saved copy ──────────────────────────────────────────────
def _readonly_4m(d: dict) -> dict:
    d = dict(d)
    d["current"] = None
    d["rows"] = [{**r, "editable": False, "qty_open": False} for r in (d.get("rows") or [])]
    return d


def _readonly_ops(d: dict) -> dict:
    d = dict(d)
    d["current"] = None
    d["open_days"] = []
    return d


def _std(cur, line_id: int) -> dict:
    cur.execute("SELECT cycle_time, target FROM mes_std_track_record WHERE line_id = %s", (line_id,))
    r = cur.fetchone() or {}
    return {"cycle_time": r.get("cycle_time"), "target": r.get("target")}


def _sign_of(track: dict) -> dict:
    """The Defect Recording Sheet's signature data (same as ControlBoard.jsx)."""
    shifts = {}
    for sh, days in ((track or {}).get("shifts") or {}).items():
        shifts[sh] = {d: {"S1": x.get("S1"), "S1_name": x.get("S1_name")}
                      for d, x in (days or {}).items() if x and (x.get("S1") or x.get("S1_name"))}
    return {"line_id": (track or {}).get("line_id"), "month": (track or {}).get("month"),
            "shifts": shifts, "leaders": (track or {}).get("leaders") or {}}


def _cells(doc: str, line_id: int, month: str) -> dict:
    """Entries typed into the live sheet (control_board_cells.py), so the copy shows them."""
    try:
        from routers.control_board_cells import read_cells
        with get_conn() as conn:
            return read_cells(dict_cursor(conn), doc, line_id, month)
    except Exception as e:
        print(f"[CB-HIST] typed entries of {doc} line {line_id} {month} not read: {str(e)[:100]}", flush=True)
        return {}


def build_line_month(line_id: int, month: str) -> dict:
    """{sheet: data} for one line and month; a sheet that fails is left out."""
    from routers.control_board_4m import get_4m, get_ops
    from routers.track_record import track_record_sheet
    from routers.line_rejection import sheet as rej_sheet
    out, errs = {}, {}
    try:
        out["4m"] = _readonly_4m(get_4m(line_id=line_id, month=month, user=None))
    except Exception as e:
        errs["4m"] = str(e)[:120]
    try:
        out["ops"] = {"sheets": {str(n): _readonly_ops(get_ops(line_id=line_id, month=month, sheet=n, user=None))
                                 for n in (1, 2)}}
    except Exception as e:
        errs["ops"] = str(e)[:120]
    track = None
    try:
        track = track_record_sheet(line_id=line_id, month=month, user=None)
        with get_conn() as conn:
            std = _std(dict_cursor(conn), line_id)
        out["track"] = {"sheet": track, "std": std, "cells": _cells("track-record", line_id, month)}
    except Exception as e:
        errs["track"] = str(e)[:120]
    try:
        out["defect"] = {"rej": rej_sheet(line_id=line_id, month=month, include_test=0, user=None),
                         "sign": _sign_of(track) if track else None,
                         "cells": {"defect-std": _cells("defect-std", line_id, month),
                                   "defect-rec": _cells("defect-rec", line_id, month)}}
    except Exception as e:
        errs["defect"] = str(e)[:120]
    if errs:
        print(f"[CB-HIST] line {line_id} {month}: not saved {errs}", flush=True)
    return out


def save_line_month(line_id: int, month: str) -> int:
    data = build_line_month(line_id, month)
    if not data:
        return 0
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SET LOCAL lock_timeout = '3s'")
        for sheet, d in data.items():
            cur.execute("""INSERT INTO mes_cb_history (sheet, line_id, month, data, saved_at)
                           VALUES (%s, %s, %s, %s::jsonb, now())
                           ON CONFLICT (sheet, line_id, month)
                           DO UPDATE SET data = EXCLUDED.data, saved_at = now()""",
                        (sheet, line_id, month, json.dumps(_jsonable(d))))
    return len(data)


def _active_lines() -> list:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM mes_lines WHERE is_active ORDER BY id")
        return [int(r["id"]) for r in cur.fetchall()]


def _prev_month(d: date) -> str:
    p = d.replace(day=1) - timedelta(days=1)
    return p.strftime("%Y-%m")


# ── Red Bin rejections, per day ──────────────────────────────────────────
def save_rejections(date_from: date, date_to: date) -> int:
    """Upsert every (non-test) Red Bin record booked date_from..date_to (IST)."""
    from routers.line_rejection import records as rej_records, _section, _due
    res = rej_records(date_from=str(date_from), date_to=str(date_to), zone_id=None, line_id=None,
                      shift=None, status=None, include_test=0, q=None, limit=3000, user=None)
    rows = res.get("rows") or []
    if res.get("more"):
        print(f"[CB-HIST] Red Bin {date_from}..{date_to}: more than 3000 records, saved the first 3000", flush=True)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SET LOCAL lock_timeout = '3s'")
        for r in rows:
            r = _jsonable(r)
            r["section"] = _section(r.get("machine"))
            r["due"] = _due(r.get("reason"))
            day = str(r.get("at") or "")[:10]
            if not day:
                continue
            cur.execute("""INSERT INTO mes_cb_rejection_day (rec_date, record_id, zone_name, line_name, data, saved_at)
                           VALUES (%s, %s, %s, %s, %s::jsonb, now())
                           ON CONFLICT (rec_date, record_id)
                           DO UPDATE SET zone_name = EXCLUDED.zone_name, line_name = EXCLUDED.line_name,
                                         data = EXCLUDED.data, saved_at = now()""",
                        (day, str(r["id"]), r.get("zone_name"), r.get("line_name"), json.dumps(r)))
    return len(rows)


# ── the run ──────────────────────────────────────────────────────────────
def run_save(first: bool = False, months: Optional[list] = None, line_ids: Optional[list] = None) -> dict:
    """One save run.  `months` / `line_ids` given = only those (admin button)."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "a save run is already going"}
    t0 = _time.time()
    try:
        today = date.today()
        lines = line_ids or _active_lines()
        if months:
            plan = {m: lines for m in months}
        else:
            cur_m, prev_m = today.strftime("%Y-%m"), _prev_month(today)
            plan = {cur_m: lines}
            if today.day <= PREV_MONTH_UNTIL_DAY:
                plan[prev_m] = lines
            elif first:                                   # previous month never saved yet
                with get_conn() as conn:
                    cur = dict_cursor(conn)
                    _ensure(cur)
                    cur.execute("SELECT DISTINCT line_id FROM mes_cb_history WHERE month = %s", (prev_m,))
                    have = {int(r["line_id"]) for r in cur.fetchall()}
                missing = [l for l in lines if l not in have]
                if missing:
                    plan[prev_m] = missing
        n = 0
        for month, ls in plan.items():
            for lid in ls:
                try:
                    n += save_line_month(lid, month)
                except Exception as e:
                    print(f"[CB-HIST] line {lid} {month} save failed: {str(e)[:160]}", flush=True)
        rej = 0
        if not months:
            try:
                d_from = today - timedelta(days=REJ_REFRESH_DAYS - 1)
                if first:
                    with get_conn() as conn:
                        cur = dict_cursor(conn)
                        _ensure(cur)
                        cur.execute("SELECT count(*) AS n FROM mes_cb_rejection_day")
                        if int((cur.fetchone() or {}).get("n") or 0) == 0:
                            d_from = date(2026, 4, 1)     # Red Bin portal's first record: 2-Apr-2026
                rej = save_rejections(d_from, today)
            except Exception as e:
                print(f"[CB-HIST] Red Bin save failed: {str(e)[:160]}", flush=True)
        out = {"sheets_saved": n, "months": {m: len(ls) for m, ls in plan.items()},
               "rejections_saved": rej, "seconds": round(_time.time() - t0, 1)}
        print(f"[CB-HIST] saved {out}", flush=True)
        return out
    finally:
        _lock.release()


def start_cb_history(minute: int = 15) -> None:
    """Hourly saver; first run 3 minutes after the start."""
    def _loop():
        _time.sleep(180)
        first = True
        while True:
            try:
                run_save(first=first)
            except Exception as e:
                print(f"[CB-HIST] run error: {str(e)[:160]}", flush=True)
            first = False
            now = datetime.now()
            nxt = now.replace(minute=minute, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(hours=1)
            _time.sleep(max(60, (nxt - now).total_seconds()))
    threading.Thread(target=_loop, name="cb-history", daemon=True).start()
    print("[CB-HIST] hourly saver started", flush=True)


# ── API ──────────────────────────────────────────────────────────────────
def _allowed_lines(user) -> Optional[set]:
    """None = every line; else the line ids this login may see (as /api/lines/)."""
    if not user or user.get("role") == "admin":
        return None
    from routers.lines import list_lines
    return {int(l["id"]) for l in (list_lines(plant_id=None, user=user) or [])}


def _check_line(user, line_id: int) -> None:
    ok = _allowed_lines(user)
    if ok is not None and line_id not in ok:
        raise HTTPException(403, "This line is not assigned to your login")


@router.get("/months")
def months(sheet: str = Query(...), line_id: int = Query(...), user=Depends(get_current_user)):
    if sheet not in SHEETS:
        raise HTTPException(400, "unknown sheet")
    _check_line(user, line_id)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT month, saved_at FROM mes_cb_history
                        WHERE sheet = %s AND line_id = %s ORDER BY month DESC""", (sheet, line_id))
        return [{"month": r["month"], "saved_at": r["saved_at"].isoformat(timespec="seconds")}
                for r in cur.fetchall()]


@router.get("/sheet")
def saved_sheet(sheet: str = Query(...), line_id: int = Query(...),
                month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), user=Depends(get_current_user)):
    if sheet not in SHEETS:
        raise HTTPException(400, "unknown sheet")
    _check_line(user, line_id)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT data, saved_at FROM mes_cb_history
                        WHERE sheet = %s AND line_id = %s AND month = %s""", (sheet, line_id, month))
        r = cur.fetchone()
    if not r:
        return {"sheet": sheet, "line_id": line_id, "month": month, "saved_at": None, "data": None}
    return {"sheet": sheet, "line_id": line_id, "month": month,
            "saved_at": r["saved_at"].isoformat(timespec="seconds"), "data": r["data"]}


@router.get("/rejection-filters")
def rejection_filters(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT DISTINCT zone_name, line_name FROM mes_cb_rejection_day
                        ORDER BY zone_name, line_name""")
        rows = cur.fetchall()
        cur.execute("SELECT min(rec_date) AS d0, max(rec_date) AS d1, max(saved_at) AS at FROM mes_cb_rejection_day")
        m = cur.fetchone() or {}
    return {"pairs": [{"zone": r["zone_name"], "line": r["line_name"]} for r in rows],
            "first_day": str(m["d0"]) if m.get("d0") else None,
            "last_day": str(m["d1"]) if m.get("d1") else None,
            "saved_at": m["at"].isoformat(timespec="seconds") if m.get("at") else None}


@router.get("/rejections")
def rejections(date_from: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
               date_to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
               zone: Optional[str] = None, line: Optional[str] = None,
               user=Depends(get_current_user)):
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if d1 < d0:
        raise HTTPException(400, "date_to is before date_from")
    if (d1 - d0).days > 366:
        raise HTTPException(400, "choose at most one year")
    sql = ["SELECT rec_date, data, saved_at FROM mes_cb_rejection_day WHERE rec_date BETWEEN %s AND %s"]
    p: list = [d0, d1]
    if zone:
        sql.append("AND zone_name = %s"); p.append(zone)
    if line:
        sql.append("AND line_name = %s"); p.append(line)
    sql.append("ORDER BY rec_date, data->>'at'")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute(" ".join(sql), p)
        rows = cur.fetchall()
    days: dict = {}
    for r in rows:
        k = str(r["rec_date"])
        days.setdefault(k, {"date": k, "records": [], "qty": 0, "saved_at": None})
        days[k]["records"].append(r["data"])
        days[k]["qty"] += int((r["data"] or {}).get("qty") or 0)
        at = r["saved_at"].isoformat(timespec="seconds")
        days[k]["saved_at"] = max(days[k]["saved_at"] or at, at)
    return {"days": sorted(days.values(), key=lambda x: x["date"], reverse=True)}


@router.post("/save")
def save_now(month: Optional[str] = None, line_id: Optional[int] = None, admin=Depends(require_admin)):
    """Admin: save now (a given month, else the normal run)."""
    if month and not MONTH_RE.match(month):
        raise HTTPException(400, "month must be YYYY-MM")
    return run_save(months=[month] if month else None, line_ids=[line_id] if line_id else None)
