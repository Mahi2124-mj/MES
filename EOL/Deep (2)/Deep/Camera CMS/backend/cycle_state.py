"""
Shared Cycle State Manager
Reads/writes cycle_state.json so both api_server.py and dashboard.py
can share recording state across separate processes.

Uses filelock (pip install filelock) instead of threading.Lock so that
multiple OS processes (Flask API + Dash dashboard) are safe from each other.
"""
import json
import os
from datetime import datetime
from typing import Dict, Optional

from filelock import FileLock, Timeout

CYCLE_STATE_FILE = "cycle_state.json"
CYCLE_CSV = "cycles.csv"
_LOCK_TIMEOUT = 5  # seconds before giving up on lock


def _state_path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, CYCLE_STATE_FILE)


def _lock_path(base_dir: Optional[str] = None) -> str:
    return _state_path(base_dir) + ".lock"


def _load_raw(base_dir: Optional[str] = None) -> Dict:
    """Read state file without acquiring lock — call only inside a lock context."""
    p = _state_path(base_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_raw(data: Dict, base_dir: Optional[str] = None) -> None:
    """Write state file without acquiring lock — call only inside a lock context."""
    with open(_state_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_state(base_dir: Optional[str] = None) -> Dict:
    """Thread- and process-safe state read."""
    lock = FileLock(_lock_path(base_dir), timeout=_LOCK_TIMEOUT)
    try:
        with lock:
            return _load_raw(base_dir)
    except Timeout:
        # Return last-known state rather than crash
        return _load_raw(base_dir)


def get_machine_state(machine_id: str, base_dir: Optional[str] = None) -> Dict:
    return load_state(base_dir).get(machine_id, {
        "recording": False,
        "cycle_number": None,
        "start_time": None,
        "machine_id": machine_id,
    })


def start_cycle(machine_id: str, cycle_number: int, base_dir: Optional[str] = None) -> Dict:
    lock = FileLock(_lock_path(base_dir), timeout=_LOCK_TIMEOUT)
    with lock:
        state = _load_raw(base_dir)
        entry = {
            "recording": True,
            "cycle_number": cycle_number,
            "start_time": datetime.now().isoformat(),
            "machine_id": machine_id,
        }
        state[machine_id] = entry
        _save_raw(state, base_dir)
        return entry


def end_cycle(machine_id: str, base_dir: Optional[str] = None) -> Dict:
    lock = FileLock(_lock_path(base_dir), timeout=_LOCK_TIMEOUT)
    with lock:
        state = _load_raw(base_dir)
        entry = state.get(machine_id, {})
        entry.update({
            "recording": False,
            "cycle_number": None,
            "start_time": None,
        })
        state[machine_id] = entry
        _save_raw(state, base_dir)
        return entry


def get_all_states(base_dir: Optional[str] = None) -> Dict:
    return load_state(base_dir)


def get_next_cycle_num(csv_path: str) -> int:
    """Read CSV and return next available cycle number."""
    if not os.path.exists(csv_path):
        return 1
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= 1:
            return 1
        nums = []
        for line in lines[1:]:
            parts = line.strip().split(",")
            if parts and parts[0].strip().isdigit():
                nums.append(int(parts[0].strip()))
        return max(nums) + 1 if nums else 1
    except Exception:
        return 1
