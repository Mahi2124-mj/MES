"""
database.py — lazy connection pool + canonical DB config.
Pool startup pe nahi banta, pehli request pe banta hai.
DB down ho toh app start hoti rehti hai.

This module is the SINGLE SOURCE for DB credentials in the stack.
Other modules (collectors, plc_diag, scripts) import `DB_CONFIG` from
here instead of redefining their own copy.

Resolution order:
  1. Individual env vars (DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASS)
  2. Legacy hardcoded values — kept so existing on-prem installs keep
     working without an immediate .env update.
"""

import os
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "127.0.0.1"),   # 2026-07-24: local is source of truth now (.210 is off-box backup only). DB_HOST env still overrides.
    "port":     int(os.getenv("DB_PORT", "5432") or 5432),
    "database": os.getenv("DB_NAME",     "energydb"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASS",     "tbdi@123"),
    # 2026-06-14 — fail FAST when the DB is down so a request returns an error
    # in seconds instead of hanging for the OS-default ~minute+.  Lets the
    # login surface "Server not connected" promptly and the JSON write-buffer
    # detect the outage quickly.
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5") or 5),
}

_pool = None


def db_reachable(timeout: float = 2.0) -> bool:
    """Fast TCP probe of the DB host:port (no auth, no pool).  Used to skip
    DB-dependent startup work (migrations) and to gate the JSON write-buffer
    flush, so nothing blocks for the full connect timeout when the DB is down."""
    import socket
    try:
        s = socket.create_connection((DB_CONFIG["host"], DB_CONFIG["port"]), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        # 2026-05-18 — Pool bumped from 1..10 to 2..30.
        # Dashboard polls /realtime every 3s + /submachines every 10s per
        # line; 8-line YNC line × 2 endpoints = ~16 concurrent during burst,
        # which would block on the old 10-cap.  30 gives 2x headroom.
        # Postgres default max_connections=100, so well within budget.
        #
        # 2026-08-10 — TWO fixes after tracing intermittent HTTP 500s.
        #
        # 1. SimpleConnectionPool -> ThreadedConnectionPool.  uvicorn runs this
        #    app with ~120 worker threads; SimpleConnectionPool is explicitly
        #    NOT thread-safe (no lock around its free-list), so concurrent
        #    getconn/putconn could hand the same connection to two threads.
        #    ThreadedConnectionPool is the same API with proper locking.
        #
        # 2. maxconn 30 -> 100.  The 500s were 292x
        #       psycopg2.pool.PoolError: connection pool exhausted
        #    out of 296 total — 0.7% of requests, always on the endpoints the
        #    dashboards poll (/lines/{id}/realtime, /poka-yoke/live/{id},
        #    /ng-list).  17 lines x several endpoints x several open wallboards
        #    bursts past 30 easily, and psycopg2 raises immediately rather than
        #    queueing.  Postgres here allows 300 connections and only ~53 are
        #    in use across every app, so 100 is comfortably inside budget.
        #
        # 2026-09-17 — the cap is now per-PROCESS and settable, because the API
        # can run several uvicorn workers (see bg_leader.py).  Each worker
        # builds its OWN pool, so a hardcoded 100 x 4 workers = 400 requested
        # connections against a server whose max_connections is 300, with ~92
        # already held by the collectors — the 5th worker's burst would start
        # failing to connect.  DB_POOL_MAX is set alongside --workers in
        # start_everything.sh and restart_api.py; the default stays 100 so a
        # single-worker install behaves exactly as before.
        _pool = psycopg2.pool.ThreadedConnectionPool(
            int(os.getenv("DB_POOL_MIN", "2") or 2),
            int(os.getenv("DB_POOL_MAX", "100") or 100),
            **DB_CONFIG)
    return _pool

@contextmanager
def get_conn():
    pool = _get_pool()          # keep reference to the pool
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)      # use the same pool reference

def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)