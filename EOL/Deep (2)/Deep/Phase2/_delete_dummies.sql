-- Delete 3 dummy rows from BOTH tables, identified by exact timestamps:
--   1) 08:36:03 NG  ct=NULL  (no previous pulse to measure from — collector boot artifact)
--   2) 08:38:18 NG  ct=83.28 (huge gap — startup junk)
--   3) 08:45:14 OK  ct=374.38 (6+ min gap — startup junk)

BEGIN;

-- L6 audit
WITH del AS (
    DELETE FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
      AND ts IN (
          '2026-05-28 08:36:03.000000',
          '2026-05-28 08:38:18.000000',
          '2026-05-28 08:45:14.000000'
      )
    RETURNING ts, bit_type
)
SELECT 'L6_audit' AS tbl, COUNT(*) AS deleted FROM del;

-- Also try with the millisecond suffix that ct_log uses (timestamps
-- in different tables may have slight precision differences).
WITH del AS (
    DELETE FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
      AND date_trunc('second', ts) IN (
          '2026-05-28 08:36:03',
          '2026-05-28 08:38:18',
          '2026-05-28 08:45:14'
      )
    RETURNING ts, bit_type
)
SELECT 'L6_audit (sec-precision)' AS tbl, COUNT(*) AS deleted FROM del;

-- ct_log (chart source)
WITH del AS (
    DELETE FROM ync_dashboard_complete_ct_log
    WHERE record_date = CURRENT_DATE
      AND date_trunc('second', ts) IN (
          '2026-05-28 08:36:03',
          '2026-05-28 08:38:18',
          '2026-05-28 08:45:14'
      )
    RETURNING ts, is_ng
)
SELECT 'ct_log' AS tbl, COUNT(*) AS deleted FROM del;

COMMIT;

-- Post-delete verification
SELECT 'L6_audit' AS tbl, COUNT(*) AS rows, COUNT(*) FILTER (WHERE bit_type='NG') AS ng
FROM mes_l6_final_inspection WHERE record_date = CURRENT_DATE
UNION ALL
SELECT 'ct_log', COUNT(*), COUNT(*) FILTER (WHERE is_ng=true)
FROM ync_dashboard_complete_ct_log WHERE record_date = CURRENT_DATE;
