"""
routers/manpower.py
===================
Skill-based manpower planning + daily allocation + alert engine.

End-to-end:
  1. Section Incharge defines processes per line (with required skill
     level + manpower count) — Admin → Processes UI.
  2. Admin assigns skill_level (1-5) to each operator — already in
     mes_operators (extended below).
  3. At shift start, operators badge-punch (existing widget) → row
     lands in mes_operator_punches.
  4. Within the per-line allocation_deadline_minutes (default 60),
     Shift Supervisor opens "Shift Allocation" and bulk-assigns
     punched-in operators to processes.  Save fires skill_match check
     and (on mismatch) immediate email to Quality + Section Incharge.
  5. Background watcher every 2 min:
       - If deadline elapsed and slots unfilled → UNALLOCATED alert
         (popup on Quality + Section Incharge dashboards + email).
       - If any new SKILL_MISMATCH lacking the email send → fire it.
       - For any unacknowledged alert older than ack_timeout_minutes
         → fire ESCALATION email to the escalation_to list, mark
         escalated_at so we don't keep escalating.
  6. Both Quality and Section Incharge must "Acknowledge" via the
     dashboard banner — separate ack columns track each side.
  7. Editable while shift is RUNNING; locked once shift ends.

History: allocations are append-only.  Removing an operator from a
process writes `removed_at` on the old row instead of DELETE, so the
"who-was-on-what-when" history stays intact and visible in the UI.

DB tables (auto-created idempotently in _ensure_tables):
  mes_processes
  mes_operator_punches
  mes_manpower_allocations
  mes_manpower_alerts
  mes_manpower_config
  mes_operators.skill_level   (ALTER TABLE ADD COLUMN)
"""
from __future__ import annotations

import os
import smtplib
import threading
import traceback
from datetime import datetime, date, timedelta, time as dt_time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/manpower", tags=["manpower"])


# ════════════════════════════════════════════════════════════════════
#  Schema
# ════════════════════════════════════════════════════════════════════

def _ensure_tables() -> None:
    """Idempotent.  Called on every endpoint hit; trivial cost."""
    with get_conn() as conn:
        cur = conn.cursor()
        # 0) Operators master — normally created by routers/operators.py,
        # but the watcher runs at boot before any UI hit, so we mirror
        # the schema here to make the manpower module standalone-safe.
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
        # 1) Section Incharge's process plan — auto-seeded from the
        # line's machine master (mes_machines).  Each row maps 1:1 to a
        # machine; Section Incharge only sets required_skill_level +
        # required_manpower_count + machines_covered.  process_name
        # always mirrors mes_machines.machine_name so renaming a machine
        # propagates without manual editing.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_processes (
                id                       SERIAL PRIMARY KEY,
                line_id                  INTEGER NOT NULL,
                machine_id               INTEGER,
                process_name             VARCHAR(120) NOT NULL,
                required_skill_level     INTEGER NOT NULL DEFAULT 3 CHECK (required_skill_level BETWEEN 1 AND 5),
                required_manpower_count  INTEGER NOT NULL DEFAULT 1 CHECK (required_manpower_count >= 1),
                machines_covered         INTEGER NOT NULL DEFAULT 1,
                display_order            INTEGER NOT NULL DEFAULT 0,
                is_active                BOOLEAN NOT NULL DEFAULT TRUE,
                created_at               TIMESTAMP DEFAULT NOW(),
                updated_at               TIMESTAMP DEFAULT NOW()
            )
        """)
        # Late-migration for installs created before machine_id column
        cur.execute("ALTER TABLE mes_processes ADD COLUMN IF NOT EXISTS machine_id INTEGER")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_processes_line_machine ON mes_processes (line_id, machine_id) WHERE machine_id IS NOT NULL")
        # 2) Operator skill column on existing master
        cur.execute("""
            ALTER TABLE mes_operators
                ADD COLUMN IF NOT EXISTS skill_level INTEGER NOT NULL DEFAULT 1
                CHECK (skill_level BETWEEN 1 AND 5)
        """)
        # 3) Daily punches (badge scan-in)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_operator_punches (
                id          SERIAL PRIMARY KEY,
                operator_id INTEGER NOT NULL REFERENCES mes_operators(id),
                line_id     INTEGER NOT NULL,
                shift_date  DATE    NOT NULL,
                shift_name  VARCHAR(10),
                punched_at  TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_punch_lookup
                       ON mes_operator_punches (line_id, shift_date, shift_name)""")
        # 4) Append-only allocations (history-preserving)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_manpower_allocations (
                id                SERIAL PRIMARY KEY,
                line_id           INTEGER NOT NULL,
                shift_date        DATE    NOT NULL,
                shift_name        VARCHAR(10) NOT NULL,
                process_id        INTEGER NOT NULL REFERENCES mes_processes(id),
                operator_id       INTEGER NOT NULL REFERENCES mes_operators(id),
                skill_match_flag  BOOLEAN NOT NULL,
                operator_skill_at INTEGER,
                process_req_at    INTEGER,
                allocated_by      VARCHAR(120),
                allocated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                removed_at        TIMESTAMP,
                removed_by        VARCHAR(120),
                notes             TEXT
            )
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_alloc_active
                       ON mes_manpower_allocations
                          (line_id, shift_date, shift_name)
                          WHERE removed_at IS NULL""")
        # 5) Per-line config (deadlines + recipient lists)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_manpower_config (
                line_id                      INTEGER PRIMARY KEY,
                allocation_deadline_minutes  INTEGER NOT NULL DEFAULT 60,
                ack_timeout_minutes          INTEGER NOT NULL DEFAULT 30,
                quality_to_addresses         TEXT NOT NULL DEFAULT '',
                section_incharge_to_addresses TEXT NOT NULL DEFAULT '',
                escalation_to_addresses      TEXT NOT NULL DEFAULT '',
                is_active                    BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at                   TIMESTAMP DEFAULT NOW()
            )
        """)
        # 6) Alerts (single source for popups + emails + escalation)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_manpower_alerts (
                id                  SERIAL PRIMARY KEY,
                line_id             INTEGER NOT NULL,
                shift_date          DATE    NOT NULL,
                shift_name          VARCHAR(10) NOT NULL,
                alert_kind          VARCHAR(40) NOT NULL,
                process_id          INTEGER,
                operator_id         INTEGER,
                context_text        TEXT,
                fired_at            TIMESTAMP NOT NULL DEFAULT NOW(),
                ack_quality_at      TIMESTAMP,
                ack_quality_by      VARCHAR(120),
                ack_incharge_at     TIMESTAMP,
                ack_incharge_by     VARCHAR(120),
                escalated_at        TIMESTAMP,
                resolved_at         TIMESTAMP
            )
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_alert_pending
                       ON mes_manpower_alerts (line_id, shift_date, shift_name)
                       WHERE resolved_at IS NULL""")
        conn.commit()


# ════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════

def _shift_start_today(line_id: int, shift_name: str) -> Optional[datetime]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT start_time FROM mes_shift_configs
                        WHERE line_id=%s AND shift_name=%s""", (line_id, shift_name))
        r = cur.fetchone()
        if not r or not r.get("start_time"):
            return None
        return datetime.combine(date.today(), r["start_time"])


def _shift_end_today(line_id: int, shift_name: str) -> Optional[datetime]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT end_time, crosses_midnight FROM mes_shift_configs
                        WHERE line_id=%s AND shift_name=%s""", (line_id, shift_name))
        r = cur.fetchone()
        if not r or not r.get("end_time"):
            return None
        end_dt = datetime.combine(date.today(), r["end_time"])
        if r.get("crosses_midnight"):
            end_dt += timedelta(days=1)
        return end_dt


def _send_mail(subject: str, html: str, to: List[str], cc: List[str]) -> None:
    """Reuses the SMTP config from .env (SMTP_HOST/PORT/USER/PASS).
    Silently no-ops if creds missing — watcher keeps running."""
    if not to:
        return
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    if not (smtp_user and smtp_pass):
        print("[MANPOWER-ALERT] SMTP not configured, skipping send.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to)
    if cc: msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
        srv.ehlo(); srv.starttls(); srv.login(smtp_user, smtp_pass)
        srv.sendmail(smtp_user, to + cc, msg.as_string())


# ════════════════════════════════════════════════════════════════════
#  Processes  (Section Incharge — derived from machine master)
# ════════════════════════════════════════════════════════════════════
#
# Processes are NOT manually entered.  They are auto-seeded from
# mes_machines for the line on every GET.  Section Incharge only edits:
#   • required_skill_level
#   • required_manpower_count
#   • machines_covered
#   • display_order
#
# process_name is always kept in sync with mes_machines.machine_name
# so a rename in the machine master propagates here on the next GET.

def _seed_processes_from_machines(line_id: int) -> None:
    """For a given line, ensure every row in mes_machines (joined via
    zone_name + line_name) has a matching mes_processes row.  Also
    refreshes process_name if the machine got renamed.

    Uses the same fuzzy (zone, line) resolver as routers/machines.py —
    mes_lines uses short codes like 'YNC-SS' while mes_machines uses
    full names like 'YNC Seat Slider', and the resolver handles the
    nf2_line_name override + prefix match.
    """
    # Local import to avoid circular import at module load time
    from routers.machines import _resolve_nf2_line  # noqa: WPS433

    with get_conn() as conn:
        zone_name, nf2_line = _resolve_nf2_line(conn, line_id)
        if not zone_name or not nf2_line:
            return

        cur = dict_cursor(conn)
        # Pull every active machine on this line, ordered by machine_no
        cur.execute("""
            SELECT id, machine_no, machine_name
              FROM mes_machines
             WHERE LOWER(zone_name) = LOWER(%s)
               AND LOWER(line_name) = LOWER(%s)
               AND is_active = TRUE
             ORDER BY machine_no
        """, (zone_name, nf2_line))
        machines = cur.fetchall()
        if not machines:
            return

        # Existing rows keyed by machine_id
        cur.execute("""SELECT id, machine_id, process_name, is_active
                         FROM mes_processes
                        WHERE line_id = %s AND machine_id IS NOT NULL""",
                    (line_id,))
        existing = {int(r["machine_id"]): r for r in cur.fetchall()}

        cur2 = conn.cursor()
        for idx, m in enumerate(machines):
            mid   = int(m["id"])
            mname = (m["machine_name"] or "").strip() or f"M-{m['machine_no']}"
            if mid in existing:
                # Refresh name + re-activate if previously soft-deleted
                if existing[mid]["process_name"] != mname or not existing[mid]["is_active"]:
                    cur2.execute("""UPDATE mes_processes
                                       SET process_name=%s, is_active=TRUE, updated_at=NOW()
                                     WHERE id=%s""",
                                  (mname, existing[mid]["id"]))
            else:
                cur2.execute("""
                    INSERT INTO mes_processes
                        (line_id, machine_id, process_name,
                         required_skill_level, required_manpower_count,
                         machines_covered, display_order, is_active)
                    VALUES (%s, %s, %s, 3, 1, 1, %s, TRUE)
                """, (line_id, mid, mname, (idx + 1) * 10))
        conn.commit()


class ProcessUpdate(BaseModel):
    """What Section Incharge is allowed to change."""
    required_skill_level:    int = 3
    required_manpower_count: int = 1
    machines_covered:        int = 1
    display_order:           int = 0
    is_active:               bool = True


@router.get("/processes")
def list_processes(line_id: Optional[int] = None, user=Depends(get_current_user)):
    """Auto-seeds from mes_machines on every call so the list always
    matches the live machine master.  Renames propagate, new machines
    appear automatically, retired machines stay (so historical
    allocations resolve their name)."""
    _ensure_tables()
    if line_id is not None:
        try:
            _seed_processes_from_machines(line_id)
        except Exception as exc:
            # Seeding failure shouldn't kill the GET — log and continue
            # with whatever's already in the table.
            print(f"[MANPOWER] seed from machines failed for line {line_id}: {exc}")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if line_id is not None:
            cur.execute("""SELECT p.*, m.machine_no, m.machine_name AS machine_master_name
                             FROM mes_processes p
                        LEFT JOIN mes_machines m ON m.id = p.machine_id
                            WHERE p.line_id=%s
                            ORDER BY p.display_order, m.machine_no, p.process_name""",
                        (line_id,))
        else:
            cur.execute("""SELECT p.*, m.machine_no, m.machine_name AS machine_master_name
                             FROM mes_processes p
                        LEFT JOIN mes_machines m ON m.id = p.machine_id
                            ORDER BY p.line_id, p.display_order, p.process_name""")
        return cur.fetchall()


@router.put("/processes/{process_id}")
def update_process(process_id: int, body: ProcessUpdate, admin=Depends(require_admin)):
    """Edit skill/manpower/display fields only.  process_name and
    machine_id are owned by the machine master — re-seeded on every GET."""
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_processes SET
                required_skill_level    = %s,
                required_manpower_count = %s,
                machines_covered        = %s,
                display_order           = %s,
                is_active               = %s,
                updated_at              = NOW()
            WHERE id = %s
        """, (body.required_skill_level, body.required_manpower_count,
              body.machines_covered, body.display_order, body.is_active,
              process_id))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Punches  (badge scan extends this)
# ════════════════════════════════════════════════════════════════════

class PunchBody(BaseModel):
    operator_id: int
    line_id:     int
    shift_name:  Optional[str] = None
    shift_date:  Optional[str] = None  # YYYY-MM-DD; default today


@router.post("/punches", status_code=201)
def add_punch(body: PunchBody, user=Depends(get_current_user)):
    _ensure_tables()
    d = (datetime.strptime(body.shift_date, "%Y-%m-%d").date()
         if body.shift_date else date.today())
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_operator_punches (operator_id, line_id, shift_date, shift_name)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (body.operator_id, body.line_id, d, body.shift_name))
        pid = cur.fetchone()[0]
        conn.commit()
    return {"id": pid, "ok": True}


@router.get("/punches")
def list_punches(line_id: int = Query(...),
                  date: str   = Query(...),
                  shift: Optional[str] = None,
                  user=Depends(get_current_user)):
    """Return the punched-in operator pool for a shift."""
    _ensure_tables()
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        params = [line_id, d]
        q = """SELECT DISTINCT ON (o.id)
                      o.id, o.badge_code, o.full_name, o.employee_id,
                      o.department, o.skill_level,
                      p.punched_at, p.shift_name
                 FROM mes_operator_punches p
                 JOIN mes_operators o ON o.id = p.operator_id
                WHERE p.line_id=%s AND p.shift_date=%s"""
        if shift:
            q += " AND p.shift_name=%s"
            params.append(shift)
        q += " ORDER BY o.id, p.punched_at DESC"
        cur.execute(q, tuple(params))
        return cur.fetchall()


# ════════════════════════════════════════════════════════════════════
#  Allocations  (with history)
# ════════════════════════════════════════════════════════════════════

class AllocRow(BaseModel):
    process_id:   int
    operator_id:  int


class AllocSaveBody(BaseModel):
    line_id:    int
    shift_date: str
    shift_name: str
    rows:       List[AllocRow]
    allocated_by: Optional[str] = None


def _is_shift_open(line_id: int, shift_date: date, shift_name: str) -> bool:
    """User said: editable during current shift, locked after shift end."""
    if shift_date != date.today():
        return False
    end_dt = _shift_end_today(line_id, shift_name)
    if not end_dt:
        return True
    return datetime.now() < end_dt


@router.get("/allocations")
def list_allocations(line_id: int = Query(...),
                      date: str   = Query(...),
                      shift: str  = Query(...),
                      include_history: bool = False,
                      user=Depends(get_current_user)):
    """Return current ACTIVE allocations (removed_at IS NULL) by default;
    with include_history=True returns every row ever written for this
    shift so the UI can show the full change log."""
    _ensure_tables()
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        sql = """SELECT a.*, p.process_name, p.required_skill_level,
                        p.required_manpower_count,
                        o.full_name, o.badge_code, o.skill_level AS op_skill,
                        o.employee_id
                   FROM mes_manpower_allocations a
                   JOIN mes_processes  p ON p.id = a.process_id
                   JOIN mes_operators  o ON o.id = a.operator_id
                  WHERE a.line_id=%s AND a.shift_date=%s AND a.shift_name=%s"""
        if not include_history:
            sql += " AND a.removed_at IS NULL"
        sql += " ORDER BY p.display_order, a.allocated_at"
        cur.execute(sql, (line_id, d, shift))
        return cur.fetchall()


@router.post("/allocations")
def save_allocations(body: AllocSaveBody,
                      user=Depends(get_current_user)):
    """Replace current active allocations with the supplied set.
    Implementation:
      1. For every active row not in the new set → set removed_at=NOW().
      2. For every new (process_id, operator_id) pair not currently
         active → INSERT a fresh row with skill_match_flag computed.
    Result: history is append-only.  Skill mismatches fire an email
    to Quality + Section Incharge on save (NOT just on watcher tick).
    """
    _ensure_tables()
    try:
        d = datetime.strptime(body.shift_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "shift_date must be YYYY-MM-DD")

    if not _is_shift_open(body.line_id, d, body.shift_name):
        raise HTTPException(423, "Shift is over — allocations are locked.")

    who = body.allocated_by or (user.get("username") if isinstance(user, dict) else "system")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        # Process + operator lookup tables
        cur.execute("""SELECT id, process_name, required_skill_level
                         FROM mes_processes WHERE line_id=%s AND is_active=TRUE""",
                    (body.line_id,))
        procs = {int(r["id"]): r for r in cur.fetchall()}
        cur.execute("""SELECT id, full_name, skill_level
                         FROM mes_operators WHERE is_active=TRUE""")
        ops   = {int(r["id"]): r for r in cur.fetchall()}

        # Snapshot of currently active allocations (so we can diff)
        cur.execute("""SELECT id, process_id, operator_id
                         FROM mes_manpower_allocations
                        WHERE line_id=%s AND shift_date=%s AND shift_name=%s
                          AND removed_at IS NULL""",
                    (body.line_id, d, body.shift_name))
        active = {(int(r["process_id"]), int(r["operator_id"])): int(r["id"])
                  for r in cur.fetchall()}

        new_set = {(r.process_id, r.operator_id) for r in body.rows}

        # 1) Close removed rows
        to_close = [aid for k, aid in active.items() if k not in new_set]
        cur2 = conn.cursor()
        for aid in to_close:
            cur2.execute("""UPDATE mes_manpower_allocations
                                SET removed_at=NOW(), removed_by=%s
                              WHERE id=%s""", (who, aid))

        # 2) Insert genuinely-new pairs
        mismatch_rows = []
        for pair in new_set - set(active.keys()):
            p_id, o_id = pair
            proc = procs.get(p_id)
            op   = ops.get(o_id)
            if not proc or not op:
                continue
            need  = int(proc["required_skill_level"] or 1)
            has   = int(op["skill_level"] or 1)
            match = has >= need
            cur2.execute("""
                INSERT INTO mes_manpower_allocations
                    (line_id, shift_date, shift_name, process_id, operator_id,
                     skill_match_flag, operator_skill_at, process_req_at,
                     allocated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (body.line_id, d, body.shift_name, p_id, o_id,
                  match, has, need, who))
            new_id = cur2.fetchone()[0]
            if not match:
                mismatch_rows.append({
                    "process": proc["process_name"],
                    "operator": op["full_name"],
                    "need": need, "has": has,
                    "alloc_id": new_id,
                })
        conn.commit()

    # 3) Fire skill-mismatch alerts (one per row) + email
    if mismatch_rows:
        _fire_skill_mismatch_alerts(body.line_id, d, body.shift_name, mismatch_rows)

    return {"ok": True,
            "closed": len(to_close),
            "added": len(new_set - set(active.keys())),
            "mismatches": len(mismatch_rows)}


# ════════════════════════════════════════════════════════════════════
#  Alerts
# ════════════════════════════════════════════════════════════════════

def _fire_skill_mismatch_alerts(line_id: int, shift_date: date, shift_name: str,
                                  rows: List[dict]) -> None:
    """Called from save_allocations on every NEW mismatch row."""
    _ensure_tables()
    cfg = _load_config(line_id)
    if not cfg:
        return
    line_name = _line_name(line_id) or f"Line #{line_id}"

    with get_conn() as conn:
        cur = conn.cursor()
        for r in rows:
            cur.execute("""
                INSERT INTO mes_manpower_alerts
                    (line_id, shift_date, shift_name, alert_kind,
                     context_text)
                VALUES (%s, %s, %s, 'SKILL_MISMATCH', %s)
                RETURNING id
            """, (line_id, shift_date, shift_name,
                  f"{r['operator']} (L{r['has']}) allocated to "
                  f"{r['process']} which needs L{r['need']}"))
        conn.commit()

    to_list = _addr_list(cfg["quality_to_addresses"]) + \
              _addr_list(cfg["section_incharge_to_addresses"])
    if not to_list:
        return
    rows_html = "".join(
        f"<tr><td style='padding:4px 12px;'>{r['process']}</td>"
        f"<td style='padding:4px 12px;'>{r['operator']}</td>"
        f"<td style='padding:4px 12px;color:#dc2626;'>L{r['has']}</td>"
        f"<td style='padding:4px 12px;color:#0f172a;'>L{r['need']}</td></tr>"
        for r in rows
    )
    subject = f"[MES · Skill mismatch] {line_name} — Shift {shift_name} — {len(rows)} row(s)"
    html = f"""
    <div style="font-family:Arial;color:#0f172a;border-left:5px solid #d97706;padding:18px 22px;">
      <h2 style="margin:0 0 6px;color:#d97706;">⚠ SKILL LEVEL MISMATCH</h2>
      <p style="margin:0 0 10px;color:#475569;font-size:13px;">
        Supervisor saved manpower allocation with operators whose skill level is
        below the process requirement.  Review on the dashboard.
      </p>
      <table style="border-collapse:collapse;font-size:12px;">
        <tr style="background:#f1f5f9;text-align:left;">
          <th style="padding:6px 12px;">Process</th>
          <th style="padding:6px 12px;">Operator</th>
          <th style="padding:6px 12px;">Has</th>
          <th style="padding:6px 12px;">Needs</th>
        </tr>{rows_html}
      </table>
    </div>"""
    try:
        _send_mail(subject, html, to_list, [])
    except Exception as exc:
        print(f"[MANPOWER-ALERT] mail send failed: {exc}")


def _line_name(line_id: int) -> Optional[str]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT line_name FROM mes_lines WHERE id=%s", (line_id,))
        r = cur.fetchone()
        return r.get("line_name") if r else None


def _load_config(line_id: int) -> Optional[dict]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_manpower_config WHERE line_id=%s AND is_active=TRUE",
                    (line_id,))
        return cur.fetchone()


def _addr_list(s: Optional[str]) -> List[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


# ── Alerts CRUD ────────────────────────────────────────────────────

@router.get("/alerts")
def list_alerts(line_id: Optional[int] = None,
                 pending_only: bool = True,
                 user=Depends(get_current_user)):
    """Return alerts.  Default `pending_only=True` filters to unresolved.
    Dashboards poll this every 30 s to render the banner."""
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        q = """SELECT a.*, p.process_name, o.full_name AS operator_name
                 FROM mes_manpower_alerts a
            LEFT JOIN mes_processes  p ON p.id = a.process_id
            LEFT JOIN mes_operators  o ON o.id = a.operator_id"""
        wh = []
        params: List = []
        if line_id is not None:
            wh.append("a.line_id=%s")
            params.append(line_id)
        if pending_only:
            wh.append("a.resolved_at IS NULL")
        if wh:
            q += " WHERE " + " AND ".join(wh)
        q += " ORDER BY a.fired_at DESC"
        cur.execute(q, tuple(params))
        return cur.fetchall()


class AckBody(BaseModel):
    alert_id: int
    side:     str   # "quality" | "incharge"


@router.post("/alerts/ack")
def acknowledge(body: AckBody, user=Depends(get_current_user)):
    _ensure_tables()
    side = body.side.strip().lower()
    if side not in ("quality", "incharge"):
        raise HTTPException(400, "side must be 'quality' or 'incharge'")
    who = user.get("username") if isinstance(user, dict) else "system"
    col = "ack_quality" if side == "quality" else "ack_incharge"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""UPDATE mes_manpower_alerts
                            SET {col}_at = NOW(), {col}_by = %s
                          WHERE id = %s AND {col}_at IS NULL
                       RETURNING ack_quality_at, ack_incharge_at""",
                    (who, body.alert_id))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return {"ok": True, "noop": True}
        # If BOTH sides have now acked, mark resolved.
        aq, ai = row
        if aq is not None and ai is not None:
            cur.execute("UPDATE mes_manpower_alerts SET resolved_at = NOW() WHERE id = %s",
                        (body.alert_id,))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Config CRUD  (admin)
# ════════════════════════════════════════════════════════════════════

class ConfigUpsert(BaseModel):
    line_id:                       int
    allocation_deadline_minutes:   int  = 60
    ack_timeout_minutes:           int  = 30
    quality_to_addresses:          str  = ""
    section_incharge_to_addresses: str  = ""
    escalation_to_addresses:       str  = ""
    is_active:                     bool = True


@router.get("/config")
def list_config(user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_manpower_config ORDER BY line_id")
        return cur.fetchall()


@router.put("/config")
def upsert_config(body: ConfigUpsert, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_manpower_config
                (line_id, allocation_deadline_minutes, ack_timeout_minutes,
                 quality_to_addresses, section_incharge_to_addresses,
                 escalation_to_addresses, is_active, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (line_id) DO UPDATE
                SET allocation_deadline_minutes   = EXCLUDED.allocation_deadline_minutes,
                    ack_timeout_minutes           = EXCLUDED.ack_timeout_minutes,
                    quality_to_addresses          = EXCLUDED.quality_to_addresses,
                    section_incharge_to_addresses = EXCLUDED.section_incharge_to_addresses,
                    escalation_to_addresses       = EXCLUDED.escalation_to_addresses,
                    is_active                     = EXCLUDED.is_active,
                    updated_at                    = NOW()
        """, (body.line_id, body.allocation_deadline_minutes, body.ack_timeout_minutes,
              body.quality_to_addresses, body.section_incharge_to_addresses,
              body.escalation_to_addresses, body.is_active))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Watcher  —  unallocated detection + escalation
# ════════════════════════════════════════════════════════════════════

_STOP   = threading.Event()
_THREAD: Optional[threading.Thread] = None
_unalloc_fired: dict = {}   # (line_id, date, shift) -> alert_id (dedupe)


def _current_active_shift(line_id: int) -> Optional[str]:
    """Return the shift the line is CURRENTLY in (not GAP)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT shift_name, start_time, end_time, crosses_midnight
                         FROM mes_shift_configs
                        WHERE line_id=%s AND NOT shift_name LIKE 'GAP%%'""",
                    (line_id,))
        now = datetime.now()
        for r in cur.fetchall():
            st = datetime.combine(date.today(), r["start_time"])
            et = datetime.combine(date.today(), r["end_time"])
            if r["crosses_midnight"]:
                et += timedelta(days=1)
            if st <= now < et:
                return r["shift_name"]
    return None


def _watcher_tick() -> None:
    try:
        _ensure_tables()
    except Exception as exc:
        print(f"[MANPOWER-ALERT] schema ensure failed: {exc}")
        return

    # Load all active configs
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT c.*, l.line_name FROM mes_manpower_config c
                         JOIN mes_lines l ON l.id = c.line_id
                        WHERE c.is_active=TRUE""")
        cfgs = cur.fetchall()

    now = datetime.now()
    for cfg in cfgs:
        line_id   = int(cfg["line_id"])
        shift     = _current_active_shift(line_id)
        if not shift:
            continue
        shift_start = _shift_start_today(line_id, shift)
        if not shift_start:
            continue
        deadline = shift_start + timedelta(minutes=int(cfg["allocation_deadline_minutes"] or 60))
        if now < deadline:
            continue   # still within first-N-minutes window

        key = (line_id, date.today(), shift)

        # ── UNALLOCATED check ──
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT COALESCE(SUM(required_manpower_count), 0)::int AS req
                             FROM mes_processes
                            WHERE line_id=%s AND is_active=TRUE""", (line_id,))
            required = int(cur.fetchone()["req"] or 0)
            cur.execute("""SELECT COUNT(*)::int AS got
                             FROM mes_manpower_allocations
                            WHERE line_id=%s AND shift_date=CURRENT_DATE
                              AND shift_name=%s AND removed_at IS NULL""",
                        (line_id, shift))
            got = int(cur.fetchone()["got"] or 0)

        if required > 0 and got < required and key not in _unalloc_fired:
            # Fire UNALLOCATED alert (one-shot per shift)
            with get_conn() as conn:
                c2 = conn.cursor()
                c2.execute("""INSERT INTO mes_manpower_alerts
                                (line_id, shift_date, shift_name, alert_kind, context_text)
                              VALUES (%s, %s, %s, 'UNALLOCATED', %s)
                              RETURNING id""",
                            (line_id, date.today(), shift,
                             f"Allocation incomplete: {got}/{required} slots filled "
                             f"after deadline ({int(cfg['allocation_deadline_minutes'])} min)."))
                aid = c2.fetchone()[0]
                conn.commit()
            _unalloc_fired[key] = aid

            line_name = cfg.get("line_name") or f"Line #{line_id}"
            to_list = _addr_list(cfg["quality_to_addresses"]) + \
                      _addr_list(cfg["section_incharge_to_addresses"])
            subject = f"[MES · Manpower] {line_name} — Shift {shift} — Allocation incomplete"
            html = f"""
            <div style="font-family:Arial;color:#0f172a;border-left:5px solid #dc2626;padding:18px 22px;">
              <h2 style="margin:0 0 6px;color:#dc2626;">🛑 Manpower allocation INCOMPLETE</h2>
              <p style="margin:0 0 10px;color:#475569;font-size:13px;">
                Allocation deadline of <b>{cfg['allocation_deadline_minutes']} min</b> after shift
                start has elapsed.  <b>{got}/{required}</b> manpower slots are filled.
              </p>
              <p style="font-size:11.5px;color:#64748b;">
                Acknowledge the alert on the dashboard.  If not acknowledged within
                <b>{cfg['ack_timeout_minutes']} min</b>, an escalation email goes to the next senior.
              </p>
            </div>"""
            try:
                _send_mail(subject, html, to_list, [])
            except Exception as exc:
                print(f"[MANPOWER-ALERT] unalloc mail fail: {exc}")
            print(f"[MANPOWER-ALERT] UNALLOCATED line={line_id} shift={shift}: {got}/{required}")

        # ── ESCALATION on stale alerts ──
        ack_timeout = int(cfg["ack_timeout_minutes"] or 30)
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT * FROM mes_manpower_alerts
                            WHERE line_id=%s AND resolved_at IS NULL
                              AND escalated_at IS NULL
                              AND fired_at < NOW() - %s::interval""",
                        (line_id, f"{ack_timeout} minutes"))
            stale = cur.fetchall()
        if stale:
            to_esc = _addr_list(cfg["escalation_to_addresses"])
            if to_esc:
                rows_html = "".join(
                    f"<tr><td style='padding:4px 12px;'>{a['alert_kind']}</td>"
                    f"<td style='padding:4px 12px;'>{a.get('process_name') or '—'}</td>"
                    f"<td style='padding:4px 12px;'>{a.get('operator_name') or '—'}</td>"
                    f"<td style='padding:4px 12px;color:#475569;'>{(a['context_text'] or '')[:140]}</td>"
                    f"<td style='padding:4px 12px;color:#94a3b8;'>{a['fired_at'].strftime('%H:%M:%S')}</td></tr>"
                    for a in stale
                )
                line_name = cfg.get("line_name") or f"Line #{line_id}"
                subject = f"[MES · ESCALATION] {line_name} — Shift {shift} — {len(stale)} unacked manpower alerts"
                html = f"""
                <div style="font-family:Arial;color:#0f172a;border-left:5px solid #7f1d1d;padding:18px 22px;">
                  <h2 style="margin:0 0 6px;color:#7f1d1d;">🚨 ESCALATION</h2>
                  <p style="margin:0 0 10px;color:#475569;font-size:13px;">
                    The following manpower alert(s) have NOT been acknowledged
                    within <b>{ack_timeout} min</b> by either Quality or Section Incharge.
                  </p>
                  <table style="border-collapse:collapse;font-size:12px;">
                    <tr style="background:#f1f5f9;text-align:left;">
                      <th style="padding:6px 12px;">Kind</th>
                      <th style="padding:6px 12px;">Process</th>
                      <th style="padding:6px 12px;">Operator</th>
                      <th style="padding:6px 12px;">Detail</th>
                      <th style="padding:6px 12px;">Fired</th>
                    </tr>{rows_html}
                  </table>
                </div>"""
                try:
                    _send_mail(subject, html, to_esc, [])
                except Exception as exc:
                    print(f"[MANPOWER-ALERT] escalation mail fail: {exc}")

            # Mark all as escalated so we don't re-send
            with get_conn() as conn:
                c2 = conn.cursor()
                c2.execute("""UPDATE mes_manpower_alerts SET escalated_at=NOW()
                                WHERE id = ANY(%s)""",
                            ([a["id"] for a in stale],))
                conn.commit()
            print(f"[MANPOWER-ALERT] Escalated {len(stale)} alert(s) for line {line_id}")


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _watcher_tick()
        except Exception as exc:
            print(f"[MANPOWER-ALERT] loop error: {exc}")
            traceback.print_exc()
        # Midnight reset of the "fired" memory so tomorrow's shifts
        # can re-arm.
        if datetime.now().hour == 0 and datetime.now().minute < 2:
            _unalloc_fired.clear()
        _STOP.wait(60)   # 1-min cadence is plenty for human deadlines


def start_watcher() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, daemon=True, name="manpower-alert")
    _THREAD.start()
    print("[MANPOWER-ALERT] Worker started — checks every 60 s")
