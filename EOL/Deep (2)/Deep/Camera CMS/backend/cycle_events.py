"""
cycle_events.py
===============
Append-only log of cycle events.  Replaces the old per-cycle .mp4
metadata flow.

Schema (cycles.csv columns):
  cycle_seq      monotonic int per machine (1, 2, 3, ...)
  machine_id     string from CMS zones config
  camera_id      string from CMS cameras config
  start_ts       ISO datetime at cycle start
  end_ts         ISO datetime at cycle end
  duration_s     end - start, seconds (float)
  status         OK | NG | COUNT (sub-machine pulse) | MANUAL
  shift_id       same label as the .ts file ("YYYY-MM-DD_X")
  ts_file        absolute path to the rolling .ts that contains the cycle
  ts_offset_s    seconds from the START of ts_file at which this cycle starts
  ts_end_offset  seconds from the START of ts_file at which this cycle ends
  notes          free-form (model name, operator id, etc.)

For sub-machines (Mbit pulses) start_ts == end_ts (instantaneous count).
For main-machine (L108/L109) we treat each pulse as the END of the
running cycle and set start_ts = end of previous cycle (or shift start
if first one).

Append-only and CSV-based for portability — same shape Excel can load,
no DB needed.  Index by machine_id + cycle_seq.
"""
from __future__ import annotations

import csv
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

CYCLES_CSV = "cycles.csv"

CSV_COLUMNS = [
    "cycle_seq", "machine_id", "camera_id",
    "start_ts", "end_ts", "duration_s", "status",
    "shift_id", "ts_file", "ts_offset_s", "ts_end_offset",
    "notes",
]

_LOCK = threading.Lock()


def _csv_path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, CYCLES_CSV)


def _ensure_header(path: str) -> None:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerow(CSV_COLUMNS)


def _next_seq_for_machine(rows: List[Dict], machine_id: str) -> int:
    last = 0
    for r in rows:
        if r.get("machine_id") == machine_id:
            try:
                v = int(r.get("cycle_seq") or 0)
                if v > last:
                    last = v
            except (TypeError, ValueError):
                pass
    return last + 1


def _read_all(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def _last_end_ts_for_machine(rows: List[Dict], machine_id: str) -> Optional[str]:
    for r in reversed(rows):
        if r.get("machine_id") == machine_id and r.get("end_ts"):
            return r["end_ts"]
    return None


def _ts_offset(file_start_epoch: float, event_iso: str) -> float:
    """Seconds from file start to event timestamp."""
    try:
        ev_epoch = datetime.fromisoformat(event_iso).timestamp()
    except Exception:
        return 0.0
    return max(0.0, ev_epoch - file_start_epoch)


def append_cycle(
    *,
    machine_id:        str,
    camera_id:         str,
    start_ts:          datetime,
    end_ts:            datetime,
    status:            str,
    shift_id:          str,
    ts_file:           str,
    ts_file_start_ep:  float,
    notes:             str = "",
    base_dir: Optional[str] = None,
) -> Dict[str, object]:
    """Append a fully-formed cycle event.  Returns the dict that was
    written, with cycle_seq filled in."""
    path = _csv_path(base_dir)
    with _LOCK:
        _ensure_header(path)
        rows  = _read_all(path)
        seq   = _next_seq_for_machine(rows, machine_id)
        s_iso = start_ts.isoformat(timespec="milliseconds")
        e_iso = end_ts.isoformat(timespec="milliseconds")
        dur   = max(0.0, (end_ts - start_ts).total_seconds())
        row = {
            "cycle_seq":      seq,
            "machine_id":     machine_id,
            "camera_id":      camera_id,
            "start_ts":       s_iso,
            "end_ts":         e_iso,
            "duration_s":     f"{dur:.3f}",
            "status":         status,
            "shift_id":       shift_id,
            "ts_file":        ts_file,
            "ts_offset_s":    f"{_ts_offset(ts_file_start_ep, s_iso):.3f}",
            "ts_end_offset":  f"{_ts_offset(ts_file_start_ep, e_iso):.3f}",
            "notes":          notes,
        }
        with open(path, "a", newline="", encoding="utf-8") as fp:
            csv.DictWriter(fp, fieldnames=CSV_COLUMNS).writerow(row)
        return row


def list_cycles(
    *,
    machine_id: Optional[str] = None,
    limit: Optional[int] = None,
    base_dir: Optional[str] = None,
) -> List[Dict]:
    path = _csv_path(base_dir)
    rows = _read_all(path)
    if machine_id:
        rows = [r for r in rows if r.get("machine_id") == machine_id]
    rows.sort(key=lambda r: int(r.get("cycle_seq") or 0), reverse=True)
    if limit:
        rows = rows[:limit]
    return rows


def get_cycle(cycle_seq: int, machine_id: str,
              base_dir: Optional[str] = None) -> Optional[Dict]:
    path = _csv_path(base_dir)
    for r in _read_all(path):
        if r.get("machine_id") == machine_id and \
           int(r.get("cycle_seq") or 0) == int(cycle_seq):
            return r
    return None
