"""
routers/clip_priority.py — the 48-hour footage plan's decisions, set by the
admin in Admin Panel → Production → Clip Priority.  2026-09-21.

The GPU can cut at most ~110,000 clips a day while the plant makes ~217,000
cycles, so clips are cut in priority order:

  P1   every zone — Final Inspection + NG cycles + poka-yoke bypass cycles
  P2   the zones the admin ranks next — every sub-machine cycle, GPU
  P3   the remaining zones — cut on click from the 48 h footage + idle fill

This module only STORES the admin's decision (zone order, machine-type order,
P1 rules, how many zones get P2, retention) and shows the volumes behind it.
The clip archiver's priority lane (plan Phase 2) reads it from here.
Nothing else is touched.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user, require_admin
from database import get_conn, dict_cursor
from ddl_once import once

router = APIRouter(prefix="/api/clip-priority", tags=["clip-priority"])

MACHINE_TYPES = [
    "Final Inspection",
    "Welding (MAG / PJW / projection)",
    "Checking (bolt strength, inspection)",
    "Insert / press (ball guide, lock bar, hinge pin)",
    "Greasing / bending / supply",
    "Other",
]
DEFAULT_ZONE_ORDER = ["Recliner", "Sub-Assembly", "SEAT SLIDER", "Loop Pipe"]
DEFAULTS = {
    "zone_order": [],                 # filled from mes_zones, DEFAULT_ZONE_ORDER first
    "machine_order": MACHINE_TYPES,
    "p1": {"final_inspection": True, "ng_cycles": True, "py_bypass": True},
    "p2_zones": 2,                    # top-N zones in zone_order get every sub-machine clip
    "p3_mode": "on_click",            # rest: cut on click from the 48 h footage + idle fill
    "retention": {"fi_ng_days": 30, "sub_ok_days": 30},   # stored only — the tier is not enforced yet
}

_VOL = {"t": 0.0, "day": None, "zones": {}}
_VOL_LOCK = threading.Lock()


@once
def _ensure_table(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_clip_priority (
                     id          INTEGER PRIMARY KEY DEFAULT 1,
                     config      JSONB NOT NULL,
                     updated_by  TEXT,
                     updated_at  TIMESTAMPTZ NOT NULL DEFAULT now())""")


def _zones(cur) -> list:
    cur.execute("SELECT zone_name FROM mes_zones WHERE COALESCE(is_active, TRUE) "
                "AND COALESCE(zone_name, '') <> '' ORDER BY zone_name")
    return [r["zone_name"] for r in cur.fetchall()]


def _volumes(cur) -> dict:
    """Yesterday's cycles per zone, Final Inspection vs sub-machines (cached 30 min)."""
    with _VOL_LOCK:
        if time.time() - _VOL["t"] < 1800 and _VOL["zones"]:
            return {"day": _VOL["day"], "zones": _VOL["zones"]}
    d = (date.today() - timedelta(days=1)).isoformat()
    cur.execute("""SELECT l.id, COALESCE(z.zone_name, '(no zone)') AS zone, l.db_table_name
                     FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
                    WHERE COALESCE(l.is_active, TRUE)""")
    lines = cur.fetchall()
    cur.execute("SELECT line_id, count(*) AS n FROM mes_submachine_ct_log "
                "WHERE record_date = %s GROUP BY 1", (d,))
    subs = {r["line_id"]: int(r["n"]) for r in cur.fetchall()}
    zones: dict = {}
    for ln in lines:
        fi = 0
        tbl = ln["db_table_name"]
        if tbl and tbl.replace("_", "").isalnum():
            cur.execute("SELECT to_regclass(%s) AS t", (tbl + "_ct_log",))
            if (cur.fetchone() or {}).get("t"):
                cur.execute(f"SELECT count(*) AS n FROM {tbl}_ct_log WHERE record_date = %s", (d,))
                fi = int(cur.fetchone()["n"])
        z = zones.setdefault(ln["zone"], {"fi": 0, "sub": 0})
        z["fi"] += fi
        z["sub"] += subs.get(ln["id"], 0)
    with _VOL_LOCK:
        _VOL.update(t=time.time(), day=d, zones=zones)
    return {"day": d, "zones": zones}


def _merged(saved: dict | None, zones: list) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    for k, v in (saved or {}).items():
        if k in cfg:
            cfg[k] = v
    order = [z for z in (cfg.get("zone_order") or []) if z in zones]
    if not order:
        order = [z for z in DEFAULT_ZONE_ORDER if z in zones]
    order += [z for z in zones if z not in order]          # new zones go last
    cfg["zone_order"] = order
    mt = [m for m in (cfg.get("machine_order") or []) if m in MACHINE_TYPES]
    cfg["machine_order"] = mt + [m for m in MACHINE_TYPES if m not in mt]
    return cfg


@router.get("")
def get_priority(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_table(cur)
        conn.commit()
        cur.execute("SELECT config, updated_by, updated_at FROM mes_clip_priority WHERE id = 1")
        row = cur.fetchone()
        zones = _zones(cur)
        cfg = _merged(row["config"] if row else None, zones)
        try:
            vol = _volumes(cur)
        except Exception:
            conn.rollback()
            vol = {"day": None, "zones": {}}
    return {"config": cfg, "saved": bool(row),
            "updated_by": row["updated_by"] if row else None,
            "updated_at": row["updated_at"].isoformat() if row else None,
            "machine_types": MACHINE_TYPES, "volumes": vol,
            "gpu_clips_per_day": 110000}


@router.put("")
def put_priority(body: dict, user=Depends(require_admin)):
    cfg = (body or {}).get("config") or {}
    try:
        p2 = int(cfg.get("p2_zones", DEFAULTS["p2_zones"]))
        ret = cfg.get("retention") or {}
        fi_days, sub_days = int(ret.get("fi_ng_days", 30)), int(ret.get("sub_ok_days", 30))
    except (TypeError, ValueError):
        raise HTTPException(400, "numbers expected for P2 zones and retention days")
    if not 0 <= p2 <= 20 or not 1 <= fi_days <= 365 or not 1 <= sub_days <= 365:
        raise HTTPException(400, "P2 zones 0-20, retention 1-365 days")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_table(cur)
        clean = _merged({"zone_order": cfg.get("zone_order"),
                         "machine_order": cfg.get("machine_order"),
                         "p1": {k: bool((cfg.get("p1") or {}).get(k, True))
                                for k in DEFAULTS["p1"]},
                         "p2_zones": p2,
                         "p3_mode": "on_click",
                         "retention": {"fi_ng_days": fi_days, "sub_ok_days": sub_days}},
                        _zones(cur))
        cur.execute("""INSERT INTO mes_clip_priority (id, config, updated_by, updated_at)
                       VALUES (1, %s::jsonb, %s, now())
                       ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config,
                              updated_by = EXCLUDED.updated_by, updated_at = now()""",
                    (json.dumps(clean), (user or {}).get("username")))
        conn.commit()
    return {"ok": True, "config": clean}
