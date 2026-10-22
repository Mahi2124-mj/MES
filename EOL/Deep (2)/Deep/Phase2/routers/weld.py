"""
routers/weld.py
===============
Weld Monitor (Quality) — live weld-parameter trends.  2026-06-18.

For now: ROBOT welding, two parameters — Weld Current (A) + Weld Voltage (V),
plotted as live wallboard-style line graphs.  PROJECTION welding section is a
placeholder (no data yet).

Data model is a generic time-series (`mes_weld_log`): one timestamped row per
reading, so whatever the real controller's timing turns out to be (per weld
spot, per interval, etc.) it just inserts rows and the charts plot them.

DATA SOURCE: none wired yet.  A built-in TEST generator fills the table so the
UI/charts are live now; when the real robot-welding controller feed is
available, point it at POST /api/weld/ingest (or a poller) with source='live'
and set WELD_TEST_GEN=0 to stop the synthetic data.  Collector untouched.
"""
import os
import time
import random
import threading
from typing import Optional

from fastapi import APIRouter, Depends, Query

from database import get_conn, dict_cursor
from auth import get_current_user_optional
from ddl_once import once

weld_router = APIRouter(prefix="/api/weld", tags=["weld"])

# Spec windows (acceptable bands) per weld type + parameter.  Hardcoded for
# now; can move to a config table later.  Tuned for MAG/arc robot welding.
WELD_SPEC = {
    "robot": {
        "current": {"min": 200.0, "max": 260.0, "set": 225.0, "unit": "A"},
        "voltage": {"min": 22.0,  "max": 28.0,  "set": 24.5,  "unit": "V"},
    },
    "projection": {
        "current": {"min": 8.0,  "max": 12.0, "set": 10.0, "unit": "kA"},
        "voltage": {"min": 2.0,  "max": 4.0,  "set": 3.0,  "unit": "V"},
    },
}


@once
def _ensure_weld_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_weld_log (
            id           BIGSERIAL PRIMARY KEY,
            ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            weld_type    TEXT NOT NULL DEFAULT 'robot',     -- robot | projection
            station      TEXT NOT NULL DEFAULT 'Robot-1',
            weld_seq     INTEGER,
            weld_current NUMERIC,                            -- Amps
            weld_voltage NUMERIC,                            -- Volts
            model        TEXT,
            shift        TEXT,
            is_ng        BOOLEAN DEFAULT FALSE,
            source       TEXT NOT NULL DEFAULT 'test'        -- test | live
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_weld_type_station_ts "
                "ON mes_weld_log (weld_type, station, ts DESC)")
    # 2026-08-03 — columns the live feed fills so the monitor can be filtered
    # (zone / line / machine / shift / date) and plotted against PART COUNT
    # instead of only wall-clock.  ADD COLUMN without a default is a catalog
    # -only change, so this stays instant on the ~700k-row table.
    cur.execute("SET lock_timeout = '3s'")
    cur.execute("""
        ALTER TABLE mes_weld_log
          ADD COLUMN IF NOT EXISTS weld_peak_a     NUMERIC,
          ADD COLUMN IF NOT EXISTS weld_duration_s NUMERIC,
          ADD COLUMN IF NOT EXISTS line_id         INTEGER,
          ADD COLUMN IF NOT EXISTS zone            TEXT,
          ADD COLUMN IF NOT EXISTS machine_name    TEXT,
          ADD COLUMN IF NOT EXISTS part_count      INTEGER,
          ADD COLUMN IF NOT EXISTS part_code       TEXT,
          ADD COLUMN IF NOT EXISTS record_date     DATE
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_weld_filter "
                "ON mes_weld_log (record_date, shift, line_id, station)")
    conn.commit()


_MASTER_READY = False


def _ensure_master_table(conn):
    """Weld MASTER — one row per welding station (robot).  This is what the
    Quality admin edits: which analog card / channel a robot's current shunt is
    wired to, where that robot sits (zone → line → machine), and the acceptable
    current / voltage band.  The live poller reads this table, so adding a
    robot here is all it takes to start logging it — no code change.

    2026-10-10 — runs ONCE per process (not per request).  The per-request
    CREATE + migrate used to re-run on every /weld/gas poll; a migrate ALTER
    racing the poller's reads of this table closed the connection and 500'd the
    read (same per-request-DDL convoy as the comments / energy_per_part fixes)."""
    global _MASTER_READY
    if _MASTER_READY:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_weld_master (
            id             SERIAL PRIMARY KEY,
            station        TEXT NOT NULL UNIQUE,            -- robot name, e.g. RC-01
            weld_type      TEXT NOT NULL DEFAULT 'robot',   -- robot | projection
            zone           TEXT,
            line_id        INTEGER,
            machine_name   TEXT,
            card_ip        TEXT NOT NULL,                   -- PPI/analog card
            card_port      INTEGER NOT NULL DEFAULT 502,    -- Modbus TCP
            unit_id        INTEGER NOT NULL DEFAULT 1,
            channel        INTEGER NOT NULL DEFAULT 8,      -- 1..8
            base_register  INTEGER NOT NULL DEFAULT 2001,   -- ch1 low reg (FC4)
            mv_to_a        NUMERIC NOT NULL DEFAULT 10,     -- 60 mV = 600 A
            current_min    NUMERIC, current_set NUMERIC, current_max NUMERIC,
            voltage_min    NUMERIC, voltage_set NUMERIC, voltage_max NUMERIC,
            on_threshold_a NUMERIC NOT NULL DEFAULT 30,     -- arc-on detect
            gap_s          NUMERIC NOT NULL DEFAULT 0.35,   -- silence ends a weld
            min_weld_s     NUMERIC NOT NULL DEFAULT 0.20,   -- ignore blips
            sample_hz      NUMERIC NOT NULL DEFAULT 50,
            is_active      BOOLEAN NOT NULL DEFAULT TRUE,
            note           TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()
    _migrate_master_once(conn)
    # Seed the one station that is physically wired today (RC-01 on the
    # recliner-zone card) so the feature works out of the box; the admin can
    # edit or delete it like any other row.
    cur.execute("SELECT COUNT(*) FROM mes_weld_master")
    if int(cur.fetchone()[0] or 0) == 0:
        cur.execute("""
            INSERT INTO mes_weld_master
              (station, weld_type, zone, line_id, machine_name, card_ip, card_port,
               unit_id, channel, base_register, mv_to_a,
               current_min, current_set, current_max, note)
            SELECT 'RC-01','robot','RECLINER', l.id, 'RC-01 Robot',
                   '192.168.31.59', 502, 1, 8, 2001, 10,
                   200, 300, 400, 'Auto-seeded 2026-08-03 — 8-ch analog card, ch8 wired, 60 mV = 600 A'
            FROM (SELECT id FROM mes_lines WHERE line_code = 'YMC-L5' LIMIT 1) l
            ON CONFLICT (station) DO NOTHING
        """)
        conn.commit()
    _MASTER_READY = True


_MASTER_MIGRATED = False


def _migrate_master_once(conn) -> None:
    """2026-09-21 — several channels per card: a row is either the robot's weld
    current (signal='current', per-weld logic) or a continuous sensor such as
    the gas sensor (signal='gas'), with its own unit / scale / offset / sample
    period.  Once per process, never per request."""
    global _MASTER_MIGRATED
    if _MASTER_MIGRATED:
        return
    # 2026-10-10 — only ALTER when a column is genuinely missing: reading the
    # catalog takes no lock, while ADD COLUMN takes an ACCESS EXCLUSIVE lock that
    # races the poller's reads of this table and closed the request's connection
    # ("connection already closed" -> 500 on /weld/gas).  Never let this break a
    # read: on any error, roll back and still mark it done so it can't retry-storm.
    defs = {"signal": "signal TEXT NOT NULL DEFAULT 'current'", "unit": "unit TEXT",
            "scale": "scale NUMERIC", "offset_val": "offset_val NUMERIC",
            "sample_s": "sample_s NUMERIC",
            "band_lo": "band_lo NUMERIC", "band_hi": "band_hi NUMERIC"}
    try:
        cur = conn.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'mes_weld_master'")
        have = {r[0] for r in cur.fetchall()}
        missing = [c for c in defs if c not in have]
        if missing:
            for c in missing:
                cur.execute(f"ALTER TABLE mes_weld_master ADD COLUMN IF NOT EXISTS {defs[c]}")
            conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[WELD] master migrate skipped: {str(exc)[:120]}", flush=True)
    _MASTER_MIGRATED = True


# ── MASTER (Quality admin) ────────────────────────────────────────────────
# Columns the admin may set.  Anything not listed is ignored, so a stray key
# in the payload can never reach the SQL.
_MASTER_FIELDS = [
    "station", "weld_type", "zone", "line_id", "machine_name",
    "card_ip", "card_port", "unit_id", "channel", "base_register", "mv_to_a",
    "current_min", "current_set", "current_max",
    "voltage_min", "voltage_set", "voltage_max",
    "on_threshold_a", "gap_s", "min_weld_s", "sample_hz", "is_active", "note",
    "signal", "unit", "scale", "offset_val", "sample_s", "band_lo", "band_hi",
]


@weld_router.get("/master")
def weld_master_list(user=Depends(get_current_user_optional)):
    """All configured welding stations + the pick-lists the form needs
    (zones / lines / machines come straight from the production master, so the
    Quality admin picks the SAME line and machine names the rest of MES uses)."""
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT m.*, l.line_code, l.line_name
            FROM mes_weld_master m
            LEFT JOIN mes_lines l ON l.id = m.line_id
            ORDER BY m.weld_type, m.station
        """)
        rows = [dict(r) for r in (cur.fetchall() or [])]
        # Zone → Line → Machine, each carrying its parent id so the form can
        # cascade: pick a zone and only its lines are offered, pick a line and
        # only its machines are.  All three come from the production master, so
        # a welding station is filed under the same names the rest of MES uses.
        cur.execute("SELECT id, zone_name FROM mes_zones "
                    "WHERE COALESCE(is_active, TRUE) AND COALESCE(zone_name,'') <> '' "
                    "ORDER BY zone_name")
        zones = [dict(r) for r in (cur.fetchall() or [])]
        cur.execute("SELECT l.id, l.line_code, l.line_name, l.zone_id, z.zone_name "
                    "FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id "
                    "WHERE COALESCE(l.is_active, TRUE) ORDER BY l.line_code")
        lines = [dict(r) for r in (cur.fetchall() or [])]
        cur.execute("SELECT DISTINCT line_id, machine_name FROM mes_plc_configs "
                    "WHERE COALESCE(machine_name,'') <> '' ORDER BY line_id, machine_name")
        machines = [dict(r) for r in (cur.fetchall() or [])]
    return {"rows": rows, "lines": lines, "machines": machines, "zones": zones,
            "channels": list(range(1, 9))}


@weld_router.post("/master")
def weld_master_upsert(body: dict, user=Depends(get_current_user_optional)):
    """Create or update a station.  Send `id` to update, omit it to create."""
    data = {k: body.get(k) for k in _MASTER_FIELDS if k in body}
    if not data.get("station"):
        return {"ok": False, "error": "station (robot name) is required"}
    if not data.get("card_ip"):
        return {"ok": False, "error": "card_ip is required"}
    rid = body.get("id")
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = conn.cursor()
        if rid:
            sets = ", ".join(f"{k} = %s" for k in data) + ", updated_at = NOW()"
            cur.execute(f"UPDATE mes_weld_master SET {sets} WHERE id = %s",
                        list(data.values()) + [rid])
        else:
            # 2026-09-21 — creating used to UPSERT on the station name, so adding
            # a second channel of the same card under the same name silently
            # rewrote the existing row (RC-01's weld current ch8 became ch6).
            # A new row needs its own name; editing goes through `id`.
            cur.execute("SELECT 1 FROM mes_weld_master WHERE station = %s",
                        (data["station"],))
            if cur.fetchone():
                return {"ok": False,
                        "error": f"A station named '{data['station']}' already exists. "
                                 f"Give the new channel its own name (e.g. "
                                 f"'{data['station']} Gas'), or edit that row."}
            cols = ", ".join(data)
            ph   = ", ".join(["%s"] * len(data))
            cur.execute(f"INSERT INTO mes_weld_master ({cols}) VALUES ({ph}) RETURNING id",
                        list(data.values()))
            rid = cur.fetchone()[0]
        conn.commit()
    return {"ok": True, "id": rid}


@weld_router.delete("/master/{row_id}")
def weld_master_delete(row_id: int, user=Depends(get_current_user_optional)):
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_weld_master WHERE id = %s", (row_id,))
        conn.commit()
    return {"ok": True}


@weld_router.get("/filters")
def weld_filters(weld_type: str = Query("robot"), user=Depends(get_current_user_optional)):
    """Dropdown values for the monitor, taken from what has actually been
    logged (so you can only filter to combinations that exist)."""
    with get_conn() as conn:
        _ensure_weld_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT DISTINCT station, zone, line_id, machine_name, shift,
                   COALESCE(record_date, ts::date) AS d
            FROM mes_weld_log WHERE weld_type = %s
        """, (weld_type,))
        rows = cur.fetchall() or []
        cur.execute("SELECT id, line_code FROM mes_lines")
        line_name = {r["id"]: r["line_code"] for r in (cur.fetchall() or [])}
    def _uniq(key):
        return sorted({r[key] for r in rows if r.get(key) not in (None, "")})
    return {
        "stations": _uniq("station"),
        "zones":    _uniq("zone"),
        "machines": _uniq("machine_name"),
        "shifts":   _uniq("shift"),
        "dates":    [str(d) for d in sorted({r["d"] for r in rows if r.get("d")}, reverse=True)][:60],
        "lines":    [{"id": i, "line_code": line_name.get(i, str(i))}
                     for i in sorted({r["line_id"] for r in rows if r.get("line_id")})],
    }


@weld_router.get("/stations")
def weld_stations(weld_type: str = Query("robot"), user=Depends(get_current_user_optional)):
    with get_conn() as conn:
        _ensure_weld_table(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT DISTINCT station FROM mes_weld_log WHERE weld_type=%s ORDER BY station",
                    (weld_type,))
        st = [r["station"] for r in (cur.fetchall() or [])]
    return {"weld_type": weld_type, "stations": st}


@weld_router.get("/live")
def weld_live(
    weld_type: str = Query("robot"),
    station:   Optional[str] = Query(None),
    date:      Optional[str] = Query(None, description="YYYY-MM-DD; omit = latest data"),
    shift:     Optional[str] = Query(None),
    line_id:   Optional[int] = Query(None),
    zone:      Optional[str] = Query(None),
    machine:   Optional[str] = Query(None),
    limit:     int = Query(120, ge=1, le=2000),
    user=Depends(get_current_user_optional),
):
    """Readings (oldest→newest for the chart) + latest + spec band.

    2026-08-03 — filterable by date / shift / zone / line / machine / station,
    and every reading carries `part_count` so the chart can use PART COUNT as
    its X axis instead of wall-clock time.  The spec band now comes from the
    station's row in mes_weld_master when one exists (so Quality can set the
    acceptable current/voltage window per robot from the admin panel); the
    hardcoded WELD_SPEC stays as the fallback."""
    spec = {k: dict(v) for k, v in WELD_SPEC.get(weld_type, WELD_SPEC["robot"]).items()}
    with get_conn() as conn:
        _ensure_weld_table(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT DISTINCT station FROM mes_weld_log WHERE weld_type=%s ORDER BY station",
                    (weld_type,))
        stations = [r["station"] for r in (cur.fetchall() or [])]
        if not station and stations:
            station = stations[0]

        where  = ["weld_type = %s"]
        params: list = [weld_type]
        if station: where.append("station = %s");                    params.append(station)
        if shift:   where.append("shift = %s");                      params.append(shift)
        if line_id: where.append("line_id = %s");                    params.append(line_id)
        if zone:    where.append("zone = %s");                       params.append(zone)
        if machine: where.append("machine_name = %s");               params.append(machine)
        if date:    where.append("COALESCE(record_date, ts::date) = %s"); params.append(date)

        cur.execute(
            "SELECT id, ts, weld_seq, weld_current, weld_voltage, weld_peak_a,"
            " weld_duration_s, is_ng, model, shift, zone, line_id, machine_name,"
            " part_count, part_code "
            f"FROM mes_weld_log WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT %s",
            params + [limit])
        rows = cur.fetchall() or []

        # Per-station spec from the master (falls back to WELD_SPEC above).
        if station:
            try:
                _ensure_master_table(conn)
                cur.execute("SELECT current_min, current_set, current_max,"
                            " voltage_min, voltage_set, voltage_max"
                            " FROM mes_weld_master WHERE station = %s", (station,))
                m = cur.fetchone()
                if m:
                    for param, pfx in (("current", "current"), ("voltage", "voltage")):
                        lo, st_, hi = m[f"{pfx}_min"], m[f"{pfx}_set"], m[f"{pfx}_max"]
                        if lo is not None: spec[param]["min"] = float(lo)
                        if hi is not None: spec[param]["max"] = float(hi)
                        if st_ is not None: spec[param]["set"] = float(st_)
            except Exception:
                conn.rollback()

    rows.reverse()   # oldest -> newest for plotting
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "weld_seq": r["weld_seq"],
            "weld_current": float(r["weld_current"]) if r["weld_current"] is not None else None,
            "weld_voltage": float(r["weld_voltage"]) if r["weld_voltage"] is not None else None,
            "weld_peak_a": float(r["weld_peak_a"]) if r.get("weld_peak_a") is not None else None,
            "weld_duration_s": float(r["weld_duration_s"]) if r.get("weld_duration_s") is not None else None,
            "is_ng": bool(r["is_ng"]),
            "model": r["model"], "shift": r["shift"],
            "zone": r.get("zone"), "line_id": r.get("line_id"),
            "machine_name": r.get("machine_name"),
            "part_count": r.get("part_count"), "part_code": r.get("part_code"),
        })
    latest = out[-1] if out else None
    return {"weld_type": weld_type, "station": station, "stations": stations,
            "spec": spec, "count": len(out), "latest": latest, "readings": out,
            "filters": {"date": date, "shift": shift, "line_id": line_id,
                        "zone": zone, "machine": machine}}


class WeldIngest(dict):
    pass


@weld_router.post("/ingest")
def weld_ingest(body: dict, user=Depends(get_current_user_optional)):
    """Push a real reading (for the future controller feed / poller).
    body: {weld_type, station, weld_current, weld_voltage, weld_seq?, model?, shift?, is_ng?}"""
    with get_conn() as conn:
        _ensure_weld_table(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mes_weld_log (weld_type, station, weld_seq, weld_current, "
            " weld_voltage, model, shift, is_ng, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'live') RETURNING id",
            (str(body.get("weld_type") or "robot"), str(body.get("station") or "Robot-1"),
             body.get("weld_seq"), body.get("weld_current"), body.get("weld_voltage"),
             body.get("model"), body.get("shift"), bool(body.get("is_ng"))))
        nid = cur.fetchone()[0]
        conn.commit()
    return {"ok": True, "id": nid}


# ── TEST data generator (synthetic robot welds so the UI is live now) ──────
# Disable by setting env WELD_TEST_GEN=0 (do this once the real feed is wired).
def weld_test_worker():
    if os.environ.get("WELD_TEST_GEN", "1") == "0":
        print("[WELD] test generator disabled (WELD_TEST_GEN=0)", flush=True)
        return
    print("[WELD] test data generator started (robot welds every ~3s)", flush=True)
    sc = WELD_SPEC["robot"]["current"]; sv = WELD_SPEC["robot"]["voltage"]
    seq = 0
    while True:
        try:
            seq += 1
            # mostly in-spec around setpoint; ~1 in 12 a noticeable excursion
            excursion = (seq % 12 == 0)
            cur_v = random.gauss(sc["set"], 6) + (random.choice([-1, 1]) * 28 if excursion else 0)
            vol_v = random.gauss(sv["set"], 0.8) + (random.choice([-1, 1]) * 3 if excursion else 0)
            cur_v = round(max(0, cur_v), 1); vol_v = round(max(0, vol_v), 1)
            is_ng = not (sc["min"] <= cur_v <= sc["max"] and sv["min"] <= vol_v <= sv["max"])
            with get_conn() as conn:
                _ensure_weld_table(conn)
                c = conn.cursor()
                c.execute(
                    "INSERT INTO mes_weld_log (weld_type, station, weld_seq, weld_current, "
                    " weld_voltage, model, shift, is_ng, source) "
                    "VALUES ('robot','Robot-1',%s,%s,%s,'YXA 4Way','A',%s,'test')",
                    (seq, cur_v, vol_v, is_ng))
                # keep the test table small — prune old test rows
                c.execute("DELETE FROM mes_weld_log WHERE source='test' AND id < "
                          "(SELECT COALESCE(MAX(id),0)-3000 FROM mes_weld_log WHERE source='test')")
                conn.commit()
        except Exception as e:
            print(f"[WELD] test gen tick error: {e}", flush=True)
        time.sleep(3)


# ── Gas sensor (Weld Monitor) — 2026-09-21 ──────────────────────────────────
# Readings written by weld_poller._sensor_worker for Weld Master rows with signal=gas.
# Read-only.  The poller lives in the background-leader worker only, so how
# fresh the feed is comes from the newest row, not from in-process state.
@weld_router.get("/gas")
def gas_readings(minutes: int = Query(30, ge=1, le=720),
                 station: Optional[str] = None,
                 user=Depends(get_current_user_optional)):
    """Recent readings of a gas/sensor channel configured in the Weld Master
    (signal='gas'), written every sample_s by weld_poller._sensor_worker over
    the card's shared connection.  Read-only."""
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""SELECT station, card_ip, channel, unit, COALESCE(sample_s, 2) AS every_s
                         FROM mes_weld_master
                        WHERE is_active AND COALESCE(signal,'current') IN ('gas','sensor')
                          AND (%s::text IS NULL OR station = %s)
                        ORDER BY station LIMIT 1""", (station, station))
        m = cur.fetchone()
        if not m:
            return {"configured": False, "readings": [], "latest": None, "age_s": None}
        meta = {"configured": True, "station": m["station"], "card": m["card_ip"],
                "channel": m["channel"], "unit": m["unit"] or "",
                "every_s": float(m["every_s"])}
        cur.execute("SELECT to_regclass('mes_gas_log') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return {**meta, "readings": [], "latest": None, "age_s": None}
        cur.execute("""SELECT ts, value FROM mes_gas_log
                        WHERE card_ip = %s AND channel = %s
                          AND ts >= now() - make_interval(mins => %s)
                        ORDER BY ts""", (m["card_ip"], m["channel"], minutes))
        rows = cur.fetchall()
        cur.execute("""SELECT ts, value, EXTRACT(EPOCH FROM now() - ts)::int AS age_s
                         FROM mes_gas_log WHERE card_ip = %s AND channel = %s
                        ORDER BY ts DESC LIMIT 1""", (m["card_ip"], m["channel"]))
        last = cur.fetchone()
    step = max(1, len(rows) // 900)          # ≤ ~900 points for the chart
    readings = [{"ts": r["ts"].isoformat(), "v": r["value"]} for r in rows[::step]]
    return {**meta, "minutes": minutes, "readings": readings,
            "latest": (last or {}).get("value"),
            "latest_ts": last["ts"].isoformat() if last else None,
            "age_s": (last or {}).get("age_s")}


# Per-CYCLE air flow for a sensor's machine — 2026-10-10.  For each production
# cycle in the line's ct_log, the air flow over that cycle's window
# [ts - ct_value, ts] from mes_gas_log, filterable by date + shift.  Read-only.
@weld_router.get("/air-cycles")
def air_cycles(station: str,
               date: Optional[str] = None,
               shift: Optional[str] = None,
               limit: int = Query(300, ge=1, le=2000),
               user=Depends(get_current_user_optional)):
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""SELECT m.card_ip, m.channel, m.unit, m.line_id, m.machine_name,
                              l.db_table_name, l.line_name
                         FROM mes_weld_master m LEFT JOIN mes_lines l ON l.id = m.line_id
                        WHERE m.station = %s
                          AND COALESCE(m.signal,'current') IN ('sensor','gas')
                        LIMIT 1""", (station,))
        m = cur.fetchone()
        if not m or not (m.get("db_table_name") or "").strip():
            conn.rollback()
            return {"configured": False, "rows": [], "dates": [], "note": "no line / cycle log for this sensor"}
        ct = m["db_table_name"].strip() + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS t", (ct,))
        if not (cur.fetchone() or {}).get("t"):
            conn.rollback()
            return {"configured": False, "rows": [], "dates": [], "note": f"no cycle log ({ct})"}
        cur.execute(f'SELECT DISTINCT record_date FROM "{ct}" ORDER BY record_date DESC LIMIT 60')
        dates = [str(r["record_date"]) for r in (cur.fetchall() or [])]
        d = date or (dates[0] if dates else None)
        meta = {"configured": True, "station": station, "unit": m.get("unit") or "L/min",
                "machine": m.get("machine_name"), "line": m.get("line_name"),
                "card": m["card_ip"], "channel": m["channel"], "dates": dates,
                "date": d, "shift": shift or ""}
        if not d:
            conn.rollback()
            return {**meta, "rows": []}
        params = [m["card_ip"], m["channel"], d]
        shift_cond = ""
        if shift in ("A", "B"):
            shift_cond = "AND c.shift_name = %s"
            params.append(shift)
        params.append(int(limit))
        cur.execute(f'''
            SELECT c.ts, c.record_date, c.shift_name, ROUND(c.ct_value, 1) AS ct,
                   c.part_code, c.is_ng,
                   ROUND(AVG(g.value)::numeric, 1) AS air_avg,
                   ROUND(MIN(g.value)::numeric, 1) AS air_min,
                   ROUND(MAX(g.value)::numeric, 1) AS air_max,
                   COUNT(g.*) AS samples
              FROM "{ct}" c
              LEFT JOIN mes_gas_log g
                ON g.card_ip = %s AND g.channel = %s
               AND g.ts >  c.ts - (COALESCE(c.ct_value, 0) * interval '1 second')
               AND g.ts <= c.ts
             WHERE c.record_date = %s {shift_cond}
             GROUP BY c.id, c.ts, c.record_date, c.shift_name, c.ct_value, c.part_code, c.is_ng
             ORDER BY c.ts DESC
             LIMIT %s
        ''', params)
        rows = [{"ts": r["ts"].isoformat() if r["ts"] else None,
                 "shift": r["shift_name"],
                 "ct": float(r["ct"]) if r["ct"] is not None else None,
                 "part_code": r["part_code"], "is_ng": bool(r["is_ng"]),
                 "air_avg": float(r["air_avg"]) if r["air_avg"] is not None else None,
                 "air_min": float(r["air_min"]) if r["air_min"] is not None else None,
                 "air_max": float(r["air_max"]) if r["air_max"] is not None else None,
                 "samples": int(r["samples"] or 0)}
                for r in (cur.fetchall() or [])]
        conn.rollback()
    return {**meta, "rows": rows}


# Per-CYCLE weld CURRENT for a station's line — 2026-10-13.  For each production
# cycle in the line's ct_log, the weld current (avg / min / max / peak) from
# mes_weld_log over that cycle's window [ts - ct_value, ts], by date + shift.
# Same shape as /air-cycles but reads the weld-current log.  Read-only.
@weld_router.get("/current-cycles")
def current_cycles(station: str,
                   date: Optional[str] = None,
                   shift: Optional[str] = None,
                   limit: int = Query(300, ge=1, le=2000),
                   user=Depends(get_current_user_optional)):
    # 2026-10-13 — grouped by the welding machine's OWN part counter
    # (mes_weld_log.part_count = one welded part = one cycle at the station),
    # NOT the line ct_log: the weld happens at this station while the line
    # counter increments later at the final station, so a ct_log-window join
    # missed ~40% of welds.  All fields come from mes_weld_log (no tz mismatch).
    with get_conn() as conn:
        _ensure_master_table(conn)
        cur = dict_cursor(conn)
        cur.execute("""SELECT m.station, m.line_id, m.machine_name, l.line_name
                         FROM mes_weld_master m LEFT JOIN mes_lines l ON l.id = m.line_id
                        WHERE m.station = %s
                          AND COALESCE(m.signal,'current') = 'current'
                        LIMIT 1""", (station,))
        m = cur.fetchone()
        if not m:
            conn.rollback()
            return {"configured": False, "rows": [], "dates": [], "note": "weld-current station not found"}
        cur.execute("SELECT DISTINCT record_date FROM mes_weld_log "
                    "WHERE station = %s AND record_date IS NOT NULL "
                    "ORDER BY record_date DESC LIMIT 60", (station,))
        dates = [str(r["record_date"]) for r in (cur.fetchall() or [])]
        d = date or (dates[0] if dates else None)
        meta = {"configured": True, "station": station, "unit": "A",
                "machine": m.get("machine_name"), "line": m.get("line_name"),
                "dates": dates, "date": d, "shift": shift or ""}
        if not d:
            conn.rollback()
            return {**meta, "rows": []}
        # the same line's gas sensor (card/channel), to pull gas flow per part.
        cur.execute("SELECT card_ip, channel FROM mes_weld_master "
                    "WHERE COALESCE(signal,'') = 'gas' AND line_id = %s LIMIT 1", (m["line_id"],))
        grow = cur.fetchone()
        gas_ip = grow["card_ip"] if grow else None
        gas_ch = grow["channel"] if grow else None
        shift_cond = ""
        params = [station, d]
        if shift in ("A", "B"):
            shift_cond = "AND shift = %s"
            params.append(shift)
        params += [gas_ip, gas_ch, int(limit)]
        # parts = welds grouped by the machine's part counter; ct_s = time since
        # the previous part (actual cycle time, incl. idle gaps); gas avg/peak
        # from mes_gas_log over the part's weld window.  mes_weld_log.ts is
        # tz-aware, mes_gas_log.ts is naive IST -> cast the weld window to naive
        # IST for the gas join.
        cur.execute(f'''
            WITH parts AS (
              SELECT part_count, MIN(ts) AS t0, MAX(ts) AS t1, MAX(shift) AS shift,
                     COUNT(*) AS welds,
                     ROUND(AVG(weld_current)::numeric, 1) AS cur_avg,
                     ROUND(MIN(weld_current)::numeric, 1) AS cur_min,
                     ROUND(MAX(weld_current)::numeric, 1) AS cur_max,
                     ROUND(MAX(weld_peak_a)::numeric, 1)  AS peak,
                     BOOL_OR(is_ng) AS is_ng,
                     MAX(part_code) AS part_code, MAX(model) AS model
                FROM mes_weld_log
               WHERE station = %s AND record_date = %s {shift_cond}
               GROUP BY part_count
            ), ranked AS (
              SELECT *, ROUND(EXTRACT(EPOCH FROM (t1 - LAG(t1) OVER (ORDER BY t1)))::numeric, 1) AS ct_s
                FROM parts
            )
            SELECT r.part_count, r.t1 AS ts, r.shift, r.welds, r.ct_s,
                   r.cur_avg, r.cur_min, r.cur_max, r.peak, r.is_ng, r.part_code, r.model,
                   ROUND(AVG(gl.value)::numeric, 1) AS gas_avg,
                   ROUND(MAX(gl.value)::numeric, 1) AS gas_max
              FROM ranked r
              LEFT JOIN mes_gas_log gl
                ON gl.card_ip = %s AND gl.channel = %s
               AND gl.ts >= (r.t0 AT TIME ZONE 'Asia/Kolkata')
               AND gl.ts <= (r.t1 AT TIME ZONE 'Asia/Kolkata')
             GROUP BY r.part_count, r.t1, r.shift, r.welds, r.ct_s,
                      r.cur_avg, r.cur_min, r.cur_max, r.peak, r.is_ng, r.part_code, r.model
             ORDER BY r.t1 DESC
             LIMIT %s
        ''', params)
        _f = lambda v: float(v) if v is not None else None
        rows = [{"part": r["part_count"],
                 "ts": r["ts"].isoformat() if r["ts"] else None,
                 "shift": r["shift"], "welds": int(r["welds"] or 0),
                 "ct": _f(r["ct_s"]),
                 "cur_avg": _f(r["cur_avg"]), "cur_min": _f(r["cur_min"]),
                 "cur_max": _f(r["cur_max"]), "peak": _f(r["peak"]),
                 "gas_avg": _f(r["gas_avg"]), "gas_max": _f(r["gas_max"]),
                 "is_ng": bool(r["is_ng"]),
                 "part_code": r["part_code"], "model": r["model"]}
                for r in (cur.fetchall() or [])]
        conn.rollback()
    return {**meta, "rows": rows, "has_gas": gas_ip is not None}
