import json
import os
import time
from typing import Dict, List, Optional, Tuple

PLC_FILE = "plcs.json"

DEFAULT_PAYLOAD = {
    "plcs": [
        {
            "id": "plc_default",
            "ip": "",
            "port": 502,
            "bit_address": "M100",
            "enabled": False,
            "description": "Main PLC \u2013 Cycle trigger bit"
        }
    ]
}

def _path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, PLC_FILE)

def load_plcs(base_dir: Optional[str] = None) -> Dict:
    p = _path(base_dir)
    if not os.path.exists(p):
        save_plcs(DEFAULT_PAYLOAD, base_dir)
        return dict(DEFAULT_PAYLOAD)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "plcs" not in data:
            # Handle migration from old single plc.json
            migrated = {"plcs": [{"id": "plc_default", **data}]}
            save_plcs(migrated, base_dir)
            return migrated
        return data

def save_plcs(data: Dict, base_dir: Optional[str] = None) -> None:
    with open(_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def list_plcs(base_dir: Optional[str] = None) -> List[Dict]:
    return load_plcs(base_dir).get("plcs", [])

def add_plc(payload: Dict, base_dir: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    data = load_plcs(base_dir)
    plcs = data.get("plcs", [])
    
    plc_id = f"plc_{int(time.time() * 1000)}"
    new_plc = {
        "id": plc_id,
        "ip": str(payload.get("ip", "")).strip(),
        "port": int(payload.get("port", 502)),
        "bit_address": str(payload.get("bit_address", "")).strip(),
        "enabled": bool(payload.get("enabled", True)),
        "description": str(payload.get("description", "")).strip()
    }
    
    plcs.append(new_plc)
    data["plcs"] = plcs
    save_plcs(data, base_dir)
    return True, "PLC added successfully", plc_id

def update_plc(plc_id: str, updates: Dict, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_plcs(base_dir)
    plcs = data.get("plcs", [])
    
    found = False
    for p in plcs:
        if str(p.get("id")) == str(plc_id):
            if "ip" in updates: p["ip"] = str(updates["ip"]).strip()
            if "port" in updates: p["port"] = int(updates["port"])
            if "bit_address" in updates: p["bit_address"] = str(updates["bit_address"]).strip()
            if "enabled" in updates: p["enabled"] = bool(updates["enabled"])
            if "description" in updates: p["description"] = str(updates["description"]).strip()
            found = True
            break
            
    if not found:
        return False, f"PLC ID {plc_id} not found"
        
    data["plcs"] = plcs
    save_plcs(data, base_dir)
    return True, "PLC updated successfully"

def delete_plc(plc_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_plcs(base_dir)
    plcs = data.get("plcs", [])
    
    updated = [p for p in plcs if str(p.get("id")) != str(plc_id)]
    if len(updated) == len(plcs):
        return False, f"PLC ID {plc_id} not found"
        
    data["plcs"] = updated
    save_plcs(data, base_dir)
    return True, "PLC deleted successfully"

# Keep legacy methods for backwards compatibility if needed
def load_plc_config(base_dir: Optional[str] = None) -> Dict:
    # return the first PLC as the legacy config
    plcs = list_plcs(base_dir)
    return plcs[0] if plcs else {}

def update_plc_config(updates: Dict, base_dir: Optional[str] = None) -> Dict:
    plcs = list_plcs(base_dir)
    if not plcs:
        return {}
    first_id = plcs[0]["id"]
    update_plc(first_id, updates, base_dir)
    return list_plcs(base_dir)[0]
