"""
provisioner.py
==============
When admin adds a new production line, this module:

  1. Creates the dashboard table  (like ync_dashboard_complete)
  2. Generates a collector script (like YNC.py) from the line config
  3. Starts the collector as a background process
  4. Updates mes_lines.collector_pid / collector_status

To add new dashboard columns → edit DASHBOARD_TABLE_DDL
To change collector template  → edit _build_collector_script()
"""

import os
import subprocess
import sys
import platform
from datetime import datetime, time

from database import get_conn, dict_cursor


# ══════════════════════════════════════════════════════════════
# 1. DASHBOARD TABLE DDL TEMPLATE
# ══════════════════════════════════════════════════════════════

def _dashboard_table_ddl(table_name: str) -> str:
    """Generate CREATE TABLE SQL for a new line's dashboard table."""
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    id                      SERIAL PRIMARY KEY,
    timestamp               TIMESTAMP DEFAULT NOW(),
    record_date             DATE,
    shift_name              VARCHAR(20),
    shift_start_time        TIME,
    shift_end_time          TIME,
    line_name               VARCHAR(100),
    current_model_number    INTEGER,
    current_model_name      VARCHAR(100),
    ok_count                INTEGER DEFAULT 0,
    ng_count                INTEGER DEFAULT 0,
    shift_plan              INTEGER DEFAULT 1860,
    shift_plan_remaining    INTEGER DEFAULT 1860,
    shift_plan_completed    INTEGER DEFAULT 0,
    cycle_time_plan         NUMERIC(5,2) DEFAULT 15.00,
    cycle_time_actual       NUMERIC(5,2) DEFAULT 0.00,
    operating_status        VARCHAR(30),
    availability            NUMERIC(5,2) DEFAULT 0.00,
    performance             NUMERIC(5,2) DEFAULT 0.00,
    quality_oee             NUMERIC(5,2) DEFAULT 0.00,
    overall_oee             NUMERIC(5,2) DEFAULT 0.00,
    oee_grade               VARCHAR(20),
    is_shift_completed      BOOLEAN DEFAULT false,
    period_type             VARCHAR(10),
    is_gap_time             BOOLEAN DEFAULT false,

    -- 7 Loss tracking (seconds)
    loss_breakdown_seconds   INTEGER DEFAULT 0,
    loss_quality_seconds     INTEGER DEFAULT 0,
    loss_setup_seconds       INTEGER DEFAULT 0,
    loss_material_seconds    INTEGER DEFAULT 0,
    loss_others_seconds      INTEGER DEFAULT 0,
    loss_speed_seconds       INTEGER DEFAULT 0,
    loss_change_over_seconds INTEGER DEFAULT 0,

    -- 7 Loss tracking (formatted HH:MM:SS)
    loss_breakdown   VARCHAR(20) DEFAULT '00:00:00',
    loss_quality     VARCHAR(20) DEFAULT '00:00:00',
    loss_setup       VARCHAR(20) DEFAULT '00:00:00',
    loss_material    VARCHAR(20) DEFAULT '00:00:00',
    loss_others      VARCHAR(20) DEFAULT '00:00:00',
    loss_speed       VARCHAR(20) DEFAULT '00:00:00',
    loss_change_over VARCHAR(20) DEFAULT '00:00:00',
    total_loss       VARCHAR(20) DEFAULT '00:00:00',

    -- Rolling cycle times (ct1 = most recent)
    ct1  NUMERIC(7,2), ct2  NUMERIC(7,2), ct3  NUMERIC(7,2), ct4  NUMERIC(7,2),
    ct5  NUMERIC(7,2), ct6  NUMERIC(7,2), ct7  NUMERIC(7,2), ct8  NUMERIC(7,2),
    ct9  NUMERIC(7,2), ct10 NUMERIC(7,2), ct11 NUMERIC(7,2), ct12 NUMERIC(7,2),
    ct13 NUMERIC(7,2), ct14 NUMERIC(7,2), ct15 NUMERIC(7,2), ct16 NUMERIC(7,2),
    ct17 NUMERIC(7,2), ct18 NUMERIC(7,2), ct19 NUMERIC(7,2), ct20 NUMERIC(7,2),
    ct_avg_20   NUMERIC(7,2),
    min_ct      NUMERIC(7,2),
    max_ct      NUMERIC(7,2),
    std_dev_ct  NUMERIC(7,2),

    -- A-shift hourly slots
    hour_0830_0930_plan INTEGER DEFAULT 0, hour_0830_0930_actual INTEGER DEFAULT 0,
    hour_0830_0930_variance INTEGER DEFAULT 0, hour_0830_0930_ok INTEGER DEFAULT 0,
    hour_0830_0930_ng INTEGER DEFAULT 0,
    hour_0930_1030_plan INTEGER DEFAULT 0, hour_0930_1030_actual INTEGER DEFAULT 0,
    hour_0930_1030_variance INTEGER DEFAULT 0, hour_0930_1030_ok INTEGER DEFAULT 0,
    hour_0930_1030_ng INTEGER DEFAULT 0,
    hour_1030_1130_plan INTEGER DEFAULT 0, hour_1030_1130_actual INTEGER DEFAULT 0,
    hour_1030_1130_variance INTEGER DEFAULT 0, hour_1030_1130_ok INTEGER DEFAULT 0,
    hour_1030_1130_ng INTEGER DEFAULT 0,
    hour_1130_1305_plan INTEGER DEFAULT 0, hour_1130_1305_actual INTEGER DEFAULT 0,
    hour_1130_1305_variance INTEGER DEFAULT 0, hour_1130_1305_ok INTEGER DEFAULT 0,
    hour_1130_1305_ng INTEGER DEFAULT 0,
    hour_1305_1405_plan INTEGER DEFAULT 0, hour_1305_1405_actual INTEGER DEFAULT 0,
    hour_1305_1405_variance INTEGER DEFAULT 0, hour_1305_1405_ok INTEGER DEFAULT 0,
    hour_1305_1405_ng INTEGER DEFAULT 0,
    hour_1405_1505_plan INTEGER DEFAULT 0, hour_1405_1505_actual INTEGER DEFAULT 0,
    hour_1405_1505_variance INTEGER DEFAULT 0, hour_1405_1505_ok INTEGER DEFAULT 0,
    hour_1405_1505_ng INTEGER DEFAULT 0,
    hour_1505_1605_plan INTEGER DEFAULT 0, hour_1505_1605_actual INTEGER DEFAULT 0,
    hour_1505_1605_variance INTEGER DEFAULT 0, hour_1505_1605_ok INTEGER DEFAULT 0,
    hour_1505_1605_ng INTEGER DEFAULT 0,
    hour_1605_1715_plan INTEGER DEFAULT 0, hour_1605_1715_actual INTEGER DEFAULT 0,
    hour_1605_1715_variance INTEGER DEFAULT 0, hour_1605_1715_ok INTEGER DEFAULT 0,
    hour_1605_1715_ng INTEGER DEFAULT 0,

    -- B-shift hourly slots
    hour_1830_1930_plan INTEGER DEFAULT 0, hour_1830_1930_actual INTEGER DEFAULT 0,
    hour_1830_1930_variance INTEGER DEFAULT 0, hour_1830_1930_ok INTEGER DEFAULT 0,
    hour_1830_1930_ng INTEGER DEFAULT 0,
    hour_1930_2030_plan INTEGER DEFAULT 0, hour_1930_2030_actual INTEGER DEFAULT 0,
    hour_1930_2030_variance INTEGER DEFAULT 0, hour_1930_2030_ok INTEGER DEFAULT 0,
    hour_1930_2030_ng INTEGER DEFAULT 0,
    hour_2030_2130_plan INTEGER DEFAULT 0, hour_2030_2130_actual INTEGER DEFAULT 0,
    hour_2030_2130_variance INTEGER DEFAULT 0, hour_2030_2130_ok INTEGER DEFAULT 0,
    hour_2030_2130_ng INTEGER DEFAULT 0,
    hour_2130_2305_plan INTEGER DEFAULT 0, hour_2130_2305_actual INTEGER DEFAULT 0,
    hour_2130_2305_variance INTEGER DEFAULT 0, hour_2130_2305_ok INTEGER DEFAULT 0,
    hour_2130_2305_ng INTEGER DEFAULT 0,
    hour_2305_0005_plan INTEGER DEFAULT 0, hour_2305_0005_actual INTEGER DEFAULT 0,
    hour_2305_0005_variance INTEGER DEFAULT 0, hour_2305_0005_ok INTEGER DEFAULT 0,
    hour_2305_0005_ng INTEGER DEFAULT 0,
    hour_0005_0105_plan INTEGER DEFAULT 0, hour_0005_0105_actual INTEGER DEFAULT 0,
    hour_0005_0105_variance INTEGER DEFAULT 0, hour_0005_0105_ok INTEGER DEFAULT 0,
    hour_0005_0105_ng INTEGER DEFAULT 0,
    hour_0105_0205_plan INTEGER DEFAULT 0, hour_0105_0205_actual INTEGER DEFAULT 0,
    hour_0105_0205_variance INTEGER DEFAULT 0, hour_0105_0205_ok INTEGER DEFAULT 0,
    hour_0105_0205_ng INTEGER DEFAULT 0,
    hour_0205_0315_plan INTEGER DEFAULT 0, hour_0205_0315_actual INTEGER DEFAULT 0,
    hour_0205_0315_variance INTEGER DEFAULT 0, hour_0205_0315_ok INTEGER DEFAULT 0,
    hour_0205_0315_ng INTEGER DEFAULT 0,

    -- Gap slots
    hour_1715_1830_actual INTEGER DEFAULT 0, hour_1715_1830_ok INTEGER DEFAULT 0,
    hour_1715_1830_ng INTEGER DEFAULT 0,
    hour_0315_0415_actual INTEGER DEFAULT 0, hour_0315_0415_ok INTEGER DEFAULT 0,
    hour_0315_0415_ng INTEGER DEFAULT 0,

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_{table_name}_date_shift
    ON {table_name}(record_date, shift_name);
CREATE INDEX IF NOT EXISTS idx_{table_name}_active
    ON {table_name}(is_shift_completed) WHERE is_shift_completed = false;
"""


# ══════════════════════════════════════════════════════════════
# 2. AUTO-GENERATE HOURLY SLOTS FROM SHIFTS
# ══════════════════════════════════════════════════════════════

def _generate_hourly_slots_from_shifts(shifts: list) -> list:
    """
    Generate hourly slots automatically from shift configuration.
    This ensures every line has proper hourly slots without manual entry.
    """
    slots = []
    
    # Define slot durations for each shift (in minutes)
    # A Shift slots
    a_slot_durations = [
        ("08:30-09:30", 55, 220, "hour_0830_0930"),
        ("09:30-10:30", 50, 200, "hour_0930_1030"),
        ("10:30-11:30", 60, 240, "hour_1030_1130"),
        ("11:30-13:05", 60, 240, "hour_1130_1305"),
        ("13:05-14:05", 60, 240, "hour_1305_1405"),
        ("14:05-15:05", 50, 200, "hour_1405_1505"),
        ("15:05-16:05", 60, 240, "hour_1505_1605"),
        ("16:05-17:15", 70, 280, "hour_1605_1715")
    ]
    
    # B Shift slots
    b_slot_durations = [
        ("18:30-19:30", 55, 220, "hour_1830_1930"),
        ("19:30-20:30", 50, 200, "hour_1930_2030"),
        ("20:30-21:30", 60, 240, "hour_2030_2130"),
        ("21:30-23:05", 60, 240, "hour_2130_2305"),
        ("23:05-00:05", 60, 240, "hour_2305_0005"),
        ("00:05-01:05", 55, 220, "hour_0005_0105"),
        ("01:05-02:05", 55, 220, "hour_0105_0205"),
        ("02:05-03:15", 70, 280, "hour_0205_0315")
    ]
    
    # Check which shifts are defined
    shift_names = [s["shift_name"] for s in shifts if s.get("is_production", False)]
    
    slot_order = 1
    if "A" in shift_names:
        for slot_label, working_min, plan_pieces, db_prefix in a_slot_durations:
            slots.append({
                "shift_name": "A",
                "slot_label": slot_label,
                "start_time": slot_label.split("-")[0],
                "end_time": slot_label.split("-")[1],
                "crosses_midnight": False,
                "working_minutes": working_min,
                "plan_pieces": plan_pieces,
                "db_column_prefix": db_prefix,
                "slot_order": slot_order
            })
            slot_order += 1
    
    if "B" in shift_names:
        slot_order = 1
        for slot_label, working_min, plan_pieces, db_prefix in b_slot_durations:
            # Handle midnight crossing for the 23:05-00:05 slot
            crosses_midnight = slot_label == "23:05-00:05"
            slots.append({
                "shift_name": "B",
                "slot_label": slot_label,
                "start_time": slot_label.split("-")[0],
                "end_time": slot_label.split("-")[1],
                "crosses_midnight": crosses_midnight,
                "working_minutes": working_min,
                "plan_pieces": plan_pieces,
                "db_column_prefix": db_prefix,
                "slot_order": slot_order
            })
            slot_order += 1
    
    return slots


# ══════════════════════════════════════════════════════════════
# 3. COLLECTOR SCRIPT GENERATOR
# ══════════════════════════════════════════════════════════════

def _build_collector_script(line_cfg: dict) -> str:
    """
    Generate a collector Python script for a given line configuration.
    line_cfg keys:
      line_id, line_name, table_name,
      plc_ip, plc_port,
      ok_bit, ng_bit, status_addr, model_addr,
      ideal_ct, max_ct,
      shifts (list), breaks (list), slots (list),
      models (dict), status_map (dict),
      poka_yoke_rules (list)
    """
    ln   = line_cfg["line_name"]
    tbl  = line_cfg["table_name"]
    lid  = line_cfg["line_id"]
    pip  = line_cfg["plc_ip"]
    pprt = line_cfg["plc_port"]
    ok_a = line_cfg["ok_bit"]
    ng_a = line_cfg["ng_bit"]
    st_a = line_cfg["status_addr"]
    md_a = line_cfg["model_addr"]
    ict  = line_cfg["ideal_ct"]
    mct  = line_cfg["max_ct"]

    # Build Python literals from config
    models_literal = repr(
        {m["model_number"]: m["model_name"] for m in line_cfg["models"]}
    )
    status_literal = repr(
        {s["status_code"]: {"name": s["status_name"], "loss": s.get("loss_type")} for s in line_cfg["status_map"]}
    )
    breaks_literal = repr([
        {"start": b["start_time"], "end": b["end_time"], "name": b["break_name"]}
        for b in line_cfg["breaks"]
    ])
    shifts_literal = repr({
        s["shift_name"]: {
            "start": s["start_time"],
            "end":   s["end_time"],
            "plan":  s["total_plan"],
            "crosses_midnight": s["crosses_midnight"],
        }
        for s in line_cfg["shifts"] if s.get("is_production", False)
    })

    return f'''#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — {ln}
Line ID    : {lid}
Table      : {tbl}
Generated  : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {{
    "line_id":    {lid},
    "line_name":  "{ln}",
    "table_name": "{tbl}",
    "plc_ip":     "{pip}",
    "plc_port":   {pprt},
    "ok_bit":     "{ok_a}",
    "ng_bit":     "{ng_a}",
    "status_addr":"{st_a}",
    "model_addr": "{md_a}",
    "ideal_ct":   {ict},
    "max_ct":     {mct},
    "models":     {models_literal},
    "status_map": {status_literal},
    "breaks":     {breaks_literal},
    "shifts":     {shifts_literal},
}}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
'''


# ══════════════════════════════════════════════════════════════
# 4. FULL PROVISIONING FLOW
# ══════════════════════════════════════════════════════════════

COLLECTORS_DIR = os.path.join(os.path.dirname(__file__), "collectors")


def provision_line(line_id: int) -> dict:
    """
    Full provisioning for a line:
      1. Load all config from DB
      2. Create dashboard table
      3. Generate collector script
      4. Start collector process
      5. Update mes_lines status

    Returns {"ok": True, "pid": int, "table": str, "script": str}
    """
    os.makedirs(COLLECTORS_DIR, exist_ok=True)

    with get_conn() as conn:
        cur = dict_cursor(conn)

        # ── Load line ──────────────────────────────────────
        cur.execute("""
            SELECT l.*, p.plant_name
            FROM mes_lines l
            JOIN mes_plants p ON p.id = l.plant_id
            WHERE l.id = %s
        """, (line_id,))
        line = cur.fetchone()
        if not line:
            raise ValueError(f"Line {line_id} not found")

        table_name = line["db_table_name"]

        # ── Load PLC config (main PLC only — parent_plc_id IS NULL) ───
        cur.execute(
            "SELECT * FROM mes_plc_configs "
            "WHERE line_id = %s AND parent_plc_id IS NULL",
            (line_id,)
        )
        plc = cur.fetchone()
        if not plc:
            raise ValueError(f"No PLC config for line {line_id}")

        # ── Load related config ────────────────────────────
        cur.execute(
            "SELECT * FROM mes_model_mappings WHERE line_id = %s ORDER BY model_number",
            (line_id,)
        )
        models = cur.fetchall()

        cur.execute(
            "SELECT * FROM mes_status_mappings WHERE line_id = %s ORDER BY status_code",
            (line_id,)
        )
        status_map = cur.fetchall()

        cur.execute(
            "SELECT * FROM mes_break_configs WHERE line_id = %s ORDER BY start_time",
            (line_id,)
        )
        breaks = cur.fetchall()

        cur.execute(
            "SELECT * FROM mes_shift_configs WHERE line_id = %s ORDER BY shift_name",
            (line_id,)
        )
        shifts = cur.fetchall()

        cur.execute(
            "SELECT * FROM mes_hourly_slots WHERE line_id = %s ORDER BY shift_name, slot_order",
            (line_id,)
        )
        existing_slots = cur.fetchall()

        cur.execute(
            "SELECT * FROM mes_poka_yoke_rules WHERE line_id = %s AND is_active = true",
            (line_id,)
        )
        poka_rules = cur.fetchall()

        # ── Convert time objects to strings for safe serialization ──
        def time_to_str(t):
            if isinstance(t, time):
                return t.strftime("%H:%M:%S")
            return t

        # Convert break times
        breaks_list = []
        for b in breaks:
            b_dict = dict(b)
            b_dict["start_time"] = time_to_str(b_dict["start_time"])
            b_dict["end_time"] = time_to_str(b_dict["end_time"])
            breaks_list.append(b_dict)

        # Convert shift times
        shifts_list = []
        for s in shifts:
            s_dict = dict(s)
            s_dict["start_time"] = time_to_str(s_dict["start_time"])
            s_dict["end_time"] = time_to_str(s_dict["end_time"])
            shifts_list.append(s_dict)

        # Convert existing slot times
        slots_list = []
        for sl in existing_slots:
            sl_dict = dict(sl)
            sl_dict["start_time"] = time_to_str(sl_dict["start_time"])
            sl_dict["end_time"] = time_to_str(sl_dict["end_time"])
            slots_list.append(sl_dict)

        # ── 1. Create dashboard table ──────────────────────
        ddl = _dashboard_table_ddl(table_name)
        conn.cursor().execute(ddl)
        print(f"[PROVISION] ✅ Table '{table_name}' created/verified")

        # ── 2. Auto-create hourly slots if none exist ──────
        if len(existing_slots) == 0:
            print(f"[PROVISION] Auto-generating hourly slots for line {line_id}")
            new_slots = _generate_hourly_slots_from_shifts(shifts_list)
            
            for slot in new_slots:
                cur.execute("""
                    INSERT INTO mes_hourly_slots 
                    (line_id, shift_name, slot_label, start_time, end_time, 
                     crosses_midnight, working_minutes, plan_pieces, 
                     db_column_prefix, slot_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    line_id, slot["shift_name"], slot["slot_label"],
                    slot["start_time"], slot["end_time"], slot["crosses_midnight"],
                    slot["working_minutes"], slot["plan_pieces"],
                    slot["db_column_prefix"], slot["slot_order"]
                ))
            conn.commit()
            print(f"[PROVISION] ✅ Created {len(new_slots)} hourly slots")
            
            # Refresh slots list after creation
            cur.execute("""
                SELECT * FROM mes_hourly_slots WHERE line_id = %s ORDER BY shift_name, slot_order
            """, (line_id,))
            slots_list = []
            for sl in cur.fetchall():
                sl_dict = dict(sl)
                sl_dict["start_time"] = time_to_str(sl_dict["start_time"])
                sl_dict["end_time"] = time_to_str(sl_dict["end_time"])
                slots_list.append(sl_dict)

        # ── 3. Build line config dict ──────────────────────
        line_cfg = {
            "line_id":    line_id,
            "line_name":  line["line_name"],
            "table_name": table_name,
            "plc_ip":     plc["plc_ip"],
            "plc_port":   plc["plc_port"],
            "ok_bit":     plc["ok_bit_address"],
            "ng_bit":     plc["ng_bit_address"],
            "status_addr":plc["status_address"],
            "model_addr": plc["model_address"],
            "ideal_ct":   float(plc["ideal_cycle_time"]),
            "max_ct":     float(plc["max_allowed_cycle"]),
            "models":     [dict(m) for m in models],
            "status_map": [dict(s) for s in status_map],
            "breaks":     breaks_list,
            "shifts":     shifts_list,
            "slots":      slots_list,
            "poka_yoke_rules": [dict(r) for r in poka_rules],
        }

        # ── 4. Generate collector script ───────────────────
        script_content = _build_collector_script(line_cfg)
        safe_name = line["line_code"].lower().replace("-", "_").replace(" ", "_")
        script_path = os.path.join(COLLECTORS_DIR, f"collector_{safe_name}.py")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        print(f"[PROVISION] ✅ Collector script: {script_path}")

        # ── 5. Start collector process (detached) ──────────
        log_path = os.path.join(COLLECTORS_DIR, f"collector_{safe_name}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creationflags = 0

        proc = subprocess.Popen(
            [sys.executable, script_path],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=True
        )
        pid = proc.pid
        print(f"[PROVISION] ✅ Collector started PID={pid}")

        # ── 6. Update mes_lines ────────────────────────────
        conn.cursor().execute("""
            UPDATE mes_lines
            SET collector_pid    = %s,
                collector_status = 'running',
                updated_at       = NOW()
            WHERE id = %s
        """, (pid, line_id))

        # ── Audit log ──────────────────────────────────────
        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('LINE_PROVISIONED', 'line', %s, %s)
        """, (line_id, f"table={table_name} pid={pid} script={script_path}"))

    return {
        "ok":     True,
        "pid":    pid,
        "table":  table_name,
        "script": script_path,
    }


def stop_collector(line_id: int) -> dict:
    """Stop the running collector process for a line."""
    import signal

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT collector_pid, collector_status FROM mes_lines WHERE id = %s",
            (line_id,)
        )
        line = cur.fetchone()
        if not line or not line["collector_pid"]:
            return {"ok": False, "message": "No running collector found"}

        pid = line["collector_pid"]
        try:
            if platform.system() == "Windows":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass   # Already gone
        except PermissionError as e:
            return {"ok": False, "message": str(e)}

        conn.cursor().execute("""
            UPDATE mes_lines
            SET collector_pid    = NULL,
                collector_status = 'stopped',
                updated_at       = NOW()
            WHERE id = %s
        """, (line_id,))

        conn.cursor().execute("""
            INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
            VALUES ('COLLECTOR_STOPPED', 'line', %s, %s)
        """, (line_id, f"pid={pid}"))

    return {"ok": True, "message": f"Collector PID {pid} stopped"}