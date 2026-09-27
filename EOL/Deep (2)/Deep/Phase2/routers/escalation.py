# ───────────────────────────────────────────────────────────────────────
# escalation.py   (/api/escalation)   2026-09-01
# ───────────────────────────────────────────────────────────────────────
# Shift-end NG/alarm ESCALATION with a per-zone, person-based hierarchy.
#
#   • Admin builds, per ZONE, an ordered chain of PEOPLE (Level 1 = shift
#     incharge, Level 2 = section incharge, … top-down)  →  mes_zone_escalation.
#   • At/after a shift's SCHEDULED END, every line that had alarm (NG) parts
#     gets one escalation instance (mes_shift_escalation) carrying a concise
#     English summary + the alarm parts (with video links), assigned to the
#     zone chain's Level 1.
#   • Each level opens "My Escalations", sees the summary + parts + video,
#     and hits COMPLETE → it advances to the next person in the chain.  When
#     the last level completes, the escalation closes.
#
# READ-ONLY on counting/OEE: this is a pure review/sign-off layer over data
# that already exists (ct_log NG rows + mes_ng_remarks).  It never writes any
# dashboard/collector column.
# ───────────────────────────────────────────────────────────────────────
import re
import json
from datetime import date, datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

# Web-push notify (optional — never let a push problem break escalations).
try:
    from routers.push import send_to_user as _push_send
except Exception:
    def _push_send(*a, **k):
        return 0

router = APIRouter(prefix="/api/escalation", tags=["escalation"])

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEAD_ROLES = ("admin", "plant_head")   # see every open escalation (oversight)


# ── schema ──────────────────────────────────────────────────────────────
@once
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        # Per-zone ordered chain of people.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_zone_escalation (
                zone_id   INTEGER NOT NULL,
                level_no  INTEGER NOT NULL,
                admin_id  INTEGER NOT NULL,
                PRIMARY KEY (zone_id, level_no)
            )""")
        #  2026-09-27 — per-SHIFT override of the chain (operator: "escalation
        #  hierarchy me shift wise shift incharge assign option").  The base
        #  table above stays the default for every shift; a zone only needs rows
        #  here for the shifts whose chain differs.  Kept as a separate table so
        #  no existing row or key changes.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_zone_escalation_shift (
                zone_id    INTEGER     NOT NULL,
                shift_name VARCHAR(10) NOT NULL,
                level_no   INTEGER     NOT NULL,
                admin_id   INTEGER     NOT NULL,
                PRIMARY KEY (zone_id, shift_name, level_no)
            )""")
        #  One place that answers "who is the chain for this zone in THIS
        #  shift" — the shift's own rows when it has any, otherwise the zone
        #  default.  Read-only and STABLE, so it is safe inside any query.
        cur.execute("""
            CREATE OR REPLACE FUNCTION mes_esc_chain(p_zone INTEGER, p_shift TEXT)
            RETURNS TABLE(level_no INTEGER, admin_id INTEGER)
            LANGUAGE sql STABLE AS $fn$
                SELECT level_no, admin_id
                  FROM mes_zone_escalation_shift
                 WHERE zone_id = p_zone AND shift_name = p_shift
                UNION ALL
                SELECT level_no, admin_id
                  FROM mes_zone_escalation
                 WHERE zone_id = p_zone
                   AND NOT EXISTS (SELECT 1 FROM mes_zone_escalation_shift
                                    WHERE zone_id = p_zone AND shift_name = p_shift)
            $fn$""")
        # One escalation instance per (line, date, shift).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_shift_escalation (
                id            SERIAL PRIMARY KEY,
                line_id       INTEGER NOT NULL,
                zone_id       INTEGER,
                record_date   DATE    NOT NULL,
                shift_name    VARCHAR(10) NOT NULL,
                current_level INTEGER NOT NULL DEFAULT 1,
                status        VARCHAR(10) NOT NULL DEFAULT 'open',  -- open | closed
                summary       TEXT,
                alarms        JSONB,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (line_id, record_date, shift_name)
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_shift_escalation_log (
                id            SERIAL PRIMARY KEY,
                escalation_id INTEGER NOT NULL,
                level_no      INTEGER NOT NULL,
                admin_id      INTEGER,
                action        VARCHAR(16) NOT NULL,   -- created | completed | closed
                comment       TEXT,
                at            TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        conn.commit()


def _parse_date(s: Optional[str]) -> date:
    if not s:
        return datetime.now().date()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return datetime.now().date()


# ── alarm summary for one line+shift (concise English) ──────────────────
def _line_shift_alarms(cur, line_id: int, db_table: str, line_name: str,
                       rec_date: date, shift: str):
    """Return (alarms[list], summary[str]) for a line's NG parts in a shift."""
    alarms = []
    if not db_table:
        return alarms, ""
    ctlog = db_table + "_ct_log"
    if not _TABLE_RE.match(ctlog):
        return alarms, ""
    try:
        # Some configured lines (e.g. newly-added recliner lines) carry a
        # db_table_name whose *_ct_log table was never created.  Querying a
        # missing table raises and — because this runs inside the sweep's shared
        # transaction — poisons it: the escalations already inserted for the
        # good lines get silently discarded at commit while their pushes still
        # fire (endless duplicate "New escalation" notifications to escalations
        # that never persisted).  to_regclass() is a safe existence probe — it
        # returns NULL (never raises) for a missing table — so we bail cleanly
        # with no alarms and, crucially, without poisoning the transaction.
        cur.execute("SELECT to_regclass(%s) AS reg", (ctlog,))
        if not (cur.fetchone() or {}).get("reg"):
            return alarms, ""
        cur.execute(f"""SELECT ts, cycle_seq, part_code, ct_value
                          FROM {ctlog}
                         WHERE record_date=%s AND shift_name=%s
                           AND COALESCE(is_ng,false)=true
                         ORDER BY ts""", (rec_date, shift))
        rows = cur.fetchall()
    except Exception:
        return alarms, ""
    rem = {}
    try:
        cur.execute("""SELECT part_code, leader_remark FROM mes_ng_remarks
                        WHERE line_id=%s AND leader_remark IS NOT NULL""", (line_id,))
        rem = {r["part_code"]: r["leader_remark"] for r in cur.fetchall() if r.get("part_code")}
    except Exception:
        rem = {}
    for r in rows:
        alarms.append({
            "line_id":   line_id,
            "cycle_seq": r.get("cycle_seq"),
            "part_code": r.get("part_code"),
            "time":      r["ts"].strftime("%H:%M:%S") if r.get("ts") else None,
            "ct":        (float(r["ct_value"]) if r.get("ct_value") is not None else None),
            "remark":    rem.get(r.get("part_code")),
        })
    n = len(alarms)
    if not n:
        return alarms, ""
    wr = sum(1 for a in alarms if a["remark"])
    summary = (f"{n} alarm{'s' if n != 1 else ''} on {line_name} in shift {shift} "
               f"({rec_date.isoformat()}); {wr} with remark, {n - wr} pending review.")
    return alarms, summary


def _sched_end(cur, line_id: int, shift: str, rec_date: date):
    """Scheduled end datetime for a line's production shift (midnight-aware)."""
    try:
        cur.execute("""SELECT start_time, end_time, COALESCE(crosses_midnight,false) xm
                         FROM mes_shift_configs
                        WHERE line_id=%s AND shift_name=%s""", (line_id, shift))
        r = cur.fetchone()
        if not (r and r.get("start_time") and r.get("end_time")):
            return None
        s = datetime.combine(rec_date, r["start_time"])
        e = datetime.combine(rec_date, r["end_time"])
        if r["xm"] or e <= s:
            e += timedelta(days=1)
        return e
    except Exception:
        return None


# ── the shift-end sweep: create missing escalation instances ────────────
def _sweep():
    """For today's ENDED production shifts on lines whose ZONE has a chain,
    create one open escalation (Level 1) per line that had alarms and has no
    instance yet.  Idempotent (UNIQUE line/date/shift).  Best-effort."""
    now = datetime.now()
    today = now.date()
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            # zones that actually have a chain configured
            cur.execute("""SELECT DISTINCT zone_id FROM mes_zone_escalation
                            UNION
                           SELECT DISTINCT zone_id FROM mes_zone_escalation_shift""")
            zone_ids = [r["zone_id"] for r in cur.fetchall()]
            if not zone_ids:
                return

            #  Level-1 person per (zone, SHIFT) — a zone may hand shift A and
            #  shift B to different people (2026-09-27).  Resolved through
            #  mes_esc_chain(), which falls back to the zone default.
            _l1_cache: dict = {}

            def l1_for(zid, shift):
                key = (zid, str(shift or ""))
                if key not in _l1_cache:
                    cur.execute("""SELECT admin_id FROM mes_esc_chain(%s,%s)
                                    WHERE level_no=1""", (zid, str(shift or "")))
                    r = cur.fetchone()
                    _l1_cache[key] = r["admin_id"] if r else None
                return _l1_cache[key]
            notify = []
            fresh  = []      # (esc_id, level1_admin_id, line_name, summary)
            cur.execute("""SELECT id, line_name, db_table_name, zone_id
                             FROM mes_lines
                            WHERE zone_id = ANY(%s) AND COALESCE(is_active,true)""",
                        (zone_ids,))
            lines = cur.fetchall()
            made = 0
            for ln in lines:
                for shift in ("A", "B"):
                    se = _sched_end(cur, ln["id"], shift, today)
                    if not se or se >= now:
                        continue     # shift not ended yet (or not configured)
                    # Isolate every line/shift in its own SAVEPOINT so one bad
                    # line (missing *_ct_log, bad data, …) can't abort the whole
                    # sweep transaction and silently discard the escalations we
                    # already inserted for the good lines.  On any error we roll
                    # back only this line's work and carry on.
                    try:
                        cur.execute("SAVEPOINT esc_line")
                        cur.execute("""SELECT 1 FROM mes_shift_escalation
                                        WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                                    (ln["id"], today, shift))
                        if cur.fetchone():
                            cur.execute("RELEASE SAVEPOINT esc_line")
                            continue      # already have an instance
                        alarms, summary = _line_shift_alarms(
                            cur, ln["id"], ln["db_table_name"], ln["line_name"], today, shift)
                        if not alarms:
                            cur.execute("RELEASE SAVEPOINT esc_line")
                            continue      # no NG parts → nothing to escalate
                        cur.execute("""INSERT INTO mes_shift_escalation
                                         (line_id, zone_id, record_date, shift_name,
                                          current_level, status, summary, alarms)
                                       VALUES (%s,%s,%s,%s,1,'open',%s,%s)
                                       ON CONFLICT (line_id, record_date, shift_name) DO NOTHING
                                       RETURNING id""",
                                    (ln["id"], ln["zone_id"], today, shift,
                                     summary, json.dumps(alarms)))
                        row = cur.fetchone()
                        if row:
                            cur.execute("""INSERT INTO mes_shift_escalation_log
                                             (escalation_id, level_no, action, comment)
                                           VALUES (%s,1,'created',%s)""",
                                        (row["id"], summary))
                            made += 1
                            fresh.append((row["id"], l1_for(ln["zone_id"], shift),
                                          ln["line_name"], summary))
                        cur.execute("RELEASE SAVEPOINT esc_line")
                    except Exception as le:
                        try:
                            cur.execute("ROLLBACK TO SAVEPOINT esc_line")
                            cur.execute("RELEASE SAVEPOINT esc_line")
                        except Exception:
                            pass
                        print(f"[ESCALATION] skip line {ln.get('id')}/{shift}: {str(le)[:100]}")
                        continue
            conn.commit()
            if made:
                print(f"[ESCALATION] sweep created {made} instance(s)")
            # Notify ONLY escalations that actually survived the commit — re-read
            # the fresh ids back and push just those.  This guarantees a rolled-
            # back insert can never fire a phantom "New escalation" notification
            # (the bug that flooded users with duplicates pointing at escalations
            # that were never persisted).
            if fresh:
                ids = [f[0] for f in fresh]
                cur.execute("SELECT id FROM mes_shift_escalation WHERE id = ANY(%s)", (ids,))
                live = {r["id"] for r in cur.fetchall()}
                for eid, aid, lname, summ in fresh:
                    if eid in live and aid:
                        notify.append((aid, lname, summ))
        # Push each fresh escalation to its Level-1 person (after commit).
        for aid, lname, summ in notify:
            if aid:
                _push_send(aid, f"New escalation — {lname}", summ,
                           url="/my-escalations", tag="escalation")
    except Exception as e:
        print(f"[ESCALATION] sweep failed: {e}")


# ── Phase 2: admin hierarchy ────────────────────────────────────────────
@router.get("/admins")
def list_admins(user=Depends(get_current_user)):
    """People (admin users) an escalation level can be assigned to."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, username AS name, username, role
                         FROM mes_admin
                        ORDER BY role, username""")
        return [dict(r) for r in cur.fetchall()]


@router.get("/zone/{zone_id}/chain")
def get_chain(zone_id: int, shift: str = "", user=Depends(get_current_user)):
    """The zone's chain.  `shift` empty = the default chain every shift uses;
    `shift=A|B|…` = that shift's own chain, and `inherited` says whether it is
    still falling back to the default."""
    _ensure_tables()
    sh = (shift or "").strip().upper()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        own = 0
        if sh:
            cur.execute("""SELECT count(*) AS n FROM mes_zone_escalation_shift
                            WHERE zone_id=%s AND shift_name=%s""", (zone_id, sh))
            own = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("""SELECT c.level_no, c.admin_id, a.username AS name, a.role
                             FROM mes_esc_chain(%s,%s) c
                             LEFT JOIN mes_admin a ON a.id = c.admin_id
                            ORDER BY c.level_no""", (zone_id, sh))
        else:
            cur.execute("""SELECT e.level_no, e.admin_id, a.username AS name, a.role
                             FROM mes_zone_escalation e
                             LEFT JOIN mes_admin a ON a.id = e.admin_id
                            WHERE e.zone_id=%s ORDER BY e.level_no""", (zone_id,))
        return {"zone_id": zone_id, "shift": sh,
                "inherited": bool(sh) and own == 0,
                "chain": [dict(r) for r in cur.fetchall()]}


class ChainBody(BaseModel):
    admin_ids: List[int]   # ordered: index 0 = Level 1, …


@router.put("/zone/{zone_id}/chain")
def set_chain(zone_id: int, body: ChainBody, shift: str = "",
              user=Depends(get_current_user)):
    """Replace a zone's chain.  Admin only.

    With no `shift` this writes the zone default, exactly as before.  With
    `shift=A|B|…` it writes only that shift's chain (2026-09-27, operator:
    "shift wise shift incharge assign option") — and an EMPTY list clears the
    override so that shift goes back to following the default.
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    _ensure_tables()
    sh = (shift or "").strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        if sh:
            cur.execute("""DELETE FROM mes_zone_escalation_shift
                            WHERE zone_id=%s AND shift_name=%s""", (zone_id, sh))
            for i, aid in enumerate(body.admin_ids, start=1):
                cur.execute("""INSERT INTO mes_zone_escalation_shift
                                 (zone_id, shift_name, level_no, admin_id)
                               VALUES (%s,%s,%s,%s)""", (zone_id, sh, i, int(aid)))
        else:
            cur.execute("DELETE FROM mes_zone_escalation WHERE zone_id=%s", (zone_id,))
            for i, aid in enumerate(body.admin_ids, start=1):
                cur.execute("""INSERT INTO mes_zone_escalation (zone_id, level_no, admin_id)
                               VALUES (%s,%s,%s)""", (zone_id, i, int(aid)))
        conn.commit()
    return get_chain(zone_id, sh, user)


@router.get("/shifts")
def esc_shifts(user=Depends(get_current_user)):
    """Shift names actually configured on the plant, for the chain editor."""
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        try:
            cur.execute("""SELECT DISTINCT shift_name FROM mes_shift_configs
                            WHERE COALESCE(shift_name,'') <> '' ORDER BY shift_name""")
            out = [r["shift_name"] for r in cur.fetchall()]
        except Exception:
            out = []
        conn.rollback()
    return {"shifts": out or ["A", "B"]}


# ── Phase 3: the flow ───────────────────────────────────────────────────
@router.get("/my")
def my_escalations(user=Depends(get_current_user)):
    """Open escalations where the CURRENT LEVEL is assigned to me (heads see
    all open).  Runs the shift-end sweep first so ended shifts show up."""
    _ensure_tables()
    _sweep()
    uid = user.get("id")
    is_head = user.get("role") in _HEAD_ROLES
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT s.*, l.line_name, z.zone_name,
                   ce.admin_id AS current_admin_id,
                   a.username AS current_name,
                   --  per-shift chain (2026-09-27): a zone can hand shift A and
                   --  shift B to different people, so both the level count and
                   --  the current person are resolved for THIS row's shift.
                   (SELECT MAX(level_no)
                      FROM mes_esc_chain(s.zone_id, s.shift_name)) AS max_level
              FROM mes_shift_escalation s
              LEFT JOIN mes_lines l ON l.id = s.line_id
              LEFT JOIN mes_zones z ON z.id = s.zone_id
              LEFT JOIN LATERAL (SELECT admin_id
                                   FROM mes_esc_chain(s.zone_id, s.shift_name)
                                  WHERE level_no = s.current_level) ce ON TRUE
              LEFT JOIN mes_admin a ON a.id = ce.admin_id
             WHERE s.status='open'
             ORDER BY s.record_date DESC, s.shift_name, l.line_name""")
        out = []
        for r in cur.fetchall():
            d = dict(r)
            if not is_head and d.get("current_admin_id") != uid:
                continue
            d["record_date"] = d["record_date"].isoformat() if d.get("record_date") else None
            d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
            d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
            d["mine"] = (d.get("current_admin_id") == uid)
            out.append(d)
        return out


class CompleteBody(BaseModel):
    comment: Optional[str] = None


@router.post("/{esc_id}/complete")
def complete_level(esc_id: int, body: CompleteBody, user=Depends(get_current_user)):
    """Current-level assignee (or a head) completes → advance to the next
    person in the zone chain; closes when the last level completes."""
    _ensure_tables()
    uid = user.get("id")
    is_head = user.get("role") in _HEAD_ROLES
    next_admin = None
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_shift_escalation WHERE id=%s FOR UPDATE", (esc_id,))
        s = cur.fetchone()
        if not s:
            raise HTTPException(404, "Escalation not found")
        if s["status"] != "open":
            raise HTTPException(409, "Already closed")
        lvl = s["current_level"]
        cur.execute("""SELECT admin_id FROM mes_esc_chain(%s,%s)
                        WHERE level_no=%s""",
                    (s["zone_id"], s["shift_name"], lvl))
        ar = cur.fetchone()
        cur_admin = ar["admin_id"] if ar else None
        if not is_head and cur_admin != uid:
            raise HTTPException(403, "Not your escalation level")
        cur.execute("""INSERT INTO mes_shift_escalation_log
                         (escalation_id, level_no, admin_id, action, comment)
                       VALUES (%s,%s,%s,'completed',%s)""",
                    (esc_id, lvl, uid, (body.comment or None)))
        cur.execute("SELECT MAX(level_no) mx FROM mes_esc_chain(%s,%s)",
                    (s["zone_id"], s["shift_name"]))
        mx = (cur.fetchone() or {}).get("mx") or lvl
        if lvl >= mx:
            cur.execute("""UPDATE mes_shift_escalation
                              SET status='closed', updated_at=now() WHERE id=%s""", (esc_id,))
            cur.execute("""INSERT INTO mes_shift_escalation_log
                             (escalation_id, level_no, admin_id, action, comment)
                           VALUES (%s,%s,%s,'closed',%s)""",
                        (esc_id, lvl, uid, "All levels complete"))
            new_status, new_level = "closed", lvl
        else:
            new_level = lvl + 1
            cur.execute("""UPDATE mes_shift_escalation
                              SET current_level=%s, updated_at=now() WHERE id=%s""",
                        (new_level, esc_id))
            new_status = "open"
            cur.execute("""SELECT admin_id FROM mes_esc_chain(%s,%s)
                            WHERE level_no=%s""",
                        (s["zone_id"], s["shift_name"], new_level))
            nr = cur.fetchone()
            next_admin = nr["admin_id"] if nr else None
        conn.commit()
    # Advanced to the next person → push them (after commit).
    if next_admin:
        _push_send(next_admin, "Escalation — action needed",
                   s.get("summary") or "An escalation was escalated to you.",
                   url="/my-escalations", tag="escalation")
    return {"id": esc_id, "status": new_status, "current_level": new_level}


@router.get("/{esc_id}/trail")
def trail(esc_id: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT g.level_no, g.action, g.comment, g.at,
                              a.username AS by_name
                         FROM mes_shift_escalation_log g
                         LEFT JOIN mes_admin a ON a.id = g.admin_id
                        WHERE g.escalation_id=%s ORDER BY g.at""", (esc_id,))
        rows = []
        for r in cur.fetchall():
            d = dict(r); d["at"] = d["at"].isoformat() if d.get("at") else None
            rows.append(d)
        return rows
