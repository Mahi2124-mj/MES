# ───────────────────────────────────────────────────────────────────────
# leaders.py   (/api/leaders)   2026-09-13
# ───────────────────────────────────────────────────────────────────────
# Leader Allocation — the 2nd module in Operator Master (shift-incharge & up).
# Assign line leaders (mes_admin role='leader') to LINES; one leader can handle
# MANY lines (stored in the shared mes_operator_lines scope table).  Each leader
# carries a CAPABILITY FACTOR — auto from the MES production (avg OEE) + quality
# (NG %) of the lines they run over the last 7 days, plus a manual
# Trained / Needs-training flag — so a shift incharge sees who's best and who
# still needs training.
# ───────────────────────────────────────────────────────────────────────
import re
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, hash_password
from ddl_once import once

router = APIRouter(prefix="/api/leaders", tags=["leaders"])
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED = ("admin", "plant_head", "section_incharge", "shift_incharge",
            "production_incharge")


def _gate(user):
    if user.get("role") not in _ALLOWED:
        raise HTTPException(403, "Shift incharge and above only")


@once
def _ensure_meta(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_leader_meta (
                     admin_id   INTEGER PRIMARY KEY REFERENCES mes_admin(id) ON DELETE CASCADE,
                     trained    BOOLEAN NOT NULL DEFAULT FALSE,
                     note       TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # Employee code (to identify the person) + a zone tag (which zone the leader
    # was added under — display / filter only, not a line assignment).
    cur.execute("ALTER TABLE mes_leader_meta ADD COLUMN IF NOT EXISTS employee_code TEXT")
    cur.execute("ALTER TABLE mes_leader_meta ADD COLUMN IF NOT EXISTS zone_id INTEGER")
    # 2026-09-17 — the leader's signature, stored as a data: URL so the
    # breakdown slip can stamp it the moment a leader is picked instead of
    # waiting for a paper signature.  Kept on the leader record (not on each
    # slip) so one upload serves every slip that leader ever signs.
    cur.execute("ALTER TABLE mes_leader_meta ADD COLUMN IF NOT EXISTS signature_image TEXT")


def _ensure_shift_alloc(cur):
    # Per-shift line-leader assignment (like manpower): who leads this line for
    # this date+shift.  One leader per line+shift; a leader may lead many lines.
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_leader_shift_alloc (
                     line_id    INTEGER NOT NULL,
                     shift_date DATE    NOT NULL,
                     shift_name VARCHAR(10) NOT NULL,
                     leader_id  INTEGER NOT NULL REFERENCES mes_admin(id) ON DELETE CASCADE,
                     assigned_by TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                     PRIMARY KEY (line_id, shift_date, shift_name))""")


def _quality(cur, db_table, d0):
    """(parts, ng) over [d0, today] from a line's ct_log — autocommit conn so a
    missing table can't poison anything."""
    if not db_table or not _TABLE_RE.match(db_table):
        return (0, 0)
    ctlog = db_table + "_ct_log"
    try:
        cur.execute("SELECT to_regclass(%s) AS r", (ctlog,))
        if not (cur.fetchone() or {}).get("r"):
            return (0, 0)
        cur.execute(f"""SELECT COUNT(*) AS tot, COUNT(*) FILTER (WHERE is_ng) AS ng
                          FROM {ctlog} WHERE record_date >= %s""", (d0,))
        r = cur.fetchone() or {}
        return (int(r.get("tot") or 0), int(r.get("ng") or 0))
    except Exception:
        return (0, 0)


def _oee(cur, db_table, d0):
    if not db_table or not _TABLE_RE.match(db_table):
        return None
    try:
        cur.execute(f"""SELECT AVG(overall_oee) AS a FROM {db_table}
                         WHERE record_date >= %s AND overall_oee IS NOT NULL""", (d0,))
        r = cur.fetchone() or {}
        return round(float(r["a"]), 1) if r.get("a") is not None else None
    except Exception:
        return None


def _capability(oee, ng_pct, trained, parts):
    """Simple 0-100 capability + a label the shift incharge can act on."""
    if parts < 20 and oee is None:
        return (None, "No data")
    o = oee if oee is not None else 0.0
    q = max(0.0, 100.0 - (ng_pct or 0.0) * 4.0)     # quality score (NG heavily penalised)
    base = round(0.6 * o + 0.4 * q, 1)               # production-weighted blend
    cap = min(100.0, base + (5 if trained else 0))
    if not trained and cap < 70:
        label = "Needs training"
    elif cap >= 80:
        label = "Best"
    elif cap >= 65:
        label = "Good"
    else:
        label = "Average"
    return (round(cap, 1), label)


@router.get("")
def list_leaders(days: int = 7, user=Depends(get_current_user)):
    _gate(user)
    d0 = date.today() - timedelta(days=max(1, min(days, 60)) - 1)
    out = []
    with get_conn() as conn:
        conn.autocommit = True          # read-only; each stmt independent so a
        try:                            # missing ct_log/table can't poison the loop
            cur = dict_cursor(conn)
            _ensure_meta(cur)
            _ensure_shift_alloc(cur)
            cur.execute("""SELECT a.id, a.username, COALESCE(m.trained,FALSE) AS trained,
                                  m.note, m.employee_code, m.signature_image, m.zone_id, z.zone_name
                             FROM mes_admin a
                             LEFT JOIN mes_leader_meta m ON m.admin_id = a.id
                             LEFT JOIN mes_zones z ON z.id = m.zone_id
                            WHERE a.role='leader' ORDER BY a.username""")
            leaders = cur.fetchall()
            for ld in leaders:
                # Lines the leader actually LED in the window (per-shift allocations)
                # drive the capability metrics.
                cur.execute("""SELECT DISTINCT l.id, l.line_name, l.db_table_name
                                 FROM mes_leader_shift_alloc sa JOIN mes_lines l ON l.id=sa.line_id
                                WHERE sa.leader_id=%s AND sa.shift_date >= %s
                                  AND COALESCE(l.is_active,TRUE)=TRUE
                                ORDER BY l.line_name""", (ld["id"], d0))
                lines = cur.fetchall()
                tot = ng = 0
                oees = []
                for ln in lines:
                    t, n = _quality(cur, ln["db_table_name"], d0)
                    tot += t; ng += n
                    o = _oee(cur, ln["db_table_name"], d0)
                    if o is not None:
                        oees.append(o)
                ng_pct = round(ng * 100.0 / tot, 2) if tot else 0.0
                avg_oee = round(sum(oees) / len(oees), 1) if oees else None
                cap, label = _capability(avg_oee, ng_pct, ld["trained"], tot)
                out.append({
                    "id": ld["id"], "username": ld["username"],
                    "trained": ld["trained"], "note": ld["note"],
                    "employee_code": ld.get("employee_code"),
                    "signature_image": ld.get("signature_image"),
                    "zone_id": ld.get("zone_id"), "zone_name": ld.get("zone_name"),
                    "lines": [{"id": l["id"], "name": l["line_name"]} for l in lines],
                    "line_count": len(lines),
                    "oee": avg_oee, "ng_pct": ng_pct, "parts": tot,
                    "capability": cap, "label": label,
                })
        finally:
            try: conn.autocommit = False
            except Exception: pass
    out.sort(key=lambda x: (x["capability"] is None, -(x["capability"] or 0), x["username"]))
    return {"days": max(1, min(days, 60)), "leaders": out}


@router.get("/candidates")
def leader_candidates(user=Depends(get_current_user)):
    """Existing users that can be made a leader (not admins/plant heads, not
    already leaders).  The Employee Master → Leaders 'Assign Leader' picker."""
    _gate(user)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, username, role FROM mes_admin
                        WHERE role NOT IN ('admin','plant_head','leader')
                        ORDER BY username""")
        return {"candidates": cur.fetchall()}


@router.get("/zones")
def leader_zones(user=Depends(get_current_user)):
    """All zones (id + name) for the 'add leader' zone dropdown."""
    _gate(user)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, zone_name FROM mes_zones ORDER BY zone_name")
        return {"zones": cur.fetchall()}


class AssignBody(BaseModel):
    user_id: int


@router.post("/assign")
def assign_leader(body: AssignBody, user=Depends(get_current_user)):
    """Promote an EXISTING user to role='leader' (no new account is created)."""
    _gate(user)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM mes_admin WHERE id=%s", (body.user_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "User not found")
        if r[0] in ("admin", "plant_head"):
            raise HTTPException(400, "Cannot reassign an admin / plant head")
        cur.execute("UPDATE mes_admin SET role='leader' WHERE id=%s", (body.user_id,))
        conn.commit()
    return {"ok": True, "id": body.user_id}


def _leader_identity(cur, name, emp, exclude_id=None):
    """Resolve the login username for a leader and refuse a real duplicate.

    2026-09-19 — two different people can share a name; they cannot share an
    employee code.  The check used to be on the NAME ("Username already
    exists"), so a second "Rahul Kumar" with his own code could not be added.
    Now the employee code is what must be unique, and the name only has to be
    unique as a LOGIN id: when the plain name is already taken by another
    account, the login becomes "Name (code)".  That same string is what every
    leader dropdown, shift allocation and comment shows, so the two people
    stay distinguishable everywhere without touching login or those screens.
    """
    if emp:
        cur.execute("""SELECT a.username FROM mes_leader_meta m
                         JOIN mes_admin a ON a.id = m.admin_id
                        WHERE lower(btrim(m.employee_code)) = lower(btrim(%s))
                          AND a.role = 'leader' AND a.id <> %s""",
                    (emp, exclude_id or -1))
        hit = cur.fetchone()
        if hit:
            raise HTTPException(409, f"Employee code {emp} already belongs to leader {hit[0]}")

    def taken(u):
        cur.execute("SELECT 1 FROM mes_admin WHERE lower(username)=lower(%s) AND id <> %s",
                    (u, exclude_id or -1))
        return cur.fetchone() is not None

    if not taken(name):
        return name
    if not emp:
        raise HTTPException(409, f"A user named {name} already exists — "
                                 f"enter the employee code to tell them apart")
    alt = f"{name} ({emp})"
    if taken(alt):
        raise HTTPException(409, f"{alt} already exists")
    return alt


class NewLeaderBody(BaseModel):
    username: str
    password: Optional[str] = None       # optional — defaults to the employee code
    employee_code: Optional[str] = None
    zone_id: Optional[int] = None
    line_ids: Optional[List[int]] = None


@router.post("")
def create_leader(body: NewLeaderBody, user=Depends(get_current_user)):
    """Create a new role='leader' user right here (no full admin panel needed).
    Shift incharge & above only; role is forced to 'leader'.  Add with just a
    NAME + EMPLOYEE CODE — the employee code (which also identifies the person)
    becomes the initial login password, so the account is valid and can log in.
    An optional zone tag is stored alongside in mes_leader_meta."""
    _gate(user)
    uname = (body.username or "").strip()
    if not uname:
        raise HTTPException(400, "Name required")
    emp = (body.employee_code or "").strip() or None
    # Password = the given one, else the employee code (used as the login pass).
    pwd = (body.password or "").strip() or (emp or "")
    if len(pwd) < 4:
        raise HTTPException(400, "Employee code (used as the login password) must be at least 4 characters")
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_meta(cur)
        uname = _leader_identity(cur, uname, emp)
        cur.execute("""INSERT INTO mes_admin (username, password_hash, password_plain, role)
                       VALUES (%s,%s,%s,'leader') RETURNING id""",
                    (uname, hash_password(pwd), pwd))
        new_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO mes_leader_meta (admin_id, employee_code, zone_id, updated_at)
                       VALUES (%s,%s,%s,now())
                       ON CONFLICT (admin_id) DO UPDATE SET
                         employee_code = EXCLUDED.employee_code,
                         zone_id       = EXCLUDED.zone_id,
                         updated_at    = now()""",
                    (new_id, emp, body.zone_id))
        for lid in (body.line_ids or []):
            cur.execute("""INSERT INTO mes_operator_lines (admin_id, line_id)
                           VALUES (%s,%s) ON CONFLICT DO NOTHING""", (new_id, int(lid)))
        conn.commit()
    return {"ok": True, "id": new_id, "username": uname}


class EditLeaderBody(BaseModel):
    username: str
    employee_code: Optional[str] = None
    zone_id: Optional[int] = None


@router.put("/{leader_id}")
def edit_leader(leader_id: int, body: EditLeaderBody, user=Depends(get_current_user)):
    """Edit a leader's name, employee code and zone (Employee Master → Edit).

    Same uniqueness rule as adding: the employee code must be unique, the name
    only as a login id.  The password is NOT touched — it started out as the
    employee code, but the leader may have changed it since, and silently
    resetting it would lock them out.
    """
    _gate(user)
    name = (body.username or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    emp = (body.employee_code or "").strip() or None
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_meta(cur)
        cur.execute("SELECT role, username FROM mes_admin WHERE id=%s", (leader_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Leader not found")
        if row[0] != "leader":
            raise HTTPException(400, "Only leaders can be edited here")
        # Keep the current login unless the edit actually changes it.  A login
        # of "Name (code)" still matches a form showing "Name", as long as the
        # code in brackets is still this leader's code.
        cur_login = row[1]
        same_plain = name.lower() == cur_login.lower()
        same_suffixed = bool(emp) and cur_login.lower() == f"{name} ({emp})".lower()
        if same_plain or same_suffixed:
            _leader_identity(cur, cur_login, emp, exclude_id=leader_id)  # code check only
            uname = cur_login
        else:
            uname = _leader_identity(cur, name, emp, exclude_id=leader_id)
        if body.zone_id is not None:
            cur.execute("SELECT 1 FROM mes_zones WHERE id=%s", (body.zone_id,))
            if not cur.fetchone():
                raise HTTPException(400, "Unknown zone")
        cur.execute("UPDATE mes_admin SET username=%s WHERE id=%s", (uname, leader_id))
        cur.execute("""INSERT INTO mes_leader_meta (admin_id, employee_code, zone_id, updated_at)
                       VALUES (%s,%s,%s,now())
                       ON CONFLICT (admin_id) DO UPDATE SET
                         employee_code = EXCLUDED.employee_code,
                         zone_id       = EXCLUDED.zone_id,
                         updated_at    = now()""",
                    (leader_id, emp, body.zone_id))
        conn.commit()
    return {"ok": True, "id": leader_id, "username": uname,
            "login_changed": uname != cur_login}


@router.delete("/{leader_id}")
def delete_leader(leader_id: int, user=Depends(get_current_user)):
    """Remove a leader that was added by mistake.  Deletes the mes_admin login
    row; mes_leader_meta and mes_leader_shift_alloc drop via ON DELETE CASCADE,
    and mes_operator_lines is cleared explicitly.  Only role='leader' rows can be
    deleted (never an admin / plant head), and not the caller's own account."""
    _gate(user)
    if int(leader_id) == int(user.get("id") or user.get("user_id") or -1):
        raise HTTPException(400, "You cannot delete your own account")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role, username FROM mes_admin WHERE id=%s", (leader_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Leader not found")
        if row[0] != "leader":
            raise HTTPException(400, "Only leaders can be deleted here")
        cur.execute("DELETE FROM mes_operator_lines WHERE admin_id=%s", (leader_id,))
        cur.execute("DELETE FROM mes_admin WHERE id=%s", (leader_id,))
        conn.commit()
    return {"ok": True, "id": leader_id, "username": row[1]}


class LinesBody(BaseModel):
    line_ids: List[int]


@router.put("/{leader_id}/lines")
def set_leader_lines(leader_id: int, body: LinesBody, user=Depends(get_current_user)):
    _gate(user)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM mes_admin WHERE id=%s", (leader_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Leader not found")
        cur.execute("DELETE FROM mes_operator_lines WHERE admin_id=%s", (leader_id,))
        seen = set()
        for lid in body.line_ids:
            if lid in seen:
                continue
            seen.add(lid)
            cur.execute("""INSERT INTO mes_operator_lines (admin_id, line_id)
                           VALUES (%s,%s) ON CONFLICT DO NOTHING""", (leader_id, int(lid)))
        conn.commit()
    return {"ok": True, "leader_id": leader_id, "line_ids": sorted(seen)}


class MetaBody(BaseModel):
    trained: Optional[bool] = None
    note: Optional[str] = None
    employee_code: Optional[str] = None
    zone_id: Optional[int] = None


@router.put("/{leader_id}/meta")
def set_leader_meta(leader_id: int, body: MetaBody, user=Depends(get_current_user)):
    _gate(user)
    emp = (body.employee_code.strip() if body.employee_code is not None else None) or None
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_meta(cur)
        cur.execute("""INSERT INTO mes_leader_meta (admin_id, trained, note, employee_code, zone_id, updated_at)
                       VALUES (%s, COALESCE(%s,FALSE), %s, %s, %s, now())
                       ON CONFLICT (admin_id) DO UPDATE SET
                         trained       = COALESCE(EXCLUDED.trained, mes_leader_meta.trained),
                         note          = COALESCE(EXCLUDED.note, mes_leader_meta.note),
                         employee_code = COALESCE(EXCLUDED.employee_code, mes_leader_meta.employee_code),
                         zone_id       = COALESCE(EXCLUDED.zone_id, mes_leader_meta.zone_id),
                         updated_at    = now()""",
                    (leader_id, body.trained, body.note, emp, body.zone_id))
        conn.commit()
    return {"ok": True}


# ── Per-shift line-leader allocation (used from the Shift Allocation page) ──
@router.get("/shift-alloc")
def get_shift_alloc(line_id: int, date: str, shift: str,
                    user=Depends(get_current_user)):
    """The leader assigned to this line for this date+shift (or null)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_shift_alloc(cur)
        cur.execute("""SELECT sa.leader_id, a.username
                         FROM mes_leader_shift_alloc sa
                         LEFT JOIN mes_admin a ON a.id = sa.leader_id
                        WHERE sa.line_id=%s AND sa.shift_date=%s AND sa.shift_name=%s""",
                    (line_id, date, shift))
        r = cur.fetchone()
        conn.commit()
    return {"leader_id": (r["leader_id"] if r else None),
            "username": (r["username"] if r else None)}


@router.get("/for-line")
def leaders_for_line(line_id: int, date: str = None, shift: str = None,
                     user=Depends(get_current_user)):
    """Dropdown data for the per-cycle-comment leader field (any logged-in user):
      • default  — the leader the shift-incharge assigned to this line for this
                   date+shift (mes_leader_shift_alloc), i.e. the pre-shift leader.
      • options  — leaders (mes_admin role='leader') assigned to this line
                   (mes_operator_lines); if none, every leader — so the operator
                   can override the attribution for a single comment."""
    from datetime import date as _date
    if not date:
        date = str(_date.today())
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_shift_alloc(cur)
        cur.execute("""SELECT a.id, a.username
                         FROM mes_admin a
                         JOIN mes_operator_lines ol ON ol.admin_id = a.id
                        WHERE a.role = 'leader' AND ol.line_id = %s
                        ORDER BY a.username""", (line_id,))
        options = cur.fetchall()
        if not options:
            cur.execute("SELECT id, username FROM mes_admin "
                        "WHERE role='leader' ORDER BY username")
            options = cur.fetchall()
        default = None
        if shift:
            cur.execute("""SELECT a.username
                             FROM mes_leader_shift_alloc sa
                             JOIN mes_admin a ON a.id = sa.leader_id
                            WHERE sa.line_id=%s AND sa.shift_date=%s AND sa.shift_name=%s""",
                        (line_id, date, shift))
            _r = cur.fetchone()
            if _r:
                default = _r["username"]
        conn.commit()
    return {"default": default, "options": options}


class ShiftAllocBody(BaseModel):
    line_id: int
    shift_date: str
    shift_name: str
    leader_id: Optional[int] = None      # None / 0 → unassign


@router.post("/shift-alloc")
def set_shift_alloc(body: ShiftAllocBody, user=Depends(get_current_user)):
    _gate(user)
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_shift_alloc(cur)
        if not body.leader_id:
            cur.execute("""DELETE FROM mes_leader_shift_alloc
                            WHERE line_id=%s AND shift_date=%s AND shift_name=%s""",
                        (body.line_id, body.shift_date, body.shift_name))
        else:
            cur.execute("""INSERT INTO mes_leader_shift_alloc
                             (line_id, shift_date, shift_name, leader_id, assigned_by, updated_at)
                           VALUES (%s,%s,%s,%s,%s,now())
                           ON CONFLICT (line_id, shift_date, shift_name) DO UPDATE SET
                             leader_id=EXCLUDED.leader_id, assigned_by=EXCLUDED.assigned_by,
                             updated_at=now()""",
                        (body.line_id, body.shift_date, body.shift_name,
                         int(body.leader_id), user.get("username")))
        conn.commit()
    return {"ok": True, "leader_id": body.leader_id}


# ── who is on this line right now, and what does their signature look like ──
# 2026-09-17 — operator: "breakdown slip me signature auto ke liye production
# leader ke sign image ka option de do, jisse vo select hote hi auto sign ho
# jaye.  Aur leader name and operator name breakdown me leader aur manpower
# allocation ke through auto aaye."
#
# Both names already existed on the slip as free-text boxes somebody retyped
# every time — and the answer was already in the system: the line leader from
# mes_leader_shift_alloc, the operators from mes_manpower_allocations.  This
# returns both for one line/date/shift, with the leader's signature, so the
# slip can fill itself in and the person only corrects it if it is wrong.
@router.get("/slip-defaults")
def slip_defaults(line_id: int,
                  date: str = None,
                  shift: str = None,
                  user=Depends(get_current_user)):
    from datetime import date as _d
    d = date or _d.today().isoformat()
    out = {"line_id": line_id, "date": d, "shift": shift,
           "leader": None, "leader_options": [], "operators": []}
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_meta(cur)
        _ensure_shift_alloc(cur)

        # the leader assigned to this line for this shift
        if shift:
            cur.execute("""SELECT a.id, a.username, m.signature_image, m.employee_code
                             FROM mes_leader_shift_alloc sa
                             JOIN mes_admin a ON a.id = sa.leader_id
                        LEFT JOIN mes_leader_meta m ON m.admin_id = a.id
                            WHERE sa.line_id = %s AND sa.shift_date = %s
                              AND sa.shift_name = %s""", (line_id, d, shift))
            row = cur.fetchone()
            if row:
                out["leader"] = {"id": row["id"], "name": row["username"],
                                 "employee_code": row["employee_code"],
                                 "signature_image": row["signature_image"]}

        # every leader who could be picked instead, each with their signature
        cur.execute("""SELECT a.id, a.username, m.signature_image
                         FROM mes_admin a
                    LEFT JOIN mes_leader_meta m ON m.admin_id = a.id
                        WHERE a.role = 'leader'
                     ORDER BY a.username""")
        out["leader_options"] = [{"id": r["id"], "name": r["username"],
                                  "signature_image": r["signature_image"]}
                                 for r in cur.fetchall()]
        if out["leader"] is None and out["leader_options"]:
            # no allocation for this shift — leave it unset rather than guess,
            # so a wrong name is never printed on a slip as if it were assigned
            pass

        # operators allocated to this line/shift through manpower allocation
        if shift:
            cur.execute("""SELECT o.full_name, o.employee_id, p.process_name
                             FROM mes_manpower_allocations ma
                             JOIN mes_operators o ON o.id = ma.operator_id
                        LEFT JOIN mes_processes p ON p.id = ma.process_id
                            WHERE ma.line_id = %s AND ma.shift_date = %s
                              AND ma.shift_name = %s AND ma.removed_at IS NULL
                         ORDER BY p.process_name NULLS LAST, o.full_name""",
                        (line_id, d, shift))
            out["operators"] = [{"name": r["full_name"],
                                 "employee_id": r["employee_id"],
                                 "process": r["process_name"]}
                                for r in cur.fetchall()]
    return out


class SignatureBody(BaseModel):
    signature_image: Optional[str] = None      # data: URL, or null to clear


@router.post("/{leader_id}/signature")
def set_signature(leader_id: int, body: SignatureBody,
                  user=Depends(get_current_user)):
    """Store (or clear) a leader's signature image.

    Capped so a photo pasted straight off a phone cannot bloat the row — a
    signature is a small transparent PNG, not a 4 MB camera JPEG.
    """
    img = (body.signature_image or "").strip() or None
    if img:
        if not img.startswith("data:image/"):
            raise HTTPException(400, "signature must be an image data URL")
        if len(img) > 400_000:
            raise HTTPException(400, "signature image too large (max ~300 KB)")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_meta(cur)
        cur.execute("""INSERT INTO mes_leader_meta (admin_id, signature_image)
                       VALUES (%s, %s)
                       ON CONFLICT (admin_id) DO UPDATE
                         SET signature_image = EXCLUDED.signature_image,
                             updated_at = now()""", (leader_id, img))
    return {"ok": True, "leader_id": leader_id, "has_signature": bool(img)}
