-- ============================================================
-- MIGRATION: Non-Production Days
-- Run on PostgreSQL to create the NPD tracking table.
-- ============================================================

CREATE TABLE IF NOT EXISTS mes_non_production_days (
    id          SERIAL PRIMARY KEY,
    line_id     INTEGER      NOT NULL REFERENCES mes_lines(id) ON DELETE CASCADE,
    date        DATE         NOT NULL,
    reason      VARCHAR(200),
    created_by  VARCHAR(50),                   -- username who marked it
    created_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE(line_id, date)
);

CREATE INDEX IF NOT EXISTS idx_npd_line_date ON mes_non_production_days(line_id, date);
CREATE INDEX IF NOT EXISTS idx_npd_date      ON mes_non_production_days(date);

-- Verify
SELECT 'mes_non_production_days' AS tbl, COUNT(*) AS rows FROM mes_non_production_days;
