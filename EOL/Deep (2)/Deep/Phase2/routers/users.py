"""
routers/users.py
================
User management for MES platform (admin only).

Roles:
  admin        — full power
  plant_head   — admin-equivalent (same access as admin)
  department   — generic department user; the specific department is
                 stored in `department_id` (FK → mes_departments).
                 The slide-nav and access checks key off of that row.
  production   — production team user (read + import + historical)
  operator     — line operator (dashboard only, with assigned lines)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from database import get_conn, dict_cursor
from auth import require_admin, hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


VALID_ROLES = {"admin", "plant_head", "department", "production", "operator"}


class UserCreate(BaseModel):
    username:      str
    password:      str
    role:          str                       # see VALID_ROLES
    department_id: Optional[int] = None      # required iff role == 'department'


class UserUpdate(BaseModel):
    role:          Optional[str] = None
    department_id: Optional[int] = None      # send to change; null clears it


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
                   u.department_id,
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
                INSERT INTO mes_admin (username, password_hash, role, department_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (body.username, password_hash, body.role, dept_id))
            user_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Create failed (username conflict?): {e}")
    return {"id": user_id, "username": body.username, "role": body.role,
            "department_id": dept_id}


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
        if row[0] != "operator":
            raise HTTPException(400, "User is not an operator")

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
