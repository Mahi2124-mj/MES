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
    "host":     os.getenv("DB_HOST",     "192.168.10.210"),
    "port":     int(os.getenv("DB_PORT", "5432") or 5432),
    "database": os.getenv("DB_NAME",     "energydb"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASS",     "tbdi@123"),
}

_pool = None

def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        # 2026-05-18 — Pool bumped from 1..10 to 2..30.
        # Dashboard polls /realtime every 3s + /submachines every 10s per
        # line; 8-line YNC line × 2 endpoints = ~16 concurrent during burst,
        # which would block on the old 10-cap.  30 gives 2x headroom.
        # Postgres default max_connections=100, so well within budget.
        _pool = psycopg2.pool.SimpleConnectionPool(2, 30, **DB_CONFIG)
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