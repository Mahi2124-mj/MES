"""
routers/lines.py
================
CRUD for mes_lines.
POST /api/lines/{id}/provision  → creates DB table + starts collector
POST /api/lines/{id}/stop       → stops collector process

Role-based access:
- admin: full CRUD
- department: read-only
- operator: read-only, only sees assigned lines
"""

import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, List

import requests
from psycopg2.extras import Json

from database import get_conn, dict_cursor

# Base URL of the "New folder 2" (NF2) camera/video backend.
# Override by setting env var CYCLE_VIDEO_BASE_URL, e.g.
#   CYCLE_VIDEO_BASE_URL=http://192.168.10.50:5555
# NF2's Flask runs on 5555 by default (see api_server.py app.run port=5555),
# so the default here MUST be 5555 — earlier this said 5000 which proxied
# to nothing and surfaced as "Upstream unreachable" / "exit status 400".
CYCLE_VIDEO_BASE_URL = os.environ.get(
    "CYCLE_VIDEO_BASE_URL", "http://127.0.0.1:5555"
).rstrip("/")
from auth import get_current_user, get_current_user_optional, require_admin
from provisioner import provision_line, stop_collector

router = APIRouter(prefix="/api/lines", tags=["lines"])


# ── Helper to check if a process is alive ────────────────────
def is_process_alive(pid: Optional[int]) -> bool:
    """Return True if the process with given PID exists."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)          # Signal 0 checks existence
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ── Schemas ────────────────────────────────────────────────────
class LineCreate(BaseModel):
    plant_id:      int
    line_code:     str
    line_name:     str
    description:   Optional[str] = None
    db_table_name: str            # e.g. "abc_dashboard"
    active_shifts: Optional[str] = "A,B"   # comma-separated shift names


class LineUpdate(BaseModel):
    line_name:     Optional[str]  = None
    description:   Optional[str]  = None
    is_active:     Optional[bool] = None
    db_table_name: Optional[str]  = None
    zone_id:       Optional[int]  = None    # ← assign to a zone
    active_shifts: Optional[str]  = None    # e.g. "A", "B", "A,B"


class MachineCreate(BaseModel):
    machine_name:        str
    plc_ip:              str
    plc_port:            int   = 5002
    protocol:            str   = "MC4E"
    ok_bit_address:      str   = "L108"
    ng_bit_address:      str   = "L109"
    status_address:      str   = "D6005"
    model_address:       str   = "D6048"
    sensor_ok_address:   Optional[str]   = None
    process_seq_address: Optional[str]   = None
    override_address:    Optional[str]   = None
    ideal_cycle_time:    float = 15.0
    max_allowed_cycle:   float = 16.0
    ok_ng_pulse_min_gap: float = 0.5
    # ── Sub-machine support (optional) ───────────────────────────
    # Set parent_plc_id to make this row a sub-machine of an existing
    # main PLC on the same line. nf2_camera_id pins which CMS camera
    # owns this sub's video — admin sets it once per sub-machine and
    # NF2 auto-detects via /api/sub-cameras (no JSON editing).
    parent_plc_id:       Optional[int] = None
    nf2_camera_id:       Optional[str] = None
    # ── Display-only sequence number (M-1, M-2 …) ────────────────
    # Admin-assigned label that drives the big "M-N" badge on the
    # Dashboard sub-machine tiles.  Pure UX — has no effect on
    # cycle counting, polling order, or DB joins.  NULL is fine.
    machine_seq:         Optional[int] = None
    # ── Semi-Auto data capture (optional, sub-machine only) ──────
    # 2026-05-14 — when sa_enabled = True on a sub-machine, the
    # collector polls `sa_fetch_bit` (e.g. M5700) in parallel with
    # the cycle bit.  Every rising edge fires three parallel reads:
    #   • Part code  (sa_part_code_addr, sa_part_code_len) — byte-
    #     reversed ASCII, same encoding as the main-line D5004 read.
    #   • Data block (sa_data_addr, sa_data_len)            — N raw
    #     integer values; each gets a label (sa_register_names[i])
    #     and a scale (sa_register_scales[i]) for display.
    #   • PLC time   (sa_time_addr, sa_time_len)            — optional,
    #     falls back to server clock when blank.
    # Result is one row in mes_submachine_data_log per cycle.  Video
    # clip extraction by CMS still fires on the cycle bit — unrelated
    # to this path.
    sa_enabled:          bool          = False
    sa_fetch_bit:        Optional[str] = None         # e.g. "M5700"
    sa_part_code_addr:   Optional[str] = None         # e.g. "D530"
    sa_part_code_len:    Optional[int] = None         # e.g. 13
    sa_data_addr:        Optional[str] = None         # e.g. "D5801"
    sa_data_len:         Optional[int] = None         # e.g. 20
    sa_time_addr:        Optional[str] = None         # e.g. "D1600"
    sa_time_len:         Optional[int] = None         # e.g. 6
    sa_register_names:   Optional[List[str]]   = None # ["Torque 1", ...]
    sa_register_scales:  Optional[List[float]] = None # [0.01, 1.0, ...]
    # ── Bottleneck flag ─────────────────────────────────────────
    # Admin checkbox.  When True, Dashboard tile + SubmachineFullscreen
    # header surface a "BOTTLENECK" badge so the floor team knows this
    # is the constraining station on the line.  Pure UX — no effect on
    # collector logic, counting, or extraction.
    is_bottleneck:       bool          = False


class DashboardPlcSet(BaseModel):
    plc_id: Optional[int] = None


class PlanningUpdate(BaseModel):
    ideal_ct:        float
    planned_takt:    Optional[float] = None      # seconds (customer demand target)
    energy_per_part: Optional[float] = None      # kWh per part (admin-entered)
    recalculate:     bool = True


# ── Machine Monitoring Config schemas ─────────────────────────
class DataRegisterItem(BaseModel):
    register:      str            # e.g. "D100"
    label:         str            # human label e.g. "Torque Value"
    desired_value: Optional[float] = None   # expected/threshold value

class LoadcellItem(BaseModel):
    register:   str               # e.g. "D200"
    label:      str               # e.g. "Loadcell 1"
    min_value:  Optional[float] = None
    max_value:  Optional[float] = None

class MachineMonitorConfig(BaseModel):
    plc_id:              int
    polling_bit:         str                        # e.g. "M99"
    has_data_registers:  bool          = False
    data_registers:      list[DataRegisterItem] = []
    has_loadcell:        bool          = False
    loadcell_registers:  list[LoadcellItem]     = []


# ── Helper to check operator access ────────────────────────────
def _check_operator_access(user, line_id: int, conn) -> None:
    """Raise 403 if user is operator and not assigned to line.
    `user` may be None when the endpoint accepts anonymous (Fullscreen TV)
    callers — anonymous reads are allowed."""
    if not user:
        return
    if user["role"] == "operator":
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id = %s AND line_id = %s",
                    (user["id"], line_id))
        if not cur.fetchone():
            raise HTTPException(403, "Not authorized to access this line")


# ============================================================
# STATIC ROUTES - NO PATH PARAMETERS (MUST COME FIRST)
# ============================================================

@router.get("/part-search")
def part_search(
    code:      str = Query(..., min_length=1, description="Part code (partial match)"),
    line_id:   Optional[int] = Query(None, description="Filter to specific line"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD start (default: 7 days ago)"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD end (default: today)"),
    user=Depends(get_current_user),
):
    """
    Search ct_log tables for cycles matching a part_code.
    Returns manufacturing data for each matching cycle: date, shift, zone,
    line, machine model, cycle time, ok/ng status, ideal CT, and identifiers
    needed to play the cycle video.
    """
    from datetime import timedelta as _td

    today = datetime.now().strftime("%Y-%m-%d")
    d_from = date_from or (datetime.now() - _td(days=7)).strftime("%Y-%m-%d")
    d_to   = date_to   or today

    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Get lines to search (all provisioned lines, or one specific)
        if line_id:
            cur.execute(
                "SELECT l.id, l.line_name, l.db_table_name, l.zone_id, "
                "l.ideal_cycle_time, z.zone_name "
                "FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id "
                "WHERE l.id = %s",
                (line_id,),
            )
        else:
            cur.execute(
                "SELECT l.id, l.line_name, l.db_table_name, l.zone_id, "
                "l.ideal_cycle_time, z.zone_name "
                "FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id "
                "WHERE l.db_table_name IS NOT NULL AND l.db_table_name != ''"
            )
        lines_to_search = cur.fetchall()

        results = []
        search_q = f"%{code}%"
        searched_tables = set()   # avoid double-searching same table (multiple lines can share one)

        for ln in lines_to_search:
            tbl_log = ln["db_table_name"] + "_ct_log"
            if tbl_log in searched_tables:
                continue
            searched_tables.add(tbl_log)
            # Check table exists
            cur.execute("SELECT to_regclass(%s) AS exists", (tbl_log,))
            if not cur.fetchone()["exists"]:
                continue
            # Check part_code column exists
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s AND column_name = 'part_code'
            """, (tbl_log,))
            if not cur.fetchone():
                continue

            # Check is_ng column
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s AND column_name = 'is_ng'
            """, (tbl_log,))
            has_ng = cur.fetchone() is not None

            cols = "ts, record_date, shift_name, ct_value, cycle_seq, part_code"
            if has_ng:
                cols += ", is_ng"

            cur.execute(
                f"SELECT {cols} FROM {tbl_log} "
                f"WHERE part_code ILIKE %s AND record_date BETWEEN %s AND %s "
                f"ORDER BY ts DESC LIMIT 200",
                (search_q, d_from, d_to),
            )
            rows = cur.fetchall()

            ideal_ct = float(ln.get("ideal_cycle_time") or 15)
            zone_name = ln.get("zone_name") or "—"

            for r in rows:
                results.append({
                    "part_code":    r["part_code"] or "",
                    "record_date":  str(r["record_date"]),
                    "shift_name":   r["shift_name"],
                    "ts":           r["ts"].isoformat() if r["ts"] else None,
                    # Keep raw datetime for downstream sub-process window
                    # enrichment; stripped before return.
                    "_ts_dt":       r["ts"],
                    "ct_value":     float(r["ct_value"]),
                    "cycle_seq":    r["cycle_seq"],
                    "is_ng":        bool(r.get("is_ng")) if has_ng else False,
                    "line_id":      ln["id"],
                    "line_name":    ln["line_name"],
                    "zone_name":    zone_name,
                    "ideal_ct":     ideal_ct,
                })

        # 2026-05-18 — ENRICH each main-cycle result with every other
        # process that ran for THIS part.  Sub-machines don't carry a
        # part_code (only the main station scans), so we tie them to
        # the main cycle by overlapping their [ts_start, ts_end]
        # window with [main.ts_start, main.ts_end].  Same window also
        # pulls poka-yoke failure events + Semi-Auto data captures.
        #
        # Net result: one row in Historical → expandable detail showing
        # exactly which sub-machine cycles ran for this part, what
        # PYs fired during it, and what SA captures got recorded.
        #
        # To avoid N+1 queries we batch each enrichment per line:
        # collect all main cycle windows for the line, then one SELECT
        # per sub-machine / py / SA table.
        if results:
            from datetime import timedelta as _td2
            # Group results by line for batched queries
            by_line = {}
            for r in results:
                if r["_ts_dt"]:
                    by_line.setdefault(r["line_id"], []).append(r)

            for line_id_e, line_rows in by_line.items():
                # Build the window for THIS part on this line.  We
                # approximate the per-cycle window as
                #   start = prev_cycle.ts (or this.ts - ideal_ct)
                #   end   = this.ts
                # That's the actual time interval during which the
                # part was being worked on.
                # Normalise main ts to naive before any datetime math so
                # subtractions don't trip on tzinfo mismatches.
                def _naive_dt(dt):
                    if dt is None: return None
                    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

                # 2026-05-18 — Windowing fix (v2).
                # Sub-machines are UPSTREAM stations — they run BEFORE
                # the main station scans the part.  So the right
                # window is from the PREVIOUS main cycle (regardless
                # of part_code) to THIS main cycle.  Query ct_log
                # once per main cycle to find that previous ts, then
                # use [prev_ts, this_ts] as the window.  This way
                # every sub-cycle that ran for this specific part
                # appears in its row.
                #
                # Capped to 10 min just in case the previous cycle is
                # far back (idle / breakdown gap) — we don't want to
                # pull in unrelated activity from before a long stop.
                windows = []
                for r in line_rows:
                    end_dt = _naive_dt(r["_ts_dt"])
                    if end_dt is None:
                        continue
                    # Find the previous cycle in the same ct_log table
                    tbl_log = next((l["db_table_name"] + "_ct_log"
                                    for l in lines_to_search
                                    if l["id"] == line_id_e), None)
                    prev_ts = None
                    if tbl_log:
                        try:
                            cur.execute(
                                f"SELECT ts FROM {tbl_log} "
                                f"WHERE ts < %s "
                                f"ORDER BY ts DESC LIMIT 1",
                                (r["_ts_dt"],)
                            )
                            row = cur.fetchone()
                            if row and row.get("ts"):
                                prev_ts = _naive_dt(row["ts"])
                        except Exception:
                            pass
                    # Window must be wide enough to catch the
                    # ENTIRE upstream sequence of sub-machine cycles
                    # that processed this part before it reached the
                    # main station.  Floor at 2 min so 5-7 upstream
                    # stations (each ~15-30 s) all appear; ceil at
                    # 10 min so a long idle gap doesn't pull in noise.
                    UPSTREAM_FLOOR_SEC = 120.0    # 2 min
                    span_cap = 600.0
                    if prev_ts and end_dt > prev_ts:
                        delta = (end_dt - prev_ts).total_seconds()
                    else:
                        delta = UPSTREAM_FLOOR_SEC
                    delta = min(max(delta, UPSTREAM_FLOOR_SEC), span_cap)
                    start_dt = end_dt - _td2(seconds=delta)
                    windows.append((r, start_dt, end_dt))
                if not windows:
                    continue

                overall_start = min(w[1] for w in windows)
                overall_end   = max(w[2] for w in windows)

                # (a) Sub-machine cycles for THIS line in the window
                try:
                    cur.execute("""
                        SELECT scl.sub_plc_id, scl.cycle_seq, scl.ts_start,
                               scl.ts_end, scl.ct_seconds, scl.shift_name,
                               pc.machine_name, pc.machine_seq
                        FROM mes_submachine_ct_log scl
                        LEFT JOIN mes_plc_configs pc ON pc.id = scl.sub_plc_id
                        WHERE scl.line_id = %s
                          AND scl.ts_end >= %s
                          AND scl.ts_start <= %s
                        ORDER BY scl.ts_end
                    """, (line_id_e, overall_start, overall_end))
                    sub_rows = cur.fetchall()
                except Exception:
                    sub_rows = []

                # (b) Poka-yoke events for THIS line in the window
                try:
                    cur.execute("""
                        SELECT detected_at, rule_type, alert_level,
                               plc_value, context_json
                        FROM mes_poka_yoke_events
                        WHERE line_id = %s
                          AND detected_at >= %s
                          AND detected_at <= %s
                        ORDER BY detected_at
                    """, (line_id_e, overall_start, overall_end))
                    py_rows = cur.fetchall()
                except Exception:
                    py_rows = []

                # (c) Semi-Auto data captures in the window
                try:
                    cur.execute("""
                        SELECT sd.sub_plc_id, sd.ts_plc, sd.part_code,
                               sd.values_json, sd.shift_name,
                               pc.machine_name, pc.sa_register_names,
                               pc.sa_register_scales
                        FROM mes_submachine_data_log sd
                        LEFT JOIN mes_plc_configs pc ON pc.id = sd.sub_plc_id
                        WHERE sd.line_id = %s
                          AND sd.ts_plc >= %s
                          AND sd.ts_plc <= %s
                        ORDER BY sd.ts_plc
                    """, (line_id_e, overall_start, overall_end))
                    sa_rows = cur.fetchall()
                except Exception:
                    sa_rows = []

                # Bucket each enrichment row into the cycle window it
                # falls inside (closest matching main cycle).
                #
                # Some tables store ts as TIMESTAMP (naive) and others as
                # TIMESTAMPTZ (aware) — psycopg2 surfaces these as datetime
                # objects with / without tzinfo, and Python refuses to
                # compare across the two.  We normalise EVERY datetime
                # to naive (local-wall-clock) before window checks so the
                # comparison never explodes.  Production runs in IST so
                # stripping tzinfo doesn't lose semantic meaning.
                def _naive(dt):
                    if dt is None:
                        return None
                    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

                for r, w_start, w_end in windows:
                    r["sub_cycles"] = []
                    r["py_events"]  = []
                    r["sa_data"]    = []
                    ws = _naive(w_start)
                    we = _naive(w_end)

                    for s in sub_rows:
                        ts = _naive(s.get("ts_end") or s.get("ts_start"))
                        if not ts: continue
                        if ws <= ts <= we:
                            r["sub_cycles"].append({
                                "machine_name": s.get("machine_name") or "—",
                                "machine_seq":  s.get("machine_seq"),
                                "cycle_seq":    s.get("cycle_seq"),
                                "ts_start":     s["ts_start"].isoformat() if s.get("ts_start") else None,
                                "ts_end":       s["ts_end"].isoformat()   if s.get("ts_end")   else None,
                                "ct_seconds":   float(s["ct_seconds"]) if s.get("ct_seconds") is not None else 0.0,
                            })

                    import json as _json_pe
                    for p in py_rows:
                        ts = _naive(p.get("detected_at"))
                        if not ts: continue
                        if ws <= ts <= we:
                            ctx = {}
                            raw = p.get("context_json")
                            if raw:
                                try: ctx = _json_pe.loads(raw) if isinstance(raw, str) else raw
                                except Exception: ctx = {}
                            r["py_events"].append({
                                "detected_at": p["detected_at"].isoformat(),
                                "py_no":       ctx.get("py_no", ""),
                                "py_name":     ctx.get("py_name", "") or ctx.get("py_no", ""),
                                "actual":      ctx.get("actual", ""),
                                "expected":    ctx.get("expected", ""),
                                "alert_level": p.get("alert_level") or "WARNING",
                                "rule_type":   p.get("rule_type") or "",
                            })

                    for s in sa_rows:
                        ts = _naive(s.get("ts_plc"))
                        if not ts: continue
                        if ws <= ts <= we:
                            vals = s.get("values_json") or []
                            if isinstance(vals, str):
                                try: vals = _json_pe.loads(vals)
                                except Exception: vals = []
                            names = s.get("sa_register_names") or []
                            if isinstance(names, str):
                                try: names = _json_pe.loads(names)
                                except Exception: names = []
                            r["sa_data"].append({
                                "machine_name": s.get("machine_name") or "—",
                                "ts_plc":       s["ts_plc"].isoformat(),
                                "part_code":    (s.get("part_code") or "").strip().rstrip(":"),
                                "values":       vals,
                                "register_names": names,
                            })

        # Strip internal datetime + sort by ts DESC
        for r in results:
            r.pop("_ts_dt", None)
        results.sort(key=lambda x: x.get("ts") or "", reverse=True)

        # Audit-trail: who searched for which part-code
        try:
            conn.cursor().execute("""
                INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                           user_id, username)
                VALUES ('PART_SEARCHED', 'part', %s, %s, %s, %s)
            """, (line_id,
                  f"code='{code}' from={d_from} to={d_to} hits={len(results)}",
                  user.get("id"), user.get("username")))
        except Exception as _se:
            print(f"[AUDIT] part-search write failed: {_se}")

        return results[:200]


@router.get("/historical")
def get_historical_data(
    line_id: int = Query(..., description="Line ID"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    shift_name: str = Query(..., description="Shift name (A or B)"),
    hour_slot: Optional[str] = Query(None, description="Specific hour slot"),
    user=Depends(get_current_user)
):
    """Retrieve historical shift data for a specific date and shift."""
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            
            # Permission check for operators
            _check_operator_access(user, line_id, conn)
            
            # Get the table name for this line
            cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Line not found")
            table = row["db_table_name"]
            
            print(f"[HISTORICAL] Querying table: {table} for date: {date}, shift: {shift_name}")
            
            # Get the most recent record for this date and shift
            # REPLACE:
            cur.execute(f"""
                SELECT * FROM {table}
                WHERE record_date = %s AND shift_name = %s
                ORDER BY 
                    CASE WHEN is_shift_completed = true THEN 0 ELSE 1 END,
                    ok_count DESC,
                    created_at DESC
                LIMIT 1
            """, (date, shift_name))
            data = cur.fetchone()
            
            if not data:
                print(f"[HISTORICAL] No data found for {date} {shift_name}")
                return {
                    "ok_count": 0,
                    "ng_count": 0,
                    "overall_oee": 0,
                    "availability": 0,
                    "performance": 0,
                    "quality_oee": 0,
                    "shift_plan_completed": 0,
                    "shift_plan": 0,
                    "operating_status": "NO_DATA"
                }
            
            print(f"[HISTORICAL] Found data with ok_count: {data.get('ok_count', 0)}")
            
            if hour_slot:
                # Get the column prefix for this slot
                cur.execute("""
                    SELECT db_column_prefix FROM mes_hourly_slots
                    WHERE line_id = %s AND slot_label = %s
                """, (line_id, hour_slot))
                slot = cur.fetchone()
                if slot:
                    prefix = slot["db_column_prefix"]
                    result = {
                        "slot": hour_slot,
                        "plan": data.get(f"{prefix}_plan", 0) or 0,
                        "actual": data.get(f"{prefix}_actual", 0) or 0,
                        "variance": data.get(f"{prefix}_variance", 0) or 0,
                        "ok": data.get(f"{prefix}_ok", 0) or 0,
                        "ng": data.get(f"{prefix}_ng", 0) or 0
                    }
                    return result
                else:
                    return {"error": f"Slot {hour_slot} not found for line {line_id}"}
            
            # Return all data for the shift
            result = {}
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    result[key] = value if value is not None else 0
                else:
                    result[key] = value
            
            return result
            
    except Exception as e:
        print(f"[HISTORICAL] Error: {str(e)}")
        raise HTTPException(500, f"Database error: {str(e)}")


@router.get("/historical/debug")
def debug_historical_data(
    line_id: int = Query(..., description="Line ID"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    shift_name: str = Query(..., description="Shift name (A or B)"),
    user=Depends(get_current_user)
):
    """Debug endpoint to see raw data"""
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            
            # Get table name
            cur.execute("SELECT db_table_name, line_code, line_name FROM mes_lines WHERE id = %s", (line_id,))
            line = cur.fetchone()
            if not line:
                return {"error": "Line not found"}
            
            table = line["db_table_name"]
            
            print(f"\n[DEBUG] Checking table: {table}")
            print(f"[DEBUG] Date: {date}, Shift: {shift_name}")
            
            # First, check if table exists
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                )
            """, (table,))
            table_exists = cur.fetchone()["exists"]
            print(f"[DEBUG] Table exists: {table_exists}")
            
            if not table_exists:
                return {"error": f"Table {table} does not exist"}
            
            # Check all records for this date and shift
            cur.execute(f"""
                SELECT id, record_date, shift_name, ok_count, ng_count, 
                       is_shift_completed, created_at
                FROM {table}
                WHERE record_date = %s AND shift_name = %s
                ORDER BY created_at DESC
            """, (date, shift_name))
            records = cur.fetchall()
            
            print(f"[DEBUG] Found {len(records)} records for {date} {shift_name}")
            
            if not records:
                # Check if there are any records at all for this line
                cur.execute(f"SELECT COUNT(*) as total FROM {table}")
                total = cur.fetchone()["total"]
                print(f"[DEBUG] Total records in table: {total}")
                
                # Check recent dates
                cur.execute(f"""
                    SELECT DISTINCT record_date, shift_name 
                    FROM {table} 
                    ORDER BY record_date DESC LIMIT 5
                """)
                recent = cur.fetchall()
                print(f"[DEBUG] Recent records: {recent}")
                
                return {
                    "error": f"No records found for {date} {shift_name}",
                    "table": table,
                    "total_records": total,
                    "recent_dates": recent,
                    "line_info": line
                }
            
            # Get the most recent record
            latest = records[0]
            
            # Get hourly slot columns
            cur.execute("""
                SELECT slot_label, db_column_prefix 
                FROM mes_hourly_slots 
                WHERE line_id = %s 
                ORDER BY slot_order
            """, (line_id,))
            slots = cur.fetchall()
            
            # Build slot data
            slot_data = {}
            for slot in slots:
                prefix = slot["db_column_prefix"]
                slot_data[slot["slot_label"]] = {
                    "plan": latest.get(f"{prefix}_plan", 0),
                    "actual": latest.get(f"{prefix}_actual", 0),
                    "variance": latest.get(f"{prefix}_variance", 0),
                    "ok": latest.get(f"{prefix}_ok", 0),
                    "ng": latest.get(f"{prefix}_ng", 0)
                }
            
            return {
                "line_info": line,
                "latest_record": {
                    "id": latest["id"],
                    "ok_count": latest["ok_count"],
                    "ng_count": latest["ng_count"],
                    "overall_oee": latest.get("overall_oee", 0),
                    "shift_plan_completed": latest.get("shift_plan_completed", 0),
                    "is_shift_completed": latest["is_shift_completed"],
                    "created_at": latest["created_at"]
                },
                "slot_data": slot_data,
                "all_records_count": len(records)
            }
    except Exception as e:
        return {"error": str(e)}


@router.post("/import/excel")
def import_excel_data(
    body: dict,
    admin=Depends(require_admin)
):
    """
    Import historical data from Excel.
    Body: { line_id, shift_name, record_date, data }
    """
    line_id = body.get("line_id")
    shift_name = body.get("shift_name")
    record_date = body.get("record_date")
    data = body.get("data", [])

    with get_conn() as conn:
        cur = dict_cursor(conn)

        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        table = row["db_table_name"]

        cur.execute(f"""
            SELECT id FROM {table}
            WHERE record_date = %s AND shift_name = %s
        """, (record_date, shift_name))
        existing = cur.fetchone()

        if existing:
            shift_id = existing["id"]
            ok_count = sum(d.get("OK", 0) for d in data)
            ng_count = sum(d.get("NG", 0) for d in data)
            cur.execute(f"""
                UPDATE {table}
                SET ok_count = %s, ng_count = %s, updated_at = NOW()
                WHERE id = %s
            """, (ok_count, ng_count, shift_id))

            for item in data:
                hour = item.get("Hour", "")
                if hour:
                    hour_parts = hour.split("-")
                    prefix = f"hour_{hour_parts[0].replace(':', '_')}_{hour_parts[1].replace(':', '_')}"
                    plan = item.get("Plan", 0)
                    actual = item.get("Actual", 0)
                    ok = item.get("OK", 0)
                    ng = item.get("NG", 0)
                    cur.execute(f"""
                        UPDATE {table}
                        SET {prefix}_plan = %s,
                            {prefix}_actual = %s,
                            {prefix}_ok = %s,
                            {prefix}_ng = %s,
                            {prefix}_variance = %s - %s
                        WHERE id = %s
                    """, (plan, actual, ok, ng, actual, plan, shift_id))
        else:
            ok_count = sum(d.get("OK", 0) for d in data)
            ng_count = sum(d.get("NG", 0) for d in data)
            # Look up shift's total_plan from config
            cur.execute("""
                SELECT total_plan FROM mes_shift_configs
                WHERE line_id = %s AND shift_name = %s
            """, (line_id, shift_name))
            scfg_row = cur.fetchone()
            _shift_plan = scfg_row["total_plan"] if scfg_row else 0
            cur.execute(f"""
                INSERT INTO {table}
                (record_date, shift_name, line_name, ok_count, ng_count, shift_plan, shift_plan_remaining, shift_plan_completed, is_shift_completed, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, NOW())
                RETURNING id
            """, (record_date, shift_name, f"Line {line_id}", ok_count, ng_count, _shift_plan, _shift_plan, 0))
            shift_id = cur.fetchone()["id"]

            for item in data:
                hour = item.get("Hour", "")
                if hour:
                    hour_parts = hour.split("-")
                    prefix = f"hour_{hour_parts[0].replace(':', '_')}_{hour_parts[1].replace(':', '_')}"
                    plan = item.get("Plan", 0)
                    actual = item.get("Actual", 0)
                    ok = item.get("OK", 0)
                    ng = item.get("NG", 0)
                    cur.execute(f"""
                        UPDATE {table}
                        SET {prefix}_plan = %s,
                            {prefix}_actual = %s,
                            {prefix}_ok = %s,
                            {prefix}_ng = %s,
                            {prefix}_variance = %s - %s
                        WHERE id = %s
                    """, (plan, actual, ok, ng, actual, plan, shift_id))

        conn.commit()
        return {"ok": True, "message": f"Imported {len(data)} rows"}


# ============================================================
# DYNAMIC ROUTES - WITH PATH PARAMETERS (MUST COME AFTER STATIC ROUTES)
# ============================================================

@router.get("/")
def list_lines(plant_id: Optional[int] = None, user=Depends(get_current_user)):
    """List all lines accessible to the user."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if user["role"] == "operator":
            # Operator: only assigned lines
            cur.execute("""
                SELECT l.*, p.plant_name, p.plant_code,
                       z.zone_name, z.zone_code
                FROM mes_lines l
                JOIN mes_plants p ON p.id = l.plant_id
                JOIN mes_operator_lines ol ON ol.line_id = l.id
                LEFT JOIN mes_zones z ON z.id = l.zone_id
                WHERE ol.admin_id = %s
                ORDER BY l.line_code
            """, (user["id"],))
        else:
            # Admin or department: all lines
            if plant_id:
                cur.execute("""
                    SELECT l.*, p.plant_name, p.plant_code,
                           z.zone_name, z.zone_code
                    FROM mes_lines l
                    JOIN mes_plants p ON p.id = l.plant_id
                    LEFT JOIN mes_zones z ON z.id = l.zone_id
                    WHERE l.plant_id = %s
                    ORDER BY l.line_code
                """, (plant_id,))
            else:
                cur.execute("""
                    SELECT l.*, p.plant_name, p.plant_code,
                           z.zone_name, z.zone_code
                    FROM mes_lines l
                    JOIN mes_plants p ON p.id = l.plant_id
                    LEFT JOIN mes_zones z ON z.id = l.zone_id
                    ORDER BY p.plant_name, l.line_code
                """)
        rows = cur.fetchall()

        # Verify collector process liveness and correct status
        for row in rows:
            stored_status = row.get("collector_status")
            pid = row.get("collector_pid")
            alive = is_process_alive(pid)

            if stored_status == "running" and not alive:
                row["collector_status"] = "stopped"
                # Update DB to match reality
                cur.execute(
                    "UPDATE mes_lines SET collector_status = 'stopped' WHERE id = %s",
                    (row["id"],)
                )
            elif stored_status == "stopped" and alive:
                row["collector_status"] = "running"
                cur.execute(
                    "UPDATE mes_lines SET collector_status = 'running' WHERE id = %s",
                    (row["id"],)
                )

        conn.commit()
        return rows


@router.get("/{line_id}")
def get_line(line_id: int, user=Depends(get_current_user_optional)):
    """Return full line detail including all config.

    PUBLIC endpoint: Fullscreen TV displays fetch the line metadata once
    on mount.  Operator-line restriction still applies for authenticated
    operators; anonymous callers get unrestricted read."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Permission check for operators
        _check_operator_access(user, line_id, conn)

        cur.execute("""
            SELECT l.*, p.plant_name, p.plant_code
            FROM mes_lines l
            JOIN mes_plants p ON p.id = l.plant_id
            WHERE l.id = %s
        """, (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, "Line not found")

        line = dict(line)

        # Attach all related config. parent_plc_id IS NULL → main PLC only
        # (sub-machines have their own listing via /machines).
        cur.execute(
            "SELECT * FROM mes_plc_configs "
            "WHERE line_id = %s AND parent_plc_id IS NULL",
            (line_id,))
        line["plc_config"] = cur.fetchone()

        cur.execute("SELECT * FROM mes_shift_configs WHERE line_id = %s ORDER BY shift_name", (line_id,))
        line["shifts"] = cur.fetchall()

        cur.execute("SELECT * FROM mes_hourly_slots WHERE line_id = %s ORDER BY shift_name, slot_order", (line_id,))
        line["hourly_slots"] = cur.fetchall()

        cur.execute("SELECT * FROM mes_break_configs WHERE line_id = %s ORDER BY start_time", (line_id,))
        line["breaks"] = cur.fetchall()

        cur.execute("SELECT * FROM mes_model_mappings WHERE line_id = %s ORDER BY model_number", (line_id,))
        line["models"] = cur.fetchall()

        cur.execute("SELECT * FROM mes_status_mappings WHERE line_id = %s ORDER BY status_code", (line_id,))
        line["status_map"] = cur.fetchall()

        cur.execute("""
            SELECT * FROM mes_poka_yoke_rules
            WHERE line_id = %s ORDER BY poka_yoke_no
        """, (line_id,))
        line["poka_yoke_rules"] = cur.fetchall()

        return line


@router.get("/{line_id}/production_history")
def get_production_history(
    line_id: int,
    days: int = Query(90, ge=1, le=2200),
    user=Depends(get_current_user_optional)
):
    """
    Return daily production totals for the last N days.
    Used by Fullscreen for daily/weekly/monthly cumulative charts.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        line_row = cur.fetchone()
        if not line_row:
            raise HTTPException(404, "Line not found")
        tbl = line_row["db_table_name"]
        cur.execute(f"""
            SELECT
                record_date,
                SUM(COALESCE(ok_count,0) + COALESCE(ng_count,0)) AS total_actual,
                SUM(COALESCE(shift_plan, 0))                      AS total_plan
            FROM {tbl}
            WHERE record_date >= CURRENT_DATE - INTERVAL '{days} days'
              AND COALESCE(is_gap_time, false) = false
              AND shift_name NOT LIKE 'GAP%%'
            GROUP BY record_date
            ORDER BY record_date
        """)
        return cur.fetchall()


@router.get("/{line_id}/ct-history")
def get_ct_history(
    line_id: int,
    date:  Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    shift: Optional[str] = Query(None, description="Shift name filter"),
    user=Depends(get_current_user_optional),
):
    """
    Return full cycle time log for a line on a given date/shift.
    Data comes from the <table>_ct_log table written by the collector.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        tbl_log = row["db_table_name"] + "_ct_log"

        # Check if the ct_log table exists yet (may not if collector never ran)
        cur.execute(
            "SELECT to_regclass(%s) AS exists",
            (tbl_log,),
        )
        if not cur.fetchone()["exists"]:
            return []

        record_date = date or datetime.now().strftime("%Y-%m-%d")
        params = [record_date]
        shift_clause = ""
        if shift:
            shift_clause = "AND shift_name = %s"
            params.append(shift)

        # part_code column may not exist on older installations — query it
        # conditionally so we don't error out on legacy tables.
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s AND column_name IN ('part_code', 'is_ng')
        """, (tbl_log,))
        extra_cols = {r["column_name"] for r in cur.fetchall()}
        has_part = "part_code" in extra_cols
        has_ng   = "is_ng" in extra_cols
        cols = "id, ts, record_date, shift_name, ct_value, cycle_seq"
        if has_part:
            cols += ", part_code"
        if has_ng:
            cols += ", is_ng"

        # ── Stale-row guard: if the collector restarted mid-shift after a
        # bug had pushed cycle_seq up to some huge number, the table now
        # contains a few "ancient" rows from before the reset interleaved
        # with the fresh 1..N sequence.  We anchor on the LAST row whose
        # cycle_seq was reset to 1 (true start of the current contiguous
        # run) and only return rows on/after it.  No reset row found →
        # fall back to plain date+shift filter (legacy behaviour).
        if shift:
            cur.execute(
                f"SELECT MAX(id) AS reset_id FROM {tbl_log} "
                f"WHERE record_date = %s AND shift_name = %s AND cycle_seq = 1",
                (record_date, shift),
            )
        else:
            cur.execute(
                f"SELECT MAX(id) AS reset_id FROM {tbl_log} "
                f"WHERE record_date = %s AND cycle_seq = 1",
                (record_date,),
            )
        r0 = cur.fetchone()
        reset_id = (r0 or {}).get("reset_id")

        id_clause = ""
        if reset_id is not None:
            id_clause = "AND id >= %s "
            params.append(reset_id)

        cur.execute(
            f"SELECT {cols} "
            f"FROM {tbl_log} "
            f"WHERE record_date = %s {shift_clause} {id_clause}"
            f"ORDER BY ts ASC",
            params,
        )
        rows = cur.fetchall()
        return [
            {
                "id":          r["id"],
                "ts":          r["ts"].isoformat() if r["ts"] else None,
                "record_date": str(r["record_date"]),
                "shift_name":  r["shift_name"],
                "ct_value":    float(r["ct_value"]),
                "cycle_seq":   r["cycle_seq"],
                "part_code":   (r.get("part_code") if has_part else None) or "",
                "is_ng":       bool(r.get("is_ng")) if has_ng else False,
            }
            for r in rows
        ]


@router.get("/{line_id}/cycle-video")
def get_cycle_video(
    line_id: int,
    cycle_seq: int = Query(..., description="cycle_seq from _ct_log"),
    date:      Optional[str] = Query(None, description="YYYY-MM-DD (defaults to today)"),
    shift:     Optional[str] = Query(None),
    token:     Optional[str] = Query(None, description="JWT fallback for <video src=...>"),
    request:   Request = None,
):
    """
    Proxy endpoint: look up the part_code for the given cycle_seq, then fetch
    the corresponding <part_code>.mp4 from the New-folder-2 camera backend.
    Range/seek headers are forwarded so HTML5 <video> seeking works.

    Auth accepts either:
      - Authorization: Bearer <jwt>   (normal API calls)
      - ?token=<jwt>                  (HTML5 <video src="..."> can't set headers)
      - (anonymous, since 2026-05-18-r14) — wallboard kiosk tabs that
        never log in still need cycle-video to work.  Cycle clips are
        not sensitive; the wallboard runs on a closed shop-floor LAN.
        Tokens are still VALIDATED if supplied, but absent tokens are
        accepted and skip the validation step entirely.
    """
    from auth import SECRET_KEY, ALGORITHM
    from jose import jwt as jose_jwt, JWTError as JoseJWTError

    # Resolve JWT from header or query param
    jwt_token = token
    if request:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            jwt_token = auth_hdr[7:]
    # 2026-05-18-r14 — Skip auth entirely when no token supplied
    # (wallboard kiosk anonymous mode).  Validate iff token present.
    if jwt_token:
        try:
            jose_jwt.decode(jwt_token, SECRET_KEY, algorithms=[ALGORITHM])
        except JoseJWTError:
            raise HTTPException(401, "Invalid or expired token")

    # Find the line table
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        tbl_log = row["db_table_name"] + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS exists", (tbl_log,))
        if not cur.fetchone()["exists"]:
            raise HTTPException(404, "No cycle log for this line yet")

        # Ensure part_code column exists
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s AND column_name = 'part_code'
        """, (tbl_log,))
        if cur.fetchone() is None:
            raise HTTPException(409, "part_code column missing — restart collector")

        record_date = date or datetime.now().strftime("%Y-%m-%d")
        params = [record_date, cycle_seq]
        shift_clause = ""
        if shift:
            shift_clause = "AND shift_name = %s"
            params.insert(1, shift)

        # Pick the newest row matching cycle_seq for that day/shift
        cur.execute(
            f"SELECT part_code FROM {tbl_log} "
            f"WHERE record_date = %s {shift_clause} AND cycle_seq = %s "
            f"ORDER BY ts DESC LIMIT 1",
            params,
        )
        hit = cur.fetchone()
        part_code = ((hit.get("part_code") if hit else "") or "").strip()

        # 2026-05-16 — NEAREST-CYCLE FALLBACK for empty part_code.
        # ~3 % of cycles have part_code='' (PLC D5004 race condition
        # caught between scanner write + L108-edge read).  Rather than
        # 404 the operator's click and leave them staring at a spinner,
        # we look at the ±3 nearest cycles in the same shift — almost
        # always at least one has a valid scan, and consecutive cycles
        # in the same part window typically share scan codes anyway.
        if not part_code:
            search_params = [record_date]
            search_clause = ""
            if shift:
                search_clause = "AND shift_name = %s"
                search_params.append(shift)
            search_params.extend([cycle_seq, cycle_seq])
            cur.execute(
                f"SELECT cycle_seq, part_code FROM {tbl_log} "
                f"WHERE record_date = %s {search_clause} "
                f"  AND cycle_seq BETWEEN %s - 3 AND %s + 3 "
                f"  AND TRIM(COALESCE(part_code, '')) != '' "
                f"ORDER BY ABS(cycle_seq - {cycle_seq}) ASC LIMIT 1",
                search_params,
            )
            near = cur.fetchone()
            if near:
                part_code = near["part_code"].strip()
                print(f"[CYCLE-VIDEO] line={line_id} cycle_seq={cycle_seq} had "
                      f"empty part_code; falling back to nearby cycle_seq="
                      f"{near['cycle_seq']} pc={part_code!r}", flush=True)

        if not part_code:
            raise HTTPException(404, "No part_code recorded for that cycle or any nearby cycle")

    # Sanitize to match the filename convention on the camera side
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", part_code).strip("_")
    if not safe:
        raise HTTPException(404, "part_code sanitized to empty string")

    # Forward Range header so seeking works
    fwd_headers = {}
    try:
        rng = request.headers.get("range") if request is not None else None
        if rng:
            fwd_headers["Range"] = rng
    except Exception:
        pass

    upstream = f"{CYCLE_VIDEO_BASE_URL}/api/video/by-part?code={safe}"
    try:
        r = requests.get(upstream, headers=fwd_headers, stream=True, timeout=15)
    except Exception as exc:
        raise HTTPException(502, f"Upstream unreachable: {exc}")

    if r.status_code == 404:
        raise HTTPException(404, f"Video not found for part_code={part_code}")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Upstream error: {r.text[:200]}")

    # Pass through streaming body + range headers
    resp_headers = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        if h in r.headers:
            resp_headers[h] = r.headers[h]
    resp_headers.setdefault("Accept-Ranges", "bytes")

    return StreamingResponse(
        r.iter_content(chunk_size=64 * 1024),
        status_code=r.status_code,
        media_type=resp_headers.get("Content-Type", "video/mp4"),
        headers=resp_headers,
    )


@router.get("/{line_id}/realtime")
def get_line_realtime(line_id: int, user=Depends(get_current_user_optional)):
    """
    Return the current (uncompleted) shift data from the line's dashboard table.
    Used by the frontend to display live OEE, plan, actual, etc.

    PUBLIC endpoint: Fullscreen TV displays poll this without logging in.
    When called by an authenticated operator, the operator-line restriction
    still applies; anonymous callers get unrestricted read.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Permission check for operators
        _check_operator_access(user, line_id, conn)

        # 1. Pull every mes_lines field this endpoint needs in ONE trip.
        # 2026-05-18 perf — was doing 3 separate SELECTs on mes_lines
        # (table/current_row, collector/ot_active, planned_takt/energy).
        # Folded into one query → /realtime now saves 2 LAN round-trips
        # per poll (~150ms each on the 192.168.10.210 DB).
        # The _ensure_*_column calls also moved to once-per-process,
        # so the takt/energy columns are guaranteed present here.
        _ensure_planned_takt_column(conn)
        _ensure_energy_per_part_column(conn)
        cur.execute(
            "SELECT db_table_name, current_shift_row_id, "
            "       collector_status, ot_active_shift, "
            "       planned_takt_time, energy_per_part "
            "FROM mes_lines WHERE id = %s",
            (line_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        table              = row["db_table_name"]
        current_row_id     = row.get("current_shift_row_id")
        _line_coll_status  = row.get("collector_status") or "stopped"
        _line_ot_active    = row.get("ot_active_shift")
        _line_planned_takt = row.get("planned_takt_time")
        _line_energy_pp    = row.get("energy_per_part")

        # 2. Fetch shift data — use the pinned row ID when available (zero ambiguity)
        data = None
        if current_row_id:
            cur.execute(
                f"SELECT * FROM {table} WHERE id = %s AND is_shift_completed = false",
                (current_row_id,),
            )
            data = cur.fetchone()

        # Self-healing fallback: if pinned row not found (NULL or stale),
        # auto-clean orphan rows and re-pin current_shift_row_id.
        # Orphan = non-completed row not updated in the last 10 seconds
        # (collector writes every 2s, so 10s means it's definitely dead).
        if not data:
            # Mark stale orphans as completed, keep only the most-recent active row
            cur.execute(f"""
                UPDATE {table}
                SET is_shift_completed = true, updated_at = NOW()
                WHERE is_shift_completed = false
                  AND (timestamp IS NULL OR timestamp < NOW() - INTERVAL '10 seconds')
                  AND id != COALESCE((
                      SELECT id FROM {table}
                      WHERE is_shift_completed = false
                      ORDER BY timestamp DESC NULLS LAST, id DESC
                      LIMIT 1
                  ), -1)
            """)
            # Find the survivor (most recently written active row)
            cur.execute(f"""
                SELECT id FROM {table}
                WHERE is_shift_completed = false
                ORDER BY timestamp DESC NULLS LAST, id DESC
                LIMIT 1
            """)
            pin_row = cur.fetchone()
            if pin_row:
                current_row_id = pin_row["id"]
                cur.execute(
                    "UPDATE mes_lines SET current_shift_row_id=%s WHERE id=%s",
                    (current_row_id, line_id),
                )
                cur.execute(
                    f"SELECT * FROM {table} WHERE id = %s AND is_shift_completed = false",
                    (current_row_id,),
                )
                data = cur.fetchone()

        # Final fallback: collector stopped — return most-recent non-completed row
        if not data:
            cur.execute(f"""
                SELECT * FROM {table}
                WHERE is_shift_completed = false
                ORDER BY timestamp DESC NULLS LAST LIMIT 1
            """)
            data = cur.fetchone()
        if not data:
            # Fallback: return empty structure (avoid None)
            return {
                "ok_count": 0,
                "ng_count": 0,
                "overall_oee": 0,
                "shift_plan_completed": 0,
                "operating_status": "IDLE",
                "shift_name": "UNKNOWN",
                "availability": 0,
                "performance": 0,
                "quality_oee": 0,
                "oee_grade": "N/A",
            }
        # Reuse the collector_status / ot_active_shift we already fetched
        # in the first mes_lines SELECT above — avoids a redundant query
        # on this 3-second-polled endpoint.
        data = dict(data)
        data["collector_status"] = _line_coll_status
        data["ot_active_shift"]  = _line_ot_active

        # Live-source current_model_name from Model Master first (always
        # reflects latest type+series), then mes_model_mappings as fallback.
        # This way the dashboard stays fresh even if the collector's
        # cfg["models"] cache is stale.
        mnum = data.get("current_model_number")
        if mnum:
            fresh_name = None
            cur.execute(
                "SELECT model_name FROM mes_py_model_master "
                "WHERE bit_number=%s AND is_active=true "
                "ORDER BY id DESC LIMIT 1",
                (mnum,),
            )
            r = cur.fetchone()
            if r and r["model_name"]:
                fresh_name = r["model_name"]
            if not fresh_name:
                cur.execute(
                    "SELECT model_name FROM mes_model_mappings "
                    "WHERE line_id=%s AND model_number=%s",
                    (line_id, mnum),
                )
                r = cur.fetchone()
                if r and r["model_name"]:
                    fresh_name = r["model_name"]
            if fresh_name:
                # Strip the legacy "TYPE-SERIES:" prefix if any row still has it.
                import re as _re
                data["current_model_name"] = _re.sub(
                    r"^TYPE-SERIES:\s*", "", fresh_name, flags=_re.IGNORECASE
                )

        # Attach OT window config + takt time for current shift.
        # Takt = customer rhythm — working_minutes × 60 / total_plan.
        # Frontend's Fullscreen CT graph overlays this as a dashed line
        # so operator sees the demand-driven target alongside the
        # machine's ideal CT.
        if data.get("shift_name"):
            cur.execute(
                """SELECT ot_start_time, ot_end_time,
                          working_minutes, total_plan
                     FROM mes_shift_configs
                    WHERE line_id = %s AND shift_name = %s""",
                (line_id, data["shift_name"]),
            )
            ot_row = cur.fetchone()
            if ot_row:
                data["ot_start_time"] = str(ot_row["ot_start_time"])[:5] if ot_row["ot_start_time"] else None
                data["ot_end_time"]   = str(ot_row["ot_end_time"])[:5]   if ot_row["ot_end_time"]   else None
                wm = ot_row.get("working_minutes") or 0
                tp = ot_row.get("total_plan") or 0
                data["takt_seconds"] = round((wm * 60.0) / tp, 2) if tp > 0 else None
                data["working_minutes"] = wm

        # 2026-05-14 — surface admin-configured planned takt time as its
        # own field.  The Fullscreen TAKT TIME card uses this as the "Plan"
        # row (with avg-CT-so-far as the "Actual" row).  Distinct from
        # `takt_seconds` (which is auto-derived from total_plan ÷ working_min);
        # the operator wanted an *explicit* knob that doesn't move when the
        # plan recalculates.
        # 2026-05-18 perf — values pulled in the single mes_lines SELECT
        # at the top of this function (no extra round-trip needed).
        try:
            data["planned_takt_seconds"] = (
                float(_line_planned_takt) if _line_planned_takt is not None else None
            )
            data["energy_per_part"] = (
                float(_line_energy_pp) if _line_energy_pp is not None else None
            )
        except Exception:
            data["planned_takt_seconds"] = None
            data["energy_per_part"]      = None
        return data


@router.get("/{line_id}/status")
def collector_status(line_id: int, user=Depends(get_current_user)):
    """Return live collector status + PID. Access controlled."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        _check_operator_access(user, line_id, conn)

        cur.execute("""
            SELECT line_code, line_name, collector_pid, collector_status, updated_at
            FROM mes_lines WHERE id = %s
        """, (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")

        # Check if process is actually still alive
        pid = row["collector_pid"]
        alive = is_process_alive(pid)

        result = dict(row)
        result["process_alive"] = alive
        return result


@router.get("/{line_id}/debug")
def debug_line(line_id: int, user=Depends(get_current_user)):
    """Inspect the latest row in the line's dashboard table. Access controlled."""
    with get_conn() as conn:
        cur = dict_cursor(conn)

        _check_operator_access(user, line_id, conn)

        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        row = cur.fetchone()
        if not row:
            return {"error": "line not found"}
        table = row["db_table_name"]
        cur.execute(f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT 1")
        data = cur.fetchone()
        return {"table": table, "data": data}


@router.post("/", status_code=201)
def create_line(body: LineCreate, admin=Depends(require_admin)):
    """
    Create a new line record.
    Does NOT provision yet — call /provision after adding PLC config.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            INSERT INTO mes_lines
                (plant_id, line_code, line_name, description, db_table_name, active_shifts, collector_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'stopped')
            RETURNING *
        """, (body.plant_id, body.line_code, body.line_name,
              body.description, body.db_table_name, body.active_shifts or "A,B"))
        line = cur.fetchone()

        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                       user_id, username)
            VALUES ('LINE_CREATED', 'line', %s, %s, %s, %s)
        """, (line["id"],
              f"code={body.line_code} table={body.db_table_name}",
              admin.get("id"), admin.get("username")))

    return line


@router.put("/{line_id}")
def update_line(line_id: int, body: LineUpdate, admin=Depends(require_admin)):
    """Update line details. Admin only."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")

    sets   = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [line_id]

    with get_conn() as conn:
        conn.cursor().execute(
            f"UPDATE mes_lines SET {sets}, updated_at = NOW() WHERE id = %s",
            values
        )
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                       user_id, username)
            VALUES ('LINE_UPDATED', 'line', %s, %s, %s, %s)
        """, (line_id, str(updates), admin.get("id"), admin.get("username")))

    return {"ok": True, "message": "Line updated"}


@router.post("/{line_id}/provision")
def provision(line_id: int, admin=Depends(require_admin)):
    try:
        result = provision_line(line_id)
        with get_conn() as conn:
            conn.cursor().execute(
                "UPDATE mes_lines SET collector_status = 'running' WHERE id = %s",
                (line_id,)
            )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Provisioning failed: {e}")


@router.post("/{line_id}/stop")
def stop(line_id: int, admin=Depends(require_admin)):
    result = stop_collector(line_id)
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_lines SET collector_status = 'stopped' WHERE id = %s",
            (line_id,)
        )
    return result


@router.post("/{line_id}/restart")
def restart(line_id: int, admin=Depends(require_admin)):
    stop_collector(line_id)
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_lines SET collector_status = 'stopped' WHERE id = %s",
            (line_id,)
        )
    try:
        result = provision_line(line_id)
        with get_conn() as conn:
            conn.cursor().execute(
                "UPDATE mes_lines SET collector_status = 'running' WHERE id = %s",
                (line_id,)
            )
        return result
    except Exception as e:
        raise HTTPException(500, f"Restart failed: {e}")


# ============================================================
# MACHINE (PLC) CRUD — multiple machines per line
# ============================================================

@router.get("/{line_id}/machines")
def list_machines(line_id: int, user=Depends(get_current_user)):
    """List all PLC machines assigned to this line.
    Auto-migrates the Semi-Auto columns on first call so admin can open
    the machine list on a fresh DB without hitting a 500."""
    with get_conn() as conn:
        _ensure_semi_auto_schema(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_plc_configs WHERE line_id = %s ORDER BY id",
            (line_id,)
        )
        return cur.fetchall()


@router.post("/{line_id}/machines", status_code=201)
def add_machine(line_id: int, body: MachineCreate, admin=Depends(require_admin)):
    """Add a PLC machine to the line."""
    with get_conn() as conn:
        _ensure_semi_auto_schema(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM mes_lines WHERE id = %s", (line_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Line not found")
        # Semi-Auto JSONB payloads — store NULL when caller didn't send anything,
        # otherwise the list as-is.  psycopg2's Json adapter handles encoding.
        sa_names_param  = Json(body.sa_register_names)  if body.sa_register_names  is not None else None
        sa_scales_param = Json(body.sa_register_scales) if body.sa_register_scales is not None else None
        cur.execute("""
            INSERT INTO mes_plc_configs
                (line_id, machine_name, plc_ip, plc_port, protocol,
                 ok_bit_address, ng_bit_address, status_address, model_address,
                 sensor_ok_address, process_seq_address, override_address,
                 ideal_cycle_time, max_allowed_cycle, ok_ng_pulse_min_gap,
                 parent_plc_id, nf2_camera_id, machine_seq,
                 sa_enabled, sa_fetch_bit,
                 sa_part_code_addr, sa_part_code_len,
                 sa_data_addr, sa_data_len,
                 sa_time_addr, sa_time_len,
                 sa_register_names, sa_register_scales,
                 is_bottleneck)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s, %s,%s, %s,%s, %s,%s, %s,%s, %s)
            RETURNING *
        """, (line_id, body.machine_name, body.plc_ip, body.plc_port, body.protocol,
              body.ok_bit_address, body.ng_bit_address, body.status_address, body.model_address,
              body.sensor_ok_address, body.process_seq_address, body.override_address,
              body.ideal_cycle_time, body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
              body.parent_plc_id, body.nf2_camera_id, body.machine_seq,
              bool(body.sa_enabled), body.sa_fetch_bit,
              body.sa_part_code_addr, body.sa_part_code_len,
              body.sa_data_addr, body.sa_data_len,
              body.sa_time_addr, body.sa_time_len,
              sa_names_param, sa_scales_param,
              bool(body.is_bottleneck)))
        machine = cur.fetchone()
        # Auto-set as dashboard PLC if it's the first one — but ONLY for
        # main machines (sub-machines must never become the dashboard PLC).
        if body.parent_plc_id is None:
            conn.cursor().execute("""
                UPDATE mes_lines SET dashboard_plc_id = %s
                WHERE id = %s AND dashboard_plc_id IS NULL
            """, (machine["id"], line_id))
    return machine


@router.put("/{line_id}/machines/{plc_id}")
def update_machine(line_id: int, plc_id: int, body: MachineCreate, admin=Depends(require_admin)):
    """Update a PLC machine's config."""
    with get_conn() as conn:
        _ensure_semi_auto_schema(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s", (plc_id, line_id))
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")
        sa_names_param  = Json(body.sa_register_names)  if body.sa_register_names  is not None else None
        sa_scales_param = Json(body.sa_register_scales) if body.sa_register_scales is not None else None
        conn.cursor().execute("""
            UPDATE mes_plc_configs SET
                machine_name=%s, plc_ip=%s, plc_port=%s, protocol=%s,
                ok_bit_address=%s, ng_bit_address=%s, status_address=%s, model_address=%s,
                sensor_ok_address=%s, process_seq_address=%s, override_address=%s,
                ideal_cycle_time=%s, max_allowed_cycle=%s, ok_ng_pulse_min_gap=%s,
                parent_plc_id=%s, nf2_camera_id=%s, machine_seq=%s,
                sa_enabled=%s, sa_fetch_bit=%s,
                sa_part_code_addr=%s, sa_part_code_len=%s,
                sa_data_addr=%s, sa_data_len=%s,
                sa_time_addr=%s, sa_time_len=%s,
                sa_register_names=%s, sa_register_scales=%s,
                is_bottleneck=%s,
                updated_at=NOW()
            WHERE id=%s
        """, (body.machine_name, body.plc_ip, body.plc_port, body.protocol,
              body.ok_bit_address, body.ng_bit_address, body.status_address, body.model_address,
              body.sensor_ok_address, body.process_seq_address, body.override_address,
              body.ideal_cycle_time, body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
              body.parent_plc_id, body.nf2_camera_id, body.machine_seq,
              bool(body.sa_enabled), body.sa_fetch_bit,
              body.sa_part_code_addr, body.sa_part_code_len,
              body.sa_data_addr, body.sa_data_len,
              body.sa_time_addr, body.sa_time_len,
              sa_names_param, sa_scales_param,
              bool(body.is_bottleneck),
              plc_id))
    return {"ok": True}


@router.delete("/{line_id}/machines/{plc_id}")
def delete_machine(line_id: int, plc_id: int, admin=Depends(require_admin)):
    """Remove a PLC machine. Dashboard PLC is cleared if this was the selected one."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s", (plc_id, line_id))
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")
        # Clear dashboard selection if this machine was selected
        conn.cursor().execute(
            "UPDATE mes_lines SET dashboard_plc_id = NULL WHERE id = %s AND dashboard_plc_id = %s",
            (line_id, plc_id)
        )
        conn.cursor().execute("DELETE FROM mes_plc_configs WHERE id = %s", (plc_id,))
    return {"ok": True}


# ── Dashboard PLC selection ───────────────────────────────────

@router.put("/{line_id}/dashboard-plc")
def set_dashboard_plc(line_id: int, body: DashboardPlcSet, admin=Depends(require_admin)):
    """Set which machine is the dashboard/fullscreen data source."""
    with get_conn() as conn:
        if body.plc_id:
            cur = dict_cursor(conn)
            cur.execute(
                "SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s",
                (body.plc_id, line_id)
            )
            if not cur.fetchone():
                raise HTTPException(404, "Machine not found or doesn't belong to this line")
        conn.cursor().execute(
            "UPDATE mes_lines SET dashboard_plc_id = %s, updated_at = NOW() WHERE id = %s",
            (body.plc_id, line_id)
        )
    return {"ok": True}


# ── Planning ──────────────────────────────────────────────────

# 2026-05-18 perf — these used to fire ALTER TABLE on every /realtime
# poll (every 3s).  Even as no-ops they take a DDL lock + round-trip,
# adding 100-300ms of buffering.  Cached per process now — first call
# does the migration, every subsequent call is an in-memory bool check.
_PLANNED_TAKT_COL_READY  = False
_ENERGY_PER_PART_COL_READY = False


def _ensure_planned_takt_column(conn) -> None:
    """Idempotent — adds the planned_takt_time column on first call.
    Cached after first success so subsequent polls skip the DDL trip."""
    global _PLANNED_TAKT_COL_READY
    if _PLANNED_TAKT_COL_READY:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE mes_lines
            ADD COLUMN IF NOT EXISTS planned_takt_time NUMERIC(8,2)
        """)
        conn.commit()
        cur.close()
        _PLANNED_TAKT_COL_READY = True
    except Exception:
        # Best effort — older Postgres versions without IF NOT EXISTS
        # already errored on the column being present; harmless.
        try: conn.rollback()
        except Exception: pass


def _ensure_energy_per_part_column(conn) -> None:
    """Idempotent — adds the energy_per_part column on first call.

    2026-05-16 — Operator wants a static "kWh per part" number on the
    main-line Fullscreen.  No live energy ingestion (PLM91 meters dead
    since March, no collector restart planned right now).  Admin sets
    this field per line based on shop-floor knowledge / nameplate math;
    the dashboard surfaces it as a small KPI card so quality / costing
    teams can read it at a glance.

    Cached after first success — see _ensure_planned_takt_column note.
    """
    global _ENERGY_PER_PART_COL_READY
    if _ENERGY_PER_PART_COL_READY:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE mes_lines
            ADD COLUMN IF NOT EXISTS energy_per_part NUMERIC(10,4)
        """)
        conn.commit()
        cur.close()
        _ENERGY_PER_PART_COL_READY = True
    except Exception:
        try: conn.rollback()
        except Exception: pass


def _ensure_semi_auto_schema(conn) -> None:
    """Idempotent migration for Semi-Auto data-capture on sub-machines.
    Adds nine optional columns to mes_plc_configs + creates the
    mes_submachine_data_log table with appropriate indexes.  Safe to
    call on every endpoint hit — Postgres skips the work if already
    present."""
    try:
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE mes_plc_configs
              ADD COLUMN IF NOT EXISTS sa_enabled          BOOLEAN     NOT NULL DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS sa_fetch_bit        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_part_code_addr   VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_part_code_len    INTEGER,
              ADD COLUMN IF NOT EXISTS sa_data_addr        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_data_len         INTEGER,
              ADD COLUMN IF NOT EXISTS sa_time_addr        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_time_len         INTEGER,
              ADD COLUMN IF NOT EXISTS sa_register_names   JSONB,
              ADD COLUMN IF NOT EXISTS sa_register_scales  JSONB,
              ADD COLUMN IF NOT EXISTS is_bottleneck       BOOLEAN     NOT NULL DEFAULT FALSE
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_submachine_data_log (
                id            BIGSERIAL   PRIMARY KEY,
                sub_plc_id    INTEGER     NOT NULL,
                line_id       INTEGER,
                record_date   DATE,
                shift_name    VARCHAR(10),
                cycle_seq     INTEGER,
                ts_plc        TIMESTAMPTZ,
                ts_server     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                part_code     VARCHAR(80),
                model_number  INTEGER,
                model_name    VARCHAR(120),
                data_values   JSONB       NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_sub_ts
                ON mes_submachine_data_log (sub_plc_id, ts_server DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_part
                ON mes_submachine_data_log (part_code)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_date_shift
                ON mes_submachine_data_log (record_date, shift_name)
        """)
        conn.commit()
        cur.close()
    except Exception as exc:
        try: conn.rollback()
        except Exception: pass
        print(f"[SEMI-AUTO] schema ensure failed (will retry on next call): {exc}")


@router.get("/{line_id}/planning")
def get_planning(line_id: int, user=Depends(get_current_user)):
    """Return ideal cycle time + planned takt time + energy per part
    + shift plan breakdown."""
    with get_conn() as conn:
        _ensure_planned_takt_column(conn)
        _ensure_energy_per_part_column(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, planned_takt_time, energy_per_part "
            "  FROM mes_lines WHERE id = %s",
            (line_id,),
        )
        ln = cur.fetchone()
        if not ln:
            raise HTTPException(404, "Line not found")
        planned_takt    = float(ln["planned_takt_time"]) if ln.get("planned_takt_time") is not None else None
        energy_per_part = float(ln["energy_per_part"])   if ln.get("energy_per_part")   is not None else None
        cur.execute("""
            SELECT COALESCE(ideal_cycle_time, 15.0) AS ideal_cycle_time
            FROM mes_plc_configs
            WHERE line_id = %s AND parent_plc_id IS NULL
            ORDER BY id LIMIT 1
        """, (line_id,))
        plc = cur.fetchone()
        ideal_ct = float(plc["ideal_cycle_time"]) if plc else 15.0
        cur.execute("""
            SELECT shift_name, start_time, end_time, working_minutes, total_plan
            FROM mes_shift_configs
            WHERE line_id = %s
            ORDER BY shift_name
        """, (line_id,))
        shifts = cur.fetchall()
        return {
            "ideal_ct":        ideal_ct,
            "planned_takt":    planned_takt,
            "energy_per_part": energy_per_part,
            "shifts":          shifts
        }


@router.put("/{line_id}/planning")
def save_planning(line_id: int, body: PlanningUpdate, admin=Depends(require_admin)):
    """Save ideal cycle time + (optionally) planned takt time.
    If recalculate=True, also updates total_plan on all production shifts."""
    if body.ideal_ct <= 0:
        raise HTTPException(400, "ideal_ct must be > 0")
    if body.planned_takt is not None and body.planned_takt <= 0:
        raise HTTPException(400, "planned_takt must be > 0 if provided")
    if body.energy_per_part is not None and body.energy_per_part < 0:
        raise HTTPException(400, "energy_per_part must be ≥ 0 if provided")
    with get_conn() as conn:
        _ensure_planned_takt_column(conn)
        _ensure_energy_per_part_column(conn)
        cur = dict_cursor(conn)
        conn.cursor().execute(
            "UPDATE mes_lines SET ideal_cycle_time = %s, updated_at = NOW() WHERE id = %s",
            (body.ideal_ct, line_id)
        )
        if body.planned_takt is not None:
            conn.cursor().execute(
                "UPDATE mes_lines SET planned_takt_time = %s, updated_at = NOW() WHERE id = %s",
                (body.planned_takt, line_id)
            )
        if body.energy_per_part is not None:
            conn.cursor().execute(
                "UPDATE mes_lines SET energy_per_part = %s, updated_at = NOW() WHERE id = %s",
                (body.energy_per_part, line_id)
            )
        if body.recalculate:
            cur.execute("""
                SELECT id, working_minutes FROM mes_shift_configs
                WHERE line_id = %s AND is_production = true
                  AND shift_name NOT LIKE 'GAP%%'
            """, (line_id,))
            for s in cur.fetchall():
                new_plan = int(s["working_minutes"] * 60 / body.ideal_ct)
                conn.cursor().execute(
                    "UPDATE mes_shift_configs SET total_plan = %s WHERE id = %s",
                    (new_plan, s["id"])
                )
        # Audit-trail: who saved which planning values
        try:
            details = (
                f"ideal_ct={body.ideal_ct}"
                + (f" takt={body.planned_takt}"      if body.planned_takt      is not None else "")
                + (f" kWh/part={body.energy_per_part}" if body.energy_per_part is not None else "")
                + (" recalculated" if body.recalculate else "")
            )
            conn.cursor().execute("""
                INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                           user_id, username)
                VALUES ('PLANNING_SAVED', 'line', %s, %s, %s, %s)
            """, (line_id, details, admin.get("id"), admin.get("username")))
        except Exception as _e:
            print(f"[AUDIT] planning save failed: {_e}")
    return {"ok": True, "message": "Planning saved"}

# ── Status History Log ───────────────────────────────────────

class StatusLogEntry(BaseModel):
    record_date: str          # "YYYY-MM-DD"
    shift_name:  str
    status:      str
    ts:          float        # Unix ms (epoch milliseconds)
    nowminfrac:  float        # hours*60 + min + sec/60


@router.get("/{line_id}/status-log")
def get_status_log(
    line_id:     int,
    date:        str = None,  # YYYY-MM-DD; defaults to today
    shift:       str = None,
    user=Depends(get_current_user_optional),
):
    """Return all status-log entries for a line on a given date (whole day by default)."""
    from datetime import date as _date
    target = date or str(_date.today())
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _check_operator_access(user, line_id, conn)
        if shift:
            cur.execute("""
                SELECT ts, nowminfrac, status, shift_name
                FROM mes_status_log
                WHERE line_id = %s AND record_date = %s AND shift_name = %s
                ORDER BY ts ASC
            """, (line_id, target, shift))
        else:
            cur.execute("""
                SELECT ts, nowminfrac, status, shift_name
                FROM mes_status_log
                WHERE line_id = %s AND record_date = %s
                ORDER BY ts ASC
            """, (line_id, target))
        rows = cur.fetchall()
    return [
        {
            "ts":         r["ts"].timestamp() * 1000,   # → epoch ms for JS
            "nowMinFrac": float(r["nowminfrac"]),
            "status":     r["status"],
            "shift":      r["shift_name"],
        }
        for r in rows
    ]


@router.post("/{line_id}/status-log", status_code=410)
def append_status_log(line_id: int, body: StatusLogEntry, user=Depends(get_current_user)):
    """DEPRECATED — frontend writes are forbidden as of 2026-05-15.

    The collector (collector_engine.py::_update_status / _write_status_log)
    reads the PLC status bit at 30 ms cadence and is the SOLE writer of
    mes_status_log.  Any browser tab that still tries to POST here gets
    HTTP 410 Gone so a stale page can never inject phantom timeline rows
    (the original bug: 10 dashboards × 3 s poll × debounce jitter =
    timeline filled with bogus IDLE / BREAKDOWN chunks even while the
    cycle count incremented normally).

    Logged so we can spot which old client / IP is still trying to write.
    """
    print(f"[STATUS-LOG-POST-BLOCKED] line={line_id} status={body.status!r} "
          f"shift={body.shift_name!r} — stale client, refuse write", flush=True)
    raise HTTPException(
        status_code=410,
        detail=(
            "frontend writes to mes_status_log are disabled — "
            "collector is the only authoritative writer. "
            "Hard-refresh the dashboard to load the read-only client."
        ),
    )


# ── OT Config ────────────────────────────────────────────────

class OTConfigEntry(BaseModel):
    shift_name:    str
    ot_start_time: Optional[str] = None
    ot_end_time:   Optional[str] = None

class OTActiveBody(BaseModel):
    shift: Optional[str] = None   # None / null = deactivate OT


@router.get("/{line_id}/cycle-extremes")
def get_cycle_extremes(
    line_id: int,
    date:    str = None,    # YYYY-MM-DD; defaults to today
    shift:   str = None,    # e.g. "A"; if omitted, current open shift
    user=Depends(get_current_user_optional),
):
    """Return the slowest + fastest cycles of the (date, shift) window.

    2026-05-15 — Department review asked for a side-box on Fullscreen
    showing the shift's min/max cycle time; clicking either should
    surface the part_code + timestamp + cycle video for that exact
    cycle.  This endpoint resolves min/max from the per-line ct_log
    table so the click target is a real cycle row (not just the
    aggregated min_ct/max_ct on the shift row).
    """
    from datetime import date as _date
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _check_operator_access(user, line_id, conn)
        cur.execute(
            "SELECT db_table_name FROM mes_lines WHERE id = %s",
            (line_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        base = row["db_table_name"]
        ct_table = f"{base}_ct_log"

        target_date = date or str(_date.today())
        params      = [target_date]
        where       = "record_date = %s"
        if shift:
            where  += " AND shift_name = %s"
            params.append(shift)
        else:
            # No shift supplied → use the current open shift from
            # the row pinned on mes_lines.  Falls back to the most
            # recent shift in ct_log if nothing pinned.
            cur.execute(
                f"SELECT shift_name FROM {base} "
                f"WHERE id = (SELECT current_shift_row_id FROM mes_lines WHERE id=%s)",
                (line_id,),
            )
            r = cur.fetchone()
            if r and r.get("shift_name"):
                where += " AND shift_name = %s"
                params.append(r["shift_name"])

        # Fetch min + max in two cheap queries (idx on record_date helps).
        try:
            cur.execute(
                f"""SELECT ts, ct_value, part_code, is_ng, cycle_seq, shift_name
                      FROM {ct_table}
                     WHERE {where} AND ct_value IS NOT NULL AND ct_value > 0
                  ORDER BY ct_value ASC, ts DESC
                     LIMIT 1""",
                tuple(params),
            )
            min_row = cur.fetchone()
            cur.execute(
                f"""SELECT ts, ct_value, part_code, is_ng, cycle_seq, shift_name
                      FROM {ct_table}
                     WHERE {where} AND ct_value IS NOT NULL AND ct_value > 0
                  ORDER BY ct_value DESC, ts DESC
                     LIMIT 1""",
                tuple(params),
            )
            max_row = cur.fetchone()
        except Exception as e:
            # Table doesn't exist for this line yet (no data) — return nulls.
            return {"min": None, "max": None, "shift": shift, "date": target_date,
                    "error": str(e)}

        def _pack(r):
            if not r:
                return None
            return {
                "ts":         r["ts"].isoformat() if r["ts"] else None,
                "ct_value":   float(r["ct_value"]) if r["ct_value"] is not None else None,
                "part_code":  (r.get("part_code") or "").strip().rstrip(":"),
                "is_ng":      bool(r.get("is_ng")),
                "cycle_seq":  r.get("cycle_seq"),
                "shift_name": r.get("shift_name"),
            }
        return {
            "min":   _pack(min_row),
            "max":   _pack(max_row),
            "shift": shift,
            "date":  target_date,
        }


@router.get("/{line_id}/hourly-loss-breakdown")
def get_hourly_loss_breakdown(
    line_id: int,
    date:    str = None,    # YYYY-MM-DD; defaults to today
    shift:   str = None,    # e.g. "A"; required
    user=Depends(get_current_user_optional),
):
    """Return per-hourly-slot breakdown of every loss bucket
    (Breakdown / Quality / Material / Setup / Change Over / Speed / Others).

    Drives the "click on Loss Distribution → expand to hourly breakup"
    modal on the operator Fullscreen page.

    Algorithm:
      1. Pull all status-log events for the line on (date, shift).
      2. Walk consecutive pairs → each pair is a (status, duration_seconds)
         span.  The status is mapped to a loss bucket via
         mes_status_mappings.
      3. Bucket the span into hourly slots (mes_hourly_slots) — if a
         span crosses a slot boundary, we split the seconds across both.
      4. Return one row per slot with seconds for each loss category,
         plus a total row at the end.

    Output shape:
        {
          "slots": [
            { "slot_label":"08:30-09:30", "start":"08:30", "end":"09:30",
              "loss_breakdown":120, "loss_quality":0, "loss_material":0,
              "loss_setup":300, "loss_change_over":0, "loss_speed":15,
              "loss_others":0, "total_loss":435 },
            ...
          ],
          "totals": { ...same keys... }
        }
    """
    from datetime import date as _date, datetime as _dt, time as _t, timedelta
    if not shift:
        raise HTTPException(400, "shift is required (e.g. 'A')")
    target_date = date or str(_date.today())

    LOSS_KEYS = ["breakdown","quality","material","setup",
                 "change_over","speed","others"]

    with get_conn() as conn:
        cur = dict_cursor(conn)
        _check_operator_access(user, line_id, conn)

        # 1. Fetch status mappings for this line — name → loss_type
        cur.execute("""
            SELECT status_name, COALESCE(loss_type,'') AS loss_type
              FROM mes_status_mappings
             WHERE line_id = %s
        """, (line_id,))
        name_to_loss = { r["status_name"]: (r["loss_type"] or "").lower()
                         for r in cur.fetchall() }

        # 2. Fetch hourly slots for this shift (ordered)
        cur.execute("""
            SELECT slot_label, start_time, end_time, crosses_midnight,
                   working_minutes
              FROM mes_hourly_slots
             WHERE line_id = %s AND shift_name = %s
             ORDER BY slot_order
        """, (line_id, shift))
        slots = [dict(r) for r in cur.fetchall()]
        if not slots:
            return {"slots": [], "totals": { f"loss_{k}": 0 for k in LOSS_KEYS } | {"total_loss": 0}}

        # 3. Fetch status log for this line / date / shift, ordered by ts
        cur.execute("""
            SELECT ts, status, shift_name
              FROM mes_status_log
             WHERE line_id = %s AND record_date = %s AND shift_name = %s
             ORDER BY ts ASC
        """, (line_id, target_date, shift))
        events = cur.fetchall()
        if not events:
            return {"slots": [
                { "slot_label": s["slot_label"],
                  "start": str(s["start_time"])[:5],
                  "end":   str(s["end_time"])[:5],
                  **{ f"loss_{k}": 0 for k in LOSS_KEYS },
                  "total_loss": 0,
                } for s in slots
            ], "totals": { f"loss_{k}": 0 for k in LOSS_KEYS } | {"total_loss": 0}}

        # Helper: convert HH:MM:SS time + base date → datetime, handling
        # cross-midnight slots by adding 1 day to end if needed.
        def _slot_window(slot, base_date):
            base = _dt.combine(base_date, _t(0))
            st = slot["start_time"]; en = slot["end_time"]
            if isinstance(st, str): st = _dt.strptime(st, "%H:%M:%S").time()
            if isinstance(en, str): en = _dt.strptime(en, "%H:%M:%S").time()
            slot_start = base + timedelta(hours=st.hour, minutes=st.minute, seconds=st.second)
            slot_end   = base + timedelta(hours=en.hour, minutes=en.minute, seconds=en.second)
            if slot["crosses_midnight"] or slot_end <= slot_start:
                slot_end += timedelta(days=1)
            return slot_start, slot_end

        try:
            base_date = _dt.strptime(target_date, "%Y-%m-%d").date()
        except Exception:
            base_date = _date.today()

        slot_windows = [(s, *_slot_window(s, base_date)) for s in slots]

        # Initialise per-slot accumulator
        slot_loss = []
        for s, _, _ in slot_windows:
            slot_loss.append({ f"loss_{k}": 0 for k in LOSS_KEYS })

        # 4. Walk events pairwise, attribute each span's seconds to slots
        # Append a sentinel "now" at the end so the last open span gets
        # counted up to the current moment.
        events = list(events)
        events.append({"ts": _dt.now(events[0]["ts"].tzinfo), "status": events[-1]["status"]})

        for i in range(len(events) - 1):
            ev   = events[i]
            nxt  = events[i+1]
            st   = ev["ts"]
            en   = nxt["ts"]
            if en <= st: continue
            loss = name_to_loss.get(ev["status"], "")
            if not loss or loss not in LOSS_KEYS: continue
            span_key = f"loss_{loss}"

            # Distribute (st, en) seconds across each slot that overlaps
            for idx, (slot, slot_st, slot_en) in enumerate(slot_windows):
                # Tz-handling — drop tz info for naive comparison if needed
                a = max(st, slot_st.replace(tzinfo=st.tzinfo) if st.tzinfo else slot_st)
                b = min(en, slot_en.replace(tzinfo=en.tzinfo) if en.tzinfo else slot_en)
                if b > a:
                    slot_loss[idx][span_key] += int((b - a).total_seconds())

        # 5. Build response
        out_slots = []
        totals = { f"loss_{k}": 0 for k in LOSS_KEYS }
        for idx, (slot, slot_st, slot_en) in enumerate(slot_windows):
            row = {
                "slot_label": slot["slot_label"],
                "start": str(slot["start_time"])[:5],
                "end":   str(slot["end_time"])[:5],
                **slot_loss[idx],
            }
            row["total_loss"] = sum(slot_loss[idx][k] for k in slot_loss[idx])
            for k in LOSS_KEYS:
                totals[f"loss_{k}"] += slot_loss[idx][f"loss_{k}"]
            out_slots.append(row)
        totals["total_loss"] = sum(totals[k] for k in totals)

    return {"slots": out_slots, "totals": totals}


@router.get("/{line_id}/ot-config")
def get_ot_config(line_id: int, user=Depends(get_current_user)):
    """Return OT start/end times for each production shift of this line."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT shift_name,
                   ot_start_time::text AS ot_start_time,
                   ot_end_time::text   AS ot_end_time
            FROM mes_shift_configs
            WHERE line_id = %s AND is_production = true
              AND shift_name NOT LIKE 'GAP%%'
            ORDER BY shift_name
        """, (line_id,))
        rows = cur.fetchall()
        return [
            {
                "shift_name":    r["shift_name"],
                "ot_start_time": r["ot_start_time"][:5] if r["ot_start_time"] else None,
                "ot_end_time":   r["ot_end_time"][:5]   if r["ot_end_time"]   else None,
            }
            for r in rows
        ]


@router.put("/{line_id}/ot-config")
def save_ot_config(line_id: int, body: List[OTConfigEntry], user=Depends(get_current_user)):
    """Save OT start/end times per production shift for a line. Zone users and above."""
    if user.get("role") not in ("admin", "zone"):
        raise HTTPException(403, "Zone or Admin role required")
    with get_conn() as conn:
        cur = conn.cursor()
        for entry in body:
            cur.execute("""
                UPDATE mes_shift_configs
                SET ot_start_time = %s,
                    ot_end_time   = %s
                WHERE line_id = %s AND shift_name = %s
            """, (entry.ot_start_time or None, entry.ot_end_time or None,
                  line_id, entry.shift_name))
        conn.commit()
        cur.close()
    return {"ok": True}


@router.put("/{line_id}/ot-active")
def set_ot_active(line_id: int, body: OTActiveBody, user=Depends(get_current_user)):
    """Activate or deactivate OT for a specific shift on a line. Zone users and above."""
    if user.get("role") not in ("admin", "zone"):
        raise HTTPException(403, "Zone or Admin role required")
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_lines SET ot_active_shift = %s, updated_at = NOW() WHERE id = %s",
            (body.shift or None, line_id),
        )
        conn.commit()
    return {"ok": True, "ot_active_shift": body.shift}


# NOTE: A second /ot-config GET/PUT pair used to live here that read/wrote
# `mes_lines.ot_start_a / ot_end_a / ot_start_b / ot_end_b` (per-line columns).
# It clashed with the canonical pair above (which uses `mes_shift_configs.
# ot_start_time / ot_end_time` per shift — the same table the collector reads
# in `_get_ot_window`).  FastAPI's last-registered-handler-wins semantics meant
# the duplicate silently shadowed the per-shift route, so the AdminPanel saved
# OT windows into a column the collector never reads → OT activation appeared
# to do nothing.  Removed entirely; the per-shift pair above is the single
# source of truth for OT windows.


# ══════════════════════════════════════════════════════════════
# MACHINE MONITORING CONFIG  (polling bit + data registers + loadcell)
# ══════════════════════════════════════════════════════════════

@router.get("/{line_id}/machines/{plc_id}/monitor-config")
def get_monitor_config(line_id: int, plc_id: int, user=Depends(get_current_user)):
    """Return the monitoring config (polling bit, data registers, loadcell) for a machine."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s",
            (plc_id, line_id)
        )
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")
        cur.execute("""
            SELECT * FROM mes_machine_monitor_configs
            WHERE plc_id = %s
            ORDER BY id DESC LIMIT 1
        """, (plc_id,))
        row = cur.fetchone()
        if not row:
            return {
                "plc_id": plc_id,
                "polling_bit": "",
                "has_data_registers": False,
                "data_registers": [],
                "has_loadcell": False,
                "loadcell_registers": [],
            }
        import json
        return {
            "plc_id":             row["plc_id"],
            "polling_bit":        row["polling_bit"] or "",
            "has_data_registers": bool(row["has_data_registers"]),
            "data_registers":     json.loads(row["data_registers"] or "[]"),
            "has_loadcell":       bool(row["has_loadcell"]),
            "loadcell_registers": json.loads(row["loadcell_registers"] or "[]"),
        }


@router.put("/{line_id}/machines/{plc_id}/monitor-config")
def save_monitor_config(
    line_id: int,
    plc_id:  int,
    body:    MachineMonitorConfig,
    admin=Depends(require_admin)
):
    """
    Upsert monitoring config for a machine.
    Creates the table if it doesn't exist (safe migration).
    """
    import json
    with get_conn() as conn:
        cur = conn.cursor()

        # Safe migration — create table if first time
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_machine_monitor_configs (
                id                  SERIAL PRIMARY KEY,
                plc_id              INTEGER NOT NULL REFERENCES mes_plc_configs(id) ON DELETE CASCADE,
                polling_bit         TEXT    NOT NULL,
                has_data_registers  BOOLEAN NOT NULL DEFAULT false,
                data_registers      JSONB   NOT NULL DEFAULT '[]',
                has_loadcell        BOOLEAN NOT NULL DEFAULT false,
                loadcell_registers  JSONB   NOT NULL DEFAULT '[]',
                updated_at          TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (plc_id)
            )
        """)
        conn.commit()

        # Verify plc belongs to line
        cur.execute(
            "SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s",
            (plc_id, line_id)
        )
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")

        data_regs  = json.dumps([r.model_dump() for r in body.data_registers])
        load_regs  = json.dumps([r.model_dump() for r in body.loadcell_registers])

        cur.execute("""
            INSERT INTO mes_machine_monitor_configs
                (plc_id, polling_bit, has_data_registers, data_registers,
                 has_loadcell, loadcell_registers, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, NOW())
            ON CONFLICT (plc_id) DO UPDATE SET
                polling_bit         = EXCLUDED.polling_bit,
                has_data_registers  = EXCLUDED.has_data_registers,
                data_registers      = EXCLUDED.data_registers,
                has_loadcell        = EXCLUDED.has_loadcell,
                loadcell_registers  = EXCLUDED.loadcell_registers,
                updated_at          = NOW()
        """, (
            plc_id,
            body.polling_bit,
            body.has_data_registers,
            data_regs,
            body.has_loadcell,
            load_regs,
        ))
        conn.commit()

        cur.execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('MONITOR_CONFIG_SAVED', 'plc', %s, %s)
        """, (plc_id, f"polling_bit={body.polling_bit} data_regs={len(body.data_registers)} loadcell={len(body.loadcell_registers)}"))
        conn.commit()

    return {"ok": True, "message": "Monitor config saved"}


@router.delete("/{line_id}/machines/{plc_id}/monitor-config")
def delete_monitor_config(line_id: int, plc_id: int, admin=Depends(require_admin)):
    """Remove monitoring config for a machine."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM mes_plc_configs WHERE id = %s AND line_id = %s",
            (plc_id, line_id)
        )
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")
        cur.execute(
            "DELETE FROM mes_machine_monitor_configs WHERE plc_id = %s",
            (plc_id,)
        )
        conn.commit()
    return {"ok": True}