"""bg_leader.py — elect ONE process to run the API's background work.

2026-09-17.  The API serves the whole plant from a single uvicorn worker, and
under 30 simulated supervisors that process pins at ~104% of ONE core while 31
of the box's 64 cores sit idle — even `/api/auth/me`, a JWT decode with no DB
call, takes seconds.  More workers is the fix for that, but the API also runs a
lot of background work in-process:

  * from main.py's startup hooks — the clip archiver, the manpower / kanban /
    OEE / loss-line alarm sweeps, the PM mail sender, the weld + network +
    machine-master pollers, the schema migrations
  * started at IMPORT time inside routers — the breakdown-mail poller
    (breakdown_mail.py), the poka-yoke digest and the hourly slot report
    (poka_yoke.py)

With one worker that was safe by accident.  Raise the worker count and every
one of those runs once PER process: clips rendered N times, N copies of each
alert notification, N breakdown mails for the same slip.

So one process takes a Postgres SESSION-level advisory lock and does the
background work; the rest serve requests only.  Session-level means Postgres
drops the lock the instant that process dies, so a crashed leader is replaced
at the next boot with nothing to clean up by hand.

Set MES_BACKGROUND=0 to disable background work in a process entirely — used
when running a second instance for measurement so it cannot double up on the
live one.

Import-time callers must guard like this:

    from bg_leader import is_leader
    if is_leader():
        _start_worker()
"""

import os

_LOCK_KEY = 7180325          # arbitrary but fixed: "MES background"
_lock_conn = None            # held open for the life of the process, on purpose
_decided = None


def is_leader() -> bool:
    """True in exactly one API process.  Cached after the first call."""
    global _lock_conn, _decided
    if _decided is not None:
        return _decided

    if os.environ.get("MES_BACKGROUND", "1") == "0":
        _decided = False
        print("[BG] MES_BACKGROUND=0 — background workers disabled in this process",
              flush=True)
        return False

    try:
        import time
        import psycopg2
        from database import DB_CONFIG
        # 2026-09-19 — the lock connection is NAMED after this instance (the
        # uvicorn master = our parent pid), so a worker can tell who beat it.
        #
        # The election used to be one try.  On 19-Sep a restart relaunched the
        # workers while the OLD leader was still being killed; its session
        # still held the lock, all four new workers settled as request-only,
        # nothing retried, and every background job — the clip archiver, the
        # alarm sweeps, the mail and poka-yoke pollers — silently stopped until
        # the next restart (no clip archived on any line after 14:10).
        # Now: a lock held by a SIBLING of this instance means it has a leader
        # — give up at once.  A lock held by anyone else is the instance being
        # replaced, still dying — keep trying for up to a minute.
        me = f"mes-bg:{os.getppid()}"
        _lock_conn = psycopg2.connect(application_name=me, **DB_CONFIG)
        _lock_conn.autocommit = True
        cur = _lock_conn.cursor()
        deadline = time.time() + float(os.environ.get("MES_BG_ELECT_WAIT_S", "60"))
        while True:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
            _decided = bool(cur.fetchone()[0])
            if _decided:
                break
            cur.execute("""SELECT a.application_name FROM pg_locks l
                             JOIN pg_stat_activity a ON a.pid = l.pid
                            WHERE l.locktype = 'advisory' AND l.objid = %s AND l.granted""",
                        (_LOCK_KEY,))
            row = cur.fetchone()
            holder = (row[0] if row else "") or ""
            if holder == me or time.time() > deadline:
                if holder != me:
                    print(f"[BG] pid {os.getpid()}: lock still held by '{holder or '?'}' "
                          f"after the wait — NOT the background leader", flush=True)
                break
            time.sleep(2)
        if not _decided:
            _lock_conn.close()
            _lock_conn = None
    except Exception as exc:
        # DB unreachable at boot — keep the old single-process behaviour rather
        # than leaving the stack with no background work at all.
        print(f"[BG] leader election failed ({exc}) — running background work here",
              flush=True)
        _decided = True

    print(f"[BG] pid {os.getpid()} is "
          f"{'THE background leader' if _decided else 'a request-only worker'}",
          flush=True)
    return _decided
