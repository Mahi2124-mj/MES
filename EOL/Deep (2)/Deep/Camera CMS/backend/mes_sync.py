"""
mes_sync.py
===========
One-way pull of Zones / Lines / Machines from the MES Postgres into
this Camera CMS's local zones.json.

Why one-way (MES -> CMS, not bidirectional):
    The MES is the operator's source of truth — every zone/line/machine
    on the shop floor is created there first.  Trying to bidirectional-
    sync two stores (CMS JSON file + Postgres) would race and conflict.
    Instead the user clicks "Sync from MES" in System Settings, and CMS
    rebuilds its zones.json from the MES tables, preserving any local
    `camera_id` assignments where the machine still exists.

Connection settings:
    Reads MES_PG_HOST/PORT/USER/PASS/DB env vars first (production),
    falls back to the well-known dev defaults shipped in the EOL repo.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple


# Default MES Postgres location.  These match the values hardcoded
# across the EOL repo (collector_engine.py, plc_diag.py, etc.).
_DEFAULT_MES_DB = {
    "host":     "192.168.10.210",
    "port":     5432,
    "user":     "postgres",
    "password": "postgres",
    "dbname":   "energydb",
}


def _mes_db_kwargs() -> Dict[str, object]:
    return {
        "host":     os.getenv("MES_PG_HOST",     _DEFAULT_MES_DB["host"]),
        "port":     int(os.getenv("MES_PG_PORT", _DEFAULT_MES_DB["port"])),
        "user":     os.getenv("MES_PG_USER",     _DEFAULT_MES_DB["user"]),
        "password": os.getenv("MES_PG_PASS",     _DEFAULT_MES_DB["password"]),
        "dbname":   os.getenv("MES_PG_DB",       _DEFAULT_MES_DB["dbname"]),
        "connect_timeout": 5,
    }


def _zones_file(base_dir: Optional[str]) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "zones.json")


def _read_local_zones(base_dir: Optional[str]) -> Dict:
    p = _zones_file(base_dir)
    if not os.path.exists(p):
        return {"zones": []}
    try:
        with open(p, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {"zones": []}


def _flatten_camera_assignments(local_zones: Dict) -> Dict[str, str]:
    """Build a {machine_id_or_name -> camera_id} map from the local
    file, so we can re-attach the user's existing camera assignments
    after replacing zones/lines/machines with MES data."""
    out: Dict[str, str] = {}
    for z in local_zones.get("zones", []) or []:
        for l in z.get("lines", []) or []:
            for m in l.get("machines", []) or []:
                cam_id = m.get("camera_id")
                if not cam_id:
                    continue
                # Index by both id AND name so we can match either way
                # (MES might have re-numbered machine ids since the user
                # last set a binding).
                if m.get("id"):
                    out[str(m["id"])] = cam_id
                if m.get("name"):
                    out[f"name::{m['name']}"] = cam_id
    return out


def _save_zones(payload: Dict, base_dir: Optional[str]) -> None:
    tmp = _zones_file(base_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, ensure_ascii=False)
    os.replace(tmp, _zones_file(base_dir))


def pull_from_mes(base_dir: Optional[str] = None) -> Tuple[bool, str, Dict]:
    """Connect to MES Postgres, read zones/lines/machines, rebuild
    local zones.json.  Returns (ok, message, summary).

    Summary shape:
        {"zones": int, "lines": int, "machines": int,
         "cameras_preserved": int, "cameras_orphaned": int}"""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        return False, "psycopg2 not installed — run `pip install psycopg2-binary` and restart the API.", {}

    try:
        conn = psycopg2.connect(**_mes_db_kwargs())
    except Exception as exc:
        return False, f"MES Postgres unreachable: {exc}", {}

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Zones
        cur.execute("SELECT id, zone_name FROM mes_zones ORDER BY id")
        zones = cur.fetchall()

        # 2. Lines
        cur.execute("""
            SELECT id, line_name, zone_id
              FROM mes_lines
             ORDER BY zone_id, line_name
        """)
        lines = cur.fetchall()

        # 3. Machines (joined to zone+line by name — matches what MES does)
        cur.execute("""
            SELECT m.id           AS machine_id,
                   m.machine_name,
                   m.machine_no,
                   m.zone_name    AS m_zone_name,
                   m.line_name    AS m_line_name,
                   m.is_active
              FROM mes_machines m
             WHERE m.is_active = TRUE
             ORDER BY m.zone_name, m.line_name, m.machine_no
        """)
        machines = cur.fetchall()
    finally:
        conn.close()

    # Preserve existing camera assignments
    local        = _read_local_zones(base_dir)
    cam_lookup   = _flatten_camera_assignments(local)
    cams_preserved = 0
    cams_orphaned  = sum(1 for _ in cam_lookup.values())

    # Build CMS-shaped nested payload
    zone_id_to_obj: Dict[int, Dict] = {}
    for z in zones:
        zone_obj = {
            "id":    f"zone_{z['id']}",
            "name":  z["zone_name"],
            "lines": [],
        }
        zone_id_to_obj[z["id"]] = zone_obj

    line_key_to_obj: Dict[Tuple[str, str], Dict] = {}
    for l in lines:
        zone_obj = zone_id_to_obj.get(l["zone_id"])
        if not zone_obj:
            continue
        line_obj = {
            "id":       f"line_{l['id']}",
            "name":     l["line_name"],
            "machines": [],
        }
        zone_obj["lines"].append(line_obj)
        # Index by (zone_name_lower, line_name_lower) so we can match
        # mes_machines rows that join by name (not zone_id).
        zone_name = zone_obj["name"]
        line_key_to_obj[(zone_name.strip().lower(), l["line_name"].strip().lower())] = line_obj

    for m in machines:
        zk = (m["m_zone_name"] or "").strip().lower()
        lk = (m["m_line_name"] or "").strip().lower()
        line_obj = line_key_to_obj.get((zk, lk))
        if not line_obj:
            # Machine references a zone/line that doesn't match our
            # zones table — skip rather than create orphans.
            continue
        machine_id_str = f"machine_{m['machine_id']}"
        # Preserve camera_id from old config (by id first, then by name)
        camera_id = cam_lookup.get(machine_id_str)
        if not camera_id:
            camera_id = cam_lookup.get(f"name::{m['machine_name']}")
        if camera_id:
            cams_preserved += 1
        line_obj["machines"].append({
            "id":         machine_id_str,
            "name":       m["machine_name"],
            "camera_id":  camera_id,
        })

    new_payload = {
        "zones": [zone_id_to_obj[z["id"]] for z in zones if z["id"] in zone_id_to_obj]
    }
    _save_zones(new_payload, base_dir)

    summary = {
        "zones":             len(new_payload["zones"]),
        "lines":             sum(len(z["lines"]) for z in new_payload["zones"]),
        "machines":          sum(len(l["machines"]) for z in new_payload["zones"] for l in z["lines"]),
        "cameras_preserved": cams_preserved,
        "cameras_orphaned":  max(0, cams_orphaned - cams_preserved),
    }
    return True, "Sync complete", summary
