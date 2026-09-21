"""
routers/hourly_sync.py

Fill the per-hour actual columns (hour_*_ok / _ng / _actual / _variance) from
the per-cycle ct_log for lines that count from a DATA REGISTER with no separate
count-bit.  Built 2026-08-26.

WHY:
  Register-mode lines (e.g. the Loop Pipe lines: ok counted from D101, no L108
  bit) increment the TOTAL correctly, and every part is logged in the line's
  `<table>_ct_log` (verified: correct per-hour distribution).  But the edge/
  delta path that feeds the collector's in-memory hourly buckets doesn't fire
  the same way for a pure-register line, so the dashboard's hour_*_actual
  columns stay empty while the cumulative total grows.  The operator sees
  "actual is cumulative, not hourly".

WHAT THIS DOES (and does NOT do):
  A background thread, every SYNC_EVERY_SEC, recomputes each hour slot's OK/NG
  straight from the ct_log (the source of truth) and writes ONLY the display
  columns hour_*_ok/_ng/_actual/_variance on the current shift's dashboard row.
  It NEVER touches ok_count / ng_count / the register mirror / OEE inputs / any
  counting logic — it only distributes the already-counted parts into the hour
  buckets.  Scoped to register-only main lines, so bit-mode lines (whose hourly
  columns are already correct) are never touched.
"""
import threading
import time as _time
import re
from datetime import datetime, date, timedelta, time as dtime

from database import get_conn, dict_cursor

SYNC_EVERY_SEC = 5    # near-live: fill the current slot within ~5 s of each part
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SLOT_RE  = re.compile(r"^hour_(\d{4})_(\d{4})(_ot)?_plan$")


def _register_only_lines(cur):
    """The lines whose hour_*_actual columns the collector's edge path can't
    fill and stay empty while the cumulative total grows — the **Loop Pipe**
    lines (count straight off D101, no count bit).  Deliberately scoped by
    db_table so no other line is ever touched: many lines are count_mode=
    'register' too but their hourly columns are already correct, and
    overwriting those from the ct_log could shift their OEE.  If another line
    is found to have the same empty-hourly symptom, add its table here."""
    cur.execute("""
        SELECT id AS line_id, db_table_name
          FROM mes_lines
         WHERE (db_table_name ILIKE 'loop_pipe%%'
                OR zone_id = 7)               -- Sub-Assembly lines: same empty/
                                              -- undercounted hourly symptom as
                                              -- Loop Pipe (edge path doesn't fill
                                              -- the per-hour columns; a mid-shift
                                              -- collector restart also resets the
                                              -- in-memory per-slot counters).
           AND COALESCE(is_active, TRUE) = TRUE
           AND COALESCE(db_table_name, '') <> ''
         ORDER BY id
    """)
    return cur.fetchall() or []


def _current_prod_shift(cur, line_id: int):
    """Return the production shift name live now (by wall clock), else None."""
    cur.execute("""SELECT shift_name, start_time, end_time, crosses_midnight
                     FROM mes_shift_configs
                    WHERE line_id=%s AND COALESCE(is_production,TRUE)=TRUE
                      AND shift_name NOT ILIKE 'GAP%%'""", (line_id,))
    now_t = datetime.now().time()
    for r in cur.fetchall() or []:
        s, e = r["start_time"], r["end_time"]
        if s is None or e is None:
            continue
        if r["crosses_midnight"]:
            if now_t >= s or now_t < e:
                return r["shift_name"]
        elif s <= now_t < e:
            return r["shift_name"]
    return None


def _sync_line(cur, line_id: int, db_table: str, rec_date: date, shift: str) -> int:
    if not db_table or not _TABLE_RE.match(db_table):
        return 0
    ct_tbl = f"{db_table}_ct_log"
    if not _TABLE_RE.match(ct_tbl):
        return 0
    # 2026-09-18 — Several ACTIVE lines have no tables at all (YSD-SA-4WAY,
    # Y17-SA-4WAY, NUT WELDING SA …).  Querying them raised UndefinedTable,
    # which poisoned the shared connection: the tick then logged "line NN
    # failed: current transaction is aborted" for every line after it and the
    # sync stopped filling slots.  Skip what does not exist instead of
    # discovering it by crashing.
    cur.execute("SELECT to_regclass(%s) AS d, to_regclass(%s) AS c",
                (db_table, ct_tbl))
    _t = cur.fetchone() or {}
    if not _t.get("d") or not _t.get("c"):
        return 0
    cur.execute(f"""SELECT * FROM {db_table}
                     WHERE record_date=%s AND shift_name=%s
                     ORDER BY id DESC LIMIT 1""", (rec_date, shift))
    row = cur.fetchone()
    if not row:
        return 0
    changed = 0
    for k in list(row.keys()):
        m = _SLOT_RE.match(k)
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        prefix = k[:-5]                     # strip "_plan"
        try:
            start = datetime.combine(rec_date, dtime(int(a[:2]), int(a[2:])))
            end   = datetime.combine(rec_date, dtime(int(b[:2]), int(b[2:])))
        except Exception:
            continue
        if end <= start:
            end += timedelta(days=1)
        # never count a window that hasn't started yet
        if start > datetime.now():
            continue
        try:
            cur.execute(f"""SELECT COUNT(*) AS tot,
                                   COUNT(*) FILTER (WHERE is_ng) AS ng
                              FROM {ct_tbl}
                             WHERE record_date=%s AND ts>=%s AND ts<%s""",
                        (rec_date, start, end))
            pc = cur.fetchone() or {}
        except Exception:
            continue
        tot = int(pc.get("tot") or 0)
        ng  = int(pc.get("ng") or 0)
        ok  = tot - ng
        plan = int(row.get(f"{prefix}_plan") or 0)
        # skip if already correct (avoid needless writes / churn)
        if (int(row.get(f"{prefix}_actual") or 0) == tot
                and int(row.get(f"{prefix}_ok") or 0) == ok
                and int(row.get(f"{prefix}_ng") or 0) == ng):
            continue
        cur.execute(f"""UPDATE {db_table} SET
                            {prefix}_ok=%s, {prefix}_ng=%s,
                            {prefix}_actual=%s, {prefix}_variance=%s,
                            updated_at=NOW()
                         WHERE id=%s""",
                    (ok, ng, tot, tot - plan, row["id"]))
        changed += 1

    # ── shift_plan_completed (realtime cumulative PLAN) ──────────────────
    # Loop Pipe lines leave shift_plan_completed=0: the collector's
    # _write_dashboard `planned` calc reads a shift-config total_plan that isn't
    # populated for these register lines, so the Fullscreen current-slot plan
    # (= shift_plan_completed − pastPlanSum) and the TOTAL both render 0 even
    # though the per-slot hour_*_plan columns are correct (collector
    # _realtime_slot_plan). The realtime cumulative plan is simply their SUM, so
    # write that here. PLAN is a target/display field — ok_count/ng_count/OEE are
    # untouched. Collector uses GREATEST() so it never lowers this value.
    #
    # 2026-09-18 — SAVEPOINT, because catching the error is not enough.  Lines
    # whose *_ct_log was never created (YSD-SA-4WAY, YRA-SA-6WAY, LPS-5 …) raise
    # UndefinedTable here; the except below swallowed it but psycopg2 left the
    # CONNECTION in a failed transaction, so every statement after it in the same
    # tick died with "current transaction is aborted" — 45 of those per log
    # window, and the whole sync stopped doing its job because of one
    # unprovisioned line.  A savepoint confines the damage to this one line.
    cur.execute("SAVEPOINT _plan_calc")
    try:
        # Realtime cumulative PLAN = full plan of every COMPLETED slot + the
        # CURRENT slot prorated by elapsed time, so the current hour's plan grows
        # LIVE through the hour (matches normal/Seat-Slider lines).  Only the
        # current shift's slots (a shift-A row can carry stale night/shift-B slot
        # plan values that must not inflate this).
        cur.execute("""SELECT db_column_prefix, start_time, end_time,
                              COALESCE(crosses_midnight, false) AS xm, plan_pieces
                         FROM mes_hourly_slots
                        WHERE line_id=%s AND shift_name=%s""", (line_id, shift))
        _slots = cur.fetchall()
        _now = datetime.now()
        plan_done = 0.0
        for s in _slots:
            p = s.get("db_column_prefix")
            if not p:
                continue
            try:
                ss = datetime.combine(rec_date, s["start_time"])
                se = datetime.combine(rec_date, s["end_time"])
            except Exception:
                continue
            if s["xm"] or se <= ss:
                se += timedelta(days=1)
            if _now >= se:
                # completed slot → its full plan (== the frontend's hour_*_plan)
                plan_done += int(row.get(f"{p}_plan") or 0)
            elif ss <= _now < se:
                # CURRENT slot → prorate the static plan by elapsed fraction
                dur = (se - ss).total_seconds()
                frac = min(1.0, max(0.0, (_now - ss).total_seconds() / dur)) if dur > 0 else 0.0
                plan_done += float(s.get("plan_pieces") or 0) * frac
            # future slot → 0
        plan_done = int(round(plan_done))
        _sp = int(row.get("shift_plan") or 0)
        if _sp:
            plan_done = min(plan_done, _sp)
        # Only shift_plan_completed — the Fullscreen PLAN row/total read this
        # (current-slot plan = shift_plan_completed − pastPlanSum; TOTAL =
        # shift_plan_completed). shift_plan_remaining is owned by the collector
        # (it rewrites it every 2 s). Collector GREATEST() never lowers ours.
        if int(row.get("shift_plan_completed") or 0) != plan_done:
            cur.execute(f"""UPDATE {db_table} SET
                                shift_plan_completed=%s, updated_at=NOW()
                             WHERE id=%s""",
                        (plan_done, row["id"]))
            changed += 1
    except Exception as _pe:
        # Undo ONLY what this block did; the connection stays usable.
        cur.execute("ROLLBACK TO SAVEPOINT _plan_calc")
        print(f"[HOURLY-SYNC] shift_plan_completed calc skipped for {db_table}: {_pe}")
    cur.execute("RELEASE SAVEPOINT _plan_calc")
    return changed


def _tick():
    with get_conn() as conn:
        cur = dict_cursor(conn)
        lines = _register_only_lines(cur)
        today = date.today()
        total = 0
        for ln in lines:
            sh = _current_prod_shift(cur, ln["line_id"])
            if not sh:
                continue
            try:
                total += _sync_line(cur, ln["line_id"], ln["db_table_name"], today, sh)
            except Exception as e:
                conn.rollback()
                print(f"[HOURLY-SYNC] line {ln['line_id']} failed: {e}")
        conn.commit()
        if total:
            print(f"[HOURLY-SYNC] filled {total} hour slot(s) from ct_log "
                  f"across {len(lines)} register-mode line(s)")


def start_hourly_sync():
    """Launch the background sync thread (called once from main.py startup)."""
    def _loop():
        print(f"[HOURLY-SYNC] worker started — every {SYNC_EVERY_SEC}s "
              f"(register-mode lines, ct_log -> hour_*_actual, counting untouched)")
        while True:
            try:
                _tick()
            except Exception as e:
                print(f"[HOURLY-SYNC] tick error: {e}")
            _time.sleep(SYNC_EVERY_SEC)
    threading.Thread(target=_loop, name="hourly-sync", daemon=True).start()
