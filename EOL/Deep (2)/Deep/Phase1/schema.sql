-- ============================================================
-- MES PLATFORM — MASTER SCHEMA
-- Toyota Boshoku | Multi-plant, Multi-line
-- ============================================================
-- Run this ONCE on PostgreSQL to create all master tables.
-- Existing tables (ync_dashboard_complete etc.) are untouched.
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================
-- 1. PLANTS
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_plants (
    id           SERIAL PRIMARY KEY,
    plant_code   VARCHAR(20)  UNIQUE NOT NULL,   -- e.g. "TBI-BHW"
    plant_name   VARCHAR(100) NOT NULL,           -- e.g. "Toyota Boshoku Bawal"
    location     VARCHAR(200),
    timezone     VARCHAR(50)  DEFAULT 'Asia/Kolkata',
    is_active    BOOLEAN      DEFAULT true,
    created_at   TIMESTAMP    DEFAULT NOW(),
    updated_at   TIMESTAMP    DEFAULT NOW()
);


-- ============================================================
-- 2. PRODUCTION LINES
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_lines (
    id              SERIAL PRIMARY KEY,
    plant_id        INTEGER      REFERENCES mes_plants(id) ON DELETE CASCADE,
    line_code       VARCHAR(30)  NOT NULL,          -- e.g. "YNC-L1"
    line_name       VARCHAR(100) NOT NULL,           -- e.g. "YNC Seat Slider Line 1"
    description     VARCHAR(200),
    is_active       BOOLEAN      DEFAULT true,
    db_table_name   VARCHAR(100),                   -- e.g. "ync_dashboard_complete"
    collector_pid   INTEGER,                        -- running collector process id
    collector_status VARCHAR(20) DEFAULT 'stopped', -- stopped/running/error
    created_at      TIMESTAMP    DEFAULT NOW(),
    updated_at      TIMESTAMP    DEFAULT NOW(),
    UNIQUE(plant_id, line_code)
);


-- ============================================================
-- 3. PLC CONFIGURATION (per line)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_plc_configs (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE UNIQUE,
    plc_ip          VARCHAR(50)  NOT NULL,           -- e.g. "192.168.10.150"
    plc_port        INTEGER      DEFAULT 5002,
    protocol        VARCHAR(20)  DEFAULT 'MC4E',    -- MC4E / MC3E / SLMP

    -- Production signals
    ok_bit_address  VARCHAR(20)  DEFAULT 'L108',    -- OK pulse bit
    ng_bit_address  VARCHAR(20)  DEFAULT 'L109',    -- NG pulse bit
    status_address  VARCHAR(20)  DEFAULT 'D6005',   -- Machine status word
    model_address   VARCHAR(20)  DEFAULT 'D6048',   -- Current model word

    -- Poka Yoke signal addresses (NULL = not used)
    sensor_ok_address    VARCHAR(20),   -- Sensor confirmation bit (before OK pulse)
    process_seq_address  VARCHAR(20),   -- Process sequence word
    override_address     VARCHAR(20),   -- Manual override bit

    -- Expected cycle
    ideal_cycle_time     NUMERIC(6,2)  DEFAULT 15.0,   -- seconds
    max_allowed_cycle    NUMERIC(6,2)  DEFAULT 16.0,
    ok_ng_pulse_min_gap  NUMERIC(4,2)  DEFAULT 0.5,    -- debounce seconds

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);


-- ============================================================
-- 4. STATUS CODE MAPPING (per line — can differ by machine)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_status_mappings (
    id          SERIAL PRIMARY KEY,
    line_id     INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE,
    status_code INTEGER      NOT NULL,
    status_name VARCHAR(50)  NOT NULL,   -- IDLE/RUNNING/BREAKDOWN/etc.
    loss_type   VARCHAR(30),             -- breakdown/quality/setup/material/others/change_over/speed
    UNIQUE(line_id, status_code)
);


-- ============================================================
-- 5. MODEL MAPPING (per line)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_model_mappings (
    id           SERIAL PRIMARY KEY,
    line_id      INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    model_number INTEGER     NOT NULL,
    model_name   VARCHAR(100) NOT NULL,
    UNIQUE(line_id, model_number)
);


-- ============================================================
-- 6. SHIFT CONFIGURATION (per line)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_shift_configs (
    id               SERIAL PRIMARY KEY,
    line_id          INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    shift_name       VARCHAR(10) NOT NULL,        -- A / B / GAP_AB / GAP_BA
    start_time       TIME        NOT NULL,
    end_time         TIME        NOT NULL,
    crosses_midnight BOOLEAN     DEFAULT false,   -- B shift crosses midnight
    total_plan       INTEGER     DEFAULT 0,       -- pieces per shift
    working_minutes  INTEGER     DEFAULT 0,
    startup_delay_min INTEGER    DEFAULT 5,       -- first slot startup delay
    is_production    BOOLEAN     DEFAULT true,    -- false for GAP periods
    UNIQUE(line_id, shift_name)
);


-- ============================================================
-- 7. HOURLY SLOT CONFIGURATION (per line per shift)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_hourly_slots (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    shift_name      VARCHAR(10) NOT NULL,
    slot_label      VARCHAR(20) NOT NULL,           -- "08:30-09:30"
    start_time      TIME        NOT NULL,
    end_time        TIME        NOT NULL,
    crosses_midnight BOOLEAN    DEFAULT false,
    working_minutes INTEGER     NOT NULL,
    plan_pieces     INTEGER     NOT NULL,           -- static plan for this slot
    db_column_prefix VARCHAR(40),                   -- "hour_0830_0930"
    slot_order      INTEGER     DEFAULT 0,
    UNIQUE(line_id, shift_name, slot_label)
);


-- ============================================================
-- 8. BREAK SCHEDULE (per line)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_break_configs (
    id           SERIAL PRIMARY KEY,
    line_id      INTEGER     REFERENCES mes_lines(id) ON DELETE CASCADE,
    break_name   VARCHAR(50) NOT NULL,
    start_time   TIME        NOT NULL,
    end_time     TIME        NOT NULL,
    crosses_midnight BOOLEAN DEFAULT false,
    applies_to_shifts VARCHAR(50) DEFAULT 'A,B',   -- comma-separated shift names
    UNIQUE(line_id, break_name)
);


-- ============================================================
-- 9. POKA YOKE RULES (per line)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_poka_yoke_rules (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER      REFERENCES mes_lines(id) ON DELETE CASCADE,
    rule_name       VARCHAR(100) NOT NULL,
    rule_type       VARCHAR(30)  NOT NULL,
    -- SENSOR_BYPASS  → OK pulse without sensor_ok_address signal
    -- PROCESS_SKIP   → process_seq_address jumped a step
    -- MANUAL_OVERRIDE→ override_address bit high during production
    -- CONSECUTIVE_NG → N consecutive NG pulses
    -- CYCLE_TOO_FAST → cycle_time < min_cycle_time
    -- CYCLE_TOO_SLOW → cycle_time > max_cycle_time (already in speed loss)

    -- Detection parameters (JSON-like individual columns for clarity)
    plc_address     VARCHAR(20),    -- which PLC address to watch
    expected_value  VARCHAR(50),    -- what value is normal
    trigger_value   VARCHAR(50),    -- what value triggers alert
    threshold_count INTEGER DEFAULT 1,   -- for consecutive checks
    window_seconds  INTEGER DEFAULT 60,  -- time window for pattern

    alert_level     VARCHAR(10) DEFAULT 'WARNING',  -- WARNING / CRITICAL
    alert_message   VARCHAR(200),
    auto_stop_line  BOOLEAN DEFAULT false,           -- halt production on trigger
    is_active       BOOLEAN DEFAULT true,

    created_at  TIMESTAMP DEFAULT NOW()
);


-- ============================================================
-- 10. POKA YOKE EVENTS LOG (auto-populated by collector)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_poka_yoke_events (
    id              SERIAL PRIMARY KEY,
    line_id         INTEGER     REFERENCES mes_lines(id),
    rule_id         INTEGER     REFERENCES mes_poka_yoke_rules(id),
    rule_type       VARCHAR(30),
    alert_level     VARCHAR(10),
    detected_at     TIMESTAMP   DEFAULT NOW(),
    shift_name      VARCHAR(10),
    plc_value       VARCHAR(50),    -- actual value seen
    context_json    TEXT,           -- JSON blob with extra context
    acknowledged    BOOLEAN DEFAULT false,
    acknowledged_at TIMESTAMP,
    acknowledged_by VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_pke_line_time ON mes_poka_yoke_events(line_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_pke_ack ON mes_poka_yoke_events(acknowledged) WHERE acknowledged = false;


-- ============================================================
-- 11. ADMIN CREDENTIALS (with role support)
-- ============================================================
-- Add role column if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'mes_admin' AND column_name = 'role') THEN
        ALTER TABLE mes_admin ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS mes_admin (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50)  UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,           -- bcrypt hash
    role          VARCHAR(20)  NOT NULL DEFAULT 'admin',  -- 'admin', 'department', 'operator'
    last_login    TIMESTAMP,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- Default admin: username=admin, password=admin@123
INSERT INTO mes_admin (username, password_hash, role)
VALUES (
    'admin',
    crypt('admin@123', gen_salt('bf', 12)),
    'admin'
)
ON CONFLICT (username) DO UPDATE SET role = EXCLUDED.role;


-- ============================================================
-- 12. OPERATOR-LINE ASSIGNMENTS (for role=operator)
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_operator_lines (
    id         SERIAL PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES mes_admin(id) ON DELETE CASCADE,
    line_id    INTEGER NOT NULL REFERENCES mes_lines(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(admin_id, line_id)
);
CREATE INDEX IF NOT EXISTS idx_operator_lines_admin ON mes_operator_lines(admin_id);
CREATE INDEX IF NOT EXISTS idx_operator_lines_line ON mes_operator_lines(line_id);


-- ============================================================
-- 13. SYSTEM AUDIT LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS mes_audit_log (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(100) NOT NULL,    -- "LINE_ADDED", "CONFIG_CHANGED", etc.
    entity_type VARCHAR(50),              -- "line", "plant", "plc_config"
    entity_id   INTEGER,
    details     TEXT,                     -- JSON details
    ip_address  VARCHAR(50),
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON mes_audit_log(created_at DESC);


-- ============================================================
-- SEED DATA — existing YNC line
-- ============================================================

-- Plant
INSERT INTO mes_plants (plant_code, plant_name, location)
VALUES ('TBI-BHW', 'Toyota Boshoku Device', 'Bawal, Haryana')
ON CONFLICT (plant_code) DO NOTHING;

-- Line
INSERT INTO mes_lines (plant_id, line_code, line_name, db_table_name, collector_status)
VALUES (
    (SELECT id FROM mes_plants WHERE plant_code = 'TBI-BHW'),
    'YNC-L1',
    'YNC Seat Slider Line 1',
    'ync_dashboard_complete',
    'running'
)
ON CONFLICT (plant_id, line_code) DO NOTHING;

-- PLC config for YNC line
INSERT INTO mes_plc_configs (line_id, plc_ip, plc_port, ok_bit_address, ng_bit_address,
                              status_address, model_address, ideal_cycle_time, max_allowed_cycle)
VALUES (
    (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1'),
    '192.168.10.150', 5002, 'L108', 'L109', 'D6005', 'D6048', 15.0, 16.0
)
ON CONFLICT (line_id) DO NOTHING;

-- Status mappings for YNC line
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_status_mappings (line_id, status_code, status_name, loss_type) VALUES
        (v_line_id, 0, 'IDLE',          NULL),
        (v_line_id, 1, 'RUNNING',       NULL),
        (v_line_id, 2, 'BREAKDOWN',     'breakdown'),
        (v_line_id, 3, 'QUALITY_ISSUE', 'quality'),
        (v_line_id, 4, 'MODEL_SETUP',   'setup'),
        (v_line_id, 5, 'MATERIAL_WAIT', 'material'),
        (v_line_id, 6, 'OTHER_LOSS',    'others'),
        (v_line_id, 7, 'CHANGE_OVER',   'change_over')
    ON CONFLICT (line_id, status_code) DO NOTHING;
END $$;

-- Shift configs for YNC line
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_shift_configs
        (line_id, shift_name, start_time, end_time, crosses_midnight,
         total_plan, working_minutes, startup_delay_min, is_production)
    VALUES
        (v_line_id, 'A',      '08:30', '17:15', false, 1860, 465, 5, true),
        (v_line_id, 'B',      '18:30', '03:15', true,  1860, 465, 5, true),
        (v_line_id, 'GAP_AB', '17:15', '18:30', false, 0,    0,   0, false),
        (v_line_id, 'GAP_BA', '03:15', '08:30', false, 0,    0,   0, false)
    ON CONFLICT (line_id, shift_name) DO NOTHING;
END $$;

-- Break configs for YNC line
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_break_configs (line_id, break_name, start_time, end_time, applies_to_shifts)
    VALUES
        (v_line_id, 'Morning Tea Break',    '10:00', '10:10', 'A'),
        (v_line_id, 'Lunch Break',          '12:00', '12:35', 'A'),
        (v_line_id, 'Evening Tea Break',    '14:30', '14:40', 'A'),
        (v_line_id, 'Dinner Break 1',       '18:00', '18:10', 'B'),
        (v_line_id, 'Tea Break',            '20:00', '20:10', 'B'),
        (v_line_id, 'Dinner Break 2',       '22:00', '22:35', 'B'),
        (v_line_id, 'Night Tea Break',      '01:00', '01:10', 'B'),
        (v_line_id, 'Early Morning Break',  '04:00', '04:10', 'B')
    ON CONFLICT (line_id, break_name) DO NOTHING;
END $$;

-- Hourly slots A shift
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_hourly_slots
        (line_id, shift_name, slot_label, start_time, end_time,
         working_minutes, plan_pieces, db_column_prefix, slot_order)
    VALUES
        (v_line_id,'A','08:30-09:30','08:30','09:30', 55, 220,'hour_0830_0930',1),
        (v_line_id,'A','09:30-10:30','09:30','10:30', 50, 200,'hour_0930_1030',2),
        (v_line_id,'A','10:30-11:30','10:30','11:30', 60, 240,'hour_1030_1130',3),
        (v_line_id,'A','11:30-13:05','11:30','13:05', 60, 240,'hour_1130_1305',4),
        (v_line_id,'A','13:05-14:05','13:05','14:05', 60, 240,'hour_1305_1405',5),
        (v_line_id,'A','14:05-15:05','14:05','15:05', 50, 200,'hour_1405_1505',6),
        (v_line_id,'A','15:05-16:05','15:05','16:05', 60, 240,'hour_1505_1605',7),
        (v_line_id,'A','16:05-17:15','16:05','17:15', 70, 280,'hour_1605_1715',8)
    ON CONFLICT (line_id, shift_name, slot_label) DO NOTHING;
END $$;

-- Hourly slots B shift
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_hourly_slots
        (line_id, shift_name, slot_label, start_time, end_time, crosses_midnight,
         working_minutes, plan_pieces, db_column_prefix, slot_order)
    VALUES
        (v_line_id,'B','18:30-19:30','18:30','19:30',false, 55,220,'hour_1830_1930',1),
        (v_line_id,'B','19:30-20:30','19:30','20:30',false, 50,200,'hour_1930_2030',2),
        (v_line_id,'B','20:30-21:30','20:30','21:30',false, 60,240,'hour_2030_2130',3),
        (v_line_id,'B','21:30-23:05','21:30','23:05',false, 60,240,'hour_2130_2305',4),
        (v_line_id,'B','23:05-00:05','23:05','00:05',true,  60,240,'hour_2305_0005',5),
        (v_line_id,'B','00:05-01:05','00:05','01:05',false, 55,220,'hour_0005_0105',6),
        (v_line_id,'B','01:05-02:05','01:05','02:05',false, 55,220,'hour_0105_0205',7),
        (v_line_id,'B','02:05-03:15','02:05','03:15',false, 70,280,'hour_0205_0315',8)
    ON CONFLICT (line_id, shift_name, slot_label) DO NOTHING;
END $$;

-- Model mappings for YNC line
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_model_mappings (line_id, model_number, model_name) VALUES
        (v_line_id,  1, '4WAY OUTER'),
        (v_line_id,  2, '4WAY INR RH'),
        (v_line_id,  3, '4WAY INR LH'),
        (v_line_id,  4, 'YRA 6WAY OTR RH'),
        (v_line_id,  5, 'YRA 6WAY INR RH'),
        (v_line_id,  6, 'YRA 6WAY OTR LH'),
        (v_line_id,  7, 'YRA 6WAY INR LH'),
        (v_line_id,  8, 'YJC 6WAY INR RH'),
        (v_line_id,  9, 'YHB/YNC/YCA 4WAY OTR'),
        (v_line_id, 10, 'YHB/YCA 4WAY INR RH'),
        (v_line_id, 11, 'YHB 4WAY INR LH'),
        (v_line_id, 12, 'YCA 4WAY INR LH'),
        (v_line_id, 13, 'YJC 4WAY INR LH'),
        (v_line_id, 14, 'YNC 4WAY INR RH'),
        (v_line_id, 15, 'YNC 4WAY INR LH'),
        (v_line_id, 16, 'YNC 4WAY INR W/O H'),
        (v_line_id, 17, 'YNC 6WAY INR RH'),
        (v_line_id, 18, 'YNC 6WAY OTR RH'),
        (v_line_id, 19, 'YTB INR LH EXPORT'),
        (v_line_id, 20, 'YY8 4WAY INR RH'),
        (v_line_id, 21, 'YY8 4WAY INR LH')
    ON CONFLICT (line_id, model_number) DO NOTHING;
END $$;

-- Poka Yoke rules for YNC line
DO $$
DECLARE v_line_id INTEGER := (SELECT id FROM mes_lines WHERE line_code = 'YNC-L1');
BEGIN
    INSERT INTO mes_poka_yoke_rules
        (line_id, rule_name, rule_type, plc_address, threshold_count,
         window_seconds, alert_level, alert_message, auto_stop_line)
    VALUES
        (v_line_id,
         'Sensor bypass detection',
         'SENSOR_BYPASS',
         'L108',       -- OK bit
         1, 5,
         'CRITICAL',
         'OK pulse received without sensor confirmation signal — possible sensor bypass',
         false),

        (v_line_id,
         'Manual override active',
         'MANUAL_OVERRIDE',
         'D6005',      -- status word
         1, 0,
         'CRITICAL',
         'Machine running in manual override mode — process integrity not guaranteed',
         false),

        (v_line_id,
         'Consecutive NG alert',
         'CONSECUTIVE_NG',
         'L109',       -- NG bit
         3, 60,
         'WARNING',
         '3 consecutive NG parts detected — check tooling and material',
         false),

        (v_line_id,
         'Cycle too fast',
         'CYCLE_TOO_FAST',
         'L108',
         1, 0,
         'WARNING',
         'Cycle time below minimum — part may not be fully processed',
         false),

        (v_line_id,
         'Process step skipped',
         'PROCESS_SKIP',
         'D6005',
         1, 10,
         'CRITICAL',
         'Expected process sequence not followed — step may have been skipped',
         false)
    ON CONFLICT DO NOTHING;
END $$;


-- ============================================================
-- SAMPLE USERS (optional)
-- ============================================================

-- Department user (read‑only)
INSERT INTO mes_admin (username, password_hash, role)
VALUES (
    'department',
    crypt('dept@123', gen_salt('bf', 12)),
    'department'
)
ON CONFLICT (username) DO NOTHING;

-- Sample operator user (to be assigned to a line via admin panel)
INSERT INTO mes_admin (username, password_hash, role)
VALUES (
    'operator1',
    crypt('op@123', gen_salt('bf', 12)),
    'operator'
)
ON CONFLICT (username) DO NOTHING;

-- Assign operator1 to YNC-L1 (you need to manually run this after creating the line)
-- INSERT INTO mes_operator_lines (admin_id, line_id)
-- SELECT a.id, l.id FROM mes_admin a, mes_lines l
-- WHERE a.username = 'operator1' AND l.line_code = 'YNC-L1'
-- ON CONFLICT DO NOTHING;


-- ============================================================
-- VERIFICATION QUERY — run after applying schema
-- ============================================================
SELECT
    'mes_plants'           AS tbl, COUNT(*) AS rows FROM mes_plants       UNION ALL
SELECT 'mes_lines',                                   COUNT(*) FROM mes_lines         UNION ALL
SELECT 'mes_plc_configs',                             COUNT(*) FROM mes_plc_configs   UNION ALL
SELECT 'mes_status_mappings',                         COUNT(*) FROM mes_status_mappings UNION ALL
SELECT 'mes_model_mappings',                          COUNT(*) FROM mes_model_mappings UNION ALL
SELECT 'mes_shift_configs',                           COUNT(*) FROM mes_shift_configs UNION ALL
SELECT 'mes_hourly_slots',                            COUNT(*) FROM mes_hourly_slots  UNION ALL
SELECT 'mes_break_configs',                           COUNT(*) FROM mes_break_configs UNION ALL
SELECT 'mes_poka_yoke_rules',                         COUNT(*) FROM mes_poka_yoke_rules UNION ALL
SELECT 'mes_poka_yoke_events',                        COUNT(*) FROM mes_poka_yoke_events UNION ALL
SELECT 'mes_admin',                                   COUNT(*) FROM mes_admin         UNION ALL
SELECT 'mes_operator_lines',                          COUNT(*) FROM mes_operator_lines UNION ALL
SELECT 'mes_audit_log',                               COUNT(*) FROM mes_audit_log;
-- Add role column to mes_admin if it doesn't exist
ALTER TABLE mes_admin ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'admin';

-- Update existing admin user(s) to have role 'admin' (if any are missing)
UPDATE mes_admin SET role = 'admin' WHERE role IS NULL;

-- Create operator-line assignment table if not exists
CREATE TABLE IF NOT EXISTS mes_operator_lines (
    id         SERIAL PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES mes_admin(id) ON DELETE CASCADE,
    line_id    INTEGER NOT NULL REFERENCES mes_lines(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(admin_id, line_id)
);