-- ─────────────────────────────────────────────────────────────────────────
-- 01_create_partitioned.sql   —  create the new monthly-partitioned table
-- SAFE: creates a brand-new empty table alongside the live one. Nothing on the
-- live table changes. Run any time (no lock, no downtime).
-- ─────────────────────────────────────────────────────────────────────────

-- New partitioned table: same columns as mes_submachine_ct_log, reusing the
-- SAME id sequence. PK must include the partition key (record_date).
CREATE TABLE IF NOT EXISTS mes_submachine_ct_log_new (
    id           bigint       NOT NULL DEFAULT nextval('mes_submachine_ct_log_id_seq'::regclass),
    sub_plc_id   integer      NOT NULL,
    line_id      integer      NOT NULL,
    record_date  date         NOT NULL,
    shift_name   varchar(20)  NOT NULL,
    cycle_seq    integer      NOT NULL,
    ts_start     timestamptz  NOT NULL,
    ts_end       timestamptz  NOT NULL,
    ct_seconds   numeric      NOT NULL,
    model_number integer,
    model_name   varchar(200),
    part_code    varchar(100),
    is_ng        boolean      NOT NULL DEFAULT false,
    PRIMARY KEY (id, record_date),
    FOREIGN KEY (sub_plc_id) REFERENCES mes_plc_configs(id) ON DELETE CASCADE
) PARTITION BY RANGE (record_date);

-- Monthly partitions for 2026-01 .. 2027-12 (covers all current data + ~15
-- future months). The auto-partition function (05) keeps extending this.
DO $$
DECLARE
    d     date := date '2026-01-01';
    stop  date := date '2028-01-01';
    part  text;
BEGIN
    WHILE d < stop LOOP
        part := 'mes_submachine_ct_log_p' || to_char(d, 'YYYYMM');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF mes_submachine_ct_log_new FOR VALUES FROM (%L) TO (%L)',
                part, d, (d + interval '1 month')::date);
        END IF;
        d := (d + interval '1 month')::date;
    END LOOP;
END $$;

-- Safety net: any row whose date somehow falls outside the ranges above lands
-- here instead of failing the INSERT. (Should normally stay empty.)
CREATE TABLE IF NOT EXISTS mes_submachine_ct_log_pdefault
    PARTITION OF mes_submachine_ct_log_new DEFAULT;

SELECT 'partitioned table + ' || count(*) || ' partitions created' AS status
FROM pg_inherits WHERE inhparent = 'mes_submachine_ct_log_new'::regclass;
