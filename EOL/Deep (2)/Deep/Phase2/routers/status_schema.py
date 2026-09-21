"""
routers/status_schema.py
========================
Global status color schema — uniform across ALL lines.
Admin can view, edit colors, and add new status types.

GET  /api/status-schema/          → list all (authenticated)
PUT  /api/status-schema/{code}    → update color/name (admin)
POST /api/status-schema/          → add new status type (admin)
DELETE /api/status-schema/{code}  → deactivate (admin)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/status-schema", tags=["status-schema"])


# ── Schemas ────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status_name:   Optional[str]  = None
    color_hex:     Optional[str]  = None   # "#22c55e"
    color_label:   Optional[str]  = None
    loss_type:     Optional[str]  = None
    is_production: Optional[bool] = None
    description:   Optional[str]  = None
    is_active:     Optional[bool] = None


class StatusCreate(BaseModel):
    status_code:   int
    status_name:   str
    color_hex:     str            # "#rrggbb"
    color_label:   str
    loss_type:     Optional[str]  = None
    is_production: bool           = False
    description:   Optional[str]  = None


# ── Routes ─────────────────────────────────────────────────────

@router.get("/")
def list_status_schema(user=Depends(get_current_user)):
    """
    Return all global status definitions.
    Any authenticated user can view.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT * FROM mes_global_status
            ORDER BY sort_order, status_code
        """)
        return cur.fetchall()


@router.put("/{status_code}")
def update_status(
    status_code: int,
    body: StatusUpdate,
    admin=Depends(require_admin)
):
    """
    Update a status entry (color, name, description).
    Changes apply immediately to ALL lines — full uniformity.
    """
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")

    # Validate hex color format if provided
    if "color_hex" in updates:
        hex_val = updates["color_hex"].strip()
        if not hex_val.startswith("#") or len(hex_val) not in (4, 7):
            raise HTTPException(400, "color_hex must be like #22c55e or #fff")
        updates["color_hex"] = hex_val

    sets   = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [status_code]

    with get_conn() as conn:
        conn.cursor().execute(
            f"UPDATE mes_global_status SET {sets}, updated_at = NOW() WHERE status_code = %s",
            values
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('STATUS_SCHEMA_UPDATED', 'global_status', %s, %s)
        """, (status_code, str(updates)))

    return {"ok": True, "message": f"Status {status_code} updated — all lines affected"}


@router.post("/", status_code=201)
def add_status(body: StatusCreate, admin=Depends(require_admin)):
    """Add a new status type. Will be available for all lines."""
    # Validate hex
    if not body.color_hex.startswith("#") or len(body.color_hex) not in (4, 7):
        raise HTTPException(400, "color_hex must be like #22c55e")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            INSERT INTO mes_global_status
                (status_code, status_name, color_hex, color_label,
                 loss_type, is_production, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            body.status_code, body.status_name, body.color_hex, body.color_label,
            body.loss_type, body.is_production, body.description
        ))
        new = cur.fetchone()

        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('STATUS_SCHEMA_ADDED', 'global_status', %s, %s)
        """, (body.status_code, f"name={body.status_name} color={body.color_hex}"))

    return new


@router.delete("/{status_code}")
def deactivate_status(status_code: int, admin=Depends(require_admin)):
    """Deactivate a status (soft delete — keeps history)."""
    protected = [0, 1, 2]   # IDLE, RUNNING, BREAKDOWN — cannot be removed
    if status_code in protected:
        raise HTTPException(400, f"Status {status_code} is protected and cannot be deactivated")

    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_global_status SET is_active = false WHERE status_code = %s",
            (status_code,)
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('STATUS_SCHEMA_DEACTIVATED', 'global_status', %s, '')
        """, (status_code,))

    return {"ok": True, "message": f"Status {status_code} deactivated"}