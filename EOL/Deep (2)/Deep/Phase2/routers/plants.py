"""
routers/plants.py
=================
CRUD for mes_plants.
All write operations require admin JWT.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/plants", tags=["plants"])


# ── Schemas ────────────────────────────────────────────────────
class PlantCreate(BaseModel):
    plant_code:  str
    plant_name:  str
    location:    Optional[str] = None
    timezone:    str = "Asia/Kolkata"


class PlantUpdate(BaseModel):
    plant_name:  Optional[str] = None
    location:    Optional[str] = None
    timezone:    Optional[str] = None
    is_active:   Optional[bool] = None


# ── Routes ─────────────────────────────────────────────────────

@router.get("/")
def list_plants(user=Depends(get_current_user)):
    """Return all plants with line counts. Public."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT
                p.*,
                COUNT(l.id)                                    AS total_lines,
                COUNT(l.id) FILTER (WHERE l.is_active = true)  AS active_lines,
                COUNT(l.id) FILTER (WHERE l.collector_status = 'running') AS running_lines
            FROM mes_plants p
            LEFT JOIN mes_lines l ON l.plant_id = p.id
            GROUP BY p.id
            ORDER BY p.plant_name
        """)
        return cur.fetchall()


@router.get("/{plant_id}")
def get_plant(plant_id: int, user=Depends(get_current_user)):
    """Return one plant with its lines. Public."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_plants WHERE id = %s", (plant_id,))
        plant = cur.fetchone()
        if not plant:
            raise HTTPException(404, "Plant not found")

        cur.execute("""
            SELECT id, line_code, line_name, is_active, collector_status
            FROM mes_lines WHERE plant_id = %s ORDER BY line_code
        """, (plant_id,))
        plant = dict(plant)
        plant["lines"] = cur.fetchall()
        return plant


@router.post("/", status_code=201)
def create_plant(body: PlantCreate, admin=Depends(require_admin)):
    """Create a new plant. Admin only."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            INSERT INTO mes_plants (plant_code, plant_name, location, timezone)
            VALUES (%s, %s, %s, %s) RETURNING *
        """, (body.plant_code, body.plant_name, body.location, body.timezone))
        plant = cur.fetchone()

        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('PLANT_CREATED', 'plant', %s, %s)
        """, (plant["id"], f"code={body.plant_code}"))

    return plant


@router.put("/{plant_id}")
def update_plant(plant_id: int, body: PlantUpdate, admin=Depends(require_admin)):
    """Update plant details. Admin only."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")

    sets   = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [plant_id]

    with get_conn() as conn:
        conn.cursor().execute(
            f"UPDATE mes_plants SET {sets}, updated_at = NOW() WHERE id = %s",
            values
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('PLANT_UPDATED', 'plant', %s, %s)
        """, (plant_id, str(updates)))

    return {"ok": True, "message": "Plant updated"}


@router.delete("/{plant_id}")
def delete_plant(plant_id: int, admin=Depends(require_admin)):
    """Soft-delete (deactivate) a plant. Admin only."""
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_plants SET is_active = false, updated_at = NOW() WHERE id = %s",
            (plant_id,)
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('PLANT_DEACTIVATED', 'plant', %s, '')
        """, (plant_id,))
    return {"ok": True, "message": "Plant deactivated"}