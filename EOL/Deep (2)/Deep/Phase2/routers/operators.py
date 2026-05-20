"""
routers/operators.py
====================
Per-operator productivity tracking.

The shop-floor has a USB barcode/RFID reader on the dashboard PC.
When an operator starts their shift they scan their badge — the reader
fires "<badge_code>\\n" into the keyboard buffer.  The Dashboard widget
catches that, POSTs `/api/operators/login`, and the row goes into
`mes_operator_sessions`.  When they leave they scan again (toggle) and
the session is closed with `ended_at`.

OK/NG cycles produced while a session is open are attributed to that
operator by joining `mes_operator_sessions` to the line's ct_log on
timestamp range — no per-cycle write needed, which keeps the collector
hot loop unchanged.

Endpoints
---------
POST   /api/operators                          (admin) create operator
GET    /api/operators                          (any)   list operators
DELETE /api/operators/{operator_id}            (admin)

POST   /api/operators/login                    badge_code + line_id   → start session
POST   /api/operators/logout                   line_id                → close active session
GET    /api/operators/active/{line_id}                                → who's on duty now
GET    /api/operators/shift-summary?line_id=&date=&shift=             productivity per operator
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/operators", tags=["operators"])


def _ensure_tables() -> None:
    """Idempotent — runs on every endpoint hit but trivial cost."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_operators (
                id           SERIAL PRIMARY KEY,
                badge_code   VARCHAR(64) UNIQUE NOT NULL,
                full_name    VARCHAR(120) NOT NULL,
                employee_id  VARCHAR(40),
                department   VARCHAR(40),
                is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                created_at   TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_operator_sessions (
                id           SERIAL PRIMARY KEY,
                operator_id  INTEGER NOT NULL REFERENCES mes_operators(id),
                line_id      INTEGER NOT NULL,
                started_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                ended_at     TIMESTAMP,
                shift_name   VARCHAR(10),
                notes        TEXT
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_op_sess_line_active
                ON mes_operator_sessions (line_id) WHERE ended_at IS NULL
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_op_sess_range
                ON mes_operator_sessions (line_id, started_at, ended_at)
        """)
        conn.commit()


# ── Operator master CRUD ─────────────────────────────────────────────

class OperatorUpsert(BaseModel):
    badge_code:  str
    full_name:   str
    employee_id: Optional[str] = None
    department:  Optional[str] = None
    skill_level: int  = 1   # 1=trainee … 5=expert; matched against process.required_skill_level
    is_active:   bool = True


@router.post("", status_code=201)
def create_operator(body: OperatorUpsert, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # Ensure skill_level column exists — manpower.py also creates it but
            # operators master may be hit first.
            cur.execute("""ALTER TABLE mes_operators
                ADD COLUMN IF NOT EXISTS skill_level INTEGER NOT NULL DEFAULT 1
                CHECK (skill_level BETWEEN 1 AND 5)""")
            cur.execute("""
                INSERT INTO mes_operators
                    (badge_code, full_name, employee_id, department,
                     skill_level, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (badge_code) DO UPDATE
                    SET full_name   = EXCLUDED.full_name,
                        employee_id = EXCLUDED.employee_id,
                        department  = EXCLUDED.department,
                        skill_level = EXCLUDED.skill_level,
                        is_active   = EXCLUDED.is_active
                RETURNING id
            """, (body.badge_code.strip(), body.full_name.strip(),
                  body.employee_id, body.department,
                  max(1, min(5, body.skill_level)), body.is_active))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(400, f"Could not save operator: {exc}")
    return {"id": new_id, "ok": True}


@router.get("")
def list_operators(user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # skill_level may be missing on fresh DB before manpower.py migrations
        cur.execute("""ALTER TABLE mes_operators
            ADD COLUMN IF NOT EXISTS skill_level INTEGER NOT NULL DEFAULT 1""")
        cur.execute("""SELECT id, badge_code, full_name, employee_id, department,
                              skill_level, is_active, created_at
                         FROM mes_operators ORDER BY full_name""")
        return cur.fetchall()


@router.delete("/{operator_id}")
def delete_operator(operator_id: int, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_operators WHERE id = %s", (operator_id,))
        conn.commit()
    return {"ok": True}


# ── Session login / logout ───────────────────────────────────────────

class LoginBody(BaseModel):
    badge_code: str
    line_id:    int
    shift_name: Optional[str] = None


@router.post("/login")
def operator_login(body: LoginBody, user=Depends(get_current_user)):
    """Open a session.  If the operator already has an open session on
    this line, no-op (returns the existing row).  If a *different*
    operator is currently active on the line, close that session first
    (auto-logout the previous operator)."""
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT * FROM mes_operators
                        WHERE badge_code = %s AND is_active = TRUE""",
                    (body.badge_code.strip(),))
        op = cur.fetchone()
        if not op:
            raise HTTPException(404, f"No active operator with badge {body.badge_code!r}")

        # Is this operator already logged in on this line?
        cur.execute("""SELECT * FROM mes_operator_sessions
                        WHERE line_id = %s AND ended_at IS NULL
                          AND operator_id = %s""", (body.line_id, op["id"]))
        existing_same = cur.fetchone()
        if existing_same:
            return {"id": existing_same["id"], "operator": op["full_name"],
                    "already_active": True}

        # Auto-close any OTHER operator's open session on this line.
        cur2 = conn.cursor()
        cur2.execute("""UPDATE mes_operator_sessions
                            SET ended_at = NOW(), notes = COALESCE(notes,'') || ' [auto-closed on new login]'
                          WHERE line_id = %s AND ended_at IS NULL""",
                     (body.line_id,))
        cur2.execute("""INSERT INTO mes_operator_sessions
                            (operator_id, line_id, shift_name)
                        VALUES (%s, %s, %s)
                        RETURNING id""",
                     (op["id"], body.line_id, body.shift_name))
        sess_id = cur2.fetchone()[0]
        # Daily punch row — feeds the supervisor's "pool of punched-in
        # operators" on the Shift Allocation page.  Best-effort: table
        # is auto-created by manpower._ensure_tables on first hit there.
        try:
            cur2.execute("""
                CREATE TABLE IF NOT EXISTS mes_operator_punches (
                    id          SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL,
                    line_id     INTEGER NOT NULL,
                    shift_date  DATE    NOT NULL,
                    shift_name  VARCHAR(10),
                    punched_at  TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            cur2.execute("""INSERT INTO mes_operator_punches
                              (operator_id, line_id, shift_date, shift_name)
                            VALUES (%s, %s, CURRENT_DATE, %s)""",
                          (op["id"], body.line_id, body.shift_name))
        except Exception as exc:
            print(f"[OPERATOR-LOGIN] punch insert skipped: {exc}")
        conn.commit()
    return {"id": sess_id, "operator": op["full_name"], "started": True}


class LogoutBody(BaseModel):
    line_id: int


@router.post("/logout")
def operator_logout(body: LogoutBody, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE mes_operator_sessions
                          SET ended_at = NOW()
                        WHERE line_id = %s AND ended_at IS NULL
                        RETURNING id""", (body.line_id,))
        rows = cur.fetchall()
        conn.commit()
    return {"closed": len(rows)}


@router.get("/active/{line_id}")
def active_operator(line_id: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT s.id AS session_id, s.started_at, s.shift_name,
                              o.id AS operator_id, o.full_name, o.badge_code,
                              o.employee_id, o.department
                         FROM mes_operator_sessions s
                         JOIN mes_operators o ON o.id = s.operator_id
                        WHERE s.line_id = %s AND s.ended_at IS NULL
                        ORDER BY s.started_at DESC LIMIT 1""", (line_id,))
        row = cur.fetchone()
    if not row:
        return {"active": False}
    return {"active": True, **row}


# ── Per-shift productivity summary ───────────────────────────────────

@router.get("/shift-summary")
def shift_summary(line_id: int = Query(...),
                   date: str   = Query(...),
                   shift: str  = Query(...),
                   user=Depends(get_current_user)):
    """For each operator session that overlapped this shift, count how
    many OK / NG cycles fell inside the session's [started_at, ended_at]
    window from the line's ct_log."""
    _ensure_tables()
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        ln = cur.fetchone()
        if not ln:
            raise HTTPException(404, "line not found")
        table = ln["db_table_name"]
        ct_table = f"{table}_ct_log"

        cur.execute(f"""
            SELECT o.id AS operator_id, o.full_name, o.employee_id, o.department,
                   s.id AS session_id, s.started_at, s.ended_at,
                   COALESCE(SUM(CASE WHEN ct.is_ng = FALSE THEN 1 ELSE 0 END), 0)::int AS oks,
                   COALESCE(SUM(CASE WHEN ct.is_ng = TRUE  THEN 1 ELSE 0 END), 0)::int AS ngs,
                   COALESCE(COUNT(ct.id), 0)::int                                       AS cycles,
                   COALESCE(AVG(ct.ct_value), 0)::float                                  AS avg_ct
              FROM mes_operator_sessions s
              JOIN mes_operators o ON o.id = s.operator_id
         LEFT JOIN {ct_table} ct
                ON ct.record_date = %s
               AND ct.shift_name  = %s
               AND ct.ts >= s.started_at
               AND ct.ts <  COALESCE(s.ended_at, NOW())
             WHERE s.line_id = %s
               AND s.started_at::date <= %s
               AND COALESCE(s.ended_at, NOW())::date >= %s
             GROUP BY o.id, o.full_name, o.employee_id, o.department,
                      s.id, s.started_at, s.ended_at
             ORDER BY s.started_at
        """, (d, shift, line_id, d, d))
        return cur.fetchall()
