"""
routers/departments.py
======================
Department master list — admin manages this from Admin Panel → Departments.
A `department` user is bound to exactly one row here via mes_admin.department_id.

Slug is the URL-safe identifier (lowercase, underscores).  Auto-derived
from `name` if not provided.

Default seeded rows (see main.py migrations): Maintenance, Quality.
Admin can add more anytime (e.g. "Tool Room", "Stores").
"""

import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/departments", tags=["departments"])


class DepartmentBody(BaseModel):
    name:        str
    slug:        Optional[str] = None
    description: Optional[str] = None


def _make_slug(name: str) -> str:
    """Lowercase, replace whitespace + non-alphanumerics with underscores,
    collapse repeats, strip ends.  e.g. 'Tool Room' → 'tool_room'."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s


# ── Read endpoints (any authenticated user can read the list) ──────────
@router.get("/")
def list_departments(user=Depends(get_current_user)):
    """List all departments (any logged-in user)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, name, slug, description, created_at, updated_at
              FROM mes_departments
             ORDER BY name
        """)
        return cur.fetchall()


@router.get("/{dept_id}")
def get_department(dept_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, name, slug, description, created_at, updated_at
              FROM mes_departments WHERE id = %s
        """, (dept_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Department not found")
        return row


# ── Mutating endpoints (admin only — plant_head also passes via require_admin) ─
@router.post("/", status_code=201)
def create_department(body: DepartmentBody, admin=Depends(require_admin)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    slug = (body.slug or "").strip().lower() or _make_slug(name)
    if not slug:
        raise HTTPException(400, "slug could not be derived from name")

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO mes_departments (name, slug, description)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (name, slug, body.description))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Create failed (duplicate name/slug?): {e}")
    return {"id": new_id, "name": name, "slug": slug, "description": body.description}


@router.put("/{dept_id}")
def update_department(dept_id: int, body: DepartmentBody,
                      admin=Depends(require_admin)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    slug = (body.slug or "").strip().lower() or _make_slug(name)

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE mes_departments
                   SET name = %s, slug = %s, description = %s, updated_at = NOW()
                 WHERE id = %s
            """, (name, slug, body.description, dept_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "Department not found")
            conn.commit()
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Update failed (duplicate name/slug?): {e}")
    return {"ok": True, "id": dept_id, "name": name, "slug": slug}


@router.delete("/{dept_id}")
def delete_department(dept_id: int, admin=Depends(require_admin)):
    """Delete a department.  Users bound to it have their department_id
    set to NULL via ON DELETE SET NULL FK."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_departments WHERE id = %s", (dept_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Department not found")
        conn.commit()
    return {"ok": True}
