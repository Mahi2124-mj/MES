-- Sync ync_dashboard_complete OK/NG counts to match cleaned
-- mes_l6_final_inspection (post-garbage-cleanup).  Drops the
-- bloated NG count down to real numbers so the dashboard stops
-- showing phantom NGs.
UPDATE ync_dashboard_complete d
SET ok_count = c.ok_cnt,
    ng_count = c.ng_cnt
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

SELECT id, shift_name, ok_count, ng_count
FROM ync_dashboard_complete
WHERE record_date = CURRENT_DATE
ORDER BY shift_name;
