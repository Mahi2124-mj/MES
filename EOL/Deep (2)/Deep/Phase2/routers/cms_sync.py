"""
routers/cms_sync.py
===================
Loopback-only sync surface for the NF2/CMS camera portal.

The CMS portal (Flask, port 5555) is the unified admin UI a user works
in.  When the operator edits a machine's PLC/camera/trigger from the
Machine Detail page there, we want the change to land in MES Postgres
(`mes_plc_configs`) without a separate login or JWT dance.

Pattern matches the existing `/api/plc-edge` sink on the CMS side
(see backend/api_server.py line 554) — both are localhost-trusted
loopback channels between the two services.

Security model
--------------
NO auth header required, BUT every request is rejected unless
`request.client.host` is one of `{127.0.0.1, ::1, localhost}`.  CORS
keeps it off the LAN; the same Origin-locking middleware that protects
/api/plc-edge applies here too.

Endpoints
---------
GET    /api/cms-sync/state                 → snapshot of plants/zones/lines/machines
GET    /api/cms-sync/lines                 → flat list with zone+plant names
GET    /api/cms-sync/machines              → flat list across all lines
POST   /api/cms-sync/machine               → upsert one machine row (by id or composite key)
DELETE /api/cms-sync/machine/{plc_id}      → remove a machine row

These are READ surface for CMS frontend (so it can show MES truth) and
WRITE surface for CMS-side admin changes (CMS pushes here on save).
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from database import get_conn, dict_cursor


router = APIRouter(prefix="/api/cms-sync", tags=["cms-sync"])

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _require_loopback(request: Request) -> None:
    """Reject any non-loopback caller.  Keeps this auth-less surface
    safe — the firewall/CORS already block LAN access, but defense in
    depth: also enforce in app code so a misconfig doesn't open it."""
    client_host = (request.client.host if request.client else "") or ""
    # Strip IPv4-mapped-IPv6 prefix (::ffff:127.0.0.1 → 127.0.0.1)
    if client_host.startswith("::ffff:"):
        client_host = client_host[7:]
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(403, "cms-sync is loopback-only")


# ── Schemas ──────────────────────────────────────────────────────────

class MachineUpsert(BaseModel):
    """One PLC machine row to insert or update.

    Either provide `id` to update an existing row, or omit it (with
    line_id + machine_name) to create.  All trigger fields are optional
    so the CMS UI can leave them blank on first save — the operator
    fills them in once the bit addresses are known on the floor.
    """
    id:                  Optional[int] = None      # mes_plc_configs.id (None = create)
    line_id:             int                        # parent mes_lines.id
    machine_name:        str
    plc_ip:              str   = ""
    plc_port:            int   = 5002
    protocol:            str   = "MC4E"
    ok_bit_address:      str   = ""                 # MAIN PLC OK pulse — user fills, no default
    ng_bit_address:      str   = ""                 # MAIN PLC NG pulse
    status_address:      str   = "D6005"
    model_address:       str   = "D6048"
    sensor_ok_address:   str   = ""
    process_seq_address: str   = ""
    override_address:    str   = ""
    ideal_cycle_time:    Optional[float] = None
    max_allowed_cycle:   Optional[float] = None
    ok_ng_pulse_min_gap: Optional[float] = None
    parent_plc_id:       Optional[int]   = None     # IF SET → this is a SUB-machine of parent
    nf2_camera_id:       Optional[str]   = None     # links to CMS cameras.json id
    machine_seq:         Optional[int]   = None


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/state")
def get_state(request: Request):
    """One-shot snapshot the CMS frontend uses to seed its dropdowns.
    Returns plants + zones + lines + machines in a single round-trip
    so the Machine Detail page can render without a chain of calls."""
    _require_loopback(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)

        cur.execute("SELECT id, plant_name, plant_code FROM mes_plants ORDER BY plant_name")
        plants = cur.fetchall()

        cur.execute("""
            SELECT id, zone_name, zone_code, plant_id
              FROM mes_zones ORDER BY zone_name
        """)
        zones = cur.fetchall()

        cur.execute("""
            SELECT id, line_name, line_code, zone_id, plant_id, is_active
              FROM mes_lines ORDER BY line_name
        """)
        lines = cur.fetchall()

        cur.execute("""
            SELECT id, line_id, machine_name, plc_ip, plc_port, protocol,
                   ok_bit_address, ng_bit_address, status_address,
                   model_address, sensor_ok_address, process_seq_address,
                   override_address, ideal_cycle_time, max_allowed_cycle,
                   ok_ng_pulse_min_gap, parent_plc_id, nf2_camera_id,
                   machine_seq
              FROM mes_plc_configs
             ORDER BY line_id, machine_seq NULLS LAST, id
        """)
        machines = cur.fetchall()

    return {"plants": plants, "zones": zones, "lines": lines, "machines": machines}


@router.get("/machines")
def list_machines(request: Request):
    """Flat list of every PLC machine with zone+line names joined in.
    Used by the CMS Machine Master grid."""
    _require_loopback(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT m.id, m.line_id, m.machine_name, m.plc_ip, m.plc_port,
                   m.protocol, m.ok_bit_address, m.ng_bit_address,
                   m.status_address, m.model_address, m.sensor_ok_address,
                   m.process_seq_address, m.override_address,
                   m.ideal_cycle_time, m.max_allowed_cycle,
                   m.ok_ng_pulse_min_gap, m.parent_plc_id, m.nf2_camera_id,
                   m.machine_seq,
                   l.line_name, l.line_code,
                   z.zone_name, z.zone_code,
                   p.plant_name
              FROM mes_plc_configs m
              JOIN mes_lines       l ON l.id = m.line_id
              LEFT JOIN mes_zones  z ON z.id = l.zone_id
              LEFT JOIN mes_plants p ON p.id = l.plant_id
             ORDER BY z.zone_name, l.line_name, m.machine_seq NULLS LAST, m.id
        """)
        return cur.fetchall()


@router.post("/machine")
def upsert_machine(body: MachineUpsert, request: Request):
    """Insert or update a single mes_plc_configs row.

    Behavior:
      - body.id is None  → INSERT new row, return the assigned id
      - body.id is given → UPDATE that row (must exist), return ok
    """
    _require_loopback(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Verify the line exists (avoid orphan rows in mes_plc_configs)
        cur.execute("SELECT id FROM mes_lines WHERE id = %s", (body.line_id,))
        if not cur.fetchone():
            raise HTTPException(404, f"Line {body.line_id} not found")

        if body.id is None:
            # ── INSERT ────────────────────────────────────────────────
            cur.execute("""
                INSERT INTO mes_plc_configs
                    (line_id, machine_name, plc_ip, plc_port, protocol,
                     ok_bit_address, ng_bit_address, status_address,
                     model_address, sensor_ok_address, process_seq_address,
                     override_address, ideal_cycle_time, max_allowed_cycle,
                     ok_ng_pulse_min_gap, parent_plc_id, nf2_camera_id,
                     machine_seq)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (body.line_id, body.machine_name, body.plc_ip, body.plc_port,
                  body.protocol, body.ok_bit_address, body.ng_bit_address,
                  body.status_address, body.model_address,
                  body.sensor_ok_address, body.process_seq_address,
                  body.override_address, body.ideal_cycle_time,
                  body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
                  body.parent_plc_id, body.nf2_camera_id, body.machine_seq))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"ok": True, "id": new_id, "created": True}

        # ── UPDATE ────────────────────────────────────────────────────
        cur.execute("SELECT id FROM mes_plc_configs WHERE id = %s", (body.id,))
        if not cur.fetchone():
            raise HTTPException(404, f"Machine {body.id} not found")
        conn.cursor().execute("""
            UPDATE mes_plc_configs SET
                line_id             = %s,
                machine_name        = %s,
                plc_ip              = %s,
                plc_port            = %s,
                protocol            = %s,
                ok_bit_address      = %s,
                ng_bit_address      = %s,
                status_address      = %s,
                model_address       = %s,
                sensor_ok_address   = %s,
                process_seq_address = %s,
                override_address    = %s,
                ideal_cycle_time    = %s,
                max_allowed_cycle   = %s,
                ok_ng_pulse_min_gap = %s,
                parent_plc_id       = %s,
                nf2_camera_id       = %s,
                machine_seq         = %s,
                updated_at          = NOW()
             WHERE id = %s
        """, (body.line_id, body.machine_name, body.plc_ip, body.plc_port,
              body.protocol, body.ok_bit_address, body.ng_bit_address,
              body.status_address, body.model_address,
              body.sensor_ok_address, body.process_seq_address,
              body.override_address, body.ideal_cycle_time,
              body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
              body.parent_plc_id, body.nf2_camera_id, body.machine_seq,
              body.id))
        conn.commit()
        return {"ok": True, "id": body.id, "created": False}


@router.delete("/machine/{plc_id}")
def delete_machine(plc_id: int, request: Request):
    """Remove a mes_plc_configs row.  Also clears dashboard_plc_id on
    the parent line if it was pointing here."""
    _require_loopback(request)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT line_id FROM mes_plc_configs WHERE id = %s", (plc_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Machine {plc_id} not found")
        line_id = row["line_id"]
        conn.cursor().execute(
            "UPDATE mes_lines SET dashboard_plc_id = NULL "
            " WHERE id = %s AND dashboard_plc_id = %s",
            (line_id, plc_id)
        )
        conn.cursor().execute("DELETE FROM mes_plc_configs WHERE id = %s", (plc_id,))
        conn.commit()
    return {"ok": True}
