"""
routers/control_board_cells.py — auto-save of what users type / click in the
Control Board sheets that had no saving yet.  2026-11-12.

Operator: "control board wale document me sab me yeh kar de ki user agar koi
detail manually input kare wo auto save ho jaaye".

The 4M Change Control Sheet and the Operator Visualization Sheet keep their
own saving (control_board_4m.py).  These sheets use this store:
  track-record   Track Record Sheet            line + month (the sheet's own picks)
  defect-std     Defect Recording Sheet        line + month
  defect-rec     Defect Recording - Recliner   line + month
  annual-safety  Annual Safety Chart           line + year
  q-chart        Q Chart                       line + financial year (2026-27)
  5s-daily       5S Daily Check Sheet          line + month
A cell is whatever the sheet keys it as (an input's id, or its position among
the sheet's inputs / editable text / click marks); the value is a string.
Values filled by MES (Red Bin, formulas, signatures) are never sent.

Every change also goes to mes_cb_doc_cell_log (old -> new, who, when), so a
cleared sheet can always be recovered.

GET /api/control-board/cells?doc=&line_id=&period=
PUT /api/control-board/cells {doc, line_id, period, changes:{key: value}, dates:{key: day}}

2026-11-13 (operator): "control board document me kisi bhi sheet me agar koi
bhi data automatic aa raha hai usko freeze kar de ... manual input hai toh wo
today ke liye hi editable ho, back date me wo bhi editable nahi hona chahiye".
"Today" is the PRODUCTION day of the line: from its first shift start (08:30)
to the next one, so the night shift still fills its own date after midnight.
  * the period (month / year / FY) must contain today;
  * a day cell (dates[key] = YYYY-MM-DD) only for today, a weekly box
    (W:<from>:<to>) only in its week, other fields (period level) any day;
  * a value saved on an earlier day can no longer be changed (all keys);
  * no Clear: it would wipe earlier days.
The sheet sends the day of each key (keys are positional, so only the sheet
knows it); the "saved on an earlier day" rule stands on the server alone.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_user
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/control-board", tags=["control-board"])

DOCS = {
    "track-record": r"^\d{4}-(0[1-9]|1[0-2])$",
    "defect-std": r"^\d{4}-(0[1-9]|1[0-2])$",
    "defect-rec": r"^\d{4}-(0[1-9]|1[0-2])$",
    "annual-safety": r"^\d{4}$",
    "q-chart": r"^\d{4}-\d{2}$",
    "5s-daily": r"^\d{4}-(0[1-9]|1[0-2])$",
}
KEY_RE = re.compile(r"^[#A-Za-z0-9_.:-]{1,80}$")
MAX_KEYS = 600
MAX_VALUE = 2000
_ready = False


def _ensure(cur) -> None:
    global _ready
    if _ready:
        return
    cur.execute("""SELECT count(*) AS n FROM pg_class
                    WHERE relname IN ('mes_cb_doc_cell', 'mes_cb_doc_cell_log') AND relkind = 'r'""")
    if int((cur.fetchone() or {}).get("n") or 0) < 2:
        with get_conn() as c2:                      # own transaction
            k = c2.cursor()
            k.execute("SET LOCAL lock_timeout = '3s'")
            k.execute("""CREATE TABLE IF NOT EXISTS mes_cb_doc_cell (
                    doc        VARCHAR(20) NOT NULL,
                    line_id    INTEGER     NOT NULL,
                    period     VARCHAR(10) NOT NULL,
                    cell_key   VARCHAR(80) NOT NULL,
                    value      TEXT        NOT NULL DEFAULT '',
                    updated_by VARCHAR(100),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (doc, line_id, period, cell_key))""")
            k.execute("""CREATE TABLE IF NOT EXISTS mes_cb_doc_cell_log (
                    id         BIGSERIAL PRIMARY KEY,
                    doc        VARCHAR(20) NOT NULL,
                    line_id    INTEGER     NOT NULL,
                    period     VARCHAR(10) NOT NULL,
                    cell_key   VARCHAR(80) NOT NULL,
                    old_value  TEXT,
                    new_value  TEXT,
                    changed_by VARCHAR(100),
                    changed_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            k.execute("CREATE INDEX IF NOT EXISTS ix_cb_doc_cell_log_scope ON mes_cb_doc_cell_log (doc, line_id, period, changed_at)")
    _ready = True


def _check(doc: str, period: str) -> None:
    if doc not in DOCS:
        raise HTTPException(400, "unknown sheet")
    if not re.match(DOCS[doc], period or ""):
        raise HTTPException(400, "bad period for this sheet")


def _check_line(user, line_id: int) -> None:
    if (user or {}).get("role") == "admin":
        return
    from routers.lines import list_lines
    ok = {int(l["id"]) for l in (list_lines(plant_id=None, user=user) or [])}
    if line_id not in ok:
        raise HTTPException(403, "This line is not assigned to your login")


def is_admin(user) -> bool:
    """2026-11-13 (operator: "control board ke jitne bhi documents hai unko edit
    karne ki permission admin ko toh de"): the admin login may edit any day."""
    return (user or {}).get("role") == "admin"


def _who(user) -> str:
    return str((user or {}).get("username") or (user or {}).get("id") or "user")[:100]


def prod_day_fn(cur, line_id: int):
    """ts -> production date of the line: the day of its most recent A / B / C
    shift start (08:30 here), so 02:00 belongs to the day before."""
    cur.execute("""SELECT start_time FROM mes_shift_configs
                    WHERE line_id = %s AND shift_name IN ('A', 'B', 'C') AND start_time IS NOT NULL""",
                (line_id,))
    starts = [r["start_time"] for r in cur.fetchall()]
    first = min(starts) if starts else None

    def f(ts) -> date:
        if ts is None:
            ts = datetime.now()
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        if first is not None and ts.time() < first:
            return ts.date() - timedelta(days=1)
        return ts.date()
    return f


def _fy(d: date) -> str:
    y0 = d.year if d.month >= 4 else d.year - 1
    return f"{y0}-{(y0 + 1) % 100:02d}"


def period_has(doc: str, period: str, d: date) -> bool:
    """Does the sheet's period (month / year / financial year) contain day d?"""
    if doc == "annual-safety":
        return period == str(d.year)
    if doc == "q-chart":
        return period == _fy(d)
    return period == d.strftime("%Y-%m")


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEK_RE = re.compile(r"^W:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$")


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def read_cells(cur, doc: str, line_id: int, period: str) -> Dict[str, str]:
    _ensure(cur)
    cur.execute("""SELECT cell_key, value FROM mes_cb_doc_cell
                    WHERE doc = %s AND line_id = %s AND period = %s""", (doc, line_id, period))
    return {r["cell_key"]: r["value"] for r in cur.fetchall()}


@router.get("/cells")
def get_cells(doc: str = Query(...), line_id: int = Query(...), period: str = Query(...),
              user=Depends(get_current_user)):
    _check(doc, period)
    _check_line(user, line_id)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        pday = prod_day_fn(cur, line_id)
        cur.execute("""SELECT cell_key, value, updated_at FROM mes_cb_doc_cell
                        WHERE doc = %s AND line_id = %s AND period = %s""", (doc, line_id, period))
        rows = cur.fetchall()
    cells = {r["cell_key"]: r["value"] for r in rows}
    # the production day each non-empty value was entered: earlier days are locked
    set_on = {r["cell_key"]: pday(r["updated_at"]).isoformat() for r in rows if (r["value"] or "").strip()}
    at = max((r["updated_at"] for r in rows), default=None)
    today = pday(None)
    return {"doc": doc, "line_id": line_id, "period": period, "cells": cells, "set": set_on,
            "today": today.isoformat(), "open": period_has(doc, period, today), "admin": is_admin(user),
            "updated_at": at.isoformat(timespec="seconds") if at else None}


class CellsBody(BaseModel):
    doc: str
    line_id: int
    period: str
    changes: Dict[str, Optional[str]] = {}
    dates: Dict[str, str] = {}   # key -> "YYYY-MM-DD" (day cell) / "W:from:to" (weekly box) / "" (period level)
    clear: bool = False          # no longer allowed (2026-11-13): it would wipe earlier days


@router.put("/cells")
def put_cells(body: CellsBody, user=Depends(get_current_user)):
    _check(body.doc, body.period)
    _check_line(user, body.line_id)
    if len(body.changes) > MAX_KEYS:
        raise HTTPException(400, "too many cells in one save")
    for k, v in body.changes.items():
        if not KEY_RE.match(k):
            raise HTTPException(400, f"bad cell key {k[:40]!r}")
        if v is not None and len(v) > MAX_VALUE:
            raise HTTPException(400, "value too long")
    if body.clear:
        raise HTTPException(409, "Clear is not allowed: entries of earlier days are locked.")
    who = _who(user)
    scope = (body.doc, body.line_id, body.period)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SET LOCAL lock_timeout = '3s'")
        pday = prod_day_fn(cur, body.line_id)
        today = pday(None)
        admin = is_admin(user)                  # any day, any period
        if not admin and not period_has(body.doc, body.period, today):
            raise HTTPException(409, f"Only today's entries ({_dmy(today)}) can be made: "
                                     "this sheet is for another period.")
        n = 0
        for k, v in body.changes.items():
            v = "" if v is None else str(v)
            dk = "" if admin else str(body.dates.get(k) or "")
            if _DAY_RE.match(dk):
                if dk != today.isoformat():
                    raise HTTPException(409, f"Only today's column ({_dmy(today)}) can be changed.")
            elif _WEEK_RE.match(dk):
                a, b = _WEEK_RE.match(dk).groups()
                if not (a <= today.isoformat() <= b):
                    raise HTTPException(409, "Only the current week's box can be signed.")
            elif dk:
                raise HTTPException(409, "This box cannot be changed.")
            cur.execute("""SELECT value, updated_at FROM mes_cb_doc_cell
                            WHERE doc = %s AND line_id = %s AND period = %s AND cell_key = %s FOR UPDATE""", (*scope, k))
            old = cur.fetchone()
            if old and old["value"] == v:
                continue
            if not admin and old and (old["value"] or "").strip() and pday(old["updated_at"]) < today:
                raise HTTPException(409, f"Entered on {_dmy(pday(old['updated_at']))}: "
                                         "entries of earlier days cannot be changed.")
            cur.execute("""INSERT INTO mes_cb_doc_cell (doc, line_id, period, cell_key, value, updated_by, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,now())
                           ON CONFLICT (doc, line_id, period, cell_key)
                           DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                        (*scope, k, v, who))
            cur.execute("""INSERT INTO mes_cb_doc_cell_log (doc, line_id, period, cell_key, old_value, new_value, changed_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""", (*scope, k, old["value"] if old else None, v, who))
            n += 1
        cur.execute("SELECT now() AS t")
        t = cur.fetchone()["t"]
    return {"ok": True, "changed": n, "saved_at": t.isoformat(timespec="seconds")}
