"""
routers/working_calendar.py — Annual (FY) working calendar for MES, 2026-10-08.

Operator: "d-qmp wale me jo annual calender h vo utha k isme ek production panel
me module add kr de; agar koi non working day working ho gya to usme edit ho
paye; vo hi peff me consider ho."

WHAT IT IS
  * The company calendar is maintained in D-QMP (holidays, additional holidays,
    shutdown ranges, Sunday-working days, events, weekly off).  MES imports it
    (read-only, straight from D-QMP's SQLite file) into its own tables, so MES
    never depends on D-QMP being up.
  * On top of that, MES keeps its OWN per-day edits (source='mes'): a day the
    plant decides to run, or to close, after the calendar was published.  An
    MES edit always wins; a re-import from D-QMP never touches it.
  * One rule decides a day, the same as D-QMP's sla.is_working_day():
        MES edit  >  working exception  >  off day (holiday/shutdown/…)  >  weekly off
  * Used by the PEFF sheet (Holiday / Regular working time) and the Plant
    Overview page (last working day).

SAFETY
  Only its own two tables are written (mes_working_calendar_fy,
  mes_working_calendar).  The D-QMP database is opened read-only (sqlite
  mode=ro); nothing in D-QMP is changed.
"""
from datetime import date, datetime, timedelta
from typing import Optional
import json
import os
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/working-calendar", tags=["working-calendar"])

DQMP_DB = os.getenv("DQMP_DB", "/opt/dqmp/sqa/backend/sqmp.db")
EDIT_ROLES = {"admin", "plant_head", "production_incharge"}
_WD = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
       "friday": 4, "saturday": 5, "sunday": 6}
KIND_LABEL = {"holiday": "Holiday", "additional": "Additional holiday",
              "shutdown": "Plant shutdown", "sunday_working": "Sunday working",
              "working_day": "Working day", "event": "Company event",
              "weekly_off": "Weekly off", "mes_working": "Working (MES edit)",
              "mes_off": "Non-working (MES edit)"}


@once
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_working_calendar_fy (
                fy                 VARCHAR(12) PRIMARY KEY,
                title              TEXT,
                company_name       TEXT,
                period_start       DATE NOT NULL,
                period_end         DATE NOT NULL,
                week_off_json      TEXT DEFAULT '["Sunday"]',
                total_working_days INTEGER,
                is_active          BOOLEAN DEFAULT FALSE,
                imported_at        TIMESTAMPTZ DEFAULT now()
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_working_calendar (
                id               SERIAL PRIMARY KEY,
                cal_date         DATE NOT NULL,
                end_date         DATE,
                name             TEXT,
                kind             VARCHAR(20) NOT NULL,
                is_working       BOOLEAN NOT NULL DEFAULT FALSE,
                recurring_annual BOOLEAN NOT NULL DEFAULT FALSE,
                fy               VARCHAR(12),
                source           VARCHAR(8) NOT NULL DEFAULT 'dqmp',
                dqmp_id          INTEGER,
                updated_by       VARCHAR(80),
                updated_at       TIMESTAMPTZ DEFAULT now()
            )""")
        cur.execute("""CREATE INDEX IF NOT EXISTS ix_mes_wcal_date
                         ON mes_working_calendar (cal_date)""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_mes_wcal_mes_day
                         ON mes_working_calendar (cal_date) WHERE source = 'mes'""")
        conn.commit()


# ── import from D-QMP ─────────────────────────────────────────────────────
def _d(v):
    if not v:
        return None
    return date.fromisoformat(str(v)[:10])


def import_from_dqmp(cur, fy: Optional[str] = None) -> dict:
    """Copy D-QMP's company calendar (plant NULL/'') into MES.  Replaces the
    source='dqmp' rows of each imported FY; MES edits are never touched."""
    if not os.path.exists(DQMP_DB):
        raise HTTPException(503, f"D-QMP calendar not found at {DQMP_DB}")
    src = sqlite3.connect(f"file:{DQMP_DB}?mode=ro", uri=True, timeout=5)
    try:
        s = src.cursor()
        hdr_sql = ("SELECT fy, title, company_name, period_start, period_end, "
                   "week_off_json, total_working_days, is_active FROM working_calendar")
        hdrs = s.execute(hdr_sql + (" WHERE fy = ?" if fy else ""), (fy,) if fy else ()).fetchall()
        if not hdrs:
            raise HTTPException(404, f"D-QMP has no working calendar{' for ' + fy if fy else ''}")
        out = {}
        for (f, title, comp, p0, p1, wk, total, active) in hdrs:
            rows = s.execute("""SELECT id, holiday_date, end_date, name, kind, is_working,
                                       recurring_annual
                                  FROM holiday_calendar
                                 WHERE (plant IS NULL OR plant = '')
                                   AND fy = ?""", (f,)).fetchall()
            cur.execute("""INSERT INTO mes_working_calendar_fy
                             (fy, title, company_name, period_start, period_end,
                              week_off_json, total_working_days, is_active, imported_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
                           ON CONFLICT (fy) DO UPDATE SET
                             title=EXCLUDED.title, company_name=EXCLUDED.company_name,
                             period_start=EXCLUDED.period_start, period_end=EXCLUDED.period_end,
                             week_off_json=EXCLUDED.week_off_json,
                             total_working_days=EXCLUDED.total_working_days,
                             is_active=EXCLUDED.is_active, imported_at=now()""",
                        (f, title, comp, _d(p0), _d(p1), wk or '["Sunday"]', total, bool(active)))
            cur.execute("DELETE FROM mes_working_calendar WHERE source='dqmp' AND fy=%s", (f,))
            for (hid, d0, d1, name, kind, isw, rec) in rows:
                if not _d(d0):
                    continue
                cur.execute("""INSERT INTO mes_working_calendar
                                 (cal_date, end_date, name, kind, is_working, recurring_annual,
                                  fy, source, dqmp_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,'dqmp',%s)""",
                            (_d(d0), _d(d1), name, kind or "holiday", bool(isw), bool(rec), f, hid))
            out[f] = len(rows)
        return out
    finally:
        src.close()


@once
def _seed_once() -> None:
    """First use in a process: if MES holds no calendar yet, import D-QMP's."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT COUNT(*) AS n FROM mes_working_calendar_fy")
        if (cur.fetchone() or {}).get("n"):
            conn.rollback()
            return
        try:
            import_from_dqmp(cur)
            conn.commit()
        except HTTPException as exc:
            conn.rollback()
            print(f"[working-calendar] first import from D-QMP skipped: {exc.detail}")


def _ready():
    _ensure_tables()
    _seed_once()


# ── the one rule ──────────────────────────────────────────────────────────
_cache = {"t": 0.0, "data": None}
_cache_lock = threading.Lock()
CACHE_S = 30


def _load(cur):
    """{fy headers}, {date: entry} for off days, working exceptions and MES edits.
    Cached 30 s per process: every PEFF load and Plant Overview refresh asks."""
    with _cache_lock:
        if _cache["data"] and time.time() - _cache["t"] < CACHE_S:
            return _cache["data"]
    cur.execute("""SELECT fy, title, company_name, period_start, period_end, week_off_json,
                          total_working_days, is_active
                     FROM mes_working_calendar_fy ORDER BY period_start""")
    fys = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT id, cal_date, end_date, name, kind, is_working, recurring_annual,
                          fy, source, updated_by, updated_at
                     FROM mes_working_calendar ORDER BY cal_date, id""")
    entries = [dict(r) for r in cur.fetchall()]
    years = sorted({y for f in fys for y in range(f["period_start"].year, f["period_end"].year + 1)}
                   | {date.today().year})
    off, work, mes = {}, {}, {}
    for e in entries:
        if e["source"] == "mes":
            mes[e["cal_date"]] = e
            continue
        tgt = work if e["is_working"] else off
        d0 = e["cal_date"]
        if e["recurring_annual"]:
            for y in years:
                try:
                    tgt.setdefault(date(y, d0.month, d0.day), e)
                except ValueError:
                    pass
            continue
        d, d1, guard = d0, e["end_date"] or d0, 0
        while d <= d1 and guard < 400:
            tgt.setdefault(d, e)
            d += timedelta(days=1)
            guard += 1
    data = {"fys": fys, "entries": entries, "off": off, "work": work, "mes": mes}
    with _cache_lock:
        _cache.update(t=time.time(), data=data)
    return data


def _clear_cache():
    with _cache_lock:
        _cache.update(t=0.0, data=None)


def _week_off(fys, d: date) -> set:
    for f in fys:
        if f["period_start"] <= d <= f["period_end"]:
            try:
                names = json.loads(f["week_off_json"] or '["Sunday"]')
                s = {_WD[str(n).strip().lower()] for n in names if str(n).strip().lower() in _WD}
                return s or {6}
            except Exception:
                return {6}
    return {6}                                   # no FY covers it: six-day week, Sunday off


def day_status(cur, d: date) -> dict:
    """{date, working, kind, label, name, source} for one day — THE calendar rule."""
    _ready()
    data = _load(cur)
    m = data["mes"].get(d)
    if m:
        return {"date": d.isoformat(), "working": bool(m["is_working"]), "kind": m["kind"],
                "label": KIND_LABEL.get(m["kind"], m["kind"]), "name": m["name"],
                "source": "mes", "updated_by": m["updated_by"]}
    for tbl, working in (("work", True), ("off", False)):
        e = data[tbl].get(d)
        if e:
            return {"date": d.isoformat(), "working": working, "kind": e["kind"],
                    "label": KIND_LABEL.get(e["kind"], e["kind"]), "name": e["name"],
                    "source": "dqmp"}
    if d.weekday() in _week_off(data["fys"], d):
        return {"date": d.isoformat(), "working": False, "kind": "weekly_off",
                "label": "Weekly off", "name": d.strftime("%A"), "source": "week_off"}
    return {"date": d.isoformat(), "working": True, "kind": "regular",
            "label": "Working day", "name": None, "source": "default"}


def is_working(cur, d: date) -> bool:
    """For other routers (Plant Overview).  Falls back to 'not Sunday' if the
    calendar can't be read, so a caller never breaks on it."""
    try:
        cur.execute("SAVEPOINT _wcal")
        ok = day_status(cur, d)["working"]
        cur.execute("RELEASE SAVEPOINT _wcal")
        return ok
    except Exception:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT _wcal")
        except Exception:
            pass
        return d.weekday() != 6


def _parse(s: Optional[str]) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")


def _fy_of(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str((y + 1) % 100).zfill(2)}"


def _can_edit(user) -> bool:
    return (user.get("role") or "") in EDIT_ROLES


# ── endpoints ─────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def get_calendar(fy: Optional[str] = Query(None, description="e.g. 2026-27 (default: today's FY)"),
                 user=Depends(get_current_user)):
    """One FY: header, every day with its status, and the entries behind it."""
    _ready()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        data = _load(cur)
        fys = data["fys"]
        want = fy or _fy_of(date.today())
        hdr = next((f for f in fys if f["fy"] == want), None)
        if hdr:
            p0, p1 = hdr["period_start"], hdr["period_end"]
        else:
            y = int(want[:4]) if want[:4].isdigit() else date.today().year
            p0, p1 = date(y, 4, 1), date(y + 1, 3, 31)
        days, d, n_work = [], p0, 0
        while d <= p1:
            st = day_status(cur, d)
            n_work += 1 if st["working"] else 0
            days.append(st)
            d += timedelta(days=1)
        edits = [e for e in data["entries"] if e["source"] == "mes" and p0 <= e["cal_date"] <= p1]
        conn.rollback()

    def _j(e):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.items()}
    return {"fy": want,
            "header": _j(hdr) if hdr else None,
            "all_fys": [f["fy"] for f in fys],
            "period": {"start": p0.isoformat(), "end": p1.isoformat()},
            "working_days": n_work,
            "days": days,
            "mes_edits": [_j(e) for e in edits],
            "can_edit": _can_edit(user),
            "can_reimport": (user.get("role") or "") in ("admin", "plant_head"),
            "kinds": KIND_LABEL}


@router.get("/day")
def get_day(date_: Optional[str] = Query(None, alias="date"), user=Depends(get_current_user)):
    """Is this one day a working day?  Used by the PEFF sheet."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        st = day_status(cur, _parse(date_))
        conn.rollback()
    return st


class DayEdit(BaseModel):
    date: str
    working: bool
    reason: str = ""


@router.put("/day")
def set_day(body: DayEdit, user=Depends(get_current_user)):
    """MES edit for one day: make it Working or Non-working, with a reason.
    Wins over the D-QMP calendar and survives every re-import."""
    if not _can_edit(user):
        raise HTTPException(403, "Only admin, plant head or production incharge can edit the calendar")
    _ready()
    d = _parse(body.date)
    reason = (body.reason or "").strip()[:200] or ("Working day" if body.working else "Non-working day")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""INSERT INTO mes_working_calendar
                         (cal_date, name, kind, is_working, fy, source, updated_by, updated_at)
                       VALUES (%s,%s,%s,%s,%s,'mes',%s,now())
                       ON CONFLICT (cal_date) WHERE source = 'mes' DO UPDATE SET
                         name=EXCLUDED.name, kind=EXCLUDED.kind, is_working=EXCLUDED.is_working,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (d, reason, "mes_working" if body.working else "mes_off", body.working,
                     _fy_of(d), user.get("username")))
        conn.commit()
    _clear_cache()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        st = day_status(cur, d)
        conn.rollback()
    return st


@router.delete("/day")
def clear_day(date_: str = Query(..., alias="date"), user=Depends(get_current_user)):
    """Remove the MES edit — the day goes back to what D-QMP's calendar says."""
    if not _can_edit(user):
        raise HTTPException(403, "Only admin, plant head or production incharge can edit the calendar")
    _ready()
    d = _parse(date_)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_working_calendar WHERE source='mes' AND cal_date=%s", (d,))
        conn.commit()
    _clear_cache()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        st = day_status(cur, d)
        conn.rollback()
    return st


@router.post("/import")
def reimport(fy: Optional[str] = Query(None), user=Depends(get_current_user)):
    """Pull the calendar from D-QMP again (all FYs, or one).  MES edits are kept."""
    if (user.get("role") or "") not in ("admin", "plant_head"):
        raise HTTPException(403, "Only admin or plant head can re-import the calendar")
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        out = import_from_dqmp(cur, fy)
        conn.commit()
    _clear_cache()
    return {"ok": True, "imported": out}
