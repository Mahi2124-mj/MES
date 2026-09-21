"""
routers/reports.py
==================
Shift-level reporting:
  - Excel download (formatted, one row per hourly slot + KPI footer)
  - PDF download (matplotlib-rendered, charts + KPI grid)
  - Background scheduler that mails the PDF to a configured list at
    every shift-end transition

Endpoints
---------
GET  /api/reports/shift-excel?line_id=&date=&shift=
GET  /api/reports/shift-pdf?line_id=&date=&shift=
GET  /api/reports/email-config         (admin)
PUT  /api/reports/email-config         (admin)
POST /api/reports/email-now            (admin — manual fire)

The auto-email scheduler is started by main.py at app boot; it lives in
the same module so the import graph stays simple.
"""
from __future__ import annotations

import io
import os
import threading
import time
import traceback
from datetime import datetime, date, timedelta, time as dtime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user
from routers.breakdown_mail import _send_email   # reuse the same SMTP helper
from ddl_once import once

router = APIRouter(prefix="/api/reports", tags=["reports"])


# ════════════════════════════════════════════════════════════════════
#  Email-config table — list of recipients per line / report kind
# ════════════════════════════════════════════════════════════════════

@once
def _ensure_email_config_table() -> None:
    """Idempotent create.  Runs once at first endpoint hit."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_report_email_config (
                id           SERIAL PRIMARY KEY,
                line_id      INTEGER NOT NULL,
                report_kind  VARCHAR(20) NOT NULL DEFAULT 'shift_end',
                to_addresses TEXT NOT NULL DEFAULT '',
                cc_addresses TEXT NOT NULL DEFAULT '',
                is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at   TIMESTAMP DEFAULT NOW(),
                UNIQUE (line_id, report_kind)
            )
        """)
        # 2026-08-14 — THREE LEVELS OF RECIPIENT, not one.
        # A shift report goes to the line's own people, to the zone head above
        # them and to the section head above that — and each of those wants its
        # own To vs Cc.  There was only one To/Cc pair, so the whole chain had to
        # be pasted into a single box and everyone landed in the same field.
        # Kept as columns on the existing row (rather than a scope table)
        # because the config is administered per LINE: one row, six boxes, which
        # is exactly how the screen reads.  Blank = that level is simply not
        # mailed, so existing rows keep behaving as they do today.
        for col in ("zone_to_addresses", "zone_cc_addresses",
                    "section_to_addresses", "section_cc_addresses"):
            cur.execute(f"ALTER TABLE mes_report_email_config "
                        f"ADD COLUMN IF NOT EXISTS {col} TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _split_addrs(raw) -> List[str]:
    return [a.strip() for a in (raw or "").split(",") if a.strip()]


def _resolve_recipients(cfg: dict) -> tuple:
    """Merge the line / zone / section levels into one (to, cc) pair.

    Order is preserved (line first, then zone, then section) and duplicates are
    dropped, so the same address listed at two levels is mailed once.  An
    address that appears in both To and Cc stays in To only — being on To is
    strictly stronger, and SMTP servers vary in how they treat the overlap.
    """
    to, cc, seen_to = [], [], set()
    for key in ("to_addresses", "zone_to_addresses", "section_to_addresses"):
        for a in _split_addrs(cfg.get(key)):
            if a.lower() not in seen_to:
                seen_to.add(a.lower()); to.append(a)
    seen_cc = set()
    for key in ("cc_addresses", "zone_cc_addresses", "section_cc_addresses"):
        for a in _split_addrs(cfg.get(key)):
            k = a.lower()
            if k not in seen_to and k not in seen_cc:
                seen_cc.add(k); cc.append(a)
    return to, cc


# ════════════════════════════════════════════════════════════════════
#  Data loader — pulls one shift's full row out of the dashboard table
# ════════════════════════════════════════════════════════════════════

def _load_shift_row(line_id: int, record_date: date, shift_name: str) -> dict:
    """Return the dashboard row for this shift (latest if multiple).
    Raises 404 if nothing found."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.line_name, l.db_table_name, z.zone_name
              FROM mes_lines l
              LEFT JOIN mes_zones z ON z.id = l.zone_id
             WHERE l.id = %s
        """, (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(404, f"line_id {line_id} not found")
        table = line["db_table_name"]
        cur.execute(f"""
            SELECT *
              FROM {table}
             WHERE record_date = %s AND shift_name = %s
             ORDER BY id DESC
             LIMIT 1
        """, (record_date, shift_name))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404,
                f"No data for line {line_id} on {record_date} shift {shift_name}")
        row["line_name"]      = line["line_name"]
        row["_zone_name"]     = line.get("zone_name") or ""
        row["table_name"]     = table
        row["_report_date"]   = record_date
        row["_report_shift"]  = shift_name
        # Needed by _slot_losses to reach the ct_log + breakdown tables.
        row["_line_id"]       = line_id
        # 2026-06-19 — manual OT target for this shift, overlaid onto the OT slot
        # in the report (the collector writes the OT bucket's _plan as 0).
        try:
            cur.execute("SELECT ot_plan FROM mes_shift_configs WHERE line_id=%s AND shift_name=%s",
                        (line_id, shift_name))
            _otp = cur.fetchone()
            row["_ot_plan"] = (_otp or {}).get("ot_plan")
        except Exception:
            row["_ot_plan"] = None
        return dict(row)


def _hourly_slots_from_row(row: dict) -> List[dict]:
    """Walk hour_*_plan / actual / ok / ng columns into a list of
    {label, plan, actual, ok, ng, variance} dicts in slot order."""
    out = []
    seen = set()
    ot_plan = row.get("_ot_plan") or 0
    for k in row.keys():
        if not k.startswith("hour_") or not k.endswith("_plan"):
            continue
        prefix = k[:-5]          # strip "_plan"
        label_raw = prefix[5:]   # strip "hour_"
        if label_raw in seen:
            continue
        seen.add(label_raw)
        # The OT bucket prefix is "HHMM_HHMM_ot" — strip the "_ot" marker before
        # parsing the two times and label it as an explicit OT slot.
        is_ot = label_raw.endswith("_ot")
        core  = label_raw[:-3] if is_ot else label_raw
        # "0830_0930" -> "08:30-09:30"
        try:
            a, b = core.split("_")
            label = f"{a[:2]}:{a[2:]}-{b[:2]}:{b[2:]}"
            if is_ot:
                label = f"OT {label}"
        except Exception:
            label = label_raw
        plan     = row.get(f"{prefix}_plan")     or 0
        actual   = row.get(f"{prefix}_actual")   or 0
        ok       = row.get(f"{prefix}_ok")       or 0
        ng       = row.get(f"{prefix}_ng")       or 0
        variance = row.get(f"{prefix}_variance") or 0
        if is_ot:
            # Skip the OT row entirely when OT didn't run this shift (no actual
            # and no manual target) — the OT slot is permanent in the schema.
            if not actual and not ot_plan:
                continue
            # Overlay the manual OT target so the report shows real plan/variance.
            if ot_plan:
                plan     = ot_plan
                variance = actual - ot_plan
        out.append({
            "label":    label,
            "plan":     plan,
            "actual":   actual,
            "ok":       ok,
            "ng":       ng,
            "variance": variance,
        })
    return out


# ════════════════════════════════════════════════════════════════════
#  PER-SLOT LOSS  (2026-08-14)
# ════════════════════════════════════════════════════════════════════
# The report carried plan/actual per hour and a single loss figure for the
# whole shift, so "which hour did we lose it in" could not be answered from it.
#
# Only two loss types can be attributed to an hour HONESTLY:
#   speed     — SUM(ct - ideal) over the cycles stamped inside the slot
#   breakdown — the overlap of each mes_breakdowns interval with the slot
# The rest (quality / setup / material / others) exist per slot only as
# operator REMARKS in mes_loss_remarks — text, no duration — so they are
# reported as the remark that was written, never as an invented number.
# ════════════════════════════════════════════════════════════════════

def _slot_bounds(label: str, record_date: date):
    """'08:30-09:30' (or 'OT 23:05-00:05') → (start_dt, end_dt).  A slot whose
    end is before its start has crossed midnight, so its end is the next day."""
    core = label[3:] if label.startswith("OT ") else label
    try:
        a, b = core.split("-")
        sh, sm = int(a[:2]), int(a[3:5])
        eh, em = int(b[:2]), int(b[3:5])
    except Exception:
        return None, None
    start = datetime.combine(record_date, dtime(sh, sm))
    end   = datetime.combine(record_date, dtime(eh, em))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _slot_losses(line_id: int, record_date: date, shift_name: str,
                 slots: List[dict]) -> None:
    """Annotate each slot in-place with speed_loss / breakdown_loss / remarks.

    Never raises: a report that is missing its loss columns is far better than
    a report that fails to send.
    """
    for s in slots:
        s.setdefault("speed_loss", 0.0)
        s.setdefault("breakdown_loss", 0.0)
        s.setdefault("remarks", "")
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT db_table_name, line_name,
                                  COALESCE(ideal_cycle_time,15) AS ict
                             FROM mes_lines WHERE id=%s""", (line_id,))
            ln = cur.fetchone()
            if not ln or not ln.get("db_table_name"):
                return
            ct_tbl = f"{ln['db_table_name']}_ct_log"
            ideal  = float(ln["ict"] or 15.0)

            # On andon-covered lines the breakdown loss comes from the physical
            # Andon Maintenance+Toolroom calls (andon_history), not mes_breakdowns
            # — same source the Dashboard/OEE uses. Display-only; guarded.
            _andon_cov = False
            _andon_ivs = []
            try:
                from routers.andon import (andon_line_set, andon_breakdown_intervals,
                                           _norm as _anorm)
                if ln.get("line_name") and _anorm(ln["line_name"]) in andon_line_set():
                    _andon_cov = True
                    _andon_ln = ln["line_name"]
                    # 2026-09-12 — PERF: fetch the shift's andon breakdown intervals
                    # ONCE (was one cross-DB call per slot ≈ 270 ms on a 10-slot
                    # shift).  Each slot then clips these merged intervals in Python
                    # — identical math (andon_breakdown_seconds itself just sums the
                    # clipped merged intervals), but a single query.
                    _bs = [x for x in
                           (_slot_bounds(s["label"], record_date) for s in slots)
                           if x and x[0]]
                    if _bs:
                        try:
                            _andon_ivs = andon_breakdown_intervals(
                                _andon_ln, min(x[0] for x in _bs),
                                max(x[1] for x in _bs)) or []
                        except Exception:
                            _andon_ivs = []
            except Exception:
                _andon_cov = False

            # Operator remarks are already keyed by slot_label + loss_type.
            cur.execute("""SELECT slot_label, loss_type, remark
                             FROM mes_loss_remarks
                            WHERE line_id=%s AND record_date=%s
                              AND (shift_name=%s OR shift_name IS NULL)""",
                        (line_id, record_date, shift_name))
            by_slot: dict = {}
            for r in cur.fetchall():
                txt = (r["remark"] or "").strip()
                by_slot.setdefault(r["slot_label"], []).append(
                    f"{r['loss_type']}: {txt}" if txt else r["loss_type"])

            for s in slots:
                s["remarks"] = " · ".join(by_slot.get(s["label"], []))[:180]
                a, b = _slot_bounds(s["label"], record_date)
                if not a:
                    continue

                # Speed loss — same rule the wallboard uses, so the two agree.
                try:
                    cur.execute(f"""
                        SELECT COALESCE(SUM(ct_value - %s), 0) AS loss
                          FROM {ct_tbl}
                         WHERE ts >= %s AND ts < %s
                           AND ct_value IS NOT NULL
                           AND ct_value >= %s * 0.5
                    """, (ideal, a, b, ideal))
                    s["speed_loss"] = float((cur.fetchone() or {}).get("loss") or 0.0)
                except Exception:
                    conn.rollback()

                # Breakdown — on andon-covered lines use the Andon
                # Maintenance+Toolroom call durations clipped to the slot;
                # otherwise the mes_breakdowns intervals clipped to the slot.
                if _andon_cov:
                    # clip the pre-fetched merged andon intervals to THIS slot
                    _bl = 0.0
                    for _iv0, _iv1 in _andon_ivs:
                        _ov = (min(_iv1, b) - max(_iv0, a)).total_seconds()
                        if _ov > 0:
                            _bl += _ov
                    s["breakdown_loss"] = max(0.0, _bl)
                else:
                    try:
                        cur.execute("""
                            SELECT COALESCE(SUM(EXTRACT(EPOCH FROM
                                       LEAST(COALESCE(ended_at, now()), %s)
                                     - GREATEST(started_at, %s))), 0) AS loss
                              FROM mes_breakdowns
                             WHERE line_id=%s
                               AND started_at < %s
                               AND COALESCE(ended_at, now()) > %s
                        """, (b, a, line_id, b, a))
                        s["breakdown_loss"] = max(0.0, float((cur.fetchone() or {}).get("loss") or 0.0))
                    except Exception:
                        conn.rollback()
    except Exception as exc:
        print(f"[REPORT] per-slot loss skipped: {exc}")


def _model_runs(line_id: int, db_table: str, record_date, shift_name: str) -> List[dict]:
    """Which model ran when, for how long, and how many parts (OK / NG).

    The model TIMELINE comes from `mes_submachine_ct_log` (line-wide — every
    machine on a line runs the same model at a time), and PARTS are counted
    from the MAIN line's per-cycle log ({db_table}_ct_log) by time overlap, so
    the counts match the line total.  Sub-2-minute boundary blips (machines
    switch a few seconds apart) are merged.  Returns [] on any trouble.
    """
    import re as _re
    if not db_table or not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_table):
        return []
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT ts_start, model_number AS mno, model_name AS mnm
                             FROM mes_submachine_ct_log
                            WHERE line_id=%s AND record_date=%s AND shift_name=%s
                              AND model_number IS NOT NULL
                            ORDER BY ts_start""",
                        (line_id, record_date, shift_name))
            rows = cur.fetchall() or []
            if not rows:
                return []
            wins = []
            for r in rows:
                if wins and wins[-1]["mno"] == r["mno"]:
                    wins[-1]["end"] = r["ts_start"]
                else:
                    wins.append({"mno": r["mno"], "mnm": r["mnm"],
                                 "start": r["ts_start"], "end": r["ts_start"]})
            merged: List[dict] = []
            for w in wins:
                if merged and merged[-1]["mno"] == w["mno"]:
                    merged[-1]["end"] = w["end"]; continue
                if merged and (w["end"] - w["start"]).total_seconds() < 120:
                    merged[-1]["end"] = w["end"]; continue   # boundary blip
                merged.append(w)
            out: List[dict] = []
            ct_tbl = f"{db_table}_ct_log"
            for w in merged:
                tot = ng = 0
                try:
                    cur.execute(f"""SELECT COUNT(*) AS tot,
                                           COUNT(*) FILTER (WHERE is_ng) AS ng
                                      FROM {ct_tbl}
                                     WHERE record_date=%s AND ts>=%s AND ts<%s""",
                                (record_date, w["start"],
                                 w["end"] + timedelta(seconds=30)))
                    pc = cur.fetchone() or {}
                    tot = int(pc.get("tot") or 0); ng = int(pc.get("ng") or 0)
                except Exception:
                    conn.rollback()
                out.append({
                    "model_number": w["mno"], "model_name": w["mnm"],
                    "from":  w["start"].strftime("%H:%M"),
                    "to":    w["end"].strftime("%H:%M"),
                    "duration_min": round((w["end"] - w["start"]).total_seconds() / 60.0, 1),
                    "ok":    tot - ng, "ng": ng, "total": tot,
                })
            return out
    except Exception as exc:
        print(f"[REPORT] model runs skipped: {exc}")
        return []


def _norm_slot(label) -> str:
    """Canonical zero-padded 'HH:MM-HH:MM' for a slot label, tolerant of the
    'OT ' marker and of UNPADDED hours ('8:30-9:30' vs '08:30-09:30').  The two
    label sources (mes_hourly_slots.slot_label and the hour_* column names) do
    not always agree on zero-padding — YMC Recliner stored '8:30-9:30', which
    failed the exact-match and silently dropped the 08:30 slot, so its report
    started at 09:30.  Normalising both sides fixes it for every line."""
    s = (label or "").strip()
    if s.upper().startswith("OT "):
        s = s[3:].strip()
    s = s.replace("OT", "").strip()
    try:
        a, b = s.split("-")
        def _z(t):
            h, m = t.strip().split(":")
            return f"{int(h):02d}:{int(m):02d}"
        return f"{_z(a)}-{_z(b)}"
    except Exception:
        return s


def _shift_slot_labels(line_id, shift_name) -> set:
    """Canonical time-range labels that belong to this shift, from
    mes_hourly_slots.  The dashboard row carries EVERY shift's hour buckets, so
    without this filter a shift-A report also lists shift-B's empty slots.
    Empty set = table not populated, so the caller keeps all."""
    labels = set()
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT slot_label FROM mes_hourly_slots
                            WHERE line_id=%s AND shift_name=%s""",
                        (line_id, shift_name))
            for r in cur.fetchall():
                lab = _norm_slot(r["slot_label"])
                if lab:
                    labels.add(lab)
    except Exception as exc:
        print(f"[REPORT] shift slot labels skipped: {exc}")
    return labels


def _shift_remarks(line_id, record_date, shift_name) -> dict:
    """{slot_label: 'type: remark · type: remark'} for this shift's hours.
    Matches a blank / NULL shift_name too — the remark-entry screen usually
    saves shift_name as '' , and those remarks still belong to the day's
    report.  Excel-export only; the PDF and dashboard are untouched."""
    out: dict = {}
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT slot_label, loss_type, remark
                             FROM mes_loss_remarks
                            WHERE line_id=%s AND record_date=%s
                              AND (shift_name=%s OR shift_name IS NULL
                                   OR shift_name='')""",
                        (line_id, record_date, shift_name))
            for r in cur.fetchall():
                txt  = (r["remark"] or "").strip()
                line = f"{r['loss_type']}: {txt}" if txt else (r["loss_type"] or "")
                out.setdefault(r["slot_label"], []).append(line)
    except Exception as exc:
        print(f"[REPORT] shift remarks skipped: {exc}")
    return {k: " · ".join(v) for k, v in out.items()}


def _shift_breakdowns(line_id, record_date, shift_name, slots) -> List[dict]:
    """Logged downtime events overlapping this shift's window, each as
    {t0:'HH:MM', t1:'HH:MM', reason, mins} clipped to the window.  These fill
    the numbered Losses list in the hourly report.  Filtered by the actual
    time window (first slot start → last slot end) so a night shift crossing
    midnight is handled, and the shift_name column quirks don't matter."""
    out: List[dict] = []
    if not line_id or not slots:
        return out
    try:
        s0, _ = _slot_bounds(slots[0]["label"],  record_date)
        _, s1 = _slot_bounds(slots[-1]["label"], record_date)
        if not s0 or not s1:
            return out
        with get_conn() as conn:
            cur = dict_cursor(conn)
            # LOCALTIMESTAMP (naive) not now() (tz-aware): mes_breakdowns
            # stores naive timestamps, and mixing the two makes the COALESCE
            # tz-aware and the Python comparison below raise.
            cur.execute("""
                SELECT started_at,
                       COALESCE(ended_at, LOCALTIMESTAMP) AS ended_at, reason
                  FROM mes_breakdowns
                 WHERE line_id=%s AND started_at < %s
                   AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                 ORDER BY started_at
            """, (line_id, s1, s0))
            for r in cur.fetchall():
                a = max(r["started_at"], s0)
                b = min(r["ended_at"], s1)
                mins = max(0.0, (b - a).total_seconds() / 60.0)
                reason = (r["reason"] or "").strip()
                # Drop the auto-detector's boilerplate prefix so the line reads
                # like the shop-floor sheet; the operator can rename it in Excel.
                reason = reason.replace("Auto-detected — line entered", "").strip()
                out.append({"a": a, "b": b, "reason": reason or "Breakdown",
                            "mins": mins})
    except Exception as exc:
        print(f"[REPORT] shift breakdowns skipped: {exc}")
    return out


# ════════════════════════════════════════════════════════════════════
#  EXCEL render
# ════════════════════════════════════════════════════════════════════

def render_hourly_shift_sheet(ws, row) -> None:
    """Write ONE shift's hourly production block (the operator's reference
    layout) into worksheet `ws`: metadata on row 1; the hours run as COLUMNS;
    then Plan / Actual / Cummulative Actual / Gap(Plan-Actual) as rows; then a
    Losses block listing each logged downtime event with its time window, and a
    total Losses Time.  Shared by the single-shift download (build_excel_report)
    and the Import/Export range export (main.py /api/export/data)."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    thin   = Side(border_style="thin", color="888888")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    LOSSAL = Alignment(horizontal="left",   vertical="top",    wrap_text=True)
    LBL    = Font(bold=True)
    HDR    = PatternFill("solid", fgColor="DCE6F1")
    NGF    = PatternFill("solid", fgColor="FFC7CE")

    d     = row.get("_report_date", "")
    shift = row.get("shift_name") or row.get("_report_shift") or ""
    zone  = row.get("_zone_name") or ""
    line  = row.get("line_name", "")

    def _t12(hhmm):
        # "13:05" -> "01:05" to read like the shop-floor sheet (no AM/PM).
        try:
            h, m = hhmm.split(":"); h = int(h)
            if h == 0:   h = 12
            elif h > 12: h -= 12
            return f"{h:02d}:{m}"
        except Exception:
            return hhmm

    def _lbl12(label):
        pre  = "OT " if label.startswith("OT ") else ""
        core = label[3:] if pre else label
        try:
            a, b = core.split("-")
            return pre + f"{_t12(a)}-{_t12(b)}"
        except Exception:
            return label

    # ── Row 1: metadata band ──
    for j, txt in enumerate([f"Date: {d}", f"Shift: {shift}", f"Zone: {zone}",
                             f"Line: {line}", "Work Centre:"], start=1):
        cc = ws.cell(row=1, column=j, value=txt)
        cc.font = LBL; cc.border = BORDER

    # ── Hourly grid: hours as columns ──
    slots = _hourly_slots_from_row(row)
    # Keep only THIS shift's slots — the row holds every shift's hour buckets.
    _allowed = _shift_slot_labels(row.get("_line_id"), shift)
    if _allowed:
        slots = [s for s in slots if _norm_slot(s["label"]) in _allowed]
    _slot_losses(row.get("_line_id"), row.get("_report_date"), shift, slots)

    # Per-shift loss context: logged breakdown events (with times) + remarks.
    events          = _shift_breakdowns(row.get("_line_id"), row.get("_report_date"), shift, slots)
    remarks_by_slot = _shift_remarks(row.get("_line_id"),   row.get("_report_date"), shift)
    _rec            = row.get("_report_date")

    # Assign each breakdown event's description to the hour it STARTED in, so
    # the loss shows under that hour's column (operator spec 2026-08-24).
    slot_bounds  = [_slot_bounds(s["label"], _rec) for s in slots]
    desc_by_slot = {i: [] for i in range(len(slots))}
    for ev in events:
        for i, (a, b) in enumerate(slot_bounds):
            if a and b and a <= ev["a"] < b:
                desc_by_slot[i].append(
                    f"{ev['reason']} ({_t12(ev['a'].strftime('%H:%M'))}-"
                    f"{_t12(ev['b'].strftime('%H:%M'))})")
                break

    # Row labels (column 1).
    R_TIME, R_PLAN, R_ACT, R_CUM, R_GAP, R_LOSS, R_LMIN, R_CLOSS = 2, 3, 4, 5, 6, 7, 8, 9
    for rr, lab in ((R_TIME, "Time"), (R_PLAN, "Plan"), (R_ACT, "Actual"),
                    (R_CUM, "Cummulative Actual"), (R_GAP, "Gap ( Plan vs Actual)"),
                    (R_LOSS, "Losses"), (R_LMIN, "Loss (min)"),
                    (R_CLOSS, "Cumulative Loss")):
        c = ws.cell(row=rr, column=1, value=lab)
        c.font = LBL; c.border = BORDER; c.fill = HDR

    cum = 0
    cum_loss = 0.0
    tot_plan = tot_act = 0
    tot_loss = 0.0
    max_loss_lines = 1     # tallest Losses cell → sets the row height below
    for i, s in enumerate(slots):
        col  = 2 + i
        plan = int(s.get("plan")   or 0)
        act  = int(s.get("actual") or 0)
        cum += act
        gap  = plan - act
        bd   = max(0.0, float(s.get("breakdown_loss") or 0))     # seconds
        sp   = max(0.0, float(s.get("speed_loss")     or 0))     # seconds
        lmin = (bd + sp) / 60.0                                  # this hour's loss, minutes
        cum_loss += lmin
        tot_plan += plan; tot_act += act; tot_loss += lmin

        tc = ws.cell(row=R_TIME, column=col, value=_lbl12(s["label"]))
        tc.font = LBL; tc.fill = HDR; tc.alignment = CENTER; tc.border = BORDER
        for rr, val in ((R_PLAN, plan), (R_ACT, act), (R_CUM, cum), (R_GAP, gap),
                        (R_LMIN,  round(lmin, 1)     if lmin     else 0),
                        (R_CLOSS, round(cum_loss, 1) if cum_loss else 0)):
            cc = ws.cell(row=rr, column=col, value=val)
            cc.alignment = CENTER; cc.border = BORDER
        if gap > 0:                                              # behind plan
            ws.cell(row=R_ACT, column=col).fill = NGF
            ws.cell(row=R_GAP, column=col).fill = NGF

        # Losses description for THIS hour: breakdown events + remark + speed
        # note.  When an hour holds MORE THAN ONE loss, number them serial-wise
        # (1. … 2. …); a lone loss shows plain.  Empty hour → "-".
        parts = list(desc_by_slot[i])
        rem = (remarks_by_slot.get(s["label"]) or "").strip()
        if rem:
            parts.append(rem)
        if sp / 60.0 >= 1:
            parts.append(f"Speed ~{int(round(sp / 60.0))} min")
        if len(parts) > 1:
            lines = [f"{k + 1}. {p}" for k, p in enumerate(parts)]
        elif parts:
            lines = [parts[0]]
        else:
            lines = ["-"]
        txt = "\n".join(lines)
        # Count VISUAL lines: a line longer than the column wraps, so estimate
        # ceil(len/width) per line and keep the tallest cell for the row height.
        _colw = 15
        _vis = sum(max(1, -(-len(ln) // (_colw - 1))) for ln in lines)
        max_loss_lines = max(max_loss_lines, _vis)
        lcell = ws.cell(row=R_LOSS, column=col, value=txt)
        lcell.alignment = LOSSAL; lcell.border = BORDER
        if bd > 0:
            lcell.fill = NGF

    # ── Total column (rightmost): total Plan / Actual / Loss ──
    tcol = 2 + len(slots)
    th = ws.cell(row=R_TIME, column=tcol, value="Total")
    th.font = LBL; th.fill = HDR; th.alignment = CENTER; th.border = BORDER
    for rr, val in ((R_PLAN, tot_plan), (R_ACT, tot_act), (R_CUM, cum),
                    (R_GAP, tot_plan - tot_act), (R_LOSS, ""),
                    (R_LMIN, int(round(tot_loss))), (R_CLOSS, int(round(cum_loss)))):
        cc = ws.cell(row=rr, column=tcol, value=val)
        cc.font = LBL; cc.alignment = CENTER; cc.border = BORDER; cc.fill = HDR

    # Losses row height grows with the busiest hour so no line is clipped.
    ws.row_dimensions[R_LOSS].height = max(30, max_loss_lines * 15 + 6)

    # ── Total-loss Pareto: category minutes (desc) + cumulative % line ──
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.chart.label import DataLabelList
    LOSS_CATS = [
        ("Breakdown",   "loss_breakdown_seconds"),
        ("Speed",       "loss_speed_seconds"),
        ("Setup",       "loss_setup_seconds"),
        ("Quality",     "loss_quality_seconds"),
        ("Material",    "loss_material_seconds"),
        ("Change Over", "loss_change_over_seconds"),
        ("Others",      "loss_others_seconds"),
    ]
    cats = []
    for _name, _col in LOSS_CATS:
        _m = round(float(row.get(_col) or 0) / 60.0, 1)
        if _m > 0:
            cats.append((_name, _m))
    cats.sort(key=lambda x: x[1], reverse=True)
    if cats:
        # Chart source data sits OFF to the far right (out of the printed grid);
        # the chart itself is anchored BELOW the hourly grid.
        pcol = 2 + len(slots) + 3
        p0   = R_TIME
        _tot = sum(m for _, m in cats) or 1.0
        for j, h in enumerate(("Category", "Minutes", "Cum %")):
            ws.cell(row=p0, column=pcol + j, value=h)
        _run = 0.0
        for k, (name, m) in enumerate(cats):
            _run += m
            ws.cell(row=p0 + 1 + k, column=pcol,     value=name)
            ws.cell(row=p0 + 1 + k, column=pcol + 1, value=m)
            ws.cell(row=p0 + 1 + k, column=pcol + 2, value=round(_run / _tot * 100, 1))
        _last = p0 + len(cats)
        try:
            bar = BarChart(); bar.type = "col"; bar.title = "Total Loss Pareto"
            bar.height = 8.5; bar.width = 20; bar.style = 10
            bar.add_data(Reference(ws, min_col=pcol + 1, min_row=p0, max_row=_last),
                         titles_from_data=True)
            bar.set_categories(Reference(ws, min_col=pcol, min_row=p0 + 1, max_row=_last))
            bar.y_axis.title = "Minutes"
            bar.y_axis.majorGridlines = None            # no background gridlines
            bar.x_axis.majorGridlines = None
            bar.x_axis.delete = False                   # keep category names visible
            bar.dataLabels = DataLabelList(); bar.dataLabels.showVal = True   # value labels
            ln = LineChart()
            ln.add_data(Reference(ws, min_col=pcol + 2, min_row=p0, max_row=_last),
                        titles_from_data=True)
            ln.y_axis.axId = 200; ln.y_axis.title = "Cum %"; ln.y_axis.crosses = "max"
            ln.y_axis.majorGridlines = None
            ln.dataLabels = DataLabelList(); ln.dataLabels.showVal = True
            bar += ln
            ws.add_chart(bar, "A" + str(R_CLOSS + 2))   # BELOW the hourly grid
        except Exception as _ce:
            print(f"[REPORT] pareto chart skipped: {_ce}")

    # ── MODEL RUNS — which model ran when + parts (model-wise & time-wise) ──
    # Placed well below the hourly grid + Pareto chart so nothing overlaps.
    try:
        _mruns = _model_runs(row.get("_line_id"), row.get("table_name"),
                             row.get("_report_date"), shift)
        MR0 = R_CLOSS + 20
        _hf = PatternFill("solid", fgColor="4472C4")
        _lf = PatternFill("solid", fgColor="DCE6F1")
        hc = ws.cell(row=MR0, column=1, value="MODEL RUNS  (which model ran, when & how many parts)")
        hc.font = Font(bold=True, color="FFFFFF"); hc.fill = _hf
        for j, h in enumerate(["Model", "From", "To", "Duration (min)", "OK", "NG"]):
            c = ws.cell(row=MR0 + 1, column=1 + j, value=h)
            c.font = Font(bold=True); c.fill = _lf
        for k, m in enumerate(_mruns):
            vals = [m["model_name"] or f"Model {m['model_number']}",
                    m["from"], m["to"], m["duration_min"], m["ok"], m["ng"]]
            for j, v in enumerate(vals):
                ws.cell(row=MR0 + 2 + k, column=1 + j, value=v)
        if not _mruns:
            ws.cell(row=MR0 + 2, column=1, value="No model-run data for this shift.")
    except Exception as _mr:
        print(f"[REPORT] model-runs table skipped: {_mr}")

    # widths (hours + the Total column)
    ws.column_dimensions["A"].width = 24
    for i in range(len(slots) + 1):
        ws.column_dimensions[get_column_letter(2 + i)].width = 15


def build_excel_report(row: dict) -> bytes:
    """xlsx bytes for one shift — thin wrapper around the shared renderer."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Shift Report"
    render_hourly_shift_sheet(ws, row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════
#  PDF render (matplotlib — no external dependency)
# ════════════════════════════════════════════════════════════════════


def _hourly_pdf_figure(row):
    """matplotlib Figure for one shift's hourly report — same numbers as the Excel: the
    transposed grid (Plan / Actual / Cumulative / Gap / Loss / Cum-Loss with a
    Total column), the per-hour losses, and a total-loss Pareto.  matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d     = row.get("_report_date", "")
    shift = row.get("shift_name") or row.get("_report_shift") or ""
    zone  = row.get("_zone_name") or ""
    line  = row.get("line_name", "")

    def _t12(hhmm):
        try:
            h, m = hhmm.split(":"); h = int(h)
            if h == 0: h = 12
            elif h > 12: h -= 12
            return f"{h:02d}:{m}"
        except Exception:
            return hhmm

    def _lbl12(label):
        pre  = "OT " if label.startswith("OT ") else ""
        core = label[3:] if pre else label
        try:
            a, b = core.split("-"); return pre + f"{_t12(a)}-{_t12(b)}"
        except Exception:
            return label

    slots = _hourly_slots_from_row(row)
    _allowed = _shift_slot_labels(row.get("_line_id"), shift)
    if _allowed:
        slots = [s for s in slots if _norm_slot(s["label"]) in _allowed]
    _slot_losses(row.get("_line_id"), row.get("_report_date"), shift, slots)
    events = _shift_breakdowns(row.get("_line_id"), row.get("_report_date"), shift, slots)
    remarks_by_slot = _shift_remarks(row.get("_line_id"), row.get("_report_date"), shift)
    _rec = row.get("_report_date")
    slot_bounds = [_slot_bounds(s["label"], _rec) for s in slots]
    desc_by_slot = {i: [] for i in range(len(slots))}
    for ev in events:
        for i, (a, b) in enumerate(slot_bounds):
            if a and b and a <= ev["a"] < b:
                desc_by_slot[i].append(
                    f"{ev['reason']} ({_t12(ev['a'].strftime('%H:%M'))}-{_t12(ev['b'].strftime('%H:%M'))})")
                break

    labels = [_lbl12(s["label"]) for s in slots]
    plan = [int(s.get("plan") or 0) for s in slots]
    act  = [int(s.get("actual") or 0) for s in slots]
    cum = []; _c = 0
    for a in act: _c += a; cum.append(_c)
    gap = [plan[i] - act[i] for i in range(len(slots))]
    lmin = []
    for s in slots:
        bd = max(0.0, float(s.get("breakdown_loss") or 0))
        sp = max(0.0, float(s.get("speed_loss") or 0))
        lmin.append(round((bd + sp) / 60.0, 1))
    cl = []; _cc = 0.0
    for m in lmin: _cc += m; cl.append(round(_cc, 1))
    tot_plan = sum(plan); tot_act = sum(act); tot_loss = int(round(sum(lmin)))

    LOSS_CATS = [("Breakdown", "loss_breakdown_seconds"), ("Speed", "loss_speed_seconds"),
                 ("Setup", "loss_setup_seconds"), ("Quality", "loss_quality_seconds"),
                 ("Material", "loss_material_seconds"), ("Change Over", "loss_change_over_seconds"),
                 ("Others", "loss_others_seconds")]
    cats = [(n, round(float(row.get(c) or 0) / 60.0, 1)) for n, c in LOSS_CATS
            if (float(row.get(c) or 0) / 60.0) > 0]
    cats.sort(key=lambda x: x[1], reverse=True)

    # Per-hour loss descriptions (numbered when >1) — same as the Excel row.
    loss_desc = []
    max_lines = 1
    for i in range(len(slots)):
        parts = list(desc_by_slot[i])
        rem = (remarks_by_slot.get(slots[i]["label"]) or "").strip()
        if rem:
            parts.append(rem)
        sp = max(0.0, float(slots[i].get("speed_loss") or 0)) / 60.0
        if sp >= 1:
            parts.append(f"Speed ~{int(round(sp))}min")
        # matplotlib table cells don't wrap, so wrap each part to the column
        # width by hand (Excel wraps natively).
        import textwrap
        parts = [textwrap.fill(p, 13) for p in parts]
        if len(parts) > 1:
            txt = "\n".join(f"{k + 1}. {p}" for k, p in enumerate(parts))
        elif parts:
            txt = parts[0]
        else:
            txt = "-"
        loss_desc.append(txt)
        max_lines = max(max_lines, txt.count("\n") + 1)

    cell_text = [
        ["Time"]            + labels                 + ["Total"],
        ["Plan"]            + [str(x) for x in plan] + [str(tot_plan)],
        ["Actual"]          + [str(x) for x in act]  + [str(tot_act)],
        ["Cumm. Actual"]    + [str(x) for x in cum]  + [str(cum[-1] if cum else 0)],
        ["Gap (Plan-Act)"]  + [str(x) for x in gap]  + [str(tot_plan - tot_act)],
        ["Losses"]          + loss_desc              + [""],
        ["Loss (min)"]      + [str(x) for x in lmin] + [str(tot_loss)],
        ["Cumulative Loss"] + [str(x) for x in cl]   + [str(int(cl[-1]) if cl else 0)],
    ]
    ncols  = len(labels) + 2
    nrows  = len(cell_text)
    loss_r = 5

    fig = plt.figure(figsize=(max(11.7, ncols * 1.15), 8.3))
    fig.suptitle(f"{line}     Shift {shift}     {d}", fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.02, 0.945, f"Zone: {zone}      Work Centre:", fontsize=9, color="#334155")

    ax_t = fig.add_axes([0.01, 0.42, 0.98, 0.50]); ax_t.axis("off")
    tbl = ax_t.table(cellText=cell_text, loc="upper center", cellLoc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(7.5)

    HDR, TOT, NG = "#DCE6F1", "#EFF6FF", "#FFC7CE"
    base_h = 1.0 / (nrows + max_lines - 1)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#999999"); cell.set_linewidth(0.4)
        cell.set_height(base_h * max_lines if r == loss_r else base_h)
        if r == loss_r and c not in (0,):
            cell.set_text_props(ha="left", va="center")
            cell.get_text().set_fontsize(6)
        if r == 0:
            cell.set_facecolor(HDR); cell.set_text_props(weight="bold")
        elif c == 0:
            cell.set_facecolor(HDR); cell.set_text_props(weight="bold", ha="left")
        elif c == ncols - 1:
            cell.set_facecolor(TOT); cell.set_text_props(weight="bold")
        if 1 <= c <= len(slots) and r in (2, 4):
            gi = c - 1
            if gi < len(gap) and gap[gi] > 0:
                cell.set_facecolor(NG)

    # ── Total-loss Pareto, BELOW the grid (left) ──
    if cats:
        ax_p = fig.add_axes([0.06, 0.06, 0.52, 0.29])
        names = [x[0] for x in cats]; vals = [x[1] for x in cats]
        _tot = sum(vals) or 1.0; cumpct = []; _r = 0.0
        for v in vals:
            _r += v; cumpct.append(_r / _tot * 100)
        bars = ax_p.bar(range(len(names)), vals, color="#3A86FF", width=0.6)
        ax_p.bar_label(bars, fmt="%.1f", fontsize=8, padding=2)          # data labels
        ax_p.set_xticks(range(len(names))); ax_p.set_xticklabels(names, fontsize=8)
        ax_p.set_ylabel("Minutes", fontsize=8)
        ax_p.set_title("Total Loss Pareto", fontsize=10, fontweight="bold")
        ax_p.grid(False)                                                 # no gridlines
        for _s in ("top", "right"):
            ax_p.spines[_s].set_visible(False)
        ax2 = ax_p.twinx()
        ax2.plot(range(len(names)), cumpct, "o-", color="#DC2626", linewidth=1.6)
        for _xi, _yv in enumerate(cumpct):
            ax2.annotate(f"{_yv:.0f}%", (_xi, _yv), textcoords="offset points",
                         xytext=(0, 7), ha="center", fontsize=7, color="#DC2626")
        ax2.set_ylabel("Cum %", fontsize=8); ax2.set_ylim(0, 115); ax2.grid(False)

    # ── MODEL RUNS table (right of the Pareto) — model-wise & time-wise ──
    try:
        _mruns = _model_runs(row.get("_line_id"), row.get("table_name"),
                             row.get("_report_date"), shift)
        ax_m = fig.add_axes([0.62, 0.05, 0.35, 0.29]); ax_m.axis("off")
        ax_m.set_title("Model Runs", fontsize=10, fontweight="bold")
        _hdr  = ["Model", "From", "To", "Min", "OK", "NG"]
        _rows = [[(_m["model_name"] or f"M{_m['model_number']}")[:16], _m["from"],
                  _m["to"], int(_m["duration_min"]), _m["ok"], _m["ng"]] for _m in _mruns]
        if not _rows:
            _rows = [["No model-run data", "", "", "", "", ""]]
        _t = ax_m.table(cellText=_rows, colLabels=_hdr, loc="upper center", cellLoc="center")
        _t.auto_set_font_size(False); _t.set_fontsize(7); _t.scale(1, 1.3)
        for (_r, _c), _cell in _t.get_celld().items():
            _cell.set_edgecolor("#D0D7E2")
            if _r == 0:
                _cell.set_facecolor("#1F4E79")
                _cell.set_text_props(color="white", fontweight="bold")
    except Exception as _mr:
        print(f"[REPORT] pdf model-runs skipped: {_mr}")

    return fig


def build_hourly_pdf(row: dict) -> bytes:
    """Single-shift hourly-report PDF (wrapper around _hourly_pdf_figure)."""
    import matplotlib.pyplot as plt
    fig = _hourly_pdf_figure(row)
    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def build_pdf_report(row: dict) -> bytes:
    """Return PDF bytes for one shift.  Single-page A4 landscape with
    KPI tiles, hourly bar chart, and OEE gauges drawn in matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.patches import FancyBboxPatch, Rectangle, Circle

    fig = plt.figure(figsize=(11.69, 8.27))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100); ax.set_ylim(0, 70)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)

    # ── Title bar
    ax.add_patch(Rectangle((0, 65), 100, 5, color="#1F4E79"))
    ax.text(2, 67.5,
            f"{row.get('line_name','')}  ·  Shift {row.get('shift_name','')}  ·  {row.get('_report_date','')}",
            color="white", weight="bold", fontsize=15, va="center")
    ax.text(99, 67.5, "End-of-Shift Report",
            color="white", fontsize=10, va="center", ha="right")

    # ── KPI tiles (top row)
    overall = float(row.get("overall_oee") or 0)
    grade   = row.get("oee_grade", "—") or "—"
    avail   = float(row.get("availability") or 0)
    perf    = float(row.get("performance") or 0)
    qual    = float(row.get("quality_oee") or 0)
    plan    = int(row.get("shift_plan") or 0)
    ok      = int(row.get("ok_count") or 0)
    ng      = int(row.get("ng_count") or 0)
    actual  = ok + ng

    def tile(x, y, w, h, label, value, color):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                    boxstyle="round,pad=0.1,rounding_size=0.4",
                    facecolor=color, edgecolor=color))
        ax.text(x + w/2, y + h*0.62, value, ha="center", va="center",
                color="white", weight="bold", fontsize=18)
        ax.text(x + w/2, y + h*0.22, label, ha="center", va="center",
                color="white", fontsize=9.5, alpha=0.95)

    tile( 2, 53,  18, 10, "OVERALL OEE", f"{overall:.1f}%",
          "#06A77D" if overall >= 75 else ("#F4A261" if overall >= 50 else "#D62828"))
    tile(22, 53,  18, 10, "AVAILABILITY",  f"{avail:.1f}%",  "#3A86FF")
    tile(42, 53,  18, 10, "PERFORMANCE",   f"{perf:.1f}%",   "#19376D")
    tile(62, 53,  18, 10, "QUALITY",       f"{qual:.1f}%",   "#5E548E")
    tile(82, 53,  16, 10, "GRADE",         grade,            "#475569")

    # ── Production block
    tile( 2, 41,  22, 10, "PLAN",    f"{plan:,}",   "#0B2447")
    tile(26, 41,  22, 10, "ACTUAL",  f"{actual:,}", "#06A77D")
    tile(50, 41,  22, 10, "OK",      f"{ok:,}",     "#06A77D")
    tile(74, 41,  22, 10, "NG",      f"{ng:,}",     "#D62828")

    # ── Hourly bar chart inset
    slots = _hourly_slots_from_row(row)
    _slot_losses(row.get("_line_id"), row.get("_report_date"),
                 row.get("_report_shift") or row.get("shift_name") or "", slots)
    chart = fig.add_axes([0.06, 0.07, 0.65, 0.32])
    if slots:
        idx = list(range(len(slots)))
        chart.bar([i - 0.20 for i in idx], [s["plan"] for s in slots],
                  width=0.4, label="Plan",   color="#3A86FF")
        chart.bar([i + 0.20 for i in idx], [s["actual"] for s in slots],
                  width=0.4, label="Actual", color="#06A77D")
        chart.set_xticks(idx)
        chart.set_xticklabels([s["label"] for s in slots], rotation=30, ha="right", fontsize=8)
        chart.set_title("Hourly Plan vs Actual  ·  Loss (min)", fontsize=10,
                        weight="bold", color="#1F4E79")
        chart.grid(axis="y", linestyle=":", alpha=0.5)
        for spine in ("top", "right"): chart.spines[spine].set_visible(False)

        # 2026-08-14 — the loss that caused each hour's shortfall, on a twin
        # axis in MINUTES.  Bars alone showed the shortfall; these lines show
        # what it was made of, which is the question the shortfall raises.
        loss_ax = chart.twinx()
        loss_ax.plot(idx, [float(s.get("speed_loss") or 0) / 60 for s in slots],
                     marker="o", ms=3, lw=1.4, color="#f59e0b", label="Speed loss")
        loss_ax.plot(idx, [float(s.get("breakdown_loss") or 0) / 60 for s in slots],
                     marker="s", ms=3, lw=1.4, color="#dc2626", label="Breakdown")
        loss_ax.axhline(0, color="#94a3b8", lw=0.6, ls=":")
        loss_ax.set_ylabel("loss (min)", fontsize=8, color="#64748b")
        loss_ax.tick_params(axis="y", labelsize=7, colors="#64748b")
        for spine in ("top",): loss_ax.spines[spine].set_visible(False)

        # One legend for both axes, or the two overlap in the corner.
        h1, l1 = chart.get_legend_handles_labels()
        h2, l2 = loss_ax.get_legend_handles_labels()
        chart.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=7, ncol=2)

    # ── Model / CT side block
    side = fig.add_axes([0.74, 0.07, 0.24, 0.32])
    side.set_xticks([]); side.set_yticks([])
    for s in side.spines.values(): s.set_visible(False)
    side.text(0.04, 0.92, "MODEL", color="#1F4E79", weight="bold", fontsize=10)
    side.text(0.04, 0.85, str(row.get("current_model_name") or "—"), fontsize=9, wrap=True)
    side.text(0.04, 0.72, "IDEAL CT (s)", color="#1F4E79", weight="bold", fontsize=10)
    side.text(0.04, 0.65, str(row.get("cycle_time_plan") or "—"), fontsize=11)
    side.text(0.04, 0.55, "ACTUAL CT (s)", color="#1F4E79", weight="bold", fontsize=10)
    side.text(0.04, 0.48, str(row.get("cycle_time_actual") or "—"), fontsize=11)
    side.text(0.04, 0.36, "STARTED",  color="#1F4E79", weight="bold", fontsize=10)
    side.text(0.04, 0.29, str(row.get("shift_start_time") or "—"), fontsize=9)
    side.text(0.04, 0.18, "ENDED", color="#1F4E79", weight="bold", fontsize=10)
    side.text(0.04, 0.11, str(row.get("shift_end_time") or "—"), fontsize=9)

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════
#  Endpoints
# ════════════════════════════════════════════════════════════════════

@router.get("/shift-excel")
def shift_excel(line_id: int = Query(...),
                date: str = Query(...),
                shift: str = Query(...),
                user=Depends(get_current_user)):
    """Stream a formatted Excel report for one shift.  Anyone with a
    valid token can download (intentionally permissive — reports flow
    up the org chart, no PII)."""
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    row = _load_shift_row(line_id, d, shift)
    data = build_excel_report(row)
    fname = f"shift_{row.get('line_name','line')}_{d}_{shift}.xlsx".replace(" ", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/shift-pdf")
def shift_pdf(line_id: int = Query(...),
              date: str = Query(...),
              shift: str = Query(...),
              user=Depends(get_current_user)):
    """Stream a formatted PDF report for one shift."""
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    row = _load_shift_row(line_id, d, shift)
    data = build_pdf_report(row)
    fname = f"shift_{row.get('line_name','line')}_{d}_{shift}.pdf".replace(" ", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ── Email-config CRUD ────────────────────────────────────────────────

class EmailConfigUpsert(BaseModel):
    line_id:      int
    report_kind:  str = "shift_end"
    to_addresses: str = ""
    cc_addresses: str = ""
    # Level 2 and 3.  Blank means that level is not mailed.
    zone_to_addresses:    str = ""
    zone_cc_addresses:    str = ""
    section_to_addresses: str = ""
    section_cc_addresses: str = ""
    is_active:    bool = True


@router.get("/email-config")
def list_email_config(line_id: Optional[int] = None,
                       user=Depends(get_current_user)):
    _ensure_email_config_table()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if line_id is not None:
            cur.execute("""SELECT * FROM mes_report_email_config
                            WHERE line_id = %s ORDER BY report_kind""", (line_id,))
        else:
            cur.execute("SELECT * FROM mes_report_email_config ORDER BY line_id, report_kind")
        return cur.fetchall()


@router.put("/email-config")
def upsert_email_config(body: EmailConfigUpsert,
                         admin=Depends(require_admin)):
    _ensure_email_config_table()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_report_email_config
                (line_id, report_kind, to_addresses, cc_addresses,
                 zone_to_addresses, zone_cc_addresses,
                 section_to_addresses, section_cc_addresses, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (line_id, report_kind) DO UPDATE
                SET to_addresses         = EXCLUDED.to_addresses,
                    cc_addresses         = EXCLUDED.cc_addresses,
                    zone_to_addresses    = EXCLUDED.zone_to_addresses,
                    zone_cc_addresses    = EXCLUDED.zone_cc_addresses,
                    section_to_addresses = EXCLUDED.section_to_addresses,
                    section_cc_addresses = EXCLUDED.section_cc_addresses,
                    is_active            = EXCLUDED.is_active,
                    updated_at           = NOW()
        """, (body.line_id, body.report_kind,
              body.to_addresses, body.cc_addresses,
              body.zone_to_addresses, body.zone_cc_addresses,
              body.section_to_addresses, body.section_cc_addresses,
              body.is_active))
        conn.commit()
    return {"ok": True}


# ── Manual fire (admin) ──────────────────────────────────────────────

class EmailNowBody(BaseModel):
    line_id: int
    date:    str   # YYYY-MM-DD
    shift:   str
    kinds:   List[str] = ["excel", "pdf"]   # which attachments


@router.post("/email-now")
def email_now(body: EmailNowBody, admin=Depends(require_admin)):
    """Generate the shift report(s) and email them to the configured
    recipients NOW.  Useful for testing the chain or replaying a missed
    auto-send."""
    _ensure_email_config_table()
    try:
        d = datetime.strptime(body.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    row = _load_shift_row(body.line_id, d, body.shift)

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT * FROM mes_report_email_config
                        WHERE line_id=%s AND report_kind='shift_end' AND is_active=TRUE""",
                    (body.line_id,))
        cfg = cur.fetchone()
    # Any of the three levels having a To is enough to send — a line whose
    # recipients live only at zone level must still get its report.
    to_list, cc_list = _resolve_recipients(cfg or {})
    if not to_list:
        raise HTTPException(400, "No active email-config for this line — add recipients first.")
    subject = f"[MES · End-of-Shift] {row.get('line_name','')} — Shift {body.shift} {body.date}"
    overall = float(row.get("overall_oee") or 0)
    grade   = row.get("oee_grade", "—") or "—"
    html = f"""
    <p>End-of-shift summary attached.</p>
    <ul>
      <li>OEE: <b>{overall:.1f}%</b> ({grade})</li>
      <li>Plan: {row.get('shift_plan','—')}  ·  Actual: {(row.get('ok_count',0) or 0)+(row.get('ng_count',0) or 0)}</li>
      <li>OK: {row.get('ok_count','—')}  ·  NG: {row.get('ng_count','—')}</li>
      <li>Model: {row.get('current_model_name','—')}</li>
    </ul>"""

    # We attach files via multipart — extend _send_email with our own
    # attachment-aware send (the breakdown_mail._send_email is HTML-only).
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not (smtp_user and smtp_pass):
        raise HTTPException(500, "SMTP credentials not configured (.env SMTP_USER / SMTP_PASS)")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to_list)
    if cc_list: msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(html, "html"))

    if "excel" in body.kinds:
        xlsx = build_excel_report(row)
        part = MIMEApplication(xlsx, _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        part.add_header("Content-Disposition", "attachment",
                        filename=f"shift_{body.date}_{body.shift}.xlsx")
        msg.attach(part)
    if "pdf" in body.kinds:
        pdf = build_pdf_report(row)
        part = MIMEApplication(pdf, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment",
                        filename=f"shift_{body.date}_{body.shift}.pdf")
        msg.attach(part)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.ehlo(); server.starttls(); server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_list + cc_list, msg.as_string())
    return {"ok": True, "to": to_list, "cc": cc_list}


# ════════════════════════════════════════════════════════════════════
#  Auto-mail scheduler  (run as a daemon thread from main.py startup)
# ════════════════════════════════════════════════════════════════════

_AUTO_REPORT_THREAD: Optional[threading.Thread] = None
_AUTO_REPORT_STOP   = threading.Event()
_LAST_SENT_KEY: dict = {}   # {(line_id, date, shift): True}


def _shift_end_today(line_id: int, shift_name: str) -> Optional[datetime]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT start_time, end_time FROM mes_shift_configs
                        WHERE line_id=%s AND shift_name=%s""", (line_id, shift_name))
        r = cur.fetchone()
        if not r or not r.get("end_time"): return None
        return datetime.combine(date.today(), r["end_time"])


def _scheduler_tick() -> None:
    """One pass through every line × every active shift config.  When
    the wall-clock has just crossed `end_time + 90s` and we haven't
    sent today, fire the email and remember we did."""
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT DISTINCT c.line_id, sc.shift_name
                             FROM mes_report_email_config c
                             JOIN mes_shift_configs sc ON sc.line_id = c.line_id
                            WHERE c.is_active = TRUE
                              AND c.report_kind = 'shift_end'
                              AND NOT sc.shift_name LIKE 'GAP%'""")
            jobs = cur.fetchall()
    except Exception as exc:
        print(f"[REPORT-SCHED] DB error: {exc}")
        return

    now = datetime.now()
    for j in jobs:
        line_id, shift = j["line_id"], j["shift_name"]
        end_dt = _shift_end_today(line_id, shift)
        if not end_dt:
            continue
        # Send window: 60-240 s after shift end (90 s grace for collector
        # to flush its final row).  Single-shot per (line, date, shift).
        delta = (now - end_dt).total_seconds()
        if not (60 <= delta <= 240):
            continue
        key = (line_id, date.today(), shift)
        if _LAST_SENT_KEY.get(key):
            continue
        try:
            from fastapi.testclient import TestClient   # not used; reuse via direct call
        except Exception:
            pass
        # Direct in-process call — mimics the email_now() body without HTTP.
        try:
            row = _load_shift_row(line_id, date.today(), shift)
            with get_conn() as conn:
                cur = dict_cursor(conn)
                cur.execute("""SELECT * FROM mes_report_email_config
                                WHERE line_id=%s AND report_kind='shift_end' AND is_active=TRUE""",
                            (line_id,))
                cfg = cur.fetchone()
            if not cfg:
                continue
            to_list, cc_list = _resolve_recipients(cfg)
            if not to_list:
                continue
            xlsx = build_excel_report(row)
            pdf  = build_pdf_report(row)
            import smtplib
            from email.mime.text        import MIMEText
            from email.mime.multipart   import MIMEMultipart
            from email.mime.application import MIMEApplication
            smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
            smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
            smtp_user = os.getenv("SMTP_USER", "")
            smtp_pass = os.getenv("SMTP_PASS", "")
            if not (smtp_user and smtp_pass):
                print("[REPORT-SCHED] SMTP_USER/PASS not set, skipping send.")
                _LAST_SENT_KEY[key] = True   # don't retry every minute
                continue

            overall = float(row.get("overall_oee") or 0)
            grade   = row.get("oee_grade", "—") or "—"
            msg = MIMEMultipart()
            msg["Subject"] = f"[MES · End-of-Shift] {row.get('line_name','')} — Shift {shift} {date.today()}"
            msg["From"] = smtp_user
            msg["To"]   = ", ".join(to_list)
            if cc_list: msg["Cc"] = ", ".join(cc_list)
            html = f"""<p>End-of-shift summary attached.</p>
                       <ul><li>OEE: <b>{overall:.1f}%</b> ({grade})</li>
                       <li>Plan: {row.get('shift_plan','—')}  ·  Actual: {(row.get('ok_count',0) or 0)+(row.get('ng_count',0) or 0)}</li>
                       <li>OK: {row.get('ok_count','—')}  ·  NG: {row.get('ng_count','—')}</li>
                       <li>Model: {row.get('current_model_name','—')}</li></ul>"""
            msg.attach(MIMEText(html, "html"))
            p1 = MIMEApplication(xlsx, _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            p1.add_header("Content-Disposition", "attachment",
                          filename=f"shift_{date.today()}_{shift}.xlsx")
            msg.attach(p1)
            p2 = MIMEApplication(pdf, _subtype="pdf")
            p2.add_header("Content-Disposition", "attachment",
                          filename=f"shift_{date.today()}_{shift}.pdf")
            msg.attach(p2)
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as srv:
                srv.ehlo(); srv.starttls(); srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_user, to_list + cc_list, msg.as_string())
            _LAST_SENT_KEY[key] = True
            print(f"[REPORT-SCHED] Sent shift-end report for line {line_id} shift {shift} to {to_list}")
        except Exception as exc:
            print(f"[REPORT-SCHED] send failed for line {line_id} shift {shift}: {exc}")
            traceback.print_exc()


def _scheduler_loop() -> None:
    while not _AUTO_REPORT_STOP.is_set():
        try:
            _scheduler_tick()
        except Exception as exc:
            print(f"[REPORT-SCHED] tick error: {exc}")
        # Reset the "sent today" memory at midnight so tomorrow's shifts
        # can fire fresh.
        if datetime.now().hour == 0 and datetime.now().minute < 2:
            _LAST_SENT_KEY.clear()
        _AUTO_REPORT_STOP.wait(30)


def start_scheduler() -> None:
    global _AUTO_REPORT_THREAD
    if _AUTO_REPORT_THREAD and _AUTO_REPORT_THREAD.is_alive():
        return
    _AUTO_REPORT_STOP.clear()
    _AUTO_REPORT_THREAD = threading.Thread(target=_scheduler_loop, daemon=True,
                                            name="report-scheduler")
    _AUTO_REPORT_THREAD.start()
    print("[REPORT-SCHED] Worker started — checks every 30 s for end-of-shift sends")
