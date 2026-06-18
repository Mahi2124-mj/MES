-- 1. Delete duplicate (cycle_seq, record_date) rows from ct_log,
--    keeping the LATEST row per cycle_seq (by id DESC).
WITH ranked AS (
    SELECT id, cycle_seq,
           ROW_NUMBER() OVER (
               PARTITION BY cycle_seq, record_date
               ORDER BY id DESC
           ) AS rn
    FROM ync_dashboard_complete_ct_log
    WHERE record_date = CURRENT_DATE
),
deleted AS (
    DELETE FROM ync_dashboard_complete_ct_log
    WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    RETURNING 1
)
SELECT COUNT(*) AS ct_log_duplicates_deleted FROM deleted;

-- 2. Same for L6 audit (in case there are any).
WITH ranked AS (
    SELECT id, ts, bit_type, counter_val,
           ROW_NUMBER() OVER (
               PARTITION BY ts, bit_type
               ORDER BY id DESC
           ) AS rn
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
),
deleted AS (
    DELETE FROM mes_l6_final_inspection
    WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    RETURNING 1
)
SELECT COUNT(*) AS l6_duplicates_deleted FROM deleted;

-- 3. Re-sync the dashboard counters to match cleaned L6 audit (true raw
--    pulse count per shift).
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
  AND (
        d.shift_name = c.shift_name
        OR (c.shift_name = 'GAP' AND d.shift_name LIKE 'GAP_%')
      );

-- Show post-sync state
SELECT id, shift_name, ok_count, ng_count, ok_count + ng_count AS actual
FROM ync_dashboard_complete
WHERE record_date = CURRENT_DATE
ORDER BY shift_name;
