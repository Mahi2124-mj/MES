-- Dedupe rows that two collectors wrote in parallel (10:40 — 11:43).
-- Pair them on bit_type + ts within 250ms; keep the one with valid
-- part_code (prefer non-null), break ties by latest ts.
WITH ranked AS (
  SELECT id, ts, bit_type, part_code,
         ROW_NUMBER() OVER (
           PARTITION BY bit_type,
                        FLOOR(EXTRACT(EPOCH FROM ts) * 4)
           ORDER BY (part_code IS NULL), ts DESC
         ) AS rn
    FROM mes_l6_final_inspection
   WHERE record_date = CURRENT_DATE
)
DELETE FROM mes_l6_final_inspection
 WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
RETURNING bit_type, ts, part_code;
