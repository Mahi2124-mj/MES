"""
routers/pdca.py
===============
PDCA / A3 problem-solving tracker.

Standalone module (separate from CAPA) so non-CAPA improvements can
also be tracked.  Each A3 record walks through the four PDCA phases:

  Plan   — define problem, find root cause, propose countermeasures
  Do     — implement the chosen countermeasure
  Check  — verify whether problem is solved (metric / observation)
  Act    — standardize the new method; close or escalate

Each record links to optional context (line, machine, capa_id) and
tracks owners + due dates per phase, so the supervisor sees at-a-glance
which A3 is stuck and where.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/pdca", tags=["pdca"])

PHASES = ("PLAN", "DO", "CHECK", "ACT")
STATUSES = ("OPEN", "IN_PROGRESS", "ON_HOLD", "CLOSED", "ESCALATED")


def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_pdca_records (
                id              SERIAL PRIMARY KEY,
                a3_no           VARCHAR(40) UNIQUE NOT NULL,
                title           VARCHAR(200) NOT NULL,
                line_id         INTEGER,
                machine_name    VARCHAR(120),
                capa_id         INTEGER,            -- optional link to mes_capa
                category        VARCHAR(40),         -- 'QUALITY' | 'MAINTENANCE' | 'PRODUCTIVITY' | 'SAFETY' | 'OTHER'
                severity        VARCHAR(10),         -- 'HIGH' | 'MED' | 'LOW'
                current_phase   VARCHAR(10) NOT NULL DEFAULT 'PLAN',
                status          VARCHAR(20) NOT NULL DEFAULT 'OPEN',
                owner           VARCHAR(120),
                created_by      VARCHAR(120),
                created_at      TIMESTAMP DEFAULT NOW(),
                target_close_dt DATE,
                closed_at       TIMESTAMP,
                problem_text    TEXT,
                root_cause      TEXT,
                countermeasure  TEXT,
                check_result    TEXT,
                act_standardise TEXT,
                plan_due_dt     DATE,
                do_due_dt       DATE,
                check_due_dt    DATE,
                act_due_dt      DATE,
                plan_done_at    TIMESTAMP,
                do_done_at      TIMESTAMP,
                check_done_at   TIMESTAMP,
                act_done_at     TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_pdca_log (
                id              SERIAL PRIMARY KEY,
                pdca_id         INTEGER NOT NULL REFERENCES mes_pdca_records(id) ON DELETE CASCADE,
                event_at        TIMESTAMP NOT NULL DEFAULT NOW(),
                event_by        VARCHAR(120),
                event_type      VARCHAR(40),       -- 'PHASE_DONE' | 'NOTE' | 'STATUS_CHANGE' | 'OWNER_CHANGE'
                from_phase      VARCHAR(10),
                to_phase        VARCHAR(10),
                note            TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pdca_status ON mes_pdca_records (status, current_phase)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pdca_log_rec ON mes_pdca_log (pdca_id)")
        conn.commit()


def _next_a3_no(cur) -> str:
    """A3-YYMMDD-NNN — daily sequence."""
    today = datetime.now()
    prefix = f"A3-{today.strftime('%y%m%d')}-"
    cur.execute("""SELECT a3_no FROM mes_pdca_records
                    WHERE a3_no LIKE %s ORDER BY a3_no DESC LIMIT 1""",
                (prefix + "%",))
    r = cur.fetchone()
    if r:
        try:
            seq = int(r[0].rsplit("-", 1)[1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:03d}"


# ════════════════════════════════════════════════════════════════════
#  CRUD
# ════════════════════════════════════════════════════════════════════
class A3Body(BaseModel):
    title:           str
    line_id:         Optional[int] = None
    machine_name:    Optional[str] = None
    capa_id:         Optional[int] = None
    category:        Optional[str] = None
    severity:        Optional[str] = None
    problem_text:    Optional[str] = None
    root_cause:      Optional[str] = None
    countermeasure:  Optional[str] = None
    check_result:    Optional[str] = None
    act_standardise: Optional[str] = None
    owner:           Optional[str] = None
    target_close_dt: Optional[str] = None
    plan_due_dt:     Optional[str] = None
    do_due_dt:       Optional[str] = None
    check_due_dt:    Optional[str] = None
    act_due_dt:      Optional[str] = None


@router.get("")
def list_records(line_id: Optional[int] = None,
                  status:  Optional[str] = None,
                  phase:   Optional[str] = None,
                  user=Depends(get_current_user)):
    _ensure_tables()
    sql = """SELECT r.*, l.line_name FROM mes_pdca_records r
        LEFT JOIN mes_lines l ON l.id = r.line_id WHERE 1=1"""
    params: list = []
    if line_id is not None:
        sql += " AND r.line_id = %s"; params.append(line_id)
    if status:
        sql += " AND r.status = %s"; params.append(status.upper())
    if phase:
        sql += " AND r.current_phase = %s"; params.append(phase.upper())
    sql += " ORDER BY r.created_at DESC LIMIT 200"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(sql, tuple(params))
        return cur.fetchall()


@router.post("", status_code=201)
def create_record(body: A3Body, user=Depends(get_current_user)):
    _ensure_tables()
    creator = user.get("username") if isinstance(user, dict) else "operator"
    with get_conn() as conn:
        cur = conn.cursor()
        a3 = _next_a3_no(cur)
        cur.execute("""
            INSERT INTO mes_pdca_records
                (a3_no, title, line_id, machine_name, capa_id, category, severity,
                 current_phase, status, owner, created_by,
                 target_close_dt, problem_text, root_cause, countermeasure,
                 check_result, act_standardise,
                 plan_due_dt, do_due_dt, check_due_dt, act_due_dt)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'PLAN','OPEN',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (a3, body.title.strip(), body.line_id, body.machine_name,
              body.capa_id, body.category, body.severity,
              body.owner, creator, body.target_close_dt,
              body.problem_text, body.root_cause, body.countermeasure,
              body.check_result, body.act_standardise,
              body.plan_due_dt, body.do_due_dt, body.check_due_dt, body.act_due_dt))
        new_id = cur.fetchone()[0]
        # Initial log entry
        cur.execute("""INSERT INTO mes_pdca_log
                          (pdca_id, event_by, event_type, to_phase, note)
                       VALUES (%s,%s,'PHASE_DONE','PLAN',%s)""",
                    (new_id, creator, f"A3 created — {body.title}"))
        conn.commit()
    return {"id": new_id, "a3_no": a3, "ok": True}


@router.put("/{record_id}")
def update_record(record_id: int, body: A3Body, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_pdca_records SET
                title           = %s,
                line_id         = %s,
                machine_name    = %s,
                capa_id         = %s,
                category        = %s,
                severity        = %s,
                owner           = %s,
                target_close_dt = %s,
                problem_text    = %s,
                root_cause      = %s,
                countermeasure  = %s,
                check_result    = %s,
                act_standardise = %s,
                plan_due_dt     = %s,
                do_due_dt       = %s,
                check_due_dt    = %s,
                act_due_dt      = %s,
                updated_at      = NOW()
            WHERE id = %s
        """, (body.title, body.line_id, body.machine_name, body.capa_id,
              body.category, body.severity, body.owner, body.target_close_dt,
              body.problem_text, body.root_cause, body.countermeasure,
              body.check_result, body.act_standardise,
              body.plan_due_dt, body.do_due_dt, body.check_due_dt, body.act_due_dt,
              record_id))
        conn.commit()
    return {"ok": True}


class PhaseAdvanceBody(BaseModel):
    to_phase: Optional[str] = None     # explicit target phase, or auto-advance
    note:     Optional[str] = None


@router.post("/{record_id}/advance")
def advance_phase(record_id: int, body: PhaseAdvanceBody, user=Depends(get_current_user)):
    """Move to next phase (PLAN→DO→CHECK→ACT) or to the explicit phase
    supplied.  Stamps the done_at column for the phase being CLOSED."""
    _ensure_tables()
    actor = user.get("username") if isinstance(user, dict) else "operator"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT current_phase FROM mes_pdca_records WHERE id = %s", (record_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "A3 not found")
        cur_phase = r["current_phase"]
        idx = PHASES.index(cur_phase) if cur_phase in PHASES else 0
        if body.to_phase and body.to_phase.upper() in PHASES:
            next_phase = body.to_phase.upper()
        else:
            next_phase = PHASES[min(idx + 1, len(PHASES) - 1)]
        # Stamp done_at on the current phase being closed
        done_col = {"PLAN": "plan_done_at", "DO": "do_done_at",
                    "CHECK": "check_done_at", "ACT": "act_done_at"}.get(cur_phase)
        cur2 = conn.cursor()
        if done_col:
            cur2.execute(f"UPDATE mes_pdca_records SET {done_col} = NOW() WHERE id = %s",
                          (record_id,))
        cur2.execute("""UPDATE mes_pdca_records
                          SET current_phase = %s,
                              status        = CASE WHEN %s = 'ACT' THEN 'CLOSED' ELSE 'IN_PROGRESS' END,
                              closed_at     = CASE WHEN %s = 'ACT' THEN NOW() ELSE closed_at END,
                              updated_at    = NOW()
                        WHERE id = %s""",
                      (next_phase, next_phase, next_phase, record_id))
        cur2.execute("""INSERT INTO mes_pdca_log
                          (pdca_id, event_by, event_type, from_phase, to_phase, note)
                        VALUES (%s,%s,'PHASE_DONE',%s,%s,%s)""",
                      (record_id, actor, cur_phase, next_phase, body.note))
        conn.commit()
    return {"ok": True, "new_phase": next_phase}


class StatusChangeBody(BaseModel):
    status: str
    note:   Optional[str] = None


@router.post("/{record_id}/status")
def change_status(record_id: int, body: StatusChangeBody, user=Depends(get_current_user)):
    _ensure_tables()
    st = body.status.upper()
    if st not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    actor = user.get("username") if isinstance(user, dict) else "operator"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE mes_pdca_records
                          SET status = %s,
                              closed_at = CASE WHEN %s IN ('CLOSED') THEN NOW() ELSE closed_at END,
                              updated_at = NOW()
                        WHERE id = %s""",
                    (st, st, record_id))
        cur.execute("""INSERT INTO mes_pdca_log
                          (pdca_id, event_by, event_type, note)
                       VALUES (%s,%s,'STATUS_CHANGE',%s)""",
                    (record_id, actor,
                     f"Status → {st}" + (f" · {body.note}" if body.note else "")))
        conn.commit()
    return {"ok": True}


@router.post("/{record_id}/note")
def add_note(record_id: int, body: StatusChangeBody, user=Depends(get_current_user)):
    """Append a free-form note to the timeline.  Reuses StatusChangeBody
    only for the `note` field; `status` is ignored here."""
    _ensure_tables()
    actor = user.get("username") if isinstance(user, dict) else "operator"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO mes_pdca_log
                          (pdca_id, event_by, event_type, note)
                       VALUES (%s,%s,'NOTE',%s)""",
                    (record_id, actor, body.note or ""))
        conn.commit()
    return {"ok": True}


@router.get("/{record_id}/log")
def get_log(record_id: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT * FROM mes_pdca_log
                        WHERE pdca_id = %s ORDER BY event_at""", (record_id,))
        return cur.fetchall()


# ════════════════════════════════════════════════════════════════════
#  Dashboard summary
# ════════════════════════════════════════════════════════════════════
@router.get("/summary/counts")
def summary_counts(user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT status, current_phase, COUNT(*) AS n
              FROM mes_pdca_records
          GROUP BY status, current_phase
        """)
        rows = cur.fetchall()
        # Aggregate
        out = {"by_status": {}, "by_phase": {}, "total": 0,
                "overdue": 0, "open": 0}
        for r in rows:
            out["by_status"][r["status"]] = out["by_status"].get(r["status"], 0) + r["n"]
            out["by_phase"][r["current_phase"]] = out["by_phase"].get(r["current_phase"], 0) + r["n"]
            out["total"] += r["n"]
        out["open"] = sum(c for st, c in out["by_status"].items() if st != "CLOSED")
        cur.execute("""SELECT COUNT(*) AS n FROM mes_pdca_records
                        WHERE status NOT IN ('CLOSED')
                          AND target_close_dt IS NOT NULL
                          AND target_close_dt < CURRENT_DATE""")
        r = cur.fetchone()
        out["overdue"] = r["n"] if r else 0
        return out
