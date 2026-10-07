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

from auth import get_current_user, get_current_user_optional, FLOOR_SCOPED_ROLES
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
        return ("some", _expand_keys(names))
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
# Shared: andon-based LOSSES + live status for the display-layer override.
# Operator decision (2026-08-30): on lines that have physical andon hardware,
# the "breakdown" loss = the andon Maintenance + Toolroom call durations, and
# OEE is recomputed from it.  This is DISPLAY-ONLY — the collector's stored
# counting/OEE is never changed; lines.get_line_realtime + the reports layer
# call these helpers and recompute on the fly, ONLY for andon-covered lines.
#
# 2026-10-06 — EVERY call type now goes to its own bucket and drives the live
# status (operator: "other ka button press kare to loss other me jana chahiye,
# material ka press kare toh material ka loss dikhna chahiye").  Until now ANY
# open call painted the line BREAKDOWN and only Maintenance/Toolroom were
# counted, so a Material / Other Loss / Quality / Model Setup press showed the
# wrong status and its time was counted nowhere (YRA-SS, YSD-SS).
# ══════════════════════════════════════════════════════════════════════
import time as _time
from datetime import timedelta as _td

# The two andon call types that count as machine "breakdown" downtime.
BREAKDOWN_CALL_TYPES = ["Maintenance", "Toolroom"]

# Andon button (display_name) -> MES loss bucket.  An unknown/new button lands
# in "others" so its time is still counted somewhere.
ANDON_LOSS_OF = {
    "Maintenance": "breakdown",
    "Toolroom":    "breakdown",
    "Quality":     "quality",
    "Model Setup": "setup",
    "Material":    "material",
    "Other Loss":  "others",
}
# Calls of different types that overlap are charged ONCE, to the first bucket
# in this order (a breakdown outranks the material wait it causes).
ANDON_LOSS_ORDER = ("breakdown", "quality", "setup", "material", "others")
# MES status per bucket — the same names the collector's status_map uses, so
# every dashboard already colours and labels them.
ANDON_LOSS_STATUS = {
    "breakdown": "BREAKDOWN",
    "quality":   "QUALITY_ISSUE",
    "setup":     "MODEL_SETUP",
    "material":  "MATERIAL_WAIT",
    "others":    "OTHER_LOSS",
}
# The collector books the first STARTUP_DELAY_MIN (5) minutes of every shift as
# MODEL_SETUP on its own (collector_engine.STARTUP_DELAY_MIN), so non-breakdown
# andon time inside that window is not added a second time.
SHIFT_START_SETUP_S = 5 * 60

# 2026-10-07 — the STATUS shown for a call is the call's own name for the two
# breakdown buttons (operator: "Maintenance aur Toolroom ka apna naam aur
# rang") — the LOSS bucket stays breakdown for both, so OEE is unchanged.
ANDON_CALL_STATUS = {
    "Maintenance": "MAINTENANCE",
    "Toolroom":    "TOOLROOM",
    "Quality":     "QUALITY_ISSUE",
    "Model Setup": "MODEL_SETUP",
    "Material":    "MATERIAL_WAIT",
    "Other Loss":  "OTHER_LOSS",
}
# Which call's status the timeline / live status shows when calls overlap —
# the same order as the loss buckets (a breakdown outranks the wait it causes),
# Maintenance before Toolroom inside breakdown.
ANDON_STATUS_ORDER = ("MAINTENANCE", "TOOLROOM", "QUALITY_ISSUE",
                      "MODEL_SETUP", "MATERIAL_WAIT", "OTHER_LOSS")
_STATUS_RANK = {st: i for i, st in enumerate(ANDON_STATUS_ORDER)}
BREAKDOWN_STATUSES = ("MAINTENANCE", "TOOLROOM")


def _status_of(display_name):
    return ANDON_CALL_STATUS.get((display_name or "").strip(), "OTHER_LOSS")


# Andon line names spelt differently from the MES line (normalised andon ->
# normalised MES).  Without these the Loop Pipe and YWD RC andon calls never
# reached their dashboards.  Same table as routers/logs.py _ANDON_ALIAS.
ANDON_LINE_ALIAS = {
    "LOOPPIPE1": "LOOPPIPELINE1",
    "LOOPPIPE2": "LOOPPIPELINE2",
    "LOOPPIPE3": "LOOPPIPELINE3",
    "YWDRC":     "YWDRECLINER",
}
_NORM_SQL = "UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g'))"
_PRIO_RANK = {"Critical": 0, "High": 1, "Normal": 2}
_LOSS_RANK = {c: i for i, c in enumerate(ANDON_LOSS_ORDER)}


def _loss_of(display_name):
    return ANDON_LOSS_OF.get((display_name or "").strip(), "others")


def andon_keys(line_name):
    """Normalised andon line names that belong to this MES line — its own name
    plus any alias — for `<normalised line> = ANY(%s)`."""
    n = _norm(line_name)
    if not n:
        return []
    return [n] + [a for a, m in ANDON_LINE_ALIAS.items() if m == n]


def _expand_keys(norm_names):
    """A set of normalised MES line names plus the andon aliases that map to
    them (for the per-user scope filters)."""
    out = set(norm_names or ())
    return out | {a for a, m in ANDON_LINE_ALIAS.items() if m in out}


_andon_lines_cache = {"ts": 0.0, "val": set()}
_li_cache = {}   # (keys, start, end) -> (ts, segments); ~8 s TTL for the hot path


def andon_line_set():
    """NORMALISED MES line names that have ANY andon coverage (history or live),
    aliases resolved.  Cached ~60 s so non-andon lines never touch maintenance_db
    on the hot /realtime path; a maintenance_db hiccup returns the last-known set."""
    now = _time.time()
    if _andon_lines_cache["val"] and now - _andon_lines_cache["ts"] < 60:
        return _andon_lines_cache["val"]
    try:
        with _maint_conn() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT line FROM andon_history WHERE line IS NOT NULL
                           UNION
                           SELECT line FROM andon_system  WHERE line IS NOT NULL""")
            val = set()
            for (ln,) in cur.fetchall():
                n = _norm(ln)
                if n:
                    val.add(ANDON_LINE_ALIAS.get(n, n))
        _andon_lines_cache.update(ts=now, val=val)
        return val
    except Exception:
        return _andon_lines_cache["val"]


def _attribute(raw):
    """[(start, end, bucket)] possibly overlapping -> NON-overlapping segments.
    Where buckets overlap the time goes to the highest one in ANDON_LOSS_ORDER;
    calls of the same bucket merge (union), so nothing is counted twice."""
    if not raw:
        return []
    cuts = sorted({t for a, b, _ in raw for t in (a, b)})
    segs = []
    for t0, t1 in zip(cuts, cuts[1:]):
        live = [c for a, b, c in raw if a <= t0 and b >= t1]
        if not live:
            continue
        cat = min(live, key=lambda c: _LOSS_RANK.get(c, len(_LOSS_RANK)))
        if segs and segs[-1][2] == cat and segs[-1][1] == t0:
            segs[-1] = (segs[-1][0], t1, cat)
        else:
            segs.append((t0, t1, cat))
    return segs


_raw_cache = {}   # (keys, start, end) -> (ts, [(start, end, display_name)])


def _andon_raw_calls(line_name, start_dt, end_dt):
    """Every andon call of the line overlapping [start_dt, end_dt] — completed
    (andon_history) + in-progress (andon_system OPEN, up to now) — clipped to
    the window, as (start, end, display_name).  ~8 s cache.  Raises on a DB
    error (callers decide how to degrade)."""
    keys = andon_keys(line_name)
    if not keys:
        return []
    ck = (tuple(keys), str(start_dt), str(end_dt))
    now = _time.time()
    hit = _raw_cache.get(ck)
    if hit and now - hit[0] < 8:
        return hit[1]
    raw = []
    with _maint_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT display_name, GREATEST(started_at, %s),
                   LEAST(COALESCE(ended_at, LOCALTIMESTAMP), %s)
              FROM andon_history
             WHERE {_NORM_SQL} = ANY(%s)
               AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
            UNION ALL
            SELECT display_name, GREATEST(started_at, %s), LEAST(LOCALTIMESTAMP, %s)
              FROM andon_system
             WHERE state = 'OPEN' AND {_NORM_SQL} = ANY(%s)
               AND started_at < %s
        """, (start_dt, end_dt, keys, end_dt, start_dt,
              start_dt, end_dt, keys, end_dt))
        for name, a, b in cur.fetchall():
            if a and b and b > a:
                raw.append((a, b, name))
    if len(_raw_cache) > 500:
        _raw_cache.clear()
    _raw_cache[ck] = (now, raw)
    return raw


def andon_status_intervals(line_name, start_dt, end_dt):
    """For the shift TIMELINE: the andon calls over [start_dt, end_dt] as
    non-overlapping (start, end, status, calls) segments.  `status` is the
    call's own status (MAINTENANCE / TOOLROOM / QUALITY_ISSUE / …); where calls
    overlap, the highest in ANDON_STATUS_ORDER is shown and `calls` names every
    call that was open (e.g. "Quality + Maintenance") for the tooltip.  Raw
    call time (not production-aware): the timeline shows when a call was OPEN.
    Never raises; [] on error."""
    try:
        raw = _andon_raw_calls(line_name, start_dt, end_dt)
    except Exception:
        return []
    if not raw:
        return []
    cuts = sorted({t for a, b, _ in raw for t in (a, b)})
    segs = []
    for t0, t1 in zip(cuts, cuts[1:]):
        live = [n for a, b, n in raw if a <= t0 and b >= t1]
        if not live:
            continue
        sts = sorted({_status_of(n) for n in live},
                     key=lambda x: _STATUS_RANK.get(x, len(_STATUS_RANK)))
        names = sorted(set(live), key=lambda n: _STATUS_RANK.get(_status_of(n), 99))
        calls = " + ".join(names)
        if segs and segs[-1][1] == t0 and segs[-1][2] == sts[0] and segs[-1][3] == calls:
            segs[-1] = (segs[-1][0], t1, sts[0], calls)
        else:
            segs.append((t0, t1, sts[0], calls))
    return segs


def andon_loss_intervals(line_name, start_dt, end_dt):
    """Every andon call of the line overlapping [start_dt, end_dt] — completed
    (andon_history) + in-progress (andon_system OPEN, up to now) — clipped to the
    window and returned as non-overlapping (start, end, bucket) segments (see
    _attribute).  Never raises; [] on error."""
    keys = andon_keys(line_name)
    if not keys:
        return []
    ck = (tuple(keys), str(start_dt), str(end_dt))
    now = _time.time()
    hit = _li_cache.get(ck)
    if hit and now - hit[0] < 8:
        return hit[1]
    raw = []
    try:
        raw = [(a, b, _loss_of(n)) for a, b, n in
               _andon_raw_calls(line_name, start_dt, end_dt)]
    except Exception:
        return hit[1] if hit else []
    val = _attribute(raw)
    if len(_li_cache) > 500:
        _li_cache.clear()
    _li_cache[ck] = (now, val)
    return val


def andon_loss_split(segs, win_start, win_end, setup_until=None):
    """{bucket: seconds} of andon `segs` clipped to [win_start, win_end].  Time
    before `setup_until` (the collector's own shift-start MODEL_SETUP window) is
    left out for every bucket except breakdown, which the andon owns outright."""
    out = {c: 0.0 for c in ANDON_LOSS_ORDER}
    for a, b, c in segs:
        lo = max(a, win_start)
        if c != "breakdown" and setup_until is not None:
            lo = max(lo, setup_until)
        hi = min(b, win_end)
        if hi > lo:
            out[c] = out.get(c, 0.0) + (hi - lo).total_seconds()
    return out


# ── what the line actually LOST while a call was open ──────────────────
# 2026-10-06 — an andon call says WHY the line stopped, not THAT it stopped.
# Calls are left open while the line runs (Y17-SS Material: 175 min open,
# 663 parts made at 94 % of ideal), and charging the whole open time sank OEE
# to 47 % on a line that made its plan.  So the loss is the call's time
# MINUS the time the line was making parts (the ideal-CT slice that ends at
# each cycle) and MINUS scheduled breaks.  A full stop (0 parts) still counts
# its whole duration; a call left open on a running line counts only its real
# gaps.
#
# The collector already filed the slow part of every cycle up to 300 s as
# SPEED loss (CycleTimeTracker._add).  The share of that which falls inside an
# andon call is returned as `speed_moved`, so the caller moves it out of speed
# into the call's bucket instead of counting the same seconds twice.

import bisect as _bisect
from datetime import datetime as _dtm

_ctx_cache = {}    # norm line -> (ts, (ct_log table, ideal_ct, breaks) | None)
_lost_cache = {}   # (keys, start, end) -> (ts, (lost_segs, slow_parts))


def _line_ctx(line_name):
    """(ct_log table, ideal CT, [(break start, break end) as time]) for the MES
    line, or None.  Cached ~60 s.  Never raises."""
    n = _norm(line_name)
    now = _time.time()
    hit = _ctx_cache.get(n)
    if hit and now - hit[0] < 60:
        return hit[1]
    val = None
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT id, db_table_name, ideal_cycle_time FROM mes_lines
                            WHERE UPPER(REGEXP_REPLACE(COALESCE(line_name,''),'[^A-Za-z0-9]','','g')) = %s
                            ORDER BY id LIMIT 1""", (n,))
            ln = cur.fetchone()
            if ln and ln.get("db_table_name") and re.fullmatch(r"[A-Za-z0-9_]+", ln["db_table_name"]):
                cur.execute("SELECT start_time, end_time FROM mes_break_configs WHERE line_id=%s",
                            (ln["id"],))
                brk = [(r["start_time"], r["end_time"]) for r in cur.fetchall()
                       if r.get("start_time") and r.get("end_time")]
                val = (ln["db_table_name"] + "_ct_log",
                       float(ln.get("ideal_cycle_time") or 15.0), brk)
    except Exception:
        return hit[1] if hit else None
    if len(_ctx_cache) > 200:
        _ctx_cache.clear()
    _ctx_cache[n] = (now, val)
    return val


def _merge(iv):
    out = []
    for x, y in sorted(iv):
        if y <= x:
            continue
        if out and x <= out[-1][1]:
            if y > out[-1][1]:
                out[-1] = (out[-1][0], y)
        else:
            out.append((x, y))
    return out


def _subtract(segs, cuts):
    """(start, end, bucket) segments minus the union of (x, y) cuts."""
    m = _merge(cuts)
    if not m:
        return list(segs)
    starts = [x for x, _ in m]
    out = []
    for a, b, c in segs:
        cur = a
        i = max(0, _bisect.bisect_right(starts, a) - 1)
        while i < len(m) and m[i][0] < b:
            x, y = m[i]
            if y > cur:
                if x > cur:
                    out.append((cur, min(x, b), c))
                cur = max(cur, y)
                if cur >= b:
                    break
            i += 1
        if cur < b:
            out.append((cur, b, c))
    return [t for t in out if t[1] > t[0]]


def _overlap(iv, segs):
    """Seconds of the (x, y) intervals `iv` that fall inside `segs`."""
    m = _merge([(a, b) for a, b, _ in segs])
    if not m or not iv:
        return 0.0
    starts = [x for x, _ in m]
    tot = 0.0
    for x, y in iv:
        i = max(0, _bisect.bisect_right(starts, x) - 1)
        while i < len(m) and m[i][0] < y:
            lo, hi = max(x, m[i][0]), min(y, m[i][1])
            if hi > lo:
                tot += (hi - lo).total_seconds()
            i += 1
    return tot


def andon_lost_intervals(line_name, start_dt, end_dt):
    """(lost, slow): `lost` = the andon segments over [start_dt, end_dt] with the
    line's producing time and scheduled breaks taken out — what the line really
    lost while each call was open, still tagged with the call's bucket.
    `slow` = the slow slices the collector filed as speed loss ([cycle start,
    end − ideal] for 1 s ≤ CT ≤ 300 s), for working out `speed_moved`.
    Falls back to the raw call segments if the line's cycles can't be read.
    Never raises."""
    segs = andon_loss_intervals(line_name, start_dt, end_dt)
    if not segs:
        return [], []
    keys = andon_keys(line_name)
    ck = (tuple(keys), str(start_dt), str(end_dt))
    now = _time.time()
    hit = _lost_cache.get(ck)
    if hit and now - hit[0] < 8:
        return hit[1]
    val, ok = _cut_production(line_name, segs)
    if not ok:
        return val
    if len(_lost_cache) > 500:
        _lost_cache.clear()
    _lost_cache[ck] = (now, val)
    return val


def _cut_production(line_name, segs):
    """((lost, slow), ok) for any (start, end, tag) segments of the line: the
    segments with scheduled breaks and the line's producing time (the ideal-CT
    slice ending at each cycle) taken out, plus the collector's slow slices
    (see andon_lost_intervals).  ok=False -> the cycles could not be read and
    (segs, []) is returned unchanged.  Never raises."""
    ctx = _line_ctx(line_name)
    if not ctx or not segs:
        return (segs, []), False
    tbl, ideal, breaks = ctx
    try:
        lo = min(a for a, _, _ in segs)
        hi = max(b for _, b, _ in segs)
        # scheduled breaks on every day the calls touch
        bw = []
        d = lo.date() - _td(days=1)
        while d <= hi.date():
            for bs, be in breaks:
                x = _dtm.combine(d, bs)
                y = _dtm.combine(d, be)
                if y <= x:
                    y += _td(days=1)
                bw.append((x, y))
            d += _td(days=1)
        work = _subtract(segs, bw)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""SELECT ts, ct_value FROM {tbl}
                             WHERE ts >= %s AND ts < %s AND ct_value > 0
                             ORDER BY ts""",
                        (lo, hi + _td(seconds=ideal + 1)))
            cyc = [(t, float(ct)) for t, ct in cur.fetchall() if t is not None]
        prod = [(t - _td(seconds=min(ct, ideal)), t) for t, ct in cyc]
        lost = _subtract(work, prod)
        slow = [(t - _td(seconds=ct), t - _td(seconds=ideal))
                for t, ct in cyc if ideal < ct <= 300.0]
        return (lost, slow), True
    except Exception as exc:
        print(f"[ANDON] production check skipped for {line_name}: {exc}")
        return (segs, []), False


def _carve_setup(segs, setup_until):
    """Non-breakdown segments with everything before `setup_until` removed (the
    collector's own shift-start MODEL_SETUP window)."""
    if setup_until is None:
        return list(segs)
    out = []
    for a, b, c in segs:
        if c != "breakdown" and a < setup_until:
            a = setup_until
        if b > a:
            out.append((a, b, c))
    return out


def andon_loss_detail(line_name, start_dt, end_dt, setup_until=None):
    """({bucket: seconds}, speed_moved) — the line's real andon losses over the
    window and the seconds of it the collector had already put in speed loss."""
    try:
        lost, slow = andon_lost_intervals(line_name, start_dt, end_dt)
        lost = _carve_setup(lost, setup_until)
        secs = andon_loss_split(lost, start_dt, end_dt)
        moved = _overlap([(max(x, start_dt), min(y, end_dt)) for x, y in slow
                          if y > start_dt and x < end_dt], lost)
        return secs, moved
    except Exception:
        return {c: 0.0 for c in ANDON_LOSS_ORDER}, 0.0


def andon_loss_seconds(line_name, start_dt, end_dt, setup_until=None):
    """{bucket: seconds} the line really lost to andon calls over [start_dt,
    end_dt] (production + breaks taken out).  Never raises."""
    return andon_loss_detail(line_name, start_dt, end_dt, setup_until)[0]


def andon_breakdown_intervals(line_name, start_dt, end_dt):
    """Maintenance+Toolroom andon breakdown INTERVALS overlapping [start,end],
    merged (union) and clipped — completed calls + the in-progress OPEN call,
    with the line's producing time and breaks taken out (andon_lost_intervals).
    Never raises."""
    merged = []
    for a, b, c in andon_lost_intervals(line_name, start_dt, end_dt)[0]:
        if c != "breakdown":
            continue
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def andon_breakdown_seconds(line_name, start_dt, end_dt):
    """Breakdown (Maintenance+Toolroom) andon downtime in seconds over the
    window — union of overlapping calls.  Never raises — 0.0 on error."""
    try:
        return max(0.0, sum((b - a).total_seconds()
                            for a, b in andon_breakdown_intervals(line_name, start_dt, end_dt)))
    except Exception:
        return 0.0


def recompute_oee_with_andon(line_name, row, start_dt, end_dt):
    """For an andon-covered line, apply the andon losses over [start_dt, end_dt]
    (start_dt = the shift start) and recompute OEE using the collector's EXACT
    identity (avail = (plan_s − avail_losses)/plan_s; perf = (run_s − speed)/run_s).

      breakdown            = andon Maintenance+Toolroom (replaces the stored value)
      quality/setup/
      material/others      = stored (PLC) value + that andon bucket

    `row` is a dashboard row (dict-like) with loss_*_seconds + availability /
    performance / quality_oee.  Returns {breakdown_seconds, losses{bucket: s},
    availability, performance, overall_oee, oee_grade}; the OEE fields are None
    when plan_s can't be derived (caller keeps stored OEE but still shows the
    andon losses).  Returns None if the line isn't andon-covered.  Never raises."""
    try:
        if _norm(line_name) not in andon_line_set():
            return None
        al, moved = andon_loss_detail(line_name, start_dt, end_dt,
                                      setup_until=start_dt + _td(seconds=SHIFT_START_SETUP_S))

        def f(k):
            try:
                v = row.get(k) if hasattr(row, "get") else row[k]
                return float(v or 0)
            except Exception:
                return 0.0
        old = {c: f(f"loss_{c}_seconds") for c in ANDON_LOSS_ORDER}
        co = f("loss_change_over_seconds")
        new = {"breakdown": al.get("breakdown", 0.0)}
        for c in ("quality", "setup", "material", "others"):
            new[c] = old[c] + al.get(c, 0.0)
        old_avail_losses = sum(old.values()) + co
        new_avail_losses = sum(new.values()) + co
        speed = f("loss_speed_seconds")
        # the collector's speed loss already holds `moved` seconds of the stops
        # now charged to an andon bucket — take them out of speed, not twice.
        new_speed = max(0.0, speed - moved)
        avail = f("availability"); perf = f("performance"); qual = f("quality_oee")
        plan_s = None
        if 0 < avail < 100 and old_avail_losses > 0:
            plan_s = old_avail_losses / (1 - avail / 100.0)
        elif 0 < perf < 100 and speed > 0:
            plan_s = speed / (1 - perf / 100.0) + old_avail_losses
        out = {"breakdown_seconds": int(round(new["breakdown"])),
               "losses": {c: int(round(v)) for c, v in new.items()},
               "speed_seconds": int(round(new_speed)),
               "availability": None, "performance": None,
               "overall_oee": None, "oee_grade": None}
        if plan_s and plan_s > 0:
            new_run = max(0.0, plan_s - new_avail_losses)
            na = min(100.0, max(0.0, new_run / plan_s * 100))
            np = (min(100.0, max(0.0, (new_run - new_speed) / new_run * 100))
                  if new_run > 0 else 0.0)
            no = na * np * qual / 10000.0
            out.update(availability=round(na, 2), performance=round(np, 2),
                       overall_oee=round(no, 2),
                       oee_grade=("EXCELLENT" if no >= 85 else "GOOD" if no >= 75
                                  else "AVERAGE" if no >= 65 else "FAIR" if no >= 55 else "POOR"))
        return out
    except Exception:
        return None


_oc_cache = {}   # keys -> (ts, open_call_dict_or_None); ~5s TTL

def andon_open_call(line_name):
    """The OPEN andon call that decides the line's LIVE status, or None.
    Picked by bucket (ANDON_LOSS_ORDER: breakdown first), then priority, then
    newest.  The dict carries display_name / priority / started_at / machine_no
    plus `loss` (bucket) and `status` (MES status name for that bucket).
    Cached ~5s so the 3s-polled /realtime path never hammers maintenance_db.
    Never raises."""
    keys = andon_keys(line_name)
    if not keys:
        return None
    ck = tuple(keys)
    now = _time.time()
    hit = _oc_cache.get(ck)
    if hit and now - hit[0] < 5:
        return hit[1]
    try:
        with _maint_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"""
                SELECT display_name, priority, started_at, machine_no
                  FROM andon_system
                 WHERE state = 'OPEN' AND {_NORM_SQL} = ANY(%s)
            """, (keys,))
            rows = [dict(r) for r in cur.fetchall()]
        rows.sort(key=lambda r: (
            _STATUS_RANK.get(_status_of(r.get("display_name")), len(_STATUS_RANK)),
            _PRIO_RANK.get(r.get("priority"), 3),
            -(r["started_at"].timestamp() if hasattr(r.get("started_at"), "timestamp") else 0)))
        val = rows[0] if rows else None
        if val:
            val["loss"] = _loss_of(val.get("display_name"))
            # 2026-10-07 — the call's OWN status (MAINTENANCE / TOOLROOM / …)
            val["status"] = _status_of(val.get("display_name"))
            # every call open right now, top one first, for "Quality + Maintenance"
            val["calls"] = " + ".join(r.get("display_name") or "" for r in rows)
            if hasattr(val.get("started_at"), "isoformat"):
                val["started_at"] = val["started_at"].isoformat()
        if len(_oc_cache) > 500:
            _oc_cache.clear()
        _oc_cache[ck] = (now, val)
        return val
    except Exception:
        return hit[1] if hit else None


def andon_calls_for(line_name, start_dt, end_dt):
    """Every andon call for a line overlapping [start_dt, end_dt] — completed
    (andon_history) + in-progress (andon_system OPEN) — for the Shift Compile
    breakdowns/loss section.  Normalised-name match; never raises."""
    keys = andon_keys(line_name)
    if not keys:
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
                 WHERE UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = ANY(%s)
                   AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                UNION ALL
                SELECT machine_no, display_name, priority,
                       EXTRACT(EPOCH FROM (LOCALTIMESTAMP - started_at))::int,
                       started_at, NULL, fault, model, TRUE
                  FROM andon_system
                 WHERE state = 'OPEN'
                   AND UPPER(REGEXP_REPLACE(COALESCE(line,''),'[^A-Za-z0-9]','','g')) = ANY(%s)
                   AND started_at < %s
                ORDER BY started_at
            """, (keys, end_dt, start_dt, keys, end_dt))
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


# ══════════════════════════════════════════════════════════════════════
# 2026-10-07 — per-slot andon calls for the Loss Remark card.
#   v1: every call in the slot with a per-type breakup.
#   v2 (operator: "breakdown pe click kiya to sirf breakdown ke 2-3 call, aur
#   har call pe remark daalne ka option"): filtered to the clicked loss type,
#   and every call carries its OWN remark (mes_andon_call_remarks).  Saving
#   per-call remarks also writes them, combined, into the slot's
#   mes_loss_remarks row, so the Hourly Report / Shift Compile remark column
#   keeps showing them.
# ══════════════════════════════════════════════════════════════════════
_call_remarks_ready = False


def _ensure_call_remarks_table(conn):
    """Create mes_andon_call_remarks once per process.  Catalog check first, so
    a warm DB never takes a DDL lock here (see comments-500-alter-lock)."""
    global _call_remarks_ready
    if _call_remarks_ready:
        return
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('mes_andon_call_remarks')")
    if cur.fetchone()[0] is None:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_andon_call_remarks (
                id          SERIAL PRIMARY KEY,
                line_id     INTEGER   NOT NULL,
                call_type   TEXT      NOT NULL,
                machine_no  TEXT      NOT NULL DEFAULT '',
                started_at  TIMESTAMP NOT NULL,
                remark      TEXT,
                entered_by  TEXT,
                audit_trail JSONB     NOT NULL DEFAULT '[]'::jsonb,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (line_id, call_type, machine_no, started_at)
            )""")
        conn.commit()
    _call_remarks_ready = True


def _slot_window(line_id, date, slot, shift=None):
    """(line_name, shift, slot_start, slot_end, shift_start_time) for one hourly
    slot of a line.  Night-shift slots that start before the shift's start time
    fall on the next calendar day."""
    from datetime import datetime as _dt, timedelta as _tdl, time as _t
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT line_name FROM mes_lines WHERE id=%s", (line_id,))
        ln = cur.fetchone()
        if not ln:
            raise HTTPException(404, "line not found")
        q = ("SELECT shift_name, start_time, end_time FROM mes_hourly_slots "
             "WHERE line_id=%s AND slot_label=%s")
        args = [line_id, slot]
        if shift:
            q += " AND shift_name=%s"
            args.append(shift)
        cur.execute(q + " ORDER BY slot_order LIMIT 1", args)
        sl = cur.fetchone()
        if not sl:
            m = re.match(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", slot or "")
            if not m:
                raise HTTPException(400, "slot must look like HH:MM-HH:MM")
            sl = {"shift_name": shift, "start_time": _t(int(m[1]), int(m[2])),
                  "end_time": _t(int(m[3]), int(m[4]))}
        sh = sl.get("shift_name") or shift
        sh_start_t = None
        if sh:
            cur.execute("SELECT start_time FROM mes_shift_configs "
                        "WHERE line_id=%s AND shift_name=%s", (line_id, sh))
            r = cur.fetchone()
            sh_start_t = r["start_time"] if r else None
    try:
        d0 = _dt.strptime(str(date)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    st = _dt.combine(d0, sl["start_time"])
    if sh_start_t and sl["start_time"] < sh_start_t:        # night shift, after midnight
        st += _tdl(days=1)
    en = _dt.combine(st.date(), sl["end_time"])
    if en <= st:
        en += _tdl(days=1)
    return ln["line_name"], sh, st, en, (_dt.combine(d0, sh_start_t) if sh_start_t else None)


def _slot_calls(line_id, lname, st, en):
    """Every andon call overlapping [st, en] (completed + open), clipped."""
    from datetime import datetime as _dt
    keys = andon_keys(lname)
    try:
        with _maint_conn() as conn:
            c2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            c2.execute(f"""
                SELECT display_name, priority, machine_no, fault, started_at,
                       ended_at, FALSE AS ongoing
                  FROM andon_history
                 WHERE {_NORM_SQL} = ANY(%s)
                   AND started_at < %s AND COALESCE(ended_at, LOCALTIMESTAMP) > %s
                UNION ALL
                SELECT display_name, priority, machine_no, fault, started_at,
                       NULL, TRUE
                  FROM andon_system
                 WHERE state = 'OPEN' AND {_NORM_SQL} = ANY(%s) AND started_at < %s
                 ORDER BY started_at
            """, (keys, en, st, keys, en))
            rows = c2.fetchall()
    except Exception as exc:
        raise HTTPException(503, f"andon data unavailable: {exc}")
    now = _dt.now()
    calls = []
    for r in rows:
        a = max(r["started_at"], st)
        b = min(r["ended_at"] or now, en)
        if b <= a:
            continue
        calls.append({
            "type":     r["display_name"],
            "status":   _status_of(r["display_name"]),
            "bucket":   _loss_of(r["display_name"]),
            "machine":  r.get("machine_no") or "",
            "priority": r.get("priority"),
            "fault":    r.get("fault"),
            "start":    r["started_at"].isoformat(),
            "end":      r["ended_at"].isoformat() if r["ended_at"] else None,
            "ongoing":  bool(r["ongoing"]),
            "open_s":   int(round((b - a).total_seconds())),
        })
    # attach each call's own remark
    if calls:
        try:
            with get_conn() as conn:
                _ensure_call_remarks_table(conn)
                cur = dict_cursor(conn)
                cur.execute("""SELECT call_type, machine_no, started_at, remark,
                                      entered_by, updated_at
                                 FROM mes_andon_call_remarks
                                WHERE line_id=%s AND started_at = ANY(%s)""",
                            (line_id, [_dt.fromisoformat(c["start"]) for c in calls]))
                rem = {(r["call_type"], r["machine_no"], r["started_at"].isoformat()): r
                       for r in cur.fetchall()}
            for c in calls:
                r = rem.get((c["type"], c["machine"], c["start"]))
                c["remark"]     = (r or {}).get("remark") or ""
                c["remark_by"]  = (r or {}).get("entered_by")
                c["remark_at"]  = r["updated_at"].isoformat() if r and r.get("updated_at") else None
        except Exception as exc:
            print(f"[ANDON] call remarks read failed: {exc}")
    return calls


@router.get("/slot-calls")
def andon_slot_calls(line_id: int = Query(...),
                     date: str = Query(..., description="record date YYYY-MM-DD"),
                     slot: str = Query(..., description="slot label, e.g. 18:30-19:30"),
                     shift: Optional[str] = None,
                     loss_type: Optional[str] = Query(None, description="breakdown / quality / setup / material / others — only that bucket's calls"),
                     user=Depends(get_current_user_optional)):
    """The andon calls open during one hourly slot of a line — only the clicked
    loss bucket's calls when `loss_type` is given — each with its own remark,
    plus a per-type summary: how many calls, how long OPEN inside the slot, and
    the LOSS they account for (same production-aware rule as the loss table;
    overlapping calls charged once, to the higher one)."""
    lname, sh, st, en, sh_start = _slot_window(line_id, date, slot, shift)
    lt = (loss_type or "").strip().lower() or None
    out = {"line_id": line_id, "line_name": lname, "shift": sh, "slot": slot,
           "start": st.isoformat(), "end": en.isoformat(), "loss_type": lt,
           "covered": _norm(lname) in andon_line_set(),
           "andon_bucket": lt in ANDON_LOSS_ORDER if lt else True,
           "by_type": [], "calls": []}
    if not out["covered"] or not out["andon_bucket"]:
        return out
    calls = _slot_calls(line_id, lname, st, en)
    if lt:
        calls = [c for c in calls if c["bucket"] == lt]
    out["calls"] = calls

    # loss per type — same rule as the loss table (precedence over ALL calls)
    segs = [(a, b, stt) for a, b, stt, _c in andon_status_intervals(lname, st, en)]
    (lost, _slow), _ok = _cut_production(lname, segs)
    if sh_start:
        until = sh_start + _td(seconds=SHIFT_START_SETUP_S)
        lost = [(a, b, t) if t in BREAKDOWN_STATUSES else (max(a, until), b, t)
                for a, b, t in lost]
        lost = [(a, b, t) for a, b, t in lost if b > a]
    lost_by = {}
    for a, b, t in lost:
        lost_by[t] = lost_by.get(t, 0.0) + (b - a).total_seconds()
    agg = {}
    for cl in calls:
        g = agg.setdefault(cl["status"], {"type": cl["type"], "status": cl["status"],
                                          "bucket": cl["bucket"], "count": 0, "open_s": 0})
        g["count"] += 1
        g["open_s"] += cl["open_s"]
    for stt, g in agg.items():
        g["lost_s"] = int(round(lost_by.get(stt, 0.0)))
    out["by_type"] = sorted(agg.values(), key=lambda g: _STATUS_RANK.get(g["status"], 99))
    out["production_checked"] = bool(_ok)
    return out


@router.post("/call-remarks")
def save_andon_call_remarks(body: dict, user=Depends(get_current_user)):
    """Save the remark of one or more andon calls of a slot.
    Body: {line_id, date, slot, shift?, loss_type, items: [{type, machine,
    start, remark}]}.  Each call's previous remark goes to its audit_trail.
    Afterwards the slot's mes_loss_remarks row for that loss type is rewritten
    as the combined call remarks ("21:41 Maintenance YJC_SS_05: …"), so every
    report that reads slot remarks shows them."""
    from datetime import datetime as _dt
    try:
        line_id = int(body.get("line_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "line_id is required")
    date_s = str(body.get("date") or "").strip()
    slot = str(body.get("slot") or "").strip()
    lt = str(body.get("loss_type") or "").strip().lower()
    items = body.get("items") or []
    if not (date_s and slot and lt and isinstance(items, list)):
        raise HTTPException(400, "date, slot, loss_type and items are required")
    author = ((user.get("username") if isinstance(user, dict) else getattr(user, "username", None))
              or "production")
    lname, sh, st, en, _ss = _slot_window(line_id, date_s, slot, body.get("shift") or None)
    saved = 0
    with get_conn() as conn:
        _ensure_call_remarks_table(conn)
        cur = dict_cursor(conn)
        for it in items:
            txt = str((it or {}).get("remark") or "").strip()[:2000]
            ctype = str((it or {}).get("type") or "").strip()
            mach = str((it or {}).get("machine") or "").strip()
            try:
                started = _dt.fromisoformat(str((it or {}).get("start") or ""))
            except ValueError:
                continue
            if not ctype or not txt:
                continue
            cur.execute("""SELECT remark, entered_by, audit_trail FROM mes_andon_call_remarks
                            WHERE line_id=%s AND call_type=%s AND machine_no=%s AND started_at=%s""",
                        (line_id, ctype, mach, started))
            ex = cur.fetchone()
            if ex and (ex.get("remark") or "") == txt:
                continue
            trail = list(ex["audit_trail"]) if ex and ex.get("audit_trail") else []
            if ex and ex.get("remark"):
                trail.append({"remark": ex["remark"], "entered_by": ex.get("entered_by"),
                              "replaced_at": _dt.utcnow().isoformat() + "Z"})
            cur.execute("""
                INSERT INTO mes_andon_call_remarks
                       (line_id, call_type, machine_no, started_at, remark, entered_by,
                        audit_trail, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (line_id, call_type, machine_no, started_at) DO UPDATE SET
                       remark = EXCLUDED.remark, entered_by = EXCLUDED.entered_by,
                       audit_trail = EXCLUDED.audit_trail, updated_at = NOW()
            """, (line_id, ctype, mach, started, txt, author, psycopg2.extras.Json(trail)))
            saved += 1
        conn.commit()

    # combined slot remark for the reports
    calls = [c for c in _slot_calls(line_id, lname, st, en) if c["bucket"] == lt]
    parts = [f"{c['start'][11:16]} {c['type']}{(' ' + c['machine']) if c['machine'] else ''}: {c['remark']}"
             for c in calls if c.get("remark")]
    combined = " | ".join(parts)[:2000]
    if combined:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT remark, entered_by, audit_trail FROM mes_loss_remarks
                            WHERE line_id=%s AND record_date=%s AND COALESCE(shift_name,'')=%s
                              AND slot_label=%s AND loss_type=%s""",
                        (line_id, date_s, sh or "", slot, lt))
            ex = cur.fetchone()
            if not ex or (ex.get("remark") or "") != combined:
                trail = list(ex["audit_trail"]) if ex and ex.get("audit_trail") else []
                if ex and ex.get("remark"):
                    trail.append({"remark": ex["remark"], "entered_by": ex.get("entered_by"),
                                  "replaced_at": _dt.utcnow().isoformat() + "Z"})
                cur.execute("""
                    INSERT INTO mes_loss_remarks
                           (line_id, record_date, shift_name, slot_label, loss_type,
                            remark, entered_by, audit_trail, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (line_id, record_date, shift_name, slot_label, loss_type)
                    DO UPDATE SET remark = EXCLUDED.remark, entered_by = EXCLUDED.entered_by,
                                  audit_trail = EXCLUDED.audit_trail, updated_at = NOW()
                """, (line_id, date_s, sh or "", slot, lt, combined, author,
                      psycopg2.extras.Json(trail)))
            conn.commit()
    return {"saved": saved, "slot_remark": combined, "calls": calls}
