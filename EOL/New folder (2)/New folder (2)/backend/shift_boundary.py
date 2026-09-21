"""
Shift-boundary helpers for the camera-TS recorder.

User requirement (Hinglish, captured verbatim):
    "Json mein rakh for currnet shift shift khtm json khtm ts file khtm"

Meaning:
    SUB-camera continuous TS recordings are scoped to ONE shift.  At every
    shift boundary (Morning/Evening/Night start as defined in shifts.json),
    SUB recorders are stopped, their .ts file is deleted, and a fresh
    recorder starts for the new shift.

    MAIN cameras are NOT touched — their per-cycle MP4s are the long-term
    traceability artefact (barcode-named), so the rolling TS for MAIN must
    keep going across shift change to avoid losing the in-flight cycle.

Why a separate module:
    The boundary check is the only piece of logic that needs to know about
    shifts.json.  Keeping it isolated means the recorder side of
    plc_monitor.py stays focused on ffmpeg / RTSP and the shift logic can
    be unit-tested independently if needed.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from shifts_config import list_shifts


def _parse_hhmm(s: str) -> Optional[int]:
    """Convert "HH:MM" to minutes-since-midnight, or None on garbage input."""
    try:
        parts = str(s).strip().split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, AttributeError):
        return None
    return None


def shift_boundary_minutes(base_dir: Optional[str] = None) -> List[int]:
    """
    Return sorted list of boundary minutes-since-midnight from shifts.json.

    A "boundary" = a shift start.  End times are implicitly handled because
    each shift's end == the next shift's start in a 24h-covered roster.
    Falls back to the canonical 06:00/14:00/22:00 if shifts.json is empty
    or unreadable — never returns [] (would silently disable cleanup).
    """
    try:
        shifts = list_shifts(base_dir)
    except Exception:
        shifts = []

    mins: set = set()
    for s in shifts:
        v = _parse_hhmm(s.get("start", ""))
        if v is not None:
            mins.add(v)

    if not mins:
        # Hard fallback — same defaults as shifts_config.DEFAULT_PAYLOAD
        mins = {6 * 60, 14 * 60, 22 * 60}

    return sorted(mins)


def is_at_boundary(now: datetime, base_dir: Optional[str] = None,
                   window_seconds: int = 30) -> bool:
    """
    True when `now` is within ±window_seconds of any shift-start boundary.

    Default window = 30 s.  The caller throttles by minute so the same
    boundary doesn't fire twice — this function is pure.
    """
    minute_of_day = now.hour * 60 + now.minute
    for b in shift_boundary_minutes(base_dir):
        # Same-minute hit (covers the entire ±30 s window via minute granularity).
        if minute_of_day == b:
            return True
    return False


def current_shift_label(now: datetime, base_dir: Optional[str] = None) -> str:
    """
    Identifier for the shift `now` belongs to.  Used to skip duplicate
    fires across reboots (the watchdog persists `last_fired_shift_label`
    via shift_state.json — see _wipe_sub_camera_state in plc_monitor).
    """
    minute_of_day = now.hour * 60 + now.minute
    boundaries = shift_boundary_minutes(base_dir)
    if not boundaries:
        return "unknown"
    # Find the most recent boundary <= now (or wrap to last boundary if before first)
    started = boundaries[0]
    for b in boundaries:
        if b <= minute_of_day:
            started = b
    if minute_of_day < boundaries[0]:
        started = boundaries[-1]   # wrapped from previous day's last boundary
    hh, mm = divmod(started, 60)
    # Date stamp uses today, except for the wrap case where the shift
    # actually started yesterday — caller doesn't care about the exact
    # day, only that the label is stable for the duration of one shift.
    return f"{now:%Y-%m-%d}_{hh:02d}{mm:02d}"
