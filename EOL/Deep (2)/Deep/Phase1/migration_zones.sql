-- ============================================================
-- MIGRATION: Add Zones support
-- Run this in pgAdmin on energydb
-- ============================================================

-- 1. Create zones table
CREATE TABLE IF NOT EXISTS mes_zones (
    id          SERIAL PRIMARY KEY,
    plant_id    INTEGER      REFERENCES mes_plants(id) ON DELETE CASCADE,
    zone_code   VARCHAR(30)  NOT NULL,       -- e.g. "ZONE-1", "BODY-SHOP"
    zone_name   VARCHAR(100) NOT NULL,       -- e.g. "Zone 1 - Seat Slider"
    description VARCHAR(200),
    is_active   BOOLEAN      DEFAULT true,
    created_at  TIMESTAMP    DEFAULT NOW(),
    updated_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE(plant_id, zone_code)
);

-- 2. Add zone_id to mes_lines (nullable — lines can exist without a zone)
ALTER TABLE mes_lines
    ADD COLUMN IF NOT EXISTS zone_id INTEGER REFERENCES mes_zones(id) ON DELETE SET NULL;

-- 3. Index for fast zone filtering
CREATE INDEX IF NOT EXISTS idx_mes_lines_zone_id ON mes_lines(zone_id);

-- 4. Add zone_id to audit log context
-- (no schema change needed — details column is free text)

-- 5. Verify
SELECT
    z.zone_code,
    z.zone_name,
    COUNT(l.id) AS line_count
FROM mes_zones z
LEFT JOIN mes_lines l ON l.zone_id = z.id
GROUP BY z.id, z.zone_code, z.zone_name
ORDER BY z.zone_code;
