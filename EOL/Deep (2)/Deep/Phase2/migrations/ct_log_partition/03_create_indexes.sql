-- ─────────────────────────────────────────────────────────────────────────
-- 03_create_indexes.sql   —  build indexes on the partitioned table
-- Run AFTER 02 (faster to index once the data is in). Indexes created on the
-- parent auto-create on every partition. SAFE: only touches the new table.
-- ─────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_subct_new_lookup
    ON mes_submachine_ct_log_new (sub_plc_id, record_date, shift_name, cycle_seq);

CREATE INDEX IF NOT EXISTS idx_subct_new_time
    ON mes_submachine_ct_log_new (sub_plc_id, ts_end DESC);

CREATE INDEX IF NOT EXISTS idx_subct_new_line_date
    ON mes_submachine_ct_log_new (line_id, record_date);

ANALYZE mes_submachine_ct_log_new;

SELECT 'indexes built + analyzed' AS status;
