"""
routers/zones.py
================
CRUD for mes_zones.
Zones are groups of production lines within a plant.
Used by the dashboard for filtering.

GET  /api/zones/              → list all zones (authenticated)
GET  /api/zones/{zone_id}     → single zone with its lines
POST /api/zones/              → create zone (admin)
PUT  /api/zones/{zone_id}     → update zone (admin)
DELETE /api/zones/{zone_id}   → deactivate zone (admin)

GET  /api/zones/{zone_id}/lines          → lines in this zone
POST /api/zones/{zone_id}/lines/{line_id} → assign line to zone (admin)
DELETE /api/zones/{zone_id}/lines/{line_id} → remove line from zone (admin)

--- Zone-level shift/break/slot/model/machine management ---
GET  /api/zones/{zone_id}/shifts               → shifts for zone (from first line)
PUT  /api/zones/{zone_id}/shifts               → set shifts for all lines in zone
GET  /api/zones/{zone_id}/breaks               → breaks for zone
PUT  /api/zones/{zone_id}/breaks               → set breaks for all lines in zone
GET  /api/zones/{zone_id}/hourly-slots         → hourly slots (?shift_name=A)
PUT  /api/zones/{zone_id}/hourly-slots         → save hourly slots for zone
GET  /api/zones/{zone_id}/models               → models for zone (bitwise, with dup flag)
GET  /api/zones/{zone_id}/machines             → PLC/machine info per line
PUT  /api/zones/{zone_id}/shifts/{shift}/ot    → toggle OT for a shift
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/zones", tags=["zones"])


# ── Schemas ────────────────────────────────────────────────────

class ZoneCreate(BaseModel):
    plant_id:    int
    zone_code:   str
    zone_name:   str
    description: Optional[str] = None


class ZoneUpdate(BaseModel):
    zone_name:   Optional[str]  = None
    description: Optional[str]  = None
    is_active:   Optional[bool] = None


# ── Routes ─────────────────────────────────────────────────────

@router.get("/")
def list_zones(plant_id: Optional[int] = None, user=Depends(get_current_user)):
    """Return all active zones, optionally filtered by plant. Includes line count."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if plant_id:
            cur.execute("""
                SELECT
                    z.*,
                    p.plant_name,
                    p.plant_code,
                    COUNT(l.id) AS line_count
                FROM mes_zones z
                JOIN mes_plants p ON p.id = z.plant_id
                LEFT JOIN mes_lines l ON l.zone_id = z.id AND l.is_active = true
                WHERE z.plant_id = %s AND z.is_active = true
                GROUP BY z.id, p.plant_name, p.plant_code
                ORDER BY z.zone_code
            """, (plant_id,))
        else:
            cur.execute("""
                SELECT
                    z.*,
                    p.plant_name,
                    p.plant_code,
                    COUNT(l.id) AS line_count
                FROM mes_zones z
                JOIN mes_plants p ON p.id = z.plant_id
                LEFT JOIN mes_lines l ON l.zone_id = z.id AND l.is_active = true
                WHERE z.is_active = true
                GROUP BY z.id, p.plant_name, p.plant_code
                ORDER BY p.plant_name, z.zone_code
            """)
        return cur.fetchall()


@router.get("/{zone_id}")
def get_zone(zone_id: int, user=Depends(get_current_user)):
    """Return one zone with all its lines."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT z.*, p.plant_name, p.plant_code
            FROM mes_zones z
            JOIN mes_plants p ON p.id = z.plant_id
            WHERE z.id = %s
        """, (zone_id,))
        zone = cur.fetchone()
        if not zone:
            raise HTTPException(404, "Zone not found")

        zone = dict(zone)

        # Attach lines in this zone
        cur.execute("""
            SELECT l.id, l.line_code, l.line_name, l.collector_status, l.is_active
            FROM mes_lines l
            WHERE l.zone_id = %s
            ORDER BY l.line_code
        """, (zone_id,))
        zone["lines"] = cur.fetchall()
        return zone


@router.post("/", status_code=201)
def create_zone(body: ZoneCreate, admin=Depends(require_admin)):
    """Create a new zone. Admin only."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Verify plant exists
        cur.execute("SELECT id FROM mes_plants WHERE id = %s", (body.plant_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Plant not found")

        cur.execute("""
            INSERT INTO mes_zones (plant_id, zone_code, zone_name, description)
            VALUES (%s, %s, %s, %s) RETURNING *
        """, (body.plant_id, body.zone_code.upper(), body.zone_name, body.description))
        zone = cur.fetchone()

        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('ZONE_CREATED', 'zone', %s, %s)
        """, (zone["id"], f"code={body.zone_code} plant={body.plant_id}"))

    return zone


@router.put("/{zone_id}")
def update_zone(zone_id: int, body: ZoneUpdate, admin=Depends(require_admin)):
    """Update zone details. Admin only."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")

    sets   = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [zone_id]

    with get_conn() as conn:
        conn.cursor().execute(
            f"UPDATE mes_zones SET {sets}, updated_at = NOW() WHERE id = %s",
            values
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('ZONE_UPDATED', 'zone', %s, %s)
        """, (zone_id, str(updates)))

    return {"ok": True, "message": "Zone updated"}


@router.delete("/{zone_id}")
def deactivate_zone(zone_id: int, admin=Depends(require_admin)):
    """Soft-delete a zone. Lines in zone become unassigned (zone_id = NULL). Admin only."""
    with get_conn() as conn:
        # Unassign all lines from this zone first
        conn.cursor().execute(
            "UPDATE mes_lines SET zone_id = NULL WHERE zone_id = %s",
            (zone_id,)
        )
        conn.cursor().execute(
            "UPDATE mes_zones SET is_active = false, updated_at = NOW() WHERE id = %s",
            (zone_id,)
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('ZONE_DEACTIVATED', 'zone', %s, 'Lines unassigned')
        """, (zone_id,))

    return {"ok": True, "message": "Zone deactivated and lines unassigned"}


# ── Line assignment ────────────────────────────────────────────

@router.get("/{zone_id}/lines")
def get_zone_lines(zone_id: int, user=Depends(get_current_user)):
    """Return all lines assigned to this zone."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.*, p.plant_name
            FROM mes_lines l
            JOIN mes_plants p ON p.id = l.plant_id
            WHERE l.zone_id = %s
            ORDER BY l.line_code
        """, (zone_id,))
        return cur.fetchall()


@router.post("/{zone_id}/lines/{line_id}")
def assign_line_to_zone(zone_id: int, line_id: int, admin=Depends(require_admin)):
    """Assign a line to a zone. Removes from any previous zone. Admin only."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Verify both exist
        cur.execute("SELECT id FROM mes_zones WHERE id = %s AND is_active = true", (zone_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Zone not found")

        cur.execute("SELECT id, line_name FROM mes_lines WHERE id = %s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")

        conn.cursor().execute(
            "UPDATE mes_lines SET zone_id = %s, updated_at = NOW() WHERE id = %s",
            (zone_id, line_id)
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('LINE_ZONE_ASSIGNED', 'line', %s, %s)
        """, (line_id, f"zone_id={zone_id}"))

    return {"ok": True, "message": f"Line assigned to zone"}


@router.delete("/{zone_id}/lines/{line_id}")
def remove_line_from_zone(zone_id: int, line_id: int, admin=Depends(require_admin)):
    """Remove a line from its zone (unassign). Admin only."""
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_lines SET zone_id = NULL, updated_at = NOW() WHERE id = %s AND zone_id = %s",
            (line_id, zone_id)
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('LINE_ZONE_REMOVED', 'line', %s, %s)
        """, (line_id, f"removed from zone_id={zone_id}"))

    return {"ok": True, "message": "Line removed from zone"}


# ── Zone-level schemas ─────────────────────────────────────────

class ShiftConfigZone(BaseModel):
    shift_name:        str
    start_time:        str
    end_time:          str
    crosses_midnight:  bool  = False
    total_plan:        int   = 0
    working_minutes:   int   = 0
    startup_delay_min: int   = 5
    is_production:     bool  = True
    ot_enabled:        bool  = False
    ot_end_time:       Optional[str] = None


class BreakConfigZone(BaseModel):
    break_name:        str
    start_time:        str
    end_time:          str
    crosses_midnight:  bool = False
    applies_to_shifts: str  = "A,B"


class HourlySlotZone(BaseModel):
    shift_name:        str
    slot_label:        str
    start_time:        str
    end_time:          str
    crosses_midnight:  bool = False
    working_minutes:   int
    plan_pieces:       int
    db_column_prefix:  Optional[str] = None
    slot_order:        int  = 0


class OTUpdate(BaseModel):
    ot_enabled:  bool
    ot_end_time: Optional[str] = None


# ── Helper: first active line in zone ─────────────────────────

def _first_line(zone_id: int, conn):
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT id FROM mes_lines
        WHERE zone_id = %s AND is_active = true
        ORDER BY line_code LIMIT 1
    """, (zone_id,))
    return cur.fetchone()


def _all_lines(zone_id: int, conn):
    cur = dict_cursor(conn)
    cur.execute(
        "SELECT id FROM mes_lines WHERE zone_id = %s AND is_active = true ORDER BY line_code",
        (zone_id,)
    )
    return cur.fetchall()


# ── Zone Shifts ───────────────────────────────────────────────

@router.get("/{zone_id}/shifts")
def get_zone_shifts(zone_id: int, user=Depends(get_current_user)):
    """Return shift configs for a zone (sourced from first active line)."""
    with get_conn() as conn:
        line = _first_line(zone_id, conn)
        if not line:
            return []
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_shift_configs WHERE line_id = %s ORDER BY shift_name",
            (line["id"],)
        )
        return cur.fetchall()


@router.put("/{zone_id}/shifts")
def set_zone_shifts(zone_id: int, body: List[ShiftConfigZone], admin=Depends(require_admin)):
    """Upsert shifts for every active line in the zone."""
    with get_conn() as conn:
        lines = _all_lines(zone_id, conn)
        if not lines:
            raise HTTPException(404, "No active lines in zone")
        for line in lines:
            for s in body:
                conn.cursor().execute("""
                    INSERT INTO mes_shift_configs
                        (line_id, shift_name, start_time, end_time, crosses_midnight,
                         total_plan, working_minutes, startup_delay_min, is_production,
                         ot_enabled, ot_end_time)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (line_id, shift_name) DO UPDATE SET
                        start_time        = EXCLUDED.start_time,
                        end_time          = EXCLUDED.end_time,
                        crosses_midnight  = EXCLUDED.crosses_midnight,
                        total_plan        = EXCLUDED.total_plan,
                        working_minutes   = EXCLUDED.working_minutes,
                        startup_delay_min = EXCLUDED.startup_delay_min,
                        is_production     = EXCLUDED.is_production,
                        ot_enabled        = EXCLUDED.ot_enabled,
                        ot_end_time       = EXCLUDED.ot_end_time
                """, (
                    line["id"], s.shift_name, s.start_time, s.end_time,
                    s.crosses_midnight, s.total_plan, s.working_minutes,
                    s.startup_delay_min, s.is_production,
                    s.ot_enabled, s.ot_end_time or None
                ))
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('ZONE_SHIFTS_UPDATED', 'zone', %s, %s)
        """, (zone_id, f"{len(body)} shifts, {len(lines)} lines"))
    return {"ok": True}


@router.put("/{zone_id}/shifts/{shift_name}/ot")
def toggle_zone_shift_ot(zone_id: int, shift_name: str, body: OTUpdate, admin=Depends(require_admin)):
    """Toggle overtime on/off for a shift across all lines in zone."""
    with get_conn() as conn:
        lines = _all_lines(zone_id, conn)
        for line in lines:
            conn.cursor().execute("""
                UPDATE mes_shift_configs
                SET ot_enabled = %s, ot_end_time = %s
                WHERE line_id = %s AND shift_name = %s
            """, (body.ot_enabled, body.ot_end_time or None, line["id"], shift_name))
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('ZONE_SHIFT_OT_TOGGLE', 'zone', %s, %s)
        """, (zone_id, f"shift={shift_name} ot={body.ot_enabled} end={body.ot_end_time}"))
    return {"ok": True}


# ── Zone Breaks ───────────────────────────────────────────────

@router.get("/{zone_id}/breaks")
def get_zone_breaks(zone_id: int, user=Depends(get_current_user)):
    """Return break configs for a zone (from first active line)."""
    with get_conn() as conn:
        line = _first_line(zone_id, conn)
        if not line:
            return []
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_break_configs WHERE line_id = %s ORDER BY start_time",
            (line["id"],)
        )
        return cur.fetchall()


@router.put("/{zone_id}/breaks")
def set_zone_breaks(zone_id: int, body: List[BreakConfigZone], admin=Depends(require_admin)):
    """Replace breaks for every active line in the zone."""
    with get_conn() as conn:
        lines = _all_lines(zone_id, conn)
        for line in lines:
            conn.cursor().execute(
                "DELETE FROM mes_break_configs WHERE line_id = %s", (line["id"],)
            )
            for b in body:
                conn.cursor().execute("""
                    INSERT INTO mes_break_configs
                        (line_id, break_name, start_time, end_time,
                         crosses_midnight, applies_to_shifts)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (line["id"], b.break_name, b.start_time, b.end_time,
                      b.crosses_midnight, b.applies_to_shifts))
    return {"ok": True}


# ── Zone Hourly Slots ─────────────────────────────────────────

@router.get("/{zone_id}/hourly-slots")
def get_zone_slots(
    zone_id: int,
    shift_name: Optional[str] = Query(None),
    user=Depends(get_current_user)
):
    """Return hourly slots for zone (optionally filtered by shift)."""
    with get_conn() as conn:
        line = _first_line(zone_id, conn)
        if not line:
            return []
        cur = dict_cursor(conn)
        if shift_name:
            cur.execute("""
                SELECT * FROM mes_hourly_slots
                WHERE line_id = %s AND shift_name = %s
                ORDER BY slot_order
            """, (line["id"], shift_name))
        else:
            cur.execute("""
                SELECT * FROM mes_hourly_slots
                WHERE line_id = %s
                ORDER BY shift_name, slot_order
            """, (line["id"],))
        return cur.fetchall()


@router.put("/{zone_id}/hourly-slots")
def set_zone_slots(zone_id: int, body: List[HourlySlotZone], admin=Depends(require_admin)):
    """Upsert hourly slots for every active line in the zone."""
    with get_conn() as conn:
        lines = _all_lines(zone_id, conn)
        if not lines:
            raise HTTPException(404, "No active lines in zone")
        for line in lines:
            for s in body:
                conn.cursor().execute("""
                    INSERT INTO mes_hourly_slots
                        (line_id, shift_name, slot_label, start_time, end_time,
                         crosses_midnight, working_minutes, plan_pieces,
                         db_column_prefix, slot_order)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (line_id, shift_name, slot_label) DO UPDATE SET
                        start_time       = EXCLUDED.start_time,
                        end_time         = EXCLUDED.end_time,
                        crosses_midnight = EXCLUDED.crosses_midnight,
                        working_minutes  = EXCLUDED.working_minutes,
                        plan_pieces      = EXCLUDED.plan_pieces,
                        slot_order       = EXCLUDED.slot_order
                """, (
                    line["id"], s.shift_name, s.slot_label, s.start_time, s.end_time,
                    s.crosses_midnight, s.working_minutes, s.plan_pieces,
                    s.db_column_prefix or None, s.slot_order
                ))
    return {"ok": True}


# ── Zone Models (bitwise) ─────────────────────────────────────

@router.get("/{zone_id}/models")
def get_zone_models(zone_id: int, user=Depends(get_current_user)):
    """
    Return all model mappings for lines in this zone.
    usage_count > 1 means the same bit (model_number) is assigned to multiple lines.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT
                mm.id,
                mm.model_number,
                mm.model_name,
                l.line_code,
                l.line_name,
                l.id AS line_id,
                COUNT(*) OVER (PARTITION BY mm.model_number) AS usage_count
            FROM mes_model_mappings mm
            JOIN mes_lines l ON l.id = mm.line_id
            WHERE l.zone_id = %s AND l.is_active = true
            ORDER BY mm.model_number, l.line_code
        """, (zone_id,))
        return cur.fetchall()


# ── Zone Machines (PLC per line) ──────────────────────────────

@router.get("/{zone_id}/machines")
def get_zone_machines(zone_id: int, user=Depends(get_current_user)):
    """Return PLC/machine configuration for each line in zone."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT
                l.id,
                l.line_code,
                l.line_name,
                l.collector_status,
                pc.plc_ip,
                pc.plc_port,
                pc.protocol,
                pc.ok_bit_address,
                pc.ng_bit_address,
                pc.status_address,
                pc.model_address,
                pc.ideal_cycle_time,
                pc.max_allowed_cycle
            FROM mes_lines l
            LEFT JOIN mes_plc_configs pc
                   ON pc.line_id = l.id AND pc.parent_plc_id IS NULL
            WHERE l.zone_id = %s AND l.is_active = true
            ORDER BY l.line_code
        """, (zone_id,))
        return cur.fetchall()
