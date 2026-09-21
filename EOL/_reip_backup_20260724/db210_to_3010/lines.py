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
from datetime import datetime, date as _date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, List

import requests
from psycopg2.extras import Json

from database import get_conn, dict_cursor

# Base URL of the "New folder 2" (NF2) camera/video backend.
# Override by setting env var CYCLE_VIDEO_BASE_URL, e.g.
#   CYCLE_VIDEO_BASE_URL=http://192.168.30.50:5555
# NF2's Flask runs on 5555 by default (see api_server.py app.run port=5555),
# so the default here MUST be 5555 — earlier this said 5000 which proxied
# to nothing and surfaced as "Upstream unreachable" / "exit status 400".
CYCLE_VIDEO_BASE_URL = os.environ.get(
    "CYCLE_VIDEO_BASE_URL", "http://127.0.0.1:5555"
).rstrip("/")
# 2026-05-29 - Allow MES to run standalone without CMS.  When
# CYCLE_VIDEO_BASE_URL is empty (or set to 'disabled' in .env),
# cycle-video endpoints return 404 immediately instead of hammering
# a dead CMS socket.  Frontend video buttons just show "not available".
CMS_INTEGRATION_ENABLED = bool(CYCLE_VIDEO_BASE_URL) and CYCLE_VIDEO_BASE_URL.lower() != "disabled"
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
    # ── Counting mode (Main PLC only) ────────────────────────────
    # 2026-05-29 - 'bit' (default, L108/L109 rising edges) or
    # 'register' (poll D-register value, increment = +N OK/NG).
    count_mode:          Optional[str] = "bit"
    ok_data_register:    Optional[str] = None
    ng_data_register:    Optional[str] = None
    # 2026-05-30 — per-machine bit address that pulses ON (~2s) at shift
    # end.  Rising edge → archive closing OK/NG, then registers reset to 0.
    # NULL = no auto-reset (machine keeps current behaviour → zero regression).
    shift_reset_bit:     Optional[str] = None
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
    # ── Seat-Slider load-cell NG traceability (2026-07-16) ──────────
    # Opt-in per machine (blank = feature off).  Semi-Auto: lc_result_reg
    # holds the PLC's load-cell verdict (1=OK, 2=NG); lc_value_reg the actual
    # weight (logged with the NG barcode).  Final: qr_reg = the scanned QR
    # register; reject_bit = the bit the collector pulses ON 5s when the QR
    # matches an unresolved NG in mes_seatslider_ng.
    lc_result_reg:       Optional[str]  = None   # semi-auto: 1=OK / 2=NG
    lc_value_reg:        Optional[str]  = None   # semi-auto: weight register
    qr_reg:              Optional[str]  = None   # final: QR data register
    reject_bit:          Optional[str]  = None   # final: 5s-ON reject bit


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


# ─────────────────────────────────────────────────────────────────────
# SHIFT COUNT RECONCILIATION  (2026-06-18)
# ─────────────────────────────────────────────────────────────────────
# The hourly bucket columns (hour_*_ok / _ng / _actual) are incremented
# per-cycle by the collector and PERMANENTLY lose counts whenever a DB
# write stalls (measured ~29 cycles/shift undercount vs the PLC register;
# one shift lost 195).  The PLC OK/NG register (ok_count / ng_count) is
# self-healing — it re-reads the PLC's cumulative counter — so it is the
# authoritative production count.  ct_log (per-cycle event rows) is far
# more complete than the buckets but still drifts a little.
#
# This reconciles, AT READ TIME (no DB writes, collector untouched), the
# per-slot ok/ng for a COMPLETED shift: rebuild each slot from ct_log
# timestamps, then scale so the grand total equals the register.  Result:
# the slot table's Total == the top KPI register == the truth.
#
# Pure compute-on-read; completed shifts are immutable so the result is
# cached.  Any failure → return None and the caller keeps the raw stored
# buckets (zero regression).
_RECON_CACHE = {}            # (line_id, date_str, shift) -> {prefix: {...}} | None


def _recon_distribute(raw, target):
    """Integer-split `target` across len(raw) slots, proportional to `raw`
    (largest-remainder), guaranteeing the result sums to exactly `target`.
    Even spread fallback when `raw` is all-zero."""
    n = len(raw)
    target = int(round(target))
    if n == 0:
        return []
    if target <= 0:
        return [0] * n
    s = sum(raw)
    if s <= 0:
        base = target // n
        out = [base] * n
        for i in range(target - base * n):
            out[i] += 1
        return out
    exact = [target * x / s for x in raw]
    floor = [int(x) for x in exact]
    rem = max(0, min(target - sum(floor), n))
    order = sorted(range(n), key=lambda i: exact[i] - floor[i], reverse=True)
    for i in range(rem):
        floor[order[i]] += 1
    return floor


def _reconcile_shift_slots(conn, line_id, table, data, date, shift_name):
    """Return {db_column_prefix: {"ok","ng","actual"}} reconciled to the PLC
    register for a COMPLETED shift, or None to leave the raw buckets alone."""
    if not data.get("is_shift_completed"):
        return None                       # live shift still moving — don't touch
    reg_ok = int(data.get("ok_count") or 0)
    reg_ng = int(data.get("ng_count") or 0)
    if reg_ok + reg_ng <= 0:
        return None                       # near-empty / test shift
    ckey = (line_id, str(date), shift_name)
    if ckey in _RECON_CACHE:
        return _RECON_CACHE[ckey]

    c2 = dict_cursor(conn)
    c2.execute(
        "SELECT slot_label, db_column_prefix, start_time, end_time "
        "FROM mes_hourly_slots WHERE line_id = %s AND shift_name = %s "
        "ORDER BY slot_order",
        (line_id, shift_name))
    slots = c2.fetchall() or []
    if not slots:
        return None

    c2.execute(
        f"SELECT ts, COALESCE(is_ng, false) AS is_ng "
        f"FROM {table}_ct_log WHERE record_date = %s AND shift_name = %s",
        (date, shift_name))
    rows = c2.fetchall() or []
    ct = len(rows)
    reg_tot = reg_ok + reg_ng
    # CORRUPTION GUARD — if the ct_log row-count is wildly off the register
    # (massive dup rows or a garbage/near-empty shift), don't trust its
    # distribution; keep the raw stored buckets instead.
    if ct > reg_tot * 1.2 or ct < reg_tot * 0.5:
        _RECON_CACHE[ckey] = None
        return None

    prefixes = [s["db_column_prefix"] for s in slots]
    acc = {p: {"ok": 0, "ng": 0} for p in prefixes}

    def _slot_of(ts):
        tt = ts.time()
        for s in slots:
            st, en = s["start_time"], s["end_time"]
            if st is None or en is None:
                continue
            if st <= en:
                if st <= tt < en:
                    return s["db_column_prefix"]
            else:                          # window wraps past midnight
                if tt >= st or tt < en:
                    return s["db_column_prefix"]
        return None

    for r in rows:
        p = _slot_of(r["ts"])
        if p:
            acc[p]["ng" if r["is_ng"] else "ok"] += 1

    rok = _recon_distribute([acc[p]["ok"] for p in prefixes], reg_ok)
    rng = _recon_distribute([acc[p]["ng"] for p in prefixes], reg_ng)
    out = {}
    for i, p in enumerate(prefixes):
        out[p] = {"ok": rok[i], "ng": rng[i], "actual": rok[i] + rng[i]}
    _RECON_CACHE[ckey] = out
    return out


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

            # 2026-06-18 — reconcile the lossy hourly buckets to the PLC
            # register (completed shifts only) so the report's slot Total
            # matches the top KPI cards.  Read-only; on any failure _recon
            # is None and the raw stored buckets are returned unchanged.
            try:
                _recon = _reconcile_shift_slots(conn, line_id, table, data, date, shift_name)
            except Exception as _re:
                print(f"[HISTORICAL] reconcile skipped: {_re}")
                _recon = None

            if hour_slot:
                # Get the column prefix for this slot
                cur.execute("""
                    SELECT db_column_prefix FROM mes_hourly_slots
                    WHERE line_id = %s AND slot_label = %s
                """, (line_id, hour_slot))
                slot = cur.fetchone()
                if slot:
                    prefix = slot["db_column_prefix"]
                    rv = _recon.get(prefix) if _recon else None
                    if rv:
                        plan = data.get(f"{prefix}_plan", 0) or 0
                        return {
                            "slot": hour_slot,
                            "plan": plan,
                            "actual": rv["actual"],
                            "variance": rv["actual"] - plan,
                            "ok": rv["ok"],
                            "ng": rv["ng"],
                        }
                    return {
                        "slot": hour_slot,
                        "plan": data.get(f"{prefix}_plan", 0) or 0,
                        "actual": data.get(f"{prefix}_actual", 0) or 0,
                        "variance": data.get(f"{prefix}_variance", 0) or 0,
                        "ok": data.get(f"{prefix}_ok", 0) or 0,
                        "ng": data.get(f"{prefix}_ng", 0) or 0
                    }
                else:
                    return {"error": f"Slot {hour_slot} not found for line {line_id}"}

            # Return all data for the shift
            result = {}
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    result[key] = value if value is not None else 0
                else:
                    result[key] = value

            # Apply reconciled per-slot counts so the slot table + its Total
            # line up with the register KPIs (top == bottom == truth).
            if _recon:
                for prefix, rv in _recon.items():
                    plan = result.get(f"{prefix}_plan", 0) or 0
                    result[f"{prefix}_ok"] = rv["ok"]
                    result[f"{prefix}_ng"] = rv["ng"]
                    result[f"{prefix}_actual"] = rv["actual"]
                    result[f"{prefix}_variance"] = rv["actual"] - plan
                result["_reconciled"] = True

            # 2026-06-19 — OT slot manual-plan overlay.  The collector creates the
            # OT hour bucket with plan_pieces=0, so its stored _plan is always 0.
            # The production admin sets a manual OT target (mes_shift_configs.
            # ot_plan); overlay it here so the OT slot shows a real plan / variance
            # / efficiency — just like a normal shift slot.  Compute-on-read only
            # (collector never writes the OT plan).
            try:
                cur.execute(
                    "SELECT ot_plan FROM mes_shift_configs WHERE line_id = %s AND shift_name = %s",
                    (line_id, shift_name),
                )
                _otp_row = cur.fetchone()
                _ot_plan = (_otp_row or {}).get("ot_plan")
                if _ot_plan:
                    cur.execute(
                        "SELECT db_column_prefix FROM mes_hourly_slots "
                        "WHERE line_id = %s AND shift_name = %s",
                        (line_id, shift_name),
                    )
                    for _s in cur.fetchall():
                        _p = _s.get("db_column_prefix") or ""
                        if _p.endswith("_ot") and f"{_p}_actual" in result:
                            _act = result.get(f"{_p}_actual", 0) or 0
                            result[f"{_p}_plan"]     = _ot_plan
                            result[f"{_p}_variance"] = _act - _ot_plan
                    result["ot_plan"] = _ot_plan
            except Exception as _oe:
                print(f"[HISTORICAL] OT plan overlay skipped: {_oe}")

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

    2026-05-21 — COLLECTOR-WRITE PROTECTION.
    Single-writer guarantee for ync_dashboard_complete: only the
    collector engine may write counts (ok/ng/actual) for live or
    in-progress shifts.  With up to 25 frontends potentially on the
    LAN, an accidental Excel import on today's date would race the
    collector and silently overwrite live counts (operator on shop
    floor sees their plan/actual numbers reset to whatever the Excel
    file said).  Three-layer defense:
       1. Reject record_date >= CURRENT_DATE  (today/future = collector)
       2. Reject if the target row has is_shift_completed = FALSE
          (shift still rolling, collector still writing)
       3. Audit every accepted/blocked attempt with admin id
    Operator-only / non-admin frontends already can't reach this
    endpoint (require_admin Depends), so the practical risk is two
    admins importing different files at the same time — the date guard
    blocks that 100 %.
    """
    from datetime import date as _date, datetime as _dt
    line_id = body.get("line_id")
    shift_name = body.get("shift_name")
    record_date = body.get("record_date")
    data = body.get("data", [])

    # ── Layer 1: Date guard ───────────────────────────────────────
    try:
        rd = _date.fromisoformat(str(record_date)) if record_date else None
    except (TypeError, ValueError):
        raise HTTPException(400, f"Invalid record_date: {record_date!r}")
    if not rd:
        raise HTTPException(400, "record_date is required")
    today = _date.today()
    if rd >= today:
        # Audit the blocked attempt so we can see who tried what.
        try:
            with get_conn() as _aconn:
                _ac = _aconn.cursor()
                _ac.execute(
                    "INSERT INTO mes_audit_log (action, entity_type, entity_id, "
                    "details, user_id, username, ip_address) "
                    "VALUES ('IMPORT_BLOCKED', 'dashboard_table', %s, %s, %s, %s, %s)",
                    (line_id,
                     f"Excel import blocked for date {rd} (today={today}); "
                     f"shift={shift_name}; rows={len(data)}",
                     getattr(admin, "id", None),
                     getattr(admin, "username", None) or getattr(admin, "name", None),
                     None),
                )
                _aconn.commit()
        except Exception:
            pass    # audit is best-effort, don't break the rejection path
        raise HTTPException(
            409,
            f"Cannot import {rd}: collector owns today's & future data. "
            f"Excel import is for historical backfill only (date < {today}).",
        )

    with get_conn() as conn:
        cur = dict_cursor(conn)

        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Line not found")
        table = row["db_table_name"]

        cur.execute(f"""
            SELECT id, is_shift_completed FROM {table}
            WHERE record_date = %s AND shift_name = %s
        """, (record_date, shift_name))
        existing = cur.fetchone()

        # ── Layer 2: Shift-in-progress guard ──────────────────────
        if existing and not existing.get("is_shift_completed", True):
            try:
                ac = conn.cursor()
                ac.execute(
                    "INSERT INTO mes_audit_log (action, entity_type, entity_id, "
                    "details, user_id, username, ip_address) "
                    "VALUES ('IMPORT_BLOCKED', 'dashboard_table', %s, %s, %s, %s, %s)",
                    (line_id,
                     f"Excel import blocked: shift row id={existing['id']} "
                     f"(date={rd} shift={shift_name}) is_shift_completed=false "
                     f"— collector still writing.",
                     getattr(admin, "id", None),
                     getattr(admin, "username", None) or getattr(admin, "name", None),
                     None),
                )
                conn.commit()
            except Exception:
                pass
            raise HTTPException(
                409,
                f"Shift {shift_name} on {rd} is still in progress "
                f"(is_shift_completed=false) — collector is currently writing. "
                f"Import only after the shift is closed.",
            )

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

        # ── Audit the successful import ────────────────────────────
        try:
            ac = conn.cursor()
            ac.execute(
                "INSERT INTO mes_audit_log (action, entity_type, entity_id, "
                "details, user_id, username, ip_address) "
                "VALUES ('IMPORT_OK', 'dashboard_table', %s, %s, %s, %s, %s)",
                (line_id,
                 f"Excel import accepted: date={rd} shift={shift_name} "
                 f"rows={len(data)} ok_count={ok_count} ng_count={ng_count} "
                 f"shift_row_id={shift_id}",
                 getattr(admin, "id", None),
                 getattr(admin, "username", None) or getattr(admin, "name", None),
                 None),
            )
        except Exception:
            pass    # don't fail the import just because audit table is missing
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
    # 2026-05-29 - Early return when CMS integration is disabled.
    # MES can run completely standalone; video is the only CMS coupling.
    if not CMS_INTEGRATION_ENABLED:
        raise HTTPException(404, "Video integration disabled (set CYCLE_VIDEO_BASE_URL in .env to enable)")

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

        # 2026-05-27 — TIME-WINDOW FIRST (operator: "ek video sab pe
        # chal rhi h").  When 30 consecutive cycles share one part_code
        # (scanner stuck / test pattern), the by-part MP4 returns the
        # SAME file for every click.  Switched to time-window-based
        # extraction as the PRIMARY path: each cycle's `ts` produces
        # a unique slice from the rolling TS file.  By-part is kept
        # as last-resort fallback (only used if ts lookup fails).
        cur.execute(
            f"SELECT ts, ct_value, part_code, is_ng FROM {tbl_log} "
            f"WHERE record_date = %s {shift_clause} AND cycle_seq = %s "
            f"ORDER BY ts DESC LIMIT 1",
            params,
        )
        hit = cur.fetchone()
        part_code = ((hit.get("part_code") if hit else "") or "").strip()

        # 2026-05-28 — Compute NG offset for shared-video seek.  When
        # multiple NGs happen within one L108 cycle, they all share the
        # same by-part MP4.  Frontend uses this offset to seek the
        # <video> tag to the exact NG moment within the larger MP4.
        # Offset = NG.ts - previous_OK.ts (gives seconds into the cycle).
        # Only meaningful for by-part fallback; time-window clips are
        # already tight to the NG itself.
        ng_offset_sec = 0.0
        if hit and hit.get("ts") and hit.get("is_ng"):
            cur.execute(
                f"SELECT ts FROM {tbl_log} "
                f"WHERE record_date = %s AND is_ng = false AND ts < %s "
                f"ORDER BY ts DESC LIMIT 1",
                (record_date, hit["ts"]),
            )
            prev_ok = cur.fetchone()
            if prev_ok and prev_ok.get("ts"):
                try:
                    ng_offset_sec = (hit["ts"] - prev_ok["ts"]).total_seconds()
                    if ng_offset_sec < 0:
                        ng_offset_sec = 0.0
                except Exception:
                    ng_offset_sec = 0.0

        # Always try time-window first when ts is available.
        tr = hit  # alias
        if tr and tr.get("ts"):
            # 2026-05-29 (revised) - Cap REMOVED per operator instruction.
            # Earlier "60s cap" hid the real issue: a 310s cycle was
            # playing as a 60s clip and the operator couldn't see the
            # full L108-to-L108 window.  Operator spec: "video ki length
            # cycle ki length se match honi chaiye" — clip duration must
            # equal the cycle duration EXACTLY, no padding, no cap.
            # If the underlying TS file doesn't have that many seconds
            # available (camera was down mid-cycle), ffmpeg will simply
            # extract whatever IS there — better a partial real clip
            # than a hard-coded 60s slice.
            from datetime import timedelta as _td
            _ts_end       = tr["ts"]
            _cycle_dur_s  = float(tr.get("ct_value") or 0) or 10.0
            _clip_dur     = max(3.0, _cycle_dur_s + 1.0)
            _ts_start     = _ts_end - _td(seconds=_clip_dur)
            # 2026-07-01 — Resolve THIS line's OWN main camera instead of a
            # hardcoded default.  The old `cam_panasonic_default` made EVERY
            # line pull the .115 (YNC) feed — e.g. YCA-SS Final Inspection
            # (.96) wrongly showed .115 instead of its bound .116 camera.
            # Prefer the main PLC's nf2_camera_id (pinned via
            # mes_lines.dashboard_plc_id, else parent_plc_id IS NULL); fall
            # back to the main PLC's IP so the CMS resolves it via
            # camera_config_bindings.  Never another line's camera.
            cur.execute(
                "SELECT NULLIF(TRIM(pc.nf2_camera_id),'') AS cam, pc.plc_ip "
                "FROM mes_plc_configs pc JOIN mes_lines l ON l.id = pc.line_id "
                "WHERE pc.line_id = %s AND ("
                "  (l.dashboard_plc_id IS NOT NULL AND pc.id = l.dashboard_plc_id) "
                "  OR (l.dashboard_plc_id IS NULL AND pc.parent_plc_id IS NULL)) "
                "ORDER BY (pc.parent_plc_id IS NULL) DESC LIMIT 1",
                (line_id,),
            )
            _cr  = cur.fetchone() or {}
            _cam = (_cr.get("cam") or "").strip()
            _sel = (f"camera_id={_cam}" if _cam
                    else f"plc_ip={(_cr.get('plc_ip') or '').strip()}")
            _qs = (f"{_sel}"
                   f"&ts_start={_ts_start.isoformat()}"
                   f"&ts_end={_ts_end.isoformat()}")
            upstream = f"{CYCLE_VIDEO_BASE_URL}/api/submachine/clip?{_qs}"
            fwd = {}
            try:
                rng = request.headers.get("range") if request else None
                if rng:
                    fwd["Range"] = rng
            except Exception:
                pass
            try:
                r = requests.get(upstream, headers=fwd, stream=True, timeout=15)
            except Exception as exc:
                # Network blip — fall through to by-part if part_code exists.
                r = None
                _last_exc = exc
            if r is not None and r.status_code < 400:
                resp_headers = {}
                for h in ("Content-Type", "Content-Length",
                          "Content-Range", "Accept-Ranges"):
                    if h in r.headers:
                        resp_headers[h] = r.headers[h]
                resp_headers.setdefault("Accept-Ranges", "bytes")
                # 2026-05-28 — Source signal + zero offset (time-window
                # clips are already tight to the NG moment, no seek needed)
                resp_headers["X-Video-Source"] = "time-window"
                resp_headers["X-Video-Offset"] = "0"
                resp_headers["Access-Control-Expose-Headers"] = "X-Video-Source, X-Video-Offset"
                print(f"[CYCLE-VIDEO] line={line_id} cycle_seq={cycle_seq} "
                      f"time-window clip "
                      f"{_ts_start.isoformat()} -> {_ts_end.isoformat()}",
                      flush=True)
                return StreamingResponse(
                    r.iter_content(chunk_size=64 * 1024),
                    status_code=r.status_code,
                    media_type=resp_headers.get("Content-Type", "video/mp4"),
                    headers=resp_headers,
                )
            # Time-window failed (TS file missing, range not satisfied,
            # camera dead) — fall through to by-part MP4 as last resort.
            print(f"[CYCLE-VIDEO] line={line_id} cycle_seq={cycle_seq} "
                  f"time-window failed (status={getattr(r,'status_code','EXC')}) "
                  f"— falling back to by-part MP4", flush=True)

        if not part_code:
            raise HTTPException(404, "No ts recorded AND no part_code — cannot resolve video")

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
    # 2026-05-28 — by-part fallback signal.  Multiple NGs in one L108
    # cycle share this MP4 — frontend seeks to ng_offset_sec so each
    # NG opens at its own moment within the larger shared video.
    resp_headers["X-Video-Source"] = "by-part"
    resp_headers["X-Video-Offset"] = str(round(ng_offset_sec, 2))
    resp_headers["Access-Control-Expose-Headers"] = "X-Video-Source, X-Video-Offset"
    print(f"[CYCLE-VIDEO] line={line_id} cycle_seq={cycle_seq} "
          f"by-part fallback offset={ng_offset_sec:.1f}s pc={part_code}",
          flush=True)

    return StreamingResponse(
        r.iter_content(chunk_size=64 * 1024),
        status_code=r.status_code,
        media_type=resp_headers.get("Content-Type", "video/mp4"),
        headers=resp_headers,
    )


# 2026-06-01 — ADDITIVE per-cycle ARCHIVE playback endpoint.
# Serves a PRE-SAVED clip from the local 2-day video archive
# (D:\VideoArchive), written by the standalone video_archiver.py
# daemon and located via the additive mes_video_archive index table.
# This is what lets the operator replay YESTERDAY's cycles: the live
# /cycle-video path can only cut while the rolling TS still exists (TS
# is wiped at every shift boundary), so older cycles 404 there.
#
# ZERO-REGRESSION — read-only.  Serves a LOCAL file: does NOT touch
# NF2/CMS, the collector, or the existing /cycle-video route.  Returns
# 404 when no clip exists so the frontend transparently falls back to
# the live cut — nothing breaks if the archive has nothing for a cycle.
VIDEO_ARCHIVE_ROOT = os.environ.get("VIDEO_ARCHIVE_ROOT", r"D:\VideoArchive")


def _serve_local_video(path: str, request: Request, extra_headers=None):
    """Stream a local mp4 with HTTP Range support (HTML5 <video> seek).

    Starlette 0.36.x FileResponse does not honour Range, so we emit
    206 partial-content by hand — identical seek behaviour to the live
    /cycle-video proxy (which forwards Range to NF2 and gets back 206)."""
    file_size = os.path.getsize(path)
    headers = {
        "Accept-Ranges": "bytes",
        "X-Video-Source": "archive",
        "X-Video-Offset": "0",
        "Access-Control-Expose-Headers": "X-Video-Source, X-Video-Offset",
    }
    if extra_headers:
        headers.update(extra_headers)

    range_header = None
    try:
        range_header = request.headers.get("range") if request is not None else None
    except Exception:
        range_header = None

    if range_header and range_header.strip().lower().startswith("bytes="):
        try:
            spec = range_header.split("=", 1)[1].split(",")[0]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s.strip() else 0
            end = int(end_s) if end_s.strip() else file_size - 1
            if start < 0 or start > end or start >= file_size:
                raise ValueError("unsatisfiable range")
            end = min(end, file_size - 1)
        except ValueError:
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{file_size}",
                                     "Accept-Ranges": "bytes"})
        length = end - start + 1

        def _iter_range():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(_iter_range(), status_code=206,
                                 media_type="video/mp4", headers=headers)

    def _iter_full():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(_iter_full(), status_code=200,
                             media_type="video/mp4", headers=headers)


@router.get("/{line_id}/archive-video")
def get_archive_video(
    line_id: int,
    cycle_seq: int = Query(..., description="cycle_seq from _ct_log"),
    date:      Optional[str] = Query(None, description="YYYY-MM-DD (defaults to today)"),
    shift:     Optional[str] = Query(None),
    machine_seq: int = Query(0, description="0 = main line (Final Inspection); 1..6 = sub machine"),
    token:     Optional[str] = Query(None, description="JWT fallback for <video src=...>"),
    request:   Request = None,
):
    """
    Serve a PRE-SAVED per-cycle clip from the local 2-day video archive
    (D:\\VideoArchive), located via the mes_video_archive index table.

    Returns 404 when no archived clip exists for (date, shift, cycle_seq,
    machine_seq) so the frontend transparently falls back to the live
    /cycle-video cut.  Auth mirrors /cycle-video: a token is validated iff
    supplied, otherwise anonymous read is allowed (closed shop-floor LAN).
    """
    from auth import SECRET_KEY, ALGORITHM
    from jose import jwt as jose_jwt, JWTError as JoseJWTError

    jwt_token = token
    if request:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            jwt_token = auth_hdr[7:]
    if jwt_token:
        try:
            jose_jwt.decode(jwt_token, SECRET_KEY, algorithms=[ALGORITHM])
        except JoseJWTError:
            raise HTTPException(401, "Invalid or expired token")

    record_date = date or datetime.now().strftime("%Y-%m-%d")

    with get_conn() as conn:
        cur = dict_cursor(conn)
        # Index table may not exist yet (archiver never ran) → "no clip".
        cur.execute("SELECT to_regclass('public.mes_video_archive') AS t")
        if not cur.fetchone()["t"]:
            raise HTTPException(404, "No video archive yet")

        params = [line_id, record_date, cycle_seq, machine_seq]
        shift_clause = ""
        if shift:
            shift_clause = "AND shift_name = %s"
            params.append(shift)
        cur.execute(
            "SELECT clip_path FROM mes_video_archive "
            "WHERE line_id = %s AND record_date = %s AND cycle_seq = %s "
            "AND machine_seq = %s AND status = 'ok' AND clip_path IS NOT NULL "
            f"{shift_clause} "
            "ORDER BY id DESC LIMIT 1",
            params,
        )
        row = cur.fetchone()

    if not row or not row.get("clip_path"):
        raise HTTPException(404, "No archived clip for this cycle")

    # Resolve + contain within the archive root (path-traversal guard).
    full = os.path.normpath(os.path.join(VIDEO_ARCHIVE_ROOT, row["clip_path"]))
    try:
        real_full = os.path.realpath(full)
        real_root = os.path.realpath(os.path.normpath(VIDEO_ARCHIVE_ROOT))
        if os.path.commonpath([real_full, real_root]) != real_root:
            raise HTTPException(404, "Archived clip path outside archive root")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "Bad archived clip path")
    if not os.path.isfile(full):
        raise HTTPException(404, "Archived clip missing on disk")

    print(f"[ARCHIVE-VIDEO] line={line_id} date={record_date} shift={shift} "
          f"cycle_seq={cycle_seq} mseq={machine_seq} -> {row['clip_path']}",
          flush=True)
    return _serve_local_video(full, request)


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
        # 2026-05-29 — Garbage-model guard.  D6048 reads occasionally
        # get polluted by ASCII bleed-through on the shared TCP socket
        # (e.g. part_code "00..." bleeds in as 0x3030 = 12336, or other
        # values like 173).  When current_model_number doesn't match
        # any known mapping for THIS line, fall back to the most-recent
        # VALID model from the per-line audit table.  Operator sees a
        # real model name on the wallboard even when the latest PLC
        # read was a one-off junk byte.  Collector is untouched —
        # the DB still records the raw value for forensic traceability.
        mnum = data.get("current_model_number")
        if mnum:
            # 2026-05-29 — strict per-line validity: a model is "real" for
            # this line ONLY if it exists in mes_model_mappings for THIS
            # line_id.  Other lines' bits in mes_py_model_master (e.g.
            # bit=20=YY8 which belongs to another line) must NOT pass
            # the gate — operator on YNC-SS would otherwise see a YY8
            # name when D6048 returns 20 as bleed-through.
            cur.execute(
                "SELECT 1 FROM mes_model_mappings "
                "WHERE line_id=%s AND model_number=%s",
                (line_id, mnum),
            )
            _valid = cur.fetchone() is not None
            # Legacy fallback: only if this line has NO mappings at all
            # (uncommon), accept any active py_model_master entry.
            if not _valid:
                cur.execute(
                    "SELECT 1 FROM mes_model_mappings WHERE line_id=%s LIMIT 1",
                    (line_id,),
                )
                _line_has_mappings = cur.fetchone() is not None
                if not _line_has_mappings:
                    cur.execute(
                        "SELECT 1 FROM mes_py_model_master "
                        "WHERE bit_number=%s AND is_active=true LIMIT 1",
                        (mnum,),
                    )
                    _valid = cur.fetchone() is not None
            if not _valid:
                # mnum is garbage.  Resolve table from mes_lines.
                try:
                    cur.execute(
                        "SELECT db_table_name FROM mes_lines WHERE id=%s",
                        (line_id,),
                    )
                    _r = cur.fetchone()
                    _audit_tbl = None
                    if _r and _r["db_table_name"]:
                        # Per-line per-machine audit table follows pattern
                        # mes_{line_code}_final_inspection.  Map common
                        # cases; fall back to mes_l6_final_inspection.
                        if line_id == 2:
                            _audit_tbl = "mes_l6_final_inspection"
                    if _audit_tbl:
                        cur.execute(
                            f"SELECT model_no, model_name FROM {_audit_tbl} "
                            f"WHERE shift_name=%s AND record_date=CURRENT_DATE "
                            f"  AND model_no IN (SELECT model_number "
                            f"                    FROM mes_model_mappings "
                            f"                    WHERE line_id=%s) "
                            f"ORDER BY ts DESC LIMIT 1",
                            (data.get("shift_name") or "A", line_id),
                        )
                        _last_good = cur.fetchone()
                        if _last_good and _last_good["model_no"]:
                            print(
                                f"[MODEL-FILTER] line={line_id} substituting "
                                f"garbage model {mnum} -> last valid "
                                f"{_last_good['model_no']} "
                                f"({_last_good['model_name']!r})",
                                flush=True,
                            )
                            mnum = _last_good["model_no"]
                            data["current_model_number"] = mnum
                            if _last_good["model_name"]:
                                data["current_model_name"] = _last_good["model_name"]
                except Exception as _e:
                    # Never let the filter break the response.
                    pass
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
                """SELECT ot_start_time, ot_end_time, ot_plan,
                          working_minutes, total_plan
                     FROM mes_shift_configs
                    WHERE line_id = %s AND shift_name = %s""",
                (line_id, data["shift_name"]),
            )
            ot_row = cur.fetchone()
            if ot_row:
                data["ot_start_time"] = str(ot_row["ot_start_time"])[:5] if ot_row["ot_start_time"] else None
                data["ot_end_time"]   = str(ot_row["ot_end_time"])[:5]   if ot_row["ot_end_time"]   else None
                data["ot_plan"]       = ot_row.get("ot_plan")   # manual OT target (pcs); None if unset
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
        # 2026-07-16 — keep is_production in sync with active_shifts so the
        # PLAN (which sums is_production shifts) automatically reflects which
        # shifts the line actually runs.  Un-ticking a shift in Active Shifts
        # now drops it from the plan; re-ticking restores it.  GAP shifts are
        # never touched (they stay non-production).
        if "active_shifts" in updates:
            active = [s.strip() for s in (updates["active_shifts"] or "").split(",") if s.strip()]
            conn.cursor().execute("""
                UPDATE mes_shift_configs
                   SET is_production = (shift_name = ANY(%s))
                 WHERE line_id = %s AND shift_name NOT LIKE 'GAP%%'
            """, (active, line_id))
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details,
                                       user_id, username)
            VALUES ('LINE_UPDATED', 'line', %s, %s, %s, %s)
        """, (line_id, str(updates), admin.get("id"), admin.get("username")))

    return {"ok": True, "message": "Line updated"}


@router.get("/{line_id}/model-ct")
def get_model_ct(line_id: int, user=Depends(get_current_user_optional)):
    """Per-model ideal cycle time for a line — drives the model-aware
    Fullscreen (plan/estimation follow the operator-selected model).
    Read-only; empty list when no per-model CT is configured."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT model_number, model_name, ideal_ct
            FROM mes_model_mappings
            WHERE line_id = %s AND ideal_ct IS NOT NULL
            ORDER BY model_number
        """, (line_id,))
        return [{"model_number": r["model_number"], "model_name": r["model_name"],
                 "ideal_ct": float(r["ideal_ct"])} for r in cur.fetchall()]


class ModelCTItem(BaseModel):
    model_number: int
    ideal_ct:     Optional[float] = None
    model_name:   Optional[str]   = None


@router.put("/{line_id}/model-ct")
def put_model_ct(line_id: int, body: List[ModelCTItem], admin=Depends(require_admin)):
    """Set per-model ideal cycle time (admin — Models tab, YWD-SS model-aware
    plan).  Upserts mes_model_mappings.ideal_ct per model_number.  ideal_ct
    None clears it (model falls back to the line CT)."""
    with get_conn() as conn:
        cur = conn.cursor()
        for it in body:
            ct = it.ideal_ct if (it.ideal_ct and it.ideal_ct > 0) else None
            cur.execute("""UPDATE mes_model_mappings SET ideal_ct = %s
                           WHERE line_id = %s AND model_number = %s""",
                        (ct, line_id, it.model_number))
            if cur.rowcount == 0 and it.model_name:
                cur.execute("""INSERT INTO mes_model_mappings (line_id, model_number, model_name, ideal_ct)
                               VALUES (%s, %s, %s, %s)""",
                            (line_id, it.model_number, it.model_name, ct))
    return {"ok": True, "count": len(body)}


def _model_group(name: str) -> str:
    """Collapse concrete model names into a CT-band group.  INR RH / INR LH
    share one cycle time (25.7s) so they count as one plan-rate; OTR is the
    other (14.42s).  Used only for the model-aware plan view (YWD-SS)."""
    n = (name or "").upper()
    if n.startswith("INR"): return "INR"
    if n.startswith("OTR"): return "OTR"
    return (name or "—").strip()


@router.get("/{line_id}/model-plan")
def get_model_plan(line_id: int,
                   date: Optional[str] = Query(None),
                   shift: Optional[str] = Query(None),
                   user=Depends(get_current_user_optional)):
    """Model-aware plan for a line, derived READ-ONLY from the per-cycle CT
    log (nothing on the collector changes).

    Each cycle in <db_table_name>_ct_log is labelled to the nearest configured
    per-model ideal CT (mes_model_mappings.ideal_ct) — INR ~25.7s vs OTR
    ~14.42s cluster ~2x apart, so classification is robust.  A light
    majority-window smoother removes single-cycle flips.  From the resulting
    model segments we compute: changeover events, a model-aware hourly plan
    (each slot's working-time split by which model actually ran, ÷ that
    model's CT), and the changeover's impact on the shift plan.

    Fully reload-proof (reads persisted ct_log), scoped to whichever line the
    caller asks for — the dedicated FullscreenYWD screen uses it for YWD-SS."""
    rec_date = date or _date.today().isoformat()

    with get_conn() as conn:
        cur = dict_cursor(conn)
        _check_operator_access(user, line_id, conn)

        # line + ct_log table
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        lr = cur.fetchone()
        if not lr:
            raise HTTPException(404, "Line not found")
        ct_tbl = f"{lr['db_table_name']}_ct_log"
        cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{ct_tbl}",))
        if not cur.fetchone()["reg"]:
            return {"enabled": False, "reason": "no ct_log", "segments": [],
                    "changeovers": [], "hourly": [], "totals": None}

        # configured per-model CT → distinct bands {ct, group}
        cur.execute("""SELECT model_name, ideal_ct FROM mes_model_mappings
                       WHERE line_id = %s AND ideal_ct IS NOT NULL""", (line_id,))
        band_by_ct = {}
        for r in cur.fetchall():
            ct = round(float(r["ideal_ct"]), 2)
            band_by_ct.setdefault(ct, _model_group(r["model_name"]))
        bands = [{"ct": ct, "group": g} for ct, g in band_by_ct.items()]
        if len(bands) < 2:
            return {"enabled": False, "reason": "need >=2 per-model CT", "segments": [],
                    "changeovers": [], "hourly": [], "totals": None}
        ct_of_group = {}
        for b in bands:
            ct_of_group.setdefault(b["group"], b["ct"])

        # resolve shift (default = shift with most cycles today)
        if not shift:
            cur.execute(f"""SELECT shift_name, count(*) c FROM {ct_tbl}
                            WHERE record_date = %s GROUP BY shift_name
                            ORDER BY c DESC LIMIT 1""", (rec_date,))
            sr = cur.fetchone()
            shift = sr["shift_name"] if sr else "A"

        # hourly slots for this shift (working_minutes excludes breaks)
        cur.execute("""SELECT slot_label, start_time, end_time, crosses_midnight,
                              working_minutes, plan_pieces, db_column_prefix, slot_order
                       FROM mes_hourly_slots
                       WHERE line_id = %s AND shift_name = %s
                       ORDER BY slot_order""", (line_id, shift))
        slots = cur.fetchall()

        # per-cycle CT stream for the day+shift
        cur.execute(f"""SELECT ts, ct_value, is_ng FROM {ct_tbl}
                        WHERE record_date = %s AND shift_name = %s
                          AND ct_value IS NOT NULL AND ct_value > 0
                        ORDER BY ts""", (rec_date, shift))
        rows = cur.fetchall()

    # ── absolute slot bounds ──
    base = datetime.fromisoformat(rec_date)
    def _slot_bounds(s):
        st, en = s["start_time"], s["end_time"]
        ds = base.replace(hour=st.hour, minute=st.minute, second=0, microsecond=0)
        de = base.replace(hour=en.hour, minute=en.minute, second=0, microsecond=0)
        if de <= ds:
            de += timedelta(days=1)
        return ds, de

    now = datetime.now()
    shift_end = max((_slot_bounds(s)[1] for s in slots), default=now)
    shift_start = min((_slot_bounds(s)[0] for s in slots), default=now)

    # ── classify each cycle to nearest band, then majority-window smooth ──
    def _nearest(v):
        return min(bands, key=lambda b: abs(v - b["ct"]))["group"]
    raw = [(r["ts"], _nearest(float(r["ct_value"])), bool(r["is_ng"])) for r in rows]
    W = 2  # +/-2 cycle window
    smoothed = []
    for i in range(len(raw)):
        window = [raw[j][1] for j in range(max(0, i-W), min(len(raw), i+W+1))]
        # majority vote
        g = max(set(window), key=window.count)
        smoothed.append((raw[i][0], g))

    # ── run-length encode into segments ──
    segments = []
    for ts, g in smoothed:
        if segments and segments[-1]["group"] == g:
            segments[-1]["end_ts"] = ts
            segments[-1]["cycles"] += 1
        else:
            segments.append({"group": g, "ct": ct_of_group.get(g),
                             "start_ts": ts, "end_ts": ts, "cycles": 1})

    # ── drop spurious short runs ──
    # End-of-shift model-test cycles and MC frame-desync blips create tiny
    # 1-5 cycle flips that would otherwise be reported as real changeovers.
    # Absorb any run below MIN_SEG cycles into the run before it (relabel to
    # that model), then re-merge adjacent same-model runs.  A genuine model
    # change lasts far longer than this, so only noise is removed.
    MIN_SEG = 15
    _clean = []
    for seg in segments:
        if _clean and (seg["cycles"] < MIN_SEG or _clean[-1]["group"] == seg["group"]):
            _clean[-1]["end_ts"] = seg["end_ts"]
            _clean[-1]["cycles"] += seg["cycles"]
        else:
            _clean.append(dict(seg))
    # a short FIRST run gets folded into the next real run
    if len(_clean) >= 2 and _clean[0]["cycles"] < MIN_SEG:
        _clean[1]["start_ts"] = _clean[0]["start_ts"]
        _clean[1]["cycles"] += _clean[0]["cycles"]
        _clean.pop(0)
    segments = _clean

    # first segment is assumed to have run from shift start
    if segments:
        segments[0]["start_ts"] = min(segments[0]["start_ts"], shift_start)

    cur_group = segments[-1]["group"] if segments else None

    # ── effective full-shift timeline (past segments + projected current model) ──
    eff = [dict(g=s["group"], ct=s["ct"],
                a=s["start_ts"], b=segments[i+1]["start_ts"] if i+1 < len(segments) else min(now, shift_end))
           for i, s in enumerate(segments)]
    if cur_group and now < shift_end:
        eff.append(dict(g=cur_group, ct=ct_of_group.get(cur_group), a=now, b=shift_end))

    def _overlap(a1, b1, a2, b2):
        lo, hi = max(a1, a2), min(b1, b2)
        return max(0.0, (hi - lo).total_seconds())

    # ── changeovers (group flips) ──
    changeovers = []
    for i in range(1, len(segments)):
        if segments[i]["group"] != segments[i-1]["group"]:
            changeovers.append({"from": segments[i-1]["group"], "to": segments[i]["group"],
                                "at": segments[i]["start_ts"].strftime("%H:%M"),
                                "ct": segments[i]["ct"]})

    # ── model-aware hourly ──
    hourly = []
    plan_full = 0.0
    by_group_work = {}   # group -> working_seconds
    for s in slots:
        ds, de = _slot_bounds(s)
        wall = (de - ds).total_seconds() or 1.0
        wm_sec = (float(s["working_minutes"] or 0)) * 60.0
        slot_plan = 0.0
        grp_overlap = {}
        for e in eff:
            ov = _overlap(ds, de, e["a"], e["b"])
            if ov <= 0 or not e["ct"]:
                continue
            frac = ov / wall
            wsec = wm_sec * frac
            slot_plan += wsec / e["ct"]
            grp_overlap[e["g"]] = grp_overlap.get(e["g"], 0.0) + ov
            by_group_work[e["g"]] = by_group_work.get(e["g"], 0.0) + wsec
        # actual OK cycles that fall in this slot
        act = sum(1 for r in rows if ds <= r["ts"] < de and not r["is_ng"])
        dom = max(grp_overlap, key=grp_overlap.get) if grp_overlap else None
        # if no model data at all for this slot, fall back to the flat plan
        if slot_plan <= 0 and (s["plan_pieces"] or 0):
            slot_plan = float(s["plan_pieces"])
        plan_full += slot_plan
        hourly.append({
            "slot_label": s["slot_label"],
            "start": s["start_time"].strftime("%H:%M"),
            "end":   s["end_time"].strftime("%H:%M"),
            "working_minutes": s["working_minutes"],
            "group": dom,
            "plan_flat": s["plan_pieces"],
            "plan": round(slot_plan),
            "actual": act,
            "started": ds <= now,
        })

    # ── totals + changeover impact ──
    total_work_sec = sum((float(s["working_minutes"] or 0)) * 60.0 for s in slots)
    # plan up to now = sum of each slot's past-portion (segments ∩ [slot_start, now])
    plan_now = 0.0
    for s in slots:
        ds, de = _slot_bounds(s)
        if ds > now:
            continue
        wall = (de - ds).total_seconds() or 1.0
        wm_sec = (float(s["working_minutes"] or 0)) * 60.0
        cap = min(de, now)
        for e in eff:
            ov = _overlap(ds, cap, e["a"], e["b"])
            if ov > 0 and e["ct"]:
                plan_now += (wm_sec * ov / wall) / e["ct"]

    actual_ok = sum(1 for r in rows if not r["is_ng"])
    first_group = segments[0]["group"] if segments else cur_group
    baseline_ct = ct_of_group.get(first_group)
    baseline = (total_work_sec / baseline_ct) if baseline_ct else 0
    if_all = {g: round(total_work_sec / c) for g, c in ct_of_group.items()}
    by_group = [{"group": g, "ct": ct_of_group.get(g),
                 "working_min": round(sec/60), "plan": round(sec/ct_of_group[g]) if ct_of_group.get(g) else 0}
                for g, sec in by_group_work.items()]

    return {
        "enabled": True,
        "date": rec_date,
        "shift": shift,
        "bands": bands,
        "current_group": cur_group,
        "current_ct": ct_of_group.get(cur_group) if cur_group else None,
        "segments": [{"group": s["group"], "ct": s["ct"],
                      "start": s["start_ts"].strftime("%H:%M"),
                      "end": (segments[i+1]["start_ts"] if i+1 < len(segments) else min(now, shift_end)).strftime("%H:%M"),
                      "cycles": s["cycles"]} for i, s in enumerate(segments)],
        "changeovers": changeovers,
        "hourly": hourly,
        "totals": {
            "actual": actual_ok,
            "plan_now": round(plan_now),
            "plan_full": round(plan_full),
            "variance": actual_ok - round(plan_now),
            "baseline_single": {"group": first_group, "plan": round(baseline)},
            "if_all": if_all,
            "by_group": by_group,
            "impact": round(plan_full - baseline),
        },
    }


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
        _ensure_register_count_schema(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM mes_lines WHERE id = %s", (line_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Line not found")
        # 2026-05-29 — guard against a second main-PLC row for the same line.
        # Background: the admin form was inadvertently POSTing to this endpoint
        # (instead of PUT'ing the existing row), creating a duplicate main
        # row that silently overrode register-mode config with the form's
        # bit-mode defaults.  Reject the request loudly — caller should
        # UPDATE the existing row, not insert another.  Sub-machines (rows
        # with parent_plc_id NOT NULL) are unaffected and may add freely.
        if body.parent_plc_id is None:
            cur.execute("""
                SELECT id FROM mes_plc_configs
                WHERE line_id = %s AND parent_plc_id IS NULL
                LIMIT 1
            """, (line_id,))
            existing_main = cur.fetchone()
            if existing_main:
                raise HTTPException(
                    409,
                    f"Line already has a main PLC (id={existing_main['id']}). "
                    f"Edit it via PUT /lines/{line_id}/machines/{existing_main['id']} "
                    f"instead of POSTing a new one."
                )
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
                 count_mode, ok_data_register, ng_data_register, shift_reset_bit,
                 parent_plc_id, nf2_camera_id, machine_seq,
                 sa_enabled, sa_fetch_bit,
                 sa_part_code_addr, sa_part_code_len,
                 sa_data_addr, sa_data_len,
                 sa_time_addr, sa_time_len,
                 sa_register_names, sa_register_scales,
                 is_bottleneck,
                 lc_result_reg, lc_value_reg, qr_reg, reject_bit)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s, %s,%s, %s,%s, %s,%s, %s,%s, %s,
                    %s,%s,%s,%s)
            RETURNING *
        """, (line_id, body.machine_name, body.plc_ip, body.plc_port, body.protocol,
              body.ok_bit_address, body.ng_bit_address, body.status_address, body.model_address,
              body.sensor_ok_address, body.process_seq_address, body.override_address,
              body.ideal_cycle_time, body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
              (body.count_mode or "bit"), body.ok_data_register, body.ng_data_register, body.shift_reset_bit,
              body.parent_plc_id, body.nf2_camera_id, body.machine_seq,
              bool(body.sa_enabled), body.sa_fetch_bit,
              body.sa_part_code_addr, body.sa_part_code_len,
              body.sa_data_addr, body.sa_data_len,
              body.sa_time_addr, body.sa_time_len,
              sa_names_param, sa_scales_param,
              bool(body.is_bottleneck),
              (body.lc_result_reg or None), (body.lc_value_reg or None),
              (body.qr_reg or None), (body.reject_bit or None)))
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
        _ensure_register_count_schema(conn)
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
                count_mode=%s, ok_data_register=%s, ng_data_register=%s, shift_reset_bit=%s,
                parent_plc_id=%s, nf2_camera_id=%s, machine_seq=%s,
                sa_enabled=%s, sa_fetch_bit=%s,
                sa_part_code_addr=%s, sa_part_code_len=%s,
                sa_data_addr=%s, sa_data_len=%s,
                sa_time_addr=%s, sa_time_len=%s,
                sa_register_names=%s, sa_register_scales=%s,
                is_bottleneck=%s,
                lc_result_reg=%s, lc_value_reg=%s, qr_reg=%s, reject_bit=%s,
                updated_at=NOW()
            WHERE id=%s
        """, (body.machine_name, body.plc_ip, body.plc_port, body.protocol,
              body.ok_bit_address, body.ng_bit_address, body.status_address, body.model_address,
              body.sensor_ok_address, body.process_seq_address, body.override_address,
              body.ideal_cycle_time, body.max_allowed_cycle, body.ok_ng_pulse_min_gap,
              (body.count_mode or "bit"), body.ok_data_register, body.ng_data_register, body.shift_reset_bit,
              body.parent_plc_id, body.nf2_camera_id, body.machine_seq,
              bool(body.sa_enabled), body.sa_fetch_bit,
              body.sa_part_code_addr, body.sa_part_code_len,
              body.sa_data_addr, body.sa_data_len,
              body.sa_time_addr, body.sa_time_len,
              sa_names_param, sa_scales_param,
              bool(body.is_bottleneck),
              (body.lc_result_reg or None), (body.lc_value_reg or None),
              (body.qr_reg or None), (body.reject_bit or None),
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


def _ensure_register_count_schema(conn) -> None:
    """Idempotent migration for register-mirror counting (2026-05-30).

    Mirrors collector_engine._ensure_register_count_schema_collector for
    the columns this router writes.  Adds count_mode / ok_data_register /
    ng_data_register / shift_reset_bit to mes_plc_configs so add/update
    machine never 500s if the collector migration hasn't run yet (admin
    may save a machine before the collector is restarted).  Purely
    additive (ADD COLUMN IF NOT EXISTS) → bit-mode machines untouched,
    zero regression.  The mes_shift_count_archive table is owned and
    created by the collector; the API never writes it."""
    try:
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE mes_plc_configs
              ADD COLUMN IF NOT EXISTS count_mode        VARCHAR(20) DEFAULT 'bit',
              ADD COLUMN IF NOT EXISTS ok_data_register  VARCHAR(20),
              ADD COLUMN IF NOT EXISTS ng_data_register  VARCHAR(20),
              ADD COLUMN IF NOT EXISTS shift_reset_bit   VARCHAR(20)
        """)
        conn.commit()
        cur.close()
    except Exception as exc:
        try: conn.rollback()
        except Exception: pass
        print(f"[REG-COUNT] schema ensure failed (will retry on next call): {exc}")


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
    ot_plan:       Optional[int] = None   # manual OT target (pcs) for the shift's OT window

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

        # 4b. 2026-06-09 — Speed loss per slot.  Speed loss is NOT a status
        # (the line is RUNNING, just slower than ideal), so it never shows up
        # in the status-log walk above.  The dashboard's Loss-Distribution
        # card carries the shift total (loss_speed_seconds, collector-computed);
        # we split THAT total across slots in proportion to each slot's running
        # time (elapsed − downtime) so Σ(slot speed) == the card total EXACTLY
        # and busier slots carry more.  No per-cycle math.
        speed_total = 0
        try:
            cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
            _lr  = cur.fetchone()
            _tbl = _lr["db_table_name"] if _lr else None
            if _tbl:
                cur.execute(
                    f"SELECT loss_speed_seconds FROM {_tbl} "
                    f"WHERE record_date = %s AND shift_name = %s "
                    f"ORDER BY id DESC LIMIT 1", (target_date, shift))
                _sr = cur.fetchone()
                if _sr and _sr.get("loss_speed_seconds") is not None:
                    speed_total = int(_sr["loss_speed_seconds"])
        except Exception:
            speed_total = 0

        now_naive = _dt.now()
        running = []
        for idx, (slot, slot_st, slot_en) in enumerate(slot_windows):
            seg_end  = min(now_naive, slot_en)
            elapsed  = max(0.0, (seg_end - slot_st).total_seconds())
            downtime = sum(slot_loss[idx].values())   # other losses (speed still 0)
            running.append(max(0.0, elapsed - downtime))
        sum_run = sum(running)
        if speed_total > 0 and sum_run > 0:
            alloc = [int(speed_total * r / sum_run) for r in running]
            rem   = speed_total - sum(alloc)
            order = sorted(range(len(running)), key=lambda i: running[i], reverse=True)
            for k in range(int(rem)):
                if order:
                    alloc[order[k % len(order)]] += 1
            for idx in range(len(slot_loss)):
                slot_loss[idx]["loss_speed"] = alloc[idx]

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
                   ot_end_time::text   AS ot_end_time,
                   ot_plan
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
                "ot_plan":       r["ot_plan"],
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
            # Coerce ot_plan to a non-negative int (or NULL).  A blank field
            # from the UI arrives as None → stored NULL (no OT target set).
            ot_plan = entry.ot_plan
            if ot_plan is not None:
                try:
                    ot_plan = max(0, int(ot_plan))
                except (TypeError, ValueError):
                    ot_plan = None
            cur.execute("""
                UPDATE mes_shift_configs
                SET ot_start_time = %s,
                    ot_end_time   = %s,
                    ot_plan       = %s
                WHERE line_id = %s AND shift_name = %s
            """, (entry.ot_start_time or None, entry.ot_end_time or None,
                  ot_plan, line_id, entry.shift_name))
        conn.commit()
        cur.close()
    return {"ok": True}


@router.put("/{line_id}/ot-active")
def set_ot_active(line_id: int, body: OTActiveBody, user=Depends(get_current_user)):
    """Activate or deactivate OT for a specific shift on a line. Zone users and above."""
    if user.get("role") not in ("admin", "zone"):
        raise HTTPException(403, "Zone or Admin role required")
    shift = (body.shift or "").strip() or None
    with get_conn() as conn:
        cur = conn.cursor()
        # Validate: when activating, the shift must be a REAL production shift
        # of THIS line (not a GAP, not an arbitrary string).  Without this the
        # collector silently ignored a bad value (it guards with `in shifts`)
        # but we'd persist garbage in mes_lines.ot_active_shift.
        if shift is not None:
            cur.execute("""
                SELECT 1 FROM mes_shift_configs
                WHERE line_id = %s AND shift_name = %s
                  AND is_production = true AND shift_name NOT LIKE 'GAP%%'
            """, (line_id, shift))
            if not cur.fetchone():
                raise HTTPException(400, f"'{shift}' is not a production shift on this line")
        cur.execute(
            "UPDATE mes_lines SET ot_active_shift = %s, updated_at = NOW() WHERE id = %s",
            (shift, line_id),
        )
        conn.commit()
        cur.close()
    return {"ok": True, "ot_active_shift": shift}


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


# ============================================================
# CYCLE COMMENTS  (per-cycle operator/admin notes, keyed by part_code)
# ============================================================
# 2026-05-21 — Operator spec: jab Final Inspection (main PLC) ka
# cycle video floating box khule, uske neeche ek "Comments" panel ho
# jaha us specific cycle ke baare me notes likhe ja sake (reason for
# slow CT, NG cause, operator observation, supervisor remark, etc.).
# Lookup is by part_code (the unique barcode per cycle) so the same
# comments surface whether the user navigates via cycle_seq from the
# chart OR by typing the part_code into the search box.
#
# Table auto-creates on first hit so no manual migration step.
# All three endpoints scoped to a line_id; comments survive across
# shifts forever.

def _ensure_cycle_comments_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_cycle_comments (
            id          SERIAL PRIMARY KEY,
            line_id     INTEGER NOT NULL,
            part_code   TEXT    NOT NULL,
            comment     TEXT    NOT NULL,
            author      TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # Compound index on the lookup key — every read filters by both
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_cycle_comments_line_part
            ON mes_cycle_comments (line_id, part_code, created_at DESC);
    """)
    # 2026-06-08 — shift + machine columns so Comments History shows
    # "kis din ki konsi shift" + the machine name (Final Inspection / sub).
    # Auto-migrate; existing rows stay NULL (no backfill per operator).
    cur.execute("ALTER TABLE mes_cycle_comments ADD COLUMN IF NOT EXISTS shift_name   TEXT")
    cur.execute("ALTER TABLE mes_cycle_comments ADD COLUMN IF NOT EXISTS machine_id   INTEGER")
    cur.execute("ALTER TABLE mes_cycle_comments ADD COLUMN IF NOT EXISTS machine_name TEXT")
    cur.execute("ALTER TABLE mes_cycle_comments ADD COLUMN IF NOT EXISTS record_date  DATE")
    conn.commit()


def _current_shift(line_id, conn):
    """(shift_name, record_date) of the line's CURRENTLY-ACTIVE shift row —
    whatever shift the collector is on right now.  Stamped onto comments /
    NG remarks at save time (operator: 'current time se fetch').  Returns
    ('UNKNOWN', None) only if the line / shift row can't be resolved."""
    try:
        c = conn.cursor()
        c.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        r = c.fetchone()
        tbl = r[0] if r else None
        if not tbl:
            return ("UNKNOWN", None)
        c.execute(
            f"SELECT shift_name, record_date FROM {tbl} "
            f"WHERE is_shift_completed = false ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if not row:
            c.execute(
                f"SELECT shift_name, record_date FROM {tbl} "
                f"ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
        if row and row[0]:
            return (row[0], row[1])
    except Exception:
        try: conn.rollback()
        except Exception: pass
    return ("UNKNOWN", None)


@router.get("/{line_id}/cycles/{part_code}/comments")
def list_cycle_comments(
    line_id: int,
    part_code: str,
    user=Depends(get_current_user_optional),
):
    """List all comments for a cycle (newest last, chronological for
    natural read order).  Public-ish — same auth posture as
    /cycle-video so wallboard / TV displays without a logged-in
    operator can still surface notes alongside the clip."""
    safe_pc = (part_code or "").strip()
    if not safe_pc:
        raise HTTPException(400, "part_code is required")
    with get_conn() as conn:
        _ensure_cycle_comments_table(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, comment, author, created_at "
            "FROM mes_cycle_comments "
            "WHERE line_id = %s AND part_code = %s "
            "ORDER BY created_at ASC, id ASC",
            (line_id, safe_pc),
        )
        rows = cur.fetchall() or []
    # Serialise timestamp for JSON
    return {
        "line_id":   line_id,
        "part_code": safe_pc,
        "count":     len(rows),
        "comments": [
            {
                "id":         r["id"],
                "comment":    r["comment"],
                "author":     r["author"] or "anonymous",
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.post("/{line_id}/cycles/{part_code}/comments")
def add_cycle_comment(
    line_id: int,
    part_code: str,
    body: dict,
    user=Depends(get_current_user),
):
    """Append a new comment to a cycle.  Login required so we can
    stamp the author.  No edit/delete by design — append-only audit
    trail; if a typo needs fixing, post a follow-up correction
    comment (same pattern as breakdown closure notes)."""
    safe_pc = (part_code or "").strip()
    if not safe_pc:
        raise HTTPException(400, "part_code is required")
    text = str(body.get("comment") or "").strip()
    if not text:
        raise HTTPException(400, "comment text is required")
    if len(text) > 2000:
        raise HTTPException(400, "comment too long (max 2000 chars)")
    # Author resolution — try a few common fields the auth layer might
    # expose; fall back to "operator" if none match.
    author = (
        getattr(user, "username", None)
        or getattr(user, "name",     None)
        or getattr(user, "email",    None)
        or "operator"
    )
    # 2026-06-08 — machine context (from the video modal — Final Inspection
    # for main-line cycle notes) + the line's current shift, so Comments
    # History shows machine name + "kis din ki konsi shift".
    machine_name = (str(body.get("machine_name") or "").strip() or None)
    _mid = body.get("machine_id")
    try:
        machine_id = int(_mid) if _mid not in (None, "", 0, "0") else None
    except Exception:
        machine_id = None
    with get_conn() as conn:
        _ensure_cycle_comments_table(conn)
        shift_name, record_date = _current_shift(line_id, conn)
        cur = dict_cursor(conn)
        cur.execute(
            "INSERT INTO mes_cycle_comments "
            "  (line_id, part_code, comment, author, "
            "   shift_name, machine_id, machine_name, record_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id, created_at",
            (line_id, safe_pc, text, author,
             shift_name, machine_id, machine_name, record_date),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "ok":           True,
        "id":           row["id"],
        "line_id":      line_id,
        "part_code":    safe_pc,
        "comment":      text,
        "author":       author,
        "shift_name":   shift_name,
        "machine_id":   machine_id,
        "machine_name": machine_name,
        "created_at":   row["created_at"].isoformat() if row["created_at"] else None,
    }


# ============================================================
# NG DETAILS  (per-NG-part reason — both auto + line-leader manual)
# ============================================================
# 2026-05-21 — Operator spec: NG cycle pe click karo, modal khule with
# 2 sections:
#   (A) "Machine ne kya NG kya" — auto, derived from the cycle's own
#       metadata + sub-machine logs around the same timestamp.
#   (B) "Line leader's exact remark" — manual entry; supervisor types
#       the actual reason after physical inspection.
#
# Table is UPSERT-style (one row per part_code) — leader can EDIT
# their remark unlike cycle_comments which is append-only.  We keep
# the previous remark + author in a JSONB audit_trail column so the
# edit history is preserved.

def _ensure_ng_remarks_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_ng_remarks (
            id              SERIAL PRIMARY KEY,
            line_id         INTEGER NOT NULL,
            part_code       TEXT    NOT NULL,
            leader_remark   TEXT,
            entered_by      TEXT,
            audit_trail     JSONB   NOT NULL DEFAULT '[]'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (line_id, part_code)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ng_remarks_line_part
            ON mes_ng_remarks (line_id, part_code);
    """)
    conn.commit()


def _build_machine_ng_reason(conn, line_id: int, part_code: str) -> dict:
    """Auto-derive WHY the machine flagged this part as NG.
    Looks at:
      1. The cycle's own row in the line table (CT vs ideal, status)
      2. Sub-machine logs (mes_submachine_data_log) for the same
         part_code — surfaces which station threw it.
    Returns a dict the frontend can render straight."""
    cur = dict_cursor(conn)
    reason = {
        "cycle":         None,
        "ct_deviation":  None,
        "sub_machines":  [],
        "notes":         [],
    }
    # 1) Cycle row from the line's main table
    try:
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        lr = cur.fetchone()
        if lr and lr.get("db_table_name"):
            tbl = lr["db_table_name"]
            cur.execute(
                f"SELECT cycle_seq, ct_value, is_ng, ts, record_date "
                f"FROM {tbl}_ct_log WHERE part_code = %s "
                f"ORDER BY ts DESC LIMIT 1",
                (part_code,),
            )
            cyc = cur.fetchone()
            if cyc:
                reason["cycle"] = {
                    "cycle_seq": cyc["cycle_seq"],
                    "ct_value":  float(cyc["ct_value"]) if cyc["ct_value"] is not None else None,
                    "is_ng":     bool(cyc["is_ng"]),
                    "ts":        cyc["ts"].isoformat() if cyc["ts"] else None,
                }
                # Compare CT against ideal_cycle_time
                cur.execute("SELECT ideal_cycle_time FROM mes_lines WHERE id = %s", (line_id,))
                lr2 = cur.fetchone()
                ideal = float(lr2["ideal_cycle_time"]) if lr2 and lr2.get("ideal_cycle_time") else None
                ct = reason["cycle"]["ct_value"]
                if ideal and ct:
                    reason["ct_deviation"] = {
                        "actual": ct, "ideal": ideal,
                        "diff":  round(ct - ideal, 2),
                        "pct":   round((ct - ideal) / ideal * 100, 1),
                    }
                    if ct > ideal * 1.5:
                        reason["notes"].append(
                            f"Cycle time {ct:.1f}s exceeded ideal {ideal:.1f}s "
                            f"by {((ct/ideal - 1) * 100):.0f}% — possible jam / "
                            f"slow operator / breakdown overlap."
                        )
    except Exception as exc:
        reason["notes"].append(f"Cycle lookup failed: {str(exc)[:80]}")

    # 2) Sub-machine activity around this part_code.
    # mes_submachine_data_log schema: sub_plc_id, line_id, cycle_seq,
    # ts_plc, ts_server, part_code, model_number, model_name, data_values
    # (JSONB).  Sub-machine name comes from mes_plc_configs lookup.
    try:
        cur.execute(
            "SELECT sdl.sub_plc_id, sdl.ts_plc, sdl.ts_server, "
            "       sdl.data_values, sdl.model_name, "
            "       pc.machine_name "
            "FROM mes_submachine_data_log sdl "
            "LEFT JOIN mes_plc_configs pc "
            "       ON pc.id = sdl.sub_plc_id "
            "WHERE sdl.line_id = %s AND sdl.part_code = %s "
            "ORDER BY sdl.ts_plc ASC NULLS LAST, sdl.ts_server ASC",
            (line_id, part_code),
        )
        for row in (cur.fetchall() or []):
            # data_values is JSONB — may carry status / NG flag / sensor
            # snapshot.  Surface anything that looks NG-relevant.
            dv = row.get("data_values") or {}
            status = None
            if isinstance(dv, dict):
                # Common keys: "status", "is_ng", "result", "fail_code"
                status = (dv.get("status") or dv.get("result")
                          or ("NG" if dv.get("is_ng") else None)
                          or dv.get("fail_code"))
            reason["sub_machines"].append({
                "plc_id":       row.get("sub_plc_id"),
                "machine_name": row.get("machine_name") or f"PLC-{row.get('sub_plc_id')}",
                "status":       status or "DATA",
                "model":        row.get("model_name"),
                "ts":          (row["ts_plc"] or row["ts_server"]).isoformat()
                                if (row.get("ts_plc") or row.get("ts_server")) else None,
            })
    except Exception as exc:
        reason["notes"].append(f"Sub-machine lookup partial: {str(exc)[:80]}")

    if not reason["sub_machines"] and not reason["notes"]:
        reason["notes"].append(
            "Main PLC's L109 (NG bit) fired during this cycle but no "
            "sub-machine recorded a fail — operator may have manually "
            "rejected, or quality station triggered without a sensor "
            "trail.  Line leader should investigate physically."
        )
    return reason


@router.get("/{line_id}/ng-details/{part_code}")
def get_ng_details(
    line_id: int,
    part_code: str,
    user=Depends(get_current_user_optional),
):
    """Return BOTH the auto-derived machine reason AND any existing
    line-leader remark for this NG part.  Public-ish (same posture
    as /cycle-video) so wallboard / TV displays surface it without
    a login."""
    safe_pc = (part_code or "").strip()
    if not safe_pc:
        raise HTTPException(400, "part_code is required")
    with get_conn() as conn:
        _ensure_ng_remarks_table(conn)
        # 2026-05-24 — Operator: "ye tujhe kisne btya ki ye part itne
        # ct se upr gya to ng h, khud se kuch bhi bna rha h kya tu".
        # Auto-derived "machine reason" (from CT vs ideal heuristic) is
        # MEANINGLESS guesswork — a slow cycle isn't necessarily NG.
        # Removed.  NG truth comes from the L109 bit + operator-entered
        # remarks only.  Empty machine_reason kept for API back-compat.
        machine_reason = {}

        cur = dict_cursor(conn)
        cur.execute(
            "SELECT leader_remark, entered_by, audit_trail, created_at, updated_at "
            "FROM mes_ng_remarks "
            "WHERE line_id = %s AND part_code = %s",
            (line_id, safe_pc),
        )
        rr = cur.fetchone()
        leader_section = {
            "leader_remark": rr["leader_remark"] if rr else "",
            "entered_by":    rr["entered_by"]    if rr else None,
            "audit_trail":   rr["audit_trail"]   if rr else [],
            "created_at":    rr["created_at"].isoformat() if rr and rr.get("created_at") else None,
            "updated_at":    rr["updated_at"].isoformat() if rr and rr.get("updated_at") else None,
        }
    return {
        "line_id":        line_id,
        "part_code":      safe_pc,
        "machine_reason": machine_reason,
        "leader":         leader_section,
    }


@router.post("/{line_id}/ng-details/{part_code}")
def save_ng_leader_remark(
    line_id: int,
    part_code: str,
    body: dict,
    user=Depends(get_current_user),
):
    """Line leader writes the exact NG reason after physical inspection.
    UPSERT — overwriting the previous remark keeps the row count low.
    Previous remark + author is preserved in audit_trail JSONB so the
    edit history is never lost."""
    safe_pc = (part_code or "").strip()
    if not safe_pc:
        raise HTTPException(400, "part_code is required")
    text = str(body.get("leader_remark") or "").strip()
    if not text:
        raise HTTPException(400, "leader_remark text is required")
    if len(text) > 2000:
        raise HTTPException(400, "remark too long (max 2000 chars)")
    author = (
        getattr(user, "username", None)
        or getattr(user, "name",     None)
        or getattr(user, "email",    None)
        or "supervisor"
    )
    with get_conn() as conn:
        _ensure_ng_remarks_table(conn)
        cur = dict_cursor(conn)
        # Read current state for audit trail
        cur.execute(
            "SELECT leader_remark, entered_by, audit_trail FROM mes_ng_remarks "
            "WHERE line_id = %s AND part_code = %s",
            (line_id, safe_pc),
        )
        existing = cur.fetchone()
        new_trail = list(existing["audit_trail"]) if (existing and existing.get("audit_trail")) else []
        if existing and existing.get("leader_remark"):
            # Push the previous remark onto the audit trail
            new_trail.append({
                "remark":      existing["leader_remark"],
                "entered_by":  existing.get("entered_by"),
                "replaced_at": datetime.utcnow().isoformat() + "Z",
            })

        cur.execute(
            "INSERT INTO mes_ng_remarks (line_id, part_code, leader_remark, "
            "                            entered_by, audit_trail, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW()) "
            "ON CONFLICT (line_id, part_code) DO UPDATE SET "
            "  leader_remark = EXCLUDED.leader_remark, "
            "  entered_by    = EXCLUDED.entered_by, "
            "  audit_trail   = EXCLUDED.audit_trail, "
            "  updated_at    = NOW() "
            "RETURNING id, created_at, updated_at",
            (line_id, safe_pc, text, author, Json(new_trail)),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "ok":            True,
        "id":            row["id"],
        "line_id":       line_id,
        "part_code":     safe_pc,
        "leader_remark": text,
        "entered_by":    author,
        "audit_trail":   new_trail,
        "created_at":    row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":    row["updated_at"].isoformat() if row["updated_at"] else None,
    }


# ──────────────────────────────────────────────────────────────────
# Per-process NG remarks (2026-05-24)
# Operator: video k niche remarks ka option de hr process pe.
# Each machine/station gets its own remark per NG part_code.
# Quality dashboard pulls a summary by shift + date.
# ──────────────────────────────────────────────────────────────────

@router.get("/{line_id}/ng-process-remarks/{part_code}")
def get_ng_process_remarks(
    line_id:   int,
    part_code: str,
    user=Depends(get_current_user_optional),
):
    """Return list of per-machine remarks already saved for this NG part.
    Also returns the list of machines on this line so frontend can render
    a remark input for each machine that doesn't have one yet."""
    safe_pc = (part_code or "").strip().rstrip(":")
    if not safe_pc:
        raise HTTPException(400, "part_code required")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, machine_id, machine_name, remark_text, "
            "       created_at, updated_at, created_by "
            "FROM mes_ng_process_remarks "
            "WHERE part_code=%s ORDER BY machine_id",
            (safe_pc,),
        )
        existing = [
            {
                "id":           r["id"],
                "machine_id":   r["machine_id"],
                "machine_name": r["machine_name"],
                "remark_text":  r["remark_text"],
                "created_at":   r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at":   r["updated_at"].isoformat() if r["updated_at"] else None,
                "created_by":   r["created_by"],
            }
            for r in cur.fetchall()
        ]
        # All machines on this line (parent PLC + sub-machines), sorted by seq
        cur.execute(
            "SELECT id, machine_name, machine_seq, parent_plc_id "
            "FROM mes_plc_configs "
            "WHERE line_id=%s "
            "ORDER BY (parent_plc_id IS NOT NULL), "
            "         COALESCE(machine_seq, 9999), id",
            (line_id,),
        )
        machines = [
            {"id": r["id"], "machine_name": r["machine_name"],
             "machine_seq": r["machine_seq"],
             "is_main": r["parent_plc_id"] is None}
            for r in cur.fetchall()
        ]
    return {
        "part_code": safe_pc,
        "line_id":   line_id,
        "machines":  machines,
        "remarks":   existing,
    }


@router.post("/{line_id}/ng-process-remarks/{part_code}")
def save_ng_process_remark(
    line_id:   int,
    part_code: str,
    body:      dict,
    user=Depends(get_current_user),
):
    """UPSERT a per-machine remark for an NG part.  Body:
       { machine_id: int, machine_name: str, remark_text: str }"""
    safe_pc = (part_code or "").strip().rstrip(":")
    try:
        machine_id   = int(body.get("machine_id") or 0)
    except Exception:
        machine_id = 0
    machine_name = (body.get("machine_name") or "").strip()
    remark_text  = (body.get("remark_text")  or "").strip()
    if not safe_pc or not machine_id or not remark_text:
        raise HTTPException(400, "part_code, machine_id, remark_text required")

    author = (user.get("user_id") if isinstance(user, dict) else
              getattr(user, "user_id", "unknown"))

    with get_conn() as conn:
        # 2026-06-08 — shift from the line's CURRENT active shift row
        # ('current time se fetch'), NOT by matching the synthetic
        # M{machine}-C{cycle} part_code against the main ct_log — that never
        # matched, so every remark used to be saved with shift "UNKNOWN".
        shift_name, record_date = _current_shift(line_id, conn)
        if not record_date:
            from datetime import date as _date
            record_date = _date.today()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO mes_ng_process_remarks "
            "  (part_code, line_id, machine_id, machine_name, "
            "   remark_text, shift_name, record_date, created_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW()) "
            "ON CONFLICT (part_code, machine_id) DO UPDATE SET "
            "  remark_text  = EXCLUDED.remark_text, "
            "  machine_name = EXCLUDED.machine_name, "
            "  updated_at   = NOW() "
            "RETURNING id, created_at, updated_at",
            (safe_pc, line_id, machine_id, machine_name,
             remark_text, shift_name, record_date, author),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "ok":            True,
        "id":            row[0],
        "part_code":     safe_pc,
        "machine_id":    machine_id,
        "machine_name":  machine_name,
        "remark_text":   remark_text,
        "shift_name":    shift_name,
        "record_date":   str(record_date),
        "created_at":    row[1].isoformat() if row[1] else None,
        "updated_at":    row[2].isoformat() if row[2] else None,
    }


@router.get("/{line_id}/ng-process-remarks-summary")
def ng_process_remarks_summary(
    line_id:    int,
    date_from:  str = None,
    date_to:    str = None,
    shift_name: str = None,
    user=Depends(get_current_user_optional),
):
    """Quality dashboard summary — all NG part remarks across machines
    grouped by shift + date.  Filterable by date range and shift."""
    from datetime import date as _date, timedelta as _td
    if not date_from:
        date_from = str(_date.today() - _td(days=7))
    if not date_to:
        date_to = str(_date.today())
    where = "line_id = %s AND record_date BETWEEN %s AND %s"
    params = [line_id, date_from, date_to]
    if shift_name:
        where += " AND shift_name = %s"
        params.append(shift_name)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            f"SELECT record_date, shift_name, part_code, machine_id, "
            f"       machine_name, remark_text, created_at, updated_at, "
            f"       created_by "
            f"FROM mes_ng_process_remarks "
            f"WHERE {where} "
            f"ORDER BY record_date DESC, shift_name, part_code, machine_id",
            params,
        )
        rows = [
            {
                "record_date":  str(r["record_date"]),
                "shift_name":   r["shift_name"],
                "part_code":    r["part_code"],
                "machine_id":   r["machine_id"],
                "machine_name": r["machine_name"],
                "remark_text":  r["remark_text"],
                "created_at":   r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at":   r["updated_at"].isoformat() if r["updated_at"] else None,
                "created_by":   r["created_by"],
            }
            for r in cur.fetchall()
        ]
    return {
        "line_id":   line_id,
        "date_from": date_from,
        "date_to":   date_to,
        "shift":     shift_name,
        "rows":      rows,
        "total":     len(rows),
    }


@router.get("/{line_id}/comments-history")
def get_comments_history(
    line_id:    int,
    date_from:  Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:    Optional[str] = Query(None, description="YYYY-MM-DD"),
    shift_name: Optional[str] = Query(None),
    part_code:  Optional[str] = Query(None, description="exact or substring match"),
    machine_id: Optional[int] = Query(None),
    q:          Optional[str] = Query(None, description="text search in comment"),
    user=Depends(get_current_user_optional),
):
    """2026-05-27 — Combined comments + NG-process-remarks history.
    Operator request: dedicated Comments History page with filters +
    CSV export.  Returns rows from BOTH `mes_cycle_comments` (per-cycle
    free-text comments from the Fullscreen video modal) AND
    `mes_ng_process_remarks` (per-machine NG remarks from Wallboard
    sub-machine modals), tagged with a `type` column so the frontend
    can render them in one table.

    line_id = 0 → return rows for ALL lines (admin / multi-line view).
    All filters are optional and combined with AND.

    Default date range: today + last 7 days (operator's typical review
    window).  Override with date_from / date_to in YYYY-MM-DD."""
    from datetime import date as _date, timedelta as _td
    if not date_from:
        date_from = str(_date.today() - _td(days=7))
    if not date_to:
        date_to = str(_date.today())

    rows = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_cycle_comments_table(conn)   # 2026-06-08 — ensure shift/machine cols exist

        # ── 1. Per-cycle comments ───────────────────────────────────
        # 2026-06-08 — mes_cycle_comments now carries shift_name + machine_*
        # (stamped at save time), so shift/machine filters apply here too and
        # the source is no longer skipped when those filters are set.
        cyc_where = "created_at::date BETWEEN %s AND %s"
        cyc_params = [date_from, date_to]
        if line_id and line_id > 0:
            cyc_where += " AND line_id = %s"
            cyc_params.append(line_id)
        if shift_name:
            cyc_where += " AND shift_name = %s"
            cyc_params.append(shift_name)
        if machine_id:
            cyc_where += " AND machine_id = %s"
            cyc_params.append(machine_id)
        if part_code:
            cyc_where += " AND part_code ILIKE %s"
            cyc_params.append(f"%{part_code}%")
        if q:
            cyc_where += " AND comment ILIKE %s"
            cyc_params.append(f"%{q}%")
        cur.execute(
            f"SELECT id, line_id, part_code, comment, author, "
            f"       shift_name, machine_id, machine_name, record_date, "
            f"       created_at AS ts "
            f"FROM mes_cycle_comments "
            f"WHERE {cyc_where} "
            f"ORDER BY created_at DESC",
            cyc_params,
        )
        for r in cur.fetchall():
            rows.append({
                "type":         "Comment",
                "id":           r["id"],
                "line_id":      r["line_id"],
                "part_code":    r["part_code"],
                "shift_name":   r.get("shift_name"),
                "machine_id":   r.get("machine_id"),
                "machine_name": r.get("machine_name"),
                "text":         r["comment"],
                "author":       r["author"],
                "ts":           r["ts"].isoformat() if r["ts"] else None,
                "record_date":  (str(r["record_date"]) if r.get("record_date")
                                 else (r["ts"].date().isoformat() if r["ts"] else None)),
            })

        # ── 2. NG process remarks ──────────────────────────────────
        ng_where = "record_date BETWEEN %s AND %s"
        ng_params = [date_from, date_to]
        if line_id and line_id > 0:
            ng_where += " AND line_id = %s"
            ng_params.append(line_id)
        if shift_name:
            ng_where += " AND shift_name = %s"
            ng_params.append(shift_name)
        if machine_id:
            ng_where += " AND machine_id = %s"
            ng_params.append(machine_id)
        if part_code:
            ng_where += " AND part_code ILIKE %s"
            ng_params.append(f"%{part_code}%")
        if q:
            ng_where += " AND remark_text ILIKE %s"
            ng_params.append(f"%{q}%")
        cur.execute(
            f"SELECT id, line_id, part_code, machine_id, machine_name, "
            f"       remark_text, created_by, shift_name, record_date, "
            f"       COALESCE(updated_at, created_at) AS ts "
            f"FROM mes_ng_process_remarks "
            f"WHERE {ng_where} "
            f"ORDER BY COALESCE(updated_at, created_at) DESC",
            ng_params,
        )
        for r in cur.fetchall():
            rows.append({
                "type":         "NG-Remark",
                "id":           r["id"],
                "line_id":      r["line_id"],
                "part_code":    r["part_code"],
                "shift_name":   r["shift_name"],
                "machine_id":   r["machine_id"],
                "machine_name": r["machine_name"],
                "text":         r["remark_text"],
                "author":       r["created_by"],
                "ts":           r["ts"].isoformat() if r["ts"] else None,
                "record_date":  str(r["record_date"]) if r["record_date"] else None,
            })

    # Sort combined list by ts DESC so latest is on top.
    rows.sort(key=lambda x: x.get("ts") or "", reverse=True)

    return {
        "line_id":   line_id,
        "date_from": date_from,
        "date_to":   date_to,
        "filters":   {
            "shift_name": shift_name,
            "machine_id": machine_id,
            "part_code":  part_code,
            "q":          q,
        },
        "rows":      rows,
        "total":     len(rows),
    }


@router.get("/{line_id}/ng-list")
def list_ng_parts_for_slot(
    line_id:    int,
    date:       str = Query(..., description="record_date, YYYY-MM-DD"),
    slot_label: str = Query(..., description='slot label like "08:30-09:30"'),
    user=Depends(get_current_user_optional),
):
    """All NG parts within a single hourly slot — feeds the popup that
    opens when the operator clicks the NG count cell in the slot table.
    Returns a table-friendly shape with one row per NG part.  Each
    row joins:
      • cycle metadata (ct_log)            → cycle_seq, ct_value, ts
      • machine reason summary (computed)  → CT deviation badge
      • leader remark (mes_ng_remarks)     → existing line-leader note
    """
    # Parse "HH:MM-HH:MM"
    try:
        start_s, end_s = slot_label.split("-", 1)
        sh, sm = int(start_s[:2]), int(start_s[3:5])
        eh, em = int(end_s[:2]),   int(end_s[3:5])
    except Exception:
        raise HTTPException(400, f"Bad slot_label: {slot_label!r} (expect HH:MM-HH:MM)")
    with get_conn() as conn:
        _ensure_ng_remarks_table(conn)
        cur = dict_cursor(conn)
        # Find this line's ct_log table
        cur.execute("SELECT db_table_name, ideal_cycle_time "
                    "FROM mes_lines WHERE id = %s", (line_id,))
        lr = cur.fetchone()
        if not lr or not lr.get("db_table_name"):
            raise HTTPException(404, f"Line {line_id} not found / no ct_log table")
        tbl   = lr["db_table_name"]
        ideal = float(lr["ideal_cycle_time"]) if lr.get("ideal_cycle_time") else None
        # Cross-midnight slot? if end < start, end belongs to next day.
        # For simplicity treat as "ts BETWEEN ... and ..." on same record_date —
        # cycles.csv keys each row by start_date so this matches the
        # collector's slot-rollup convention.
        cur.execute(
            f"""SELECT ct.cycle_seq, ct.part_code, ct.ct_value, ct.ts,
                       nr.leader_remark, nr.entered_by, nr.updated_at AS remark_updated
                FROM {tbl}_ct_log ct
                LEFT JOIN mes_ng_remarks nr
                       ON nr.line_id = %s AND nr.part_code = ct.part_code
                WHERE ct.record_date = %s
                  AND ct.is_ng = TRUE
                  AND EXTRACT(HOUR FROM ct.ts) * 60 + EXTRACT(MINUTE FROM ct.ts)
                      BETWEEN %s AND %s
                ORDER BY ct.ts ASC""",
            (line_id, date, sh * 60 + sm, eh * 60 + em - 1),
        )
        rows = cur.fetchall() or []

    # 2026-06-18 — register=truth NG list.  Keep every real NG — INCLUDING the
    # ones with no scanned barcode (the PLC register D102 counts those too;
    # a scan-fail still ran a full inspection) — and drop only the non-NG
    # noise the register also ignores: sub-CT warm-up phantoms (CT < 0.67×ideal)
    # and gap-time junk "cycles" (CT in the hundreds/thousands of seconds).
    # The frontend USED to require a part_code, which wrongly hid genuine NGs
    # the operator didn't barcode-scan (e.g. 5 of 6 NG one shift showed as 1).
    _idl   = float(ideal) if ideal and float(ideal) > 0 else 15.0
    _ng_lo = _idl * 0.67
    _ng_hi = max(600.0, _idl * 40.0)

    result = []
    for r in rows:
        ct = float(r["ct_value"]) if r.get("ct_value") is not None else None
        if ct is None or ct < _ng_lo or ct > _ng_hi:
            continue            # drop phantom / gap-junk; keep real NG (barcoded or not)
        # 2026-05-26 — machine_alarm intentionally left empty.
        # Operator clarified CT has NO relation to NG (slow cycle ≠ NG).
        # Future PY-style alarm-name config will populate this from
        # actual PLC alarm bits.  For now: empty placeholder so the
        # column in NgListModal renders "—".
        result.append({
            "cycle_seq":      r.get("cycle_seq"),
            "part_code":      r.get("part_code") or "",
            "ct_value":       ct,
            "ts":             r["ts"].isoformat() if r.get("ts") else None,
            "machine_alarm":  "",
            "leader_remark":  r.get("leader_remark") or "",
            "entered_by":     r.get("entered_by"),
            "remark_updated": r["remark_updated"].isoformat()
                                  if r.get("remark_updated") else None,
        })
    return {
        "line_id":    line_id,
        "date":       date,
        "slot_label": slot_label,
        "count":      len(result),
        "rows":       result,
    }


# ============================================================
# SHIFT-END NG MAIL → Quality team
# ============================================================
# 2026-06-09 — Quality NG-mail recipients, DB-backed.  Set live from the
# Quality panel UI; send-ng-mail reads these FIRST (.env only as fallback),
# so changing the recipient takes effect immediately — NO restart needed.
# ============================================================
def _ensure_quality_mail_config(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_mail_config (
            line_id     INTEGER PRIMARY KEY,
            to_emails   TEXT,
            cc_emails   TEXT,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    conn.commit()


def _get_quality_mail_config(line_id):
    """(to_emails, cc_emails) from DB for this line, or (None, None)."""
    try:
        with get_conn() as conn:
            _ensure_quality_mail_config(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT to_emails, cc_emails FROM mes_quality_mail_config "
                "WHERE line_id = %s", (line_id,))
            r = cur.fetchone()
            if r:
                return (r[0], r[1])
    except Exception:
        pass
    return (None, None)


@router.get("/{line_id}/quality-mail-config")
def get_quality_mail_config(line_id: int, user=Depends(get_current_user_optional)):
    """Current NG-mail recipients for this line (DB), plus the .env values as
    the effective fallback so the UI can show what's actually in use."""
    import os as _os
    to_db, cc_db = _get_quality_mail_config(line_id)
    upd_by = None; upd_at = None
    try:
        with get_conn() as conn:
            _ensure_quality_mail_config(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT updated_by, updated_at FROM mes_quality_mail_config "
                "WHERE line_id = %s", (line_id,))
            r = cur.fetchone()
            if r:
                upd_by = r[0]
                upd_at = r[1].isoformat() if r[1] else None
    except Exception:
        pass
    return {
        "line_id":    line_id,
        "to_emails":  to_db or "",
        "cc_emails":  cc_db or "",
        "env_to":     _os.getenv("QUALITY_NG_TO", ""),
        "env_cc":     _os.getenv("QUALITY_NG_CC", ""),
        "source":     "db" if (to_db or "").strip() else "env",
        "updated_by": upd_by,
        "updated_at": upd_at,
    }


@router.post("/{line_id}/quality-mail-config")
def set_quality_mail_config(line_id: int, body: dict, user=Depends(get_current_user)):
    """Set/Update NG-mail recipients for this line.  Effective IMMEDIATELY for
    the next 'Send to Quality' — NO restart.
    Body: { to_emails: 'a@x.com,b@y.com', cc_emails?: '...' }"""
    import re as _re
    to_emails = str(body.get("to_emails") or "").strip()
    cc_emails = str(body.get("cc_emails") or "").strip()
    toks = [t.strip() for t in to_emails.split(",") if t.strip()]
    if not toks:
        raise HTTPException(400, "At least one recipient email required (comma-separated)")
    _ok = lambda t: bool(_re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", t))
    bad = [t for t in toks if not _ok(t)] + \
          [t for t in (cc_emails.split(",") if cc_emails else []) if t.strip() and not _ok(t.strip())]
    if bad:
        raise HTTPException(400, f"Invalid email(s): {', '.join(bad)}")
    author = (getattr(user, "username", None) or getattr(user, "name", None)
              or getattr(user, "email", None) or "operator")
    with get_conn() as conn:
        _ensure_quality_mail_config(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mes_quality_mail_config "
            "  (line_id, to_emails, cc_emails, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (line_id) DO UPDATE SET "
            "  to_emails  = EXCLUDED.to_emails, "
            "  cc_emails  = EXCLUDED.cc_emails, "
            "  updated_by = EXCLUDED.updated_by, "
            "  updated_at = NOW()",
            (line_id, to_emails, cc_emails, author))
        conn.commit()
    return {"ok": True, "line_id": line_id, "to_emails": to_emails,
            "cc_emails": cc_emails, "updated_by": author}


# ============================================================
# 2026-06-09 — NG-mail video helpers.  Get a cycle clip (pre-archived
# {part_code}.mp4 first, else a LIVE cut from /cycle-video — the same clip
# the wallboard plays) and, when it's too big for one e-mail, COMPRESS it
# (full duration, lower resolution) so the WHOLE video still goes instead of
# being skipped.  Used only by send-ng-mail.  Collector untouched.
# ============================================================
def _ffmpeg_bin():
    import os as _o, shutil as _sh
    cand = (r"C:\Users\DX-ADMIN\AppData\Roaming\Python\Python312"
            r"\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe")
    if _o.path.isfile(cand):
        return cand
    return _sh.which("ffmpeg") or "ffmpeg"


def _mail_clip_bytes(line_id, r):
    """Raw mp4 bytes for an NG row: pre-archived {part_code}.mp4 first, else a
    LIVE cut from the cycle-video endpoint.  Returns bytes or None."""
    import os as _o
    pc = r.get("part_code")
    if pc:
        for base in (r"D:\MES_Videos\YNC-SS",
                     r"D:\EOL\EOL\New folder (2)\New folder (2)\backend\videos\YNC-SS"):
            cand = _o.path.join(base, f"{pc}.mp4")
            if _o.path.isfile(cand):
                try:
                    with open(cand, "rb") as fh:
                        return fh.read()
                except OSError:
                    pass
    cyc = r.get("cycle_seq")
    if cyc is not None:
        try:
            import urllib.request as _u
            url = (f"http://127.0.0.1:8080/api/lines/{line_id}"
                   f"/cycle-video?cycle_seq={int(cyc)}")
            with _u.urlopen(url, timeout=90) as resp:
                return resp.read()
        except Exception:
            pass
    return None


def _compress_to_fit(raw, target_bytes):
    """Re-encode mp4 bytes to <= target_bytes keeping FULL duration (lower
    resolution, no audio).  Returns compressed bytes, or None if it can't get
    under target at any tier."""
    import os as _o, tempfile as _tf, subprocess as _sp
    ff = _ffmpeg_bin()
    tin = None
    try:
        f = _tf.NamedTemporaryFile(suffix=".mp4", delete=False)
        f.write(raw); f.close(); tin = f.name
        for (h, crf) in ((480, 30), (360, 32), (240, 34)):
            tout = f"{tin}.{h}.mp4"
            try:
                _sp.run([ff, "-y", "-loglevel", "error", "-i", tin,
                         "-vf", f"scale=-2:{h}", "-c:v", "libx264",
                         "-preset", "veryfast", "-crf", str(crf), "-an",
                         "-movflags", "+faststart", tout],
                        timeout=180, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                if _o.path.isfile(tout) and 0 < _o.path.getsize(tout) <= target_bytes:
                    with open(tout, "rb") as fh:
                        return fh.read()
            except Exception:
                pass
            finally:
                try: _o.unlink(tout)
                except Exception: pass
    except Exception:
        pass
    finally:
        if tin:
            try: _o.unlink(tin)
            except Exception: pass
    return None


# ============================================================
# 2026-05-21 — Operator spec: manual "Send to Quality" button that
# gathers all NG parts in the slot (or whole shift), builds an HTML
# report with machine alarms + line leader remarks, attaches the
# top 3 worst-CT NG cycle videos, and emails it to the Quality team.
# Recipient list comes from .env (QUALITY_NG_TO, comma-separated).
# Top-3 cap keeps the message under 25 MB (most SMTP relay limits);
# remaining NGs get clickable links to the CMS by-part endpoint.

@router.post("/{line_id}/send-ng-mail")
def send_ng_mail_to_quality(
    line_id:    int,
    body:       dict,
    user=Depends(get_current_user),
):
    """Body: { date: 'YYYY-MM-DD', slot_label?: '08:30-09:30',
               shift_name?: 'A', attach_videos?: true }
    If slot_label is given, mails only that slot's NGs.  Otherwise
    mails the whole shift's NGs."""
    import os as _os
    import smtplib as _smtplib
    from email.mime.multipart import MIMEMultipart as _MM
    from email.mime.text      import MIMEText      as _MT
    from email.mime.base      import MIMEBase      as _MB
    from email                import encoders      as _enc

    date_s     = (body.get("date")       or "").strip()
    slot_lbl   = (body.get("slot_label") or "").strip()
    shift_nm   = (body.get("shift_name") or "").strip()
    attach_vid = body.get("attach_videos", True)
    if not date_s:
        raise HTTPException(400, "date is required (YYYY-MM-DD)")

    smtp_user = _os.getenv("SMTP_USER", "")
    smtp_pass = _os.getenv("SMTP_PASS", "")
    smtp_host = _os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(_os.getenv("SMTP_PORT", "587") or 587)
    if not (smtp_user and smtp_pass):
        raise HTTPException(500,
            "SMTP_USER / SMTP_PASS not configured in .env — "
            "cannot send Quality mail")

    # 2026-06-09 — recipients from the DB config (set live via the Quality
    # panel, NO restart) FIRST; .env QUALITY_NG_TO/CC only as fallback.
    _db_to, _db_cc = _get_quality_mail_config(line_id)
    to_csv  = ((_db_to or "").strip() or _os.getenv("QUALITY_NG_TO", "").strip())
    cc_csv  = ((_db_cc or "").strip() or _os.getenv("QUALITY_NG_CC", "").strip())
    to_list = [x.strip() for x in to_csv.split(",") if x.strip()]
    cc_list = [x.strip() for x in cc_csv.split(",") if x.strip()]
    if not to_list:
        raise HTTPException(500,
            "No NG-mail recipient configured — set it in the Quality panel "
            "(or QUALITY_NG_TO in .env)")

    # Gather rows
    with get_conn() as conn:
        _ensure_ng_remarks_table(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT line_name, db_table_name, ideal_cycle_time "
            "FROM mes_lines WHERE id = %s", (line_id,))
        lr = cur.fetchone()
        if not lr or not lr.get("db_table_name"):
            raise HTTPException(404, f"Line {line_id} not found")
        line_name = lr.get("line_name") or f"Line {line_id}"
        tbl       = lr["db_table_name"]
        ideal     = float(lr["ideal_cycle_time"]) if lr.get("ideal_cycle_time") else None

        # Build WHERE clause — optional slot/shift filter
        where = ["ct.record_date = %s", "ct.is_ng = TRUE"]
        params: list = [date_s]
        if slot_lbl:
            try:
                start_s, end_s = slot_lbl.split("-", 1)
                sh, sm = int(start_s[:2]), int(start_s[3:5])
                eh, em = int(end_s[:2]),   int(end_s[3:5])
                where.append(
                    "EXTRACT(HOUR FROM ct.ts) * 60 + EXTRACT(MINUTE FROM ct.ts) "
                    "BETWEEN %s AND %s"
                )
                params.extend([sh*60+sm, eh*60+em-1])
            except Exception:
                raise HTTPException(400, f"Bad slot_label: {slot_lbl!r}")
        if shift_nm:
            where.append("ct.shift_name = %s")
            params.append(shift_nm)
        sql = (
            f"SELECT ct.cycle_seq, ct.part_code, ct.ct_value, ct.ts, "
            f"       ct.shift_name, "
            f"       nr.leader_remark, nr.entered_by "
            f"FROM {tbl}_ct_log ct "
            f"LEFT JOIN mes_ng_remarks nr "
            f"       ON nr.line_id = %s AND nr.part_code = ct.part_code "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY ct.ts ASC"
        )
        cur.execute(sql, [line_id] + params)
        rows = cur.fetchall() or []

    if not rows:
        raise HTTPException(404,
            "No NG parts found for the given date/slot/shift")

    # ── Build HTML body ───────────────────────────────────────────
    title_scope = (
        f"slot {slot_lbl}" if slot_lbl
        else f"shift {shift_nm}" if shift_nm
        else "full day"
    )
    subject = f"[MES Quality Alert] {line_name} · {date_s} · {title_scope} · {len(rows)} NG part(s)"

    def _fmt_ng(r):
        ct  = float(r["ct_value"]) if r.get("ct_value") is not None else None
        if ct is not None and ideal:
            diff = round(ct - ideal, 2)
            pct  = round((ct - ideal) / ideal * 100, 1)
            alarm = (f"CT {ct:.1f}s vs ideal {ideal:.0f}s "
                     f"({'+' if diff > 0 else ''}{diff}s, {pct}%)")
        else:
            alarm = "—"
        return {
            "cycle_seq":     r.get("cycle_seq"),
            "part_code":     r.get("part_code") or "",
            "ts":            r["ts"].strftime("%H:%M:%S") if r.get("ts") else "—",
            "alarm":         alarm,
            "leader_remark": r.get("leader_remark") or "(no remark entered)",
            "entered_by":    r.get("entered_by") or "—",
            "ct":            ct,
        }
    nice = [_fmt_ng(r) for r in rows]

    rows_html = "\n".join([
        f"<tr style='border-bottom:1px solid #ddd;{'' if i % 2 == 0 else 'background:#fafafa;'}'>"
        f"<td style='padding:6px 10px;font-family:monospace;font-size:12px;color:#1d4ed8;'>{r['part_code']}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#555;'>{r['ts']}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#dc2626;'>{r['alarm']}</td>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r['leader_remark']}<br>"
        f"<span style='color:#888;font-size:10px;'>by {r['entered_by']}</span></td>"
        f"</tr>"
        for i, r in enumerate(nice)
    ])
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ font-family: Arial, sans-serif; color:#111; }}
  h2   {{ color:#dc2626; margin:0 0 6px 0; }}
  .meta{{ font-size:13px; color:#555; margin-bottom:14px; }}
  table{{ border-collapse:collapse; width:100%; }}
  th   {{ background:#dc2626; color:#fff; padding:8px 10px; text-align:left;
          font-size:11px; letter-spacing:.04em; text-transform:uppercase; }}
</style></head>
<body>
  <h2>⚠ NG Quality Report — {line_name}</h2>
  <div class="meta">
    Date: <b>{date_s}</b> · Scope: <b>{title_scope}</b>
    · Total NG parts: <b style="color:#dc2626;">{len(rows)}</b>
    · Generated by: {(getattr(user, 'username', None) or getattr(user, 'name', None) or '—')}
  </div>
  <table>
    <thead>
      <tr><th>Part Code</th><th>Time</th><th>Machine Alarm</th><th>Line Leader Remark</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <p style="margin-top:18px;font-size:11px;color:#888;">
    Top 3 NG videos attached (worst CT first).  Full video library on the MES wallboard
    — open any Final Inspection cycle to see machine reason + leader remarks.
  </p>
</body></html>"""

    # ── Build the message + attach top-3 videos ──────────────────
    msg = _MM("mixed")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    alt = _MM("alternative")
    alt.attach(_MT(html, "html"))
    msg.attach(alt)

    attached_paths: list = []
    skipped_paths:  list = []
    if attach_vid:
        # Worst-CT first.
        nice_sorted = sorted(
            nice,
            key=lambda r: (r["ct"] if r["ct"] is not None else 0),
            reverse=True,
        )
        # 2026-06-09 — FULL video in the mail.  Per NG part: get the clip
        # (pre-archived file → else a LIVE /cycle-video cut), and if it's too
        # big for one e-mail, COMPRESS it (full duration kept) so the WHOLE
        # video still goes instead of being silently skipped.
        MAX_TOTAL    = 22 * 1024 * 1024     # safe under Office365's ~25 MB cap
        PER_CLIP_RAW = 8  * 1024 * 1024     # attach as-is when already this small
        total_bytes  = 0
        for r in nice_sorted[:3]:
            pc = r.get("part_code")
            if not pc:
                continue
            data = _mail_clip_bytes(line_id, r)     # archived file → live cut
            if not data:
                skipped_paths.append(pc)            # genuinely no footage
                continue
            budget = MAX_TOTAL - total_bytes
            if len(data) > min(PER_CLIP_RAW, budget):
                target = max(1, min(PER_CLIP_RAW, budget))
                comp = _compress_to_fit(data, target)   # full duration, smaller
                if not comp:
                    skipped_paths.append(pc)
                    continue
                data = comp
            if total_bytes + len(data) > MAX_TOTAL:
                skipped_paths.append(pc)
                continue
            part = _MB("video", "mp4")
            part.set_payload(data)
            _enc.encode_base64(part)
            part.add_header("Content-Disposition",
                            f'attachment; filename="NG_{pc}.mp4"')
            msg.attach(part)
            attached_paths.append(pc)
            total_bytes += len(data)

    # ── Send ───────────────────────────────────────────────────────
    try:
        with _smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
            s.ehlo(); s.starttls(); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_list + cc_list, msg.as_string())
    except Exception as exc:
        raise HTTPException(502, f"SMTP send failed: {exc}")

    return {
        "ok":              True,
        "to":              to_list,
        "cc":              cc_list,
        "ng_count":        len(rows),
        "videos_attached": attached_paths,
        "videos_skipped":  skipped_paths,
        "subject":         subject,
    }


# ============================================================
# LOSS REMARKS  (per-slot per-loss-type production team note)
# ============================================================
# 2026-05-22 — Operator spec: Hourly Loss Breakup modal me kisi bhi
# slot ke kisi bhi loss bucket (Breakdown / Quality / Material / Setup /
# Change Over / Speed Loss / Others) ke non-zero cell pe click karne pe
# Production team apna remark fill kar sake.  E.g. "08:30-09:30 me
# Breakdown 6:46 — Conveyor jam at station 3, cleared by maint at 09:08".
# UPSERT-style: one row per (line, date, shift, slot, loss_type);
# previous remark + author preserved in audit_trail JSONB.

def _ensure_loss_remarks_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_loss_remarks (
            id              SERIAL PRIMARY KEY,
            line_id         INTEGER NOT NULL,
            record_date     DATE    NOT NULL,
            shift_name      TEXT,
            slot_label      TEXT    NOT NULL,
            loss_type       TEXT    NOT NULL,
            remark          TEXT,
            entered_by      TEXT,
            audit_trail     JSONB   NOT NULL DEFAULT '[]'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (line_id, record_date, shift_name, slot_label, loss_type)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_loss_remarks_line_date
            ON mes_loss_remarks (line_id, record_date, shift_name);
    """)
    conn.commit()


@router.get("/{line_id}/loss-remarks")
def get_loss_remark(
    line_id:    int,
    date:       str = Query(..., description="record_date YYYY-MM-DD"),
    shift_name: str = Query("",  description="shift name (e.g. 'A' or 'B')"),
    slot_label: str = Query(..., description="slot label e.g. '08:30-09:30'"),
    loss_type:  str = Query(..., description="loss bucket key e.g. 'breakdown'"),
    user=Depends(get_current_user_optional),
):
    """Return the existing remark for one (slot, loss_type) cell.
    Returns empty remark if none has been entered yet."""
    with get_conn() as conn:
        _ensure_loss_remarks_table(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, remark, entered_by, audit_trail, "
            "       created_at, updated_at "
            "FROM mes_loss_remarks "
            "WHERE line_id = %s AND record_date = %s "
            "  AND COALESCE(shift_name,'') = %s "
            "  AND slot_label = %s AND loss_type = %s",
            (line_id, date, shift_name or "", slot_label, loss_type),
        )
        row = cur.fetchone()
    if not row:
        return {
            "line_id":     line_id,
            "date":        date,
            "shift_name":  shift_name,
            "slot_label":  slot_label,
            "loss_type":   loss_type,
            "remark":      "",
            "entered_by":  None,
            "audit_trail": [],
            "created_at":  None,
            "updated_at":  None,
        }
    return {
        "line_id":     line_id,
        "date":        date,
        "shift_name":  shift_name,
        "slot_label":  slot_label,
        "loss_type":   loss_type,
        "remark":      row["remark"] or "",
        "entered_by":  row["entered_by"],
        "audit_trail": row["audit_trail"] or [],
        "created_at":  row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at":  row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.post("/{line_id}/loss-remarks")
def save_loss_remark(
    line_id: int,
    body:    dict,
    user=Depends(get_current_user),
):
    """UPSERT a loss remark.  Previous remark + author archived to
    audit_trail before overwrite.  Body must include date, shift_name,
    slot_label, loss_type, remark."""
    date_s     = str(body.get("date")       or "").strip()
    shift_nm   = str(body.get("shift_name") or "").strip()
    slot_lbl   = str(body.get("slot_label") or "").strip()
    loss_type  = str(body.get("loss_type")  or "").strip()
    text       = str(body.get("remark")     or "").strip()
    if not (date_s and slot_lbl and loss_type):
        raise HTTPException(400, "date, slot_label, loss_type are required")
    if not text:
        raise HTTPException(400, "remark text is required")
    if len(text) > 2000:
        raise HTTPException(400, "remark too long (max 2000 chars)")
    author = (
        getattr(user, "username", None)
        or getattr(user, "name",     None)
        or getattr(user, "email",    None)
        or "production"
    )
    with get_conn() as conn:
        _ensure_loss_remarks_table(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT remark, entered_by, audit_trail FROM mes_loss_remarks "
            "WHERE line_id = %s AND record_date = %s "
            "  AND COALESCE(shift_name,'') = %s "
            "  AND slot_label = %s AND loss_type = %s",
            (line_id, date_s, shift_nm, slot_lbl, loss_type),
        )
        existing = cur.fetchone()
        trail = list(existing["audit_trail"]) if (existing and existing.get("audit_trail")) else []
        if existing and existing.get("remark"):
            trail.append({
                "remark":      existing["remark"],
                "entered_by":  existing.get("entered_by"),
                "replaced_at": datetime.utcnow().isoformat() + "Z",
            })
        cur.execute(
            "INSERT INTO mes_loss_remarks "
            "  (line_id, record_date, shift_name, slot_label, loss_type, "
            "   remark, entered_by, audit_trail, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()) "
            "ON CONFLICT (line_id, record_date, shift_name, slot_label, loss_type) "
            "DO UPDATE SET "
            "  remark      = EXCLUDED.remark, "
            "  entered_by  = EXCLUDED.entered_by, "
            "  audit_trail = EXCLUDED.audit_trail, "
            "  updated_at  = NOW() "
            "RETURNING id, created_at, updated_at",
            (line_id, date_s, shift_nm, slot_lbl, loss_type,
             text, author, Json(trail)),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "line_id":     line_id,
        "date":        date_s,
        "shift_name":  shift_nm,
        "slot_label":  slot_lbl,
        "loss_type":   loss_type,
        "remark":      text,
        "entered_by":  author,
        "audit_trail": trail,
        "created_at":  row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at":  row["updated_at"].isoformat() if row.get("updated_at") else None,
    }