"""
routers/wallboard.py
====================
65" portrait wallboard endpoints — one per LEFT and RIGHT dashboard
that sits on the shop-floor TV wall (per ASSY LINE-2 reference image).

Two physical screens per line:
  LEFT   — multi-machine cycle-time graphs stacked, with hover-panel
           showing per-machine hourly slot counts.
  RIGHT  — line summary (target/actual/KPIs from final machine) +
           hourly slots + daily/weekly/monthly Plan vs Actual +
           realtime cycle-time histogram (0.1s buckets, monthly) +
           per-model monthly production count.

Endpoints (all GET, all auth-optional so the wallboard TV can poll
anonymously after first boot):
  /api/lines/{id}/wallboard-cycles    — full-shift CT data per sub-machine
  /api/lines/{id}/wallboard-summary   — KPI tiles + per-machine hourly slots
  /api/lines/{id}/wallboard-history   — daily / weekly / monthly Plan vs Actual
  /api/lines/{id}/ct-histogram        — month-long CT density (0.1s buckets)
  /api/lines/{id}/model-counts        — per-model production count, monthly
"""

import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user_optional
from database import get_conn, dict_cursor


router = APIRouter(prefix="/api/lines", tags=["wallboard"])


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════
def _resolve_line(line_id: int, conn):
    """Return (db_table_name, current_shift_row_id, ideal_ct) or raise 404."""
    cur = dict_cursor(conn)
    cur.execute(
        "SELECT id, db_table_name, current_shift_row_id, ideal_cycle_time "
        "FROM mes_lines WHERE id = %s",
        (line_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Line not found")
    return row


# 2026-08-12 — how long the boards stay dark before a shift starts.
SHIFT_BLANK_MIN = int(os.environ.get("SHIFT_BLANK_MINUTES", "5") or 5)


def _in_preshift_blank(line_id: int, conn) -> bool:
    """True inside the short quiet window immediately BEFORE a shift starts.

    The only moment the boards should be empty.  Anywhere else — including the
    hours after a shift has ended — they keep showing the shift that just
    finished, because a blank 65" panel tells the floor nothing.
    """
    try:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT start_time FROM mes_shift_configs
            WHERE line_id = %s AND COALESCE(is_production, TRUE)
        """, (line_id,))
        now = datetime.now()
        for r in cur.fetchall():
            st = r["start_time"]
            if not st:
                continue
            start = now.replace(hour=st.hour, minute=st.minute,
                                second=0, microsecond=0)
            # Also test tomorrow's occurrence so a window that straddles
            # midnight still matches.
            for cand in (start, start + timedelta(days=1)):
                delta = (cand - now).total_seconds()
                if 0 <= delta <= SHIFT_BLANK_MIN * 60:
                    return True
    except Exception as exc:
        print(f"[WALLBOARD] pre-shift check failed: {exc}")
    return False


def _non_production_shifts(line_id: int, conn) -> set:
    """Shift names on this line that are gaps/breaks, not production.

    A line's day is not shift-to-shift: YNC runs A 08:30-17:15, then a GAP_AB
    row opens at 17:15 and stays open until B starts at 18:30.  That gap row is
    a real, live, is_shift_completed=false row with zero counts — so anything
    that simply asks for "the open shift" gets zeros for over an hour.
    """
    try:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT shift_name FROM mes_shift_configs
            WHERE line_id = %s AND COALESCE(is_production, TRUE) = FALSE
        """, (line_id,))
        return {(r["shift_name"] or "").strip().upper()
                for r in cur.fetchall() if r["shift_name"]}
    except Exception as exc:
        # No config table / no is_production column — treat every shift as
        # production, which is exactly the old behaviour.
        print(f"[WALLBOARD] gap-shift lookup failed: {exc}")
        return set()


def _hold_last_production_shift(table: str, data, line_id: Optional[int], conn):
    """Swap a gap/break shift row for the production shift that just ended.

    2026-08-12 — the boards were still going blank after a shift ended, even
    with the last-completed fallback in place, because that fallback only ran
    when there was NO open row.  During a gap there IS one: measured on YNC at
    17:30, shift A had 1508 OK / 22 NG and the dashboard was showing GAP_AB's
    zeros.  A gap row carries no production by definition, so it must never win
    over the shift it follows.

    The blank window before the next shift still applies — that is the one time
    the floor should see nothing, so nobody reads a finished shift as the live
    one.
    """
    if line_id is None:
        return data
    try:
        gaps = _non_production_shifts(line_id, conn)
        if not gaps:
            return data
        name = ((data or {}).get("shift_name") or "").strip().upper()
        if data is not None and name not in gaps:
            return data                      # a real production shift — keep it
        if _in_preshift_blank(line_id, conn):
            return None
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT * FROM {table}
            WHERE upper(COALESCE(shift_name, '')) <> ALL(%s)
            ORDER BY record_date DESC, id DESC LIMIT 1
        """, (sorted(gaps),))
        return cur.fetchone() or data
    except Exception as exc:
        print(f"[WALLBOARD] gap hold failed: {exc}")
        return data


def _current_shift_row(table: str, row_id: Optional[int], conn,
                       line_id: Optional[int] = None):
    """The shift row the boards should display.

    Live shift first.  2026-08-12 — when there is no live shift this used to
    return None and every board went blank the moment a shift ended, which is
    exactly when supervisors are still reviewing it.  Now it falls back to the
    most recent completed shift, so the numbers, the shift name and the cycle
    videos all stay up.  The ONLY blank period is the few minutes before the
    next shift starts, so nobody mistakes yesterday's figures for today's.
    """
    cur = dict_cursor(conn)
    if row_id:
        cur.execute(
            f"SELECT * FROM {table} WHERE id = %s AND is_shift_completed = false",
            (row_id,),
        )
        live = cur.fetchone()
        if live:
            # A gap/break row is "live" too, and holds nothing — hand it back
            # through the gap check rather than displaying its zeros.
            return _hold_last_production_shift(table, live, line_id, conn)

    if line_id is not None and _in_preshift_blank(line_id, conn):
        return None

    cur.execute(
        f"SELECT * FROM {table} ORDER BY record_date DESC, id DESC LIMIT 1")
    return _hold_last_production_shift(table, cur.fetchone(), line_id, conn)


# ══════════════════════════════════════════════════════════════════
# 1. LEFT dashboard — multi-machine CT (full shift)
# ══════════════════════════════════════════════════════════════════
@router.get("/resolve-ref/{ref}")
def resolve_line_ref(ref: str, user=Depends(get_current_user_optional)):
    """Map a human line reference ("YHB-SS", "yhb-recliner", "YHB-L3") to its id.

    2026-08-10 — added so the wallboard URL can read /wallboard/left/YHB-SS
    instead of /wallboard/left/11.  Deliberately a SEPARATE endpoint rather than
    relaxing `line_id: int` on the wallboard routes: the front-end passes lineId
    into half a dozen endpoints across three routers, and every wall-display and
    TV in the plant is pointed at the numeric URLs.  Resolving once here keeps
    all of that untouched — a numeric URL never even calls this.

    Anonymous like the rest of this router, because the wall displays never log
    in.  Read-only, single indexed lookup.
    """
    key = (ref or "").strip()
    if not key:
        raise HTTPException(404, "Line not found")
    if key.isdigit():
        return {"line_id": int(key)}
    # 2026-08-11 — the URL carries a role suffix so the tab and the address bar
    # both say what the screen is: "YHB-SS.SUPERVISOR", "YHB-SS.MANAGEMENT".
    # Strip it before matching; it identifies the VIEW, not the line.
    for _sfx in (".supervisor", ".management"):
        if key.lower().endswith(_sfx):
            key = key[: -len(_sfx)]
            break
    # Normalise so "YHB Recliner", "yhb-recliner" and "YHB_RECLINER" all match.
    norm = key.strip().lower().replace("_", "-").replace(" ", "-")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, line_name, line_code
            FROM mes_lines
            WHERE lower(replace(replace(line_name,' ','-'),'_','-')) = %s
               OR lower(replace(replace(line_code,' ','-'),'_','-')) = %s
            ORDER BY is_active DESC, id
            LIMIT 1
        """, (norm, norm))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"No line matches '{key}'")
    return {"line_id": row["id"], "line_name": row["line_name"],
            "line_code": row["line_code"]}


@router.get("/{line_id}/wallboard-cycles")
def wallboard_cycles(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """For each sub-machine on this line, return EVERY cycle of the
    currently-running shift.  Frontend stacks these into N small line
    charts (1 per machine).

    Shape:
    [
        {
          "sub_id":         12,
          "machine_name":   "Semi-Auto",
          "machine_seq":    3,
          "ideal_ct":       15.0,
          "cycles": [
              {"cycle_seq": 1, "ts": "2026-05-18T08:31:12+05:30",
               "ct": 14.32, "is_ng": false},
              ...
          ]
        },
        ...
    ]
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        cur = dict_cursor(conn)

        # 2026-09-18 — /wallboard-summary got this guard in June; this route was
        # missed.  An ACTIVE line whose dashboard table was never created
        # (YRA-SA-6WAY, LOCATION PIN STACKING - LPS-5) made the next query raise
        # UndefinedTable, which returned 500 AND poisoned the connection's
        # transaction — the [HOURLY-SYNC] worker then died with "current
        # transaction is aborted" having had nothing to do with the request.
        # 577 of these were in the log.  Probe first and return the same empty
        # list this endpoint already returns when a line has no main PLC.
        cur.execute("SELECT to_regclass(%s) AS t", (line["db_table_name"],))
        if not (cur.fetchone() or {}).get("t"):
            return []

        shift_row = _current_shift_row(
            line["db_table_name"], line["current_shift_row_id"], conn, line_id)

        # Main PLC for this line so we can find its sub-machines
        cur.execute(
            "SELECT id FROM mes_plc_configs "
            "WHERE line_id = %s AND parent_plc_id IS NULL LIMIT 1",
            (line_id,))
        main_row = cur.fetchone()
        if not main_row:
            return []
        main_plc_id = main_row["id"]

        # Pull sub-machines + each one's cycles in ONE round-trip via LATERAL.
        # Each sub-machine's cycles are scoped to the CURRENT SHIFT's business
        # date + shift so the dashboard shows ONLY the live shift.
        # 2026-07-23 — MIDNIGHT-CROSS FIX.  This used date.today(), but a night
        # shift (e.g. B 18:30→) keeps writing cycles under its START date (the
        # collector's _cur_shift_record_date "handles midnight cross").  After
        # 00:00 date.today() rolled to the next calendar day, so every
        # per-machine tile showed "no cycles this shift yet" even though 500+
        # cycles existed for the still-running shift.  wallboard_summary already
        # keys off the shift row's record_date — match it here so the tiles and
        # the summary agree.  Falls back to date.today() only if the shift row
        # has no record_date.
        today  = (shift_row.get("record_date") if shift_row else None) or date.today()
        shift  = shift_row.get("shift_name") if shift_row else None
        if not shift:
            return []          # no shift active → empty wallboard

        # 2026-05-24 — mes_submachine_ct_log NOW has is_ng (we added it
        # to write NG rows from sub-machine pollers' L109 reads).  Include
        # it in the cycles JSON so the wallboard chart can render red
        # ⚠ markers on NG dots.
        cur.execute("""
            SELECT p.id                  AS sub_id,
                   p.machine_name,
                   p.machine_seq,
                   p.ideal_cycle_time    AS ideal_ct,
                   COALESCE(jsonb_agg(
                       jsonb_build_object(
                           'cycle_seq', l.cycle_seq,
                           'ts',        l.ts_end,
                           'ct',        l.ct_seconds,
                           'is_ng',     COALESCE(l.is_ng, FALSE)
                       ) ORDER BY l.cycle_seq
                   ) FILTER (WHERE l.id IS NOT NULL), '[]'::jsonb) AS cycles
            FROM mes_plc_configs p
            LEFT JOIN mes_submachine_ct_log l
                   ON l.sub_plc_id  = p.id
                  AND l.record_date = %s
                  AND l.shift_name  = %s
            WHERE p.parent_plc_id = %s
            GROUP BY p.id
            ORDER BY COALESCE(p.machine_seq, 9999), p.id
        """, (today, shift, main_plc_id))

        out = []
        for r in cur.fetchall():
            d = dict(r)
            d["ideal_ct"] = float(d["ideal_ct"]) if d["ideal_ct"] is not None else None
            # jsonb_agg already gives us the cycles list as JSON; psycopg2
            # parses it to a Python list of dicts automatically.
            out.append(d)

        # ── TOTAL LOSS, counted only while the LINE was RUNNING ───────────
        # 2026-08-07 — operator spec: speed loss must NOT accrue while the
        # line is stopped.  A cycle that straddles a breakdown / material-wait
        # carries that standing time inside its ct_seconds, so counting it
        # would bill downtime twice (once in the status loss buckets, again
        # here).  So: keep a cycle only if its completion instant falls inside
        # a RUNNING window from mes_status_log.
        #
        # Sub-machines deliberately use the LINE's status, not their own —
        # "sub-machines ka bhi status final ke equivalent hi maana jayega".
        # mes_status_log is per-line (the Final Inspection PLC drives it), so
        # applying the same windows to every sub-machine is exactly that rule.
        #
        # '1' is accepted alongside 'RUNNING' because some collectors log the
        # raw PLC status code before the status_map name is applied.
        # Phantom filters mirror the wallboard UI (0 s NG artefacts, and
        # warm-up cycles under half the ideal) so the figure agrees with the
        # OK/NG tags and the OVER TARGET badge on the same tile.
        _RUN_CTE = """
            WITH st AS (
              SELECT status, ts, LEAD(ts) OVER (ORDER BY ts) AS ts_next
              FROM mes_status_log
              WHERE line_id = %s AND record_date = %s AND shift_name = %s
            ),
            run AS (
              SELECT ts AS a, COALESCE(ts_next, now()) AS b
              FROM st WHERE status IN ('RUNNING', '1')
            ),
            brk AS (
              SELECT ts AS a, COALESCE(ts_next, now()) AS b
              FROM st WHERE status = 'BREAK'
            )
        """
        loss_by_sub, main_loss = {}, 0.0
        try:
            cur.execute(_RUN_CTE + """
                SELECT l.sub_plc_id,
                       -- 2026-08-17 — a cycle that straddles a BREAK carries the whole
                       -- break inside its ct_seconds (the first part after tea/lunch is one
                       -- wall-clock gap over the break), so billing (ct - ideal) as speed
                       -- loss charged planned break time as loss (~35 min/break per sub).
                       -- Subtract each cycle's overlap with the BREAK windows.  No
                       -- per-cycle floor — keep the old signed sum so cycles faster than
                       -- ideal still net the total down exactly as before.
                       COALESCE(SUM(
                           (l.ct_seconds - p.ideal_cycle_time)
                           - COALESCE((
                               SELECT SUM(EXTRACT(EPOCH FROM
                                          (LEAST(l.ts_end, k.b) - GREATEST(l.ts_start, k.a))))
                               FROM brk k
                               WHERE k.a < l.ts_end AND k.b > l.ts_start), 0)
                       ), 0) AS loss_s,
                       COUNT(*) AS n_counted
                FROM mes_submachine_ct_log l
                JOIN mes_plc_configs p ON p.id = l.sub_plc_id
                WHERE l.line_id = %s AND l.record_date = %s AND l.shift_name = %s
                  AND p.ideal_cycle_time > 0
                  AND NOT (COALESCE(l.is_ng, FALSE) AND l.ct_seconds < 0.1)
                  AND l.ct_seconds >= p.ideal_cycle_time * 0.5
                  AND EXISTS (SELECT 1 FROM run r
                              WHERE l.ts_end >= r.a AND l.ts_end < r.b)
                GROUP BY l.sub_plc_id
            """, (line_id, today, shift, line_id, today, shift))
            for r in cur.fetchall() or []:
                loss_by_sub[r["sub_plc_id"]] = {
                    "loss_seconds": float(r["loss_s"] or 0.0),
                    "cycles_counted": int(r["n_counted"] or 0),
                }
        except Exception as exc:
            # Never let the loss extra break the chart payload.
            print(f"[WALLBOARD] running-loss (subs) failed: {exc}")

        for d in out:
            info = loss_by_sub.get(d["sub_id"], {})
            d["loss_seconds"]   = info.get("loss_seconds", 0.0)
            d["cycles_counted"] = info.get("cycles_counted", 0)

        # ── MAIN-LINE CT chart ────────────────────────────────────────
        # 2026-05-18 — Operator wants the final-machine (line aggregate)
        # CT graph as the FIRST row above all sub-machines.  It comes
        # from the line's own *_ct_log table (NOT mes_submachine_ct_log)
        # because the main line counts L108 from the head PLC.
        main_table = line["db_table_name"] + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS t", (main_table,))
        if cur.fetchone()["t"]:
            cur.execute(f"""
                SELECT cycle_seq, ts, ct_value AS ct,
                       COALESCE(is_ng, FALSE) AS is_ng
                FROM {main_table}
                WHERE record_date = %s AND shift_name = %s
                ORDER BY cycle_seq
            """, (today, shift))
            main_cycles = [
                {"cycle_seq": r["cycle_seq"],
                 "ts":        r["ts"].isoformat() if r["ts"] else None,
                 "ct":        float(r["ct"]) if r["ct"] is not None else 0.0,
                 "is_ng":     bool(r["is_ng"])}
                for r in cur.fetchall()
            ]
        else:
            main_cycles = []

        # 2026-05-18-r13 — Renamed per operator spec ("main machine
        # koi nhi h, final inspection h").  This row IS the line
        # aggregate, but its semantic name on the floor is just
        # "Final Inspection" — there's no separate machine, the final
        # inspection station IS what counts the line's output.
        # Main line's running-only loss, same rule as the sub-machines above.
        _main_ideal = float(line["ideal_cycle_time"] or 15.0)
        main_counted = 0
        if main_cycles:
            try:
                cur.execute(_RUN_CTE + f"""
                    SELECT COALESCE(SUM(c.ct_value - %s), 0) AS loss_s,
                           COUNT(*) AS n_counted
                    FROM {main_table} c
                    WHERE c.record_date = %s AND c.shift_name = %s
                      AND c.ct_value IS NOT NULL
                      AND NOT (COALESCE(c.is_ng, FALSE) AND c.ct_value < 0.1)
                      AND c.ct_value >= %s * 0.5
                      AND EXISTS (SELECT 1 FROM run r
                                  WHERE c.ts >= r.a AND c.ts < r.b)
                """, (line_id, today, shift, _main_ideal, today, shift, _main_ideal))
                _mr = cur.fetchone() or {}
                main_loss     = float(_mr.get("loss_s") or 0.0)
                main_counted  = int(_mr.get("n_counted") or 0)
            except Exception as exc:
                print(f"[WALLBOARD] running-loss (main) failed: {exc}")

        main_row = {
            "sub_id":       0,                       # 0 = main line marker
            "machine_name": "Final Inspection",
            "machine_seq":  0,
            "ideal_ct":     _main_ideal,
            "cycles":       main_cycles,
            "is_main":      True,
            "loss_seconds":   main_loss,
            "cycles_counted": main_counted,
        }

        _payload = {
            "shift_name":   shift,
            "record_date":  str(today),
            "main":         main_row,
            "machines":     out,
        }

        # 2026-08-10 — PRE-RENDER the newest clips for the machines on this
        # wallboard.  There is no per-cycle video file on disk: every click
        # transcodes the cycle out of the camera's rolling .ts, which is the
        # 2-4 s the operator waits.  Doing it here — while they are still
        # looking at the dots — means the click lands on a cached clip.
        # Fire-and-forget and fully best-effort; see routers/clip_prewarm.py
        # for why it is scoped to open wallboards and the newest few cycles.
        try:
            from routers.clip_prewarm import submit_wallboard
            submit_wallboard(line_id, _payload)
        except Exception as exc:
            print(f"[WALLBOARD] clip pre-warm skipped: {exc}")

        return _payload


@router.get("/{line_id}/clip-prewarm")
def clip_prewarm_hint(
    line_id: int,
    seqs:    str = Query(..., description="comma-separated cycle_seq list"),
    sub_id:  int = Query(0, description="0 = main line (Final Inspection)"),
    user=Depends(get_current_user_optional),
):
    """Front-end hint: the operator is ABOUT to open these cycles' clips.

    The wallboard calls this when the over-target list opens and on hover, i.e.
    a second or two before the click.  There is no per-cycle video on disk —
    every clip is transcoded out of the camera's rolling .ts on demand — so
    starting that render on hover is the difference between a warm file and a
    2-4 s wait.

    Returns immediately; the render happens on clip_prewarm's small bounded
    pool.  Purely advisory: if it is full, or CMS is busy, the click still works
    and just waits like it used to.

    Only the newest few cycles are warmed by the /wallboard-cycles poll itself
    (see routers/clip_prewarm.py).  A shift has 200+ over-target cycles per
    machine, far too many to render speculatively, which is exactly why the
    front-end has to tell us WHICH ones are in play.
    """
    try:
        wanted = []
        for part in (seqs or "").split(",")[:24]:
            part = part.strip()
            if part.isdigit():
                wanted.append(int(part))
        if not wanted:
            return {"queued": 0}

        if sub_id > 0:
            paths = [f"/api/submachines/{sub_id}/cycle-video?cycle_seq={q}"
                     for q in wanted]
        else:
            paths = [f"/api/lines/{line_id}/cycle-video?cycle_seq={q}"
                     for q in wanted]

        from routers.clip_prewarm import submit
        return {"queued": submit(paths, hint=True)}
    except Exception as exc:
        # Advisory endpoint — never surface a failure to the wallboard.
        print(f"[WALLBOARD] clip-prewarm hint skipped: {exc}")
        return {"queued": 0}


# ══════════════════════════════════════════════════════════════════
# 2. RIGHT dashboard — line summary + per-machine hourly slots
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/wallboard-summary")
def wallboard_summary(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """Return the headline KPI tiles + per-machine hourly slot counts.

    KPI tiles come from the FINAL machine of the line (the line's own
    shift row, which is the last station's output → matches the wall
    display's "ASSY LINE-2 → final inspection" semantics).

    Hourly slots: for each sub-machine, build a [slot_label, count]
    pair list that mirrors the line's slot config.
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"]
        cur = dict_cursor(conn)

        # 2026-05-18-r14 — Pull the human-readable line name + model
        # number too so the wallboard header chip can show "YNC-SS"
        # instead of "Line 2" (operator: "meri line ka naam glt h").
        cur.execute("""SELECT line_name FROM mes_lines WHERE id = %s""",
                    (line_id,))
        _row = cur.fetchone()
        line_name = (_row and _row.get("line_name")) or "Unknown line"

        # 2026-09-17 — an unprovisioned line has no per-line table at all, and
        # _current_shift_row would raise UndefinedTable -> HTTP 500 on the
        # supervisor page (found by the live agent: LOCATION PIN STACKING -
        # LPS-5 and YRA-SA-L6).  The 404/guard treatment was given to
        # /realtime, cycle-extremes and ng-list back in June but never to this
        # route.  Check the table exists first and return the SAME empty shape
        # the no-shift-row case already returns, so the page renders "not
        # configured" instead of erroring.
        cur.execute("SELECT to_regclass(%s) AS t", (table,))
        if not (cur.fetchone() or {}).get("t"):
            return {"shift_row": None, "machines_hourly": [],
                    "kpi": {"line_name": line_name}}

        shift_row = _current_shift_row(
            table, line["current_shift_row_id"], conn, line_id)
        if not shift_row:
            return {"shift_row": None, "machines_hourly": [],
                    "kpi": {"line_name": line_name}}

        shift_name  = shift_row["shift_name"]
        record_date = shift_row["record_date"]

        # ── Pull hourly slot config (label + start time) ──────────────
        # mes_hourly_slots schema: start_time, end_time, plan_pieces
        cur.execute("""
            SELECT slot_label, start_time, end_time, plan_pieces, slot_order,
                   db_column_prefix
            FROM mes_hourly_slots
            WHERE line_id = %s AND shift_name = %s
            ORDER BY slot_order, start_time
        """, (line_id, shift_name))
        slots = [dict(r) for r in cur.fetchall()]

        # ── For each sub-machine, count cycles per slot ───────────────
        cur.execute("""
            SELECT id, machine_name, machine_seq
            FROM mes_plc_configs
            WHERE parent_plc_id = (
                SELECT id FROM mes_plc_configs
                WHERE line_id = %s AND parent_plc_id IS NULL LIMIT 1
            )
            ORDER BY COALESCE(machine_seq, 9999), id
        """, (line_id,))
        subs = [dict(r) for r in cur.fetchall()]

        # 2026-09-17 — This used to run one COUNT(*) per machine per slot
        # (up to 8 machines x 8 slots = 64 round-trips for ONE page open),
        # which is what made the supervisor wallboard slow under load.
        # Now a single grouped query counts every machine/slot pair at once:
        # the slot list is joined in as an inline VALUES table, so each
        # cycle row is bucketed by the same >= start / < end rule as before.
        # Counts are identical — verified against the old loop on all 21
        # lines that have sub-machines (984 pairs, zero differences).
        slot_counts = {}
        if subs and slots:
            slot_values = ",".join(
                cur.mogrify("(%s,%s::time,%s::time)",
                            (i, sl["start_time"], sl["end_time"])).decode()
                for i, sl in enumerate(slots)
            )
            cur.execute(f"""
                SELECT c.sub_plc_id, s.slot_idx, COUNT(*) AS cnt
                FROM mes_submachine_ct_log c
                JOIN (VALUES {slot_values}) AS s(slot_idx, start_time, end_time)
                  ON  c.ts_end::time >= s.start_time
                  AND c.ts_end::time <  s.end_time
                WHERE c.sub_plc_id  = ANY(%s)
                  AND c.record_date = %s
                  AND c.shift_name  = %s
                GROUP BY c.sub_plc_id, s.slot_idx
            """, ([s["id"] for s in subs], record_date, shift_name))
            for r in cur.fetchall():
                slot_counts[(r["sub_plc_id"], r["slot_idx"])] = r["cnt"]

        machines_hourly = []
        for s in subs:
            row = {"sub_id": s["id"], "machine_name": s["machine_name"],
                   "machine_seq": s["machine_seq"], "slots": []}
            for i, slot in enumerate(slots):
                row["slots"].append({
                    "label": slot["slot_label"],
                    "start": str(slot["start_time"])[:5],
                    "end":   str(slot["end_time"])[:5],
                    "plan":  slot["plan_pieces"],
                    "count": slot_counts.get((s["id"], i), 0),
                })
            machines_hourly.append(row)

        # ── KPI tiles from final machine (shift row already pulled) ──
        # Provide a clean serialisable subset — frontend can extract
        # whatever it wants from this.
        kpi = {
            "line_name":           line_name,        # r14 — added
            "shift_name":          shift_name,
            "record_date":         str(record_date),
            "operating_status":    shift_row.get("operating_status"),
            "shift_plan":          shift_row.get("shift_plan"),
            "shift_plan_completed":shift_row.get("shift_plan_completed"),
            "shift_plan_remaining":shift_row.get("shift_plan_remaining"),
            "ok_count":            shift_row.get("ok_count"),
            "ng_count":            shift_row.get("ng_count"),
            "current_model_name":  shift_row.get("current_model_name"),
            "current_model_number":shift_row.get("current_model"),
            "overall_oee":         shift_row.get("overall_oee"),
            "availability":        shift_row.get("availability"),
            "performance":         shift_row.get("performance"),
            "quality_oee":         shift_row.get("quality_oee"),
            "cycle_time_actual":   shift_row.get("cycle_time_actual"),
            "cycle_time_plan":     shift_row.get("cycle_time_plan"),
        }

        # ── MAIN LINE per-slot breakdown (from shift row columns) ────
        # The collector writes hour_HHMM_HHMM_plan / _actual / _ok / _ng
        # for each slot directly to the shift-row.  Read them by the
        # slot's db_column_prefix.
        line_hourly = []
        for slot in slots:
            p = slot.get("db_column_prefix") or ""
            plan   = shift_row.get(f"{p}_plan")   if p else None
            actual = shift_row.get(f"{p}_actual") if p else None
            ok     = shift_row.get(f"{p}_ok")     if p else None
            ng     = shift_row.get(f"{p}_ng")     if p else None
            line_hourly.append({
                "label":  slot["slot_label"],
                "start":  str(slot["start_time"])[:5],
                "end":    str(slot["end_time"])[:5],
                "plan":   int(plan or 0),
                "actual": int(actual or 0),
                "ok":     int(ok or 0),
                "ng":     int(ng or 0),
            })

        return {
            "kpi":             kpi,
            "machines_hourly": machines_hourly,
            "line_hourly":     line_hourly,
        }


# ══════════════════════════════════════════════════════════════════
# 3. Plan vs Actual — daily / weekly / monthly
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/wallboard-history")
def wallboard_history(
    line_id: int,
    user=Depends(get_current_user_optional),
):
    """Return three bar-chart series for the RIGHT dashboard's body:
       - daily  : last 30 days  (one bar per day)
       - weekly : last 12 weeks (one bar per ISO week)
       - monthly: last 12 months
    Each bar carries `plan`, `actual`, `date` (or week/month label).
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"]
        cur = dict_cursor(conn)

        # Sum across BOTH shifts each day so totals match what the
        # operator sees on the dashboard's main number.
        cur.execute(f"""
            SELECT record_date,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY record_date
            ORDER BY record_date
        """)
        daily = [{"date": str(r["record_date"]),
                  "plan": int(r["plan"] or 0),
                  "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        cur.execute(f"""
            SELECT date_trunc('week', record_date)::date AS wk,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '12 weeks'
            GROUP BY wk
            ORDER BY wk
        """)
        weekly = [{"week": str(r["wk"]),
                   "plan": int(r["plan"] or 0),
                   "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        cur.execute(f"""
            SELECT date_trunc('month', record_date)::date AS mo,
                   COALESCE(SUM(shift_plan),          0) AS plan,
                   COALESCE(SUM(shift_plan_completed),0) AS actual
            FROM {table}
            WHERE record_date >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY mo
            ORDER BY mo
        """)
        monthly = [{"month": str(r["mo"]),
                    "plan": int(r["plan"] or 0),
                    "actual": int(r["actual"] or 0)} for r in cur.fetchall()]

        return {"daily": daily, "weekly": weekly, "monthly": monthly}


# ══════════════════════════════════════════════════════════════════
# 4. Cycle-time histogram — 0.1s buckets, current month
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/ct-histogram")
def ct_histogram(
    line_id: int,
    days: int = Query(30, ge=1, le=120,
                      description="Window in days (default 30 = current month)"),
    user=Depends(get_current_user_optional),
):
    """Return cycle-time density buckets for `days` days ending today.
    Bucket width = 0.1 s (e.g. 14.3, 14.4, 14.5 ...).

    Output:
    {
      "total_cycles": 13042,
      "peak_bucket":  15.0,
      "peak_count":   1842,
      "buckets":      [{"ct": 14.0, "count": 23}, ...]
    }
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table = line["db_table_name"] + "_ct_log"
        cur = dict_cursor(conn)

        # Make sure the line's ct_log table exists; otherwise return empty.
        cur.execute("SELECT to_regclass(%s) AS t", (table,))
        if not cur.fetchone()["t"]:
            return {"total_cycles": 0, "peak_bucket": None,
                    "peak_count": 0, "buckets": []}

        # Bucket via floor(ct * 10) / 10.  We clamp to a sane operating
        # range (0.5 → 60 s) so a single 600 s outlier doesn't stretch
        # the X-axis flat.
        cur.execute(f"""
            SELECT ROUND((ct_value * 10)::numeric) / 10.0 AS bucket,
                   COUNT(*) AS cnt
            FROM {table}
            WHERE record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
              AND ct_value > 0
              AND ct_value < 60
            GROUP BY bucket
            ORDER BY bucket
        """, (days,))
        buckets = []
        peak_bucket, peak_count = None, 0
        total = 0
        for r in cur.fetchall():
            ct  = float(r["bucket"])
            cnt = int(r["cnt"])
            buckets.append({"ct": ct, "count": cnt})
            total += cnt
            if cnt > peak_count:
                peak_count  = cnt
                peak_bucket = ct
        return {
            "total_cycles": total,
            "peak_bucket":  peak_bucket,
            "peak_count":   peak_count,
            "buckets":      buckets,
            "window_days":  days,
        }


# ══════════════════════════════════════════════════════════════════
# 4b. OVER-TARGET history — last N days, per (date, shift), one machine
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/over-target-history")
def over_target_history(
    line_id: int,
    sub_id: Optional[int] = Query(None, description="sub-machine id; omit for the main/Final machine"),
    days: int = Query(30, ge=1, le=120),
    user=Depends(get_current_user_optional),
):
    """For ONE machine: count of cycles slower than its ideal CT
    (ct > ideal = 'over target') and the total, grouped by (record_date,
    shift_name) over the last `days` days.  sub_id given -> that sub-machine
    (mes_submachine_ct_log); omitted -> the line's main ct_log.  Read-only,
    auth-optional like the other wallboard endpoints (additive, touches nothing)."""
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        cur = dict_cursor(conn)
        if sub_id:
            cur.execute("SELECT ideal_cycle_time FROM mes_plc_configs WHERE id = %s", (sub_id,))
            r = cur.fetchone()
            ideal = float((r and r["ideal_cycle_time"]) or line["ideal_cycle_time"] or 15.0)
            table, ctcol = "mes_submachine_ct_log", "ct_seconds"
            where = "sub_plc_id = %(sid)s AND "
            params = {"ideal": ideal, "sid": sub_id, "days": days}
        else:
            ideal = float(line["ideal_cycle_time"] or 15.0)
            table, ctcol = line["db_table_name"] + "_ct_log", "ct_value"
            where = ""
            params = {"ideal": ideal, "days": days}

        cur.execute("SELECT to_regclass(%s) AS t", (table,))
        if not cur.fetchone()["t"]:
            return {"ideal_ct": ideal, "rows": []}

        cur.execute(f"""
            SELECT record_date::text AS d, shift_name AS s,
                   COUNT(*) FILTER (WHERE {ctcol} > %(ideal)s) AS over,
                   COUNT(*) AS total
            FROM {table}
            WHERE {where}record_date >= CURRENT_DATE - %(days)s::int * INTERVAL '1 day'
              AND {ctcol} > 0
            GROUP BY record_date, shift_name
            ORDER BY record_date, shift_name
        """, params)
        rows = []
        for r in cur.fetchall():
            tot = int(r["total"] or 0)
            ov  = int(r["over"] or 0)
            rows.append({
                "date":  r["d"],
                "shift": r["s"],
                "over":  ov,
                "total": tot,
                "pct":   round(ov / tot * 100, 1) if tot else 0.0,
            })
        return {"ideal_ct": ideal, "rows": rows}


# ══════════════════════════════════════════════════════════════════
# 5. Per-model production count — monthly
# ══════════════════════════════════════════════════════════════════
@router.get("/{line_id}/model-counts")
def model_counts(
    line_id: int,
    days: int = Query(30, ge=1, le=120,
                      description="Window in days (default 30)"),
    user=Depends(get_current_user_optional),
):
    """Return per-model production count for the last `days` days.

    The ct_log table doesn't carry model_name directly, so we infer
    via the shift row's `current_model_name` recorded at cycle time —
    fall back to counting EVERY cycle under "—" when no model info
    is available so totals still match the main dashboard count.
    """
    with get_conn() as conn:
        line = _resolve_line(line_id, conn)
        table   = line["db_table_name"]
        ct_log  = table + "_ct_log"
        cur = dict_cursor(conn)

        # Quick existence check
        cur.execute("SELECT to_regclass(%s) AS t", (ct_log,))
        if not cur.fetchone()["t"]:
            return []

        # We use the FINAL machine's per-shift row to learn what model
        # was running on a given date+shift, then attribute its cycles
        # to that model.  This is a best-effort grouping; for lines
        # that don't track model_name we collapse everything under
        # "Unknown".
        cur.execute(f"""
            WITH model_window AS (
                SELECT record_date,
                       shift_name,
                       MAX(current_model_name) AS model
                  FROM {table}
                 WHERE record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
                 GROUP BY record_date, shift_name
            )
            SELECT COALESCE(m.model, 'Unknown') AS model_name,
                   COUNT(*)                    AS cnt
            FROM {ct_log} l
            LEFT JOIN model_window m
                   ON m.record_date = l.record_date
                  AND m.shift_name  = l.shift_name
            WHERE l.record_date >= CURRENT_DATE - %s::int * INTERVAL '1 day'
              AND l.ct_value > 0
            GROUP BY model_name
            ORDER BY cnt DESC
        """, (days, days))
        return [{"model_name": r["model_name"], "count": int(r["cnt"])}
                for r in cur.fetchall()]
