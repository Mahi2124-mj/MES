"""
collector_engine.py
===================
Fully dynamic production data collector.
"""

import time
import statistics
import threading
import traceback
import requests
from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional

import pymcprotocol
import psycopg2
import psycopg2.extras

# DB connection — pulled from the canonical config in database.py so
# credentials live in ONE place (env-driven with legacy fallbacks).  We
# add the connect_timeout the collector needs on top.
import os as _os_db
try:
    from database import DB_CONFIG as _BASE_DB_CONFIG
    DB_CONFIG = {**_BASE_DB_CONFIG, "connect_timeout": 5}
except Exception:
    # Standalone-script fallback: env vars → legacy literal.
    DB_CONFIG = {
        "host":     _os_db.getenv("DB_HOST",     "192.168.10.210"),
        "port":     int(_os_db.getenv("DB_PORT", "5432") or 5432),
        "database": _os_db.getenv("DB_NAME",     "energydb"),
        "user":     _os_db.getenv("DB_USER",     "postgres"),
        "password": _os_db.getenv("DB_PASS",     "tbdi@123"),
        "connect_timeout": 5,
    }

# Backend URL — collector POSTs PY events / sensor sweeps / health updates
# here.  Resolution order:
#   1. BACKEND_URL  (full URL, wins)
#   2. BACKEND_HOST + BACKEND_PORT (default port 8080)
#   3. legacy fallback http://127.0.0.1:8080  (dev / single-host install)
import os as _os
BACKEND_URL = (
    _os.getenv("BACKEND_URL")
    or f"http://{_os.getenv('BACKEND_HOST','127.0.0.1')}:{_os.getenv('BACKEND_PORT','8080')}"
)


def _db_conn():
    return psycopg2.connect(**DB_CONFIG)


# ════════════════════════════════════════════════════════════════════
# CROSS-PC SINGLETON LOCK
# ════════════════════════════════════════════════════════════════════
# Operator's pain point: "I run the frontend from another PC over LAN,
# and somehow the collector behaves wrong."
#
# Root cause: when a 2nd machine (laptop / supervisor PC) accidentally
# starts the collector_<line>.py launcher too, BOTH instances open MC
# protocol sockets to the PLC.  The Mitsubishi PLC then sets its
# "remote-active" flag (bit 4 of D6005 = decimal 16) and our status
# enum gets corrupted.  Even if we mask the flag (`& 0x0F`), the
# duplicate writes to `mes_dashboard_*` and `mes_breakdowns` race each
# other and produce ghost rows.
#
# Fix: a DB-backed lock keyed on line_id.  Each collector writes a
# heartbeat row; on startup another would-be collector sees the fresh
# heartbeat and refuses to run.  Cross-PC because everyone shares the
# same Postgres.
#
#   Table  : mes_collector_locks
#   Columns: line_id PK, hostname, pid, heartbeat_at
#
# Heartbeat refresh interval: 10 s.  A lock is considered STALE if its
# heartbeat is older than 30 s — that means the previous collector
# crashed and we can safely steal the slot.
# ════════════════════════════════════════════════════════════════════

import socket as _socket_for_lock

_HEARTBEAT_INTERVAL_SEC = 10
_HEARTBEAT_STALE_AFTER_SEC = 30


def _ensure_collector_lock_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_collector_locks (
            line_id      INTEGER     PRIMARY KEY,
            hostname     TEXT        NOT NULL,
            pid          INTEGER     NOT NULL,
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()


class CollectorSingletonLock:
    """Cross-PC lock for one line.  Use as a normal object — call
    .acquire() at startup (raises RuntimeError if another fresh
    collector holds it), and .release() on shutdown.

    The acquire path also spins a daemon thread that refreshes the
    heartbeat every _HEARTBEAT_INTERVAL_SEC so a stuck collector
    eventually loses the lock to a fresh restart.
    """

    def __init__(self, line_id: int):
        self.line_id  = line_id
        self.hostname = _socket_for_lock.gethostname()
        self.pid      = _os.getpid()
        self._stop    = threading.Event()
        self._thread  = None

    def acquire(self) -> None:
        with _db_conn() as c:
            _ensure_collector_lock_table(c)
            cur = c.cursor()
            # Read existing lock holder (if any)
            cur.execute("""
                SELECT hostname, pid, heartbeat_at,
                       NOW() - heartbeat_at AS age
                  FROM mes_collector_locks
                 WHERE line_id = %s
            """, (self.line_id,))
            row = cur.fetchone()
            if row:
                hostname, pid, hb, age = row
                age_sec = age.total_seconds() if hasattr(age, "total_seconds") else 0
                # Same host + same PID → tail-end of a previous run that
                # crashed before releasing.  Steal the lock without fuss.
                if hostname == self.hostname and pid == self.pid:
                    pass
                elif age_sec < _HEARTBEAT_STALE_AFTER_SEC:
                    # Fresh holder — refuse.
                    raise RuntimeError(
                        f"Another collector is already running for "
                        f"line_id={self.line_id} on host '{hostname}' "
                        f"(PID {pid}, last heartbeat {age_sec:.0f}s ago).  "
                        f"Stop that one first, or wait "
                        f"{_HEARTBEAT_STALE_AFTER_SEC - age_sec:.0f}s "
                        f"for its lock to go stale.\n"
                        f"\n"
                        f"This guard exists so a frontend opened from "
                        f"another LAN PC can't accidentally start a "
                        f"second collector and corrupt the PLC's "
                        f"remote-active bit (D6005 bit 4)."
                    )
                # else: stale holder → claim
            cur.execute("""
                INSERT INTO mes_collector_locks
                       (line_id, hostname, pid, heartbeat_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (line_id)
                DO UPDATE SET hostname     = EXCLUDED.hostname,
                              pid          = EXCLUDED.pid,
                              heartbeat_at = NOW()
            """, (self.line_id, self.hostname, self.pid))
            c.commit()
        print(f"[LOCK] OK Acquired singleton lock for line_id={self.line_id} "
              f"on {self.hostname} PID={self.pid}")
        # Start heartbeat thread
        self._thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True,
            name=f"collector-lock-hb-line{self.line_id}",
        )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SEC):
            try:
                with _db_conn() as c:
                    cur = c.cursor()
                    cur.execute("""
                        UPDATE mes_collector_locks
                           SET heartbeat_at = NOW()
                         WHERE line_id  = %s
                           AND hostname = %s
                           AND pid      = %s
                    """, (self.line_id, self.hostname, self.pid))
                    c.commit()
            except Exception as e:
                # DB hiccup — log once, keep trying.  If DB stays down
                # past 30 s our lock goes stale and another collector
                # CAN take over, which is correct behaviour.
                print(f"[LOCK] heartbeat failed: {e}")

    def release(self) -> None:
        self._stop.set()
        try:
            with _db_conn() as c:
                cur = c.cursor()
                cur.execute("""
                    DELETE FROM mes_collector_locks
                     WHERE line_id  = %s
                       AND hostname = %s
                       AND pid      = %s
                """, (self.line_id, self.hostname, self.pid))
                c.commit()
            print(f"[LOCK] released for line_id={self.line_id}")
        except Exception:
            pass



# ============================================================
# CONFIG LOADER
# ============================================================

def load_line_config(line_id: int) -> dict:
    conn = _db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Safety net — if a zombie 'idle in transaction' session is holding a
    # lock on any of these tables, fail loudly after 15 s instead of hanging
    # the whole collector process forever.
    try:
        cur.execute("SET statement_timeout = '15s'")
        cur.execute("SET lock_timeout = '10s'")
    except Exception:
        pass

    # parent_plc_id IS NULL → main PLC only (sub-machines handled separately).
    cur.execute("""
        SELECT l.*, p.plant_name,
               pc.id AS plc_id,
               pc.plc_ip, pc.plc_port,
               pc.ok_bit_address, pc.ng_bit_address,
               pc.status_address, pc.model_address,
               pc.sensor_ok_address, pc.process_seq_address, pc.override_address,
               pc.ideal_cycle_time, pc.max_allowed_cycle, pc.ok_ng_pulse_min_gap
        FROM mes_lines l
        JOIN mes_plants p ON p.id = l.plant_id
        JOIN mes_plc_configs pc ON pc.line_id = l.id AND pc.parent_plc_id IS NULL
        WHERE l.id = %s
    """, (line_id,))
    line = dict(cur.fetchone())

    cur.execute("""
        SELECT model_number, model_name FROM mes_model_mappings
        WHERE line_id = %s ORDER BY model_number
    """, (line_id,))
    models = {r["model_number"]: r["model_name"] for r in cur.fetchall()}

    cur.execute("""
        SELECT status_code, status_name, loss_type FROM mes_status_mappings
        WHERE line_id = %s ORDER BY status_code
    """, (line_id,))
    status_map = {r["status_code"]: {"name": r["status_name"], "loss": r["loss_type"]}
                  for r in cur.fetchall()}

    # 2026-05-18 — AUTO-INJECT synthetic BREAK row if none exists.
    # Without this, scheduled break windows fall back to IDLE (gray)
    # on the timeline because _find_break_status() returns (None,None)
    # and the override code path in _update_status sets status_code=0
    # (IDLE) as legacy fallback.  Operator complaint: "PRR BHAI YE IDLE
    # MEIN SWITCH HO JATI HAI" — the 12:00-12:35 lunch break window
    # was painting IDLE at 12:34:59 right before break ended.
    # The synthetic row is purely in-memory (status_map dict) — no DB
    # write — so this is safe to re-run on every collector start.  The
    # admin can override by adding a real row to mes_status_mappings
    # under Admin -> Production -> Status Colour; ours is only injected
    # when the existing rows have no break-typed entry.
    _has_break = any(
        isinstance(info, dict) and (
            (info.get("loss") == "break") or
            ((info.get("name") or "").upper() == "BREAK") or
            ((info.get("name") or "").upper().endswith("_BREAK"))
        )
        for info in status_map.values()
    )
    if not _has_break:
        # Find first free status_code starting at 99 and walking down.
        # We avoid the documented codes 0-9 (RUNNING / IDLE / loss
        # buckets) so an operator-defined break row never clobbers a
        # production status.  99 is conventional for "synthetic" codes
        # across our other line configs.
        _free_code = 99
        while _free_code in status_map and _free_code > 10:
            _free_code -= 1
        status_map[_free_code] = {"name": "BREAK", "loss": "break"}
        print(f"[CONFIG] line_id={line_id}: no BREAK row found in "
              f"mes_status_mappings — auto-injected synthetic code "
              f"{_free_code} (name='BREAK', loss='break').  Timeline "
              f"will now paint scheduled break windows in BREAK blue "
              f"instead of IDLE gray.  To customize the code/name, add "
              f"a real row under Admin -> Production -> Status Colour.",
              flush=True)

    cur.execute("""
        SELECT * FROM mes_shift_configs
        WHERE line_id = %s ORDER BY shift_name
    """, (line_id,))
    shifts = {r["shift_name"]: dict(r) for r in cur.fetchall()}

    cur.execute("""
        SELECT * FROM mes_hourly_slots
        WHERE line_id = %s ORDER BY shift_name, slot_order
    """, (line_id,))
    slots_raw = cur.fetchall()

    hourly_plan       = {}
    slot_boundaries   = {}
    slot_to_db_prefix = {}
    for s in slots_raw:
        sn = s["shift_name"]
        sl = s["slot_label"]
        if sn not in hourly_plan:
            hourly_plan[sn] = {}
        hourly_plan[sn][sl] = s["plan_pieces"]
        slot_boundaries[sl] = (s["start_time"], s["end_time"], s["crosses_midnight"])
        slot_to_db_prefix[sl] = s["db_column_prefix"]

    cur.execute("""
        SELECT break_name, start_time, end_time, crosses_midnight
        FROM mes_break_configs
        WHERE line_id = %s ORDER BY start_time
    """, (line_id,))
    breaks = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT id, poka_yoke_no, side, poka_yoke_name,
               model, bit, value, machine_name,
               sheet_name, alert_level
        FROM mes_poka_yoke_rules
        WHERE line_id = %s AND is_active = true AND bit IS NOT NULL
        ORDER BY bit
    """, (line_id,))
    poka_rules = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "line_id":          line_id,
        "zone_id":          line.get("zone_id"),
        "main_plc_id":      line.get("plc_id"),
        "line_name":        line["line_name"],
        "table_name":       line["db_table_name"],
        "plc_ip":           line["plc_ip"],
        "plc_port":         line["plc_port"],
        "ok_bit":           line["ok_bit_address"],
        "ng_bit":           line["ng_bit_address"],
        "status_addr":      line["status_address"],
        "model_addr":       line["model_address"],
        "sensor_ok_addr":   line["sensor_ok_address"],
        "process_seq_addr": line["process_seq_address"],
        "override_addr":    line["override_address"],
        "ideal_ct":         float(line["ideal_cycle_time"]),
        "max_ct":           float(line["max_allowed_cycle"]),
        "pulse_gap":        float(line["ok_ng_pulse_min_gap"]),
        "models":           models,
        "status_map":       status_map,
        "shifts":           shifts,
        "hourly_plan":      hourly_plan,
        "slot_boundaries":  slot_boundaries,
        "slot_to_db":       slot_to_db_prefix,
        "breaks":           breaks,
        "poka_rules":       poka_rules,
    }


# ============================================================
# SUB-MACHINE LOADER
# ============================================================

def _ensure_semi_auto_schema_collector(conn) -> None:
    """Mirror of routers/lines.py:_ensure_semi_auto_schema — kept in
    sync here so the collector can pull SA columns even when MES has
    been restarted and no admin call has touched the schema yet.
    Idempotent: Postgres `IF NOT EXISTS` skips the work after first run."""
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
              ADD COLUMN IF NOT EXISTS sa_register_scales  JSONB
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
        print(f"[SEMI-AUTO] collector schema-ensure failed: {exc}")


def load_submachines(main_plc_id: int) -> list:
    """Return every sub-machine whose parent_plc_id matches the main PLC.
    Each dict has the fields the sub-poller needs: id, plc_ip, plc_port,
    count_bit (stored in ok_bit_address), ideal_ct, machine_name, line_id,
    plus the optional Semi-Auto data-capture config (sa_enabled +
    addresses + register names/scales).  Returns [] if no sub-machines
    configured — safe for legacy lines.
    """
    if not main_plc_id:
        return []
    try:
        conn = _db_conn()
        _ensure_semi_auto_schema_collector(conn)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, plc_ip, plc_port, line_id,
                   NULLIF(TRIM(ok_bit_address), '') AS count_bit,
                   NULLIF(TRIM(ng_bit_address), '') AS ng_bit,
                   ideal_cycle_time                 AS ideal_ct,
                   machine_name,
                   COALESCE(sa_enabled, FALSE)      AS sa_enabled,
                   NULLIF(TRIM(sa_fetch_bit), '')   AS sa_fetch_bit,
                   NULLIF(TRIM(sa_part_code_addr), '') AS sa_part_code_addr,
                   sa_part_code_len,
                   NULLIF(TRIM(sa_data_addr), '')   AS sa_data_addr,
                   sa_data_len,
                   NULLIF(TRIM(sa_time_addr), '')   AS sa_time_addr,
                   sa_time_len,
                   sa_register_names,
                   sa_register_scales
            FROM mes_plc_configs
            WHERE parent_plc_id = %s
            ORDER BY id
        """, (main_plc_id,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"[SUB] load_submachines failed: {e}")
        return []


# ============================================================
# CYCLE TIME TRACKER
# ============================================================

class CycleTimeTracker:
    def __init__(self, ideal_ct: float, max_ct: float, window: int = 20):
        self.ideal_ct    = ideal_ct
        self.max_ct      = max_ct
        self.window      = window
        self.cycle_times: list = []
        self.last_pulse  = None
        self.speed_loss  = 0.0
        self.is_running  = False
        self.pulse_recv  = False

    def set_running(self, running: bool):
        self.is_running = running
        if not running:
            self.last_pulse = None
            self.pulse_recv = False

    def on_pulse(self, now: float) -> None:
        if not self.is_running:
            self.last_pulse = None
            return
        if self.last_pulse is None:
            self.last_pulse = now
            self.pulse_recv = True
            return
        ct = round(now - self.last_pulse, 2)
        self.last_pulse = now
        self.pulse_recv = True
        if 1.0 <= ct <= 300.0:
            self._add(ct)

    def _add(self, ct: float):
        self.cycle_times.append(ct)
        if len(self.cycle_times) > self.window:
            self.cycle_times.pop(0)
        if ct > self.ideal_ct:
            self.speed_loss += ct - self.ideal_ct
        elif ct < self.ideal_ct:
            self.speed_loss = max(0.0, self.speed_loss - (self.ideal_ct - ct))

    def check_continuous(self, now: float) -> float:
        if not self.is_running or self.last_pulse is None or not self.pulse_recv:
            return 0.0
        gap = now - self.last_pulse
        if self.max_ct < gap < 300:
            extra = min(1.0, gap - self.max_ct)
            self.speed_loss += extra
            return extra
        return 0.0

    def stats(self) -> dict:
        cts = self.cycle_times
        if not cts:
            return {"avg": self.ideal_ct, "min": self.ideal_ct,
                    "max": self.ideal_ct, "std": 0.0, "list": []}
        return {
            "avg":  round(sum(cts) / len(cts), 2),
            "min":  round(min(cts), 2),
            "max":  round(max(cts), 2),
            "std":  round(statistics.stdev(cts) if len(cts) > 1 else 0.0, 2),
            "list": cts.copy(),
        }

    def ct_dict(self) -> dict:
        s = self.stats()
        d = {f"ct{i}": (s["list"][-(i)] if i <= len(s["list"]) else None)
             for i in range(1, 21)}
        d.update({"ct_avg_20": s["avg"], "min_ct": s["min"],
                  "max_ct": s["max"], "std_dev_ct": s["std"],
                  "speed_loss": self.speed_loss})
        return d

    def reset(self):
        self.speed_loss  = 0.0
        self.last_pulse  = None
        self.pulse_recv  = False
        self.cycle_times.clear()


# ============================================================
# POKA YOKE MONITOR
# ============================================================

class PokaYokeMonitor:
    def __init__(self, rules: list, line_id: int):
        self.rules    = rules
        self.line_id  = line_id
        self.d_rules: list    = []
        self.poka_state: dict = {}
        self._last_reload     = 0.0
        self._ng_streak          = 0
        self._last_event_time: dict = {}
        # ── New PY Master / Assignment based bypass detection ────────────────
        self._py_configs: list = []
        self._py_last_reload: float = 0.0
        self._py_bypass_state: dict = {}  # {(py_no, model_bit, reg) → last_code}
        # ── Sensor Health — passive X-bit monitoring (READ-ONLY) ──────
        # Sample every unique sensing X-bit configured across all PYs
        # roughly once a second.  Per-bit state tracks last_toggle_ts in
        # memory only.  If a bit goes >900 s (15 min) without any value
        # change, status flips to 'stuck' and ONE SENSOR_HEALTH email
        # fires.  Natural toggle resets the timer + clears the email flag.
        # No PLC writes — collector NEVER overwrites sensor bits.
        self._x_state:             dict  = {}   # {x_bit → state dict}
        # 2026-05-22 — Sweep cadence reduced 1.0 → 0.2 sec to catch
        # short sensor pulses (typical part-pass = 50-500ms).  Earlier
        # 1-sec sweep missed any toggle shorter than 1 sec, leading to
        # "sensor stuck" false positives even when the bit was actually
        # firing 200ms pulses on every cycle.  Publishing rate stays
        # at 10s (backend doesn't need 5Hz updates — just the sweep
        # internal state needs to track every toggle).
        self._x_track_interval:    float = 0.2   # was 1.0
        self._x_track_last:        float = 0.0
        # 2026-05-22 — Track toggle count per bit per minute so the
        # operator can see "is this sensor actually firing per cycle?"
        # at a glance.  Reset every 60s.
        self._x_toggle_counts:     dict  = {}    # {x_bit → int}
        self._x_toggle_window_ts:  float = 0.0
        self._publish_interval:    float = 10.0
        self._publish_last:        float = 0.0
        self._stuck_threshold_sec: int   = 900   # 15 min — email after this
        self._partition_rules()

    def _partition_rules(self):
        self.d_rules     = [r for r in self.rules if r.get("bit")]
        self.logic_rules = [r for r in self.rules if not r.get("bit")]
        print(f"[POKA] {len(self.d_rules)} D-register rules, "
              f"{len(self.logic_rules)} logic rules loaded")

    def reload_rules_from_db(self, line_id: int):
        now = time.time()
        if now - self._last_reload < 600:
            return
        self._last_reload = now
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, poka_yoke_no, side, poka_yoke_name,
                       model, bit, value, machine_name,
                       sheet_name, alert_level
                FROM mes_poka_yoke_rules
                WHERE line_id = %s AND is_active = true AND bit IS NOT NULL
                ORDER BY bit
            """, (line_id,))
            self.d_rules = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
            print(f"[POKA] Reloaded {len(self.d_rules)} D-register rules")
        except Exception as e:
            print(f"[POKA] Reload error: {e}")

    def check_d_registers(self, plc, shift_name: str):
        if not self.d_rules or plc is None:
            return
        d_nums = {}
        for rule in self.d_rules:
            bit = str(rule.get("bit", "") or "").strip().upper()
            if bit.startswith("D"):
                try:
                    num = int(bit[1:])
                    d_nums[bit] = num
                except ValueError:
                    pass
        if not d_nums:
            return
        nums  = list(d_nums.values())
        min_d = min(nums)
        max_d = max(nums)
        count = max_d - min_d + 1
        try:
            values  = plc.batchread_wordunits(headdevice=f"D{min_d}", readsize=count)
            val_map = {f"D{min_d + i}": (values[i] or 0) for i in range(count)}
        except Exception as e:
            print(f"[POKA] PLC read error: {e}")
            return
        for rule in self.d_rules:
            bit         = str(rule.get("bit", "") or "").strip().upper()
            # Desired/expected value: can be int (0/1/2) OR label (on/off/pass).
            # "pass" / "bypass" → skip this check (no alert for this model).
            raw_val     = rule.get("value")
            val_str     = str(raw_val if raw_val is not None else "1").strip().lower()
            if val_str in ("pass", "bypass", "skip", ""):
                self.poka_state[bit] = val_map.get(bit, 0)
                continue
            # Map label → numeric. Accept both "on"/"1" and "off"/"0".
            if val_str in ("on", "1", "true", "yes"):
                trigger_val = 1
            elif val_str in ("off", "0", "false", "no"):
                trigger_val = 0
            else:
                try:
                    trigger_val = int(val_str)
                except ValueError:
                    continue  # unknown label → skip
            current_val = val_map.get(bit, 0)
            last_val    = self.poka_state.get(bit, 0)
            if current_val == trigger_val and last_val != trigger_val:
                poka_name = rule.get("poka_yoke_name", bit)
                side      = rule.get("side", "ALL")
                machine   = rule.get("machine_name", "")
                level     = rule.get("alert_level", "WARNING")
                print(f"[POKA] FAULT ▶ {bit}={current_val} | {poka_name} | {side} | {machine}")
                try:
                    requests.post(
                        f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                        json={
                            "line_id":      self.line_id,
                            "rule_id":      rule.get("id"),
                            "rule_type":    "SENSOR_BYPASS",
                            "alert_level":  level,
                            "shift_name":   shift_name,
                            "plc_value":    str(current_val),
                            "context_json": (
                                f'{{"bit":"{bit}",'
                                f'"value":{current_val},'
                                f'"rule":"{poka_name}",'
                                f'"side":"{side}",'
                                f'"machine":"{machine}",'
                                f'"py_no":"{rule.get("poka_yoke_no","")}",'
                                f'"model":"{rule.get("model","all")}"}}'
                            ),
                        },
                        timeout=2,
                    )
                except Exception as e:
                    print(f"[POKA] Event post failed: {e}")
            self.poka_state[bit] = current_val

    # ── NEW: PY Master based bypass detection ──────────────────────────────
    def reload_py_configs(self, line_id: int):
        """Pull PY Master + per-model assignments from DB every 20s."""
        now = time.time()
        if now - self._py_last_reload < 20:
            return
        self._py_last_reload = now
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Make absolutely sure the new sensing_bits column exists before
            # we SELECT it — collector may be running against a DB whose
            # schema migration hasn't been triggered (the backend's
            # _ensure_py_register_col only runs on /master/ access).
            try:
                cur.execute(
                    "ALTER TABLE mes_py_master "
                    "ADD COLUMN IF NOT EXISTS sensing_bits VARCHAR(100)"
                )
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
            # Defensive SELECT — if for any reason sensing_bits isn't
            # available, fall back to the column-less form so the bypass
            # detector keeps working.
            try:
                cur.execute("""
                    SELECT p.id         AS py_id,
                           p.py_no,
                           p.description,
                           p.bit        AS register_addr,
                           COALESCE(p.register_count, 1) AS register_count,
                           p.sensing_bits,
                           p.model_type, p.side
                    FROM mes_py_master p
                    WHERE p.is_active = true
                """)
            except Exception as e_pri:
                print(f"[POKA-BYPASS] sensing_bits SELECT failed ({e_pri}); "
                      f"retrying without it")
                try: conn.rollback()
                except Exception: pass
                cur.execute("""
                    SELECT p.id         AS py_id,
                           p.py_no,
                           p.description,
                           p.bit        AS register_addr,
                           COALESCE(p.register_count, 1) AS register_count,
                           NULL         AS sensing_bits,
                           p.model_type, p.side
                    FROM mes_py_master p
                    WHERE p.is_active = true
                """)
            pys = cur.fetchall()
            py_map = {p["py_id"]: {**dict(p), "assignments": []} for p in pys}
            if py_map:
                cur.execute("""
                    SELECT a.py_id, a.model_id,
                           m.bit_number AS model_bit,
                           a.desired_bit, a.desired_value, a.desired_value_2
                    FROM mes_py_assignments a
                    JOIN mes_py_model_master m
                      ON m.id = a.model_id AND m.is_active = true
                    WHERE a.py_id = ANY(%s) AND m.bit_number IS NOT NULL
                """, (list(py_map.keys()),))
                for r in cur.fetchall():
                    py_map[r["py_id"]]["assignments"].append(dict(r))
            self._py_configs = list(py_map.values())
            cur.close(); conn.close()
            total_asgn = sum(len(p["assignments"]) for p in self._py_configs)
            print(f"[POKA-BYPASS] Reloaded {len(self._py_configs)} PYs, "
                  f"{total_asgn} model assignments")
        except Exception as e:
            print(f"[POKA-BYPASS] Reload error: {e}")

    @staticmethod
    def _decode_code(code: int, reg_cnt: int) -> str:
        if code == 0: return "PASS"
        if reg_cnt == 1:
            return {1: "OFF", 2: "ON"}.get(code, f"code{code}")
        return {1: "OFF,OFF", 2: "OFF,ON", 3: "ON,OFF", 4: "ON,ON"}.get(code, f"code{code}")

    @staticmethod
    def _expected_codes_1reg(dv):
        try:    dv = int(dv) if dv is not None else None
        except (ValueError, TypeError): return None
        if dv is None or dv == 0: return None
        return {dv}

    @staticmethod
    def _expected_codes_2reg(dv1, dv2):
        # cast both sides to int (DB column may be VARCHAR)
        try:    dv1 = int(dv1) if dv1 is not None else None
        except (ValueError, TypeError): dv1 = None
        try:    dv2 = int(dv2) if dv2 is not None else None
        except (ValueError, TypeError): dv2 = None
        if (dv1 is None or dv1 == 0) and (dv2 is None or dv2 == 0):
            return None
        def opts(v):
            if v is None or v == 0: return {1, 2}
            return {int(v)}
        out = set()
        for o1 in opts(dv1):
            for o2 in opts(dv2):
                if   o1 == 1 and o2 == 1: out.add(1)
                elif o1 == 1 and o2 == 2: out.add(2)
                elif o1 == 2 and o2 == 1: out.add(3)
                elif o1 == 2 and o2 == 2: out.add(4)
        return out or None

    def check_py_bypass(self, plc, shift_name: str, current_model_bit):
        """Per-cycle: compare each PY's PLC register to the user-configured
        desirable output for the active model. Fires SENSOR_BYPASS on mismatch.

        2026-05-26 — DISABLED.  Operator: "meri plc side issue nhi h jb
        m koi or code chalauga to sab shi dikhayega ye sirf tera excuse
        h hmesa".  The auto desired_value comparison was generating
        false-positive alarms whenever PLC published bit-mask values
        that the assignment table didn't anticipate.  Until the
        assignment table is rebuilt to match the actual PLC encoding
        (operator will redo this manually), suppress all auto event
        creation.  Function still runs (it tracks read health for the
        all-fail escalation), but never POSTs alarm events."""
        if True:
            return                 # ←  hard-disable until config redone
        if not plc or not self._py_configs or not current_model_bit:
            return

        import re as _re
        BIT_PREFIXES  = ("X", "Y", "M", "L", "F", "B", "T", "C", "S")
        REG_RE = _re.compile(
            r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+",
            _re.IGNORECASE,
        )

        # 2026-05-23 — Track per-call read success/fail so we can raise
        # when EVERY read in this call dies (signals connection dropped).
        # PY-CHECK loop's reconnect block runs only when an exception
        # bubbles up; without this signal the silent `continue` swallowed
        # all-dead-connection cases and the thread limped forward with
        # zero successful reads for hours.
        _pyb_total_reads = 0
        _pyb_failed_reads = 0
        _pyb_last_err = ""

        for py in self._py_configs:
            raw = (py.get("register_addr") or "").upper()
            regs = REG_RE.findall(raw)
            if not regs:
                continue

            asgn = next(
                (a for a in py["assignments"] if a.get("model_bit") == current_model_bit),
                None,
            )
            if not asgn:
                continue

            reg_cnt = int(py.get("register_count") or 1)
            if reg_cnt == 1:
                expected = self._expected_codes_1reg(asgn.get("desired_value"))
            else:
                expected = self._expected_codes_2reg(
                    asgn.get("desired_value"), asgn.get("desired_value_2"))
            if expected is None:
                continue

            for reg in regs:
                prefix = reg[0].upper()
                is_bit = prefix in BIT_PREFIXES
                _pyb_total_reads += 1
                try:
                    if is_bit:
                        vals = plc.batchread_bitunits(headdevice=reg, readsize=1)
                    else:
                        vals = plc.batchread_wordunits(headdevice=reg, readsize=1)
                    code = int(vals[0] or 0)
                except Exception as e:
                    print(f"[POKA-BYPASS] PLC read {reg} failed: {e}")
                    _pyb_failed_reads += 1
                    _pyb_last_err = f"{reg}: {str(e)[:60]}"
                    continue

                key = (py["py_no"], current_model_bit, reg)

                if is_bit:
                    _dv = asgn.get("desired_value")
                    try:    dv = int(_dv) if _dv is not None else None
                    except (ValueError, TypeError): dv = None
                    if dv == 1:
                        match = (code == 0); human_expected = "OFF"
                    elif dv == 2:
                        match = (code == 1); human_expected = "ON"
                    else:
                        continue
                    human_actual = "ON" if code == 1 else "OFF"
                else:
                    # 2026-05-26 — PASS auto-clear REMOVED.
                    # Operator: "py me kya issue h apne app shi ho rha h
                    # apne app fail no change fir bhi ok".  Earlier we
                    # treated code 0 as auto-PASS, which made the alarm
                    # flap (fail → clear → fail → clear) as PLC bounced
                    # between bad value and 0.  Now strict match only —
                    # alarm clears only when PLC actually publishes the
                    # expected value.  No more flicker.
                    match = (code in expected)
                    human_actual   = self._decode_code(code, reg_cnt)
                    human_expected = " | ".join(
                        self._decode_code(c, reg_cnt) for c in sorted(expected))

                if match:
                    # Only auto-clear when PLC publishes EXPECTED value.
                    # Transient code 0 / unrelated values do NOT clear.
                    if key in self._py_bypass_state:
                        try:
                            import json as _json
                            requests.post(
                                f"{BACKEND_URL}/api/poka-yoke/events/auto-ack",
                                json={"line_id": self.line_id,
                                      "py_no": py["py_no"],
                                      "register": reg},
                                timeout=2,
                            )
                        except Exception as e:
                            print(f"[POKA-BYPASS] Auto-ack failed: {e}")
                    self._py_bypass_state.pop(key, None)
                    continue

                # 2026-05-26 — STABILITY GUARD.
                # Operator: "ye dekh ye ho rha h baar baar isko stable kr".
                # PLC bounces between bad codes (16 → 0 → 16 → ...) caused
                # a NEW alarm event for EVERY transition — 110 events in
                # 7 h for one stuck sensor.  Fix: once an alarm has fired
                # for (py, reg), suppress further events until either
                # (a) PLC publishes the expected value (clears via the
                #     `match` branch above), or
                # (b) the operator acknowledges the alarm in the UI.
                # The stuck PLC bouncing through different bad codes is
                # the SAME ongoing fault, not new ones.
                if key in self._py_bypass_state:
                    # Already in fault state — just update last-seen code,
                    # but DON'T fire another event.
                    self._py_bypass_state[key] = code
                    continue
                self._py_bypass_state[key] = code

                all_regs_str = ",".join(regs) if len(regs) > 1 else reg
                print(f"[POKA-BYPASS] {py['py_no']} [{reg}] mismatch on model bit "
                      f"{current_model_bit}: PLC={human_actual}, expected={human_expected}")

                try:
                    import json as _json
                    requests.post(
                        f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                        json={
                            "line_id":     self.line_id,
                            "rule_id":     None,
                            "rule_type":   "SENSOR_BYPASS",
                            "alert_level": "WARNING",
                            "shift_name":  shift_name,
                            "plc_value":   str(code),
                            "context_json": _json.dumps({
                                "py_no":          py["py_no"],
                                "py_name":        py.get("description") or "",
                                "register":       reg,
                                "registers_all":  all_regs_str,
                                "register_count": reg_cnt,
                                "model_bit":      current_model_bit,
                                "actual":         human_actual,
                                "expected":       human_expected,
                                "desired_bit":    asgn.get("desired_bit"),
                            }),
                        },
                        timeout=2,
                    )
                except Exception as e:
                    print(f"[POKA-BYPASS] Event post failed: {e}")

        # 2026-05-23 — ALL-FAIL ESCALATION (mirror of track_sensors_health).
        # If every single PLC read in this call failed, the connection is
        # dead — raise so PY-CHECK loop's reconnect block actually runs.
        if _pyb_total_reads > 0 and _pyb_failed_reads == _pyb_total_reads:
            raise RuntimeError(
                f"All {_pyb_failed_reads} bypass-check reads failed "
                f"(connection appears dead). Last error: "
                f"{_pyb_last_err or 'unknown'}"
            )

    # ── Sensor Health — passive X-bit monitoring (READ-ONLY, no PLC writes) ─
    #
    # We sample every unique sensing X-bit (configured per-PY in mes_py_master)
    # roughly once per second.  For every bit we maintain a tiny in-memory
    # state struct whose key field is `last_toggle_ts` — the moment the bit
    # last changed value.  No DB persistence; state is rebuilt on collector
    # restart.  Verdict is purely passive:
    #
    #   • bit toggled within stuck threshold → status='alive'
    #   • no toggle for >900 s (15 min)      → status='stuck' + 1 email
    #   • later natural toggle               → status='alive', email flag clears
    #
    # The collector NEVER writes back to the PLC for sensor health checks.
    # If a sensor truly stops toggling, the operator sees 'stuck' in the UI
    # plus the email and physically inspects.

    def _x_state_default(self, val: int, ts: float) -> dict:
        # 2026-05-27 — Initial status is now "unknown", not "alive".
        # Operator pointed out the bug: if a sensor bit has NEVER been
        # observed toggling (e.g. line hasn't produced anything yet today),
        # the dashboard was still showing "9/9 alive" — falsely confirming
        # health on bits the collector has only READ once.  "alive" must
        # require an actual observed toggle.  Status flips to "alive" the
        # moment we see `value` change on a real PLC poll (see toggle
        # block below).  Until then, the PY rolls up as "unknown" → the
        # dashboard shows WARNING instead of OK, which matches reality.
        return {
            "value":           val,
            "last_toggle_ts":  ts,
            "first_seen_ts":   ts,       # used to suppress sliding anchor for never-toggled bits
            "ever_toggled":    False,    # flips True on first natural value change
            "status":          "unknown",  # unknown | alive | stuck
            "stuck_emailed":   False,    # one-shot guard per stuck event
        }

    def _fire_health_event(self, x_bit: str, reason: str, py: dict | None):
        """Fire a SENSOR_HEALTH event into the existing alert pipeline."""
        import json as _json
        print(f"[POKA-HEALTH] FAIL ▶ {x_bit} — {reason}")
        try:
            requests.post(
                f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                json={
                    "line_id":     self.line_id,
                    "rule_id":     None,
                    "rule_type":   "SENSOR_HEALTH",
                    "alert_level": "WARNING",
                    "shift_name":  "",
                    "plc_value":   reason,
                    "context_json": _json.dumps({
                        "py_id":   (py or {}).get("py_id"),
                        "py_no":   (py or {}).get("py_no"),
                        "py_name": (py or {}).get("description"),
                        "x_bit":   x_bit,
                        "d_bit":   (py or {}).get("register_addr"),
                        "reason":  reason,
                    }),
                },
                timeout=2,
            )
        except Exception as e:
            print(f"[POKA-HEALTH] Event post failed: {e}")

    def track_sensors_health(self, plc):
        """Passive sensing-X-bit health monitor — READ ONLY.  Sample at
        ~1 Hz; track each bit's last natural-toggle timestamp in memory.
          • bit value changes              → status='alive', timer reset
          • no toggle for >stuck_threshold → status='stuck' + 1 email
          • later natural toggle on stuck  → status='alive', email guard clears

        The collector NEVER writes back to the PLC.  If a sensor stays
        stuck, the operator sees the status + receives the email and
        inspects the wiring/sensor physically.

        IMPORTANT: this function publishes a snapshot every
        `_publish_interval` seconds REGARDLESS of whether the PLC was
        reachable on this tick.  Earlier the early-return on
        `plc is None or not self._py_configs` froze the UI's "Last
        snapshot" timestamp whenever the PLC blinked or PY configs were
        still loading; now we always tick the snapshot forward so the
        operator sees that the monitor itself is alive."""
        now = time.time()
        if now - self._x_track_last < self._x_track_interval:
            return
        self._x_track_last = now

        import re as _re
        REG_RE = _re.compile(
            r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+",
            _re.IGNORECASE,
        )
        BIT_PREFIXES = ("X", "Y", "M", "L", "F", "B")

        # Map every unique sensing X-bit → first PY that references it.
        # 2026-05-15 — REMOVED the current-model filter from the collector.
        # It was incorrectly excluding every PY when `_cur_model` didn't
        # match the assignment rows (or when the assignment table linkage
        # was off), leaving the sweep cache empty and the maintenance
        # dashboard stuck on "WAITING".  The backend's `/sensor-health/`
        # endpoint already filters by current model when serving the UI,
        # so the collector's job is the simpler one: publish EVERY
        # configured sensing bit's toggle status.  This way the sweep
        # cache is always populated and the UI's model-filter handles
        # which subset to render.
        py_by_xbit: dict = {}
        for py in self._py_configs:
            for tok in REG_RE.findall((py.get("sensing_bits") or "").upper()):
                py_by_xbit.setdefault(tok, py)

        # Throttled diagnostic — once every 30 s surface the publish state
        # so the operator can see WHY "NO SNAPSHOT" might be showing.
        _diag = (now - getattr(self, "_sweep_diag_last", 0) >= 30)
        if _diag:
            self._sweep_diag_last = now

        # If no PY rules have sensing_bits configured we still publish a
        # heartbeat (empty entry list with a fresh swept_at) so the UI
        # can show "0 sensors configured" rather than "NO SNAPSHOT".  This
        # is a state, not an error — the bypass detector keeps working
        # off `register_addr` regardless of this column.
        if not py_by_xbit:
            if now - self._publish_last >= self._publish_interval:
                self._publish_health_snapshot(now, {})
                self._publish_last = now
            if _diag:
                sample = [(p.get("py_no"), p.get("sensing_bits")) for p in self._py_configs[:3]]
                print(f"[POKA-SWEEP] no sensing_bits configured "
                      f"(_py_configs={len(self._py_configs)}, sample={sample}) "
                      f"— published empty heartbeat", flush=True)
            return

        # PLC blipped (configs loaded but socket lost) — skip the bit
        # reads but DO refresh the published snapshot so the UI's
        # "Last snapshot" clock keeps moving.  Existing _x_state entries
        # are preserved, so the table doesn't go blank.
        if plc is None:
            if now - self._publish_last >= self._publish_interval:
                self._publish_health_snapshot(now, py_by_xbit)
                self._publish_last = now
            if _diag:
                print(f"[POKA-SWEEP] plc=None, published "
                      f"{len(self._x_state)} cached entries", flush=True)
            return

        read_ok = 0
        read_fail = 0
        last_err = ""
        for bit, py in py_by_xbit.items():
            prefix = bit[0].upper()
            try:
                if prefix in BIT_PREFIXES:
                    vals = plc.batchread_bitunits(headdevice=bit, readsize=1)
                else:
                    vals = plc.batchread_wordunits(headdevice=bit, readsize=1)
                val = 1 if int(vals[0] or 0) else 0
                read_ok += 1
            except Exception as _e:
                read_fail += 1
                last_err = f"{bit}: {str(_e)[:60]}"
                continue

            state = self._x_state.get(bit)
            if state is None:
                self._x_state[bit] = self._x_state_default(val, now)
                continue

            # Natural transition observed — sensor responding on its own.
            if state["value"] != val:
                state["value"]          = val
                state["last_toggle_ts"] = now
                state["stuck_emailed"]  = False
                state["ever_toggled"]   = True  # promote to verified-alive
                # First real toggle promotes unknown→alive.  Subsequent
                # toggles also clear "stuck" if the sensor recovers.
                if state["status"] in ("stuck", "unknown"):
                    state["status"] = "alive"
                # 2026-05-22 — Per-minute toggle counter for diagnostics.
                # Operator can see "is X15 firing on every cycle?"
                # without waiting for the stuck-threshold to flip.
                self._x_toggle_counts[bit] = self._x_toggle_counts.get(bit, 0) + 1

            # 2026-05-21 / 2026-05-22 — PRODUCTION-WINDOW GATE.
            # Sensors only toggle when parts move through the line.  When
            # the line is IDLE / BREAKDOWN / SCHEDULED-BREAK / SHIFT-GAP,
            # "no toggle" is the expected resting state — NOT a fault.
            # Operator complaint after 2026-05-22 morning: tea break
            # (10:00-10:10) flagged all 9 sensors as "stuck for 16 min"
            # because the earlier fix only checked `is_running` — PLC
            # often holds is_running=True during a tea break (machine
            # READY but operators away).  The fix now also honours the
            # SAME gate `_should_record_pulse()` uses for cycle counts:
            # skip stuck escalation during break + gap windows AND while
            # is_running is False.
            # Engine pushes its production-window state onto the Poka
            # instance just before each call (see PY-CHECK loop in
            # CollectorEngine._tick) so this method doesn't need to call
            # back into the engine.
            should_track = bool(getattr(self, "sensors_should_track", True))
            if not should_track:
                # Slide the toggle anchor forward so elapsed stays 0
                # while not producing.  No status transition possible.
                # 2026-05-27 — Only slide for bits that have ALREADY been
                # observed toggling at least once (status == alive or
                # was alive before stuck).  A bit we've never seen toggle
                # must remain "unknown" — sliding its anchor would mask
                # the fact that we've never verified it.  Without this,
                # a sensor wired wrong / never wired would show "alive"
                # all shift just because the line was idle.
                if state.get("ever_toggled"):
                    state["last_toggle_ts"] = now
                continue

            # Stuck > threshold → flag + 1 email.  Only while running.
            elapsed = now - state["last_toggle_ts"]
            if state["status"] == "alive" and elapsed > self._stuck_threshold_sec:
                state["status"] = "stuck"
                if not state["stuck_emailed"]:
                    self._fire_health_event(
                        bit,
                        f"{bit}:no-toggle for {int(elapsed)}s during RUNNING "
                        f"(>{self._stuck_threshold_sec}s threshold)",
                        py,
                    )
                    state["stuck_emailed"] = True

        if now - self._publish_last >= self._publish_interval:
            self._publish_health_snapshot(now, py_by_xbit)
            self._publish_last = now
            # Throttled summary so operator can confirm pipeline is alive.
            if _diag:
                msg = (f"[POKA-SWEEP] line={self.line_id} "
                       f"tracked={len(py_by_xbit)} state={len(self._x_state)} "
                       f"reads ok={read_ok} fail={read_fail}")
                if read_fail and last_err:
                    msg += f" lastErr={last_err}"
                print(msg, flush=True)

        # 2026-05-23 — CRITICAL ALL-FAIL ESCALATION.
        # Operator complaint: sensors all stuck since 09:16 (1h 36m no
        # update) despite PY-CHECK thread running.  Root cause: every
        # PLC read here was catching its own exception with `continue`,
        # so the function returned normally even when EVERY read failed.
        # PY-CHECK loop's reconnect logic depends on tick_exc being set
        # by this function — without that signal, the dead connection
        # was never replaced and stayed dead all day.
        # Fix: when read_fail equals the number of bits attempted AND
        # read_ok is zero (= dead connection, not just one bad bit),
        # raise the last exception so the PY-CHECK reconnect block
        # actually runs.  Single-bit failures still continue silently
        # (might just be a misconfigured X-bit address).
        if py_by_xbit and read_ok == 0 and read_fail == len(py_by_xbit):
            raise RuntimeError(
                f"All {read_fail} sensor reads failed (connection appears "
                f"dead). Last error: {last_err or 'unknown'}"
            )

        # 2026-05-22 — Per-minute toggle-rate log.  Helps operator verify
        # each sensor is actually firing on cycles.  Resets every 60s.
        if self._x_toggle_window_ts == 0.0:
            self._x_toggle_window_ts = now
        elif now - self._x_toggle_window_ts >= 60.0:
            if self._x_toggle_counts:
                items = sorted(self._x_toggle_counts.items())
                summary = " ".join(f"{b}={n}" for b, n in items)
                print(f"[POKA-TOGGLE] last 60s edges per bit: {summary}",
                      flush=True)
            self._x_toggle_counts.clear()
            self._x_toggle_window_ts = now

    def _publish_health_snapshot(self, now: float, py_by_xbit: dict):
        from datetime import datetime as _dt
        swept_at = _dt.now().isoformat(timespec="seconds")
        entries = []
        # 2026-05-14 — emit one row per CONFIGURED sensing X-bit (instead
        # of only those with a successful read).  Operator now sees every
        # PY's configured sensor regardless of whether the first PLC read
        # has landed; the row falls back to "unknown" status until a real
        # value is captured, which is more honest than "NO SNAPSHOT" when
        # the collector is alive but the PLC is slow / a single bit
        # address is bad.
        all_bits = set(py_by_xbit.keys()) | set(self._x_state.keys())
        for x_bit in sorted(all_bits):
            py       = py_by_xbit.get(x_bit, {}) or {}
            state    = self._x_state.get(x_bit)
            if state is not None:
                last_iso = _dt.fromtimestamp(state["last_toggle_ts"]).isoformat(timespec="seconds")
                cur_val  = state["value"]
                ago      = round(now - state["last_toggle_ts"], 1)
                status   = state["status"]
            else:
                last_iso = None
                cur_val  = None
                ago      = None
                status   = "unknown"
            # Stuck-for is the time since last toggle when status is stuck;
            # surfaced as its own field so the maintenance panel doesn't
            # have to reconstruct it from ago + status.
            stuck_for = ago if (status == "stuck" and ago is not None) else None
            entries.append({
                "bit":                 x_bit,
                "x_bit":               x_bit,
                "d_bit":               py.get("register_addr"),
                "current_value":       cur_val,
                "last_toggle_at":      last_iso,
                "last_toggle_ago_sec": ago,
                "stuck_for_sec":       stuck_for,
                "status":              status,
                "py_id":               py.get("py_id"),
                "py_no":               py.get("py_no"),
                "py_name":             py.get("description"),
                "sensing_bits":        py.get("sensing_bits"),
            })
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/poka-yoke/sensor-sweep/update",
                json={"line_id": self.line_id, "entries": entries,
                      "swept_at": swept_at},
                timeout=3,
            )
            if resp.status_code >= 400:
                # 2026-05-15 — silent 4xx was hiding sweep failures.
                # Surface the body (truncated) so the operator can see
                # what the backend rejected.
                body = (resp.text or "")[:200]
                print(f"[POKA-SWEEP] Publish HTTP {resp.status_code} "
                      f"-> {body}", flush=True)
        except Exception as e:
            print(f"[POKA-SWEEP] Publish failed: {e}", flush=True)

    def on_ok_pulse(self, sensor_ok: int, shift_name: str):
        rule = self._logic_rule("SENSOR_BYPASS")
        if not rule or sensor_ok is None:
            return
        if sensor_ok == 0:
            self._fire(rule, shift_name,
                       plc_value="sensor_ok=0",
                       ctx="OK pulse without sensor confirmation")

    def on_ng_pulse(self, shift_name: str):
        rule = self._logic_rule("CONSECUTIVE_NG")
        if not rule:
            self._ng_streak += 1
            return
        self._ng_streak += 1
        if self._ng_streak >= rule.get("threshold_count", 3):
            self._fire(rule, shift_name,
                       plc_value=str(self._ng_streak),
                       ctx=f"{self._ng_streak} consecutive NG parts")
            self._ng_streak = 0

    def on_ok_clears_ng(self):
        self._ng_streak = 0

    def check_override(self, status_code: int, shift_name: str):
        rule = self._logic_rule("MANUAL_OVERRIDE")
        if not rule:
            return
        if status_code > 7:
            self._fire(rule, shift_name,
                       plc_value=str(status_code),
                       ctx=f"Unknown status {status_code}")

    def check_cycle_fast(self, ct: float, shift_name: str):
        rule = self._logic_rule("CYCLE_TOO_FAST")
        if not rule:
            return
        if ct < 5.0:
            self._fire(rule, shift_name,
                       plc_value=f"{ct}s",
                       ctx=f"Cycle {ct}s below minimum 5s")

    def _logic_rule(self, rule_type: str):
        return next((r for r in self.logic_rules
                     if r.get("rule_type") == rule_type), None)

    def _fire(self, rule: dict, shift_name: str, plc_value: str, ctx: str):
        now = time.time()
        key = rule.get("id", rule.get("poka_yoke_name", "unknown"))
        if now - self._last_event_time.get(key, 0) < 30:
            return
        self._last_event_time[key] = now
        level     = rule.get("alert_level", "WARNING")
        rule_name = rule.get("poka_yoke_name") or rule.get("rule_name", "Unknown")
        rule_type = rule.get("rule_type", "SENSOR_BYPASS")
        print(f"[POKA-YOKE] {level} | {rule_name} | {ctx}")
        try:
            requests.post(f"{BACKEND_URL}/api/poka-yoke/events/ingest", json={
                "line_id":      self.line_id,
                "rule_id":      rule.get("id"),
                "rule_type":    rule_type,
                "alert_level":  level,
                "shift_name":   shift_name,
                "plc_value":    plc_value,
                "context_json": f'{{"message": "{ctx}"}}',
            }, timeout=2)
        except Exception:
            pass


# ============================================================
# MAIN COLLECTOR ENGINE
# ============================================================

class CollectorEngine:
    DB_UPDATE_INTERVAL     = 2
    HOURLY_UPDATE_INTERVAL = 5
    SPEED_CHECK_INTERVAL   = 1

    # Build tag — bumped whenever we touch the count or status pipeline so
    # the running collector's identity is unambiguous from the log.  When
    # the operator says "kal ka fix gayab ho gaya" we can grep _collector.log
    # for [BUILD] and instantly tell which revision is live.
    BUILD_TAG = "2026-05-18-r4 | NG-dwell-debounce 300ms + BREAK auto-inject + break-override v2 + cycle-bound NG + L108-watchdog + NG-edge-preserve + OK-edge-trust + count-skip logs"

    def __init__(self, init_cfg: dict):
        line_id = init_cfg["line_id"]

        # ── Cross-PC singleton lock ──────────────────────────────
        # Acquire BEFORE loading config so that a duplicate launch
        # from another LAN PC fails fast with a clear error, instead
        # of half-initialising and racing the legitimate collector.
        # The lock auto-releases via heartbeat-staleness if this
        # process crashes.
        self._lock = CollectorSingletonLock(line_id)
        self._lock.acquire()    # raises RuntimeError if dupe

        print(f"[BUILD] CollectorEngine {self.BUILD_TAG}", flush=True)
        print(f"[ENGINE] Loading config for line_id={line_id} from DB...")
        self.cfg = load_line_config(line_id)
        print(f"[ENGINE] Config loaded: {self.cfg['line_name']}")
        print(f"[ENGINE]    PLC   : {self.cfg['plc_ip']}:{self.cfg['plc_port']}")
        print(f"[ENGINE]    Table : {self.cfg['table_name']}")
        print(f"[ENGINE]    Shifts: {list(self.cfg['shifts'].keys())}")
        print(f"[ENGINE]    Breaks: {len(self.cfg['breaks'])}")
        print(f"[ENGINE]    Models: {len(self.cfg['models'])}")
        print(f"[ENGINE]    Poka Yoke rules: {len(self.cfg['poka_rules'])}")

        self.ct   = CycleTimeTracker(
            ideal_ct=self.cfg["ideal_ct"],
            max_ct=self.cfg["max_ct"],
        )
        self.poka = PokaYokeMonitor(self.cfg["poka_rules"], line_id)

        self._plc: pymcprotocol.Type4E = None
        self._db   = None
        self._plc_ok = False
        self._db_ok  = False

        self.ok_total = 0
        self.ng_total = 0
        self.ok_shift = 0
        self.ng_shift = 0

        self._last_ok_state = 0
        self._last_ng_state = 0
        self._last_ok_time  = None
        self._last_ng_time  = None
        # 2026-05-24 — Persists across NG events to compute inter-NG CT.
        # Hydrated from DB on _connect_db so first NG after restart isn't 0.
        self._last_ng_time_for_ct = None
        # 2026-05-27 — Unified any-pulse-to-any-pulse anchor.  Either
        # L108 (OK) or L109 (NG) commit advances this timestamp.  CT
        # written to both `mes_l6_final_inspection` and the chart's
        # `_ct_log` table is `now - _last_any_pulse_dt`, regardless of
        # which bit just fired.  Operator: "L108 aaya, fir NG L109
        # aaya — unke beech ka time hi NG ka CT.  Bit kuch bhi aaye,
        # calculate karke write kar."  Hydrated from DB on _connect_db
        # so the first pulse after a restart isn't a junk huge number.
        self._last_any_pulse_dt        = None
        self._last_ct_for_chart_ok     = None
        self._last_ct_for_chart_ng     = None
        # 2026-05-27 — Raw cycle counter that advances on EVERY rising
        # edge (L108 or L109), independent of break / is_running gating.
        # Drives `cycle_seq` for ct_log rows so each pulse gets a unique
        # X-axis position on the chart.  Hydrated from MAX(cycle_seq) in
        # ct_log at boot.  Production counter (ok_shift / ng_shift) is
        # separate and still gated for clean OEE math.
        self._raw_cycle_seq            = 0
        # Unconditional L108-edge observer (2026-05-16) — captures every
        # rising edge regardless of status gating.  _update_status uses
        # this as a truth-detector to override PLC's D6005 when the
        # register lies (publishes IDLE while production is actually
        # firing L108 pulses).  See L108_TRUTH_WINDOW_SEC.
        self._last_ok_edge_observed = 0.0
        # ── NG state for the new cycle-bound counter (2026-05-16 v3) ──
        # _ng_seen_since_last_ok  → flag set on L109 rising edge, cleared
        #                            on L108 commit.  L108 looks at this
        #                            to label the cycle OK or NG.
        # _ng_consec_count        → consecutive NG-labeled cycles count.
        #                            Used by the stuck-bit guard to flip
        #                            to OK after MES_NG_STUCK_CYCLES.
        # _ng_stuck_alarm_fired   → one-shot flag so we log "presumed
        #                            stuck" exactly once per stuck event.
        self._ng_seen_since_last_ok  = False
        self._ng_consec_count        = 0
        self._ng_stuck_alarm_fired   = False
        # 2026-05-23 Option-C — bi-directional ladder filter state.
        # When L109 rises and the look-back check passes (no L108 within
        # NG_LADDER_WINDOW_SEC before it), we DEFER the NG commit by
        # the same window and watch for an L108 follow-up.  If L108
        # fires within the window → ladder echo, drop.  Otherwise →
        # real operator NG, flush.  Single slot — bursts collapse to
        # one pending event (the most recent overrides).
        self._pending_ng             = None   # {ts, part_code} or None
        self._pulse_gap     = self.cfg["pulse_gap"]
        # Legacy NG-hold state kept for compatibility with the
        # _load_shift_from_db restore path (referenced indirectly via
        # `self.ct.speed_loss` snapshot).  Not used by the new
        # cycle-bound counter — see _ng_seen_since_last_ok above.
        self._ng_high_since        = None
        self._ng_counted_this_high = False

        self._cur_model      = 1
        self._cur_model_name = (list(self.cfg["models"].values())[0]
                                if self.cfg["models"] else "Unknown")
        self._cur_status      = 0
        self._cur_status_name = "IDLE"

        self._cur_shift      = None
        self._shift_id       = None
        self._shift_start_ts = None

        self._loss = {
            "breakdown": 0.0, "quality":     0.0, "setup": 0.0,
            "material":  0.0, "others":      0.0, "speed": 0.0,
            "change_over": 0.0,
        }
        self._last_status_check = time.time()

        self._plan_completed  = 0
        self._last_plan_calc  = 0.0

        self._hourly_data: dict    = {}
        self._cur_hour_key: str    = None
        self._last_hourly_write    = time.time()

        self._last_db_write   = time.time()
        self._last_speed_chk  = time.time()
        self._last_display    = time.time()
        self._last_break_log  = time.time()
        self._last_plc_warn   = time.time()
        self._last_db_check   = time.time()
        # 2026-05-13 — admins can change zone breaks mid-shift via the
        # Production Admin Panel.  Without periodic reload, the new
        # break window never fires until tomorrow.  See _reload_breaks_from_db.
        self._last_break_reload = 0.0

        # Machine-process sampling — admin-configured per-process targets
        # under Admin → Production → Machines → ④ Process Config.
        # Cache the list, reload from DB every 30s (so admin edits go
        # live without a collector restart), sample every 60s and write
        # to mes_machine_process_log → drives the Process Graphs page.
        self._machine_processes: list = []
        self._last_process_reload = 0.0
        self._last_process_sample = 0.0

        # Poka-yoke / sensor check throttle (2026-05-12).
        # check_d_registers + check_py_bypass + track_sensors_health
        # each loop through 20-40 PLC bit reads.  At ~30-50 ms per read
        # that's up to 2 s per call, blocking the main poll loop.  L108
        # OK pulses can be as short as 100-200 ms, so when the main loop
        # ran at ~1 Hz instead of 33 Hz, brief pulses were missed and
        # the user saw "200 sec ki one big cycle aaya, 7-8 missing"
        # symptom.  Running these once every 2 s is enough for PY
        # bypass detection (operator latency is seconds anyway) and
        # keeps the pulse poll loop fast.
        self._last_py_check = 0.0

        # IDLE-dwell timer (2026-05-12).  YNC-SS PLC ladder oscillates
        # D6005 between raw=0 (between cycles, transient) and raw=16
        # (during cycle execution, decoded → RUNNING).  Without a dwell
        # the dashboard flips IDLE ↔ RUNNING every 4-6 s during normal
        # production.  Hold the previous status for N seconds before
        # committing an IDLE transition; if any non-IDLE state arrives
        # within the window, cancel the pending transition.
        self._pending_idle_since = None
        # 12s was too short — operator saw repeated IDLE flickers when
        # inter-cycle gap stretched to 14-18s.  Bumped to 25s so the
        # dashboard stays RUNNING through any normal between-part pause
        # but still surfaces a real stop within half a minute.
        self.IDLE_DWELL_SEC      = 25.0

        # ── CT log buffer ─────────────────────────────────────────
        self._ct_pending_log: list = []
        self._ct_log_table_ready   = False
        # status=-2 means "not yet successfully read".  Earlier we
        # initialised to 0 (= IDLE), which made the dashboard briefly
        # show IDLE during boot before the first PLC read landed —
        # operator interpreted this as a "ghost IDLE" event.  -2 is
        # treated as no-op by _update_status.
        self._last_plc_data   = {
            "ok_bit": 0, "ng_bit": 0,
            "status": -2, "model":  1,
            "sensor_ok": None,
        }

        # Break accumulator — tracks total break seconds elapsed in current shift
        self._break_seconds_acc  = 0.0
        self._cur_break_start_ts = None

        # ── Sub-machines (auxiliary PLCs on the same line) ─────────
        # Each gets its own MC connection + count-bit rising-edge poller.
        # Inherits model/shift/status from this parent engine via plain
        # attribute reads (GIL-safe). Writes to mes_submachine_ct_log.
        self.submachines      = load_submachines(self.cfg.get("main_plc_id"))
        self._sub_threads     = []           # legacy list of all spawned threads (alive + dead)
        self._sub_stop        = threading.Event()   # legacy "stop everything" event (still works)
        # 2026-05-15 — `_stop` was missing on CollectorEngine, so every
        # PY-check thread crashed at `self._stop.wait(2.0)` the moment
        # it started.  That's why `track_sensors_health` never ran and
        # the sensor sweep cache stayed empty no matter what.  Mirror
        # of `_sub_stop` semantically — both get .set() on shutdown.
        self._stop            = threading.Event()
        # Per-sub-machine worker registry — enables dynamic add/remove/edit
        # without restarting the collector. Reload loop populates this from
        # mes_plc_configs every 30 s.
        # Shape: {sub_id: {"stop": Event, "thread": Thread, "cfg_snapshot": dict}}
        self._sub_workers: dict = {}
        self._cur_part_code   = ""

        # ── OT polling cache ───────────────────────────────────────
        # mes_lines.ot_active_shift is set by the Zone/Admin toggle.
        # We re-read every 5 s so the collector flips into OT mode
        # automatically without restart.
        self._ot_cache_ts    = 0.0
        self._ot_active_val  = None
        print(f"[ENGINE]    Sub-machines: {len(self.submachines)}")
        for _s in self.submachines:
            print(f"[ENGINE]       - id={_s['id']}  "
                  f"{_s['plc_ip']}:{_s['plc_port']}  "
                  f"bit={_s['count_bit']}  ideal={_s['ideal_ct']}s  "
                  f"({_s['machine_name']})")

    # ----------------------------------------------------------
    # CONNECTIONS
    # ----------------------------------------------------------

    def _connect_plc(self) -> bool:
        try:
            if self._plc:
                try: self._plc.close()
                except: pass
            self._plc = pymcprotocol.Type4E()
            self._plc.connect(self.cfg["plc_ip"], self.cfg["plc_port"])
            self._plc.batchread_wordunits(
                headdevice=self.cfg["status_addr"], readsize=1)
            self._plc_ok = True
            print(f"[PLC] Connected {self.cfg['plc_ip']}:{self.cfg['plc_port']}")
            return True
        except Exception as e:
            self._plc_ok = False
            print(f"[PLC] Connection failed: {e}")
            return False

    def _connect_db(self) -> bool:
        try:
            self._db = psycopg2.connect(**DB_CONFIG)
            self._db.cursor().execute("SELECT 1")
            self._db_ok = True
            print(f"[DB] Connected")
            # 2026-05-24 — Hydrate last NG timestamp so the first NG row
            # written after a collector restart has a real inter-NG CT
            # (not 0).  Same idea as the sub-machine hydrate.
            try:
                _hc = self._db.cursor()
                _hc.execute(
                    f"SELECT MAX(ts) FROM {self.cfg['table_name']}_ct_log "
                    "WHERE record_date = CURRENT_DATE AND is_ng = true"
                )
                _r = _hc.fetchone()
                if _r and _r[0]:
                    self._last_ng_time_for_ct = _r[0].timestamp()
                    print(f"[MAIN] hydrated last_ng_for_ct = {_r[0]}",
                          flush=True)
                # 2026-05-27 — Hydrate the unified any-pulse anchor.
                # Take the most recent timestamp from mes_l6_final_inspection
                # regardless of OK/NG so the first pulse after restart
                # measures against the actual last pulse on disk, not 0.
                _hc.execute(
                    "SELECT MAX(ts) FROM mes_l6_final_inspection "
                    "WHERE record_date = CURRENT_DATE"
                )
                _r2 = _hc.fetchone()
                if _r2 and _r2[0]:
                    self._last_any_pulse_dt = _r2[0]
                    print(f"[MAIN] hydrated last_any_pulse_dt = {_r2[0]}",
                          flush=True)
                # 2026-05-27 — Hydrate raw chart cycle counter so the
                # chart's X-axis stays monotonic across restarts.
                try:
                    _hc.execute(
                        f"SELECT COALESCE(MAX(cycle_seq), 0) "
                        f"FROM {self.cfg['table_name']}_ct_log "
                        f"WHERE record_date = CURRENT_DATE"
                    )
                    _r3 = _hc.fetchone()
                    if _r3 and _r3[0] is not None:
                        self._raw_cycle_seq = int(_r3[0])
                        print(f"[MAIN] hydrated raw_cycle_seq = "
                              f"{self._raw_cycle_seq}", flush=True)
                except Exception:
                    pass
                _hc.close()
            except Exception as _e:
                print(f"[MAIN] hydrate failed: {_e}")
                try: self._db.rollback()
                except Exception: pass
            return True
        except Exception as e:
            self._db_ok = False
            print(f"[DB] Connection failed: {e}")
            return False

    def _ensure_db_connection(self):
        if self._db_ok and self._db:
            try:
                self._db.cursor().execute("SELECT 1")
                return True
            except Exception:
                self._db_ok = False
                try: self._db.close()
                except: pass
                self._db = None

        try:
            self._db = psycopg2.connect(**DB_CONFIG)
            self._db_ok = True
            self._shift_id = None
            print(f"[DB] Reconnected")
            return True
        except Exception as e:
            self._db_ok = False
            print(f"[DB] Reconnection failed: {e}")
            return False

    # ----------------------------------------------------------
    # WORKING SECONDS
    # ----------------------------------------------------------

    def _get_shift_start_timestamp(self, shift_name: str,
                                   record_date: date) -> float:
        scfg = self.cfg["shifts"].get(shift_name)
        if not scfg:
            return None
        start_time = scfg["start_time"]
        if isinstance(start_time, str):
            start_time = dt_time(*map(int, start_time.split(":")))
        return datetime.combine(record_date, start_time).timestamp()

    def _working_seconds(self) -> int:
        """
        Return elapsed productive seconds (excludes startup delay + all break time).
        Uses a running _break_seconds_acc so break-exit never causes a plan jump.
        """
        if not self._cur_shift or self._cur_shift.startswith("GAP"):
            return 0
        if not self._shift_start_ts:
            return 0
        scfg = self.cfg["shifts"].get(self._cur_shift)
        if not scfg:
            return 0

        # Hardcoded 5-min startup delay — see STARTUP_DELAY_MIN comment
        # near _is_in_startup_delay.  Must match so plan freeze and
        # status override use the same window.
        startup_delay = self.STARTUP_DELAY_MIN * 60
        now_ts        = time.time()

        in_break, current_break = self._is_break()

        if in_break:
            # Mark break start only on first tick inside this break
            if self._cur_break_start_ts is None:
                self._cur_break_start_ts = now_ts
                print(f"[BREAK] Started '{current_break}', freezing plan")
            # Freeze plan at the second the break started
            elapsed_to_break = max(0.0, self._cur_break_start_ts - self._shift_start_ts - startup_delay)
            # round() absorbs float drift so plan hits the exact target at shift end
            # (e.g. 27899.9999s / 15s would otherwise floor to 1859 instead of 1860)
            return max(0, int(round(elapsed_to_break - self._break_seconds_acc)))
        else:
            # Commit just-ended break duration to accumulator
            if self._cur_break_start_ts is not None:
                break_duration = now_ts - self._cur_break_start_ts
                self._break_seconds_acc += break_duration
                print(f"[BREAK] Ended, duration={break_duration:.1f}s acc={self._break_seconds_acc:.1f}s")
                self._cur_break_start_ts = None
            # Normal: wall-clock elapsed minus startup minus total break time
            elapsed = max(0.0, now_ts - self._shift_start_ts - startup_delay)
            # round() absorbs float drift so plan hits the exact target at shift end
            # (e.g. 27899.9999s / 15s would otherwise floor to 1859 instead of 1860)
            working = max(0, int(round(elapsed - self._break_seconds_acc)))

            # ── OT cap: if we're in the OT window (past shift end), freeze
            # working seconds at the shift's normal duration so plan stops
            # incrementing. Actual count keeps going but plan stays fixed.
            ot = self._check_ot_active()
            if ot == self._cur_shift and self._is_in_ot_window(self._cur_shift):
                s_end = scfg.get("end_time")
                if isinstance(s_end, str):
                    s_end = dt_time(*map(int, s_end.split(":")))
                s_start = scfg.get("start_time")
                if isinstance(s_start, str):
                    s_start = dt_time(*map(int, s_start.split(":")))
                # Shift duration in seconds (normal, excluding OT)
                s_min = s_start.hour * 60 + s_start.minute
                e_min = s_end.hour * 60 + s_end.minute
                if e_min <= s_min:
                    e_min += 1440  # crosses midnight
                shift_dur = (e_min - s_min) * 60
                cap = max(0, int(round(shift_dur - startup_delay - self._break_seconds_acc)))
                return min(working, cap)

            return working

    # ----------------------------------------------------------
    # PLC READ
    # ----------------------------------------------------------

    def _read_plc(self) -> dict:
        """Resilient PLC read.

        Each register read is wrapped in its own try so a hiccup on
        `ok_bit` no longer discards a successful `status` read.

        Spam guard: when `_plc_ok` is already False (connection dead),
        we DON'T retry the dead socket on every 30-ms tick — the
        underlying `pymcprotocol.Type4E` object holds a TCP socket
        that's been forcibly closed (WinError 10054).  Re-issuing
        reads against it just spams the log and burns CPU.  Instead
        we return cached `_last_plc_data` and let the main-loop
        reconnect path open a fresh socket.

        Print throttle: errors are logged ONCE per dead-period (on
        the live→dead transition) and ONCE per fresh `_connect_plc`
        failure — never per-cycle."""
        if self._plc is None:
            return self._last_plc_data

        # Spam guard: socket is dead, don't even try.  Caller's job
        # is to call _connect_plc() (main loop does this every 2 s).
        if not self._plc_ok:
            return self._last_plc_data

        data = dict(self._last_plc_data)
        failures = 0
        first_err = None

        def _safe_word(addr, key, keep_last):
            nonlocal failures, first_err
            try:
                v = self._plc.batchread_wordunits(headdevice=addr, readsize=1)
                if v:
                    data[key] = int(v[0])
                elif not keep_last:
                    data[key] = 0
            except Exception as e:
                failures += 1
                if first_err is None:
                    first_err = (key, e)

        def _safe_bit(addr, key, keep_last):
            nonlocal failures, first_err
            # 2026-05-23 — RETRY-ONCE on transient bit-read failure.
            # Single-packet glitches over LAN (~1-2 ms loss / TCP retransmit)
            # were silently setting L108/L109 to 0, dropping rising edges.
            # Operator: "merge ho hi kyu rhi hai... koii merging nhi" — root
            # cause is read miss, not edge logic.  Two reads × ~5 ms is
            # still well under the 30 ms poll budget and the PLC pulse is
            # multi-hundred-ms HIGH, so a retry sees the true bit state.
            _last_err = None
            for _try in range(2):
                try:
                    v = self._plc.batchread_bitunits(headdevice=addr, readsize=1)
                    if v:
                        data[key] = int(v[0])
                    elif not keep_last:
                        data[key] = 0
                    return
                except Exception as e:
                    _last_err = e
                    if _try == 0:
                        continue
            # both reads failed — count + log + (silently zero unless keep_last)
            failures += 1
            if first_err is None:
                first_err = (key, _last_err)
            _now = time.time()
            _attr = f"_safe_bit_last_log_{key}"
            _last = getattr(self, _attr, 0)
            if _now - _last > 5.0:
                setattr(self, _attr, _now)
                print(f"[PLC-HALF-FAIL] {key}={addr} read failed 2x: "
                      f"{str(_last_err)[:80]}  (rising edge happening "
                      f"RIGHT NOW would be lost). Throttled 5s.", flush=True)

        # Status: keep last on empty/error so we don't accidentally
        # publish IDLE after a transient miss.
        _safe_word(self.cfg["status_addr"], "status", keep_last=True)
        # Pulse bits: zero on miss is OK because edge detection treats
        # 0→1 as the trigger and we always have a last_state mirror.
        _safe_bit(self.cfg["ok_bit"],  "ok_bit", keep_last=False)
        _safe_bit(self.cfg["ng_bit"],  "ng_bit", keep_last=False)
        # Model: keep last on miss so a one-off blip doesn't change model.
        _safe_word(self.cfg["model_addr"], "model", keep_last=True)
        if self.cfg.get("sensor_ok_addr"):
            _safe_bit(self.cfg["sensor_ok_addr"], "sensor_ok", keep_last=False)

        # All-fail handling — only mark dead after 2 cycles of complete
        # failure to avoid bouncing on single-packet glitches.
        any_succeeded = failures < 4   # at least one of 4-5 reads got through
        if not any_succeeded:
            self._plc_fail_streak = getattr(self, "_plc_fail_streak", 0) + 1
            if self._plc_fail_streak >= 2:
                # Log ONCE per dead-period — only on the live→dead flip.
                if self._plc_ok:
                    key, err = first_err if first_err else ("?", "unknown")
                    print(f"[PLC] All reads failed × 2 cycles "
                          f"({key}: {err}) — marking dead, will reconnect.")
                self._plc_ok = False
                self._plc_died_at = time.time()
                # Force-close the dead socket so the next reconnect
                # opens a fresh one (pymcprotocol won't auto-rebind on
                # a hung TCP socket otherwise).
                try:
                    if self._plc:
                        self._plc.close()
                except Exception:
                    pass
                self._plc_fail_streak = 0
        else:
            self._plc_fail_streak = 0
            self._plc_ok = True

        self._last_plc_data = data
        return data

    # ----------------------------------------------------------
    # BREAK / SHIFT HELPERS
    # ----------------------------------------------------------

    def _reload_breaks_from_db(self) -> None:
        """Re-pull break windows from mes_break_configs.

        Admins edit zone breaks from the Production Admin Panel while
        the collector is already running.  routers/zones.py PUT
        /api/zones/{zone_id}/breaks REPLACES break rows for every line
        in the zone, so reading this line's rows fresh is enough — no
        need to know which zone owns this line.

        Called every 60 s from the main poll loop.  Silent on DB errors.
        """
        line_id = self.cfg.get("line_id")
        if not line_id:
            return
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT break_name, start_time, end_time, "
                "       crosses_midnight, applies_to_shifts "
                "  FROM mes_break_configs "
                " WHERE line_id = %s "
                " ORDER BY start_time",
                (line_id,)
            )
            new_breaks = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[BREAK-RELOAD] DB error for line {line_id}: {e}")
            return

        # Log only on diff so the console doesn't spam every minute.
        def _fp(rows):
            return tuple(
                (r.get("break_name"),
                 str(r.get("start_time")),
                 str(r.get("end_time")),
                 bool(r.get("crosses_midnight")),
                 r.get("applies_to_shifts"))
                for r in rows
            )
        old_fp = _fp(self.cfg.get("breaks", []))
        new_fp = _fp(new_breaks)
        if old_fp != new_fp:
            self.cfg["breaks"] = new_breaks
            labels = [f"{r['break_name']} {r['start_time']}-{r['end_time']}"
                      for r in new_breaks]
            print(f"[BREAK-RELOAD] line {line_id} now has {len(new_breaks)} "
                  f"break(s): {labels or '—'}")

    def _break_overlap_seconds(self, t_start: datetime, t_end: datetime) -> float:
        """Total seconds of configured break time that overlap the
        wall-clock interval [t_start, t_end].  Used by the sub-machine
        cycle-time path so a single cycle that spans a tea/lunch break
        doesn't appear as a 700 s spike on the chart.  Walks every
        break in cfg and clamps each break window to today's date;
        midnight-crossing breaks get their end pushed +1 day.
        """
        if t_end <= t_start:
            return 0.0
        overlap = 0.0
        for b in self.cfg.get("breaks", []) or []:
            bs = b.get("start_time")
            be = b.get("end_time")
            if isinstance(bs, str):
                bs = dt_time(*map(int, bs.split(":")))
            if isinstance(be, str):
                be = dt_time(*map(int, be.split(":")))
            # Combine onto the cycle's START date.  A cycle never spans
            # >24 h on this floor, so we only need to look at today's
            # instance of each break (and the previous day's if t_start
            # is just past midnight and the break crossed midnight).
            day0 = t_start.date()
            bs_dt = datetime.combine(day0, bs)
            be_dt = datetime.combine(day0, be)
            if b.get("crosses_midnight"):
                be_dt += timedelta(days=1)
            ov_s = max(t_start, bs_dt)
            ov_e = min(t_end,   be_dt)
            if ov_e > ov_s:
                overlap += (ov_e - ov_s).total_seconds()
            # Also handle the case where the break belongs to the
            # PREVIOUS day's window (e.g. cycle straddles midnight,
            # break started yesterday and crossed into today).
            if t_start.time() < be:
                bs_y = datetime.combine(day0 - timedelta(days=1), bs)
                be_y = datetime.combine(day0 - timedelta(days=1), be)
                if b.get("crosses_midnight"):
                    be_y += timedelta(days=1)
                ov_s2 = max(t_start, bs_y)
                ov_e2 = min(t_end,   be_y)
                if ov_e2 > ov_s2:
                    overlap += (ov_e2 - ov_s2).total_seconds()
        return overlap

    def _is_break(self):
        now = datetime.now().time()
        for b in self.cfg["breaks"]:
            s, e = b["start_time"], b["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if b["crosses_midnight"]:
                if now >= s or now < e:
                    return True, b["break_name"]
            else:
                if s <= now < e:
                    return True, b["break_name"]
        return False, None

    # ── OT state ─────────────────────────────────────────────────
    _OT_DURATION_MIN = 60   # OT window = 1 hour after shift end

    def _check_ot_active(self) -> str:
        """Read ot_active_shift from mes_lines. Returns shift name or ''."""
        if not self._db_ok or not self._db:
            return ""
        try:
            cur = self._db.cursor()
            cur.execute(
                "SELECT ot_active_shift FROM mes_lines WHERE id = %s",
                (self.cfg["line_id"],)
            )
            row = cur.fetchone()
            cur.close()
            return (row[0] or "") if row else ""
        except Exception:
            return ""

    def _get_ot_window(self, shift_name: str):
        """Return (ot_start_time, ot_end_time) from mes_shift_configs for this
        shift. If not configured, falls back to (shift_end, shift_end + 1hr)."""
        ot_s = ot_e = None
        if self._db_ok and self._db:
            try:
                cur = self._db.cursor()
                cur.execute(
                    "SELECT ot_start_time, ot_end_time FROM mes_shift_configs "
                    "WHERE line_id = %s AND shift_name = %s",
                    (self.cfg["line_id"], shift_name)
                )
                row = cur.fetchone()
                cur.close()
                if row:
                    ot_s, ot_e = row[0], row[1]
            except Exception:
                pass
        # Fallback: 1 hour after shift end
        if not (ot_s and ot_e):
            scfg = self.cfg["shifts"].get(shift_name, {})
            e = scfg.get("end_time")
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if e:
                ot_s = e
                end_m = (e.hour * 60 + e.minute + self._OT_DURATION_MIN) % 1440
                ot_e = dt_time(end_m // 60, end_m % 60)
        return ot_s, ot_e

    def _is_in_ot_window(self, shift_name: str) -> bool:
        """True if current time is within the OT window for this shift."""
        scfg = self.cfg["shifts"].get(shift_name)
        if not scfg or not scfg.get("is_production"):
            return False
        ot_s, ot_e = self._get_ot_window(shift_name)
        if not (ot_s and ot_e):
            return False
        now_t = datetime.now().time()
        s_min = ot_s.hour * 60 + ot_s.minute
        e_min = ot_e.hour * 60 + ot_e.minute
        n_min = now_t.hour * 60 + now_t.minute
        if e_min <= s_min:  # crosses midnight
            return n_min >= s_min or n_min < e_min
        return s_min <= n_min < e_min

    def _get_current_shift(self):
        now   = datetime.now()
        t     = now.time()
        today = now.date()

        # ── OT check FIRST: if OT is active for a shift and we're within
        # the 1-hour OT window after that shift's end, keep that shift alive.
        # This prevents the collector from transitioning to GAP when OT is on.
        ot_shift = self._check_ot_active()
        if ot_shift and ot_shift in self.cfg["shifts"]:
            if self._is_in_ot_window(ot_shift):
                scfg = self.cfg["shifts"][ot_shift]
                if scfg.get("crosses_midnight") and t < scfg["start_time"]:
                    return ot_shift, today - timedelta(days=1)
                return ot_shift, today

        # Normal shift detection — check time windows
        for sname, scfg in self.cfg["shifts"].items():
            if not scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if scfg["crosses_midnight"]:
                if t >= s:
                    return sname, today
                elif t < e:
                    return sname, today - timedelta(days=1)
            else:
                if s <= t < e:
                    return sname, today

        # GAP shifts
        for sname, scfg in self.cfg["shifts"].items():
            if scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if s <= t < e:
                return sname, today

        return None, today

    def _get_current_slot(self) -> str:
        t = datetime.now().time()

        # ── OT slot priority: if OT is active + window live, route to OT slot
        ot_shift = self._check_ot_active()
        if ot_shift and self._is_in_ot_window(ot_shift):
            # Ensure OT slot exists (creates on first call)
            ot_label = self._ensure_ot_slot(ot_shift)
            if ot_label:
                return ot_label

        for sname, slots in self.cfg["hourly_plan"].items():
            for slot_label in slots:
                if slot_label not in self.cfg["slot_boundaries"]:
                    continue
                s, e, crosses = self.cfg["slot_boundaries"][slot_label]
                if isinstance(s, str):
                    s = dt_time(*map(int, s.split(":")))
                if isinstance(e, str):
                    e = dt_time(*map(int, e.split(":")))
                if crosses:
                    if t >= s or t < e:
                        return slot_label
                else:
                    if s <= t < e:
                        return slot_label
        return None

    def _ensure_ot_slot(self, shift_name: str) -> str:
        """Create the OT slot + dashboard columns if not present. Returns slot label."""
        if not self._db_ok:
            return None
        ot_s, ot_e = self._get_ot_window(shift_name)
        if not (ot_s and ot_e):
            return None
        label = f"{ot_s.strftime('%H:%M')}-{ot_e.strftime('%H:%M')} OT"
        # Already in config? return
        if label in self.cfg.get("slot_boundaries", {}):
            return label
        crosses = (ot_e <= ot_s)
        sm = ot_s.hour * 60 + ot_s.minute
        em = ot_e.hour * 60 + ot_e.minute
        dur_min = (em - sm) if not crosses else ((1440 - sm) + em)
        prefix = f"hour_{ot_s.strftime('%H%M')}_{ot_e.strftime('%H%M')}_ot"
        try:
            cur = self._db.cursor()
            # Insert into mes_hourly_slots (idempotent via unique constraint)
            cur.execute("""
                INSERT INTO mes_hourly_slots
                    (line_id, shift_name, slot_label, start_time, end_time,
                     crosses_midnight, working_minutes, plan_pieces,
                     db_column_prefix, slot_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, 999)
                ON CONFLICT (line_id, shift_name, slot_label) DO NOTHING
            """, (self.cfg["line_id"], shift_name, label, ot_s, ot_e,
                  crosses, dur_min, prefix))
            self._db.commit()

            # Add columns to dashboard table (idempotent)
            tbl = self.cfg["table_name"]
            for col_suffix, col_type in [("ok","INTEGER DEFAULT 0"),
                                          ("ng","INTEGER DEFAULT 0"),
                                          ("plan","INTEGER DEFAULT 0"),
                                          ("actual","INTEGER DEFAULT 0"),
                                          ("variance","INTEGER DEFAULT 0")]:
                cur.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {prefix}_{col_suffix} {col_type}"
                )
            self._db.commit()
            cur.close()
        except Exception as exc:
            print(f"[OT] _ensure_ot_slot error: {exc}")
            self._safe_rollback()
            return None

        # Update in-memory config so _get_current_slot/_write_hourly_to_db find it
        hp = self.cfg.setdefault("hourly_plan", {}).setdefault(shift_name, {})
        hp[label] = 0
        self.cfg.setdefault("slot_boundaries", {})[label] = (ot_s, ot_e, crosses)
        self.cfg.setdefault("slot_to_db", {})[label] = prefix
        print(f"[OT] Ensured OT slot '{label}' prefix={prefix} for shift {shift_name}")
        return label

    def _is_in_gap_period(self) -> bool:
        if self._cur_shift and self._cur_shift.startswith("GAP"):
            return True
        now = datetime.now().time()
        for sname, scfg in self.cfg["shifts"].items():
            if scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if scfg.get("crosses_midnight"):
                if now >= s or now < e:
                    return True
            else:
                if s <= now < e:
                    return True
        return False

    def _should_record_pulse(self) -> bool:
        # Suppress counting during GAP (between-shift) and scheduled
        # breaks — these are official non-production windows for OEE.
        # Status display during those windows is also forced IDLE/BREAK
        # in _update_status. Outside those windows, any PLC pulse counts.
        if self._is_in_gap_period():
            return False
        in_break, _ = self._is_break()
        return not in_break

    def _realtime_slot_plan(self, slot_label: str) -> int:
        static_plan = 0
        for sname, slots in self.cfg["hourly_plan"].items():
            if slot_label in slots:
                static_plan = slots[slot_label]
                break
        if not static_plan or slot_label not in self.cfg["slot_boundaries"]:
            return 0

        s, e, crosses = self.cfg["slot_boundaries"][slot_label]
        if isinstance(s, str):
            s = dt_time(*map(int, s.split(":")))
        if isinstance(e, str):
            e = dt_time(*map(int, e.split(":")))

        now   = datetime.now()
        today = now.date()

        slot_start = datetime.combine(today, s)
        slot_end   = (datetime.combine(slot_start.date() + timedelta(days=1), e)
                      if crosses else datetime.combine(slot_start.date(), e))

        if slot_start <= now < slot_end:
            elapsed   = (now - slot_start).total_seconds()
            break_sec = 0.0
            for b in self.cfg["breaks"]:
                bs = b["start_time"]
                be = b["end_time"]
                if isinstance(bs, str):
                    bs = dt_time(*map(int, bs.split(":")))
                if isinstance(be, str):
                    be = dt_time(*map(int, be.split(":")))
                bs_dt = datetime.combine(slot_start.date(), bs)
                be_dt = datetime.combine(slot_start.date(), be)
                if b["crosses_midnight"]:
                    be_dt += timedelta(days=1)
                ov_s = max(slot_start, bs_dt)
                ov_e = min(now, be_dt)
                if ov_e > ov_s:
                    break_sec += (ov_e - ov_s).total_seconds()

            working_sec = max(0.0, elapsed - break_sec)
            rt_plan     = int(working_sec / self.cfg["ideal_ct"])
            stored      = self._hourly_data.get(slot_label, {}).get("plan", 0)
            return min(static_plan, max(rt_plan, stored))

        elif now >= slot_end:
            return static_plan
        return 0

    # ----------------------------------------------------------
    # HOURLY SLOT INIT & BACKFILL
    # ----------------------------------------------------------

    def _init_all_hourly_slots_for_shift(self, shift_name: str):
        if not self._shift_id:
            return
        shift_slots = [
            sl for sl in self.cfg["slot_to_db"].keys()
            if any(sl in slots
                   for sn, slots in self.cfg["hourly_plan"].items()
                   if sn == shift_name)
        ]
        print(f"[HOURLY] Initializing {len(shift_slots)} slots for shift {shift_name}")

        for slot_label in shift_slots:
            if slot_label not in self._hourly_data:
                self._hourly_data[slot_label] = {"ok": 0, "ng": 0, "plan": 0}
            try:
                col = self.cfg["slot_to_db"].get(slot_label)
                if col and self._db_ok and self._db:
                    cur = self._db.cursor()
                    cur.execute(f"""
                        SELECT {col}_ok, {col}_ng, {col}_plan
                        FROM {self.cfg['table_name']}
                        WHERE id = %s
                    """, (self._shift_id,))
                    row = cur.fetchone()
                    if row and any(row):
                        self._hourly_data[slot_label]["ok"]   = row[0] or 0
                        self._hourly_data[slot_label]["ng"]   = row[1] or 0
                        self._hourly_data[slot_label]["plan"] = row[2] or 0
                    cur.close()
            except Exception as e:
                print(f"[HOURLY] Error loading slot {slot_label}: {e}")
                self._db_ok = False

        self._backfill_past_slots(shift_name)

    def _write_all_slots_to_db_once(self):
        """On startup resuming a shift, write all past slot plans to DB immediately."""
        if not self._shift_id or not self._ensure_db_connection():
            return
        now = datetime.now()
        try:
            cur = self._db.cursor()
            for slot_label, col in self.cfg["slot_to_db"].items():
                if slot_label not in self.cfg["slot_boundaries"]:
                    continue
                s, e, crosses = self.cfg["slot_boundaries"][slot_label]
                if isinstance(s, str):
                    s = dt_time(*map(int, s.split(":")))
                if isinstance(e, str):
                    e = dt_time(*map(int, e.split(":")))

                slot_end_dt = datetime.combine(now.date(), e)
                if crosses:
                    slot_end_dt += timedelta(days=1)

                # Only fix PAST slots (already ended)
                if now <= slot_end_dt:
                    continue

                hd = self._hourly_data.get(slot_label, {"ok": 0, "ng": 0, "plan": 0})

                # Calculate plan if still 0
                if hd["plan"] == 0:
                    slot_start_dt = datetime.combine(now.date(), s)
                    if crosses and slot_start_dt > slot_end_dt:
                        slot_start_dt -= timedelta(days=1)
                    working_sec = max(0, (slot_end_dt - slot_start_dt).total_seconds())
                    for b in self.cfg["breaks"]:
                        bs = b["start_time"]
                        be = b["end_time"]
                        if isinstance(bs, str): bs = dt_time(*map(int, bs.split(":")))
                        if isinstance(be, str): be = dt_time(*map(int, be.split(":")))
                        bs_dt = datetime.combine(slot_start_dt.date(), bs)
                        be_dt = datetime.combine(slot_start_dt.date(), be)
                        if b["crosses_midnight"]: be_dt += timedelta(days=1)
                        ov_s = max(slot_start_dt, bs_dt)
                        ov_e = min(slot_end_dt, be_dt)
                        if ov_e > ov_s:
                            working_sec -= (ov_e - ov_s).total_seconds()
                    static_max = 0
                    for sname, slots in self.cfg["hourly_plan"].items():
                        if slot_label in slots:
                            static_max = slots[slot_label]
                    hd["plan"] = min(static_max, max(0, int(working_sec / self.cfg["ideal_ct"])))
                    self._hourly_data[slot_label] = hd

                ok_count = hd.get("ok", 0)
                ng_count = hd.get("ng", 0)
                plan     = hd.get("plan", 0)
                actual   = ok_count + ng_count
                variance = actual - plan

                cur.execute(f"""
                    UPDATE {self.cfg['table_name']} SET
                        {col}_ok       = %s,
                        {col}_ng       = %s,
                        {col}_plan     = %s,
                        {col}_actual   = %s,
                        {col}_variance = %s,
                        updated_at     = NOW()
                    WHERE id = %s
                """, (ok_count, ng_count, plan, actual, variance, self._shift_id))

            self._db.commit()
            cur.close()
            print(f"[STARTUP] Past slot plans written to DB for shift_id={self._shift_id}")
        except Exception as e:
            print(f"[STARTUP] Backfill write error: {e}")
            self._db_ok = False
            self._safe_rollback()

    def _backfill_past_slots(self, shift_name: str):
        now   = datetime.now()
        today = now.date()
        scfg  = self.cfg["shifts"].get(shift_name)
        if not scfg:
            return

        for slot_label, hdata in self._hourly_data.items():
            if slot_label not in self.cfg["slot_boundaries"]:
                continue
            s, e, crosses = self.cfg["slot_boundaries"][slot_label]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))

            slot_end_dt = datetime.combine(today, e)
            if crosses:
                slot_end_dt += timedelta(days=1)

            if now > slot_end_dt and hdata.get("plan", 0) == 0:
                slot_start_dt = datetime.combine(today, s)
                if crosses and slot_start_dt > slot_end_dt:
                    slot_start_dt -= timedelta(days=1)

                working_seconds = max(
                    0, (slot_end_dt - slot_start_dt).total_seconds())

                for b in self.cfg["breaks"]:
                    bs = b["start_time"]
                    be = b["end_time"]
                    if isinstance(bs, str):
                        bs = dt_time(*map(int, bs.split(":")))
                    if isinstance(be, str):
                        be = dt_time(*map(int, be.split(":")))
                    bs_dt = datetime.combine(slot_start_dt.date(), bs)
                    be_dt = datetime.combine(slot_start_dt.date(), be)
                    if b["crosses_midnight"]:
                        be_dt += timedelta(days=1)
                    ov_s = max(slot_start_dt, bs_dt)
                    ov_e = min(slot_end_dt, be_dt)
                    if ov_e > ov_s:
                        working_seconds -= (ov_e - ov_s).total_seconds()

                working_seconds = max(0, working_seconds)
                plan = int(working_seconds / self.cfg["ideal_ct"])
                plan = min(
                    self.cfg["hourly_plan"].get(shift_name, {}).get(slot_label, plan),
                    plan)
                hdata["plan"] = plan
                print(f"[HOURLY] Backfilled {slot_label}: plan={plan}")

    # ----------------------------------------------------------
    # COUNTS
    # ----------------------------------------------------------

    def _update_counts(self, ok_bit: int, ng_bit: int) -> tuple:
        new_ok = 0
        new_ng = 0
        now    = time.time()

        # 2026-05-16 — TRUTH TRACKER for L108.
        # Track every L108 rising edge unconditionally, even when
        # _update_counts gates the actual count on `is_running`.  Used by
        # _update_status as a fallback "is the machine REALLY running?"
        # signal when PLC's D6005 register lies (e.g., ladder bug leaves
        # status=0 IDLE while operator is actively cycling).
        # Now ALSO logs the transition so we can see whether the PLC
        # read of L108 is healthy (was suspecting silent failures while
        # L109 reads worked fine).
        if self._last_ok_state == 0 and ok_bit == 1:
            self._last_ok_edge_observed = now
            print(f"[OK-RAW-WATCH] L108 0->1 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                  f"(running={self._cur_status == 1}, "
                  f"should_record={self._should_record_pulse()})", flush=True)
            # 2026-05-26 — Refresh part_code at THIS rising edge BEFORE
            # writing the row.  Earlier `_cur_part_code` was stale (it
            # was last refreshed at the previous L108 commit), so the
            # mes_l6 audit row got NULL part_code while ct_log got it.
            try:
                _pc_fresh = (self._read_part_code() or "").strip().rstrip(":")
                if _pc_fresh and _pc_fresh.upper() != "ERROR":
                    self._cur_part_code = _pc_fresh
            except Exception:
                pass

            # Raw write to per-machine L6 audit (no dwell gating).
            # 2026-05-26 — Garbage-part_code guard.  Same problem the NG
            # path hit: PLC sometimes leaks `\x10` (0x10 status byte)
            # into D5004 around an L108 chatter pulse, producing OK
            # rows with junk part_code.  Skip the write when the read
            # looks like a control-char dump — better to leave the row
            # off the audit than poison the per-part lookups (videos,
            # remarks, charts) downstream.
            try:
                # 2026-05-27 — CT MODEL CHANGE: any-pulse-to-any-pulse.
                # Operator: "L108 (OK) aaya, next cycle NG (L109) aaya,
                # to unke beech ka time hi NG ka CT hua.  Fir agli cycle
                # L108 aaya, wo OK ka CT hua.  Bit OK ya NG kuch bhi aaye,
                # calculate and write."  Single shared anchor that any
                # pulse (OK or NG) advances, instead of two separate
                # bit-to-bit chains.
                _now_dt = datetime.now()
                _prev_ts = getattr(self, "_last_any_pulse_dt", None)
                _ct = (_now_dt - _prev_ts).total_seconds() if _prev_ts else None
                # Clamp obviously invalid gaps (collector restart, cross-shift)
                if _ct is not None and (_ct < 0 or _ct > 600):
                    _ct = None
                _pc = (self._cur_part_code or "").strip().rstrip(":") or None
                # 2026-05-27 — Empty vs corrupted distinction (same as NG
                # side).  Empty = no scanner / test pattern, still write
                # NULL row.  Corrupted = control-char dump, skip write.
                def _looks_corrupted_pc(s):
                    if not s:
                        return False
                    if s.upper() == "ERROR":
                        return True
                    return any((ord(c) < 0x20 or ord(c) == 0x7F) for c in s)
                # Always advance the anchor + stash chart CT so ct_log
                # row downstream has accurate data — independent of
                # whether we end up writing the L6 audit row.
                self._last_any_pulse_dt = _now_dt
                self._last_ct_for_chart_ok = _ct
                if _pc is not None and _looks_corrupted_pc(_pc):
                    print(f"[OK-DB-SKIP] L108 edge but part_code is "
                          f"corrupted ({_pc!r}) — skipping audit write "
                          f"but ct_log still records ct={_ct}s.",
                          flush=True)
                else:
                    self._write_machine_log(
                        machine_id   = int(self.cfg.get("main_plc_id") or self.cfg["line_id"]),
                        bit_type     = "OK",
                        bit_address  = self.cfg.get("ok_bit") or "L108",
                        ts           = _now_dt,
                        ct_seconds   = _ct,
                        part_code    = _pc,
                    )
                # 2026-05-27 — UNCONDITIONAL ct_log OK write (matches the
                # NG path above).  Operator: "kuch bhi skip nahi karna,
                # ok bhi as cycle count hogi".  Every L108 rise = 1 chart
                # row, regardless of break window or PLC status.  Counter
                # increment (ok_shift) still happens later via the gated
                # path inside _update_counts.
                _shift_ok = self._cur_shift or "UNKNOWN"
                if _shift_ok.startswith("GAP"):
                    _shift_ok = "GAP"
                self._raw_cycle_seq += 1
                self._ct_pending_log.append((
                    _now_dt, _now_dt.date(), _shift_ok,
                    round(float(_ct or 0.0), 2),
                    self._raw_cycle_seq,
                    _pc, False,  # is_ng = False
                ))
                self._raw_ok_already_logged_this_press = True
            except Exception as _e:
                pass
        elif self._last_ok_state == 1 and ok_bit == 0:
            print(f"[OK-RAW-WATCH] L108 1->0 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}", flush=True)

        # Periodic state dump (every 30 s) — single source of truth for
        # debugging "why didn't the counter increment".
        if not hasattr(self, "_count_diag_last") or now - self._count_diag_last > 30:
            self._count_diag_last = now
            print(f"[COUNT-DIAG] ok_bit={ok_bit} ng_bit={ng_bit} "
                  f"last_ok={self._last_ok_state} last_ng={self._last_ng_state} "
                  f"cur_status={self._cur_status} is_running={self._cur_status == 1} "
                  f"ng_seen_pending={getattr(self, '_ng_seen_since_last_ok', False)} "
                  f"ng_consec={getattr(self, '_ng_consec_count', 0)} "
                  f"ok_count={self.ok_shift} ng_count={self.ng_shift}", flush=True)

        # 2026-05-16 — STUCK-L108 WATCHDOG.
        # Operator saw OK counter freeze at 1148 for 18+ min while L109
        # kept firing.  Root cause: PLC TCP connection went "half-open"
        # — pymcprotocol's socket looked alive (L109 reads still worked)
        # but L108 reads silently returned stale 0.  No exception was
        # raised, so the existing reconnect path never triggered.
        # Solution: a per-bit liveness watchdog.  Track when L108 was
        # last observed in the rising state; if >STUCK_THRESHOLD_SEC
        # has passed AND L109 fired at least N times in that window
        # (proving the PLC connection IS otherwise alive), forcibly
        # close + reconnect the PLC socket so the next poll gets fresh
        # reads.  L109 firing without L108 = PLC half-open signature.
        STUCK_OK_THRESHOLD_SEC  = 180.0     # 3 min — far above any normal cycle
        STUCK_OK_NG_WITNESSES   = 3         # need at least 3 L109 fires to suspect
        if self._last_ng_state == 0 and ng_bit == 1:
            # increment witness counter on every L109 rising edge
            self._stuck_l108_ng_witness = getattr(self, "_stuck_l108_ng_witness", 0) + 1
        last_ok_seen = getattr(self, "_last_ok_edge_observed", 0.0) or 0.0
        if (last_ok_seen > 0
                and now - last_ok_seen > STUCK_OK_THRESHOLD_SEC
                and getattr(self, "_stuck_l108_ng_witness", 0) >= STUCK_OK_NG_WITNESSES
                and not getattr(self, "_stuck_l108_recover_firing", False)):
            print(f"[L108-WATCHDOG] No L108 edge for "
                  f"{now - last_ok_seen:.0f}s while L109 fired "
                  f"{self._stuck_l108_ng_witness}x — PLC half-open suspected. "
                  f"Forcing PLC reconnect.", flush=True)
            self._stuck_l108_recover_firing = True   # one-shot until reconnect path runs
            try:
                if self._plc is not None:
                    try: self._plc.close()
                    except Exception: pass
                self._plc_ok = False                  # main loop will reopen on next tick
            except Exception as exc:
                print(f"[L108-WATCHDOG] close failed: {exc}", flush=True)
        # Reset watchdog state on a real L108 rising edge (we recovered)
        if self._last_ok_state == 0 and ok_bit == 1:
            self._stuck_l108_ng_witness   = 0
            self._stuck_l108_recover_firing = False

        # 2026-05-16 — RAW L109 observer (zero gating).  Operator reports
        # "NG bit fires kabhi nahi yaa 40 ek saath" but direct PLC scan
        # showed L109 = 0 throughout 90 s of high-freq polling.  This
        # observer prints to log on EVERY 0→1 / 1→0 transition of the
        # configured NG bit, with timestamps + hold duration on the
        # falling edge.  Independent of is_running gating, dwell rules,
        # everything — pure ground truth.  When operator presses NG
        # tomorrow, the log will show:
        #   • L109 toggle captured  → wiring + bit address are correct,
        #     the new cycle-bound counter will pick it up on next L108
        #   • L109 NEVER toggles    → NG button is wired to a different
        #     bit (look at the [NG-RAW-WATCH] gap and check PLC ladder)
        if self._last_ng_state == 0 and ng_bit == 1:
            self._ng_raw_rise_ts = now
            print(f"[NG-RAW-WATCH] L109 0->1 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                  f"(running={self._cur_status == 1})", flush=True)
            # 2026-05-27 — UNCONDITIONAL raw NG write (operator: "kuch
            # bhi skip nahi karna, ng bhi as cycle count hogi").  Every
            # L109 rising edge produces a row in mes_l6_final_inspection
            # AND ync_dashboard_complete_ct_log — irrespective of dwell,
            # interval, is_running, or break window.  Mirrors the L108
            # raw write block above; same any-pulse-to-any-pulse anchor.
            # The dashboard counter (ng_shift) still respects the gates
            # below — those exist to keep the production OEE clean.
            try:
                _ng_now_dt = datetime.now()
                _prev_ts = getattr(self, "_last_any_pulse_dt", None)
                _ng_ct_raw = ((_ng_now_dt - _prev_ts).total_seconds()
                              if _prev_ts else None)
                if _ng_ct_raw is not None and (_ng_ct_raw < 0 or _ng_ct_raw > 600):
                    _ng_ct_raw = None
                _ng_pc = (self._cur_part_code or "").strip().rstrip(":") or None
                def _looks_corrupted_ng(s):
                    if not s:
                        return False
                    if s.upper() == "ERROR":
                        return True
                    return any((ord(c) < 0x20 or ord(c) == 0x7F) for c in s)
                # Advance unified anchor + stash CT regardless of write decision
                self._last_any_pulse_dt    = _ng_now_dt
                self._last_ct_for_chart_ng = _ng_ct_raw
                # L6 audit write (skip only on corrupted part_code)
                if _ng_pc is not None and _looks_corrupted_ng(_ng_pc):
                    print(f"[NG-DB-SKIP] L109 edge but part_code is "
                          f"corrupted ({_ng_pc!r}) — skipping audit "
                          f"write but ct_log still records ct={_ng_ct_raw}s.",
                          flush=True)
                else:
                    self._write_machine_log(
                        machine_id   = int(self.cfg.get("main_plc_id") or self.cfg["line_id"]),
                        bit_type     = "NG",
                        bit_address  = self.cfg.get("ng_bit") or "L109",
                        ts           = _ng_now_dt,
                        ct_seconds   = _ng_ct_raw,
                        part_code    = _ng_pc,
                    )
                # ct_log row — drives the front-end chart.  Append even
                # during break so every PLC pulse shows as its own dot.
                _shift_ng = self._cur_shift or "UNKNOWN"
                if _shift_ng.startswith("GAP"):
                    _shift_ng = "GAP"
                self._raw_cycle_seq += 1
                self._ct_pending_log.append((
                    _ng_now_dt, _ng_now_dt.date(), _shift_ng,
                    round(float(_ng_ct_raw or 0.0), 2),
                    self._raw_cycle_seq,
                    _ng_pc, True,  # is_ng = True
                ))
                # Suppress the duplicate ct_log + L6 write inside the
                # later commit-gate block — raw write here already
                # covered both.  Counter increment in that block still
                # fires under its own conditions.
                self._raw_ng_already_logged_this_press = True
            except Exception as _e_ng_raw:
                print(f"[NG-RAW-WRITE-ERR] {_e_ng_raw}", flush=True)
        elif self._last_ng_state == 1 and ng_bit == 0:
            held = now - getattr(self, "_ng_raw_rise_ts", now)
            print(f"[NG-RAW-WATCH] L109 1->0 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                  f"(held {held:.3f}s, running={self._cur_status == 1})",
                  flush=True)

        # Capture the L108 / L109 rising-edge intent BEFORE the gates so
        # we can log the skip reason if a gate vetoes the count.
        # 2026-05-18 — operator wanted "ek-ek count ho, koi miss naa ho".
        # Explicit skip logs make every dropped edge auditable.
        _ok_edge_now = (self._last_ok_state == 0 and ok_bit == 1)
        _ng_edge_now = (self._last_ng_state == 0 and ng_bit == 1)

        if not self._should_record_pulse():
            if _ok_edge_now or _ng_edge_now:
                print(f"[COUNT-SKIP] _should_record_pulse=False "
                      f"ok_edge={_ok_edge_now} ng_edge={_ng_edge_now} "
                      f"— edge dropped", flush=True)
            self._last_ok_state = ok_bit
            self._last_ng_state = ng_bit
            return 0, 0

        # 2026-05-12 ROLLBACK — earlier rev removed the status==RUNNING(1)
        # gate trying to fix count-miss.  That introduced a worse bug:
        # L109 (NG) transient pulses during IDLE / status-transition
        # windows started being counted as real NGs, producing 60+ NGs
        # in a half day when actual reject rate is 2-3/day.
        #
        # Restored the safer original behaviour: ONLY count OK/NG when
        # PLC says RUNNING (status_code 1) AND we're not in a break.
        # If the PLC flips into transient QUALITY_ISSUE (3) on cycle
        # end, the IDLE-dwell + raw=16→RUNNING handlers keep cur_status
        # at 1 so legitimate pulses still count.  Pulse miss from the
        # earlier collector blocking issues is separately addressed
        # by the PY-check thread (moved to its own PLC connection).
        in_break, _ = self._is_break()
        is_running  = (self._cur_status == 1 and not in_break)

        # 2026-05-18 — INLINE L108-EDGE OVERRIDE.
        # The L108-TRUTH-OVERRIDE in _update_status only fires on the
        # NEXT poll (it needs _last_ok_edge_observed to already be set,
        # which we set further down inside this same call).  So when
        # the FIRST L108 edge after a long IDLE comes through, this
        # call still sees _cur_status=0 → is_running=False → that very
        # cycle would be silently dropped.  Cover for it: if we see an
        # L108 rising edge *right now* and we're not in a scheduled
        # break, trust the edge — the machine IS running.  Break window
        # still wins (we never count parts during scheduled break).
        if _ok_edge_now and not is_running and not in_break:
            print(f"[OK-EDGE-TRUST] L108 edge while PLC status="
                  f"{self._cur_status} (IDLE/transient) — counting anyway "
                  f"(machine is clearly producing).", flush=True)
            is_running = True
        elif (_ok_edge_now or _ng_edge_now) and not is_running:
            # In break — explicit log so operator can verify nothing
            # legitimate was missed.
            print(f"[COUNT-SKIP] in_break={in_break} cur_status={self._cur_status} "
                  f"ok_edge={_ok_edge_now} ng_edge={_ng_edge_now} "
                  f"— edge intentionally not counted (scheduled break / PLC idle)",
                  flush=True)

        # 2026-05-18 — TRACK NG EDGE BEFORE is_running GATE.
        # Earlier the L109 rising-edge detection sat INSIDE `if is_running:`
        # at line ~2355.  Risk: operator presses NG during a brief status
        # transition (status flips to QUALITY_ISSUE / IDLE for 1-2 polls),
        # is_running goes False, the L109 rise is ignored, then when the
        # next L108 fires that cycle gets labelled OK incorrectly.
        # Fix: set the NG-pending flag any time we see an L109 rising edge
        # outside of a scheduled break — even when is_running is False.
        # The flag persists until the next L108 commits, so a real NG press
        # is never lost regardless of status flapping mid-cycle.
        #
        # 2026-05-23 — DWELL REMOVED.  Operator demand: "koii hold nhi
        # lgana bss ye dekhna hai ki itne N kaa cause kya tha — agr tu
        # hold lga dega toh point kya hai hme real-time mein accurate
        # hona hai".  Pure rising-edge counting now.  Every 0→1 of L109
        # outside a scheduled break is a candidate NG, full stop.
        # The stuck-bit guard further down (NG_STUCK_CYCLES) is the
        # ONLY safety net — it caps RUNAWAY ladder behavior, not noise.
        #
        # To diagnose phantom-NG bursts WITHOUT suppressing them, every
        # L109 rising edge is now written to a dedicated JSONL forensic
        # file with full context (cycle-relative timing, status, model,
        # part code, PLC health).  When operator reports "30 NG aaye",
        # we open _ng_forensics_{date}_{shift}.jsonl and pattern-match
        # the cause (electrical noise vs ladder bug vs status flap vs
        # real operator press).
        # 2026-05-26 — DWELL + INTER-NG GAP combined filter.
        # PLC ladder pulse width drifted up to 500-1000 ms today, so
        # 500 ms dwell alone leaks ladder pulses.  Real operator presses
        # are minutes apart (1-2 per shift typically), ladder pulses fire
        # every cycle (~15 s).  Combined rule:
        #   1) L109 must be held >= NG_MIN_HOLD_SEC (default 0.5 s)
        #   2) AND at least NG_MIN_INTERVAL_SEC since last counted NG
        #      (default 30 s — kills ladder bursts at 15 s cadence)
        # Tunable: MES_NG_MIN_HOLD_SEC, MES_NG_MIN_INTERVAL_SEC.
        NG_MIN_HOLD_SEC     = float(_os.environ.get(
            "MES_NG_MIN_HOLD_SEC", "0.5"))
        NG_MIN_INTERVAL_SEC = float(_os.environ.get(
            "MES_NG_MIN_INTERVAL_SEC", "30"))
        NG_MIN_HOLD_POLLS   = 1   # legacy var kept for L108 commit branch

        # Keep _ng_hold_polls counter alive for the rest of the logic
        # below (it still uses it as a sanity check inside the L108
        # commit branch).  No drops are logged here — the forensic
        # logger captures everything.
        if ng_bit == 1:
            self._ng_hold_polls = getattr(self, "_ng_hold_polls", 0) + 1
        else:
            held_polls = getattr(self, "_ng_hold_polls", 0)
            # FALLING-EDGE FORENSICS — captures the actual L109 dwell time
            # for the just-completed press.  Combined with the rising-edge
            # log line, this gives a complete pulse picture per NG event.
            if held_polls > 0:
                try:
                    import json as _json_ng
                    _hold_ms = held_polls * 100
                    _ev = {
                        "ts": datetime.now().isoformat(timespec="milliseconds"),
                        "kind": "L109_fall",
                        "hold_ms": _hold_ms,
                        "hold_polls": held_polls,
                        "cur_status": self._cur_status,
                        "in_break": in_break,
                        "is_running": is_running,
                        "model": self._cur_model_name,
                        "part_code": self._cur_part_code,
                        "since_last_ok_s": round(
                            time.time() - (self._last_ok_time or 0), 3
                        ) if self._last_ok_time else None,
                        "ng_seen_pending": bool(self._ng_seen_since_last_ok),
                        "plc_ok": bool(self._plc_ok),
                    }
                    self._ng_forensic_write(_ev)
                except Exception:
                    pass
            self._ng_hold_polls = 0

        # 2026-05-23 — real-time NG accept: ANY L109 rising edge outside
        # a scheduled break sets the cycle's NG flag.  No dwell wait —
        # the operator wants every press visible the instant it happens.
        # The forensic JSONL writer captures the rising edge with full
        # context (so post-mortem can tell ladder pulse from real press).
        # 2026-05-26 — TIME-BASED dwell gate.  Set rise timestamp on
        # the rising edge but DON'T flag the cycle NG until the bit has
        # been held HIGH for >= NG_MIN_HOLD_SEC wall-clock seconds.  The
        # check below runs every poll while ng_bit=1.
        if _ng_edge_now and not in_break:
            self._ng_rise_ts_for_dwell = now
            try:
                import json as _json_ng2
                _ev2 = {
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "kind": "L109_rise",
                    "cur_status": self._cur_status,
                    "is_running": is_running,
                    "model": self._cur_model_name,
                    "part_code": self._cur_part_code,
                    "since_last_ok_s": round(
                        time.time() - (self._last_ok_time or 0), 3
                    ) if self._last_ok_time else None,
                    "ok_bit_now": ok_bit,
                    "plc_ok": bool(self._plc_ok),
                }
                self._ng_forensic_write(_ev2)
            except Exception:
                pass
        # While L109 stays HIGH, only flag the cycle once the dwell
        # threshold is crossed.  Ladder pulses fall below this; real
        # operator presses clear it easily.
        if ng_bit == 1 and not in_break:
            _rt = getattr(self, "_ng_rise_ts_for_dwell", None)
            if _rt is not None and (now - _rt) >= NG_MIN_HOLD_SEC:
                if not self._ng_seen_since_last_ok:
                    print(f"[NG-DWELL-OK] L109 held {(now - _rt)*1000:.0f}ms "
                          f"(>= {NG_MIN_HOLD_SEC*1000:.0f}ms threshold) — "
                          f"counted as real operator press.", flush=True)
                self._ng_seen_since_last_ok = True
        elif ng_bit == 0:
            # Falling edge — log a chatter-drop if the rise didn't clear the dwell
            _rt = getattr(self, "_ng_rise_ts_for_dwell", None)
            if _rt is not None and (now - _rt) < NG_MIN_HOLD_SEC:
                _held_ms = (now - _rt) * 1000
                if _held_ms > 50:  # ignore noise-floor edges
                    print(f"[NG-CHATTER-DROP] L109 held {_held_ms:.0f}ms "
                          f"(< {NG_MIN_HOLD_SEC*1000:.0f}ms) — likely PLC "
                          f"ladder echo, NOT counted.", flush=True)
            self._ng_rise_ts_for_dwell = None

        # ────────────────────────────────────────────────────────────
        # OK / NG — SWITCH MODEL (2026-05-26).
        # ────────────────────────────────────────────────────────────
        # Operator: "ye mera pura concept as a first pulse of any one
        # bit ok not as continues monitor every thing".  Each bit's
        # rising edge is an INDEPENDENT event.  Don't merge cycles.
        #   • L108 rise → +1 OK row
        #   • L109 rise (dwell-gated ≥500 ms) → +1 NG row
        # ACTUAL = OK + NG (each visible row is its own physical part).
        # When the operator runs 5-7 NG parts in a row, we get 5-7 NG
        # rows, not one merged row pretending to be 186 s of work.
        if is_running:
            # L108 rising edge → independent OK row
            if self._last_ok_state == 0 and ok_bit == 1:
                try:
                    _pc = self._read_part_code() or ""
                    _pc_clean = _pc.strip().rstrip(":")
                    if _pc_clean and _pc_clean.upper() != "ERROR":
                        self._cur_part_code = _pc_clean
                except Exception:
                    pass
                self.ok_total += 1
                self.ok_shift += 1
                new_ok = 1
                self._last_ok_time = now
                print(f"[OK-COUNT] +1 (total={self.ok_total} "
                      f"shift={self.ok_shift}) pc={self._cur_part_code}",
                      flush=True)
                self._emit_edge_webhook("L108", now)

            # L109 dwell-completed → independent NG row.
            # Two gates required to commit:
            #  (a) dwell flag set (held >= NG_MIN_HOLD_SEC) above
            #  (b) at least NG_MIN_INTERVAL_SEC since last counted NG
            # The interval gate kills ladder bursts at cycle cadence
            # (15 s) — real operator NG presses are minutes apart.
            # One-shot guard prevents double-commit while bit stays HIGH.
            _prev_committed = getattr(self, "_last_ng_committed_ts", 0)
            _gap_ok = (now - _prev_committed) >= NG_MIN_INTERVAL_SEC
            if (self._ng_seen_since_last_ok
                    and not getattr(self, "_ng_committed_this_press", False)
                    and _gap_ok):
                try:
                    _pc = self._read_part_code() or ""
                    _pc_clean = _pc.strip().rstrip(":")
                    if _pc_clean and _pc_clean.upper() != "ERROR":
                        self._cur_part_code = _pc_clean
                except Exception:
                    pass
                self.ng_total += 1
                self.ng_shift += 1
                new_ng = 1
                self._last_ng_time = now
                self._last_ng_committed_ts = now
                self._ng_committed_this_press = True
                print(f"[NG-COUNT] +1 (total={self.ng_total} "
                      f"shift={self.ng_shift}) pc={self._cur_part_code} "
                      f"interval_ok=({now - _prev_committed:.1f}s)",
                      flush=True)
                self._emit_edge_webhook("L109", now)

                # 2026-05-26 — Gated NG DB write (moved from the raw
                # rising-edge handler above).  Only writes when BOTH
                # the dwell gate (>=500 ms hold) and the interval gate
                # (>=30 s since last counted NG) have passed AND the
                # part_code looks like a real barcode.  Anything else
                # (PLC chatter, transient L109 with stale D5004) gets
                # dropped, keeping the audit table in 1:1 sync with the
                # dashboard NG counter.
                # 2026-05-27 — L6 audit + ct_log writes for NG MOVED to
                # the raw L109 rising-edge handler at the top of this
                # function (see "[NG-RAW-WATCH] L109 0->1" block).  This
                # commit-gate block now only handles the COUNTER bump
                # (ng_shift) — which still respects dwell + interval +
                # is_running because the dashboard OEE / shift target
                # math depends on a clean production count.  Operator
                # accepted this split: every PLC pulse shows up as a
                # row, but the counter stays honest about real produced
                # parts.
            elif (self._ng_seen_since_last_ok
                    and not getattr(self, "_ng_committed_this_press", False)
                    and not _gap_ok):
                # Dwell passed but interval too short — ladder burst.
                # Mark as committed-this-press so we don't keep retrying.
                self._ng_committed_this_press = True
                print(f"[NG-INTERVAL-DROP] L109 dwell passed but only "
                      f"{now - _prev_committed:.1f}s since last NG "
                      f"(< {NG_MIN_INTERVAL_SEC:.0f}s) — ladder burst, "
                      f"NOT counted.", flush=True)
            # On falling edge of L109 — clear both press guards so the
            # next press can dwell-pass + commit fresh.
            if ng_bit == 0 and self._last_ng_state == 1:
                self._ng_seen_since_last_ok = False
                self._ng_committed_this_press = False

        self._last_ok_state = ok_bit
        self._last_ng_state = ng_bit
        return new_ok, new_ng

    # ──────────────────────────────────────────────────────────────
    # Per-machine L6 tables (2026-05-24)
    # Operator design: ek hi mes_pulse_log me sab mix tha — alag table
    # chahiye har machine ki.  Map machine_id → table name and route
    # writes accordingly.  Final Inspection ka row me status + model
    # bhi (woh sirf main PLC se aate).  Sub-machines me sirf bit data.
    # ──────────────────────────────────────────────────────────────

    # machine_id → (table_name, supports_status_model)
    _L6_TABLE_MAP = {
        2:  ("mes_l6_final_inspection", True),
        8:  ("mes_l6_upper_rail",       False),
        10: ("mes_l6_lower_rail",       False),
        12: ("mes_l6_semi_auto",        False),
        13: ("mes_l6_ball_guide_13",    False),
        14: ("mes_l6_ball_guide_14",    False),
        16: ("mes_l6_lock_bar",         False),
    }

    def _write_machine_log(self, *, machine_id: int,
                           bit_type: str, bit_address: str,
                           ts, ct_seconds, part_code: str) -> None:
        """Route a raw L108/L109 rise into the machine-specific L6 table.
        Final Inspection rows automatically include current status + model.
        Other machines get a minimal row.  Counter auto-derived per
        (machine, bit_type, shift, date).  Best-effort — never raises
        out of the caller.
        2026-05-24 — connection-leak fix: try/finally + a single short-
        lived connection per write so an exception doesn't leak."""
        meta = self._L6_TABLE_MAP.get(machine_id)
        if not meta:
            return
        table, has_status = meta
        conn = None
        cur  = None
        try:
            shift = self._cur_shift or "UNKNOWN"
            if shift.startswith("GAP"):
                shift = "GAP"
            rec_date = ts.date() if hasattr(ts, "date") else date.today()
            if not hasattr(self, "_l6_counters"):
                self._l6_counters = {}
            key = (table, bit_type, shift, rec_date)

            conn = _db_conn()
            cur  = conn.cursor()

            if key not in self._l6_counters:
                try:
                    cur.execute(
                        f"SELECT COALESCE(MAX(counter_val), 0) FROM {table} "
                        "WHERE bit_type=%s AND shift_name=%s AND record_date=%s",
                        (bit_type, shift, rec_date),
                    )
                    self._l6_counters[key] = int(cur.fetchone()[0] or 0)
                except Exception:
                    self._l6_counters[key] = 0
            self._l6_counters[key] += 1
            counter_val = self._l6_counters[key]

            if has_status:
                # 2026-05-26 — Predict video_path (flat CMS naming
                # convention).  File may not exist yet on disk; UI
                # falls back to /api/lines/{id}/cycle-video if path
                # 404s.  Sanitize part_code identically to CMS:
                # only A-Za-z0-9._- allowed.
                _vpath = None
                if part_code:
                    import re as _re_vp
                    _safe = _re_vp.sub(r"[^A-Za-z0-9._-]", "_",
                                       str(part_code)).strip("_")
                    if _safe:
                        _vpath = f"videos/YNC-SS/{_safe}.mp4"
                cur.execute(f"""
                    INSERT INTO {table}
                      (ts, bit_type, bit_address, ct_seconds, counter_val,
                       part_code, shift_name, record_date,
                       status_code, status_name, model_no, model_name,
                       video_path)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    ts, bit_type, bit_address,
                    (None if ct_seconds is None else round(float(ct_seconds), 3)),
                    counter_val, (part_code or None),
                    shift, rec_date,
                    int(self._cur_status) if self._cur_status is not None else None,
                    self._cur_status_name or None,
                    int(self._cur_model) if self._cur_model else None,
                    self._cur_model_name or None,
                    _vpath,
                ))
            else:
                cur.execute(f"""
                    INSERT INTO {table}
                      (ts, bit_type, bit_address, ct_seconds, counter_val,
                       shift_name, record_date)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (
                    ts, bit_type, bit_address,
                    (None if ct_seconds is None else round(float(ct_seconds), 3)),
                    counter_val, shift, rec_date,
                ))
            conn.commit()
        except Exception as e:
            _now = time.time()
            _last = getattr(self, "_l6_err_last", 0)
            if _now - _last > 5.0:
                self._l6_err_last = _now
                print(f"[L6-WRITE] {table} {bit_type} failed: "
                      f"{str(e)[:80]}", flush=True)
        finally:
            # Always close, even on exceptions, to prevent connection leak.
            if cur is not None:
                try: cur.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    # ──────────────────────────────────────────────────────────────
    # mes_pulse_log raw-edge audit writer (2026-05-23)
    # Operator design: every L108 rise → 1 row (bit_type='OK'), every
    # L109 rise → 1 row (bit_type='NG').  Pure audit log — no gating,
    # no merging, no derived state.  Runs PARALLEL to existing
    # _update_counts logic; nothing here changes the legacy tables.
    # ──────────────────────────────────────────────────────────────

    def _write_pulse_log(self, *, machine_id: int, machine_name: str,
                         bit_type: str, bit_address: str,
                         ts, ct_seconds, part_code: str) -> None:
        """Append one raw-edge row to mes_pulse_log.  counter_val auto-
        derived from a per-(machine, bit, shift, date) running counter
        held in self._pulse_counters.  Best-effort — DB blip throttled-
        logs and never raises out of the caller (count path must not
        break on audit-log failure)."""
        try:
            shift = self._cur_shift or "UNKNOWN"
            if shift.startswith("GAP"):
                shift = "GAP"
            rec_date = ts.date() if hasattr(ts, "date") else date.today()
            key      = (machine_id, bit_type, shift, rec_date)
            if not hasattr(self, "_pulse_counters"):
                self._pulse_counters = {}
            # Hydrate from DB once per (machine, bit, shift, date) so a
            # collector restart mid-shift continues the counter instead
            # of resetting to 1.
            if key not in self._pulse_counters:
                try:
                    _c = _db_conn()
                    _cur = _c.cursor()
                    _cur.execute(
                        "SELECT COALESCE(MAX(counter_val), 0) "
                        "FROM mes_pulse_log "
                        "WHERE machine_id=%s AND bit_type=%s "
                        "  AND shift_name=%s AND record_date=%s",
                        (machine_id, bit_type, shift, rec_date),
                    )
                    self._pulse_counters[key] = int(_cur.fetchone()[0] or 0)
                    _cur.close(); _c.close()
                except Exception:
                    self._pulse_counters[key] = 0
            self._pulse_counters[key] += 1
            counter_val = self._pulse_counters[key]

            conn = _db_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO mes_pulse_log
                  (line_id, machine_id, machine_name, bit_type, bit_address,
                   ts, ct_seconds, counter_val, part_code,
                   shift_name, record_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                self.cfg["line_id"], machine_id, machine_name,
                bit_type, bit_address, ts,
                (None if ct_seconds is None else round(float(ct_seconds), 3)),
                counter_val,
                (part_code or None),
                shift, rec_date,
            ))
            conn.commit()
            cur.close(); conn.close()
        except Exception as e:
            _now = time.time()
            _last = getattr(self, "_pulse_log_err_last", 0)
            if _now - _last > 5.0:
                self._pulse_log_err_last = _now
                print(f"[PULSE-LOG] write failed ({bit_type} "
                      f"machine={machine_id}): {str(e)[:80]}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # NG forensic logger (one JSONL line per L109 rise / fall event)
    # ──────────────────────────────────────────────────────────────

    def _ng_forensic_write(self, event: dict) -> None:
        """Append one JSON line to a per-day NG forensic log so post-mortem
        on phantom-NG bursts can identify the trigger (ladder pulse vs
        electrical chatter vs real press vs status flap).  Path:
            Phase2/_ng_forensics_line{line_id}_{YYYY-MM-DD}.jsonl
        Writes are best-effort — never raise out of the count path."""
        try:
            import json as _json_fw
            line_id = self.cfg.get("line_id", "X")
            date_s  = datetime.now().strftime("%Y-%m-%d")
            base    = _os.path.dirname(_os.path.abspath(__file__))
            path    = _os.path.join(
                base,
                f"_ng_forensics_line{line_id}_{date_s}.jsonl",
            )
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json_fw.dumps(event, default=str) + "\n")
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Edge webhook → Camera CMS (non-blocking, best-effort)
    # ──────────────────────────────────────────────────────────────

    def _emit_edge_webhook(self, bit_label: str, edge_epoch: float) -> None:
        """POST a tiny JSON ping to the Camera CMS so it can cut a clip
        from the rolling .ts file at this exact wall-clock instant.

        Why this exists: CMS used to poll the PLC itself, but Mitsubishi
        Q-series accept only a couple of simultaneous TCP clients on
        port 5002 — when collector and CMS competed for the same socket,
        ~half the L108/L109 rising edges silently dropped on the
        collector side.  After this webhook landed, CMS no longer talks
        to the PLC; it just receives our timestamps.

        The request runs on a daemon thread so a slow / dead CMS never
        blocks the 30 ms poll loop."""
        url = getattr(self, "_edge_webhook_url", None)
        if url is None:
            self._edge_webhook_url = _os.environ.get(
                "CMS_EDGE_WEBHOOK_URL",
                "http://127.0.0.1:5555/api/plc-edge",
            )
            url = self._edge_webhook_url
        if not url:
            return
        cfg     = self.cfg
        line_id = cfg.get("line_id")
        line_nm = cfg.get("line_name", "")
        # Resolve the model name we last saw — gives CMS something
        # human-readable to filename clips with if the part_code lookup
        # fails on its end.
        model_no = getattr(self, "_cur_model", None)
        model_nm = getattr(self, "_cur_model_name", "") or ""
        # Pass through whatever part_code we last read from D5004.  CMS
        # uses this to name the extracted clip — gives the operator a
        # one-glance link from a defective part to its video.
        part_code = getattr(self, "_cur_part_code", "") or ""
        payload = {
            "line_id":      line_id,
            "line_name":    line_nm,
            "plc_ip":       cfg.get("plc_ip", ""),
            "plc_port":     cfg.get("plc_port", 5002),
            "bit":          bit_label,
            "status":       "OK" if bit_label == "L108" else "NG",
            "epoch":        edge_epoch,
            "epoch_ms":     int(edge_epoch * 1000),
            "iso":          datetime.fromtimestamp(edge_epoch).isoformat(timespec="milliseconds"),
            "part_code":    part_code,
            "model_number": model_no,
            "model_name":   model_nm,
            "ok_total":     self.ok_total,
            "ng_total":     self.ng_total,
        }
        # 2026-05-21 — RETRY LOOP.  Earlier this was single-shot with
        # 1.5 s timeout; any miss caused CMS to skip that L108 edge.
        # When CMS missed N consecutive edges, its next received edge
        # produced a phantom cycle of (N+1)× the real cycle time and a
        # multi-cycle MP4 (e.g. 127 s "cycle" containing 11 real cycles).
        # Fix: 3 retries with 250 ms → 500 ms → 1 s backoff, preserving
        # the ORIGINAL epoch_ms timestamp on each attempt so CMS sees
        # the true edge time even if delivery is delayed by ≤2 s total.
        # Timeout per attempt raised 1.5 → 3 s to cover the DB-slow
        # window when CMS's /api/plc-edge handler is mid-blocked on a
        # cycle finalization.
        def _send():
            import urllib.request, json as _j
            body = _j.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            last_exc = None
            for attempt, backoff in enumerate((0.0, 0.25, 0.5, 1.0)):
                if backoff:
                    time.sleep(backoff)
                try:
                    req = urllib.request.Request(
                        url, method="POST", headers=headers, data=body)
                    urllib.request.urlopen(req, timeout=3.0).read()
                    if attempt > 0:
                        # Recovered after retry — log once so we know the
                        # backup attempts actually saved an edge.
                        print(f"[EDGE-WEBHOOK] {bit_label} delivered on "
                              f"attempt {attempt+1}", flush=True)
                    return
                except Exception as exc:
                    last_exc = exc
                    continue
            # All 4 attempts exhausted — log once per 30 s
            last = getattr(self, "_edge_webhook_last_err_ts", 0)
            if time.time() - last > 30:
                print(f"[EDGE-WEBHOOK] {bit_label} -> {url} failed after "
                      f"4 attempts: {last_exc}", flush=True)
                self._edge_webhook_last_err_ts = time.time()
        threading.Thread(target=_send, daemon=True,
                         name=f"edge-webhook-{bit_label}").start()

    # ----------------------------------------------------------
    # STATUS / LOSS
    # ----------------------------------------------------------

    # 2026-05-13 — operator spec: every shift's first 5 minutes are
    # reserved for model setup / hand-over, hardcoded.  Used to come
    # from `mes_shift_configs.startup_delay_min` but admins kept setting
    # it inconsistently per line; hardcoding makes every line identical.
    STARTUP_DELAY_MIN = 5

    def _is_in_startup_delay(self) -> bool:
        """True iff we're in the first STARTUP_DELAY_MIN minutes of a
        real (non-GAP) shift.  Forces MODEL_SETUP override + freezes
        the plan counter for that window."""
        if not self._cur_shift or self._cur_shift.startswith("GAP"):
            return False
        if not self._shift_start_ts:
            return False
        startup_delay = self.STARTUP_DELAY_MIN * 60
        return (time.time() - self._shift_start_ts) < startup_delay

    def _find_setup_status(self):
        """Look up the (code, name) of MODEL_SETUP in the status_map.
        Prefers loss_type == 'setup'; falls back to name matching."""
        cached = getattr(self, "_setup_status_cache", None)
        if cached is not None:
            return cached
        code, name = None, None
        for c, info in self.cfg.get("status_map", {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("loss") == "setup":
                code, name = c, info.get("name", "MODEL_SETUP")
                break
        if code is None:
            for c, info in self.cfg.get("status_map", {}).items():
                if not isinstance(info, dict):
                    continue
                nm = (info.get("name") or "").upper()
                if "SETUP" in nm:
                    code, name = c, info["name"]
                    break
        self._setup_status_cache = (code, name)
        return code, name

    def _find_break_status(self):
        """Look up the (code, name) of BREAK in the status_map.
        Prefers loss_type == 'break'; falls back to name matching.
        Returns (None, None) if no BREAK row exists — caller should
        fall back to IDLE (status code 0)."""
        cached = getattr(self, "_break_status_cache", None)
        if cached is not None:
            return cached
        code, name = None, None
        for c, info in self.cfg.get("status_map", {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("loss") == "break":
                code, name = c, info.get("name", "BREAK")
                break
        if code is None:
            for c, info in self.cfg.get("status_map", {}).items():
                if not isinstance(info, dict):
                    continue
                nm = (info.get("name") or "").upper()
                if nm == "BREAK" or nm.endswith("_BREAK"):
                    code, name = c, info["name"]
                    break
        self._break_status_cache = (code, name)
        return code, name

    # ----------------------------------------------------------
    # Breakdown auto-tracking (drives the Maintenance ANDON live table)
    # ----------------------------------------------------------
    # Whenever the line transitions INTO a status whose loss_type is
    # 'breakdown', we open a row in mes_breakdowns (state='OPEN').  When
    # it transitions OUT to any non-breakdown status, we stamp ended_at
    # and flip the row to 'RESOLVED'.  The Maintenance Dashboard reads
    # straight from this table — so ANDON + history are always live.
    def _is_breakdown_status(self, code) -> bool:
        info = self.cfg.get("status_map", {}).get(code, {})
        return isinstance(info, dict) and (info.get("loss") == "breakdown")

    def _handle_breakdown_transition(self, old_code, new_code):
        """Open / resolve mes_breakdowns rows on status changes.

        Open rule  : transition INTO a status whose loss_type='breakdown'
                     → INSERT new row state='OPEN' (skipped if one already
                     exists for this line, so a flicker between two
                     breakdown statuses doesn't create duplicates).

        Resolve rule: transition INTO RUNNING (status_code == 1) while an
                     OPEN row exists for this line → stamp ended_at and
                     flip to 'RESOLVED'.  We deliberately do NOT resolve
                     on intermediate states (BREAK, IDLE, MODEL_SETUP,
                     etc.) — per spec the ticket only graduates to
                     History when the line is truly back to RUNNING.

        Best-effort: DB hiccups must never crash the collector."""
        try:
            new_is_bd  = self._is_breakdown_status(new_code)
            new_is_run = (new_code == 1)
            line_id    = self.cfg["line_id"]
            zone_id    = self.cfg.get("zone_id")
            now        = datetime.now()

            # Nothing to do unless we entered breakdown or returned to RUNNING.
            if not (new_is_bd or new_is_run):
                return

            with _db_conn() as conn:
                cur = conn.cursor()

                if new_is_bd:
                    # Skip if an OPEN row already exists (e.g. flicker
                    # between two breakdown sub-statuses).
                    cur.execute("""
                        SELECT 1 FROM mes_breakdowns
                         WHERE line_id = %s AND state = 'OPEN' LIMIT 1
                    """, (line_id,))
                    if cur.fetchone():
                        return

                    shift_name = self._cur_shift if self._cur_shift else None
                    cur.execute("""
                        SELECT COALESCE(MAX(serial_in_shift), 0) + 1
                          FROM mes_breakdowns
                         WHERE line_id = %s
                           AND shift_name IS NOT DISTINCT FROM %s
                           AND DATE(started_at) = DATE(%s)
                    """, (line_id, shift_name, now))
                    serial = cur.fetchone()[0] or 1

                    if zone_id is None:
                        cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s",
                                    (line_id,))
                        row = cur.fetchone()
                        zone_id = row[0] if row else None

                    status_name = self.cfg["status_map"].get(new_code, {}).get("name", str(new_code))
                    cur.execute("""
                        INSERT INTO mes_breakdowns
                            (line_id, zone_id, shift_name, serial_in_shift,
                             started_at, state, reason)
                        VALUES (%s, %s, %s, %s, %s, 'OPEN', %s)
                        RETURNING id
                    """, (line_id, zone_id, shift_name, serial,
                          now, f"Auto-detected — line entered {status_name}"))
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    print(f"[BREAKDOWN] OPEN  id={new_id} line={line_id} "
                          f"shift={shift_name} serial={serial} ({status_name})")

                elif new_is_run:
                    # Back to RUNNING — resolve any OPEN row for this line.
                    cur.execute("""
                        UPDATE mes_breakdowns
                           SET state='RESOLVED', ended_at=%s, updated_at=NOW()
                         WHERE line_id = %s AND state = 'OPEN'
                         RETURNING id
                    """, (now, line_id))
                    rows = cur.fetchall()
                    conn.commit()
                    if rows:
                        print(f"[BREAKDOWN] RESOLVE id={rows[0][0]} line={line_id} "
                              f"(line back to RUNNING)")
        except Exception as e:
            # Never let breakdown bookkeeping crash the collector.
            print(f"[BREAKDOWN] tracking failed line={self.cfg.get('line_id')}: {e}")

    def _update_status(self, status_code: int):
        now     = time.time()

        # Sentinel: -2 = "PLC never successfully read".  Skip silently
        # so the dashboard doesn't briefly flash IDLE on cold boot
        # before the first successful read lands.
        if status_code == -2:
            self._last_status_check = now
            return

        elapsed = now - self._last_status_check
        old     = self._cur_status

        # ── PLC bit-flag mask + sticky fallback ──────────────────────
        # PLC D6005 is a 16-bit word.  Operator confirmed the ladder
        # uses ONLY bits 0-3 (decimal 0-15) for the status enum; the
        # higher bits are control/remote flags PLC sets independently.
        # When a 2nd HMI / MES client opens on the LAN the PLC sets the
        # "remote active" flag (bit 4 = decimal 16) — without this mask
        # MES then read raw values 16 / 17 / 18 / etc. and the dashboard
        # flickered between unmapped codes and the real status.
        #
        # Strategy:
        #   1. Try the raw value first (back-compat for PLCs whose
        #      enum legitimately uses larger ints).
        #   2. If raw isn't in status_map, try lower nibble (raw & 0x0F)
        #      to strip control flags.
        #   3. If neither maps, hold the LAST KNOWN status and log a
        #      one-time warning so admin can add the mapping.
        status_map = self.cfg.get("status_map", {}) or {}
        raw_code   = status_code
        # Stash the most-recent raw value so the periodic display loop
        # can surface "Status (raw=N)" — diagnoses PLC ladder bugs that
        # publish unexpected codes.
        self._last_raw_status = raw_code

        if status_code not in status_map and status_code not in (0, -1):
            masked = status_code & 0x0F

            # ── Ambiguous-mask handling (2026-05-12 rev) ──
            # Operator spec, verbatim: "PLC only publishes IDLE when the
            # machine is physically stopped.  Any non-zero raw value
            # means the machine is RUNNING."
            #
            # Three sub-cases:
            #   1. raw=16/32/48 etc with masked==0  → PLC flag bit set
            #      while lower nibble is 0.  The previous "hold last
            #      known" behaviour locked the dashboard at IDLE forever
            #      when a brief raw=0 pulse preceded raw=16.  Per
            #      operator spec → RUNNING (status 1).
            #   2. masked is in status_map → use the lower nibble.
            #   3. Truly unmapped → hold last known + WARN once.
            if masked == 0 and raw_code != 0:
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published raw={raw_code} "
                          f"(bit-pattern with flag set + lower nibble 0). "
                          f"Interpreting as RUNNING per operator spec — "
                          f"PLC engineer: check D{self.cfg.get('status_addr','????')} "
                          f"ladder upper-bit assignments.")
                status_code = 1   # RUNNING
            elif masked in status_map or masked in (0, -1):
                # Bit-flag stripped — use the lower-nibble status enum.
                status_code = masked
            else:
                # Truly unmapped (neither raw nor lower nibble) — hold.
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published unmapped code {raw_code} "
                          f"(masked {masked} also unmapped) — sticking to last known "
                          f"{self._cur_status_name!r}.  Add it under "
                          f"Admin → Production → Status Colour to map it.")
                self._last_status_check = now
                return

        # ── L108 TRUTH OVERRIDE (2026-05-16) ─────────────────────────
        # Operator reported A-shift window 09:12-09:16 painted IDLE
        # while machine was actually running.  Root cause analysis on
        # the collector log (OK count stuck at 19, PY sensors X16/X17
        # no-toggle 903s, BREAKDOWN at 09:16) showed the machine was
        # GENUINELY idle in that window — PLC D6005 was telling the
        # truth.  BUT to harden against a future PLC ladder bug that
        # publishes IDLE while L108 still fires (which would silently
        # zero out production stats), we add an L108-edge-based
        # override here:
        #   • _last_ok_edge_observed is set on every L108 0→1 edge,
        #     regardless of whether `is_running` was True at the time.
        #   • If the PLC-decoded status_code is IDLE (0) but an L108
        #     edge fired within the last L108_TRUTH_WINDOW_SEC
        #     (default 30s ≈ 2× max expected CT), we OVERRIDE to
        #     RUNNING.  Pulses on the production bit are hard
        #     evidence; the status register is software-derived and
        #     more prone to ladder bugs.
        # This is a safety net, NOT a workaround for normal flow —
        # the operator should still report any IDLE-during-production
        # so PLC engineer can fix the ladder.
        # 2026-05-22 — Window 30 → 60 s after operator complaint
        # "break ke baad IDLE jaa raha hai".  Post-break cycle resumption
        # often has a 20-40s gap before the first L108 fires (operators
        # settling back at stations, machine warm-up).  With 30s window,
        # any first-post-break L108 over 30s after PLC's D6005=0 would
        # leave IDLE painted.  60s covers the practical resume window.
        # 2026-05-26 — Bumped 60 → 180.  After collector restart or a
        # brief 1-2 min op pause, status was flipping to IDLE then
        # back to RUNNING within seconds (operator: "status idle ho
        # gya h").  3-minute window absorbs normal short interruptions
        # without flapping while still committing real long IDLE.
        L108_TRUTH_WINDOW_SEC = 180.0
        if (status_code == 0
                and self._last_ok_edge_observed
                and now - self._last_ok_edge_observed < L108_TRUTH_WINDOW_SEC):
            age = now - self._last_ok_edge_observed
            seen = getattr(self, "_l108_truth_override_seen", 0)
            self._l108_truth_override_seen = seen + 1
            # Log every override so PLC engineer has a record to chase
            # the underlying ladder bug.  Throttle to one per minute
            # so the log isn't spammed during a stuck-IDLE situation.
            if seen % 30 == 0:
                print(f"[STATUS] L108-TRUTH-OVERRIDE — PLC published IDLE "
                      f"but L108 edge {age:.1f}s ago → forcing RUNNING. "
                      f"(occurrence #{seen+1}; PLC engineer should check "
                      f"D{self.cfg.get('status_addr','????')} ladder logic.)",
                      flush=True)
            status_code = 1
        elif (status_code == 0
                and self._last_ok_edge_observed
                and now - self._last_ok_edge_observed < 120.0):
            # 2026-05-22 — BORDERLINE-MISS DIAG.  PLC says IDLE but L108
            # fired 60-120s ago.  Just outside our override window.
            # Log this so if user reports "IDLE phase post-break" we can
            # see the exact gap that's just-missing and tune the window.
            age = now - self._last_ok_edge_observed
            print(f"[STATUS] L108-TRUTH-BORDERLINE — PLC IDLE, last L108 "
                  f"{age:.1f}s ago (just outside {L108_TRUTH_WINDOW_SEC:.0f}s "
                  f"window — IDLE will commit if no edge in next "
                  f"{(self.IDLE_DWELL_SEC):.0f}s).", flush=True)

        # ── IDLE-dwell suppression (2026-05-12 fluttering-fix) ─────
        # YNC-SS PLC ladder publishes raw=0 (lower nibble IDLE) for a
        # few seconds between consecutive cycles even though the machine
        # is running normally.  Without dwell, the dashboard flaps
        # IDLE↔RUNNING every 4-6 s.  Hold the previous non-IDLE state
        # until raw stays 0 for IDLE_DWELL_SEC continuously.  Real IDLE
        # (operator stops machine) eventually commits after the dwell.
        # GAP-period transitions are NOT dwelled — non-production shift
        # phases must transition immediately.
        #
        # 2026-05-16 — TIMELINE TIMESTAMP FIX.  Previously when the dwell
        # expired we wrote the IDLE row with ts=now(), which is 25 s
        # AFTER IDLE actually started on the PLC.  Frontend then painted
        # 25 s of phantom RUNNING followed by a tiny IDLE strip → users
        # complained "timeline kuch bhi show kar rahi hai".  Now we
        # backdate the commit ts to `_pending_idle_since` so the
        # rendered segment matches the real PLC dwell window.
        dwell_commit_ts = None       # if set, write_status_log uses this
        if (status_code == 0
                and self._cur_status not in (0, -1)
                and not self._is_in_gap_period()):
            if self._pending_idle_since is None:
                self._pending_idle_since = now
            if now - self._pending_idle_since < self.IDLE_DWELL_SEC:
                # Suppress: keep previous non-IDLE state
                status_code = self._cur_status
            else:
                # Dwell expired → genuine IDLE.  Capture the true start
                # time BEFORE we clear _pending_idle_since so the DB row
                # reflects when IDLE really began, not when dwell expired.
                dwell_commit_ts = datetime.fromtimestamp(self._pending_idle_since)
                self._pending_idle_since = None
        else:
            # Any non-IDLE arrival clears a pending dwell
            self._pending_idle_since = None

        if not (self._cur_shift and self._cur_shift.startswith("GAP")):
            # Loss accumulates based on the PLC's reported status — no
            # shift-start grace window. If the machine publishes BREAKDOWN
            # at 08:34, those seconds become breakdown loss even if we're
            # still inside the old "startup delay" minute-count.
            old_info  = status_map.get(old, {})
            loss_type = old_info.get("loss") if isinstance(old_info, dict) else None
            if loss_type and loss_type in self._loss:
                self._loss[loss_type] += elapsed

        # ── CONDITIONAL SCHEDULE OVERRIDES (2026-05-12) ────────────
        # Reinstated by operator request after observing GAP_BA showing
        # RUNNING even though the machine was clearly stopped — the PLC
        # ladder leaves D6005 holding its last RUNNING value when the
        # operator forgets to hit the IDLE button between shifts.
        #
        # Three soft overrides — BUT ONLY when PLC publishes RUNNING (1).
        # Real loss codes from PLC (BREAKDOWN / QUALITY_ISSUE /
        # MATERIAL_WAIT / OTHER_LOSS / CHANGE_OVER / MODEL_SETUP) ALWAYS
        # pass through unchanged because those represent actual machine
        # state more important than the schedule.
        #
        #   1. GAP between shifts         → IDLE
        #   2. Scheduled break (lunch/tea) → IDLE
        #   3. Shift-start startup delay  → MODEL_SETUP
        #
        # Bit-level decoding above (`& 0x0F` mask + ambiguous-IDLE guard)
        # is preserved.  This block only acts on the already-decoded code.
        in_break, break_name = self._is_break()
        override_reason = None

        # 2026-05-13 — startup-delay is now an UNCONDITIONAL hard
        # override.  Earlier this was gated behind `status_code == 1`,
        # so if the PLC published IDLE / BREAKDOWN during the first
        # 5 min the dashboard showed THAT instead of MODEL_SETUP.
        # Operator wants the first 5 min painted MODEL_SETUP regardless
        # of what the PLC reports, so hand-over time is unambiguous.
        if self._is_in_startup_delay():
            setup = self._find_setup_status()
            if setup and setup[0] is not None:
                status_code = setup[0]              # → MODEL_SETUP
                override_reason = "STARTUP_DELAY"

        # Soft overrides — fire whenever PLC reports a NEUTRAL status
        # (RUNNING / IDLE) but the wall clock says we're inside a
        # scheduled non-production window.  Real loss codes from PLC
        # (BREAKDOWN / QUALITY / MATERIAL / SETUP / CHANGE_OVER /
        # OTHER) pass through unchanged so a genuine fault during break
        # is never masked.
        #
        # 2026-05-18 — Extended the gate from `status_code == 1` to
        # `status_code in (0, 1)`.  The old logic only painted BREAK
        # when PLC happened to report RUNNING; once the machine truly
        # stopped (PLC=0 IDLE) the override skipped and the timeline
        # painted IDLE over the rest of the break.  Operator complaint:
        # "break time IDLE so overwrite hue hai".  Now BREAK covers the
        # full scheduled window regardless of PLC IDLE/RUNNING flap.
        elif status_code in (0, 1):
            if self._is_in_gap_period():
                status_code = 0                     # → IDLE
                override_reason = "GAP"
            elif in_break:
                # → BREAK if mes_status_mappings has a row with
                # loss_type='break'; otherwise fall back to IDLE.
                # Operator wants the dashboard timeline to PAINT the
                # scheduled break in blue (#7dd3fc), distinct from
                # operator-absent IDLE.
                brk = self._find_break_status()
                if brk and brk[0] is not None:
                    status_code = brk[0]
                else:
                    status_code = 0                 # legacy fallback
                override_reason = f"BREAK[{break_name or '?'}]"

        info        = self.cfg["status_map"].get(status_code, {})
        status_name = info.get("name", str(status_code))

        # 2026-05-15 — Seed-write on first PLC read so the timeline has
        # an anchor even when collector starts up while machine is
        # already in its terminal state (e.g. RUNNING since last shift)
        # and no transition will ever fire.  Without this seed, the
        # timeline bar shows neutral gray until the next status change,
        # which can be hours.  The DB-side dedup in _write_status_log
        # prevents this from spamming when restarts cluster together.
        if not getattr(self, "_status_seeded", False) and status_code not in (-1, -2):
            self._cur_status_name = status_name
            self._write_status_log(status_name)
            self._status_seeded = True

        if status_code != self._cur_status:
            self._cur_status      = status_code
            self._cur_status_name = status_name
            self.ct.set_running(status_code == 1)
            old_name = self.cfg["status_map"].get(old, {}).get("name", str(old))
            # Include raw PLC value so we can diagnose phantom transitions —
            # "RUNNING -> IDLE +4.7s (raw=0)" means PLC truly said IDLE,
            # "RUNNING -> IDLE +4.7s (raw=16)" would have been the masking bug.
            raw_str = ""
            if hasattr(self, "_last_raw_status") and self._last_raw_status != status_code:
                raw_str = f" (raw={self._last_raw_status})"
            override_tag = f" [override:{override_reason}]" if override_reason else ""
            print(f"[STATUS] {old_name} -> {self._cur_status_name} +{elapsed:.1f}s{raw_str}{override_tag}")
            # 2026-05-15 — Department review:  Timeline must be painted
            # ONLY from the collector's PLC-bit reading.  Earlier the
            # frontend POSTed status changes to mes_status_log, which
            # meant N open dashboards (operator HMI + supervisor LCD +
            # plant manager laptop) each wrote their OWN debounced
            # interpretation — different polling jitter, different
            # transient PLC blips → timeline filled with bogus IDLE /
            # BREAKDOWN / BREAK chunks even while the count incremented.
            #
            # Authoritative write moves here: collector sees the PLC
            # status bit directly at 30 ms cadence and is the single
            # source of truth.  Frontends now READ-ONLY from this table.
            # When this transition is the IDLE-dwell expiration, pass
            # the true PLC-side IDLE start time (dwell_commit_ts) so
            # the timeline row's ts is when IDLE *really* started, not
            # when the dwell timer hit zero (25 s late).
            self._write_status_log(self._cur_status_name, at_ts=dwell_commit_ts)
            # Auto-track Maintenance breakdown ticket on this transition
            # — opens an ANDON row when entering breakdown, resolves it
            # when leaving.  Frontend MaintenanceDashboard polls the same
            # rows, so the ANDON + History tables update in realtime.
            self._handle_breakdown_transition(old, status_code)

        self._last_status_check = now

    # ----------------------------------------------------------
    # STATUS LOG WRITER — single source of truth (2026-05-15)
    # ----------------------------------------------------------
    def _write_status_log(self, status_name: str, at_ts: Optional[datetime] = None) -> None:
        """Append one row to mes_status_log for the line.  Called from
        _update_status() on every PLC status transition.

        `at_ts` (optional) lets the caller backdate the entry to when
        the PLC actually entered this status.  Used by the IDLE-dwell
        path so the timeline doesn't show 25 s of phantom RUNNING
        followed by a thin IDLE strip.  Defaults to datetime.now().

        Idempotent guard: if the LAST row already matches this status
        the insert is skipped (cheap noise filter against rapid
        transition retries).
        """
        try:
            line_id = self.cfg.get("line_id")
            if not line_id or not self._cur_shift or self._cur_shift.startswith("GAP"):
                # GAP rows would pollute the per-shift timeline — and
                # operators don't care about the gap-period status anyway.
                return
            if not self._ensure_db_connection():
                return
            cur = self._db.cursor()
            cur.execute(
                "SELECT status FROM mes_status_log "
                "WHERE line_id = %s ORDER BY ts DESC LIMIT 1",
                (line_id,),
            )
            last = cur.fetchone()
            if last and last[0] == status_name:
                cur.close()
                return
            ts  = at_ts if at_ts is not None else datetime.now()
            nmf = ts.hour * 60.0 + ts.minute + ts.second / 60.0 + ts.microsecond / 60_000_000.0
            cur.execute(
                "INSERT INTO mes_status_log "
                "(line_id, record_date, shift_name, status, ts, nowminfrac) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (line_id, ts.date(), self._cur_shift, status_name, ts, nmf),
            )
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[STATUS-LOG] write failed: {e}")
            try: self._db.rollback()
            except Exception: pass

    # ----------------------------------------------------------
    # OEE
    # ----------------------------------------------------------

    def _oee(self) -> dict:
        if not self._shift_start_ts or (self._cur_shift or "").startswith("GAP"):
            return {"avail": 0, "perf": 0, "qual": 100,
                    "overall": 0, "grade": "GAP"}

        working_seconds = self._working_seconds()
        plan_s          = max(1, working_seconds)
        total           = self.ok_shift + self.ng_shift

        # 2026-05-15 — OEE FIX after department review.  Old code summed
        # ALL loss buckets (including speed) into total_loss and used
        # run_s = plan_s − total_loss in BOTH availability and
        # performance.  That makes the math self-cancel:
        #   speed_loss ≡ actual_run − total*ideal_ct
        #   run_s     == actual_run − speed_loss == total*ideal_ct
        #   perf      == total*ideal_ct / run_s × 100 == 100%
        # Result: Performance was pinned to 100% no matter how slow
        # the cycles ran.  Textbook OEE keeps Speed loss in the
        # Performance ratio ONLY (via the total × ideal_ct numerator),
        # so we now split losses into "availability" (downtime) and
        # "speed" buckets and use the downtime-only run_s for both
        # avail and perf calculations.
        avail_losses = (
            self._loss.get("breakdown",   0.0)
            + self._loss.get("quality",     0.0)
            + self._loss.get("setup",       0.0)
            + self._loss.get("material",    0.0)
            + self._loss.get("others",      0.0)
            + self._loss.get("change_over", 0.0)
        )
        run_s = max(0, plan_s - avail_losses)

        avail   = min(100, max(0, run_s / plan_s * 100))
        perf    = (min(100, max(0, (total * self.cfg["ideal_ct"] / run_s * 100)))
                   if run_s > 0 and total > 0 else 0)
        qual    = (self.ok_shift / total * 100) if total > 0 else 100
        overall = avail * perf * qual / 10000

        if overall >= 85:   grade = "EXCELLENT"
        elif overall >= 75: grade = "GOOD"
        elif overall >= 65: grade = "AVERAGE"
        elif overall >= 55: grade = "FAIR"
        else:               grade = "POOR"

        return {"avail":   round(avail, 2),   "perf":    round(perf, 2),
                "qual":    round(qual, 2),     "overall": round(overall, 2),
                "grade":   grade}

    # ----------------------------------------------------------
    # HOURLY
    # ----------------------------------------------------------

    def _update_hourly(self, new_ok: int, new_ng: int):
        if self._is_in_gap_period():
            return
        slot = self._get_current_slot()
        if not slot:
            return
        if slot not in self._hourly_data:
            self._hourly_data[slot] = {"ok": 0, "ng": 0, "plan": 0}

        self._hourly_data[slot]["ok"] += new_ok
        self._hourly_data[slot]["ng"] += new_ng

        new_plan = self._realtime_slot_plan(slot)
        if new_plan > self._hourly_data[slot].get("plan", 0):
            self._hourly_data[slot]["plan"] = new_plan

        self._write_hourly_to_db(slot)

        if slot != self._cur_hour_key:
            if self._cur_hour_key:
                self._write_hourly_to_db(self._cur_hour_key)
            self._cur_hour_key = slot

    def _write_hourly_to_db(self, slot: str):
        if not self._shift_id or not self._ensure_db_connection():
            return
        col = self.cfg["slot_to_db"].get(slot)
        if not col:
            return

        hd       = self._hourly_data.get(slot, {})
        ok_count = hd.get("ok",   0)
        ng_count = hd.get("ng",   0)
        plan     = hd.get("plan", 0)
        actual   = ok_count + ng_count
        variance = actual - plan

        try:
            cur = self._db.cursor()
            cur.execute(f"""
                UPDATE {self.cfg['table_name']} SET
                    {col}_ok       = %s,
                    {col}_ng       = %s,
                    {col}_plan     = %s,
                    {col}_actual   = %s,
                    {col}_variance = %s,
                    updated_at     = NOW()
                WHERE id = %s
            """, (ok_count, ng_count, plan, actual, variance, self._shift_id))
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[HOURLY] Write error: {e}")
            self._db_ok = False
            self._safe_rollback()

    def _refresh_all_slot_plans(self):
        if not self._shift_id or not self._ensure_db_connection():
            return
        try:
            cur       = self._db.cursor()
            all_slots = [sl for slots in self.cfg["hourly_plan"].values()
                         for sl in slots.keys()]
            for slot in all_slots:
                rt     = self._realtime_slot_plan(slot)
                col    = self.cfg["slot_to_db"].get(slot)
                if not col:
                    continue
                stored = self._hourly_data.get(slot, {}).get("plan", 0)
                final  = max(rt, stored)
                if slot in self._hourly_data:
                    self._hourly_data[slot]["plan"] = final
                hd       = self._hourly_data.get(slot, {})
                actual   = hd.get("ok", 0) + hd.get("ng", 0)
                variance = actual - final
                cur.execute(f"""
                    UPDATE {self.cfg['table_name']} SET
                        {col}_plan     = %s,
                        {col}_variance = %s,
                        updated_at     = NOW()
                    WHERE id = %s
                """, (final, variance, self._shift_id))
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[REFRESH] Error: {e}")
            self._db_ok = False
            self._safe_rollback()

    # ----------------------------------------------------------
    # SHIFT RECORD
    # ----------------------------------------------------------

    def _get_or_create_shift(self, shift_name: str, record_date: date) -> int:
        tbl = self.cfg["table_name"]
        cur = self._db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Cleanup: mark stale orphaned non-completed rows as completed
        try:
            cur.execute(f"""
                UPDATE {tbl}
                SET is_shift_completed = true, updated_at = NOW()
                WHERE is_shift_completed = false
                  AND (timestamp IS NULL OR timestamp < NOW() - INTERVAL '30 seconds')
                  AND id != COALESCE((
                      SELECT id FROM {tbl}
                      WHERE shift_name = %s AND record_date = %s
                        AND is_shift_completed = false
                      ORDER BY created_at DESC LIMIT 1
                  ), -1)
            """, (shift_name, record_date))
            self._db.commit()
        except Exception as _oc_err:
            print(f"[SHIFT] Orphan cleanup warning: {_oc_err}")
            self._db.rollback()

        # Check for active (non-completed) shift record
        cur.execute(f"""
            SELECT id FROM {tbl}
            WHERE record_date = %s AND shift_name = %s
              AND is_shift_completed = false
            ORDER BY created_at DESC LIMIT 1
        """, (record_date, shift_name))
        row = cur.fetchone()
        if row:
            shift_id = row["id"]
            self._shift_start_ts = self._get_shift_start_timestamp(
                shift_name, record_date)
            print(f"[SHIFT] Continuing existing {shift_name} ID={shift_id}")
            self._shift_id = shift_id
            self._load_shift_data(shift_id)
            self._init_all_hourly_slots_for_shift(shift_name)
            self._write_all_slots_to_db_once()
            cur.close()
            return shift_id

        # B shift — check yesterday
        if shift_name == "B":
            yesterday = record_date - timedelta(days=1)
            cur.execute(f"""
                SELECT id FROM {tbl}
                WHERE record_date = %s AND shift_name = 'B'
                  AND is_shift_completed = false
                ORDER BY created_at DESC LIMIT 1
            """, (yesterday,))
            row = cur.fetchone()
            if row:
                shift_id = row["id"]
                self._shift_start_ts = self._get_shift_start_timestamp(
                    shift_name, yesterday)
                print(f"[SHIFT] Continuing B shift from yesterday ID={shift_id}")
                self._shift_id = shift_id
                self._load_shift_data(shift_id)
                self._init_all_hourly_slots_for_shift(shift_name)
                self._write_all_slots_to_db_once()
                cur.close()
                return shift_id

        # Check for a completed record
        cur.execute(f"""
            SELECT id FROM {tbl}
            WHERE record_date=%s AND shift_name=%s AND is_shift_completed=true
            ORDER BY id DESC LIMIT 1
        """, (record_date, shift_name))
        row = cur.fetchone()
        if row:
            shift_id = row["id"]
            self._shift_start_ts = self._get_shift_start_timestamp(
                shift_name, record_date)

            # ── OT resumption: if overtime is active for this shift, REOPEN the
            # completed row with its existing counts instead of resetting to 0.
            # This way actual continues from 1554 (or wherever it left off).
            ot = self._check_ot_active()
            if ot == shift_name:
                self._db.cursor().execute(f"""
                    UPDATE {tbl} SET
                        is_shift_completed=false,
                        operating_status=%s, timestamp=NOW()
                    WHERE id=%s
                """, (self._cur_status_name, shift_id))
                self._db.commit()
                self._shift_id = shift_id
                self._load_shift_data(shift_id)
                self._init_all_hourly_slots_for_shift(shift_name)
                self._write_all_slots_to_db_once()
                cur.close()
                print(f"[SHIFT] OT resuming {shift_name} ID={shift_id} (keeping existing counts)")
                return shift_id

            # Normal shift restart — reset to 0
            self._reset_counts()
            _reset_scfg = self.cfg["shifts"].get(shift_name)
            _reset_plan = 0 if shift_name.startswith("GAP") else (_reset_scfg.get("total_plan", 0) if _reset_scfg else 0)
            self._db.cursor().execute(f"""
                UPDATE {tbl} SET
                    ok_count=0, ng_count=0,
                    shift_plan=%s, shift_plan_remaining=%s,
                    shift_plan_completed=0,
                    is_shift_completed=false,
                    operating_status=%s, timestamp=NOW()
                WHERE id=%s
            """, (_reset_plan, _reset_plan, self._cur_status_name, shift_id))
            self._db.commit()
            self._init_all_hourly_slots_for_shift(shift_name)
            self._shift_id = shift_id
            self._write_all_slots_to_db_once()
            cur.close()
            print(f"[SHIFT] Reset existing record ID={shift_id}")
            return shift_id

        # Create new shift record
        self._reset_counts()
        is_gap = shift_name.startswith("GAP")
        scfg   = self.cfg["shifts"].get(shift_name)
        plan   = 0 if is_gap else (scfg.get("total_plan", 0) if scfg else 0)

        shift_start_time = scfg["start_time"] if scfg else dt_time(8, 30)
        if isinstance(shift_start_time, dt_time):
            shift_start_time = shift_start_time.strftime("%H:%M:%S")

        cur2 = self._db.cursor()
        cur2.execute(f"""
            INSERT INTO {tbl}
                (record_date, shift_name, shift_start_time, line_name,
                 ok_count, ng_count,
                 shift_plan, shift_plan_remaining, shift_plan_completed,
                 cycle_time_plan, operating_status, is_shift_completed,
                 period_type, is_gap_time, timestamp)
            VALUES (%s,%s,%s,%s,0,0,%s,%s,0,%s,%s,false,%s,%s,NOW())
            RETURNING id
        """, (
            record_date, shift_name, shift_start_time,
            self.cfg["line_name"],
            plan, plan, self.cfg["ideal_ct"],
            self._cur_status_name,
            "GAP" if is_gap else "SHIFT", is_gap,
        ))
        shift_id = cur2.fetchone()[0]
        self._db.commit()
        cur2.close()
        cur.close()

        self._shift_id = shift_id
        self._shift_start_ts = self._get_shift_start_timestamp(
            shift_name, record_date)
        self._init_all_hourly_slots_for_shift(shift_name)
        self._write_all_slots_to_db_once()
        print(f"[SHIFT] Created new {shift_name} ID={shift_id}")
        return shift_id

    def _load_shift_data(self, shift_id: int):
        """
        Load all persisted data from DB into memory on collector startup/resume.
        Restores: ok/ng counts, loss seconds, hourly slot data,
                  cycle times (ct1-ct20), and break accumulator.
        This ensures the collector continues exactly from where it left off.
        """
        tbl = self.cfg["table_name"]
        cur = self._db.cursor()
        try:
            # ── 1. Load counts + losses + hourly slots ─────────────────
            slot_columns = []
            for prefix in self.cfg["slot_to_db"].values():
                slot_columns.extend([
                    f"{prefix}_ok", f"{prefix}_ng",
                    f"{prefix}_plan", f"{prefix}_actual", f"{prefix}_variance"
                ])
            cols = [
                "ok_count", "ng_count",
                "loss_breakdown_seconds", "loss_quality_seconds",
                "loss_setup_seconds",     "loss_material_seconds",
                "loss_others_seconds",    "loss_speed_seconds",
                "loss_change_over_seconds",
            ] + slot_columns

            cur.execute(
                f"SELECT {', '.join(cols)} FROM {tbl} WHERE id = %s",
                (shift_id,))
            row = cur.fetchone()
            if row:
                self.ok_shift             = row[0] or 0
                self.ng_shift             = row[1] or 0
                self._loss["breakdown"]   = row[2] or 0
                self._loss["quality"]     = row[3] or 0
                self._loss["setup"]       = row[4] or 0
                self._loss["material"]    = row[5] or 0
                self._loss["others"]      = row[6] or 0
                self._loss["speed"]       = row[7] or 0
                self._loss["change_over"] = row[8] or 0
                self.ct.speed_loss        = self._loss["speed"]

                idx = 9
                for slot_label in self.cfg["slot_to_db"].keys():
                    if idx + 2 < len(row):
                        if slot_label not in self._hourly_data:
                            self._hourly_data[slot_label] = {"ok": 0, "ng": 0, "plan": 0}
                        self._hourly_data[slot_label]["ok"]   = row[idx]     or 0
                        self._hourly_data[slot_label]["ng"]   = row[idx + 1] or 0
                        self._hourly_data[slot_label]["plan"] = row[idx + 2] or 0
                        idx += 5

                print(f"[SHIFT] Loaded: OK={self.ok_shift}, NG={self.ng_shift}, "
                      f"Loss={sum(self._loss.values()):.0f}s")

            # ── 2. Restore cycle times from DB (ct1-ct20) ──────────────
            # This prevents the CT avg from resetting to ideal_ct on restart
            try:
                ct_col_list = ", ".join(f"ct{i}" for i in range(1, 21))
                cur.execute(
                    f"SELECT {ct_col_list} FROM {tbl} WHERE id = %s",
                    (shift_id,))
                ct_row = cur.fetchone()
                if ct_row:
                    ct_vals = [float(v) for v in ct_row
                               if v is not None and float(v) > 0]
                    if ct_vals:
                        self.ct.cycle_times = ct_vals[-20:]
                        print(f"[SHIFT] Restored {len(ct_vals)} cycle time samples "
                              f"(avg={sum(ct_vals)/len(ct_vals):.2f}s)")
            except Exception as ct_err:
                print(f"[SHIFT] CT restore warning (non-fatal): {ct_err}")

            # ── 3. Restore break accumulator ───────────────────────────
            # Calculate how much break time has already passed since shift
            # start so _working_seconds() doesn't recount past breaks.
            self._break_seconds_acc  = 0.0
            self._cur_break_start_ts = None

            if self._shift_start_ts:
                now_dt         = datetime.now()
                shift_start_dt = datetime.fromtimestamp(self._shift_start_ts)

                for b in self.cfg["breaks"]:
                    bs = b["start_time"]
                    be = b["end_time"]
                    if isinstance(bs, str):
                        bs = dt_time(*map(int, bs.split(":")))
                    if isinstance(be, str):
                        be = dt_time(*map(int, be.split(":")))

                    bs_dt = datetime.combine(shift_start_dt.date(), bs)
                    be_dt = datetime.combine(shift_start_dt.date(), be)
                    if b["crosses_midnight"]:
                        be_dt += timedelta(days=1)

                    # Only count breaks that have fully ended before now
                    # (ongoing break is handled by _cur_break_start_ts logic)
                    ov_s = max(shift_start_dt, bs_dt)
                    ov_e = min(now_dt, be_dt)
                    if ov_e > ov_s:
                        self._break_seconds_acc += (ov_e - ov_s).total_seconds()

                print(f"[SHIFT] Break accumulator restored: "
                      f"{self._break_seconds_acc:.1f}s elapsed in breaks so far")

        except Exception as e:
            print(f"[SHIFT] Error loading data: {e}")
            traceback.print_exc()
        finally:
            cur.close()

    def _reset_counts(self):
        self.ok_shift        = 0
        self.ng_shift        = 0
        self._loss           = {k: 0.0 for k in self._loss}
        self._plan_completed = 0
        self._hourly_data    = {}
        self.ct.reset()
        # Reset break accumulator so every new shift starts clean
        self._break_seconds_acc  = 0.0
        self._cur_break_start_ts = None

    # ----------------------------------------------------------
    # MAIN DB WRITE
    # ----------------------------------------------------------

    def _ensure_ct_log_table(self) -> bool:
        """Create the per-line ct_log table once, if it doesn't exist yet."""
        if self._ct_log_table_ready:
            return True
        tbl = self.cfg["table_name"] + "_ct_log"
        try:
            cur = self._db.cursor()
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {tbl} (
                    id          SERIAL PRIMARY KEY,
                    ts          TIMESTAMP NOT NULL,
                    record_date DATE      NOT NULL,
                    shift_name  VARCHAR(20),
                    ct_value    NUMERIC(7,2) NOT NULL,
                    cycle_seq   INTEGER,
                    part_code   VARCHAR(64),
                    is_ng       BOOLEAN DEFAULT FALSE
                );
                CREATE INDEX IF NOT EXISTS {tbl}_date_shift
                    ON {tbl}(record_date, shift_name);
            """)
            # Backfill columns for existing installations (idempotent).
            cur.execute(
                f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS part_code VARCHAR(64)"
            )
            cur.execute(
                f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS is_ng BOOLEAN DEFAULT FALSE"
            )
            self._db.commit()
            cur.close()
            self._ct_log_table_ready = True
            return True
        except Exception as e:
            print(f"[CT_LOG] Table create error: {e}")
            self._safe_rollback()
            return False

    # ── Part code from PLC word registers ────────────────────────
    # Same Node-RED / New-folder-2 convention: D5004, 13 word registers,
    # each register holds 2 ASCII chars in byte-reversed order (low byte first).
    _PART_CODE_ADDR = "D5004"
    _PART_CODE_LEN  = 13

    def _read_part_code(self) -> str:
        if not self._plc_ok or self._plc is None:
            return ""
        try:
            regs = self._plc.batchread_wordunits(
                headdevice=self._PART_CODE_ADDR,
                readsize=self._PART_CODE_LEN,
            )
        except Exception as exc:
            print(f"[PLC] Part code read error: {exc}")
            return ""
        chars = []
        for reg in regs:
            high_byte = reg & 0xFF
            low_byte  = (reg >> 8) & 0xFF
            if high_byte > 0:
                chars.append(chr(high_byte))
            if low_byte > 0:
                chars.append(chr(low_byte))
        return "".join(chars).strip().strip("\x00")

    def _flush_ct_log(self):
        """Write buffered cycle time entries to the ct_log table."""
        if not self._ct_pending_log:
            return
        if not self._ensure_ct_log_table():
            return
        tbl = self.cfg["table_name"] + "_ct_log"
        rows = list(self._ct_pending_log)
        self._ct_pending_log.clear()
        try:
            cur = self._db.cursor()
            cur.executemany(
                f"INSERT INTO {tbl}(ts, record_date, shift_name, ct_value, cycle_seq, part_code, is_ng) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[CT_LOG] Flush error: {e}")
            self._safe_rollback()

    # ─────────────────────────────────────────────────────────────────
    # MACHINE PROCESS SAMPLING
    # ─────────────────────────────────────────────────────────────────
    # Drives the Process Graphs page (/process-graphs in frontend).
    # Each process is one row in mes_machine_processes:
    #     (process_no, process_name, target_value, actual_register, register_type)
    # We poll the configured PLC register on a schedule and INSERT a
    # timestamped row into mes_machine_process_log so the frontend can
    # render a bar chart of actual vs target over time.
    #
    # Only main-PLC processes are sampled here (parent_plc_id IS NULL)
    # because that's the PLC this collector instance has open.
    # Sub-machine processes need their own sub-poller — TODO when the
    # operator configures any.
    def _reload_machine_processes(self):
        """Pull the current process config from DB.  Idempotent.  Runs
        every 30 s in the main loop so admin-panel edits go live
        without a collector restart."""
        if not self._db_ok or not self._db:
            return
        try:
            cur = self._db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Join with mes_plc_configs (NOT legacy mes_machines master).
            # Filter to MAIN PLC machines only — sub-machine processes
            # would need their own connection and aren't sampled here.
            cur.execute("""
                SELECT p.id, p.machine_id, p.process_no,
                       p.process_name, p.target_value,
                       p.actual_register, p.register_type
                  FROM mes_machine_processes p
                  JOIN mes_plc_configs m ON m.id = p.machine_id
                 WHERE m.line_id = %s
                   AND m.parent_plc_id IS NULL
                   AND p.is_active = TRUE
                 ORDER BY p.process_no
            """, (self.cfg["line_id"],))
            new_list = [dict(r) for r in cur.fetchall()]
            cur.close()

            # Only log on actual change so we don't spam the console
            if len(new_list) != len(self._machine_processes):
                names = [p["process_name"] for p in new_list]
                print(f"[PROCESS] Reloaded {len(new_list)} configured "
                      f"process{'es' if len(new_list)!=1 else ''}: {names}")
            self._machine_processes = new_list
        except Exception as e:
            print(f"[PROCESS] Reload failed: {e}")
            self._safe_rollback()

    def _poll_machine_process_pulses(self):
        """Called from the main loop every poll iteration (~30 ms).

        For BIT-type process registers (e.g. L108 OK pulse):
          • Rising edge (0→1) → record start_ts for that process.
          • Falling edge (1→0) → compute ON duration and buffer a row
            for mes_machine_process_pulses; this drives the per-pulse
            spike graph (width = ON time) on the Process Graphs page.
          • Also bumps the per-minute rising-edge count for backward
            compat (still written to mes_machine_process_log).

        Word-type processes are NOT touched here — they're sampled once
        per 60 s by `_sample_machine_processes()`."""
        if not self._machine_processes:
            return
        if not (self._plc_ok and self._plc):
            return

        if not hasattr(self, "_proc_pulse_state"):
            self._proc_pulse_state    = {}    # {process_id: last_bit_value}
            self._proc_pulse_count    = {}    # {process_id: rising-edge count}
            self._proc_pulse_start_ts = {}    # {process_id: datetime when bit went HIGH}
            self._proc_pulse_log_buf  = []    # [(process_id, started_at, duration_ms), ...]

        for p in self._machine_processes:
            if (p["register_type"] or "").lower() != "bit":
                continue
            reg = (p["actual_register"] or "").strip()
            if not reg:
                continue
            try:
                v = self._plc.batchread_bitunits(headdevice=reg, readsize=1)
                if not v:
                    continue
                cur_val  = int(v[0])
                prev_val = self._proc_pulse_state.get(p["id"], 0)
                self._proc_pulse_state[p["id"]] = cur_val
                # Rising edge = one cycle / pulse / part
                if cur_val == 1 and prev_val == 0:
                    self._proc_pulse_count[p["id"]] = \
                        self._proc_pulse_count.get(p["id"], 0) + 1
                    self._proc_pulse_start_ts[p["id"]] = datetime.now()
                # Falling edge = pulse ended → log start_ts + duration
                elif cur_val == 0 and prev_val == 1:
                    start = self._proc_pulse_start_ts.pop(p["id"], None)
                    if start is not None:
                        dur_ms = int((datetime.now() - start).total_seconds() * 1000)
                        if dur_ms < 1:
                            dur_ms = 1          # PLC scan boundary — minimum 1 ms
                        self._proc_pulse_log_buf.append((p["id"], start, dur_ms))
            except Exception:
                # Silent — bad register already warned by _sample_*
                pass

    def _sample_machine_processes(self):
        """Write one log row per configured process to
        mes_machine_process_log.  Runs every 60 s.

        BIT-type   : value = number of rising edges counted in the last
                     60 s window (drained from _proc_pulse_count).
                     This is what the operator graphs as "cycles per
                     window".
        WORD-type  : value = current PLC word value (cumulative count
                     register, sensor reading, etc.).  Read fresh here."""
        if not self._machine_processes:
            return
        if not (self._plc_ok and self._plc) or not (self._db_ok and self._db):
            return

        now_ts = datetime.now()
        rows_to_insert = []

        # Ensure pulse-count dict exists (it normally does after the
        # first fast-poll tick, but be defensive).
        if not hasattr(self, "_proc_pulse_count"):
            self._proc_pulse_count = {}
            self._proc_pulse_state = {}

        for p in self._machine_processes:
            reg  = (p["actual_register"] or "").strip()
            rtyp = (p["register_type"] or "word").lower()
            if not reg:
                continue

            if rtyp == "bit":
                # Drain the accumulated pulse count for this 60s window.
                count = self._proc_pulse_count.get(p["id"], 0)
                rows_to_insert.append((p["id"], count, now_ts))
                self._proc_pulse_count[p["id"]] = 0    # reset for next window
                continue

            # Word — current value
            try:
                v = self._plc.batchread_wordunits(headdevice=reg, readsize=1)
                if v:
                    val = int(v[0])
                    rows_to_insert.append((p["id"], val, now_ts))
            except Exception as e:
                seen = getattr(self, "_proc_read_warned", None)
                if seen is None:
                    seen = set(); self._proc_read_warned = seen
                key = (p["id"], reg)
                if key not in seen:
                    seen.add(key)
                    print(f"[PROCESS] Read failed for {p['process_name']!r} "
                          f"@ {reg} ({rtyp}): {e} — skipping until fixed")

        # Flush buffered per-pulse rows (BIT) into the pulses table so
        # the Process Graphs page can render one spike per ON event.
        pulse_rows = []
        if hasattr(self, "_proc_pulse_log_buf") and self._proc_pulse_log_buf:
            pulse_rows = self._proc_pulse_log_buf
            self._proc_pulse_log_buf = []

        if not rows_to_insert and not pulse_rows:
            return

        try:
            cur = self._db.cursor()
            if rows_to_insert:
                cur.executemany("""
                    INSERT INTO mes_machine_process_log
                        (process_id, actual_value, sampled_at)
                    VALUES (%s, %s, %s)
                """, rows_to_insert)
            if pulse_rows:
                cur.executemany("""
                    INSERT INTO mes_machine_process_pulses
                        (process_id, started_at, duration_ms)
                    VALUES (%s, %s, %s)
                """, pulse_rows)
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[PROCESS] Log insert failed: {e}")
            self._safe_rollback()

    def _write_dashboard(self):
        if not self._ensure_db_connection() or not self._shift_id:
            return

        self._loss["speed"] = self.ct.speed_loss

        # GAP period — write IDLE only
        if self._cur_shift and self._cur_shift.startswith("GAP"):
            try:
                cur = self._db.cursor()
                cur.execute(f"""
                    UPDATE {self.cfg['table_name']} SET
                        operating_status     = 'IDLE',
                        shift_plan_completed = 0,
                        shift_plan_remaining = 0,
                        updated_at           = NOW(),
                        timestamp            = NOW()
                    WHERE id = %s
                """, (self._shift_id,))
                self._db.commit()
                cur.close()
            except Exception as e:
                print(f"[DB] GAP write error: {e}")
                self._db_ok = False
                self._safe_rollback()
            return

        _cur_scfg        = self.cfg["shifts"].get(self._cur_shift, {})
        _shift_plan      = 0 if (self._cur_shift or "").startswith("GAP") else _cur_scfg.get("total_plan", 0)
        working_seconds  = self._working_seconds()
        # int(round(...)) instead of plain int() so the last cycle of the
        # shift hits total_plan exactly.  Without this, a shift designed
        # for 1860 parts at 15 s ideal CT would freeze at 1859 because
        # working_seconds at 17:14:59 = 27899, and 27899 / 15 = 1859.93
        # floors to 1859.  round() turns that 1859.93 into 1860.  Mid-
        # shift behaviour is unaffected — round only diverges from floor
        # in the last half-second of any single cycle.
        planned          = min(_shift_plan,
                                int(round(working_seconds / self.cfg["ideal_ct"]))) \
                            if _shift_plan > 0 else 0
        self._plan_completed = planned

        oee        = self._oee()
        ct_d       = self.ct.ct_dict()
        total_loss = sum(self._loss.values())

        def fmt(s):
            s = int(s)
            return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

        try:
            cur = self._db.cursor()
            cur.execute("""
                UPDATE mes_lines SET collector_status='running', updated_at=NOW()
                WHERE id = %s
            """, (self.cfg["line_id"],))
            self._db.commit()
            cur.close()

            cur = self._db.cursor()
            cur.execute(f"""
                UPDATE {self.cfg['table_name']} SET
                    ok_count=GREATEST(ok_count, %s), ng_count=GREATEST(ng_count, %s),
                    current_model_number=%s, current_model_name=%s,
                    cycle_time_actual=%s, operating_status=%s,
                    availability=%s, performance=%s,
                    quality_oee=%s, overall_oee=%s, oee_grade=%s,
                    shift_plan=%s, shift_plan_remaining=%s,
                    shift_plan_completed=GREATEST(shift_plan_completed, %s),
                    loss_breakdown_seconds=%s, loss_quality_seconds=%s,
                    loss_setup_seconds=%s,     loss_material_seconds=%s,
                    loss_others_seconds=%s,    loss_speed_seconds=%s,
                    loss_change_over_seconds=%s,
                    loss_breakdown=%s, loss_quality=%s, loss_setup=%s,
                    loss_material=%s,  loss_others=%s,  loss_speed=%s,
                    loss_change_over=%s, total_loss=%s,
                    ct1=%s,ct2=%s,ct3=%s,ct4=%s,ct5=%s,
                    ct6=%s,ct7=%s,ct8=%s,ct9=%s,ct10=%s,
                    ct11=%s,ct12=%s,ct13=%s,ct14=%s,ct15=%s,
                    ct16=%s,ct17=%s,ct18=%s,ct19=%s,ct20=%s,
                    ct_avg_20=%s, min_ct=%s, max_ct=%s, std_dev_ct=%s,
                    updated_at=NOW(), timestamp=NOW()
                WHERE id=%s
            """, (
                self.ok_shift, self.ng_shift,
                self._cur_model, self._cur_model_name,
                min(99.99, ct_d["ct_avg_20"]), self._cur_status_name,
                min(99.99, oee["avail"]),  min(99.99, oee["perf"]),
                min(99.99, oee["qual"]),   min(99.99, oee["overall"]),
                oee["grade"],
                _shift_plan, max(0, _shift_plan - planned), planned,
                int(self._loss["breakdown"]),   int(self._loss["quality"]),
                int(self._loss["setup"]),        int(self._loss["material"]),
                int(self._loss["others"]),       int(self._loss["speed"]),
                int(self._loss["change_over"]),
                fmt(self._loss["breakdown"]),    fmt(self._loss["quality"]),
                fmt(self._loss["setup"]),        fmt(self._loss["material"]),
                fmt(self._loss["others"]),       fmt(self._loss["speed"]),
                fmt(self._loss["change_over"]),  fmt(total_loss),
                ct_d["ct1"],  ct_d["ct2"],  ct_d["ct3"],  ct_d["ct4"],  ct_d["ct5"],
                ct_d["ct6"],  ct_d["ct7"],  ct_d["ct8"],  ct_d["ct9"],  ct_d["ct10"],
                ct_d["ct11"], ct_d["ct12"], ct_d["ct13"], ct_d["ct14"], ct_d["ct15"],
                ct_d["ct16"], ct_d["ct17"], ct_d["ct18"], ct_d["ct19"], ct_d["ct20"],
                ct_d["ct_avg_20"], ct_d["min_ct"], ct_d["max_ct"], ct_d["std_dev_ct"],
                self._shift_id,
            ))
            self._db.commit()
            cur.close()

            # Flush buffered CT log entries
            self._flush_ct_log()

            # Periodic orphan cleanup
            try:
                cur2 = self._db.cursor()
                cur2.execute(f"""
                    UPDATE {self.cfg['table_name']}
                    SET is_shift_completed = true, updated_at = NOW()
                    WHERE is_shift_completed = false
                      AND id != %s
                      AND (timestamp IS NULL OR timestamp < NOW() - INTERVAL '30 seconds')
                """, (self._shift_id,))
                self._db.commit()
                cur2.close()
            except Exception:
                self._safe_rollback()

        except Exception as e:
            print(f"[DB] Write error: {e}")
            traceback.print_exc()
            self._db_ok = False
            self._safe_rollback()

    def _safe_rollback(self):
        try: self._db.rollback()
        except: pass

    # ----------------------------------------------------------
    # SUB-MACHINE POLLER  +  DYNAMIC RELOAD
    # ----------------------------------------------------------

    def _spawn_sub_thread(self, sub: dict) -> None:
        """Start one poller thread for `sub` and register it.
        Idempotent — no-op if a thread for this sub_id already exists.
        Caller (`_reload_subs_loop`) calls `_stop_sub_thread` first when it
        wants a hot-restart with new config."""
        sid = sub["id"]
        if sid in self._sub_workers:
            return
        stop_event = threading.Event()
        t = threading.Thread(
            target=self._run_submachine_poller,
            args=(sub, stop_event),
            daemon=True,
            name=f"sub-{sid}",
        )
        self._sub_workers[sid] = {
            "stop":   stop_event,
            "thread": t,
            "cfg_snapshot": {
                "plc_ip":    sub.get("plc_ip"),
                "plc_port":  sub.get("plc_port"),
                "count_bit": sub.get("count_bit"),
            },
        }
        t.start()
        self._sub_threads.append(t)

    def _stop_sub_thread(self, sub_id: int) -> None:
        """Signal the poller for `sub_id` to exit and remove it from the
        registry.  The loop checks its stop_event every 100 ms so the
        thread dies quickly without needing a join."""
        w = self._sub_workers.pop(sub_id, None)
        if w:
            w["stop"].set()
            print(f"[SUB-RELOAD] stop signal → sub-{sub_id}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # PY / sensor check thread (own PLC connection)
    # ──────────────────────────────────────────────────────────────
    def _py_check_loop(self) -> None:
        """Background thread that runs all the PLC-heavy poka-yoke /
        sensor-health checks WITHOUT blocking the main pulse-poll loop.

        Opens its own pymcprotocol.Type4E to the main PLC so the main
        thread's reads aren't serialised behind a 20-PY × 30-50 ms
        sequential sweep (which was missing L108/L109 edges).  If the
        dedicated connection can't be opened (PLC busy / max sessions),
        the thread exits silently — bypass detection won't run but the
        main pulse counting keeps working, which is the higher priority.
        """
        # Wait for main connection to be up first
        for _ in range(30):
            if self._plc_ok and self._plc is not None:
                break
            if self._stop.wait(1.0):
                return

        # Open a SEPARATE TCP connection to the same PLC
        my_plc = None
        try:
            my_plc = pymcprotocol.Type4E()
            my_plc.setaccessopt(commtype="binary")
            my_plc.connect(self.cfg["plc_ip"], self.cfg["plc_port"])
            print(f"[PY-CHECK] dedicated PLC connection opened "
                  f"({self.cfg['plc_ip']}:{self.cfg['plc_port']})", flush=True)
        except Exception as exc:
            print(f"[PY-CHECK] could not open dedicated PLC connection "
                  f"({exc}) — bypass detection disabled, pulse counting "
                  f"still active.", flush=True)
            return

        # Run loop.
        # 2026-05-22 — Reduced from 2.0 → 0.5 sec so PY bypass detection
        # catches D-register transients that previously slipped between
        # polls.  This thread owns its OWN TCP connection (line ~4146)
        # so the higher rate doesn't contend with the main collector
        # poll's PLC slot — Mitsubishi 2-client limit is respected.
        # Sensor health sweep inside check still self-throttles to its
        # own _x_track_interval (200ms) regardless of this loop cadence.
        while not self._stop.wait(0.5):
            tick_exc = None     # carry first error for the reconnect logic
            try:
                if self._is_in_gap_period():
                    continue
                in_break, _ = self._is_break()
                self.poka.reload_rules_from_db(self.cfg["line_id"])
                self.poka.reload_py_configs(self.cfg["line_id"])
            except Exception as exc:
                tick_exc = exc

            # 2026-05-20 — CRITICAL BUG FIX.
            # Earlier the three poka calls (check_d_registers,
            # check_py_bypass, track_sensors_health) shared a single
            # try-except.  When check_d_registers raised WinError 10054
            # (PLC closed connection), the exception bubbled up and
            # SKIPPED both check_py_bypass and track_sensors_health.
            # Result: `_x_state` for the X-bit sensor sweep stopped
            # updating completely the moment D-register reads started
            # failing, so the Sensor Health panel froze at the last
            # successful sweep time (03:14:31 in the observed case)
            # and showed every sensor as "stuck for 20h" even though
            # the X-bits were physically toggling fine.
            #
            # Fix: each poka call gets its own try-except so a failure
            # in one path does NOT silently kill the others.  The
            # WinError still triggers a single reconnect at the bottom.
            if not in_break:
                try:
                    self.poka.check_d_registers(my_plc, self._cur_shift or "")
                except Exception as exc:
                    tick_exc = tick_exc or exc
                    print(f"[PY-CHECK] check_d_registers error: {str(exc)[:80]}", flush=True)
                try:
                    self.poka.check_py_bypass(
                        my_plc, self._cur_shift or "", self._cur_model)
                except Exception as exc:
                    tick_exc = tick_exc or exc
                    print(f"[PY-CHECK] check_py_bypass error: {str(exc)[:80]}", flush=True)
            # 2026-05-22 — Push the engine's full production-window
            # state onto the Poka instance so track_sensors_health can
            # gate its stuck-flag escalation properly.  Sensor toggles
            # are only meaningful during ACTIVE production — idle,
            # breakdown, scheduled break, and between-shift gap windows
            # must all be skipped or operator sees false-positive
            # "stuck for 16 min" during a 10-min tea break.
            try:
                _gap = self._is_in_gap_period()
            except Exception:
                _gap = False
            self.poka.sensors_should_track = (
                bool(getattr(self, "is_running", False))
                and not in_break
                and not _gap
            )
            try:
                self.poka.track_sensors_health(my_plc)
            except Exception as exc:
                tick_exc = tick_exc or exc
                print(f"[PY-CHECK] track_sensors_health error: {str(exc)[:80]}", flush=True)

            # If ANY of the calls failed with a socket-style error, do
            # one reconnect attempt for next tick.
            if tick_exc is not None:
                msg = str(tick_exc)[:80]
                if "timed out" in msg.lower() or "connection" in msg.lower() or "forcibly closed" in msg.lower():
                    try:
                        my_plc.close()
                    except Exception:
                        pass
                    try:
                        my_plc = pymcprotocol.Type4E()
                        my_plc.setaccessopt(commtype="binary")
                        my_plc.connect(self.cfg["plc_ip"], self.cfg["plc_port"])
                        print(f"[PY-CHECK] reconnected after: {msg}", flush=True)
                    except Exception as r_exc:
                        print(f"[PY-CHECK] reconnect failed: {r_exc}", flush=True)

        try:
            my_plc.close()
        except Exception:
            pass
        print(f"[PY-CHECK] thread stopped", flush=True)


    def _reload_subs_loop(self) -> None:
        """Watcher thread: every 30 s diffs `mes_plc_configs` against the
        in-memory worker registry.

          • new sub_id in DB           → spawn poller (no restart needed)
          • sub_id gone from DB        → signal stop, drop from registry
          • plc_ip / port / count_bit changed → hot-restart with fresh config

        Lets admins add / edit / remove sub-machines via AdminPanel and have
        them go live within 30 s, with no Phase 2 restart."""
        # First reload starts faster than 30 s so a freshly-added sub
        # doesn't have to wait the full window after collector launch.
        sleep_s = 10
        while True:
            try:
                current_subs   = load_submachines(self.cfg.get("main_plc_id")) or []
                current_by_id  = {s["id"]: s for s in current_subs}
                current_ids    = set(current_by_id)
                known_ids      = set(self._sub_workers)

                # 1. Newly-added sub-machines → spawn
                for sid in current_ids - known_ids:
                    sub = current_by_id[sid]
                    print(
                        f"[SUB-RELOAD] +new sub id={sid} "
                        f"{sub.get('plc_ip')}:{sub.get('plc_port')} "
                        f"bit={sub.get('count_bit')} ({sub.get('machine_name')})",
                        flush=True,
                    )
                    self._spawn_sub_thread(sub)

                # 2. Removed sub-machines → stop
                for sid in known_ids - current_ids:
                    print(f"[SUB-RELOAD] -sub id={sid} no longer in DB", flush=True)
                    self._stop_sub_thread(sid)

                # 3. Existing sub-machines with changed wiring → hot-restart
                for sid in current_ids & known_ids:
                    new = current_by_id[sid]
                    old = self._sub_workers[sid]["cfg_snapshot"]
                    if (str(old.get("plc_ip"))             != str(new.get("plc_ip"))
                        or int(old.get("plc_port") or 0)   != int(new.get("plc_port") or 0)
                        or str(old.get("count_bit") or "") != str(new.get("count_bit") or "")):
                        print(
                            f"[SUB-RELOAD] ~sub id={sid} config changed "
                            f"ip={old.get('plc_ip')}→{new.get('plc_ip')} "
                            f"port={old.get('plc_port')}→{new.get('plc_port')} "
                            f"bit={old.get('count_bit')}→{new.get('count_bit')} "
                            f"— hot restart",
                            flush=True,
                        )
                        self._stop_sub_thread(sid)
                        self._spawn_sub_thread(new)
            except Exception as exc:
                print(f"[SUB-RELOAD] error: {exc}", flush=True)
            time.sleep(sleep_s)
            sleep_s = 30   # steady-state cadence after first tick

    def _run_submachine_poller(self, sub: dict, stop_event=None):
        """One thread per sub-machine. Polls its count bit on a dedicated
        MC4E connection and writes a row to mes_submachine_ct_log on every
        rising edge. Shares no mutable state with the main loop — only
        reads parent attributes (shift, model, part_code, status).

        `stop_event` is a per-sub threading.Event() that the reload loop
        flips when this sub is removed/changed in mes_plc_configs.  When
        it fires the loop exits cleanly within ~100 ms.  Falls back to the
        legacy engine-wide `self._sub_stop` if not given (backward compat)."""
        if stop_event is None:
            stop_event = self._sub_stop
        sub_id    = sub["id"]
        plc_ip    = sub["plc_ip"]
        plc_port  = int(sub["plc_port"] or 5002)
        # count_bit must be configured in admin — no hardcoded fallback.
        count_bit = (sub["count_bit"] or "").strip()
        # 2026-05-23 — Also read NG bit for mes_pulse_log audit.
        # If ng_bit isn't configured in mes_plc_configs, just skip NG
        # tracking for this sub (no error).
        ng_bit_addr = (sub.get("ng_bit") or "").strip()
        if not count_bit:
            print(f"[SUB {sub_id}] SKIP — ok_bit_address not configured in "
                  f"mes_plc_configs (machine_name={sub.get('machine_name')})",
                  flush=True)
            return
        name      = sub["machine_name"] or f"sub_{sub_id}"
        line_id   = self.cfg["line_id"]
        tag       = f"[SUB {sub_id} {name}]"
        # Persist per-sub OK/NG edge state for mes_pulse_log writes.
        last_sub_ok_ts = None   # last OK rise timestamp (for CT delta)
        last_sub_ng_ts = None   # last NG rise timestamp (for NG inter-arrival)
        last_ng_bit    = 0      # NG edge detector state
        # 2026-05-27 — Unified any-pulse-to-any-pulse anchor (same
        # model as Final Inspection).  Advances on EITHER L108 (OK)
        # OR L109 (NG) rising edge; CT for any new pulse = now - anchor.
        # Cycle's bit_type just labels which pulse closed it.
        last_any_pulse_sub_dt = None
        # 2026-05-24 — Hydrate last timestamps from DB so the FIRST row
        # after collector restart still has a meaningful ct_seconds
        # (delta from the previous shift's last edge, not NULL).
        try:
            _hc = _db_conn()
            _hcur = _hc.cursor()
            _l6tbl = (self._L6_TABLE_MAP.get(sub_id, ("","",))[0]
                      or "mes_l6_upper_rail")
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE bit_type='OK' AND record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_sub_ok_ts = _r[0] if isinstance(_r[0], datetime) else None
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE bit_type='NG' AND record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_sub_ng_ts = _r[0] if isinstance(_r[0], datetime) else None
            # 2026-05-27 — Hydrate the unified anchor too: most recent
            # pulse of ANY bit_type on this machine today.
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_any_pulse_sub_dt = (_r[0]
                    if isinstance(_r[0], datetime) else None)
            _hcur.close(); _hc.close()
            if last_sub_ok_ts or last_sub_ng_ts:
                print(f"[SUB {sub_id}] hydrated: last_ok={last_sub_ok_ts} "
                      f"last_ng={last_sub_ng_ts} "
                      f"last_any={last_any_pulse_sub_dt}", flush=True)
        except Exception as _e:
            print(f"[SUB {sub_id}] hydrate failed: {_e}", flush=True)

        # 2026-05-23 — CHATTER GUARD for sub-machine count_bit.
        # Bug found: Semi-Auto (sub_plc_id=12, M5700) produced 35 phantom
        # cycles in one shift with CT=0.4s.  The PLC ladder pulses M5700
        # ~400ms after the real cycle completion (likely a "data ready"
        # ack from the SA controller, not a new cycle).  Previous gate
        # of `ct >= 0.3` let these through, inflating Semi-Auto count
        # from 521 (real) to 557 (phantom +36).
        # New rule: reject any cycle shorter than max(2.0s, ideal_ct*0.2).
        # • ideal_ct=15 → 3.0s floor       (kills 0.4s chatter)
        # • ideal_ct=30 → 6.0s floor       (Ball Guide safe)
        # • Anything < this is impossible physical CT and almost always
        #   electrical chatter / double-pulse from the ladder.
        try:
            _sub_ideal = float(sub.get("ideal_ct") or 15.0)
        except Exception:
            _sub_ideal = 15.0
        _sub_min_ct = max(2.0, _sub_ideal * 0.2)
        print(f"{tag} chatter-guard: min CT = {_sub_min_ct:.2f}s "
              f"(ideal={_sub_ideal:.1f}s)", flush=True)

        # ── Semi-Auto data capture config (optional, separate trigger) ──
        sa_enabled   = bool(sub.get("sa_enabled"))
        sa_fetch_bit = (sub.get("sa_fetch_bit") or "").strip() if sa_enabled else ""
        sa_part_addr = (sub.get("sa_part_code_addr") or "").strip() if sa_enabled else ""
        sa_part_len  = int(sub.get("sa_part_code_len") or 0)
        sa_data_addr = (sub.get("sa_data_addr") or "").strip() if sa_enabled else ""
        sa_data_len  = int(sub.get("sa_data_len") or 0)
        sa_time_addr = (sub.get("sa_time_addr") or "").strip() if sa_enabled else ""
        sa_time_len  = int(sub.get("sa_time_len") or 0)
        sa_names     = sub.get("sa_register_names") or []
        sa_scales    = sub.get("sa_register_scales") or []
        sa_active    = (sa_enabled and sa_fetch_bit
                         and sa_data_addr and sa_data_len > 0)
        last_sa_bit  = 0
        # 2026-05-22 — SA-as-cycle fallback.  Some sub-machines' PLC
        # ladders don't drive L108 — the cycle-complete signal is the
        # `sa_fetch_bit` itself (e.g. Semi-Auto on 192.168.10.152
        # only pulses M5700, never L108).  When count_bit stays 0 all
        # day but sa_fetch_bit fires N times, treat sa_fetch_bit
        # rising edges AS cycle completions for ct_log purposes.
        # `last_sa_edge_ts` is the previous sa_fetch_bit edge so we
        # can compute CT = delta between fetches.
        last_sa_edge_ts = None

        def _sa_reg_addr(base: str, offset: int) -> str:
            """Compute the i-th register address from a base.  "D5801"
            + offset=2 → "D5803".  Falls back to "{base}+offset" if the
            base doesn't match the expected letters+digits pattern."""
            import re as _re_addr
            m = _re_addr.match(r"([A-Za-z]+)(\d+)", base or "")
            if not m: return f"{base}+{offset}"
            return f"{m.group(1)}{int(m.group(2)) + offset}"

        print(f"{tag} starting poller @ {plc_ip}:{plc_port} bit={count_bit}", flush=True)
        if sa_active:
            print(f"{tag} Semi-Auto ENABLED: fetch={sa_fetch_bit} "
                  f"part={sa_part_addr},{sa_part_len} "
                  f"data={sa_data_addr},{sa_data_len} "
                  f"time={sa_time_addr or '(server)'}", flush=True)

        plc = None
        last_bit        = 0
        last_edge_ts    = None
        cycle_seq_today = 0
        last_date       = None
        last_shift      = None    # seq resets when shift flips (A → B → OT …)
        next_reconnect  = 0.0
        poll_count      = 0
        last_heartbeat  = time.time()

        def _connect() -> bool:
            nonlocal plc
            try:
                if plc:
                    try: plc.close()
                    except: pass
                # 2026-05-24 — Type3E for sub-machines.
                # Operator test confirmed: Lock Bar (192.168.10.181) +
                # Lower Rail (192.168.10.182) are Q-series CPUs that
                # silently return 0 on Type4E reads.  Type3E is the
                # baseline MELSEC frame — supported by BOTH Q-series
                # AND iQ-R, so universal.  Other sub-machines (Upper
                # Rail, Semi-Auto, Ball Guide) work fine on Type3E too
                # (no functional difference for the read calls we use).
                plc = pymcprotocol.Type3E()
                plc.connect(plc_ip, plc_port)
                plc.batchread_bitunits(headdevice=count_bit, readsize=1)
                print(f"{tag} connected (Type3E)", flush=True)
                return True
            except Exception as e:
                plc = None
                print(f"{tag} connect failed: {e}", flush=True)
                return False

        def _reload_cycle_seq(d, shift):
            """Max cycle_seq written for this sub-machine, date AND shift.
            Per-shift scoping keeps 'cycle #1' always meaning the first
            part of the currently-running shift, matching how the main
            PLC dashboard restarts counts at each shift boundary."""
            try:
                c = _db_conn()
                cur = c.cursor()
                cur.execute(
                    "SELECT COALESCE(MAX(cycle_seq), 0) "
                    "FROM mes_submachine_ct_log "
                    "WHERE sub_plc_id = %s "
                    "  AND record_date = %s "
                    "  AND shift_name  = %s",
                    (sub_id, d, shift),
                )
                seq = cur.fetchone()[0] or 0
                cur.close()
                c.close()
                return int(seq)
            except Exception as e:
                print(f"{tag} reload seq failed: {e}", flush=True)
                return 0

        while not stop_event.is_set():
            now = time.time()

            if plc is None:
                if now < next_reconnect:
                    time.sleep(0.5)
                    continue
                if not _connect():
                    next_reconnect = now + 5
                    continue

            today    = date.today()
            cur_shift = self._cur_shift or "UNKNOWN"
            if cur_shift.startswith("GAP"):
                cur_shift = "GAP"
            # Reset the seq counter on date OR shift transition so the
            # frontend shows "cycle #1" at the start of every shift.
            if last_date != today or last_shift != cur_shift:
                last_date       = today
                last_shift      = cur_shift
                cycle_seq_today = _reload_cycle_seq(today, cur_shift)
                last_edge_ts    = None
                last_bit        = 0
                print(f"{tag} day={today} shift={cur_shift} "
                      f"resume seq={cycle_seq_today}", flush=True)

            try:
                bits = plc.batchread_bitunits(headdevice=count_bit, readsize=1)
                cur_bit = 1 if int(bits[0]) else 0
                poll_count += 1
            except Exception as e:
                print(f"{tag} poll error: {e}", flush=True)
                try: plc.close()
                except: pass
                plc = None
                next_reconnect = time.time() + 3
                continue

            # 2026-05-23 — Pure radio-button NG: every L109 rise on a
            # sub-machine = 1 raw NG row in pulse_log.  No filter, no
            # wait.  Operator: "OK and NG dono as radio... one time on
            # button on".
            cur_ng_bit = 0
            if ng_bit_addr:
                try:
                    _ngb = plc.batchread_bitunits(headdevice=ng_bit_addr, readsize=1)
                    cur_ng_bit = 1 if int(_ngb[0]) else 0
                except Exception:
                    cur_ng_bit = 0
            if cur_ng_bit == 1 and last_ng_bit == 0:
                _ng_now_dt = datetime.now()
                # 2026-05-27 — REVERTED any-pulse model for sub-machines
                # at operator request ("pehle wala bit-to-bit rakh, sub
                # machines ka bigad gaya tha 100s+ ct dikh raha tha").
                # Back to NG-to-NG only: CT = gap from previous NG event
                # on this sub-machine.  First NG of shift falls back to
                # 0 instead of NULL so chart can still plot it.
                _ng_ct_delta = ((_ng_now_dt - last_sub_ng_ts).total_seconds()
                                if last_sub_ng_ts else 0.0)
                _ng_pc = (self._cur_part_code or "").strip().rstrip(":") or None
                # 2026-05-26 — Garbage part_code guard (same as main).
                # Sub-machines inherit the main collector's _cur_part_code;
                # when the main side reads `\x10` from D5004, sub NG rows
                # also pick up junk.  Skip the DB write rather than
                # poison the audit + legacy ct_log tables.
                _ng_pc_is_garbage = False
                if _ng_pc:
                    if _ng_pc.upper() == "ERROR" or any(
                            (ord(_c) < 0x20 or ord(_c) == 0x7F) for _c in _ng_pc):
                        _ng_pc_is_garbage = True
                if _ng_pc_is_garbage:
                    print(f"{tag} [NG-DB-SKIP] L109 rise but part_code "
                          f"garbage ({_ng_pc!r}) — skipping write.",
                          flush=True)
                    last_ng_bit = cur_ng_bit
                    last_bit    = cur_bit
                    time.sleep(0.03)
                    continue
                # Write to new per-machine table
                try:
                    self._write_machine_log(
                        machine_id   = sub_id,
                        bit_type     = "NG",
                        bit_address  = ng_bit_addr,
                        ts           = _ng_now_dt,
                        ct_seconds   = _ng_ct_delta,
                        part_code    = _ng_pc,
                    )
                except Exception:
                    pass
                # Also write to LEGACY mes_submachine_ct_log with is_ng=true
                # so the existing dashboard / wallboard charts pick it up.
                # 2026-05-24 — ts_start = ts_end - 30s so the video clip
                # endpoint serves a viewable 30-sec window (was previously
                # = last_sub_ng_ts which produced 20+ min windows that
                # the camera CMS rejected with 416 Range Not Satisfiable).
                try:
                    _ts_start_ng = _ng_now_dt - timedelta(seconds=30)
                    _shift_ng = self._cur_shift or "UNKNOWN"
                    if _shift_ng.startswith("GAP"): _shift_ng = "GAP"
                    _c2 = _db_conn()
                    _cur2 = _c2.cursor()
                    _cur2.execute("""
                        INSERT INTO mes_submachine_ct_log
                            (sub_plc_id, line_id, record_date, shift_name,
                             cycle_seq, ts_start, ts_end, ct_seconds,
                             model_number, model_name, part_code, is_ng)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        sub_id, line_id, _ng_now_dt.date(), _shift_ng,
                        cycle_seq_today + 1,
                        _ts_start_ng, _ng_now_dt, round(_ng_ct_delta, 3),
                        self._cur_model, self._cur_model_name,
                        _ng_pc, True,
                    ))
                    _c2.commit()
                    _cur2.close(); _c2.close()
                    cycle_seq_today += 1
                    print(f"{tag} NG #{cycle_seq_today} ct={_ng_ct_delta:.2f}s "
                          f"pc={_ng_pc}", flush=True)
                except Exception as _ne:
                    print(f"{tag} NG legacy-insert failed: {_ne}", flush=True)
                last_sub_ng_ts        = _ng_now_dt
                # 2026-05-27 — Anchor still tracked for future use, but
                # NG ct above no longer reads from it.
                last_any_pulse_sub_dt = _ng_now_dt
            last_ng_bit = cur_ng_bit

            # Heartbeat every 30 s so we can see if the poller is healthy
            # even when M100 has been idle
            if time.time() - last_heartbeat >= 30:
                print(f"{tag} heartbeat polls={poll_count} bit={cur_bit} "
                      f"seq_today={cycle_seq_today}", flush=True)
                last_heartbeat = time.time()

            if cur_bit == 1 and last_bit == 0:
                now_dt = datetime.now()
                now_ts = now_dt.timestamp()

                # 2026-05-24 — Per-machine L6 table (replaces pulse_log).
                # Every count_bit rise on this sub-machine writes one row
                # to the machine's own mes_l6_* table.  Zero gating.
                # 2026-05-27 — REVERTED to OK-to-OK CT.  Any-pulse
                # broke sub-machine charts (showed 100s+ for the first
                # pulse after long idle).  Each sub-machine independent
                # of NG events now.
                try:
                    _ct_delta = ((now_dt - last_sub_ok_ts).total_seconds()
                                 if last_sub_ok_ts else 0.0)
                    self._write_machine_log(
                        machine_id   = sub_id,
                        bit_type     = "OK",
                        bit_address  = count_bit,
                        ts           = now_dt,
                        ct_seconds   = _ct_delta,
                        part_code    = (self._cur_part_code or "").strip().rstrip(":") or None,
                    )
                    last_sub_ok_ts        = now_dt
                    last_any_pulse_sub_dt = now_dt  # still tracked but unused
                except Exception:
                    pass


                # 2026-05-27 — REVERTED to OK-to-OK for ct_log raw_ct.
                # Sub-machine charts use OK-only model so post-NG OK
                # doesn't show artificial spike from rare NG events.
                if last_edge_ts is None:
                    last_edge_ts = now_ts
                else:
                    raw_ct = now_ts - last_edge_ts
                    # 2026-05-16 — net out any break time that fell
                    # inside [last_edge, now].
                    ts_start_dt = datetime.fromtimestamp(last_edge_ts)
                    brk_sec     = self._break_overlap_seconds(ts_start_dt, now_dt)
                    ct = max(0.0, raw_ct - brk_sec)
                    if brk_sec > 0.5:
                        print(f"{tag} cycle spanned {brk_sec:.0f}s of "
                              f"break time — raw={raw_ct:.1f}s, "
                              f"net={ct:.1f}s", flush=True)
                    # 2026-05-24 — chatter-guard on RAW CT, not net.
                    # Earlier bug: if a cycle's window happened to
                    # overlap a scheduled break entirely, net=0 and the
                    # cycle was CHATTER-DROPPED even though it was a
                    # real machine cycle (operator working through
                    # break).  Symptom: Lock Bar today produced 9 OK
                    # cycles 12:13–12:20 but mes_submachine_ct_log
                    # showed 0 because every cycle was lunch-overlap.
                    # New rule: chatter is judged on wall-clock raw_ct
                    # (a true ladder double-pulse is sub-second
                    # regardless of break alignment).  Store the
                    # break-netted ct so the chart still doesn't
                    # spike during real breaks.
                    if raw_ct < _sub_min_ct:
                        print(f"{tag} CHATTER-DROP raw_ct={raw_ct:.2f}s "
                              f"< {_sub_min_ct:.2f}s — true ladder "
                              f"double-pulse.", flush=True)
                        last_edge_ts = now_ts
                    elif raw_ct >= _sub_min_ct:
                        # Commit FIRST, then bump the counter — otherwise a
                        # failed insert leaves a gap (row #N missing but seq
                        # advanced to N+1). Keeps "cycles count" and
                        # "last cycle_seq" in the UI header identical.
                        candidate_seq = cycle_seq_today + 1
                        ts_start = ts_start_dt
                        ts_end   = now_dt
                        shift    = self._cur_shift or "UNKNOWN"
                        if shift.startswith("GAP"):
                            shift = "GAP"
                        try:
                            c2 = _db_conn()
                            cur2 = c2.cursor()
                            # part_code is intentionally NULL for sub-machines:
                            # the code scanner is only on the FINAL/main machine.
                            # Sub-machine cycles are identified by (sub_plc_id,
                            # cycle_seq, ts_start, ts_end) only — model context
                            # is still recorded for filtering, but the part_code
                            # field is reserved for the part actually scanned at
                            # the line's final station.
                            cur2.execute("""
                                INSERT INTO mes_submachine_ct_log
                                    (sub_plc_id, line_id, record_date,
                                     shift_name, cycle_seq,
                                     ts_start, ts_end, ct_seconds,
                                     model_number, model_name, part_code)
                                VALUES
                                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                            """, (
                                sub_id, line_id, today,
                                shift, candidate_seq,
                                ts_start, ts_end, round(ct, 3),
                                self._cur_model, self._cur_model_name,
                            ))
                            c2.commit()
                            cur2.close()
                            c2.close()
                            cycle_seq_today = candidate_seq
                            print(f"{tag} #{cycle_seq_today} "
                                  f"CT={ct:.2f}s shift={shift}", flush=True)
                        except Exception as e:
                            print(f"{tag} insert failed (seq held at "
                                  f"{cycle_seq_today}): {e}", flush=True)
                        last_edge_ts = now_ts

            # ── Semi-Auto data capture ──────────────────────────────
            # Independent rising-edge tracker on sa_fetch_bit (e.g.
            # M5700).  On each 0→1, do three parallel reads (part code,
            # data block, optional PLC time), apply scaling, INSERT one
            # row into mes_submachine_data_log.  Failures here never
            # affect the cycle-count loop above — we just log and move on.
            if sa_active and plc is not None:
                try:
                    sa_bits = plc.batchread_bitunits(headdevice=sa_fetch_bit, readsize=1)
                    sa_cur  = 1 if int(sa_bits[0]) else 0
                except Exception as e:
                    sa_cur = 0
                    # Throttle so a dead bit doesn't flood the log
                    if poll_count % 100 == 0:
                        print(f"{tag} SA fetch_bit read err: {e}", flush=True)
                if sa_cur == 1 and last_sa_bit == 0:
                    try:
                        # Part code (byte-reversed ASCII, low|high per register)
                        part_code = ""
                        if sa_part_addr and sa_part_len > 0:
                            regs = plc.batchread_wordunits(
                                headdevice=sa_part_addr, readsize=sa_part_len)
                            chars = []
                            for r in regs:
                                hi = r & 0xFF
                                lo = (r >> 8) & 0xFF
                                if hi: chars.append(chr(hi))
                                if lo: chars.append(chr(lo))
                            part_code = ("".join(chars)
                                            .strip().strip("\x00").rstrip(":"))

                        # Data block (raw int registers)
                        raw_data = list(plc.batchread_wordunits(
                            headdevice=sa_data_addr, readsize=sa_data_len))

                        # PLC time (optional — 6 registers: yr, mo, dy, hr, min, sec)
                        ts_plc = None
                        if sa_time_addr and sa_time_len >= 6:
                            try:
                                t = plc.batchread_wordunits(
                                    headdevice=sa_time_addr, readsize=sa_time_len)
                                yr, mo, dy, hr, mn, sc = (
                                    int(t[0]), int(t[1]), int(t[2]),
                                    int(t[3]), int(t[4]), int(t[5]),
                                )
                                if 0 <= yr < 100: yr += 2000
                                ts_plc = datetime(yr, mo, dy, hr, mn, sc)
                            except Exception:
                                ts_plc = None

                        # Decorate each value with its label + scaled form
                        data_values = []
                        for i, raw in enumerate(raw_data):
                            try: raw_i = int(raw)
                            except Exception: raw_i = 0
                            label = sa_names[i]  if i < len(sa_names)  else f"data_{i+1}"
                            try: scale = float(sa_scales[i]) if i < len(sa_scales) else 1.0
                            except Exception: scale = 1.0
                            try: scaled = round(raw_i * scale, 4)
                            except Exception: scaled = None
                            data_values.append({
                                "register": _sa_reg_addr(sa_data_addr, i),
                                "label":    str(label),
                                "raw":      raw_i,
                                "scaled":   scaled,
                            })

                        c3 = _db_conn()
                        cur3 = c3.cursor()
                        cur3.execute("""
                            INSERT INTO mes_submachine_data_log
                                (sub_plc_id, line_id, record_date, shift_name,
                                 cycle_seq, ts_plc, ts_server, part_code,
                                 model_number, model_name, data_values)
                            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s)
                        """, (sub_id, line_id, today, cur_shift,
                              cycle_seq_today, ts_plc, part_code or None,
                              self._cur_model, self._cur_model_name,
                              psycopg2.extras.Json(data_values)))
                        c3.commit()
                        cur3.close(); c3.close()
                        print(f"{tag} SA #{cycle_seq_today} "
                              f"part={part_code!r} vals={len(data_values)} "
                              f"ts_plc={ts_plc.isoformat() if ts_plc else '(server)'}",
                              flush=True)
                    except Exception as e:
                        print(f"{tag} SA capture failed: {e}", flush=True)

                    # 2026-05-22 — SA-AS-CYCLE FALLBACK.
                    # If count_bit (L108) hasn't fired today (cycle_seq_today
                    # still 0 — meaning L108 is genuinely dead on this PLC),
                    # treat THIS sa_fetch_bit rising edge as the cycle
                    # complete signal and write a ct_log row too.
                    # Semi-Auto's PLC 192.168.10.152 only drives M5700, not
                    # L108 — without this fallback the sub-machine page
                    # showed "Waiting for cycle data" every morning until
                    # an L108 edge happened to slip through (rare/never).
                    if cycle_seq_today == 0:
                        now_ts_sa = time.time()
                        ct_sa = (now_ts_sa - last_sa_edge_ts) if last_sa_edge_ts else 0.0
                        # 2026-05-23 — same chatter-guard as count_bit branch.
                        if last_sa_edge_ts is not None and ct_sa >= _sub_min_ct:
                            # Net out break overlaps (mirrors count_bit branch)
                            ts_start_sa = datetime.fromtimestamp(last_sa_edge_ts)
                            ts_end_sa   = datetime.fromtimestamp(now_ts_sa)
                            try:
                                brk = self._break_overlap_seconds(ts_start_sa, ts_end_sa)
                            except Exception:
                                brk = 0.0
                            ct_sa_net = max(0.0, ct_sa - brk)
                            candidate_sa_seq = cycle_seq_today + 1
                            shift_sa = self._cur_shift or "UNKNOWN"
                            if shift_sa.startswith("GAP"):
                                shift_sa = "GAP"
                            try:
                                c_sa = _db_conn()
                                cur_sa = c_sa.cursor()
                                cur_sa.execute("""
                                    INSERT INTO mes_submachine_ct_log
                                        (sub_plc_id, line_id, record_date,
                                         shift_name, cycle_seq,
                                         ts_start, ts_end, ct_seconds,
                                         model_number, model_name, part_code)
                                    VALUES
                                        (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                                """, (
                                    sub_id, line_id, today,
                                    shift_sa, candidate_sa_seq,
                                    ts_start_sa, ts_end_sa, round(ct_sa_net, 3),
                                    self._cur_model, self._cur_model_name,
                                ))
                                c_sa.commit()
                                cur_sa.close(); c_sa.close()
                                cycle_seq_today = candidate_sa_seq
                                print(f"{tag} SA-as-cycle #{cycle_seq_today} "
                                      f"CT={ct_sa_net:.2f}s (fallback — L108 "
                                      f"silent, using sa_fetch_bit as cycle)",
                                      flush=True)
                            except Exception as e_sa:
                                print(f"{tag} SA-as-cycle insert failed: "
                                      f"{e_sa}", flush=True)
                        last_sa_edge_ts = now_ts_sa
                last_sa_bit = sa_cur

            last_bit = cur_bit
            time.sleep(0.1)   # 100 ms — catch brief pulses reliably

        if plc:
            try: plc.close()
            except: pass
        print(f"{tag} poller stopped", flush=True)

    # ----------------------------------------------------------
    # MAIN RUN LOOP
    # ----------------------------------------------------------

    def run(self):
        print(f"\n{'='*60}")
        print(f"  {self.cfg['line_name']} — Collector Engine")
        print(f"{'='*60}")
        print(f"  PLC   : {self.cfg['plc_ip']}:{self.cfg['plc_port']}")
        print(f"  Table : {self.cfg['table_name']}")
        print(f"  Ctrl+C to stop")
        print(f"{'='*60}\n")

        self._connect_plc()
        self._connect_db()
        self._last_db_check = time.time()

        # Launch sub-machine pollers — one thread per sub from initial DB snapshot
        for _sub in self.submachines:
            self._spawn_sub_thread(_sub)

        # Background watcher: picks up newly-added / removed / edited
        # sub-machines from mes_plc_configs without needing a restart.
        threading.Thread(
            target=self._reload_subs_loop,
            daemon=True,
            name="sub-reloader",
        ).start()
        print(
            "[ENGINE] Sub-machine reloader started — "
            "AdminPanel adds/edits/deletes go live within ~30 s without restart",
            flush=True,
        )

        # ── Poka-yoke / sensor check thread (2026-05-12 fix) ─────────
        # Each PY-check pass does 20-40 sequential PLC bit reads
        # (~30-50 ms each = 1-2 s total).  When this ran inline in the
        # main loop, the 30 ms pulse-poll cadence was killed for that
        # entire window → L108/L109 rising edges missed → "OK count
        # 222 vs machine counter 650" symptom.
        #
        # Solution: run PY checks in their own thread with its own
        # PLC connection (Mitsubishi Q03/Q06 supports 4-8 concurrent
        # MC4E TCP clients — we use 1 main + this 1 PY + sub-machines
        # are on different IPs, so ≤3 connections on the main PLC).
        # If the dedicated connection fails to open, the thread silently
        # exits and we lose nothing — bypass detection just won't run.
        threading.Thread(
            target=self._py_check_loop,
            daemon=True,
            name="py-check",
        ).start()
        print(
            "[ENGINE] PY/sensor check thread started — runs independently "
            "of main pulse poll loop, can no longer starve L108/L109 reads",
            flush=True,
        )

        # One-time migration: add current_shift_row_id to mes_lines if missing
        if self._db_ok:
            try:
                _mc = self._db.cursor()
                _mc.execute(
                    "ALTER TABLE mes_lines ADD COLUMN IF NOT EXISTS current_shift_row_id INTEGER"
                )
                self._db.commit()
                _mc.close()
            except Exception:
                self._safe_rollback()

        while True:
            try:
                now = time.time()

                # Reconnect if lost — aggressive 2-sec retry instead
                # of the old "1 second window every 30 sec" pattern
                # which gave a 30-sec dead window on every PLC blip.
                if not self._plc_ok:
                    if now - getattr(self, "_last_reconnect_try", 0) >= 2:
                        self._last_reconnect_try = now
                        if self._connect_plc():
                            died = getattr(self, "_plc_died_at", now)
                            print(f"[PLC] Reconnected after {now - died:.1f}s")
                if not self._db_ok and now % 10 < 1:
                    self._ensure_db_connection()

                # ── Auto-clear expired OT ──
                # If OT is active but the 1-hour window has passed, clear it
                # so dashboard goes back to normal and shift transitions to GAP.
                _ot = self._check_ot_active()
                if _ot and not self._is_in_ot_window(_ot):
                    try:
                        _oc = self._db.cursor()
                        _oc.execute(
                            "UPDATE mes_lines SET ot_active_shift = NULL WHERE id = %s",
                            (self.cfg["line_id"],)
                        )
                        self._db.commit()
                        _oc.close()
                        print(f"[OT] Auto-cleared expired OT for shift {_ot}")
                    except Exception:
                        self._safe_rollback()

                # Shift detection
                shift_name, record_date = self._get_current_shift()

                # ── Shift change ──
                if shift_name and shift_name != self._cur_shift:
                    print(f"\n[SHIFT] {self._cur_shift or 'None'} -> {shift_name}")
                    # Don't mark shift completed if OT is about to bring it back.
                    # Only mark completed if the OLD shift is NOT the OT active shift.
                    ot_active = self._check_ot_active()
                    should_complete = (ot_active != self._cur_shift) if self._cur_shift else True
                    if self._shift_id and self._db_ok and should_complete:
                        try:
                            cur = self._db.cursor()
                            cur.execute(
                                f"UPDATE {self.cfg['table_name']} "
                                f"SET is_shift_completed=true WHERE id=%s",
                                (self._shift_id,))
                            self._db.commit()
                            cur.close()
                        except Exception:
                            self._safe_rollback()

                    self._cur_shift     = shift_name
                    self._last_ok_state = 0
                    self._last_ng_state = 0

                    if self._db_ok:
                        self._shift_id = self._get_or_create_shift(
                            shift_name, record_date)
                        if self._shift_id:
                            try:
                                _rc = self._db.cursor()
                                _rc.execute(
                                    "UPDATE mes_lines SET current_shift_row_id=%s WHERE id=%s",
                                    (self._shift_id, self.cfg["line_id"]),
                                )
                                self._db.commit()
                                _rc.close()
                            except Exception:
                                self._safe_rollback()

                # ── DB came back mid-shift — register the shift now ──
                if (shift_name and self._db_ok and not self._shift_id):
                    print(f"[SHIFT] DB reconnected mid-shift — registering {shift_name}")
                    self._shift_id = self._get_or_create_shift(
                        shift_name, record_date)
                    if self._shift_id:
                        try:
                            _rc = self._db.cursor()
                            _rc.execute(
                                "UPDATE mes_lines SET current_shift_row_id=%s WHERE id=%s",
                                (self._shift_id, self.cfg["line_id"]),
                            )
                            self._db.commit()
                            _rc.close()
                        except Exception:
                            self._safe_rollback()

                if not shift_name:
                    time.sleep(5)
                    continue

                # Read PLC
                plc = self._read_plc()

                # Model change
                m = plc["model"]
                if m != self._cur_model and m > 0:
                    self._cur_model      = m
                    self._cur_model_name = self.cfg["models"].get(m, f"Model#{m}")
                    print(f"[MODEL] -> {self._cur_model_name}")

                # Status & loss
                self._update_status(plc["status"])
                self.poka.check_override(plc["status"], self._cur_shift or "")

                # Per-process bit-pulse edge counting (fast poll, ~30 ms).
                # Catches every L108-style rising edge so the 60-s window
                # logged to mes_machine_process_log accumulates the full
                # cycle count.  Word-type processes are skipped here —
                # they're sampled once-per-60s by _sample_machine_processes.
                self._poll_machine_process_pulses()

                # Count pulses
                new_ok, new_ng = self._update_counts(
                    plc["ok_bit"], plc["ng_bit"])

                # 2026-05-23 — SWITCH MODEL (Option C).
                # Each L108 rise = 1 OK row.  Each L109 rise (passing the
                # ladder-echo filter in _update_counts) = 1 NG row.  They
                # are INDEPENDENT — both may fire in the same poll, in
                # which case both rows get written.  Cycles never merge.
                _now = datetime.now()

                # 2026-05-26 — SWITCH MODEL ct_log writes.
                # Each L108 rise → its own OK row.  Each L109-dwell-pass
                # → its own NG row.  Both may fire in the same poll, in
                # which case BOTH rows get written.  Operator: "first
                # pulse of any one bit = one count, not continuous
                # monitor".
                if new_ok > 0:
                    # 2026-05-27 — ct_log OK write MOVED to the raw
                    # L108 rising-edge handler inside _update_counts
                    # (same as NG).  This block now only feeds the
                    # avg/min/max stats panel via `self.ct.on_pulse()`
                    # and clears the chart stash flag.  Counter still
                    # increments here for clean OEE math.
                    self.ct.on_pulse(time.time())
                    ct_s = self.ct.stats()
                    if ct_s["list"]:
                        self.poka.check_cycle_fast(
                            ct_s["list"][-1], self._cur_shift or "")
                    self._raw_ok_already_logged_this_press = False
                    self._last_ct_for_chart_ok = None
                # 2026-05-27 — NG ct_log write MOVED to the raw L109
                # rising-edge handler inside _update_counts.  Skipping
                # the duplicate here that used to fire on counter
                # increment.  The raw handler already appended the row
                # before this point in the same poll iteration.
                if new_ng > 0:
                    # Reset flag so next press starts fresh.  Counter
                    # bumped → counter telemetry, but chart/audit rows
                    # already in flight.
                    self._raw_ng_already_logged_this_press = False
                    self._last_ct_for_chart_ng = None

                if new_ok > 0:
                    self.poka.on_ok_pulse(
                        plc.get("sensor_ok"), self._cur_shift or "")
                    self.poka.on_ok_clears_ng()

                if new_ng > 0:
                    self.poka.on_ng_pulse(self._cur_shift or "")

                # PY / sensor checks no longer run inline — moved to a
                # dedicated thread (_py_check_loop) on collector startup
                # so the main pulse-poll loop stays at full 33 Hz.  The
                # thread holds its own PLC connection, never blocks here.

                # Speed loss check
                if now - self._last_speed_chk >= self.SPEED_CHECK_INTERVAL:
                    in_break, _ = self._is_break()
                    if not in_break and self._cur_status == 1:
                        added = self.ct.check_continuous(now)
                        if added > 0:
                            self._loss["speed"] += added
                    self._last_speed_chk = now

                # Break log + periodic break-config refresh.
                #
                # Admins change zone breaks via Production Admin Panel
                # WHILE the collector is running.  Without this reload
                # the new break window is invisible until tomorrow.
                if now - self._last_break_reload > 60:
                    self._reload_breaks_from_db()
                    self._last_break_reload = now

                in_break, bname = self._is_break()
                if in_break and now - self._last_break_log > 60:
                    print(f"[BREAK] {bname}")
                    self._last_break_log = now

                # Hourly update
                self._update_hourly(new_ok, new_ng)

                # Backfill plans every 30 seconds
                if now - self._last_plan_calc > 30:
                    if (self._shift_id and self._cur_shift
                            and not self._cur_shift.startswith("GAP")):
                        self._backfill_past_slots(self._cur_shift)
                        self._refresh_all_slot_plans()
                    self._last_plan_calc = now

                # Machine-process config reload every 30 s (admin edits go live)
                if now - self._last_process_reload > 30:
                    self._reload_machine_processes()
                    self._last_process_reload = now

                # Machine-process PLC sample + log every 60 s — drives
                # the Process Graphs page's bars/lines.
                if now - self._last_process_sample > 60:
                    self._sample_machine_processes()
                    self._last_process_sample = now

                # Dashboard DB write every 2 seconds
                if now - self._last_db_write > self.DB_UPDATE_INTERVAL:
                    if self._db_ok and self._shift_id:
                        self._write_dashboard()
                    self._last_db_write = now

                # Console display every second
                if now - self._last_display > 1:
                    working_seconds = self._working_seconds()
                    _disp_scfg   = self.cfg["shifts"].get(self._cur_shift, {})
                    _disp_plan   = _disp_scfg.get("total_plan", 0) if not (self._cur_shift or "").startswith("GAP") else 0
                    planned      = min(_disp_plan, int(working_seconds / self.cfg["ideal_ct"])) if _disp_plan > 0 else 0
                    oee          = self._oee()
                    cts          = self.ct.stats()
                    total_l      = sum(self._loss.values())

                    def fmt(s):
                        s = int(s)
                        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

                    in_break, _ = self._is_break()
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] "
                        f"Shift:{self._cur_shift or '---':6s} | "
                        f"{'Break' if in_break else '':8s} | "
                        f"{self._cur_status_name[:10]:10s} | "
                        f"OK:{self.ok_shift:4d} | "
                        f"Plan:{planned:4d} | "
                        f"OEE:{oee['overall']:5.1f}% | "
                        f"Loss:{fmt(total_l)} | "
                        f"CT:{cts['avg']:5.2f}s"
                    )
                    self._last_display = now

                # 2026-05-23 — 10 ms (100 Hz) poll.  Operator: "poll mtt
                # krr listen krr rise ke liye" — MC4E is a request/response
                # protocol so true event subscription isn't available, but
                # tight polling at 100 Hz with retry-once on each read is
                # effectively continuous listening.  Each loop reads four
                # registers (status / ok / ng / model) totaling ~20-40 ms
                # over LAN, so the 10 ms target sleeps to ~0 ms most of
                # the time — the loop body itself is the throttle.  This
                # guarantees we sample more than once during the PLC's
                # multi-hundred-ms L108 HIGH window even under packet
                # retransmits, so no rising edge is ever silently lost.
                time.sleep(0.01)

            except KeyboardInterrupt:
                print("\nStopped by user")
                break
            except Exception as e:
                print(f"\n[ERROR] {type(e).__name__}: {e}")
                time.sleep(2)

        if self._plc:
            try: self._plc.close()
            except: pass
        if self._db:
            try: self._db.close()
            except: pass
        try:
            if hasattr(self, "_lock") and self._lock:
                self._lock.release()
        except Exception:
            pass
        print("Collector stopped")
