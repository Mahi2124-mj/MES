"""
Andon History — read-only view over the PHYSICAL Andon system's call log.

That log lives in a SEPARATE database, ``maintenance_db`` (table
``andon_history``), written by the standalone Andon PLC service — NOT the MES's
own breakdown-based "Andon" (mes_breakdown_log in energydb).  Phase2's main pool
is energydb only, so this router owns a small, lazy, per-request connection to
maintenance_db.  Everything here is SELECT-only; it never writes.

andon_history columns used: started_at, ended_at, duration_seconds,
response_seconds, display_name (call type: Maintenance/Quality/Material/…),
priority (Critical/High/Normal), zone, line, machine_no, model, fault.
"""

import os
import re
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, FLOOR_SCOPED_ROLES
from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/andon", tags=["andon"])


def _norm(s):
    """Canonicalise a line/zone label for cross-DB matching: uppercase and strip
    every non-alphanumeric.  So andon_history's `YHB_SS` / `SEAT_SLIDER` match
    mes_lines' `YHB-SS` / mes_zones' `SEAT SLIDER`."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _user_line_scope(user):
    """Assignment scope for THIS user, resolved against energydb, returned as a
    set of NORMALISED line names to match andon_history.line.  Mirrors the app's
    scoping rule (see zone-line-assignment-scoping):
      ('all',  None)  → admin / unassigned head: no filter
      ('none', set()) → floor role with NO assignment: sees nothing
      ('some', {..})  → only their assigned lines (normalised line_name)"""
    if user.get("role") == "admin":
        return ("all", None)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.line_name
              FROM mes_operator_lines ol
              JOIN mes_lines l ON l.id = ol.line_id
             WHERE ol.admin_id = %s
        """, (user["id"],))
        names = {_norm(r["line_name"]) for r in cur.fetchall() if r.get("line_name")}
    if names:
        return ("some", names)
    if user.get("role") in FLOOR_SCOPED_ROLES:
        return ("none", set())
    return ("all", None)

# Reuse the same host/creds as energydb (see database.DB_CONFIG), only the
# database name differs.  All overridable by env.
_MAINT_DSN = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432") or 5432),
    "dbname":          os.getenv("MAINT_DB_NAME", "maintenance_db"),
    "user":            os.getenv("DB_USER", "postgres"),
    "password":        os.getenv("DB_PASS", "tbdi@123"),
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5") or 5),
}


@contextmanager
def _maint_conn():
    """Per-request maintenance_db connection (read-only).  A 503 is raised if
    the maintenance DB is unreachable, so the page degrades cleanly instead of
    500-ing the whole request."""
    try:
        conn = psycopg2.connect(**_MAINT_DSN)
    except Exception as exc:                       # DB down / wrong creds
        raise HTTPException(503, f"maintenance_db unavailable: {exc}")
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_where(date_from, date_to, zone, line, priority, call_type, scope_names=None):
    where, params = [], []
    if date_from:
        where.append("started_at >= %s");            params.append(date_from)
    if date_to:
        where.append("started_at < (%s::date + 1)"); params.append(date_to)
    if zone:
        where.append("zone = %s");                   params.append(zone)
    if line:
        where.append("line = %s");                   params.append(line)
    if priority:
        where.append("priority = %s");               params.append(priority)
    if call_type:
        where.append("display_name = %s");           params.append(call_type)
    # Per-line scope: match the normalised andon line against the user's assigned
    # line names.  scope_names=None → no scope (admin / head); [] → matches
    # nothing (floor role, unassigned).
    if scope_names is not None:
        where.append(
            "UPPER(REGEXP_REPLACE(COALESCE(line,''), '[^A-Za-z0-9]', '', 'g')) = ANY(%s)")
        params.append(list(scope_names))
    return (("WHERE " + " AND ".join(where)) if where else ""), params


@router.get("/options")
def andon_options(user=Depends(get_current_user)):
    """Distinct values for the filter dropdowns — scoped to the user's lines."""
    mode, names = _user_line_scope(user)
    if mode == "none":
        return {"zones": [], "lines": [], "types": [], "priorities": []}
    wsql, params = _build_where(None, None, None, None, None, None,
                                scope_names=(names if mode == "some" else None))
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT array_agg(DISTINCT zone)         FILTER (WHERE zone IS NOT NULL)         AS zones,
                   array_agg(DISTINCT line)         FILTER (WHERE line IS NOT NULL)         AS lines,
                   array_agg(DISTINCT display_name) FILTER (WHERE display_name IS NOT NULL) AS types,
                   array_agg(DISTINCT priority)     FILTER (WHERE priority IS NOT NULL)     AS priorities
              FROM andon_history
              {wsql}
        """, params)
        r = cur.fetchone() or {}
    return {
        "zones":      sorted(r.get("zones") or []),
        "lines":      sorted(r.get("lines") or []),
        "types":      sorted(r.get("types") or []),
        "priorities": sorted(r.get("priorities") or []),
    }


@router.get("/history")
def andon_history(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to:   Optional[str] = Query(None, alias="to"),
    zone:      Optional[str] = None,
    line:      Optional[str] = None,
    priority:  Optional[str] = None,
    call_type: Optional[str] = Query(None, alias="type"),
    limit:     int = Query(1000, ge=1, le=10000),
    user=Depends(get_current_user),
):
    """Filtered Andon call log + summary.  The summary/breakdowns are computed
    over the SAME filter (not just the returned page).  Rows are scoped to the
    user's assigned lines (normalised name match)."""
    _empty = {"rows": [], "summary": {"total": 0, "avg_response": 0, "avg_duration": 0,
                                      "max_duration": 0, "total_duration": 0},
              "by_type": [], "by_priority": []}
    mode, names = _user_line_scope(user)
    if mode == "none":
        return _empty                      # floor role, no assignment → sees nothing
    wsql, params = _build_where(date_from, date_to, zone, line, priority, call_type,
                                scope_names=(names if mode == "some" else None))
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"""
            SELECT id, started_at, ended_at, duration_seconds, response_seconds,
                   display_name, priority, zone, line, machine_no, model, fault
              FROM andon_history
              {wsql}
             ORDER BY started_at DESC NULLS LAST
             LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()

        cur.execute(f"""
            SELECT COUNT(*)                                       AS total,
                   COALESCE(ROUND(AVG(response_seconds))::int, 0) AS avg_response,
                   COALESCE(ROUND(AVG(duration_seconds))::int, 0) AS avg_duration,
                   COALESCE(MAX(duration_seconds), 0)             AS max_duration,
                   COALESCE(SUM(duration_seconds), 0)             AS total_duration
              FROM andon_history
              {wsql}
        """, params)
        summary = cur.fetchone() or {}

        cur.execute(f"""
            SELECT display_name AS name, COUNT(*) AS n,
                   COALESCE(ROUND(AVG(response_seconds))::int, 0) AS avg_response,
                   COALESCE(SUM(duration_seconds), 0)            AS total_duration
              FROM andon_history
              {wsql}
             GROUP BY display_name
             ORDER BY n DESC
        """, params)
        by_type = cur.fetchall()

        cur.execute(f"""
            SELECT priority AS name, COUNT(*) AS n
              FROM andon_history
              {wsql}
             GROUP BY priority
             ORDER BY n DESC
        """, params)
        by_priority = cur.fetchall()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    for r in rows:
        r["started_at"] = _iso(r["started_at"])
        r["ended_at"]   = _iso(r["ended_at"])

    return {
        "rows":        rows,
        "summary":     summary,
        "by_type":     by_type,
        "by_priority": by_priority,
    }


@router.get("/active")
def andon_active(user=Depends(get_current_user)):
    """LIVE — the andon calls OPEN right now (from andon_system, which holds only
    unclosed calls; a call moves to andon_history once it ends).  Same per-line
    scope as the history view."""
    mode, names = _user_line_scope(user)
    if mode == "none":
        return {"rows": [], "count": 0}
    where, params = ["state = 'OPEN'"], []
    if mode == "some":
        where.append(
            "UPPER(REGEXP_REPLACE(COALESCE(line,''), '[^A-Za-z0-9]', '', 'g')) = ANY(%s)")
        params.append(list(names))
    wsql = "WHERE " + " AND ".join(where)
    with _maint_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT id, started_at, acknowledged_at, display_name, priority,
                   zone, line, machine_no, model, fault, state,
                   EXTRACT(EPOCH FROM (now() - started_at))::int AS elapsed_seconds,
                   CASE WHEN acknowledged_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (acknowledged_at - started_at))::int
                   END AS response_seconds
              FROM andon_system
              {wsql}
             ORDER BY started_at ASC
        """, params)
        rows = cur.fetchall()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    for r in rows:
        r["started_at"]      = _iso(r["started_at"])
        r["acknowledged_at"] = _iso(r["acknowledged_at"])
    return {"rows": rows, "count": len(rows)}


# ══════════════════════════════════════════════════════════════════════
# Shared: andon-based BREAKDOWN downtime for the display-layer OEE override.
# Operator decision (2026-08-30): on lines that have physical andon hardware,
# the "breakdown" loss = the andon Maintenance + Toolroom call durations, and
# OEE is recomputed from it.  This is DISPLAY-ONLY — the collector's stored
# counting/OEE is never changed; lines.get_line_realtime + the reports layer
# call these helpers and recompute on the fly, ONLY for andon-covered lines.
# ══════════════════════════════════════════════════════════════════════
import time as _time

# The two andon call types that count as machine "breakdown" downtime.
BREAKDOWN_CALL_TYPES = ["Maintenance", "Toolroom"]

_andon_lines_cache = {"ts": 0.0, "val": set()}
_bd_cache = {}   # (norm_line, start, end) -> (ts, seconds); ~8s TTL for the hot path


def andon_line_set():
    """NORMALISED line names that have ANY andon coverage (history or live).
    Cached ~60 s so non-andon lines never touch maintenance_db on the hot
    /realtime path; a maintenance_db hiccup returns the last-known set."""
    now = _time.time()
    if _andon_lines_cache["val"] and now - _andon_lines_cache["ts"] < 60:
        return _andon_lines_cache["val"]
    try:
        with _maint_conn() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT line FROM andon_history WHERE line IS NOT NULL
                           UNION
                           SELECT line FROM andon_system  WHERE line IS NOT NULL""")
            val = {_norm(r[0]) for r in cur.fetchall() if r[0]}
        _andon_lines_cache.update(ts=now, val=val)
        return val
    except Exception:
        return _andon_lines_cache["val"]


def andon_breakdown_seconds(line_name, start_dt, end_dt):
    """Maintenance+Toolroom andon downtime (seconds) overlapping [start_dt,end_dt]
    for a line (matched on normalised name), from the COMPLETED-call log
    ``andon_history`` only, each interval clipped to the window.

    Deliberately EXCLUDES currently-open calls (andon_system): an open call that
    is never closed (stale/abandoned) would balloon its elapsed time and drive
    OEE to 0 — and the operator asked for the *andon history* table.  An
    in-progress breakdown is counted the moment its call closes and lands in
    andon_history; while open it shows only on the LIVE board.
    Never raises — returns 0.0 on error (OEE must not break)."""
    n = _norm(line_name)
    if not n:
        return 0.0
    key = (n, str(start_dt), str(end_dt))
    now = _time.time()
    hit = _bd_cache.get(key)
    if hit and now - hit[0] < 8:
        return hit[1]
    try:
        # UNION of the merged breakdown intervals — overlapping calls (e.g.
        # Maintenance + Toolroom raised together on the SAME machine) must NOT
        # be double-counted: the line is down for their union, not the sum of
        # the two durations.  Includes completed (andon_history) + in-progress
        # (andon_system OPEN) calls, each clipped to the window.
        merged = andon_breakdown_intervals(line_name, start_dt, end_dt)
        val = max(0.0, sum((b - a).total_seconds() for a, b in merged))
        if len(_bd_cache) > 500:
            _bd_cache.clear()
        _bd_cache[key] = (now, val)
        return val
    except Exception:
        return hit[1] if hit else 0.0


def recompute_oee_with_andon(line_name, row, start_dt, end_dt):
    """For an andon-covered line, replace the stored breakdown with the andon
    Maintenance+Toolroom downtime over [start_dt,end_dt] and recompute OEE using
    the collector's EXACT identity (avail = (plan_s − avail_losses)/plan_s;
    perf = (run_s − speed)/run_s).  `row` is a dashboard row (dict-like) with
    loss_*_seconds + availability/performance/quality_oee.  Returns
    {breakdown_seconds, availability, performance, overall_oee, oee_grade}; the
    OEE fields are None when plan_s can't be derived (caller keeps stored OEE but
    may still show the andon breakdown).  Returns None if the line isn't
    andon-covered.  Never raises."""
    try:
        if _norm(line_name) not in andon_line_set():
            return None
        bd = andon_breakdown_seconds(line_name, start_dt, end_dt)

        def f(k):
            try:
                v = row.get(k) if hasattr(row, "get") else row[k]
                return float(v or 0)
            except Exception:
                return 0.0
        other = (f("loss_quality_seconds") + f("loss_setup_seconds")
                 + f("loss_material_seconds") + f("loss_others_seconds")
                 + f("loss_change_over_seconds"))
        old_avail_losses = f("loss_breakdown_seconds") + other
        speed = f("loss_speed_seconds")
        avail = f("availability"); perf = f("performance"); qual = f("quality_oee")
        plan_s = None
        if 0 < avail < 100 and old_avail_losses > 0:
            plan_s = old_avail_losses / (1 - avail / 100.0)
        elif 0 < perf < 100 and speed > 0:
            plan_s = speed / (1 - perf / 100.0) + old_avail_losses
        out = {"breakdown_seconds": int(round(bd)), "availability": None,
               "performance": None, "overall_oee": None, "oee_grade": None}
        if plan_s and plan_s > 0:
            new_run = max(0.0, plan_s - (bd + other))
            na = min(100.0, max(0.0, new_run / plan_s * 100))
            np = (min(100.0, max(0.0, (new_run - speed) / new_run * 100))
                  if new_run > 0 else 0.0)
            no = na * np * qual / 10000.0
            out.update(availability=round(na, 2), performance=round(np, 2),
                       overall_oee=round(no, 2),
                       oee_grade=("EXCELLENT" if no >= 85 else "GOOD" if no >= 75
                                  else "AVERAGE" if no >= 65 else "FAIR" if no >= 55 else "POOR"))
        return out
    except Exception:
        return None


_oc_cache = {}   # norm_line -> (ts, open_call_dict_or_None); ~5s TTL

def andon_open_call(line_name):
    """The most-relevant OPEN andon call for a line (highest priority, newest),
    or None.  Drives the LIVE operating status on andon-covered lines: ANY open
    call = a loss is running (not RUNNING); none = RUNNING.  Cached ~5s so the
    3s-polled /realtime path never hammers maintenance_db.  Never raises."""
    n = _norm(line_name)
    if not n:
        return None
    now = _time.time()
    hit = _oc_cache.get(n)
    if hit and now - hit[0] < 5:
        return hit[1]
    try:
        with _maint_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT display_name, priority, started_at, machine_no
                  FROM andon_system
                 WHERE state = 'OPEN'
                   AND UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = %s
                 ORDER BY CASE priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
                          WHEN 'Normal' THEN 2 ELSE 3 END,
                          started_at DESC
                 LIMIT 1
            """, (n,))
            row = cur.fetchone()
        val = dict(row) if row else None
        if val and hasattr(val.get("started_at"), "isoformat"):
            val["started_at"] = val["started_at"].isoformat()
        if len(_oc_cache) > 500:
            _oc_cache.clear()
        _oc_cache[n] = (now, val)
        return val
    except Exception:
        return hit[1] if hit else None


def andon_breakdown_intervals(line_name, start_dt, end_dt):
    """Maintenance+Toolroom andon breakdown INTERVALS overlapping [start,end] for
    a line (normalised name), each clipped to the window — completed calls from
    andon_history + the in-progress OPEN call (started_at → now).  Returns a list
    of (start_datetime, end_datetime) tuples for drawing the shift timeline.
    Never raises."""
    n = _norm(line_name)
    if not n:
        return []
    out = []
    try:
        with _maint_conn() as conn:
            cur = conn.cursor()
            # LOCALTIMESTAMP (naive local), NOT now() (tz-aware): started_at is
            # a naive timestamp, so mixing in now() would return tz-aware ends
            # and the Python `b > a` compare below would raise (naive vs aware).
            cur.execute("""
                SELECT GREATEST(started_at, %s),
                       LEAST(COALESCE(ended_at, LOCALTIMESTAMP), %s)
                  FROM andon_history
                 WHERE display_name = ANY(%s)
                   AND UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = %s
                   AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                UNION ALL
                SELECT GREATEST(started_at, %s), LEAST(LOCALTIMESTAMP, %s)
                  FROM andon_system
                 WHERE state='OPEN' AND display_name = ANY(%s)
                   AND UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = %s
                   AND started_at < %s
                ORDER BY 1
            """, (start_dt, end_dt, BREAKDOWN_CALL_TYPES, n, end_dt, start_dt,
                  start_dt, end_dt, BREAKDOWN_CALL_TYPES, n, end_dt))
            for a, b in cur.fetchall():
                if a and b and b > a:
                    out.append((a, b))
    except Exception:
        return []
    # Merge overlapping/adjacent intervals (union) so overlapping calls
    # (e.g. Maintenance + Toolroom raised together) count the downtime ONCE.
    out.sort(key=lambda x: x[0])
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def andon_calls_for(line_name, start_dt, end_dt):
    """Every andon call for a line overlapping [start_dt, end_dt] — completed
    (andon_history) + in-progress (andon_system OPEN) — for the Shift Compile
    breakdowns/loss section.  Normalised-name match; never raises."""
    n = _norm(line_name)
    if not n:
        return []
    out = []
    try:
        with _maint_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT machine_no, display_name AS ctype, priority,
                       duration_seconds AS dur, started_at, ended_at, fault, model,
                       FALSE AS ongoing
                  FROM andon_history
                 WHERE UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = %s
                   AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                UNION ALL
                SELECT machine_no, display_name, priority,
                       EXTRACT(EPOCH FROM (LOCALTIMESTAMP - started_at))::int,
                       started_at, NULL, fault, model, TRUE
                  FROM andon_system
                 WHERE state = 'OPEN'
                   AND UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = %s
                   AND started_at < %s
                ORDER BY started_at
            """, (n, end_dt, start_dt, n, end_dt))
            for r in cur.fetchall():
                d = dict(r)
                out.append({
                    "machine":      d.get("machine_no") or "—",
                    "type":         d.get("ctype"),
                    "priority":     d.get("priority"),
                    "downtime_min": (round((d["dur"] or 0) / 60.0, 1)
                                     if d.get("dur") is not None else None),
                    "start": d["started_at"].strftime("%H:%M") if d.get("started_at") else None,
                    "ok":    d["ended_at"].strftime("%H:%M")   if d.get("ended_at")   else None,
                    "fault": d.get("fault") or None,
                    "model": d.get("model") or None,
                    "ongoing": bool(d.get("ongoing")),
                })
    except Exception:
        return []
    return out
