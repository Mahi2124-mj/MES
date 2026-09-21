"""ddl_once.py — run a router's schema-ensure at most once per process.

2026-09-17.  Nearly every router has an `_ensure_tables()` that fires
`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` at
the top of each request handler.  A sweep found **244 handlers across 30
routers** doing it, and only one guarded.

`ADD COLUMN IF NOT EXISTS` does no work once the column exists, but Postgres
still takes an **ACCESS EXCLUSIVE lock on the table before it can find that
out**.  With a single uvicorn worker the requests queued and it merely cost
time.  With four workers two requests arrive together, each grabs the lock for
the same table from a different process, and they deadlock:

    psycopg2.errors.DeadlockDetected: deadlock detected
    Process A waits for AccessExclusiveLock ... blocked by process B
    Process B waits for AccessExclusiveLock ... blocked by process A

That is what was turning `/api/push/inbox` into a 500 — the Inbox, where the
guardian posts its hourly report.  Same root cause as the collector convoy
(see the collector_engine `_SA_SCHEMA_DONE` guard), one layer up.

Usage — decorate the ensure function, nothing else changes:

    from ddl_once import once

    @once
    def _ensure_tables():
        ...

The wrapped function runs on the FIRST call in each process and is skipped
afterwards.  If it raises, the flag is NOT set, so the next request retries —
a DB that was briefly unreachable at boot still gets its schema.
"""

import functools
import threading

_done: set[str] = set()
_lock = threading.Lock()


def once(fn):
    key = f"{fn.__module__}.{fn.__qualname__}"

    @functools.wraps(fn)
    def _wrapped(*a, **kw):
        if key in _done:
            return None
        with _lock:
            if key in _done:                 # another thread won the race
                return None
            out = fn(*a, **kw)               # may raise -> not marked done
            _done.add(key)
            return out

    _wrapped.__ddl_once__ = True
    return _wrapped


def reset(name: str | None = None) -> None:
    """Forget that an ensure has run — for tests, or after a manual schema drop."""
    with _lock:
        if name is None:
            _done.clear()
        else:
            _done.discard(name)
