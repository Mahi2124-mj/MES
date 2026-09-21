-- Delete dummy rows (NULL part_code NG OR ct > 60s OR ct = 0)
-- from BOTH tables for today.
BEGIN;

WITH del AS (
    DELETE FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
      AND (
            ct_seconds IS NULL
         OR ct_seconds > 60
         OR ct_seconds = 0
         OR (bit_type = 'NG' AND COALESCE(part_code, '') = '')
      )
    RETURNING ts, bit_type
)
SELECT 'L6_audit' AS tbl, COUNT(*) AS deleted FROM del;

WITH del AS (
    DELETE FROM ync_dashboard_complete_ct_log
    WHERE record_date = CURRENT_DATE
      AND (
            ct_value IS NULL
         OR ct_value > 60
         OR ct_value = 0
         OR (is_ng = true AND COALESCE(part_code, '') = '')
      )
    RETURNING ts, is_ng
)
SELECT 'ct_log' AS tbl, COUNT(*) AS deleted FROM del;

COMMIT;

-- Re-sync the shift counter to cleaned L6 audit
UPDATE ync_dashboard_complete d
SET ok_count = COALESCE(c.ok_cnt, 0),
    ng_count = COALESCE(c.ng_cnt, 0)
FROM (
    SELECT shift_name,
           COUNT(*) FILTER (WHERE bit_type='OK') AS ok_cnt,
           COUNT(*) FILTER (WHERE bit_type='NG') AS ng_cnt
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
    GROUP BY shift_name
) c
WHERE d.record_date = CURRENT_DATE
  AND (d.shift_name = c.shift_name
       OR (c.shift_name = 'GAP' AND d.shift_name LIKE 'GAP_%'));

-- Re-sync hour_0830_0930 slot too
UPDATE ync_dashboard_complete d
SET hour_0830_0930_ok = COALESCE(s.s1_ok, 0),
    hour_0830_0930_ng = COALESCE(s.s1_ng, 0)
FROM (
    SELECT shift_name,
           COUNT(*) FILTER (WHERE bit_type='OK' AND ts::time >= '08:30' AND ts::time < '09:30') AS s1_ok,
           COUNT(*) FILTER (WHERE bit_type='NG' AND ts::time >= '08:30' AND ts::time < '09:30') AS s1_ng
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
    GROUP BY shift_name
) s
WHERE d.record_date = CURRENT_DATE
  AND d.shift_name = s.shift_name;

-- Re-sync hour_0930_1030 slot
UPDATE ync_dashboard_complete d
SET hour_0930_1030_ok = COALESCE(s.s_ok, 0),
    hour_0930_1030_ng = COALESCE(s.s_ng, 0)
FROM (
    SELECT shift_name,
           COUNT(*) FILTER (WHERE bit_type='OK' AND ts::time >= '09:30' AND ts::time < '10:30') AS s_ok,
           COUNT(*) FILTER (WHERE bit_type='NG' AND ts::time >= '09:30' AND ts::time < '10:30') AS s_ng
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
    GROUP BY shift_name
) s
WHERE d.record_date = CURRENT_DATE
  AND d.shift_name = s.shift_name;

-- Final state
SELECT 'L6_audit' AS tbl, COUNT(*) AS rows,
       COUNT(*) FILTER (WHERE bit_type='NG') AS ng
FROM mes_l6_final_inspection WHERE record_date = CURRENT_DATE
UNION ALL
SELECT 'ct_log', COUNT(*), COUNT(*) FILTER (WHERE is_ng=true)
FROM ync_dashboard_complete_ct_log WHERE record_date = CURRENT_DATE;

SELECT shift_name, ok_count, ng_count,
       hour_0830_0930_ok AS s1_ok, hour_0830_0930_ng AS s1_ng,
       hour_0930_1030_ok AS s2_ok, hour_0930_1030_ng AS s2_ng
FROM ync_dashboard_complete WHERE record_date = CURRENT_DATE;
