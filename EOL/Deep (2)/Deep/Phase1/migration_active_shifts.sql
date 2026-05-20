-- ============================================================
-- MIGRATION: Active Shifts per Line
-- Run on PostgreSQL to add active_shifts column to mes_lines.
-- ============================================================

ALTER TABLE mes_lines
  ADD COLUMN IF NOT EXISTS active_shifts VARCHAR(50) DEFAULT 'A,B';

-- Back-fill existing rows
UPDATE mes_lines SET active_shifts = 'A,B' WHERE active_shifts IS NULL;

-- Verify
SELECT id, line_code, active_shifts FROM mes_lines ORDER BY line_code;
