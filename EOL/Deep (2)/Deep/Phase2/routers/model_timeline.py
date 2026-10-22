"""
Per-cycle model CT (2026-10-12).

Operator, YWD-SS: "over target cycles 70% se upar … pahle inner chal raha tha
jiska ct 23 sec tha … ab outer chal raha hai jiska ct 14.4 hai toh jo inner
wale ct the wo saare over target ho gaye".  Every cycle has to be judged
against the CT of the model that made it, not the one running now.

mes_line_model_log is the line's model timeline: one row per model change
(written by the collector, _log_model_change; history back-filled from the
collector log and, before the model register worked, from the CT bands).  The
model of a cycle = the last row started at or before the cycle's timestamp.
Only lines that HAVE a timeline use this; every other line's queries are
unchanged.
"""
import time

_CACHE: dict = {}
_TTL_S = 60.0


def has_timeline(cur, line_id) -> bool:
    """True when the line has model-timeline rows (cached 60 s).  Runs inside a
    SAVEPOINT so a missing table never aborts the caller's transaction."""
    try:
        k = int(line_id)
    except (TypeError, ValueError):
        return False
    now = time.time()
    c = _CACHE.get(k)
    if c and now - c[0] < _TTL_S:
        return c[1]
    ok = False
    try:
        cur.execute("SAVEPOINT _mtl")
        cur.execute("SELECT to_regclass('mes_line_model_log') AS t")
        r = cur.fetchone()
        t = (r.get("t") if isinstance(r, dict) else (r[0] if r else None))
        if t:
            cur.execute("SELECT 1 AS x FROM mes_line_model_log WHERE line_id = %s LIMIT 1", (k,))
            ok = cur.fetchone() is not None
        cur.execute("RELEASE SAVEPOINT _mtl")
    except Exception:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT _mtl")
            cur.execute("RELEASE SAVEPOINT _mtl")
        except Exception:
            pass
        ok = False
    _CACHE[k] = (now, ok)
    return ok


def ideal_expr(ts_col: str, line_id: int, fallback: float) -> str:
    """SQL for the ideal CT of the model running at `ts_col`, falling back to
    `fallback` before the timeline starts.  line_id / fallback are inlined as
    numbers (int / float), never user text."""
    return ("COALESCE((SELECT m.ideal_ct FROM mes_line_model_log m "
            f"WHERE m.line_id = {int(line_id)} AND m.started_at <= {ts_col} "
            "ORDER BY m.started_at DESC LIMIT 1), "
            f"{float(fallback)!r})")
