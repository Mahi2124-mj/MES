-- ─────────────────────────────────────────────────────────────────────────
-- 06_rollback.sql   —  undo the swap (04) if anything looks wrong.
-- Only valid BEFORE you DROP mes_submachine_ct_log_old. Puts the original
-- table back as the live one. Any rows written to the partitioned table after
-- the swap are preserved in mes_submachine_ct_log_partbak for manual merge.
-- ─────────────────────────────────────────────────────────────────────────
BEGIN;
LOCK TABLE mes_submachine_ct_log IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE mes_submachine_ct_log     RENAME TO mes_submachine_ct_log_partbak;
ALTER TABLE mes_submachine_ct_log_old RENAME TO mes_submachine_ct_log;

ALTER SEQUENCE mes_submachine_ct_log_id_seq OWNED BY mes_submachine_ct_log.id;
SELECT setval('mes_submachine_ct_log_id_seq',
              (SELECT COALESCE(max(id), 1) FROM mes_submachine_ct_log));
COMMIT;

-- Rows the collectors wrote to the partitioned table AFTER the swap are in
-- mes_submachine_ct_log_partbak (id > the old table's max). Merge them back:
--   INSERT INTO mes_submachine_ct_log
--   SELECT * FROM mes_submachine_ct_log_partbak b
--   WHERE b.id > (SELECT max(id) FROM mes_submachine_ct_log);
SELECT 'rolled back to original table' AS status;
