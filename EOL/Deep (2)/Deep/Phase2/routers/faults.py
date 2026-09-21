# ════════════════════════════════════════════════════════════════
# routers/faults.py   →  /api/faults/...
# ════════════════════════════════════════════════════════════════
"""Fault Config (Phase 1 — CONFIG ONLY).

Per-machine fault list, organised by zone → line → machine, mirroring how
poka-yoke / model mapping is configured. Each fault is either a BIT or a DATA
REGISTER on the machine's PLC, plus the value that means "this fault is active".

Phase 2 (separate, not here) will have the collector read these on an NG bit and
write detected faults to a Fault History page. This router only stores the config.

Table (created on first use, additive — no other schema touched):
    mes_fault_config(id, zone_id, line_id, machine_id, machine_name,
                     fault_name, source_type['bit'|'register'], address,
                     trigger_value, is_active, created_at, updated_at)
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/faults", tags=["faults"])


@once
def _ensure(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_fault_config (
            id            SERIAL PRIMARY KEY,
            zone_id       INTEGER,
            line_id       INTEGER,
            machine_id    INTEGER NOT NULL,
            machine_name  TEXT,
            fault_name    TEXT NOT NULL,
            source_type   TEXT NOT NULL DEFAULT 'bit',   -- 'bit' | 'register'
            address       TEXT NOT NULL,                 -- PLC bit / register address
            trigger_value INTEGER,                       -- bit: ON value (1); register: the code that = this fault
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            updated_at    TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fault_cfg_machine "
                "ON mes_fault_config(machine_id)")


class FaultRow(BaseModel):
    fault_name:    str
    source_type:   str = "bit"          # 'bit' | 'register'
    address:       str
    trigger_value: Optional[int] = None
    is_active:     bool = True


class FaultSave(BaseModel):
    zone_id:      Optional[int] = None
    line_id:      Optional[int] = None
    machine_name: Optional[str] = None
    faults:       List[FaultRow] = []


@router.get("/config/{machine_id}")
def get_machine_faults(machine_id: int, user=Depends(get_current_user)):
    """The saved fault list for one machine."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        conn.commit()
        cur.execute(
            "SELECT id, zone_id, line_id, machine_id, machine_name, fault_name, "
            "       source_type, address, trigger_value, is_active "
            "FROM mes_fault_config WHERE machine_id = %s "
            "ORDER BY id",
            (machine_id,),
        )
        return {"machine_id": machine_id, "faults": cur.fetchall()}


@router.post("/config/{machine_id}")
def save_machine_faults(machine_id: int, body: FaultSave, admin=Depends(require_admin)):
    """Replace the whole fault list for one machine (the config editor sends the
    full edited list on Save). Runs in one transaction."""
    # basic validation
    clean: List[FaultRow] = []
    for f in body.faults:
        name = (f.fault_name or "").strip()
        addr = (f.address or "").strip()
        st   = (f.source_type or "bit").strip().lower()
        if st not in ("bit", "register"):
            st = "bit"
        if not name or not addr:
            continue   # skip blank rows
        clean.append(FaultRow(fault_name=name, source_type=st, address=addr,
                              trigger_value=f.trigger_value, is_active=f.is_active))

    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("DELETE FROM mes_fault_config WHERE machine_id = %s", (machine_id,))
        for f in clean:
            cur.execute(
                "INSERT INTO mes_fault_config "
                "  (zone_id, line_id, machine_id, machine_name, fault_name, "
                "   source_type, address, trigger_value, is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (body.zone_id, body.line_id, machine_id, body.machine_name,
                 f.fault_name, f.source_type, f.address, f.trigger_value, f.is_active),
            )
        conn.commit()
    return {"ok": True, "machine_id": machine_id, "count": len(clean)}
