"""Clear the collector singleton lock for ONE specific line before (re)launch.

Usage:
    _clear_stale_lock.py                      -> line_id = 2   (legacy YNC default)
    _clear_stale_lock.py <line_id>            -> that integer line
    _clear_stale_lock.py collector_xxx.py     -> reads line_id from the file's CONFIG

2026-07-01 — generalized for the multi-collector dynamic launcher.  The clear is
ALWAYS scoped to a SINGLE line_id (mes_collector_locks.line_id is the PK), so it
can NEVER delete another running collector's lock row.  The per-collector loop
calls this only right before (re)launching THAT collector, whose python child is
already dead at that moment, so its lock row is stale by definition (same
rationale as the original nuke-for-line-2 behaviour, now per-line).
"""
import sys
import os
import re
import psycopg2


def _resolve_line_id(arg):
    if arg is None:
        return 2                      # legacy default — identical to old behaviour
    arg = arg.strip().strip('"')
    if arg.isdigit():
        return int(arg)
    # Treat as a collector script path/name; read line_id from its CONFIG.
    path = arg
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), arg)
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r'"line_id"\s*:\s*(\d+)', txt)
        if m:
            return int(m.group(1))
    except Exception as e:
        print(f"[LAUNCHER] could not read line_id from {arg!r}: {e}")
    return None


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    line_id = _resolve_line_id(arg)
    if line_id is None:
        print(f"[LAUNCHER] line_id unresolved from {arg!r} — skipping lock clear "
              f"(collector engine will steal the stale lock after ~30s).")
        return
    try:
        # 2026-07-24 — use the configured DB (DB_HOST) instead of a hardcoded
        # 192.168.10.210, and a connect_timeout so an UNREACHABLE DB can never
        # HANG this launcher.  Root cause of "every line's collector down after
        # reboot": the app runs on the local DB now (127.0.0.1) but .210 was
        # hardcoded here AND had no timeout, so on a reboot with .210 offline
        # this DELETE blocked forever -> the never-die loop never reached the
        # collector -> all 13 lines dead.  Timeout=5s means worst case it just
        # skips the lock-clear (the engine steals a stale lock after ~30s).
        c = psycopg2.connect(
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "5432") or 5432),
            database=os.environ.get("DB_NAME", "energydb"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASS", "tbdi@123"),
            connect_timeout=5,
        )
        cur = c.cursor()
        cur.execute("DELETE FROM mes_collector_locks WHERE line_id = %s", (line_id,))
        c.commit()
        print(f"[LAUNCHER] cleared {cur.rowcount} lock row(s) for line_id={line_id}")
        c.close()
    except Exception as e:
        print(f"[LAUNCHER] lock cleanup skipped: {e}")


if __name__ == "__main__":
    main()
