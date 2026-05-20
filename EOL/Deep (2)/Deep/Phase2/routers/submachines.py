"""
submachines.py — API for per-line auxiliary (sub-machine) PLCs.

A sub-machine is a mes_plc_configs row whose parent_plc_id is set to the
main PLC of the same line.  The collector tracks only its count-bit and
computes per-cycle CT, writing into mes_submachine_ct_log.  Everything
else (model, shift, status) comes from the parent line, so these endpoints
are intentionally tiny and do NOT duplicate OEE/loss/plan logic.
"""

import os
from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse

from database import get_conn, dict_cursor
from auth import get_current_user


router = APIRouter(tags=["submachines"])


# NF2 Flask base URL (per-cycle trim endpoint lives there). Configurable
# via env var so deployments can point to a different host.
CYCLE_VIDEO_BASE_URL = os.environ.get(
    "CYCLE_VIDEO_BASE_URL", "http://127.0.0.1:5555"
).rstrip("/")


def _check_sub_belongs_to_line(sub_id: int, conn) -> dict:
    """Return the sub-machine row, or 404 if it doesn't exist / isn't a sub."""
    cur = dict_cursor(conn)
    # No hardcoded defaults — values come straight from the admin config.
    # If count_bit or ideal_ct were never configured the response will
    # surface them as NULL so the UI can tell the operator to fill them in.
    cur.execute("""
        SELECT s.id, s.plc_ip, s.plc_port,
               NULLIF(TRIM(s.ok_bit_address),  '') AS count_bit,
               s.ideal_cycle_time                 AS ideal_ct,
               s.machine_name,
               s.machine_seq,
               s.line_id,
               s.parent_plc_id,
               NULLIF(TRIM(s.nf2_camera_id), '')  AS nf2_camera_id,
               COALESCE(s.is_bottleneck, FALSE)   AS is_bottleneck,
               COALESCE(s.sa_enabled,     FALSE)  AS sa_enabled,
               l.line_name                        AS line_name,
               l.line_code                        AS line_code,
               z.zone_name                        AS zone_name
        FROM mes_plc_configs s
        LEFT JOIN mes_lines l ON l.id = s.line_id
        LEFT JOIN mes_zones z ON z.id = l.zone_id
        WHERE s.id = %s
    """, (sub_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Sub-machine not found")
    if not row.get("parent_plc_id"):
        raise HTTPException(400, "PLC is a main PLC, not a sub-machine")
    return dict(row)


# ── PUBLIC LIST: NF2-camera-ids belonging to ANY sub-machine ─────────
# NF2 Flask polls this so it can auto-skip per-cycle MP4 extraction for
# sub-machine cameras WITHOUT requiring an extract_per_cycle flag in
# camera_config_bindings.json. Adding a new sub-machine in admin UI is
# enough — no NF2 file edit needed.
@router.get("/api/sub-cameras")
def public_sub_cameras():
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT DISTINCT NULLIF(TRIM(nf2_camera_id), '') AS camera_id
            FROM mes_plc_configs
            WHERE parent_plc_id IS NOT NULL
              AND nf2_camera_id IS NOT NULL
              AND TRIM(nf2_camera_id) <> ''
        """)
        return {"camera_ids": [r["camera_id"] for r in cur.fetchall()]}


# ── LIST sub-machines for a line ──────────────────────────────────────
@router.get("/api/lines/{line_id}/submachines")
def list_submachines(line_id: int, user=Depends(get_current_user)):
    """
    Return every sub-machine configured under this line's main PLC,
    enriched with today's cycle count and last cycle CT.
    Frontend Dashboard.jsx uses this to render tiles.
    """
    with get_conn() as conn:
        cur = dict_cursor(conn)

        # Main PLC for this line
        cur.execute("""
            SELECT id FROM mes_plc_configs
            WHERE line_id = %s AND parent_plc_id IS NULL
            ORDER BY id LIMIT 1
        """, (line_id,))
        main_row = cur.fetchone()
        if not main_row:
            return []   # line has no main PLC yet
        main_plc_id = main_row["id"]

        # Pull sub-list + today's per-sub aggregates in ONE round-trip via
        # LATERAL JOIN.  Previously this ran 2*N queries (one shift-pick
        # + one aggregate per sub), which made the LAN dashboard buffer.
        # The semantics are identical — for each sub, pick the shift of
        # its latest row today, then aggregate today's rows in that shift.
        # 2026-05-18 perf — collapses N+1 to 1 query (no logic change).
        today = date.today()
        cur.execute("""
            SELECT p.id, p.plc_ip, p.plc_port, p.machine_name,
                   NULLIF(TRIM(p.ok_bit_address), '') AS count_bit,
                   p.ideal_cycle_time                 AS ideal_ct,
                   p.machine_seq,
                   COALESCE(p.is_bottleneck, FALSE)   AS is_bottleneck,
                   COALESCE(agg.cnt, 0)               AS today_count,
                   agg.avg_ct                         AS today_avg_ct,
                   COALESCE(agg.last_seq, 0)          AS last_cycle_seq,
                   agg.last_ts                        AS last_cycle_ts
            FROM mes_plc_configs p
            LEFT JOIN LATERAL (
                -- Step 1: pick the most recent shift seen today for this sub
                WITH last_row AS (
                    SELECT shift_name
                      FROM mes_submachine_ct_log
                     WHERE sub_plc_id = p.id AND record_date = %s
                     ORDER BY ts_end DESC NULLS LAST
                     LIMIT 1
                )
                -- Step 2: aggregate today's cycles in that shift
                SELECT COUNT(*)        AS cnt,
                       AVG(ct_seconds) AS avg_ct,
                       MAX(cycle_seq)  AS last_seq,
                       MAX(ts_end)     AS last_ts
                  FROM mes_submachine_ct_log l, last_row r
                 WHERE l.sub_plc_id   = p.id
                   AND l.record_date  = %s
                   AND l.shift_name   = r.shift_name
            ) agg ON TRUE
            WHERE p.parent_plc_id = %s
            ORDER BY COALESCE(p.machine_seq, 9999), p.id
        """, (today, today, main_plc_id))

        subs = []
        for r in cur.fetchall():
            d = dict(r)
            d["today_avg_ct"] = (
                round(float(d["today_avg_ct"]), 2)
                if d.get("today_avg_ct") is not None else None
            )
            d["last_cycle_seq"] = int(d.get("last_cycle_seq") or 0)
            d["today_count"]    = int(d.get("today_count") or 0)
            d["last_cycle_ts"]  = (
                d["last_cycle_ts"].isoformat() if d.get("last_cycle_ts") else None
            )
            subs.append(d)

        return subs


# ── CT history (graph data) ───────────────────────────────────────────
@router.get("/api/submachines/{sub_id}/ct-history")
def ct_history(
    sub_id: int,
    date_str: Optional[str] = Query(None, alias="date",
                                    description="YYYY-MM-DD, defaults today"),
    shift:    Optional[str] = None,
    limit:    int = 500,
    user=Depends(get_current_user),
):
    """
    Last N cycles for this sub-machine, used by the Fullscreen CT chart.
    Returns list of { cycle_seq, ts_start, ts_end, ct_seconds,
                      model_name, part_code }.

    2026-05-16 — Outlier cap: cycles whose CT > OUTLIER_CAP_MULT × ideal_ct
    are clamped down (their `ct_seconds_raw` is preserved so analytics
    can still see the original).  This handles two cases:
      • Pre-fix rows in DB where break time wasn't subtracted (one
        cycle spans tea/lunch and shows up as 700+ s).
      • Real long idle / breakdown windows where the spike is true
        but useless for chart scale.
    Default cap = 5× ideal_ct (so a 15 s ideal → 75 s ceiling).
    """
    with get_conn() as conn:
        sub = _check_sub_belongs_to_line(sub_id, conn)
        cur = dict_cursor(conn)
        record_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_str else date.today()
        )

        where  = "sub_plc_id = %s AND record_date = %s"
        params = [sub_id, record_date]
        if shift:
            where += " AND shift_name = %s"
            params.append(shift)

        cur.execute(f"""
            SELECT cycle_seq, ts_start, ts_end, ct_seconds,
                   shift_name, model_number, model_name, part_code
            FROM mes_submachine_ct_log
            WHERE {where}
            ORDER BY cycle_seq DESC
            LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()

    # Resolve cap from this sub-machine's ideal_ct.  Falls back to 75 s
    # (= 5 × 15 s default) if ideal isn't configured.
    try:
        ideal_ct = float(sub["ideal_ct"]) if sub.get("ideal_ct") else 0.0
    except Exception:
        ideal_ct = 0.0
    OUTLIER_CAP_MULT = 5.0
    cap = max(ideal_ct * OUTLIER_CAP_MULT, 60.0) if ideal_ct > 0 else 300.0

    # oldest first for easy plotting
    rows.reverse()
    out = []
    for r in rows:
        raw_ct = float(r["ct_seconds"]) if r.get("ct_seconds") is not None else 0.0
        clamped = min(raw_ct, cap) if raw_ct > 0 else 0.0
        out.append({
            "cycle_seq":     r["cycle_seq"],
            "ts_start":      r["ts_start"].isoformat() if r.get("ts_start") else None,
            "ts_end":        r["ts_end"].isoformat()   if r.get("ts_end")   else None,
            "ct_seconds":    clamped,
            "ct_seconds_raw": raw_ct,
            "ct_capped":     raw_ct > cap,
            "shift_name":    r["shift_name"],
            "model_number":  r["model_number"],
            "model_name":    r["model_name"],
            "part_code":     r["part_code"] or "",
        })
    return out


# ── Hourly target vs actual (aligned to MAIN line's slot boundaries) ─
@router.get("/api/submachines/{sub_id}/hourly")
def hourly_target_actual(
    sub_id: int,
    date_str: Optional[str] = Query(None, alias="date"),
    shift:    Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Bucket today's sub-machine cycles into the main line's hourly slot
    BOUNDARIES (same labels/clock windows so the dashboards stay aligned)
    but compute the slot's TARGET from THIS sub-machine's own
    `ideal_cycle_time`, not the main line's plan_pieces.

    2026-05-16 — Operator spec: "BS H MACHINE ka cycle time set kru or
    as per time auto plan ho ok".  Each sub-machine has its own
    ideal_ct in admin config; the target should reflect THAT machine's
    achievable count per slot, not the main line's plan.

    Math:
      working_min_per_slot = main_line_plan_pieces × main_ideal_ct / 60
      sub_target_per_slot  = working_min_per_slot × 60 / sub_ideal_ct
                           = main_plan × (main_ideal_ct / sub_ideal_ct)

    This preserves break-time handling (already baked into main line's
    plan_pieces) without needing a parallel break/working-min calc.
    """
    with get_conn() as conn:
        sub = _check_sub_belongs_to_line(sub_id, conn)
        cur = dict_cursor(conn)
        record_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_str else date.today()
        )

        # Slot boundaries (labels/clock) from main line; we'll RESCALE
        # the plan column to this sub-machine's ideal_ct below.
        cur.execute("""
            SELECT shift_name, slot_label, start_time, end_time,
                   crosses_midnight, plan_pieces, slot_order
            FROM mes_hourly_slots
            WHERE line_id = %s
            ORDER BY shift_name, slot_order
        """, (sub["line_id"],))
        slots = [dict(r) for r in cur.fetchall()]

        # Main line's ideal_ct — needed to back out working-min from
        # plan_pieces so we can re-multiply by sub's ideal_ct.  Falls
        # back to 15 s if not configured (matches the column default).
        cur.execute("""
            SELECT COALESCE(ideal_cycle_time, 15.0) AS ideal_cycle_time
            FROM mes_plc_configs
            WHERE line_id = %s AND parent_plc_id IS NULL
            ORDER BY id LIMIT 1
        """, (sub["line_id"],))
        _main = cur.fetchone()
        main_ideal_ct = float(_main["ideal_cycle_time"]) if _main else 15.0

        # Main line's shift_plan (raw total, also rescaled below)
        cur.execute("""
            SELECT shift_name, total_plan
            FROM mes_shift_configs
            WHERE line_id = %s
        """, (sub["line_id"],))
        shift_plans_main = {r["shift_name"]: int(r["total_plan"] or 0)
                            for r in cur.fetchall()}

        # Per-shift sub-machine cycles
        where  = "sub_plc_id = %s AND record_date = %s"
        params = [sub_id, record_date]
        if shift:
            where += " AND shift_name = %s"
            params.append(shift)

        cur.execute(f"""
            SELECT shift_name, ts_end, ct_seconds
            FROM mes_submachine_ct_log
            WHERE {where}
            ORDER BY ts_end
        """, params)
        cycle_rows = [dict(r) for r in cur.fetchall()]

    def _parse_t(v):
        if hasattr(v, "hour"):      # time/datetime
            return v.hour * 60 + v.minute
        if isinstance(v, str):
            h, m = v.split(":")[:2]
            return int(h) * 60 + int(m)
        return 0

    # Filter slots to active shift if specified
    active_shifts = {shift} if shift else {s["shift_name"] for s in slots}

    buckets = []
    # Sub-machine's own ideal CT — drives the per-slot target rescale.
    ideal_ct = float(sub["ideal_ct"]) if sub.get("ideal_ct") is not None else 0.0
    # Rescale factor: main_plan × (main_ideal_ct / sub_ideal_ct).
    # When sub is faster (smaller ideal_ct), factor > 1 → higher target.
    # When sub is slower (larger ideal_ct), factor < 1 → lower target.
    # Guard against zero/None sub-machine ideal_ct so we fall back to
    # main line's plan as-is (better than dividing by zero).
    rescale = (main_ideal_ct / ideal_ct) if ideal_ct > 0 else 1.0
    for s in slots:
        if s["shift_name"] not in active_shifts:
            continue
        ss = _parse_t(s["start_time"])
        se = _parse_t(s["end_time"])
        crosses = bool(s.get("crosses_midnight"))
        cnt  = 0
        ct_sum = 0.0
        for cy in cycle_rows:
            if cy["shift_name"] != s["shift_name"]:
                continue
            t = cy["ts_end"]
            t_min = t.hour * 60 + t.minute
            in_range = (
                (t_min >= ss or t_min < se) if crosses
                else (ss <= t_min < se)
            )
            if in_range:
                cnt     += 1
                ct_sum  += float(cy["ct_seconds"])
        avg_ct = round(ct_sum / cnt, 2) if cnt else 0.0
        sub_target = int(round((int(s["plan_pieces"] or 0)) * rescale))
        buckets.append({
            "slot_label":  s["slot_label"],
            "shift_name":  s["shift_name"],
            "target":      sub_target,
            "actual":      cnt,
            "avg_ct":      avg_ct,
        })

    total_target = sum(b["target"] for b in buckets)
    total_actual = sum(b["actual"] for b in buckets)

    # Rescale the shift_plan total too, otherwise the header total
    # (e.g. "shift plan 3720") still reflects main-line's count and
    # contradicts the per-slot rescaled targets below it.
    raw_shift_plan = (shift_plans_main.get(shift)
                      if shift else
                      sum(shift_plans_main.values()))
    shift_plan_rescaled = int(round(raw_shift_plan * rescale))

    return {
        "ideal_ct":     ideal_ct,
        "main_ideal_ct": main_ideal_ct,
        "buckets":      buckets,
        "total_target": total_target,
        "total_actual": total_actual,
        "shift_plan":   shift_plan_rescaled,
    }


# ── Cycle video (proxy to NF2 Flask per-cycle trim) ───────────────────
@router.get("/api/submachines/{sub_id}/cycle-video")
def cycle_video(
    sub_id:    int,
    cycle_seq: int = Query(..., description="cycle_seq from ct-history"),
    token:     Optional[str] = Query(None,
                  description="JWT fallback for <video src=...>"),
    request:   Request = None,
):
    """
    Look up ts_start/ts_end for (sub_id, cycle_seq) in
    mes_submachine_ct_log, then ask the NF2 camera backend to trim the
    shift-long TS recording from that camera into a cycle-sized MP4.
    Range headers are forwarded so HTML5 seeking works.
    """
    from auth import SECRET_KEY, ALGORITHM
    from jose import jwt as jose_jwt, JWTError as JoseJWTError

    # Accept JWT in header OR as ?token= (HTML5 video can't set headers).
    # 2026-05-18-r14 — Anonymous access allowed for wallboard kiosks
    # that never log in.  Validates iff token IS supplied, else skips.
    jwt_token = token
    if request:
        hdr = request.headers.get("authorization", "")
        if hdr.lower().startswith("bearer "):
            jwt_token = hdr[7:]
    if jwt_token:
        try:
            jose_jwt.decode(jwt_token, SECRET_KEY, algorithms=[ALGORITHM])
        except JoseJWTError:
            raise HTTPException(401, "Invalid or expired token")

    with get_conn() as conn:
        sub = _check_sub_belongs_to_line(sub_id, conn)
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT ts_start, ts_end
            FROM mes_submachine_ct_log
            WHERE sub_plc_id = %s AND cycle_seq = %s
            ORDER BY ts_end DESC LIMIT 1
        """, (sub_id, cycle_seq))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Cycle not found")
        ts_start = row["ts_start"]
        ts_end   = row["ts_end"]
        plc_ip   = sub["plc_ip"]   # canonical link to NF2's plcs.json/bindings

    # Forward Range header for seeking
    fwd_headers = {}
    if request is not None:
        rng = request.headers.get("range")
        if rng:
            fwd_headers["Range"] = rng

    # Use requests' params= so the "+" in tz offsets like "+05:30" gets
    # %-encoded properly. Hand-built f-strings were sending raw "+" which
    # Flask decoded back to a space → "Bad ISO timestamp" 400 errors.
    params = {
        "plc_ip":   plc_ip,
        "ts_start": ts_start.isoformat(),
        "ts_end":   ts_end.isoformat(),
    }
    nf2_cam = sub.get("nf2_camera_id")
    if nf2_cam:
        params["camera_id"] = nf2_cam
    upstream = f"{CYCLE_VIDEO_BASE_URL}/api/submachine/clip"
    try:
        r = requests.get(upstream, params=params,
                         headers=fwd_headers,
                         stream=True, timeout=30)
    except Exception as exc:
        raise HTTPException(502, f"Upstream unreachable: {exc}")

    if r.status_code >= 400:
        raise HTTPException(r.status_code,
                            f"Upstream: {r.text[:200]}")

    resp_headers = {}
    for h in ("Content-Type", "Content-Length",
              "Content-Range", "Accept-Ranges"):
        if h in r.headers:
            resp_headers[h] = r.headers[h]
    resp_headers.setdefault("Accept-Ranges", "bytes")
    # 2026-05-18 — Force no-cache so different cycle_seq URLs never get
    # served the same blob from the browser cache.  Without this the
    # operator saw "same video for every cycle" because Chrome was
    # treating the underlying StreamingResponse as cacheable.
    resp_headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp_headers["Pragma"]        = "no-cache"

    return StreamingResponse(
        r.iter_content(chunk_size=64 * 1024),
        status_code=r.status_code,
        media_type=resp_headers.get("Content-Type", "video/mp4"),
        headers=resp_headers,
    )


# ══════════════════════════════════════════════════════════════════════
# Semi-Auto data-log search (2026-05-14)
# ══════════════════════════════════════════════════════════════════════
# Every rising edge of a sub-machine's sa_fetch_bit (configured under
# Admin → Lines → Sub Machine → Semi-Auto section) writes one row into
# mes_submachine_data_log with the part_code + 1-N register values.
# This endpoint serves that history.  Filters: time range (from/to ISO)
# OR a date/shift pair OR partial part_code match.  Output: JSON list
# (default) or downloadable CSV when `format=csv`.

@router.get("/api/submachines/{sub_id}/data-log")
def submachine_data_log(
    sub_id:    int,
    from_:     Optional[str] = Query(None, alias="from",
                                description="ISO start timestamp (inclusive)"),
    to:        Optional[str] = Query(None,
                                description="ISO end timestamp (exclusive)"),
    date_str:  Optional[str] = Query(None, alias="date",
                                description="YYYY-MM-DD — alternative to from/to"),
    shift:     Optional[str] = Query(None,
                                description="A / B / C — filter by shift_name"),
    part_code: Optional[str] = Query(None,
                                description="Partial part-code match (ILIKE %code%)"),
    fmt:       str = Query("json", alias="format",
                                description="json | csv"),
    limit:     int = Query(2000, ge=1, le=20000),
    user=Depends(get_current_user),
):
    """List Semi-Auto data-capture rows for a sub-machine.

    Time filter precedence:
      1. from + to       — explicit range (any varying length the operator wants)
      2. date (+ shift)  — convenience filter for whole-shift queries
      3. neither         — last 24 h
    """
    with get_conn() as conn:
        sub = _check_sub_belongs_to_line(sub_id, conn)
        cur = dict_cursor(conn)
        # Pull the SA register names/scales so the response (and CSV
        # header) can use real human labels instead of "data_1..data_20".
        cur.execute("""
            SELECT machine_name, sa_register_names, sa_register_scales,
                   sa_data_addr, sa_data_len, sa_enabled
              FROM mes_plc_configs
             WHERE id = %s
        """, (sub_id,))
        cfg = cur.fetchone() or {}
        reg_names  = cfg.get("sa_register_names")  or []
        sa_data_len = int(cfg.get("sa_data_len") or 0)
        # Pad / trim names to length so CSV columns line up even when
        # admin only labelled some of the registers.
        names = []
        for i in range(sa_data_len):
            try:
                names.append(str(reg_names[i]) if reg_names[i] else f"data_{i+1}")
            except (IndexError, TypeError):
                names.append(f"data_{i+1}")

        where  = ["sub_plc_id = %s"]
        params = [sub_id]
        if from_ and to:
            where.append("ts_server >= %s AND ts_server < %s")
            params.extend([from_, to])
        elif from_:
            where.append("ts_server >= %s")
            params.append(from_)
        elif to:
            where.append("ts_server < %s")
            params.append(to)
        elif date_str:
            where.append("record_date = %s")
            params.append(date_str)
            if shift:
                where.append("shift_name = %s")
                params.append(shift.upper())
        else:
            # Default: last 24 hours
            where.append("ts_server >= NOW() - INTERVAL '24 hours'")
        if part_code:
            where.append("part_code ILIKE %s")
            params.append(f"%{part_code}%")

        cur.execute(f"""
            SELECT id, sub_plc_id, line_id, record_date, shift_name,
                   cycle_seq, ts_plc, ts_server, part_code,
                   model_number, model_name, data_values
              FROM mes_submachine_data_log
             WHERE {' AND '.join(where)}
             ORDER BY ts_server DESC
             LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()

    # ---- CSV branch -------------------------------------------------
    if fmt.lower() == "csv":
        import csv as _csv
        from io import StringIO
        buf = StringIO()
        w = _csv.writer(buf)
        header = ["ts_server", "ts_plc", "shift", "cycle_seq",
                  "part_code", "model_number", "model_name"] + names
        w.writerow(header)
        for r in rows:
            dv = r.get("data_values") or []
            scaled_by_i = {}
            for i, item in enumerate(dv):
                try:
                    scaled_by_i[i] = item.get("scaled") if isinstance(item, dict) else None
                except Exception:
                    scaled_by_i[i] = None
            row_out = [
                r["ts_server"].isoformat() if r.get("ts_server") else "",
                r["ts_plc"].isoformat()    if r.get("ts_plc")    else "",
                r.get("shift_name") or "",
                r.get("cycle_seq") or "",
                r.get("part_code") or "",
                r.get("model_number") or "",
                r.get("model_name") or "",
            ] + [scaled_by_i.get(i, "") for i in range(sa_data_len)]
            w.writerow(row_out)
        sub_name = (cfg.get("machine_name") or f"sub{sub_id}").replace(" ", "_")
        fname = f"semi_auto_{sub_name}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ---- JSON branch ------------------------------------------------
    return {
        "sub_id":      sub_id,
        "machine":     cfg.get("machine_name"),
        "register_names": names,
        "sa_data_len": sa_data_len,
        "count":       len(rows),
        "rows":        rows,
    }

