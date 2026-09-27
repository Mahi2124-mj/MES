# ════════════════════════════════════════════════════════════════
# routers/faults.py   →  /api/faults/...
# ════════════════════════════════════════════════════════════════
"""Fault Config (Phase 1 — CONFIG ONLY).

Per-machine fault list, organised by zone → line → machine, mirroring how
poka-yoke / model mapping is configured. Each fault is either a BIT or a DATA
REGISTER on the machine's PLC, plus the value that means "this fault is active".

Phase 2 (separate, not here) will have the collector read these on an NG bit and
write detected faults to a Fault History page. This router only stores the config.

Table (created on first use, additive — no other schema touched):
    mes_fault_config(id, zone_id, line_id, machine_id, machine_name,
                     fault_name, source_type['bit'|'register'], address,
                     trigger_value, is_active, created_at, updated_at)
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/faults", tags=["faults"])


@once
def _ensure(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_fault_config (
            id            SERIAL PRIMARY KEY,
            zone_id       INTEGER,
            line_id       INTEGER,
            machine_id    INTEGER NOT NULL,
            machine_name  TEXT,
            fault_name    TEXT NOT NULL,
            source_type   TEXT NOT NULL DEFAULT 'bit',   -- 'bit' | 'register'
            address       TEXT NOT NULL,                 -- PLC bit / register address
            trigger_value INTEGER,                       -- bit: ON value (1); register: the code that = this fault
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            updated_at    TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fault_cfg_machine "
                "ON mes_fault_config(machine_id)")


class FaultRow(BaseModel):
    fault_name:    str
    source_type:   str = "bit"          # 'bit' | 'register'
    address:       str
    trigger_value: Optional[int] = None
    is_active:     bool = True


class FaultSave(BaseModel):
    zone_id:      Optional[int] = None
    line_id:      Optional[int] = None
    machine_name: Optional[str] = None
    faults:       List[FaultRow] = []


@router.get("/config/{machine_id}")
def get_machine_faults(machine_id: int, user=Depends(get_current_user)):
    """The saved fault list for one machine."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        conn.commit()
        cur.execute(
            "SELECT id, zone_id, line_id, machine_id, machine_name, fault_name, "
            "       source_type, address, trigger_value, is_active "
            "FROM mes_fault_config WHERE machine_id = %s "
            "ORDER BY id",
            (machine_id,),
        )
        return {"machine_id": machine_id, "faults": cur.fetchall()}


@router.post("/config/{machine_id}")
def save_machine_faults(machine_id: int, body: FaultSave, admin=Depends(require_admin)):
    """Replace the whole fault list for one machine (the config editor sends the
    full edited list on Save). Runs in one transaction."""
    # basic validation
    clean: List[FaultRow] = []
    for f in body.faults:
        name = (f.fault_name or "").strip()
        addr = (f.address or "").strip()
        st   = (f.source_type or "bit").strip().lower()
        if st not in ("bit", "register"):
            st = "bit"
        if not name or not addr:
            continue   # skip blank rows
        clean.append(FaultRow(fault_name=name, source_type=st, address=addr,
                              trigger_value=f.trigger_value, is_active=f.is_active))

    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("DELETE FROM mes_fault_config WHERE machine_id = %s", (machine_id,))
        for f in clean:
            cur.execute(
                "INSERT INTO mes_fault_config "
                "  (zone_id, line_id, machine_id, machine_name, fault_name, "
                "   source_type, address, trigger_value, is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (body.zone_id, body.line_id, machine_id, body.machine_name,
                 f.fault_name, f.source_type, f.address, f.trigger_value, f.is_active),
            )
        conn.commit()
    return {"ok": True, "machine_id": machine_id, "count": len(clean)}


# ─────────────────────────────────────────────────────────────────────────────
# FAULT HISTORY  (2026-09-27)
# ─────────────────────────────────────────────────────────────────────────────
# The config above was written with "Phase 2 will read these" in the docstring,
# and nothing ever did — 158 assigned fault bits across 5 Final-Inspection
# machines sat unused.  The line's own collector now reads them (it holds the
# only PLC session) and writes an edge-pair per fault into mes_fault_history:
# rising edge opens a row, falling edge closes it with a duration.
#
# This is the read side: filters, a Pareto, and the same data as a workbook.
import io
from datetime import date, datetime, timedelta
from fastapi import Query
from fastapi.responses import StreamingResponse


@once
def _ensure_hist(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_fault_history (
            id            BIGSERIAL PRIMARY KEY,
            fault_id      INTEGER,
            machine_id    INTEGER NOT NULL,
            line_id       INTEGER,
            zone_id       INTEGER,
            machine_name  TEXT,
            fault_name    TEXT NOT NULL,
            source_type   TEXT,
            address       TEXT,
            trigger_value INTEGER,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at      TIMESTAMPTZ,
            duration_s    NUMERIC(12,2),
            record_date   DATE,
            shift_name    TEXT
        )""")
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_fault_hist_started ON mes_fault_history (started_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_fault_hist_line    ON mes_fault_history (line_id, started_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_fault_hist_machine ON mes_fault_history (machine_id, started_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_fault_hist_date    ON mes_fault_history (record_date, shift_name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fault_hist_open ON mes_fault_history "
        "(machine_id, fault_name) WHERE ended_at IS NULL",
    ):
        cur.execute(ddl)


def _hist_scope(cur, user):
    """Lines this user may see — same rule the rest of the app uses."""
    try:
        from routers.shift_compile import _accessible_lines
        return {r["id"] for r in _accessible_lines(cur, user)}
    except Exception:
        cur.execute("SELECT id FROM mes_lines WHERE COALESCE(is_active,TRUE)")
        return {r["id"] for r in cur.fetchall()}


def _hist_query(cur, user, date_from, date_to, zone_id, line_id, machine_id,
                fault, shift, state, min_s):
    allowed = _hist_scope(cur, user)
    if not allowed:
        return None, [], []
    where, args = ["h.line_id = ANY(%s)"], [list(allowed)]
    if date_from:
        where.append("h.started_at >= %s::date");        args.append(date_from)
    if date_to:
        where.append("h.started_at < (%s::date + 1)");   args.append(date_to)
    if zone_id:
        where.append("h.zone_id = %s");                  args.append(int(zone_id))
    if line_id:
        where.append("h.line_id = %s");                  args.append(int(line_id))
    if machine_id:
        where.append("h.machine_id = %s");               args.append(int(machine_id))
    if fault:
        where.append("h.fault_name ILIKE %s");           args.append(f"%{fault}%")
    if shift:
        where.append("upper(COALESCE(h.shift_name,'')) = %s"); args.append(shift.upper())
    if state == "open":
        where.append("h.ended_at IS NULL")
    elif state == "closed":
        where.append("h.ended_at IS NOT NULL")
    if min_s:
        where.append("COALESCE(h.duration_s, EXTRACT(EPOCH FROM (now() - h.started_at))) >= %s")
        args.append(float(min_s))
    return " AND ".join(where), args, allowed


@router.get("/history")
def fault_history(date_from: Optional[str] = None,
                  date_to:   Optional[str] = None,
                  zone_id:   Optional[int] = None,
                  line_id:   Optional[int] = None,
                  machine_id: Optional[int] = None,
                  fault:     Optional[str] = None,
                  shift:     Optional[str] = None,
                  state:     Optional[str] = None,     # open | closed
                  min_s:     Optional[float] = None,
                  page:      int = Query(1, ge=1),
                  page_size: int = Query(200, ge=1, le=2000),
                  user=Depends(get_current_user)):
    """Every fault the collectors have recorded, with a Pareto over the same
    filter.  `duration_s` is live for a fault that is still on."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_hist(cur)
        w, args, allowed = _hist_query(cur, user, date_from, date_to, zone_id,
                                       line_id, machine_id, fault, shift, state, min_s)
        if w is None:
            return {"rows": [], "total": 0, "pareto": [], "kpis": {},
                    "by_date": [], "by_shift": [],
                    "lines": [], "machines": [], "faults": []}

        dur = "COALESCE(h.duration_s, EXTRACT(EPOCH FROM (now() - h.started_at)))"

        #  2026-09-27 — operator: *"mujhe fault ki timing nhi chahiye blki
        #  frequency and occurance chahiye"*.  So everything below counts
        #  OCCURRENCES; duration is still stored, just not what this page is
        #  about.
        cur.execute(f"""SELECT count(*) n,
                               count(*) FILTER (WHERE h.ended_at IS NULL) open,
                               count(DISTINCT h.record_date) AS days,
                               min(h.started_at) AS first_at,
                               max(h.started_at) AS last_at
                          FROM mes_fault_history h WHERE {w}""", args)
        k = dict(cur.fetchone() or {})
        total = int(k.get("n") or 0)

        cur.execute(f"""SELECT h.fault_name, count(*) AS hits,
                               count(DISTINCT h.record_date) AS on_days,
                               count(DISTINCT h.machine_id) AS machines,
                               max(h.started_at) AS last_at
                          FROM mes_fault_history h WHERE {w}
                         GROUP BY h.fault_name
                         ORDER BY hits DESC, h.fault_name""", args)
        par = [dict(r) for r in cur.fetchall()]
        tot_n = float(total) or 1.0
        days = max(1, int(k.get("days") or 1))
        run = 0.0
        for p in par:
            p["hits"] = int(p["hits"])
            p["pct"] = round(100.0 * p["hits"] / tot_n, 1)
            run += p["pct"]
            p["cum_pct"] = round(min(run, 100.0), 1)
            #  How often it happens: occurrences per day it was seen at all,
            #  and across the whole filtered range.
            p["per_day_seen"] = round(p["hits"] / max(1, int(p["on_days"] or 1)), 2)
            p["per_day_range"] = round(p["hits"] / days, 2)

        cur.execute(f"""SELECT h.*, {dur} AS dur_s, l.line_name, z.zone_name
                          FROM mes_fault_history h
                          LEFT JOIN mes_lines l ON l.id = h.line_id
                          LEFT JOIN mes_zones z ON z.id = h.zone_id
                         WHERE {w}
                         ORDER BY h.started_at DESC
                         LIMIT %s OFFSET %s""", args + [page_size, (page - 1) * page_size])
        rows = [dict(r) for r in cur.fetchall()]

        #  Frequency over time and over shifts — "how often", the two cuts
        #  that answer it without mentioning duration anywhere.
        cur.execute(f"""SELECT h.record_date AS d, count(*) AS hits
                          FROM mes_fault_history h WHERE {w}
                         GROUP BY h.record_date ORDER BY h.record_date""", args)
        by_date = [{"date": str(r["d"]) if r["d"] else "", "hits": int(r["hits"])}
                   for r in cur.fetchall()]
        cur.execute(f"""SELECT COALESCE(NULLIF(h.shift_name,''),'?') AS sh,
                               count(*) AS hits
                          FROM mes_fault_history h WHERE {w}
                         GROUP BY 1 ORDER BY 1""", args)
        by_shift = [{"shift": r["sh"], "hits": int(r["hits"])} for r in cur.fetchall()]

        #  Filter options, limited to what the caller may see.
        cur.execute("""SELECT l.id, l.line_name, l.zone_id, z.zone_name
                         FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE l.id = ANY(%s) ORDER BY z.zone_name, l.line_name""",
                    (list(allowed),))
        lines = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT machine_id, machine_name, line_id
                         FROM mes_fault_config WHERE line_id = ANY(%s)
                        ORDER BY machine_name""", (list(allowed),))
        machines = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT fault_name FROM mes_fault_config
                        WHERE line_id = ANY(%s) ORDER BY fault_name""", (list(allowed),))
        faults = [r["fault_name"] for r in cur.fetchall()]
        conn.rollback()

    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pareto": par,
            "by_date": by_date, "by_shift": by_shift,
            "kpis": {"occurrences": total,
                     "open": int(k.get("open") or 0),
                     "distinct_faults": len(par),
                     "days": days,
                     "per_day": round(total / days, 2),
                     "top_fault": (par[0]["fault_name"] if par else None),
                     "top_hits": (par[0]["hits"] if par else 0),
                     "first_at": k.get("first_at"), "last_at": k.get("last_at")},
            "lines": lines, "machines": machines, "faults": faults}


@router.get("/history-excel")
def fault_history_excel(date_from: Optional[str] = None,
                        date_to:   Optional[str] = None,
                        zone_id:   Optional[int] = None,
                        line_id:   Optional[int] = None,
                        machine_id: Optional[int] = None,
                        fault:     Optional[str] = None,
                        shift:     Optional[str] = None,
                        state:     Optional[str] = None,
                        min_s:     Optional[float] = None,
                        user=Depends(get_current_user)):
    """Two sheets: every fault event, and the Pareto over the same filter."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    d = fault_history(date_from, date_to, zone_id, line_id, machine_id, fault,
                      shift, state, min_s, 1, 2000, user)
    hdr_fill = PatternFill("solid", fgColor="1E40AF")
    hdr_font = Font(color="FFFFFF", bold=True)

    def stamp(v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else (v or "")

    wb = Workbook()
    ws = wb.active
    ws.title = "Occurrences"
    head = ["Zone", "Line", "Machine", "Fault", "Address", "Occurred at",
            "Date", "Shift", "State"]
    ws.append(head)
    for c in ws[1]:
        c.fill, c.font = hdr_fill, hdr_font
        c.alignment = Alignment(horizontal="center")
    for r in d["rows"]:
        ws.append([r.get("zone_name") or "", r.get("line_name") or "",
                   r.get("machine_name") or "", r.get("fault_name") or "",
                   r.get("address") or "", stamp(r.get("started_at")),
                   str(r.get("record_date") or ""), r.get("shift_name") or "",
                   "still on" if not r.get("ended_at") else "cleared"])
    for i, w in enumerate([16, 18, 22, 38, 10, 20, 12, 8, 10], start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"

    k = d.get("kpis") or {}
    s2 = wb.create_sheet("Pareto")
    s2.append(["Fault", "Occurrences", "Share %", "Cumulative %",
               "Days it occurred", "Per day (those days)", "Per day (range)",
               "Machines", "Last seen"])
    for c in s2[1]:
        c.fill, c.font = hdr_fill, hdr_font
    for p in d["pareto"]:
        s2.append([p["fault_name"], p["hits"], p["pct"], p["cum_pct"],
                   p.get("on_days"), p.get("per_day_seen"), p.get("per_day_range"),
                   p.get("machines"), stamp(p.get("last_at"))])
    s2.append(["TOTAL", k.get("occurrences", 0), 100.0, 100.0,
               k.get("days"), "", k.get("per_day"), "", ""])
    s2["A" + str(s2.max_row)].font = Font(bold=True)
    for i, w in enumerate([38, 13, 10, 14, 17, 20, 16, 11, 20], start=1):
        s2.column_dimensions[s2.cell(row=1, column=i).column_letter].width = w
    s2.freeze_panes = "A2"

    s3 = wb.create_sheet("Frequency")
    s3.append(["Date", "Occurrences"])
    for c in s3[1]:
        c.fill, c.font = hdr_fill, hdr_font
    for r in d.get("by_date", []):
        s3.append([r["date"], r["hits"]])
    s3.append([])
    s3.append(["Shift", "Occurrences"])
    for c in s3[s3.max_row]:
        c.fill, c.font = hdr_fill, hdr_font
    for r in d.get("by_shift", []):
        s3.append([r["shift"], r["hits"]])
    for i, w in enumerate([16, 14], start=1):
        s3.column_dimensions[s3.cell(row=1, column=i).column_letter].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"fault_history_{date_from or 'all'}_{date_to or 'now'}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})
