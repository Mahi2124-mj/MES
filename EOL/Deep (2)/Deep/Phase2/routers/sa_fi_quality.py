"""
Semi-Auto ↔ Final Inspection quality trace (SEAT SLIDER zone only).

Operator spec (2026-08-19):
  • Semi-Auto already captures a part code + N data registers each cycle.
    One of those registers now carries the station verdict (1 = OK, 2 = NG);
    which register, and which values, is per-machine config — blank means the
    whole feature is off for that machine.
  • When a part that was NG at Semi-Auto reaches Final Inspection, the
    collector writes a configured bit on Final's PLC so the ladder can react.
    MES counting/OEE is deliberately NOT touched — operator asked for the bit
    plus a log, nothing else.
  • This router serves the history behind that: every Semi-Auto and Final
    Inspection OK/NG, filterable, exportable to Excel.  Logging is
    append-only — there is no purge or retention job anywhere, by request
    ("no retentive, continue data lo").

Where the rows come from
    FINAL  — read live from each line's existing `<db_table_name>_ct_log`.
             That table already holds every Final cycle with its part code and
             OK/NG, so the page has full history from day one instead of only
             from the day this feature was switched on.
    SEMI   — `mes_sa_fi_quality_log`, written by the collector on each
             Semi-Auto capture once `sa_result_register` is configured.
             The same table also records the Final-side rows where an
             SA-NG part was matched and the bit was written, which is what
             `sa_result` / `bit_written` surface on a Final row.

Scope is hard-limited to the SEAT SLIDER zone in `_seat_slider_lines()`; no
other zone is ever queried, so nothing outside it can change.

GET  /api/quality/sa-fi/meta     lines + per-line config state for the filters
GET  /api/quality/sa-fi/log      filtered, paginated history
GET  /api/quality/sa-fi/export   the same rows as .xlsx
"""
from datetime import datetime, timedelta
from io       import BytesIO
from typing   import Any, Dict, List, Optional

from fastapi           import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from database import get_conn, dict_cursor
from auth     import get_current_user

router = APIRouter(prefix="/api/quality/sa-fi", tags=["sa-fi-quality"])

ZONE_NAME_LIKE = "%seat%slider%"     # the ONLY zone this feature touches
VIEW_CAP       = 5_000               # rows a single page request may scan
EXPORT_CAP     = 25_000              # rows one Excel download may hold


# ── schema (idempotent; mirrors the collector's own ensure) ───────────
def ensure_schema() -> None:
    """Create the log table + config columns if they are missing.

    Safe to call on every boot: everything is IF NOT EXISTS, and a short
    lock_timeout means a busy `mes_plc_configs` delays startup by seconds
    rather than blocking behind a long-running query.
    """
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SET lock_timeout = '15s'")
            cur.execute("""
                ALTER TABLE mes_plc_configs
                  ADD COLUMN IF NOT EXISTS sa_result_register    VARCHAR(20),
                  ADD COLUMN IF NOT EXISTS sa_result_ok_value    INTEGER DEFAULT 1,
                  ADD COLUMN IF NOT EXISTS sa_result_ng_value    INTEGER DEFAULT 2,
                  ADD COLUMN IF NOT EXISTS fi_sa_ng_bit          VARCHAR(20),
                  ADD COLUMN IF NOT EXISTS fi_sa_ng_bit_hold_sec NUMERIC(5,2) DEFAULT 2.0,
                  ADD COLUMN IF NOT EXISTS fi_fetch_bit          VARCHAR(20),
                  ADD COLUMN IF NOT EXISTS sa_ok_bit             VARCHAR(20),
                  ADD COLUMN IF NOT EXISTS sa_ng_bit             VARCHAR(20),
                  -- 2026-08-24 — separate NG read trigger.  Some Semi-Auto
                  -- ladders only pulse the OK fetch bit and never raise it on
                  -- an NG part, so the NG cycle's data was never read.  When
                  -- this bit is set, its rising edge ALSO fires the SA capture
                  -- (verdict then = NG).  Blank = unchanged behaviour.
                  ADD COLUMN IF NOT EXISTS sa_ng_trigger_bit     VARCHAR(20)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mes_sa_fi_quality_log (
                    id           BIGSERIAL   PRIMARY KEY,
                    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
                    record_date  DATE        NOT NULL,
                    shift_name   VARCHAR(10),
                    zone_id      INTEGER,
                    line_id      INTEGER     NOT NULL,
                    line_name    VARCHAR(60),
                    station      VARCHAR(6)  NOT NULL,
                    plc_id       INTEGER,
                    machine_name VARCHAR(120),
                    part_code    VARCHAR(80),
                    result       VARCHAR(4),
                    raw_value    INTEGER,
                    cycle_seq    INTEGER,
                    sa_ng        BOOLEAN     NOT NULL DEFAULT FALSE,
                    bit_address  VARCHAR(20),
                    bit_written  BOOLEAN     NOT NULL DEFAULT FALSE,
                    note         TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            for ddl in (
                "CREATE INDEX IF NOT EXISTS ix_safi_date_line ON mes_sa_fi_quality_log (record_date DESC, line_id)",
                "CREATE INDEX IF NOT EXISTS ix_safi_part      ON mes_sa_fi_quality_log (part_code)",
                "CREATE INDEX IF NOT EXISTS ix_safi_station   ON mes_sa_fi_quality_log (station, result)",
                "CREATE INDEX IF NOT EXISTS ix_safi_ts        ON mes_sa_fi_quality_log (ts DESC)",
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_safi_row ON mes_sa_fi_quality_log "
                "(station, plc_id, record_date, cycle_seq, part_code)",
            ):
                cur.execute(ddl)
            conn.commit()
            cur.close()
    except Exception as exc:                     # never block API startup
        print(f"[SA-FI] schema ensure skipped: {exc}")


# ── helpers ───────────────────────────────────────────────────────────
def _seat_slider_lines(cur, line_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Seat Slider lines with a usable ct_log table, plus their SA/FI config.

    A line with no `db_table_name` has no Final history to read, so it is
    dropped here rather than blowing up the UNION later.
    """
    cur.execute(
        """
        SELECT l.id, l.line_name, l.db_table_name, l.zone_id,
               fi.id  AS fi_plc_id, fi.machine_name AS fi_machine,
               fi.fi_sa_ng_bit,
               sa.id  AS sa_plc_id, sa.machine_name AS sa_machine,
               sa.sa_result_register, sa.sa_result_ok_value, sa.sa_result_ng_value,
               sa.sa_ok_bit, sa.sa_ng_bit,
               sa.sa_enabled
          FROM mes_lines l
          JOIN mes_zones z ON z.id = l.zone_id
          LEFT JOIN LATERAL (
               SELECT p.* FROM mes_plc_configs p
                WHERE p.line_id = l.id AND p.parent_plc_id IS NULL
                ORDER BY p.id LIMIT 1
          ) fi ON TRUE
          LEFT JOIN LATERAL (
               SELECT p.* FROM mes_plc_configs p
                WHERE p.line_id = l.id AND p.machine_name ILIKE '%%semi%%'
                ORDER BY p.id LIMIT 1
          ) sa ON TRUE
         WHERE z.zone_name ILIKE %s
           AND l.db_table_name IS NOT NULL AND l.db_table_name <> ''
           AND (%s::int IS NULL OR l.id = %s::int)
         ORDER BY l.id
        """,
        (ZONE_NAME_LIKE, line_id, line_id),
    )
    return cur.fetchall()


def _xl(v):
    """Make a value safe for a worksheet cell.

    Shift-start / scan-fail cycles land control bytes in part_code (\x01,
    \x0e, ...).  openpyxl raises IllegalCharacterError on those, which would
    fail the whole download, so strip them and keep the row.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return "".join(ch for ch in v if ch == "\t" or ch >= " ")
    return v


def _table_ok(cur, tbl: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS t", (tbl,))
    return cur.fetchone()["t"] is not None


def _fetch_rows(cur, lines, d_from, d_to, station, result, part, limit,
                since_hours: Optional[int] = None) -> List[Dict]:
    """Build the unified SEMI+FINAL history for the given lines/filters.

    `since_hours` (optional): when set, only rows from the last N hours are
    returned (a timestamp lower bound applied in SQL). This is the page's
    default "last 4 hours" view — it keeps the scan tiny so the query is fast.
    """
    rows: List[Dict[str, Any]] = []
    like = f"%{part.strip()}%" if part and part.strip() else None

    # ── FINAL: straight out of each line's existing ct_log ──
    if station in ("ALL", "FINAL"):
        for ln in lines:
            tbl = f"{ln['db_table_name']}_ct_log"
            if not _table_ok(cur, tbl):
                continue
            sql = [
                f"SELECT ts, record_date, shift_name, cycle_seq, part_code, is_ng",
                f"  FROM {tbl}",
                " WHERE record_date BETWEEN %s AND %s",
            ]
            args: List[Any] = [d_from, d_to]
            if like:
                sql.append(" AND part_code ILIKE %s"); args.append(like)
            if result == "NG":
                sql.append(" AND is_ng IS TRUE")
            elif result == "OK":
                sql.append(" AND (is_ng IS FALSE OR is_ng IS NULL)")
            if since_hours:
                sql.append(" AND ts >= (now() - make_interval(hours => %s))")
                args.append(since_hours)
            sql.append(" ORDER BY ts DESC LIMIT %s"); args.append(limit)
            cur.execute("\n".join(sql), tuple(args))
            for r in cur.fetchall():
                rows.append({
                    "ts":           r["ts"].isoformat() if r["ts"] else None,
                    "record_date":  str(r["record_date"]),
                    "shift_name":   r["shift_name"],
                    "line_id":      ln["id"],
                    "line_name":    ln["line_name"],
                    "station":      "FINAL",
                    "machine_name": ln["fi_machine"] or "Final Inspection",
                    "part_code":    (r["part_code"] or "").rstrip(":"),
                    "result":       "NG" if r["is_ng"] else "OK",
                    "raw_value":    None,
                    "cycle_seq":    r["cycle_seq"],
                    "sa_result":    None,      # filled below from the SEMI log
                    "bit_written":  False,
                    "load":         "",
                })

    # ── SEMI: every capture, with the verdict attached when there is one ──
    # Driven by `mes_submachine_data_log` (one row per Semi-Auto capture: part
    # code + the load/force block) rather than the verdict log, because the
    # verdict only exists once an admin fills in that machine's Verdict
    # Register.  Reading the capture log means the station's history — part,
    # time, cycle and the actual load values — is visible from the moment the
    # capture works, and the OK/NG column simply fills in later.
    if station in ("ALL", "SEMI"):
        ids = [ln["id"] for ln in lines]
        if ids:
            sql = [
                "SELECT d.ts_server AS ts, d.record_date, d.shift_name, d.line_id,",
                "       l.line_name, p.machine_name, d.part_code, d.cycle_seq,",
                "       d.data_values, q.result, q.raw_value, q.bit_written",
                "  FROM mes_submachine_data_log d",
                "  JOIN mes_plc_configs p ON p.id = d.sub_plc_id",
                "  LEFT JOIN mes_lines l  ON l.id = d.line_id",
                "  LEFT JOIN mes_sa_fi_quality_log q",
                "         ON q.station = 'SEMI' AND q.plc_id = d.sub_plc_id",
                "        AND q.record_date = d.record_date",
                "        AND q.cycle_seq = d.cycle_seq",
                " WHERE d.line_id = ANY(%s) AND d.record_date BETWEEN %s AND %s",
            ]
            args: List[Any] = [ids, d_from, d_to]
            if like:
                sql.append(" AND d.part_code ILIKE %s"); args.append(like)
            if result in ("OK", "NG"):
                # Only rows whose verdict is known can satisfy an OK/NG filter.
                sql.append(" AND q.result = %s"); args.append(result)
            if since_hours:
                sql.append(" AND d.ts_server >= (now() - make_interval(hours => %s))")
                args.append(since_hours)
            sql.append(" ORDER BY d.ts_server DESC LIMIT %s"); args.append(limit)
            cur.execute("\n".join(sql), tuple(args))
            for r in cur.fetchall():
                # Compact preview of the load/force block so the operator can
                # read the values straight off the table.
                vals = r["data_values"] or []
                nums = [v.get("raw") for v in vals if isinstance(v, dict)
                        and v.get("raw") not in (None, 0)]
                _pc   = (r["part_code"] or "").rstrip(":")
                _load = ", ".join(str(n) for n in nums[:10])
                # 2026-08-26 — NG-fetch parts get captured BEFORE the load cell
                # is read; the station leaves the PART-CODE barcode sitting in
                # the load/force registers while the dedicated part-code
                # register is still blank.  So a row with no part code whose
                # "load" block is really printable ASCII (register values in
                # 0x2000-0x7F7F, e.g. 12336 = 0x3030 = "00") is a barcode, not
                # loads: decode it into the Part ID and blank the load column
                # instead of showing raw 12336-style numbers.
                if not _pc and nums:
                    _ascii = [n for n in nums if isinstance(n, int) and 0x2000 <= n <= 0x7F7F]
                    if len(_ascii) >= max(3, len(nums) * 0.6):
                        _bc = []
                        for n in nums:
                            for b in (n & 0xFF, (n >> 8) & 0xFF):
                                if 32 <= b < 127:
                                    _bc.append(chr(b))
                        _pc   = "".join(_bc).strip().rstrip(":")
                        _load = ""     # it was the barcode, not a real load
                rows.append({
                    "ts":           r["ts"].isoformat() if r["ts"] else None,
                    "record_date":  str(r["record_date"]),
                    "shift_name":   r["shift_name"],
                    "line_id":      r["line_id"],
                    "line_name":    r["line_name"],
                    "station":      "SEMI",
                    "machine_name": r["machine_name"] or "Semi-Auto",
                    "part_code":    _pc,
                    "result":       r["result"],          # None until configured
                    "raw_value":    r["raw_value"],
                    "cycle_seq":    r["cycle_seq"],
                    "sa_result":    None,
                    "bit_written":  bool(r["bit_written"]),
                    "load":         _load,
                })

    # Each line is queried for its own newest `limit`, so the global newest
    # `limit` is guaranteed to be inside the merged set — truncating here is
    # exact, and keeps the enrichment + Excel work bounded no matter how many
    # Seat Slider lines are selected.
    rows.sort(key=lambda x: (x["ts"] or ""), reverse=True)
    del rows[limit:]

    # ── enrich Final rows with what Semi-Auto said about the same part ──
    # Done as one extra lookup over the page's part codes rather than a join
    # across the UNION, which keeps the main query index-only.
    finals = [r for r in rows if r["station"] == "FINAL" and r["part_code"]]
    if finals:
        codes = list({r["part_code"] for r in finals})[:5000]
        cur.execute(
            "SELECT part_code, result, bit_written FROM mes_sa_fi_quality_log "
            "WHERE station = 'SEMI' AND part_code = ANY(%s)",
            (codes,),
        )
        sa_map = {}
        for r in cur.fetchall():
            pc = (r["part_code"] or "").rstrip(":")
            # An NG verdict wins: if a part was ever NG at Semi-Auto that is
            # what the Final row needs to show.
            if pc not in sa_map or r["result"] == "NG":
                sa_map[pc] = r
        for r in finals:
            hit = sa_map.get(r["part_code"])
            if hit:
                r["sa_result"]   = hit["result"]
                r["bit_written"] = bool(hit["bit_written"]) or r["bit_written"]
    return rows


def _parse_dates(date_from: Optional[str], date_to: Optional[str]):
    today = datetime.now().date()
    try:
        d_to   = datetime.strptime(date_to,   "%Y-%m-%d").date() if date_to   else today
        d_from = (datetime.strptime(date_from, "%Y-%m-%d").date() if date_from
                  else d_to - timedelta(days=6))
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


# ── endpoints ─────────────────────────────────────────────────────────
@router.get("/meta")
def meta(user=Depends(get_current_user)):
    """Lines for the filter dropdown + whether each end is configured yet."""
    with get_conn() as conn:
        cur   = dict_cursor(conn)
        lines = _seat_slider_lines(cur)
        return {
            "zone": "SEAT SLIDER",
            "lines": [{
                "line_id":       ln["id"],
                "line_name":     ln["line_name"],
                "semi_machine":  ln["sa_machine"],
                "final_machine": ln["fi_machine"],
                # What the operator still has to fill in, surfaced so the page
                # can say "not configured" instead of just showing nothing.
                "sa_result_register": ln["sa_result_register"],
                "sa_ok_bit":          ln["sa_ok_bit"],
                "sa_ng_bit":          ln["sa_ng_bit"],
                "sa_ok_value":        ln["sa_result_ok_value"],
                "sa_ng_value":        ln["sa_result_ng_value"],
                "fi_sa_ng_bit":       ln["fi_sa_ng_bit"],
                # 2026-08-24 — the verdict now comes from two bits; the old
                # register still counts as configured so nothing regresses.
                "semi_configured":    bool(ln["sa_ok_bit"] or ln["sa_ng_bit"]
                                           or ln["sa_result_register"]),
                "final_configured":   bool(ln["fi_sa_ng_bit"]),
            } for ln in lines],
        }


@router.get("/log")
def log(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (default: 7 days back)"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD (default: today)"),
    line_id:   Optional[int] = Query(None),
    station:   str = Query("ALL", pattern="^(ALL|SEMI|FINAL)$"),
    result:    str = Query("ALL", pattern="^(ALL|OK|NG)$"),
    part_code: Optional[str] = Query(None),
    hours:     Optional[int] = Query(None, ge=1, le=168,
                                     description="last N hours (overrides dates); default view"),
    page:      int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    user=Depends(get_current_user),
):
    d_from, d_to = _parse_dates(date_from, date_to)
    # "last N hours" mode: narrow the date window to just the days the window
    # spans (keeps the scan small/fast), and pass the hour bound to the query.
    if hours:
        now    = datetime.now()
        d_from = (now - timedelta(hours=hours)).date()
        d_to   = now.date()
    with get_conn() as conn:
        cur   = dict_cursor(conn)
        lines = _seat_slider_lines(cur, line_id)
        if not lines:
            return {"total": 0, "page": page, "page_size": page_size, "rows": [],
                    "date_from": str(d_from), "date_to": str(d_to), "capped": False}
        rows  = _fetch_rows(cur, lines, d_from, d_to, station, result,
                            part_code or "", VIEW_CAP, hours)
        total = len(rows)
        start = (page - 1) * page_size
        return {
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "date_from": str(d_from),
            "date_to":   str(d_to),
            # Tell the UI when the window hit the scan cap, so a narrower date
            # range can be suggested instead of silently showing partial data.
            "capped":    total >= VIEW_CAP,
            "rows":      rows[start:start + page_size],
        }


@router.get("/export")
def export(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    line_id:   Optional[int] = Query(None),
    station:   str = Query("ALL", pattern="^(ALL|SEMI|FINAL)$"),
    result:    str = Query("ALL", pattern="^(ALL|OK|NG)$"),
    part_code: Optional[str] = Query(None),
    hours:     Optional[int] = Query(None, ge=1, le=168),
    user=Depends(get_current_user),
):
    """The filtered history as .xlsx (what the operator filters, they download)."""
    from openpyxl        import Workbook
    from openpyxl.styles import Font, PatternFill

    d_from, d_to = _parse_dates(date_from, date_to)
    if hours:
        now    = datetime.now()
        d_from = (now - timedelta(hours=hours)).date()
        d_to   = now.date()
    with get_conn() as conn:
        cur   = dict_cursor(conn)
        lines = _seat_slider_lines(cur, line_id)
        rows  = _fetch_rows(cur, lines, d_from, d_to, station, result,
                            part_code or "", EXPORT_CAP, hours) if lines else []

    wb = Workbook()
    ws = wb.active
    ws.title = "SA-FI Quality Log"
    headers = ["Date", "Time", "Shift", "Line", "Station", "Machine",
               "Part ID", "Result", "Semi-Auto Result", "NG Bit Sent",
               "Cycle #", "Raw Value", "Load / Force values"]
    ws.append(headers)
    head_fill = PatternFill("solid", fgColor="1E3A8A")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
    ng_fill = PatternFill("solid", fgColor="FEE2E2")

    for r in rows:
        ts = (r["ts"] or "")
        ws.append([
            _xl(r["record_date"]),
            ts[11:19] if len(ts) >= 19 else "",
            _xl(r["shift_name"]),
            _xl(r["line_name"]),
            _xl(r["station"]),
            _xl(r["machine_name"]),
            _xl(r["part_code"]),
            _xl(r["result"]),
            _xl(r["sa_result"]),
            "YES" if r["bit_written"] else "",
            r["cycle_seq"] if r["cycle_seq"] is not None else "",
            r["raw_value"] if r["raw_value"] is not None else "",
            _xl(r.get("load")),
        ])
        if r["result"] == "NG" or r["sa_result"] == "NG":
            for c in ws[ws.max_row]:
                c.fill = ng_fill

    for col, w in zip("ABCDEFGHIJKLM", (11, 9, 6, 12, 8, 26, 30, 8, 16, 12, 9, 10, 40)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"SA-FI_Quality_{d_from}_to_{d_to}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
