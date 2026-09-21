# ════════════════════════════════════════════════════════════════
# routers/py_config.py
# ════════════════════════════════════════════════════════════════
"""
Model-driven, per-machine Poka-Yoke config  →  /api/py-config/...

CORRECT flow (confirmed 2026-07-30):
    Model Master (existing) → models assigned to LINES (mes_model_mappings,
    model_number = the value the PLC model register D6048 reports) → the PLC
    sends the CURRENT model number as status → for EACH machine, for EACH model,
    each PY check has a D-bit + a DESIRED register value (0/1/2), for 1 or 2
    registers.  Every machine is INDEPENDENT:
       YNC · Final · Model 9 · Locate Pin → D401 desired 2
       YCA_SS · Final · Model 9 · Locate Pin → D411 desired 1   (separate)

Single editable table `mes_py_config` (parallel — touches no old PY table):
    (line_id, plc_config_id, model_number, py_no) → d_bit + reg_count + desired(0/1/2).

Models come from `mes_model_mappings` (per line, as-is).  PY catalog / model
master stay as-is.  The OLD /api/poka-yoke + /api/py-stations run in parallel
until this is verified end-to-end (sensing → fullscreen) and cut over.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/py-config", tags=["py-config"])


# ══════════════════════════════════════════════════════════════
# SCHEMA — sensing_bits (2026-08-14)
# ══════════════════════════════════════════════════════════════
# The SENSOR X-bit lived only on mes_py_master, so it was shared by every line
# that used a given PY.  In the plant the same check is wired to a different
# input per line (D401/X15 here, a different pin there), and there was no way
# to say so — the master value was all anyone got.  This column makes the
# sensor a per-(line, station, model, PY) value exactly like d_bit already is.
#
# NULL means "not overridden" → fall back to the master's sensing_bits, so the
# 434 rows that already exist keep behaving exactly as they do today.
_SCHEMA_READY = False


@once
def _ensure_schema() -> None:
    """Add sensing_bits if an older deploy created the table without it.
    Runs once per process; ALTER ... IF NOT EXISTS is a no-op afterwards."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        with get_conn() as conn:
            conn.cursor().execute(
                "ALTER TABLE mes_py_config "
                "ADD COLUMN IF NOT EXISTS sensing_bits VARCHAR(120)")
            conn.commit()
    except Exception:
        # A read-only replica or a missing table must not take the router down;
        # the queries below simply return NULL for the column in that case.
        pass
    _SCHEMA_READY = True


# ══════════════════════════════════════════════════════════════
# META — dropdown sources for the editor
# ══════════════════════════════════════════════════════════════
@router.get("/meta/lines")
def meta_lines(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.id, l.line_code AS "lineCode", l.line_name AS "lineName",
                   z.zone_name AS "zoneName"
            FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
            WHERE COALESCE(l.is_active, true)
            ORDER BY l.line_code
        """)
        return cur.fetchall()


@router.get("/meta/models")
def meta_models(line_id: int = Query(...), user=Depends(get_current_user)):
    """The models ASSIGNED to this line (mes_model_mappings) — model_number is
    the value the PLC model register (D6048) reports for that model."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT model_number AS "modelNumber", model_name AS "modelName"
            FROM mes_model_mappings
            WHERE line_id = %s
            ORDER BY model_number
        """, (line_id,))
        return cur.fetchall()


@router.get("/meta/machines")
def meta_machines(line_id: int = Query(...), user=Depends(get_current_user)):
    """Machines/stations of the line (mes_plc_configs) whose PLC the runtime reads."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, machine_name AS "machineName", machine_seq AS "machineSeq",
                   plc_ip AS "plcIp", model_address AS "modelReg"
            FROM mes_plc_configs
            WHERE line_id = %s
            ORDER BY machine_seq NULLS LAST, id
        """, (line_id,))
        return cur.fetchall()


@router.get("/meta/stations")
def meta_stations(line_id: int = Query(...), user=Depends(get_current_user)):
    """The STATION codes of the line, from the machine master (mes_machine_master):
    SS_01..SS_08 for a Seat-Slider line (SS_08 = final inspection), RC_01..RC_0n for
    a recliner line. These replace raw machine names so PY matching stays uniform
    across lines — the same station code (e.g. SS_08) means the same station."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            WITH ln AS (
                SELECT split_part(line_code, '-', 1) AS pfx, zone_id
                FROM mes_lines WHERE id = %s
            )
            SELECT station_code AS "stationCode",
                   min(sort_order) AS "seq",
                   (array_agg(machine_name ORDER BY sort_order))[1] AS "machineName",
                   (array_agg(machine_no   ORDER BY sort_order))[1] AS "fullCode"
            FROM (
                SELECT substring(mm.machine_no from '(SS_[0-9]+|RC_[0-9]+)') AS station_code,
                       mm.machine_name, mm.machine_no, mm.sort_order
                FROM mes_machine_master mm, ln
                WHERE upper(mm.line) LIKE upper(ln.pfx) || '%%'
                  AND mm.machine_no ~ CASE WHEN ln.zone_id = 1
                                           THEN '_SS_[0-9]' ELSE '_RC_[0-9]' END
            ) t
            WHERE station_code IS NOT NULL
            GROUP BY station_code
            ORDER BY min(sort_order)
        """, (line_id,))
        return cur.fetchall()


@router.get("/meta/py-master")
def meta_py_master(line_id: int = Query(...),
                   station_code: Optional[str] = Query(None),
                   user=Depends(get_current_user)):
    """The CONFIGURED PY checks from the PY Master (mes_py_master) — the catalog the
    user picks from (auto-fills py_no, py_name, d_bit, reg_count, sensing_bits).
    Scoped to the line's zone, and — when a station is chosen — to that station's PYs
    (mes_py_master.station_code, e.g. all final-inspection PYs are tagged SS_08)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT m.id, m.py_no AS "pyNo", m.description AS "pyName",
                   COALESCE(NULLIF(m.bit, ''), m.register) AS "dBit",
                   COALESCE(m.register_count, 1) AS "regCount",
                   m.sensing_bits AS "sensingBits", m.model_type AS "modelType",
                   m.side, m.machine_name AS "machineName",
                   m.station_code AS "stationCode", m.zone_id AS "zoneId"
            FROM mes_py_master m
            WHERE COALESCE(m.is_active, true)
              AND (m.zone_id IS NULL
                   OR m.zone_id = (SELECT zone_id FROM mes_lines WHERE id = %s))
              AND (%s IS NULL OR m.station_code = %s)
            ORDER BY m.py_no NULLS LAST, m.id
        """, (line_id, station_code, station_code))
        return cur.fetchall()


# ══════════════════════════════════════════════════════════════
# READ — the editable config grid
# ══════════════════════════════════════════════════════════════
@router.get("")
@router.get("/")
def list_config(line_id: int = Query(...),
                model_number: Optional[int] = Query(None),
                plc_config_id: Optional[int] = Query(None),
                station_label: Optional[str] = Query(None),
                user=Depends(get_current_user)):
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT c.id, c.line_id AS "lineId",
                   c.plc_config_id AS "plcConfigId",
                   p.machine_name  AS "machineName",
                   c.station_label AS "stationLabel", c.station_seq AS "stationSeq",
                   c.model_number AS "modelNumber", c.model_name AS "modelName",
                   c.py_seq AS "pySeq", c.py_no AS "pyNo", c.py_name AS "pyName",
                   c.d_bit AS "dBit", c.reg_count AS "regCount",
                   c.sensing_bits AS "sensingBits",
                   c.desired_value AS "desiredValue", c.desired_value_2 AS "desiredValue2",
                   c.enabled
            FROM mes_py_config c
            LEFT JOIN mes_plc_configs p ON p.id = c.plc_config_id
            WHERE c.line_id = %s
              AND (%s IS NULL OR c.model_number = %s)
              AND (%s IS NULL OR c.plc_config_id = %s)
              AND (%s IS NULL OR c.station_label = %s)
            ORDER BY c.station_seq NULLS LAST, c.station_label,
                     c.model_number, c.py_seq NULLS LAST, c.id
        """, (line_id, model_number, model_number, plc_config_id, plc_config_id,
              station_label, station_label))
        return cur.fetchall()


# ══════════════════════════════════════════════════════════════
# WRITE — create / edit / delete (all editable)
# ══════════════════════════════════════════════════════════════
class PyConfigIn(BaseModel):
    line_id:         int
    plc_config_id:   Optional[int] = None
    station_label:   Optional[str] = None
    station_seq:     Optional[int] = None
    model_number:    Optional[int] = None
    model_name:      Optional[str] = None
    py_seq:          Optional[int] = None
    py_no:           Optional[str] = None
    py_name:         Optional[str] = None
    d_bit:           Optional[str] = None
    reg_count:       Optional[int] = 1
    # Per-line sensor override.  None = inherit mes_py_master.sensing_bits.
    sensing_bits:    Optional[str] = None
    desired_value:   Optional[str] = None
    desired_value_2: Optional[str] = None
    enabled:         Optional[bool] = True


_COLS = ("line_id", "plc_config_id", "station_label", "station_seq",
         "model_number", "model_name", "py_seq", "py_no", "py_name",
         "d_bit", "reg_count", "sensing_bits",
         "desired_value", "desired_value_2", "enabled")


@router.post("", status_code=201)
@router.post("/", status_code=201)
def create_config(body: PyConfigIn, admin=Depends(require_admin)):
    _ensure_schema()
    d = body.dict()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cols = ", ".join(_COLS); ph = ", ".join(["%s"] * len(_COLS))
        cur.execute(f"INSERT INTO mes_py_config ({cols}) VALUES ({ph}) RETURNING id",
                    tuple(d[c] for c in _COLS))
        return {"ok": True, "id": cur.fetchone()["id"]}


@router.post("/bulk", status_code=201)
def create_bulk(rows: List[PyConfigIn], admin=Depends(require_admin)):
    if not rows:
        return {"ok": True, "inserted": 0}
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cols = ", ".join(_COLS); ph = ", ".join(["%s"] * len(_COLS))
        for body in rows:
            d = body.dict()
            cur.execute(f"INSERT INTO mes_py_config ({cols}) VALUES ({ph})",
                        tuple(d[c] for c in _COLS))
        return {"ok": True, "inserted": len(rows)}


@router.put("/{cfg_id}")
def update_config(cfg_id: int, body: PyConfigIn, admin=Depends(require_admin)):
    _ensure_schema()
    d = body.dict()
    sets = ", ".join(f"{c}=%s" for c in _COLS) + ", updated_at=now()"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"UPDATE mes_py_config SET {sets} WHERE id=%s",
                    tuple(d[c] for c in _COLS) + (cfg_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "config row not found")
        return {"ok": True}


@router.patch("/{cfg_id}/enabled")
def toggle_enabled(cfg_id: int, enabled: bool = Query(...), admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("UPDATE mes_py_config SET enabled=%s, updated_at=now() WHERE id=%s",
                    (enabled, cfg_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "config row not found")
        return {"ok": True, "enabled": enabled}


@router.delete("/{cfg_id}")
def delete_config(cfg_id: int, admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("DELETE FROM mes_py_config WHERE id=%s", (cfg_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "config row not found")
        return {"ok": True}


# ══════════════════════════════════════════════════════════════
# LIVE — runtime status for the fullscreen (sensing → fullscreen).
# Phase 4 attaches the collector's real PLC read (current model_number from
# D6048 + each PY's D-bit vs desired).  For now returns the model's configured
# rows; status defaults to "unknown" until the runtime feed is wired.
# ══════════════════════════════════════════════════════════════
@router.get("/live/{line_id}")
def live(line_id: int, model_number: Optional[int] = Query(None),
         user=Depends(get_current_user)):
    _ensure_schema()
    _ensure_live_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # Serve the PUBLISHED snapshot, not the editable draft — see
        # _ensure_live_table.  A line that has never been published falls back
        # to its draft so nothing changes for lines nobody has touched yet.
        src = "mes_py_config_live" if _line_is_published(conn, line_id) else "mes_py_config"
        cur.execute(f"""
            SELECT c.id, c.station_label AS "stationLabel", c.station_seq AS "stationSeq",
                   c.model_number AS "modelNumber",
                   c.py_no AS "pyNo", c.py_name AS "pyName",
                   c.d_bit AS "dBit", c.reg_count AS "regCount",
                   -- Per-line sensor wins; fall back to the PY master so rows
                   -- that were never overridden read exactly as before.
                   COALESCE(NULLIF(c.sensing_bits, ''), m.sensing_bits)
                       AS "sensingBits",
                   c.desired_value AS "desiredValue", c.desired_value_2 AS "desiredValue2",
                   p.plc_ip AS "plcIp", p.machine_name AS "machineName",
                   'unknown'::text AS status
            FROM {src} c
            LEFT JOIN mes_plc_configs p ON p.id = c.plc_config_id
            LEFT JOIN mes_py_master   m ON m.py_no = c.py_no
            WHERE c.line_id = %s AND c.enabled
              AND (%s IS NULL OR c.model_number = %s)
            ORDER BY c.station_seq NULLS LAST, c.py_seq NULLS LAST, c.id
        """, (line_id, model_number, model_number))
        return {"lineId": line_id, "modelNumber": model_number,
                "source": "published" if src.endswith("_live") else "draft",
                "pys": cur.fetchall()}


# ══════════════════════════════════════════════════════════════
# DRAFT → PUBLISHED  (2026-08-17)
# ══════════════════════════════════════════════════════════════
# Editing mes_py_config used to change what the LIVE poka-yoke check reads the
# instant Save was pressed.  Half-finished config therefore reached the floor
# mid-shift: a row saved with the D-bit typed but the desired value not yet
# set would start failing parts immediately.
#
# So the editor now writes a DRAFT and the runtime reads a PUBLISHED SNAPSHOT.
# Nothing an admin types affects production until they press "Update to
# Software".  Until then the line keeps running exactly the config it has been
# running — which is the point: work must not be disturbed mid-edit.
_LIVE_TABLE_READY = False


def _ensure_live_table() -> None:
    """Snapshot table: identical shape to mes_py_config plus published_at."""
    global _LIVE_TABLE_READY
    if _LIVE_TABLE_READY:
        return
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mes_py_config_live
                    (LIKE mes_py_config INCLUDING DEFAULTS)
            """)
            cur.execute("ALTER TABLE mes_py_config_live "
                        "ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ DEFAULT NOW()")
            cur.execute("ALTER TABLE mes_py_config_live "
                        "ADD COLUMN IF NOT EXISTS published_by TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_pycfg_live_line "
                        "ON mes_py_config_live (line_id, enabled)")
            conn.commit()
        _LIVE_TABLE_READY = True
    except Exception as exc:
        print(f"[PY-CONFIG] live snapshot table unavailable: {exc}")


def _line_is_published(conn, line_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM mes_py_config_live WHERE line_id=%s LIMIT 1", (line_id,))
    return cur.fetchone() is not None


# The columns that decide what the PLC check actually does.  Cosmetic edits
# (a renamed PY) are not what "unpublished changes" should warn about, but
# these are, so the diff is computed on exactly this set.
_LIVE_COLS = ("station_label", "station_seq", "model_number", "py_seq", "py_no",
              "d_bit", "reg_count", "sensing_bits",
              "desired_value", "desired_value_2", "enabled")


@router.get("/publish-status")
def publish_status(line_id: int = Query(...), user=Depends(get_current_user)):
    """How far the draft has drifted from what the floor is running."""
    _ensure_schema(); _ensure_live_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if not _line_is_published(conn, line_id):
            cur.execute("SELECT count(*) AS n FROM mes_py_config WHERE line_id=%s", (line_id,))
            n = int((cur.fetchone() or {}).get("n") or 0)
            return {"lineId": line_id, "published": False, "pending": n,
                    "publishedAt": None,
                    "note": "Never published — the line is still running the draft."}
        cols = ", ".join(_LIVE_COLS)
        cur.execute(f"""
            SELECT
              (SELECT count(*) FROM (
                  SELECT {cols} FROM mes_py_config      WHERE line_id=%s
                  EXCEPT
                  SELECT {cols} FROM mes_py_config_live WHERE line_id=%s) d) AS added,
              (SELECT count(*) FROM (
                  SELECT {cols} FROM mes_py_config_live WHERE line_id=%s
                  EXCEPT
                  SELECT {cols} FROM mes_py_config      WHERE line_id=%s) d) AS removed,
              (SELECT max(published_at) FROM mes_py_config_live WHERE line_id=%s) AS published_at
        """, (line_id, line_id, line_id, line_id, line_id))
        r = cur.fetchone() or {}
        pending = int(r.get("added") or 0) + int(r.get("removed") or 0)
        return {"lineId": line_id, "published": True, "pending": pending,
                "publishedAt": r.get("published_at")}


@router.post("/publish")
def publish_config(line_id: int = Query(...), admin=Depends(require_admin)):
    """Push this line's draft to the floor.  One transaction, so the runtime
    never sees a half-replaced config."""
    _ensure_schema(); _ensure_live_table()
    cols = ", ".join(_LIVE_COLS)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_py_config_live WHERE line_id=%s", (line_id,))
        # Carry the DRAFT's id across.  The snapshot is a copy of a specific
        # draft row, and keeping the id is what lets anything downstream say
        # "the floor is running row 412" and have that mean the same row the
        # editor shows.  Without it the two sets could never be correlated.
        cur.execute(f"""
            INSERT INTO mes_py_config_live (id, line_id, plc_config_id, model_name,
                                            py_name, {cols}, published_by)
            SELECT id, line_id, plc_config_id, model_name,
                   py_name, {cols}, %s
              FROM mes_py_config WHERE line_id=%s
        """, ((admin or {}).get("username"), line_id))
        n = cur.rowcount
        conn.commit()
    return {"ok": True, "lineId": line_id, "published_rows": n}
