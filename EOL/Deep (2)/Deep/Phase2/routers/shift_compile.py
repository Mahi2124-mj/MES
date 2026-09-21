"""
routers/shift_compile.py

Shift Compile — a shift-end review + sign-off layer, built 2026-08-25.

WHAT IT IS (operator spec):
  * The LINE LEADER (role section_incharge, scoped to their line) opens this
    page, sees their shift's FULL compiled data in one place — production
    (OK/NG/plan/OEE), manpower per machine, punched-in operators — and then
    slides a "close shift" control to sign the shift off.
  * SECTION / PRODUCTION / PLANT heads (admin, plant_head, production,
    production_incharge) get a read-only OVERVIEW across every line they can
    see: which shifts were closed, on-time or late, by whom — and can drill
    into any line's compiled data.

SAFETY (important):
  This is a SEPARATE sign-off record.  It lives in its own table
  `mes_shift_compile` and NEVER touches the counting/OEE shift-rotation path:
  it does not flip `is_shift_completed`, does not pulse any PLC reset bit
  (L110 / shift_reset_bit), does not bump the collector's shift epoch, and
  does not write any dashboard/count column.  Closing here is purely a
  human "I have reviewed and closed my shift" acknowledgement.

  "On-time" = closed at/before the shift's scheduled end_time
  (mes_shift_configs.end_time, crosses_midnight aware) — same source the
  manpower module uses to lock edits after the shift ends.
"""
from datetime import date, datetime, timedelta, time as _time
from typing import Optional
import re
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, FLOOR_SCOPED_ROLES
from ddl_once import once

router = APIRouter(prefix="/api/shift-compile", tags=["shift-compile"])

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")   # guard dynamic table names


# ── schema ─────────────────────────────────────────────────────────────
@once
def _ensure_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_shift_compile (
                id            SERIAL PRIMARY KEY,
                line_id       INTEGER     NOT NULL,
                record_date   DATE        NOT NULL,
                shift_name    VARCHAR(10) NOT NULL,
                closed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                closed_by     VARCHAR(80),
                scheduled_end TIMESTAMPTZ,
                on_time       BOOLEAN,
                note          TEXT,
                UNIQUE (line_id, record_date, shift_name)
            )
        """)
        conn.commit()


# ── helpers ────────────────────────────────────────────────────────────
def _all_shifts(cur, line_id: int):
    """Every compile-able shift OPTION for a line, each a dict:
      {shift_name, label, type, base, end_time, crosses_midnight}
    type is 'prod' (A/B), 'ot' (a shift's overtime window, synthetic
    shift_name '<X>_OT'), or 'nonprod' (the GAP periods).  'base' is the real
    dashboard shift_name to read data from (an OT option reads its parent
    shift's row).  Sorted production-first, then that shift's OT, gaps last."""
    cur.execute(
        """SELECT shift_name, start_time, end_time, crosses_midnight,
                  COALESCE(is_production, TRUE) AS is_prod,
                  ot_start_time, ot_end_time
             FROM mes_shift_configs
            WHERE line_id=%s
            ORDER BY start_time""", (line_id,))
    prod, ot, nonprod = [], [], []
    def _hm(t): return t.strftime("%H:%M") if t else ""
    for r in cur.fetchall():
        nm = r["shift_name"]
        is_gap = str(nm or "").upper().startswith("GAP") or not r["is_prod"]
        if not is_gap:
            prod.append({"shift_name": nm, "label": f"Shift {nm}", "type": "prod",
                         "base": nm, "end_time": r["end_time"],
                         "crosses_midnight": r["crosses_midnight"]})
            if r["ot_start_time"]:
                ot.append({"shift_name": f"{nm}_OT", "label": f"OT ({nm})",
                           "type": "ot", "base": nm,
                           "end_time": r["ot_end_time"] or r["end_time"],
                           "crosses_midnight": r["crosses_midnight"]})
        else:
            nonprod.append({"shift_name": nm,
                            "label": f"Non-Prod {_hm(r['start_time'])}-{_hm(r['end_time'])}",
                            "type": "nonprod", "base": nm, "end_time": r["end_time"],
                            "crosses_midnight": r["crosses_midnight"]})
    return prod + ot + nonprod


def _scheduled_end_dt(rec_date: date, end_time, crosses_midnight: bool):
    if end_time is None:
        return None
    end_dt = datetime.combine(rec_date, end_time)
    if crosses_midnight:
        end_dt += timedelta(days=1)
    return end_dt


def _accessible_lines(cur, user: dict):
    """Lines this user may see — mirrors routers/lines.list_lines scoping:
    a non-admin WITH rows in mes_operator_lines is limited to those; a user
    with none is unrestricted (admins always unrestricted)."""
    scoped = False
    if user["role"] != "admin":
        cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id=%s LIMIT 1",
                    (user["id"],))
        scoped = cur.fetchone() is not None
        if not scoped and user["role"] in FLOOR_SCOPED_ROLES:
            return []          # floor role, no assignment → no lines
    if scoped:
        cur.execute("""SELECT l.id, l.line_name, l.line_code, l.db_table_name
                         FROM mes_lines l
                         JOIN mes_operator_lines ol ON ol.line_id=l.id
                        WHERE ol.admin_id=%s AND COALESCE(l.is_active,TRUE)=TRUE
                        ORDER BY l.line_code""", (user["id"],))
    else:
        cur.execute("""SELECT id, line_name, line_code, db_table_name
                         FROM mes_lines
                        WHERE COALESCE(is_active,TRUE)=TRUE
                        ORDER BY line_code""")
    return cur.fetchall() or []


def _line_access_or_403(cur, user: dict, line_id: int):
    if user["role"] == "admin":
        return
    cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id=%s LIMIT 1",
                (user["id"],))
    has_assignment = cur.fetchone() is not None
    if has_assignment:   # scoped user — must own this line
        cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id=%s AND line_id=%s",
                    (user["id"], line_id))
        if not cur.fetchone():
            raise HTTPException(403, "Not authorized for this line")
    elif user["role"] in FLOOR_SCOPED_ROLES:
        # floor role with no assignment owns no line at all
        raise HTTPException(403, "Not authorized for this line")


def _can_close(cur, user: dict, line_id: int) -> bool:
    """Who may slide-to-close a line's shift: admins / plant heads anywhere;
    otherwise ANYONE assigned to THIS line — operator, production,
    section_incharge, production_incharge alike.
    2026-09-12 — widened from only the two *_incharge roles to every floor role
    assigned to the line, so the whole operator→incharge chain can sign off."""
    if user["role"] in ("admin", "plant_head"):
        return True
    cur.execute("SELECT 1 FROM mes_operator_lines WHERE admin_id=%s AND line_id=%s",
                (user["id"], line_id))
    return cur.fetchone() is not None


def _prod_summary(cur, db_table: str, rec_date: date, shift_name: str):
    """Latest cumulative production row for a line's shift (counting-owned;
    we only READ it)."""
    if not db_table or not _TABLE_RE.match(db_table):
        return None
    try:
        # SAVEPOINT so a bad/missing line table can't poison a shared transaction
        # (this helper loops over many lines in overview/compiled/historical).
        cur.execute("SAVEPOINT _ps")
        cur.execute(
            f"""SELECT ok_count, ng_count, shift_plan, overall_oee, availability,
                       performance, quality_oee, operating_status,
                       current_model_name, is_shift_completed
                  FROM {db_table}
                 WHERE record_date=%s AND shift_name=%s
                 ORDER BY timestamp DESC LIMIT 1""",
            (rec_date, shift_name))
        r = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT _ps")
        return dict(r) if r else None
    except Exception:
        try: cur.execute("ROLLBACK TO SAVEPOINT _ps")
        except Exception: pass
        return None


def _compile_row(cur, line_id: int, db_table: str, line_name: str,
                 rec_date: date, sh) -> dict:
    shift_name = sh["shift_name"]
    sched_end = _scheduled_end_dt(rec_date, sh["end_time"], sh["crosses_midnight"])
    cur.execute("""SELECT closed_at, closed_by, on_time
                     FROM mes_shift_compile
                    WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                (line_id, rec_date, shift_name))
    cl = cur.fetchone()
    now = datetime.now()
    if cl:
        status = "closed_ontime" if cl["on_time"] else "closed_late"
    elif sched_end and now < sched_end:
        status = "running"          # shift still in progress
    else:
        status = "pending"          # ended but nobody closed it
    prod = _prod_summary(cur, db_table, rec_date, sh.get("base", shift_name)) or {}
    return {
        "line_id":       line_id,
        "line_name":     line_name,
        "shift_name":    shift_name,
        "label":         sh.get("label", shift_name),
        "type":          sh.get("type", "prod"),
        "scheduled_end": sched_end.isoformat() if sched_end else None,
        "closed":        bool(cl),
        "closed_by":     cl["closed_by"] if cl else None,
        "closed_at":     cl["closed_at"].isoformat() if cl else None,
        "on_time":       cl["on_time"] if cl else None,
        "status":        status,
        "ok":            prod.get("ok_count"),
        "ng":            prod.get("ng_count"),
        "plan":          prod.get("shift_plan"),
        "oee":           float(prod["overall_oee"]) if prod.get("overall_oee") is not None else None,
        "model":         prod.get("current_model_name"),
    }


# ── endpoints ──────────────────────────────────────────────────────────
@router.get("/overview")
def overview(date: Optional[str] = Query(None, description="YYYY-MM-DD (default today)"),
             user=Depends(get_current_user)):
    """Cross-line shift-close status for section/production/plant heads.
    Returns every accessible line × its production shifts with close status."""
    _ensure_table()
    rec_date = _parse_date(date)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        lines = _accessible_lines(cur, user)
        out = []
        for ln in lines:
            for sh in _all_shifts(cur, ln["id"]):
                out.append(_compile_row(cur, ln["id"], ln["db_table_name"],
                                        ln["line_name"], rec_date, sh))
    # roll-up counts for the header
    total = len(out)
    closed = sum(1 for r in out if r["closed"])
    late = sum(1 for r in out if r["status"] == "closed_late")
    pending = sum(1 for r in out if r["status"] == "pending")
    return {"date": rec_date.isoformat(), "rows": out,
            "summary": {"total": total, "closed": closed,
                        "late": late, "pending": pending,
                        "running": total - closed - pending}}


@router.get("/detail")
def detail(line_id: int = Query(...),
           date: Optional[str] = Query(None),
           shift: Optional[str] = Query(None),
           user=Depends(get_current_user)):
    """Full compiled data for ONE line + shift: production summary, manpower
    per machine/process, punched-in operators, and close state."""
    _ensure_table()
    rec_date = _parse_date(date)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _line_access_or_403(cur, user, line_id)
        cur.execute("SELECT id, line_name, db_table_name, "
                    "COALESCE(ideal_cycle_time, 15.0) AS ideal_cycle_time "
                    "FROM mes_lines WHERE id=%s",
                    (line_id,))
        ln = cur.fetchone()
        if not ln:
            raise HTTPException(404, "Line not found")
        shifts = _all_shifts(cur, line_id)
        if not shift and shifts:
            shift = shifts[0]["shift_name"]          # default = first production shift
        sh = next((s for s in shifts if s["shift_name"] == shift), None) \
             or (shifts[0] if shifts else None)
        shift_name = sh["shift_name"] if sh else (shift or "A")
        base_shift = sh.get("base", shift_name) if sh else shift_name   # OT reads its parent row
        sh_type    = sh.get("type", "prod") if sh else "prod"
        sched_end  = _scheduled_end_dt(rec_date, sh.get("end_time"),
                                       sh.get("crosses_midnight")) if sh else None

        prod = _prod_summary(cur, ln["db_table_name"], rec_date, base_shift) or {}

        # Andon-covered lines: recompute the OEE shown here from the andon
        # Maintenance+Toolroom breakdown — same source & math as the Dashboard
        # (display-only; the collector's stored OEE is untouched).
        try:
            from routers.andon import recompute_oee_with_andon
            _tbl = ln["db_table_name"]
            if prod and _tbl and _TABLE_RE.match(_tbl) \
               and not str(base_shift).upper().startswith("GAP"):
                cur.execute("""SELECT start_time, end_time,
                                      COALESCE(crosses_midnight,false) AS xm
                                 FROM mes_shift_configs
                                WHERE line_id=%s AND shift_name=%s""",
                            (line_id, base_shift))
                _sc = cur.fetchone()
                if _sc and _sc.get("start_time") and _sc.get("end_time"):
                    cur.execute(f"""SELECT loss_breakdown_seconds, loss_quality_seconds,
                                           loss_setup_seconds, loss_material_seconds,
                                           loss_others_seconds, loss_change_over_seconds,
                                           loss_speed_seconds, availability, performance,
                                           quality_oee
                                      FROM {_tbl}
                                     WHERE record_date=%s AND shift_name=%s
                                     ORDER BY timestamp DESC LIMIT 1""",
                                (rec_date, base_shift))
                    _lr = cur.fetchone()
                    if _lr:
                        _st = datetime.combine(rec_date, _sc["start_time"])
                        _en = datetime.combine(rec_date, _sc["end_time"])
                        if _sc["xm"] or _en <= _st:
                            _en += timedelta(days=1)
                        _res = recompute_oee_with_andon(ln["line_name"], _lr, _st, _en)
                        if _res and _res["overall_oee"] is not None:
                            prod["overall_oee"] = _res["overall_oee"]
                            prod["availability"] = _res["availability"]
                            prod["performance"]  = _res["performance"]
        except Exception as _ax:
            print(f"[shift-compile] andon oee recompute skipped: {_ax}")

        # manpower per machine / process
        cur.execute("""
            SELECT p.process_name, p.display_order, p.machine_id,
                   p.required_manpower_count, p.machines_covered,
                   o.full_name, o.badge_code, o.skill_level,
                   a.skill_match_flag
              FROM mes_manpower_allocations a
              JOIN mes_processes p ON p.id = a.process_id
              JOIN mes_operators o ON o.id = a.operator_id
             WHERE a.line_id=%s AND a.shift_date=%s AND a.shift_name=%s
               AND a.removed_at IS NULL
             ORDER BY p.display_order, p.process_name, o.full_name""",
            (line_id, rec_date, base_shift))
        alloc = cur.fetchall() or []
        # group by process
        procs = {}
        for a in alloc:
            key = a["process_name"] or f"proc"
            g = procs.setdefault(key, {
                "process_name": a["process_name"],
                "display_order": a["display_order"],
                "required": a["required_manpower_count"],
                "machines_covered": a["machines_covered"],
                "operators": [],
            })
            g["operators"].append({
                "name": a["full_name"], "badge": a["badge_code"],
                "skill": a["skill_level"], "skill_ok": a["skill_match_flag"],
            })
        manpower = sorted(procs.values(), key=lambda x: (x["display_order"] or 999,
                                                         x["process_name"] or ""))

        # punched-in operators (present headcount)
        cur.execute("""SELECT COUNT(DISTINCT operator_id) AS n
                         FROM mes_operator_punches
                        WHERE line_id=%s AND shift_date=%s AND shift_name=%s""",
                    (line_id, rec_date, base_shift))
        punched = int((cur.fetchone() or {}).get("n") or 0)

        # close state
        cur.execute("""SELECT closed_at, closed_by, on_time, note
                         FROM mes_shift_compile
                        WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                    (line_id, rec_date, shift_name))
        cl = cur.fetchone()

        # OT / NPD assignment state (per-line) — reflected on the ASSIGN buttons.
        # Wrapped so a missing column/table can never 500 the detail view.
        ot_active = False
        npd_whole = npd_shift = False
        npd_reason = None
        try:
            cur.execute("SELECT ot_active_shift FROM mes_lines WHERE id=%s", (line_id,))
            _otr = cur.fetchone()
            ot_active = bool(_otr and _otr.get("ot_active_shift") == base_shift)
        except Exception:
            pass
        try:
            cur.execute("""SELECT shift_name, reason FROM mes_non_production_days
                            WHERE line_id=%s AND date=%s
                              AND (shift_name IS NULL OR shift_name=%s)""",
                        (line_id, rec_date, base_shift))
            for _r in (cur.fetchall() or []):
                if _r["shift_name"] is None:
                    npd_whole = True
                elif _r["shift_name"] == base_shift:
                    npd_shift = True
                if npd_reason is None:
                    npd_reason = _r.get("reason")
        except Exception:
            pass

        # hourly breakdown with per-slot losses + operator comments — reuse the
        # Hourly Report engine so the numbers match that report exactly.
        hourly = []
        try:
            from routers.reports import (_load_shift_row, _hourly_slots_from_row,
                                         _slot_losses)
            srow  = _load_shift_row(line_id, rec_date, base_shift)
            slots = _hourly_slots_from_row(srow)
            _slot_losses(line_id, rec_date, base_shift, slots)
            for s in slots:
                sl = round(float(s.get("speed_loss") or 0) / 60.0, 1)
                bd = round(float(s.get("breakdown_loss") or 0) / 60.0, 1)
                hourly.append({
                    "time":   s["label"],
                    "plan":   s.get("plan") or 0,
                    "actual": s.get("actual") or 0,
                    "ok":     s.get("ok") or 0,
                    "ng":     s.get("ng") or 0,
                    "gap":    (s.get("plan") or 0) - (s.get("actual") or 0),
                    "speed_loss_min":     sl,
                    "breakdown_loss_min": bd,
                    "loss_min":           round(sl + bd, 1),
                    "remarks":            s.get("remarks") or "",
                })
        except HTTPException:
            hourly = []      # no dashboard row for this line/shift/date
        except Exception as _he:
            print(f"[shift-compile] hourly build failed: {_he}")
            hourly = []

        # model runs — which model ran when, for how long, and parts (OK/NG)
        try:
            from routers.reports import _model_runs
            model_runs = _model_runs(line_id, ln["db_table_name"], rec_date, base_shift)
        except Exception as _me:
            print(f"[shift-compile] model runs failed: {_me}")
            model_runs = []

        # An OT option shows ONLY that shift's OT slots; its KPIs are the sum of
        # those slots (the base-shift row carries the full-shift totals, so we
        # recompute the OT-only figures here).
        if sh_type == "ot":
            hourly = [h for h in hourly if str(h["time"]).upper().startswith("OT")]
            prod = {
                "ok_count":   sum(h["ok"]   for h in hourly),
                "ng_count":   sum(h["ng"]   for h in hourly),
                "shift_plan": sum(h["plan"] for h in hourly),
                "current_model_name": prod.get("current_model_name"),
            }

        # ── Losses summary — the shift's loss totals by category (minutes) ──
        losses = {}
        try:
            _lt = ln["db_table_name"]
            if _lt and _TABLE_RE.match(_lt):
                cur.execute(f"""SELECT loss_breakdown_seconds, loss_speed_seconds,
                                       loss_quality_seconds, loss_material_seconds,
                                       loss_setup_seconds, loss_change_over_seconds,
                                       loss_others_seconds
                                  FROM {_lt}
                                 WHERE record_date=%s AND shift_name=%s
                                 ORDER BY timestamp DESC LIMIT 1""",
                            (rec_date, base_shift))
                _lrow = cur.fetchone() or {}
                _mn = lambda k: round(float(_lrow.get(k) or 0) / 60.0, 1)
                losses = {"breakdown": _mn("loss_breakdown_seconds"),
                          "speed":     _mn("loss_speed_seconds"),
                          "quality":   _mn("loss_quality_seconds"),
                          "material":  _mn("loss_material_seconds"),
                          "setup":     _mn("loss_setup_seconds"),
                          "change_over": _mn("loss_change_over_seconds"),
                          "others":    _mn("loss_others_seconds")}
                # andon-covered line: breakdown loss = andon Maintenance+Toolroom
                # (union), matching the Dashboard OEE + the breakdowns list below.
                try:
                    from routers.andon import (andon_line_set,
                                               andon_breakdown_seconds, _norm as _an)
                    if _an(ln["line_name"]) in andon_line_set():
                        cur.execute("""SELECT start_time, end_time,
                                              COALESCE(crosses_midnight,false) AS xm
                                         FROM mes_shift_configs
                                        WHERE line_id=%s AND shift_name=%s""",
                                    (line_id, base_shift))
                        _wsc = cur.fetchone()
                        if _wsc and _wsc.get("start_time") and _wsc.get("end_time"):
                            _ws = datetime.combine(rec_date, _wsc["start_time"])
                            _we = datetime.combine(rec_date, _wsc["end_time"])
                            if _wsc["xm"] or _we <= _ws:
                                _we += timedelta(days=1)
                            losses["breakdown"] = round(
                                andon_breakdown_seconds(ln["line_name"], _ws, _we) / 60.0, 1)
                except Exception:
                    pass
                losses["total"] = round(sum(v for k, v in losses.items()
                                            if k != "total"), 1)
        except Exception as _le:
            print(f"[shift-compile] losses summary failed: {_le}")
            losses = {}

        # ── Breakdowns for this line+shift — the ANDON breakdown calls ──────
        # (maintenance_db andon_history + open andon_system, matched by line &
        # this shift's time window — the same source as the Andon page / slips.
        # The MES's own mes_breakdown_log is currently the empty leftover of the
        # cleared history, so andon is where live breakdown data actually is.)
        breakdowns = []
        try:
            from routers.andon import andon_calls_for
            cur.execute("""SELECT start_time, end_time,
                                  COALESCE(crosses_midnight,false) AS xm
                             FROM mes_shift_configs
                            WHERE line_id=%s AND shift_name=%s""",
                        (line_id, base_shift))
            _bsc = cur.fetchone()
            if _bsc and _bsc.get("start_time") and _bsc.get("end_time"):
                _bs = datetime.combine(rec_date, _bsc["start_time"])
                _be = datetime.combine(rec_date, _bsc["end_time"])
                if _bsc["xm"] or _be <= _bs:
                    _be += timedelta(days=1)
                breakdowns = andon_calls_for(ln["line_name"], _bs, _be)
        except Exception as _bx:
            print(f"[shift-compile] breakdowns (andon) failed: {_bx}")
            breakdowns = []

        # ── Alarm / NG parts for this line+shift (details + video) ──────────
        # 2026-09-01 — operator: each ALARM (NG) part's details + its cycle clip
        # must appear in Shift Compile (and later escalate on shift close).  Read
        # the NG cycles from the line's OWN ct_log; attach the line-leader remark
        # (mes_ng_remarks) so the "why" is visible.  The UI plays the clip via
        # /api/lines/{id}/cycle-video?cycle_seq=<seq>&ng=1 (the archived NG clip).
        # Read-only; counting/OEE untouched.
        alarms = []
        alarm_summary = ""
        try:
            _ctlog = (ln.get("db_table_name") or "") + "_ct_log"
            if _TABLE_RE.match(_ctlog):
                cur.execute(f"""SELECT ts, cycle_seq, part_code, ct_value
                                  FROM {_ctlog}
                                 WHERE record_date=%s AND shift_name=%s
                                   AND COALESCE(is_ng, false) = true
                                 ORDER BY ts""",
                            (rec_date, base_shift))
                _ng_rows = cur.fetchall()
                _rem = {}
                try:
                    cur.execute("""SELECT part_code, leader_remark
                                     FROM mes_ng_remarks
                                    WHERE line_id=%s AND leader_remark IS NOT NULL""",
                                (line_id,))
                    _rem = {r["part_code"]: r["leader_remark"]
                            for r in cur.fetchall() if r.get("part_code")}
                except Exception:
                    _rem = {}
                for r in _ng_rows:
                    alarms.append({
                        "cycle_seq": r.get("cycle_seq"),
                        "part_code": r.get("part_code"),
                        "time": r["ts"].strftime("%H:%M:%S") if r.get("ts") else None,
                        "ct": (float(r["ct_value"]) if r.get("ct_value") is not None else None),
                        "remark": _rem.get(r.get("part_code")),
                    })
                _n = len(alarms)
                if _n:
                    _wr = sum(1 for a in alarms if a["remark"])
                    alarm_summary = (
                        f"{_n} alarm{'s' if _n != 1 else ''} on {ln['line_name']} "
                        f"in shift {base_shift} ({rec_date.isoformat()}); "
                        f"{_wr} with remark, {_n - _wr} pending review.")
        except Exception as _ax:
            print(f"[shift-compile] alarms build failed: {_ax}")
            alarms = []

        # ── per-machine comment counts (video comments the operators added on the
        # management dashboard this shift) — mes_cycle_comments carries machine +
        # shift + record_date since 2026-06.  Read-only.
        comment_counts = []
        comment_total = 0
        try:
            cur.execute("""SELECT COALESCE(NULLIF(machine_name,''), '—') AS machine,
                                  COUNT(*) AS n
                             FROM mes_cycle_comments
                            WHERE line_id=%s AND record_date=%s AND shift_name=%s
                            GROUP BY 1
                            ORDER BY n DESC, machine""",
                        (line_id, rec_date, base_shift))
            comment_counts = [{"machine": r["machine"], "count": int(r["n"])}
                              for r in (cur.fetchall() or [])]
            comment_total = sum(c["count"] for c in comment_counts)
        except Exception as _ce:
            print(f"[shift-compile] comment counts failed: {_ce}")
            comment_counts, comment_total = [], 0

        # ── cycle-time distribution (Pareto): 2-second buckets over the shift's
        # cycles, from the line's own ct_log.  e.g. 8-10s → N, 10-12s → N …
        # Capped at 40: every cycle ≥40 s folds into the single "40+" bucket
        # (lo=40).  target_ct = the line's ideal cycle time (for the target line).
        ct_buckets = []
        target_ct = float(ln.get("ideal_cycle_time") or 15.0)
        try:
            _ctlog2 = (ln.get("db_table_name") or "") + "_ct_log"
            if _TABLE_RE.match(_ctlog2):
                cur.execute(f"""SELECT LEAST((floor(ct_value/2.0)*2)::int, 40) AS lo,
                                       COUNT(*) AS n
                                  FROM {_ctlog2}
                                 WHERE record_date=%s AND shift_name=%s
                                   AND ct_value IS NOT NULL AND ct_value > 0
                                 GROUP BY 1
                                 ORDER BY 1""",
                            (rec_date, base_shift))
                ct_buckets = [{"lo": int(r["lo"]),
                               "hi": (None if int(r["lo"]) >= 40 else int(r["lo"]) + 2),
                               "count": int(r["n"])}
                              for r in (cur.fetchall() or [])]
        except Exception as _cbe:
            print(f"[shift-compile] ct buckets failed: {_cbe}")
            ct_buckets = []

        # total parts (cycles) + how many ran over the target CT — context for the
        # comments card (how many parts, how many over-target, comments % of total)
        total_parts = sum(b["count"] for b in ct_buckets)
        over_target = 0
        try:
            _ctlog3 = (ln.get("db_table_name") or "") + "_ct_log"
            if ct_buckets and _TABLE_RE.match(_ctlog3):
                cur.execute(f"""SELECT COUNT(*) AS n
                                  FROM {_ctlog3}
                                 WHERE record_date=%s AND shift_name=%s
                                   AND ct_value IS NOT NULL AND ct_value > %s""",
                            (rec_date, base_shift, target_ct))
                over_target = int((cur.fetchone() or {}).get("n") or 0)
        except Exception as _ote:
            print(f"[shift-compile] over-target count failed: {_ote}")
            over_target = 0

        return {
            "line_id": line_id, "line_name": ln["line_name"],
            "comment_counts": comment_counts,
            "comment_total": comment_total,
            "total_parts": total_parts,
            "over_target": over_target,
            "ct_buckets": ct_buckets,
            "target_ct": target_ct,
            "hourly": hourly,
            "losses": losses,
            "breakdowns": breakdowns,
            "alarms": alarms,
            "alarm_summary": alarm_summary,
            "model_runs": model_runs,
            "record_date": rec_date.isoformat(), "shift_name": shift_name,
            "shift_type": sh_type,
            "shifts": [{"shift_name": s["shift_name"], "label": s["label"],
                        "type": s["type"]} for s in shifts],
            "scheduled_end": sched_end.isoformat() if sched_end else None,
            "production": {
                "ok": prod.get("ok_count"), "ng": prod.get("ng_count"),
                "plan": prod.get("shift_plan"),
                "oee": float(prod["overall_oee"]) if prod.get("overall_oee") is not None else None,
                "availability": float(prod["availability"]) if prod.get("availability") is not None else None,
                "performance": float(prod["performance"]) if prod.get("performance") is not None else None,
                "quality": float(prod["quality_oee"]) if prod.get("quality_oee") is not None else None,
                "status": prod.get("operating_status"),
                "model": prod.get("current_model_name"),
            },
            "manpower": manpower,
            "manpower_total": sum(len(p["operators"]) for p in manpower),
            "punched_in": punched,
            "close": {
                "closed": bool(cl),
                "closed_by": cl["closed_by"] if cl else None,
                "closed_at": cl["closed_at"].isoformat() if cl else None,
                "on_time": cl["on_time"] if cl else None,
                "note": cl["note"] if cl else None,
            },
            "base_shift": base_shift,
            "ot_active": ot_active,
            "npd": {"marked": bool(npd_whole or npd_shift), "whole_day": npd_whole,
                    "shift": npd_shift, "reason": npd_reason},
            "can_close": _can_close(cur, user, line_id),
        }


class CloseBody(BaseModel):
    line_id: int
    record_date: Optional[str] = None
    shift_name: str
    note: Optional[str] = None


@router.post("/close")
def close_shift(body: CloseBody, user=Depends(get_current_user)):
    """Line leader slides to close — writes the sign-off (counting untouched)."""
    _ensure_table()
    rec_date = _parse_date(body.record_date)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if not _can_close(cur, user, body.line_id):
            raise HTTPException(403, "You are not authorised to close this line's shift")
        _sn = body.shift_name
        if _sn.endswith("_OT"):     # OT option — scheduled end is the base shift's OT end
            cur.execute("""SELECT ot_end_time AS end_time, crosses_midnight
                             FROM mes_shift_configs WHERE line_id=%s AND shift_name=%s""",
                        (body.line_id, _sn[:-3]))
        else:
            cur.execute("""SELECT end_time, crosses_midnight FROM mes_shift_configs
                            WHERE line_id=%s AND shift_name=%s""",
                        (body.line_id, _sn))
        sh = cur.fetchone()
        if not sh:
            raise HTTPException(404, "Shift not configured for this line")
        sched_end = _scheduled_end_dt(rec_date, sh["end_time"], sh["crosses_midnight"])
        now = datetime.now()
        # 2026-09-13 — the "on-time" grace after scheduled end is configurable in
        # the Timer / Alerts module (Admin → Production).  Default 30 min.
        try:
            from routers.timer_config import get_timer_config
            _win = int(get_timer_config().get("shift_close_window_minutes", 30) or 0)
        except Exception:
            _win = 30
        on_time = (sched_end is None) or (now <= sched_end + timedelta(minutes=_win))
        cur.execute("""
            INSERT INTO mes_shift_compile
                (line_id, record_date, shift_name, closed_at, closed_by,
                 scheduled_end, on_time, note)
            VALUES (%s,%s,%s, now(), %s, %s, %s, %s)
            ON CONFLICT (line_id, record_date, shift_name) DO NOTHING
            RETURNING closed_at, closed_by, on_time""",
            (body.line_id, rec_date, body.shift_name,
             user.get("username"), sched_end, on_time, body.note))
        row = cur.fetchone()
        conn.commit()
        # 2026-09-15 — on a NEW successful close, drop a "Shift compile successful"
        # notification into the line team's Inbox (/my-escalations).
        if row:
            try:
                cur.execute("SELECT line_name FROM mes_lines WHERE id=%s", (body.line_id,))
                _lr = cur.fetchone()
                _ln = (_lr["line_name"] if _lr else None) or "Unknown line"
                from routers.push import send_to_line
                send_to_line(
                    body.line_id,
                    "Shift compile successful",
                    f"{_ln} — Shift {body.shift_name} compiled & closed by "
                    f"{user.get('username')}"
                    + ("" if on_time else " (late)") + ".",
                    url="/shift-compile", tag="shift_compile")
            except Exception as _ex:
                print("[shift-compile] inbox notify failed:", str(_ex)[:120])
        if not row:   # already closed — return existing
            cur.execute("""SELECT closed_at, closed_by, on_time FROM mes_shift_compile
                            WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                        (body.line_id, rec_date, body.shift_name))
            row = cur.fetchone()
            return {"ok": True, "already": True,
                    "closed_by": row["closed_by"],
                    "closed_at": row["closed_at"].isoformat(),
                    "on_time": row["on_time"]}
    return {"ok": True, "already": False,
            "closed_by": row["closed_by"],
            "closed_at": row["closed_at"].isoformat(),
            "on_time": row["on_time"]}


class ReopenBody(BaseModel):
    line_id: int
    record_date: Optional[str] = None
    shift_name: str


@router.post("/reopen")
def reopen_shift(body: ReopenBody, user=Depends(get_current_user)):
    """Undo a close — admins / plant heads only."""
    if user["role"] not in ("admin", "plant_head"):
        raise HTTPException(403, "Only admin / plant head can reopen a closed shift")
    _ensure_table()
    rec_date = _parse_date(body.record_date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""DELETE FROM mes_shift_compile
                        WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                    (body.line_id, rec_date, body.shift_name))
        n = cur.rowcount
        conn.commit()
    return {"ok": True, "reopened": n}


# ── OT / Non-Production-Day assign (per-line) ──────────────────────────
class OTBody(BaseModel):
    line_id: int
    shift_name: str
    enable: bool
    ot_end_time: Optional[str] = None
    ot_plan: Optional[int] = None


@router.post("/ot")
def toggle_line_ot(body: OTBody, user=Depends(get_current_user)):
    """Per-line OT assign: heads, or THIS line's leader, turn a shift's overtime
    on/off for this line only. Mirrors the zone-wide toggle but scoped to a
    single line — sets mes_shift_configs.ot_enabled + mes_lines.ot_active_shift
    (the collector's real OT switch). ok_count/ng_count are never touched."""
    base = body.shift_name[:-3] if body.shift_name.endswith("_OT") else body.shift_name
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if not _can_close(cur, user, body.line_id):
            raise HTTPException(403, "Not authorised to set OT for this line")
        cur.execute("SELECT COALESCE(is_production,TRUE) AS is_prod "
                    "FROM mes_shift_configs WHERE line_id=%s AND shift_name=%s",
                    (body.line_id, base))
        sc = cur.fetchone()
        if not sc:
            raise HTTPException(404, "Shift not configured for this line")
        if not sc["is_prod"]:
            raise HTTPException(400, "OT applies only to a production shift")
        if body.enable:
            cur.execute("""UPDATE mes_shift_configs
                              SET ot_enabled=true, ot_end_time=%s,
                                  ot_plan=COALESCE(%s, ot_plan)
                            WHERE line_id=%s AND shift_name=%s""",
                        (body.ot_end_time or None, body.ot_plan, body.line_id, base))
            cur.execute("UPDATE mes_lines SET ot_active_shift=%s, updated_at=NOW() "
                        "WHERE id=%s", (base, body.line_id))
        else:
            cur.execute("UPDATE mes_shift_configs SET ot_enabled=false "
                        "WHERE line_id=%s AND shift_name=%s", (body.line_id, base))
            cur.execute("UPDATE mes_lines SET ot_active_shift=NULL, updated_at=NOW() "
                        "WHERE id=%s AND ot_active_shift=%s", (body.line_id, base))
        conn.cursor().execute(
            """INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
               VALUES ('LINE_SHIFT_OT_TOGGLE','line',%s,%s)""",
            (body.line_id,
             f"shift={base} ot={body.enable} end={body.ot_end_time} by={user.get('username')}"))
        conn.commit()
    return {"ok": True, "enabled": body.enable, "shift": base}


class NPDBody(BaseModel):
    line_id: int
    record_date: Optional[str] = None
    shift_name: Optional[str] = None      # None = whole day
    enable: bool
    reason: Optional[str] = None


@router.post("/npd")
def toggle_line_npd(body: NPDBody, user=Depends(get_current_user)):
    """Per-line Non-Production-Day assign: heads, or THIS line's leader, mark a
    line's shift (or the whole day when shift_name is null) as non-production.
    Writes mes_non_production_days only (the plan/target side); the counting
    rows are untouched."""
    rec_date = _parse_date(body.record_date)
    sn = body.shift_name
    if sn and sn.endswith("_OT"):
        sn = sn[:-3]
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if not _can_close(cur, user, body.line_id):
            raise HTTPException(403, "Not authorised for this line")
        cur.execute("""SELECT id FROM mes_non_production_days
                        WHERE line_id=%s AND date=%s
                          AND shift_name IS NOT DISTINCT FROM %s""",
                    (body.line_id, rec_date, sn))
        existing = cur.fetchone()
        if body.enable:
            if existing:
                conn.cursor().execute(
                    """UPDATE mes_non_production_days
                          SET reason=%s, hourly_slots=NULL,
                              created_by=%s, created_at=NOW()
                        WHERE id=%s""",
                    (body.reason or "Non-production", user.get("username"), existing["id"]))
            else:
                conn.cursor().execute(
                    """INSERT INTO mes_non_production_days
                          (line_id, date, shift_name, hourly_slots, reason, created_by)
                       VALUES (%s,%s,%s,NULL,%s,%s)""",
                    (body.line_id, rec_date, sn,
                     body.reason or "Non-production", user.get("username")))
            action = "NPD_MARKED"
        else:
            if existing:
                conn.cursor().execute(
                    "DELETE FROM mes_non_production_days WHERE id=%s", (existing["id"],))
            action = "NPD_REMOVED"
        conn.cursor().execute(
            """INSERT INTO mes_audit_log (action, entity_type, entity_id, details)
               VALUES (%s,'line',%s,%s)""",
            (action, body.line_id,
             f"date={rec_date} shift={sn or 'whole day'} by={user.get('username')}"))
        conn.commit()
    return {"ok": True, "marked": body.enable, "shift": sn}


# ── tiny utils ─────────────────────────────────────────────────────────
def _parse_date(s: Optional[str]) -> date:
    if not s:
        return date.today()
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        raise HTTPException(400, "date must be YYYY-MM-DD")


def _current_shift_name(shifts) -> Optional[str]:
    """Best-effort: which production shift is 'now' by wall-clock."""
    now_t = datetime.now().time()
    for s in shifts:
        st, en = s["start_time"], s["end_time"]
        if st is None or en is None:
            continue
        if s["crosses_midnight"]:
            if now_t >= st or now_t < en:
                return s["shift_name"]
        elif st <= now_t < en:
            return s["shift_name"]
    return None


# ═══════════════════════════════════════════════════════════════════════
# COMPILED ROLL-UP  (2026-09-13)
# ───────────────────────────────────────────────────────────────────────
#   shift_incharge   → compiled totals across the lines assigned to them.
#   section_incharge → zone-wide (every line in the zones of their assigned
#                      lines) + manpower.
#   admin/plant_head → everything, with optional zone/line filter.
# Read-only, like the rest of this router — only READS the collector tables.
# ═══════════════════════════════════════════════════════════════════════
def _scope_lines_compiled(cur, user, zone_only=False, zone_id=None, line_ids=None):
    """Lines (with zone info) that feed the compiled roll-up for THIS user."""
    is_admin = user["role"] in ("admin", "plant_head")
    if is_admin:
        cur.execute("""SELECT l.id, l.line_name, l.line_code, l.db_table_name,
                              l.zone_id, z.zone_name
                         FROM mes_lines l
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE COALESCE(l.is_active,TRUE)=TRUE
                        ORDER BY l.zone_id NULLS LAST, l.line_code""")
        base = cur.fetchall() or []
    else:
        cur.execute("""SELECT l.id, l.line_name, l.line_code, l.db_table_name,
                              l.zone_id, z.zone_name
                         FROM mes_lines l
                         JOIN mes_operator_lines ol ON ol.line_id = l.id
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE ol.admin_id=%s AND COALESCE(l.is_active,TRUE)=TRUE
                        ORDER BY l.zone_id NULLS LAST, l.line_code""",
                    (user["id"],))
        base = cur.fetchall() or []
        if zone_only and base:
            zids = sorted({r["zone_id"] for r in base if r.get("zone_id")})
            if zids:
                cur.execute("""SELECT l.id, l.line_name, l.line_code,
                                      l.db_table_name, l.zone_id, z.zone_name
                                 FROM mes_lines l
                                 LEFT JOIN mes_zones z ON z.id = l.zone_id
                                WHERE l.zone_id = ANY(%s)
                                  AND COALESCE(l.is_active,TRUE)=TRUE
                                ORDER BY l.zone_id NULLS LAST, l.line_code""",
                            (zids,))
                base = cur.fetchall() or base
    if zone_id not in (None, "", "all"):
        base = [r for r in base if str(r.get("zone_id")) == str(zone_id)]
    if line_ids:
        want = {str(x).strip() for x in line_ids if str(x).strip()}
        if want:
            base = [r for r in base if str(r["id"]) in want]
    return base


def _required_manpower(cur, line_id):
    try:
        cur.execute("SAVEPOINT _rm")
        cur.execute("""SELECT COALESCE(SUM(required_manpower_count),0) AS n
                         FROM mes_processes WHERE line_id=%s""", (line_id,))
        n = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute("RELEASE SAVEPOINT _rm")
        return n
    except Exception:
        try: cur.execute("ROLLBACK TO SAVEPOINT _rm")
        except Exception: pass
        return 0


def _present_manpower(cur, line_id, rec_date, base_shift):
    try:
        cur.execute("SAVEPOINT _pm")
        cur.execute("""SELECT COUNT(DISTINCT operator_id) AS n
                         FROM mes_operator_punches
                        WHERE line_id=%s AND shift_date=%s AND shift_name=%s""",
                    (line_id, rec_date, base_shift))
        n = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute("RELEASE SAVEPOINT _pm")
        return n
    except Exception:
        try: cur.execute("ROLLBACK TO SAVEPOINT _pm")
        except Exception: pass
        return 0


def _compiled_day(cur, lines, rec_date, shift):
    """ok/ng/plan/oee/manpower + per-model totals for ONE date across `lines`.
    shift = 'A' / 'B' / 'ALL'."""
    try:
        from routers.reports import _model_runs
    except Exception:
        _model_runs = None
    per_line, model_tot = [], {}
    tot_ok = tot_ng = tot_plan = tot_present = tot_required = 0
    oee_vals, seen_lines = [], set()
    all_shifts = shift in (None, "", "ALL")
    for ln in lines:
        for sh in _all_shifts(cur, ln["id"]):
            if not all_shifts and sh["shift_name"] != shift:
                continue
            # "All shifts" = each real shift once (2026-09-19).  An OT option
            # re-reads its parent shift's row (the collector counts OT cycles
            # under A/B), so including it counted that shift twice — ~40%
            # inflated totals.  GAP windows never record a cycle (the
            # collector suppresses counting in them), so they only added
            # empty rows.  A non-production B is kept: B's past counts stay.
            if all_shifts and (sh.get("type") == "ot"
                               or str(sh["shift_name"] or "").upper().startswith("GAP")):
                continue
            base_shift = sh.get("base", sh["shift_name"])
            prod = _prod_summary(cur, ln["db_table_name"], rec_date, base_shift) or {}
            ok   = int(prod.get("ok_count") or 0)
            ng   = int(prod.get("ng_count") or 0)
            plan = int(prod.get("shift_plan") or 0)
            oee  = (float(prod["overall_oee"])
                    if prod.get("overall_oee") is not None else None)
            present  = _present_manpower(cur, ln["id"], rec_date, base_shift)
            required = _required_manpower(cur, ln["id"])
            cur.execute("""SELECT closed_at, on_time FROM mes_shift_compile
                            WHERE line_id=%s AND record_date=%s AND shift_name=%s""",
                        (ln["id"], rec_date, sh["shift_name"]))
            cl = cur.fetchone()
            models_here = []
            if _model_runs:
                try:
                    for r in _model_runs(ln["id"], ln["db_table_name"],
                                         rec_date, base_shift):
                        nm  = (r.get("model_name") or r.get("mnm") or "—")
                        tot = int(r.get("total") or r.get("tot") or 0)
                        rng = int(r.get("ng") or 0)
                        mt  = model_tot.setdefault(nm, {"ok": 0, "ng": 0})
                        mt["ok"] += max(0, tot - rng); mt["ng"] += rng
                        models_here.append({"model": nm,
                                            "ok": max(0, tot - rng), "ng": rng})
                except Exception:
                    pass
            model_label = (prod.get("current_model_name")
                           or (models_here[0]["model"] if models_here else None))
            per_line.append({
                "line_id": ln["id"], "line_name": ln["line_name"],
                "line_code": ln.get("line_code"),
                "zone_id": ln.get("zone_id"), "zone_name": ln.get("zone_name"),
                "shift_name": sh["shift_name"], "model": model_label,
                "models": models_here,
                "ok": ok, "ng": ng, "plan": plan,
                "oee": round(oee, 1) if oee is not None else None,
                "present": present, "required": required,
                "closed": bool(cl),
                "on_time": (cl["on_time"] if cl else None),
            })
            tot_ok += ok; tot_ng += ng; tot_plan += plan
            tot_present += present; tot_required += required
            if oee is not None:
                oee_vals.append(oee)
            seen_lines.add(ln["id"])
    models = [{"model": k, "ok": v["ok"], "ng": v["ng"], "total": v["ok"] + v["ng"]}
              for k, v in sorted(model_tot.items(),
                                 key=lambda kv: -(kv[1]["ok"] + kv[1]["ng"]))]
    return {"lines": per_line, "models": models,
            "totals": {"lines": len(seen_lines), "ok": tot_ok, "ng": tot_ng,
                       "plan": tot_plan, "total": tot_ok + tot_ng,
                       "oee": round(sum(oee_vals) / len(oee_vals), 1) if oee_vals else None,
                       "present": tot_present, "required": tot_required}}


@router.get("/compiled")
def compiled(date: Optional[str] = Query(None),
             shift: str = Query("A"),
             scope: Optional[str] = Query(None, description="lines|zone (auto by role)"),
             zone_id: Optional[str] = Query(None),
             line_ids: Optional[str] = Query(None, description="comma-separated line ids"),
             user=Depends(get_current_user)):
    """Compiled roll-up for one date+shift across the user's scope."""
    _ensure_table()
    rec_date = _parse_date(date)
    zone_only = (scope == "zone") or (user["role"] == "section_incharge")
    lids = [x for x in (line_ids.split(",") if line_ids else []) if x.strip()]
    with get_conn() as conn:
        cur = dict_cursor(conn)
        lines = _scope_lines_compiled(cur, user, zone_only=zone_only,
                                      zone_id=zone_id, line_ids=lids)
        data = _compiled_day(cur, lines, rec_date, shift)
    data.update({"date": rec_date.isoformat(), "shift": shift,
                 "scope": "zone" if zone_only else "lines",
                 "zones": sorted({(l.get("zone_name") or "—") for l in data["lines"]})})
    return data


def _daterange(d0, d1):
    for i in range((d1 - d0).days + 1):
        yield d0 + timedelta(days=i)


def _parse_range(date_from, date_to):
    try:
        d0 = datetime.fromisoformat(date_from).date() if date_from else date.today()
        d1 = datetime.fromisoformat(date_to).date() if date_to else d0
    except Exception:
        raise HTTPException(400, "dates must be YYYY-MM-DD")
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days > 92:
        raise HTTPException(400, "range too large (max 93 days)")
    return d0, d1


def _historical(cur, user, d0, d1, shift, zone_only, zone_id, lids):
    lines = _scope_lines_compiled(cur, user, zone_only=zone_only,
                                  zone_id=zone_id, line_ids=lids)
    days, all_rows, model_tot = [], [], {}
    g = {"ok": 0, "ng": 0, "plan": 0, "present": 0, "required": 0}
    oee_vals = []
    for d in _daterange(d0, d1):
        day = _compiled_day(cur, lines, d, shift)
        for r in day["lines"]:
            r2 = dict(r); r2["date"] = d.isoformat(); all_rows.append(r2)
        for m in day["models"]:
            mt = model_tot.setdefault(m["model"], {"ok": 0, "ng": 0})
            mt["ok"] += m["ok"]; mt["ng"] += m["ng"]
        t = day["totals"]
        g["ok"] += t["ok"]; g["ng"] += t["ng"]; g["plan"] += t["plan"]
        g["present"] += t["present"]; g["required"] += t["required"]
        if t["oee"] is not None:
            oee_vals.append(t["oee"])
        days.append({"date": d.isoformat(), **t})
    g["oee"] = round(sum(oee_vals) / len(oee_vals), 1) if oee_vals else None
    g["total"] = g["ok"] + g["ng"]
    models = [{"model": k, "ok": v["ok"], "ng": v["ng"], "total": v["ok"] + v["ng"]}
              for k, v in sorted(model_tot.items(),
                                 key=lambda kv: -(kv[1]["ok"] + kv[1]["ng"]))]
    return {"rows": all_rows, "days": days, "models": models, "totals": g,
            "lines_count": len(lines)}


@router.get("/historical")
def historical(date_from: Optional[str] = Query(None),
               date_to: Optional[str] = Query(None),
               shift: str = Query("ALL"),
               scope: Optional[str] = Query(None),
               zone_id: Optional[str] = Query(None),
               line_ids: Optional[str] = Query(None),
               user=Depends(get_current_user)):
    """Compiled roll-up across a date range (date-to-date + shift filter)."""
    _ensure_table()
    d0, d1 = _parse_range(date_from, date_to)
    zone_only = (scope == "zone") or (user["role"] == "section_incharge")
    lids = [x for x in (line_ids.split(",") if line_ids else []) if x.strip()]
    with get_conn() as conn:
        cur = dict_cursor(conn)
        out = _historical(cur, user, d0, d1, shift, zone_only, zone_id, lids)
    out.update({"date_from": d0.isoformat(), "date_to": d1.isoformat(),
                "shift": shift, "scope": "zone" if zone_only else "lines"})
    return out


# ═══════════════════════════════════════════════════════════════════════
# ZONE SUMMARY  (2026-09-19)
# ───────────────────────────────────────────────────────────────────────
# Operator: per zone — how many lines ran and how many stood, the zone's OEE
# and its major losses.  A line is RUNNING when it was in the plan
# (shift_plan > 0) and actual parts were made; STOPPED when it was planned but
# made nothing; NOT PLANNED when it had no plan.  Read-only — one dashboard
# row per line/date/shift (indexed) + one maintenance_db query for the whole
# range, so a month costs about as much as a day.
# ═══════════════════════════════════════════════════════════════════════
_ZONE_LOSSES = (("breakdown", "Breakdown", "loss_breakdown_seconds"),
                ("speed", "Speed", "loss_speed_seconds"),
                ("setup", "Setup", "loss_setup_seconds"),
                ("change_over", "Change-over", "loss_change_over_seconds"),
                ("material", "Material", "loss_material_seconds"),
                ("quality", "Quality", "loss_quality_seconds"),
                ("others", "Others", "loss_others_seconds"))
# A planned shift that made nothing records no loss at all in the collector
# row, which would hide the biggest loss of the day — so it is charged its
# planned working minutes (pro-rated while that shift is still running).
_NOT_RUN = ("not_run", "Planned, not run")
_ZONE_LOSS_LABEL = dict([(k, lab) for k, lab, _c in _ZONE_LOSSES] + [_NOT_RUN])


def _andon_intervals_by_line(norm_names, start_dt, end_dt):
    """{normalised line: [(start, end), …]} Maintenance+Toolroom andon calls
    overlapping [start_dt, end_dt] — the same sources as
    andon.andon_breakdown_intervals (completed andon_history + the OPEN
    andon_system call), fetched in ONE query for the whole range; clipping to
    each shift is done by _union_seconds.  Returns None if maintenance_db can't
    be read, so callers keep the collector's own breakdown instead."""
    if not norm_names:
        return {}
    try:
        from routers.andon import _maint_conn, BREAKDOWN_CALL_TYPES
        norm_sql = "UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g'))"
        out = {}
        with _maint_conn() as mconn:
            mcur = mconn.cursor()
            mcur.execute(f"""
                SELECT {norm_sql}, started_at, COALESCE(ended_at, LOCALTIMESTAMP)
                  FROM andon_history
                 WHERE display_name = ANY(%s) AND {norm_sql} = ANY(%s)
                   AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                UNION ALL
                SELECT {norm_sql}, started_at, LOCALTIMESTAMP
                  FROM andon_system
                 WHERE state = 'OPEN' AND display_name = ANY(%s)
                   AND {norm_sql} = ANY(%s) AND started_at < %s""",
                (BREAKDOWN_CALL_TYPES, list(norm_names), end_dt, start_dt,
                 BREAKDOWN_CALL_TYPES, list(norm_names), end_dt))
            for n, a, b in mcur.fetchall():
                if a and b and b > a:
                    out.setdefault(n, []).append((a, b))
        return out
    except Exception as exc:
        print(f"[shift-compile] zone summary andon read failed: {exc}")
        return None


def _union_seconds(intervals, ws, we):
    """Seconds covered by the union of `intervals` clipped to [ws, we] —
    overlapping calls (Maintenance + Toolroom on the same stop) count once,
    exactly as andon.andon_breakdown_intervals merges them."""
    segs = sorted((max(a, ws), min(b, we)) for a, b in intervals if a < we and b > ws)
    total, cs, ce = 0.0, None, None
    for a, b in segs:
        if b <= a:
            continue
        if ce is not None and a <= ce:
            ce = max(ce, b)
        else:
            if ce is not None:
                total += (ce - cs).total_seconds()
            cs, ce = a, b
    if ce is not None:
        total += (ce - cs).total_seconds()
    return total


def _zone_summary(cur, user, d0, d1, shift, zone_only, zone_id, lids):
    lines = _scope_lines_compiled(cur, user, zone_only=zone_only,
                                  zone_id=zone_id, line_ids=lids)
    all_shifts = shift in (None, "", "ALL")
    now = datetime.now()
    ids = [ln["id"] for ln in lines]

    # shift windows — every non-GAP shift for "All" (the same set /historical
    # uses), or just the one asked for
    cfg = {}
    if ids:
        cur.execute("""SELECT line_id, shift_name, start_time, end_time,
                              COALESCE(crosses_midnight, FALSE) AS xm,
                              COALESCE(working_minutes, 0) AS wm
                         FROM mes_shift_configs WHERE line_id = ANY(%s)""", (ids,))
        for r in cur.fetchall():
            nm = str(r["shift_name"] or "")
            if nm.upper().startswith("GAP") or (not all_shifts and nm != shift):
                continue
            cfg.setdefault(r["line_id"], {})[nm] = r

    # Non-Production Days in range — (line, date, None) for a whole day,
    # (line, date, shift) for one shift.  The collector still writes the
    # configured plan into those rows (17-Sep "holiday": plan 1860, made 0), so
    # an NPD shift is treated as NOT planned here, never as a stopped line.
    npd = set()
    if ids:
        try:
            cur.execute("SAVEPOINT _zs_npd")
            cur.execute("""SELECT line_id, date, shift_name FROM mes_non_production_days
                            WHERE line_id = ANY(%s) AND date BETWEEN %s AND %s""",
                        (ids, d0, d1))
            npd = {(r["line_id"], r["date"], r["shift_name"] or None) for r in cur.fetchall()}
            cur.execute("RELEASE SAVEPOINT _zs_npd")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _zs_npd")

    # andon breakdown for covered lines — one read for the whole range
    try:
        from routers.andon import andon_line_set, _norm as _an
        covered = andon_line_set()
    except Exception:
        covered, _an = set(), (lambda s: s)
    want = {_an(ln["line_name"]) for ln in lines} & set(covered)
    andon = _andon_intervals_by_line(
        want, datetime.combine(d0, _time.min), datetime.combine(d1 + timedelta(days=2), _time.min))

    zones, order = {}, []
    for ln in lines:
        zname = ln.get("zone_name") or "—"
        if zname not in zones:
            order.append(zname)
            zones[zname] = {"zone_name": zname, "zone_id": ln.get("zone_id"), "lines": []}
        L = {"line_id": ln["id"], "line_name": ln["line_name"], "status": "not_planned",
             "plan": 0, "actual": 0, "ok": 0, "ng": 0, "planned_shifts": 0,
             "ran_shifts": 0, "oee": None, "losses": {}, "live_status": None,
             "npd_shifts": 0, "note": None}
        zones[zname]["lines"].append(L)
        tbl = ln.get("db_table_name")
        shifts = cfg.get(ln["id"], {})
        if not tbl or not _TABLE_RE.match(tbl):
            L["note"] = "no data table"; continue
        cur.execute("SELECT to_regclass(%s) AS t", (tbl,))
        if not (cur.fetchone() or {}).get("t"):
            L["note"] = "no data table"; continue
        if not shifts:
            continue
        try:
            cur.execute("SAVEPOINT _zs_row")
            cur.execute(f"""SELECT DISTINCT ON (record_date, shift_name)
                                   record_date, shift_name, ok_count, ng_count, shift_plan,
                                   overall_oee, operating_status,
                                   {", ".join(c for _k, _l, c in _ZONE_LOSSES)}
                              FROM {tbl}
                             WHERE record_date BETWEEN %s AND %s AND shift_name = ANY(%s)
                             ORDER BY record_date, shift_name, timestamp DESC""",
                        (d0, d1, list(shifts)))
            rows = cur.fetchall()
            cur.execute("RELEASE SAVEPOINT _zs_row")
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT _zs_row")
            print(f"[shift-compile] zone summary {tbl}: {exc}")
            L["note"] = "data unreadable"; continue
        # None = keep the collector's breakdown (not andon-covered, or
        # maintenance_db unreadable); [] = covered with no calls = 0 breakdown
        ivs = (andon.get(_an(ln["line_name"]), [])
               if andon is not None and _an(ln["line_name"]) in covered else None)
        oees, loss = [], {}
        for r in rows:
            sc = shifts.get(r["shift_name"])
            ws = datetime.combine(r["record_date"], sc["start_time"])
            we = datetime.combine(r["record_date"], sc["end_time"])
            if sc["xm"] or we <= ws:
                we += timedelta(days=1)
            if now <= ws:
                continue                      # shift not started yet
            made = int(r["ok_count"] or 0) + int(r["ng_count"] or 0)
            is_npd = ((ln["id"], r["record_date"], None) in npd
                      or (ln["id"], r["record_date"], r["shift_name"]) in npd)
            plan = 0 if is_npd else int(r["shift_plan"] or 0)
            if is_npd:
                L["npd_shifts"] += 1
            L["plan"] += plan; L["actual"] += made
            L["ok"] += int(r["ok_count"] or 0); L["ng"] += int(r["ng_count"] or 0)
            L["live_status"] = r["operating_status"] or L["live_status"]
            if plan > 0:
                L["planned_shifts"] += 1
            if made > 0:
                L["ran_shifts"] += 1
                if r["overall_oee"] is not None:
                    oees.append(float(r["overall_oee"]))
                for k, _lab, col in _ZONE_LOSSES:
                    loss[k] = loss.get(k, 0.0) + float(r[col] or 0)
                if ivs is not None:
                    # andon-covered: breakdown = andon Maintenance+Toolroom,
                    # as on the Shift Compile detail and the Dashboard
                    loss["breakdown"] = (loss.get("breakdown", 0.0)
                                         - float(r["loss_breakdown_seconds"] or 0)
                                         + _union_seconds(ivs, ws, min(we, now)))
            elif plan > 0:
                frac = min(1.0, max(0.0, (now - ws).total_seconds()
                                    / max(1.0, (we - ws).total_seconds())))
                loss["not_run"] = loss.get("not_run", 0.0) + float(sc["wm"] or 0) * 60 * frac
        L["status"] = ("running" if L["actual"] > 0 else
                       "stopped" if L["plan"] > 0 else "not_planned")
        L["oee"] = round(sum(oees) / len(oees), 1) if oees else None
        L["_oees"] = oees
        L["losses"] = {k: round(v / 60.0, 1) for k, v in loss.items() if v > 0}

    out = []
    for zname in order:
        z = zones[zname]; ls = z["lines"]
        oees = [o for l in ls for o in l.pop("_oees", [])]
        loss = {}
        for l in ls:
            for k, v in l["losses"].items():
                loss[k] = round(loss.get(k, 0.0) + v, 1)
        top = sorted(((k, v) for k, v in loss.items() if v > 0), key=lambda kv: -kv[1])
        running = [l for l in ls if l["status"] == "running"]
        z.update({
            "lines_total": len(ls),
            "running": len(running),
            "stopped": sum(1 for l in ls if l["status"] == "stopped"),
            "not_planned": sum(1 for l in ls if l["status"] == "not_planned"),
            "plan": sum(l["plan"] for l in ls), "actual": sum(l["actual"] for l in ls),
            "ok": sum(l["ok"] for l in ls), "ng": sum(l["ng"] for l in ls),
            "oee": round(sum(oees) / len(oees), 1) if oees else None,
            "losses": loss,
            "loss_total": round(sum(loss.values()), 1),
            "top_losses": [{"key": k, "label": _ZONE_LOSS_LABEL.get(k, k), "minutes": v}
                           for k, v in top[:3]],
            # ran, yet not one loss minute recorded — the collector isn't
            # measuring losses on these lines, so their OEE reads ~100%
            "no_loss_data": bool(running) and not any(
                v > 0 for l in running for k, v in l["losses"].items() if k != "not_run"),
        })
        z["planned"] = z["running"] + z["stopped"]
        out.append(z)
    return {"zones": out,
            "totals": {"lines_total": sum(z["lines_total"] for z in out),
                       "running": sum(z["running"] for z in out),
                       "stopped": sum(z["stopped"] for z in out),
                       "not_planned": sum(z["not_planned"] for z in out)},
            "andon_ok": andon is not None,
            "loss_labels": _ZONE_LOSS_LABEL}


_ZONE_STATUS = {"running": "Running", "stopped": "Stopped", "not_planned": "Not planned"}


def _zone_ach(z):
    return round(z["actual"] * 100.0 / z["plan"], 1) if z.get("plan") else None


def _zone_loss_text(z):
    if z.get("top_losses"):
        return ", ".join(f'{t["label"]} {t["minutes"]:g}' for t in z["top_losses"])
    return "No loss data" if z.get("no_loss_data") else "-"


def _line_top_loss(l):
    if not l.get("losses"):
        return "-"
    k, v = max(l["losses"].items(), key=lambda kv: kv[1])
    return f"{_ZONE_LOSS_LABEL.get(k, k)} {v:g} min"


def _line_note(l):
    bits = []
    if l.get("note"):
        bits.append(l["note"])
    if l.get("npd_shifts"):
        bits.append(f'Non-Production Day ({l["npd_shifts"]} shift{"s" if l["npd_shifts"] > 1 else ""})')
    return "; ".join(bits)


@router.get("/zone-summary")
def zone_summary(date_from: Optional[str] = Query(None),
                 date_to: Optional[str] = Query(None),
                 shift: str = Query("ALL"),
                 scope: Optional[str] = Query(None),
                 zone_id: Optional[str] = Query(None),
                 line_ids: Optional[str] = Query(None),
                 user=Depends(get_current_user)):
    """Per zone: lines running / stopped / not planned, zone OEE, top losses."""
    d0, d1 = _parse_range(date_from, date_to)
    zone_only = (scope == "zone") or (user["role"] == "section_incharge")
    lids = [x for x in (line_ids.split(",") if line_ids else []) if x.strip()]
    with get_conn() as conn:
        cur = dict_cursor(conn)
        out = _zone_summary(cur, user, d0, d1, shift, zone_only, zone_id, lids)
    out.update({"date_from": d0.isoformat(), "date_to": d1.isoformat(), "shift": shift})
    return out


@router.get("/compiled-export")
def compiled_export(format: str = Query("xlsx", description="xlsx|pdf"),
                    date_from: Optional[str] = Query(None),
                    date_to: Optional[str] = Query(None),
                    shift: str = Query("ALL"),
                    scope: Optional[str] = Query(None),
                    zone_id: Optional[str] = Query(None),
                    line_ids: Optional[str] = Query(None),
                    user=Depends(get_current_user)):
    """Download the compiled roll-up (date range) as .xlsx or .pdf."""
    _ensure_table()
    d0, d1 = _parse_range(date_from, date_to)
    zone_only = (scope == "zone") or (user["role"] == "section_incharge")
    lids = [x for x in (line_ids.split(",") if line_ids else []) if x.strip()]
    with get_conn() as conn:
        cur = dict_cursor(conn)
        data = _historical(cur, user, d0, d1, shift, zone_only, zone_id, lids)
        try:       # the zone page must never cost the whole download
            cur.execute("SAVEPOINT _zs_exp")
            data["zone_summary"] = _zone_summary(cur, user, d0, d1, shift,
                                                 zone_only, zone_id, lids)
            cur.execute("RELEASE SAVEPOINT _zs_exp")
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT _zs_exp")
            print(f"[shift-compile] export zone summary failed: {exc}")
    meta = {"date_from": d0.isoformat(), "date_to": d1.isoformat(),
            "shift": shift, "scope": "zone" if zone_only else "lines"}
    tag = f"{d0.isoformat()}_{d1.isoformat()}_{shift}"
    if (format or "").lower() == "pdf":
        blob = _build_compiled_pdf(data, meta)
        return StreamingResponse(io.BytesIO(blob), media_type="application/pdf",
            headers={"Content-Disposition":
                     f'attachment; filename="shift_compiled_{tag}.pdf"'})
    blob = _build_compiled_xlsx(data, meta)
    return StreamingResponse(io.BytesIO(blob),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="shift_compiled_{tag}.xlsx"'})


def _build_compiled_xlsx(data, meta) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    hfill = PatternFill("solid", fgColor="1E40AF"); hfont = Font(bold=True, color="FFFFFF")
    bold = Font(bold=True)
    thin = Side(style="thin", color="D6DEEA")
    bdr = Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr = Alignment(horizontal="center", vertical="center")

    def hrow(ws, ncols, rowno=1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=rowno, column=c)
            cell.fill = hfill; cell.font = hfont; cell.alignment = ctr; cell.border = bdr

    ws = wb.active; ws.title = "Summary"
    ws["A1"] = "Shift Compile — Compiled Report"; ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"{meta['date_from']} to {meta['date_to']}    Shift: {meta['shift']}"
                f"    Scope: {meta['scope']}")
    t = data["totals"]
    r = 4
    for k, v in [("Lines", data.get("lines_count")), ("Total OK", t["ok"]),
                 ("Total NG", t["ng"]), ("Total Produced", t["total"]),
                 ("Total Plan", t["plan"]), ("Avg OEE %", t["oee"]),
                 ("Manpower Present", t["present"]), ("Manpower Required", t["required"])]:
        ws.cell(row=r, column=1, value=k).font = bold
        ws.cell(row=r, column=2, value=v); r += 1
    r += 1
    ws.cell(row=r, column=1, value="Per-Model Totals").font = Font(bold=True, size=12); r += 1
    for i, c in enumerate(["Model", "OK", "NG", "Total"], 1):
        ws.cell(row=r, column=i, value=c)
    hrow(ws, 4, r); r += 1
    for m in data["models"]:
        ws.cell(row=r, column=1, value=m["model"]); ws.cell(row=r, column=2, value=m["ok"])
        ws.cell(row=r, column=3, value=m["ng"]); ws.cell(row=r, column=4, value=m["total"]); r += 1
    for col, w in {"A": 26, "B": 14, "C": 12, "D": 12}.items():
        ws.column_dimensions[col].width = w

    zs = data.get("zone_summary")
    if zs:
        wz = wb.create_sheet("Zones")
        zcols = ["Zone", "Lines", "Planned", "Running", "Stopped", "Not planned",
                 "Plan", "Actual", "Ach %", "OEE %", "Major losses (min)", "Total loss (min)"]
        for i, c in enumerate(zcols, 1):
            wz.cell(row=1, column=i, value=c)
        hrow(wz, len(zcols))
        rr = 2
        for z in zs["zones"]:
            vals = [z["zone_name"], z["lines_total"], z["planned"], z["running"],
                    z["stopped"], z["not_planned"], z["plan"], z["actual"],
                    _zone_ach(z), z["oee"], _zone_loss_text(z), z["loss_total"]]
            for i, v in enumerate(vals, 1):
                wz.cell(row=rr, column=i, value=v).border = bdr
            rr += 1
        rr += 1
        wz.cell(row=rr, column=1, value="Lines by zone").font = Font(bold=True, size=12); rr += 1
        lcols = ["Zone", "Line", "Status", "Plan", "Actual", "OEE %",
                 "Shifts ran / planned", "Top loss", "Live status", "Note"]
        for i, c in enumerate(lcols, 1):
            wz.cell(row=rr, column=i, value=c)
        hrow(wz, len(lcols), rr); rr += 1
        for z in zs["zones"]:
            for l in z["lines"]:
                vals = [z["zone_name"], l["line_name"], _ZONE_STATUS.get(l["status"], l["status"]),
                        l["plan"], l["actual"], l["oee"],
                        f'{l["ran_shifts"]} / {l["planned_shifts"]}', _line_top_loss(l),
                        l.get("live_status"), _line_note(l)]
                for i, v in enumerate(vals, 1):
                    wz.cell(row=rr, column=i, value=v).border = bdr
                rr += 1
        for i, w in enumerate([16, 22, 11, 10, 10, 11, 10, 10, 8, 8, 44, 14], 1):
            wz.column_dimensions[get_column_letter(i)].width = w

    ws2 = wb.create_sheet("Details")
    cols = ["Date", "Zone", "Line", "Shift", "Model", "OK", "NG", "Plan",
            "OEE %", "Present", "Required", "Closed", "On-Time"]
    for i, c in enumerate(cols, 1):
        ws2.cell(row=1, column=i, value=c)
    hrow(ws2, len(cols))
    rr = 2
    for row in data["rows"]:
        ontime = ("Yes" if row.get("on_time") else
                  ("No" if row.get("on_time") is not None else "—"))
        vals = [row.get("date"), row.get("zone_name"), row.get("line_name"),
                row.get("shift_name"), row.get("model"), row.get("ok"), row.get("ng"),
                row.get("plan"), row.get("oee"), row.get("present"), row.get("required"),
                "Yes" if row.get("closed") else "No", ontime]
        for i, v in enumerate(vals, 1):
            ws2.cell(row=rr, column=i, value=v)
        rr += 1
    for i, w in enumerate([12, 16, 22, 7, 18, 9, 9, 9, 8, 9, 9, 8, 9], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    ws3 = wb.create_sheet("Daily")
    dcols = ["Date", "Lines", "OK", "NG", "Total", "Plan", "OEE %", "Present", "Required"]
    for i, c in enumerate(dcols, 1):
        ws3.cell(row=1, column=i, value=c)
    hrow(ws3, len(dcols))
    rr = 2
    for d in data["days"]:
        vals = [d["date"], d["lines"], d["ok"], d["ng"], d["total"], d["plan"],
                d["oee"], d["present"], d["required"]]
        for i, v in enumerate(vals, 1):
            ws3.cell(row=rr, column=i, value=v)
        rr += 1
    for i, w in enumerate([12, 8, 10, 10, 10, 10, 8, 9, 9], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _build_compiled_pdf(data, meta) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    buf = io.BytesIO()
    t = data["totals"]
    with PdfPages(buf) as pdf:
        # Page 1 — summary + per-model totals
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.suptitle("Shift Compile — Compiled Report", fontsize=16, fontweight="bold")
        plt.figtext(0.5, 0.93,
                    f"{meta['date_from']} to {meta['date_to']}   Shift: {meta['shift']}"
                    f"   Scope: {meta['scope']}", ha="center", fontsize=10)
        ax = fig.add_axes([0.06, 0.55, 0.88, 0.32]); ax.axis("off")
        summ = [["Lines", data.get("lines_count")], ["Total OK", t["ok"]],
                ["Total NG", t["ng"]], ["Total Produced", t["total"]],
                ["Total Plan", t["plan"]], ["Avg OEE %", t["oee"]],
                ["Manpower Present", t["present"]], ["Manpower Required", t["required"]]]
        tb = ax.table(cellText=[[k, str(v)] for k, v in summ],
                      colLabels=["Metric", "Value"], loc="center", cellLoc="left")
        tb.auto_set_font_size(False); tb.set_fontsize(9); tb.scale(1, 1.4)
        ax2 = fig.add_axes([0.06, 0.06, 0.88, 0.40]); ax2.axis("off")
        ax2.set_title("Per-Model Totals", fontsize=11, fontweight="bold", loc="left")
        mrows = [[m["model"], m["ok"], m["ng"], m["total"]] for m in data["models"][:22]]
        if mrows:
            tb2 = ax2.table(cellText=mrows, colLabels=["Model", "OK", "NG", "Total"],
                            loc="upper center", cellLoc="center")
            tb2.auto_set_font_size(False); tb2.set_fontsize(9); tb2.scale(1, 1.4)
        pdf.savefig(fig); plt.close(fig)
        # Zone summary page — running / stopped per zone, OEE, major losses
        zs = data.get("zone_summary")
        if zs and zs.get("zones"):
            fig = plt.figure(figsize=(11.69, 8.27))
            fig.suptitle("Zone Summary", fontsize=14, fontweight="bold")
            plt.figtext(0.5, 0.93, f"{meta['date_from']} to {meta['date_to']}   Shift: {meta['shift']}"
                        "   Running = planned + actual made   Stopped = planned, nothing made",
                        ha="center", fontsize=9)
            ax = fig.add_axes([0.03, 0.52, 0.94, 0.37]); ax.axis("off")
            zrows = [[z["zone_name"][:14], z["lines_total"], z["running"], z["stopped"],
                      z["not_planned"], z["plan"], z["actual"],
                      "-" if _zone_ach(z) is None else f"{_zone_ach(z)}%",
                      "-" if z["oee"] is None else f"{z['oee']}%",
                      _zone_loss_text(z)[:60]] for z in zs["zones"]]
            tb = ax.table(cellText=zrows, colLabels=["Zone", "Lines", "Running", "Stopped",
                          "Not pl.", "Plan", "Actual", "Ach", "OEE", "Major losses (min)"],
                          loc="upper center", cellLoc="center",
                          colWidths=[.11, .05, .06, .06, .06, .07, .07, .05, .05, .42])
            tb.auto_set_font_size(False); tb.set_fontsize(8); tb.scale(1, 1.5)
            idle = [[z["zone_name"][:14], l["line_name"][:24],
                     _ZONE_STATUS.get(l["status"], l["status"]), l["plan"],
                     str(l.get("live_status") or "-"), _line_note(l)[:30]]
                    for z in zs["zones"] for l in z["lines"] if l["status"] != "running"][:18]
            if idle:
                ax2 = fig.add_axes([0.03, 0.03, 0.94, 0.44]); ax2.axis("off")
                ax2.set_title("Lines that did not run", fontsize=11, fontweight="bold", loc="left")
                tb2 = ax2.table(cellText=idle, colLabels=["Zone", "Line", "Status", "Plan",
                                "Live status", "Note"], loc="upper center", cellLoc="center")
                tb2.auto_set_font_size(False); tb2.set_fontsize(8); tb2.scale(1, 1.3)
            pdf.savefig(fig); plt.close(fig)
        # Pages 2+ — per line/day/shift details, paginated
        rows = data["rows"]; per = 26
        cols = ["Date", "Zone", "Line", "Sh", "Model", "OK", "NG", "Plan",
                "OEE", "Pres", "Reqd", "Cl"]
        for pg in range(0, max(1, len(rows)), per):
            chunk = rows[pg:pg + per]
            if not chunk:
                break
            fig = plt.figure(figsize=(11.69, 8.27))
            ax = fig.add_axes([0.02, 0.03, 0.96, 0.92]); ax.axis("off")
            ax.set_title("Line Details", fontsize=12, fontweight="bold", loc="left")
            cell = [[r.get("date"), (r.get("zone_name") or "")[:10],
                     (r.get("line_name") or "")[:16], r.get("shift_name"),
                     (str(r.get("model") or ""))[:14], r.get("ok"), r.get("ng"),
                     r.get("plan"),
                     (r.get("oee") if r.get("oee") is not None else "-"),
                     r.get("present"), r.get("required"),
                     "Y" if r.get("closed") else "N"] for r in chunk]
            tb = ax.table(cellText=cell, colLabels=cols, loc="upper center",
                          cellLoc="center")
            tb.auto_set_font_size(False); tb.set_fontsize(7); tb.scale(1, 1.3)
            pdf.savefig(fig); plt.close(fig)
    return buf.getvalue()
