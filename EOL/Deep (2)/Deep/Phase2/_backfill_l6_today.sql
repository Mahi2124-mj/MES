-- Backfill missing rows in mes_l6_final_inspection from ync_dashboard_complete_ct_log
-- for today.  Insert any ct_log row whose ts has no matching L6 row.
-- counter_val auto-derived per (bit_type, shift, date) using MAX+1.

WITH missing AS (
    SELECT c.ts, c.is_ng, c.ct_value, c.part_code, c.shift_name, c.record_date,
           ROW_NUMBER() OVER (
               PARTITION BY c.is_ng, c.shift_name, c.record_date
               ORDER BY c.ts
           ) AS rn
    FROM ync_dashboard_complete_ct_log c
    WHERE c.record_date = CURRENT_DATE
      AND NOT EXISTS (
          SELECT 1 FROM mes_l6_final_inspection l
          WHERE l.ts = c.ts
      )
),
max_counters AS (
    SELECT bit_type, shift_name, record_date,
           COALESCE(MAX(counter_val), 0) AS max_cv
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
    GROUP BY bit_type, shift_name, record_date
),
to_insert AS (
    SELECT m.ts,
           CASE WHEN m.is_ng THEN 'NG' ELSE 'OK' END AS bit_type,
           CASE WHEN m.is_ng THEN 'L109' ELSE 'L108' END AS bit_address,
           m.ct_value AS ct_seconds,
           COALESCE(mc.max_cv, 0) + m.rn AS counter_val,
           m.part_code,
           m.shift_name,
           m.record_date
    FROM missing m
    LEFT JOIN max_counters mc
      ON mc.bit_type   = CASE WHEN m.is_ng THEN 'NG' ELSE 'OK' END
     AND mc.shift_name = m.shift_name
     AND mc.record_date = m.record_date
)
INSERT INTO mes_l6_final_inspection
    (ts, bit_type, bit_address, ct_seconds, counter_val,
     part_code, shift_name, record_date)
SELECT ts, bit_type, bit_address, ct_seconds, counter_val,
       part_code, shift_name, record_date
FROM to_insert
ORDER BY ts;

SELECT bit_type, COUNT(*) AS cnt FROM mes_l6_final_inspection
WHERE record_date = CURRENT_DATE
GROUP BY bit_type;
