#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# py_sensor_fault_sync.py — "this sensor is not working" for the maintenance
# dashboard.
#
# Operator ask (2026-09-20): on Sensor Health, a sensor that has not worked for
# a while must appear in a NEW table with its details so maintenance can show
# it on their own dashboard, and must disappear from that table the moment the
# sensor works again.
#
# WHAT IT DOES
#   Every SYNC_S seconds it asks the SAME sensor-health logic the Sensor Health
#   page uses (routers.poka_yoke.sensor_health) for every line that has PY sweep
#   data, with the stuck threshold set to FAULT_MIN_SEC.  Any PY whose sensing
#   bit has not toggled for that long — while the line is actually producing —
#   is written to maintenance_py_sensor_faults in maintenance_db.  As soon as
#   the PY reports alive again its row is DELETED.
#
# THE IDLE-LINE GUARD
#   A sensor on a stopped line does not toggle either, so without this guard
#   every sensor would look faulty at shift end.  A line is only evaluated when
#   it produced a cycle within PRODUCING_MIN minutes.  Rows of a line that is
#   not producing are left exactly as they are: a real fault does not disappear
#   because the line stopped, and it is not confirmed fixed either.
#
# SAFETY
#   • The poka-yoke code, its config and the collectors are NOT touched — this
#     module only READS through the existing sensor-health function.
#   • On the MES database it only ever runs SELECTs.
#   • The only writes are to its own table in maintenance_db.
#   • Runs in ONE process only (the background leader), like every other
#     background worker here.  PY_FAULT_SYNC=0 disables it completely.
# ─────────────────────────────────────────────────────────────────────────────
import json
import os
import threading
import time
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

from database import get_conn, dict_cursor

SYNC_S         = int(os.environ.get("PY_FAULT_SYNC_S", "60"))
FAULT_MIN_SEC  = int(os.environ.get("PY_FAULT_MIN_SEC", "120"))   # operator: 2 minutes
PRODUCING_MIN  = int(os.environ.get("PY_FAULT_PRODUCING_MIN", "10"))
# A line whose collector has not published a sweep recently is not judged at
# all: its numbers are frozen at the last sweep, so every bit would age past
# the threshold and look faulty.  Measured 2026-09-20: several lines still
# carried sweeps from 03:13 while the plant was running.
STALE_MIN      = int(os.environ.get("PY_FAULT_STALE_MIN", "10"))
TABLE          = os.environ.get("PY_FAULT_TABLE", "maintenance_py_sensor_faults")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SWEEP_CACHE = os.path.join(_HERE, "_sensor_sweep_cache.json")
_SYS_USER = {"id": 1, "username": "system", "role": "admin"}
_STARTED = False

_MAINT_DSN = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432") or 5432),
    "dbname":          os.getenv("MAINT_DB_NAME", "maintenance_db"),
    "user":            os.getenv("DB_USER", "postgres"),
    "password":        os.getenv("DB_PASS", "tbdi@123"),
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5") or 5),
}


def _maint():
    c = psycopg2.connect(**_MAINT_DSN)
    c.autocommit = True
    return c


def _ensure_table(cur):
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id                SERIAL PRIMARY KEY,
            line_id           INTEGER NOT NULL,
            line_name         TEXT,
            zone_name         TEXT,
            py_no             TEXT,
            py_name           TEXT,
            sensing_bits      TEXT,
            stuck_bits        TEXT,
            register_addr     TEXT,
            model_bit         INTEGER,
            model_name        TEXT,
            not_working_since TIMESTAMPTZ,
            stuck_for_sec     INTEGER,
            first_detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            line_producing    BOOLEAN,
            source            TEXT DEFAULT 'MES sensor health',
            UNIQUE (line_id, py_no, sensing_bits)
        )""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_line ON {TABLE} (line_id)")


def _sweep_line_ids():
    """Lines that have PY sweep data: the in-process map plus the shared cache
    file (each API worker writes it, so the file covers workers we are not)."""
    ids = set()
    try:
        from routers.poka_yoke import _SENSOR_SWEEP       # read-only
        ids |= {int(k) for k in _SENSOR_SWEEP.keys()}
    except Exception:
        pass
    try:
        with open(_SWEEP_CACHE, "r", encoding="utf-8") as f:
            ids |= {int(k) for k in (json.load(f) or {}).keys()}
    except Exception:
        pass
    return sorted(ids)


def _lines_meta(cur, ids):
    if not ids:
        return {}
    cur.execute("""SELECT l.id, l.line_name, l.db_table_name,
                          COALESCE(z.zone_name, '') AS zone_name
                     FROM mes_lines l
                     LEFT JOIN mes_zones z ON z.id = l.zone_id
                    WHERE l.id = ANY(%s)""", (list(ids),))
    return {r["id"]: r for r in cur.fetchall()}


def _is_producing(cur, tbl):
    """A cycle within PRODUCING_MIN minutes — the idle-line guard."""
    if not tbl:
        return False
    cur.execute("SELECT to_regclass(%s) AS t", (f"{tbl}_ct_log",))
    if not (cur.fetchone() or {}).get("t"):
        return False
    cur.execute(f"SELECT 1 FROM {tbl}_ct_log "
                f"WHERE ts > now() - make_interval(mins => %s) LIMIT 1", (PRODUCING_MIN,))
    return cur.fetchone() is not None


def _sweep_is_stale(swept_at):
    """True when the line's last sensor sweep is too old to judge."""
    if not swept_at:
        return True
    try:
        ts = swept_at if isinstance(swept_at, datetime) else datetime.fromisoformat(str(swept_at))
        if ts.tzinfo is None:
            ts = ts.astimezone()
        return (datetime.now().astimezone() - ts).total_seconds() > STALE_MIN * 60
    except Exception:
        return True


def _evaluate():
    """One pass: returns (faults, healthy, skipped) without writing anything."""
    from routers.poka_yoke import sensor_health            # read-only
    faults, healthy, skipped = [], {}, []
    ids = _sweep_line_ids()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        meta = _lines_meta(cur, ids)
        for lid in ids:
            m = meta.get(lid)
            if not m:
                continue
            if not _is_producing(cur, m.get("db_table_name")):
                skipped.append(lid)
                continue
            try:
                r = sensor_health(line_id=lid, stuck_sec=FAULT_MIN_SEC, user=_SYS_USER)
            except Exception as exc:
                print(f"[PY-FAULT] line {lid}: sensor-health failed: {str(exc)[:90]}", flush=True)
                skipped.append(lid)
                continue
            if _sweep_is_stale(r.get("swept_at")):
                skipped.append(lid)
                continue
            ok_pys = []
            for c in r.get("checks") or []:
                secs = c.get("stuck_for_sec") or 0
                if c.get("status") == "stuck" and secs >= FAULT_MIN_SEC:
                    stuck_bits = ",".join(b["bit"] for b in (c.get("bits") or [])
                                          if b.get("status") == "stuck")
                    faults.append({
                        "line_id":      lid,
                        "line_name":    m.get("line_name"),
                        "zone_name":    m.get("zone_name"),
                        "py_no":        c.get("py_no"),
                        "py_name":      c.get("py_name"),
                        "sensing_bits": c.get("sensing_bits"),
                        "stuck_bits":   stuck_bits,
                        "register_addr": c.get("register_addr"),
                        "model_bit":    r.get("model_bit"),
                        "model_name":   r.get("model_name"),
                        "since":        datetime.now().astimezone() - timedelta(seconds=float(secs)),
                        "stuck_for_sec": int(secs),
                    })
                elif c.get("status") == "alive":
                    ok_pys.append(c.get("py_no"))
            healthy[lid] = ok_pys
    return faults, healthy, skipped


def sync_once(verbose=True):
    faults, healthy, skipped = _evaluate()
    added = cleared = 0
    with _maint() as conn:
        cur = conn.cursor()
        _ensure_table(cur)
        for f in faults:
            cur.execute(f"""
                INSERT INTO {TABLE} (line_id, line_name, zone_name, py_no, py_name,
                        sensing_bits, stuck_bits, register_addr, model_bit, model_name,
                        not_working_since, stuck_for_sec, line_producing)
                VALUES (%(line_id)s, %(line_name)s, %(zone_name)s, %(py_no)s, %(py_name)s,
                        %(sensing_bits)s, %(stuck_bits)s, %(register_addr)s, %(model_bit)s,
                        %(model_name)s, %(since)s, %(stuck_for_sec)s, TRUE)
                ON CONFLICT (line_id, py_no, sensing_bits) DO UPDATE SET
                        stuck_bits = EXCLUDED.stuck_bits,
                        register_addr = EXCLUDED.register_addr,
                        model_bit = EXCLUDED.model_bit,
                        model_name = EXCLUDED.model_name,
                        not_working_since = LEAST({TABLE}.not_working_since, EXCLUDED.not_working_since),
                        stuck_for_sec = EXCLUDED.stuck_for_sec,
                        line_producing = TRUE,
                        last_seen_at = now()""", f)
            added += cur.rowcount or 0
        for lid, ok_pys in healthy.items():
            if not ok_pys:
                continue
            cur.execute(f"DELETE FROM {TABLE} WHERE line_id = %s AND py_no = ANY(%s)",
                        (lid, ok_pys))
            cleared += cur.rowcount or 0
        cur.execute(f"SELECT count(*) FROM {TABLE}")
        open_now = cur.fetchone()[0]
    if verbose:
        print(f"[PY-FAULT] {len(faults)} not working, {cleared} cleared, "
              f"{len(skipped)} line(s) idle/skipped — {open_now} row(s) in {TABLE}",
              flush=True)
    return {"faults": len(faults), "cleared": cleared, "skipped": len(skipped),
            "open": open_now}


def _loop():
    print(f"[PY-FAULT] sensor fault sync started — {FAULT_MIN_SEC}s threshold, "
          f"every {SYNC_S}s → maintenance_db.{TABLE} (read-only on PY)", flush=True)
    while True:
        try:
            sync_once()
        except Exception as exc:
            print(f"[PY-FAULT] sync error: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        time.sleep(SYNC_S)


def start():
    """Start the sync in the background leader only."""
    global _STARTED
    if _STARTED or os.environ.get("PY_FAULT_SYNC", "1") == "0":
        return
    try:
        import bg_leader
        if not bg_leader.is_leader():
            return
    except Exception:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="py-sensor-fault-sync", daemon=True).start()


if __name__ == "__main__":
    import sys
    print(json.dumps(sync_once(verbose=True), indent=1))
