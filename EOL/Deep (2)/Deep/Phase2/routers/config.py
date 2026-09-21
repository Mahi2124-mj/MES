"""
routers/config.py
=================
Manage all configuration for a line:
  • PLC config     → /api/config/plc/{line_id}
  • Status mapping → /api/config/status/{line_id}
  • Model mapping  → /api/config/models/{line_id}
  • Shift config   → /api/config/shifts/{line_id}
  • Hourly slots   → /api/config/slots/{line_id}
  • Break config   → /api/config/breaks/{line_id}
All writes require admin JWT.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/config", tags=["config"])


# ══════════════════════════════════════════════════════════════
# Helper – operator line access check
# ══════════════════════════════════════════════════════════════
def _check_operator_access(user: dict, line_id: int, conn) -> None:
    if user["role"] == "operator":
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id = %s AND line_id = %s",
                    (user["id"], line_id))
        if not cur.fetchone():
            raise HTTPException(403, "Not authorized to access this line")


# ══════════════════════════════════════════════════════════════
# PLC CONFIG
# ══════════════════════════════════════════════════════════════

class PLCConfig(BaseModel):
    plc_ip:              str
    plc_port:            int   = 5002
    protocol:            str   = "MC4E"
    ok_bit_address:      str   = "L108"
    ng_bit_address:      str   = "L109"
    status_address:      str   = "D6005"
    model_address:       str   = "D6048"
    sensor_ok_address:   Optional[str] = None
    process_seq_address: Optional[str] = None
    override_address:    Optional[str] = None
    ideal_cycle_time:    float = 15.0
    max_allowed_cycle:   float = 16.0
    ok_ng_pulse_min_gap: float = 0.5


@router.get("/plc/{line_id}")
def get_plc(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        # Main PLC only (parent_plc_id IS NULL). Sub-machines served elsewhere.
        cur.execute(
            "SELECT * FROM mes_plc_configs "
            "WHERE line_id = %s AND parent_plc_id IS NULL",
            (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "PLC config not found")
        return row


@router.put("/plc/{line_id}")
def save_plc(line_id: int, body: PLCConfig, admin=Depends(require_admin)):
    """Insert or update PLC config for a line."""
    with get_conn() as conn:
        conn.cursor().execute("""
            INSERT INTO mes_plc_configs
                (line_id, plc_ip, plc_port, protocol,
                 ok_bit_address, ng_bit_address, status_address, model_address,
                 sensor_ok_address, process_seq_address, override_address,
                 ideal_cycle_time, max_allowed_cycle, ok_ng_pulse_min_gap)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (line_id) DO UPDATE SET
                plc_ip              = EXCLUDED.plc_ip,
                plc_port            = EXCLUDED.plc_port,
                protocol            = EXCLUDED.protocol,
                ok_bit_address      = EXCLUDED.ok_bit_address,
                ng_bit_address      = EXCLUDED.ng_bit_address,
                status_address      = EXCLUDED.status_address,
                model_address       = EXCLUDED.model_address,
                sensor_ok_address   = EXCLUDED.sensor_ok_address,
                process_seq_address = EXCLUDED.process_seq_address,
                override_address    = EXCLUDED.override_address,
                ideal_cycle_time    = EXCLUDED.ideal_cycle_time,
                max_allowed_cycle   = EXCLUDED.max_allowed_cycle,
                ok_ng_pulse_min_gap = EXCLUDED.ok_ng_pulse_min_gap,
                updated_at          = NOW()
        """, (
            line_id, body.plc_ip, body.plc_port, body.protocol,
            body.ok_bit_address, body.ng_bit_address,
            body.status_address, body.model_address,
            body.sensor_ok_address, body.process_seq_address, body.override_address,
            body.ideal_cycle_time, body.max_allowed_cycle, body.ok_ng_pulse_min_gap
        ))
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                       user_id, username)
            VALUES ('PLC_CONFIG_SAVED', 'line', %s, %s, %s, %s)
        """, (line_id,
              f"ip={body.plc_ip}:{body.plc_port} ideal_ct={body.ideal_cycle_time}",
              admin.get("id"), admin.get("username")))
    return {"ok": True, "message": "PLC config saved"}


# ══════════════════════════════════════════════════════════════
# STATUS MAPPING
# ══════════════════════════════════════════════════════════════

class StatusEntry(BaseModel):
    status_code: int
    status_name: str
    loss_type:   Optional[str] = None


@router.get("/status/{line_id}")
def get_status_map(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_status_mappings WHERE line_id = %s ORDER BY status_code",
            (line_id,)
        )
        return cur.fetchall()


@router.put("/status/{line_id}")
def save_status_map(line_id: int, entries: List[StatusEntry], admin=Depends(require_admin)):
    """Replace all status mappings for a line."""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM mes_status_mappings WHERE line_id = %s", (line_id,)
        )
        for e in entries:
            conn.cursor().execute("""
                INSERT INTO mes_status_mappings (line_id, status_code, status_name, loss_type)
                VALUES (%s, %s, %s, %s)
            """, (line_id, e.status_code, e.status_name, e.loss_type))
    return {"ok": True, "message": f"{len(entries)} status entries saved"}


# ══════════════════════════════════════════════════════════════
# MODEL MAPPING
# ══════════════════════════════════════════════════════════════

class ModelEntry(BaseModel):
    model_number: int
    model_name:   str


@router.get("/models/{line_id}")
def get_models(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_model_mappings WHERE line_id = %s ORDER BY model_number",
            (line_id,)
        )
        return cur.fetchall()


@router.put("/models/{line_id}")
def save_models(line_id: int, entries: List[ModelEntry], admin=Depends(require_admin)):
    """Replace all model mappings for a line."""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM mes_model_mappings WHERE line_id = %s", (line_id,)
        )
        for m in entries:
            conn.cursor().execute("""
                INSERT INTO mes_model_mappings (line_id, model_number, model_name)
                VALUES (%s, %s, %s)
            """, (line_id, m.model_number, m.model_name))
    return {"ok": True, "message": f"{len(entries)} models saved"}


# ── New: Assign PY Model Master entries (by ID) to a Production Line ──────
@router.get("/py-models/{line_id}")
def get_line_py_models(line_id: int, user=Depends(get_current_user)):
    """Return the Model Master entries currently assigned to this line."""
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT mm.id         AS "id",
                   mm.bit_number AS "bitNumber",
                   mm.model_name AS "modelName",
                   mm.model_type AS "type",
                   mm.series     AS "model"
            FROM mes_model_mappings lm
            JOIN mes_py_model_master mm
              ON mm.bit_number = lm.model_number
             AND mm.is_active  = true
            WHERE lm.line_id = %s
            ORDER BY mm.bit_number NULLS LAST
        """, (line_id,))
        return cur.fetchall()


@router.put("/py-models/{line_id}")
def set_line_py_models(line_id: int, ids: List[int], admin=Depends(require_admin)):
    """Replace a line's model list with the given PY Model Master IDs.

    Also mirrors the selection into mes_model_mappings (with bit_number +
    model_name copied from the master) so the collector and any legacy
    dashboard queries keep working unchanged."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_model_mappings WHERE line_id = %s", (line_id,))
        if ids:
            d_cur = dict_cursor(conn)
            d_cur.execute("""
                SELECT id, bit_number, model_name
                FROM mes_py_model_master
                WHERE id = ANY(%s)
                  AND is_active    = true
                  AND bit_number IS NOT NULL
                ORDER BY bit_number
            """, (ids,))
            rows = d_cur.fetchall()
            for m in rows:
                cur.execute("""
                    INSERT INTO mes_model_mappings (line_id, model_number, model_name)
                    VALUES (%s, %s, %s)
                """, (line_id, m["bit_number"], m["model_name"]))
            return {"ok": True, "count": len(rows)}
    return {"ok": True, "count": 0}


# ══════════════════════════════════════════════════════════════
# SHIFT CONFIG
# ══════════════════════════════════════════════════════════════

class ShiftEntry(BaseModel):
    shift_name:        str
    start_time:        str    # "08:30"
    end_time:          str    # "17:15"
    crosses_midnight:  bool   = False
    total_plan:        int    = 0
    working_minutes:   int    = 465
    startup_delay_min: int    = 5
    is_production:     bool   = True


@router.get("/shifts/{line_id}")
def get_shifts(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_shift_configs WHERE line_id = %s ORDER BY shift_name",
            (line_id,)
        )
        return cur.fetchall()


@router.put("/shifts/{line_id}")
def save_shifts(line_id: int, entries: List[ShiftEntry], admin=Depends(require_admin)):
    """Replace all shift configs for a line."""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM mes_shift_configs WHERE line_id = %s", (line_id,)
        )
        for s in entries:
            conn.cursor().execute("""
                INSERT INTO mes_shift_configs
                    (line_id, shift_name, start_time, end_time, crosses_midnight,
                     total_plan, working_minutes, startup_delay_min, is_production)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                line_id, s.shift_name, s.start_time, s.end_time,
                s.crosses_midnight, s.total_plan, s.working_minutes,
                s.startup_delay_min, s.is_production
            ))
    return {"ok": True, "message": f"{len(entries)} shifts saved"}


# ══════════════════════════════════════════════════════════════
# HOURLY SLOTS
# ══════════════════════════════════════════════════════════════

class SlotEntry(BaseModel):
    shift_name:       str
    slot_label:       str    # "08:30-09:30"
    start_time:       str
    end_time:         str
    crosses_midnight: bool  = False
    working_minutes:  int
    plan_pieces:      int
    db_column_prefix: str
    slot_order:       int   = 0


@router.get("/slots/{line_id}")
def get_slots(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT * FROM mes_hourly_slots
            WHERE line_id = %s
            ORDER BY shift_name, slot_order
        """, (line_id,))
        return cur.fetchall()


@router.put("/slots/{line_id}")
def save_slots(line_id: int, entries: List[SlotEntry], admin=Depends(require_admin)):
    """Replace all hourly slots for a line."""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM mes_hourly_slots WHERE line_id = %s", (line_id,)
        )
        for s in entries:
            conn.cursor().execute("""
                INSERT INTO mes_hourly_slots
                    (line_id, shift_name, slot_label, start_time, end_time,
                     crosses_midnight, working_minutes, plan_pieces,
                     db_column_prefix, slot_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                line_id, s.shift_name, s.slot_label, s.start_time, s.end_time,
                s.crosses_midnight, s.working_minutes, s.plan_pieces,
                s.db_column_prefix, s.slot_order
            ))
    return {"ok": True, "message": f"{len(entries)} slots saved"}


# ══════════════════════════════════════════════════════════════
# BREAK CONFIG
# ══════════════════════════════════════════════════════════════

class BreakEntry(BaseModel):
    break_name:        str
    start_time:        str
    end_time:          str
    crosses_midnight:  bool  = False
    applies_to_shifts: str   = "A,B"


@router.get("/breaks/{line_id}")
def get_breaks(line_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _check_operator_access(user, line_id, conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_break_configs WHERE line_id = %s ORDER BY start_time",
            (line_id,)
        )
        return cur.fetchall()


@router.put("/breaks/{line_id}")
def save_breaks(line_id: int, entries: List[BreakEntry], admin=Depends(require_admin)):
    """Replace all break configs for a line."""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM mes_break_configs WHERE line_id = %s", (line_id,)
        )
        for b in entries:
            conn.cursor().execute("""
                INSERT INTO mes_break_configs
                    (line_id, break_name, start_time, end_time,
                     crosses_midnight, applies_to_shifts)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                line_id, b.break_name, b.start_time, b.end_time,
                b.crosses_midnight, b.applies_to_shifts
            ))
    return {"ok": True, "message": f"{len(entries)} breaks saved"}