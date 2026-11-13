"""machine_wise_ok_count -- one row per OK part, every machine, one table.

Operator request 2026-11-12: save the OK count of every machine (date, shift,
time, zone, line, machine no., register, value) in ONE new table, at the same
moment the collector writes the part, keep 60 days, delete only from this table.

How it works (nothing in the collector / API / dashboard changes):
  * An AFTER INSERT trigger on each line's part log (<db_table_name>_ct_log)
    and on mes_submachine_ct_log copies every OK row into machine_wise_ok_count.
  * The copy runs inside its own error guard: if it fails, the collector's
    own row is still saved and only the copy is skipped (WARNING in the
    PostgreSQL log).  The nightly run copies any such skipped rows.
  * Started 2026-11-12 "from now": machine_wise_ok_count_sources keeps, per
    source table, the last row id that existed when its trigger was attached;
    rows at or below it are never copied.

Usage (Phase2 venv):
  python machine_wise_ok_count_job.py schema   # new table + functions only
  python machine_wise_ok_count_job.py attach   # triggers on the part logs
  python machine_wise_ok_count_job.py setup    # schema + attach
  python machine_wise_ok_count_job.py daily    # missing triggers, catch-up, 60-day delete
  python machine_wise_ok_count_job.py status
  python machine_wise_ok_count_job.py remove   # drop the triggers only (table kept)

The ONLY table this script ever deletes from is machine_wise_ok_count
(KEEP_TABLE below).  Source tables are only read; their sole change is the
trigger (added by `setup`/`daily`, dropped by `remove`).
"""
import os
import sys
import time

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import DB_CONFIG  # noqa: E402

KEEP_TABLE = "machine_wise_ok_count"          # the one table rows are deleted from
SRC_TABLE = "machine_wise_ok_count_sources"
SUB_SOURCE = "mes_submachine_ct_log"
TRIGGER = "trg_machine_wise_ok_count"
KEEP_DAYS = int(os.getenv("MWOC_KEEP_DAYS", "60"))
DELETE_BATCH = 10000
LOCK_TIMEOUT = "2s"                           # never let collector writes queue behind us


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


def connect():
    c = psycopg2.connect(**{**DB_CONFIG, "connect_timeout": 5,
                            "application_name": "machine_wise_ok_count_job"})
    c.autocommit = True
    return c


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {KEEP_TABLE} (
    id               BIGSERIAL PRIMARY KEY,
    record_date      DATE        NOT NULL,
    shift_name       VARCHAR(20),
    ts               TIMESTAMP   NOT NULL,
    zone_id          INTEGER,
    zone_name        VARCHAR(100),
    line_id          INTEGER,
    line_name        VARCHAR(100),
    machine_id       INTEGER,
    machine_no       VARCHAR(40),
    machine_name     VARCHAR(100),
    register_address VARCHAR(20),
    ok_value         INTEGER,
    source           VARCHAR(80) NOT NULL,
    source_row_id    BIGINT      NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mwoc_source_row ON {KEEP_TABLE} (source, source_row_id);
CREATE INDEX IF NOT EXISTS ix_mwoc_date_shift ON {KEEP_TABLE} (record_date, shift_name);
CREATE INDEX IF NOT EXISTS ix_mwoc_machine_ts ON {KEEP_TABLE} (machine_id, ts);
COMMENT ON TABLE {KEEP_TABLE} IS
  'One row per OK part, every machine (copied by trigger from <line>_ct_log and mes_submachine_ct_log). Keeps {KEEP_DAYS} days. Phase2/machine_wise_ok_count_job.py';

CREATE TABLE IF NOT EXISTS {SRC_TABLE} (
    source        VARCHAR(80) PRIMARY KEY,
    line_id       INTEGER,
    start_row_id  BIGINT      NOT NULL,
    attached_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION machine_wise_ok_count_add(
    p_source text, p_row_id bigint, p_line_id integer, p_machine_id integer,
    p_date date, p_shift text, p_ts timestamp, p_value integer)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v_line integer := p_line_id;
    v_mid  integer := p_machine_id;
BEGIN
    IF v_line IS NULL AND v_mid IS NOT NULL THEN
        SELECT line_id INTO v_line FROM mes_plc_configs WHERE id = v_mid;
    END IF;
    IF v_mid IS NULL AND v_line IS NOT NULL THEN
        SELECT dashboard_plc_id INTO v_mid FROM mes_lines WHERE id = v_line;
    END IF;
    INSERT INTO {KEEP_TABLE}
        (record_date, shift_name, ts, zone_id, zone_name, line_id, line_name,
         machine_id, machine_no, machine_name, register_address, ok_value,
         source, source_row_id)
    SELECT p_date, p_shift, p_ts, l.zone_id, z.zone_name, v_line, l.line_name,
           v_mid, pc.machine_no, pc.machine_name,
           CASE WHEN lower(COALESCE(pc.count_mode, 'bit')) = 'register'
                THEN COALESCE(NULLIF(trim(pc.ok_data_register), ''), NULLIF(trim(pc.ok_bit_address), ''))
                ELSE COALESCE(NULLIF(trim(pc.ok_bit_address), ''), NULLIF(trim(pc.ok_data_register), ''))
           END,
           p_value, p_source, p_row_id
      FROM (SELECT 1) AS one
      LEFT JOIN mes_plc_configs pc ON pc.id = v_mid
      LEFT JOIN mes_lines       l  ON l.id  = v_line
      LEFT JOIN mes_zones       z  ON z.id  = l.zone_id
    ON CONFLICT (source, source_row_id) DO NOTHING;
END
$fn$;

-- Line part logs: TG_ARGV[0] = mes_lines.id.  Main machine = mes_lines.dashboard_plc_id.
CREATE OR REPLACE FUNCTION machine_wise_ok_count_from_line() RETURNS trigger
LANGUAGE plpgsql SET lock_timeout = '1s' AS $fn$
BEGIN
    IF COALESCE(NEW.is_ng, false) THEN
        RETURN NULL;
    END IF;
    BEGIN
        PERFORM machine_wise_ok_count_add(TG_TABLE_NAME::text, NEW.id::bigint,
                    TG_ARGV[0]::integer, NULL::integer, NEW.record_date::date,
                    NEW.shift_name::text, NEW.ts::timestamp, NEW.cycle_seq::integer);
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'machine_wise_ok_count: copy skipped %.% (%)', TG_TABLE_NAME, NEW.id, SQLERRM;
    END;
    RETURN NULL;
END
$fn$;

-- Sub-machine part log (partitioned; one trigger on the parent covers every month).
CREATE OR REPLACE FUNCTION machine_wise_ok_count_from_sub() RETURNS trigger
LANGUAGE plpgsql SET lock_timeout = '1s' AS $fn$
BEGIN
    IF COALESCE(NEW.is_ng, false) THEN
        RETURN NULL;
    END IF;
    BEGIN
        PERFORM machine_wise_ok_count_add('{SUB_SOURCE}', NEW.id::bigint,
                    NEW.line_id::integer, NEW.sub_plc_id::integer, NEW.record_date::date,
                    NEW.shift_name::text, (NEW.ts_end AT TIME ZONE 'Asia/Kolkata')::timestamp,
                    NEW.cycle_seq::integer);
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'machine_wise_ok_count: copy skipped %.% (%)', TG_TABLE_NAME, NEW.id, SQLERRM;
    END;
    RETURN NULL;
END
$fn$;
"""


def wanted_sources(cur):
    """[(table, line_id or None)] -- every active line's part log that exists, plus the sub log."""
    cur.execute("""SELECT db_table_name || '_ct_log', id FROM mes_lines
                    WHERE is_active AND NULLIF(trim(db_table_name), '') IS NOT NULL
                      AND to_regclass(db_table_name || '_ct_log') IS NOT NULL
                    ORDER BY id""")
    out = [(t, lid) for t, lid in cur.fetchall()]
    out.append((SUB_SOURCE, None))
    return out


def has_trigger(cur, table):
    cur.execute("""SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass(%s) AND tgname = %s AND NOT tgisinternal""",
                (table, TRIGGER))
    return cur.fetchone() is not None


def attach(cur, table, line_id):
    """Record the start id, then add the trigger (2 s lock limit, skipped if busy)."""
    func = "machine_wise_ok_count_from_sub()" if table == SUB_SOURCE else \
           f"machine_wise_ok_count_from_line('{int(line_id)}')"
    try:
        cur.execute("BEGIN")
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute(f'CREATE TRIGGER {TRIGGER} AFTER INSERT ON "{table}" '
                    f"FOR EACH ROW EXECUTE FUNCTION {func}")
        # Under the trigger's lock no insert is in flight, so MAX(id) is exactly
        # the last row written before copying started.
        cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
        start = int(cur.fetchone()[0])
        cur.execute(f"""INSERT INTO {SRC_TABLE} (source, line_id, start_row_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (source) DO NOTHING""", (table, line_id, start))
        cur.execute("COMMIT")
        log(f"trigger added on {table} (copy starts after row id {start})")
        return True
    except psycopg2.Error as e:
        cur.execute("ROLLBACK")
        log(f"SKIPPED {table}: {str(e).strip()[:160]}")
        return False


def cmd_schema(conn):
    """New table + functions only (no trigger on any existing table)."""
    cur = conn.cursor()
    cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
    cur.execute(SCHEMA_SQL)
    log(f"table {KEEP_TABLE}, {SRC_TABLE} and functions ready")


def cmd_attach(conn):
    cur = conn.cursor()
    cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
    attach_missing(cur)


def cmd_setup(conn):
    cmd_schema(conn)
    cmd_attach(conn)


def attach_missing(cur):
    added = skipped = 0
    for table, line_id in wanted_sources(cur):
        if has_trigger(cur, table):
            continue
        if attach(cur, table, line_id):
            added += 1
        else:
            skipped += 1
    log(f"triggers: {added} added, {skipped} skipped (busy), "
        f"{len(wanted_sources(cur))} sources wanted")


def catch_up(cur):
    """Copy OK rows of the last 2 days that the trigger skipped (error guard), never before the start id."""
    cur.execute(f"SELECT source, line_id, start_row_id FROM {SRC_TABLE}")
    total = 0
    for source, line_id, start in cur.fetchall():
        if to_regclass(cur, source) is None:
            continue
        if source == SUB_SOURCE:
            sql = f"""SELECT machine_wise_ok_count_add(%s, s.id, s.line_id, s.sub_plc_id,
                             s.record_date, s.shift_name, (s.ts_end AT TIME ZONE 'Asia/Kolkata'), s.cycle_seq)
                        FROM {SUB_SOURCE} s
                       WHERE s.record_date >= CURRENT_DATE - 1 AND s.id > %s
                         AND NOT COALESCE(s.is_ng, false)
                         AND NOT EXISTS (SELECT 1 FROM {KEEP_TABLE} m
                                          WHERE m.source = %s AND m.source_row_id = s.id)"""
            args = (source, start, source)
        else:
            sql = f"""SELECT machine_wise_ok_count_add(%s, s.id, %s, NULL,
                             s.record_date, s.shift_name, s.ts, s.cycle_seq)
                        FROM "{source}" s
                       WHERE s.record_date >= CURRENT_DATE - 1 AND s.id > %s
                         AND NOT COALESCE(s.is_ng, false)
                         AND NOT EXISTS (SELECT 1 FROM {KEEP_TABLE} m
                                          WHERE m.source = %s AND m.source_row_id = s.id)"""
            args = (source, line_id, start, source)
        cur.execute(sql, args)
        n = cur.rowcount
        if n:
            log(f"catch-up: {n} skipped rows copied from {source}")
            total += n
    log(f"catch-up done ({total} rows)")


def to_regclass(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0]


def delete_old(cur):
    """Delete rows older than KEEP_DAYS from machine_wise_ok_count -- and nothing else."""
    total = 0
    while True:
        cur.execute(f"""DELETE FROM {KEEP_TABLE}
                         WHERE id IN (SELECT id FROM {KEEP_TABLE}
                                       WHERE record_date < CURRENT_DATE - %s
                                       LIMIT %s)""", (KEEP_DAYS, DELETE_BATCH))
        n = cur.rowcount
        total += n
        if n < DELETE_BATCH:
            break
        time.sleep(0.5)
    log(f"60-day delete: {total} rows older than {KEEP_DAYS} days removed from {KEEP_TABLE}")


def cmd_daily(conn):
    cur = conn.cursor()
    cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
    attach_missing(cur)
    catch_up(cur)
    delete_old(cur)


def cmd_status(conn):
    cur = conn.cursor()
    for table, line_id in wanted_sources(cur):
        print(f"  {'OK ' if has_trigger(cur, table) else '-- '} {table} (line {line_id})")
    if to_regclass(cur, KEEP_TABLE):
        cur.execute(f"SELECT count(*), min(ts), max(ts) FROM {KEEP_TABLE}")
        print("  rows, first, last:", cur.fetchone())


def cmd_remove(conn):
    """Drop the triggers only.  The table and its rows stay."""
    cur = conn.cursor()
    cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
    cur.execute("""SELECT tgrelid::regclass::text FROM pg_trigger
                    WHERE tgname = %s AND NOT tgisinternal AND tgparentid = 0""", (TRIGGER,))
    for (table,) in cur.fetchall():
        cur.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {table}")
        log(f"trigger dropped on {table}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"schema": cmd_schema, "attach": cmd_attach, "setup": cmd_setup,
          "daily": cmd_daily, "status": cmd_status, "remove": cmd_remove}.get(cmd)
    if not fn:
        sys.exit(__doc__)
    c = connect()
    try:
        fn(c)
    finally:
        c.close()
