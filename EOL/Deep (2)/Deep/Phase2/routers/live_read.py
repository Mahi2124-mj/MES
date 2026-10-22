"""
routers/live_read.py — "Read live values" for one machine, 2026-10-09.

Operator: "ek button har machine ke aage in machine section for read live value
of all registers on that machine."

The SA / FX5 PLCs accept ONE MC session and the collector holds it: a read from
here would time out, and taking the session away drops the collector for
minutes (seen 9-Oct on Nut Welding).  So the API never opens a PLC connection.
It queues a request in mes_plc_read_requests; the line's collector reads every
configured register of that machine on its OWN connection and writes the answer
(collector_engine._serve_read_requests / _serve_sub_reads).  Read-only: nothing
is ever written to a PLC.
"""
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/live-read", tags=["live-read"])

READ_ROLES = {"admin", "plant_head", "production_incharge", "section_incharge",
              "department", "quality_incharge"}
WAIT_S = 12.0

# (column, label, length column) — every address-type field a machine can carry
FIELDS = [
    ("ok_bit_address", "OK bit", None), ("ng_bit_address", "NG bit", None),
    ("ok_data_register", "OK count register", None), ("ng_data_register", "NG count register", None),
    ("status_address", "Status", None), ("model_address", "Model", None),
    ("sensor_ok_address", "Sensor OK", None), ("process_seq_address", "Process sequence", None),
    ("override_address", "Override", None), ("shift_reset_bit", "Shift reset bit", None),
    ("reject_bit", "Reject bit", None), ("fi_fetch_bit", "Final fetch bit", None),
    ("fi_sa_ng_bit", "Final SA-NG bit", None),
    ("sa_fetch_bit", "SA fetch bit", None), ("sa_ok_bit", "SA OK bit", None),
    ("sa_ng_bit", "SA NG bit", None), ("sa_ng_trigger_bit", "SA NG trigger bit", None),
    ("sa_result_register", "SA result register", None),
    ("sa_shift_data_bit", "SA shift data bit", None), ("sa_shift_reset_bit", "SA shift reset bit", None),
    ("sa_part_code_addr", "SA part code", "sa_part_code_len"),
    ("sa_data_addr", "SA data", "sa_data_len"), ("sa_time_addr", "SA time", "sa_time_len"),
    ("lc_result_reg", "Load-cell result", None), ("lc_value_reg", "Load-cell value", None),
    ("qr_reg", "QR register", None),
]


@once
def _ensure_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS mes_plc_read_requests (
                           id            SERIAL PRIMARY KEY,
                           line_id       INTEGER NOT NULL,
                           plc_config_id INTEGER NOT NULL,
                           addresses     JSONB NOT NULL,
                           requested_by  VARCHAR(80),
                           requested_at  TIMESTAMPTZ DEFAULT now(),
                           status        VARCHAR(10) DEFAULT 'pending',
                           taken_at      TIMESTAMPTZ,
                           done_at       TIMESTAMPTZ,
                           result        JSONB,
                           error         TEXT)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS ix_plc_read_req_pending
                         ON mes_plc_read_requests (line_id, status, requested_at)""")
        conn.commit()


def _machine(cur, plc_config_id):
    cur.execute("SELECT * FROM mes_plc_configs WHERE id = %s", (plc_config_id,))
    m = cur.fetchone()
    if not m:
        raise HTTPException(404, "Machine not found")
    return dict(m)


def _addresses(m):
    out, seen = [], set()
    for col, label, len_col in FIELDS:
        a = str(m.get(col) or "").strip().upper()
        if not a or (a, col) in seen:
            continue
        seen.add((a, col))
        n = int(m.get(len_col) or 1) if len_col else 1
        out.append({"addr": a, "label": label, "field": col, "len": max(1, min(n, 32))})
    return out


def _row(cur, rid):
    cur.execute("""SELECT id, status, result, error, requested_at, taken_at, done_at
                     FROM mes_plc_read_requests WHERE id = %s""", (rid,))
    r = cur.fetchone()
    return dict(r) if r else None


def _out(r, m=None):
    took = None
    if r.get("done_at") and r.get("requested_at"):
        took = int((r["done_at"] - r["requested_at"]).total_seconds() * 1000)
    res = r.get("result")
    if isinstance(res, str):
        res = json.loads(res)
    o = {"request_id": r["id"], "status": r["status"], "error": r.get("error"),
         "values": res, "took_ms": took,
         "read_at": r["done_at"].isoformat(timespec="seconds") if r.get("done_at") else None}
    if m:
        o.update(machine=m.get("machine_name"), plc=f"{m.get('plc_ip')}:{m.get('plc_port')}")
    return o


@router.post("/{plc_config_id}")
def request_read(plc_config_id: int, user=Depends(get_current_user)):
    """Queue a read of every configured register of this machine and wait up to
    12 s for the collector's answer (pending after that: poll the GET)."""
    if (user.get("role") or "") not in READ_ROLES:
        raise HTTPException(403, "Not allowed to read PLC values")
    _ensure_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        m = _machine(cur, plc_config_id)
        addrs = _addresses(m)
        if not addrs:
            raise HTTPException(400, "This machine has no register addresses configured")
        cur.execute("SELECT collector_status FROM mes_lines WHERE id = %s", (m["line_id"],))
        ln = cur.fetchone() or {}
        cur.execute("""INSERT INTO mes_plc_read_requests (line_id, plc_config_id, addresses, requested_by)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (m["line_id"], plc_config_id, json.dumps(addrs), user.get("username")))
        rid = cur.fetchone()["id"]
        conn.commit()
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        time.sleep(0.5)
        with get_conn() as conn:
            cur = dict_cursor(conn)
            r = _row(cur, rid)
            conn.rollback()
        if r and r["status"] in ("done", "error"):
            return _out(r, m)
    out = _out(r or {"id": rid, "status": "pending"}, m)
    out["error"] = ("The line's collector has not answered yet. "
                    + ("It is stopped." if ln.get("collector_status") == "stopped"
                       else "It may still be on the old code (needs one restart) or busy reconnecting."))
    return out


@router.get("/result/{request_id}")
def get_result(request_id: int, user=Depends(get_current_user)):
    if (user.get("role") or "") not in READ_ROLES:
        raise HTTPException(403, "Not allowed to read PLC values")
    _ensure_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        r = _row(cur, request_id)
        conn.rollback()
    if not r:
        raise HTTPException(404, "Request not found")
    return _out(r)
