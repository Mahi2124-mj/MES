"""
Shared Cycle State Manager
Reads/writes cycle_state.json so both api_server.py and dashboard.py
can share recording state across separate processes.

Uses filelock (pip install filelock) instead of threading.Lock so that
multiple OS processes (Flask API + Dash dashboard) are safe from each other.
"""
import json
import os
import threading
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


def start_cycle(machine_id: str, cycle_number: int,
                 base_dir: Optional[str] = None,
                 start_time: Optional[datetime] = None) -> Dict:
    """If `start_time` is provided (e.g. PLC-accurate timestamp from the
    MES edge webhook) it is recorded as-is.  Otherwise the CMS wall-clock
    at this call is used — which is fine for manual UI triggers but
    introduces HTTP-arrival jitter when used for PLC edges, so MES-driven
    callers MUST pass the explicit PLC time."""
    lock = FileLock(_lock_path(base_dir), timeout=_LOCK_TIMEOUT)
    with lock:
        state = _load_raw(base_dir)
        st = start_time or datetime.now()
        entry = {
            "recording": True,
            "cycle_number": cycle_number,
            "start_time": st.isoformat(),
            "machine_id": machine_id,
        }
        state[machine_id] = entry
        _save_raw(state, base_dir)
        return entry


def rotate_cycle(machine_id: str, cycle_number: int,
                  base_dir: Optional[str] = None,
                  start_time: Optional[datetime] = None) -> Dict:
    """
    Advance a machine to a new cycle number immediately.

    We intentionally keep `recording=True` so recorder processes can detect
    a cycle-number change and rotate files without requiring a transient stop.
    """
    return start_cycle(machine_id, cycle_number, base_dir, start_time=start_time)


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


# 2026-10-09 — remember how far the CSV was read.  cycles.csv had grown to
# 177 MB / 1.08 M rows, and this ran on EVERY PLC edge: 1.7 s of pure-Python
# parsing that held the GIL, so the single edge worker managed ~0.4 edges/s
# against ~1/s arriving.  The queue ran 2 h behind, filled to 5000 and dropped
# edges, and every other CMS thread (clip cuts, video serving) crawled behind
# it.  The file is append-only here, so only the new tail is parsed; the
# answer is the same max(cycle_number)+1 as the full scan.
_NEXT_CYCLE_CACHE: Dict[str, Dict] = {}
_NEXT_CYCLE_LOCK = threading.Lock()


def _scan_cycle_rows(data: bytes, cur_max: int) -> int:
    for line in data.split(b"\n"):
        head = line.split(b",", 1)[0].strip()
        if head.isdigit():
            n = int(head)
            if n > cur_max:
                cur_max = n
    return cur_max


def get_next_cycle_num(csv_path: str) -> int:
    """Read CSV and return next available cycle number."""
    try:
        st = os.stat(csv_path)
    except OSError:
        return 1
    try:
        with _NEXT_CYCLE_LOCK, open(csv_path, "rb") as f:
            c = _NEXT_CYCLE_CACHE.get(csv_path)
            if c and c["ino"] == st.st_ino and c["off"] <= st.st_size:
                # Still a row boundary?  A rewrite (update_cycle_tag) can shift
                # the rows; then start over rather than parse half a row.
                if c["off"] > 0:
                    f.seek(c["off"] - 1)
                    if f.read(1) != b"\n":
                        c = None
            else:
                c = None
            if c is None:
                c = {"ino": st.st_ino, "off": 0, "max": 0}
            f.seek(c["off"])
            data = f.read(st.st_size - c["off"])
            end = data.rfind(b"\n")
            if end >= 0:
                chunk = data[:end + 1]
                if c["off"] == 0:                       # skip the header row
                    nl = chunk.find(b"\n")
                    chunk = chunk[nl + 1:]
                c["max"] = _scan_cycle_rows(chunk, c["max"])
                c["off"] += end + 1
            _NEXT_CYCLE_CACHE[csv_path] = c
            return c["max"] + 1 if c["max"] > 0 else 1
    except Exception:
        return 1
