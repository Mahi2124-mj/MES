-- ════════════════════════════════════════════════════════════════════════════
-- EOL / MES PLATFORM — COMPLETE SAMPLE SCHEMA
-- Toyota Boshoku · Multi-plant · Multi-zone · Multi-line
-- ════════════════════════════════════════════════════════════════════════════
-- Single-file reference of every table the EOL backend, collectors, Camera
-- CMS and Sensor-Health panel touch.  Run on PostgreSQL 13+.
--
-- Tables grouped by feature:
--   1. Core hierarchy          (plants → zones → lines → machines)
--   2. PLC & shift config      (per-line)
--   3. Models / Status / Breaks
--   4. Poka-Yoke master + assignments
--   5. Poka-Yoke runtime       (rules / events / sensor sweep / mail)
--   6. Per-line dashboard      (one table per line, name from mes_lines.db_table_name)
--   7. Cycle-time logs         (per line + submachines)
--   8. Auth / audit
--   9. Optional sample seed data
--
-- Most tables auto-migrate themselves on first backend access (CREATE TABLE
-- IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS), so re-running this
-- file on an existing DB is safe — no data loss.
-- ════════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ════════════════════════════════════════════════════════════════════════════
-- 1. CORE HIERARCHY
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS mes_plants (
    id           SERIAL PRIMARY KEY,
    plant_code   VARCHAR(20)  UNIQUE NOT NULL,
    plant_name   VARCHAR(100) NOT NULL,
    location     VARCHAR(200),
    timezone     VARCHAR(50)  DEFAULT 'Asia/Kolkata',
    is_active    BOOLEAN      DEFAULT true,
    created_at   TIMESTAMP    DEFAULT NOW(),
    updated_at   TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_zones (
    id          SERIAL PRIMARY KEY,
    plant_id    INTEGER      REFERENCES mes_plants(id) ON DELETE CASCADE,
    zone_code   VARCHAR(30)  NOT NULL,
    zone_name   VARCHAR(100) NOT NULL,
    description VARCHAR(200),
    is_active   BOOLEAN      DEFAULT true,
    created_at  TIMESTAMP    DEFAULT NOW(),
    updated_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE(plant_id, zone_code)
);

CREATE TABLE IF NOT EXISTS mes_lines (
    id                  SERIAL PRIMARY KEY,
    plant_id            INTEGER      REFERENCES mes_plants(id) ON DELETE CASCADE,
    zone_id             INTEGER      REFERENCES mes_zones(id)  ON DELETE SET NULL,
    line_code           VARCHAR(30)  NOT NULL,
    line_name           VARCHAR(100) NOT NULL,
    description         VARCHAR(200),
    is_active           BOOLEAN      DEFAULT true,
    db_table_name       VARCHAR(100),
    collector_pid       INTEGER,
    collector_status    VARCHAR(20)  DEFAULT 'stopped',
    -- Shift bookkeeping (added by ALTER on existing installs)
    current_shift_row_id INTEGER,
    ot_active_shift     VARCHAR(10),
    ot_start_a TIME, ot_end_a TIME,
    ot_start_b TIME, ot_end_b TIME,
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    UNIQUE(plant_id, line_code)
);
CREATE INDEX IF NOT EXISTS idx_mes_lines_zone_id ON mes_lines(zone_id);


-- ════════════════════════════════════════════════════════════════════════════
-- 2. PLC + MACHINE-MONITOR CONFIG (per line)
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS mes_plc_configs (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE UNIQUE,
    plc_ip          VARCHAR(50)  NOT NULL,
    plc_port        INTEGER      DEFAULT 5002,
    protocol        VARCHAR(20)  DEFAULT 'MC4E',

    ok_bit_address  VARCHAR(20),
    ng_bit_address  VARCHAR(20),
    status_address  VARCHAR(20),
    model_address   VARCHAR(20),

    sensor_ok_address    VARCHAR(20),
    process_seq_address  VARCHAR(20),
    override_address     VARCHAR(20),

    ideal_cycle_time     NUMERIC(6,2),
    max_allowed_cycle    NUMERIC(6,2),
    ok_ng_pulse_min_gap  NUMERIC(4,2)  DEFAULT 0.5,

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- Per-PLC submachine monitoring (load cells, data registers, etc.)
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
);


-- ════════════════════════════════════════════════════════════════════════════
-- 3. MODELS · STATUS · SHIFT · BREAK · HOURLY SLOT MAPPINGS (per line)
-- ════════════════════════════════════════════════════════════════════════════

-- Global status colour palette — same for every line in the plant.
CREATE TABLE IF NOT EXISTS mes_global_status (
    id           SERIAL PRIMARY KEY,
    status_code  INTEGER     UNIQUE NOT NULL,
    status_name  VARCHAR(50) NOT NULL,
    color_hex    VARCHAR(10) NOT NULL,
    color_label  VARCHAR(30) NOT NULL,
    loss_type    VARCHAR(30),
    is_production BOOLEAN DEFAULT false,
    description  VARCHAR(200),
    sort_order   INTEGER DEFAULT 0,
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_status_mappings (
    id          SERIAL PRIMARY KEY,
    line_id     INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE,
    status_code INTEGER      NOT NULL,
    status_name VARCHAR(50)  NOT NULL,
    loss_type   VARCHAR(30),
    UNIQUE(line_id, status_code)
);

CREATE TABLE IF NOT EXISTS mes_model_mappings (
    id           SERIAL PRIMARY KEY,
    line_id      INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    model_number INTEGER     NOT NULL,
    model_name   VARCHAR(100) NOT NULL,
    UNIQUE(line_id, model_number)
);

CREATE TABLE IF NOT EXISTS mes_shift_configs (
    id                SERIAL PRIMARY KEY,
    line_id           INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    shift_name        VARCHAR(10) NOT NULL,
    start_time        TIME        NOT NULL,
    end_time          TIME        NOT NULL,
    crosses_midnight  BOOLEAN     DEFAULT false,
    total_plan        INTEGER     DEFAULT 0,
    working_minutes   INTEGER     DEFAULT 0,
    startup_delay_min INTEGER     DEFAULT 5,
    is_production     BOOLEAN     DEFAULT true,
    ot_start_time     TIME,                       -- for overtime extension
    UNIQUE(line_id, shift_name)
);

CREATE TABLE IF NOT EXISTS mes_hourly_slots (
    id               SERIAL PRIMARY KEY,
    line_id          INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    shift_name       VARCHAR(10) NOT NULL,
    slot_label       VARCHAR(20) NOT NULL,
    start_time       TIME        NOT NULL,
    end_time         TIME        NOT NULL,
    crosses_midnight BOOLEAN     DEFAULT false,
    working_minutes  INTEGER     NOT NULL,
    plan_pieces      INTEGER     NOT NULL,
    db_column_prefix VARCHAR(40),
    slot_order       INTEGER     DEFAULT 0,
    UNIQUE(line_id, shift_name, slot_label)
);

CREATE TABLE IF NOT EXISTS mes_break_configs (
    id                SERIAL PRIMARY KEY,
    line_id           INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    break_name        VARCHAR(50) NOT NULL,
    start_time        TIME        NOT NULL,
    end_time          TIME        NOT NULL,
    crosses_midnight  BOOLEAN     DEFAULT false,
    applies_to_shifts VARCHAR(50) DEFAULT 'A,B',
    UNIQUE(line_id, break_name)
);

-- Non-production days (holidays / planned shutdowns)
CREATE TABLE IF NOT EXISTS mes_non_production_days (
    id          SERIAL PRIMARY KEY,
    line_id     INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    npd_date    DATE        NOT NULL,
    reason      VARCHAR(100),
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(line_id, npd_date)
);


-- ════════════════════════════════════════════════════════════════════════════
-- 4. POKA-YOKE MASTER + ASSIGNMENTS
-- ════════════════════════════════════════════════════════════════════════════

-- Series codes (YHB / YNC / YY8 etc.) used in model names.
CREATE TABLE IF NOT EXISTS mes_py_series (
    id         SERIAL PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Model master — zone-aware; same bit_number can repeat across zones.
CREATE TABLE IF NOT EXISTS mes_py_model_master (
    id            SERIAL PRIMARY KEY,
    zone_id       INTEGER      REFERENCES mes_zones(id) ON DELETE SET NULL,
    model_name    VARCHAR(200) NOT NULL,
    model_type    VARCHAR(50),
    series        VARCHAR(50),
    old_model_no  VARCHAR(50),
    bit_number    INTEGER,
    is_active     BOOLEAN     DEFAULT true,
    created_at    TIMESTAMP   DEFAULT NOW(),
    updated_at    TIMESTAMP   DEFAULT NOW()
);
-- One active model per (zone, bit_number) — same bit can exist in another zone.
CREATE UNIQUE INDEX IF NOT EXISTS ux_py_model_zone_bit_active
    ON mes_py_model_master (zone_id, bit_number)
    WHERE is_active = true AND bit_number IS NOT NULL;

-- PY master — every Poka-Yoke check, with output D-bit + sensing X-bit(s).
CREATE TABLE IF NOT EXISTS mes_py_master (
    id              SERIAL PRIMARY KEY,
    py_no           VARCHAR(50),                 -- nullable; D-bit is the real key
    description     VARCHAR(200),
    model_type      VARCHAR(50),                 -- "4 Way" / "6 Way"
    side            VARCHAR(20),                 -- LH / RH / OTR / ALL / Otr LH / Otr RH
    bit             VARCHAR(50),                 -- Output D-register (D401, D406…)
    desired_value   VARCHAR(10),
    machine_name    VARCHAR(100),
    register        VARCHAR(50),                 -- legacy mirror of `bit`
    register_count  INTEGER     DEFAULT 1,       -- 1 → {0,1,2}; 2 → {0..4}
    zone_id         INTEGER     REFERENCES mes_zones(id) ON DELETE SET NULL,
    sensing_bits    VARCHAR(100),                -- X-bit(s) for sensor health
    is_active       BOOLEAN     DEFAULT true,
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

-- PY × model assignment: per-model expected output (PASS / OFF / ON).
CREATE TABLE IF NOT EXISTS mes_py_assignments (
    id            SERIAL PRIMARY KEY,
    py_id         INTEGER     REFERENCES mes_py_master(id) ON DELETE CASCADE,
    py_no         VARCHAR(50),                  -- legacy join key
    py_name       VARCHAR(200),
    side          VARCHAR(20),
    model_id      INTEGER     REFERENCES mes_py_model_master(id) ON DELETE CASCADE,
    model_name    VARCHAR(200),
    model_type    VARCHAR(50),
    model_series  VARCHAR(50),
    old_model_no  VARCHAR(50),
    d_bit         VARCHAR(50),
    desired_bit   INTEGER,
    desired_value INTEGER,                      -- 0=PASS, 1=OFF, 2=ON  (or 0..4 for 2-reg)
    desired_value_2 INTEGER,                    -- second half of a 2-register PY
    machine_name  VARCHAR(100),
    created_at    TIMESTAMP   DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_py_assignments_model ON mes_py_assignments(model_id);
CREATE INDEX IF NOT EXISTS idx_py_assignments_py_no ON mes_py_assignments(py_no);

-- Optional: PLC-side helpers (sensor↔D-bit map + PLC actuals + model column hints)
CREATE TABLE IF NOT EXISTS mes_py_sensor_mapping (
    id           SERIAL PRIMARY KEY,
    sensor_name  VARCHAR(200) NOT NULL,
    device_no    VARCHAR(50),
    d_bit        VARCHAR(20),
    UNIQUE(sensor_name)
);

CREATE TABLE IF NOT EXISTS mes_py_plc_actuals (
    id           SERIAL PRIMARY KEY,
    d_bit        VARCHAR(20) NOT NULL,
    model_col    VARCHAR(10) NOT NULL,
    actual_value INTEGER,
    updated_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(d_bit, model_col)
);

CREATE TABLE IF NOT EXISTS mes_py_model_columns (
    id           SERIAL PRIMARY KEY,
    col_key      VARCHAR(10) NOT NULL UNIQUE,
    model_code   VARCHAR(20),
    model_type   VARCHAR(50),
    type_side    VARCHAR(10),
    full_name    VARCHAR(200)
);


-- ════════════════════════════════════════════════════════════════════════════
-- 5. POKA-YOKE RUNTIME — RULES · EVENTS · SENSOR SWEEP · MAIL · ACK
-- ════════════════════════════════════════════════════════════════════════════

-- Logic rules (legacy + supplementary) — bypass / override / consecutive NG / etc.
CREATE TABLE IF NOT EXISTS mes_poka_yoke_rules (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE,
    poka_yoke_no    VARCHAR(50),
    poka_yoke_name  VARCHAR(200),
    rule_type       VARCHAR(30),
    side            VARCHAR(10)  DEFAULT 'ALL',
    model           VARCHAR(50)  DEFAULT 'ALL',
    bit             VARCHAR(20),
    value           VARCHAR(50),
    machine_name    VARCHAR(100),
    sheet_name      VARCHAR(50),
    plc_address     VARCHAR(20),
    expected_value  VARCHAR(50),
    trigger_value   VARCHAR(50),
    threshold_count INTEGER DEFAULT 1,
    window_seconds  INTEGER DEFAULT 60,
    alert_level     VARCHAR(10) DEFAULT 'WARNING',
    alert_message   VARCHAR(200),
    auto_stop_line  BOOLEAN DEFAULT false,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Event log — ingested by collector, consumed by alert pipeline.
CREATE TABLE IF NOT EXISTS mes_poka_yoke_events (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER     REFERENCES mes_lines(id),
    rule_id         INTEGER     REFERENCES mes_poka_yoke_rules(id),
    rule_type       VARCHAR(30),                  -- SENSOR_BYPASS / SENSOR_HEALTH / etc.
    alert_level     VARCHAR(10),
    detected_at     TIMESTAMP   DEFAULT NOW(),
    shift_name      VARCHAR(10),
    plc_value       VARCHAR(50),
    context_json    TEXT,                         -- {py_no, x_bit, d_bit, reason, ...}
    acknowledged    BOOLEAN DEFAULT false,
    acknowledged_at TIMESTAMP,
    acknowledged_by VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_pke_line_time ON mes_poka_yoke_events(line_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_pke_ack       ON mes_poka_yoke_events(acknowledged) WHERE acknowledged = false;

-- Mail-config: per-task TO / CC.  Edited via Admin → Mail Config.  Read by
-- _get_mail_addrs(kind) — DB > env > legacy fallback.
CREATE TABLE IF NOT EXISTS mes_mail_config (
    key         VARCHAR(50) PRIMARY KEY,         -- bypass_to, bypass_cc, health_to, hourly_to, …
    value       TEXT,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_by  VARCHAR(80)
);

-- Sensor-Health ACK queue.  UI inserts a row when the operator clicks ACK
-- on a BROKEN bit; the collector consumes the row on its next sweep, runs
-- a forced toggle, and writes the result back here.
CREATE TABLE IF NOT EXISTS mes_sensor_ack_requests (
    id            SERIAL PRIMARY KEY,
    line_id       INTEGER NOT NULL,
    d_bit         VARCHAR(20) NOT NULL,
    py_id         INTEGER,
    requested_by  VARCHAR(80),
    requested_at  TIMESTAMPTZ DEFAULT NOW(),
    processed     BOOLEAN DEFAULT false,
    processed_at  TIMESTAMPTZ,
    toggled       BOOLEAN,
    result_msg    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sensor_ack_pending
    ON mes_sensor_ack_requests (line_id, processed);


-- ════════════════════════════════════════════════════════════════════════════
-- 6. PER-LINE DASHBOARD TABLE  (one per line — name from mes_lines.db_table_name)
-- ════════════════════════════════════════════════════════════════════════════
-- Provisioned automatically by Phase2/provisioner.py when a new line is added
-- via Admin Panel.  Schema below is the TEMPLATE — actual table name varies.
-- Replace `<table_name>` placeholder before running manually.
--
-- Example: ync_dashboard_complete  (one row per shift × date, updated live)
-- ────────────────────────────────────────────────────────────────────────────
/*
CREATE TABLE IF NOT EXISTS <table_name> (
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
    shift_plan              INTEGER,
    shift_plan_remaining    INTEGER,
    shift_plan_completed    INTEGER DEFAULT 0,
    cycle_time_plan         NUMERIC(5,2),
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

    -- 7-loss tracking (seconds + formatted strings)
    loss_breakdown_seconds   INTEGER DEFAULT 0,
    loss_quality_seconds     INTEGER DEFAULT 0,
    loss_setup_seconds       INTEGER DEFAULT 0,
    loss_material_seconds    INTEGER DEFAULT 0,
    loss_others_seconds      INTEGER DEFAULT 0,
    loss_speed_seconds       INTEGER DEFAULT 0,
    loss_change_over_seconds INTEGER DEFAULT 0,
    loss_breakdown   VARCHAR(20) DEFAULT '00:00:00',
    loss_quality     VARCHAR(20) DEFAULT '00:00:00',
    loss_setup       VARCHAR(20) DEFAULT '00:00:00',
    loss_material    VARCHAR(20) DEFAULT '00:00:00',
    loss_others      VARCHAR(20) DEFAULT '00:00:00',
    loss_speed       VARCHAR(20) DEFAULT '00:00:00',
    loss_change_over VARCHAR(20) DEFAULT '00:00:00',
    total_loss       VARCHAR(20) DEFAULT '00:00:00',

    -- Rolling cycle times (ct1 = most recent)
    ct1  NUMERIC(7,2), ct2  NUMERIC(7,2), ... ct20 NUMERIC(7,2),
    ct_avg_20 NUMERIC(7,2), min_ct NUMERIC(7,2), max_ct NUMERIC(7,2),
    std_dev_ct NUMERIC(7,2),

    -- Hourly slot columns (per A & B shift) — see provisioner.py for full list
    -- hour_<HHMM>_<HHMM>_plan / actual / variance / ok / ng
    -- Plus gap slots (no plan)

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_<table_name>_date_shift
    ON <table_name>(record_date, shift_name);
CREATE INDEX IF NOT EXISTS idx_<table_name>_active
    ON <table_name>(is_shift_completed) WHERE is_shift_completed = false;
*/


-- ════════════════════════════════════════════════════════════════════════════
-- 7. CYCLE-TIME LOGS
-- ════════════════════════════════════════════════════════════════════════════

-- Per-line cycle log — table name = "<dashboard_table>_ct_log".  Created
-- automatically by collector_engine._ensure_ct_log_table on first cycle.
-- Template:
/*
CREATE TABLE IF NOT EXISTS <line_table>_ct_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMP NOT NULL,
    record_date DATE      NOT NULL,
    shift_name  VARCHAR(20),
    ct_value    NUMERIC(7,2) NOT NULL,
    cycle_seq   INTEGER,
    part_code   VARCHAR(64),
    is_ng       BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS <line_table>_ct_log_date_shift
    ON <line_table>_ct_log(record_date, shift_name);
*/

-- Submachine cycle log — shared table for all sub-PLC pollers (per sub_plc_id).
CREATE TABLE IF NOT EXISTS mes_submachine_ct_log (
    id           SERIAL PRIMARY KEY,
    sub_plc_id   INTEGER     NOT NULL,
    line_id      INTEGER,
    record_date  DATE        NOT NULL,
    shift_name   VARCHAR(20),
    cycle_seq    INTEGER,
    ts_start     TIMESTAMP,
    ts_end       TIMESTAMP,
    ct_seconds   NUMERIC(7,2),
    model_number INTEGER,
    model_name   VARCHAR(100),
    part_code    VARCHAR(64),
    created_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_submachine_ct_log_lookup
    ON mes_submachine_ct_log(sub_plc_id, record_date, shift_name);


-- ════════════════════════════════════════════════════════════════════════════
-- 8. AUTH · OPERATOR ACCESS · AUDIT LOG · STATUS LOG
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS mes_admin (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50)  UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    role          VARCHAR(20)  NOT NULL DEFAULT 'admin',  -- admin / department / operator
    last_login    TIMESTAMP,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_operator_lines (
    id         SERIAL PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES mes_admin(id) ON DELETE CASCADE,
    line_id    INTEGER NOT NULL REFERENCES mes_lines(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(admin_id, line_id)
);
CREATE INDEX IF NOT EXISTS idx_operator_lines_admin ON mes_operator_lines(admin_id);
CREATE INDEX IF NOT EXISTS idx_operator_lines_line  ON mes_operator_lines(line_id);

CREATE TABLE IF NOT EXISTS mes_audit_log (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50),
    entity_id   INTEGER,
    details     TEXT,
    ip_address  VARCHAR(50),
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON mes_audit_log(created_at DESC);

-- Per-line status timeline (raw event stream from collector)
CREATE TABLE IF NOT EXISTS mes_status_log (
    id           SERIAL PRIMARY KEY,
    line_id      INTEGER REFERENCES mes_lines(id) ON DELETE CASCADE,
    record_date  DATE,
    shift_name   VARCHAR(10),
    status_code  INTEGER,
    status_name  VARCHAR(50),
    started_at   TIMESTAMP,
    ended_at     TIMESTAMP,
    duration_sec INTEGER,
    created_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mes_status_log_line_date
    ON mes_status_log(line_id, record_date);


-- ════════════════════════════════════════════════════════════════════════════
-- 9. SAMPLE SEED DATA  (optional — comment out on production install)
-- ════════════════════════════════════════════════════════════════════════════

-- Default admin: username=admin / password=admin@123  (change immediately)
INSERT INTO mes_admin (username, password_hash, role)
VALUES ('admin', crypt('admin@123', gen_salt('bf', 12)), 'admin')
ON CONFLICT (username) DO NOTHING;

-- 8 standard status codes (matches Fullscreen colour palette)
INSERT INTO mes_global_status
    (status_code, status_name, color_hex, color_label, loss_type, is_production, sort_order) VALUES
    (0, 'IDLE',          '#94a3b8', 'Gray',   NULL,          false, 0),
    (1, 'RUNNING',       '#22c55e', 'Green',  NULL,          true,  1),
    (2, 'BREAKDOWN',     '#ef4444', 'Red',    'breakdown',   false, 2),
    (3, 'QUALITY ISSUE', '#f97316', 'Orange', 'quality',     false, 3),
    (4, 'SETUP',         '#3b82f6', 'Blue',   'setup',       false, 4),
    (5, 'MATERIAL WAIT', '#eab308', 'Yellow', 'material',    false, 5),
    (6, 'OTHERS',        '#a855f7', 'Purple', 'others',      false, 6),
    (7, 'CHANGE OVER',   '#06b6d4', 'Cyan',   'change_over', false, 7)
ON CONFLICT (status_code) DO NOTHING;

-- Mail-config seed rows — admin UI fills the 'value' column.
INSERT INTO mes_mail_config (key, description) VALUES
    ('bypass_to',  'Poka-Yoke Bypass alerts — To addresses (comma-separated)'),
    ('bypass_cc',  'Poka-Yoke Bypass alerts — Cc addresses'),
    ('health_to',  'Sensor Health Fail alerts — To addresses'),
    ('health_cc',  'Sensor Health Fail alerts — Cc addresses'),
    ('hourly_to',  'Hourly slot report — To addresses'),
    ('hourly_cc',  'Hourly slot report — Cc addresses')
ON CONFLICT (key) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- VERIFICATION — run after applying schema
-- ════════════════════════════════════════════════════════════════════════════
SELECT 'mes_plants',                  COUNT(*) FROM mes_plants                  UNION ALL
SELECT 'mes_zones',                   COUNT(*) FROM mes_zones                   UNION ALL
SELECT 'mes_lines',                   COUNT(*) FROM mes_lines                   UNION ALL
SELECT 'mes_plc_configs',             COUNT(*) FROM mes_plc_configs             UNION ALL
SELECT 'mes_machine_monitor_configs', COUNT(*) FROM mes_machine_monitor_configs UNION ALL
SELECT 'mes_global_status',           COUNT(*) FROM mes_global_status           UNION ALL
SELECT 'mes_status_mappings',         COUNT(*) FROM mes_status_mappings         UNION ALL
SELECT 'mes_model_mappings',          COUNT(*) FROM mes_model_mappings          UNION ALL
SELECT 'mes_shift_configs',           COUNT(*) FROM mes_shift_configs           UNION ALL
SELECT 'mes_hourly_slots',            COUNT(*) FROM mes_hourly_slots            UNION ALL
SELECT 'mes_break_configs',           COUNT(*) FROM mes_break_configs           UNION ALL
SELECT 'mes_non_production_days',     COUNT(*) FROM mes_non_production_days     UNION ALL
SELECT 'mes_py_series',               COUNT(*) FROM mes_py_series               UNION ALL
SELECT 'mes_py_model_master',         COUNT(*) FROM mes_py_model_master         UNION ALL
SELECT 'mes_py_master',               COUNT(*) FROM mes_py_master               UNION ALL
SELECT 'mes_py_assignments',          COUNT(*) FROM mes_py_assignments          UNION ALL
SELECT 'mes_py_sensor_mapping',       COUNT(*) FROM mes_py_sensor_mapping       UNION ALL
SELECT 'mes_py_plc_actuals',          COUNT(*) FROM mes_py_plc_actuals          UNION ALL
SELECT 'mes_py_model_columns',        COUNT(*) FROM mes_py_model_columns        UNION ALL
SELECT 'mes_poka_yoke_rules',         COUNT(*) FROM mes_poka_yoke_rules         UNION ALL
SELECT 'mes_poka_yoke_events',        COUNT(*) FROM mes_poka_yoke_events        UNION ALL
SELECT 'mes_mail_config',             COUNT(*) FROM mes_mail_config             UNION ALL
SELECT 'mes_sensor_ack_requests',     COUNT(*) FROM mes_sensor_ack_requests     UNION ALL
SELECT 'mes_submachine_ct_log',       COUNT(*) FROM mes_submachine_ct_log       UNION ALL
SELECT 'mes_admin',                   COUNT(*) FROM mes_admin                   UNION ALL
SELECT 'mes_operator_lines',          COUNT(*) FROM mes_operator_lines          UNION ALL
SELECT 'mes_audit_log',               COUNT(*) FROM mes_audit_log               UNION ALL
SELECT 'mes_status_log',              COUNT(*) FROM mes_status_log;
