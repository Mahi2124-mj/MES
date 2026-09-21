"""loss_line_alarm.py — background watcher (2026-09-15).

Fires an INBOX alert (tag 'loss_line') when a line stays in a continuous
BREAKDOWN / stop during an ACTIVE shift for >= LOSS_SUSTAIN_MIN minutes.

"Breakdown / stop" uses the SAME signal the dashboard shows for BREAKDOWN on
andon-covered lines: an OPEN andon call (routers.andon.andon_open_call) — ANY
open call means a loss is running. We alert only while the line is in an active
shift (mes_lines.current_shift_row_id set) so off-shift / idle lines never spam.
Per-line cooldown so it never re-alerts within LOSS_COOLDOWN_MIN. Never raises.

Mirrors oee_alarm.py. Recipients come from routers.push.send_to_line (the line's
assigned people, else admins).
"""
import os
import time
import threading
from datetime import datetime, timezone

LOSS_SUSTAIN_MIN  = int(os.getenv("LOSS_LINE_SUSTAIN_MIN", "15"))
LOSS_COOLDOWN_MIN = int(os.getenv("LOSS_LINE_COOLDOWN_MIN", "30"))
_POLL_SEC = 60

_last_fired: dict = {}   # line_id -> epoch seconds
_THREAD = None


def _open_call_minutes(started):
    """Minutes since an andon call's started_at (handles tz-aware / naive)."""
    try:
        if hasattr(started, "tzinfo"):
            if started.tzinfo is None:
                return (datetime.now() - started).total_seconds() / 60.0
            return (datetime.now(timezone.utc) - started).total_seconds() / 60.0
        return (time.time() - float(started)) / 60.0
    except Exception:
        return 0.0


def _tick():
    from database import get_conn, dict_cursor
    try:
        from routers.andon import andon_open_call, andon_line_set, _norm as _anorm
    except Exception:
        return
    try:
        aset = andon_line_set()          # andon-covered line names (normalised)
    except Exception:
        aset = None
    if not aset:
        return                            # no andon lines → nothing to watch

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, line_name, current_shift_row_id "
                    "FROM mes_lines WHERE is_active = true AND line_name IS NOT NULL")
        lines = cur.fetchall()

    now = time.time()
    for ln in lines:
        lid   = ln["id"]
        lname = ln["line_name"]
        if not lname or _anorm(lname) not in aset:
            continue
        if not ln.get("current_shift_row_id"):     # not in an active shift → skip
            continue
        try:
            oc = andon_open_call(lname)             # open breakdown call (or None)
        except Exception:
            oc = None
        if not oc or not oc.get("started_at"):
            continue
        mins = _open_call_minutes(oc["started_at"])
        if mins < LOSS_SUSTAIN_MIN:
            continue
        if now - _last_fired.get(lid, 0) < LOSS_COOLDOWN_MIN * 60:
            continue
        _last_fired[lid] = now
        try:
            from routers.push import send_to_line
            call = oc.get("display_name") or "Breakdown"
            send_to_line(
                lid, f"Line loss — {lname}",
                f"{lname} in continuous {call} for {int(mins)} min during the shift "
                f"(line stopped).",
                url="/dashboard", tag="loss_line")
            print(f"[LOSS-LINE] fired line {lid} ({lname}) — {int(mins)}m {call}")
        except Exception as ex:
            print("[LOSS-LINE] notify failed:", str(ex)[:120])


def _loop():
    # small initial delay so the app finishes booting first
    time.sleep(20)
    while True:
        try:
            _tick()
        except Exception as ex:
            print("[LOSS-LINE] tick error:", str(ex)[:150])
        time.sleep(_POLL_SEC)


def start():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _THREAD = threading.Thread(target=_loop, daemon=True, name="loss-line-alarm")
    _THREAD.start()
    print(f"[LOSS-LINE] watcher started (sustain {LOSS_SUSTAIN_MIN}m, "
          f"cooldown {LOSS_COOLDOWN_MIN}m)")
