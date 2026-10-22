"""
Historical → Breakdown History  (2026-10-07)

One list of every breakdown slip, from the moment it is raised until it is
closed, merged from all places a slip can live:

    maintenance_db (Maintenance_DX, port 9965 flow)
        maintenance_auto_breakdown_slip   AUTO-MAINT    (andon Maintenance call)
        toolroom_auto_breakdown_slip      AUTO-TOOLROOM (andon Toolroom call)
        maintenance_breakdown_data        MANUAL        (filled on 9965)
    energydb (MES)
        mes_breakdown_log                 MES-LOG       (Historical "+ Add Breakdown")
        mes_breakdowns                    MES-SLIP      (old Breakdown Slips tab)

Stage of an auto slip follows its prod_stage:
    PENDING_PRODUCTION -> PENDING_MAINTENANCE -> COMPLETED
A manual 9965 slip is filled by maintenance in one go, so it is CLOSED.

Read-only; scoped to the user's assigned lines (same rule as Andon History).
GET /api/breakdown-history          merged list + summary + zone/line options
GET /api/breakdown-history/slip     one slip, every field (full slip view)
"""
import io
import json
import os
import re
from datetime import date, datetime, time as dtime
from decimal import Decimal
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth import get_current_user
from database import get_conn, dict_cursor
from routers.andon import _maint_conn, _user_line_scope, _norm

router = APIRouter(prefix="/api/breakdown-history", tags=["breakdown-history"])

MAINT_TABLES = {
    "AUTO-MAINT":    ("maintenance_auto_breakdown_slip", "Maintenance", True),
    "AUTO-TOOLROOM": ("toolroom_auto_breakdown_slip",    "Toolroom",    True),
    "MANUAL":        ("maintenance_breakdown_data",      "Maintenance", False),
}
STAGE_LABEL = {
    "PENDING_PRODUCTION":  "Production pending",
    "PENDING_MAINTENANCE": "Maintenance pending",
    "COMPLETED":           "Closed",
    "CLOSED":              "Closed",
    "OPEN":                "Open",
    "RESOLVED":            "Resolved",
}


def _stage_label(stage, dept=None):
    """2026-10-21 — a Toolroom slip waiting on its own side reads "Toolroom
    pending" (operator: it said Maintenance pending for Toolroom breakdowns)."""
    if stage == "PENDING_MAINTENANCE" and str(dept or "").lower().startswith("tool"):
        return "Toolroom pending"
    return STAGE_LABEL.get(stage, stage)
_COMMON = ("id, zone, line, machine_no, machine_name, slip_date, shift, model_no, "
           "category, bd_start_date, bd_start_time, bd_end_date, bd_ok_time, "
           "bd_received_time, mc_down_time_minutes, response_time_minutes, "
           "problem_reported_by_production, problem_observed_by_maintenance, "
           "action_taken_on_problem, spares_used, spares, bd_attended_by, "
           "line_leader_name, machine_operator_name, submitted_at")


def _s(v):
    """JSON-safe scalar."""
    if v is None:
        return None
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _txt(v):
    v = _s(v)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _in_scope(scope, line):
    kind, names = scope
    if kind == "all":
        return True
    if kind == "none":
        return False
    return _norm(line) in names


def _maint_rows(scope):
    out = []
    with _maint_conn() as mc:
        cur = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for src, (table, dept, is_auto) in MAINT_TABLES.items():
            extra = ", prod_stage, andon_event_id" if is_auto else ""
            cur.execute(f"SELECT {_COMMON}{extra} FROM {table}")
            for r in cur.fetchall():
                if not _in_scope(scope, r.get("line")):
                    continue
                stage = (r.get("prod_stage") or "PENDING_PRODUCTION") if is_auto else "COMPLETED"
                spares = _txt(r.get("spares_used")) or _txt(r.get("spares"))
                d = r.get("slip_date") or r.get("bd_start_date")
                out.append({
                    "key": f"{src}:{r['id']}", "src": src, "id": r["id"], "dept": dept,
                    "date": _s(d), "shift": _txt(r.get("shift")),
                    "zone": _txt(r.get("zone")), "line": _txt(r.get("line")),
                    "machine_no": _txt(r.get("machine_no")),
                    "machine_name": _txt(r.get("machine_name")),
                    "model": _txt(r.get("model_no")), "category": _txt(r.get("category")),
                    "bd_start": " ".join(x for x in (_txt(r.get("bd_start_date")),
                                                     _txt(r.get("bd_start_time"))) if x) or None,
                    "bd_ok": " ".join(x for x in (_txt(r.get("bd_end_date")),
                                                  _txt(r.get("bd_ok_time"))) if x) or None,
                    "down_min": _s(r.get("mc_down_time_minutes")),
                    "response_min": _s(r.get("response_time_minutes")),
                    "problem": _txt(r.get("problem_reported_by_production")),
                    "observed": _txt(r.get("problem_observed_by_maintenance")),
                    "action": _txt(r.get("action_taken_on_problem")),
                    "spares": spares,
                    "attended_by": _txt(r.get("bd_attended_by")),
                    "line_leader": _txt(r.get("line_leader_name")),
                    "stage": stage, "stage_label": _stage_label(stage, dept),
                    "closed": stage == "COMPLETED",
                    "andon_event_id": r.get("andon_event_id"),
                })
    return out


def _mes_rows(scope):
    out = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, bd_date, shift, zone_code, line_code, machine_no,
                              machine_name, model_no, problem_production,
                              problem_maintenance, action_taken, spares_detail,
                              attended_by, dept, category, solve_time_min,
                              solve_time_hours, line_leader_name
                         FROM mes_breakdown_log""")
        for r in cur.fetchall():
            if not _in_scope(scope, r.get("line_code")):
                continue
            mins = r.get("solve_time_min")
            if mins is None and r.get("solve_time_hours") is not None:
                mins = float(r["solve_time_hours"]) * 60
            out.append({
                "key": f"MES-LOG:{r['id']}", "src": "MES-LOG", "id": r["id"],
                "dept": _txt(r.get("dept")), "date": _s(r.get("bd_date")),
                "shift": _txt(r.get("shift")), "zone": _txt(r.get("zone_code")),
                "line": _txt(r.get("line_code")), "machine_no": _txt(r.get("machine_no")),
                "machine_name": _txt(r.get("machine_name")), "model": _txt(r.get("model_no")),
                "category": _txt(r.get("category")), "bd_start": None, "bd_ok": None,
                "down_min": round(float(mins), 1) if mins is not None else None,
                "response_min": None, "problem": _txt(r.get("problem_production")),
                "observed": _txt(r.get("problem_maintenance")),
                "action": _txt(r.get("action_taken")), "spares": _txt(r.get("spares_detail")),
                "attended_by": _txt(r.get("attended_by")),
                "line_leader": _txt(r.get("line_leader_name")),
                "stage": "CLOSED", "stage_label": "Closed", "closed": True,
                "andon_event_id": None,
            })
        cur.execute("""SELECT b.id, b.started_at, b.ended_at, b.shift_name, b.state,
                              b.reason, l.line_name, z.zone_name
                         FROM mes_breakdowns b
                         LEFT JOIN mes_lines l ON l.id = b.line_id
                         LEFT JOIN mes_zones z ON z.id = b.zone_id""")
        for r in cur.fetchall():
            if not _in_scope(scope, r.get("line_name")):
                continue
            st, en = r.get("started_at"), r.get("ended_at")
            state = (r.get("state") or "").strip().upper()
            out.append({
                "key": f"MES-SLIP:{r['id']}", "src": "MES-SLIP", "id": r["id"],
                "dept": None, "date": _s(st.date()) if st else None,
                "shift": _txt(r.get("shift_name")), "zone": _txt(r.get("zone_name")),
                "line": _txt(r.get("line_name")), "machine_no": None, "machine_name": None,
                "model": None, "category": None,
                "bd_start": _s(st)[:19] if st else None, "bd_ok": _s(en)[:19] if en else None,
                "down_min": round((en - st).total_seconds() / 60, 1) if (st and en) else None,
                "response_min": None, "problem": _txt(r.get("reason")), "observed": None,
                "action": None, "spares": None, "attended_by": None, "line_leader": None,
                "stage": state, "stage_label": STAGE_LABEL.get(state, state.title()),
                "closed": state in ("CLOSED", "RESOLVED"), "andon_event_id": None,
            })
    return out


@router.get("")
def breakdown_history(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    zone:      Optional[str] = Query(None),
    line:      Optional[str] = Query(None),
    stage:     Optional[str] = Query(None, description="open | closed"),
    src:       Optional[str] = Query(None),
    q:         Optional[str] = Query(None),
    limit:     int = Query(3000, ge=1, le=10000),
    user=Depends(get_current_user),
):
    scope = _user_line_scope(user)
    rows, warn = [], None
    try:
        rows += _maint_rows(scope)
    except HTTPException as e:
        warn = f"Maintenance slips not available: {e.detail}"
    rows += _mes_rows(scope)

    # zone -> lines options from everything the user may see (before filters)
    opts = {}                      # norm(zone) -> [display, {norm(line): display}]
    for r in rows:
        if r["zone"]:
            z = opts.setdefault(_norm(r["zone"]), [r["zone"], {}])
            if r["line"]:
                z[1].setdefault(_norm(r["line"]), r["line"])

    def keep(r):
        d = (r["date"] or "")[:10]
        if date_from and (not d or d < date_from):
            return False
        if date_to and (not d or d > date_to):
            return False
        if zone and _norm(r["zone"]) != _norm(zone):
            return False
        if line and _norm(r["line"]) != _norm(line):
            return False
        if stage == "open" and r["closed"]:
            return False
        if stage == "closed" and not r["closed"]:
            return False
        if src and r["src"] != src:
            return False
        if q:
            ql = q.strip().lower()
            hay = " ".join(str(r.get(k) or "") for k in (
                "machine_no", "machine_name", "problem", "observed", "action",
                "spares", "attended_by", "model", "line_leader")).lower()
            if ql not in hay:
                return False
        return True

    rows = [r for r in rows if keep(r)]
    rows.sort(key=lambda r: ((r["date"] or ""), (r["bd_start"] or "")), reverse=True)
    total_min = sum(float(r["down_min"] or 0) for r in rows)
    summary = {
        "total": len(rows),
        "open": sum(1 for r in rows if not r["closed"]),
        "closed": sum(1 for r in rows if r["closed"]),
        "production_pending": sum(1 for r in rows if r["stage"] == "PENDING_PRODUCTION"),
        "maintenance_pending": sum(1 for r in rows if r["stage"] == "PENDING_MAINTENANCE"
                                   and not str(r.get("dept") or "").lower().startswith("tool")),
        "toolroom_pending": sum(1 for r in rows if r["stage"] == "PENDING_MAINTENANCE"
                                and str(r.get("dept") or "").lower().startswith("tool")),
        "downtime_hours": round(total_min / 60, 1),
    }
    return {
        "rows": rows[:limit], "summary": summary, "warning": warn,
        "options": [{"zone": zd, "lines": sorted(ls.values())}
                    for zd, ls in sorted(opts.values(), key=lambda v: v[0])],
    }


@router.get("/slip")
def slip(src: str, id: int, user=Depends(get_current_user)):
    """Every field of one Maintenance_DX slip, for the full-slip view."""
    return _load_slip(src, id, user)


def _load_slip(src: str, id: int, user) -> dict:
    if src not in MAINT_TABLES:
        raise HTTPException(400, "Full slip view is available for Maintenance slips only")
    table, dept, is_auto = MAINT_TABLES[src]
    with _maint_conn() as mc:
        cur = mc.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {table} WHERE id = %s", (id,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "Slip not found")
    if not _in_scope(_user_line_scope(user), r.get("line")):
        raise HTTPException(403, "This line is not assigned to you")
    out = {k: _s(v) for k, v in r.items()}
    stage = (r.get("prod_stage") or "PENDING_PRODUCTION") if is_auto else "COMPLETED"
    out.update({"src": src, "dept": dept, "stage": stage,
                "stage_label": _stage_label(stage, dept)})
    return out


# ── 2026-10-16 — download one slip as a PDF (operator: "breakdown history ke
# slip ka PDF download button").  Same paper as BreakdownSlipPaper.jsx /
# the 9965 slip (TBDI / MAINT. / F / 001), drawn on A4 with matplotlib like
# the other MES PDFs (no extra library on the box).
_SRC_LABEL = {"AUTO-MAINT": "Auto slip - Maintenance", "AUTO-TOOLROOM": "Auto slip - Tool Room",
              "MANUAL": "Manual slip"}
_LOGO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                      "mes-frontend", "public", "logo.jpg"))


@router.get("/slip/pdf")
def slip_pdf(src: str, id: int, user=Depends(get_current_user)):
    d = _load_slip(src, id, user)
    blob = _slip_pdf_bytes(d)
    line = re.sub(r"[^A-Za-z0-9_-]+", "_", str(d.get("line") or "line")).strip("_")
    name = f"BD_Slip_{line}_{d.get('slip_date') or ''}_{src}_{id}.pdf".replace("__", "_")
    return StreamingResponse(io.BytesIO(blob), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _slip_pdf_bytes(d: dict) -> bytes:
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    W, H, M = 210.0, 297.0, 10.0          # A4 in mm, margin
    fig = plt.figure(figsize=(W / 25.4, H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis("off")
    INK, SHADE, GRID = "#0f172a", "#f1f5f9", "#cbd5e1"

    def t(v):
        return "" if v is None or str(v).strip() == "" else str(v).strip()

    def box(x, y, w, h, fill=None, lw=0.9, ec=INK):
        ax.add_patch(Rectangle((x, y), w, h, fill=fill is not None, facecolor=fill or "none",
                               edgecolor=ec, linewidth=lw))

    def text(x, y, s, size=8, bold=False, ha="left", va="center", color=INK):
        ax.text(x, y, s, fontsize=size, fontweight="bold" if bold else "normal",
                ha=ha, va=va, color=color, family="DejaVu Sans")

    def wrapped(x, y, w, h, s, size=8, bold=False, pad=1.6):
        """Text wrapped into the box; the font shrinks (to 5.5 pt) before it is cut."""
        s = t(s)
        if not s:
            return
        size_ = size
        while True:
            cw = size_ * 0.3528 * (0.68 if bold else 0.62)   # avg DejaVu glyph width, mm
            per = max(4, int((w - 2 * pad) / cw))
            lines = []
            for para in s.splitlines() or [s]:
                lines += textwrap.wrap(para, per) or [""]
            lh = size_ * 0.3528 * 1.25
            if len(lines) * lh <= h - 2 * pad or size_ <= 5.5:
                break
            size_ -= 0.5
        maxl = max(1, int((h - 2 * pad) / lh))
        if len(lines) > maxl:
            lines = lines[:maxl]
            lines[-1] = lines[-1][:max(0, per - 1)] + "…"
        top = y + h / 2 - (len(lines) - 1) * lh / 2 if len(lines) * lh < h else y + pad + lh / 2
        for i, ln in enumerate(lines):
            text(x + pad, top + i * lh, ln, size=size_, bold=bold)

    def tick(x, y, on, round_=False, s=3.6):
        if round_:
            from matplotlib.patches import Circle
            ax.add_patch(Circle((x + s / 2, y + s / 2), s / 2, fill=on, facecolor="#2563eb" if on else "none",
                                edgecolor="#2563eb" if on else "#475569", linewidth=0.9))
        else:
            box(x, y, s, s, fill="#2563eb" if on else None, ec="#2563eb" if on else "#475569")
        if on:
            text(x + s / 2, y + s / 2 + 0.1, "✓", size=7, bold=True, ha="center", color="white")

    def cell_grid(y, cells, rh=9.0, lw_label=30.0):
        cw = (W - 2 * M) / 3
        for i, (lab, val) in enumerate(cells):
            r, c = divmod(i, 3)
            x, yy = M + c * cw, y + r * rh
            box(x, yy, lw_label, rh, fill=SHADE)
            box(x + lw_label, yy, cw - lw_label, rh)
            if lab:
                wrapped(x, yy, lw_label, rh, f"{lab} :-", size=6.2, bold=True)
                wrapped(x + lw_label, yy, cw - lw_label, rh, val, size=7.8, bold=True)
        return y + ((len(cells) + 2) // 3) * rh

    def row(y, label, val, h=16.0, lw_label=52.0):
        box(M, y, lw_label, h, fill=SHADE)
        box(M + lw_label, y, W - 2 * M - lw_label, h)
        wrapped(M, y, lw_label, h, label, size=6.8, bold=True)
        wrapped(M + lw_label, y, W - 2 * M - lw_label, h, val, size=8)
        return y + h

    # ── letterhead
    y = M
    box(M, y, W - 2 * M, 20, lw=1.4)
    box(M, y, 28, 20, lw=1.4)
    try:
        if os.path.exists(_LOGO):
            img = plt.imread(_LOGO)
            ax.imshow(img, extent=(M + 4, M + 24, y + 15, y + 2), aspect="auto", zorder=2)
    except Exception:
        pass
    text(M + 14, y + 17.5, "TOYOTA BOSHOKU", size=5, bold=True, ha="center")
    text(W / 2, y + 7.5, "TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.", size=12.5, bold=True, ha="center")
    text(W / 2, y + 14, "BREAK DOWN SLIP", size=10.5, bold=True, ha="center")
    box(W - M - 38, y, 38, 20, lw=1.4)
    text(W - M - 19, y + 8, t(d.get("stage_label")), size=7.5, bold=True, ha="center")
    text(W - M - 19, y + 13.5, _SRC_LABEL.get(d.get("src"), t(d.get("src"))), size=6, ha="center",
         color="#475569")
    y += 22

    y = cell_grid(y, [
        ("ZONE", d.get("zone")), ("MACHINE NO.", d.get("machine_no")), ("DATE", d.get("slip_date")),
        ("LINE", d.get("line")), ("SHIFT", d.get("shift")), ("LINE LEADER NAME", d.get("line_leader_name")),
        ("MACHINE OPERATOR NAME", d.get("machine_operator_name")), ("MACHINE NAME", d.get("machine_name")),
        ("MODEL NO.", d.get("model_no")),
    ])

    # ── category
    cat = t(d.get("category")).upper()
    box(M, y, W - 2 * M, 7, fill=SHADE)
    text(M + 2, y + 3.5, "BREAK DOWN TYPE ( CATEGORY ) :-", size=7.2, bold=True)
    text(W - M - 12, y + 3.5, "TICK (✓)", size=6, bold=True, ha="center")
    y += 7
    for code, desc in (("A", "MACHINE OR LINE HAS STOPPED AND PRODUCTION LOSS DIRECTLY"),
                       ("B", "MACHINE RUNNING WITH PRODUCTION LOSS ( PRODUCTION EFFECTED ) ( ADJUSTMENT )")):
        box(M, y, 36, 8, fill="#f8fafc"); box(M + 36, y, W - 2 * M - 36 - 24, 8); box(W - M - 24, y, 24, 8)
        text(M + 2, y + 4, f"{code} CATEGORY B/D :-", size=7, bold=True)
        wrapped(M + 36, y, W - 2 * M - 60, 8, desc, size=7)
        tick(W - M - 12 - 1.8, y + 2.2, cat == code)
        y += 8

    y = cell_grid(y, [
        ("B/D START TIME", d.get("bd_start_time")), ("B/D RECEIVED TIME", d.get("bd_received_time")),
        ("RESPONSE TIME ( MIN )", d.get("response_time_minutes")),
        ("B/D OK TIME", d.get("bd_ok_time")), ("M/C DOWN TIME ( MIN )", d.get("mc_down_time_minutes")),
        ("FREQUENCY", d.get("frequency")),
        ("B/D START DATE", d.get("bd_start_date")), ("B/D END DATE", d.get("bd_end_date")), ("", ""),
    ])
    y = row(y, "PROBLEM REPORTED BY PRODUCTION", d.get("problem_reported_by_production"))

    # ── maintenance / tool room half
    box(M, y, W - 2 * M, 7, fill="#fee2e2")
    text(M + 2, y + 3.5, "TO BE FILLED BY MAINTENANCE / TOOL ROOM :-", size=7.5, bold=True, color="#991b1b")
    y += 7
    rel = t(d.get("problem_related_to") or ("tool_room" if d.get("dept") == "Toolroom" else "")).lower()

    def opts_row(y, label, opts):
        box(M, y, W - 2 * M, 8)
        text(M + 2, y + 4, label, size=7, bold=True)
        x = M + 92
        for name, on, rnd in opts:
            tick(x, y + 2.2, on, round_=rnd)
            text(x + 5, y + 4, name, size=7, bold=True)
            x += 38
        return y + 8

    y = opts_row(y, "PROBLEM RELATED TO ( PLEASE TICK )",
                 [("MAINTENANCE", rel.startswith("maint"), True), ("TOOL ROOM", "tool" in rel, True)])
    y = opts_row(y, "TYPE OF PROBLEM ( TICK ALL THAT APPLY )",
                 [("ELECTRICAL", bool(d.get("type_electrical")), False),
                  ("MECHANICAL", bool(d.get("type_mechanical")), False)])
    y = row(y, "ACTUAL PROBLEM OBSERVED BY MAINTENANCE / TOOL ROOM", d.get("problem_observed_by_maintenance"))
    y = row(y, "ACTION TAKEN ON PROBLEM", d.get("action_taken_on_problem"))

    spares = d.get("spares")
    if isinstance(spares, str):
        try:
            spares = json.loads(spares)
        except Exception:
            spares = []
    spares = [sp for sp in (spares if isinstance(spares, list) else [])
              if isinstance(sp, dict) and any(t(v) for v in sp.values())]
    used = bool(spares) or bool(t(d.get("spares_used")))
    closed = d.get("stage") in ("COMPLETED", "CLOSED")
    y = opts_row(y, "SPARE USED ?", [("YES", used, True), ("NO", (not used) and closed, True)])
    if used:
        cols = (("spare_name", "SPARE NAME"), ("spare_model_no", "MODEL NUMBER"),
                ("spare_cnmm_no", "SPARE ERP NUMBER"), ("spare_qty", "QUANTITY"))
        cw = (W - 2 * M) / 4
        for i, (_k, lab) in enumerate(cols):
            box(M + i * cw, y, cw, 6, fill=SHADE)
            text(M + i * cw + 1.6, y + 3, lab, size=6.2, bold=True)
        y += 6
        if spares:
            for sp in spares[:6]:
                for i, (k, _lab) in enumerate(cols):
                    box(M + i * cw, y, cw, 7)
                    wrapped(M + i * cw, y, cw, 7, sp.get(k), size=7.2)
                y += 7
        else:
            y = row(y, "SPARES", d.get("spares_used"), h=9)
    y = row(y, "B/D ATTENDED BY", d.get("bd_attended_by"), h=10)

    # ── names
    heads = ("PREPARED BY :-", "RECEIVED BY :-", "HANDOVER TO :- LINE LEADER / OPERATOR",
             "HANDOVER TO :- QUALITY ENGINEER")
    keys = ("prepared_by_name", "received_by_name", "line_leader_operator_name", "quality_engineer_name")
    cw = (W - 2 * M) / 4
    for i, hd in enumerate(heads):
        box(M + i * cw, y, cw, 9, fill=SHADE)
        wrapped(M + i * cw, y, cw, 9, hd, size=6.5, bold=True)
        box(M + i * cw, y + 9, cw, 11)
        text(M + i * cw + 1.6, y + 14.5, "NAME :-", size=6.5, bold=True)
        wrapped(M + i * cw + 12, y + 9, cw - 12, 11, d.get(keys[i]), size=7.5, bold=True)
    y += 22

    text(W / 2, y + 4, "FORMAT NO.:- TBDI / MAINT. / F / 001      REV. NO.:- 00      REV. DATE:- 20/03/2024",
         size=7.5, bold=True, ha="center")
    stamp = []
    if d.get("submitted_at"):
        stamp.append(f"Submitted {str(d.get('submitted_at'))[:16]}")
    if d.get("production_at"):
        stamp.append(f"Production filled {str(d.get('production_at'))[:16]}")
    stamp.append(f"Downloaded from MES {datetime.now():%d-%m-%Y %H:%M}")
    text(W / 2, H - 6, "  ·  ".join(stamp), size=6, ha="center", color="#64748b")

    buf = io.BytesIO()
    fig.savefig(buf, format="pdf")
    plt.close(fig)
    return buf.getvalue()
