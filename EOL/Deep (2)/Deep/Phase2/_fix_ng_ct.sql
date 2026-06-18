-- Fix NULL ct in NG rows of main ct_log using inter-NG delta
WITH ng_seq AS (
  SELECT id, ts,
    EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (ORDER BY ts))) AS delta
  FROM ync_dashboard_complete_ct_log
  WHERE record_date=CURRENT_DATE AND is_ng=true
)
UPDATE ync_dashboard_complete_ct_log t SET ct_value = COALESCE(n.delta, 0)
FROM ng_seq n WHERE t.id=n.id AND (t.ct_value IS NULL OR t.ct_value = 0);

-- Clamp huge cross-shift values to 0
UPDATE ync_dashboard_complete_ct_log SET ct_value = 0
WHERE record_date=CURRENT_DATE AND ct_value > 600;

-- Same for mes_l6_* tablesWITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_final_inspection WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_final_inspection t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_final_inspection SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_upper_rail WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_upper_rail t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_upper_rail SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_lower_rail WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_lower_rail t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_lower_rail SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_semi_auto WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_semi_auto t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_semi_auto SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_ball_guide_13 WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_ball_guide_13 t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_ball_guide_13 SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_ball_guide_14 WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_ball_guide_14 t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_ball_guide_14 SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;
WITH s AS (
  SELECT id, ts, EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (PARTITION BY bit_type ORDER BY ts))) AS d
  FROM mes_l6_lock_bar WHERE record_date=CURRENT_DATE
)
UPDATE mes_l6_lock_bar t SET ct_seconds = COALESCE(s.d, 0)
FROM s WHERE t.id=s.id AND t.ct_seconds IS NULL;

UPDATE mes_l6_lock_bar SET ct_seconds = 0 WHERE record_date=CURRENT_DATE AND ct_seconds > 600;

