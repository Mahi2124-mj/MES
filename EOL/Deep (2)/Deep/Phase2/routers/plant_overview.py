"""
routers/plant_overview.py — Plant Overview for HOD and Plant Head, 2026-10-08.

Operator: "mere hod and plant head level ki today information ke according and
last working day ki details live rhe … py bypass include."  Approved one-pager:
HOD = production_incharge, Sunday off (now: the Annual Calendar decides).

ONE read-only call builds the whole page:
  today  — the production day running now (a B shift after midnight still
           belongs to the day it started), refreshed by the page every 60 s;
  lwd    — the last working day: walk back from yesterday, skip days the
           Annual Calendar marks non-working, take the first with real output
           (OK + NG > 0 on any line — collectors write the plan even on
           holidays, so plan alone proves nothing);
  prev   — the working day before that, only for the up/down numbers.

Numbers come from the SAME code Shift Compile uses (`_zone_summary`), so the
two pages always agree.  Everything else is plain SELECTs: andon
(maintenance_db), PY bypass cases, PY sensor faults (maintenance_db), quality
deviations, shift close records, open escalations.

SAFETY: the energydb transaction is SET READ ONLY; nothing is written anywhere.
Scope: admin / plant_head see every line; production_incharge (HOD) and
section_incharge see every line in the zones of their assigned lines; anyone
else only their assigned lines (floor roles with none see nothing).
"""
from datetime import date, datetime, timedelta, time as _time
import re
import threading
import time

from fastapi import APIRouter, Depends

from database import get_conn, dict_cursor
from auth import get_current_user

router = APIRouter(prefix="/api/plant-overview", tags=["plant-overview"])

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ZONE_WIDE_ROLES = {"production_incharge", "section_incharge"}
TODAY_TTL = 30            # s — ten open screens cost the DB the same as one
CLOSED_TTL = 900          # s — last working day hardly changes (late shift closes)
LWD_LOOKBACK = 14         # days
BREAKDOWN_TYPES = ("Maintenance", "Toolroom")

_cache = {}
_cache_lock = threading.Lock()


def _cached(key, ttl, build):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = build()
    with _cache_lock:
        _cache[key] = (time.time(), val)
        if len(_cache) > 200:                      # many users → drop the oldest
            for k, _v in sorted(_cache.items(), key=lambda kv: kv[1][0])[:50]:
                _cache.pop(k, None)
    return val


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ── scope + calendar ──────────────────────────────────────────────────────
def _scope(cur, user):
    from routers.shift_compile import _scope_lines_compiled
    zone_wide = (user.get("role") or "") in ZONE_WIDE_ROLES
    lines = _scope_lines_compiled(cur, user, zone_only=zone_wide)
    return lines, zone_wide


def _day_start(cur, line_ids):
    """Start of the production day = the earliest production-shift start."""
    if line_ids:
        cur.execute("""SELECT MIN(start_time) AS t FROM mes_shift_configs
                        WHERE line_id = ANY(%s) AND COALESCE(is_production, TRUE)
                          AND UPPER(shift_name) NOT LIKE 'GAP%%'""", (line_ids,))
        t = (cur.fetchone() or {}).get("t")
        if t:
            return t
    return _time(8, 30)


def _production_day(cur, line_ids):
    now = datetime.now()
    start = _day_start(cur, line_ids)
    return (now.date() if now.time() >= start else now.date() - timedelta(days=1)), start


def _had_output(cur, lines, d):
    for ln in lines:
        tbl = ln.get("db_table_name")
        if not tbl or not _TABLE_RE.match(tbl):
            continue
        try:
            cur.execute("SAVEPOINT _po_out")
            cur.execute(f"""SELECT 1 FROM {tbl}
                             WHERE record_date = %s AND UPPER(shift_name) NOT LIKE 'GAP%%'
                               AND COALESCE(ok_count,0) + COALESCE(ng_count,0) > 0
                             LIMIT 1""", (d,))
            hit = cur.fetchone() is not None
            cur.execute("RELEASE SAVEPOINT _po_out")
            if hit:
                return True
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _po_out")
    return False


def _working_day_before(cur, lines, d):
    """Latest working day strictly before d with real output, else the latest
    calendar working day, else None."""
    from routers.working_calendar import is_working
    first_working = None
    for i in range(1, LWD_LOOKBACK + 1):
        c = d - timedelta(days=i)
        if not is_working(cur, c):
            continue
        first_working = first_working or c
        if _had_output(cur, lines, c):
            return c
    return first_working


# ── per-day blocks ────────────────────────────────────────────────────────
def _production(cur, user, d, zone_wide, shift="ALL"):
    from routers.shift_compile import _zone_summary
    zs = _zone_summary(cur, user, d, d, shift, zone_wide, None, [])
    zones, loss, oees = [], {}, []
    for z in zs["zones"]:
        for k, v in (z.get("losses") or {}).items():
            loss[k] = round(loss.get(k, 0.0) + v, 1)
        lines = []
        for l in z["lines"]:
            if l.get("oee") is not None and l.get("actual"):
                oees.append(l["oee"])
            top = max((l.get("losses") or {}).items(), key=lambda kv: kv[1], default=None)
            lines.append({"line_id": l["line_id"], "line_name": l["line_name"],
                          "status": l["status"], "live_status": l.get("live_status"),
                          "plan": l["plan"], "actual": l["actual"], "ok": l["ok"], "ng": l["ng"],
                          "oee": l.get("oee"), "losses": l.get("losses") or {},
                          "top_loss": {"key": top[0], "minutes": top[1]} if top else None,
                          "note": l.get("note"), "npd_shifts": l.get("npd_shifts", 0)})
        zones.append({"zone_id": z.get("zone_id"), "zone_name": z["zone_name"],
                      "lines_total": z["lines_total"], "running": z["running"],
                      "stopped": z["stopped"], "not_planned": z["not_planned"],
                      "plan": z["plan"], "actual": z["actual"], "ok": z["ok"], "ng": z["ng"],
                      "oee": z.get("oee"), "losses": z.get("losses") or {},
                      "loss_total": z.get("loss_total", 0), "top_losses": z.get("top_losses", []),
                      "no_loss_data": z.get("no_loss_data", False), "lines": lines})
    plan = sum(z["plan"] for z in zones)
    actual = sum(z["actual"] for z in zones)
    totals = {"plan": plan, "actual": actual,
              "ok": sum(z["ok"] for z in zones), "ng": sum(z["ng"] for z in zones),
              "pct": round(actual * 100.0 / plan, 1) if plan else None,
              "oee": round(sum(oees) / len(oees), 1) if oees else None,
              "loss_minutes": round(sum(loss.values()), 1)}
    totals.update(zs.get("totals", {}))
    return {"date": d.isoformat(), "totals": totals, "zones": zones, "losses": loss,
            "loss_labels": zs.get("loss_labels", {}), "andon_ok": zs.get("andon_ok", True)}


def _shift_names(cur, line_ids):
    if not line_ids:
        return []
    cur.execute("""SELECT shift_name, MIN(start_time) AS st FROM mes_shift_configs
                    WHERE line_id = ANY(%s) AND COALESCE(is_production, TRUE)
                      AND UPPER(shift_name) NOT LIKE 'GAP%%'
                    GROUP BY shift_name ORDER BY MIN(start_time)""", (line_ids,))
    return [r["shift_name"] for r in cur.fetchall()]


def _per_shift(cur, user, d, zone_wide, line_ids):
    out = []
    for sh in _shift_names(cur, line_ids):
        p = _production(cur, user, d, zone_wide, shift=sh)
        t = p["totals"]
        out.append({"shift": sh, "plan": t["plan"], "actual": t["actual"], "ok": t["ok"],
                    "ng": t["ng"], "pct": t["pct"], "oee": t["oee"],
                    "running": t.get("running", 0), "loss_minutes": t["loss_minutes"]})
    return out


def _shift_close(cur, lines, d):
    """Close status of every production shift that actually ran on d."""
    ids = [ln["id"] for ln in lines]
    if not ids:
        return {"total": 0, "closed_ontime": 0, "closed_late": 0, "pending": 0,
                "running": 0, "pending_rows": []}
    cur.execute("""SELECT line_id, shift_name, end_time, COALESCE(crosses_midnight,FALSE) AS xm
                     FROM mes_shift_configs
                    WHERE line_id = ANY(%s) AND COALESCE(is_production, TRUE)
                      AND UPPER(shift_name) NOT LIKE 'GAP%%'""", (ids,))
    shifts = {}
    for r in cur.fetchall():
        shifts.setdefault(r["line_id"], []).append(r)
    closed = {}
    try:
        cur.execute("SAVEPOINT _po_sc")
        cur.execute("""SELECT line_id, shift_name, on_time FROM mes_shift_compile
                        WHERE record_date = %s AND line_id = ANY(%s)""", (d, ids))
        closed = {(r["line_id"], r["shift_name"]): r["on_time"] for r in cur.fetchall()}
        cur.execute("RELEASE SAVEPOINT _po_sc")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _po_sc")
    now = datetime.now()
    c = {"total": 0, "closed_ontime": 0, "closed_late": 0, "pending": 0, "running": 0}
    pending_rows = []
    for ln in lines:
        tbl = ln.get("db_table_name")
        if not tbl or not _TABLE_RE.match(tbl):
            continue
        for sh in shifts.get(ln["id"], []):
            try:
                cur.execute("SAVEPOINT _po_ran")
                cur.execute(f"""SELECT 1 FROM {tbl} WHERE record_date=%s AND shift_name=%s
                                  AND COALESCE(ok_count,0)+COALESCE(ng_count,0) > 0 LIMIT 1""",
                            (d, sh["shift_name"]))
                ran = cur.fetchone() is not None
                cur.execute("RELEASE SAVEPOINT _po_ran")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT _po_ran")
                ran = False
            if not ran:
                continue
            c["total"] += 1
            key = (ln["id"], sh["shift_name"])
            end = datetime.combine(d, sh["end_time"]) if sh["end_time"] else None
            if end and (sh["xm"] or end.time() < _time(6, 0)):
                end += timedelta(days=1)
            if key in closed:
                c["closed_ontime" if closed[key] else "closed_late"] += 1
            elif end and now < end:
                c["running"] += 1
            else:
                c["pending"] += 1
                pending_rows.append({"line_id": ln["id"], "line_name": ln["line_name"],
                                     "shift": sh["shift_name"], "ended_at": _iso(end)})
    c["pending_rows"] = pending_rows
    return c


def _andon_names(lines):
    from routers.andon import _norm, _expand_keys, ANDON_LINE_ALIAS
    by_norm = {_norm(ln["line_name"]): ln["line_name"] for ln in lines}
    keys = list(_expand_keys(set(by_norm)))
    def name_of(raw):
        k = _norm(raw)
        k = ANDON_LINE_ALIAS.get(k, k)
        return by_norm.get(k, raw)
    return keys, name_of


def _breakdowns(lines, ws, we):
    """Closed + open Maintenance/Toolroom andon calls that started in [ws, we)."""
    if not lines:
        return {"count": 0, "minutes": 0, "longest": []}
    from routers.andon import _maint_conn
    keys, name_of = _andon_names(lines)
    norm = "UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g'))"
    with _maint_conn() as m:
        mc = m.cursor()
        mc.execute(f"""
            SELECT line, machine_no, display_name, fault, started_at,
                   COALESCE(duration_seconds,
                            EXTRACT(EPOCH FROM (COALESCE(ended_at, LOCALTIMESTAMP) - started_at)))
              FROM andon_history
             WHERE display_name = ANY(%s) AND {norm} = ANY(%s)
               AND started_at >= %s AND started_at < %s
            UNION ALL
            SELECT line, machine_no, display_name, fault, started_at,
                   EXTRACT(EPOCH FROM (LOCALTIMESTAMP - started_at))
              FROM andon_system
             WHERE state = 'OPEN' AND display_name = ANY(%s) AND {norm} = ANY(%s)
               AND started_at >= %s AND started_at < %s""",
            (list(BREAKDOWN_TYPES), keys, ws, we, list(BREAKDOWN_TYPES), keys, ws, we))
        rows = mc.fetchall()
        m.rollback()
    calls = [{"line_name": name_of(r[0]), "machine": r[1], "type": r[2], "fault": r[3],
              "started_at": _iso(r[4]), "minutes": round(float(r[5] or 0) / 60.0, 1)} for r in rows]
    calls.sort(key=lambda c: -c["minutes"])
    return {"count": len(calls), "minutes": round(sum(c["minutes"] for c in calls), 1),
            "longest": calls[:3]}


def _andon_open(lines):
    if not lines:
        return []
    from routers.andon import _maint_conn
    keys, name_of = _andon_names(lines)
    norm = "UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g'))"
    with _maint_conn() as m:
        mc = m.cursor()
        mc.execute(f"""SELECT line, machine_no, display_name, fault, started_at,
                              EXTRACT(EPOCH FROM (LOCALTIMESTAMP - started_at)), acknowledged_at
                         FROM andon_system
                        WHERE state = 'OPEN' AND {norm} = ANY(%s)
                        ORDER BY started_at""", (keys,))
        rows = mc.fetchall()
        m.rollback()
    return [{"line_name": name_of(r[0]), "machine": r[1], "type": r[2], "fault": r[3],
             "started_at": _iso(r[4]), "minutes": round(float(r[5] or 0) / 60.0, 1),
             "acknowledged": r[6] is not None} for r in rows]


def _py_day(cur, ids, ws, we):
    """PY bypass cases detected in [ws, we) and how each ended."""
    c = {"raised": 0, "cleared": 0, "approved": 0, "rejected": 0, "waiting": 0, "by_line": []}
    if not ids:
        return c
    cur.execute("""SELECT line_name, status, closed_at FROM mes_py_bypass_cases
                    WHERE line_id = ANY(%s) AND detected_at >= %s AND detected_at < %s""",
                (ids, ws, we))
    per = {}
    for r in cur.fetchall():
        c["raised"] += 1
        st = (r["status"] or "").upper()
        k = {"CLEARED": "cleared", "APPROVED": "approved", "REJECTED": "rejected"}.get(st, "waiting")
        c[k] += 1
        per[r["line_name"]] = per.get(r["line_name"], 0) + 1
    c["by_line"] = [{"line_name": k, "count": v}
                    for k, v in sorted(per.items(), key=lambda kv: -kv[1])][:8]
    return c


def _py_now(cur, ids):
    out = {"open": [], "waiting": 0, "manual": [], "mail_configured": None}
    if not ids:
        return out
    cur.execute("""SELECT c.line_name, c.zone_name, c.py_no, c.py_name, c.status, c.detected_at,
                          c.bit_state,
                          COALESCE(NULLIF(m.machine_name,''), m.station_code) AS machine,
                          EXTRACT(EPOCH FROM (now() - c.detected_at)) AS age_s
                     FROM mes_py_bypass_cases c
                     LEFT JOIN mes_py_master m ON m.py_no = c.py_no
                    WHERE c.line_id = ANY(%s) AND c.closed_at IS NULL
                    ORDER BY c.detected_at""", (ids,))
    for r in cur.fetchall():
        out["open"].append({"line_name": r["line_name"], "zone_name": r["zone_name"],
                            "py_no": r["py_no"], "py_name": r["py_name"], "status": r["status"],
                            "machine": r["machine"], "detected_at": _iso(r["detected_at"]),
                            "minutes": round(float(r["age_s"] or 0) / 60.0, 1),
                            "stop_bit": bool(r["bit_state"])})
    out["waiting"] = sum(1 for r in out["open"] if (r["status"] or "").upper() == "WAITING")
    try:
        cur.execute("SAVEPOINT _po_man")
        cur.execute("""SELECT mb.machine_key, mb.reason, mb.turned_by, mb.turned_at, l.line_name
                         FROM mes_py_manual_bypass mb LEFT JOIN mes_lines l ON l.id = mb.line_id
                        WHERE mb.is_on AND mb.line_id = ANY(%s)""", (ids,))
        out["manual"] = [{"line_name": r["line_name"], "machine": r["machine_key"],
                          "reason": r["reason"], "by": r["turned_by"],
                          "since": _iso(r["turned_at"])} for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT _po_man")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _po_man")
    try:
        cur.execute("SAVEPOINT _po_mail")
        cur.execute("""SELECT COUNT(*) AS n FROM mes_py_bypass_mail
                        WHERE line_id = ANY(%s) AND COALESCE(TRIM(to_addrs),'') <> ''""", (ids,))
        per_line = (cur.fetchone() or {}).get("n", 0)
        cur.execute("RELEASE SAVEPOINT _po_mail")
        glob = []
        try:
            from routers.poka_yoke import _get_mail_addrs
            glob = list((_get_mail_addrs("bypass") or ([], []))[0] or [])
        except Exception:
            glob = []
        out["mail_configured"] = bool(per_line or glob)
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _po_mail")
    return out


def _sensor_faults(ids):
    if not ids:
        return []
    from routers.andon import _maint_conn
    with _maint_conn() as m:
        mc = m.cursor()
        try:
            mc.execute("""SELECT line_name, zone_name, py_no, py_name, not_working_since,
                                 stuck_for_sec
                            FROM maintenance_py_sensor_faults
                           WHERE line_id = ANY(%s) ORDER BY stuck_for_sec DESC""", (ids,))
            rows = mc.fetchall()
        except Exception:
            rows = []
        m.rollback()
    return [{"line_name": r[0], "zone_name": r[1], "py_no": r[2], "py_name": r[3],
             "since": _iso(r[4]), "minutes": round(float(r[5] or 0) / 60.0, 1)} for r in rows]


def _deviations(cur, ids, see_all, ws, we):
    where = "(line_id = ANY(%s)" + (" OR line_id IS NULL)" if see_all else ")")
    cur.execute(f"""SELECT COUNT(*) FILTER (WHERE status = 'PENDING_QA') AS pending,
                           COUNT(*) FILTER (WHERE created_at >= %s AND created_at < %s) AS created
                      FROM mes_quality_deviations WHERE {where}""", (ws, we, ids))
    r = cur.fetchone() or {}
    return {"pending": int(r.get("pending") or 0), "created": int(r.get("created") or 0)}


def _escalations(cur, ids, since):
    """Open shift escalations from `since` on (older open rows are history
    nobody closed, not something to act on now)."""
    cur.execute("""SELECT e.record_date, e.shift_name, e.current_level, l.line_name
                     FROM mes_shift_escalation e LEFT JOIN mes_lines l ON l.id = e.line_id
                    WHERE e.status = 'open' AND e.line_id = ANY(%s) AND e.record_date >= %s
                    ORDER BY e.record_date DESC, l.line_name""", (ids, since))
    return [{"line_name": r["line_name"], "date": _iso(r["record_date"]),
             "shift": r["shift_name"], "level": r["current_level"]} for r in cur.fetchall()]


def _safe(errors, label, fn, default):
    try:
        return fn()
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        return default


def _guarded(cur, errors, label, fn, default):
    """Like _safe, for energydb reads sharing the transaction."""
    try:
        cur.execute("SAVEPOINT _po_g")
        out = fn()
        cur.execute("RELEASE SAVEPOINT _po_g")
        return out
    except Exception as exc:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT _po_g")
        except Exception:
            pass
        errors.append(f"{label}: {exc}")
        return default


def _closed_day(cur, user, lines, zone_wide, d, start, errors, detail=True):
    ids = [ln["id"] for ln in lines]
    ws = datetime.combine(d, start)
    we = ws + timedelta(days=1)
    see_all = (user.get("role") or "") in ("admin", "plant_head")
    out = _guarded(cur, errors, "production", lambda: _production(cur, user, d, zone_wide), None)
    if out is None or not detail:
        return out
    out["per_shift"] = _guarded(cur, errors, "per shift", lambda: _per_shift(cur, user, d, zone_wide, ids), [])
    out["shift_close"] = _guarded(cur, errors, "shift close", lambda: _shift_close(cur, lines, d), None)
    out["breakdowns"] = _safe(errors, "breakdowns", lambda: _breakdowns(lines, ws, we), None)
    out["py"] = _guarded(cur, errors, "py bypass", lambda: _py_day(cur, ids, ws, we), None)
    out["deviations"] = _guarded(cur, errors, "deviations", lambda: _deviations(cur, ids, see_all, ws, we), None)
    return out


# ── endpoint ──────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def plant_overview(user=Depends(get_current_user)):
    t0 = time.time()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SET TRANSACTION READ ONLY")
        lines, zone_wide = _scope(cur, user)
        ids = sorted(ln["id"] for ln in lines)
        role = user.get("role") or ""
        skey = "ALL" if role in ("admin", "plant_head") else f"{int(zone_wide)}:{','.join(map(str, ids))}"
        prod_day, start = _production_day(cur, ids)

        def build_today():
            errors = []
            ws = datetime.combine(prod_day, start)
            we = ws + timedelta(days=1)
            see_all = role in ("admin", "plant_head")
            today = _guarded(cur, errors, "production", lambda: _production(cur, user, prod_day, zone_wide), None)
            if today is not None:
                today["shift_close"] = _guarded(cur, errors, "shift close", lambda: _shift_close(cur, lines, prod_day), None)
                today["breakdowns"] = _safe(errors, "breakdowns", lambda: _breakdowns(lines, ws, we), None)
                today["py"] = _guarded(cur, errors, "py bypass", lambda: _py_day(cur, ids, ws, we), None)
                today["deviations"] = _guarded(cur, errors, "deviations", lambda: _deviations(cur, ids, see_all, ws, we), None)
            attention = {
                "andon_open": _safe(errors, "andon", lambda: _andon_open(lines), []),
                "py_now": _guarded(cur, errors, "py now", lambda: _py_now(cur, ids), None),
                "sensor_faults": _safe(errors, "sensor faults", lambda: _sensor_faults(ids), []),
                "escalations": _guarded(cur, errors, "escalations",
                                        lambda: _escalations(cur, ids, prod_day - timedelta(days=1)), []),
                "stopped_lines": [{"zone_name": z["zone_name"], "line_name": l["line_name"],
                                   "plan": l["plan"], "live_status": l["live_status"]}
                                  for z in (today or {}).get("zones", []) for l in z["lines"]
                                  if l["status"] == "stopped"],
                "shift_pending": ((today or {}).get("shift_close") or {}).get("pending_rows", []),
            }
            return {"today": today, "attention": attention, "errors": errors}

        def build_closed():
            errors = []
            from routers.working_calendar import day_status
            lwd = _working_day_before(cur, lines, prod_day)
            prev = _working_day_before(cur, lines, lwd) if lwd else None
            lwd_block = _closed_day(cur, user, lines, zone_wide, lwd, start, errors) if lwd else None
            prev_block = _closed_day(cur, user, lines, zone_wide, prev, start, errors, detail=False) if prev else None
            today_cal = _guarded(cur, errors, "calendar", lambda: day_status(cur, prod_day), None)
            return {"lwd": lwd_block, "prev": (prev_block or {}).get("totals"),
                    "prev_date": prev.isoformat() if prev else None,
                    "today_calendar": today_cal, "errors": errors}

        live = _cached(("today", skey, prod_day), TODAY_TTL, build_today)
        closed = _cached(("closed", skey, prod_day), CLOSED_TTL, build_closed)
        conn.rollback()
    zones = sorted({ln.get("zone_name") or "—" for ln in lines})
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "production_day": prod_day.isoformat(), "day_start": start.strftime("%H:%M"),
            "scope": {"role": role, "zone_wide": zone_wide, "lines": len(lines), "zones": zones},
            "today": live["today"], "attention": live["attention"],
            "today_calendar": closed["today_calendar"],
            "last_working_day": closed["lwd"], "prev_working_day": closed["prev_date"],
            "prev_totals": closed["prev"],
            "errors": live["errors"] + closed["errors"],
            "took_ms": int((time.time() - t0) * 1000)}
