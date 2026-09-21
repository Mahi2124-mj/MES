-- ─────────────────────────────────────────────────────────────────────────
-- 04_swap.sql   —  THE ONLY STEP WITH A (brief) LOCK.  Run in a low-activity
-- window (night / shift gap).  Everything runs in ONE transaction: if any line
-- fails, nothing changes (auto-rollback). Duration = a few seconds (only the
-- delta since 02 is copied under the lock). Collectors briefly retry, then
-- write to the new table transparently (same name, no code change).
-- ─────────────────────────────────────────────────────────────────────────
BEGIN;

-- Block writes to the live table for the swap (reads still allowed).
LOCK TABLE mes_submachine_ct_log IN SHARE ROW EXCLUSIVE MODE;

-- Catch up rows inserted since the bulk copy (02).
INSERT INTO mes_submachine_ct_log_new
    (id, sub_plc_id, line_id, record_date, shift_name, cycle_seq,
     ts_start, ts_end, ct_seconds, model_number, model_name, part_code, is_ng)
SELECT
    id, sub_plc_id, line_id, record_date, shift_name, cycle_seq,
    ts_start, ts_end, ct_seconds, model_number, model_name, part_code, is_ng
FROM mes_submachine_ct_log o
WHERE o.id > (SELECT COALESCE(max(id), 0) FROM mes_submachine_ct_log_new);

-- Swap names: old → _old (kept as backup), new → live name.
ALTER TABLE mes_submachine_ct_log     RENAME TO mes_submachine_ct_log_old;
ALTER TABLE mes_submachine_ct_log_new RENAME TO mes_submachine_ct_log;

-- Re-point the id sequence to the NEW live table so dropping _old later does
-- NOT drop the sequence, and align its value to the current max id.
ALTER SEQUENCE mes_submachine_ct_log_id_seq OWNED BY mes_submachine_ct_log.id;
SELECT setval('mes_submachine_ct_log_id_seq',
              (SELECT COALESCE(max(id), 1) FROM mes_submachine_ct_log));

COMMIT;

ANALYZE mes_submachine_ct_log;

-- Verify: counts should match (old backup vs new live).
SELECT (SELECT count(*) FROM mes_submachine_ct_log)     AS live_now,
       (SELECT count(*) FROM mes_submachine_ct_log_old) AS old_backup;

-- ── AFTER you have verified for a day or two, reclaim the space: ──
--   DROP TABLE mes_submachine_ct_log_old;
-- (Keep it until you are confident. It is just a backup copy now.)
