import json
import os
import time
from typing import Dict, List, Optional, Tuple

SHIFTS_FILE = "shifts.json"

DEFAULT_PAYLOAD = {
    "shifts": [
        { "id": "shift_a", "name": "Shift A", "start": "06:00", "end": "14:00" },
        { "id": "shift_b", "name": "Shift B", "start": "14:00", "end": "22:00" },
        { "id": "shift_c", "name": "Shift C", "start": "22:00", "end": "06:00" }
    ]
}

def _path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, SHIFTS_FILE)

def load_shifts(base_dir: Optional[str] = None) -> Dict:
    p = _path(base_dir)
    if not os.path.exists(p):
        save_shifts(DEFAULT_PAYLOAD, base_dir)
        return dict(DEFAULT_PAYLOAD)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_shifts(data: Dict, base_dir: Optional[str] = None) -> None:
    with open(_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def list_shifts(base_dir: Optional[str] = None) -> List[Dict]:
    return load_shifts(base_dir).get("shifts", [])

def add_or_update_shift(payload: Dict, base_dir: Optional[str] = None) -> Tuple[bool, str, str]:
    data = load_shifts(base_dir)
    shifts = data.get("shifts", [])
    
    shift_id = str(payload.get("id", "")).strip()
    if not shift_id:
        shift_id = f"shift_{int(time.time() * 1000)}"
        
    name = str(payload.get("name", "")).strip()
    start = str(payload.get("start", "")).strip()
    end = str(payload.get("end", "")).strip()
    
    if not name or not start or not end:
        return False, "Name, start time, and end time are required", shift_id
        
    # Check if update
    found = False
    for s in shifts:
        if str(s.get("id")) == shift_id:
            s["name"] = name
            s["start"] = start
            s["end"] = end
            found = True
            break
            
    if not found:
        shifts.append({
            "id": shift_id,
            "name": name,
            "start": start,
            "end": end
        })
        
    data["shifts"] = shifts
    save_shifts(data, base_dir)
    return True, "Shift saved successfully", shift_id

def delete_shift(shift_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    data = load_shifts(base_dir)
    shifts = data.get("shifts", [])
    updated = [shift for shift in shifts if str(shift.get("id")) != str(shift_id)]
    if len(updated) == len(shifts):
        return False, "Shift not found"
    data["shifts"] = updated
    save_shifts(data, base_dir)
    return True, "Shift deleted successfully"
