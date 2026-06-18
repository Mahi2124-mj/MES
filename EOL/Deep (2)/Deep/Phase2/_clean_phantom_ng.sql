WITH real_times AS (
  SELECT unnest(ARRAY['2026-05-26T02:37:14.146','2026-05-26T02:53:08.127','2026-05-26T11:18:23.580','2026-05-26T12:43:19.259','2026-05-26T12:45:47.627','2026-05-26T13:01:04.557','2026-05-26T13:37:25.964','2026-05-26T14:02:30.316','2026-05-26T14:23:01.664','2026-05-26T15:10:15.600','2026-05-26T15:16:44.461']::timestamp[]) AS t
),
to_delete AS (
  SELECT r.id
  FROM ync_dashboard_complete_ct_log r
  WHERE r.record_date=CURRENT_DATE AND r.is_ng=true
    AND NOT EXISTS (
      SELECT 1 FROM real_times rt
      WHERE ABS(EXTRACT(EPOCH FROM (r.ts - rt.t))) < 5
    )
)
DELETE FROM ync_dashboard_complete_ct_log
WHERE id IN (SELECT id FROM to_delete);

SELECT COUNT(*) FILTER (WHERE NOT is_ng) AS ok,
       COUNT(*) FILTER (WHERE is_ng) AS ng
FROM ync_dashboard_complete_ct_log
WHERE record_date=CURRENT_DATE;
