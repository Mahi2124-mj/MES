"""
routers/control_board_4m.py — 4M Change Control Sheet (Control Board), saved.
2026-11-10.

Operator spec (10-Nov):
  * a RED circle (A or B shift block, any 4M / unexpected-change row, any day)
    adds one row to "4M CHANGE DETAILS": Date = DD-MM-YYYY (shift), in date
    order, A before B on the same day, as many rows as red circles;
  * all text in CAPITALS;
  * Man / Machine / Method / Material / Unexpected Change Details: ONE column
    per row — filling another clears the first;
  * Name of Supervisor = the line leader saved in Shift Allocation for that
    date + shift;
  * OK / NG Qty = typed by the user (12-Nov: the parts checked after the
    change, as on the paper board — no longer the shift's OK / NG count),
    open from 15 min before to 15 min after the shift end only;
  * Product Details, Action Taken, Quality Result and Status are compulsory;
  * a red circle still missing one of them when its shift ends is PENDING:
    an alarm goes to the line team (repeated hourly) and Shift Compile refuses
    to close that shift until it is filled.

GET  /api/control-board/4m?line_id=&month=YYYY-MM
PUT  /api/control-board/4m/mark   {line_id, date, shift, param, state}
PUT  /api/control-board/4m/row    {line_id, date, shift, param, fields:{...}}
Counting / OEE untouched; own tables only.
"""
from __future__ import annotations

import calendar
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_user, require_admin
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/control-board", tags=["control-board"])

SHIFTS = ("A", "B")
PARAMS = ("MAN", "MATERIAL", "MACHINE", "METHOD",
          "TOOL_BREAKAGE", "POWER_FAILURE", "UNPLANNED_LEAVE")
CHANGE_COLS = ("MAN", "MACHINE", "METHOD", "MATERIAL", "UNEXPECTED")
STATUSES = ("RETURNED", "NOT_RETURNED")
REQUIRED = (("product", "Product Details"), ("action_taken", "Action Taken"),
            ("quality_result", "Quality Result"), ("status", "Status"))
TEXT_FIELDS = ("product", "action_taken", "quality_result", "manager")
MAX_TEXT = 300
# 2026-11-13 (operator): every breakdown slip (Maintenance_DX auto slips in
# maintenance_db, maintenance + tool room) turns the line's "TOOL BREAKAGE /
# M/C B/D" circle red for its date / shift and gets its OWN detail row, filled
# from the slip — "breakdown slip 4M ka strong backup hai, koi gap na rahe".
SLIP_PARAM = "TOOL_BREAKAGE"
SLIP_SRC = {"maintenance": ("maintenance_auto_breakdown_slip", "M", "MAINT."),
            "toolroom": ("toolroom_auto_breakdown_slip", "T", "TOOL ROOM")}
SLIP_KEY_RE = re.compile(r"^(?:[MT][0-9]{1,9})?$")
SLIP_SYNC_DAYS = 3          # besides the current month, a few days back (month change)
ALARM_EVERY_MIN = 60        # reminder while still pending
ALARM_FOR_H = 24            # stop reminding this long after the shift end
QTY_WINDOW_MIN = 15         # OK / NG Qty: shift end -15 min .. +15 min (operator, 12-Nov)
QTY_MAX = 99999
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ready = False


def _ensure(cur) -> None:
    """Own tables, created once per process (catalog check first: no DDL lock
    on a table that already exists)."""
    global _ready
    if _ready:
        if not _qty_cols:
            _ensure_qty(cur)
        if not _slip_cols:
            _ensure_slip(cur)
        return
    cur.execute("""SELECT count(*) AS n FROM pg_class
                    WHERE relname IN ('mes_cb_4m_mark','mes_cb_4m_detail','mes_cb_4m_alarm','mes_cb_settings',
                                      'mes_cb_ops_cell','mes_cb_ops_alarm')
                      AND relkind = 'r'""")
    if int((cur.fetchone() or {}).get("n") or 0) < 6:
        with get_conn() as c2:                      # own transaction: committed even if the caller rolls back
            _create(c2.cursor())
    _ensure_qty(cur)
    _ensure_slip(cur)
    _ready = True


# 2026-11-12: typed OK / NG Qty.  Two nullable columns on our own table, added
# once (catalog check first, so no DDL lock once they exist; short lock timeout
# — a busy moment just retries on a later request).  Until they exist the sheet
# shows the boxes empty and a save says so; nothing else depends on them.
_qty_cols = False
_qty_tried = 0.0


def _ensure_qty(cur) -> bool:
    global _qty_cols, _qty_tried
    if _qty_cols:
        return True
    try:
        cur.execute("""SELECT count(*) AS n FROM pg_attribute
                        WHERE attrelid = 'mes_cb_4m_detail'::regclass
                          AND attname IN ('ok_qty', 'ng_qty') AND NOT attisdropped""")
        if int((cur.fetchone() or {}).get("n") or 0) >= 2:
            _qty_cols = True
            return True
    except Exception as e:
        print(f"[4M] qty column check failed: {str(e)[:120]}", flush=True)
        return False
    if _time.time() - _qty_tried < 300:
        return False
    _qty_tried = _time.time()
    try:
        with get_conn() as c2:
            k = c2.cursor()
            k.execute("SET LOCAL lock_timeout = '2s'")
            k.execute("""ALTER TABLE mes_cb_4m_detail ADD COLUMN IF NOT EXISTS ok_qty INTEGER,
                                                  ADD COLUMN IF NOT EXISTS ng_qty INTEGER""")
        _qty_cols = True
        print("[4M] OK / NG Qty columns added to mes_cb_4m_detail", flush=True)
    except Exception as e:
        print(f"[4M] OK / NG Qty columns not added yet (retry later): {str(e)[:120]}", flush=True)
    return _qty_cols


# 2026-11-13: breakdown-slip rows.  mes_cb_4m_detail gets slip_key ('' = the
# circle's own row, 'M<id>' / 'T<id>' = a maintenance / tool room slip) in its
# primary key, so one red circle can hold one row per slip; mes_cb_4m_slip
# links each slip to its line / date / shift and keeps the slip's text.  Done
# once, in its own transaction, short lock timeout, retried every 5 min.
_slip_cols = False
_slip_tried = 0.0


def _ensure_slip(cur) -> bool:
    global _slip_cols, _slip_tried
    if _slip_cols:
        return True
    try:
        cur.execute("""SELECT
              (SELECT count(*) FROM pg_attribute WHERE attrelid = 'mes_cb_4m_detail'::regclass
                  AND attname = 'slip_key' AND NOT attisdropped) AS col,
              (SELECT count(*) FROM pg_index i JOIN pg_attribute a
                   ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'mes_cb_4m_detail'::regclass AND i.indisprimary
                  AND a.attname = 'slip_key') AS pk,
              (SELECT count(*) FROM pg_class WHERE relname = 'mes_cb_4m_slip' AND relkind = 'r') AS tbl""")
        r = cur.fetchone() or {}
        if r.get("col") and r.get("pk") and r.get("tbl"):
            _slip_cols = True
            return True
    except Exception as e:
        print(f"[4M] slip column check failed: {str(e)[:120]}", flush=True)
        return False
    if _time.time() - _slip_tried < 300:
        return False
    _slip_tried = _time.time()
    try:
        with get_conn() as c2:
            k = c2.cursor()
            k.execute("SET LOCAL lock_timeout = '2s'")
            k.execute("ALTER TABLE mes_cb_4m_detail ADD COLUMN IF NOT EXISTS slip_key VARCHAR(20) NOT NULL DEFAULT ''")
            k.execute("""SELECT c.conname,
                                bool_or(a.attname = 'slip_key') AS has
                           FROM pg_constraint c
                           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                          WHERE c.conrelid = 'mes_cb_4m_detail'::regclass AND c.contype = 'p'
                          GROUP BY c.conname""")
            pk = k.fetchone()
            if not pk or not pk[1]:
                if pk:
                    k.execute(f'ALTER TABLE mes_cb_4m_detail DROP CONSTRAINT "{pk[0]}"')
                k.execute("ALTER TABLE mes_cb_4m_detail ADD PRIMARY KEY (line_id, mark_date, shift_name, param, slip_key)")
            k.execute("""CREATE TABLE IF NOT EXISTS mes_cb_4m_slip (
                           src          VARCHAR(12) NOT NULL,
                           slip_id      INTEGER NOT NULL,
                           line_id      INTEGER NOT NULL,
                           mark_date    DATE NOT NULL,
                           shift_name   VARCHAR(10) NOT NULL,
                           slip_line    TEXT,
                           machine      TEXT,
                           problem      TEXT,
                           observed     TEXT,
                           action       TEXT,
                           model        TEXT,
                           bd_start     TEXT,
                           bd_ok        TEXT,
                           downtime_min INTEGER,
                           stage        TEXT,
                           andon_event_id INTEGER,
                           slip_created TIMESTAMPTZ,
                           linked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                           synced_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                           PRIMARY KEY (src, slip_id))""")
            k.execute("CREATE INDEX IF NOT EXISTS ix_cb_4m_slip_line ON mes_cb_4m_slip (line_id, mark_date, shift_name)")
            c2.commit()
        _slip_cols = True
        print("[4M] breakdown-slip link ready (mes_cb_4m_detail.slip_key + mes_cb_4m_slip)", flush=True)
    except Exception as e:
        print(f"[4M] breakdown-slip link not ready yet (retry later): {str(e)[:160]}", flush=True)
    return _slip_cols


def _qty_window(end: Optional[datetime]):
    """(open_from, open_to) of the OK / NG Qty boxes for a shift ending at `end`."""
    if not end:
        return None, None
    return end - timedelta(minutes=QTY_WINDOW_MIN), end + timedelta(minutes=QTY_WINDOW_MIN)


def _qty_open(end: Optional[datetime], now: datetime) -> bool:
    a, b = _qty_window(end)
    return bool(a and a <= now <= b)


def _create(cur) -> None:
    cur.execute("SET LOCAL lock_timeout = '3s'")
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_4m_mark (
                     line_id    INTEGER NOT NULL,
                     mark_date  DATE NOT NULL,
                     shift_name VARCHAR(10) NOT NULL,
                     param      VARCHAR(20) NOT NULL,
                     state      VARCHAR(10) NOT NULL,
                     updated_by TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                     PRIMARY KEY (line_id, mark_date, shift_name, param))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_4m_detail (
                     line_id        INTEGER NOT NULL,
                     mark_date      DATE NOT NULL,
                     shift_name     VARCHAR(10) NOT NULL,
                     param          VARCHAR(20) NOT NULL,
                     change_col     VARCHAR(12),
                     change_text    TEXT,
                     product        TEXT,
                     action_taken   TEXT,
                     quality_result TEXT,
                     manager        TEXT,
                     status         VARCHAR(14),
                     updated_by     TEXT,
                     updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                     PRIMARY KEY (line_id, mark_date, shift_name, param))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_4m_alarm (
                     line_id    INTEGER NOT NULL,
                     mark_date  DATE NOT NULL,
                     shift_name VARCHAR(10) NOT NULL,
                     first_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                     last_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                     sent       INTEGER NOT NULL DEFAULT 1,
                     PRIMARY KEY (line_id, mark_date, shift_name))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_settings (
                     key        TEXT PRIMARY KEY,
                     value      BOOLEAN NOT NULL,
                     updated_by TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # Operator Visualization Sheet: one row per filled cell of a line's month sheet
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_ops_cell (
                     line_id    INTEGER NOT NULL,
                     month      CHAR(7) NOT NULL,
                     sheet_no   SMALLINT NOT NULL,
                     cell_key   VARCHAR(16) NOT NULL,
                     value      TEXT NOT NULL DEFAULT '',
                     set_date   DATE,
                     set_shift  VARCHAR(10),
                     updated_by TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                     PRIMARY KEY (line_id, month, sheet_no, cell_key))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_cb_ops_alarm (
                     line_id    INTEGER NOT NULL,
                     mark_date  DATE NOT NULL,
                     shift_name VARCHAR(10) NOT NULL,
                     first_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                     last_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                     sent       INTEGER NOT NULL DEFAULT 1,
                     PRIMARY KEY (line_id, mark_date, shift_name))""")


# 2026-11-10 (operator): the Control Board documents are still in development —
# Admin → Admin → Control Board switches their shift-close rules on / off.
SETTINGS = {
    "4m_close_block": "Shift Compile cannot close a shift while a 4M change of that shift is open",
    "4m_alarm": "Line-team alarm at shift end (then hourly) while a 4M change is open",
    "ops_close_block": "Shift Compile cannot close a shift of a running line while the day's operator status is not filled",
    "ops_alarm": "Line-team alarm at shift end (then hourly) while the day's operator status is not filled",
}


def _settings(cur) -> dict:
    """Current switches (default ON = the rules as built)."""
    out = {k: True for k in SETTINGS}
    try:
        _ensure(cur)
        cur.execute("SELECT key, value FROM mes_cb_settings WHERE key = ANY(%s)", (list(SETTINGS),))
        for r in cur.fetchall():
            out[r["key"]] = bool(r["value"])
    except Exception as e:                                   # a settings hiccup never blocks anything new
        print(f"[4M] settings read failed: {str(e)[:120]}", flush=True)
    return out


def _label(d: date, shift: str) -> str:
    return f"{d.strftime('%d-%m-%Y')} ({shift})"


def _shift_ends(cur, line_id: int) -> dict:
    cur.execute("""SELECT shift_name, end_time, crosses_midnight FROM mes_shift_configs
                    WHERE line_id = %s AND shift_name = ANY(%s)""", (line_id, list(SHIFTS)))
    return {r["shift_name"]: (r["end_time"], bool(r["crosses_midnight"])) for r in cur.fetchall()}


def _end_dt(ends: dict, d: date, shift: str) -> Optional[datetime]:
    e = ends.get(shift)
    if not e or e[0] is None:
        return None
    dt = datetime.combine(d, e[0])
    return dt + timedelta(days=1) if e[1] else dt


def _current_shift(cur, line_id: int, now: Optional[datetime] = None):
    """(record_date, shift, end) of the production shift running now on this line
    (shift timing from mes_shift_configs; B after midnight = the day it began)."""
    now = now or datetime.now()
    cur.execute("""SELECT shift_name, start_time, end_time, crosses_midnight FROM mes_shift_configs
                    WHERE line_id = %s AND shift_name = ANY(%s)""", (line_id, list(SHIFTS)))
    for r in cur.fetchall():
        if r["start_time"] is None or r["end_time"] is None:
            continue
        for d in (now.date(), now.date() - timedelta(days=1)):
            st = datetime.combine(d, r["start_time"])
            en = datetime.combine(d, r["end_time"]) + timedelta(days=1 if r["crosses_midnight"] else 0)
            if st <= now < en:
                return d, r["shift_name"], en
    return None


def _prod_today(cur, line_id: int, now: Optional[datetime] = None) -> date:
    """2026-11-13 (operator: manual entries editable "today ke liye hi", never back
    dated): the line's PRODUCTION day — from its first shift start (08:30) to the
    next, so the night shift still fills its own date after midnight."""
    from routers.control_board_cells import prod_day_fn
    return prod_day_fn(cur, line_id)(now or datetime.now())


def _admin(user) -> bool:
    """2026-11-13 (operator): the admin login may edit every Control Board sheet
    on any day (circles, rows, OK / NG Qty, Operator sheet).  Data that comes
    automatically (breakdown slips) stays frozen for everyone."""
    from routers.control_board_cells import is_admin
    return is_admin(user)


def _missing(row: dict) -> list:
    return [label for key, label in REQUIRED if not (row.get(key) or "").strip()]


def _slip_key(src: str, slip_id) -> str:
    return SLIP_SRC.get(src, ("", "?", ""))[1] + str(slip_id)


def _slip_text(sl: dict) -> str:
    """Unexpected Change Details of a slip row (operator 13-Nov): only the
    machine number and the problem PRODUCTION reported, shown once production
    has filled its half of the slip (CAPITALS, like every 4M entry)."""
    if str(sl.get("stage") or "").upper() == "PENDING_PRODUCTION":
        return ""
    parts = [str(sl.get("machine") or "").strip(), str(sl.get("problem") or "").strip()]
    return re.sub(r"\s+", " ", " - ".join(x for x in parts if x)).strip().upper()[:MAX_TEXT]


_DETAIL_KEYS = ("change_col", "change_text", "product", "action_taken", "quality_result",
                "manager", "status", "ok_qty", "ng_qty")


def _has_content(d: Optional[dict]) -> bool:
    return bool(d) and any(d.get(k) not in (None, "") for k in _DETAIL_KEYS)


def _rows(cur, line_id: int, d0: date, d1: date, shift: Optional[str] = None) -> list:
    """Red marks in [d0, d1] (optionally one shift) with their details, in sheet
    order: date, A before B, then the sheet's row order.  A TOOL BREAKAGE / M/C
    B/D circle with breakdown slips gives one row per slip (filled from the
    slip), plus its own row only if someone typed into it."""
    q = "d.ok_qty, d.ng_qty" if _qty_cols else "NULL::int AS ok_qty, NULL::int AS ng_qty"
    sk = "d.slip_key" if _slip_cols else "''::text AS slip_key"
    cur.execute("""SELECT m.mark_date, m.shift_name, m.param FROM mes_cb_4m_mark m
                    WHERE m.line_id = %s AND m.state = 'red' AND m.mark_date BETWEEN %s AND %s
                      AND (%s::text IS NULL OR m.shift_name = %s)""",
                (line_id, d0, d1, shift, shift))
    marks = [(r["mark_date"], r["shift_name"], r["param"]) for r in cur.fetchall()]
    if not marks:
        return []
    cur.execute(f"""SELECT d.mark_date, d.shift_name, d.param, {sk}, d.change_col, d.change_text, d.product,
                           d.action_taken, d.quality_result, d.manager, d.status, {q}
                      FROM mes_cb_4m_detail d
                     WHERE d.line_id = %s AND d.mark_date BETWEEN %s AND %s
                       AND (%s::text IS NULL OR d.shift_name = %s)""",
                (line_id, d0, d1, shift, shift))
    det = {(r["mark_date"], r["shift_name"], r["param"], r["slip_key"] or ""): dict(r) for r in cur.fetchall()}
    slips = {}
    if _slip_cols:
        cur.execute("""SELECT * FROM mes_cb_4m_slip
                        WHERE line_id = %s AND mark_date BETWEEN %s AND %s
                          AND (%s::text IS NULL OR shift_name = %s)
                        ORDER BY slip_created NULLS LAST, slip_id""",
                    (line_id, d0, d1, shift, shift))
        for r in cur.fetchall():
            slips.setdefault((r["mark_date"], r["shift_name"]), []).append(dict(r))
    out = []
    for md, sh, p in marks:
        base = {"mark_date": md, "shift_name": sh, "param": p}
        here = slips.get((md, sh), []) if p == SLIP_PARAM else []
        for n, sl in enumerate(here):
            key = _slip_key(sl["src"], sl["slip_id"])
            d = det.get((md, sh, p, key)) or {}
            model = (sl.get("model") or "").strip().upper()
            action = (sl.get("action") or "").strip().upper()
            out.append({**base, **{k: d.get(k) for k in _DETAIL_KEYS}, "_o": n,
                        "slip_key": key, "change_col": "UNEXPECTED", "change_text": _slip_text(sl),
                        # 2026-11-13 (operator): data that comes automatically is frozen —
                        # the slip's model / action win over anything typed earlier
                        "product": model or d.get("product") or None,
                        "action_taken": action or d.get("action_taken") or None,
                        "auto": {"product": bool(model), "action_taken": bool(action)},
                        "slip": {"src": sl["src"], "id": sl["slip_id"], "key": key,
                                 "no": f"{SLIP_SRC[sl['src']][2]} #{sl['slip_id']}" if sl["src"] in SLIP_SRC else str(sl["slip_id"]),
                                 "stage": sl.get("stage"), "machine": sl.get("machine"),
                                 "bd_start": sl.get("bd_start"), "bd_ok": sl.get("bd_ok"),
                                 "downtime_min": sl.get("downtime_min")}})
        own = det.get((md, sh, p, ""))
        if not here or _has_content(own):
            out.append({**base, **{k: (own or {}).get(k) for k in _DETAIL_KEYS}, "_o": 9999,
                        "slip_key": "", "slip": None, "auto": {}})
    out.sort(key=lambda r: (r["mark_date"], SHIFTS.index(r["shift_name"]) if r["shift_name"] in SHIFTS else 9,
                            PARAMS.index(r["param"]) if r["param"] in PARAMS else 99, r["_o"]))
    return out


def _slips_linked(cur, line_id: int, d: date, shift: str) -> list:
    if not _slip_cols:
        return []
    cur.execute("""SELECT src, slip_id FROM mes_cb_4m_slip
                    WHERE line_id = %s AND mark_date = %s AND shift_name = %s ORDER BY slip_id""",
                (line_id, d, shift))
    return [_slip_key(r["src"], r["slip_id"]) for r in cur.fetchall()]


def pending_4m(cur, line_id: int, d: date, shift: str) -> list:
    """Red circles of this line / date / shift still missing a compulsory field
    — [{label, param, missing:[...]}].  Used by Shift Compile (close gate)."""
    base = (shift or "").upper()
    if base.endswith("_OT"):
        base = base[:-3]
    if base not in SHIFTS:
        return []
    if not _settings(cur).get("4m_close_block", True):
        return []
    try:
        _ensure(cur)
        rows = _rows(cur, line_id, d, d, base)
    except Exception as e:                                    # never break Shift Compile
        print(f"[4M] pending check failed: {str(e)[:120]}", flush=True)
        return []
    out = []
    for r in rows:
        miss = _missing(r)
        if miss:
            out.append({"label": _label(r["mark_date"], r["shift_name"]),
                        "param": r["param"], "missing": miss})
    return out


@router.get("/4m")
def get_4m(line_id: int = Query(...), month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
           user=Depends(get_current_user)):
    y, m = int(month[:4]), int(month[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(400, "month must be YYYY-MM")
    ndays = calendar.monthrange(y, m)[1]
    d0, d1 = date(y, m, 1), date(y, m, ndays)
    now = datetime.now()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SELECT line_name FROM mes_lines WHERE id = %s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")
        cur.execute("""SELECT mark_date, shift_name, param, state FROM mes_cb_4m_mark
                        WHERE line_id = %s AND mark_date BETWEEN %s AND %s""", (line_id, d0, d1))
        marks = {s: {} for s in SHIFTS}
        for r in cur.fetchall():
            if r["shift_name"] in marks:
                marks[r["shift_name"]].setdefault(r["param"], {})[str(r["mark_date"].day)] = r["state"]
        rows = _rows(cur, line_id, d0, d1)
        ends = _shift_ends(cur, line_id)
        cs = _current_shift(cur, line_id, now)
        ptoday = _prod_today(cur, line_id, now)
        adm = _admin(user)
        keys = sorted({(r["mark_date"], r["shift_name"]) for r in rows})
        sup = {}
        if keys:
            cur.execute("""SELECT sa.shift_date, sa.shift_name, a.username
                             FROM mes_leader_shift_alloc sa JOIN mes_admin a ON a.id = sa.leader_id
                            WHERE sa.line_id = %s AND sa.shift_date BETWEEN %s AND %s""", (line_id, d0, d1))
            sup = {(r["shift_date"], r["shift_name"]): (r["username"] or "").upper() for r in cur.fetchall()}
    out_rows, n_pend = [], 0
    for r in rows:
        k = (r["mark_date"], r["shift_name"])
        end = _end_dt(ends, *k)
        ended = bool(end and now >= end)
        miss = _missing(r)
        pend = ended and bool(miss)
        n_pend += pend
        qa, qb = _qty_window(end)
        out_rows.append({
            "key": f"{r['mark_date']}|{r['shift_name']}|{r['param']}" + (f"|{r['slip_key']}" if r.get("slip_key") else ""),
            "slip_key": r.get("slip_key") or "", "slip": r.get("slip"), "auto": r.get("auto") or {},
            "date": str(r["mark_date"]), "shift": r["shift_name"], "param": r["param"],
            "label": _label(*k),
            "change_col": r["change_col"], "change_text": r["change_text"] or "",
            "product": r["product"] or "", "action_taken": r["action_taken"] or "",
            "quality_result": r["quality_result"] or "", "manager": r["manager"] or "",
            "status": r["status"] or "",
            "supervisor": sup.get(k, ""), "ok_qty": r.get("ok_qty"), "ng_qty": r.get("ng_qty"),
            # typed OK / NG Qty: open from 15 min before to 15 min after the shift end,
            # also when the rest of the row is locked
            "qty_open": adm or _qty_open(end, now),
            "qty_from": qa.isoformat(timespec="minutes") if qa else None,
            "qty_to": qb.isoformat(timespec="minutes") if qb else None,
            "shift_end": end.isoformat() if end else None, "shift_ended": ended,
            "missing": miss, "pending": pend,
            # 2026-11-13 (operator): rows of today (production day) only — a
            # change of an earlier day is locked, open or not
            "editable": adm or bool(cs and (r["mark_date"], r["shift_name"]) == (cs[0], cs[1]))
                        or r["mark_date"] == ptoday,
        })
    current = ({"date": str(cs[0]), "shift": cs[1], "label": _label(cs[0], cs[1]),
                "end": cs[2].isoformat(timespec="seconds")} if cs else None)
    return {"line_id": line_id, "line_name": line["line_name"], "month": month, "days": ndays,
            "marks": marks, "rows": out_rows, "pending": n_pend, "current": current,
            "today": ptoday.isoformat(), "admin": adm, "now": now.isoformat(timespec="seconds")}


@router.get("/4m/settings")
def get_settings(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        vals = _settings(cur)
        cur.execute("SELECT key, updated_by, updated_at FROM mes_cb_settings WHERE key = ANY(%s)", (list(SETTINGS),))
        meta = {r["key"]: r for r in cur.fetchall()}
    return {"settings": [{"key": k, "label": SETTINGS[k], "on": vals[k],
                          "updated_by": (meta.get(k) or {}).get("updated_by"),
                          "updated_at": (meta[k]["updated_at"].isoformat(timespec="seconds") if k in meta else None)}
                         for k in SETTINGS]}


class SettingBody(BaseModel):
    key: str
    on: bool


@router.put("/4m/settings")
def put_setting(body: SettingBody, admin=Depends(require_admin)):
    if body.key not in SETTINGS:
        raise HTTPException(400, "unknown setting")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""INSERT INTO mes_cb_settings (key, value, updated_by) VALUES (%s,%s,%s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by,
                                                       updated_at=now()""",
                    (body.key, body.on, admin.get("username")))
        conn.commit()
    print(f"[4M] setting {body.key} -> {'ON' if body.on else 'OFF'} by {admin.get('username')}", flush=True)
    return get_settings(user=admin)


def _key(line_id, d: str, shift: str, param: str):
    try:
        dd = date.fromisoformat(d)
    except Exception:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    if shift not in SHIFTS:
        raise HTTPException(400, "shift must be A or B")
    if param not in PARAMS:
        raise HTTPException(400, "unknown 4M row")
    return int(line_id), dd, shift, param


class MarkBody(BaseModel):
    line_id: int
    date: str
    shift: str
    param: str
    state: str


@router.put("/4m/mark")
def put_mark(body: MarkBody, user=Depends(get_current_user)):
    k = _key(body.line_id, body.date, body.shift, body.param)
    state = (body.state or "").lower()
    if state not in ("blank", "green", "red"):
        raise HTTPException(400, "state must be blank, green or red")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cs = _current_shift(cur, k[0])
        if not _admin(user) and (not cs or (k[1], k[2]) != (cs[0], cs[1])):
            raise HTTPException(409, ("Only the running shift can be marked: " + _label(cs[0], cs[1])) if cs
                                else "No shift is running now: circles can be marked during the shift only.")
        if state != "red" and k[3] == SLIP_PARAM:
            linked = _slips_linked(cur, k[0], k[1], k[2])
            if linked:
                raise HTTPException(409, "This circle is linked to breakdown slip(s) "
                                         + ", ".join(linked) + ": it stays red.")
        if state == "blank":
            cur.execute("""DELETE FROM mes_cb_4m_mark WHERE line_id=%s AND mark_date=%s
                             AND shift_name=%s AND param=%s""", k)
        else:
            cur.execute("""INSERT INTO mes_cb_4m_mark (line_id, mark_date, shift_name, param, state, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (line_id, mark_date, shift_name, param)
                           DO UPDATE SET state=EXCLUDED.state, updated_by=EXCLUDED.updated_by, updated_at=now()""",
                        k + (state, user.get("username")))
        conn.commit()
    # a filled detail row is kept when its circle goes back from red, and
    # comes back with it — never lost by a stray click
    return {"ok": True, "saved_at": datetime.now().isoformat(timespec="seconds")}


class RowBody(BaseModel):
    line_id: int
    date: str
    shift: str
    param: str
    fields: dict
    slip_key: str = ""


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()[:MAX_TEXT]


@router.put("/4m/row")
def put_row(body: RowBody, user=Depends(get_current_user)):
    k = _key(body.line_id, body.date, body.shift, body.param)
    f = body.fields or {}
    skey = (body.slip_key or "").strip().upper()
    if not SLIP_KEY_RE.match(skey):
        raise HTTPException(400, "bad slip key")
    if skey and "change" in f:
        raise HTTPException(409, "The change details of this row come from the breakdown slip.")
    sets = {}
    if "change" in f:
        ch = f.get("change") or {}
        col = str(ch.get("col") or "").upper() or None
        text = _clean(ch.get("text"))
        if col is not None and col not in CHANGE_COLS:
            raise HTTPException(400, "unknown change column")
        # one change column per row: the new one replaces the old (text and column)
        sets["change_col"] = col if text else None
        sets["change_text"] = text or None
    for name in TEXT_FIELDS:
        if name in f:
            sets[name] = _clean(f.get(name)) or None
    if "status" in f:
        st = str(f.get("status") or "").upper().replace("-", "_") or None
        if st is not None and st not in STATUSES:
            raise HTTPException(400, "status must be RETURNED or NOT_RETURNED")
        sets["status"] = st
    qty = {}
    for name in ("ok_qty", "ng_qty"):
        if name in f:
            v = str(f.get(name) if f.get(name) is not None else "").strip()
            if v and (not v.isdigit() or int(v) > QTY_MAX):
                raise HTTPException(400, "OK / NG Qty must be a whole number")
            qty[name] = int(v) if v else None
    if not sets and not qty:
        raise HTTPException(400, "nothing to save")
    now = datetime.now()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        if skey:
            if not _slip_cols:
                raise HTTPException(503, "Breakdown-slip rows cannot be saved yet: try again in a few minutes.")
            if k[3] != SLIP_PARAM or skey not in _slips_linked(cur, k[0], k[1], k[2]):
                raise HTTPException(404, "This breakdown slip is not linked to this 4M row.")
            # 2026-11-13 (operator): what comes from the slip is frozen
            cur.execute("""SELECT model, action FROM mes_cb_4m_slip
                            WHERE line_id=%s AND mark_date=%s AND shift_name=%s
                              AND src=%s AND slip_id::text=%s""",
                        (k[0], k[1], k[2], "toolroom" if skey[:1] == "T" else "maintenance", skey[1:]))
            sl = cur.fetchone() or {}
            for name, col in (("product", "model"), ("action_taken", "action")):
                if name in sets and (sl.get(col) or "").strip():
                    raise HTTPException(409, "This field comes from the breakdown slip and cannot be changed.")
        end = _end_dt(_shift_ends(cur, k[0]), k[1], k[2])
        if qty:
            if not _qty_cols:
                raise HTTPException(503, "OK / NG Qty cannot be saved yet: try again in a few minutes.")
            a, b = _qty_window(end)
            if not _admin(user) and not _qty_open(end, now):
                raise HTTPException(409, "OK / NG Qty can be filled only from 15 min before to 15 min after "
                                         "the shift end" + (f" ({a:%H:%M}-{b:%H:%M})." if a else "."))
            cur.execute("""SELECT 1 FROM mes_cb_4m_mark WHERE line_id=%s AND mark_date=%s AND shift_name=%s
                              AND param=%s AND state='red'""", k)
            if not cur.fetchone():
                raise HTTPException(409, "No 4M change (red circle) for this row.")
            sets.update(qty)
        if set(sets) - set(qty):
            # 2026-11-13 (operator): manual entries only for today (production
            # day) — an earlier day's row is locked, open or not
            cs = _current_shift(cur, k[0], now)
            ptoday = _prod_today(cur, k[0], now)
            if not _admin(user) and not (cs and (k[1], k[2]) == (cs[0], cs[1])) and k[1] != ptoday:
                raise HTTPException(409, "Only today's entries (" + ptoday.strftime("%d-%m-%Y") + ") can be "
                                         "changed: earlier days are locked.")
        cols = list(sets)
        if _slip_cols:
            cur.execute(f"""INSERT INTO mes_cb_4m_detail
                              (line_id, mark_date, shift_name, param, slip_key, {', '.join(cols)}, updated_by)
                            VALUES (%s,%s,%s,%s,%s, {', '.join(['%s'] * len(cols))}, %s)
                            ON CONFLICT (line_id, mark_date, shift_name, param, slip_key) DO UPDATE SET
                              {', '.join(f'{c}=EXCLUDED.{c}' for c in cols)},
                              updated_by=EXCLUDED.updated_by, updated_at=now()""",
                        k + (skey,) + tuple(sets[c] for c in cols) + (user.get("username"),))
        else:
            cur.execute(f"""INSERT INTO mes_cb_4m_detail
                              (line_id, mark_date, shift_name, param, {', '.join(cols)}, updated_by)
                            VALUES (%s,%s,%s,%s, {', '.join(['%s'] * len(cols))}, %s)
                            ON CONFLICT (line_id, mark_date, shift_name, param) DO UPDATE SET
                              {', '.join(f'{c}=EXCLUDED.{c}' for c in cols)},
                              updated_by=EXCLUDED.updated_by, updated_at=now()""",
                        k + tuple(sets[c] for c in cols) + (user.get("username"),))
        conn.commit()
    return {"ok": True, "saved": {c: sets[c] for c in cols},
            "saved_at": datetime.now().isoformat(timespec="seconds")}


# ── Operator Visualization Sheet (saved, filled by the user) ───────────────
# 2026-11-10 (operator): no auto-fill from Shift Allocation any more — the user
# fills the sheet; same shift-time limits as the 4M sheet; a new row once all
# rows are used; same alarm / close rule, switchable in Alarm Permission.
# 2026-11-10 (later, operator): EMP. CODE / NAME / PROCESS stay auto-filled from
# Shift Allocation (routers/operator_status — saved crews only); the user fills
# only the day-wise marks.  A mark is keyed by the OPERATOR (not the row), so a
# crew change in the month never moves a mark to another person, and it is kept
# line-wide (sheet_no 0) so it follows the operator whichever sheet shows him.
# Cell keys: o<operator id>.d<day>  (operator marks, sheet 0),
#            skill|m4|sup .e|d<day>  (the three day rows), mgr.e|w<1-5>  (per sheet).
OPS_KEY_RE = re.compile(r"^(?:o(?P<op>\d{1,9})\.d(?P<od>\d{1,2})"
                        r"|(?P<p>skill|m4|sup)\.(?:e|d(?P<pd>\d{1,2}))|mgr\.(?:e|w(?P<w>[1-5])))$")
OPS_MARKS = {"", "●", "□", "○", "△", "▲"}        # P removed 13-Nov (operator: never filled)
# 2026-11-12 (operator): the two day rows take the legend's symbols — ✓ (yes),
# ✕ (no), □ (multi skill) — instead of YES; "YES" / "X" saved before still count.
# 2026-11-12 (operator, later): the skill-matrix row takes ✓ / ✕ / □; the 4M row
# below it is "-" when the skill row is ✓ (all deployed as per the skill matrix,
# shown by the sheet, not saved) and YES / NO when it is ✕ or □.  Saved before:
# skill "YES" = ✓, "X" = ✕; 4M "X" / "✕" = NO, "✓" = YES (read that way, not rewritten).
OPS_SKILL = {"", "✓", "✕", "□"}
OPS_SKILL_OLD = {"YES": "✓", "X": "✕"}
OPS_M4 = {"", "YES", "NO"}
OPS_M4_OLD = {"X": "NO", "✕": "NO", "✓": "YES"}


def _skill_v(v) -> str:
    v = str(v or "").strip()
    return OPS_SKILL_OLD.get(v, v)


def _m4_v(v) -> str:
    v = str(v or "").strip()
    return OPS_M4_OLD.get(v, v)
OPS_WEEKS = {1: (1, 6), 2: (7, 12), 3: (13, 18), 4: (19, 24), 5: (25, 31)}


def _week_of(day: int) -> int:
    return next((k for k, (a, b) in OPS_WEEKS.items() if a <= day <= b), 0)


def _line_ran(cur, line_id: int, d: date, shift: str) -> bool:
    """OK + NG > 0 in that shift (a line that did not run needs no sheet)."""
    cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
    tbl = (cur.fetchone() or {}).get("db_table_name") or ""
    if not _TABLE_RE.match(tbl):
        return False
    cur.execute("SELECT to_regclass(%s) AS r", (tbl,))
    if not (cur.fetchone() or {}).get("r"):
        return False
    cur.execute(f"""SELECT COALESCE(MAX(ok_count), 0) + COALESCE(MAX(ng_count), 0) AS n
                      FROM {tbl} WHERE record_date = %s AND shift_name = %s""", (d, shift))
    return int((cur.fetchone() or {}).get("n") or 0) > 0


def _ops_day_filled(cur, line_id: int, d: date) -> bool:
    """Any operator mark in that day's column, on any sheet of the line."""
    cur.execute("""SELECT 1 FROM mes_cb_ops_cell
                    WHERE line_id = %s AND month = %s AND value <> ''
                      AND cell_key ~ %s LIMIT 1""",
                (line_id, d.strftime("%Y-%m"), rf"^o[0-9]+\.d{d.day}$"))
    return cur.fetchone() is not None


def _ops_pending(cur, line_id: int, d: date, shift: str) -> bool:
    return _line_ran(cur, line_id, d, shift) and not _ops_day_filled(cur, line_id, d)


def pending_ops(cur, line_id: int, d: date, shift: str) -> list:
    """Shift Compile close gate: the line ran and the day's operator status is empty."""
    base = (shift or "").upper()
    if base.endswith("_OT"):
        base = base[:-3]
    if base not in SHIFTS or not _settings(cur).get("ops_close_block", True):
        return []
    try:
        _ensure(cur)
        if _ops_pending(cur, line_id, d, base):
            return [{"label": _label(d, base), "param": "OPERATOR_STATUS", "sheet": "operator-visual",
                     "missing": ["the day's operator status (Operator Visualization Sheet)"]}]
    except Exception as e:
        print(f"[OPS] pending check failed: {str(e)[:120]}", flush=True)
    return []


def _ops_open_days(cur, line_id: int, y: int, m: int, now: datetime) -> list:
    """Days of an ended shift (last 24 h) that are still pending — they may be
    filled late so the alarm / close rule can be satisfied.  Only while one of
    the two operator-sheet rules is ON; otherwise today only."""
    cfg = _settings(cur)
    if not (cfg.get("ops_close_block", True) or cfg.get("ops_alarm", True)):
        return []
    ends, out = _shift_ends(cur, line_id), set()
    for d in (now.date() - timedelta(days=1), now.date()):
        if (d.year, d.month) != (y, m):
            continue
        for sh in SHIFTS:
            end = _end_dt(ends, d, sh)
            if end and end <= now <= end + timedelta(hours=ALARM_FOR_H) and _ops_pending(cur, line_id, d, sh):
                out.add(d.day)
    return sorted(out)


@router.get("/ops")
def get_ops(line_id: int = Query(...), month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
            sheet: int = Query(..., ge=1, le=2), user=Depends(get_current_user)):
    y, m = int(month[:4]), int(month[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(400, "month must be YYYY-MM")
    now = datetime.now()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("SELECT line_name FROM mes_lines WHERE id = %s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")
        cur.execute("""SELECT cell_key, value, set_date, set_shift FROM mes_cb_ops_cell
                        WHERE line_id = %s AND month = %s AND sheet_no IN (0, %s)""", (line_id, month, sheet))
        cells = {r["cell_key"]: {"v": r["value"], "d": str(r["set_date"]) if r["set_date"] else None,
                                 "s": r["set_shift"]} for r in cur.fetchall()}
        cs = _current_shift(cur, line_id, now)
        # 2026-11-13 (operator): earlier days are never filled late (no back-dated entries)
        open_days = []
    # the sheet's crew (EMP. CODE / NAME / PROCESS) from Shift Allocation
    operators, crew_shift, process_nos = [], {}, {}
    try:
        from routers.operator_status import operator_status
        sd = operator_status(line_id=line_id, month=month, user=user)
        crew = (sd.get("sheets") or [{}, {}])[sheet - 1] or {}
        operators = [{"id": o.get("id"), "code": o.get("code") or "", "name": o.get("name") or "",
                      "process": o.get("process") or "",
                      # 2026-11-13: process number(s) of the first day / of days on another process
                      "pno": o.get("pno") or "", "day_pno": o.get("day_pno") or {}}
                     for o in (crew.get("operators") or [])]
        process_nos = sd.get("process_nos") or {}
        crew_shift = crew.get("shifts") or {}
    except Exception as e:
        print(f"[OPS] crew load failed: {str(e)[:120]}", flush=True)
    sup_sign, leaders = _ops_sup_sign(line_id, y, m, cells, crew_shift)
    return {"line_id": line_id, "line_name": line["line_name"], "month": month, "sheet": sheet,
            "days": calendar.monthrange(y, m)[1], "cells": cells, "open_days": open_days, "admin": _admin(user),
            "operators": operators, "process_nos": process_nos, "sup_sign": sup_sign, "leaders": leaders,
            "current": ({"date": str(cs[0]), "shift": cs[1], "label": _label(cs[0], cs[1])} if cs else None),
            "now": now.isoformat(timespec="seconds")}


def _ops_sup_sign(line_id: int, y: int, m: int, cells: dict, crew_shift: dict):
    """2026-11-12 (operator): "Name / Signature of Supervisor (Daily)" = the line
    leader's signature, fetched ONLY for a day whose rows above are filled: the
    skill-matrix row ✓ (the 4M row is then "-"), or ✕ / □ with YES / NO in the
    4M row — never a signature without values.
    Leader (operator, later): the one saved in Shift Allocation with THIS crew —
    the leader of the shift this sheet's operators were allocated to that day —
    never simply the leader of the shift running now.  No saved allocation for
    the crew that day = no signature.  Image = Employee Master.
    -> ({day: {leader, shift}} for every filled day (leader / shift None when
        not saved), {leader id: {name, signature}})"""
    want, out = {}, {}
    for day in range(1, calendar.monthrange(y, m)[1] + 1):
        sk = _skill_v((cells.get(f"skill.d{day}") or {}).get("v"))
        m4 = _m4_v((cells.get(f"m4.d{day}") or {}).get("v"))
        if not sk or (sk != "✓" and m4 not in ("YES", "NO")):
            continue
        sh = crew_shift.get(str(day))
        out[str(day)] = {"leader": None, "shift": sh if sh in SHIFTS else None}
        if sh in SHIFTS:
            want[day] = sh
    leaders = {}
    if not want:
        return out, leaders
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT sa.shift_date, sa.shift_name, sa.leader_id, ad.username, lm.signature_image
                             FROM mes_leader_shift_alloc sa
                             JOIN mes_admin ad ON ad.id = sa.leader_id
                             LEFT JOIN mes_leader_meta lm ON lm.admin_id = sa.leader_id
                            WHERE sa.line_id = %s AND sa.shift_date = ANY(%s)""",
                        (line_id, [date(y, m, d) for d in want]))
            for r in cur.fetchall():
                d = r["shift_date"].day
                if want.get(d) != r["shift_name"] or not r["leader_id"]:
                    continue
                lid = str(r["leader_id"])
                out[str(d)] = {"leader": lid, "shift": r["shift_name"]}
                sig = r.get("signature_image") or None
                leaders[lid] = {"name": r["username"] or "",
                                "signature": sig if sig and str(sig).startswith("data:image/") else None}
    except Exception as e:
        print(f"[OPS] supervisor sign load failed: {str(e)[:120]}", flush=True)
    return out, leaders


class OpsCellBody(BaseModel):
    line_id: int
    month: str
    sheet: int
    key: str
    value: str = ""


@router.put("/ops/cell")
def put_ops_cell(body: OpsCellBody, user=Depends(get_current_user)):
    if not re.match(r"^\d{4}-\d{2}$", body.month or "") or body.sheet not in (1, 2):
        raise HTTPException(400, "month must be YYYY-MM and sheet 1 or 2")
    mt = OPS_KEY_RE.match(body.key or "")
    if not mt:
        raise HTTPException(400, "unknown cell")
    y, m = int(body.month[:4]), int(body.month[5:7])
    ndays = calendar.monthrange(y, m)[1]
    day = int(mt.group("od") or mt.group("pd") or 0)
    week = int(mt.group("w") or 0)
    val = re.sub(r"\s+", " ", str(body.value or "")).strip()[:120]
    if day and not (1 <= day <= ndays):
        raise HTTPException(400, "no such day in this month")
    if mt.group("op") and val not in OPS_MARKS:
        raise HTTPException(400, "use the sheet's status marks")
    if mt.group("p") == "skill" and day:
        val = OPS_SKILL_OLD.get(val, val)
        if val not in OPS_SKILL:
            raise HTTPException(400, "use ✓, ✕ or □")
    if mt.group("p") == "m4" and day:
        val = OPS_M4_OLD.get(val, val)
        if val not in OPS_M4:
            raise HTTPException(400, "use YES or NO")
    sheet_no = 0 if mt.group("op") else body.sheet          # operator marks are line-wide
    now = datetime.now()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cs = _current_shift(cur, body.line_id, now)
        same_month = bool(cs and (cs[0].year, cs[0].month) == (y, m))
        if day:
            ok = same_month and cs[0].day == day      # 2026-11-13: no late fill of earlier days
            why = "Only the running shift's date can be filled"
        elif week:
            ok = same_month and _week_of(cs[0].day) == week
            why = "Only the current week can be signed"
        else:
            cur.execute("""SELECT value, set_date, set_shift FROM mes_cb_ops_cell
                            WHERE line_id=%s AND month=%s AND sheet_no=%s AND cell_key=%s""",
                        (body.line_id, body.month, sheet_no, body.key))
            old = cur.fetchone()
            ok = same_month and (not old or not (old["value"] or "").strip()
                                 or (old["set_date"], old["set_shift"]) == (cs[0], cs[1]))
            why = "Filled in an earlier shift: it cannot be changed"
        if not ok and not _admin(user):
            raise HTTPException(409, why + (f" (running shift: {_label(cs[0], cs[1])})." if cs
                                            else " (no shift is running now)."))
        if mt.group("p") == "m4" and day and val:
            cur.execute("""SELECT value FROM mes_cb_ops_cell
                            WHERE line_id=%s AND month=%s AND sheet_no=%s AND cell_key=%s""",
                        (body.line_id, body.month, sheet_no, f"skill.d{day}"))
            sk = _skill_v((cur.fetchone() or {}).get("value"))
            if not sk:
                raise HTTPException(409, "Mark the skill-matrix row first: YES / NO is filled when it is ✕ or □.")
            if sk == "✓":
                raise HTTPException(409, "Not needed: all operators are deployed as per the skill matrix (✓), "
                                         "so this row shows \"-\".")
        cur.execute("""INSERT INTO mes_cb_ops_cell (line_id, month, sheet_no, cell_key, value, set_date, set_shift, updated_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (line_id, month, sheet_no, cell_key) DO UPDATE SET
                         value=EXCLUDED.value, set_date=EXCLUDED.set_date, set_shift=EXCLUDED.set_shift,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (body.line_id, body.month, sheet_no, body.key, val,
                     cs[0] if cs else date(y, m, day) if day else None, cs[1] if cs else None,
                     user.get("username")))
        conn.commit()
    return {"ok": True, "key": body.key, "value": val, "saved_at": now.isoformat(timespec="seconds")}


def _ops_sweep() -> int:
    """Operator sheet: a line that ran in an ended shift (last 24 h) with an empty
    day column → line-team alarm, then hourly until filled."""
    now = datetime.now()
    sent = 0
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cfg = _settings(cur)
        if not cfg.get("ops_alarm", True):
            return 0
        cur.execute("SELECT id, line_name FROM mes_lines WHERE COALESCE(is_active, TRUE) ORDER BY id")
        lines = cur.fetchall()
        for ln in lines:
            lid = ln["id"]
            ends = _shift_ends(cur, lid)
            for d in (now.date() - timedelta(days=1), now.date()):
                for sh in SHIFTS:
                    end = _end_dt(ends, d, sh)
                    if not end or now < end or now > end + timedelta(hours=ALARM_FOR_H):
                        continue
                    if not _ops_pending(cur, lid, d, sh):
                        continue
                    cur.execute("""SELECT last_at, sent FROM mes_cb_ops_alarm
                                    WHERE line_id=%s AND mark_date=%s AND shift_name=%s""", (lid, d, sh))
                    a = cur.fetchone()
                    if a and a["last_at"].replace(tzinfo=None) > now - timedelta(minutes=ALARM_EVERY_MIN):
                        continue
                    body = (f"{ln['line_name']} · {_label(d, sh)}: the Operator Visualization Sheet is not "
                            f"filled for the day. Fill the operator status."
                            + (" The shift cannot be closed until then." if cfg.get("ops_close_block", True) else ""))
                    try:
                        from routers.push import send_to_line
                        send_to_line(lid, "Operator sheet pending", body,
                                     url="/control-board?sheet=operator-visual", tag=f"ops:{lid}:{d}:{sh}")
                    except Exception as e:
                        print(f"[OPS] alarm send failed: {str(e)[:120]}", flush=True)
                        continue
                    cur.execute("""INSERT INTO mes_cb_ops_alarm (line_id, mark_date, shift_name)
                                   VALUES (%s,%s,%s)
                                   ON CONFLICT (line_id, mark_date, shift_name)
                                   DO UPDATE SET last_at=now(), sent=mes_cb_ops_alarm.sent+1""", (lid, d, sh))
                    conn.commit()
                    sent += 1
                    print(f"[OPS] alarm: {ln['line_name']} {_label(d, sh)} (#{(a['sent'] + 1) if a else 1})", flush=True)
        conn.commit()
    return sent


# ── shift-end alarm ─────────────────────────────────────────────────────────
def _alarm_sweep() -> int:
    """Pending red circles of ended shifts (last 2 days) → line-team alarm, then
    a reminder every ALARM_EVERY_MIN until filled, for ALARM_FOR_H hours."""
    now = datetime.now()
    sent = 0
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cfg = _settings(cur)
        if not cfg.get("4m_alarm", True):
            return 0
        cur.execute("""SELECT DISTINCT m.line_id FROM mes_cb_4m_mark m
                        WHERE m.state = 'red' AND m.mark_date >= %s""", (now.date() - timedelta(days=2),))
        line_ids = [r["line_id"] for r in cur.fetchall()]
        for lid in line_ids:
            ends = _shift_ends(cur, lid)
            groups: dict = {}
            for r in _rows(cur, lid, now.date() - timedelta(days=2), now.date()):
                end = _end_dt(ends, r["mark_date"], r["shift_name"])
                if not end or now < end or now > end + timedelta(hours=ALARM_FOR_H) or not _missing(r):
                    continue
                groups.setdefault((r["mark_date"], r["shift_name"]), []).append(r)
            if not groups:
                continue
            cur.execute("SELECT line_name FROM mes_lines WHERE id = %s", (lid,))
            ln = ((cur.fetchone() or {}).get("line_name")) or f"Line {lid}"
            for (d, sh), rs in groups.items():
                cur.execute("""SELECT last_at, sent FROM mes_cb_4m_alarm
                                WHERE line_id=%s AND mark_date=%s AND shift_name=%s""", (lid, d, sh))
                a = cur.fetchone()
                if a and a["last_at"].replace(tzinfo=None) > now - timedelta(minutes=ALARM_EVERY_MIN):
                    continue
                what = ", ".join(r["param"].replace("_", " ") for r in rs)
                body = (f"{ln} · {_label(d, sh)}: {len(rs)} 4M change(s) not closed ({what}). "
                        f"Fill Product Details, Action Taken, Quality Result and Status in the "
                        f"4M Change Control Sheet."
                        + (" The shift cannot be closed until then." if cfg.get("4m_close_block", True) else ""))
                try:
                    from routers.push import send_to_line
                    send_to_line(lid, "4M change pending", body,
                                 url="/control-board?sheet=4m-change", tag=f"4m:{lid}:{d}:{sh}")
                except Exception as e:
                    print(f"[4M] alarm send failed: {str(e)[:120]}", flush=True)
                    continue
                cur.execute("""INSERT INTO mes_cb_4m_alarm (line_id, mark_date, shift_name)
                               VALUES (%s,%s,%s)
                               ON CONFLICT (line_id, mark_date, shift_name)
                               DO UPDATE SET last_at=now(), sent=mes_cb_4m_alarm.sent+1""", (lid, d, sh))
                conn.commit()
                sent += 1
                print(f"[4M] alarm: {ln} {_label(d, sh)} {len(rs)} pending "
                      f"(#{(a['sent'] + 1) if a else 1})", flush=True)
        conn.commit()
    return sent


def _slip_aliases(cur) -> dict:
    """{normalised slip line: MES line_id} — the Breakdown Slip page's map, plus
    the Sub-Assembly codes (SA_4W_YRA = YRA-SA-4WAY, SA_LPS = the LPS line)."""
    from routers.prod_breakdown_slips import _line_aliases, _norm
    al = dict(_line_aliases(cur))
    cur.execute("SELECT id, line_name FROM mes_lines")
    for r in cur.fetchall():
        n = _norm(r["line_name"])
        m = re.match(r"^([A-Z0-9]+?)SA([0-9])WAY$", n)
        if m:
            al.setdefault(f"SA{m.group(2)}W{m.group(1)}", r["id"])
        if "LPS" in n:
            al.setdefault("SALPS", r["id"])
    return al


def _slip_sync() -> int:
    """Link every breakdown slip of this month (and the last few days) to the 4M
    sheet: TOOL BREAKAGE / M/C B/D circle red for its line / date / shift, the
    slip's text cached for its row.  maintenance_db is only read."""
    from routers.prod_breakdown_slips import _maint_conn, _norm
    import psycopg2.extras
    today = date.today()
    since = min(today.replace(day=1), today - timedelta(days=SLIP_SYNC_DAYS))
    got = []
    with _maint_conn() as mc:
        mc.set_session(readonly=True)
        mk = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for src, (tbl, _l, _lab) in SLIP_SRC.items():
            mk.execute(f"""SELECT id, line, slip_date, shift, machine_no, machine_name,
                                  problem_reported_by_production, problem_observed_by_maintenance,
                                  action_taken_on_problem, model_no, bd_start_time, bd_ok_time,
                                  mc_down_time_minutes, prod_stage, andon_event_id, created_at
                             FROM {tbl} WHERE slip_date >= %s""", (since,))
            got += [(src, dict(r)) for r in mk.fetchall()]
    new = 0
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        if not _slip_cols:
            return 0
        al = _slip_aliases(cur)
        seen = []
        for src, r in got:
            line_id = al.get(_norm(r.get("line")))
            sh = str(r.get("shift") or "").strip().upper()
            if not line_id or sh not in SHIFTS or not r.get("slip_date"):
                continue
            # the machine NUMBER (operator 13-Nov); the name only when no number was filled
            mach = str(r.get("machine_no") or "").strip() or str(r.get("machine_name") or "").strip()
            try:
                dt_min = int(float(r["mc_down_time_minutes"])) if r.get("mc_down_time_minutes") not in (None, "") else None
            except (TypeError, ValueError):
                dt_min = None
            try:
                andon = int(r["andon_event_id"]) if r.get("andon_event_id") not in (None, "") else None
            except (TypeError, ValueError):
                andon = None
            vals = (src, int(r["id"]), line_id, r["slip_date"], sh, r.get("line"), mach or None,
                    (r.get("problem_reported_by_production") or None), (r.get("problem_observed_by_maintenance") or None),
                    (r.get("action_taken_on_problem") or None), (r.get("model_no") or None),
                    (str(r["bd_start_time"]) if r.get("bd_start_time") else None),
                    (str(r["bd_ok_time"]) if r.get("bd_ok_time") else None),
                    dt_min, r.get("prod_stage"), andon, r.get("created_at"))
            cur.execute("""INSERT INTO mes_cb_4m_slip
                             (src, slip_id, line_id, mark_date, shift_name, slip_line, machine, problem, observed,
                              action, model, bd_start, bd_ok, downtime_min, stage, andon_event_id, slip_created)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (src, slip_id) DO UPDATE SET
                             line_id=EXCLUDED.line_id, mark_date=EXCLUDED.mark_date, shift_name=EXCLUDED.shift_name,
                             slip_line=EXCLUDED.slip_line, machine=EXCLUDED.machine, problem=EXCLUDED.problem,
                             observed=EXCLUDED.observed, action=EXCLUDED.action, model=EXCLUDED.model,
                             bd_start=EXCLUDED.bd_start, bd_ok=EXCLUDED.bd_ok, downtime_min=EXCLUDED.downtime_min,
                             stage=EXCLUDED.stage, andon_event_id=EXCLUDED.andon_event_id,
                             slip_created=EXCLUDED.slip_created, synced_at=now()
                           WHERE (mes_cb_4m_slip.line_id, mes_cb_4m_slip.mark_date, mes_cb_4m_slip.shift_name,
                                  mes_cb_4m_slip.machine, mes_cb_4m_slip.problem, mes_cb_4m_slip.observed,
                                  mes_cb_4m_slip.action, mes_cb_4m_slip.model, mes_cb_4m_slip.bd_start,
                                  mes_cb_4m_slip.bd_ok, mes_cb_4m_slip.downtime_min, mes_cb_4m_slip.stage)
                             IS DISTINCT FROM
                                 (EXCLUDED.line_id, EXCLUDED.mark_date, EXCLUDED.shift_name, EXCLUDED.machine,
                                  EXCLUDED.problem, EXCLUDED.observed, EXCLUDED.action, EXCLUDED.model,
                                  EXCLUDED.bd_start, EXCLUDED.bd_ok, EXCLUDED.downtime_min, EXCLUDED.stage)
                           RETURNING (xmax = 0) AS inserted""", vals)
            x = cur.fetchone()
            if x and x["inserted"]:
                new += 1
            cur.execute("""INSERT INTO mes_cb_4m_mark (line_id, mark_date, shift_name, param, state, updated_by)
                           VALUES (%s,%s,%s,%s,'red','BREAKDOWN SLIP')
                           ON CONFLICT (line_id, mark_date, shift_name, param)
                           DO UPDATE SET state='red', updated_by='BREAKDOWN SLIP', updated_at=now()
                           WHERE mes_cb_4m_mark.state <> 'red'""",
                        (line_id, r["slip_date"], sh, SLIP_PARAM))
            seen.append(f"{src}:{int(r['id'])}")
        # a slip deleted on the maintenance side: drop its link (the circle stays red)
        cur.execute("""DELETE FROM mes_cb_4m_slip WHERE mark_date >= %s
                          AND NOT ((src || ':' || slip_id) = ANY(%s))""", (since, seen))
        gone = cur.rowcount
        conn.commit()
    if new or gone:
        print(f"[4M] breakdown slips linked: {new} new, {gone} removed ({len(seen)} in window since {since})", flush=True)
    return new


def start_4m_alarm(every_s: int = 60) -> None:
    def _loop():
        _time.sleep(30)
        while True:
            try:
                _slip_sync()
            except Exception as e:
                print(f"[4M] breakdown-slip sync error: {str(e)[:160]}", flush=True)
            try:
                _alarm_sweep()
            except Exception as e:
                print(f"[4M] alarm sweep error: {str(e)[:160]}", flush=True)
            try:
                _ops_sweep()
            except Exception as e:
                print(f"[OPS] alarm sweep error: {str(e)[:160]}", flush=True)
            _time.sleep(every_s)
    threading.Thread(target=_loop, name="cb-4m-alarm", daemon=True).start()
    print("[4M] shift-end alarm watcher started", flush=True)
