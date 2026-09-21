"""
routers/pm.py
=============
Preventive Maintenance (PM) — DB-backed, FLAT tables (2026-06-17 rebuild).

Flat data model (one row per check-point; no relational joins, no JSONB):
  pm_check_sheet  — master template. one row per check point.
                    sheet_id (stable machine identity, = old pm_machines.id),
                    zone, line, machine_no, machine_name, serial, check_point,
                    judgement_standard, method, rev_no, rev_date, sort_order, active
  pm_filled       — filled sheets. one row per filled point.
                    sheet_id, pm_record_id (=sheet_id__YYYY-MM), header fields,
                    point snapshot (serial/check_point/…), observation/action_taken/
                    spares_used/status/sign, record_status
  pm_schedule     — the PM planner/schedule (one row per planned PM).
  pm_mail_config  — single-row reminder recipient config.

Identity: a "machine"/check-sheet = a distinct `sheet_id`.  The API still calls
it {mid} so the existing PMPanel.jsx keeps working (machine.id == sheet_id, and
each point's id == pm_check_sheet row id, fills keyed by point id).

Revision is USER-EDITABLE (PUT /machines/{mid}/rev) — no auto copy-on-write fork.
Editing a point is in-place; filled history stays correct because every pm_filled
row snapshots its own point text + rev.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/pm", tags=["pm"])


def _author(user) -> str:
    if isinstance(user, dict):
        return user.get("username") or user.get("name") or user.get("email") or "operator"
    return (getattr(user, "username", None) or getattr(user, "name", None)
            or getattr(user, "email", None) or "operator")


@once
def _ensure_tables() -> None:
    """Create the two flat PM tables if they are missing.

    2026-09-17 — `pm_schedule` and `pm_mail_config` existed in this database but
    `pm_check_sheet` / `pm_filled` (added by the 2026-06-17 flat rebuild
    described above) were never created, so /api/pm/zones and /api/pm/dashboard
    answered HTTP 500 with `relation "pm_check_sheet" does not exist` while the
    other two PM endpoints worked.  Every other router here self-creates its
    tables the same way; PM was the one that did not.  Idempotent and additive —
    it only ever creates what is absent and never touches existing rows.
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pm_check_sheet (
                id                  SERIAL PRIMARY KEY,
                sheet_id            INTEGER NOT NULL,
                zone                VARCHAR(120),
                line                VARCHAR(120),
                machine_no          VARCHAR(120),
                machine_name        VARCHAR(200),
                serial              INTEGER,
                check_point         TEXT NOT NULL,
                judgement_standard  TEXT,
                method              TEXT,
                rev_no              VARCHAR(40),
                rev_date            DATE,
                sort_order          INTEGER DEFAULT 0,
                active              BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at          TIMESTAMP DEFAULT NOW()
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pm_sheet_sheetid "
                    "ON pm_check_sheet (sheet_id) WHERE active")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pm_filled (
                id                  SERIAL PRIMARY KEY,
                sheet_id            INTEGER NOT NULL,
                pm_record_id        VARCHAR(80) NOT NULL,
                zone                VARCHAR(120),
                line                VARCHAR(120),
                machine_no          VARCHAR(120),
                machine_name        VARCHAR(200),
                pm_month            VARCHAR(7),
                pm_date             DATE,
                team_name           VARCHAR(200),
                rev_no              VARCHAR(40),
                rev_date            DATE,
                serial              INTEGER,
                check_point         TEXT,
                judgement_standard  TEXT,
                method              TEXT,
                observation         TEXT,
                action_taken        TEXT,
                spares_used         TEXT,
                status              VARCHAR(40),
                sign                VARCHAR(120),
                record_status       VARCHAR(40) DEFAULT 'open',
                created_by          VARCHAR(120),
                updated_at          TIMESTAMP DEFAULT NOW()
            )""")
        # the fill upsert is ON CONFLICT (pm_record_id, serial)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_filled_record_serial "
                    "ON pm_filled (pm_record_id, serial)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pm_filled_lookup "
                    "ON pm_filled (sheet_id, pm_month)")
        conn.commit()


# ════════════════════════════════════════════════════════════════════
# CHECK-SHEET (master template + fill)
# ════════════════════════════════════════════════════════════════════

# ── Zones ───────────────────────────────────────────────────────────
@router.get("/zones")
def list_zones(user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT zone, COUNT(DISTINCT sheet_id) AS machines "
                    "FROM pm_check_sheet WHERE active "
                    "GROUP BY zone ORDER BY zone")
        return {"zones": cur.fetchall()}


# ── Machines (= distinct sheets within a zone) ──────────────────────
@router.get("/machines")
def list_machines(zone: str = Query(...), user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT sheet_id AS id, MIN(machine_name) AS machine_name, "
            "       MIN(machine_no) AS machine_code, MIN(line) AS area_line, "
            "       MIN(machine_name) AS sheet_name, MIN(rev_no) AS rev_no, "
            "       MIN(rev_date) AS rev_date, COUNT(*) AS points "
            "FROM pm_check_sheet WHERE zone=%s AND active "
            "GROUP BY sheet_id ORDER BY MIN(machine_name), sheet_id", (zone,))
        return {"zone": zone, "machines": cur.fetchall()}


@router.get("/machines/{mid}")
def machine_detail(mid: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT sheet_id AS id, zone, MIN(line) AS area_line, "
            "       MIN(machine_no) AS machine_code, MIN(machine_name) AS machine_name, "
            "       MIN(rev_no) AS rev_no, MIN(rev_date) AS rev_date, "
            "       'Maintenance' AS dept "
            "FROM pm_check_sheet WHERE sheet_id=%s GROUP BY sheet_id, zone", (mid,))
        m = cur.fetchone()
        if not m:
            raise HTTPException(404, "machine not found")
        cur.execute("SELECT id, serial AS sno, check_point, judgement_standard, method, sort_order "
                    "FROM pm_check_sheet WHERE sheet_id=%s AND active "
                    "ORDER BY sort_order, serial, id", (mid,))
        pts = cur.fetchall()
    return {"machine": m, "points": pts}


# ── Points CRUD (admin) — in-place, USER-EDITABLE revision (no fork) ─
class PointIn(BaseModel):
    sno: Optional[int] = None
    check_point: str = ""
    judgement_standard: Optional[str] = ""
    method: Optional[str] = ""


class RevIn(BaseModel):
    rev_no: Optional[str] = None
    rev_date: Optional[str] = None


def _sheet_ctx(cur, mid):
    cur.execute("SELECT zone, line, machine_no, machine_name, rev_no, rev_date "
                "FROM pm_check_sheet WHERE sheet_id=%s LIMIT 1", (mid,))
    return cur.fetchone()


@router.post("/machines/{mid}/points")
def add_point(mid: int, body: PointIn, user=Depends(get_current_user)):
    _ensure_tables()
    if not (body.check_point or "").strip():
        raise HTTPException(400, "check_point required")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        ctx = _sheet_ctx(cur, mid)
        if not ctx:
            raise HTTPException(404, "machine not found")
        cur.execute("SELECT COALESCE(MAX(serial),0)+1 AS sn, COALESCE(MAX(sort_order),0)+1 AS so "
                    "FROM pm_check_sheet WHERE sheet_id=%s", (mid,))
        nx = cur.fetchone()
        cur.execute(
            "INSERT INTO pm_check_sheet(sheet_id,zone,line,machine_no,machine_name,serial,"
            " check_point,judgement_standard,method,rev_no,rev_date,sort_order,active,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW()) RETURNING id",
            (mid, ctx["zone"], ctx["line"], ctx["machine_no"], ctx["machine_name"],
             body.sno if body.sno is not None else nx["sn"], body.check_point.strip(),
             (body.judgement_standard or "").strip() or None,
             (body.method or "").strip() or None, ctx["rev_no"], ctx["rev_date"], nx["so"]))
        pid = cur.fetchone()["id"]
        conn.commit()
    return {"ok": True, "id": pid}


@router.put("/points/{pid}")
def edit_point(pid: int, body: PointIn, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT 1 FROM pm_check_sheet WHERE id=%s", (pid,))
        if not cur.fetchone():
            raise HTTPException(404, "point not found")
        cur.execute("UPDATE pm_check_sheet SET serial=COALESCE(%s,serial), check_point=%s, "
                    "judgement_standard=%s, method=%s, updated_at=NOW() WHERE id=%s",
                    (body.sno, (body.check_point or "").strip(),
                     (body.judgement_standard or "").strip() or None,
                     (body.method or "").strip() or None, pid))
        conn.commit()
    return {"ok": True}


@router.delete("/points/{pid}")
def delete_point(pid: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("DELETE FROM pm_check_sheet WHERE id=%s", (pid,))
        conn.commit()
    return {"ok": True}


@router.put("/machines/{mid}/rev")
def set_rev(mid: int, body: RevIn, user=Depends(get_current_user)):
    _ensure_tables()
    """User-editable revision: stamps rev_no / rev_date on the whole sheet."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("UPDATE pm_check_sheet SET rev_no=COALESCE(%s,rev_no), "
                    "rev_date=COALESCE(%s,rev_date), updated_at=NOW() WHERE sheet_id=%s",
                    ((body.rev_no or None), (body.rev_date or None), mid))
        if cur.rowcount == 0:
            raise HTTPException(404, "machine not found")
        conn.commit()
    return {"ok": True, "rev_no": body.rev_no, "rev_date": body.rev_date}


# ── PM record (fill the sheet) — one pm_filled row per point ────────
class FillIn(BaseModel):
    point_id: int                       # = pm_check_sheet.id
    observation: Optional[str] = ""
    action_taken: Optional[str] = ""
    spares_used: Optional[str] = ""
    status: Optional[str] = ""
    sign: Optional[str] = ""


class RecordIn(BaseModel):
    pm_month: str                       # 'YYYY-MM'
    pm_date: Optional[str] = None       # 'YYYY-MM-DD'
    team_name: Optional[str] = ""
    status: Optional[str] = "open"
    fills: List[FillIn] = []


@router.get("/machines/{mid}/record")
def get_record(mid: int, month: str = Query(...), user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, serial AS sno, check_point, judgement_standard, method, sort_order "
                    "FROM pm_check_sheet WHERE sheet_id=%s AND active "
                    "ORDER BY sort_order, serial, id", (mid,))
        points = cur.fetchall()
        if not points:
            raise HTTPException(404, "machine not found")
        cur.execute("SELECT pm_record_id, pm_date, team_name, record_status, serial, "
                    "       observation, action_taken, spares_used, status, sign "
                    "FROM pm_filled WHERE sheet_id=%s AND pm_month=%s", (mid, month))
        rows = cur.fetchall()
        by_serial = {r["serial"]: r for r in rows}
        fills = {}
        for p in points:
            r = by_serial.get(p["sno"])
            if r:
                fills[p["id"]] = {"point_id": p["id"], "observation": r["observation"],
                                  "action_taken": r["action_taken"], "spares_used": r["spares_used"],
                                  "status": r["status"], "sign": r["sign"]}
        rec = None
        if rows:
            fr = rows[0]
            rec = {"id": fr["pm_record_id"],
                   "pm_date": fr["pm_date"].isoformat() if fr["pm_date"] else None,
                   "team_name": fr["team_name"] or "", "status": fr["record_status"] or "open"}
    return {"machine_id": mid, "pm_month": month, "points": points,
            "record": rec, "fills": fills}


@router.post("/machines/{mid}/record")
def save_record(mid: int, body: RecordIn, user=Depends(get_current_user)):
    _ensure_tables()
    if not (body.pm_month or "").strip():
        raise HTTPException(400, "pm_month required")
    author = _author(user)
    month = body.pm_month.strip()
    rec_id = f"{mid}__{month}"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if not _sheet_ctx(cur, mid):
            raise HTTPException(404, "machine not found")
        for f in body.fills:
            # snapshot the point text + machine header into the filled row
            cur.execute("SELECT zone,line,machine_no,machine_name,rev_no,rev_date,serial,"
                        "check_point,judgement_standard,method FROM pm_check_sheet WHERE id=%s",
                        (f.point_id,))
            p = cur.fetchone()
            if not p:
                continue
            cur.execute(
                "INSERT INTO pm_filled(sheet_id,pm_record_id,zone,line,machine_no,machine_name,"
                " pm_month,pm_date,team_name,rev_no,rev_date,serial,check_point,judgement_standard,"
                " method,observation,action_taken,spares_used,status,sign,record_status,created_by,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
                "ON CONFLICT (pm_record_id,serial) DO UPDATE SET "
                "  observation=EXCLUDED.observation, action_taken=EXCLUDED.action_taken, "
                "  spares_used=EXCLUDED.spares_used, status=EXCLUDED.status, sign=EXCLUDED.sign, "
                "  pm_date=EXCLUDED.pm_date, team_name=EXCLUDED.team_name, "
                "  record_status=EXCLUDED.record_status, updated_at=NOW()",
                (mid, rec_id, p["zone"], p["line"], p["machine_no"], p["machine_name"],
                 month, (body.pm_date or None), (body.team_name or "").strip(),
                 p["rev_no"], p["rev_date"], p["serial"], p["check_point"],
                 p["judgement_standard"], p["method"],
                 (f.observation or "").strip() or None, (f.action_taken or "").strip() or None,
                 (f.spares_used or "").strip() or None, (f.status or "").strip() or None,
                 (f.sign or "").strip() or None, (body.status or "open"), author))
        conn.commit()
    return {"ok": True, "record_id": rec_id}


# ════════════════════════════════════════════════════════════════════
# DASHBOARD  (actual fill status + schedule-driven due — Stage 3 fills the latter)
# ════════════════════════════════════════════════════════════════════
@router.get("/dashboard")
def dashboard(month: str = Query(None), user=Depends(get_current_user)):
    _ensure_tables()
    if not month:
        month = date.today().strftime("%Y-%m")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT cs.sheet_id AS id, MIN(cs.zone) AS zone,
                   MIN(cs.machine_name) AS machine_name, MIN(cs.machine_no) AS machine_code,
                   COUNT(*) AS pts,
                   (SELECT COUNT(*) FROM pm_filled f
                      WHERE f.sheet_id=cs.sheet_id AND f.pm_month=%s
                        AND COALESCE(f.observation,'')<>'') AS filled,
                   (SELECT COUNT(*) FROM pm_filled f
                      WHERE f.sheet_id=cs.sheet_id AND f.pm_month=%s) AS has_rec,
                   (SELECT MAX(f.pm_date) FROM pm_filled f
                      WHERE f.sheet_id=cs.sheet_id AND f.pm_month=%s) AS pm_date,
                   (SELECT MAX(f.team_name) FROM pm_filled f
                      WHERE f.sheet_id=cs.sheet_id AND f.pm_month=%s) AS team_name
            FROM pm_check_sheet cs WHERE cs.active
            GROUP BY cs.sheet_id ORDER BY MIN(cs.zone), MIN(cs.machine_name)
        """, (month, month, month, month))
        rows = cur.fetchall()

    done, fill_pending, not_started = [], [], []
    for r in rows:
        item = {"id": r["id"], "zone": r["zone"], "machine_name": r["machine_name"],
                "machine_code": r["machine_code"], "points": r["pts"],
                "filled": r["filled"] or 0,
                "pm_date": r["pm_date"].isoformat() if r["pm_date"] else None,
                "team_name": r["team_name"]}
        if not r["has_rec"]:
            not_started.append(item)
        elif (r["pts"] or 0) > 0 and (r["filled"] or 0) >= r["pts"]:
            done.append(item)
        else:
            fill_pending.append(item)

    sched = _schedule_dashboard(month)
    return {
        "month": month,
        "counts": {"done": len(done), "fill_pending": len(fill_pending),
                   "not_started": len(not_started), "total": len(rows)},
        "fill_pending": fill_pending,
        "done": done,
        "scheduled_pending": sched["this_month_pending"],
        "next_month": sched["next_month"],
    }


# ════════════════════════════════════════════════════════════════════
# SCHEDULE / PLANNER  (Stage 3)
# ════════════════════════════════════════════════════════════════════
class PlanIn(BaseModel):
    sheet_id: Optional[int] = None
    zone: Optional[str] = ""
    line: Optional[str] = ""
    machine_no: Optional[str] = ""
    machine_name: Optional[str] = ""
    due_date: str                       # 'YYYY-MM-DD'
    task: Optional[str] = "Line preventive maintenance"
    frequency: Optional[str] = "Monthly"
    owner: Optional[str] = "Maintenance"
    status: Optional[str] = "Pending"
    repeat_12m: bool = False            # clone monthly for 12 months


def _month_clamped_dates(start: date, n: int) -> List[date]:
    """n monthly dates from `start`, clamping the day to each month's length."""
    out = []
    for i in range(n):
        y = start.year + (start.month - 1 + i) // 12
        m = (start.month - 1 + i) % 12 + 1
        # clamp day to month length
        if m == 12:
            last = 31
        else:
            last = (date(y, m + 1, 1) - timedelta(days=1)).day
        out.append(date(y, m, min(start.day, last)))
    return out


@router.get("/schedule")
def list_schedule(month: str = Query(None), user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if month:
            cur.execute("SELECT * FROM pm_schedule WHERE pm_month=%s ORDER BY due_date, machine_name", (month,))
        else:
            cur.execute("SELECT * FROM pm_schedule ORDER BY due_date, machine_name")
        rows = cur.fetchall()
    for r in rows:
        if r.get("due_date"):
            r["due_date"] = r["due_date"].isoformat()
        for k in ("created_at", "updated_at"):
            if r.get(k):
                r[k] = r[k].isoformat()
    return {"schedule": rows}


@router.post("/schedule")
def add_plan(body: PlanIn, user=Depends(get_current_user)):
    try:
        start = datetime.strptime(body.due_date, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(400, "due_date must be YYYY-MM-DD")
    author = _author(user)
    dates = _month_clamped_dates(start, 12) if body.repeat_12m else [start]
    ids = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # fill machine identity from the sheet if sheet_id given
        z, ln, mno, mname = body.zone, body.line, body.machine_no, body.machine_name
        if body.sheet_id:
            cur.execute("SELECT MIN(zone) z, MIN(line) l, MIN(machine_no) n, MIN(machine_name) nm "
                        "FROM pm_check_sheet WHERE sheet_id=%s", (body.sheet_id,))
            ctx = cur.fetchone()
            if ctx:
                z, ln, mno, mname = ctx["z"], ctx["l"], ctx["n"], ctx["nm"]
        for d in dates:
            cur.execute(
                "INSERT INTO pm_schedule(sheet_id,zone,line,machine_no,machine_name,task,frequency,"
                " due_date,owner,status,pm_month,created_by,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
                "ON CONFLICT (sheet_id,due_date) DO UPDATE SET "
                "  task=EXCLUDED.task, frequency=EXCLUDED.frequency, owner=EXCLUDED.owner, "
                "  pm_month=EXCLUDED.pm_month, updated_at=NOW() RETURNING id",
                (body.sheet_id, z, ln, mno, mname, body.task, body.frequency, d,
                 body.owner, (body.status or "Pending"), d.strftime("%Y-%m"), author))
            ids.append(cur.fetchone()["id"])
        conn.commit()
    return {"ok": True, "ids": ids, "count": len(ids)}


class PlanPatch(BaseModel):
    status: Optional[str] = None
    due_date: Optional[str] = None


@router.patch("/schedule/{sid}")
def update_plan(sid: int, body: PlanPatch, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if body.status is not None:
            cur.execute("UPDATE pm_schedule SET status=%s, updated_at=NOW() WHERE id=%s", (body.status, sid))
        if body.due_date:
            try:
                d = datetime.strptime(body.due_date, "%Y-%m-%d").date()
            except Exception:
                raise HTTPException(400, "due_date must be YYYY-MM-DD")
            cur.execute("UPDATE pm_schedule SET due_date=%s, pm_month=%s, updated_at=NOW() WHERE id=%s",
                        (d, d.strftime("%Y-%m"), sid))
        conn.commit()
    return {"ok": True}


@router.delete("/schedule/{sid}")
def delete_plan(sid: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("DELETE FROM pm_schedule WHERE id=%s", (sid,))
        conn.commit()
    return {"ok": True}


def _schedule_dashboard(month: str) -> dict:
    """this-month pending + next-month scheduled (for the dashboard)."""
    try:
        y, m = map(int, month.split("-"))
        nxt = f"{y + (m // 12)}-{(m % 12) + 1:02d}"
    except Exception:
        nxt = None
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, zone, line, machine_no, machine_name, due_date, status "
                    "FROM pm_schedule WHERE pm_month=%s AND status<>'Done' ORDER BY due_date", (month,))
        this_pending = cur.fetchall()
        nm = []
        if nxt:
            cur.execute("SELECT id, zone, line, machine_no, machine_name, due_date, status "
                        "FROM pm_schedule WHERE pm_month=%s ORDER BY due_date", (nxt,))
            nm = cur.fetchall()
    for lst in (this_pending, nm):
        for r in lst:
            if r.get("due_date"):
                r["due_date"] = r["due_date"].isoformat()
    return {"this_month_pending": this_pending, "next_month": nm}


# ════════════════════════════════════════════════════════════════════
# MAIL CONFIG  (recipient for reminder worker — Stage 4)
# ════════════════════════════════════════════════════════════════════
class MailCfgIn(BaseModel):
    recipient: Optional[str] = ""
    cc: Optional[str] = ""
    auto_enabled: Optional[bool] = True


@router.get("/mail-config")
def get_mail_config(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT recipient, cc, auto_enabled FROM pm_mail_config WHERE id=1")
        row = cur.fetchone() or {"recipient": "", "cc": "", "auto_enabled": True}
    return row


@router.put("/mail-config")
def set_mail_config(body: MailCfgIn, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "INSERT INTO pm_mail_config(id,recipient,cc,auto_enabled,updated_at) "
            "VALUES(1,%s,%s,%s,NOW()) ON CONFLICT (id) DO UPDATE SET "
            "  recipient=EXCLUDED.recipient, cc=EXCLUDED.cc, "
            "  auto_enabled=EXCLUDED.auto_enabled, updated_at=NOW()",
            ((body.recipient or "").strip(), (body.cc or "").strip(),
             bool(body.auto_enabled)))
        conn.commit()
    return {"ok": True}
