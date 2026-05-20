"""
routers/machines.py
===================
Machine master list (zone × line × machine_no → machine_name).

Source-of-truth was imported from NF2's `zones.json`.  Used by the
Maintenance closure form to auto-fill the Machine Name when the user
types a Machine No.

Endpoints
---------
GET  /api/machines/by-line/{line_id}     → list of machines for a MES line
                                            (resolves zone + NF2 line name
                                            mapping, with fuzzy fallback)
GET  /api/machines/?zone=X&line=Y        → raw lookup (no MES line FK)
GET  /api/machines/lookup?line_id=X&no=N → single machine_name for type-ahead
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/machines", tags=["machines"])


def _resolve_nf2_line(conn, line_id: int):
    """Given a MES line_id, return (zone_name_for_lookup, line_name_for_lookup).

    Resolution order:
      1. mes_lines.nf2_line_name (admin-set explicit override)  → use that
      2. fuzzy: first-token of mes_lines.line_name + zone match  → return that
      3. fall back to the raw mes_lines.line_name
    """
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT l.line_name, l.nf2_line_name, z.zone_name
          FROM mes_lines l
          LEFT JOIN mes_zones z ON z.id = l.zone_id
         WHERE l.id = %s
    """, (line_id,))
    row = cur.fetchone()
    if not row:
        return None, None

    zone_name = row["zone_name"] or ""
    if row.get("nf2_line_name"):
        return zone_name, row["nf2_line_name"]

    # Fuzzy: take the first token (split on '-', '_', ' ') and match prefix
    raw = row["line_name"] or ""
    token = (raw.split("-", 1)[0].split("_", 1)[0].split(" ", 1)[0] or "").strip()
    if token:
        cur.execute("""
            SELECT line_name FROM mes_machines
             WHERE LOWER(zone_name) = LOWER(%s)
               AND line_name ILIKE %s
             LIMIT 1
        """, (zone_name, token + "%"))
        m = cur.fetchone()
        if m:
            return zone_name, m["line_name"]

    return zone_name, raw


@router.get("/by-line/{line_id}")
def list_for_line(line_id: int, user=Depends(get_current_user)):
    """Return every machine row for the (zone, line) combo of a MES line —
    sorted by machine_no.  Frontend keeps this list in memory and does
    client-side lookup as the user types a Machine No."""
    with get_conn() as conn:
        zone_name, nf2_line = _resolve_nf2_line(conn, line_id)
        if not zone_name or not nf2_line:
            return {"zone_name": None, "line_name": None, "machines": []}

        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, source_id, zone_name, line_name, machine_no,
                   machine_name, is_active
              FROM mes_machines
             WHERE LOWER(zone_name) = LOWER(%s)
               AND LOWER(line_name) = LOWER(%s)
               AND is_active = TRUE
             ORDER BY machine_no
        """, (zone_name, nf2_line))
        return {
            "zone_name": zone_name,
            "line_name": nf2_line,
            "machines":  cur.fetchall(),
        }


@router.get("/")
def list_machines(zone: Optional[str] = None,
                  line: Optional[str] = None,
                  user=Depends(get_current_user)):
    """Raw lookup by zone_name + line_name.  Either or both can be omitted
    to widen the result.  Used for ad-hoc admin browsing."""
    where = ["is_active = TRUE"]
    params: list = []
    if zone:
        where.append("LOWER(zone_name) = LOWER(%s)"); params.append(zone)
    if line:
        where.append("LOWER(line_name) = LOWER(%s)"); params.append(line)

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT id, zone_name, line_name, machine_no, machine_name
              FROM mes_machines
             WHERE {' AND '.join(where)}
             ORDER BY zone_name, line_name, machine_no
             LIMIT 1000
        """, params)
        return cur.fetchall()


@router.get("/lookup")
def lookup_one(line_id: int = Query(...),
               no: int = Query(..., description="machine_no (1-based per line)"),
               user=Depends(get_current_user)):
    """Single-machine lookup (zone resolved from MES line FK).  Used by
    the closure form on every keystroke if needed."""
    with get_conn() as conn:
        zone_name, nf2_line = _resolve_nf2_line(conn, line_id)
        if not zone_name or not nf2_line:
            raise HTTPException(404, "Line not found or not mapped")

        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, machine_no, machine_name
              FROM mes_machines
             WHERE LOWER(zone_name) = LOWER(%s)
               AND LOWER(line_name) = LOWER(%s)
               AND machine_no = %s
               AND is_active = TRUE
             LIMIT 1
        """, (zone_name, nf2_line, no))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Machine not found")
        return row


# ═══════════════════════════════════════════════════════════════════════
# MACHINE PROCESS GRAPHS (per-process actual vs. target)
# ═══════════════════════════════════════════════════════════════════════
# Operator's request: "ek machine me 5 process h kisi me 7 h — sabka bar
# graph chahiye, target line ke saath. Machine setup ke saath hi process
# count + name + target value + actual value ka PLC bit set karne ka
# option chahiye."
#
# Schema:
#   mes_machine_processes        — per-machine config (1 row per process)
#   mes_machine_process_log      — collector writes timestamped samples
#                                  for each process; frontend graphs read
#                                  from here.
# ═══════════════════════════════════════════════════════════════════════

class ProcessRow(BaseModel):
    process_no:      int
    process_name:    str
    target_value:    float = 0
    actual_register: str   = ""        # e.g. "D2000", "M100", "Y10"
    register_type:   str   = "word"    # "word" | "bit"
    is_active:       bool  = True


class ProcessBulk(BaseModel):
    processes: List[ProcessRow]


def _ensure_process_tables(conn) -> None:
    """Create both process tables if missing.  Idempotent."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_machine_processes (
            id              SERIAL      PRIMARY KEY,
            machine_id      INTEGER     NOT NULL,
            process_no      INTEGER     NOT NULL,
            process_name    TEXT        NOT NULL,
            target_value    NUMERIC(12,2) DEFAULT 0,
            actual_register TEXT        NOT NULL DEFAULT '',
            register_type   TEXT        NOT NULL DEFAULT 'word',
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (machine_id, process_no)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_machine_process_log (
            id           BIGSERIAL   PRIMARY KEY,
            process_id   INTEGER     NOT NULL,
            actual_value NUMERIC(12,2),
            sampled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_mes_machine_process_log_pid_ts
            ON mes_machine_process_log (process_id, sampled_at DESC)
    """)
    # Per-pulse log (BIT only) — one row per ON event so the Process
    # Graphs page can render "spike width = ON duration" like the main
    # cycle-time chart.  Added 2026-05-13.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_machine_process_pulses (
            id           BIGSERIAL   PRIMARY KEY,
            process_id   INTEGER     NOT NULL,
            started_at   TIMESTAMPTZ NOT NULL,
            duration_ms  INTEGER     NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_mes_machine_process_pulses_pid_ts
            ON mes_machine_process_pulses (process_id, started_at DESC)
    """)
    conn.commit()


@router.get("/{machine_id}/processes")
def list_processes(machine_id: int, user=Depends(get_current_user)):
    """Return the process config for a machine, with each row enriched by
    the LATEST sampled actual value (NULL if collector hasn't logged yet)."""
    with get_conn() as conn:
        _ensure_process_tables(conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT p.*,
                   l.actual_value AS latest_value,
                   l.sampled_at   AS latest_at
              FROM mes_machine_processes p
              LEFT JOIN LATERAL (
                  SELECT actual_value, sampled_at
                    FROM mes_machine_process_log
                   WHERE process_id = p.id
                   ORDER BY sampled_at DESC
                   LIMIT 1
              ) l ON TRUE
             WHERE p.machine_id = %s
             ORDER BY p.process_no
        """, (machine_id,))
        return cur.fetchall()


@router.put("/{machine_id}/processes")
def replace_processes(machine_id: int,
                      body: ProcessBulk,
                      admin=Depends(require_admin)):
    """Admin-only: bulk-replace the entire process list for a machine.

    Caller sends the full intended list — anything missing here gets
    deleted (so process_no=3 disappearing from the payload removes its
    row, and its history rows too via ON DELETE CASCADE).  Names get
    trimmed; targets clamped to ≥0; register defaults to 'word'."""
    with get_conn() as conn:
        _ensure_process_tables(conn)
        cur = conn.cursor()

        # Sanity-check the machine exists.  `machine_id` refers to
        # mes_plc_configs.id (the PLC-machine row admin edits in the
        # Machines tab), NOT the legacy mes_machines master list.
        cur.execute("SELECT id FROM mes_plc_configs WHERE id = %s", (machine_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Machine not found")

        # Hard-replace strategy: wipe old config + their log entries, insert fresh.
        cur.execute("""
            DELETE FROM mes_machine_process_log
             WHERE process_id IN (
                 SELECT id FROM mes_machine_processes WHERE machine_id = %s
             )
        """, (machine_id,))
        cur.execute("DELETE FROM mes_machine_processes WHERE machine_id = %s",
                    (machine_id,))

        seen = set()
        for row in body.processes:
            pno  = int(row.process_no)
            if pno in seen:
                continue            # duplicate process_no — skip
            seen.add(pno)
            name = (row.process_name or "").strip() or f"Process {pno}"
            tgt  = max(0.0, float(row.target_value or 0))
            reg  = (row.actual_register or "").strip().upper()
            rtyp = (row.register_type or "word").strip().lower()
            if rtyp not in ("word", "bit"):
                rtyp = "word"
            cur.execute("""
                INSERT INTO mes_machine_processes
                    (machine_id, process_no, process_name, target_value,
                     actual_register, register_type, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (machine_id, pno, name, tgt, reg, rtyp, bool(row.is_active)))
        conn.commit()

    # Return the freshly-stored list so the frontend doesn't need a re-GET.
    return list_processes(machine_id, user={"id": 0})


@router.get("/{machine_id}/processes/log")
def process_log(machine_id: int,
                hours: int = Query(8, ge=1, le=168),
                user=Depends(get_current_user)):
    """Return time-bucketed samples for every process on this machine,
    suitable for stacked bar charts (actual per slot vs. configured
    target).  Bucket size is 1 hour by default; the frontend just
    consumes the rows in order.

    Output shape:
        [
          { process_id: 7, process_name: "Pressing", target: 100,
            samples: [{bucket:"2026-05-04T10:00", actual: 87}, ...] },
          ...
        ]
    """
    from datetime import datetime, timedelta
    with get_conn() as conn:
        _ensure_process_tables(conn)
        cur = dict_cursor(conn)
        # Get the process config first — register_type drives the
        # aggregation choice below (SUM for pulse counts, MAX for
        # cumulative word values).
        cur.execute("""
            SELECT id, process_no, process_name, target_value, register_type
              FROM mes_machine_processes
             WHERE machine_id = %s
             ORDER BY process_no
        """, (machine_id,))
        procs = cur.fetchall()
        if not procs:
            return []

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Bucket size = 1 minute always.  Operator wants one bar PER
        # MINUTE (since each PLC sample IS a minute's pulse count) —
        # not aggregated 5/10-min averages.  For very wide windows
        # (e.g. 72h) this produces lots of bars; the frontend's
        # auto-thin bar renderer handles that — bars get narrow but
        # the per-minute granularity is preserved.
        bucket_minutes = 1

        out = []
        for p in procs:
            rtyp = (p.get("register_type") or "word").lower()
            pulses = []
            samples = []

            # BIT: per-pulse rendering — return one row per ON event so the
            # frontend can draw "spike width = ON duration" like the main
            # cycle-time graph.  Per-minute aggregation is dropped.
            if rtyp == "bit":
                cur.execute("""
                    SELECT started_at, duration_ms
                      FROM mes_machine_process_pulses
                     WHERE process_id = %s
                       AND started_at >= %s
                     ORDER BY started_at ASC
                """, (p["id"], cutoff))
                pulses = [
                    {"started_at": r["started_at"].isoformat() if r["started_at"] else None,
                     "duration_ms": int(r["duration_ms"] or 0),
                     "duration_s":  round(float(r["duration_ms"] or 0) / 1000.0, 2)}
                    for r in cur.fetchall()
                ]
            else:
                # WORD: MAX = latest cumulative reading in the bucket.
                # Custom-width buckets via integer-math floor() of the
                # epoch second (date_trunc only supports fixed scales).
                cur.execute(f"""
                    SELECT
                      to_timestamp(
                        floor(extract(epoch from sampled_at) / (60 * %s)) * (60 * %s)
                      ) AS bucket,
                      MAX(actual_value)::NUMERIC(12,2) AS actual
                    FROM mes_machine_process_log
                    WHERE process_id = %s
                      AND sampled_at >= %s
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """, (bucket_minutes, bucket_minutes, p["id"], cutoff))
                samples = [
                    {"bucket": r["bucket"].isoformat() if r["bucket"] else None,
                     "actual": float(r["actual"]) if r["actual"] is not None else None}
                    for r in cur.fetchall()
                ]

            out.append({
                "process_id":     p["id"],
                "process_no":     p["process_no"],
                "process_name":   p["process_name"],
                "target":         float(p["target_value"] or 0),
                "register_type":  rtyp,
                "bucket_minutes": bucket_minutes,
                "window_hours":   hours,
                "window_start":   cutoff.isoformat(),
                "samples":        samples,
                "pulses":         pulses,
            })
        return out
