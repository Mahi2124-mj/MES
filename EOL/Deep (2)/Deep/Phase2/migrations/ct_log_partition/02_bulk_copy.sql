-- ─────────────────────────────────────────────────────────────────────────
-- 02_bulk_copy.sql   —  copy all current rows into the partitioned table
-- SAFE: reads the live table (does NOT block collector inserts) and writes to
-- the new table. Takes a few minutes for ~8M rows. Run any time before the
-- swap. Re-runnable: it only copies rows not already there.
-- ─────────────────────────────────────────────────────────────────────────
SET synchronous_commit = off;          -- faster bulk load for this session only

INSERT INTO mes_submachine_ct_log_new
    (id, sub_plc_id, line_id, record_date, shift_name, cycle_seq,
     ts_start, ts_end, ct_seconds, model_number, model_name, part_code, is_ng)
SELECT
    id, sub_plc_id, line_id, record_date, shift_name, cycle_seq,
    ts_start, ts_end, ct_seconds, model_number, model_name, part_code, is_ng
FROM mes_submachine_ct_log o
WHERE o.id > (SELECT COALESCE(max(id), 0) FROM mes_submachine_ct_log_new);

SELECT (SELECT count(*) FROM mes_submachine_ct_log_new) AS copied_rows,
       (SELECT count(*) FROM mes_submachine_ct_log)     AS source_rows;
