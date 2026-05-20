import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

ZONES_FILE = "zones.json"

_DEFAULT_DATA: Dict = {
    "zones": [
        {
            "id": "zone_1",
            "name": "Default Zone",
            "lines": [
                {
                    "id": "line_1",
                    "name": "Main Line",
                    "machines": [
                        {"id": "machine_1", "name": "Test Machine", "camera_id": "cam_panasonic_default"},
                    ],
                }
            ],
        }
    ]
}

def _path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, ZONES_FILE)

def load_zones(base_dir: Optional[str] = None) -> Dict:
    p = _path(base_dir)
    if not os.path.exists(p):
        save_zones(_DEFAULT_DATA, base_dir)
        return _DEFAULT_DATA
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "lines" in data and "zones" not in data:
            # Handle old schema gracefully by resetting
            save_zones(_DEFAULT_DATA, base_dir)
            return _DEFAULT_DATA
        return data

def save_zones(data: Dict, base_dir: Optional[str] = None) -> None:
    with open(_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def list_zones(base_dir: Optional[str] = None) -> List[Dict]:
    return load_zones(base_dir).get("zones", [])

def list_lines(base_dir: Optional[str] = None) -> List[Dict]:
    """Legacy helper retained for dashboard_legacy compatibility."""
    return list_all_lines_flat(base_dir)

def get_lines_for_zone(zone_id: str, base_dir: Optional[str] = None) -> List[Dict]:
    for z in list_zones(base_dir):
        if z.get("id") == zone_id:
            return z.get("lines", [])
    return []

def get_machines_for_line(zone_id: str, line_id: str, base_dir: Optional[str] = None) -> List[Dict]:
    for l in get_lines_for_zone(zone_id, base_dir):
        if l.get("id") == line_id:
            return l.get("machines", [])
    return []

def get_zones_for_line(line_id: str, base_dir: Optional[str] = None) -> List[Dict]:
    """Legacy helper retained for dashboard_legacy compatibility."""
    matches = []
    for zone in list_zones(base_dir):
        if any(line.get("id") == line_id for line in zone.get("lines", [])):
            matches.append({"id": zone.get("id"), "name": zone.get("name")})
    return matches

def get_machines_for_zone(zone_id: str, base_dir: Optional[str] = None) -> List[Dict]:
    """Legacy helper retained for dashboard_legacy compatibility."""
    machines: List[Dict] = []
    for line in get_lines_for_zone(zone_id, base_dir):
        for machine in line.get("machines", []):
            machines.append({
                "id": machine.get("id"),
                "name": machine.get("name"),
                "camera_id": machine.get("camera_id"),
                "line_id": line.get("id"),
                "line_name": line.get("name"),
                "zone_id": zone_id,
            })
    return machines

def add_zone(name: str, base_dir: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    name = name.strip()
    if not name:
        return False, "Name required", None
    data = load_zones(base_dir)
    zid = f"zone_{re.sub(r'[^a-z0-9]', '_', name.lower())}_{int(time.time())}"
    data.setdefault("zones", []).append({"id": zid, "name": name, "lines": []})
    save_zones(data, base_dir)
    return True, f"Zone '{name}' added", zid

def add_line(zone_id: str, name: str, base_dir: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    name = name.strip()
    if not name:
        return False, "Name required", None
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            lid = f"line_{re.sub(r'[^a-z0-9]', '_', name.lower())}_{int(time.time())}"
            zone.setdefault("lines", []).append({"id": lid, "name": name, "machines": []})
            save_zones(data, base_dir)
            return True, f"Line '{name}' added", lid
    return False, "Zone not found", None

def add_machine(zone_id: str, line_id: str, name: str, camera_id: Optional[str] = None, base_dir: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    name = name.strip()
    if not name:
        return False, "Name required", None
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            for line in zone.get("lines", []):
                if line.get("id") == line_id:
                    mid = f"machine_{re.sub(r'[^a-z0-9]', '_', name.lower())}_{int(time.time())}"
                    line.setdefault("machines", []).append({"id": mid, "name": name, "camera_id": camera_id})
                    save_zones(data, base_dir)
                    return True, f"Machine '{name}' added", mid
    return False, "Zone or Line not found", None

def delete_zone(zone_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_zones(base_dir)
    zones = data.get("zones", [])
    updated = [z for z in zones if z.get("id") != zone_id]
    if len(updated) == len(zones):
        return False, "Zone not found"
    data["zones"] = updated
    save_zones(data, base_dir)
    return True, "Zone deleted"

def delete_line(zone_id: str, line_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            lines = zone.get("lines", [])
            updated = [l for l in lines if l.get("id") != line_id]
            if len(updated) == len(lines):
                return False, "Line not found"
            zone["lines"] = updated
            save_zones(data, base_dir)
            return True, "Line deleted"
    return False, "Zone not found"

def delete_machine(zone_id: str, line_id: str, machine_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            for line in zone.get("lines", []):
                if line.get("id") == line_id:
                    machines = line.get("machines", [])
                    updated = [m for m in machines if m.get("id") != machine_id]
                    if len(updated) == len(machines):
                        return False, "Machine not found"
                    line["machines"] = updated
                    save_zones(data, base_dir)
                    return True, "Machine deleted"
    return False, "Zone or Line not found"

def rename_zone(zone_id: str, name: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "Name required"
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            zone["name"] = name
            save_zones(data, base_dir)
            return True, "Zone renamed"
    return False, "Zone not found"

def rename_line(zone_id: str, line_id: str, name: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "Name required"
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            for line in zone.get("lines", []):
                if line.get("id") == line_id:
                    line["name"] = name
                    save_zones(data, base_dir)
                    return True, "Line renamed"
    return False, "Zone or Line not found"

def rename_machine(zone_id: str, line_id: str, machine_id: str, name: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "Name required"
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            for line in zone.get("lines", []):
                if line.get("id") == line_id:
                    for m in line.get("machines", []):
                        if m.get("id") == machine_id:
                            m["name"] = name
                            save_zones(data, base_dir)
                            return True, "Machine renamed"
    return False, "Machine not found"

def assign_camera(zone_id: str, line_id: str, machine_id: str, camera_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_zones(base_dir)
    for zone in data.get("zones", []):
        if zone.get("id") == zone_id:
            for line in zone.get("lines", []):
                if line.get("id") == line_id:
                    for m in line.get("machines", []):
                        if m.get("id") == machine_id:
                            m["camera_id"] = camera_id
                            save_zones(data, base_dir)
                            return True, "Camera assigned"
    return False, "Machine not found"

def all_machines_flat(base_dir: Optional[str] = None) -> List[Dict]:
    result = []
    for zone in list_zones(base_dir):
        for line in zone.get("lines", []):
            for m in line.get("machines", []):
                result.append({
                    "zone_id": zone.get("id"),
                    "zone_name": zone.get("name"),
                    "line_id": line.get("id"),
                    "line_name": line.get("name"),
                    "machine_id": m.get("id"),
                    "machine_name": m.get("name"),
                    "camera_id": m.get("camera_id"),
                })
    return result

def list_all_lines_flat(base_dir: Optional[str] = None) -> List[Dict]:
    result = []
    for zone in list_zones(base_dir):
        for line in zone.get("lines", []):
            result.append({
                "zone_id": zone.get("id"),
                "zone_name": zone.get("name"),
                "id": line.get("id"),
                "name": line.get("name"),
                "machine_count": len(line.get("machines", []))
            })
    return result
