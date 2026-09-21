-- ============================================================
-- MIGRATION: Zone Shifts OT Support
-- Run this on PostgreSQL to add overtime columns to shift configs
-- ============================================================

ALTER TABLE mes_shift_configs
  ADD COLUMN IF NOT EXISTS ot_enabled  BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS ot_end_time TIME,
  ADD COLUMN IF NOT EXISTS ot_notes    VARCHAR(200);

-- Verify
SELECT shift_name, start_time, end_time, ot_enabled, ot_end_time
FROM mes_shift_configs
ORDER BY line_id, shift_name;
