import json
import os
import time
from typing import Dict, List, Optional, Tuple

BINDINGS_FILE = "camera_config_bindings.json"

DEFAULT_PAYLOAD = {
    "bindings": []
}

def _path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, BINDINGS_FILE)

def load_bindings(base_dir: Optional[str] = None) -> Dict:
    p = _path(base_dir)
    if not os.path.exists(p):
        save_bindings(DEFAULT_PAYLOAD, base_dir)
        return dict(DEFAULT_PAYLOAD)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_bindings(data: Dict, base_dir: Optional[str] = None) -> None:
    with open(_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def list_bindings(base_dir: Optional[str] = None) -> List[Dict]:
    return load_bindings(base_dir).get("bindings", [])

VALID_TRIGGER_TYPES = ("MAIN", "SUB")
# MAIN = main PLC L108/L109 OK/NG edge → per-cycle MP4 saved by part_code
# SUB  = sub PLC M-bit rising edge → single continuous TS, on-demand
#        clip extraction when user clicks sub-CT graph point


def add_binding(payload: Dict, base_dir: Optional[str] = None) -> Tuple[bool, str, str]:
    data = load_bindings(base_dir)
    bindings = data.get("bindings", [])

    machine_id = str(payload.get("machine_id", "")).strip()
    if not machine_id:
        return False, "Machine ID is required", ""

    # Trigger type — defaults to MAIN for back-compat with rows created
    # before this field was added.  Sub bindings save a single continuous
    # TS file and never write per-cycle MP4s.
    trigger_type = str(payload.get("trigger_type", "MAIN")).upper().strip()
    if trigger_type not in VALID_TRIGGER_TYPES:
        return False, f"trigger_type must be one of {VALID_TRIGGER_TYPES}", ""

    # Remove existing binding for this machine
    bindings = [b for b in bindings if str(b.get("machine_id")) != machine_id]

    binding_id = f"bind_{int(time.time() * 1000)}"
    new_binding = {
        "id":            binding_id,
        "machine_id":    machine_id,
        "camera_id":     str(payload.get("camera_id", "")).strip(),
        "plc_id":        str(payload.get("plc_id", "")).strip(),
        "trigger_type":  trigger_type,
        # SUB needs the M-bit address (e.g. "M100") since the sub PLC
        # ladder fires the cycle pulse on this bit, not L108/L109.
        # Optional for MAIN — main PLC's L108/L109 are picked up via
        # the main PLC config row, not per binding.
        "m_bit_address": str(payload.get("m_bit_address", "")).strip().upper(),
        "target_time":   int(payload.get("target_time") or 30),
        # When True, /api/plc-edge will write a per-cycle MP4 named by
        # part_code.  Auto-resolved from trigger_type at save time so
        # plc_monitor doesn't have to re-check.
        "extract_per_cycle": (trigger_type == "MAIN"),
    }

    bindings.append(new_binding)
    data["bindings"] = bindings
    save_bindings(data, base_dir)
    return True, "Binding created successfully", binding_id

def delete_binding(binding_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_bindings(base_dir)
    bindings = data.get("bindings", [])
    
    updated = [b for b in bindings if str(b.get("id")) != str(binding_id)]
    if len(updated) == len(bindings):
        return False, f"Binding ID {binding_id} not found"
        
    data["bindings"] = updated
    save_bindings(data, base_dir)
    return True, "Binding deleted successfully"
