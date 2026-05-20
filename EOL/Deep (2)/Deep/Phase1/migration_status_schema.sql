-- ============================================================
-- MIGRATION: Global Status Color Schema
-- Run this in pgAdmin on energydb
-- ============================================================

-- 1. Global status schema table (one row per status, ALL lines use same colors)
CREATE TABLE IF NOT EXISTS mes_global_status (
    id           SERIAL PRIMARY KEY,
    status_code  INTEGER     UNIQUE NOT NULL,
    status_name  VARCHAR(50) NOT NULL,
    color_hex    VARCHAR(10) NOT NULL,   -- e.g. "#22c55e"
    color_label  VARCHAR(30) NOT NULL,   -- e.g. "Green"
    loss_type    VARCHAR(30),            -- breakdown/quality/setup/material/others/change_over/speed
    is_production BOOLEAN DEFAULT false, -- true = machine actually making parts
    description  VARCHAR(200),
    sort_order   INTEGER DEFAULT 0,
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- 2. Seed default statuses (fixed, uniform across ALL lines)
INSERT INTO mes_global_status
    (status_code, status_name, color_hex, color_label, loss_type, is_production, description, sort_order)
VALUES
    (0, 'IDLE',          '#94a3b8', 'Gray',   NULL,          false, 'Machine powered on but not running',         0),
    (1, 'RUNNING',       '#22c55e', 'Green',  NULL,          true,  'Machine actively producing parts',            1),
    (2, 'BREAKDOWN',     '#ef4444', 'Red',    'breakdown',   false, 'Machine failure — maintenance required',      2),
    (3, 'QUALITY ISSUE', '#f97316', 'Orange', 'quality',     false, 'Quality defect being investigated',          3),
    (4, 'SETUP',         '#3b82f6', 'Blue',   'setup',       false, 'Machine being set up for production',        4),
    (5, 'MATERIAL WAIT', '#eab308', 'Yellow', 'material',    false, 'Waiting for raw material or components',     5),
    (6, 'OTHERS',        '#a855f7', 'Purple', 'others',      false, 'Other unclassified stoppage',                6),
    (7, 'CHANGE OVER',   '#06b6d4', 'Cyan',   'change_over', false, 'Changing from one model/product to another', 7)
ON CONFLICT (status_code) DO NOTHING;

-- 3. Model number uniqueness — already exists in schema.sql as UNIQUE(line_id, model_number)
--    This just verifies it exists, safe to run again
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'mes_model_mappings_line_id_model_number_key'
    ) THEN
        ALTER TABLE mes_model_mappings
        ADD CONSTRAINT mes_model_mappings_line_id_model_number_key
        UNIQUE (line_id, model_number);
        RAISE NOTICE 'Unique constraint added on mes_model_mappings';
    ELSE
        RAISE NOTICE 'Unique constraint already exists — OK';
    END IF;
END $$;

-- 4. Verify
SELECT
    status_code,
    status_name,
    color_hex,
    color_label,
    COALESCE(loss_type, 'none') AS loss_type,
    is_production
FROM mes_global_status
ORDER BY sort_order;
