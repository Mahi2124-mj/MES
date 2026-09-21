-- ============================================================
-- MIGRATION: NPD shift-level and hourly-slot support
-- Run on PostgreSQL after migration_non_production_days.sql
-- ============================================================

-- Add shift_name and hourly_slots columns
ALTER TABLE mes_non_production_days
  ADD COLUMN IF NOT EXISTS shift_name   VARCHAR(10),
  ADD COLUMN IF NOT EXISTS hourly_slots TEXT[];

-- Drop old day-level unique constraint (line_id, date)
ALTER TABLE mes_non_production_days
  DROP CONSTRAINT IF EXISTS mes_non_production_days_line_id_date_key;

-- Partial unique index for whole-day NPD (shift_name IS NULL)
CREATE UNIQUE INDEX IF NOT EXISTS idx_npd_day_unique
  ON mes_non_production_days(line_id, date)
  WHERE shift_name IS NULL;

-- Partial unique index for shift-level NPD
CREATE UNIQUE INDEX IF NOT EXISTS idx_npd_shift_unique
  ON mes_non_production_days(line_id, date, shift_name)
  WHERE shift_name IS NOT NULL;

-- Verify
SELECT id, line_id, date, shift_name, hourly_slots, reason
FROM mes_non_production_days
ORDER BY date DESC, line_id
LIMIT 10;
