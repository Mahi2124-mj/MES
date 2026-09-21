-- ─────────────────────────────────────────────────────────────────────────
-- 05_auto_partition.sql   —  keep future months' partitions created ahead of
-- time, forever, with zero manual work. Run this ONCE after the swap, then
-- schedule the monthly cron below.
-- ─────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION ensure_ct_log_partitions(months_ahead int DEFAULT 3)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    i       int;
    start_d date;
    end_d   date;
    part    text;
BEGIN
    FOR i IN 0..months_ahead LOOP
        start_d := date_trunc('month', (CURRENT_DATE + (i || ' month')::interval))::date;
        end_d   := (start_d + interval '1 month')::date;
        part    := 'mes_submachine_ct_log_p' || to_char(start_d, 'YYYYMM');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF mes_submachine_ct_log FOR VALUES FROM (%L) TO (%L)',
                part, start_d, end_d);
        END IF;
    END LOOP;
END $$;

-- Create the next few months right now.
SELECT ensure_ct_log_partitions(3);

-- ── Schedule it monthly (system cron; runs 02:10 on the 1st) ──
-- Add this ONE line via `crontab -e`  (adjust the psql path/host if needed):
--
--   10 2 1 * * PGPASSWORD=tbdi@123 psql -h 127.0.0.1 -U postgres -d energydb \
--              -c "SELECT ensure_ct_log_partitions(3);" >> /tmp/ct_log_partition.log 2>&1
--
-- That's it — new months are always ready before data arrives. Old months just
-- sit there (queried only when someone asks for that month). To free very old
-- data instantly some day:  DROP TABLE mes_submachine_ct_log_p2026MM;
