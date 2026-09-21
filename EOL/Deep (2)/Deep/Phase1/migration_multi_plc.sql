-- ============================================================
-- MIGRATION: Multi-Machine per Line + Planning + Dashboard PLC
-- ============================================================

-- 1. Allow multiple PLC configs per line (drop UNIQUE on line_id)
ALTER TABLE mes_plc_configs DROP CONSTRAINT IF EXISTS mes_plc_configs_line_id_key;

-- 2. Add machine_name label to identify each PLC
ALTER TABLE mes_plc_configs
  ADD COLUMN IF NOT EXISTS machine_name VARCHAR(100) DEFAULT 'Main PLC';

-- 3. Add planning + dashboard fields to mes_lines
ALTER TABLE mes_lines
  ADD COLUMN IF NOT EXISTS ideal_cycle_time  NUMERIC(6,2) DEFAULT 15.0,
  ADD COLUMN IF NOT EXISTS dashboard_plc_id  INTEGER REFERENCES mes_plc_configs(id) ON DELETE SET NULL;

-- 4. Back-fill machine_name for existing rows
UPDATE mes_plc_configs SET machine_name = 'Main PLC' WHERE machine_name IS NULL OR machine_name = '';

-- 5. Set dashboard_plc_id = their existing single PLC (back-fill)
UPDATE mes_lines l
SET dashboard_plc_id = (
    SELECT id FROM mes_plc_configs WHERE line_id = l.id ORDER BY id LIMIT 1
)
WHERE dashboard_plc_id IS NULL;

-- 6. Back-fill ideal_cycle_time from existing PLC config
UPDATE mes_lines l
SET ideal_cycle_time = COALESCE(
    (SELECT ideal_cycle_time FROM mes_plc_configs WHERE line_id = l.id ORDER BY id LIMIT 1),
    15.0
)
WHERE ideal_cycle_time IS NULL OR ideal_cycle_time = 15.0;

-- Verify
SELECT id, line_code, ideal_cycle_time, dashboard_plc_id FROM mes_lines ORDER BY line_code;
SELECT id, line_id, machine_name, plc_ip FROM mes_plc_configs ORDER BY line_id, id;
