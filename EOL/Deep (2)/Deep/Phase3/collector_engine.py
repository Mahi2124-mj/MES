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

import pymcprotocol
import psycopg2
import psycopg2.extras

# DB connection — single source from database.py with env-var override.
import os as _os_db
try:
    from database import DB_CONFIG as _BASE_DB_CONFIG
    DB_CONFIG = {**_BASE_DB_CONFIG, "connect_timeout": 5}
except Exception:
    DB_CONFIG = {
        "host":     _os_db.getenv("DB_HOST",     "192.168.10.210"),
        "port":     int(_os_db.getenv("DB_PORT", "5432") or 5432),
        "database": _os_db.getenv("DB_NAME",     "energydb"),
        "user":     _os_db.getenv("DB_USER",     "postgres"),
        "password": _os_db.getenv("DB_PASS",     "tbdi@123"),
        "connect_timeout": 5,
    }

import os as _os
BACKEND_URL = (
    _os.getenv("BACKEND_URL")
    or f"http://{_os.getenv('BACKEND_HOST','127.0.0.1')}:{_os.getenv('BACKEND_PORT','8080')}"
)


def _db_conn():
    return psycopg2.connect(**DB_CONFIG)


# ════════════════════════════════════════════════════════════════════
# CROSS-PC SINGLETON LOCK (see Phase2/collector_engine.py for design)
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
                if hostname == self.hostname and pid == self.pid:
                    pass
                elif age_sec < _HEARTBEAT_STALE_AFTER_SEC:
                    raise RuntimeError(
                        f"Another collector is already running for "
                        f"line_id={self.line_id} on host '{hostname}' "
                        f"(PID {pid}, last heartbeat {age_sec:.0f}s ago)."
                    )
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
        print(f"[LOCK] ✓ Acquired singleton lock for line_id={self.line_id}")
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

def load_submachines(main_plc_id: int) -> list:
    """Return every sub-machine whose parent_plc_id matches the main PLC.
    Each dict has the fields the sub-poller needs: id, plc_ip, plc_port,
    count_bit (stored in ok_bit_address), ideal_ct, machine_name.
    Returns [] if no sub-machines configured — safe for legacy lines.
    """
    if not main_plc_id:
        return []
    try:
        conn = _db_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, plc_ip, plc_port,
                   NULLIF(TRIM(ok_bit_address), '') AS count_bit,
                   ideal_cycle_time                 AS ideal_ct,
                   machine_name
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
        # No PLC writes ever.  Sample at ~1 Hz; >900 s no toggle → 'stuck'
        # status + 1 SENSOR_HEALTH email.
        self._x_state:             dict  = {}
        self._x_track_interval:    float = 1.0
        self._x_track_last:        float = 0.0
        self._publish_interval:    float = 10.0
        self._publish_last:        float = 0.0
        self._stuck_threshold_sec: int   = 900
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
            try:
                cur.execute(
                    "ALTER TABLE mes_py_master "
                    "ADD COLUMN IF NOT EXISTS sensing_bits VARCHAR(100)"
                )
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
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
        desirable output for the active model. Fires SENSOR_BYPASS on mismatch."""
        if not plc or not self._py_configs or not current_model_bit:
            return

        import re as _re
        BIT_PREFIXES  = ("X", "Y", "M", "L", "F", "B", "T", "C", "S")
        REG_RE = _re.compile(
            r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+",
            _re.IGNORECASE,
        )

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
                try:
                    if is_bit:
                        vals = plc.batchread_bitunits(headdevice=reg, readsize=1)
                    else:
                        vals = plc.batchread_wordunits(headdevice=reg, readsize=1)
                    code = int(vals[0] or 0)
                except Exception as e:
                    print(f"[POKA-BYPASS] PLC read {reg} failed: {e}")
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
                    match = (code in expected)
                    human_actual   = self._decode_code(code, reg_cnt)
                    human_expected = " | ".join(
                        self._decode_code(c, reg_cnt) for c in sorted(expected))

                if match:
                    # If we had an active fault for this register, tell the
                    # backend to acknowledge the old events so the dashboard
                    # drops the red alert immediately (no 8h wait).
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

                if self._py_bypass_state.get(key) == code:
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

    # ── Sensor Health — passive X-bit monitoring (READ-ONLY, no PLC writes) ─
    #
    # Sample each sensing X-bit ~1 Hz; in-memory `last_toggle_ts` per bit.
    # No toggle for >900 s → status='stuck' + 1 SENSOR_HEALTH email.
    # Natural toggle clears stuck + email guard.  Collector NEVER writes PLC.

    def _x_state_default(self, val: int, ts: float) -> dict:
        return {
            "value":          val,
            "last_toggle_ts": ts,
            "status":         "alive",   # alive | stuck
            "stuck_emailed":  False,
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
        """Passive X-bit health monitor — READ ONLY.  Always publishes the
        snapshot on the 10-second tick — even if the PLC blipped or
        configs aren't loaded yet — so the UI's 'Last snapshot' clock
        keeps moving.  No PLC writes anywhere."""
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

        py_by_xbit: dict = {}
        for py in self._py_configs:
            for tok in REG_RE.findall((py.get("sensing_bits") or "").upper()):
                py_by_xbit.setdefault(tok, py)

        # No configs → nothing to monitor; don't blank the cache.
        if not py_by_xbit:
            return

        # PLC blip → keep UI moving but don't lose state.
        if plc is None:
            if self._x_state and (now - self._publish_last >= self._publish_interval):
                self._publish_health_snapshot(now, py_by_xbit)
                self._publish_last = now
            return

        for bit, py in py_by_xbit.items():
            prefix = bit[0].upper()
            try:
                if prefix in BIT_PREFIXES:
                    vals = plc.batchread_bitunits(headdevice=bit, readsize=1)
                else:
                    vals = plc.batchread_wordunits(headdevice=bit, readsize=1)
                val = 1 if int(vals[0] or 0) else 0
            except Exception:
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
                if state["status"] == "stuck":
                    state["status"] = "alive"

            # Stuck > threshold → flag + 1 email.  Purely passive.
            elapsed = now - state["last_toggle_ts"]
            if state["status"] == "alive" and elapsed > self._stuck_threshold_sec:
                state["status"] = "stuck"
                if not state["stuck_emailed"]:
                    self._fire_health_event(
                        bit,
                        f"{bit}:no-toggle for {int(elapsed)}s "
                        f"(>{self._stuck_threshold_sec}s threshold)",
                        py,
                    )
                    state["stuck_emailed"] = True

        if now - self._publish_last >= self._publish_interval:
            self._publish_health_snapshot(now, py_by_xbit)
            self._publish_last = now

    def _publish_health_snapshot(self, now: float, py_by_xbit: dict):
        from datetime import datetime as _dt
        swept_at = _dt.now().isoformat(timespec="seconds")
        entries = []
        for x_bit, state in self._x_state.items():
            py             = py_by_xbit.get(x_bit, {}) or {}
            last_iso       = _dt.fromtimestamp(state["last_toggle_ts"]).isoformat(timespec="seconds")
            entries.append({
                "bit":                 x_bit,
                "x_bit":               x_bit,
                "d_bit":               py.get("register_addr"),
                "current_value":       state["value"],
                "last_toggle_at":      last_iso,
                "last_toggle_ago_sec": round(now - state["last_toggle_ts"], 1),
                "status":              state["status"],
                "py_id":               py.get("py_id"),
                "py_no":               py.get("py_no"),
                "py_name":             py.get("description"),
                "sensing_bits":        py.get("sensing_bits"),
            })
        try:
            requests.post(
                f"{BACKEND_URL}/api/poka-yoke/sensor-sweep/update",
                json={"line_id": self.line_id, "entries": entries,
                      "swept_at": swept_at},
                timeout=3,
            )
        except Exception as e:
            print(f"[POKA-SWEEP] Publish failed: {e}")

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

    def __init__(self, init_cfg: dict):
        line_id = init_cfg["line_id"]
        # Cross-PC singleton lock — refuses to start if another
        # collector for this line is alive elsewhere on the LAN.
        self._lock = CollectorSingletonLock(line_id)
        self._lock.acquire()
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
        self._pulse_gap     = self.cfg["pulse_gap"]

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
        # 2026-05-13 — admins change zone breaks mid-shift via Production
        # Admin Panel.  Reload from DB every 60s so the new window fires
        # without restarting the collector.  See _reload_breaks_from_db.
        self._last_break_reload = 0.0

        # ── CT log buffer ─────────────────────────────────────────
        self._ct_pending_log: list = []
        self._ct_log_table_ready   = False
        # status=-2 means "not yet successfully read"; treated as no-op
        # by _update_status so dashboard doesn't briefly flash IDLE on
        # cold boot before the first PLC read lands.
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
        self._sub_threads     = []
        self._sub_stop        = threading.Event()
        # Per-sub-machine worker registry — see _reload_subs_loop()
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
            # round() absorbs float drift so plan hits exact target at shift end
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
            # round() absorbs float drift so plan hits exact target at shift end
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
        """Resilient PLC read — single hiccup no longer kills the
        connection for 30 s. Per-register try/except + 2-cycle
        all-fail check before flipping _plc_ok.

        Spam guard: when `_plc_ok` is already False (connection dead),
        we DON'T retry the dead socket on every 30-ms tick — the
        underlying TCP socket has been forcibly closed (WinError 10054).
        Re-issuing reads against it just spams the log.  Print throttle:
        errors logged ONCE per dead-period (live→dead transition)."""
        if self._plc is None:
            return self._last_plc_data

        # Spam guard: socket is dead, don't even try.
        if not self._plc_ok:
            return self._last_plc_data

        data = dict(self._last_plc_data)
        failures = 0
        first_err = None

        def _safe_word(addr, key, keep_last):
            nonlocal failures, first_err
            try:
                v = self._plc.batchread_wordunits(headdevice=addr, readsize=1)
                if v:        data[key] = int(v[0])
                elif not keep_last: data[key] = 0
            except Exception as e:
                failures += 1
                if first_err is None:
                    first_err = (key, e)

        def _safe_bit(addr, key, keep_last):
            nonlocal failures, first_err
            try:
                v = self._plc.batchread_bitunits(headdevice=addr, readsize=1)
                if v:        data[key] = int(v[0])
                elif not keep_last: data[key] = 0
            except Exception as e:
                failures += 1
                if first_err is None:
                    first_err = (key, e)

        _safe_word(self.cfg["status_addr"], "status", keep_last=True)
        _safe_bit (self.cfg["ok_bit"],      "ok_bit", keep_last=False)
        _safe_bit (self.cfg["ng_bit"],      "ng_bit", keep_last=False)
        _safe_word(self.cfg["model_addr"],  "model",  keep_last=True)
        if self.cfg.get("sensor_ok_addr"):
            _safe_bit(self.cfg["sensor_ok_addr"], "sensor_ok", keep_last=False)

        any_succeeded = failures < 4
        if not any_succeeded:
            self._plc_fail_streak = getattr(self, "_plc_fail_streak", 0) + 1
            if self._plc_fail_streak >= 2:
                if self._plc_ok:
                    key, err = first_err if first_err else ("?", "unknown")
                    print(f"[PLC] All reads failed x 2 cycles "
                          f"({key}: {err}) - marking dead, will reconnect.")
                self._plc_ok = False
                self._plc_died_at = time.time()
                # Force-close the dead socket so reconnect opens a fresh one.
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

        if not self._should_record_pulse():
            self._last_ok_state = ok_bit
            self._last_ng_state = ng_bit
            return 0, 0

        # is_running = PLC says RUNNING AND we're not in a scheduled break.
        # During breaks, even if the machine accidentally fires a pulse,
        # we don't count it — break windows are official non-production.
        in_break, _ = self._is_break()
        is_running  = (self._cur_status == 1 and not in_break)

        if is_running:
            if self._last_ok_state == 0 and ok_bit == 1:
                if (self._last_ok_time is None or
                        now - self._last_ok_time >= self._pulse_gap):
                    self.ok_total += 1
                    self.ok_shift += 1
                    new_ok = 1
                    self._last_ok_time = now

            if self._last_ng_state == 0 and ng_bit == 1:
                if (self._last_ng_time is None or
                        now - self._last_ng_time >= self._pulse_gap):
                    self.ng_total += 1
                    self.ng_shift += 1
                    new_ng = 1
                    self._last_ng_time = now

        self._last_ok_state = ok_bit
        self._last_ng_state = ng_bit
        return new_ok, new_ng

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

        Prefers `loss_type == 'break'`; falls back to name matching.
        Returns (None, None) if no BREAK row exists — caller must then
        fall back to IDLE (status code 0).

        Cached on first call since status_map is reloaded only on
        collector restart.
        """
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

    def _reload_breaks_from_db(self) -> None:
        """Re-pull break windows from mes_break_configs every 60 s so
        zone-breaks added via Production Admin Panel take effect mid-shift.

        routers/zones.py PUT /api/zones/{zone_id}/breaks replaces the
        break rows for every line in the zone, so reading this line's
        rows fresh is enough — no zone lookup needed here.

        Silent on DB errors so a transient hiccup never crashes collection.
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
        if _fp(self.cfg.get("breaks", [])) != _fp(new_breaks):
            self.cfg["breaks"] = new_breaks
            labels = [f"{r['break_name']} {r['start_time']}-{r['end_time']}"
                      for r in new_breaks]
            print(f"[BREAK-RELOAD] line {line_id} now has {len(new_breaks)} "
                  f"break(s): {labels or '—'}")

    def _update_status(self, status_code: int):
        now     = time.time()

        # Sentinel: -2 = "PLC never successfully read".  Skip silently
        # so the dashboard doesn't briefly flash IDLE on cold boot.
        if status_code == -2:
            self._last_status_check = now
            return

        elapsed = now - self._last_status_check
        old     = self._cur_status

        # PLC bit-flag mask + sticky fallback.  See the main
        # Phase2/collector_engine.py for the full rationale.  Short:
        # PLC D6005 packs status enum (bits 0-3) with control flags
        # (bit 4 = remote-active, etc.).  Mask to lower nibble first;
        # if still unmapped, hold the last known status.
        status_map = self.cfg.get("status_map", {}) or {}
        raw_code   = status_code
        # Stash for periodic display "(raw=N)" diagnostics.
        self._last_raw_status = raw_code

        if status_code not in status_map and status_code not in (0, -1):
            masked = status_code & 0x0F

            # Ambiguous-mask-to-IDLE guard.  raw=16/32/48 with lower
            # nibble 0 used to be force-mapped to 0 = IDLE, even though
            # the machine was clearly running.  Refuse this transition
            # and hold last known status instead.
            if masked == 0 and raw_code != 0:
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published raw={raw_code} "
                          f"— refusing IDLE interpretation, holding "
                          f"{self._cur_status_name!r}. PLC ladder check needed.")
                self._last_status_check = now
                return

            if masked in status_map or masked in (0, -1):
                status_code = masked
            else:
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published unmapped code {raw_code} "
                          f"(masked {masked} also unmapped) — sticking to last known "
                          f"{self._cur_status_name!r}.")
                self._last_status_check = now
                return

        if not (self._cur_shift and self._cur_shift.startswith("GAP")):
            # Loss accumulates based on the PLC's reported status — no
            # shift-start grace window. If the machine publishes BREAKDOWN
            # at 08:34, those seconds become breakdown loss even if we're
            # still inside the old "startup delay" minute-count.
            old_info  = status_map.get(old, {})
            loss_type = old_info.get("loss") if isinstance(old_info, dict) else None
            if loss_type and loss_type in self._loss:
                self._loss[loss_type] += elapsed

        in_break, break_name = self._is_break()

        # 2026-05-13 — restore overrides (was previously PURE-PLC MODE).
        # Three classes of override:
        #
        # ── HARD override ────────────────────────────────────────────
        # 1. Startup-delay window (FIRST 5 MIN OF EVERY SHIFT) → forced
        #    MODEL_SETUP regardless of what the PLC reports.  Operator
        #    spec: those 5 minutes are reserved for model setup and
        #    hand-over, no production counted.  Hardcoded to 5 min so
        #    every line behaves identically and admins don't need to
        #    set startup_delay_min per shift.
        #
        # ── SOFT overrides (only when PLC says RUNNING == 1) ─────────
        # Real loss codes from PLC (BREAKDOWN / QUALITY / MATERIAL etc.)
        # pass through unchanged so a genuine fault is never masked.
        # 2. GAP between shifts  → IDLE
        # 3. Scheduled break     → BREAK (paints blue on timeline)
        override_reason = None

        if self._is_in_startup_delay():
            setup = self._find_setup_status()
            if setup and setup[0] is not None:
                status_code = setup[0]
                override_reason = "STARTUP_DELAY"

        elif status_code == 1:
            if self._is_in_gap_period():
                status_code = 0
                override_reason = "GAP"
            elif in_break:
                brk = self._find_break_status()
                if brk and brk[0] is not None:
                    status_code = brk[0]
                else:
                    status_code = 0                     # legacy fallback
                override_reason = f"BREAK[{break_name or '?'}]"

        info        = self.cfg["status_map"].get(status_code, {})
        status_name = info.get("name", str(status_code))

        if status_code != self._cur_status:
            self._cur_status      = status_code
            self._cur_status_name = status_name
            self.ct.set_running(status_code == 1)
            old_name = self.cfg["status_map"].get(old, {}).get("name", str(old))
            print(f"[STATUS] {old_name} -> {self._cur_status_name} +{elapsed:.1f}s")

        self._last_status_check = now

    # ----------------------------------------------------------
    # OEE
    # ----------------------------------------------------------

    def _oee(self) -> dict:
        if not self._shift_start_ts or (self._cur_shift or "").startswith("GAP"):
            return {"avail": 0, "perf": 0, "qual": 100,
                    "overall": 0, "grade": "GAP"}

        working_seconds = self._working_seconds()
        plan_s          = max(1, working_seconds)
        total_loss      = sum(self._loss.values())
        run_s           = max(0, plan_s - total_loss)
        total           = self.ok_shift + self.ng_shift

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
        planned          = min(_shift_plan, int(working_seconds / self.cfg["ideal_ct"])) if _shift_plan > 0 else 0
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
        """Idempotent spawn for one sub-machine poller thread."""
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
        w = self._sub_workers.pop(sub_id, None)
        if w:
            w["stop"].set()
            print(f"[SUB-RELOAD] stop signal → sub-{sub_id}", flush=True)

    def _reload_subs_loop(self) -> None:
        """Every 30 s: diff mes_plc_configs vs. running threads.  Adds new
        subs, stops removed ones, hot-restarts on IP / port / bit change."""
        sleep_s = 10
        while True:
            try:
                current_subs  = load_submachines(self.cfg.get("main_plc_id")) or []
                current_by_id = {s["id"]: s for s in current_subs}
                current_ids   = set(current_by_id)
                known_ids     = set(self._sub_workers)

                for sid in current_ids - known_ids:
                    sub = current_by_id[sid]
                    print(
                        f"[SUB-RELOAD] +new sub id={sid} "
                        f"{sub.get('plc_ip')}:{sub.get('plc_port')} "
                        f"bit={sub.get('count_bit')} ({sub.get('machine_name')})",
                        flush=True,
                    )
                    self._spawn_sub_thread(sub)

                for sid in known_ids - current_ids:
                    print(f"[SUB-RELOAD] -sub id={sid} no longer in DB", flush=True)
                    self._stop_sub_thread(sid)

                for sid in current_ids & known_ids:
                    new = current_by_id[sid]
                    old = self._sub_workers[sid]["cfg_snapshot"]
                    if (str(old.get("plc_ip"))             != str(new.get("plc_ip"))
                        or int(old.get("plc_port") or 0)   != int(new.get("plc_port") or 0)
                        or str(old.get("count_bit") or "") != str(new.get("count_bit") or "")):
                        print(
                            f"[SUB-RELOAD] ~sub id={sid} config changed "
                            f"ip={old.get('plc_ip')}→{new.get('plc_ip')} "
                            f"bit={old.get('count_bit')}→{new.get('count_bit')} — hot restart",
                            flush=True,
                        )
                        self._stop_sub_thread(sid)
                        self._spawn_sub_thread(new)
            except Exception as exc:
                print(f"[SUB-RELOAD] error: {exc}", flush=True)
            time.sleep(sleep_s)
            sleep_s = 30

    def _run_submachine_poller(self, sub: dict, stop_event=None):
        """One thread per sub-machine. Polls its count bit on a dedicated
        MC4E connection and writes a row to mes_submachine_ct_log on every
        rising edge. Shares no mutable state with the main loop — only
        reads parent attributes (shift, model, part_code, status).

        `stop_event` is per-sub (set by reload loop on remove/config-change);
        falls back to legacy engine-wide event when not given."""
        if stop_event is None:
            stop_event = self._sub_stop
        sub_id    = sub["id"]
        plc_ip    = sub["plc_ip"]
        plc_port  = int(sub["plc_port"] or 5002)
        # count_bit must be configured in admin — no hardcoded fallback.
        count_bit = (sub["count_bit"] or "").strip()
        if not count_bit:
            print(f"[SUB {sub_id}] SKIP — ok_bit_address not configured in "
                  f"mes_plc_configs (machine_name={sub.get('machine_name')})",
                  flush=True)
            return
        name      = sub["machine_name"] or f"sub_{sub_id}"
        line_id   = self.cfg["line_id"]
        tag       = f"[SUB {sub_id} {name}]"

        print(f"{tag} starting poller @ {plc_ip}:{plc_port} bit={count_bit}", flush=True)

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
                plc = pymcprotocol.Type4E()
                plc.connect(plc_ip, plc_port)
                plc.batchread_bitunits(headdevice=count_bit, readsize=1)
                print(f"{tag} connected", flush=True)
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

            # Heartbeat every 30 s so we can see if the poller is healthy
            # even when M100 has been idle
            if time.time() - last_heartbeat >= 30:
                print(f"{tag} heartbeat polls={poll_count} bit={cur_bit} "
                      f"seq_today={cycle_seq_today}", flush=True)
                last_heartbeat = time.time()

            if cur_bit == 1 and last_bit == 0:
                now_dt = datetime.now()
                now_ts = now_dt.timestamp()

                if last_edge_ts is None:
                    last_edge_ts = now_ts
                else:
                    ct = now_ts - last_edge_ts
                    if ct >= 0.3:
                        # Commit FIRST, then bump the counter — otherwise a
                        # failed insert leaves a gap (row #N missing but seq
                        # advanced to N+1). Keeps "cycles count" and
                        # "last cycle_seq" in the UI header identical.
                        candidate_seq = cycle_seq_today + 1
                        ts_start = datetime.fromtimestamp(last_edge_ts)
                        ts_end   = now_dt
                        shift    = self._cur_shift or "UNKNOWN"
                        if shift.startswith("GAP"):
                            shift = "GAP"
                        try:
                            c2 = _db_conn()
                            cur2 = c2.cursor()
                            # part_code intentionally NULL for sub-machines —
                            # scanner is only on the final/main station.
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
                # of the old "1 second window every 30 sec" pattern.
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

                # Count pulses
                new_ok, new_ng = self._update_counts(
                    plc["ok_bit"], plc["ng_bit"])

                # Any pulse (OK or NG) is a completed cycle — counted in actual
                # and must appear in the CT log so the graph matches actual count.
                if new_ok > 0 or new_ng > 0:
                    self.ct.on_pulse(time.time())
                    ct_s = self.ct.stats()
                    if ct_s["list"]:
                        self.poka.check_cycle_fast(
                            ct_s["list"][-1], self._cur_shift or "")
                        # Single non-blocking part-code read (was a 1.5 s
                        # retry loop that dropped intermediate OK pulses).
                        # See note in Phase2/collector_engine.py.
                        _pc = self._read_part_code() or ""
                        if _pc.upper() == "ERROR":
                            _pc = ""
                        # Buffer for ct_log flush (written every 2s with dashboard)
                        _now = datetime.now()
                        # cycle_seq = count - 1 because the CT value measured
                        # between pulse N-1 and pulse N is cycle (N-1)'s production
                        # time. Without this, the first recorded CT appears as
                        # cycle_seq=2 instead of cycle_seq=1.
                        self._ct_pending_log.append((
                            _now,
                            _now.date(),
                            self._cur_shift or "",
                            ct_s["list"][-1],
                            max(1, self.ok_shift + self.ng_shift - 1),
                            _pc,
                            new_ng > 0,   # is_ng flag
                        ))

                if new_ok > 0:
                    self.poka.on_ok_pulse(
                        plc.get("sensor_ok"), self._cur_shift or "")
                    self.poka.on_ok_clears_ng()

                if new_ng > 0:
                    self.poka.on_ng_pulse(self._cur_shift or "")

                # D-register poka yoke check
                if (self._plc_ok and self._plc is not None
                        and not self._is_in_gap_period()):
                    in_break, _ = self._is_break()
                    # 2026-05-13 — Reloads are break-independent so
                    # `_py_configs` populates even when collector starts
                    # during a break window.  Earlier bug: empty configs
                    # → track_sensors_health early-return → empty sweep.
                    self.poka.reload_rules_from_db(self.cfg["line_id"])
                    self.poka.reload_py_configs(self.cfg["line_id"])
                    if not in_break:
                        self.poka.check_d_registers(
                            self._plc, self._cur_shift or "")
                        self.poka.check_py_bypass(
                            self._plc, self._cur_shift or "", self._cur_model)
                    # 2026-05-13 — Sensor health monitoring is INDEPENDENT
                    # of break state.  Wiring faults / stuck proximity
                    # heads must surface during downtime too, so this
                    # runs whether or not we're in a break window.
                    self.poka.track_sensors_health(self._plc)

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
                # WHILE the collector is running.  Reload every 60s so
                # the new window fires without restarting the collector.
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

                # 30 ms ≈ 33 Hz polling.  100 ms was missing brief PLC
                # OK-bit transitions, compressing multiple cycles into
                # one ct_log row.  See note in Phase2/collector_engine.py.
                time.sleep(0.03)

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
