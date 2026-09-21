# ───────────────────────────────────────────────────────────────────────
# timer_config.py   (/api/timer-config)   2026-09-13
# ───────────────────────────────────────────────────────────────────────
# One place ("Timer / Alerts" module in the Production admin panel) to set the
# time-based thresholds the plant runs on:
#   • inactive_user_hours        — a user idle (no panel/app use) longer than
#                                  this is flagged INACTIVE in the hierarchy view.
#   • oee_sustain_minutes /       — global default for the OEE-drop alarm
#     oee_cooldown_minutes          (per-line rows in mes_oee_alarm_config still win).
#   • shift_close_window_minutes  — how many minutes AFTER a shift's scheduled
#                                  end it can still be closed "on time".
#   • manpower_alert_minutes      — if a shift's manpower isn't allocated, raise a
#                                  hierarchy alert after this many minutes; set a
#                                  DIFFERENT delay per level (leader / shift_incharge
#                                  / section_incharge …).
# Single global row (id=1).  Read-only for everyone (consumers need it); only
# admin/plant_head may write.
# ───────────────────────────────────────────────────────────────────────
import json
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/timer-config", tags=["timer-config"])

_DEFAULT_MANPOWER = {"leader": 15, "shift_incharge": 30, "section_incharge": 45, "plant_head": 60}

_DEFAULTS = {
    "inactive_user_hours":        8,
    "oee_sustain_minutes":        10,
    "oee_cooldown_minutes":       60,
    "shift_close_window_minutes": 30,
    "shift_rotation_weeks":       1,     # 1 or 2 — how often A/B shifts rotate
    "manpower_alert_minutes":     dict(_DEFAULT_MANPOWER),
}


@once
def _ensure_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_timer_config (
                id                          INTEGER PRIMARY KEY DEFAULT 1,
                inactive_user_hours         INTEGER NOT NULL DEFAULT 8,
                oee_sustain_minutes         INTEGER NOT NULL DEFAULT 10,
                oee_cooldown_minutes        INTEGER NOT NULL DEFAULT 60,
                shift_close_window_minutes  INTEGER NOT NULL DEFAULT 30,
                shift_rotation_weeks        INTEGER NOT NULL DEFAULT 1,
                manpower_alert_minutes      JSONB   NOT NULL DEFAULT '{}'::jsonb,
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_by                  TEXT,
                CONSTRAINT mes_timer_config_singleton CHECK (id = 1)
            )""")
        cur.execute("""INSERT INTO mes_timer_config (id, manpower_alert_minutes)
                       VALUES (1, %s) ON CONFLICT (id) DO NOTHING""",
                    (json.dumps(_DEFAULT_MANPOWER),))
        cur.execute("""ALTER TABLE mes_timer_config
                       ADD COLUMN IF NOT EXISTS shift_rotation_weeks INTEGER NOT NULL DEFAULT 1""")
        conn.commit()


def get_timer_config() -> dict:
    """Read the live config (with defaults for any missing piece).  Safe to call
    from other routers/workers that need a threshold."""
    try:
        _ensure_table()
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT * FROM mes_timer_config WHERE id=1")
            r = cur.fetchone()
    except Exception:
        r = None
    out = dict(_DEFAULTS)
    if r:
        for k in ("inactive_user_hours", "oee_sustain_minutes",
                  "oee_cooldown_minutes", "shift_close_window_minutes",
                  "shift_rotation_weeks"):
            if r.get(k) is not None:
                out[k] = int(r[k])
        mm = r.get("manpower_alert_minutes")
        if isinstance(mm, str):
            try: mm = json.loads(mm)
            except Exception: mm = {}
        if isinstance(mm, dict) and mm:
            merged = dict(_DEFAULT_MANPOWER); merged.update({k: int(v) for k, v in mm.items() if str(v).strip() != ""})
            out["manpower_alert_minutes"] = merged
        out["updated_at"] = r["updated_at"].isoformat() if r.get("updated_at") else None
        out["updated_by"] = r.get("updated_by")
    return out


@router.get("")
def read_config(user=Depends(get_current_user)):
    return get_timer_config()


class TimerBody(BaseModel):
    inactive_user_hours:        Optional[int] = None
    oee_sustain_minutes:        Optional[int] = None
    oee_cooldown_minutes:       Optional[int] = None
    shift_close_window_minutes: Optional[int] = None
    shift_rotation_weeks:       Optional[int] = None
    manpower_alert_minutes:     Optional[Dict[str, int]] = None


@router.put("")
def save_config(body: TimerBody, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "plant_head"):
        raise HTTPException(403, "Admin only")
    _ensure_table()
    cur_cfg = get_timer_config()
    vals = {
        "inactive_user_hours":        body.inactive_user_hours        if body.inactive_user_hours        is not None else cur_cfg["inactive_user_hours"],
        "oee_sustain_minutes":        body.oee_sustain_minutes        if body.oee_sustain_minutes        is not None else cur_cfg["oee_sustain_minutes"],
        "oee_cooldown_minutes":       body.oee_cooldown_minutes       if body.oee_cooldown_minutes       is not None else cur_cfg["oee_cooldown_minutes"],
        "shift_close_window_minutes": body.shift_close_window_minutes if body.shift_close_window_minutes is not None else cur_cfg["shift_close_window_minutes"],
    }
    # clamp to sane ranges
    for k in vals:
        try: vals[k] = max(0, min(int(vals[k]), 100000))
        except Exception: vals[k] = _DEFAULTS[k]
    rot = body.shift_rotation_weeks if body.shift_rotation_weeks is not None else cur_cfg["shift_rotation_weeks"]
    rot = 2 if int(rot or 1) == 2 else 1     # only 1 or 2
    mm = cur_cfg["manpower_alert_minutes"]
    if body.manpower_alert_minutes is not None:
        mm = {k: max(0, min(int(v), 100000)) for k, v in body.manpower_alert_minutes.items()
              if str(v).strip() != ""}
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_timer_config
               SET inactive_user_hours=%s, oee_sustain_minutes=%s,
                   oee_cooldown_minutes=%s, shift_close_window_minutes=%s,
                   shift_rotation_weeks=%s,
                   manpower_alert_minutes=%s, updated_at=now(), updated_by=%s
             WHERE id=1""",
            (vals["inactive_user_hours"], vals["oee_sustain_minutes"],
             vals["oee_cooldown_minutes"], vals["shift_close_window_minutes"],
             rot, json.dumps(mm), user.get("username")))
        conn.commit()
    return get_timer_config()
