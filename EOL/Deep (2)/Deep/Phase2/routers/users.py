"""
routers/users.py
================
User management for MES platform (admin only).

Roles:
  admin                — full power
  plant_head           — admin-equivalent (same access as admin)
  department           — generic department user; the specific department is
                         stored in `department_id` (FK → mes_departments).
                         The slide-nav and access checks key off of that row.
  production           — production team user (read + import + historical)
  operator             — line operator (dashboard only, with assigned lines)

  2026-08-14 — the three shopfloor supervisor roles the plant actually runs on.
  Each is a starting point, not a cage: every page is individually settable to
  none/read/full per user via /permissions below, and any of them can be scoped
  to specific lines via /lines, so an incharge who owns one line sees only it.
  section_incharge     — line supervisor: own line's dashboard + shopfloor
                         tools.  Expected to be line-scoped.
  production_incharge  — owns production across lines; writes the production
                         flow (allocation, store, dispatch), reads the rest.
  quality_incharge     — owns the quality flow (deviations, weld monitor,
                         comments history); reads production.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from database import get_conn, dict_cursor
from auth import require_admin, hash_password
from ddl_once import once

router = APIRouter(prefix="/api/users", tags=["users"])


VALID_ROLES = {"admin", "plant_head", "department", "production", "operator",
               "leader", "shift_incharge",
               "section_incharge", "production_incharge", "quality_incharge"}
# 2026-09-13 — shopfloor hierarchy for the Shift Compile roll-up:
#   operator → leader → shift_incharge → section_incharge
#   • leader        : same per-line Shift Compile view as an operator.
#   • shift_incharge: COMPILED roll-up across the lines assigned to them.
#   • section_incharge: zone-wide roll-up (all lines in the zones of their
#                       assigned lines) including manpower.


class UserCreate(BaseModel):
    username:      str
    password:      str
    role:          str                       # see VALID_ROLES
    department_id: Optional[int] = None      # required iff role == 'department'


class UserUpdate(BaseModel):
    role:          Optional[str] = None
    department_id: Optional[int] = None      # send to change; null clears it


class UserEdit(BaseModel):
    username:      Optional[str] = None
    password:      Optional[str] = None      # admin reset; re-hashed + plaintext record
    role:          Optional[str] = None
    department_id: Optional[int] = None


def _validate_role(role: Optional[str]) -> None:
    if role is not None and role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {sorted(VALID_ROLES)}")


def _check_department_id(department_id: Optional[int]) -> None:
    """If a department_id is supplied, make sure it actually exists."""
    if department_id is None:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM mes_departments WHERE id = %s", (department_id,))
        if cur.fetchone() is None:
            raise HTTPException(400, f"department_id={department_id} does not exist")


@router.get("/")
def list_users(admin=Depends(require_admin)):
    """List all users (admin only).  Joins department row so the UI can
    render the department name + slug without an extra round-trip."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT u.id, u.username, u.role, u.last_login, u.created_at,
                   u.department_id, u.password_plain,
                   d.name AS department_name,
                   d.slug AS department_slug
              FROM mes_admin u
              LEFT JOIN mes_departments d ON d.id = u.department_id
             ORDER BY u.id
        """)
        return cur.fetchall()


@router.post("/", status_code=201)
def create_user(body: UserCreate, admin=Depends(require_admin)):
    _validate_role(body.role)
    # `department_id` is meaningful only when role='department'.  Strip it
    # for other roles so a stray value doesn't leak through.
    dept_id = body.department_id if body.role == "department" else None
    if body.role == "department" and dept_id is None:
        raise HTTPException(400, "Department user must have department_id set")
    _check_department_id(dept_id)

    password_hash = hash_password(body.password)
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO mes_admin (username, password_hash, password_plain, role, department_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (body.username, password_hash, body.password, body.role, dept_id))
            user_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Create failed (username conflict?): {e}")
    return {"id": user_id, "username": body.username, "role": body.role,
            "department_id": dept_id}


@router.put("/{user_id}")
def edit_user(user_id: int, body: UserEdit, admin=Depends(require_admin)):
    """Admin full-edit: username / password (reset) / role / department.
    Password is bcrypt-hashed for login AND stored as plaintext (password_plain)
    so the admin keeps a readable record (explicit ops requirement, admin-only).
    The built-in `admin` user's username/role stay locked (password resettable)."""
    _validate_role(body.role)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT username FROM mes_admin WHERE id = %s", (user_id,))
        cur_u = cur.fetchone()
    if not cur_u:
        raise HTTPException(404, "User not found")
    is_builtin = (cur_u["username"] == "admin")

    upd, params = [], []
    if body.username and body.username.strip() and not is_builtin:
        upd.append("username = %s"); params.append(body.username.strip())
    if body.password:
        upd.append("password_hash = %s");  params.append(hash_password(body.password))
        upd.append("password_plain = %s"); params.append(body.password)
    if body.role and not is_builtin:
        upd.append("role = %s"); params.append(body.role)
        if body.role == "department":
            if body.department_id is None:
                raise HTTPException(400, "Department role needs department_id")
            _check_department_id(body.department_id)
            upd.append("department_id = %s"); params.append(body.department_id)
        else:
            upd.append("department_id = NULL")
    elif body.department_id is not None and not is_builtin:
        _check_department_id(body.department_id)
        upd.append("department_id = %s"); params.append(body.department_id)

    if not upd:
        return {"ok": True, "updated": False}
    params.append(user_id)
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"UPDATE mes_admin SET {', '.join(upd)} WHERE id = %s", params)
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Update failed (username taken?): {e}")
    return {"ok": True}


@router.put("/{user_id}/role")
def update_user_role(user_id: int, body: UserUpdate,
                     admin=Depends(require_admin)):
    """Patch role and/or department_id.  Endpoint name kept as `/role`
    for backward compatibility with existing AdminPanel calls."""
    _validate_role(body.role)
    if body.department_id is not None:
        _check_department_id(body.department_id)

    upd, params = [], []
    new_role = body.role
    if new_role is not None:
        upd.append("role = %s"); params.append(new_role)

    # If role is being changed (or already known) we enforce the dept-id
    # constraint: 'department' role requires a dept; other roles must clear it.
    # The caller can pass department_id explicitly to set it.
    if body.department_id is not None or new_role is not None:
        # Determine the resulting role to decide whether dept_id is meaningful.
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM mes_admin WHERE id = %s", (user_id,))
            row = cur.fetchone()
            current_role = row[0] if row else None
        effective_role = new_role or current_role
        if effective_role == "department":
            if body.department_id is None and new_role == "department":
                raise HTTPException(400, "Switching a user to 'department' role requires department_id")
            if body.department_id is not None:
                upd.append("department_id = %s"); params.append(body.department_id)
        else:
            # Any non-department role → clear the dept link.
            upd.append("department_id = NULL")

    if not upd:
        return {"ok": True, "updated": False}
    params.append(user_id)
    with get_conn() as conn:
        conn.cursor().execute(
            f"UPDATE mes_admin SET {', '.join(upd)} WHERE id = %s",
            params,
        )
        conn.commit()
    return {"ok": True, "updated": True}


@router.delete("/{user_id}")
def delete_user(user_id: int, admin=Depends(require_admin)):
    """Delete a user (admin only)."""
    with get_conn() as conn:
        conn.cursor().execute("DELETE FROM mes_admin WHERE id = %s", (user_id,))
        conn.commit()
    return {"ok": True}


@router.get("/{user_id}/lines")
def get_operator_lines(user_id: int, admin=Depends(require_admin)):
    """Get lines assigned to an operator (admin only)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT line_id FROM mes_operator_lines WHERE admin_id = %s
        """, (user_id,))
        return [row["line_id"] for row in cur.fetchall()]


@router.put("/{user_id}/lines")
def set_operator_lines(user_id: int, line_ids: List[int], admin=Depends(require_admin)):
    """Set assigned lines for an operator (admin only)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM mes_admin WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        # 2026-07-31 — any non-admin role can be line-scoped, not just
        # 'operator'.  Real logins on the floor are 'production'/'department'
        # users tied to one line (e.g. the SeatSlider account), and they need
        # the same scoping so their dashboard opens on — and stays on — their
        # own line.  Admins stay global on purpose: an empty assignment means
        # unrestricted, so refusing to scope them keeps that invariant clear.
        if row[0] == "admin":
            raise HTTPException(400, "Admin users are global and cannot be line-scoped")

        cur.execute("DELETE FROM mes_operator_lines WHERE admin_id = %s", (user_id,))
        for line_id in line_ids:
            cur.execute("INSERT INTO mes_operator_lines (admin_id, line_id) VALUES (%s, %s)",
                        (user_id, line_id))
        conn.commit()
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════
# PER-PAGE PERMISSIONS
# ═════════════════════════════════════════════════════════════════════
# Operator's request: when admin creates/edits a user, they want to
# pick which pages the user can SEE and whether each page is read-only
# or full CRUD.
#
# Schema (auto-created on first call):
#   mes_user_page_permissions
#       user_id    FK → mes_admin
#       page_key   TEXT (matches the canAccess keys used by the frontend)
#       perm_level 'none' | 'read' | 'full'
#       updated_at
#
# perm_level semantics:
#   none  – page hidden from slide-nav, blocked by canAccess()
#   read  – page visible, but admin sub-panels render readOnly
#   full  – full CRUD (default for admin-equivalents, configurable per
#           page for everyone else)
#
# When NO row exists for a (user, page), the auth layer falls back to
# the role/department defaults baked into AuthContext.canAccess() —
# nothing is broken for users who haven't had explicit perms set.
# ═════════════════════════════════════════════════════════════════════

VALID_PERM_LEVELS = {"none", "read", "full"}


class UserPermission(BaseModel):
    page_key:   str
    perm_level: str


class UserPermissionBulk(BaseModel):
    permissions: List[UserPermission]


@once
def _ensure_perm_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_user_page_permissions (
            user_id    INTEGER NOT NULL
                       REFERENCES mes_admin(id) ON DELETE CASCADE,
            page_key   TEXT    NOT NULL,
            perm_level TEXT    NOT NULL DEFAULT 'none',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, page_key)
        )
    """)
    conn.commit()


@router.get("/{user_id}/permissions")
def get_user_permissions(user_id: int, admin=Depends(require_admin)):
    """Return the explicit per-page permission map for a user.  Pages
    not listed in the response inherit the role/department defaults."""
    with get_conn() as conn:
        _ensure_perm_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT page_key, perm_level
              FROM mes_user_page_permissions
             WHERE user_id = %s
             ORDER BY page_key
        """, (user_id,))
        return cur.fetchall()


@router.put("/{user_id}/permissions")
def set_user_permissions(user_id: int,
                          body: UserPermissionBulk,
                          admin=Depends(require_admin)):
    """Replace the entire permission set for a user.  Pages omitted from
    the payload (or sent with perm_level='none') effectively hide that
    page for the user."""
    # Validate
    for p in body.permissions:
        if p.perm_level not in VALID_PERM_LEVELS:
            raise HTTPException(400,
                f"perm_level must be one of {sorted(VALID_PERM_LEVELS)}, "
                f"got {p.perm_level!r} for {p.page_key}")

    with get_conn() as conn:
        _ensure_perm_table(conn)
        cur = conn.cursor()

        # Sanity: user must exist
        cur.execute("SELECT 1 FROM mes_admin WHERE id = %s", (user_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "User not found")

        cur.execute("DELETE FROM mes_user_page_permissions WHERE user_id = %s",
                    (user_id,))
        seen = set()
        for p in body.permissions:
            key = p.page_key.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            # 'none' rows are stored too (so the absence of a row truly
            # means "no override" → fall back to role defaults).  Admin
            # who explicitly chose 'none' wants the page HIDDEN even if
            # the role default would expose it.
            cur.execute("""
                INSERT INTO mes_user_page_permissions
                    (user_id, page_key, perm_level)
                VALUES (%s, %s, %s)
            """, (user_id, key, p.perm_level))
        conn.commit()
    return {"ok": True, "count": len(seen)}


# ═════════════════════════════════════════════════════════════════════
# LINE + MODULE SCOPE  (2026-08-14)
# ═════════════════════════════════════════════════════════════════════
# Page permissions answer "WHICH SCREENS can this user open".  They do not
# answer "which LINES and MACHINES may they see on those screens" — so an
# operator granted the Production Dashboard saw all 19 lines.  mes_operator_lines
# already carried a raw line list, but it is a bare membership table: no
# read/full distinction and no machine granularity at all.
#
# This adds ONE scope per user that every page shares, rather than a
# page×line matrix.  Deliberate: 31 pages × 19 lines × 109 machines is ~64k
# toggles nobody can administer, and in practice a supervisor who owns Line 12
# owns it on every screen — the scope is a property of the PERSON, not of the
# page they happen to be looking at.
#
#   mes_user_scope_permissions
#       user_id     FK → mes_admin
#       scope_type  'line'    → scope_id = mes_lines.id
#                   'machine' → scope_id = mes_plc_configs.id
#       perm_level  'none' | 'read' | 'full'
#
# Empty scope = UNRESTRICTED, matching the invariant mes_operator_lines
# already had (an operator with no assigned lines sees everything).  That
# keeps every existing user behaving exactly as before this shipped.
#
# mes_operator_lines is kept in sync on every write: any line at 'read' or
# 'full' gets a row.  auth.py's /me and the dashboards read that table, so
# they keep working untouched while the new table carries the extra level.
# ═════════════════════════════════════════════════════════════════════

VALID_SCOPE_TYPES = {"line", "machine"}


class ScopeEntry(BaseModel):
    scope_type: str                    # 'line' | 'machine'
    scope_id:   int
    perm_level: str                    # 'none' | 'read' | 'full'


class ScopeBulk(BaseModel):
    scopes: List[ScopeEntry]


def _ensure_scope_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_user_scope_permissions (
            user_id    INTEGER NOT NULL
                       REFERENCES mes_admin(id) ON DELETE CASCADE,
            scope_type TEXT    NOT NULL,
            scope_id   INTEGER NOT NULL,
            perm_level TEXT    NOT NULL DEFAULT 'none',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, scope_type, scope_id)
        )
    """)
    conn.commit()


@router.get("/scope-catalog")
def scope_catalog(admin=Depends(require_admin)):
    """Every line with its machines — the tree the permission UI renders.

    Served here (not derived on the client) so the dialog does not have to
    fan out to /api/lines + /api/lines/{id}/machines for all 19 lines.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, line_code, line_name, zone_id
              FROM mes_lines
             WHERE is_active
             ORDER BY line_code
        """)
        lines = cur.fetchall()
        cur.execute("""
            SELECT id, line_id, machine_name, machine_seq
              FROM mes_plc_configs
             ORDER BY line_id, COALESCE(machine_seq, 0), id
        """)
        machines = cur.fetchall()
    by_line: dict = {}
    for m in machines:
        by_line.setdefault(m["line_id"], []).append({
            "id":    m["id"],
            "name":  m["machine_name"] or f"Machine {m['id']}",
        })
    return [{
        "id":        l["id"],
        "line_code": l["line_code"],
        "line_name": l["line_name"],
        "zone_id":   l["zone_id"],
        "machines":  by_line.get(l["id"], []),
    } for l in lines]


@router.get("/{user_id}/scope")
def get_user_scope(user_id: int, admin=Depends(require_admin)):
    """Explicit line/machine scope for a user.  Empty list = unrestricted."""
    with get_conn() as conn:
        _ensure_scope_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT scope_type, scope_id, perm_level
              FROM mes_user_scope_permissions
             WHERE user_id = %s
             ORDER BY scope_type, scope_id
        """, (user_id,))
        return cur.fetchall()


@router.put("/{user_id}/scope")
def set_user_scope(user_id: int, body: ScopeBulk,
                   admin=Depends(require_admin)):
    """Replace a user's whole line/machine scope.

    Also rewrites mes_operator_lines from the 'line' rows so the dashboards
    and auth.py /me — which still read that table — stay consistent with
    what was just set here.  Sending an empty list clears the scope, which
    means UNRESTRICTED, not "no access".
    """
    for s in body.scopes:
        if s.scope_type not in VALID_SCOPE_TYPES:
            raise HTTPException(400,
                f"scope_type must be one of {sorted(VALID_SCOPE_TYPES)}, "
                f"got {s.scope_type!r}")
        if s.perm_level not in VALID_PERM_LEVELS:
            raise HTTPException(400,
                f"perm_level must be one of {sorted(VALID_PERM_LEVELS)}, "
                f"got {s.perm_level!r} for {s.scope_type} {s.scope_id}")

    with get_conn() as conn:
        _ensure_scope_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT role FROM mes_admin WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "User not found")
        # Same rule the line-assignment endpoint already enforces: admins are
        # global, and scoping one would contradict "empty = unrestricted".
        if row[0] == "admin":
            raise HTTPException(400, "Admin users are global and cannot be scoped")

        cur.execute("DELETE FROM mes_user_scope_permissions WHERE user_id = %s",
                    (user_id,))
        seen, granted_lines = set(), []
        for s in body.scopes:
            key = (s.scope_type, s.scope_id)
            if key in seen:
                continue
            seen.add(key)
            cur.execute("""
                INSERT INTO mes_user_scope_permissions
                    (user_id, scope_type, scope_id, perm_level)
                VALUES (%s, %s, %s, %s)
            """, (user_id, s.scope_type, s.scope_id, s.perm_level))
            if s.scope_type == "line" and s.perm_level != "none":
                granted_lines.append(s.scope_id)

        # Keep the legacy membership table in step with what was just saved —
        # BUT only when this save ACTUALLY carries line scopes.
        # 2026-09-02 BUG FIX: the Permissions modal ALWAYS calls this /scope
        # endpoint (even when the admin edited only the PAGES tab).  Users whose
        # lines were set via the "Assign Lines" modal have NO scope_type='line'
        # rows in mes_user_scope_permissions, so `granted_lines` came back EMPTY
        # and this DELETE wiped their ENTIRE line assignment — which looked like
        # "editing a user removed another user's assignment".  Guard it: if the
        # payload contains no line scopes at all, leave mes_operator_lines
        # untouched — the Assign-Lines modal (/lines) owns it for those users.
        if any(s.scope_type == "line" for s in body.scopes):
            cur.execute("DELETE FROM mes_operator_lines WHERE admin_id = %s",
                        (user_id,))
            for line_id in granted_lines:
                cur.execute(
                    "INSERT INTO mes_operator_lines (admin_id, line_id) VALUES (%s, %s)",
                    (user_id, line_id))
        conn.commit()
    return {"ok": True, "count": len(seen), "lines_granted": len(granted_lines)}


# ═════════════════════════════════════════════════════════════════════
# DEPARTMENT HIERARCHY  (2026-08-14)
# ═════════════════════════════════════════════════════════════════════
# "kal ko har department ka hierarchy dekh saku — konsi id kis ke liye hai".
# One read-only roll-up: every department, the users under it, and for each
# user the role, the lines they are scoped to and how many pages were
# explicitly set.  Users with no department are returned under a synthetic
# "Unassigned" bucket rather than dropped, because that bucket is exactly
# where a misconfigured account hides.
# ═════════════════════════════════════════════════════════════════════

@router.get("/hierarchy")
def department_hierarchy(admin=Depends(require_admin)):
    with get_conn() as conn:
        _ensure_perm_table(conn)
        _ensure_scope_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT u.id, u.username, u.role, u.last_login,
                   u.department_id,
                   d.name AS department_name,
                   d.slug AS department_slug,
                   COALESCE(pp.n, 0) AS pages_set,
                   COALESCE(ln.codes, '')  AS line_codes
              FROM mes_admin u
              LEFT JOIN mes_departments d ON d.id = u.department_id
              LEFT JOIN (SELECT user_id, count(*) AS n
                           FROM mes_user_page_permissions
                          GROUP BY user_id) pp ON pp.user_id = u.id
              LEFT JOIN (SELECT ol.admin_id,
                                string_agg(l.line_code, ', '
                                           ORDER BY l.line_code) AS codes
                           FROM mes_operator_lines ol
                           JOIN mes_lines l ON l.id = ol.line_id
                          GROUP BY ol.admin_id) ln ON ln.admin_id = u.id
             ORDER BY d.name NULLS LAST, u.username
        """)
        users = cur.fetchall()
        cur.execute("SELECT id, name, slug FROM mes_departments ORDER BY name")
        departments = cur.fetchall()

    buckets = {d["id"]: {**d, "users": []} for d in departments}
    unassigned: List[dict] = []
    for u in users:
        entry = {
            "id":         u["id"],
            "username":   u["username"],
            "role":       u["role"],
            "last_login": u["last_login"],
            "pages_set":  u["pages_set"],
            # Empty means unrestricted — spelled out so the UI never has to
            # guess what a blank cell means.
            "lines":      u["line_codes"] or "ALL (unrestricted)",
        }
        if u["department_id"] in buckets:
            buckets[u["department_id"]]["users"].append(entry)
        else:
            unassigned.append(entry)

    out = list(buckets.values())
    if unassigned:
        out.append({"id": None, "name": "Unassigned",
                    "slug": "unassigned", "users": unassigned})
    return out
