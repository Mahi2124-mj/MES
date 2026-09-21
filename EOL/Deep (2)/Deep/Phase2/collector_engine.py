"""
collector_engine.py
===================
Fully dynamic production data collector.
"""

import time
import statistics
import threading
import traceback
import requests
from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional

import pymcprotocol
import psycopg2
import psycopg2.extras

# DB connection — pulled from the canonical config in database.py so
# credentials live in ONE place (env-driven with legacy fallbacks).  We
# add the connect_timeout the collector needs on top.
import os as _os_db
try:
    from database import DB_CONFIG as _BASE_DB_CONFIG
    DB_CONFIG = {**_BASE_DB_CONFIG, "connect_timeout": 5}
except Exception:
    # Standalone-script fallback: env vars → legacy literal.
    DB_CONFIG = {
        "host":     _os_db.getenv("DB_HOST",     "192.168.10.210"),
        "port":     int(_os_db.getenv("DB_PORT", "5432") or 5432),
        "database": _os_db.getenv("DB_NAME",     "energydb"),
        "user":     _os_db.getenv("DB_USER",     "postgres"),
        "password": _os_db.getenv("DB_PASS",     "tbdi@123"),
        "connect_timeout": 5,
    }

# Backend URL — collector POSTs PY events / sensor sweeps / health updates
# here.  Resolution order:
#   1. BACKEND_URL  (full URL, wins)
#   2. BACKEND_HOST + BACKEND_PORT (default port 8080)
#   3. legacy fallback http://127.0.0.1:8080  (dev / single-host install)
import os as _os

# 2026-08-01 — lines whose NG register uses the STRICT garbage guard.
# Rolled out on operator sign-off: Y17-L7 (15) first, then all the seat-slider
# register-NG lines that the 10-min monitor showed flickering NG down to 0 (and
# on Y17, a pipelined-frame mis-read of the OK register ~300).  The non-SS
# register lines (6 YWD-SS, 7 GEAR_LIFTER, 8 2UA, 10 YWD_REC, 20 YMC-L5,
# 21 Loop Pipe) read NG cleanly for the whole 10 min, so they are deliberately
# LEFT OUT — the guard only ever HOLDS a value, so applying it where it isn't
# needed can only add risk.  Env override: NG_STRICT_LINES="2,4,11,...".
_NG_STRICT_LINES = {int(x) for x in
                    _os.getenv("NG_STRICT_LINES",
                               "2,4,11,12,13,14,15,18,19").split(",")
                    if x.strip()}

BACKEND_URL = (
    _os.getenv("BACKEND_URL")
    or f"http://{_os.getenv('BACKEND_HOST','127.0.0.1')}:{_os.getenv('BACKEND_PORT','8080')}"
)


def _db_conn():
    return psycopg2.connect(**DB_CONFIG)


# ════════════════════════════════════════════════════════════════════
# DB-DOWN RESILIENCE — boot from cache (2026-06-14)
# ════════════════════════════════════════════════════════════════════
# The collector's __init__ pulls its line config + sub-machine list from
# the DB, and the singleton lock is DB-backed.  When the DB was down the
# whole process used to crash in __init__ — BEFORE it ever reached the PLC
# or the live monitor — so a running machine produced no live data and no
# buffered data.  These helpers let it boot from the last-good cached
# config/subs + a local file-lock, so it keeps reading the PLC; the DB lock
# and writes re-establish automatically on reconnect.  The cache self-seeds
# on every successful DB load (so the DB must be reachable at least once).
import pickle as _pickle


def _db_reachable(timeout: float = 2.0) -> bool:
    """Fast TCP probe of the DB host:port so DB-dependent startup (lock /
    config load) can fall back to cache instead of blocking on the full
    connect timeout."""
    import socket as _s
    try:
        _c = _s.create_connection((DB_CONFIG["host"], DB_CONFIG["port"]), timeout=timeout)
        _c.close()
        return True
    except OSError:
        return False


def _cache_dir() -> str:
    # Durable location for the DB-down write buffer (wbuf_line*.jsonl) + caches.
    # COLLECTOR_CACHE_DIR (set by start_everything.sh) points this at the 22 TB
    # data disk so the buffer lives on dedicated storage.  SAFETY: we only honour
    # the override when its parent is an actual mount point — if the data disk is
    # not mounted, makedirs would silently create the dir on the root fs and the
    # "durable" buffer would live on the wrong volume.  In that case we fall back
    # to the local dir beside this file, so buffering degrades gracefully instead
    # of pointing at phantom storage.
    override = _os.getenv("COLLECTOR_CACHE_DIR", "").strip()
    if override:
        override = override.rstrip("/")
        # The data disk carries a ".disk_ok" marker in the parent (eol-data) dir.
        # udisks/fstab removes the mount point contents when the disk is absent,
        # so the marker vanishes if it isn't mounted.  Using the override only
        # when the marker is present guarantees we never write the "durable"
        # buffer onto the root fs by mistake.
        marker = _os.path.join(_os.path.dirname(override), ".disk_ok")
        if _os.path.exists(marker):
            try:
                _os.makedirs(override, exist_ok=True)
                return override
            except Exception:
                pass  # fall through to local
    d = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_collector_cache")
    try:
        _os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _cache_save(name: str, obj) -> None:
    """Best-effort pickle of a DB-derived object so the collector can boot
    when the DB is down.  Never raises — a cache failure must not break the
    live (DB-up) path."""
    try:
        with open(_os.path.join(_cache_dir(), name), "wb") as f:
            _pickle.dump(obj, f)
    except Exception as _e:
        print(f"[CACHE] save {name} failed (non-fatal): {_e}")


def _cache_load(name: str):
    """Return the cached object, or None if missing/corrupt."""
    try:
        p = _os.path.join(_cache_dir(), name)
        with open(p, "rb") as f:
            obj = _pickle.load(f)
        age_min = (time.time() - _os.path.getmtime(p)) / 60.0
        print(f"[CACHE] DB down — loaded {name} from cache "
              f"(last good {age_min:.0f} min ago)")
        return obj
    except FileNotFoundError:
        return None
    except Exception as _e:
        print(f"[CACHE] load {name} failed: {_e}")
        return None


# ════════════════════════════════════════════════════════════════════
# JSON WRITE-BUFFER — zero data-loss for DB-down event writes (2026-06-16)
# ════════════════════════════════════════════════════════════════════
# Operator: "jo data db me jana h, db disconnect ho jaye to miss na ho;
# db connect ho to json se db me sync ho — data miss nahi hona at any cost."
#
# SCOPE (phase 1 — provably double-count-SAFE): only the per-EVENT history
# writes that are 100% LOST today on a DB outage AND have NO other recovery
# path, so a later replay can NEVER double a count:
#   mes_status_log, mes_machine_process_log/_pulses, mes_submachine_data_log.
# The per-CYCLE COUNT tables (ct_log / mes_l6_* / mes_submachine_ct_log) are
# DELIBERATELY EXCLUDED: their counts already self-heal on reconnect via the
# register-mirror backfill + in-memory _ct_pending_log, so buffering+replaying
# them would DOUBLE-count (the exact regression to avoid).  Those need a
# separate, register-backfill-coordinated stage.
#
# Mechanism: a write that fails because the DB is UNREACHABLE is type-tagged,
# serialized, and appended to a per-line append-only JSONL queue under
# _cache_dir().  On the DB-up rising edge the MAIN loop replays the queue
# EXACTLY-ONCE through a dedup ledger (mes_collector_replay_log, rec_id PK),
# truncating only records that fully committed.  The DB-UP hot path is
# unchanged — the buffer code runs ONLY on the failure branch.
import json as _json_wbuf
import uuid as _uuid_wbuf
from decimal import Decimal as _Decimal_wbuf


def _wbuf_enc(p):
    """Type-tagged encode of ONE bound param so the DB gets the correct type
    back on replay (psycopg2 binds Python types).  Fails LOUD on an unknown
    type — never silently corrupt."""
    if p is None:                        return {"t": "none"}
    if isinstance(p, bool):              return {"t": "py",  "v": p}   # before int!
    if isinstance(p, (int, float, str)): return {"t": "py",  "v": p}
    if isinstance(p, datetime):          return {"t": "dt",  "v": p.isoformat()}
    if isinstance(p, date):              return {"t": "date","v": p.isoformat()}
    if isinstance(p, dt_time):           return {"t": "time","v": p.isoformat()}
    if isinstance(p, _Decimal_wbuf):     return {"t": "dec", "v": str(p)}
    if isinstance(p, psycopg2.extras.Json):
        return {"t": "json", "v": p.adapted}      # the underlying list/dict
    raise TypeError(f"[WBUF] unbufferable param type {type(p).__name__}")


def _wbuf_dec(o):
    t = o["t"]
    if t == "none": return None
    if t == "py":   return o["v"]
    if t == "dt":   return datetime.fromisoformat(o["v"])
    if t == "date": return date.fromisoformat(o["v"])
    if t == "time": return dt_time.fromisoformat(o["v"])
    if t == "dec":  return _Decimal_wbuf(o["v"])
    if t == "json": return psycopg2.extras.Json(o["v"])   # RE-WRAP for JSONB
    raise ValueError(f"[WBUF] unknown encoded param tag {t!r}")


def _wbuf_enc_params(params, many):
    if many:
        return [[_wbuf_enc(x) for x in row] for row in params]
    return [_wbuf_enc(x) for x in params]


def _wbuf_dec_params(rec):
    P = rec["params"]
    if rec.get("many"):
        return [tuple(_wbuf_dec(x) for x in row) for row in P]
    return [_wbuf_dec(x) for x in P]


class _WriteQueue:
    """Append-only JSONL durable queue for failed DB writes — one file per
    line_id under _cache_dir().  Thread-safe (single lock) so the main loop
    and every sub-machine poller thread can append concurrently without
    tearing a line.  Survives a process restart (on disk)."""

    def __init__(self, line_id):
        self.path = _os.path.join(_cache_dir(), f"wbuf_line{line_id}.jsonl")
        self.lock = threading.Lock()
        try:
            self._pending = _os.path.exists(self.path) and _os.path.getsize(self.path) > 0
        except OSError:
            self._pending = False

    def append(self, sql, params, many):
        """Durably append a failed write.  Returns True if it reached disk,
        False (LOUD) only if even the buffer failed (real data-loss signal)."""
        try:
            rec = {"id": str(_uuid_wbuf.uuid4()), "ts": time.time(),
                   "sql": sql, "many": bool(many),
                   "params": _wbuf_enc_params(params, many)}
            line = _json_wbuf.dumps(rec) + "\n"
        except Exception as _e:
            print(f"[WBUF] ENCODE FAILED — row LOST: {_e}", flush=True)
            return False
        with self.lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    _os.fsync(f.fileno())
                self._pending = True
                return True
            except Exception as _e:
                print(f"[WBUF] APPEND FAILED — row LOST: {_e}", flush=True)
                return False

    def has_pending(self):
        return self._pending

    def _read_all_nolock(self):
        if not _os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(_json_wbuf.loads(ln))
                except Exception:
                    # Torn/partial trailing line — keep the rest durable.
                    print("[WBUF] skipping malformed queue line", flush=True)
        return out

    def read_all(self):
        with self.lock:
            return self._read_all_nolock()

    def commit_progress(self, done):
        """Drop the first `done` fully-committed records; keep the rest
        (incl. any appended during replay).  Atomic via tmp + os.replace."""
        if done <= 0:
            return
        with self.lock:
            recs = self._read_all_nolock()
            rest = recs[done:]
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rest:
                    f.write(_json_wbuf.dumps(r) + "\n")
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp, self.path)
            self._pending = bool(rest)

    def deadletter(self, rec):
        """Park a poison record (a data error that would wedge replay) so it
        never blocks the queue or silently vanishes."""
        try:
            with open(self.path + ".deadletter", "a", encoding="utf-8") as f:
                f.write(_json_wbuf.dumps(rec) + "\n")
        except Exception:
            pass


# Module singleton — set once line_id is known (CollectorEngine.__init__).
_QUEUE = None


def _init_write_queue(line_id):
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = _WriteQueue(line_id)
    return _QUEUE


def _ensure_replay_ledger(conn) -> None:
    """Dedup ledger so a queued record replays EXACTLY-ONCE even across a
    crash mid-replay.  rec_id PK + ON CONFLICT DO NOTHING in the SAME txn as
    the data write = atomic exactly-once."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_collector_replay_log (
            rec_id     UUID PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()


def _buffered_exec(conn, sql, params, *, executemany=False):
    """Wrap a write on an EXISTING connection (self._db sites).  DB-up path is
    identical to today (execute/executemany + commit).  ONLY a connection-level
    failure diverts the row to the durable queue; a real DATA error re-raises
    so it surfaces (a bad row must never replay-loop forever).
    Returns True = written-or-durably-queued, False = even the buffer failed."""
    if conn is None:
        # caller's connection was already cleared (DB down) — queue directly.
        if _QUEUE is not None:
            return _QUEUE.append(sql, params, executemany)
        return False
    try:
        cur = conn.cursor()
        if executemany:
            cur.executemany(sql, params)
        else:
            cur.execute(sql, params)
        conn.commit()
        cur.close()
        return True
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        try: conn.rollback()
        except Exception: pass
        if _QUEUE is not None:
            return _QUEUE.append(sql, params, executemany)
        return False


def _buffered_exec_own(sql, params, *, executemany=False):
    """Wrap a write that opens its OWN short-lived connection (sub-machine /
    audit sites).  If the connect itself fails (the common outage case) the
    row goes straight to the queue without a connection."""
    if not _db_reachable(timeout=1.5):
        # Fast TCP probe avoids a full 5 s connect_timeout block on every
        # buffered write while the DB is down (these run off the 30 ms loop).
        if _QUEUE is not None:
            return _QUEUE.append(sql, params, executemany)
        return False
    try:
        c = _db_conn()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        if _QUEUE is not None:
            return _QUEUE.append(sql, params, executemany)
        return False
    try:
        return _buffered_exec(c, sql, params, executemany=executemany)
    finally:
        try: c.close()
        except Exception: pass


# ════════════════════════════════════════════════════════════════════
# CROSS-PC SINGLETON LOCK
# ════════════════════════════════════════════════════════════════════
# Operator's pain point: "I run the frontend from another PC over LAN,
# and somehow the collector behaves wrong."
#
# Root cause: when a 2nd machine (laptop / supervisor PC) accidentally
# starts the collector_<line>.py launcher too, BOTH instances open MC
# protocol sockets to the PLC.  The Mitsubishi PLC then sets its
# "remote-active" flag (bit 4 of D6005 = decimal 16) and our status
# enum gets corrupted.  Even if we mask the flag (`& 0x0F`), the
# duplicate writes to `mes_dashboard_*` and `mes_breakdowns` race each
# other and produce ghost rows.
#
# Fix: a DB-backed lock keyed on line_id.  Each collector writes a
# heartbeat row; on startup another would-be collector sees the fresh
# heartbeat and refuses to run.  Cross-PC because everyone shares the
# same Postgres.
#
#   Table  : mes_collector_locks
#   Columns: line_id PK, hostname, pid, heartbeat_at
#
# Heartbeat refresh interval: 10 s.  A lock is considered STALE if its
# heartbeat is older than 30 s — that means the previous collector
# crashed and we can safely steal the slot.
# ════════════════════════════════════════════════════════════════════

import socket as _socket_for_lock

_HEARTBEAT_INTERVAL_SEC = 10
_HEARTBEAT_STALE_AFTER_SEC = 30


def _ensure_collector_lock_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_collector_locks (
            line_id      INTEGER     PRIMARY KEY,
            hostname     TEXT        NOT NULL,
            pid          INTEGER     NOT NULL,
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()


class CollectorSingletonLock:
    """Cross-PC lock for one line.  Use as a normal object — call
    .acquire() at startup (raises RuntimeError if another fresh
    collector holds it), and .release() on shutdown.

    The acquire path also spins a daemon thread that refreshes the
    heartbeat every _HEARTBEAT_INTERVAL_SEC so a stuck collector
    eventually loses the lock to a fresh restart.
    """

    def __init__(self, line_id: int):
        self.line_id  = line_id
        self.hostname = _socket_for_lock.gethostname()
        self.pid      = _os.getpid()
        self._stop    = threading.Event()
        self._thread  = None
        # True once the DB-backed cross-PC lock is held; False while running
        # on the local file-lock fallback (DB was down at acquire time).  The
        # heartbeat loop flips it True again once the DB comes back.
        self._db_locked = False

    def acquire(self) -> None:
        # 2026-06-14 — DB-down resilience.  The cross-PC lock is DB-backed, but
        # an unreachable DB must NOT stop the collector from booting, reading
        # the PLC and buffering data.  When the DB is down we fall back to a
        # local same-PC file-lock (guards the realistic double-launch on this
        # machine) and re-establish the real cross-PC lock automatically once
        # the DB returns (see _heartbeat_loop).  The DB-up path is unchanged.
        if not _db_reachable():
            print(f"[LOCK] DB unreachable on startup — file-lock fallback "
                  f"for line_id={self.line_id}")
            self._db_locked = False
            self._acquire_file_fallback()
        else:
            try:
                with _db_conn() as c:
                    _ensure_collector_lock_table(c)
                    cur = c.cursor()
                    # Read existing lock holder (if any)
                    cur.execute("""
                        SELECT hostname, pid, heartbeat_at,
                               NOW() - heartbeat_at AS age
                          FROM mes_collector_locks
                         WHERE line_id = %s
                    """, (self.line_id,))
                    row = cur.fetchone()
                    if row:
                        hostname, pid, hb, age = row
                        age_sec = age.total_seconds() if hasattr(age, "total_seconds") else 0
                        # Same host + same PID → tail-end of a previous run that
                        # crashed before releasing.  Steal the lock without fuss.
                        if hostname == self.hostname and pid == self.pid:
                            pass
                        elif age_sec < _HEARTBEAT_STALE_AFTER_SEC:
                            # Fresh holder — refuse.
                            raise RuntimeError(
                                f"Another collector is already running for "
                                f"line_id={self.line_id} on host '{hostname}' "
                                f"(PID {pid}, last heartbeat {age_sec:.0f}s ago).  "
                                f"Stop that one first, or wait "
                                f"{_HEARTBEAT_STALE_AFTER_SEC - age_sec:.0f}s "
                                f"for its lock to go stale.\n"
                                f"\n"
                                f"This guard exists so a frontend opened from "
                                f"another LAN PC can't accidentally start a "
                                f"second collector and corrupt the PLC's "
                                f"remote-active bit (D6005 bit 4)."
                            )
                        # else: stale holder → claim
                    cur.execute("""
                        INSERT INTO mes_collector_locks
                               (line_id, hostname, pid, heartbeat_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (line_id)
                        DO UPDATE SET hostname     = EXCLUDED.hostname,
                                      pid          = EXCLUDED.pid,
                                      heartbeat_at = NOW()
                    """, (self.line_id, self.hostname, self.pid))
                    c.commit()
                self._db_locked = True
                print(f"[LOCK] OK Acquired singleton lock for line_id={self.line_id} "
                      f"on {self.hostname} PID={self.pid}")
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as _e:
                # DB blinked out between the probe and the connect — fall back.
                print(f"[LOCK] DB unreachable during acquire ({_e}) — "
                      f"file-lock fallback")
                self._db_locked = False
                self._acquire_file_fallback()
        # Heartbeat thread refreshes the DB lock, or re-establishes it after a
        # DB-down boot (and keeps the local file-lock fresh meanwhile).
        self._thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True,
            name=f"collector-lock-hb-line{self.line_id}",
        )
        self._thread.start()

    # ── Local file-lock fallback (used only while the DB is down) ────────
    def _lockfile_path(self) -> str:
        return _os.path.join(_cache_dir(), f"collector_line{self.line_id}.lock")

    def _touch_lockfile(self) -> None:
        """Rewrite the lockfile (updates mtime) so a live file-lock holder
        stays 'fresh'; a crashed one's lockfile goes stale in
        _HEARTBEAT_STALE_AFTER_SEC and can be claimed by a restart."""
        try:
            with open(self._lockfile_path(), "w") as f:
                f.write(str(self.pid))
        except Exception:
            pass

    def _acquire_file_fallback(self) -> None:
        """Same-PC lock when the DB is unreachable.  Refuses only if a *fresh*
        lockfile from a different PID exists (a real concurrent launch on this
        host); otherwise claims it.  Cross-PC protection is degraded until the
        DB returns — logged loudly."""
        path = self._lockfile_path()
        try:
            if _os.path.exists(path):
                age = time.time() - _os.path.getmtime(path)
                try:
                    with open(path) as f:
                        other = f.read().strip()
                except Exception:
                    other = ""
                if (age < _HEARTBEAT_STALE_AFTER_SEC
                        and other and other != str(self.pid)):
                    raise RuntimeError(
                        f"Another collector (PID {other}) holds the local "
                        f"file-lock for line_id={self.line_id} (DB-down mode, "
                        f"last beat {age:.0f}s ago).  Stop it, or wait "
                        f"{_HEARTBEAT_STALE_AFTER_SEC - age:.0f}s.")
        except RuntimeError:
            raise
        except Exception:
            pass
        self._touch_lockfile()
        print(f"[LOCK] WARNING DB unreachable — running WITHOUT cross-PC DB "
              f"lock (file-lock fallback, line_id={self.line_id}, "
              f"PID={self.pid}).  DB lock re-establishes when the DB returns.")

    def _db_upsert_lock(self, conn) -> None:
        """Claim/refresh our row in mes_collector_locks (used by the heartbeat
        to re-establish the cross-PC lock after a DB-down boot)."""
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_collector_locks
                   (line_id, hostname, pid, heartbeat_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (line_id)
            DO UPDATE SET hostname     = EXCLUDED.hostname,
                          pid          = EXCLUDED.pid,
                          heartbeat_at = NOW()
        """, (self.line_id, self.hostname, self.pid))
        conn.commit()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SEC):
            # File-fallback mode: keep the local lockfile fresh and try to
            # (re)establish the DB lock the moment the DB comes back.
            if not self._db_locked:
                self._touch_lockfile()
                if _db_reachable():
                    try:
                        with _db_conn() as c:
                            _ensure_collector_lock_table(c)
                            self._db_upsert_lock(c)
                        self._db_locked = True
                        print(f"[LOCK] DB back — re-established cross-PC lock "
                              f"for line_id={self.line_id}")
                    except Exception as e:
                        print(f"[LOCK] re-establish attempt failed: {e}")
                continue
            # Normal DB-lock heartbeat (unchanged behaviour).
            try:
                with _db_conn() as c:
                    cur = c.cursor()
                    cur.execute("""
                        UPDATE mes_collector_locks
                           SET heartbeat_at = NOW()
                         WHERE line_id  = %s
                           AND hostname = %s
                           AND pid      = %s
                    """, (self.line_id, self.hostname, self.pid))
                    c.commit()
            except Exception as e:
                # DB hiccup — log once, keep trying.  If DB stays down
                # past 30 s our lock goes stale and another collector
                # CAN take over, which is correct behaviour.
                print(f"[LOCK] heartbeat failed: {e}")

    def release(self) -> None:
        self._stop.set()
        try:
            with _db_conn() as c:
                cur = c.cursor()
                cur.execute("""
                    DELETE FROM mes_collector_locks
                     WHERE line_id  = %s
                       AND hostname = %s
                       AND pid      = %s
                """, (self.line_id, self.hostname, self.pid))
                c.commit()
            print(f"[LOCK] released for line_id={self.line_id}")
        except Exception:
            pass
        # Drop the local file-lock too (best-effort).
        try:
            _p = self._lockfile_path()
            if _os.path.exists(_p):
                _os.remove(_p)
        except Exception:
            pass



# ============================================================
# CONFIG LOADER
# ============================================================

def load_line_config(line_id: int) -> dict:
    """DB-down resilient wrapper.  Tries the DB; on a connection failure falls
    back to the last cached config so the collector can still boot, read the
    PLC and buffer data.  Refreshes the cache on every successful DB load.
    Needs the DB reachable at least once to seed the cache."""
    _name = f"line_config_{line_id}.pkl"
    if not _db_reachable():
        cached = _cache_load(_name)
        if cached is not None:
            return cached
        raise RuntimeError(
            f"DB unreachable and no cached config for line_id={line_id} — the "
            f"collector needs the DB up at least once to seed the config cache."
        )
    try:
        cfg = _load_line_config_from_db(line_id)
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as _e:
        print(f"[CONFIG] DB error during load ({_e}) — trying cache")
        cached = _cache_load(_name)
        if cached is not None:
            return cached
        raise
    _cache_save(_name, cfg)
    return cfg


def _load_line_config_from_db(line_id: int) -> dict:
    conn = _db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Safety net — if a zombie 'idle in transaction' session is holding a
    # lock on any of these tables, fail loudly after 15 s instead of hanging
    # the whole collector process forever.
    try:
        cur.execute("SET statement_timeout = '15s'")
        cur.execute("SET lock_timeout = '10s'")
    except Exception:
        pass

    # 2026-05-30 — Ensure register-mirror schema (shift_reset_bit column
    # + mes_shift_count_archive table) BEFORE the main SELECT below, which
    # now reads pc.shift_reset_bit.  Idempotent + additive → safe on every
    # start and on a fresh DB that predates the column.
    _ensure_register_count_schema_collector(conn)

    # 2026-05-29 — pin main PLC by `l.dashboard_plc_id` instead of relying
    # on `parent_plc_id IS NULL`.  Background: the admin Save-Machine flow
    # was occasionally INSERTing a second main-PLC row (parent_plc_id NULL)
    # instead of UPDATEing the existing one — Postgres returned the dup
    # first (no ORDER BY), and a fresh row carried default count_mode='bit'
    # ok_data_register=NULL, silently reverting any register-mode config.
    # Pinning to dashboard_plc_id makes the loader idempotent: even if more
    # duplicates appear in mes_plc_configs, we always read the canonical
    # row that `mes_lines.dashboard_plc_id` points to.  Fallback to the
    # legacy `parent_plc_id IS NULL ORDER BY id ASC LIMIT 1` only when
    # dashboard_plc_id is NULL (older lines that pre-date this column).
    cur.execute("""
        SELECT l.*, p.plant_name,
               pc.id AS plc_id,
               pc.machine_name AS main_machine_name,
               pc.plc_ip, pc.plc_port,
               pc.ok_bit_address, pc.ng_bit_address,
               COALESCE(pc.count_mode, 'bit')         AS count_mode,
               NULLIF(TRIM(pc.ok_data_register), '')  AS ok_data_register,
               NULLIF(TRIM(pc.ng_data_register), '')  AS ng_data_register,
               NULLIF(TRIM(pc.shift_reset_bit), '')   AS shift_reset_bit,
               pc.status_address, pc.model_address,
               pc.sensor_ok_address, pc.process_seq_address, pc.override_address,
               pc.ideal_cycle_time, pc.max_allowed_cycle, pc.ok_ng_pulse_min_gap,
               -- 2026-08-19 SEAT SLIDER trace: bit pulsed on THIS PLC when a
               -- part that was NG at Semi-Auto reaches Final.  Blank = off.
               NULLIF(TRIM(pc.fi_sa_ng_bit), '')       AS fi_sa_ng_bit,
               COALESCE(pc.fi_sa_ng_bit_hold_sec, 2.0) AS fi_sa_ng_bit_hold_sec,
               -- 2026-08-24 — Final compare-fetch bit.  When set, the SA-NG
               -- reject fires on THIS bit's rising edge (part present +
               -- decision ready) instead of the OK-count commit, so the reject
               -- lands on the part's own cycle, not one cycle late.  Blank =
               -- unchanged (reject keeps firing on count commit).
               NULLIF(TRIM(pc.fi_fetch_bit), '')       AS fi_fetch_bit
        FROM mes_lines l
        JOIN mes_plants p ON p.id = l.plant_id
        JOIN mes_plc_configs pc
             ON pc.line_id = l.id
            AND (
                  (l.dashboard_plc_id IS NOT NULL AND pc.id = l.dashboard_plc_id)
               OR (l.dashboard_plc_id IS NULL     AND pc.parent_plc_id IS NULL)
            )
        WHERE l.id = %s
        ORDER BY pc.id ASC
        LIMIT 1
    """, (line_id,))
    line = dict(cur.fetchone())

    cur.execute("""
        SELECT model_number, model_name FROM mes_model_mappings
        WHERE line_id = %s ORDER BY model_number
    """, (line_id,))
    models = {r["model_number"]: r["model_name"] for r in cur.fetchall()}

    cur.execute("""
        SELECT status_code, status_name, loss_type FROM mes_status_mappings
        WHERE line_id = %s ORDER BY status_code
    """, (line_id,))
    status_map = {r["status_code"]: {"name": r["status_name"], "loss": r["loss_type"]}
                  for r in cur.fetchall()}

    # 2026-05-18 — AUTO-INJECT synthetic BREAK row if none exists.
    # Without this, scheduled break windows fall back to IDLE (gray)
    # on the timeline because _find_break_status() returns (None,None)
    # and the override code path in _update_status sets status_code=0
    # (IDLE) as legacy fallback.  Operator complaint: "PRR BHAI YE IDLE
    # MEIN SWITCH HO JATI HAI" — the 12:00-12:35 lunch break window
    # was painting IDLE at 12:34:59 right before break ended.
    # The synthetic row is purely in-memory (status_map dict) — no DB
    # write — so this is safe to re-run on every collector start.  The
    # admin can override by adding a real row to mes_status_mappings
    # under Admin -> Production -> Status Colour; ours is only injected
    # when the existing rows have no break-typed entry.
    _has_break = any(
        isinstance(info, dict) and (
            (info.get("loss") == "break") or
            ((info.get("name") or "").upper() == "BREAK") or
            ((info.get("name") or "").upper().endswith("_BREAK"))
        )
        for info in status_map.values()
    )
    if not _has_break:
        # Find first free status_code starting at 99 and walking down.
        # We avoid the documented codes 0-9 (RUNNING / IDLE / loss
        # buckets) so an operator-defined break row never clobbers a
        # production status.  99 is conventional for "synthetic" codes
        # across our other line configs.
        _free_code = 99
        while _free_code in status_map and _free_code > 10:
            _free_code -= 1
        status_map[_free_code] = {"name": "BREAK", "loss": "break"}
        print(f"[CONFIG] line_id={line_id}: no BREAK row found in "
              f"mes_status_mappings — auto-injected synthetic code "
              f"{_free_code} (name='BREAK', loss='break').  Timeline "
              f"will now paint scheduled break windows in BREAK blue "
              f"instead of IDLE gray.  To customize the code/name, add "
              f"a real row under Admin -> Production -> Status Colour.",
              flush=True)

    cur.execute("""
        SELECT * FROM mes_shift_configs
        WHERE line_id = %s ORDER BY shift_name
    """, (line_id,))
    shifts = {r["shift_name"]: dict(r) for r in cur.fetchall()}

    cur.execute("""
        SELECT * FROM mes_hourly_slots
        WHERE line_id = %s ORDER BY shift_name, slot_order
    """, (line_id,))
    slots_raw = cur.fetchall()

    hourly_plan       = {}
    slot_boundaries   = {}
    slot_to_db_prefix = {}
    for s in slots_raw:
        sn = s["shift_name"]
        sl = s["slot_label"]
        if sn not in hourly_plan:
            hourly_plan[sn] = {}
        hourly_plan[sn][sl] = s["plan_pieces"]
        slot_boundaries[sl] = (s["start_time"], s["end_time"], s["crosses_midnight"])
        slot_to_db_prefix[sl] = s["db_column_prefix"]

    cur.execute("""
        SELECT break_name, start_time, end_time, crosses_midnight
        FROM mes_break_configs
        WHERE line_id = %s ORDER BY start_time
    """, (line_id,))
    breaks = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT id, poka_yoke_no, side, poka_yoke_name,
               model, bit, value, machine_name,
               sheet_name, alert_level
        FROM mes_poka_yoke_rules
        WHERE line_id = %s AND is_active = true AND bit IS NOT NULL
        ORDER BY bit
    """, (line_id,))
    poka_rules = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "line_id":          line_id,
        "zone_id":          line.get("zone_id"),
        "main_plc_id":      line.get("plc_id"),
        "main_machine_name": line.get("main_machine_name"),
        "line_name":        line["line_name"],
        "table_name":       line["db_table_name"],
        "plc_ip":           line["plc_ip"],
        "plc_port":         line["plc_port"],
        "ok_bit":           line["ok_bit_address"],
        "ng_bit":           line["ng_bit_address"],
        # 2026-05-29 - Register-mode (Final Inspection only).
        # When count_mode='register', collector reads ok_data_register +
        # ng_data_register as 16-bit words.  Value increment = +N OK/NG.
        # When count_mode='bit' (default), uses ok_bit/ng_bit as before.
        # Sub-machines are NOT affected (their loader is separate).
        "count_mode":       (line.get("count_mode") or "bit").lower(),
        "ok_data_register": line.get("ok_data_register"),
        "ng_data_register": line.get("ng_data_register"),
        # 2026-05-30 — Per-machine shift rollover bit (register-mirror
        # design).  NULL = feature off (legacy clock-only behaviour).
        "shift_reset_bit":  line.get("shift_reset_bit"),
        # 2026-08-19 — SEAT SLIDER Semi-Auto→Final NG trace.  Raised on this
        # (Final) PLC when a part the Semi-Auto marked NG reaches Final.
        # None = feature off, which is every line until an admin fills it in.
        # 2026-08-20 — backstop for the FI desync churn.  With the socket now
        # dropped as soon as a read times out (see _on_plc_socket_error) this
        # should rarely fire at all; when it does, 15 s of frozen count was far
        # too long to sit on a stream we already know is bad.  Env-tunable:
        # REG_DESYNC_RECONNECT_S=15 restores the old behaviour.
        "reg_desync_reconnect_s": float(_os.environ.get("REG_DESYNC_RECONNECT_S", "5") or 5),
        "fi_sa_ng_bit":     line.get("fi_sa_ng_bit"),
        "fi_sa_ng_hold":    float(line.get("fi_sa_ng_bit_hold_sec") or 2.0),
        # 2026-08-24 — optional Final compare-fetch bit.  Blank = reject fires
        # on count commit (old behaviour).  Set = reject fires on this bit's
        # rising edge only (correct cycle), and the count-commit path is
        # skipped so L230 never double-pulses for one part.
        "fi_fetch_bit":     line.get("fi_fetch_bit"),
        "status_addr":      line["status_address"],
        "model_addr":       line["model_address"],
        "sensor_ok_addr":   line["sensor_ok_address"],
        "process_seq_addr": line["process_seq_address"],
        "override_addr":    line["override_address"],
        "ideal_ct":         float(line["ideal_cycle_time"]),
        "max_ct":           float(line["max_allowed_cycle"]),
        "pulse_gap":        float(line["ok_ng_pulse_min_gap"]),
        "models":           models,
        "status_map":       status_map,
        "shifts":           shifts,
        "hourly_plan":      hourly_plan,
        "slot_boundaries":  slot_boundaries,
        "slot_to_db":       slot_to_db_prefix,
        "breaks":           breaks,
        "poka_rules":       poka_rules,
    }


# ============================================================
# SUB-MACHINE LOADER
# ============================================================

# 2026-09-17 — Run the schema-sync ONCE per collector process.
# These two helpers sit at the top of _load_line_config_from_db() and
# _load_submachines_from_db(), which the collector calls again every 60 s
# to hot-reload config.  So each ALTER was being re-issued ~23x a minute
# across the fleet.  `ADD COLUMN IF NOT EXISTS` does no work once the
# column exists, but Postgres still takes an ACCESS EXCLUSIVE lock on
# mes_plc_configs before it can find that out — and every reader AND the
# collectors' own `INSERT INTO mes_submachine_ct_log` queues behind it.
# Measured live: the wallboard's machine-list SELECT executes in 0.3 ms
# but waited up to 2,885 ms behind this convoy; over 75 s of sampling the
# two ALTERs accounted for 11,368 of 14,563 blocked-query observations,
# 3,140 of them blocked cycle-log INSERTs.  The 3 s lock_timeout below
# was the 2026-07-23 band-aid for the same convoy; this removes the cause.
# The flag is set only after a successful commit, so a lock_timeout abort
# still retries on the next call exactly as it does today.
_SA_SCHEMA_DONE  = False
_REG_SCHEMA_DONE = False


def _ensure_semi_auto_schema_collector(conn) -> None:
    """Mirror of routers/lines.py:_ensure_semi_auto_schema — kept in
    sync here so the collector can pull SA columns even when MES has
    been restarted and no admin call has touched the schema yet.
    Idempotent: Postgres `IF NOT EXISTS` skips the work after first run,
    and the process-level flag skips the statement itself after that."""
    global _SA_SCHEMA_DONE
    if _SA_SCHEMA_DONE:
        return
    try:
        cur = conn.cursor()
        # 2026-07-23 — FAIL-FAST LOCK so this idempotent schema-sync can never
        # JAM the DB.  Without it, a collector-startup ALTER waits INDEFINITELY
        # for ACCESS EXCLUSIVE behind a slow SELECT on mes_plc_configs, and
        # every other query queues behind the ALTER -> whole DB frozen ->
        # "login 503 / video dead" outage (recurred 2026-07-23).  The columns
        # already exist after first run, so a lock_timeout abort here is
        # harmless (caught below); we just skip the no-op ALTER this time.
        cur.execute("SET lock_timeout = '3s'")
        cur.execute("""
            ALTER TABLE mes_plc_configs
              ADD COLUMN IF NOT EXISTS sa_enabled          BOOLEAN     NOT NULL DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS sa_fetch_bit        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_part_code_addr   VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_part_code_len    INTEGER,
              ADD COLUMN IF NOT EXISTS sa_data_addr        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_data_len         INTEGER,
              ADD COLUMN IF NOT EXISTS sa_time_addr        VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_time_len         INTEGER,
              ADD COLUMN IF NOT EXISTS sa_register_names   JSONB,
              ADD COLUMN IF NOT EXISTS sa_register_scales  JSONB,
              -- 2026-08-06 — shift-wise Semi-Auto capture (mirror of
              -- routers/lines.py:_ensure_semi_auto_schema).
              ADD COLUMN IF NOT EXISTS sa_shift_data_bit   VARCHAR(20),
              ADD COLUMN IF NOT EXISTS sa_shift_reset_bit  VARCHAR(20)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_submachine_data_log (
                id            BIGSERIAL   PRIMARY KEY,
                sub_plc_id    INTEGER     NOT NULL,
                line_id       INTEGER,
                record_date   DATE,
                shift_name    VARCHAR(10),
                cycle_seq     INTEGER,
                ts_plc        TIMESTAMPTZ,
                ts_server     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                part_code     VARCHAR(80),
                model_number  INTEGER,
                model_name    VARCHAR(120),
                data_values   JSONB       NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_sub_ts
                ON mes_submachine_data_log (sub_plc_id, ts_server DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_part
                ON mes_submachine_data_log (part_code)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_submachine_data_log_date_shift
                ON mes_submachine_data_log (record_date, shift_name)
        """)
        conn.commit()
        cur.close()
        _SA_SCHEMA_DONE = True      # committed — don't re-issue the DDL
    except Exception as exc:
        try: conn.rollback()
        except Exception: pass
        print(f"[SEMI-AUTO] collector schema-ensure failed: {exc}")


def _ensure_register_count_schema_collector(conn) -> None:
    """Schema for the register-mirror counting redesign (2026-05-30).

    Operator spec (Hinglish): har machine (semi-auto chhod ke) ka OK/NG
    count ab ek D-data-register se EXACT mirror hoga (na freeze, na
    reject).  Shift ke end pe ek 'shift_reset_bit' ~2s ON hoti hai —
    us par pehle closing count ko archive table me move karte hain,
    PHIR register 0 hota hai (interlock: jab tak data move na ho,
    rollover accept nahi).

    Adds:
      • mes_plc_configs.shift_reset_bit  — per-machine bit address that
        signals shift rollover (admin UI sets it; NULL = feature off,
        machine keeps its current behaviour → zero regression).
      • mes_shift_count_archive          — one row per machine per shift
        holding the closing OK/NG count, written BEFORE the reset.

    Idempotent (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS),
    so it is safe to run on every collector start.  Purely additive —
    no existing column/row is touched, so bit-mode machines and the
    current Final-Inspection register flow are unaffected until the
    admin actually fills in shift_reset_bit + count_mode=register.

    Runs once per process (see _REG_SCHEMA_DONE above) — on every collector
    START as the docstring says, but no longer on every 60 s config reload.
    """
    global _REG_SCHEMA_DONE
    if _REG_SCHEMA_DONE:
        return
    try:
        cur = conn.cursor()
        cur.execute("SET lock_timeout = '3s'")   # 2026-07-23 fail-fast, never jam the DB (see sa_enabled ALTER)
        cur.execute("""
            ALTER TABLE mes_plc_configs
              ADD COLUMN IF NOT EXISTS shift_reset_bit VARCHAR(20)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_shift_count_archive (
                id            BIGSERIAL   PRIMARY KEY,
                machine_id    INTEGER     NOT NULL,   -- mes_plc_configs.id
                line_id       INTEGER,
                machine_name  VARCHAR(120),
                record_date   DATE        NOT NULL,
                shift_name    VARCHAR(10) NOT NULL,
                ok_count      INTEGER     NOT NULL DEFAULT 0,
                ng_count      INTEGER     NOT NULL DEFAULT 0,
                reset_bit     VARCHAR(20),
                ts_archived   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (machine_id, record_date, shift_name)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_shift_count_archive_machine_ts
                ON mes_shift_count_archive (machine_id, ts_archived DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_mes_shift_count_archive_date_shift
                ON mes_shift_count_archive (record_date, shift_name)
        """)
        conn.commit()
        cur.close()
        _REG_SCHEMA_DONE = True     # committed — don't re-issue the DDL
    except Exception as exc:
        try: conn.rollback()
        except Exception: pass
        print(f"[REG-COUNT] collector schema-ensure failed: {exc}")


def load_submachines(main_plc_id: int) -> list:
    """DB-down resilient wrapper around the sub-machine loader.  On a DB outage
    it returns the last cached sub list (so the sub-pollers still start) instead
    of [].  Refreshes the cache on every successful load."""
    if not main_plc_id:
        return []
    _name = f"submachines_{main_plc_id}.pkl"
    if not _db_reachable():
        cached = _cache_load(_name)
        return cached if cached is not None else []
    try:
        rows = _load_submachines_from_db(main_plc_id)
    except Exception as e:
        print(f"[SUB] load_submachines failed: {e}")
        cached = _cache_load(_name)
        return cached if cached is not None else []
    _cache_save(_name, rows)
    return rows


def _load_submachines_from_db(main_plc_id: int) -> list:
    """Return every sub-machine whose parent_plc_id matches the main PLC.
    Each dict has the fields the sub-poller needs: id, plc_ip, plc_port,
    count_bit (stored in ok_bit_address), ideal_ct, machine_name, line_id,
    plus the optional Semi-Auto data-capture config (sa_enabled +
    addresses + register names/scales).  Returns [] if no sub-machines
    configured — safe for legacy lines.
    """
    conn = _db_conn()
    _ensure_semi_auto_schema_collector(conn)
    _ensure_register_count_schema_collector(conn)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, plc_ip, plc_port, line_id,
               NULLIF(TRIM(ok_bit_address), '') AS count_bit,
               NULLIF(TRIM(ng_bit_address), '') AS ng_bit,
               ideal_cycle_time                 AS ideal_ct,
               machine_name,
               -- 2026-05-30 register-mirror counting for sub-machines.
               -- Sub keeps bit-mode UNLESS count_mode='register' AND an
               -- ok_data_register is set → then count mirrors the reg.
               COALESCE(count_mode, 'bit')        AS count_mode,
               NULLIF(TRIM(ok_data_register), '') AS ok_data_register,
               NULLIF(TRIM(ng_data_register), '') AS ng_data_register,
               NULLIF(TRIM(shift_reset_bit), '')  AS shift_reset_bit,
               COALESCE(sa_enabled, FALSE)      AS sa_enabled,
               NULLIF(TRIM(sa_fetch_bit), '')   AS sa_fetch_bit,
               NULLIF(TRIM(sa_part_code_addr), '') AS sa_part_code_addr,
               sa_part_code_len,
               NULLIF(TRIM(sa_data_addr), '')   AS sa_data_addr,
               sa_data_len,
               NULLIF(TRIM(sa_shift_data_bit), '')  AS sa_shift_data_bit,
               NULLIF(TRIM(sa_shift_reset_bit), '') AS sa_shift_reset_bit,
               NULLIF(TRIM(sa_time_addr), '')   AS sa_time_addr,
               sa_time_len,
               sa_register_names,
               sa_register_scales,
               -- 2026-08-19 SEAT SLIDER Semi-Auto -> Final NG trace.  One of
               -- the SA data registers carries the station verdict; blank
               -- here means the trace is off for this machine (no logging,
               -- no bit), so every other line behaves exactly as before.
               NULLIF(TRIM(sa_result_register), '') AS sa_result_register,
               COALESCE(sa_result_ok_value, 1)      AS sa_result_ok_value,
               COALESCE(sa_result_ng_value, 2)      AS sa_result_ng_value,
               -- 2026-08-24 — verdict moved to two plain bits; whichever is
               -- ON when the part is captured is the verdict.
               NULLIF(TRIM(sa_ok_bit), '')          AS sa_ok_bit,
               NULLIF(TRIM(sa_ng_bit), '')          AS sa_ng_bit,
               -- 2026-08-24 — separate NG read trigger (Semi-Auto only).  Its
               -- rising edge also fires the SA capture so an NG part whose OK
               -- fetch bit never pulsed still gets read + logged NG.
               NULLIF(TRIM(sa_ng_trigger_bit), '')  AS sa_ng_trigger_bit
        FROM mes_plc_configs
        WHERE parent_plc_id = %s
        ORDER BY id
    """, (main_plc_id,))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ============================================================
# CYCLE TIME TRACKER
# ============================================================

class CycleTimeTracker:
    def __init__(self, ideal_ct: float, max_ct: float, window: int = 20):
        self.ideal_ct    = ideal_ct
        self.max_ct      = max_ct
        self.window      = window
        self.cycle_times: list = []
        self.last_pulse  = None
        self.speed_loss  = 0.0
        self.is_running  = False
        self.pulse_recv  = False

    def set_running(self, running: bool):
        self.is_running = running
        if not running:
            self.last_pulse = None
            self.pulse_recv = False

    def on_pulse(self, now: float) -> None:
        if not self.is_running:
            self.last_pulse = None
            return
        if self.last_pulse is None:
            self.last_pulse = now
            self.pulse_recv = True
            return
        ct = round(now - self.last_pulse, 2)
        self.last_pulse = now
        self.pulse_recv = True
        if 1.0 <= ct <= 300.0:
            self._add(ct)

    def _add(self, ct: float):
        self.cycle_times.append(ct)
        if len(self.cycle_times) > self.window:
            self.cycle_times.pop(0)
        if ct > self.ideal_ct:
            self.speed_loss += ct - self.ideal_ct
        elif ct < self.ideal_ct:
            self.speed_loss = max(0.0, self.speed_loss - (self.ideal_ct - ct))

    def check_continuous(self, now: float) -> float:
        if not self.is_running or self.last_pulse is None or not self.pulse_recv:
            return 0.0
        gap = now - self.last_pulse
        if self.max_ct < gap < 300:
            extra = min(1.0, gap - self.max_ct)
            self.speed_loss += extra
            return extra
        return 0.0

    def stats(self) -> dict:
        cts = self.cycle_times
        if not cts:
            return {"avg": self.ideal_ct, "min": self.ideal_ct,
                    "max": self.ideal_ct, "std": 0.0, "list": []}
        return {
            "avg":  round(sum(cts) / len(cts), 2),
            "min":  round(min(cts), 2),
            "max":  round(max(cts), 2),
            "std":  round(statistics.stdev(cts) if len(cts) > 1 else 0.0, 2),
            "list": cts.copy(),
        }

    def ct_dict(self) -> dict:
        s = self.stats()
        d = {f"ct{i}": (s["list"][-(i)] if i <= len(s["list"]) else None)
             for i in range(1, 21)}
        d.update({"ct_avg_20": s["avg"], "min_ct": s["min"],
                  "max_ct": s["max"], "std_dev_ct": s["std"],
                  "speed_loss": self.speed_loss})
        return d

    def reset(self):
        self.speed_loss  = 0.0
        self.last_pulse  = None
        self.pulse_recv  = False
        self.cycle_times.clear()


# ============================================================
# POKA YOKE MONITOR
# ============================================================

class PokaYokeMonitor:
    def __init__(self, rules: list, line_id: int):
        self.rules    = rules
        self.line_id  = line_id
        self.d_rules: list    = []
        self.poka_state: dict = {}
        self._last_reload     = 0.0
        self._ng_streak          = 0
        self._last_event_time: dict = {}
        # ── New PY Master / Assignment based bypass detection ────────────────
        self._py_configs: list = []
        self._py_last_reload: float = 0.0
        self._py_bypass_state: dict = {}  # {(py_no, model_bit, reg) → last_code}
        # ── Sensor Health — passive X-bit monitoring (READ-ONLY) ──────
        # Sample every unique sensing X-bit configured across all PYs
        # roughly once a second.  Per-bit state tracks last_toggle_ts in
        # memory only.  If a bit goes >900 s (15 min) without any value
        # change, status flips to 'stuck' and ONE SENSOR_HEALTH email
        # fires.  Natural toggle resets the timer + clears the email flag.
        # No PLC writes — collector NEVER overwrites sensor bits.
        self._x_state:             dict  = {}   # {x_bit → state dict}
        # 2026-05-22 — Sweep cadence reduced 1.0 → 0.2 sec to catch
        # short sensor pulses (typical part-pass = 50-500ms).  Earlier
        # 1-sec sweep missed any toggle shorter than 1 sec, leading to
        # "sensor stuck" false positives even when the bit was actually
        # firing 200ms pulses on every cycle.  Publishing rate stays
        # at 10s (backend doesn't need 5Hz updates — just the sweep
        # internal state needs to track every toggle).
        self._x_track_interval:    float = 0.2   # was 1.0
        self._x_track_last:        float = 0.0
        # 2026-05-22 — Track toggle count per bit per minute so the
        # operator can see "is this sensor actually firing per cycle?"
        # at a glance.  Reset every 60s.
        self._x_toggle_counts:     dict  = {}    # {x_bit → int}
        self._x_toggle_window_ts:  float = 0.0
        self._publish_interval:    float = 10.0
        self._publish_last:        float = 0.0
        self._stuck_threshold_sec: int   = 900   # 15 min — email after this
        self._partition_rules()

    def _partition_rules(self):
        self.d_rules     = [r for r in self.rules if r.get("bit")]
        self.logic_rules = [r for r in self.rules if not r.get("bit")]
        print(f"[POKA] {len(self.d_rules)} D-register rules, "
              f"{len(self.logic_rules)} logic rules loaded")

    def reload_rules_from_db(self, line_id: int):
        now = time.time()
        if now - self._last_reload < 600:
            return
        self._last_reload = now
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, poka_yoke_no, side, poka_yoke_name,
                       model, bit, value, machine_name,
                       sheet_name, alert_level
                FROM mes_poka_yoke_rules
                WHERE line_id = %s AND is_active = true AND bit IS NOT NULL
                ORDER BY bit
            """, (line_id,))
            self.d_rules = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
            print(f"[POKA] Reloaded {len(self.d_rules)} D-register rules")
        except Exception as e:
            print(f"[POKA] Reload error: {e}")

    def check_d_registers(self, plc, shift_name: str):
        if not self.d_rules or plc is None:
            return
        d_nums = {}
        for rule in self.d_rules:
            bit = str(rule.get("bit", "") or "").strip().upper()
            if bit.startswith("D"):
                try:
                    num = int(bit[1:])
                    d_nums[bit] = num
                except ValueError:
                    pass
        if not d_nums:
            return
        nums  = list(d_nums.values())
        min_d = min(nums)
        max_d = max(nums)
        count = max_d - min_d + 1
        try:
            values  = plc.batchread_wordunits(headdevice=f"D{min_d}", readsize=count)
            val_map = {f"D{min_d + i}": (values[i] or 0) for i in range(count)}
        except Exception as e:
            print(f"[POKA] PLC read error: {e}")
            return
        for rule in self.d_rules:
            bit         = str(rule.get("bit", "") or "").strip().upper()
            # Desired/expected value: can be int (0/1/2) OR label (on/off/pass).
            # "pass" / "bypass" → skip this check (no alert for this model).
            raw_val     = rule.get("value")
            val_str     = str(raw_val if raw_val is not None else "1").strip().lower()
            if val_str in ("pass", "bypass", "skip", ""):
                self.poka_state[bit] = val_map.get(bit, 0)
                continue
            # Map label → numeric. Accept both "on"/"1" and "off"/"0".
            if val_str in ("on", "1", "true", "yes"):
                trigger_val = 1
            elif val_str in ("off", "0", "false", "no"):
                trigger_val = 0
            else:
                try:
                    trigger_val = int(val_str)
                except ValueError:
                    continue  # unknown label → skip
            current_val = val_map.get(bit, 0)
            last_val    = self.poka_state.get(bit, 0)
            if current_val == trigger_val and last_val != trigger_val:
                poka_name = rule.get("poka_yoke_name", bit)
                side      = rule.get("side", "ALL")
                machine   = rule.get("machine_name", "")
                level     = rule.get("alert_level", "WARNING")
                print(f"[POKA] FAULT ▶ {bit}={current_val} | {poka_name} | {side} | {machine}")
                try:
                    requests.post(
                        f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                        json={
                            "line_id":      self.line_id,
                            "rule_id":      rule.get("id"),
                            "rule_type":    "SENSOR_BYPASS",
                            "alert_level":  level,
                            "shift_name":   shift_name,
                            "plc_value":    str(current_val),
                            "context_json": (
                                f'{{"bit":"{bit}",'
                                f'"value":{current_val},'
                                f'"rule":"{poka_name}",'
                                f'"side":"{side}",'
                                f'"machine":"{machine}",'
                                f'"py_no":"{rule.get("poka_yoke_no","")}",'
                                f'"model":"{rule.get("model","all")}"}}'
                            ),
                        },
                        timeout=2,
                    )
                except Exception as e:
                    print(f"[POKA] Event post failed: {e}")
            self.poka_state[bit] = current_val

    # ── NEW: PY Master based bypass detection ──────────────────────────────
    def reload_py_configs(self, line_id: int):
        """Pull PY Master + per-model assignments from DB every 20s."""
        now = time.time()
        if now - self._py_last_reload < 20:
            return
        self._py_last_reload = now
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Make absolutely sure the new sensing_bits column exists before
            # we SELECT it — collector may be running against a DB whose
            # schema migration hasn't been triggered (the backend's
            # _ensure_py_register_col only runs on /master/ access).
            try:
                cur.execute(
                    "ALTER TABLE mes_py_master "
                    "ADD COLUMN IF NOT EXISTS sensing_bits VARCHAR(100)"
                )
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
            # Defensive SELECT — if for any reason sensing_bits isn't
            # available, fall back to the column-less form so the bypass
            # detector keeps working.
            try:
                cur.execute("""
                    SELECT p.id         AS py_id,
                           p.py_no,
                           p.description,
                           p.bit        AS register_addr,
                           COALESCE(p.register_count, 1) AS register_count,
                           p.sensing_bits,
                           p.model_type, p.side
                    FROM mes_py_master p
                    WHERE p.is_active = true
                """)
            except Exception as e_pri:
                print(f"[POKA-BYPASS] sensing_bits SELECT failed ({e_pri}); "
                      f"retrying without it")
                try: conn.rollback()
                except Exception: pass
                cur.execute("""
                    SELECT p.id         AS py_id,
                           p.py_no,
                           p.description,
                           p.bit        AS register_addr,
                           COALESCE(p.register_count, 1) AS register_count,
                           NULL         AS sensing_bits,
                           p.model_type, p.side
                    FROM mes_py_master p
                    WHERE p.is_active = true
                """)
            pys = cur.fetchall()
            # 2026-08-22 — operator's rule: the Active / Not-Active flag in
            # Maintenance Panel → PY Config is what decides whether a PY
            # applies to the running model; `desired` is then the value the
            # collector must see in that PY's own register.  That list lives
            # in the PUBLISHED config (`mes_py_config_live`, written by the
            # "Update to Software" button), not in the legacy
            # mes_py_master / mes_py_assignments pair this used to read —
            # which is why the floor's edits never reached the check.
            # Fall back to the legacy tables for any line that has not been
            # published through the new editor, so nothing regresses.
            _pub = []
            try:
                cur.execute("""
                    SELECT id            AS py_id,
                           py_no,
                           py_name       AS description,
                           d_bit         AS register_addr,
                           COALESCE(reg_count, 1) AS register_count,
                           sensing_bits,
                           model_number  AS model_bit,
                           desired_value,
                           desired_value_2,
                           enabled
                      FROM mes_py_config_live
                     WHERE line_id = %s
                       AND COALESCE(d_bit, '') <> ''
                     -- 2026-08-22 — load the WHOLE published list, not just the
                     -- Active rows.  This same list feeds the sensor sweep, and
                     -- the operator wants every configured PY on the health
                     -- board ("list me 23 hai to 23 hi dikhne chahiye").  The
                     -- Active flag is carried per row and enforced where it
                     -- actually belongs — the alarm compare in check_py_bypass.
                     ORDER BY py_no, model_number
                """, (line_id,))
                _pub = cur.fetchall()
            except Exception as _e_pub:
                _pub = []
            if _pub:
                # One entry per PY, its per-model desired values underneath —
                # same shape check_py_bypass already consumes.
                # 2026-08-25 — key on (py_no, sensing_bits), NOT py_no alone.
                # py_no (the D-register) is NOT unique: on YNC-SS both
                # "RH HARNES BKT NG.X44" (X44) and "YTB LH EXP H.BKT" (X1023)
                # carry py_no=D423, so grouping by py_no collapsed them into one
                # and silently dropped the second PY's sensing bit — that bit
                # then never entered the sweep and sat stuck on WAITING.  The
                # sensing_bits are upper-cased so a case-only dup (x41 vs X41)
                # still folds into a single entry.
                _by_py = {}
                for r in _pub:
                    k = (r["py_no"], (r["sensing_bits"] or "").upper())
                    e = _by_py.setdefault(k, {
                        "py_id":          r["py_id"],
                        "py_no":          r["py_no"],
                        "description":    r["description"],
                        "register_addr":  r["register_addr"],
                        "register_count": r["register_count"],
                        "sensing_bits":   r["sensing_bits"],
                        "model_type":     None,
                        "side":           None,
                        "assignments":    [],
                    })
                    e["assignments"].append({
                        "model_bit":       r["model_bit"],
                        "desired_bit":     None,
                        "desired_value":   r["desired_value"],
                        "desired_value_2": r["desired_value_2"],
                        "enabled":         bool(r["enabled"]),
                    })
                self._py_configs = list(_by_py.values())
                cur.close(); conn.close()
                _ta = sum(len(x["assignments"]) for x in self._py_configs)
                _act = sum(1 for x in self._py_configs
                           for a in x["assignments"] if a.get("enabled"))
                print(f"[POKA-BYPASS] Reloaded {len(self._py_configs)} PYs from "
                      f"published config, {_ta} model rows ({_act} active -> "
                      f"alarm; rest tracked for sensor health only)")
                return

            py_map = {p["py_id"]: {**dict(p), "assignments": []} for p in pys}
            if py_map:
                cur.execute("""
                    SELECT a.py_id, a.model_id,
                           m.bit_number AS model_bit,
                           a.desired_bit, a.desired_value, a.desired_value_2
                    FROM mes_py_assignments a
                    JOIN mes_py_model_master m
                      ON m.id = a.model_id AND m.is_active = true
                    WHERE a.py_id = ANY(%s) AND m.bit_number IS NOT NULL
                """, (list(py_map.keys()),))
                for r in cur.fetchall():
                    py_map[r["py_id"]]["assignments"].append(dict(r))
            self._py_configs = list(py_map.values())
            cur.close(); conn.close()
            total_asgn = sum(len(p["assignments"]) for p in self._py_configs)
            print(f"[POKA-BYPASS] Reloaded {len(self._py_configs)} PYs, "
                  f"{total_asgn} model assignments")
        except Exception as e:
            print(f"[POKA-BYPASS] Reload error: {e}")

    @staticmethod
    def _decode_code(code: int, reg_cnt: int) -> str:
        if code == 0: return "PASS"
        if reg_cnt == 1:
            return {1: "OFF", 2: "ON"}.get(code, f"code{code}")
        return {1: "OFF,OFF", 2: "OFF,ON", 3: "ON,OFF", 4: "ON,ON"}.get(code, f"code{code}")

    @staticmethod
    def _expected_codes_1reg(dv):
        try:    dv = int(dv) if dv is not None else None
        except (ValueError, TypeError): return None
        if dv is None or dv == 0: return None
        return {dv}

    @staticmethod
    def _expected_codes_2reg(dv1, dv2):
        # cast both sides to int (DB column may be VARCHAR)
        try:    dv1 = int(dv1) if dv1 is not None else None
        except (ValueError, TypeError): dv1 = None
        try:    dv2 = int(dv2) if dv2 is not None else None
        except (ValueError, TypeError): dv2 = None
        if (dv1 is None or dv1 == 0) and (dv2 is None or dv2 == 0):
            return None
        def opts(v):
            if v is None or v == 0: return {1, 2}
            return {int(v)}
        out = set()
        for o1 in opts(dv1):
            for o2 in opts(dv2):
                if   o1 == 1 and o2 == 1: out.add(1)
                elif o1 == 1 and o2 == 2: out.add(2)
                elif o1 == 2 and o2 == 1: out.add(3)
                elif o1 == 2 and o2 == 2: out.add(4)
        return out or None

    def check_py_bypass(self, plc, shift_name: str, current_model_bit):
        """Per-cycle: compare each PY's PLC register to the user-configured
        desirable output for the active model. Fires SENSOR_BYPASS on mismatch.

        2026-05-26 — DISABLED.  Operator: "meri plc side issue nhi h jb
        m koi or code chalauga to sab shi dikhayega ye sirf tera excuse
        h hmesa".  The auto desired_value comparison was generating
        false-positive alarms whenever PLC published bit-mask values
        that the assignment table didn't anticipate.  Until the
        assignment table is rebuilt to match the actual PLC encoding
        (operator will redo this manually), suppress all auto event
        creation.  Function still runs (it tracks read health for the
        all-fail escalation), but never POSTs alarm events."""
        # 2026-08-22 — RE-ENABLED.  The condition the 2026-05-26 disable was
        # waiting on is met: desired values no longer come from the stale
        # assignment table but from the operator's own published PY Config
        # (Active rows only), and the compare now runs once per completed
        # cycle instead of twice a second.  Operator asked for exactly this:
        # set a desired, collector reads that same register live, and a
        # difference is a PY error.
        if not plc or not self._py_configs or not current_model_bit:
            return

        import re as _re
        BIT_PREFIXES  = ("X", "Y", "M", "L", "F", "B", "T", "C", "S")
        REG_RE = _re.compile(
            r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+",
            _re.IGNORECASE,
        )

        # 2026-05-23 — Track per-call read success/fail so we can raise
        # when EVERY read in this call dies (signals connection dropped).
        # PY-CHECK loop's reconnect block runs only when an exception
        # bubbles up; without this signal the silent `continue` swallowed
        # all-dead-connection cases and the thread limped forward with
        # zero successful reads for hours.
        _pyb_total_reads = 0
        _pyb_failed_reads = 0
        _pyb_last_err = ""

        for py in self._py_configs:
            raw = (py.get("register_addr") or "").upper()
            regs = REG_RE.findall(raw)
            if not regs:
                continue

            asgn = next(
                (a for a in py["assignments"] if a.get("model_bit") == current_model_bit),
                None,
            )
            if not asgn:
                continue
            # Active / Not-Active is the operator's applicability switch: an
            # inactive PY is still swept for sensor health, but it must never
            # raise a desired-vs-live alarm for this model.  `enabled` is only
            # present on published rows; legacy rows keep their old behaviour.
            if "enabled" in asgn and not asgn.get("enabled"):
                continue

            reg_cnt = int(py.get("register_count") or 1)
            if reg_cnt == 1:
                expected = self._expected_codes_1reg(asgn.get("desired_value"))
            else:
                expected = self._expected_codes_2reg(
                    asgn.get("desired_value"), asgn.get("desired_value_2"))
            if expected is None:
                continue

            for reg in regs:
                prefix = reg[0].upper()
                is_bit = prefix in BIT_PREFIXES
                _pyb_total_reads += 1
                try:
                    if is_bit:
                        vals = plc.batchread_bitunits(headdevice=reg, readsize=1)
                    else:
                        vals = plc.batchread_wordunits(headdevice=reg, readsize=1)
                    code = int(vals[0] or 0)
                except Exception as e:
                    print(f"[POKA-BYPASS] PLC read {reg} failed: {e}")
                    _pyb_failed_reads += 1
                    _pyb_last_err = f"{reg}: {str(e)[:60]}"
                    continue

                key = (py["py_no"], current_model_bit, reg)

                if is_bit:
                    _dv = asgn.get("desired_value")
                    try:    dv = int(_dv) if _dv is not None else None
                    except (ValueError, TypeError): dv = None
                    if dv == 1:
                        match = (code == 0); human_expected = "OFF"
                    elif dv == 2:
                        match = (code == 1); human_expected = "ON"
                    else:
                        continue
                    human_actual = "ON" if code == 1 else "OFF"
                else:
                    # 2026-05-26 — PASS auto-clear REMOVED.
                    # Operator: "py me kya issue h apne app shi ho rha h
                    # apne app fail no change fir bhi ok".  Earlier we
                    # treated code 0 as auto-PASS, which made the alarm
                    # flap (fail → clear → fail → clear) as PLC bounced
                    # between bad value and 0.  Now strict match only —
                    # alarm clears only when PLC actually publishes the
                    # expected value.  No more flicker.
                    match = (code in expected)
                    human_actual   = self._decode_code(code, reg_cnt)
                    human_expected = " | ".join(
                        self._decode_code(c, reg_cnt) for c in sorted(expected))

                if match:
                    # Only auto-clear when PLC publishes EXPECTED value.
                    # Transient code 0 / unrelated values do NOT clear.
                    if key in self._py_bypass_state:
                        try:
                            import json as _json
                            requests.post(
                                f"{BACKEND_URL}/api/poka-yoke/events/auto-ack",
                                json={"line_id": self.line_id,
                                      "py_no": py["py_no"],
                                      "register": reg},
                                timeout=2,
                            )
                        except Exception as e:
                            print(f"[POKA-BYPASS] Auto-ack failed: {e}")
                    self._py_bypass_state.pop(key, None)
                    continue

                # 2026-05-26 — STABILITY GUARD.
                # Operator: "ye dekh ye ho rha h baar baar isko stable kr".
                # PLC bounces between bad codes (16 → 0 → 16 → ...) caused
                # a NEW alarm event for EVERY transition — 110 events in
                # 7 h for one stuck sensor.  Fix: once an alarm has fired
                # for (py, reg), suppress further events until either
                # (a) PLC publishes the expected value (clears via the
                #     `match` branch above), or
                # (b) the operator acknowledges the alarm in the UI.
                # The stuck PLC bouncing through different bad codes is
                # the SAME ongoing fault, not new ones.
                if key in self._py_bypass_state:
                    # Already in fault state — just update last-seen code,
                    # but DON'T fire another event.
                    self._py_bypass_state[key] = code
                    continue
                self._py_bypass_state[key] = code

                all_regs_str = ",".join(regs) if len(regs) > 1 else reg
                print(f"[POKA-BYPASS] {py['py_no']} [{reg}] mismatch on model bit "
                      f"{current_model_bit}: PLC={human_actual}, expected={human_expected}")

                try:
                    import json as _json
                    requests.post(
                        f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                        json={
                            "line_id":     self.line_id,
                            "rule_id":     None,
                            "rule_type":   "SENSOR_BYPASS",
                            "alert_level": "WARNING",
                            "shift_name":  shift_name,
                            "plc_value":   str(code),
                            "context_json": _json.dumps({
                                "py_no":          py["py_no"],
                                "py_name":        py.get("description") or "",
                                "register":       reg,
                                "registers_all":  all_regs_str,
                                "register_count": reg_cnt,
                                "model_bit":      current_model_bit,
                                "actual":         human_actual,
                                "expected":       human_expected,
                                "desired_bit":    asgn.get("desired_bit"),
                            }),
                        },
                        timeout=2,
                    )
                except Exception as e:
                    print(f"[POKA-BYPASS] Event post failed: {e}")

        # 2026-05-23 — ALL-FAIL ESCALATION (mirror of track_sensors_health).
        # If every single PLC read in this call failed, the connection is
        # dead — raise so PY-CHECK loop's reconnect block actually runs.
        if _pyb_total_reads > 0 and _pyb_failed_reads == _pyb_total_reads:
            raise RuntimeError(
                f"All {_pyb_failed_reads} bypass-check reads failed "
                f"(connection appears dead). Last error: "
                f"{_pyb_last_err or 'unknown'}"
            )

    # ── Sensor Health — passive X-bit monitoring (READ-ONLY, no PLC writes) ─
    #
    # We sample every unique sensing X-bit (configured per-PY in mes_py_master)
    # roughly once per second.  For every bit we maintain a tiny in-memory
    # state struct whose key field is `last_toggle_ts` — the moment the bit
    # last changed value.  No DB persistence; state is rebuilt on collector
    # restart.  Verdict is purely passive:
    #
    #   • bit toggled within stuck threshold → status='alive'
    #   • no toggle for >900 s (15 min)      → status='stuck' + 1 email
    #   • later natural toggle               → status='alive', email flag clears
    #
    # The collector NEVER writes back to the PLC for sensor health checks.
    # If a sensor truly stops toggling, the operator sees 'stuck' in the UI
    # plus the email and physically inspects.

    def _x_state_default(self, val: int, ts: float) -> dict:
        # 2026-05-27 — Initial status is now "unknown", not "alive".
        # Operator pointed out the bug: if a sensor bit has NEVER been
        # observed toggling (e.g. line hasn't produced anything yet today),
        # the dashboard was still showing "9/9 alive" — falsely confirming
        # health on bits the collector has only READ once.  "alive" must
        # require an actual observed toggle.  Status flips to "alive" the
        # moment we see `value` change on a real PLC poll (see toggle
        # block below).  Until then, the PY rolls up as "unknown" → the
        # dashboard shows WARNING instead of OK, which matches reality.
        return {
            "value":           val,
            "last_toggle_ts":  ts,
            "first_seen_ts":   ts,       # used to suppress sliding anchor for never-toggled bits
            "ever_toggled":    False,    # flips True on first natural value change
            "status":          "unknown",  # unknown | alive | stuck
            "stuck_emailed":   False,    # one-shot guard per stuck event
        }

    def _fire_health_event(self, x_bit: str, reason: str, py: dict | None):
        """Fire a SENSOR_HEALTH event into the existing alert pipeline."""
        import json as _json
        print(f"[POKA-HEALTH] FAIL ▶ {x_bit} — {reason}")
        try:
            requests.post(
                f"{BACKEND_URL}/api/poka-yoke/events/ingest",
                json={
                    "line_id":     self.line_id,
                    "rule_id":     None,
                    "rule_type":   "SENSOR_HEALTH",
                    "alert_level": "WARNING",
                    "shift_name":  "",
                    "plc_value":   reason,
                    "context_json": _json.dumps({
                        "py_id":   (py or {}).get("py_id"),
                        "py_no":   (py or {}).get("py_no"),
                        "py_name": (py or {}).get("description"),
                        "x_bit":   x_bit,
                        "d_bit":   (py or {}).get("register_addr"),
                        "reason":  reason,
                    }),
                },
                timeout=2,
            )
        except Exception as e:
            print(f"[POKA-HEALTH] Event post failed: {e}")

    def track_sensors_health(self, plc):
        """Passive sensing-X-bit health monitor — READ ONLY.  Sample at
        ~1 Hz; track each bit's last natural-toggle timestamp in memory.
          • bit value changes              → status='alive', timer reset
          • no toggle for >stuck_threshold → status='stuck' + 1 email
          • later natural toggle on stuck  → status='alive', email guard clears

        The collector NEVER writes back to the PLC.  If a sensor stays
        stuck, the operator sees the status + receives the email and
        inspects the wiring/sensor physically.

        IMPORTANT: this function publishes a snapshot every
        `_publish_interval` seconds REGARDLESS of whether the PLC was
        reachable on this tick.  Earlier the early-return on
        `plc is None or not self._py_configs` froze the UI's "Last
        snapshot" timestamp whenever the PLC blinked or PY configs were
        still loading; now we always tick the snapshot forward so the
        operator sees that the monitor itself is alive."""
        now = time.time()
        if now - self._x_track_last < self._x_track_interval:
            return
        self._x_track_last = now

        import re as _re
        REG_RE = _re.compile(
            r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+",
            _re.IGNORECASE,
        )
        BIT_PREFIXES = ("X", "Y", "M", "L", "F", "B")

        # Map every unique sensing X-bit → first PY that references it.
        # 2026-05-15 — REMOVED the current-model filter from the collector.
        # It was incorrectly excluding every PY when `_cur_model` didn't
        # match the assignment rows (or when the assignment table linkage
        # was off), leaving the sweep cache empty and the maintenance
        # dashboard stuck on "WAITING".  The backend's `/sensor-health/`
        # endpoint already filters by current model when serving the UI,
        # so the collector's job is the simpler one: publish EVERY
        # configured sensing bit's toggle status.  This way the sweep
        # cache is always populated and the UI's model-filter handles
        # which subset to render.
        py_by_xbit: dict = {}
        for py in self._py_configs:
            for tok in REG_RE.findall((py.get("sensing_bits") or "").upper()):
                py_by_xbit.setdefault(tok, py)

        # Throttled diagnostic — once every 30 s surface the publish state
        # so the operator can see WHY "NO SNAPSHOT" might be showing.
        _diag = (now - getattr(self, "_sweep_diag_last", 0) >= 30)
        if _diag:
            self._sweep_diag_last = now

        # If no PY rules have sensing_bits configured we still publish a
        # heartbeat (empty entry list with a fresh swept_at) so the UI
        # can show "0 sensors configured" rather than "NO SNAPSHOT".  This
        # is a state, not an error — the bypass detector keeps working
        # off `register_addr` regardless of this column.
        if not py_by_xbit:
            if now - self._publish_last >= self._publish_interval:
                self._publish_health_snapshot(now, {})
                self._publish_last = now
            if _diag:
                sample = [(p.get("py_no"), p.get("sensing_bits")) for p in self._py_configs[:3]]
                print(f"[POKA-SWEEP] no sensing_bits configured "
                      f"(_py_configs={len(self._py_configs)}, sample={sample}) "
                      f"— published empty heartbeat", flush=True)
            return

        # PLC blipped (configs loaded but socket lost) — skip the bit
        # reads but DO refresh the published snapshot so the UI's
        # "Last snapshot" clock keeps moving.  Existing _x_state entries
        # are preserved, so the table doesn't go blank.
        if plc is None:
            if now - self._publish_last >= self._publish_interval:
                self._publish_health_snapshot(now, py_by_xbit)
                self._publish_last = now
            if _diag:
                print(f"[POKA-SWEEP] plc=None, published "
                      f"{len(self._x_state)} cached entries", flush=True)
            return

        read_ok = 0
        read_fail = 0
        last_err = ""
        for bit, py in py_by_xbit.items():
            prefix = bit[0].upper()
            try:
                if prefix in BIT_PREFIXES:
                    vals = plc.batchread_bitunits(headdevice=bit, readsize=1)
                else:
                    vals = plc.batchread_wordunits(headdevice=bit, readsize=1)
                val = 1 if int(vals[0] or 0) else 0
                read_ok += 1
            except Exception as _e:
                read_fail += 1
                last_err = f"{bit}: {str(_e)[:60]}"
                continue

            state = self._x_state.get(bit)
            if state is None:
                self._x_state[bit] = self._x_state_default(val, now)
                continue

            # Natural transition observed — sensor responding on its own.
            if state["value"] != val:
                state["value"]          = val
                state["last_toggle_ts"] = now
                state["stuck_emailed"]  = False
                state["ever_toggled"]   = True  # promote to verified-alive
                # First real toggle promotes unknown→alive.  Subsequent
                # toggles also clear "stuck" if the sensor recovers.
                if state["status"] in ("stuck", "unknown"):
                    state["status"] = "alive"
                # 2026-05-22 — Per-minute toggle counter for diagnostics.
                # Operator can see "is X15 firing on every cycle?"
                # without waiting for the stuck-threshold to flip.
                self._x_toggle_counts[bit] = self._x_toggle_counts.get(bit, 0) + 1

            # 2026-05-21 / 2026-05-22 — PRODUCTION-WINDOW GATE.
            # Sensors only toggle when parts move through the line.  When
            # the line is IDLE / BREAKDOWN / SCHEDULED-BREAK / SHIFT-GAP,
            # "no toggle" is the expected resting state — NOT a fault.
            # Operator complaint after 2026-05-22 morning: tea break
            # (10:00-10:10) flagged all 9 sensors as "stuck for 16 min"
            # because the earlier fix only checked `is_running` — PLC
            # often holds is_running=True during a tea break (machine
            # READY but operators away).  The fix now also honours the
            # SAME gate `_should_record_pulse()` uses for cycle counts:
            # skip stuck escalation during break + gap windows AND while
            # is_running is False.
            # Engine pushes its production-window state onto the Poka
            # instance just before each call (see PY-CHECK loop in
            # CollectorEngine._tick) so this method doesn't need to call
            # back into the engine.
            should_track = bool(getattr(self, "sensors_should_track", True))
            if not should_track:
                # Slide the toggle anchor forward so elapsed stays 0
                # while not producing.  No status transition possible.
                # 2026-05-27 — Only slide for bits that have ALREADY been
                # observed toggling at least once (status == alive or
                # was alive before stuck).  A bit we've never seen toggle
                # must remain "unknown" — sliding its anchor would mask
                # the fact that we've never verified it.  Without this,
                # a sensor wired wrong / never wired would show "alive"
                # all shift just because the line was idle.
                if state.get("ever_toggled"):
                    state["last_toggle_ts"] = now
                continue

            # Stuck > threshold → flag + 1 email.  Only while running.
            elapsed = now - state["last_toggle_ts"]
            if state["status"] == "alive" and elapsed > self._stuck_threshold_sec:
                state["status"] = "stuck"
                if not state["stuck_emailed"]:
                    self._fire_health_event(
                        bit,
                        f"{bit}:no-toggle for {int(elapsed)}s during RUNNING "
                        f"(>{self._stuck_threshold_sec}s threshold)",
                        py,
                    )
                    state["stuck_emailed"] = True

        if now - self._publish_last >= self._publish_interval:
            self._publish_health_snapshot(now, py_by_xbit)
            self._publish_last = now
            # Throttled summary so operator can confirm pipeline is alive.
            if _diag:
                msg = (f"[POKA-SWEEP] line={self.line_id} "
                       f"tracked={len(py_by_xbit)} state={len(self._x_state)} "
                       f"reads ok={read_ok} fail={read_fail}")
                if read_fail and last_err:
                    msg += f" lastErr={last_err}"
                print(msg, flush=True)

        # 2026-05-23 — CRITICAL ALL-FAIL ESCALATION.
        # Operator complaint: sensors all stuck since 09:16 (1h 36m no
        # update) despite PY-CHECK thread running.  Root cause: every
        # PLC read here was catching its own exception with `continue`,
        # so the function returned normally even when EVERY read failed.
        # PY-CHECK loop's reconnect logic depends on tick_exc being set
        # by this function — without that signal, the dead connection
        # was never replaced and stayed dead all day.
        # Fix: when read_fail equals the number of bits attempted AND
        # read_ok is zero (= dead connection, not just one bad bit),
        # raise the last exception so the PY-CHECK reconnect block
        # actually runs.  Single-bit failures still continue silently
        # (might just be a misconfigured X-bit address).
        if py_by_xbit and read_ok == 0 and read_fail == len(py_by_xbit):
            raise RuntimeError(
                f"All {read_fail} sensor reads failed (connection appears "
                f"dead). Last error: {last_err or 'unknown'}"
            )

        # 2026-05-22 — Per-minute toggle-rate log.  Helps operator verify
        # each sensor is actually firing on cycles.  Resets every 60s.
        if self._x_toggle_window_ts == 0.0:
            self._x_toggle_window_ts = now
        elif now - self._x_toggle_window_ts >= 60.0:
            if self._x_toggle_counts:
                items = sorted(self._x_toggle_counts.items())
                summary = " ".join(f"{b}={n}" for b, n in items)
                print(f"[POKA-TOGGLE] last 60s edges per bit: {summary}",
                      flush=True)
            self._x_toggle_counts.clear()
            self._x_toggle_window_ts = now

    def _publish_health_snapshot(self, now: float, py_by_xbit: dict):
        from datetime import datetime as _dt
        swept_at = _dt.now().isoformat(timespec="seconds")
        entries = []
        # 2026-05-14 — emit one row per CONFIGURED sensing X-bit (instead
        # of only those with a successful read).  Operator now sees every
        # PY's configured sensor regardless of whether the first PLC read
        # has landed; the row falls back to "unknown" status until a real
        # value is captured, which is more honest than "NO SNAPSHOT" when
        # the collector is alive but the PLC is slow / a single bit
        # address is bad.
        all_bits = set(py_by_xbit.keys()) | set(self._x_state.keys())
        for x_bit in sorted(all_bits):
            py       = py_by_xbit.get(x_bit, {}) or {}
            state    = self._x_state.get(x_bit)
            if state is not None:
                last_iso = _dt.fromtimestamp(state["last_toggle_ts"]).isoformat(timespec="seconds")
                cur_val  = state["value"]
                ago      = round(now - state["last_toggle_ts"], 1)
                status   = state["status"]
            else:
                last_iso = None
                cur_val  = None
                ago      = None
                status   = "unknown"
            # Stuck-for is the time since last toggle when status is stuck;
            # surfaced as its own field so the maintenance panel doesn't
            # have to reconstruct it from ago + status.
            stuck_for = ago if (status == "stuck" and ago is not None) else None
            entries.append({
                "bit":                 x_bit,
                "x_bit":               x_bit,
                "d_bit":               py.get("register_addr"),
                "current_value":       cur_val,
                "last_toggle_at":      last_iso,
                "last_toggle_ago_sec": ago,
                "stuck_for_sec":       stuck_for,
                "status":              status,
                "py_id":               py.get("py_id"),
                "py_no":               py.get("py_no"),
                "py_name":             py.get("description"),
                "sensing_bits":        py.get("sensing_bits"),
            })
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/poka-yoke/sensor-sweep/update",
                json={"line_id": self.line_id, "entries": entries,
                      "swept_at": swept_at},
                timeout=3,
            )
            if resp.status_code >= 400:
                # 2026-05-15 — silent 4xx was hiding sweep failures.
                # Surface the body (truncated) so the operator can see
                # what the backend rejected.
                body = (resp.text or "")[:200]
                print(f"[POKA-SWEEP] Publish HTTP {resp.status_code} "
                      f"-> {body}", flush=True)
        except Exception as e:
            print(f"[POKA-SWEEP] Publish failed: {e}", flush=True)

    def on_ok_pulse(self, sensor_ok: int, shift_name: str):
        rule = self._logic_rule("SENSOR_BYPASS")
        if not rule or sensor_ok is None:
            return
        if sensor_ok == 0:
            self._fire(rule, shift_name,
                       plc_value="sensor_ok=0",
                       ctx="OK pulse without sensor confirmation")

    def on_ng_pulse(self, shift_name: str):
        rule = self._logic_rule("CONSECUTIVE_NG")
        if not rule:
            self._ng_streak += 1
            return
        self._ng_streak += 1
        if self._ng_streak >= rule.get("threshold_count", 3):
            self._fire(rule, shift_name,
                       plc_value=str(self._ng_streak),
                       ctx=f"{self._ng_streak} consecutive NG parts")
            self._ng_streak = 0

    def on_ok_clears_ng(self):
        self._ng_streak = 0

    def check_override(self, status_code: int, shift_name: str):
        rule = self._logic_rule("MANUAL_OVERRIDE")
        if not rule:
            return
        if status_code > 7:
            self._fire(rule, shift_name,
                       plc_value=str(status_code),
                       ctx=f"Unknown status {status_code}")

    def check_cycle_fast(self, ct: float, shift_name: str):
        rule = self._logic_rule("CYCLE_TOO_FAST")
        if not rule:
            return
        if ct < 5.0:
            self._fire(rule, shift_name,
                       plc_value=f"{ct}s",
                       ctx=f"Cycle {ct}s below minimum 5s")

    def _logic_rule(self, rule_type: str):
        return next((r for r in self.logic_rules
                     if r.get("rule_type") == rule_type), None)

    def _fire(self, rule: dict, shift_name: str, plc_value: str, ctx: str):
        now = time.time()
        key = rule.get("id", rule.get("poka_yoke_name", "unknown"))
        if now - self._last_event_time.get(key, 0) < 30:
            return
        self._last_event_time[key] = now
        level     = rule.get("alert_level", "WARNING")
        rule_name = rule.get("poka_yoke_name") or rule.get("rule_name", "Unknown")
        rule_type = rule.get("rule_type", "SENSOR_BYPASS")
        print(f"[POKA-YOKE] {level} | {rule_name} | {ctx}")
        try:
            requests.post(f"{BACKEND_URL}/api/poka-yoke/events/ingest", json={
                "line_id":      self.line_id,
                "rule_id":      rule.get("id"),
                "rule_type":    rule_type,
                "alert_level":  level,
                "shift_name":   shift_name,
                "plc_value":    plc_value,
                "context_json": f'{{"message": "{ctx}"}}',
            }, timeout=2)
        except Exception:
            pass


# ============================================================
# Thread-safe PLC proxy  (2026-05-30 -- FI socket bleed-through fix)
# ============================================================
class _PLCLockProxy:
    """Serialise EVERY call into a pymcprotocol client through a
    shared threading.Lock.

    Why this exists: Mitsubishi E-series CPUs allow ~2-3 concurrent
    sessions on the same Ethernet port, but under load they will
    cross-route responses between sessions ("socket bleed-through").
    For the FI main PLC at .150 we had TWO sessions open -- the
    main pulse-poll loop AND the PY/sensor-check thread, each with
    its own Type4E -- and the PY-CHECK reads were returning value=1
    on D101 polls for hundreds of reads in a row.  Downstream that
    looked like either a drop (caught by [REG-GARBAGE-DROP]) OR a
    false L110 rising edge (firing [SHIFT-ROLLOVER] every few
    seconds), stalling FI count after 2-3 real increments.

    Fix: open ONE session, share it via this proxy.  Every method
    call acquires the lock before going to the wire, so the PLC
    sees a single ordered stream of requests and its response is
    always returned to the requester."""
    __slots__ = ("_p", "_l", "_e")
    def __init__(self, plc, lock, on_socket_error=None):
        object.__setattr__(self, "_p", plc)
        object.__setattr__(self, "_l", lock)
        # 2026-08-20 — see _wrapped: called when a read dies at the SOCKET
        # layer, so the engine can rebuild the session before the next read.
        object.__setattr__(self, "_e", on_socket_error)
    def __getattr__(self, name):
        # __getattr__ runs only when normal lookup misses, so our
        # own _p / _l (set via object.__setattr__) are found first.
        attr = getattr(self._p, name)
        if callable(attr):
            lk = self._l
            on_err = self._e
            def _wrapped(*a, **kw):
                with lk:
                    try:
                        return attr(*a, **kw)
                    except (TimeoutError, ConnectionError, OSError) as exc:
                        # 2026-08-20 — ROOT CAUSE of the FI "keeps going
                        # offline" churn (.136: 80-2500 REG-DESYNC-HEALs/day
                        # while the network pings 0% loss @1.1 ms).
                        #
                        # A socket timeout does NOT cancel the request: the PLC
                        # still answers, and that answer lands in the buffer
                        # unmatched.  The next read then returns the PREVIOUS
                        # request's frame -- D101 comes back holding D102's or
                        # the model word's value, i.e. a much LOWER number, so
                        # the register-mirror rejects it as garbage and freezes
                        # the count until the 15 s desync-heal reconnects.
                        # _send()'s pre-send drain cannot close this: it runs
                        # BEFORE the late reply arrives.
                        #
                        # So the moment a call dies at the socket layer, treat
                        # the session as unusable and let the engine's existing
                        # reconnect path build a clean one.  MCProtocolError is
                        # deliberately NOT caught -- that is a well-formed error
                        # frame, the stream is still aligned, and reconnecting
                        # on it would be pointless churn.
                        if on_err is not None:
                            try:
                                on_err(exc)
                            except Exception:
                                pass
                        raise
            return _wrapped
        return attr
    def __setattr__(self, name, value):
        if name in ("_p", "_l", "_e"):
            object.__setattr__(self, name, value)
        else:
            with self._l:
                setattr(self._p, name, value)


# ============================================================
# MAIN COLLECTOR ENGINE
# ============================================================

# Sub-machine config fields the hot-reload watcher diffs to decide a respawn.
# MUST be the exact set _spawn_sub_thread stores in cfg_snapshot — if the
# watcher compares a key the snapshot never stored, `old.get(k)` is None while
# `new.get(k)` is the real value, so every 30 s tick reads as "changed" and the
# poller thrashes (respawn loop → single-session PLC churn → missed edges).
_SUB_WATCH_KEYS = (
    "plc_ip", "plc_port", "count_bit",
    "sa_enabled", "sa_fetch_bit", "sa_part_code_addr",
    "sa_data_addr", "sa_data_len", "sa_time_addr",
    "sa_shift_data_bit", "sa_shift_reset_bit",
    "sa_ok_bit", "sa_ng_bit", "sa_result_register",
    "sa_result_ok_value", "sa_result_ng_value",
    "sa_ng_trigger_bit",
    # 2026-09-17 — the register LABELS and SCALES were missing from this list,
    # so editing them changed the stored config and nothing else: the sub
    # worker kept its old snapshot and went on writing the previous scale until
    # somebody restarted the collector.  Found while setting the load cells to
    # 0.1 (a raw 211 means 21.1) — the config said 0.1 and the data kept
    # arriving at 1.0.  A setting that silently does nothing is worse than one
    # that is missing.
    "sa_register_scales", "sa_register_names",
)


class CollectorEngine:
    DB_UPDATE_INTERVAL     = 2
    HOURLY_UPDATE_INTERVAL = 5
    SPEED_CHECK_INTERVAL   = 1

    # Build tag — bumped whenever we touch the count or status pipeline so
    # the running collector's identity is unambiguous from the log.  When
    # the operator says "kal ka fix gayab ho gaya" we can grep _collector.log
    # for [BUILD] and instantly tell which revision is live.
    BUILD_TAG = "2026-06-16-r2 | JSON write-buffer: status/process/SA-data zero-loss on DB-down + exactly-once replay (dedup ledger) | L110 END+START pulse 5s (start-of-shift register zero -> kills cross-shift carry-over) + DB-down resilient boot (cache+file-lock) | 2026-06-04-r2 First-part-of-shift CT naps from shift_start (anti Part#1 phantom ~1hr CT) main OK+NG + sub chart+audit | Upper-Rail ATOMIC D101+D102 read (anti OK<->NG swap) | FI reg-read AUTO-HEAL on comm-break | cross-type 5s chatter-reject OK+NG | NG-dwell-debounce 300ms + BREAK auto-inject + break-override v2 + cycle-bound NG + L108-watchdog + NG-edge-preserve + OK-edge-trust + count-skip logs"

    def __init__(self, init_cfg: dict):
        line_id = init_cfg["line_id"]

        # 2026-06-16 — durable JSON write-buffer (zero data-loss on DB-down).
        # One per-line JSONL queue; failed event writes land here and replay
        # exactly-once on reconnect.  Safe to init before the lock/config.
        _init_write_queue(line_id)

        # ── Cross-PC singleton lock ──────────────────────────────
        # Acquire BEFORE loading config so that a duplicate launch
        # from another LAN PC fails fast with a clear error, instead
        # of half-initialising and racing the legitimate collector.
        # The lock auto-releases via heartbeat-staleness if this
        # process crashes.
        self._lock = CollectorSingletonLock(line_id)
        self._lock.acquire()    # raises RuntimeError if dupe

        print(f"[BUILD] CollectorEngine {self.BUILD_TAG}", flush=True)
        print(f"[ENGINE] Loading config for line_id={line_id} from DB...")
        self.cfg = load_line_config(line_id)
        # 2026-09-08 — carry per-collector overrides that aren't in the DB config.
        # `defer_hourly` (set in the collector_*.py CONFIG) tells the engine to
        # NOT write hour_*_ok/_ng/_actual — those are owned by the API
        # hourly_sync (recomputed from ct_log) for edge-fed lines (Sub-Assembly)
        # whose per-slot in-memory counters can't be trusted (e.g. PLC read via
        # edge, or a mid-shift restart zeroing them). Only the plan is written.
        if init_cfg.get("defer_hourly"):
            self.cfg["defer_hourly"] = True
        print(f"[ENGINE] Config loaded: {self.cfg['line_name']}")
        # 2026-05-29 - Log count mode at startup so operator can verify
        # admin UI's register/bit selection actually took effect.
        _cm_startup = self.cfg.get("count_mode") or "bit"
        if _cm_startup == "register":
            print(f"[ENGINE]    Count mode: REGISTER  "
                  f"(OK={self.cfg.get('ok_data_register') or '?'} "
                  f"NG={self.cfg.get('ng_data_register') or 'disabled'})",
                  flush=True)
        else:
            print(f"[ENGINE]    Count mode: BIT  "
                  f"(OK={self.cfg.get('ok_bit') or 'OK'} "
                  f"NG={self.cfg.get('ng_bit') or 'NG'})",
                  flush=True)
        print(f"[ENGINE]    PLC   : {self.cfg['plc_ip']}:{self.cfg['plc_port']}")
        print(f"[ENGINE]    Table : {self.cfg['table_name']}")
        print(f"[ENGINE]    Shifts: {list(self.cfg['shifts'].keys())}")
        print(f"[ENGINE]    Breaks: {len(self.cfg['breaks'])}")
        print(f"[ENGINE]    Models: {len(self.cfg['models'])}")
        print(f"[ENGINE]    Poka Yoke rules: {len(self.cfg['poka_rules'])}")

        self.ct   = CycleTimeTracker(
            ideal_ct=self.cfg["ideal_ct"],
            max_ct=self.cfg["max_ct"],
        )
        self.poka = PokaYokeMonitor(self.cfg["poka_rules"], line_id)

        self._plc: pymcprotocol.Type4E = None
        self._db   = None
        self._plc_ok = False
        self._db_ok  = False

        self.ok_total = 0
        self.ng_total = 0
        self.ok_shift = 0
        self.ng_shift = 0

        self._last_ok_state = 0
        self._last_ng_state = 0
        self._last_ok_time  = None
        self._last_ng_time  = None
        # 2026-05-24 — Persists across NG events to compute inter-NG CT.
        # Hydrated from DB on _connect_db so first NG after restart isn't 0.
        self._last_ng_time_for_ct = None
        # 2026-05-27 — Unified any-pulse-to-any-pulse anchor.  Either
        # L108 (OK) or L109 (NG) commit advances this timestamp.  CT
        # written to both `mes_l6_final_inspection` and the chart's
        # `_ct_log` table is `now - _last_any_pulse_dt`, regardless of
        # which bit just fired.  Operator: "L108 aaya, fir NG L109
        # aaya — unke beech ka time hi NG ka CT.  Bit kuch bhi aaye,
        # calculate karke write kar."  Hydrated from DB on _connect_db
        # so the first pulse after a restart isn't a junk huge number.
        self._last_any_pulse_dt        = None
        self._last_ct_for_chart_ok     = None
        self._last_ct_for_chart_ng     = None
        # 2026-05-27 — Raw cycle counter that advances on EVERY rising
        # edge (L108 or L109), independent of break / is_running gating.
        # Drives `cycle_seq` for ct_log rows so each pulse gets a unique
        # X-axis position on the chart.  Hydrated from MAX(cycle_seq) in
        # ct_log at boot.  Production counter (ok_shift / ng_shift) is
        # separate and still gated for clean OEE math.
        self._raw_cycle_seq            = 0
        # Unconditional L108-edge observer (2026-05-16) — captures every
        # rising edge regardless of status gating.  _update_status uses
        # this as a truth-detector to override PLC's D6005 when the
        # register lies (publishes IDLE while production is actually
        # firing L108 pulses).  See L108_TRUTH_WINDOW_SEC.
        self._last_ok_edge_observed = 0.0
        # ── NG state for the new cycle-bound counter (2026-05-16 v3) ──
        # _ng_seen_since_last_ok  → flag set on L109 rising edge, cleared
        #                            on L108 commit.  L108 looks at this
        #                            to label the cycle OK or NG.
        # _ng_consec_count        → consecutive NG-labeled cycles count.
        #                            Used by the stuck-bit guard to flip
        #                            to OK after MES_NG_STUCK_CYCLES.
        # _ng_stuck_alarm_fired   → one-shot flag so we log "presumed
        #                            stuck" exactly once per stuck event.
        self._ng_seen_since_last_ok  = False
        self._ng_consec_count        = 0
        self._ng_stuck_alarm_fired   = False
        # 2026-05-23 Option-C — bi-directional ladder filter state.
        # When L109 rises and the look-back check passes (no L108 within
        # NG_LADDER_WINDOW_SEC before it), we DEFER the NG commit by
        # the same window and watch for an L108 follow-up.  If L108
        # fires within the window → ladder echo, drop.  Otherwise →
        # real operator NG, flush.  Single slot — bursts collapse to
        # one pending event (the most recent overrides).
        self._pending_ng             = None   # {ts, part_code} or None
        self._pulse_gap     = self.cfg["pulse_gap"]
        # Legacy NG-hold state kept for compatibility with the
        # _load_shift_from_db restore path (referenced indirectly via
        # `self.ct.speed_loss` snapshot).  Not used by the new
        # cycle-bound counter — see _ng_seen_since_last_ok above.
        self._ng_high_since        = None
        self._ng_counted_this_high = False

        self._cur_model      = 1
        self._cur_model_name = (list(self.cfg["models"].values())[0]
                                if self.cfg["models"] else "Unknown")
        self._cur_status      = 0
        self._cur_status_name = "IDLE"

        self._cur_shift      = None
        self._shift_id       = None
        self._shift_start_ts = None

        # ── AUTO L110 shift-reset pulse (2026-05-30) ──────────────────
        # At each REAL shift end the collector itself pulses the per-machine
        # shift_reset_bit (L110) so the PLC zeroes its OK/NG register —
        # replacing the old "HMI/operator drives L110" assumption.  A single
        # monotonic epoch counter is bumped on real shift-end (and by the
        # manual test flag below); every register machine fires exactly ONE
        # pulse per epoch on its OWN MC connection (the MAIN / Final
        # Inspection machine here via self._plc, each sub in its own thread).
        # Starts at 0 with done=0 so a restart NEVER fires a spurious pulse.
        # Bit-mode and semi-auto machines never enter the pulse path (guarded
        # by count_mode=register AND shift_reset_bit) => zero regression.
        self._shift_reset_epoch       = 0     # bumped on real shift end / manual flag
        self._fi_reset_epoch_done     = 0     # last epoch the MAIN (FI) machine pulsed
        # Set by the counting path when a Final cycle commits; the PY-CHECK
        # thread consumes it to run the desired-vs-live compare exactly once
        # per part.  Plain bool assignment is GIL-atomic, no lock needed.
        self._py_check_due = False
        # 2026-08-24 — carry-over baseline.  When the collector is DOWN at
        # shift start it misses the L110 pulse that zeroes D101, so on the next
        # (re)start the register still holds the previous shift(s) count.  If
        # the seed value is physically impossible for the time elapsed since
        # shift start, we treat it as a baseline and report ok_shift =
        # rawD101 - base.  0 = no offset (the normal, register-was-reset case).
        # Cleared automatically when the register genuinely resets below it.
        self._ok_reg_base = 0
        self._fi_reset_pulse_on_since = None  # monotonic ts while FI L110 held ON, else None
        self._L110_PULSE_SEC          = 5.0   # 2026-06-16: 3.0->5.0s ON per pulse
                                              # (operator: longer hold so the PLC
                                              #  reliably latches the register zero)
        # 2026-06-11 — RESET re-snap probe (register mode).  Armed by the
        # DESYNC-HEAL after it forces a fresh socket; if that fresh, frame-
        # desync-immune socket STILL reads below the frozen peak and holds/
        # climbs, the peak is stale (a real register reset the L110 watch
        # missed) and the count is snapped DOWN to the live register.
        self._reg_resnap_armed        = False
        self._reg_resnap_low1         = None  # first fresh-socket low read (probe)
        # 2026-06-11 — When the count legitimately DROPS (startup seed, reset
        # re-snap, or an L110 honoured reset), the next dashboard write must
        # bypass the ok_count=GREATEST(...) never-decrease guard and write the
        # EXACT live value — otherwise the DB row stays pinned at the previous
        # shift's stale peak even after the in-memory count followed the reset.
        # One-shot: set on the drop, cleared right after the write.
        self._reg_force_db_exact      = False
        # Manual test-trigger: drop a file at this path and the collector
        # bumps the epoch once (one pulse on every register machine) then
        # deletes the file — lets the operator verify the reset wiring
        # without waiting for a real shift end.
        self._L110_FLAG_PATH = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "L110_PULSE_NOW.flag")
        self._l110_flag_mtime    = None   # mtime of last-consumed flag file
        self._last_l110_flag_chk = 0.0    # throttle for the flag-file stat

        self._loss = {
            "breakdown": 0.0, "quality":     0.0, "setup": 0.0,
            "material":  0.0, "others":      0.0, "speed": 0.0,
            "change_over": 0.0,
        }
        self._last_status_check = time.time()

        self._plan_completed  = 0
        self._last_plan_calc  = 0.0

        self._hourly_data: dict    = {}
        self._cur_hour_key: str    = None
        self._last_hourly_write    = time.time()

        self._last_db_write   = time.time()
        self._last_speed_chk  = time.time()
        self._last_display    = time.time()
        self._last_break_log  = time.time()
        self._last_plc_warn   = time.time()
        self._last_db_check   = time.time()
        # 2026-05-13 — admins can change zone breaks mid-shift via the
        # Production Admin Panel.  Without periodic reload, the new
        # break window never fires until tomorrow.  See _reload_breaks_from_db.
        self._last_break_reload = 0.0
        # 2026-07-16 — hot config reload: re-read plan/slot/model config from
        # the DB every 60s so admin PLAN/SLOT/MODEL edits apply WITHOUT a
        # collector restart (see _maybe_reload_line_config).
        self._cfg_last_reload  = 0.0

        # Machine-process sampling — admin-configured per-process targets
        # under Admin → Production → Machines → ④ Process Config.
        # Cache the list, reload from DB every 30s (so admin edits go
        # live without a collector restart), sample every 60s and write
        # to mes_machine_process_log → drives the Process Graphs page.
        self._machine_processes: list = []
        self._last_process_reload = 0.0
        self._last_process_sample = 0.0

        # Poka-yoke / sensor check throttle (2026-05-12).
        # check_d_registers + check_py_bypass + track_sensors_health
        # each loop through 20-40 PLC bit reads.  At ~30-50 ms per read
        # that's up to 2 s per call, blocking the main poll loop.  L108
        # OK pulses can be as short as 100-200 ms, so when the main loop
        # ran at ~1 Hz instead of 33 Hz, brief pulses were missed and
        # the user saw "200 sec ki one big cycle aaya, 7-8 missing"
        # symptom.  Running these once every 2 s is enough for PY
        # bypass detection (operator latency is seconds anyway) and
        # keeps the pulse poll loop fast.
        self._last_py_check = 0.0

        # IDLE-dwell timer (2026-05-12).  YNC-SS PLC ladder oscillates
        # D6005 between raw=0 (between cycles, transient) and raw=16
        # (during cycle execution, decoded → RUNNING).  Without a dwell
        # the dashboard flips IDLE ↔ RUNNING every 4-6 s during normal
        # production.  Hold the previous status for N seconds before
        # committing an IDLE transition; if any non-IDLE state arrives
        # within the window, cancel the pending transition.
        self._pending_idle_since = None
        # 12s was too short — operator saw repeated IDLE flickers when
        # inter-cycle gap stretched to 14-18s.  Bumped to 25s so the
        # dashboard stays RUNNING through any normal between-part pause
        # but still surfaces a real stop within half a minute.
        # 2026-05-29 — Bumped 25 → 45s.  Operator complaint: "status
        # fluctuations sab hata de".  DB showed 7s/9s IDLE blips
        # leaking through the 25s dwell when PLC briefly went RUNNING
        # mid-dwell and back to IDLE.  45s suppresses these residual
        # tails while still committing real long stops within a minute.
        self.IDLE_DWELL_SEC      = 45.0

        # ── CT log buffer ─────────────────────────────────────────
        self._ct_pending_log: list = []
        self._ct_log_table_ready   = False
        # status=-2 means "not yet successfully read".  Earlier we
        # initialised to 0 (= IDLE), which made the dashboard briefly
        # show IDLE during boot before the first PLC read landed —
        # operator interpreted this as a "ghost IDLE" event.  -2 is
        # treated as no-op by _update_status.
        self._last_plc_data   = {
            "ok_bit": 0, "ng_bit": 0,
            "status": -2, "model":  1,
            "sensor_ok": None,
        }

        # Break accumulator — tracks total break seconds elapsed in current shift
        self._break_seconds_acc  = 0.0
        self._cur_break_start_ts = None

        # ── Sub-machines (auxiliary PLCs on the same line) ─────────
        # Each gets its own MC connection + count-bit rising-edge poller.
        # Inherits model/shift/status from this parent engine via plain
        # attribute reads (GIL-safe). Writes to mes_submachine_ct_log.
        self.submachines      = load_submachines(self.cfg.get("main_plc_id"))
        self._sub_threads     = []           # legacy list of all spawned threads (alive + dead)
        self._sub_stop        = threading.Event()   # legacy "stop everything" event (still works)
        # 2026-05-15 — `_stop` was missing on CollectorEngine, so every
        # PY-check thread crashed at `self._stop.wait(2.0)` the moment
        # it started.  That's why `track_sensors_health` never ran and
        # the sensor sweep cache stayed empty no matter what.  Mirror
        # of `_sub_stop` semantically — both get .set() on shutdown.
        self._stop            = threading.Event()
        # 2026-05-30 — Shared lock that serialises EVERY call into the
        # main PLC socket (see _PLCLockProxy doc).  The PY-CHECK thread
        # used to own a separate Type4E session on the same Mitsubishi
        # CPU which caused socket bleed-through; now both threads share
        # self._plc and this lock guarantees ordered single-session
        # access.
        self._plc_lock        = threading.Lock()
        # Per-sub-machine worker registry — enables dynamic add/remove/edit
        # without restarting the collector. Reload loop populates this from
        # mes_plc_configs every 30 s.
        # Shape: {sub_id: {"stop": Event, "thread": Thread, "cfg_snapshot": dict}}
        self._sub_workers: dict = {}
        self._cur_part_code   = ""
        self._last_fi_fetch   = 0     # rising-edge tracker for fi_fetch_bit

        # ── Live status-table tracking (2026-05-31, additive/observe-only) ──
        # Periodic per-machine health table the operator eyeballs to verify
        # "sab shi h ya nhi".  PURE DISPLAY: it only READS existing engine /
        # sub-poller state and writes nothing to the count / break / semi-auto
        # paths, and runs in its own daemon thread so it never touches the
        # 30 ms pulse cadence -> zero regression.
        self._machine_status: dict = {}   # sub_id -> {"name","plc_ok","ts"}
        self._last_video_saved_ts  = 0.0  # stamped when an edge-clip webhook lands
        self._status_tbl_ts        = 0.0  # throttle marker for the table loop

        # ── OT polling cache ───────────────────────────────────────
        # mes_lines.ot_active_shift is set by the Zone/Admin toggle.
        # We re-read every 5 s so the collector flips into OT mode
        # automatically without restart.
        self._ot_cache_ts    = 0.0
        self._ot_active_val  = None
        print(f"[ENGINE]    Sub-machines: {len(self.submachines)}")
        for _s in self.submachines:
            print(f"[ENGINE]       - id={_s['id']}  "
                  f"{_s['plc_ip']}:{_s['plc_port']}  "
                  f"bit={_s['count_bit']}  ideal={_s['ideal_ct']}s  "
                  f"({_s['machine_name']})")

    # ----------------------------------------------------------
    # CONNECTIONS
    # ----------------------------------------------------------

    def _on_plc_socket_error(self, exc):
        """A read on the shared main-PLC session died at the socket layer.

        2026-08-20 — Mark the session unusable so the next poll rebuilds it.
        A timed-out MC request is still answered by the PLC, and that orphan
        reply would be handed to the FOLLOWING read (D101 returning D102's or
        the model word's value), which the register-mirror then rejects as
        garbage and freezes the count until the slow desync-heal fires.
        Rebuilding straight away costs a few ms on this LAN and skips that
        whole window.  Only the flag is set here: the existing reconnect path
        owns the actual close/connect, so the never-die loop is untouched.
        """
        if getattr(self, "_plc_ok", False):
            print(f"[PLC-SOCKET] {type(exc).__name__} on "
                  f"{self.cfg.get('plc_ip')} -- session dropped so the orphan "
                  f"reply can't desync the next read", flush=True)
        self._plc_ok = False
        self._plc_died_at = time.time()

    def _connect_plc(self) -> bool:
        try:
            if self._plc:
                try: self._plc.close()
                except: pass
            # 2026-05-30 — Wrap the live Type4E in _PLCLockProxy so the
            # PY-CHECK thread can SHARE this single socket via the lock,
            # eliminating the dual-session bleed-through that was making
            # FI D101 reads return value=1 (and firing false L110 shift
            # rollovers).  Initial setaccessopt + connect + ping happen
            # on the raw object so they don't redundantly acquire the
            # lock during startup.
            _raw = pymcprotocol.Type4E()
            _raw.connect(self.cfg["plc_ip"], self.cfg["plc_port"])
            _raw.batchread_wordunits(
                headdevice=self.cfg["status_addr"], readsize=1)
            self._plc = _PLCLockProxy(_raw, self._plc_lock,
                                      self._on_plc_socket_error)
            self._plc_ok = True
            print(f"[PLC] Connected {self.cfg['plc_ip']}:{self.cfg['plc_port']} (locked-proxy)")
            return True
        except Exception as e:
            self._plc_ok = False
            print(f"[PLC] Connection failed: {e}")
            return False

    def _connect_db(self) -> bool:
        try:
            self._db = psycopg2.connect(**DB_CONFIG)
            self._db.cursor().execute("SELECT 1")
            self._db_ok = True
            print(f"[DB] Connected")
            # 2026-05-24 — Hydrate last NG timestamp so the first NG row
            # written after a collector restart has a real inter-NG CT
            # (not 0).  Same idea as the sub-machine hydrate.
            try:
                _hc = self._db.cursor()
                _hc.execute(
                    f"SELECT MAX(ts) FROM {self.cfg['table_name']}_ct_log "
                    "WHERE record_date = CURRENT_DATE AND is_ng = true"
                )
                _r = _hc.fetchone()
                if _r and _r[0]:
                    self._last_ng_time_for_ct = _r[0].timestamp()
                    print(f"[MAIN] hydrated last_ng_for_ct = {_r[0]}",
                          flush=True)
                # 2026-05-27 — Hydrate the unified any-pulse anchor.
                # Take the most recent timestamp from mes_l6_final_inspection
                # regardless of OK/NG so the first pulse after restart
                # measures against the actual last pulse on disk, not 0.
                _hc.execute(
                    "SELECT MAX(ts) FROM mes_l6_final_inspection "
                    "WHERE record_date = CURRENT_DATE"
                )
                _r2 = _hc.fetchone()
                if _r2 and _r2[0]:
                    self._last_any_pulse_dt = _r2[0]
                    print(f"[MAIN] hydrated last_any_pulse_dt = {_r2[0]}",
                          flush=True)
                # 2026-05-28 - Hydrate raw_cycle_seq filtered by CURRENT
                # shift so on restart the chart continues this shift's
                # numbering (starts from #1 per shift, not per day).
                # If shift not yet known, defer to first shift-change.
                try:
                    if self._cur_shift:
                        _sh = self._cur_shift if not self._cur_shift.startswith("GAP") else "GAP"
                        _hc.execute(
                            f"SELECT COALESCE(MAX(cycle_seq), 0) "
                            f"FROM {self.cfg['table_name']}_ct_log "
                            f"WHERE record_date=CURRENT_DATE AND shift_name=%s",
                            (_sh,)
                        )
                    else:
                        _hc.execute(
                            f"SELECT COALESCE(MAX(cycle_seq), 0) "
                            f"FROM {self.cfg['table_name']}_ct_log "
                            f"WHERE record_date = CURRENT_DATE"
                        )
                    _r3 = _hc.fetchone()
                    if _r3 and _r3[0] is not None:
                        self._raw_cycle_seq = int(_r3[0])
                        print(f"[MAIN] hydrated raw_cycle_seq = "
                              f"{self._raw_cycle_seq} (shift={self._cur_shift})", flush=True)
                except Exception:
                    pass
                _hc.close()
            except Exception as _e:
                print(f"[MAIN] hydrate failed: {_e}")
                try: self._db.rollback()
                except Exception: pass
            return True
        except Exception as e:
            self._db_ok = False
            print(f"[DB] Connection failed: {e}")
            return False

    def _ensure_db_connection(self):
        if self._db_ok and self._db:
            try:
                self._db.cursor().execute("SELECT 1")
                return True
            except Exception:
                self._db_ok = False
                try: self._db.close()
                except: pass
                self._db = None

        try:
            self._db = psycopg2.connect(**DB_CONFIG)
            self._db_ok = True
            self._shift_id = None
            print(f"[DB] Reconnected")
            return True
        except Exception as e:
            self._db_ok = False
            print(f"[DB] Reconnection failed: {e}")
            return False

    # ----------------------------------------------------------
    # WORKING SECONDS
    # ----------------------------------------------------------

    def _get_shift_start_timestamp(self, shift_name: str,
                                   record_date: date) -> float:
        scfg = self.cfg["shifts"].get(shift_name)
        if not scfg:
            return None
        start_time = scfg["start_time"]
        if isinstance(start_time, str):
            start_time = dt_time(*map(int, start_time.split(":")))
        return datetime.combine(record_date, start_time).timestamp()

    def _baseline_key(self):
        sh = self._cur_shift
        if not sh or sh.startswith("GAP"):
            return None
        rec = getattr(self, "_cur_shift_record_date", None) or datetime.now().date()
        return (int(self.cfg["line_id"]), rec, sh)

    def _load_reg_baseline(self) -> int:
        # 2026-09-05 — Register baseline / carry-over offset DISABLED permanently
        # at the operator's explicit, repeated instruction ("baseline wala code
        # hta de, permanent"). The offset heuristic mis-seeded values (both
        # inflation and under-count — e.g. YMC showed 6 vs a real D101 of 1261),
        # so ok_count now mirrors the RAW register directly (no subtraction).
        # CAVEAT: correct only when the register resets each shift (machine-side
        # L110). A line whose register does NOT reset will show a cumulative
        # (inflated) count until its L110 shift-reset is fixed on the machine.
        # Reversible: delete this `return 0` (and the one in _seed_carryover_base).
        return 0
        k = self._baseline_key()
        if not k:
            return 0
        try:
            c = _db_conn(); cur = c.cursor()
            cur.execute("SELECT base FROM mes_reg_baseline WHERE line_id=%s "
                        "AND record_date=%s AND shift_name=%s", k)
            r = cur.fetchone(); cur.close(); c.close()
            return int(r[0]) if r else 0
        except Exception:
            return 0

    def _save_reg_baseline(self, base: int) -> None:
        k = self._baseline_key()
        if not k:
            return
        try:
            c = _db_conn(); cur = c.cursor()
            cur.execute("INSERT INTO mes_reg_baseline (line_id, record_date, "
                        "shift_name, base) VALUES (%s,%s,%s,%s) ON CONFLICT "
                        "(line_id, record_date, shift_name) DO UPDATE SET base=EXCLUDED.base",
                        (k[0], k[1], k[2], int(base)))
            c.commit(); cur.close(); c.close()
        except Exception as _e:
            print(f"[REG-BASELINE] save failed: {_e}", flush=True)

    def _seed_carryover_base(self, raw_seed: int) -> int:
        """Baseline for a mid-shift seed whose register was never reset.

        Returns raw_seed when it is physically impossible to have produced that
        many parts in the time elapsed since the current shift started (a
        collector that missed the shift-start L110 pulse), else 0.  The bound
        uses 1.5 s / part — nothing real is faster — so a genuine live count can
        never trip it; only multi-shift carry-over can.

        2026-09-05 — DISABLED permanently (returns 0) per operator instruction;
        see the note in _load_reg_baseline. No baseline is ever seeded now, so
        the count follows the raw register. Reversible: delete this `return 0`.
        """
        return 0
        try:
            sh = self._cur_shift
            if not sh or sh.startswith("GAP"):
                return 0
            rec = getattr(self, "_cur_shift_record_date", None) or datetime.now().date()
            start_ts = self._get_shift_start_timestamp(sh, rec)
            if not start_ts:
                return 0
            elapsed = time.time() - start_ts
            if elapsed < 300:          # <5 min in — too early to judge
                return 0
            max_possible = elapsed / 1.5
            if raw_seed > max_possible:
                print(f"[REG-BASELINE] seed={raw_seed} impossible in "
                      f"{elapsed/60:.0f} min (max ~{max_possible:.0f}); register "
                      f"missed the shift-start reset -- treating {raw_seed} as "
                      f"baseline, count shown from 0", flush=True)
                return int(raw_seed)
        except Exception as _e:
            print(f"[REG-BASELINE] check skipped: {_e}", flush=True)
        return 0

    def _working_seconds(self) -> int:
        """
        Return elapsed productive seconds (excludes startup delay + all break time).
        Uses a running _break_seconds_acc so break-exit never causes a plan jump.
        """
        if not self._cur_shift or self._cur_shift.startswith("GAP"):
            return 0
        if not self._shift_start_ts:
            return 0
        scfg = self.cfg["shifts"].get(self._cur_shift)
        if not scfg:
            return 0

        # Hardcoded 5-min startup delay — see STARTUP_DELAY_MIN comment
        # near _is_in_startup_delay.  Must match so plan freeze and
        # status override use the same window.
        startup_delay = self.STARTUP_DELAY_MIN * 60
        now_ts        = time.time()

        in_break, current_break = self._is_break()

        if in_break:
            # Mark break start only on first tick inside this break
            if self._cur_break_start_ts is None:
                self._cur_break_start_ts = now_ts
                print(f"[BREAK] Started '{current_break}', freezing plan")
            # Freeze plan at the second the break started
            elapsed_to_break = max(0.0, self._cur_break_start_ts - self._shift_start_ts - startup_delay)
            # round() absorbs float drift so plan hits the exact target at shift end
            # (e.g. 27899.9999s / 15s would otherwise floor to 1859 instead of 1860)
            return max(0, int(round(elapsed_to_break - self._break_seconds_acc)))
        else:
            # Commit just-ended break duration to accumulator
            if self._cur_break_start_ts is not None:
                break_duration = now_ts - self._cur_break_start_ts
                self._break_seconds_acc += break_duration
                print(f"[BREAK] Ended, duration={break_duration:.1f}s acc={self._break_seconds_acc:.1f}s")
                self._cur_break_start_ts = None
            # Normal: wall-clock elapsed minus startup minus total break time
            elapsed = max(0.0, now_ts - self._shift_start_ts - startup_delay)
            # round() absorbs float drift so plan hits the exact target at shift end
            # (e.g. 27899.9999s / 15s would otherwise floor to 1859 instead of 1860)
            working = max(0, int(round(elapsed - self._break_seconds_acc)))

            # ── OT cap: if we're in the OT window (past shift end), freeze
            # working seconds at the shift's normal duration so plan stops
            # incrementing. Actual count keeps going but plan stays fixed.
            ot = self._check_ot_active()
            if ot == self._cur_shift and self._is_in_ot_window(self._cur_shift):
                s_end = scfg.get("end_time")
                if isinstance(s_end, str):
                    s_end = dt_time(*map(int, s_end.split(":")))
                s_start = scfg.get("start_time")
                if isinstance(s_start, str):
                    s_start = dt_time(*map(int, s_start.split(":")))
                # Shift duration in seconds (normal, excluding OT)
                s_min = s_start.hour * 60 + s_start.minute
                e_min = s_end.hour * 60 + s_end.minute
                if e_min <= s_min:
                    e_min += 1440  # crosses midnight
                shift_dur = (e_min - s_min) * 60
                cap = max(0, int(round(shift_dur - startup_delay - self._break_seconds_acc)))
                return min(working, cap)

            return working

    # ----------------------------------------------------------
    # PHANTOM-DUMP RATE GUARD  (2026-05-30 v2)
    # ----------------------------------------------------------
    # ── Signed-16bit / desync-garbage guard (2026-07-16) ─────────────
    # Mitsubishi word reads come back SIGNED 16-bit, so a register that has
    # passed 32767 (genuine overflow OR — the common case here — MC frame-
    # desync / socket-contention garbage) reads NEGATIVE and used to flow
    # raw into counts / cycle_seq / status (the YJC negative-cycle_seq and
    # YMC 1-sec-negative incidents).  A shift-reset count register never
    # legitimately reaches 32000 in one shift, so any read that high is
    # treated as garbage: return None so the caller SKIPS this poll and
    # HOLDS the last good count (self-heals on the next clean read — never
    # negative, never a permanent freeze).  `_u16` alone (unsigned, no
    # reject) is used for status/model where a stale-but-decodable code is
    # harmless.  Applied at every raw register read (main OK/NG, subs,
    # recliner, status/model) so the fix is engine-wide, not just main OK.
    _REG_SANE_MAX = 32000

    def _u16(self, raw):
        """Signed 16-bit word -> unsigned (0..65535).  None passes through."""
        return None if raw is None else (int(raw) & 0xFFFF)

    def _reg_count(self, raw):
        """Unsigned count read, or None (caller skips + holds last good) when
        the read is missing or looks like signed-overflow / desync garbage."""
        v = self._u16(raw)
        if v is None or v >= self._REG_SANE_MAX:
            return None
        return v

    # 2026-09-19 — sub-machine register-mirror reads only.  The 32000 ceiling
    # above assumes the count register is zeroed every shift.  On the SA-4WAY
    # sub-machines it is not — the shift-reset pulse does not clear D6001 there,
    # so the register is a running total — and once a genuine count passed
    # 32000 every read was thrown away and the machine stopped logging for
    # good: YHB-SA-4WAY's four sub-machines went blank on 16-18 Sep (last good
    # read ok_reg=31997; the "self-heals on the next clean read" above never
    # comes).  The sister lines' subs sit at ~4,500 and were on course for the
    # same wall.  The sub path does not need the ceiling: it already refuses
    # any jump over +200 in one poll as garbage/wrap and rebaselines on a
    # drop, so a desync read cannot write phantom rows.  It takes the full
    # unsigned 16-bit range; 0xFFFF (-1, the classic desync value) is still
    # refused.  Main-line register reads keep _reg_count() unchanged.
    def _sub_reg_count(self, raw):
        v = self._u16(raw)
        if v is None or v >= 0xFFFF:
            return None
        return v

    def _near_shift_boundary(self, window_min=15):
        """True only within `window_min` minutes of a configured shift
        start_time.  A genuine count reset (register -> 0) can ONLY occur at a
        real shift boundary; the RESET-RE-SNAP is gated by this so that a
        low register read arriving MID-SHIFT (socket garbage after a
        DESYNC-HEAL) can never be mistaken for a reset and zero the dashboard
        count.  Fail-open (True) if shifts are unknown so a genuine reset is
        never blocked by a config gap."""
        try:
            shifts = self.cfg.get("shifts") or {}
            if not shifts:
                return True
            now = datetime.now()
            now_min = now.hour * 60 + now.minute
            for scfg in shifts.values():
                st = scfg.get("start_time")
                if st is None:
                    continue
                st_min = st.hour * 60 + st.minute
                diff = abs(now_min - st_min)
                diff = min(diff, 1440 - diff)      # wrap around midnight
                if diff <= window_min:
                    return True
            return False
        except Exception:
            return True

    def _rate_clamp_climb(self, reg, last, now_val, accept_attr):
        """Physics-based phantom-dump guard for a climbing DATA register.

        The Final-Inspection line makes at most ONE real part per cycle
        (~`_cycle` s, never closer than `_min_gap` s), so a single poll
        may only count as many parts as the elapsed wall-time since the
        last counted part allows.  A jump needing MORE time than has
        actually passed is a garbage spike from PLC socket contention
        (the 2026-05-30 14k / 2222 incidents) → clamped to the
        time-justified amount, the rest hard-dropped.

        Guarantees (zero regression on the count that drives every
        dashboard graph):
          * a real small step (Δ ≤ reg_sane_step, normally +1/+2) is
            ALWAYS counted once `_min_gap` has passed — small increments
            bypass the rate math entirely, so a real part is never lost
            to a slow cycle;
          * a genuine catch-up after a long gap (reconnect / restart
            downtime) is accepted up to what the elapsed time justifies,
            and the register mirror re-diffs next poll so any transient
            clamp self-heals — no part is permanently lost;
          * a big garbage spike (Δ > reg_sane_step) can't beat the clock:
            with NO max(1,) floor, a fast socket-contention jump counts
            ZERO (fixes the v2 slow-drip that leaked one phantom part per
            `_min_gap`), no matter how many polls it persists.

        Returns (accept, mirror_value):
          * accept >= 1 -> count `accept` parts, mirror `mirror_value`
            (== last+accept, NEVER the raw garbage number);
          * accept == 0 -> jump arrived faster than physically possible;
            caller must not count/mirror this poll (defer).

        Tunables: cfg['reg_min_gap_s'] (min believable gap, default
        ok_ng_pulse_min_gap or 3 s) and cfg['ideal_ct'] (normal cycle,
        the rate divisor).  `_min_gap` is always well below the real
        cycle so a real part is never rejected.
        """
        _delta = now_val - last
        _min_gap = float(self.cfg.get("reg_min_gap_s") or 0)
        if _min_gap <= 0:
            _min_gap = float(self.cfg.get("ok_ng_pulse_min_gap") or 0) or 3.0
        _min_gap = max(2.0, _min_gap)
        _cycle = float(self.cfg.get("ideal_ct") or 0)
        if _cycle < _min_gap:
            _cycle = _min_gap
        _now_mono = time.monotonic()
        _last_acc = getattr(self, accept_attr, None)
        _elapsed = None if _last_acc is None else (_now_mono - _last_acc)
        # ── SANE-STEP split (2026-05-30 v3) ──────────────────────────
        # A genuine Final-Inspection increment is +1 (rarely +2 when a
        # poll straddled two cycles).  Trust those small steps directly —
        # only gate them by _min_gap so a sub-second double-read of the
        # SAME physical part can't count twice.  A BIG jump (the 1199 /
        # 12336 socket-contention garbage) is real ONLY if enough
        # wall-time has actually elapsed to make that many parts at the
        # ideal cycle; otherwise it is dropped to ZERO.  No max(1,) floor
        # — that floor was the v2 leak that let ONE phantom part through
        # every _min_gap (the slow drip that inflated hour_1605_1715_ok
        # while ok_count stayed flat).  Either way the register MIRROR
        # self-heals on the next poll (it re-diffs the live register
        # against our mirror), so a transient clamp never permanently
        # loses a real part.
        _SANE_STEP = int(self.cfg.get("reg_sane_step") or 2)
        if _delta <= _SANE_STEP:
            if _elapsed is not None and _elapsed < _min_gap:
                _accept = 0          # same part re-read within _min_gap
            else:
                _accept = _delta     # genuine small step -> always count
        else:
            if _elapsed is None:
                _accept = 0          # big jump on first climb = garbage
            else:
                _accept = min(_delta, int(_elapsed // _cycle))
        if _accept >= 1:
            setattr(self, accept_attr, _now_mono)
            if _accept < _delta:
                # partial clamp: some of this jump was time-justified,
                # the rest is garbage -> fold the dropped amount into the
                # throttled summary, not a line per poll.
                self._note_reg_noise(reg, now_val, _delta - _accept)
            return _accept, last + _accept
        # Nothing accepted -> pure garbage spike or too-soon repeat.
        # Folded into the throttled [REG-NOISE] summary so the console
        # stays readable instead of a [REG-RATE] line every ~0.5 s.
        self._note_reg_noise(reg, now_val, _delta)
        return 0, last

    def _note_reg_noise(self, reg, now_val, dropped):
        """Fold dropped phantom register reads into a throttled, readable
        one-line summary (~ every 10 s) instead of a log line on every
        garbage poll.  Keeps the collector console clean while still
        proving the filter is working and the real count is untouched."""
        if not hasattr(self, "_reg_noise"):
            self._reg_noise = {}
        st = self._reg_noise.get(reg)
        if st is None:
            st = {"drops": 0, "parts": 0, "lo": now_val, "hi": now_val,
                  "last": time.monotonic()}
            self._reg_noise[reg] = st
        st["drops"] += 1
        st["parts"] += int(dropped)
        st["lo"] = min(st["lo"], now_val)
        st["hi"] = max(st["hi"], now_val)
        _now = time.monotonic()
        if _now - st["last"] >= 10.0:
            print(f"[REG-NOISE] {reg}: filtered {st['drops']} garbage "
                  f"read(s) in {_now - st['last']:.0f}s (junk values "
                  f"{st['lo']}..{st['hi']}), phantom parts blocked: "
                  f"{st['parts']} -- real count untouched", flush=True)
            st.update({"drops": 0, "parts": 0, "lo": now_val,
                       "hi": now_val, "last": _now})

    def _count_src_label(self, side: str = "ok") -> str:
        """Human label for the count SOURCE — the data register in
        register mode, else the configured bit.  Never emits the bare
        'L108'/'L109' literal (operator: "L108 use nahi karni kisi bhi
        machine pe").  Used for both the console logs and the per-machine
        audit row's bit_address column so L108 never appears anywhere."""
        _cm = (self.cfg.get("count_mode") or "bit").lower()
        if _cm == "register":
            if side == "ng":
                return self.cfg.get("ng_data_register") or "NG-REG"
            return self.cfg.get("ok_data_register") or "OK-REG"
        if side == "ng":
            return self.cfg.get("ng_bit") or "NG"
        return self.cfg.get("ok_bit") or "OK"

    # ----------------------------------------------------------
    # PLC READ
    # ----------------------------------------------------------

    def _read_plc(self) -> dict:
        """Resilient PLC read.

        Each register read is wrapped in its own try so a hiccup on
        `ok_bit` no longer discards a successful `status` read.

        Spam guard: when `_plc_ok` is already False (connection dead),
        we DON'T retry the dead socket on every 30-ms tick — the
        underlying `pymcprotocol.Type4E` object holds a TCP socket
        that's been forcibly closed (WinError 10054).  Re-issuing
        reads against it just spams the log and burns CPU.  Instead
        we return cached `_last_plc_data` and let the main-loop
        reconnect path open a fresh socket.

        Print throttle: errors are logged ONCE per dead-period (on
        the live→dead transition) and ONCE per fresh `_connect_plc`
        failure — never per-cycle."""
        if self._plc is None:
            return self._last_plc_data

        # Spam guard: socket is dead, don't even try.  Caller's job
        # is to call _connect_plc() (main loop does this every 2 s).
        if not self._plc_ok:
            return self._last_plc_data

        data = dict(self._last_plc_data)
        failures = 0
        first_err = None

        def _safe_word(addr, key, keep_last):
            nonlocal failures, first_err
            try:
                v = self._plc.batchread_wordunits(headdevice=addr, readsize=1)
                if v:
                    data[key] = self._u16(v[0])   # signed->unsigned (status/model code)
                elif not keep_last:
                    data[key] = 0
            except Exception as e:
                failures += 1
                if first_err is None:
                    first_err = (key, e)

        def _safe_bit(addr, key, keep_last):
            nonlocal failures, first_err
            # 2026-05-23 — RETRY-ONCE on transient bit-read failure.
            # Single-packet glitches over LAN (~1-2 ms loss / TCP retransmit)
            # were silently setting L108/L109 to 0, dropping rising edges.
            # Operator: "merge ho hi kyu rhi hai... koii merging nhi" — root
            # cause is read miss, not edge logic.  Two reads × ~5 ms is
            # still well under the 30 ms poll budget and the PLC pulse is
            # multi-hundred-ms HIGH, so a retry sees the true bit state.
            _last_err = None
            for _try in range(2):
                try:
                    v = self._plc.batchread_bitunits(headdevice=addr, readsize=1)
                    if v:
                        data[key] = int(v[0])
                    elif not keep_last:
                        data[key] = 0
                    return
                except Exception as e:
                    _last_err = e
                    if _try == 0:
                        continue
            # both reads failed — count + log + (silently zero unless keep_last)
            failures += 1
            if first_err is None:
                first_err = (key, _last_err)
            _now = time.time()
            _attr = f"_safe_bit_last_log_{key}"
            _last = getattr(self, _attr, 0)
            if _now - _last > 5.0:
                setattr(self, _attr, _now)
                print(f"[PLC-HALF-FAIL] {key}={addr} read failed 2x: "
                      f"{str(_last_err)[:80]}  (rising edge happening "
                      f"RIGHT NOW would be lost). Throttled 5s.", flush=True)

        # Status: keep last on empty/error so we don't accidentally
        # publish IDLE after a transient miss.
        _safe_word(self.cfg["status_addr"], "status", keep_last=True)

        # 2026-05-29 - Count mode: 'bit' (L108/L109 rising edge) or
        # 'register' (D-register value increment).  Sub-machines are
        # NEVER affected — they have their own poller loop.
        _mode = (self.cfg.get("count_mode") or "bit").lower()
        if _mode == "register":
            # Register mode: read word values, compute virtual rising
            # edges based on value increment.  The rest of the code
            # (rising-edge detection, row writes) works unchanged.
            data["ok_delta"] = 0
            data["ng_delta"] = 0
            data["ok_reg_value"] = 0
            data["ng_reg_value"] = 0
            # ════════════════════════════════════════════════════════
            # 2026-05-30 — EXACT-MIRROR counting (operator redesign).
            # ════════════════════════════════════════════════════════
            # Operator spec (Hinglish): "ui me jo data registers h, count
            # HMESA match hoga ... forget l108/l109 ... na freeze na reject."
            #   • Dashboard shift count = the live register value, EVERY
            #     poll.  self.ok_shift / self.ng_shift are SET (not +=
            #     accumulated) to the reading here, so they can never drift
            #     from the PLC regardless of run-state or skipped reads.
            #   • The old MAX_SANE_REG_DELTA garbage-reject — which froze
            #     the count at 775 when a bled "212" arrived — is REMOVED.
            #     That bleed came from the DUPLICATE collector hammering the
            #     PLC's 2-3 socket limit; the real fix is ONE collector +
            #     staggered reads, NOT value rejection.  We now trust the
            #     register absolutely (operator: "na freeze na reject").
            #   • A virtual rising edge (data["ok_bit"]=1 + ok_delta) is
            #     still emitted on every increment so the per-part audit
            #     rows, ct_log and the VIDEO webhook keep firing.  The
            #     "+2-or-more = skip the clip" rule lives in _update_counts
            #     (video only — the COUNT still mirrors the jump exactly).
            #   • _ok_shift_peak / _ng_shift_peak hold the running shift's
            #     high-water mark so the shift-reset-bit rollover archives
            #     the true closing count even if the PLC zeroes the register
            #     a tick before we read the bit.
            _ok_reg = self.cfg.get("ok_data_register")
            if _ok_reg:
                _ok_v = {"v": None}
                def _grab_ok():
                    nonlocal failures, first_err
                    try:
                        _r = self._plc.batchread_wordunits(headdevice=_ok_reg, readsize=1)
                        if _r:
                            _ok_v["v"] = self._reg_count(_r[0])   # unsigned + desync-garbage reject
                    except Exception as _e:
                        # 2026-06-03 — AUTO-HEAL on comm break.  Count this failed
                        # count-register read toward `failures` so the existing
                        # all-fail handler (failures>=4, 2 cycles) drops & rebuilds
                        # the socket -> reconnect resumes reading, NO manual restart.
                        # Before, `except: pass` swallowed a WinError 10054 here, and
                        # register mode only had status+model in `failures` (max <4),
                        # so the socket was never marked dead, _plc_ok stayed True,
                        # the count froze, and only Ctrl+C+restart recovered it.
                        # Count logic is unchanged: _ok_v stays None on failure, so
                        # this poll still skips the count update exactly as before.
                        failures += 1
                        if first_err is None:
                            first_err = ("ok_reg", _e)
                _grab_ok()
                if _ok_v["v"] is not None:
                    _raw_val = int(_ok_v["v"])
                    _last = getattr(self, "_last_ok_reg_value", None)
                    # 2026-08-24 — carry-over baseline offset.  Everything below
                    # (ok_shift, cycle_seq, deltas) works off _now_val, so
                    # subtracting the baseline here once keeps the whole mirror
                    # in "since-shift-start" space with no other change.
                    _base = getattr(self, "_ok_reg_base", 0)
                    if _last is None:
                        # First read after (re)start — decide if the register
                        # carries a missed-reset from a previous shift.  A base
                        # saved earlier THIS shift wins over the time-heuristic,
                        # so a late restart (when the elapsed time makes the big
                        # value look plausible again) still applies the offset.
                        _base = self._load_reg_baseline()
                        if _base == 0:
                            _base = self._seed_carryover_base(_raw_val)
                            if _base:
                                self._save_reg_baseline(_base)
                        else:
                            print(f"[REG-BASELINE] {_ok_reg} loaded saved "
                                  f"baseline={_base} for this shift", flush=True)
                        self._ok_reg_base = _base
                    elif _base and _raw_val < _base:
                        # Register dropped below our baseline => a genuine reset
                        # happened (L110 at a real shift end); the offset is
                        # stale, drop it so the new shift counts from the metal.
                        print(f"[REG-BASELINE] {_ok_reg} reset below baseline "
                              f"({_raw_val} < {_base}) -- clearing offset",
                              flush=True)
                        self._ok_reg_base = _base = 0
                    _now_val = max(0, _raw_val - _base)
                    data["ok_reg_value"] = _now_val
                    _apply_ok = True   # mirror this read into ok_shift/_last?
                    if _last is None:
                        # First read after (re)start — snap dashboard to PLC.
                        print(f"[REGISTER-MIRROR] {_ok_reg} seed={_now_val} "
                              f"-> ok_shift snapped (was {self.ok_shift})",
                              flush=True)
                        data["ok_bit"] = 0
                        self._ok_pending_high = None
                        self._ok_drop_garbage_streak = 0
                        self._reg_force_db_exact = True   # snap DB to live, even down
                        # 2026-05-31 — Arm graph-point backfill.  The snap
                        # leaves a downtime hole: every part the PLC counted
                        # while THIS collector was down has no cycle row, so
                        # the count runs ahead of the chart / per-part list.
                        # Remember this seed; the NEXT poll confirms it is a
                        # real value (holds/climbs) or aborts (craters = a
                        # garbage first read).  No rows here — the actual
                        # backfill runs in the poll loop once the shift is
                        # known and non-GAP.  See _reg_backfill_after_resync.
                        self._reg_resync_seed = _now_val
                        self._reg_resync_pending = True
                    elif _now_val > _last:
                        # Register climbed → +N parts.  RATE-REALITY guard
                        # (2026-05-30 v2).  v1 (confirm-on-next-poll) FAILED at
                        # 16:23: socket-contention garbage that REPEATED for >=2
                        # polls got "confirmed" and re-dumped ~1000 ct_log rows,
                        # poisoning hour_1605_1715_ok (3->2222).  v2 bounds the
                        # per-poll delta by elapsed wall-time (see
                        # _rate_clamp_climb): a real single part is never lost,
                        # garbage spikes can't beat the clock however long they
                        # persist.  ok_shift mirrors only the ACCEPTED amount,
                        # never the raw garbage value.
                        _accept, _mirror = self._rate_clamp_climb(
                            _ok_reg, _last, _now_val, "_last_ok_accept_mono")
                        self._ok_pending_high = None
                        self._ok_drop_garbage_streak = 0
                        self._reg_resnap_armed = False   # normal climb → heal done
                        self._reg_resnap_low1  = None
                        if _accept >= 1:
                            data["ok_delta"] = _accept
                            data["ok_bit"]   = 1
                            _now_val = _mirror
                        else:
                            data["ok_bit"] = 0
                            _apply_ok = False
                    elif _now_val < _last:
                        # 2026-05-30 v3 — STRICT downward guard (replaces
                        # the streak-3 ACCEPT path which still leaked).
                        # FI's Type4E socket on .150 is shared (main poll
                        # loop + the PY-CHECK thread), and the resulting
                        # bleed-through returns spurious near-zero reads
                        # at a high rate (live 22:08:  793->1, 24->1,
                        # 17->1, 20->1, ...).  Anything that lets a low
                        # read into ok_shift causes the rate-clamp to
                        # then re-climb cycle-by-cycle from that low
                        # baseline, writing N audit rows for every real
                        # part (live: ~3x inflation in mes_l6_*).
                        # STRICT RULE: an in-shift register drop is
                        # HONOURED only when L110 is actively pulsing
                        # (genuine collector- or operator-driven reset).
                        # Any other drop is bleed-through and rejected.
                        # If an operator wipes D101 by hand WITHOUT
                        # pulsing L110, the in-memory count freezes at
                        # the peak until L110 fires or the shift
                        # transitions.  That is the correct trade-off
                        # for not letting socket noise destroy the
                        # dashboard mid-shift.
                        _l110_active = (
                            getattr(self, "_fi_reset_pulse_on_since",
                                    None) is not None)
                        if _l110_active:
                            print(f"[REGISTER-MIRROR] {_ok_reg} drop "
                                  f"{_last}->{_now_val} (L110 reset in "
                                  f"progress) -- ok_shift mirrors down",
                                  flush=True)
                            data["ok_bit"] = 0
                            self._ok_pending_high = None
                            self._ok_drop_garbage_streak = 0
                            self._reg_resnap_armed = False   # L110 reset handled
                            self._reg_resnap_low1  = None
                            self._reg_force_db_exact = True  # snap DB down to live
                        elif (getattr(self, "_reg_resnap_armed", False)
                              and self._near_shift_boundary()):
                            # 2026-07-25 — CLOCK GATE: only honour a RESET
                            # RE-SNAP within ~15 min of a real shift boundary.
                            # Mid-shift a low read is socket garbage, never a
                            # genuine reset, so it falls through to the strict
                            # garbage-drop guard below (ok_shift HELD at peak)
                            # -- this is what stops the dashboard count from
                            # collapsing to a spurious near-zero mid-shift.
                            # 2026-06-11 — RESET RE-SNAP.  Armed by the DESYNC-
                            # HEAL, which just forced a fresh, frame-desync-
                            # immune socket.  A genuinely reset register reads
                            # LOW on the fresh socket and then HOLDS/CLIMBS
                            # (real new-shift production); leftover socket junk
                            # does NOT.  Confirm with TWO fresh reads (2nd >=
                            # 1st), then snap the count DOWN to the live
                            # register so the dashboard follows the reset
                            # instead of freezing at the previous shift's peak.
                            # NO backfill — the new shift's parts log their own
                            # ct_log rows the normal way.
                            _lo1 = getattr(self, "_reg_resnap_low1", None)
                            if _lo1 is None:
                                # first fresh read — probe only, keep holding peak
                                self._reg_resnap_low1 = _now_val
                                data["ok_bit"] = 0
                                _apply_ok = False
                            elif _now_val >= _lo1:
                                # 2nd fresh read held/climbed below peak →
                                # CONFIRMED real reset.  _apply_ok stays True so
                                # the EXACT-MIRROR block below writes _now_val
                                # into ok_shift + _last_ok_reg_value.
                                print(f"[REG-RESET-RESNAP] {_ok_reg} fresh "
                                      f"socket confirms RESET: peak {_last} is "
                                      f"stale, live={_now_val} (probe={_lo1}) "
                                      f"-- ok_shift snapped DOWN to live, no "
                                      f"backfill", flush=True)
                                self._ok_shift_peak = _now_val   # new baseline
                                self._reg_resnap_armed = False
                                self._reg_resnap_low1  = None
                                self._ok_drop_garbage_streak = 0
                                self._ok_garbage_since = None
                                self._ok_pending_high = None
                                self._reg_force_db_exact = True  # snap DB down to live
                                data["ok_bit"] = 0
                            else:
                                # fresh read fell BELOW the probe → erratic, not
                                # a clean count.  Re-baseline, keep holding peak.
                                self._reg_resnap_low1 = _now_val
                                data["ok_bit"] = 0
                                _apply_ok = False
                        else:
                            _gs = int(getattr(self,
                                              "_ok_drop_garbage_streak", 0)) + 1
                            self._ok_drop_garbage_streak = _gs
                            if _gs == 1:
                                # Wall-clock start of THIS garbage run, used
                                # by the desync auto-heal at the end of
                                # _read_plc.  Reset implicitly: any accepted /
                                # equal / L110 read zeroes the streak above, so
                                # the next garbage starts a fresh _gs==1 here.
                                self._ok_garbage_since = time.time()
                                # 2026-08-22 — COLLAPSE = heal NOW, don't wait.
                                # A desynced read doesn't drift, it craters:
                                # live case was D101 257 -> 1 mid-shift while
                                # the line was running.  Sitting on that for the
                                # normal garbage window costs real seconds, and
                                # the count that finally lands carries the whole
                                # stall as cycle time — cycle #258 was recorded
                                # at 21.7 s against a ~12 s part, so its clip was
                                # cut 23 s long and the operator watched ~10 s of
                                # an already-finished cycle.
                                # A genuine register reset only happens at a
                                # shift edge (handled by the L110 / re-snap paths
                                # above), so a mid-shift collapse is ALWAYS junk:
                                # backdate the window so the heal at the end of
                                # this same poll fires immediately.
                                # 2026-08-22 (later) — widened.  The first cut
                                # only healed a CRATER (< 25 % of the last
                                # value).  An hour of live data showed 19 desync
                                # heals against 8 collapses: the other 11 read
                                # back something merely lower, sat out the full
                                # garbage window, and still stretched a cycle.
                                # Every mid-shift drop is already treated as
                                # junk by the guard above (the count is HELD,
                                # never lowered), so there is nothing extra to
                                # lose by rebuilding the socket on the first one
                                # instead of the tenth — a reconnect costs ~0.1 s
                                # on this LAN.  A real reset still only counts at
                                # a shift edge, which is what the boundary check
                                # keeps protecting.
                                try:
                                    _collapse = (_last >= 20)
                                    if _collapse and not self._near_shift_boundary():
                                        _win_now = float(
                                            self.cfg.get("reg_desync_reconnect_s") or 15.0)
                                        self._ok_garbage_since = time.time() - _win_now
                                        print(f"[REG-COLLAPSE] {_ok_reg} {_last}->"
                                              f"{_now_val} mid-shift drop -- healing "
                                              f"NOW instead of holding {_win_now:.0f}s",
                                              flush=True)
                                except Exception:
                                    pass
                            # Throttled log: first 3, then 1 per 60.
                            if _gs <= 3 or _gs % 60 == 0:
                                print(f"[REG-GARBAGE-DROP] {_ok_reg} "
                                      f"{_last}->{_now_val} (no L110) "
                                      f"rejected x{_gs} -- ok_shift held "
                                      f"at {_last}", flush=True)
                            data["ok_bit"] = 0
                            _apply_ok = False
                    else:
                        data["ok_bit"] = 0
                        self._ok_pending_high = None
                        self._ok_drop_garbage_streak = 0
                        self._reg_resnap_armed = False   # register steady → heal
                        self._reg_resnap_low1  = None
                    # 2026-05-31 — Graph-point backfill confirm/abort (single
                    # point, runs the poll AFTER a snap).  A real seed holds
                    # or climbs (_now_val >= seed) -> arm the backfill up to
                    # the seed.  A garbage-high first read craters on this
                    # poll (_now_val < seed, the strict drop-guard above held
                    # ok_shift) -> abort, never backfill an untrusted seed.
                    # Cleared after exactly one poll; _last is not None gates
                    # out the snap poll itself.
                    if (getattr(self, "_reg_resync_pending", False)
                            and _last is not None):
                        self._reg_resync_pending = False
                        _seed = int(getattr(self, "_reg_resync_seed", _last)
                                    or _last)
                        self._do_reg_backfill_to = (
                            _seed if _now_val >= _seed else None)
                    # EXACT MIRROR — register mode OWNS ok_shift here.
                    # _update_counts must NOT also += in register mode.
                    # Skipped only while a suspect jump is being deferred, so a
                    # single garbage read can never move the dashboard or the
                    # shift-peak archive value.
                    if _apply_ok:
                        self.ok_shift = _now_val
                        self._last_ok_reg_value = _now_val
                        # High-water mark for the shift-rollover archive —
                        # advanced only on an ACCEPTED read.
                        if _now_val > getattr(self, "_ok_shift_peak", 0):
                            self._ok_shift_peak = _now_val
                else:
                    data["ok_bit"] = 0
            else:
                data["ok_bit"] = 0
            # NG side — identical exact-mirror model.
            _ng_reg = self.cfg.get("ng_data_register")
            if _ng_reg:
                _ng_v = {"v": None}
                def _grab_ng():
                    nonlocal failures, first_err
                    try:
                        _r = self._plc.batchread_wordunits(headdevice=_ng_reg, readsize=1)
                        if _r:
                            _ng_v["v"] = self._reg_count(_r[0])   # unsigned + desync-garbage reject
                    except Exception as _e:
                        # 2026-06-03 — AUTO-HEAL (same as _grab_ok): feed the
                        # all-fail handler so a dead/half-open FI socket is rebuilt
                        # on reconnect without a manual restart.  Value logic
                        # unchanged: _ng_v stays None on failure.
                        failures += 1
                        if first_err is None:
                            first_err = ("ng_reg", _e)
                _grab_ng()
                if _ng_v["v"] is not None:
                    _now_val = int(_ng_v["v"])
                    data["ng_reg_value"] = _now_val
                    _last = getattr(self, "_last_ng_reg_value", None)
                    self._ng_pending_high = None
                    # 2026-07-04 — NG PHANTOM GUARD.  Real rejects arrive ONE at a
                    # time (Δ +1, rarely +2).  A sudden big UP-jump (+16/+32 — the
                    # bit-4/bit-5 socket-contention / 2nd-MC-session byte-shift junk)
                    # is NEVER a real reject.  So:
                    #   • first read      -> seed (mirror D102);
                    #   • EQUAL or DROP   -> mirror EXACTLY (shift reset / down-sync;
                    #                        never latches, exact-mirror preserved);
                    #   • small climb (Δ ≤ ng_sane_step) -> count it (real reject);
                    #   • big climb       -> REJECT + HOLD last good NG, folded into
                    #                        [REG-NOISE].  Self-heals: when D102 reads
                    #                        the real value again the small delta from
                    #                        the held baseline is counted normally.
                    # Latch-proofing: if the SAME big value persists ng_confirm_polls
                    # times it is a genuine new baseline -> synced, so a real change
                    # can never be held forever (the old drop-hold latch bug).
                    _NG_SANE = int(self.cfg.get("ng_sane_step") or 3)
                    if _last is None:
                        self.ng_shift = _now_val
                        self._last_ng_reg_value = _now_val
                        data["ng_bit"] = 0
                    elif self.cfg.get("line_id") in _NG_STRICT_LINES:
                        # 2026-08-01 — STRICT NG guard (Y17-L7 first; see
                        # _NG_STRICT_LINES).  The 10-min monitor showed D102 on
                        # Y17 both (a) dropping to 0 on a dropped frame -> the old
                        # `<= _last` path mirrored it DOWN instantly (NG 1->0->1
                        # flip), and (b) reading the OK register (~300 while real
                        # NG≈1) on a pipelined-frame mis-read, which stayed
                        # constant for a whole cycle and so passed confirm-polls.
                        # A REAL reject only ever steps +1..+ng_sane_step and NG
                        # NEVER decreases mid-shift, so:
                        #   Δ == 0        -> hold, nothing to do
                        #   0 < Δ <= sane -> genuine reject, count it
                        #   Δ  >  sane    -> ALWAYS garbage (incl. the ~300 OK
                        #                    bleed); HOLD last-good, never confirm
                        #                    (a real NG never jumps up in bulk)
                        #   Δ  <  0       -> follow ONLY if the SAME value persists
                        #                    ng_confirm_polls times (a genuine
                        #                    shift-reset to 0); else HOLD last-good.
                        # Zero effect on any line not in _NG_STRICT_LINES.
                        _delta = _now_val - _last
                        if _delta == 0:
                            data["ng_bit"] = 0
                            self._ng_jump_cand = None
                        elif 0 < _delta <= _NG_SANE:
                            data["ng_delta"] = _delta
                            data["ng_bit"]   = 1
                            self.ng_shift = _now_val
                            self._last_ng_reg_value = _now_val
                            self._ng_jump_cand = None
                        elif _delta > _NG_SANE:
                            data["ng_bit"] = 0
                            self._ng_jump_cand = None
                            self._note_reg_noise(_ng_reg, _now_val, _delta)
                        else:   # drop -> NG never decreases mid-shift.
                            # A real NG reset (register -> 0) can ONLY happen at a
                            # shift boundary, so a mid-shift drop is ALWAYS a
                            # dropped/garbage frame: HOLD last-good.  Near a real
                            # boundary, follow a reset only once the low value
                            # PERSISTS (a one-poll blip still can't zero it).
                            data["ng_bit"] = 0
                            if self._near_shift_boundary():
                                _cand = getattr(self, "_ng_jump_cand", None)
                                _cand = (_now_val, (_cand[1] + 1)
                                         if (_cand and _cand[0] == _now_val) else 1)
                                self._ng_jump_cand = _cand
                                if _cand[1] >= int(self.cfg.get("ng_confirm_polls") or 5):
                                    self.ng_shift = _now_val
                                    self._last_ng_reg_value = _now_val
                                    self._ng_jump_cand = None
                            else:
                                self._ng_jump_cand = None
                                self._note_reg_noise(_ng_reg, _now_val, _delta)
                    elif _now_val <= _last:
                        data["ng_bit"] = 0
                        self.ng_shift = _now_val
                        self._last_ng_reg_value = _now_val
                        self._ng_jump_cand = None
                    elif (_now_val - _last) <= _NG_SANE:
                        data["ng_delta"] = _now_val - _last
                        data["ng_bit"]   = 1
                        self.ng_shift = _now_val
                        self._last_ng_reg_value = _now_val
                        self._ng_jump_cand = None
                    else:
                        _cand = getattr(self, "_ng_jump_cand", None)
                        _cand = (_now_val, (_cand[1] + 1) if (_cand and _cand[0] == _now_val) else 1)
                        self._ng_jump_cand = _cand
                        data["ng_bit"] = 0
                        if _cand[1] >= int(self.cfg.get("ng_confirm_polls") or 5):
                            self.ng_shift = _now_val
                            self._last_ng_reg_value = _now_val
                            self._ng_jump_cand = None
                        else:
                            self._note_reg_noise(_ng_reg, _now_val, _now_val - _last)
                    if self.ng_shift > getattr(self, "_ng_shift_peak", 0):
                        self._ng_shift_peak = self.ng_shift
                else:
                    data["ng_bit"] = 0
            else:
                data["ng_bit"] = 0
        else:
            # Bit mode (default, legacy).
            _safe_bit(self.cfg["ok_bit"], "ok_bit", keep_last=False)
            # NG bit DISABLED (proven phantom-prone).  Re-enable by
            # uncommenting + removing the data["ng_bit"]=0 below.
            data["ng_bit"] = 0
            # _safe_bit(self.cfg["ng_bit"],  "ng_bit", keep_last=False)
            data["ok_delta"] = 1   # bit mode = always +1 per edge
            data["ng_delta"] = 1
            data["ok_reg_value"] = 0
            data["ng_reg_value"] = 0
        # Model: keep last on miss so a one-off blip doesn't change model.
        _safe_word(self.cfg["model_addr"], "model", keep_last=True)
        if self.cfg.get("sensor_ok_addr"):
            _safe_bit(self.cfg["sensor_ok_addr"], "sensor_ok", keep_last=False)

        # All-fail handling — only mark dead after 2 cycles of complete
        # failure to avoid bouncing on single-packet glitches.
        any_succeeded = failures < 4   # at least one of 4-5 reads got through
        if not any_succeeded:
            self._plc_fail_streak = getattr(self, "_plc_fail_streak", 0) + 1
            if self._plc_fail_streak >= 2:
                # Log ONCE per dead-period — only on the live→dead flip.
                if self._plc_ok:
                    key, err = first_err if first_err else ("?", "unknown")
                    print(f"[PLC] All reads failed × 2 cycles "
                          f"({key}: {err}) — marking dead, will reconnect.")
                self._plc_ok = False
                self._plc_died_at = time.time()
                # Force-close the dead socket so the next reconnect
                # opens a fresh one (pymcprotocol won't auto-rebind on
                # a hung TCP socket otherwise).
                try:
                    if self._plc:
                        self._plc.close()
                except Exception:
                    pass
                self._plc_fail_streak = 0
        else:
            self._plc_fail_streak = 0
            self._plc_ok = True

        # ════════════════════════════════════════════════════════════
        # 2026-06-01 — DESYNCED-SESSION AUTO-HEAL  (FI main line only)
        # ════════════════════════════════════════════════════════════
        # PROVEN root cause of the "count frozen at the peak while the
        # PLC keeps climbing" hang (live: dashboard stuck 406, a fresh
        # Type4E read of the SAME D101 returned a clean, climbing 533/534):
        # an MC-protocol FRAME DESYNC on this long-lived shared socket.  A
        # partial/late response left bytes in the buffer, so every later
        # read is byte-shifted into junk (D101 -> 1/16).  pymcprotocol does
        # NOT raise on this (the shifted bytes still parse to an int), so
        # `failures` stays 0, the all-fail handler above never fires,
        # _plc_ok was just set True again -> the socket is NEVER rebuilt
        # and the count freezes (the drop-guard correctly refuses to
        # mirror the junk, but nothing ever clears the bad socket).
        #
        # Cure = what a fresh process gets for free: drop & rebuild the
        # socket.  Triggered ONLY after the OK register has returned
        # nothing but rejected garbage for a sustained wall-clock window
        # (transient bleed self-heals in << 1 s and never reaches here),
        # and ONLY through the existing dead-socket reconnect path — no
        # L110 pulse, the process stays alive (never-die intact).  We do
        # NOT reseed: _last is preserved, so the fresh socket's first real
        # read climbs the count back up through the normal rate-clamp/
        # mirror (no fabrication), and any junk that still slips through is
        # < _last and stays rejected.  A genuine line STOP never triggers
        # this — a stopped station reads _now == _last (silent, no drop,
        # no streak), not garbage.  Register/main-line only: the streak is
        # only ever incremented in the register-mode OK branch.
        if int(getattr(self, "_ok_drop_garbage_streak", 0)) > 0:
            _g_since = getattr(self, "_ok_garbage_since", None)
            _win = float(self.cfg.get("reg_desync_reconnect_s") or 15.0)
            if _g_since is not None and (time.time() - _g_since) >= _win:
                print(f"[REG-DESYNC-HEAL] {self.cfg.get('ok_data_register')} "
                      f"returned only garbage for "
                      f"{time.time() - _g_since:.0f}s "
                      f"(streak x{int(self._ok_drop_garbage_streak)}) -- "
                      f"socket frame-desynced (a fresh read is clean); "
                      f"forcing {self.cfg['plc_ip']} reconnect to resync the "
                      f"count.", flush=True)
                self._plc_ok = False
                self._plc_died_at = time.time()
                try:
                    if self._plc:
                        self._plc.close()
                except Exception:
                    pass
                self._ok_drop_garbage_streak = 0
                self._ok_garbage_since = None
                # 2026-06-11 — Arm the RESET re-snap probe.  The reconnect this
                # heal triggers yields a guaranteed-fresh socket whose reads are
                # frame-desync-immune.  If those fresh reads STILL come back
                # BELOW the frozen peak and hold/climb, the peak is stale: the
                # register was genuinely RESET (a shift rollover the L110 watch
                # missed), not socket junk -- and the drop branch snaps the
                # count DOWN to the live register.  Cleared the instant a normal
                # (climb / equal / L110) read resumes.
                self._reg_resnap_armed = True
                self._reg_resnap_low1  = None

        self._last_plc_data = data
        return data

    # ----------------------------------------------------------
    # BREAK / SHIFT HELPERS
    # ----------------------------------------------------------

    def _maybe_reload_line_config(self) -> None:
        """Hot-reload the CONFIG bits admins edit often — per-shift plan
        (mes_shift_configs.total_plan), hourly-slot plans/boundaries
        (mes_hourly_slots), models and status map — from the DB every 60s,
        so a plan/slot/model edit applies WITHOUT a collector restart.  The
        existing _refresh_all_slot_plans() then writes the fresh numbers on
        its next 30s pass.  ONLY self.cfg values are swapped; runtime state
        (OK/NG counts, cycle_seq, shift id, register mirror baselines) is
        NEVER touched, so counting continues uninterrupted."""
        now = time.time()
        if now - getattr(self, "_cfg_last_reload", 0.0) < 60:
            return
        self._cfg_last_reload = now
        try:
            fresh = _load_line_config_from_db(self.cfg["line_id"])
        except Exception as e:
            print(f"[CFG-RELOAD] failed: {e}", flush=True)
            return
        changed = []
        for k in ("shifts", "hourly_plan", "slot_boundaries", "slot_to_db",
                  "models", "status_map", "ideal_ct", "max_ct"):
            if k in fresh and fresh[k] != self.cfg.get(k):
                self.cfg[k] = fresh[k]
                changed.append(k)
        # keep the live CT tracker in sync if the ideal CT was edited
        if "ideal_ct" in changed and getattr(self, "ct", None) is not None:
            try: self.ct.ideal_ct = self.cfg["ideal_ct"]
            except Exception: pass
        if changed:
            print(f"[CFG-RELOAD] applied: {', '.join(changed)}", flush=True)

    def _reload_breaks_from_db(self) -> None:
        """Re-pull break windows from mes_break_configs.

        Admins edit zone breaks from the Production Admin Panel while
        the collector is already running.  routers/zones.py PUT
        /api/zones/{zone_id}/breaks REPLACES break rows for every line
        in the zone, so reading this line's rows fresh is enough — no
        need to know which zone owns this line.

        Called every 60 s from the main poll loop.  Silent on DB errors.
        """
        line_id = self.cfg.get("line_id")
        if not line_id:
            return
        try:
            conn = _db_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT break_name, start_time, end_time, "
                "       crosses_midnight, applies_to_shifts "
                "  FROM mes_break_configs "
                " WHERE line_id = %s "
                " ORDER BY start_time",
                (line_id,)
            )
            new_breaks = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[BREAK-RELOAD] DB error for line {line_id}: {e}")
            return

        # Log only on diff so the console doesn't spam every minute.
        def _fp(rows):
            return tuple(
                (r.get("break_name"),
                 str(r.get("start_time")),
                 str(r.get("end_time")),
                 bool(r.get("crosses_midnight")),
                 r.get("applies_to_shifts"))
                for r in rows
            )
        old_fp = _fp(self.cfg.get("breaks", []))
        new_fp = _fp(new_breaks)
        if old_fp != new_fp:
            self.cfg["breaks"] = new_breaks
            labels = [f"{r['break_name']} {r['start_time']}-{r['end_time']}"
                      for r in new_breaks]
            print(f"[BREAK-RELOAD] line {line_id} now has {len(new_breaks)} "
                  f"break(s): {labels or '—'}")

    def _ct_clock_guard(self, raw_ct, ideal_ct=None, tag=""):
        """Sanitise a wall-clock cycle-time delta against host-clock jumps.
        2026-05-31 — The host (TBDI-BI-SVR-01) had repeated NTP / VM
        time-sync excursions that made datetime.now() leap forward or
        backward by minutes-to-hours between two consecutive parts.  Every
        CT here is a raw wall-clock delta (now - prev_pulse), so one such
        jump stored an absurd CT (e.g. +9212 s / -4861 s) that spiked the
        cycle-time chart and threw off the cycle-video window length.
        COUNT is taken from the PLC register and is completely independent
        of CT, so sanitising CT here can NOT change any part count — only
        the displayed/stored cycle time is corrected.  A negative gap, or a
        gap larger than ct_clock_jump_cap (default 3600 s — far above any
        real pause; today's clock jumps were all > 4800 s while genuine
        line pauses were < 350 s), is treated as a clock glitch and the CT
        falls back to the configured ideal CT (or 0 when none is set).
        Returns raw_ct unchanged when it is plausible (or None)."""
        try:
            if raw_ct is None:
                return raw_ct
            cap = float(self.cfg.get("ct_clock_jump_cap") or 3600.0)
            if raw_ct < 0.0 or raw_ct > cap:
                _id = float(ideal_ct if ideal_ct is not None
                            else (self.cfg.get("ideal_ct") or 0.0))
                _fb = _id if _id > 0 else 0.0
                print(f"[CT-CLOCK-GUARD]{(' ' + tag) if tag else ''} "
                      f"implausible cycle gap {raw_ct:.0f}s (host clock "
                      f"jump) -> CT {_fb:.1f}s (count unaffected)", flush=True)
                return _fb
        except Exception:
            pass
        return raw_ct

    def _break_overlap_seconds(self, t_start: datetime, t_end: datetime) -> float:
        """Total seconds of configured break time that overlap the
        wall-clock interval [t_start, t_end].  Used by the sub-machine
        cycle-time path so a single cycle that spans a tea/lunch break
        doesn't appear as a 700 s spike on the chart.  Walks every
        break in cfg and clamps each break window to today's date;
        midnight-crossing breaks get their end pushed +1 day.
        """
        if t_end <= t_start:
            return 0.0
        overlap = 0.0
        for b in self.cfg.get("breaks", []) or []:
            bs = b.get("start_time")
            be = b.get("end_time")
            if isinstance(bs, str):
                bs = dt_time(*map(int, bs.split(":")))
            if isinstance(be, str):
                be = dt_time(*map(int, be.split(":")))
            # Combine onto the cycle's START date.  A cycle never spans
            # >24 h on this floor, so we only need to look at today's
            # instance of each break (and the previous day's if t_start
            # is just past midnight and the break crossed midnight).
            day0 = t_start.date()
            bs_dt = datetime.combine(day0, bs)
            be_dt = datetime.combine(day0, be)
            if b.get("crosses_midnight"):
                be_dt += timedelta(days=1)
            ov_s = max(t_start, bs_dt)
            ov_e = min(t_end,   be_dt)
            if ov_e > ov_s:
                overlap += (ov_e - ov_s).total_seconds()
            # Also handle the case where the break belongs to the
            # PREVIOUS day's window (e.g. cycle straddles midnight,
            # break started yesterday and crossed into today).
            if t_start.time() < be:
                bs_y = datetime.combine(day0 - timedelta(days=1), bs)
                be_y = datetime.combine(day0 - timedelta(days=1), be)
                if b.get("crosses_midnight"):
                    be_y += timedelta(days=1)
                ov_s2 = max(t_start, bs_y)
                ov_e2 = min(t_end,   be_y)
                if ov_e2 > ov_s2:
                    overlap += (ov_e2 - ov_s2).total_seconds()
        return overlap

    def _is_break(self):
        now = datetime.now().time()
        for b in self.cfg["breaks"]:
            s, e = b["start_time"], b["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if b["crosses_midnight"]:
                if now >= s or now < e:
                    return True, b["break_name"]
            else:
                if s <= now < e:
                    return True, b["break_name"]
        return False, None

    # ── OT state ─────────────────────────────────────────────────
    _OT_DURATION_MIN = 60   # OT window = 1 hour after shift end

    def _check_ot_active(self) -> str:
        """Read ot_active_shift from mes_lines. Returns shift name or ''.

        2026-05-29 — CRITICAL: commit (or rollback) after the SELECT.
        Without it, psycopg2 leaves the connection "idle in transaction"
        which holds a SHARE lock on mes_lines.  Any subsequent ALTER
        TABLE (e.g. the admin's ADD COLUMN IF NOT EXISTS run from MES
        API auto-migrations) blocks waiting for that lock.  Every other
        thread/process then queues behind the ALTER, freezing the line.
        Observed in production: 41 stuck sessions, collector crash-loop
        with `LockNotAvailable: canceling statement due to lock timeout`.
        Method is called every poll iteration (>1Hz), so the cost is
        critical-path — but commit() on a read-only txn is cheap (no
        WAL write).
        """
        if not self._db_ok or not self._db:
            return ""
        try:
            cur = self._db.cursor()
            cur.execute(
                "SELECT ot_active_shift FROM mes_lines WHERE id = %s",
                (self.cfg["line_id"],)
            )
            row = cur.fetchone()
            cur.close()
            try:
                self._db.commit()        # release the implicit txn's lock
            except Exception:
                self._safe_rollback()
            return (row[0] or "") if row else ""
        except Exception:
            try:
                self._safe_rollback()
            except Exception:
                pass
            return ""

    def _get_ot_window(self, shift_name: str):
        """Return (ot_start_time, ot_end_time) from mes_shift_configs for this
        shift. If not configured, falls back to (shift_end, shift_end + 1hr)."""
        ot_s = ot_e = None
        if self._db_ok and self._db:
            try:
                cur = self._db.cursor()
                cur.execute(
                    "SELECT ot_start_time, ot_end_time FROM mes_shift_configs "
                    "WHERE line_id = %s AND shift_name = %s",
                    (self.cfg["line_id"], shift_name)
                )
                row = cur.fetchone()
                cur.close()
                if row:
                    ot_s, ot_e = row[0], row[1]
            except Exception:
                pass
        # Fallback: 1 hour after shift end
        if not (ot_s and ot_e):
            scfg = self.cfg["shifts"].get(shift_name, {})
            e = scfg.get("end_time")
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if e:
                ot_s = e
                end_m = (e.hour * 60 + e.minute + self._OT_DURATION_MIN) % 1440
                ot_e = dt_time(end_m // 60, end_m % 60)
        return ot_s, ot_e

    def _is_in_ot_window(self, shift_name: str) -> bool:
        """True if current time is within the OT window for this shift."""
        scfg = self.cfg["shifts"].get(shift_name)
        if not scfg or not scfg.get("is_production"):
            return False
        ot_s, ot_e = self._get_ot_window(shift_name)
        if not (ot_s and ot_e):
            return False
        now_t = datetime.now().time()
        s_min = ot_s.hour * 60 + ot_s.minute
        e_min = ot_e.hour * 60 + ot_e.minute
        n_min = now_t.hour * 60 + now_t.minute
        if e_min <= s_min:  # crosses midnight
            return n_min >= s_min or n_min < e_min
        return s_min <= n_min < e_min

    def _get_current_shift(self):
        now   = datetime.now()
        t     = now.time()
        today = now.date()

        # ── OT check FIRST: if OT is active for a shift and we're within
        # the 1-hour OT window after that shift's end, keep that shift alive.
        # This prevents the collector from transitioning to GAP when OT is on.
        ot_shift = self._check_ot_active()
        if ot_shift and ot_shift in self.cfg["shifts"]:
            if self._is_in_ot_window(ot_shift):
                scfg = self.cfg["shifts"][ot_shift]
                if scfg.get("crosses_midnight") and t < scfg["start_time"]:
                    return ot_shift, today - timedelta(days=1)
                return ot_shift, today

        # Normal shift detection — check time windows
        for sname, scfg in self.cfg["shifts"].items():
            if not scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if scfg["crosses_midnight"]:
                if t >= s:
                    return sname, today
                elif t < e:
                    return sname, today - timedelta(days=1)
            else:
                if s <= t < e:
                    return sname, today

        # GAP shifts
        for sname, scfg in self.cfg["shifts"].items():
            if scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if s <= t < e:
                return sname, today

        return None, today

    def _clock_shift(self):
        """(shift_name, record_date) from the configured shift TIMES alone —
        the same window logic as _get_current_shift() minus the OT lookup.
        No DB access and no state change, so any thread may call it (the OT
        lookup commits on the main loop's connection and must stay there)."""
        now   = datetime.now()
        t     = now.time()
        today = now.date()
        for sname, scfg in self.cfg["shifts"].items():
            if not scfg["is_production"]:
                continue
            s, e = scfg["start_time"], scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if scfg["crosses_midnight"]:
                if t >= s:
                    return sname, today
                elif t < e:
                    return sname, today - timedelta(days=1)
            elif s <= t < e:
                return sname, today
        for sname, scfg in self.cfg["shifts"].items():
            if scfg["is_production"]:
                continue
            s, e = scfg["start_time"], scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if s <= t < e:
                return sname, today
        return None, today

    def _shift_label(self) -> str:
        """Shift name to STAMP on a row.

        2026-09-19 — `_cur_shift` is set by the main loop's shift block; in the
        first seconds after a (re)start it is still None, and every row written
        meanwhile (sub-machine cycles, SA-FI log, data log) was stamped
        'UNKNOWN' — 22 rows from five collector restarts on 19-Sep, shown as an
        "UNKNOWN Shift" in the Video Archive.  Until the main loop has run, use
        the clock (the same shift windows it is about to apply) and prime the
        shift's record_date, so a B-shift row after midnight gets B's start
        date too.  _cur_shift itself is NEVER set here, so no shift-change
        action (register-zero pulses, cycle_seq reset, archive) fires early.
        A label only — counting is untouched."""
        if self._cur_shift:
            return self._cur_shift
        try:
            sname, rec = self._clock_shift()
        except Exception:
            sname, rec = None, None
        if sname and rec and getattr(self, "_cur_shift_record_date", None) is None:
            self._cur_shift_record_date = rec
        return sname or "UNKNOWN"

    def _get_current_slot(self) -> str:
        t = datetime.now().time()

        # ── OT slot priority: if OT is active + window live, route to OT slot
        ot_shift = self._check_ot_active()
        if ot_shift and self._is_in_ot_window(ot_shift):
            # Ensure OT slot exists (creates on first call)
            ot_label = self._ensure_ot_slot(ot_shift)
            if ot_label:
                return ot_label

        for sname, slots in self.cfg["hourly_plan"].items():
            for slot_label in slots:
                if slot_label not in self.cfg["slot_boundaries"]:
                    continue
                s, e, crosses = self.cfg["slot_boundaries"][slot_label]
                if isinstance(s, str):
                    s = dt_time(*map(int, s.split(":")))
                if isinstance(e, str):
                    e = dt_time(*map(int, e.split(":")))
                if crosses:
                    if t >= s or t < e:
                        return slot_label
                else:
                    if s <= t < e:
                        return slot_label
        return None

    def _ensure_ot_slot(self, shift_name: str) -> str:
        """Create the OT slot + dashboard columns if not present. Returns slot label."""
        if not self._db_ok:
            return None
        ot_s, ot_e = self._get_ot_window(shift_name)
        if not (ot_s and ot_e):
            return None
        label = f"{ot_s.strftime('%H:%M')}-{ot_e.strftime('%H:%M')} OT"
        # Already in config? return
        if label in self.cfg.get("slot_boundaries", {}):
            return label
        crosses = (ot_e <= ot_s)
        sm = ot_s.hour * 60 + ot_s.minute
        em = ot_e.hour * 60 + ot_e.minute
        dur_min = (em - sm) if not crosses else ((1440 - sm) + em)
        prefix = f"hour_{ot_s.strftime('%H%M')}_{ot_e.strftime('%H%M')}_ot"
        try:
            cur = self._db.cursor()
            # Insert into mes_hourly_slots (idempotent via unique constraint)
            cur.execute("""
                INSERT INTO mes_hourly_slots
                    (line_id, shift_name, slot_label, start_time, end_time,
                     crosses_midnight, working_minutes, plan_pieces,
                     db_column_prefix, slot_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, 999)
                ON CONFLICT (line_id, shift_name, slot_label) DO NOTHING
            """, (self.cfg["line_id"], shift_name, label, ot_s, ot_e,
                  crosses, dur_min, prefix))
            self._db.commit()

            # Add columns to dashboard table (idempotent)
            tbl = self.cfg["table_name"]
            for col_suffix, col_type in [("ok","INTEGER DEFAULT 0"),
                                          ("ng","INTEGER DEFAULT 0"),
                                          ("plan","INTEGER DEFAULT 0"),
                                          ("actual","INTEGER DEFAULT 0"),
                                          ("variance","INTEGER DEFAULT 0")]:
                cur.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {prefix}_{col_suffix} {col_type}"
                )
            self._db.commit()
            cur.close()
        except Exception as exc:
            print(f"[OT] _ensure_ot_slot error: {exc}")
            self._safe_rollback()
            return None

        # Update in-memory config so _get_current_slot/_write_hourly_to_db find it
        hp = self.cfg.setdefault("hourly_plan", {}).setdefault(shift_name, {})
        hp[label] = 0
        self.cfg.setdefault("slot_boundaries", {})[label] = (ot_s, ot_e, crosses)
        self.cfg.setdefault("slot_to_db", {})[label] = prefix
        print(f"[OT] Ensured OT slot '{label}' prefix={prefix} for shift {shift_name}")
        return label

    def _is_in_gap_period(self) -> bool:
        # 2026-05-29 — OT override.  When operator has activated OT for a
        # shift (mes_lines.ot_active_shift = 'A' or 'B') and the wall
        # clock is inside that shift's OT window (1-hour grace after
        # shift end), the time IS technically inside a GAP_* schedule
        # row — but production is officially live.  Without this check
        # _update_status's GAP override forced status_code=IDLE during
        # OT (operator complaint "OT mein status IDLE kyu aa raha hai")
        # and _should_record_pulse() silently dropped OT cycles.
        # Honour OT first; only fall back to clock-based GAP detection
        # when no OT is active.
        try:
            ot_shift = self._check_ot_active()
            if ot_shift and ot_shift in self.cfg["shifts"]:
                if self._is_in_ot_window(ot_shift):
                    return False
        except Exception:
            # Defensive: never let an OT-check failure flip gap state.
            pass
        if self._cur_shift and self._cur_shift.startswith("GAP"):
            return True
        now = datetime.now().time()
        for sname, scfg in self.cfg["shifts"].items():
            if scfg["is_production"]:
                continue
            s = scfg["start_time"]
            e = scfg["end_time"]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))
            if scfg.get("crosses_midnight"):
                if now >= s or now < e:
                    return True
            else:
                if s <= now < e:
                    return True
        return False

    def _should_record_pulse(self) -> bool:
        # Suppress counting during GAP (between-shift) and scheduled
        # breaks — these are official non-production windows for OEE.
        # Status display during those windows is also forced IDLE/BREAK
        # in _update_status. Outside those windows, any PLC pulse counts.
        if self._is_in_gap_period():
            return False
        in_break, _ = self._is_break()
        return not in_break

    def _realtime_slot_plan(self, slot_label: str) -> int:
        static_plan = 0
        for sname, slots in self.cfg["hourly_plan"].items():
            if slot_label in slots:
                static_plan = slots[slot_label]
                break
        if not static_plan or slot_label not in self.cfg["slot_boundaries"]:
            return 0

        s, e, crosses = self.cfg["slot_boundaries"][slot_label]
        if isinstance(s, str):
            s = dt_time(*map(int, s.split(":")))
        if isinstance(e, str):
            e = dt_time(*map(int, e.split(":")))

        now   = datetime.now()
        today = now.date()

        slot_start = datetime.combine(today, s)
        slot_end   = (datetime.combine(slot_start.date() + timedelta(days=1), e)
                      if crosses else datetime.combine(slot_start.date(), e))

        if slot_start <= now < slot_end:
            elapsed   = (now - slot_start).total_seconds()
            break_sec = 0.0
            for b in self.cfg["breaks"]:
                bs = b["start_time"]
                be = b["end_time"]
                if isinstance(bs, str):
                    bs = dt_time(*map(int, bs.split(":")))
                if isinstance(be, str):
                    be = dt_time(*map(int, be.split(":")))
                bs_dt = datetime.combine(slot_start.date(), bs)
                be_dt = datetime.combine(slot_start.date(), be)
                if b["crosses_midnight"]:
                    be_dt += timedelta(days=1)
                ov_s = max(slot_start, bs_dt)
                ov_e = min(now, be_dt)
                if ov_e > ov_s:
                    break_sec += (ov_e - ov_s).total_seconds()

            working_sec = max(0.0, elapsed - break_sec)
            rt_plan     = int(working_sec / self.cfg["ideal_ct"])
            stored      = self._hourly_data.get(slot_label, {}).get("plan", 0)
            return min(static_plan, max(rt_plan, stored))

        elif now >= slot_end:
            return static_plan
        return 0

    # ----------------------------------------------------------
    # HOURLY SLOT INIT & BACKFILL
    # ----------------------------------------------------------

    def _init_all_hourly_slots_for_shift(self, shift_name: str):
        if not self._shift_id:
            return
        shift_slots = [
            sl for sl in self.cfg["slot_to_db"].keys()
            if any(sl in slots
                   for sn, slots in self.cfg["hourly_plan"].items()
                   if sn == shift_name)
        ]
        print(f"[HOURLY] Initializing {len(shift_slots)} slots for shift {shift_name}")

        for slot_label in shift_slots:
            if slot_label not in self._hourly_data:
                self._hourly_data[slot_label] = {"ok": 0, "ng": 0, "plan": 0}
            try:
                col = self.cfg["slot_to_db"].get(slot_label)
                if col and self._db_ok and self._db:
                    cur = self._db.cursor()
                    cur.execute(f"""
                        SELECT {col}_ok, {col}_ng, {col}_plan
                        FROM {self.cfg['table_name']}
                        WHERE id = %s
                    """, (self._shift_id,))
                    row = cur.fetchone()
                    if row and any(row):
                        self._hourly_data[slot_label]["ok"]   = row[0] or 0
                        self._hourly_data[slot_label]["ng"]   = row[1] or 0
                        self._hourly_data[slot_label]["plan"] = row[2] or 0
                    cur.close()
            except Exception as e:
                print(f"[HOURLY] Error loading slot {slot_label}: {e}")
                self._db_ok = False

        self._backfill_past_slots(shift_name)

    def _write_all_slots_to_db_once(self):
        """On startup resuming a shift, write all past slot plans to DB immediately."""
        if not self._shift_id or not self._ensure_db_connection():
            return
        now = datetime.now()
        try:
            cur = self._db.cursor()
            for slot_label, col in self.cfg["slot_to_db"].items():
                if slot_label not in self.cfg["slot_boundaries"]:
                    continue
                s, e, crosses = self.cfg["slot_boundaries"][slot_label]
                if isinstance(s, str):
                    s = dt_time(*map(int, s.split(":")))
                if isinstance(e, str):
                    e = dt_time(*map(int, e.split(":")))

                slot_end_dt = datetime.combine(now.date(), e)
                if crosses:
                    slot_end_dt += timedelta(days=1)

                # Only fix PAST slots (already ended)
                if now <= slot_end_dt:
                    continue

                hd = self._hourly_data.get(slot_label, {"ok": 0, "ng": 0, "plan": 0})

                # Calculate plan if still 0
                if hd["plan"] == 0:
                    slot_start_dt = datetime.combine(now.date(), s)
                    if crosses and slot_start_dt > slot_end_dt:
                        slot_start_dt -= timedelta(days=1)
                    working_sec = max(0, (slot_end_dt - slot_start_dt).total_seconds())
                    for b in self.cfg["breaks"]:
                        bs = b["start_time"]
                        be = b["end_time"]
                        if isinstance(bs, str): bs = dt_time(*map(int, bs.split(":")))
                        if isinstance(be, str): be = dt_time(*map(int, be.split(":")))
                        bs_dt = datetime.combine(slot_start_dt.date(), bs)
                        be_dt = datetime.combine(slot_start_dt.date(), be)
                        if b["crosses_midnight"]: be_dt += timedelta(days=1)
                        ov_s = max(slot_start_dt, bs_dt)
                        ov_e = min(slot_end_dt, be_dt)
                        if ov_e > ov_s:
                            working_sec -= (ov_e - ov_s).total_seconds()
                    static_max = 0
                    for sname, slots in self.cfg["hourly_plan"].items():
                        if slot_label in slots:
                            static_max = slots[slot_label]
                    hd["plan"] = min(static_max, max(0, int(working_sec / self.cfg["ideal_ct"])))
                    self._hourly_data[slot_label] = hd

                ok_count = hd.get("ok", 0)
                ng_count = hd.get("ng", 0)
                plan     = hd.get("plan", 0)
                actual   = ok_count + ng_count
                variance = actual - plan

                cur.execute(f"""
                    UPDATE {self.cfg['table_name']} SET
                        {col}_ok       = %s,
                        {col}_ng       = %s,
                        {col}_plan     = %s,
                        {col}_actual   = %s,
                        {col}_variance = %s,
                        updated_at     = NOW()
                    WHERE id = %s
                """, (ok_count, ng_count, plan, actual, variance, self._shift_id))

            self._db.commit()
            cur.close()
            print(f"[STARTUP] Past slot plans written to DB for shift_id={self._shift_id}")
        except Exception as e:
            print(f"[STARTUP] Backfill write error: {e}")
            self._db_ok = False
            self._safe_rollback()

    def _backfill_past_slots(self, shift_name: str):
        now   = datetime.now()
        today = now.date()
        scfg  = self.cfg["shifts"].get(shift_name)
        if not scfg:
            return

        for slot_label, hdata in self._hourly_data.items():
            if slot_label not in self.cfg["slot_boundaries"]:
                continue
            s, e, crosses = self.cfg["slot_boundaries"][slot_label]
            if isinstance(s, str):
                s = dt_time(*map(int, s.split(":")))
            if isinstance(e, str):
                e = dt_time(*map(int, e.split(":")))

            slot_end_dt = datetime.combine(today, e)
            if crosses:
                slot_end_dt += timedelta(days=1)

            if now > slot_end_dt and hdata.get("plan", 0) == 0:
                slot_start_dt = datetime.combine(today, s)
                if crosses and slot_start_dt > slot_end_dt:
                    slot_start_dt -= timedelta(days=1)

                working_seconds = max(
                    0, (slot_end_dt - slot_start_dt).total_seconds())

                for b in self.cfg["breaks"]:
                    bs = b["start_time"]
                    be = b["end_time"]
                    if isinstance(bs, str):
                        bs = dt_time(*map(int, bs.split(":")))
                    if isinstance(be, str):
                        be = dt_time(*map(int, be.split(":")))
                    bs_dt = datetime.combine(slot_start_dt.date(), bs)
                    be_dt = datetime.combine(slot_start_dt.date(), be)
                    if b["crosses_midnight"]:
                        be_dt += timedelta(days=1)
                    ov_s = max(slot_start_dt, bs_dt)
                    ov_e = min(slot_end_dt, be_dt)
                    if ov_e > ov_s:
                        working_seconds -= (ov_e - ov_s).total_seconds()

                working_seconds = max(0, working_seconds)
                plan = int(working_seconds / self.cfg["ideal_ct"])
                plan = min(
                    self.cfg["hourly_plan"].get(shift_name, {}).get(slot_label, plan),
                    plan)
                hdata["plan"] = plan
                print(f"[HOURLY] Backfilled {slot_label}: plan={plan}")

    # ----------------------------------------------------------
    # COUNTS
    # ----------------------------------------------------------

    def _schedule_delayed_pc_read(self, delay_s: float = 2.0):
        """2026-05-28 — Operator: "part code ko ct start se 2 sec
        baad read kr".  Scanner takes ~2s to settle the new barcode
        into D5004 after a cycle starts.  Reading immediately at the
        rising edge can capture either the previous cycle's stale
        code or a transient mid-update value.  This schedules a
        one-shot PLC read 2s after the current pulse — the result
        becomes _cur_part_code, which the NEXT pulse will use when
        writing its L6 audit + ct_log row."""
        import threading as _th
        def _do_read():
            try:
                _pc = (self._read_part_code() or "").strip().rstrip(":")
                self._cur_part_code = _pc
                # Only surface a REAL scanned barcode (>=10 chars).  Junk
                # short / control-char reads (scanner still settling) stay
                # silent so the console isn't polluted between real parts.
                if _pc and len(_pc) >= 10:
                    print(f"[PART] scanned {_pc!r}", flush=True)
            except Exception as _e:
                # Don't overwrite cached value on read failure
                print(f"[PC-DELAYED-ERR] {_e}", flush=True)
        _th.Timer(delay_s, _do_read).start()

    def _update_counts(self, ok_bit: int, ng_bit: int) -> tuple:
        new_ok = 0
        new_ng = 0
        now    = time.time()

        # 2026-05-16 — TRUTH TRACKER for L108.
        # Track every L108 rising edge unconditionally, even when
        # _update_counts gates the actual count on `is_running`.  Used by
        # _update_status as a fallback "is the machine REALLY running?"
        # signal when PLC's D6005 register lies (e.g., ladder bug leaves
        # status=0 IDLE while operator is actively cycling).
        # Now ALSO logs the transition so we can see whether the PLC
        # read of L108 is healthy (was suspecting silent failures while
        # L109 reads worked fine).
        # 2026-05-29 (v3) - PURE GAP-BASED CHATTER REJECT.
        # v2 still missed phantoms because PLC double-pulse on a
        # placement OFTEN happens BEFORE the scanner has written
        # part_code to D5004 — so both rows have empty pc, and v2's
        # `_cur_pc_clean and _last_pc_clean` gate let them through.
        # Operator verified on dashboard chart:
        #   seq=555 13:05:44 ct=2.44s pc=None  ← phantom
        #   seq=556 13:05:46 ct=2.36s pc=None  ← phantom
        #   ... six such phantoms in 20 min, all pc=None.
        #
        # v3 rule (matches operator's own words: "L108 ke beech mein
        # 2.5 sec mein aa hi nhi skti"):
        #   • Reject ANY L108 rising edge that arrives within
        #     MIN_OK_GAP_FOR_CHATTER_SEC of the previous COMMITTED OK.
        #   • part_code is irrelevant — empty / matching / different
        #     all rejected if gap < threshold.
        #   • 5 s threshold safely above observed phantom range
        #     (2.2-3.3 s) and well below any real cycle gap (min
        #     observed ≥ 9 s on this line).
        # Phantom rejection does NOT advance _last_ok_committed_ts,
        # so the next real cycle's gap is measured from the real
        # previous commit (≥ 9 s naturally → passes).
        # 2026-06-02 — CROSS-TYPE gap.  Phantom bursts around a reject fire
        # MIXED L108+L109 edges within 1-3s; measuring only OK->OK let an OK
        # pulse land 0.9s after an NG and slip through.  Compare against the
        # last committed pulse of EITHER type so any <5s pulse is caught.
        _last_commit_ts = max(getattr(self, "_last_ok_committed_ts", 0.0) or 0.0,
                              getattr(self, "_last_ng_committed_ts", 0.0) or 0.0)
        _gap_s = (now - _last_commit_ts) if _last_commit_ts else 999.0
        MIN_OK_GAP_FOR_CHATTER_SEC = 5.0
        _is_ok_pc_chatter = False
        # 2026-05-29 — Register-mode bypass: when count_mode='register'
        # the PLC writes the count directly via ladder.  Every increment
        # in D1001 is a deliberate ladder write — there is no L108 line
        # bounce to filter.  Honor PLC truth: zero chatter rejection.
        # In bit mode the 5s phantom filter stays active (real wire
        # bounces still possible).
        _cm_chk = (self.cfg.get("count_mode") or "bit").lower()
        # 2026-06-02 — register bypass REMOVED.  This PLC's D101/L108 DO bounce
        # (verified: sub-5s phantom cycles on the chart), so we filter chatter
        # from the ct_log / chart in register mode too.  The COUNT is register-
        # mirrored (ok_shift = D101, SET in the read path), so this drops only
        # the phantom ROW — never the count.  Dashboard count stays == PLC == HMI.
        if (self._last_ok_state == 0 and ok_bit == 1
                and _gap_s < MIN_OK_GAP_FOR_CHATTER_SEC):
            _is_ok_pc_chatter = True
            _cur_pc_dbg = (self._cur_part_code or "").strip()[:20] or "<empty>"
            print(f"[OK-CHATTER-DEDUP] {self._count_src_label('ok')} rise gap={_gap_s:.2f}s < "
                  f"{MIN_OK_GAP_FOR_CHATTER_SEC:.1f}s (pc={_cur_pc_dbg}) "
                  f"— humanly impossible, PLC double-pulse REJECTED "
                  f"(no row, no counter, no webhook)", flush=True)

        self._ok_gap_ok_this_press = not _is_ok_pc_chatter
        # 2026-06-02 — NG cross-type chatter gate (was hardcoded True, so NG
        # phantoms were NEVER rejected).  Same 5s rule as OK: an NG rising
        # edge within MIN_OK_GAP_FOR_CHATTER_SEC of the last committed pulse
        # of EITHER type is a PLC double-pulse (real reject min gap >=9s).
        # This single flag gates BOTH the NG counter AND the NG ct_log row.
        _ng_gap_for_chatter = (now - _last_commit_ts) if _last_commit_ts else 999.0
        # 2026-06-02 — register bypass REMOVED here too (see OK note above).
        # The NG ct_log row is logged off the RAW L109 bit, which bounces far
        # more than the debounced D102 register (L109 fired ~3x the real NG).
        # Filtering <5s edges aligns the chart/ct_log with the D102 count.
        # ng_shift mirrors D102 in the read path, so the count is untouched.
        _is_ng_chatter = (self._last_ng_state == 0 and ng_bit == 1
                          and _ng_gap_for_chatter < MIN_OK_GAP_FOR_CHATTER_SEC)
        self._ng_gap_ok_this_press = not _is_ng_chatter
        if _is_ng_chatter:
            print(f"[NG-CHATTER-DEDUP] {self._count_src_label('ng')} rise "
                  f"gap={_ng_gap_for_chatter:.2f}s < {MIN_OK_GAP_FOR_CHATTER_SEC:.1f}s "
                  f"— humanly impossible, PLC double-pulse REJECTED "
                  f"(no row, no counter, no webhook)", flush=True)

        if self._last_ok_state == 0 and ok_bit == 1 and not _is_ok_pc_chatter:
            self._last_ok_edge_observed = now
            # 2026-05-29 - Mode-aware log: bit edge OR register increment.
            _cm = (self.cfg.get("count_mode") or "bit").lower()
            if _cm == "register":
                _reg = self.cfg.get("ok_data_register") or "?"
                _delta = int(getattr(self, "_pending_ok_delta", 1) or 1)
                _val = getattr(self, "_last_ok_reg_value", "?")
                print(f"[OK-RAW-WATCH] {_reg} +{_delta} (value={_val}) at "
                      f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                      f"(running={self._cur_status == 1})", flush=True)
            else:
                _src = self._count_src_label("ok")
                print(f"[OK-RAW-WATCH] {_src} 0->1 at "
                      f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                      f"(running={self._cur_status == 1}, "
                      f"should_record={self._should_record_pulse()})", flush=True)
            # 2026-05-29 - Replaced direct _read_part_code() (50-100ms
            # blocking LAN call that was making us miss back-to-back
            # L108 pulses) with async delayed read.  Updates
            # _cur_part_code in background ~2s later for the NEXT row.
            # Main poll loop stays free to catch fast pulses.
            self._schedule_delayed_pc_read(2.0)
            self._last_ok_committed_ts = now

            # Raw write to per-machine L6 audit (no dwell gating).
            # 2026-05-26 — Garbage-part_code guard.  Same problem the NG
            # path hit: PLC sometimes leaks `\x10` (0x10 status byte)
            # into D5004 around an L108 chatter pulse, producing OK
            # rows with junk part_code.  Skip the write when the read
            # looks like a control-char dump — better to leave the row
            # off the audit than poison the per-part lookups (videos,
            # remarks, charts) downstream.
            try:
                # 2026-05-28 - PURE PASS-THROUGH.  Whatever PLC says,
                # store as-is.  No garbage filter, no NULL substitution.
                _now_dt = datetime.now()
                _prev_ts = getattr(self, "_last_any_pulse_dt", None)
                # 2026-06-04 — FIRST-PART-OF-SHIFT CT anchor.  _last_any_pulse_dt
                # carries across shift boundaries, so a shift's first part was
                # measured now - (previous shift / gap's last pulse) => a phantom
                # ~1 hr CT on "Part #1" (e.g. 18:37 - 17:35 = 3706s).  Floor the
                # anchor at THIS shift's own start so the first part naps from
                # shift start (now - shift_start).  Every later part already has
                # prev_ts > shift_start, so this is a pure no-op (zero regression).
                # COUNT is register-based and totally independent of CT.
                _sst = getattr(self, "_shift_start_ts", None)
                if _sst:
                    _ss_dt = datetime.fromtimestamp(_sst)
                    if _prev_ts is None or _prev_ts < _ss_dt:
                        _prev_ts = _ss_dt
                _ct_raw = (_now_dt - _prev_ts).total_seconds() if _prev_ts else None
                # 2026-05-31 — clock-jump guard (see _ct_clock_guard).  A host
                # NTP/VM time-sync excursion between two parts otherwise stores
                # an absurd CT that spikes the chart + breaks the video-length
                # window.  Count is register-based and stays unaffected.  We
                # neutralise _ct_raw in place so the break-net block below also
                # sees the sane value.
                _ct_raw = self._ct_clock_guard(_ct_raw)
                _ct = _ct_raw
                # 2026-05-29 - Break-aware CT.  Operator: "break time
                # cycle time mein count nhi hoga".  Subtract any
                # scheduled break overlap from the cycle window.
                if _ct_raw is not None and _prev_ts is not None:
                    try:
                        _brk = self._break_overlap_seconds(_prev_ts, _now_dt)
                        # 2026-05-31 — Only subtract break time when the gap is
                        # long enough to actually CONTAIN a stoppage.  If parts
                        # keep completing at normal cadence the line ran THROUGH
                        # the break window — that gap is a real cycle, not idle
                        # time, and must NOT be zeroed.  Symptom: every ~15s
                        # cycle during the 01:00 Night Tea / 22:00 Dinner break
                        # netted to ct=0, which made the FI cycle-video default
                        # to a 10s clip instead of the real ~15s window ("video
                        # length off").  Shortest break here is 10 min, so any
                        # gap below ~3x ideal CT (floored at 90s) cannot hold a
                        # scheduled break.  Genuine break-SPANNING gaps (line
                        # truly stopped) are >= this floor and still net as before.
                        _stoppage_min = max(3.0 * float(self.cfg.get("ideal_ct") or 0.0), 90.0)
                        if _brk > 0.5 and _ct_raw >= _stoppage_min:
                            _ct = max(0.0, _ct_raw - _brk)
                            print(f"[OK-BREAK-NET] raw={_ct_raw:.1f}s "
                                  f"- break={_brk:.0f}s = net={_ct:.1f}s",
                                  flush=True)
                    except Exception:
                        pass
                _pc = (self._cur_part_code or "").strip().rstrip(":") or None
                self._last_any_pulse_dt = _now_dt
                self._last_ct_for_chart_ok = _ct
                _shift_ok = self._shift_label()
                if _shift_ok.startswith("GAP"):
                    _shift_ok = "GAP"
                _rec_dt = getattr(self, "_cur_shift_record_date", None) or _now_dt.date()
                _machine_id = int(self.cfg.get("main_plc_id") or self.cfg["line_id"])
                _bit_addr = self._count_src_label("ok")
                # 2026-05-29 - Register-mode multi-part expansion.
                # Operator spec: "if + ja rha h to 200 se direct tune
                # read kia d204 to ye 4 part ko to vo cycle skip na ho
                # 4 alg alg cycle hi count ho" — 4 alag rows, each with
                # its own seq + own CT (we evenly distribute the gap
                # since we don't have individual timestamps from PLC).
                # Bit mode always produces 1 row (delta forced to 1
                # upstream).
                _delta_rows = int(getattr(self, "_pending_ok_delta", 1) or 1)
                if _delta_rows < 1:
                    _delta_rows = 1
                # 2026-05-30 — Phantom-dump BACKSTOP.  The register guard in
                # _read_plc already bounds the per-poll delta, so for normal
                # flow this never trips.  It is a hard last line of defence: no
                # single poll may ever fabricate more than _ROWCAP ct_log rows,
                # so a bug anywhere upstream can never again flood one hour with
                # thousands of rows (the 2026-05-30 14k incident).
                _ROWCAP = int(self.cfg.get("reg_row_cap") or 500)
                if _delta_rows > _ROWCAP:
                    print(f"[REG-MULTI] delta_rows={_delta_rows} exceeds cap "
                          f"{_ROWCAP} — clamped (phantom-dump backstop)",
                          flush=True)
                    _delta_rows = _ROWCAP
                _ct_per = (float(_ct or 0.0) / _delta_rows) if _delta_rows > 1 else float(_ct or 0.0)
                for _i in range(_delta_rows):
                    # Backdate row i so the i=last row lands at _now_dt
                    # and earlier rows step back by _ct_per each.
                    if _delta_rows > 1:
                        _row_ts = _now_dt - timedelta(seconds=_ct_per * (_delta_rows - 1 - _i))
                        _row_ct = _ct_per
                    else:
                        _row_ts = _now_dt
                        _row_ct = _ct
                    # 2026-05-30 — counter_val + cycle_seq PINNED to the
                    # D-register value so the UI's per-machine "OK: N"
                    # tracks the PLC absolute count.  For multi-row +N
                    # expansion, the last row lands on ok_shift exactly
                    # (the post-accept register value) and earlier rows
                    # step back by 1 each.
                    _row_seq = int(self.ok_shift) - (_delta_rows - 1 - _i)
                    # L6 audit write — one row per part.
                    self._write_machine_log(
                        machine_id   = _machine_id,
                        bit_type     = "OK",
                        bit_address  = _bit_addr,
                        ts           = _row_ts,
                        ct_seconds   = _row_ct,
                        part_code    = _pc,
                        counter_val_override = _row_seq,
                    )
                    # ct_log — one row per part with cycle_seq also
                    # pinned to the D-register value.
                    self._raw_cycle_seq = max(int(self._raw_cycle_seq or 0), _row_seq)
                    self._ct_pending_log.append((
                        _row_ts, _rec_dt, _shift_ok,
                        round(float(_row_ct or 0.0), 2),
                        _row_seq,
                        _pc, False,  # is_ng = False
                    ))
                if _delta_rows > 1:
                    print(f"[REG-MULTI] expanded delta={_delta_rows} "
                          f"into {_delta_rows} separate rows "
                          f"(ct_per={_ct_per:.2f}s)", flush=True)
                self._raw_ok_already_logged_this_press = True
                # 2026-08-22 — operator: the PY desired-vs-live compare must
                # run when a CYCLE COMPLETES, not on a free-running timer.
                # Arm it here; the PY-CHECK thread does the PLC reads so the
                # counting path is never slowed by them.
                self._py_check_due = True
                # SEAT SLIDER trace: if Semi-Auto called this part NG, tell
                # Final's PLC now that the part is actually at Final.
                # When a Final compare-fetch bit is configured the reject fires
                # on THAT bit's rising edge instead (correct cycle, see
                # _check_fi_fetch), so skip the count-commit trigger here to
                # avoid a second L230 pulse for the same part.
                if not self.cfg.get("fi_fetch_bit"):
                    self._sa_ng_signal_final(_pc, _shift_ok, _rec_dt,
                                             int(self.ok_shift))
                # 2026-05-29 — Update last-committed part_code for
                # the part-code chatter dedup check at top of this fn.
                # Only stamp when this commit had a non-empty pc;
                # empty/None codes never participate in the dedup.
                if _pc:
                    self._last_committed_pc = _pc
            except Exception as _e:
                pass
        elif self._last_ok_state == 1 and ok_bit == 0:
            # 2026-05-29 - In register mode the virtual bit goes 1->0 the
            # tick after every increment.  Suppress the misleading L108
            # log; rising-edge log already shows the D-register delta.
            _cm = (self.cfg.get("count_mode") or "bit").lower()
            if _cm != "register":
                _src = self._count_src_label("ok")
                print(f"[OK-RAW-WATCH] {_src} 1->0 at "
                      f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}",
                      flush=True)

        # Periodic state dump (every 30 s) — single source of truth for
        # debugging "why didn't the counter increment".  In register
        # mode we ALSO surface the live D-register value + baseline so
        # the operator can see whether the PLC is actually ticking the
        # counter (stuck baseline = PLC ladder issue, not collector).
        if not hasattr(self, "_count_diag_last") or now - self._count_diag_last > 30:
            self._count_diag_last = now
            _cm_dbg = (self.cfg.get("count_mode") or "bit").lower()
            _reg_dbg = ""
            if _cm_dbg == "register":
                # Exact-mirror model: ok_shift == live register value.  Show
                # the OK/NG register names + last-read values + the running
                # shift peak (what the shift-reset-bit rollover will archive).
                # If the register value isn't moving while parts run, the PLC
                # ladder isn't ticking the counter (not a collector issue).
                _ok_reg_dbg = self.cfg.get("ok_data_register") or "-"
                _ng_reg_dbg = self.cfg.get("ng_data_register") or "-"
                _ok_val_now = getattr(self, "_last_ok_reg_value", "?")
                _ng_val_now = getattr(self, "_last_ng_reg_value", "?")
                _reg_dbg = (f" | REG-MIRROR OK[{_ok_reg_dbg}]={_ok_val_now} "
                            f"NG[{_ng_reg_dbg}]={_ng_val_now} "
                            f"peak_ok={getattr(self, '_ok_shift_peak', 0)} "
                            f"peak_ng={getattr(self, '_ng_shift_peak', 0)} "
                            f"reset_bit={self.cfg.get('shift_reset_bit') or 'off'}")
            print(f"[COUNT-DIAG] ok_bit={ok_bit} ng_bit={ng_bit} "
                  f"last_ok={self._last_ok_state} last_ng={self._last_ng_state} "
                  f"cur_status={self._cur_status} is_running={self._cur_status == 1} "
                  f"ng_seen_pending={getattr(self, '_ng_seen_since_last_ok', False)} "
                  f"ng_consec={getattr(self, '_ng_consec_count', 0)} "
                  f"ok_count={self.ok_shift} ng_count={self.ng_shift}"
                  f"{_reg_dbg}", flush=True)

        # 2026-05-16 — STUCK-L108 WATCHDOG.
        # Operator saw OK counter freeze at 1148 for 18+ min while L109
        # kept firing.  Root cause: PLC TCP connection went "half-open"
        # — pymcprotocol's socket looked alive (L109 reads still worked)
        # but L108 reads silently returned stale 0.  No exception was
        # raised, so the existing reconnect path never triggered.
        # Solution: a per-bit liveness watchdog.  Track when L108 was
        # last observed in the rising state; if >STUCK_THRESHOLD_SEC
        # has passed AND L109 fired at least N times in that window
        # (proving the PLC connection IS otherwise alive), forcibly
        # close + reconnect the PLC socket so the next poll gets fresh
        # reads.  L109 firing without L108 = PLC half-open signature.
        STUCK_OK_THRESHOLD_SEC  = 180.0     # 3 min — far above any normal cycle
        STUCK_OK_NG_WITNESSES   = 3         # need at least 3 L109 fires to suspect
        if self._last_ng_state == 0 and ng_bit == 1:
            # increment witness counter on every L109 rising edge
            self._stuck_l108_ng_witness = getattr(self, "_stuck_l108_ng_witness", 0) + 1
        last_ok_seen = getattr(self, "_last_ok_edge_observed", 0.0) or 0.0
        if (last_ok_seen > 0
                and now - last_ok_seen > STUCK_OK_THRESHOLD_SEC
                and getattr(self, "_stuck_l108_ng_witness", 0) >= STUCK_OK_NG_WITNESSES
                and not getattr(self, "_stuck_l108_recover_firing", False)):
            print(f"[L108-WATCHDOG] No L108 edge for "
                  f"{now - last_ok_seen:.0f}s while L109 fired "
                  f"{self._stuck_l108_ng_witness}x — PLC half-open suspected. "
                  f"Forcing PLC reconnect.", flush=True)
            self._stuck_l108_recover_firing = True   # one-shot until reconnect path runs
            try:
                if self._plc is not None:
                    try: self._plc.close()
                    except Exception: pass
                self._plc_ok = False                  # main loop will reopen on next tick
            except Exception as exc:
                print(f"[L108-WATCHDOG] close failed: {exc}", flush=True)
        # Reset watchdog state on a real L108 rising edge (we recovered)
        if self._last_ok_state == 0 and ok_bit == 1:
            self._stuck_l108_ng_witness   = 0
            self._stuck_l108_recover_firing = False

        # 2026-05-16 — RAW L109 observer (zero gating).  Operator reports
        # "NG bit fires kabhi nahi yaa 40 ek saath" but direct PLC scan
        # showed L109 = 0 throughout 90 s of high-freq polling.  This
        # observer prints to log on EVERY 0→1 / 1→0 transition of the
        # configured NG bit, with timestamps + hold duration on the
        # falling edge.  Independent of is_running gating, dwell rules,
        # everything — pure ground truth.  When operator presses NG
        # tomorrow, the log will show:
        #   • L109 toggle captured  → wiring + bit address are correct,
        #     the new cycle-bound counter will pick it up on next L108
        #   • L109 NEVER toggles    → NG button is wired to a different
        #     bit (look at the [NG-RAW-WATCH] gap and check PLC ladder)
        if (self._last_ng_state == 0 and ng_bit == 1
                and self._ng_gap_ok_this_press):   # 2026-06-02 — skip chatter NG row
            self._ng_raw_rise_ts = now
            self._last_ng_committed_ts = now
            print(f"[NG-RAW-WATCH] L109 0->1 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                  f"(running={self._cur_status == 1})", flush=True)
            try:
                # 2026-05-29 - Async delayed PC read (non-blocking).
                # Direct read was 50-100ms LAN call -> missed pulses.
                self._schedule_delayed_pc_read(2.0)
                _ng_now_dt = datetime.now()
                _prev_ts = getattr(self, "_last_any_pulse_dt", None)
                # 2026-06-04 — First-part-of-shift anchor (same as OK path).
                # Floor the carried-over anchor at this shift's start so the
                # first NG of a shift naps from shift start, not the previous
                # shift/gap's last pulse.  No-op for every later part.
                _sst = getattr(self, "_shift_start_ts", None)
                if _sst:
                    _ss_dt = datetime.fromtimestamp(_sst)
                    if _prev_ts is None or _prev_ts < _ss_dt:
                        _prev_ts = _ss_dt
                _ng_ct_raw_full = ((_ng_now_dt - _prev_ts).total_seconds()
                                   if _prev_ts else None)
                _ng_ct_raw = _ng_ct_raw_full
                # 2026-05-29 - Break-aware CT (same as OK side).
                if _ng_ct_raw_full is not None and _prev_ts is not None:
                    try:
                        _brk = self._break_overlap_seconds(_prev_ts, _ng_now_dt)
                        # 2026-05-31 — Same guard as the OK side: only subtract
                        # break time for gaps long enough to contain a real
                        # stoppage, so a part completing at normal cadence during
                        # a break window keeps its true cycle time (not zeroed).
                        _stoppage_min = max(3.0 * float(self.cfg.get("ideal_ct") or 0.0), 90.0)
                        if _brk > 0.5 and _ng_ct_raw_full >= _stoppage_min:
                            _ng_ct_raw = max(0.0, _ng_ct_raw_full - _brk)
                            print(f"[NG-BREAK-NET] raw={_ng_ct_raw_full:.1f}s "
                                  f"- break={_brk:.0f}s = net={_ng_ct_raw:.1f}s",
                                  flush=True)
                    except Exception:
                        pass
                _ng_pc = (self._cur_part_code or "").strip().rstrip(":") or None
                self._last_any_pulse_dt    = _ng_now_dt
                self._last_ct_for_chart_ng = _ng_ct_raw
                # L6 audit write - ALWAYS
                self._write_machine_log(
                    machine_id   = int(self.cfg.get("main_plc_id") or self.cfg["line_id"]),
                    bit_type     = "NG",
                    bit_address  = self._count_src_label("ng"),
                    ts           = _ng_now_dt,
                    ct_seconds   = _ng_ct_raw,
                    part_code    = _ng_pc,
                )
                # ct_log write - ALWAYS.  Use shift's record_date for
                # midnight-cross safety (same as OK side above).
                _shift_ng = self._shift_label()
                if _shift_ng.startswith("GAP"):
                    _shift_ng = "GAP"
                _rec_dt_ng = getattr(self, "_cur_shift_record_date", None) or _ng_now_dt.date()
                self._raw_cycle_seq += 1
                self._ct_pending_log.append((
                    _ng_now_dt, _rec_dt_ng, _shift_ng,
                    round(float(_ng_ct_raw or 0.0), 2),
                    self._raw_cycle_seq,
                    _ng_pc, True,  # is_ng = True
                ))
                self._raw_ng_already_logged_this_press = True
            except Exception as _e_ng_raw:
                print(f"[NG-RAW-WRITE-ERR] {_e_ng_raw}", flush=True)
        elif self._last_ng_state == 1 and ng_bit == 0:
            held = now - getattr(self, "_ng_raw_rise_ts", now)
            print(f"[NG-RAW-WATCH] L109 1->0 at "
                  f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                  f"(held {held:.3f}s, running={self._cur_status == 1})",
                  flush=True)

        # Capture the L108 / L109 rising-edge intent BEFORE the gates so
        # we can log the skip reason if a gate vetoes the count.
        # 2026-05-18 — operator wanted "ek-ek count ho, koi miss naa ho".
        # Explicit skip logs make every dropped edge auditable.
        _ok_edge_now = (self._last_ok_state == 0 and ok_bit == 1)
        _ng_edge_now = (self._last_ng_state == 0 and ng_bit == 1)

        if not self._should_record_pulse():
            if _ok_edge_now or _ng_edge_now:
                print(f"[COUNT-SKIP] _should_record_pulse=False "
                      f"ok_edge={_ok_edge_now} ng_edge={_ng_edge_now} "
                      f"— edge dropped", flush=True)
            self._last_ok_state = ok_bit
            self._last_ng_state = ng_bit
            return 0, 0

        # 2026-05-12 ROLLBACK — earlier rev removed the status==RUNNING(1)
        # gate trying to fix count-miss.  That introduced a worse bug:
        # L109 (NG) transient pulses during IDLE / status-transition
        # windows started being counted as real NGs, producing 60+ NGs
        # in a half day when actual reject rate is 2-3/day.
        #
        # Restored the safer original behaviour: ONLY count OK/NG when
        # PLC says RUNNING (status_code 1) AND we're not in a break.
        # If the PLC flips into transient QUALITY_ISSUE (3) on cycle
        # end, the IDLE-dwell + raw=16→RUNNING handlers keep cur_status
        # at 1 so legitimate pulses still count.  Pulse miss from the
        # earlier collector blocking issues is separately addressed
        # by the PY-check thread (moved to its own PLC connection).
        in_break, _ = self._is_break()
        is_running  = (self._cur_status == 1 and not in_break)

        # 2026-05-18 — INLINE L108-EDGE OVERRIDE.
        # The L108-TRUTH-OVERRIDE in _update_status only fires on the
        # NEXT poll (it needs _last_ok_edge_observed to already be set,
        # which we set further down inside this same call).  So when
        # the FIRST L108 edge after a long IDLE comes through, this
        # call still sees _cur_status=0 → is_running=False → that very
        # cycle would be silently dropped.  Cover for it: if we see an
        # L108 rising edge *right now* and we're not in a scheduled
        # break, trust the edge — the machine IS running.  Break window
        # still wins (we never count parts during scheduled break).
        if _ok_edge_now and not is_running and not in_break:
            print(f"[OK-EDGE-TRUST] {self._count_src_label('ok')} edge while "
                  f"PLC status={self._cur_status} (IDLE/transient) - counting "
                  f"anyway (machine is clearly producing).", flush=True)
            is_running = True
        elif (_ok_edge_now or _ng_edge_now) and not is_running:
            # In break — explicit log so operator can verify nothing
            # legitimate was missed.
            print(f"[COUNT-SKIP] in_break={in_break} cur_status={self._cur_status} "
                  f"ok_edge={_ok_edge_now} ng_edge={_ng_edge_now} "
                  f"— edge intentionally not counted (scheduled break / PLC idle)",
                  flush=True)

        # 2026-05-18 — TRACK NG EDGE BEFORE is_running GATE.
        # Earlier the L109 rising-edge detection sat INSIDE `if is_running:`
        # at line ~2355.  Risk: operator presses NG during a brief status
        # transition (status flips to QUALITY_ISSUE / IDLE for 1-2 polls),
        # is_running goes False, the L109 rise is ignored, then when the
        # next L108 fires that cycle gets labelled OK incorrectly.
        # Fix: set the NG-pending flag any time we see an L109 rising edge
        # outside of a scheduled break — even when is_running is False.
        # The flag persists until the next L108 commits, so a real NG press
        # is never lost regardless of status flapping mid-cycle.
        #
        # 2026-05-23 — DWELL REMOVED.  Operator demand: "koii hold nhi
        # lgana bss ye dekhna hai ki itne N kaa cause kya tha — agr tu
        # hold lga dega toh point kya hai hme real-time mein accurate
        # hona hai".  Pure rising-edge counting now.  Every 0→1 of L109
        # outside a scheduled break is a candidate NG, full stop.
        # The stuck-bit guard further down (NG_STUCK_CYCLES) is the
        # ONLY safety net — it caps RUNAWAY ladder behavior, not noise.
        #
        # To diagnose phantom-NG bursts WITHOUT suppressing them, every
        # L109 rising edge is now written to a dedicated JSONL forensic
        # file with full context (cycle-relative timing, status, model,
        # part code, PLC health).  When operator reports "30 NG aaye",
        # we open _ng_forensics_{date}_{shift}.jsonl and pattern-match
        # the cause (electrical noise vs ladder bug vs status flap vs
        # real operator press).
        # 2026-05-28 — ALL NG FILTERS REMOVED.  Operator: "koi filter
        # nhi h isme, ok h to ok rhega ng h to ng hi rhega".  Every
        # L109 rising edge = 1 NG count.  No dwell, no interval gate.
        # Constants kept as no-op zeros so downstream references don't
        # break, but the gates below are bypassed.
        NG_MIN_HOLD_SEC     = 0.0
        NG_MIN_INTERVAL_SEC = 0.0
        NG_MIN_HOLD_POLLS   = 1   # legacy var kept for L108 commit branch

        # Keep _ng_hold_polls counter alive for the rest of the logic
        # below (it still uses it as a sanity check inside the L108
        # commit branch).  No drops are logged here — the forensic
        # logger captures everything.
        if ng_bit == 1:
            self._ng_hold_polls = getattr(self, "_ng_hold_polls", 0) + 1
        else:
            held_polls = getattr(self, "_ng_hold_polls", 0)
            # FALLING-EDGE FORENSICS — captures the actual L109 dwell time
            # for the just-completed press.  Combined with the rising-edge
            # log line, this gives a complete pulse picture per NG event.
            if held_polls > 0:
                try:
                    import json as _json_ng
                    _hold_ms = held_polls * 100
                    _ev = {
                        "ts": datetime.now().isoformat(timespec="milliseconds"),
                        "kind": "L109_fall",
                        "hold_ms": _hold_ms,
                        "hold_polls": held_polls,
                        "cur_status": self._cur_status,
                        "in_break": in_break,
                        "is_running": is_running,
                        "model": self._cur_model_name,
                        "part_code": self._cur_part_code,
                        "since_last_ok_s": round(
                            time.time() - (self._last_ok_time or 0), 3
                        ) if self._last_ok_time else None,
                        "ng_seen_pending": bool(self._ng_seen_since_last_ok),
                        "plc_ok": bool(self._plc_ok),
                    }
                    self._ng_forensic_write(_ev)
                except Exception:
                    pass
            self._ng_hold_polls = 0

        # 2026-05-23 — real-time NG accept: ANY L109 rising edge outside
        # a scheduled break sets the cycle's NG flag.  No dwell wait —
        # the operator wants every press visible the instant it happens.
        # The forensic JSONL writer captures the rising edge with full
        # context (so post-mortem can tell ladder pulse from real press).
        # 2026-05-26 — TIME-BASED dwell gate.  Set rise timestamp on
        # the rising edge but DON'T flag the cycle NG until the bit has
        # been held HIGH for >= NG_MIN_HOLD_SEC wall-clock seconds.  The
        # check below runs every poll while ng_bit=1.
        # 2026-05-28 — NO DWELL.  Flag the cycle NG INSTANTLY on every
        # L109 rising edge outside a scheduled break.  Forensics writer
        # still captures the rise for post-mortem, but no hold time is
        # required to count.
        if _ng_edge_now and not in_break:
            self._ng_rise_ts_for_dwell = now
            self._ng_seen_since_last_ok = True
            try:
                _ev2 = {
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "kind": "L109_rise",
                    "cur_status": self._cur_status,
                    "is_running": is_running,
                    "model": self._cur_model_name,
                    "part_code": self._cur_part_code,
                    "since_last_ok_s": round(
                        time.time() - (self._last_ok_time or 0), 3
                    ) if self._last_ok_time else None,
                    "ok_bit_now": ok_bit,
                    "plc_ok": bool(self._plc_ok),
                }
                self._ng_forensic_write(_ev2)
            except Exception:
                pass

        # ────────────────────────────────────────────────────────────
        # OK / NG — SWITCH MODEL (2026-05-26).
        # ────────────────────────────────────────────────────────────
        # Operator: "ye mera pura concept as a first pulse of any one
        # bit ok not as continues monitor every thing".  Each bit's
        # rising edge is an INDEPENDENT event.  Don't merge cycles.
        #   • L108 rise → +1 OK row
        #   • L109 rise (dwell-gated ≥500 ms) → +1 NG row
        # ACTUAL = OK + NG (each visible row is its own physical part).
        # When the operator runs 5-7 NG parts in a row, we get 5-7 NG
        # rows, not one merged row pretending to be 186 s of work.
        if is_running:
            # L108 rising edge → independent OK row.
            # 2026-05-28 — Part_code already refreshed at the raw
            # rising-edge block above (fresh PLC read, no fallback).
            # No re-read here, no substitution — whatever was captured
            # at the raw edge IS the part_code for this row.
            # 2026-05-28 — Counter increments now honour the 3-sec
            # inter-pulse gap flag set at top of _update_counts.  Same
            # gate as raw write blocks so DB row + dashboard counter
            # stay in lockstep (no silent drift).
            if (self._last_ok_state == 0 and ok_bit == 1
                    and self._ok_gap_ok_this_press):
                # 2026-05-29 - Register mode supports multi-step jumps
                # (D-register 42 -> 47 in one poll = +5 OK).  Bit mode
                # always sets ok_delta = 1.
                _delta = int(getattr(self, "_pending_ok_delta", 1) or 1)
                _reg_mode = (self.cfg.get("count_mode") or "bit").lower() == "register"
                self.ok_total += _delta
                # Register-mirror mode: self.ok_shift is the EXACT register
                # value, already SET in _read_plc every poll.  Do NOT also
                # accumulate here (would double-count).  Bit mode accumulates.
                if not _reg_mode:
                    self.ok_shift += _delta
                new_ok = _delta
                self._last_ok_time = now
                if _delta > 1:
                    print(f"[OK-COUNT] +{_delta} (REGISTER jump, total="
                          f"{self.ok_total} shift={self.ok_shift}) "
                          f"pc={self._cur_part_code}", flush=True)
                else:
                    print(f"[OK-COUNT] +1 (total={self.ok_total} "
                          f"shift={self.ok_shift}) pc={self._cur_part_code}",
                          flush=True)
                # Video: +1 -> cut one per-part clip.  A register jump of
                # +2-or-more means parts arrived faster than we can cleanly
                # segment -> SKIP the clip, resume fresh from the new count
                # (the COUNT still mirrors the jump exactly).  Bit mode always
                # has _delta == 1, so it always emits -> zero regression.
                if _delta <= 1:
                    self._emit_edge_webhook("L108", now)
                else:
                    print(f"[VIDEO-SKIP] OK +{_delta} jump — clip skipped, "
                          f"count resumes at {self.ok_shift}", flush=True)

            # NG counter: same gap gate + multi-step delta support.
            if (self._ng_seen_since_last_ok
                    and not getattr(self, "_ng_committed_this_press", False)
                    and self._ng_gap_ok_this_press):
                _delta_ng = int(getattr(self, "_pending_ng_delta", 1) or 1)
                _reg_mode_ng = (self.cfg.get("count_mode") or "bit").lower() == "register"
                self.ng_total += _delta_ng
                # Register-mirror mode owns ng_shift in _read_plc — don't
                # double-count here.  Bit mode accumulates as before.
                if not _reg_mode_ng:
                    self.ng_shift += _delta_ng
                new_ng = _delta_ng
                self._last_ng_time = now
                self._ng_committed_this_press = True
                if _delta_ng > 1:
                    print(f"[NG-COUNT] +{_delta_ng} (REGISTER jump, total="
                          f"{self.ng_total} shift={self.ng_shift})",
                          flush=True)
                else:
                    print(f"[NG-COUNT] +1 (total={self.ng_total} "
                      f"shift={self.ng_shift}) pc={self._cur_part_code}",
                      flush=True)
                # Same +2-skip video rule as OK (count still mirrors exactly).
                if _delta_ng <= 1:
                    self._emit_edge_webhook("L109", now)
                else:
                    print(f"[VIDEO-SKIP] NG +{_delta_ng} jump — clip skipped, "
                          f"count resumes at {self.ng_shift}", flush=True)
            # On falling edge of L109 — clear both press guards so the
            # next press can dwell-pass + commit fresh.
            if ng_bit == 0 and self._last_ng_state == 1:
                self._ng_seen_since_last_ok = False
                self._ng_committed_this_press = False

        self._last_ok_state = ok_bit
        self._last_ng_state = ng_bit
        return new_ok, new_ng

    # ──────────────────────────────────────────────────────────────
    # Per-machine L6 tables (2026-05-24)
    # Operator design: ek hi mes_pulse_log me sab mix tha — alag table
    # chahiye har machine ki.  Map machine_id → table name and route
    # writes accordingly.  Final Inspection ka row me status + model
    # bhi (woh sirf main PLC se aate).  Sub-machines me sirf bit data.
    # ──────────────────────────────────────────────────────────────

    # machine_id → (table_name, supports_status_model)
    _L6_TABLE_MAP = {
        2:  ("mes_l6_final_inspection", True),
        8:  ("mes_l6_upper_rail",       False),
        10: ("mes_l6_lower_rail",       False),
        12: ("mes_l6_semi_auto",        False),
        13: ("mes_l6_ball_guide_13",    False),
        14: ("mes_l6_ball_guide_14",    False),
        16: ("mes_l6_lock_bar",         False),
        17: ("mes_l6_lower_rail",       False),   # Lower Rail Greasing → same table
    }
    # 2026-05-28 — Friendly machine name for [DB-WRITE] log lines.
    # Operator: "collector k cmd me row aani chaihye konsi machine konsi
    # bit status jo jo db me ja rha h vo cmd me dikhta rhe".
    _MACHINE_NAMES = {
        2:  "Final Inspection",
        8:  "Upper Rail Greasing",
        10: "Lower Rail Greasing",
        12: "Semi-Auto",
        13: "Ball Guide #13",
        14: "Ball Guide #14",
        16: "Lock Bar Insert",
        17: "Lower Rail Greasing",
    }

    # ── SEAT SLIDER: Semi-Auto verdict → Final Inspection bit ─────────
    # 2026-08-19 operator spec: a part the Semi-Auto marked NG must show NG
    # when it reaches Final.  On each committed Final cycle we look the part
    # code up in the SEMI verdict log; on a hit we raise the configured bit on
    # THIS (Final) PLC and record that we did.  Deliberately NOT wired into
    # counting/OEE — operator asked for "bit + log only", so a miss here can
    # never move a production number.
    #
    # `fi_sa_ng_bit` blank (every line until an admin fills it in) makes this a
    # single dict lookup and an immediate return, so lines outside Seat Slider
    # pay nothing and behave byte-for-byte as before.
    def _check_fi_fetch(self) -> None:
        """Final compare-fetch trigger (2026-08-24).  When `fi_fetch_bit` is
        configured, its rising edge — the moment the part is present and the
        station's decision is ready — is when we read the Final part code and
        run the Semi-Auto-NG compare, so the reject lands on THIS part's own
        cycle rather than one cycle late on the OK-count commit.  Cheap per-tick
        bit read; the (blocking) reject pulse only happens on the rare NG match,
        exactly like the count-commit path it replaces.  Blank bit = no-op, and
        the count-commit trigger stays in charge (unchanged for every line)."""
        _fb = self.cfg.get("fi_fetch_bit")
        if not _fb or not getattr(self, "_plc_ok", False) or self._plc is None:
            return
        try:
            _bits = self._plc.batchread_bitunits(headdevice=_fb, readsize=1)
            _cur = 1 if int(_bits[0]) else 0
        except Exception:
            return
        _rose = (_cur == 1 and self._last_fi_fetch == 0)
        self._last_fi_fetch = _cur
        # 2026-08-27 — the Final part-code register (D5004) LAGS the fetch bit:
        # measured on this PLC it fills anywhere from ~55 ms to ~1.1 s after the
        # M100 rising edge (bimodal).  A single read at the edge got "" and the
        # SA-NG lookup was skipped every time (no part ever rejected).  So on
        # the edge open a ~2 s window and read D5004 ONCE PER POLL until it
        # populates — no blocking sleep, so the count/status loop keeps running;
        # the compare fires on the first non-blank read, then the window closes.
        if _rose:
            self._fi_pending_until = time.time() + 2.0
        if getattr(self, "_fi_pending_until", 0.0) <= 0.0:
            return
        if time.time() > self._fi_pending_until:
            self._fi_pending_until = 0.0        # gave up — code never populated
            return
        pc = (self._read_part_code() or "").strip().rstrip(":") or None
        if not pc:
            return                              # still blank — retry next poll
        self._fi_pending_until = 0.0            # got the code — close the window
        _shift = self._shift_label()
        if _shift.startswith("GAP"):
            _shift = "GAP"
        _rec = getattr(self, "_cur_shift_record_date", None) or date.today()
        # Same compare-and-pulse as the count-commit path, just triggered here.
        self._sa_ng_signal_final(pc, _shift, _rec, int(self.ok_shift))

    def _sa_ng_signal_final(self, part_code, shift_name, rec_date, cycle_seq):
        _bit = self.cfg.get("fi_sa_ng_bit")
        if not _bit or not part_code:
            return
        try:
            # Was this exact part EVER NG at Semi-Auto?  2026-08-24 — operator
            # spec: an NG part can reach Final on ANY later day (it need not
            # flow through the same shift), so there is NO date window — a part
            # 2026-08-27 — LATEST Semi-Auto result wins (operator: a part that
            # was NG then re-run OK must NOT be rejected at Final).  Take the
            # most recent DEFINITIVE (non-blank) SEMI verdict for this code:
            # latest run OK (reworked) -> no reject; latest NG -> reject.  A
            # blank/no-verdict capture is skipped so it can't mask a real NG.
            # Assumes part codes are unique per physical part (serial-coded);
            # if a code is reused, a later OK on another part would clear it.
            _hit = None
            if _db_reachable(timeout=1.5):
                _c = _db_conn()
                try:
                    _cur = _c.cursor()
                    _cur.execute(
                        "SELECT result FROM mes_sa_fi_quality_log "
                        " WHERE station = 'SEMI' AND part_code = %s "
                        "   AND result IS NOT NULL "
                        " ORDER BY id DESC LIMIT 1",
                        (part_code,))
                    _row = _cur.fetchone()
                    _hit = _row[0] if _row else None
                    _cur.close()
                finally:
                    try: _c.close()
                    except Exception: pass
            if _hit != "NG":
                return          # no Semi-Auto NG for this part — nothing to do

            # Raise the bit.  Short blocking pulse (default 2 s) mirrors the
            # SA shift-reset pulse; the ladder sees a clean edge.  A write
            # failure is logged and still recorded as bit_written=False so the
            # Quality page shows the miss rather than implying the PLC was told.
            _written = False
            if getattr(self, "_plc_ok", False) and self._plc is not None:
                try:
                    self._plc.batchwrite_bitunits(headdevice=_bit, values=[1])
                    time.sleep(float(self.cfg.get("fi_sa_ng_hold") or 2.0))
                    self._plc.batchwrite_bitunits(headdevice=_bit, values=[0])
                    _written = True
                    print(f"[SA-NG->FI] part={part_code} was NG at Semi-Auto "
                          f"-- pulsed {_bit} on Final", flush=True)
                except Exception as _be:
                    print(f"[SA-NG->FI] {_bit} write FAILED for part="
                          f"{part_code}: {_be}", flush=True)
            else:
                print(f"[SA-NG->FI] PLC not connected -- {_bit} not written "
                      f"for part={part_code}", flush=True)

            _buffered_exec_own(
                "INSERT INTO mes_sa_fi_quality_log "
                "(record_date, shift_name, line_id, line_name, station, plc_id, "
                " machine_name, part_code, result, cycle_seq, sa_ng, bit_address, "
                " bit_written) "
                "VALUES (%s,%s,%s,%s,'FINAL',%s,%s,%s,%s,%s,TRUE,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (rec_date, shift_name, self.cfg["line_id"],
                 self.cfg.get("line_name"),
                 int(self.cfg.get("main_plc_id") or 0) or None,
                 self.cfg.get("main_machine_name") or "Final Inspection",
                 part_code, "NG", cycle_seq, _bit, _written))
        except Exception as _e:
            # Trace problems must never disturb the cycle-count path.
            print(f"[SA-NG->FI] check failed for part={part_code}: {_e}",
                  flush=True)

    def _write_machine_log(self, *, machine_id: int,
                           bit_type: str, bit_address: str,
                           ts, ct_seconds, part_code: str,
                           counter_val_override=None) -> None:
        """Route a raw L108/L109 rise into the machine-specific L6 table.
        Final Inspection rows automatically include current status + model.
        Other machines get a minimal row.  Counter auto-derived per
        (machine, bit_type, shift, date).

        2026-05-30 — `counter_val_override` lets register-mode callers
        pin counter_val to the D-register value at write time so the UI
        always shows the PLC's true count.  Bit-mode callers omit it
        and fall back to the MAX+1 auto-increment behaviour.

        2026-05-28 — Operator: "kuch silent kill na ho".  Hardened:
        every failure prints LOUDLY (no 5 s throttle), the call retries
        up to 2 times with reconnect on the first failure, and a final
        miss appends to a `mes_l6_writeback_queue`-equivalent print so
        we have an audit trail.  Even after fixes, the chart's ct_log
        path is independent — L6 audit failures NEVER block the chart.
        2026-05-24 — connection-leak fix: try/finally + a single short-
        lived connection per write so an exception doesn't leak."""
        meta = self._L6_TABLE_MAP.get(machine_id)
        if not meta:
            return
        table, has_status = meta

        # Prepare row payload once so retries don't recompute it.
        shift = self._shift_label()
        if shift.startswith("GAP"):
            shift = "GAP"
        # 2026-05-29 - Use shift's record_date (handles midnight cross).
        # Without this, Shift B post-midnight pulses go to a different
        # date row, splitting the shift's data across two records.
        rec_date = getattr(self, "_cur_shift_record_date", None)
        if rec_date is None:
            rec_date = ts.date() if hasattr(ts, "date") else date.today()
        if not hasattr(self, "_l6_counters"):
            self._l6_counters = {}
        key = (table, bit_type, shift, rec_date)

        ct_clean = (None if ct_seconds is None
                    else round(float(ct_seconds), 3))
        _vpath = None
        if has_status and part_code:
            import re as _re_vp
            _safe = _re_vp.sub(r"[^A-Za-z0-9._-]", "_",
                               str(part_code)).strip("_")
            if _safe:
                _vpath = f"videos/YNC-SS/{_safe}.mp4"
        status_code = int(self._cur_status) if self._cur_status is not None else None
        status_name = self._cur_status_name or None
        model_no    = int(self._cur_model)  if self._cur_model else None
        model_name  = self._cur_model_name or None

        last_exc = None
        # 2 attempts: first with whatever's in the counter cache, second
        # after a reconnect (in case the first failed on a stale TCP).
        for attempt in (1, 2):
            conn = None
            cur  = None
            try:
                conn = _db_conn()
                cur  = conn.cursor()

                # 2026-05-30 — Register-mode callers PIN counter_val to
                # the D-register value via counter_val_override.  This
                # makes the UI's per-machine "OK: N" track the PLC
                # absolute count instead of an inflated audit-row
                # counter (which historically drifted ~2x due to multi-
                # collector duplicate writes + rate-clamp re-climbs).
                # Bit-mode callers (no override) keep MAX+1 behaviour.
                if counter_val_override is not None:
                    counter_val = int(counter_val_override)
                else:
                    # Re-resolve counter inside the txn — if the cache was
                    # bumped on a previous failed attempt we'd duplicate-key
                    # otherwise.  Always reload from MAX(counter_val).
                    cur.execute(
                        f"SELECT COALESCE(MAX(counter_val), 0) FROM {table} "
                        "WHERE bit_type=%s AND shift_name=%s AND record_date=%s",
                        (bit_type, shift, rec_date),
                    )
                    counter_val = int(cur.fetchone()[0] or 0) + 1
                self._l6_counters[key] = counter_val

                if has_status:
                    cur.execute(f"""
                        INSERT INTO {table}
                          (ts, bit_type, bit_address, ct_seconds, counter_val,
                           part_code, shift_name, record_date,
                           status_code, status_name, model_no, model_name,
                           video_path)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        ts, bit_type, bit_address, ct_clean,
                        counter_val, (part_code or None),
                        shift, rec_date,
                        status_code, status_name, model_no, model_name,
                        _vpath,
                    ))
                else:
                    cur.execute(f"""
                        INSERT INTO {table}
                          (ts, bit_type, bit_address, ct_seconds, counter_val,
                           shift_name, record_date)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        ts, bit_type, bit_address, ct_clean,
                        counter_val, shift, rec_date,
                    ))
                conn.commit()
                # 2026-05-28 — Per-row DB-WRITE visibility (operator wanted
                # to see every successful insert in the collector cmd).
                # Pure ASCII only — Windows cp1252 console rejects unicode
                # arrows/dashes, which throws inside this try block AFTER
                # commit, triggering a false-positive retry that creates
                # a duplicate DB row.
                try:
                    _mname = self._MACHINE_NAMES.get(machine_id, f"machine_{machine_id}")
                    _pc_short = (part_code[:14] + "..") if (part_code and len(part_code) > 16) else (part_code or "-")
                    _ct_str = f"{ct_clean:.2f}s" if ct_clean is not None else "-"
                    print(f"[DB-WRITE] {_mname:<22} bit={bit_address:<5} "
                          f"{bit_type} ct={_ct_str:<8} pc={_pc_short:<18} "
                          f"#{counter_val} -> {table}", flush=True)
                except Exception:
                    pass  # never let log printing break the DB write path
                return
            except Exception as e:
                last_exc = e
                # LOUD error per attempt (no throttle — operator wanted
                # zero silent failures).
                print(f"[L6-WRITE-FAIL attempt={attempt}] "
                      f"{table} {bit_type} ts={ts} ct={ct_clean}: "
                      f"{str(e)[:200]}", flush=True)
            finally:
                if cur is not None:
                    try: cur.close()
                    except Exception: pass
                if conn is not None:
                    try: conn.close()
                    except Exception: pass

        # Both attempts exhausted — log unrecoverable miss + dump the
        # row so it's preserved in the collector log for manual
        # backfill if needed.
        print(f"[L6-WRITE-DROPPED] {table} {bit_type} ts={ts} "
              f"ct={ct_clean} pc={part_code!r} — both retries failed "
              f"(last_err={str(last_exc)[:120]}).  Chart ct_log still "
              f"has this cycle; backfill via SQL if needed.",
              flush=True)

    # ──────────────────────────────────────────────────────────────
    # mes_pulse_log raw-edge audit writer (2026-05-23)
    # Operator design: every L108 rise → 1 row (bit_type='OK'), every
    # L109 rise → 1 row (bit_type='NG').  Pure audit log — no gating,
    # no merging, no derived state.  Runs PARALLEL to existing
    # _update_counts logic; nothing here changes the legacy tables.
    # ──────────────────────────────────────────────────────────────

    def _write_pulse_log(self, *, machine_id: int, machine_name: str,
                         bit_type: str, bit_address: str,
                         ts, ct_seconds, part_code: str) -> None:
        """Append one raw-edge row to mes_pulse_log.  counter_val auto-
        derived from a per-(machine, bit, shift, date) running counter
        held in self._pulse_counters.  Best-effort — DB blip throttled-
        logs and never raises out of the caller (count path must not
        break on audit-log failure)."""
        try:
            shift = self._shift_label()
            if shift.startswith("GAP"):
                shift = "GAP"
            rec_date = ts.date() if hasattr(ts, "date") else date.today()
            key      = (machine_id, bit_type, shift, rec_date)
            if not hasattr(self, "_pulse_counters"):
                self._pulse_counters = {}
            # Hydrate from DB once per (machine, bit, shift, date) so a
            # collector restart mid-shift continues the counter instead
            # of resetting to 1.
            if key not in self._pulse_counters:
                try:
                    _c = _db_conn()
                    _cur = _c.cursor()
                    _cur.execute(
                        "SELECT COALESCE(MAX(counter_val), 0) "
                        "FROM mes_pulse_log "
                        "WHERE machine_id=%s AND bit_type=%s "
                        "  AND shift_name=%s AND record_date=%s",
                        (machine_id, bit_type, shift, rec_date),
                    )
                    self._pulse_counters[key] = int(_cur.fetchone()[0] or 0)
                    _cur.close(); _c.close()
                except Exception:
                    self._pulse_counters[key] = 0
            self._pulse_counters[key] += 1
            counter_val = self._pulse_counters[key]

            conn = _db_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO mes_pulse_log
                  (line_id, machine_id, machine_name, bit_type, bit_address,
                   ts, ct_seconds, counter_val, part_code,
                   shift_name, record_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                self.cfg["line_id"], machine_id, machine_name,
                bit_type, bit_address, ts,
                (None if ct_seconds is None else round(float(ct_seconds), 3)),
                counter_val,
                (part_code or None),
                shift, rec_date,
            ))
            conn.commit()
            cur.close(); conn.close()
        except Exception as e:
            _now = time.time()
            _last = getattr(self, "_pulse_log_err_last", 0)
            if _now - _last > 5.0:
                self._pulse_log_err_last = _now
                print(f"[PULSE-LOG] write failed ({bit_type} "
                      f"machine={machine_id}): {str(e)[:80]}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # NG forensic logger (one JSONL line per L109 rise / fall event)
    # ──────────────────────────────────────────────────────────────

    def _ng_forensic_write(self, event: dict) -> None:
        """Append one JSON line to a per-day NG forensic log so post-mortem
        on phantom-NG bursts can identify the trigger (ladder pulse vs
        electrical chatter vs real press vs status flap).  Path:
            Phase2/_ng_forensics_line{line_id}_{YYYY-MM-DD}.jsonl
        Writes are best-effort — never raise out of the count path."""
        try:
            import json as _json_fw
            line_id = self.cfg.get("line_id", "X")
            date_s  = datetime.now().strftime("%Y-%m-%d")
            base    = _os.path.dirname(_os.path.abspath(__file__))
            path    = _os.path.join(
                base,
                f"_ng_forensics_line{line_id}_{date_s}.jsonl",
            )
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json_fw.dumps(event, default=str) + "\n")
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Edge webhook → Camera CMS (non-blocking, best-effort)
    # ──────────────────────────────────────────────────────────────

    def _emit_edge_webhook(self, bit_label: str, edge_epoch: float) -> None:
        """POST a tiny JSON ping to the Camera CMS so it can cut a clip
        from the rolling .ts file at this exact wall-clock instant.

        Why this exists: CMS used to poll the PLC itself, but Mitsubishi
        Q-series accept only a couple of simultaneous TCP clients on
        port 5002 — when collector and CMS competed for the same socket,
        ~half the L108/L109 rising edges silently dropped on the
        collector side.  After this webhook landed, CMS no longer talks
        to the PLC; it just receives our timestamps.

        The request runs on a daemon thread so a slow / dead CMS never
        blocks the 30 ms poll loop."""
        url = getattr(self, "_edge_webhook_url", None)
        if url is None:
            self._edge_webhook_url = _os.environ.get(
                "CMS_EDGE_WEBHOOK_URL",
                "http://127.0.0.1:5555/api/plc-edge",
            )
            url = self._edge_webhook_url
        if not url:
            return
        cfg     = self.cfg
        line_id = cfg.get("line_id")
        line_nm = cfg.get("line_name", "")
        # Resolve the model name we last saw — gives CMS something
        # human-readable to filename clips with if the part_code lookup
        # fails on its end.
        model_no = getattr(self, "_cur_model", None)
        model_nm = getattr(self, "_cur_model_name", "") or ""
        # Pass through whatever part_code we last read from D5004.  CMS
        # uses this to name the extracted clip — gives the operator a
        # one-glance link from a defective part to its video.
        part_code = getattr(self, "_cur_part_code", "") or ""
        payload = {
            "line_id":      line_id,
            "line_name":    line_nm,
            "plc_ip":       cfg.get("plc_ip", ""),
            "plc_port":     cfg.get("plc_port", 5002),
            "bit":          bit_label,
            "status":       "OK" if bit_label == "L108" else "NG",
            "epoch":        edge_epoch,
            "epoch_ms":     int(edge_epoch * 1000),
            "iso":          datetime.fromtimestamp(edge_epoch).isoformat(timespec="milliseconds"),
            "part_code":    part_code,
            "model_number": model_no,
            "model_name":   model_nm,
            "ok_total":     self.ok_total,
            "ng_total":     self.ng_total,
        }
        # 2026-05-21 — RETRY LOOP.  Earlier this was single-shot with
        # 1.5 s timeout; any miss caused CMS to skip that L108 edge.
        # When CMS missed N consecutive edges, its next received edge
        # produced a phantom cycle of (N+1)× the real cycle time and a
        # multi-cycle MP4 (e.g. 127 s "cycle" containing 11 real cycles).
        # Fix: 3 retries with 250 ms → 500 ms → 1 s backoff, preserving
        # the ORIGINAL epoch_ms timestamp on each attempt so CMS sees
        # the true edge time even if delivery is delayed by ≤2 s total.
        # Timeout per attempt raised 1.5 → 3 s to cover the DB-slow
        # window when CMS's /api/plc-edge handler is mid-blocked on a
        # cycle finalization.
        def _send():
            import urllib.request, json as _j
            body = _j.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            last_exc = None
            for attempt, backoff in enumerate((0.0, 0.25, 0.5, 1.0)):
                if backoff:
                    time.sleep(backoff)
                try:
                    req = urllib.request.Request(
                        url, method="POST", headers=headers, data=body)
                    urllib.request.urlopen(req, timeout=3.0).read()
                    # Stamp for the status-table "video saved" tick — a clip
                    # edge was accepted by the Camera CMS for this part.
                    self._last_video_saved_ts = time.time()
                    if attempt > 0:
                        # Recovered after retry — log once so we know the
                        # backup attempts actually saved an edge.
                        print(f"[EDGE-WEBHOOK] {bit_label} delivered on "
                              f"attempt {attempt+1}", flush=True)
                    return
                except Exception as exc:
                    last_exc = exc
                    continue
            # All 4 attempts exhausted — log once per 30 s
            last = getattr(self, "_edge_webhook_last_err_ts", 0)
            if time.time() - last > 30:
                print(f"[EDGE-WEBHOOK] {bit_label} -> {url} failed after "
                      f"4 attempts: {last_exc}", flush=True)
                self._edge_webhook_last_err_ts = time.time()
        threading.Thread(target=_send, daemon=True,
                         name=f"edge-webhook-{bit_label}").start()

    # ----------------------------------------------------------
    # STATUS / LOSS
    # ----------------------------------------------------------

    # 2026-05-13 — operator spec: every shift's first 5 minutes are
    # reserved for model setup / hand-over, hardcoded.  Used to come
    # from `mes_shift_configs.startup_delay_min` but admins kept setting
    # it inconsistently per line; hardcoding makes every line identical.
    STARTUP_DELAY_MIN = 5

    def _is_in_startup_delay(self) -> bool:
        """True iff we're in the first STARTUP_DELAY_MIN minutes of a
        real (non-GAP) shift.  Forces MODEL_SETUP override + freezes
        the plan counter for that window."""
        if not self._cur_shift or self._cur_shift.startswith("GAP"):
            return False
        if not self._shift_start_ts:
            return False
        startup_delay = self.STARTUP_DELAY_MIN * 60
        return (time.time() - self._shift_start_ts) < startup_delay

    def _find_setup_status(self):
        """Look up the (code, name) of MODEL_SETUP in the status_map.
        Prefers loss_type == 'setup'; falls back to name matching."""
        cached = getattr(self, "_setup_status_cache", None)
        if cached is not None:
            return cached
        code, name = None, None
        for c, info in self.cfg.get("status_map", {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("loss") == "setup":
                code, name = c, info.get("name", "MODEL_SETUP")
                break
        if code is None:
            for c, info in self.cfg.get("status_map", {}).items():
                if not isinstance(info, dict):
                    continue
                nm = (info.get("name") or "").upper()
                if "SETUP" in nm:
                    code, name = c, info["name"]
                    break
        self._setup_status_cache = (code, name)
        return code, name

    def _find_break_status(self):
        """Look up the (code, name) of BREAK in the status_map.
        Prefers loss_type == 'break'; falls back to name matching.
        Returns (None, None) if no BREAK row exists — caller should
        fall back to IDLE (status code 0)."""
        cached = getattr(self, "_break_status_cache", None)
        if cached is not None:
            return cached
        code, name = None, None
        for c, info in self.cfg.get("status_map", {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("loss") == "break":
                code, name = c, info.get("name", "BREAK")
                break
        if code is None:
            for c, info in self.cfg.get("status_map", {}).items():
                if not isinstance(info, dict):
                    continue
                nm = (info.get("name") or "").upper()
                if nm == "BREAK" or nm.endswith("_BREAK"):
                    code, name = c, info["name"]
                    break
        self._break_status_cache = (code, name)
        return code, name

    # ----------------------------------------------------------
    # Breakdown auto-tracking (drives the Maintenance ANDON live table)
    # ----------------------------------------------------------
    # Whenever the line transitions INTO a status whose loss_type is
    # 'breakdown', we open a row in mes_breakdowns (state='OPEN').  When
    # it transitions OUT to any non-breakdown status, we stamp ended_at
    # and flip the row to 'RESOLVED'.  The Maintenance Dashboard reads
    # straight from this table — so ANDON + history are always live.
    def _is_breakdown_status(self, code) -> bool:
        info = self.cfg.get("status_map", {}).get(code, {})
        return isinstance(info, dict) and (info.get("loss") == "breakdown")

    def _handle_breakdown_transition(self, old_code, new_code):
        """Open / resolve mes_breakdowns rows on status changes.

        Open rule  : transition INTO a status whose loss_type='breakdown'
                     → INSERT new row state='OPEN' (skipped if one already
                     exists for this line, so a flicker between two
                     breakdown statuses doesn't create duplicates).

        Resolve rule: transition INTO RUNNING (status_code == 1) while an
                     OPEN row exists for this line → stamp ended_at and
                     flip to 'RESOLVED'.  We deliberately do NOT resolve
                     on intermediate states (BREAK, IDLE, MODEL_SETUP,
                     etc.) — per spec the ticket only graduates to
                     History when the line is truly back to RUNNING.

        Best-effort: DB hiccups must never crash the collector."""
        try:
            new_is_bd  = self._is_breakdown_status(new_code)
            new_is_run = (new_code == 1)
            # 2026-09-01 — OLD status-based breakdown generation DISABLED
            # (operator: "purana andon system hata; breakdown ab 9965 andon
            # table se aayega").  We no longer OPEN mes_breakdowns rows from the
            # PLC status (D6005 loss='breakdown') transition — breakdowns now
            # come from the maintenance_db (9965) andon flow.  Existing OPEN rows
            # are still RESOLVED below when the line returns to RUNNING (so old
            # tickets clear); only the CREATE is turned off.  To revert: delete
            # this one line.
            new_is_bd = False
            line_id    = self.cfg["line_id"]
            zone_id    = self.cfg.get("zone_id")
            now        = datetime.now()

            # Nothing to do unless we entered breakdown or returned to RUNNING.
            if not (new_is_bd or new_is_run):
                return

            with _db_conn() as conn:
                cur = conn.cursor()

                if new_is_bd:
                    # Skip if an OPEN row already exists (e.g. flicker
                    # between two breakdown sub-statuses).
                    cur.execute("""
                        SELECT 1 FROM mes_breakdowns
                         WHERE line_id = %s AND state = 'OPEN' LIMIT 1
                    """, (line_id,))
                    if cur.fetchone():
                        return

                    shift_name = self._cur_shift if self._cur_shift else None
                    cur.execute("""
                        SELECT COALESCE(MAX(serial_in_shift), 0) + 1
                          FROM mes_breakdowns
                         WHERE line_id = %s
                           AND shift_name IS NOT DISTINCT FROM %s
                           AND DATE(started_at) = DATE(%s)
                    """, (line_id, shift_name, now))
                    serial = cur.fetchone()[0] or 1

                    if zone_id is None:
                        cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s",
                                    (line_id,))
                        row = cur.fetchone()
                        zone_id = row[0] if row else None

                    status_name = self.cfg["status_map"].get(new_code, {}).get("name", str(new_code))
                    cur.execute("""
                        INSERT INTO mes_breakdowns
                            (line_id, zone_id, shift_name, serial_in_shift,
                             started_at, state, reason)
                        VALUES (%s, %s, %s, %s, %s, 'OPEN', %s)
                        RETURNING id
                    """, (line_id, zone_id, shift_name, serial,
                          now, f"Auto-detected — line entered {status_name}"))
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    print(f"[BREAKDOWN] OPEN  id={new_id} line={line_id} "
                          f"shift={shift_name} serial={serial} ({status_name})")

                elif new_is_run:
                    # Back to RUNNING — resolve any OPEN row for this line.
                    cur.execute("""
                        UPDATE mes_breakdowns
                           SET state='RESOLVED', ended_at=%s, updated_at=NOW()
                         WHERE line_id = %s AND state = 'OPEN'
                         RETURNING id
                    """, (now, line_id))
                    rows = cur.fetchall()
                    conn.commit()
                    if rows:
                        print(f"[BREAKDOWN] RESOLVE id={rows[0][0]} line={line_id} "
                              f"(line back to RUNNING)")
        except Exception as e:
            # Never let breakdown bookkeeping crash the collector.
            print(f"[BREAKDOWN] tracking failed line={self.cfg.get('line_id')}: {e}")

    def _update_status(self, status_code: int):
        now     = time.time()

        # Sentinel: -2 = "PLC never successfully read".  Skip silently
        # so the dashboard doesn't briefly flash IDLE on cold boot
        # before the first successful read lands.
        if status_code == -2:
            self._last_status_check = now
            return

        elapsed = now - self._last_status_check
        old     = self._cur_status

        # ── PLC bit-flag mask + sticky fallback ──────────────────────
        # PLC D6005 is a 16-bit word.  Operator confirmed the ladder
        # uses ONLY bits 0-3 (decimal 0-15) for the status enum; the
        # higher bits are control/remote flags PLC sets independently.
        # When a 2nd HMI / MES client opens on the LAN the PLC sets the
        # "remote active" flag (bit 4 = decimal 16) — without this mask
        # MES then read raw values 16 / 17 / 18 / etc. and the dashboard
        # flickered between unmapped codes and the real status.
        #
        # Strategy:
        #   1. Try the raw value first (back-compat for PLCs whose
        #      enum legitimately uses larger ints).
        #   2. If raw isn't in status_map, try lower nibble (raw & 0x0F)
        #      to strip control flags.
        #   3. If neither maps, hold the LAST KNOWN status and log a
        #      one-time warning so admin can add the mapping.
        status_map = self.cfg.get("status_map", {}) or {}
        raw_code   = status_code
        # Stash the most-recent raw value so the periodic display loop
        # can surface "Status (raw=N)" — diagnoses PLC ladder bugs that
        # publish unexpected codes.
        self._last_raw_status = raw_code

        if status_code not in status_map and status_code not in (0, -1):
            masked = status_code & 0x0F

            # ── Ambiguous-mask handling (2026-05-12 rev) ──
            # Operator spec, verbatim: "PLC only publishes IDLE when the
            # machine is physically stopped.  Any non-zero raw value
            # means the machine is RUNNING."
            #
            # Three sub-cases:
            #   1. raw=16/32/48 etc with masked==0  → PLC flag bit set
            #      while lower nibble is 0.  The previous "hold last
            #      known" behaviour locked the dashboard at IDLE forever
            #      when a brief raw=0 pulse preceded raw=16.  Per
            #      operator spec → RUNNING (status 1).
            #   2. masked is in status_map → use the lower nibble.
            #   3. Truly unmapped → hold last known + WARN once.
            if masked == 0 and raw_code != 0:
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published raw={raw_code} "
                          f"(bit-pattern with flag set + lower nibble 0). "
                          f"Interpreting as RUNNING per operator spec — "
                          f"PLC engineer: check D{self.cfg.get('status_addr','????')} "
                          f"ladder upper-bit assignments.")
                status_code = 1   # RUNNING
            elif masked in status_map or masked in (0, -1):
                # Bit-flag stripped — use the lower-nibble status enum.
                status_code = masked
            else:
                # Truly unmapped (neither raw nor lower nibble) — hold.
                seen = getattr(self, "_unknown_status_seen", None)
                if seen is None:
                    seen = set(); self._unknown_status_seen = seen
                if raw_code not in seen:
                    seen.add(raw_code)
                    print(f"[STATUS] WARN: PLC published unmapped code {raw_code} "
                          f"(masked {masked} also unmapped) — sticking to last known "
                          f"{self._cur_status_name!r}.  Add it under "
                          f"Admin → Production → Status Colour to map it.")
                self._last_status_check = now
                return

        # ── L108 TRUTH OVERRIDE (2026-05-16) ─────────────────────────
        # Operator reported A-shift window 09:12-09:16 painted IDLE
        # while machine was actually running.  Root cause analysis on
        # the collector log (OK count stuck at 19, PY sensors X16/X17
        # no-toggle 903s, BREAKDOWN at 09:16) showed the machine was
        # GENUINELY idle in that window — PLC D6005 was telling the
        # truth.  BUT to harden against a future PLC ladder bug that
        # publishes IDLE while L108 still fires (which would silently
        # zero out production stats), we add an L108-edge-based
        # override here:
        #   • _last_ok_edge_observed is set on every L108 0→1 edge,
        #     regardless of whether `is_running` was True at the time.
        #   • If the PLC-decoded status_code is IDLE (0) but an L108
        #     edge fired within the last L108_TRUTH_WINDOW_SEC
        #     (default 30s ≈ 2× max expected CT), we OVERRIDE to
        #     RUNNING.  Pulses on the production bit are hard
        #     evidence; the status register is software-derived and
        #     more prone to ladder bugs.
        # This is a safety net, NOT a workaround for normal flow —
        # the operator should still report any IDLE-during-production
        # so PLC engineer can fix the ladder.
        # 2026-05-22 — Window 30 → 60 s after operator complaint
        # "break ke baad IDLE jaa raha hai".  Post-break cycle resumption
        # often has a 20-40s gap before the first L108 fires (operators
        # settling back at stations, machine warm-up).  With 30s window,
        # any first-post-break L108 over 30s after PLC's D6005=0 would
        # leave IDLE painted.  60s covers the practical resume window.
        # 2026-05-26 — Bumped 60 → 180.  After collector restart or a
        # brief 1-2 min op pause, status was flipping to IDLE then
        # back to RUNNING within seconds (operator: "status idle ho
        # gya h").  3-minute window absorbs normal short interruptions
        # without flapping while still committing real long IDLE.
        L108_TRUTH_WINDOW_SEC = 180.0
        if (status_code == 0
                and self._last_ok_edge_observed
                and now - self._last_ok_edge_observed < L108_TRUTH_WINDOW_SEC):
            age = now - self._last_ok_edge_observed
            seen = getattr(self, "_l108_truth_override_seen", 0)
            self._l108_truth_override_seen = seen + 1
            # Log every override so PLC engineer has a record to chase
            # the underlying ladder bug.  Throttle to one per minute
            # so the log isn't spammed during a stuck-IDLE situation.
            if seen % 30 == 0:
                print(f"[STATUS] OK-EDGE-OVERRIDE - PLC published IDLE "
                      f"but OK edge {age:.1f}s ago -> forcing RUNNING. "
                      f"(occurrence #{seen+1}; PLC engineer should check "
                      f"D{self.cfg.get('status_addr','????')} ladder logic.)",
                      flush=True)
            status_code = 1
        # ── SUB-MACHINE TRUTH OVERRIDE (2026-05-29) ──────────────────
        # Operator physically present on line confirmed: when M-1 to
        # M-5 sub-machines are producing parts but Final Inspection
        # operator isn't pressing OK yet, the line is OBVIOUSLY still
        # running.  PLC D6005 sometimes paints IDLE in this window
        # (Final Inspection station idle ≠ line idle).  Cross-check:
        # if ANY sub-machine fired a pulse within the last
        # SUBMACHINE_TRUTH_WINDOW_SEC, override PLC's IDLE → RUNNING.
        # Sub-poller threads stamp _last_submachine_pulse_ts on every
        # successful pulse insert (line ~5338).
        SUBMACHINE_TRUTH_WINDOW_SEC = 90.0
        _last_sub = getattr(self, "_last_submachine_pulse_ts", 0.0) or 0.0
        if (status_code == 0
                and _last_sub
                and now - _last_sub < SUBMACHINE_TRUTH_WINDOW_SEC):
            sub_age = now - _last_sub
            seen_sub = getattr(self, "_sub_truth_override_seen", 0)
            self._sub_truth_override_seen = seen_sub + 1
            if seen_sub % 30 == 0:
                print(f"[STATUS] SUB-MACHINE-TRUTH-OVERRIDE — PLC IDLE "
                      f"but sub-machine fired {sub_age:.1f}s ago → "
                      f"forcing RUNNING. (occurrence #{seen_sub+1})",
                      flush=True)
            status_code = 1
        elif (status_code == 0
                and self._last_ok_edge_observed
                and now - self._last_ok_edge_observed < 120.0):
            # 2026-05-22 — BORDERLINE-MISS DIAG.  PLC says IDLE but L108
            # fired 60-120s ago.  Just outside our override window.
            # Log this so if user reports "IDLE phase post-break" we can
            # see the exact gap that's just-missing and tune the window.
            age = now - self._last_ok_edge_observed
            print(f"[STATUS] OK-EDGE-BORDERLINE - PLC IDLE, last OK edge "
                  f"{age:.1f}s ago (just outside {L108_TRUTH_WINDOW_SEC:.0f}s "
                  f"window - IDLE will commit if no edge in next "
                  f"{(self.IDLE_DWELL_SEC):.0f}s).", flush=True)

        # ── IDLE-dwell suppression (2026-05-12 fluttering-fix) ─────
        # YNC-SS PLC ladder publishes raw=0 (lower nibble IDLE) for a
        # few seconds between consecutive cycles even though the machine
        # is running normally.  Without dwell, the dashboard flaps
        # IDLE↔RUNNING every 4-6 s.  Hold the previous non-IDLE state
        # until raw stays 0 for IDLE_DWELL_SEC continuously.  Real IDLE
        # (operator stops machine) eventually commits after the dwell.
        # GAP-period transitions are NOT dwelled — non-production shift
        # phases must transition immediately.
        #
        # 2026-05-16 — TIMELINE TIMESTAMP FIX.  Previously when the dwell
        # expired we wrote the IDLE row with ts=now(), which is 25 s
        # AFTER IDLE actually started on the PLC.  Frontend then painted
        # 25 s of phantom RUNNING followed by a tiny IDLE strip → users
        # complained "timeline kuch bhi show kar rahi hai".  Now we
        # backdate the commit ts to `_pending_idle_since` so the
        # rendered segment matches the real PLC dwell window.
        dwell_commit_ts = None       # if set, write_status_log uses this
        if (status_code == 0
                and self._cur_status not in (0, -1)
                and not self._is_in_gap_period()):
            if self._pending_idle_since is None:
                self._pending_idle_since = now
            if now - self._pending_idle_since < self.IDLE_DWELL_SEC:
                # Suppress: keep previous non-IDLE state
                status_code = self._cur_status
            else:
                # Dwell expired → genuine IDLE.  Capture the true start
                # time BEFORE we clear _pending_idle_since so the DB row
                # reflects when IDLE really began, not when dwell expired.
                dwell_commit_ts = datetime.fromtimestamp(self._pending_idle_since)
                self._pending_idle_since = None
        else:
            # Any non-IDLE arrival clears a pending dwell
            self._pending_idle_since = None

        if not (self._cur_shift and self._cur_shift.startswith("GAP")):
            # Loss accumulates based on the PLC's reported status — no
            # shift-start grace window. If the machine publishes BREAKDOWN
            # at 08:34, those seconds become breakdown loss even if we're
            # still inside the old "startup delay" minute-count.
            old_info  = status_map.get(old, {})
            loss_type = old_info.get("loss") if isinstance(old_info, dict) else None
            if loss_type and loss_type in self._loss:
                self._loss[loss_type] += elapsed

        # ── CONDITIONAL SCHEDULE OVERRIDES (2026-05-12) ────────────
        # Reinstated by operator request after observing GAP_BA showing
        # RUNNING even though the machine was clearly stopped — the PLC
        # ladder leaves D6005 holding its last RUNNING value when the
        # operator forgets to hit the IDLE button between shifts.
        #
        # Three soft overrides — BUT ONLY when PLC publishes RUNNING (1).
        # Real loss codes from PLC (BREAKDOWN / QUALITY_ISSUE /
        # MATERIAL_WAIT / OTHER_LOSS / CHANGE_OVER / MODEL_SETUP) ALWAYS
        # pass through unchanged because those represent actual machine
        # state more important than the schedule.
        #
        #   1. GAP between shifts         → IDLE
        #   2. Scheduled break (lunch/tea) → IDLE
        #   3. Shift-start startup delay  → MODEL_SETUP
        #
        # Bit-level decoding above (`& 0x0F` mask + ambiguous-IDLE guard)
        # is preserved.  This block only acts on the already-decoded code.
        in_break, break_name = self._is_break()
        override_reason = None

        # 2026-05-13 — startup-delay is now an UNCONDITIONAL hard
        # override.  Earlier this was gated behind `status_code == 1`,
        # so if the PLC published IDLE / BREAKDOWN during the first
        # 5 min the dashboard showed THAT instead of MODEL_SETUP.
        # Operator wants the first 5 min painted MODEL_SETUP regardless
        # of what the PLC reports, so hand-over time is unambiguous.
        if self._is_in_startup_delay():
            setup = self._find_setup_status()
            if setup and setup[0] is not None:
                status_code = setup[0]              # → MODEL_SETUP
                override_reason = "STARTUP_DELAY"

        # Soft overrides — fire whenever PLC reports a NEUTRAL status
        # (RUNNING / IDLE) but the wall clock says we're inside a
        # scheduled non-production window.  Real loss codes from PLC
        # (BREAKDOWN / QUALITY / MATERIAL / SETUP / CHANGE_OVER /
        # OTHER) pass through unchanged so a genuine fault during break
        # is never masked.
        #
        # 2026-05-18 — Extended the gate from `status_code == 1` to
        # `status_code in (0, 1)`.  The old logic only painted BREAK
        # when PLC happened to report RUNNING; once the machine truly
        # stopped (PLC=0 IDLE) the override skipped and the timeline
        # painted IDLE over the rest of the break.  Operator complaint:
        # "break time IDLE so overwrite hue hai".  Now BREAK covers the
        # full scheduled window regardless of PLC IDLE/RUNNING flap.
        elif status_code in (0, 1):
            if self._is_in_gap_period():
                status_code = 0                     # → IDLE
                override_reason = "GAP"
            elif in_break:
                # → BREAK if mes_status_mappings has a row with
                # loss_type='break'; otherwise fall back to IDLE.
                # Operator wants the dashboard timeline to PAINT the
                # scheduled break in blue (#7dd3fc), distinct from
                # operator-absent IDLE.
                brk = self._find_break_status()
                if brk and brk[0] is not None:
                    status_code = brk[0]
                else:
                    status_code = 0                 # legacy fallback
                override_reason = f"BREAK[{break_name or '?'}]"

        # ── GENERAL TRANSITION DWELL (2026-07-08 — bleed/flap guard) ──
        # A frame-desync on the shared PLC socket bleeds the status word
        # (raw 16→RUNNING, 21→MATERIAL_WAIT, …) into rapid NON-IDLE flaps
        # the IDLE-only dwell above cannot catch.  Every flap (a) paints a
        # phantom timeline segment AND (b) mis-attributes `elapsed` into a
        # loss bucket, corrupting OEE (YRA-SS: 1724 flaps / 8000 log lines).
        # Cure: a CHANGED non-IDLE status must persist continuously for
        # status_flap_dwell_s before it commits — an alternating bleed never
        # sustains it, a real status always does.  IDLE(0) keeps its own
        # longer dwell above; sentinels, same-status and the first-read seed
        # are untouched.  DISPLAY + LOSS only — never touches the count path.
        _flap_dwell = float(self.cfg.get("status_flap_dwell_s", 2.5))
        if (getattr(self, "_status_seeded", False)
                and status_code != self._cur_status
                and status_code not in (0, -1, -2)):
            if getattr(self, "_pending_status_code", None) != status_code:
                # New candidate — start its confirmation timer, hold current.
                self._pending_status_code  = status_code
                self._pending_status_since = now
                status_code = self._cur_status
            elif (now - getattr(self, "_pending_status_since", now)) < _flap_dwell:
                status_code = self._cur_status          # still proving → hold
            # else: persisted ≥ dwell → let the real change fall through.
        elif status_code == self._cur_status:
            self._pending_status_code = None            # reality reasserted

        info        = self.cfg["status_map"].get(status_code, {})
        status_name = info.get("name", str(status_code))

        # 2026-05-15 — Seed-write on first PLC read so the timeline has
        # an anchor even when collector starts up while machine is
        # already in its terminal state (e.g. RUNNING since last shift)
        # and no transition will ever fire.  Without this seed, the
        # timeline bar shows neutral gray until the next status change,
        # which can be hours.  The DB-side dedup in _write_status_log
        # prevents this from spamming when restarts cluster together.
        if not getattr(self, "_status_seeded", False) and status_code not in (-1, -2):
            self._cur_status_name = status_name
            self._write_status_log(status_name)
            self._status_seeded = True

        if status_code != self._cur_status:
            self._cur_status      = status_code
            self._cur_status_name = status_name
            self.ct.set_running(status_code == 1)
            old_name = self.cfg["status_map"].get(old, {}).get("name", str(old))
            # Include raw PLC value so we can diagnose phantom transitions —
            # "RUNNING -> IDLE +4.7s (raw=0)" means PLC truly said IDLE,
            # "RUNNING -> IDLE +4.7s (raw=16)" would have been the masking bug.
            raw_str = ""
            if hasattr(self, "_last_raw_status") and self._last_raw_status != status_code:
                raw_str = f" (raw={self._last_raw_status})"
            override_tag = f" [override:{override_reason}]" if override_reason else ""
            print(f"[STATUS] {old_name} -> {self._cur_status_name} +{elapsed:.1f}s{raw_str}{override_tag}")
            # 2026-05-15 — Department review:  Timeline must be painted
            # ONLY from the collector's PLC-bit reading.  Earlier the
            # frontend POSTed status changes to mes_status_log, which
            # meant N open dashboards (operator HMI + supervisor LCD +
            # plant manager laptop) each wrote their OWN debounced
            # interpretation — different polling jitter, different
            # transient PLC blips → timeline filled with bogus IDLE /
            # BREAKDOWN / BREAK chunks even while the count incremented.
            #
            # Authoritative write moves here: collector sees the PLC
            # status bit directly at 30 ms cadence and is the single
            # source of truth.  Frontends now READ-ONLY from this table.
            # When this transition is the IDLE-dwell expiration, pass
            # the true PLC-side IDLE start time (dwell_commit_ts) so
            # the timeline row's ts is when IDLE *really* started, not
            # when the dwell timer hit zero (25 s late).
            self._write_status_log(self._cur_status_name, at_ts=dwell_commit_ts)
            # Auto-track Maintenance breakdown ticket on this transition
            # — opens an ANDON row when entering breakdown, resolves it
            # when leaving.  Frontend MaintenanceDashboard polls the same
            # rows, so the ANDON + History tables update in realtime.
            self._handle_breakdown_transition(old, status_code)

        self._last_status_check = now

    # ----------------------------------------------------------
    # STATUS LOG WRITER — single source of truth (2026-05-15)
    # ----------------------------------------------------------
    def _write_status_log(self, status_name: str, at_ts: Optional[datetime] = None) -> None:
        """Append one row to mes_status_log for the line.  Called from
        _update_status() on every PLC status transition.

        `at_ts` (optional) lets the caller backdate the entry to when
        the PLC actually entered this status.  Used by the IDLE-dwell
        path so the timeline doesn't show 25 s of phantom RUNNING
        followed by a thin IDLE strip.  Defaults to datetime.now().

        Idempotent guard: if the LAST row already matches this status
        the insert is skipped (cheap noise filter against rapid
        transition retries).
        """
        line_id = self.cfg.get("line_id")
        if not line_id or not self._cur_shift or self._cur_shift.startswith("GAP"):
            # GAP rows would pollute the per-shift timeline — and
            # operators don't care about the gap-period status anyway.
            return
        ts   = at_ts if at_ts is not None else datetime.now()
        nmf  = ts.hour * 60.0 + ts.minute + ts.second / 60.0 + ts.microsecond / 60_000_000.0
        _sql = ("INSERT INTO mes_status_log "
                "(line_id, record_date, shift_name, status, ts, nowminfrac) "
                "VALUES (%s, %s, %s, %s, %s, %s)")
        _params = (line_id, ts.date(), self._cur_shift, status_name, ts, nmf)
        try:
            if not self._ensure_db_connection():
                # DB down — don't lose the status transition; durable-buffer it.
                if _QUEUE is not None:
                    _QUEUE.append(_sql, _params, False)
                return
            cur = self._db.cursor()
            cur.execute(
                "SELECT status FROM mes_status_log "
                "WHERE line_id = %s ORDER BY ts DESC LIMIT 1",
                (line_id,),
            )
            last = cur.fetchone()
            if last and last[0] == status_name:
                cur.close()
                return
            cur.execute(_sql, _params)
            self._db.commit()
            cur.close()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            print(f"[STATUS-LOG] DB drop mid-write -> buffering: {e}")
            try: self._db.rollback()
            except Exception: pass
            if _QUEUE is not None:
                _QUEUE.append(_sql, _params, False)
        except Exception as e:
            print(f"[STATUS-LOG] write failed: {e}")
            try: self._db.rollback()
            except Exception: pass

    # ----------------------------------------------------------
    # OEE
    # ----------------------------------------------------------

    def _oee(self) -> dict:
        if not self._shift_start_ts or (self._cur_shift or "").startswith("GAP"):
            return {"avail": 0, "perf": 0, "qual": 100,
                    "overall": 0, "grade": "GAP"}

        working_seconds = self._working_seconds()
        plan_s          = max(1, working_seconds)
        total           = self.ok_shift + self.ng_shift

        # 2026-05-15 — OEE FIX after department review.  Old code summed
        # ALL loss buckets (including speed) into total_loss and used
        # run_s = plan_s − total_loss in BOTH availability and
        # performance.  That makes the math self-cancel:
        #   speed_loss ≡ actual_run − total*ideal_ct
        #   run_s     == actual_run − speed_loss == total*ideal_ct
        #   perf      == total*ideal_ct / run_s × 100 == 100%
        # Result: Performance was pinned to 100% no matter how slow
        # the cycles ran.  Textbook OEE keeps Speed loss in the
        # Performance ratio ONLY (via the total × ideal_ct numerator),
        # so we now split losses into "availability" (downtime) and
        # "speed" buckets and use the downtime-only run_s for both
        # avail and perf calculations.
        avail_losses = (
            self._loss.get("breakdown",   0.0)
            + self._loss.get("quality",     0.0)
            + self._loss.get("setup",       0.0)
            + self._loss.get("material",    0.0)
            + self._loss.get("others",      0.0)
            + self._loss.get("change_over", 0.0)
        )
        run_s = max(0, plan_s - avail_losses)

        avail   = min(100, max(0, run_s / plan_s * 100))
        # Performance now reflects SPEED LOSS directly: the fraction of the
        # run window NOT lost to slow cycles → perf = (run_s − speed_loss)/run_s.
        # (Was total×ideal_ct/run_s, which capped at 100% whenever the part
        #  count was high vs run time, ignoring the tracked speed loss.)
        _speed  = float(self._loss.get("speed", 0.0))
        perf    = (min(100, max(0, (run_s - _speed) / run_s * 100))
                   if run_s > 0 else 0)
        qual    = (self.ok_shift / total * 100) if total > 0 else 100
        overall = avail * perf * qual / 10000

        if overall >= 85:   grade = "EXCELLENT"
        elif overall >= 75: grade = "GOOD"
        elif overall >= 65: grade = "AVERAGE"
        elif overall >= 55: grade = "FAIR"
        else:               grade = "POOR"

        return {"avail":   round(avail, 2),   "perf":    round(perf, 2),
                "qual":    round(qual, 2),     "overall": round(overall, 2),
                "grade":   grade}

    # ----------------------------------------------------------
    # HOURLY
    # ----------------------------------------------------------

    def _update_hourly(self, new_ok: int, new_ng: int):
        if self._is_in_gap_period():
            return
        slot = self._get_current_slot()
        if not slot:
            return
        if slot not in self._hourly_data:
            self._hourly_data[slot] = {"ok": 0, "ng": 0, "plan": 0}

        self._hourly_data[slot]["ok"] += new_ok
        self._hourly_data[slot]["ng"] += new_ng

        new_plan = self._realtime_slot_plan(slot)
        if new_plan > self._hourly_data[slot].get("plan", 0):
            self._hourly_data[slot]["plan"] = new_plan

        self._write_hourly_to_db(slot)

        if slot != self._cur_hour_key:
            if self._cur_hour_key:
                self._write_hourly_to_db(self._cur_hour_key)
            self._cur_hour_key = slot

    def _write_hourly_to_db(self, slot: str):
        if not self._shift_id or not self._ensure_db_connection():
            return
        # 2026-08-26 — Loop Pipe lines count from a data register with no
        # count-bit, so the edge path can't feed the per-slot buckets and this
        # would write a stuck hour_*_actual=0 that CLOBBERS the live value the
        # API's hourly_sync worker fills from the ct_log.  Skip it for those
        # lines: the sync owns hour_*_ok/_ng/_actual; the plan column is still
        # written by _refresh_all_slot_plans.  Counting (ok_count) untouched.
        if str(self.cfg.get("table_name", "")).startswith("loop_pipe") or self.cfg.get("defer_hourly"):
            return
        col = self.cfg["slot_to_db"].get(slot)
        if not col:
            return

        hd       = self._hourly_data.get(slot, {})
        ok_count = hd.get("ok",   0)
        ng_count = hd.get("ng",   0)
        plan     = hd.get("plan", 0)
        actual   = ok_count + ng_count
        variance = actual - plan

        try:
            cur = self._db.cursor()
            cur.execute(f"""
                UPDATE {self.cfg['table_name']} SET
                    {col}_ok       = %s,
                    {col}_ng       = %s,
                    {col}_plan     = %s,
                    {col}_actual   = %s,
                    {col}_variance = %s,
                    updated_at     = NOW()
                WHERE id = %s
            """, (ok_count, ng_count, plan, actual, variance, self._shift_id))
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[HOURLY] Write error: {e}")
            self._db_ok = False
            self._safe_rollback()

    def _refresh_all_slot_plans(self):
        if not self._shift_id or not self._ensure_db_connection():
            return
        try:
            cur       = self._db.cursor()
            all_slots = [sl for slots in self.cfg["hourly_plan"].values()
                         for sl in slots.keys()]
            for slot in all_slots:
                rt     = self._realtime_slot_plan(slot)
                col    = self.cfg["slot_to_db"].get(slot)
                if not col:
                    continue
                stored = self._hourly_data.get(slot, {}).get("plan", 0)
                final  = max(rt, stored)
                if slot in self._hourly_data:
                    self._hourly_data[slot]["plan"] = final
                hd       = self._hourly_data.get(slot, {})
                actual   = hd.get("ok", 0) + hd.get("ng", 0)
                variance = actual - final
                # 2026-08-26 — Loop Pipe: hour_*_actual/_variance are owned by
                # the API hourly_sync (ct_log-based); write only the plan here
                # so the stuck edge-path actual (0) can't skew the variance.
                if str(self.cfg.get("table_name", "")).startswith("loop_pipe") or self.cfg.get("defer_hourly"):
                    cur.execute(f"""UPDATE {self.cfg['table_name']} SET
                                        {col}_plan = %s, updated_at = NOW()
                                     WHERE id = %s""", (final, self._shift_id))
                else:
                    cur.execute(f"""
                        UPDATE {self.cfg['table_name']} SET
                            {col}_plan     = %s,
                            {col}_variance = %s,
                            updated_at     = NOW()
                        WHERE id = %s
                    """, (final, variance, self._shift_id))
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[REFRESH] Error: {e}")
            self._db_ok = False
            self._safe_rollback()

    # ----------------------------------------------------------
    # SHIFT RECORD
    # ----------------------------------------------------------

    def _get_or_create_shift(self, shift_name: str, record_date: date) -> int:
        tbl = self.cfg["table_name"]
        cur = self._db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Cleanup: mark stale orphaned non-completed rows as completed
        try:
            cur.execute(f"""
                UPDATE {tbl}
                SET is_shift_completed = true, updated_at = NOW()
                WHERE is_shift_completed = false
                  AND (timestamp IS NULL OR timestamp < NOW() - INTERVAL '30 seconds')
                  AND id != COALESCE((
                      SELECT id FROM {tbl}
                      WHERE shift_name = %s AND record_date = %s
                        AND is_shift_completed = false
                      ORDER BY created_at DESC LIMIT 1
                  ), -1)
            """, (shift_name, record_date))
            self._db.commit()
        except Exception as _oc_err:
            print(f"[SHIFT] Orphan cleanup warning: {_oc_err}")
            self._db.rollback()

        # Check for active (non-completed) shift record
        cur.execute(f"""
            SELECT id FROM {tbl}
            WHERE record_date = %s AND shift_name = %s
              AND is_shift_completed = false
            ORDER BY created_at DESC LIMIT 1
        """, (record_date, shift_name))
        row = cur.fetchone()
        if row:
            shift_id = row["id"]
            self._shift_start_ts = self._get_shift_start_timestamp(
                shift_name, record_date)
            print(f"[SHIFT] Continuing existing {shift_name} ID={shift_id}")
            self._shift_id = shift_id
            self._load_shift_data(shift_id)
            self._init_all_hourly_slots_for_shift(shift_name)
            self._write_all_slots_to_db_once()
            cur.close()
            return shift_id

        # B shift — check yesterday
        if shift_name == "B":
            yesterday = record_date - timedelta(days=1)
            cur.execute(f"""
                SELECT id FROM {tbl}
                WHERE record_date = %s AND shift_name = 'B'
                  AND is_shift_completed = false
                ORDER BY created_at DESC LIMIT 1
            """, (yesterday,))
            row = cur.fetchone()
            if row:
                shift_id = row["id"]
                self._shift_start_ts = self._get_shift_start_timestamp(
                    shift_name, yesterday)
                print(f"[SHIFT] Continuing B shift from yesterday ID={shift_id}")
                self._shift_id = shift_id
                self._load_shift_data(shift_id)
                self._init_all_hourly_slots_for_shift(shift_name)
                self._write_all_slots_to_db_once()
                cur.close()
                return shift_id

        # Check for a completed record
        cur.execute(f"""
            SELECT id FROM {tbl}
            WHERE record_date=%s AND shift_name=%s AND is_shift_completed=true
            ORDER BY id DESC LIMIT 1
        """, (record_date, shift_name))
        row = cur.fetchone()
        if row:
            shift_id = row["id"]
            self._shift_start_ts = self._get_shift_start_timestamp(
                shift_name, record_date)

            # ── OT resumption: if overtime is active for this shift, REOPEN the
            # completed row with its existing counts instead of resetting to 0.
            # This way actual continues from 1554 (or wherever it left off).
            ot = self._check_ot_active()
            if ot == shift_name:
                self._db.cursor().execute(f"""
                    UPDATE {tbl} SET
                        is_shift_completed=false,
                        operating_status=%s, timestamp=NOW()
                    WHERE id=%s
                """, (self._cur_status_name, shift_id))
                self._db.commit()
                self._shift_id = shift_id
                self._load_shift_data(shift_id)
                self._init_all_hourly_slots_for_shift(shift_name)
                self._write_all_slots_to_db_once()
                cur.close()
                print(f"[SHIFT] OT resuming {shift_name} ID={shift_id} (keeping existing counts)")
                return shift_id

            # Normal shift restart — reset to 0
            self._reset_counts()
            _reset_scfg = self.cfg["shifts"].get(shift_name)
            _reset_plan = 0 if shift_name.startswith("GAP") else (_reset_scfg.get("total_plan", 0) if _reset_scfg else 0)
            self._db.cursor().execute(f"""
                UPDATE {tbl} SET
                    ok_count=0, ng_count=0,
                    shift_plan=%s, shift_plan_remaining=%s,
                    shift_plan_completed=0,
                    is_shift_completed=false,
                    operating_status=%s, timestamp=NOW()
                WHERE id=%s
            """, (_reset_plan, _reset_plan, self._cur_status_name, shift_id))
            self._db.commit()
            self._init_all_hourly_slots_for_shift(shift_name)
            self._shift_id = shift_id
            self._write_all_slots_to_db_once()
            cur.close()
            print(f"[SHIFT] Reset existing record ID={shift_id}")
            return shift_id

        # Create new shift record
        self._reset_counts()
        is_gap = shift_name.startswith("GAP")
        scfg   = self.cfg["shifts"].get(shift_name)
        plan   = 0 if is_gap else (scfg.get("total_plan", 0) if scfg else 0)

        shift_start_time = scfg["start_time"] if scfg else dt_time(8, 30)
        if isinstance(shift_start_time, dt_time):
            shift_start_time = shift_start_time.strftime("%H:%M:%S")

        cur2 = self._db.cursor()
        cur2.execute(f"""
            INSERT INTO {tbl}
                (record_date, shift_name, shift_start_time, line_name,
                 ok_count, ng_count,
                 shift_plan, shift_plan_remaining, shift_plan_completed,
                 cycle_time_plan, operating_status, is_shift_completed,
                 period_type, is_gap_time, timestamp)
            VALUES (%s,%s,%s,%s,0,0,%s,%s,0,%s,%s,false,%s,%s,NOW())
            RETURNING id
        """, (
            record_date, shift_name, shift_start_time,
            self.cfg["line_name"],
            plan, plan, self.cfg["ideal_ct"],
            self._cur_status_name,
            "GAP" if is_gap else "SHIFT", is_gap,
        ))
        shift_id = cur2.fetchone()[0]
        self._db.commit()
        cur2.close()
        cur.close()

        self._shift_id = shift_id
        self._shift_start_ts = self._get_shift_start_timestamp(
            shift_name, record_date)
        self._init_all_hourly_slots_for_shift(shift_name)
        self._write_all_slots_to_db_once()
        print(f"[SHIFT] Created new {shift_name} ID={shift_id}")
        return shift_id

    def _load_shift_data(self, shift_id: int):
        """
        Load all persisted data from DB into memory on collector startup/resume.
        Restores: ok/ng counts, loss seconds, hourly slot data,
                  cycle times (ct1-ct20), and break accumulator.
        This ensures the collector continues exactly from where it left off.
        """
        tbl = self.cfg["table_name"]
        cur = self._db.cursor()
        try:
            # ── 1. Load counts + losses + hourly slots ─────────────────
            slot_columns = []
            for prefix in self.cfg["slot_to_db"].values():
                slot_columns.extend([
                    f"{prefix}_ok", f"{prefix}_ng",
                    f"{prefix}_plan", f"{prefix}_actual", f"{prefix}_variance"
                ])
            cols = [
                "ok_count", "ng_count",
                "loss_breakdown_seconds", "loss_quality_seconds",
                "loss_setup_seconds",     "loss_material_seconds",
                "loss_others_seconds",    "loss_speed_seconds",
                "loss_change_over_seconds",
            ] + slot_columns

            cur.execute(
                f"SELECT {', '.join(cols)} FROM {tbl} WHERE id = %s",
                (shift_id,))
            row = cur.fetchone()
            if row:
                self.ok_shift             = row[0] or 0
                self.ng_shift             = row[1] or 0
                self._loss["breakdown"]   = row[2] or 0
                self._loss["quality"]     = row[3] or 0
                self._loss["setup"]       = row[4] or 0
                self._loss["material"]    = row[5] or 0
                self._loss["others"]      = row[6] or 0
                self._loss["speed"]       = row[7] or 0
                self._loss["change_over"] = row[8] or 0
                self.ct.speed_loss        = self._loss["speed"]

                idx = 9
                for slot_label in self.cfg["slot_to_db"].keys():
                    if idx + 2 < len(row):
                        if slot_label not in self._hourly_data:
                            self._hourly_data[slot_label] = {"ok": 0, "ng": 0, "plan": 0}
                        self._hourly_data[slot_label]["ok"]   = row[idx]     or 0
                        self._hourly_data[slot_label]["ng"]   = row[idx + 1] or 0
                        self._hourly_data[slot_label]["plan"] = row[idx + 2] or 0
                        idx += 5

                print(f"[SHIFT] Loaded: OK={self.ok_shift}, NG={self.ng_shift}, "
                      f"Loss={sum(self._loss.values()):.0f}s")

            # ── 2. Restore cycle times from DB (ct1-ct20) ──────────────
            # This prevents the CT avg from resetting to ideal_ct on restart
            try:
                ct_col_list = ", ".join(f"ct{i}" for i in range(1, 21))
                cur.execute(
                    f"SELECT {ct_col_list} FROM {tbl} WHERE id = %s",
                    (shift_id,))
                ct_row = cur.fetchone()
                if ct_row:
                    ct_vals = [float(v) for v in ct_row
                               if v is not None and float(v) > 0]
                    if ct_vals:
                        self.ct.cycle_times = ct_vals[-20:]
                        print(f"[SHIFT] Restored {len(ct_vals)} cycle time samples "
                              f"(avg={sum(ct_vals)/len(ct_vals):.2f}s)")
            except Exception as ct_err:
                print(f"[SHIFT] CT restore warning (non-fatal): {ct_err}")

            # ── 3. Restore break accumulator ───────────────────────────
            # Calculate how much break time has already passed since shift
            # start so _working_seconds() doesn't recount past breaks.
            self._break_seconds_acc  = 0.0
            self._cur_break_start_ts = None

            if self._shift_start_ts:
                now_dt         = datetime.now()
                shift_start_dt = datetime.fromtimestamp(self._shift_start_ts)

                for b in self.cfg["breaks"]:
                    bs = b["start_time"]
                    be = b["end_time"]
                    if isinstance(bs, str):
                        bs = dt_time(*map(int, bs.split(":")))
                    if isinstance(be, str):
                        be = dt_time(*map(int, be.split(":")))

                    bs_dt = datetime.combine(shift_start_dt.date(), bs)
                    be_dt = datetime.combine(shift_start_dt.date(), be)
                    if b["crosses_midnight"]:
                        be_dt += timedelta(days=1)

                    # Only count breaks that have fully ended before now
                    # (ongoing break is handled by _cur_break_start_ts logic)
                    ov_s = max(shift_start_dt, bs_dt)
                    ov_e = min(now_dt, be_dt)
                    if ov_e > ov_s:
                        self._break_seconds_acc += (ov_e - ov_s).total_seconds()

                print(f"[SHIFT] Break accumulator restored: "
                      f"{self._break_seconds_acc:.1f}s elapsed in breaks so far")

        except Exception as e:
            print(f"[SHIFT] Error loading data: {e}")
            traceback.print_exc()
        finally:
            cur.close()

    def _reset_counts(self):
        # 2026-05-30 — In register-mirror mode the OK/NG count is OWNED by
        # the PLC register (set every poll in _read_plc) and only resets when
        # the PLC zeroes that register — driven by the per-machine
        # shift_reset_bit.  So the clock-based shift change must NOT zero the
        # count here, otherwise the very next poll's mirror would restore it
        # and the two would fight (visible flicker).  Losses / hourly / CT
        # remain clock-scoped and reset normally below.
        if (self.cfg.get("count_mode") or "bit").lower() != "register":
            self.ok_shift        = 0
            self.ng_shift        = 0
        self._loss           = {k: 0.0 for k in self._loss}
        self._plan_completed = 0
        self._hourly_data    = {}
        self.ct.reset()
        # Reset break accumulator so every new shift starts clean
        self._break_seconds_acc  = 0.0
        self._cur_break_start_ts = None

    # ----------------------------------------------------------
    # REGISTER-MIRROR SHIFT ROLLOVER (2026-05-30)
    # ----------------------------------------------------------

    def _maybe_archive_and_reset_shift(self) -> None:
        """Shift rollover for register-mirror machines, driven by the
        per-machine `shift_reset_bit`.

        Operator design (Hinglish): shift end pe ek bit ~2s ON hoti hai.
        Us ke rising edge par hum closing OK/NG count ko archive table me
        likhte hain.  Count ka 0 hona PLC khud register zero karke karta
        hai — mirror dashboard ko 0 pe le aata hai.  Hamara kaam sirf
        data ko SAFELY archive karna hai, reset se PEHLE.

        Interlock ("jab tak data move na ho"): rising edge par closing
        count ka snapshot le lete hain (peak = shift ka high-water mark,
        taaki register pehle hi 0 ho jaye to bhi sahi value miley) aur
        usko har poll retry karke archive table me commit karte hain.
        Jab tak INSERT commit na ho, snapshot pending rehta hai — data
        kabhi lost nahi hota.

        No-op unless count_mode=register AND shift_reset_bit configured,
        so bit-mode machines and not-yet-configured register machines are
        completely untouched (zero regression)."""
        _bit = self.cfg.get("shift_reset_bit")
        if not _bit:
            return
        if (self.cfg.get("count_mode") or "bit").lower() != "register":
            return
        # PLC down — can't read the bit this poll, but still retry any
        # already-captured pending archive so a transient DB outage during
        # the last rollover eventually flushes.
        if not getattr(self, "_plc_ok", False) or self._plc is None:
            self._flush_pending_shift_archive()
            return
        # Stagger: read the reset bit at most ~once per second, separate
        # from the 30 ms register reads, so we don't pile simultaneous
        # reads on the shared MC socket ("alag alag time poll kar").
        _nowm = time.monotonic()
        if _nowm - getattr(self, "_shift_bit_last_read", 0.0) < 1.0:
            self._flush_pending_shift_archive()
            return
        self._shift_bit_last_read = _nowm
        _val = None
        try:
            _r = self._plc.batchread_bitunits(headdevice=_bit, readsize=1)
            if _r:
                _val = int(_r[0])
        except Exception:
            _val = None
        if _val is None:
            self._flush_pending_shift_archive()
            return
        _last_bit = getattr(self, "_last_shift_reset_bit", 0)
        # ── 2026-05-30 (v2) — Bleed-proof the rising-edge detection ─────
        # L110 is COLLECTOR-DRIVEN (see _maybe_pulse_fi_shift_reset): the
        # bit is only legitimately 1 for ~3s while WE hold our OWN pulse ON
        # at a real shift end.  The Final-Inspection Type4E socket is shared
        # and crosses MC responses, so a mid-shift read of L110 intermittently
        # returns a spurious 1 — the SAME bleed that briefly drops D101 to
        # 1/16 and is rejected by [REG-GARBAGE-DROP].  Trusting those false
        # 1's fired 203 phantom [SHIFT-ROLLOVER]s in a SINGLE shift, hammering
        # the archive and — whenever a phantom edge lined up with a low
        # register read — walking the dashboard count back toward 0 (the
        # operator's "2-3 badhta phir ruk jata hai").  A GENUINE edge ALWAYS
        # coincides with our own pulse, so honour the edge ONLY while that
        # pulse is active — the identical guard the register-drop honour
        # already uses (search _fi_reset_pulse_on_since above).  Zero
        # regression: a real shift-end pulse still fires exactly one archive;
        # bleed 1's are folded into a throttled [RESET-BIT-BLEED] summary.
        _pulse_active = getattr(self, "_fi_reset_pulse_on_since", None) is not None
        # Rising edge 0->1 (while OUR pulse is ON) => snapshot closing count.
        if _last_bit == 0 and _val == 1 and _pulse_active:
            _closing_ok = max(int(getattr(self, "_ok_shift_peak", 0)),
                              int(self.ok_shift or 0))
            _closing_ng = max(int(getattr(self, "_ng_shift_peak", 0)),
                              int(self.ng_shift or 0))
            _mid = int(self.cfg.get("main_plc_id") or self.cfg["line_id"])
            self._pending_shift_archive = {
                "machine_id":   _mid,
                "line_id":      self.cfg.get("line_id"),
                "machine_name": self._MACHINE_NAMES.get(
                                    _mid, self.cfg.get("line_name")),
                "record_date":  getattr(self, "_cur_shift_record_date", None)
                                    or datetime.now().date(),
                "shift_name":   (self._shift_label()),
                "ok_count":     _closing_ok,
                "ng_count":     _closing_ng,
                "reset_bit":    _bit,
            }
            print(f"[SHIFT-ROLLOVER] {_bit} 0->1 — closing shift "
                  f"{self._pending_shift_archive['shift_name']} "
                  f"OK={_closing_ok} NG={_closing_ng}; archiving BEFORE "
                  f"reset (interlock).", flush=True)
        elif _last_bit == 0 and _val == 1 and not _pulse_active:
            # Phantom rising edge from shared-socket bleed — our L110 pulse
            # is NOT active, so this is NOT a real shift end.  Suppress the
            # archive entirely and fold into a throttled summary so we keep
            # proof the filter works without 200+ phantom rollover lines.
            self._false_reset_edges = int(
                getattr(self, "_false_reset_edges", 0)) + 1
            _fnow = time.monotonic()
            if _fnow - getattr(self, "_false_reset_log_ts", 0.0) >= 30.0:
                print(f"[RESET-BIT-BLEED] {_bit} read 1 with no collector "
                      f"pulse active -> phantom shift-edge ignored "
                      f"({self._false_reset_edges} suppressed; live count "
                      f"and archive untouched).", flush=True)
                self._false_reset_log_ts = _fnow
        self._last_shift_reset_bit = _val
        # Interlock retry — flush whatever is pending.
        self._flush_pending_shift_archive()

    def _flush_pending_shift_archive(self) -> None:
        """Commit the pending shift-close snapshot to mes_shift_count_archive.
        Retries on every call until the INSERT succeeds (interlock: data
        must be safely moved before the shift is considered rolled over).
        On success, resets the running peak so the NEW shift archives its
        own closing value.  Does NOT force the live count to 0 — the PLC
        zeroes the register and the mirror walks the dashboard down."""
        _pend = getattr(self, "_pending_shift_archive", None)
        if not _pend:
            return
        conn = None
        try:
            conn = _db_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO mes_shift_count_archive
                    (machine_id, line_id, machine_name, record_date,
                     shift_name, ok_count, ng_count, reset_bit)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (machine_id, record_date, shift_name)
                DO UPDATE SET
                    ok_count    = GREATEST(mes_shift_count_archive.ok_count,
                                           EXCLUDED.ok_count),
                    ng_count    = GREATEST(mes_shift_count_archive.ng_count,
                                           EXCLUDED.ng_count),
                    reset_bit   = EXCLUDED.reset_bit,
                    ts_archived = NOW()
            """, (
                _pend["machine_id"], _pend["line_id"], _pend["machine_name"],
                _pend["record_date"], _pend["shift_name"],
                _pend["ok_count"], _pend["ng_count"], _pend["reset_bit"],
            ))
            conn.commit()
            cur.close()
            print(f"[SHIFT-ARCHIVE] saved machine={_pend['machine_id']} "
                  f"{_pend['record_date']} {_pend['shift_name']} "
                  f"OK={_pend['ok_count']} NG={_pend['ng_count']} "
                  f"-> mes_shift_count_archive (rollover complete)",
                  flush=True)
            # Archive committed → rollover done.  Re-baseline the peak to the
            # live count so the next shift starts its own high-water mark.
            self._pending_shift_archive = None
            self._ok_shift_peak = int(self.ok_shift or 0)
            self._ng_shift_peak = int(self.ng_shift or 0)
        except Exception as exc:
            try:
                if conn: conn.rollback()
            except Exception:
                pass
            # Keep the snapshot — retry next poll (interlock).  Throttle log.
            if time.monotonic() - getattr(self, "_shift_archive_err_ts", 0) > 10:
                print(f"[SHIFT-ARCHIVE] INSERT failed — will retry, count "
                      f"NOT reset, data safe: {exc}", flush=True)
                self._shift_archive_err_ts = time.monotonic()
        finally:
            try:
                if conn: conn.close()
            except Exception:
                pass

    # ----------------------------------------------------------
    # AUTO L110 SHIFT-RESET PULSE (2026-05-30)
    # ----------------------------------------------------------

    def _check_l110_test_flag(self) -> None:
        """Manual test-trigger for the L110 shift-reset pulse.

        Operator drops a file at self._L110_FLAG_PATH (e.g. the operator
        creates `L110_PULSE_NOW.flag` next to collector_engine.py).  On the
        next check we bump self._shift_reset_epoch by one — exactly like a
        real shift end — so every register machine fires ONE L110 pulse, and
        then we DELETE the flag so it is a clean one-shot.  We key off the
        file's mtime so that even if the delete fails (locked / permissions)
        the SAME file never re-fires; only a freshly re-created file (new
        mtime) triggers again.  Throttled to ~once per 2 s so the file stat
        never slows the pulse-poll loop.  Completely inert until the file
        exists -> zero effect on normal running."""
        _now = time.monotonic()
        if _now - self._last_l110_flag_chk < 2.0:
            return
        self._last_l110_flag_chk = _now
        try:
            _flag = self._L110_FLAG_PATH
            _mt = _os.path.getmtime(_flag) if _os.path.exists(_flag) else None
        except Exception:
            _mt = None
        if _mt is None or _mt == self._l110_flag_mtime:
            return
        # New flag file seen -> consume it once.
        self._l110_flag_mtime = _mt
        self._shift_reset_epoch += 1
        print(f"[L110-TEST] manual flag seen -> shift-reset epoch "
              f"{self._shift_reset_epoch}; one L110 pulse on every register "
              f"machine (semi-auto/bit machines untouched).", flush=True)
        try:
            _os.remove(self._L110_FLAG_PATH)
        except Exception as _e:
            print(f"[L110-TEST] could not delete flag ({_e}); it won't "
                  f"re-fire (mtime keyed), delete it by hand when convenient.",
                  flush=True)

    # 2026-09-20 — MES-QUEUED BIT COMMANDS (PY bypass reject → bit ON, PY OK
    # → bit OFF).  MES cannot write to this PLC itself: it accepts a single
    # session and THIS collector holds it, so the API queues a row in
    # mes_plc_bit_commands and the line's own collector applies it here, on
    # the same connection it already owns.  No-op for every line that has no
    # command queued, which is all of them until a bypass is rejected.
    _BIT_CMD_EVERY_S = 5.0

    def _apply_bit_commands(self) -> None:
        _now = time.monotonic()
        if _now - getattr(self, "_bit_cmd_last", 0.0) < self._BIT_CMD_EVERY_S:
            return
        self._bit_cmd_last = _now
        if not getattr(self, "_plc_ok", False) or self._plc is None:
            return
        _line_id = self.cfg.get("line_id") if getattr(self, "cfg", None) else None
        if _line_id is None:
            return
        try:
            self._ensure_db_connection()
            cur = self._db.cursor()
            cur.execute("SELECT to_regclass('mes_plc_bit_commands')")
            if not (cur.fetchone() or [None])[0]:
                cur.close()
                self._BIT_CMD_EVERY_S = 300.0    # feature not deployed yet
                return
            cur.execute("""SELECT id, bit_addr, value FROM mes_plc_bit_commands
                            WHERE line_id = %s AND applied_at IS NULL AND attempts < 5
                            ORDER BY id LIMIT 10""", (_line_id,))
            rows = cur.fetchall()
            cur.close()
        except Exception as exc:
            print(f"[BIT-CMD] queue read failed: {str(exc)[:90]}", flush=True)
            return
        for cid, bit, val in rows:
            _bit = str(bit or "").strip().upper()
            ok, err = False, None
            if not _bit:
                err = "empty bit address"
            else:
                try:
                    self._plc.batchwrite_bitunits(headdevice=_bit, values=[int(val)])
                    ok = True
                    print(f"[BIT-CMD] {_bit} <- {int(val)} (command {cid})", flush=True)
                except Exception as _be:
                    err = str(_be)[:180]
                    print(f"[BIT-CMD] {_bit} <- {int(val)} FAILED: {err}", flush=True)
            try:
                cur = self._db.cursor()
                if ok:
                    cur.execute("""UPDATE mes_plc_bit_commands
                                      SET applied_at = now(), applied_ok = TRUE,
                                          attempts = attempts + 1, error = NULL
                                    WHERE id = %s""", (cid,))
                else:
                    # give up after 5 tries so a bad address cannot retry forever
                    cur.execute("""UPDATE mes_plc_bit_commands
                                      SET attempts = attempts + 1, error = %s,
                                          applied_at = CASE WHEN attempts + 1 >= 5
                                                            THEN now() ELSE NULL END,
                                          applied_ok = CASE WHEN attempts + 1 >= 5
                                                            THEN FALSE ELSE NULL END
                                    WHERE id = %s""", (err, cid))
                self._db.commit()
                cur.close()
            except Exception as _ue:
                print(f"[BIT-CMD] status update failed for {cid}: {str(_ue)[:90]}", flush=True)

    def _maybe_pulse_fi_shift_reset(self) -> None:
        """Non-blocking L110 shift-reset pulse for the MAIN (Final Inspection)
        register machine, written on its OWN self._plc connection so there is
        NO external client / socket contention.

        State machine, one step per poll (never sleeps / blocks):
          • epoch advanced past what we've done AND not currently pulsing
            -> write L110=1, remember the time, mark this epoch done.
          • currently pulsing AND >= _L110_PULSE_SEC elapsed
            -> write L110=0, pulse complete.
        The existing _maybe_archive_and_reset_shift() reads this same bit and,
        on its 0->1 rising edge, archives the closing OK/NG BEFORE the PLC
        zeroes the register (interlock) — so writing the bit here both resets
        the count AND drives the archive, with no extra wiring.

        No-op unless count_mode=register AND shift_reset_bit configured, so
        bit-mode and semi-auto machines are never touched => zero regression.
        Fires EXACTLY once per epoch; a write failure is retried on the next
        poll (ON not marked done; OFF keeps the pulse-on state)."""
        _bit = self.cfg.get("shift_reset_bit")
        if not _bit:
            return
        if (self.cfg.get("count_mode") or "bit").lower() != "register":
            return
        if not getattr(self, "_plc_ok", False) or self._plc is None:
            return
        _now = time.monotonic()
        # Currently pulsing -> turn OFF once the ON window has elapsed.
        if self._fi_reset_pulse_on_since is not None:
            if _now - self._fi_reset_pulse_on_since >= self._L110_PULSE_SEC:
                try:
                    self._plc.batchwrite_bitunits(headdevice=_bit, values=[0])
                except Exception as e:
                    print(f"[L110-PULSE] FI {_bit} OFF write failed: {e}; "
                          f"retry next poll", flush=True)
                    return   # keep pulse-on state, retry OFF next poll
                self._fi_reset_pulse_on_since = None
                print(f"[L110-PULSE] FI {_bit} -> 0 (pulse end, epoch "
                      f"{self._fi_reset_epoch_done} complete)", flush=True)
            return
        # Not pulsing -> start a pulse if the epoch advanced beyond done.
        if self._shift_reset_epoch > self._fi_reset_epoch_done:
            try:
                self._plc.batchwrite_bitunits(headdevice=_bit, values=[1])
            except Exception as e:
                # 2026-06-03 — RECONNECT + throttle + give up.  A dropped /
                # half-open FI socket (WinError 10054) made this fail EVERY poll
                # and spam the log, so the shift never reset and the next shift
                # inherited the old count.  On a connection error force a
                # reconnect (main loop reopens a fresh socket so the next
                # attempt can actually succeed), log only the 1st + every 60th,
                # and give up after N so it can never spam forever.
                _fc = int(getattr(self, "_fi_l110_fail_count", 0)) + 1
                self._fi_l110_fail_count = _fc
                _es = str(e)
                if ("10054" in _es or "10053" in _es or "forcibly" in _es
                        or "closed" in _es or "WinError" in _es):
                    try: self._plc.close()
                    except Exception: pass
                    self._plc_ok = False    # main loop reopens -> fresh socket
                if _fc == 1 or _fc % 60 == 0:
                    print(f"[L110-PULSE] FI {_bit} ON write failed x{_fc}: {e}; "
                          f"reconnecting + retry", flush=True)
                if _fc >= 30:
                    self._fi_reset_epoch_done = self._shift_reset_epoch
                    print(f"[L110-PULSE] FI {_bit} ON failed {_fc}x — GIVING UP "
                          f"this epoch (FI connection/bit issue; check {_bit} "
                          f"is writable on this PLC).", flush=True)
                return   # do NOT mark done -> retry ON next poll
            self._fi_reset_pulse_on_since = _now
            self._fi_reset_epoch_done     = self._shift_reset_epoch
            self._fi_l110_fail_count      = 0
            print(f"[L110-PULSE] FI {_bit} -> 1 (shift-reset epoch "
                  f"{self._fi_reset_epoch_done}); hold "
                  f"{self._L110_PULSE_SEC:.0f}s then 0. Archive logic catches "
                  f"the rising edge.", flush=True)

    # ----------------------------------------------------------
    # MAIN DB WRITE
    # ----------------------------------------------------------

    def _ensure_ct_log_table(self) -> bool:
        """Create the per-line ct_log table once, if it doesn't exist yet."""
        if self._ct_log_table_ready:
            return True
        tbl = self.cfg["table_name"] + "_ct_log"
        try:
            cur = self._db.cursor()
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {tbl} (
                    id          SERIAL PRIMARY KEY,
                    ts          TIMESTAMP NOT NULL,
                    record_date DATE      NOT NULL,
                    shift_name  VARCHAR(20),
                    ct_value    NUMERIC(7,2) NOT NULL,
                    cycle_seq   INTEGER,
                    part_code   VARCHAR(64),
                    is_ng       BOOLEAN DEFAULT FALSE
                );
                CREATE INDEX IF NOT EXISTS {tbl}_date_shift
                    ON {tbl}(record_date, shift_name);
            """)
            # Backfill columns for existing installations (idempotent).
            cur.execute(
                f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS part_code VARCHAR(64)"
            )
            cur.execute(
                f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS is_ng BOOLEAN DEFAULT FALSE"
            )
            self._db.commit()
            cur.close()
            self._ct_log_table_ready = True
            return True
        except Exception as e:
            print(f"[CT_LOG] Table create error: {e}")
            self._safe_rollback()
            return False

    # ── Part code from PLC word registers ────────────────────────
    # Same Node-RED / New-folder-2 convention: D5004, 13 word registers,
    # each register holds 2 ASCII chars in byte-reversed order (low byte first).
    _PART_CODE_ADDR = "D5004"
    _PART_CODE_LEN  = 13

    def _read_part_code(self) -> str:
        if not self._plc_ok or self._plc is None:
            return ""
        try:
            regs = self._plc.batchread_wordunits(
                headdevice=self._PART_CODE_ADDR,
                readsize=self._PART_CODE_LEN,
            )
        except Exception as exc:
            print(f"[PLC] Part code read error: {exc}")
            return ""
        chars = []
        for reg in regs:
            high_byte = reg & 0xFF
            low_byte  = (reg >> 8) & 0xFF
            if high_byte > 0:
                chars.append(chr(high_byte))
            if low_byte > 0:
                chars.append(chr(low_byte))
        return "".join(chars).strip().strip("\x00")

    def _flush_ct_log(self):
        """Write buffered cycle time entries to the ct_log table."""
        if not self._ct_pending_log:
            return
        if not self._ensure_ct_log_table():
            return
        tbl = self.cfg["table_name"] + "_ct_log"
        rows = list(self._ct_pending_log)
        self._ct_pending_log.clear()
        try:
            cur = self._db.cursor()
            cur.executemany(
                f"INSERT INTO {tbl}(ts, record_date, shift_name, ct_value, cycle_seq, part_code, is_ng) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
            self._db.commit()
            cur.close()
        except Exception as e:
            print(f"[CT_LOG] Flush error: {e}")
            self._safe_rollback()

    def _reg_backfill_after_resync(self) -> None:
        """One-shot graph-point backfill after a register resync.

        2026-05-31 — On a collector restart/reconnect the OK register is
        SNAPPED to the PLC's live count (the `_last is None` branch in
        _read_plc) without writing any cycle rows.  Every part the PLC
        counted while this collector was down therefore has no graph point
        and the dashboard count runs ahead of the chart + per-part list.
        Operator ask: "shift cross kr gyi or dubara time ussi shift ka aagya
        to usme add ho jaye data or graph point jarur bne".

        Writes one OK cycle row per missed part — to BOTH the L6 audit
        (counter_val pinned to the register value) and the chart ct_log
        (cycle_seq pinned the same) — so count and graph re-sync.  Videos
        are online-rendered from the rolling camera by timestamp, so rows
        carry an estimated ts inside the real downtime window (best effort
        to still land on footage) and NO part_code (genuinely unknown).

        Hard safety rails — never fabricate a phantom:
          • register mode only;
          • current shift known and NOT a GAP/idle window;
          • only the TOP gap [DB-max+1 .. seed] is filled, so every written
            counter_val is provably absent (cannot duplicate an existing row
            whatever constraints exist);
          • positive gap only; bounded by reg_backfill_cap (SKIP, never
            clamp, if exceeded — a huge gap means a suspect first read or a
            multi-hour outage, both better left for manual review);
          • the seed was already confirmed stable for one poll in _read_plc
            (a garbage-high first read craters next poll -> aborted there).
        """
        target = getattr(self, "_do_reg_backfill_to", None)
        # One-shot: clear up-front so a failure can never loop-retry.
        self._do_reg_backfill_to = None
        if target is None:
            return
        try:
            if (self.cfg.get("count_mode") or "bit").lower() != "register":
                return
            shift = self._cur_shift
            if not shift or str(shift).startswith("GAP"):
                return
            rec_dt = getattr(self, "_cur_shift_record_date", None)
            if rec_dt is None:
                return
            target = int(target)
            bit_addr = self._count_src_label("ok")
            machine_id = int(self.cfg.get("main_plc_id")
                             or self.cfg["line_id"])
            # Current high-water mark already on disk for THIS shift/date.
            # Short-lived connection (same pattern as _write_machine_log) so
            # the main poll connection's transaction state is never touched.
            # 2026-09-21 — the mark must come from THIS line's own rows.
            # mes_l6_final_inspection belongs to YNC-SS's main PLC (id 2) and
            # has no line filter, so every other register line compared its
            # seed against YNC-SS's count: a YSD-SA-4WAY restart re-wrote
            # cycles 723..888 that already existed (166 duplicate ct_log rows)
            # and added them to the hourly slot a second time.  The FI owner
            # keeps its table; every other line reads its own ct_log, plus any
            # rows still waiting in the flush buffer.
            _hw_meta = self._L6_TABLE_MAP.get(machine_id)
            conn = _db_conn()
            try:
                cur = conn.cursor()
                if _hw_meta and _hw_meta[1]:
                    cur.execute(
                        f"SELECT COALESCE(MAX(counter_val), 0), MAX(ts) "
                        f"FROM {_hw_meta[0]} "
                        "WHERE bit_type='OK' AND shift_name=%s "
                        "AND record_date=%s",
                        (shift, rec_dt),
                    )
                else:
                    cur.execute(
                        f"SELECT COALESCE(MAX(cycle_seq), 0), MAX(ts) "
                        f"FROM {self.cfg['table_name']}_ct_log "
                        "WHERE shift_name=%s AND record_date=%s "
                        "AND NOT COALESCE(is_ng, FALSE)",
                        (shift, rec_dt),
                    )
                _row = cur.fetchone() or (0, None)
                db_max = int(_row[0] or 0)
                last_ts = _row[1]
                cur.close()
            finally:
                conn.close()
            for _p in list(self._ct_pending_log):
                if (_p[1] == rec_dt and _p[2] == shift and not _p[6]
                        and _p[4] is not None and int(_p[4]) > db_max):
                    db_max, last_ts = int(_p[4]), _p[0]
            gap = target - db_max
            if gap <= 0:
                return  # DB already at/above the register — nothing missed.
            cap = int(self.cfg.get("reg_backfill_cap") or 500)
            if gap > cap:
                print(f"[REG-BACKFILL] gap {db_max}->{target} = {gap} "
                      f"exceeds cap {cap} (shift={shift} {rec_dt}); "
                      f"SKIPPED - suspect first read or long outage, left "
                      f"for manual review.  Dashboard count unaffected.",
                      flush=True)
                return
            # Reconstruct the downtime window so the synthetic rows land in
            # real time and per-part ct = elapsed/parts ~= the true average
            # cycle the line was actually running while we were down.  Clamp
            # ct into a sane band so a long-idle window can't paint an absurd
            # CT spike on the graph.
            now_dt = datetime.now()
            t0 = last_ts
            span_s = 0.0
            if t0 is not None:
                try:
                    span_s = max((now_dt - t0).total_seconds(), 0.0)
                except Exception:
                    span_s = 0.0
            ct_per = (span_s / gap) if gap > 0 else 0.0
            _ideal = float(self.cfg.get("ideal_ct") or 0.0)
            _ct_cap = max(float(self.cfg.get("max_ct") or 0.0),
                          3.0 * _ideal, 60.0)
            if ct_per <= 0.0 or ct_per > _ct_cap:
                # No usable window (or absurd) -> ideal CT for a clean,
                # non-spiky graph point.
                ct_per = _ideal if _ideal > 0 else 0.0
            if t0 is None:
                t0 = now_dt - timedelta(seconds=ct_per * gap)
            n = 0
            for _i in range(gap):
                seq = db_max + 1 + _i
                row_ts = t0 + timedelta(seconds=ct_per * (_i + 1))
                if row_ts > now_dt:
                    row_ts = now_dt
                # L6 audit row — counter_val pinned; part_code None means
                # _write_machine_log leaves video_path NULL (video still
                # resolves by ts online if the camera was up).
                self._write_machine_log(
                    machine_id   = machine_id,
                    bit_type     = "OK",
                    bit_address  = bit_addr,
                    ts           = row_ts,
                    ct_seconds   = ct_per,
                    part_code    = None,
                    counter_val_override = seq,
                )
                # Chart ct_log row — cycle_seq pinned; ct_value MUST be
                # numeric (the chart endpoint does float(ct_value)).
                self._raw_cycle_seq = max(int(self._raw_cycle_seq or 0), seq)
                self._ct_pending_log.append((
                    row_ts, rec_dt, shift,
                    round(float(ct_per or 0.0), 2),
                    seq, None, False,   # part_code None, is_ng False
                ))
                n += 1
            print(f"[REG-BACKFILL] filled {n} graph-point rows "
                  f"#{db_max + 1}..{target} shift={shift} {rec_dt} "
                  f"(ct~{ct_per:.1f}s/part over {span_s:.0f}s downtime; "
                  f"video by-ts online, part_code unknown).", flush=True)
            # 2026-07-03 — also mirror the backfilled parts into the hourly
            # bucket.  Without this, resync-backfilled parts land in the count +
            # per-part rows but NOT the hour_*_ok columns, so the hourly grid
            # undercounts the register (ync 07-02 A: buckets 1573 vs reg 1842).
            # _update_hourly self-guards gap/slot and writes the slot to DB, and
            # can never inflate past the register (n = provably-missed parts).
            if n > 0:
                self._update_hourly(n, 0)
        except Exception as _e:
            print(f"[REG-BACKFILL] aborted (no rows written): {_e}",
                  flush=True)

    # ─────────────────────────────────────────────────────────────────
    # MACHINE PROCESS SAMPLING
    # ─────────────────────────────────────────────────────────────────
    # Drives the Process Graphs page (/process-graphs in frontend).
    # Each process is one row in mes_machine_processes:
    #     (process_no, process_name, target_value, actual_register, register_type)
    # We poll the configured PLC register on a schedule and INSERT a
    # timestamped row into mes_machine_process_log so the frontend can
    # render a bar chart of actual vs target over time.
    #
    # Only main-PLC processes are sampled here (parent_plc_id IS NULL)
    # because that's the PLC this collector instance has open.
    # Sub-machine processes need their own sub-poller — TODO when the
    # operator configures any.
    def _reload_machine_processes(self):
        """Pull the current process config from DB.  Idempotent.  Runs
        every 30 s in the main loop so admin-panel edits go live
        without a collector restart."""
        if not self._db_ok or not self._db:
            return
        try:
            cur = self._db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Join with mes_plc_configs (NOT legacy mes_machines master).
            # Filter to MAIN PLC machines only — sub-machine processes
            # would need their own connection and aren't sampled here.
            cur.execute("""
                SELECT p.id, p.machine_id, p.process_no,
                       p.process_name, p.target_value,
                       p.actual_register, p.register_type
                  FROM mes_machine_processes p
                  JOIN mes_plc_configs m ON m.id = p.machine_id
                 WHERE m.line_id = %s
                   AND m.parent_plc_id IS NULL
                   AND p.is_active = TRUE
                 ORDER BY p.process_no
            """, (self.cfg["line_id"],))
            new_list = [dict(r) for r in cur.fetchall()]
            cur.close()
            # End the read's transaction. Without this the connection sits
            # 'idle in transaction' (this runs every 30 s) holding an
            # ACCESS SHARE lock on mes_plc_configs. A startup ALTER TABLE
            # mes_plc_configs then blocks on ACCESS EXCLUSIVE, and every
            # collector's mes_plc_configs read queues behind it → the whole
            # DB pileup that exhausts the MES-API pool (all endpoints 500).
            self._db.commit()

            # Only log on actual change so we don't spam the console
            if len(new_list) != len(self._machine_processes):
                names = [p["process_name"] for p in new_list]
                print(f"[PROCESS] Reloaded {len(new_list)} configured "
                      f"process{'es' if len(new_list)!=1 else ''}: {names}")
            self._machine_processes = new_list
        except Exception as e:
            print(f"[PROCESS] Reload failed: {e}")
            self._safe_rollback()

    def _poll_machine_process_pulses(self):
        """Called from the main loop every poll iteration (~30 ms).

        For BIT-type process registers (e.g. L108 OK pulse):
          • Rising edge (0→1) → record start_ts for that process.
          • Falling edge (1→0) → compute ON duration and buffer a row
            for mes_machine_process_pulses; this drives the per-pulse
            spike graph (width = ON time) on the Process Graphs page.
          • Also bumps the per-minute rising-edge count for backward
            compat (still written to mes_machine_process_log).

        Word-type processes are NOT touched here — they're sampled once
        per 60 s by `_sample_machine_processes()`."""
        if not self._machine_processes:
            return
        if not (self._plc_ok and self._plc):
            return

        if not hasattr(self, "_proc_pulse_state"):
            self._proc_pulse_state    = {}    # {process_id: last_bit_value}
            self._proc_pulse_count    = {}    # {process_id: rising-edge count}
            self._proc_pulse_start_ts = {}    # {process_id: datetime when bit went HIGH}
            self._proc_pulse_log_buf  = []    # [(process_id, started_at, duration_ms), ...]

        for p in self._machine_processes:
            if (p["register_type"] or "").lower() != "bit":
                continue
            reg = (p["actual_register"] or "").strip()
            if not reg:
                continue
            try:
                v = self._plc.batchread_bitunits(headdevice=reg, readsize=1)
                if not v:
                    continue
                cur_val  = int(v[0])
                prev_val = self._proc_pulse_state.get(p["id"], 0)
                self._proc_pulse_state[p["id"]] = cur_val
                # Rising edge = one cycle / pulse / part
                if cur_val == 1 and prev_val == 0:
                    self._proc_pulse_count[p["id"]] = \
                        self._proc_pulse_count.get(p["id"], 0) + 1
                    self._proc_pulse_start_ts[p["id"]] = datetime.now()
                # Falling edge = pulse ended → log start_ts + duration
                elif cur_val == 0 and prev_val == 1:
                    start = self._proc_pulse_start_ts.pop(p["id"], None)
                    if start is not None:
                        dur_ms = int((datetime.now() - start).total_seconds() * 1000)
                        if dur_ms < 1:
                            dur_ms = 1          # PLC scan boundary — minimum 1 ms
                        self._proc_pulse_log_buf.append((p["id"], start, dur_ms))
            except Exception:
                # Silent — bad register already warned by _sample_*
                pass

    def _sample_machine_processes(self):
        """Write one log row per configured process to
        mes_machine_process_log.  Runs every 60 s.

        BIT-type   : value = number of rising edges counted in the last
                     60 s window (drained from _proc_pulse_count).
                     This is what the operator graphs as "cycles per
                     window".
        WORD-type  : value = current PLC word value (cumulative count
                     register, sensor reading, etc.).  Read fresh here."""
        if not self._machine_processes:
            return
        if not (self._plc_ok and self._plc) or not (self._db_ok and self._db):
            return

        now_ts = datetime.now()
        rows_to_insert = []

        # Ensure pulse-count dict exists (it normally does after the
        # first fast-poll tick, but be defensive).
        if not hasattr(self, "_proc_pulse_count"):
            self._proc_pulse_count = {}
            self._proc_pulse_state = {}

        for p in self._machine_processes:
            reg  = (p["actual_register"] or "").strip()
            rtyp = (p["register_type"] or "word").lower()
            if not reg:
                continue

            if rtyp == "bit":
                # Drain the accumulated pulse count for this 60s window.
                count = self._proc_pulse_count.get(p["id"], 0)
                rows_to_insert.append((p["id"], count, now_ts))
                self._proc_pulse_count[p["id"]] = 0    # reset for next window
                continue

            # Word — current value
            try:
                v = self._plc.batchread_wordunits(headdevice=reg, readsize=1)
                if v:
                    val = int(v[0])
                    rows_to_insert.append((p["id"], val, now_ts))
            except Exception as e:
                seen = getattr(self, "_proc_read_warned", None)
                if seen is None:
                    seen = set(); self._proc_read_warned = seen
                key = (p["id"], reg)
                if key not in seen:
                    seen.add(key)
                    print(f"[PROCESS] Read failed for {p['process_name']!r} "
                          f"@ {reg} ({rtyp}): {e} — skipping until fixed")

        # Flush buffered per-pulse rows (BIT) into the pulses table so
        # the Process Graphs page can render one spike per ON event.
        pulse_rows = []
        if hasattr(self, "_proc_pulse_log_buf") and self._proc_pulse_log_buf:
            pulse_rows = self._proc_pulse_log_buf
            self._proc_pulse_log_buf = []

        if not rows_to_insert and not pulse_rows:
            return

        try:
            if rows_to_insert:
                _buffered_exec(self._db,
                    "INSERT INTO mes_machine_process_log "
                    "(process_id, actual_value, sampled_at) VALUES (%s, %s, %s)",
                    rows_to_insert, executemany=True)
            if pulse_rows:
                _buffered_exec(self._db,
                    "INSERT INTO mes_machine_process_pulses "
                    "(process_id, started_at, duration_ms) VALUES (%s, %s, %s)",
                    pulse_rows, executemany=True)
        except Exception as e:
            print(f"[PROCESS] Log insert failed: {e}")
            self._safe_rollback()

    def _replay_queue(self):
        """Drain the durable JSON write-buffer into the DB EXACTLY-ONCE.
        Runs on the MAIN thread only (uses self._db, a known-live conn) so it
        never races a live capture.  Each record applies inside ONE txn that
        first claims its rec_id in the dedup ledger (ON CONFLICT DO NOTHING) and
        writes the data ONLY if the claim was new — so a crash mid-replay or a
        re-read of an un-truncated tail can never double-write.  Stops on a DB
        blink (keeps the rest); dead-letters a poison row so it can't wedge."""
        if _QUEUE is None or not _QUEUE.has_pending():
            return
        try:
            _ensure_replay_ledger(self._db)
        except Exception as e:
            print(f"[WBUF] ledger ensure failed, replay deferred: {e}")
            self._safe_rollback()
            return
        recs = _QUEUE.read_all()
        if not recs:
            _QUEUE._pending = False
            return
        done = 0
        CAP = 1000   # bound per call so a huge backlog can't stall the poll loop
        LEDGER = ("INSERT INTO mes_collector_replay_log (rec_id) VALUES (%s) "
                  "ON CONFLICT (rec_id) DO NOTHING")
        for r in recs[:CAP]:
            try:
                cur = self._db.cursor()
                cur.execute(LEDGER, (r["id"],))
                if cur.rowcount == 1:                      # not yet applied
                    P = _wbuf_dec_params(r)
                    if r.get("many"):
                        cur.executemany(r["sql"], P)
                    else:
                        cur.execute(r["sql"], P)
                self._db.commit()
                cur.close()
                done += 1
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self._safe_rollback()
                break                                      # DB blinked — keep the rest
            except Exception as e:                         # poison data row
                self._safe_rollback()
                _QUEUE.deadletter(r)
                done += 1
                print(f"[WBUF] dead-letter {r.get('id')}: {e}", flush=True)
        if done:
            _QUEUE.commit_progress(done)
            print(f"[WBUF] replayed {done}/{len(recs)} buffered writes to DB",
                  flush=True)

    def _write_dashboard(self):
        if not self._ensure_db_connection() or not self._shift_id:
            return

        self._loss["speed"] = self.ct.speed_loss

        # GAP period — write IDLE only
        if self._cur_shift and self._cur_shift.startswith("GAP"):
            try:
                cur = self._db.cursor()
                cur.execute(f"""
                    UPDATE {self.cfg['table_name']} SET
                        operating_status     = 'IDLE',
                        shift_plan_completed = 0,
                        shift_plan_remaining = 0,
                        updated_at           = NOW(),
                        timestamp            = NOW()
                    WHERE id = %s
                """, (self._shift_id,))
                self._db.commit()
                cur.close()
            except Exception as e:
                print(f"[DB] GAP write error: {e}")
                self._db_ok = False
                self._safe_rollback()
            return

        _cur_scfg        = self.cfg["shifts"].get(self._cur_shift, {})
        _shift_plan      = 0 if (self._cur_shift or "").startswith("GAP") else _cur_scfg.get("total_plan", 0)
        working_seconds  = self._working_seconds()
        # int(round(...)) instead of plain int() so the last cycle of the
        # shift hits total_plan exactly.  Without this, a shift designed
        # for 1860 parts at 15 s ideal CT would freeze at 1859 because
        # working_seconds at 17:14:59 = 27899, and 27899 / 15 = 1859.93
        # floors to 1859.  round() turns that 1859.93 into 1860.  Mid-
        # shift behaviour is unaffected — round only diverges from floor
        # in the last half-second of any single cycle.
        planned          = min(_shift_plan,
                                int(round(working_seconds / self.cfg["ideal_ct"]))) \
                            if _shift_plan > 0 else 0
        self._plan_completed = planned

        oee        = self._oee()
        ct_d       = self.ct.ct_dict()
        total_loss = sum(self._loss.values())

        def fmt(s):
            s = int(s)
            return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

        try:
            cur = self._db.cursor()
            cur.execute("""
                UPDATE mes_lines SET collector_status='running', updated_at=NOW()
                WHERE id = %s
            """, (self.cfg["line_id"],))
            self._db.commit()
            cur.close()

            cur = self._db.cursor()
            # 2026-06-11 — ok_count normally never decreases (GREATEST guard vs
            # transient glitches).  But on a VALIDATED legitimate drop (startup
            # seed / reset re-snap / L110 reset) the read-path sets
            # _reg_force_db_exact, so this one write mirrors the live register
            # EXACTLY — letting the UI follow a real reset instead of freezing
            # at the previous shift's peak.  Cleared after commit (one-shot).
            # 2026-07-03 — only honor the exact (can-decrease) write when the
            # live value is POSITIVE.  A force-exact of 0 is a boundary/reset
            # down-mirror; on a fresh next-shift row GREATEST(0,0)=0 (identical
            # behaviour, UI still follows a real reset), but on a populated /
            # closing row GREATEST prevents it from being wiped to 0 (the
            # OT-reopen zeroing that made ync 07-02 A ok_count=0).
            _ok_set = ("ok_count=%s"
                       if (getattr(self, "_reg_force_db_exact", False)
                           and int(self.ok_shift or 0) > 0)
                       else "ok_count=GREATEST(ok_count, %s)")
            cur.execute(f"""
                UPDATE {self.cfg['table_name']} SET
                    {_ok_set}, ng_count=%s,
                    current_model_number=%s, current_model_name=%s,
                    cycle_time_actual=%s, operating_status=%s,
                    availability=%s, performance=%s,
                    quality_oee=%s, overall_oee=%s, oee_grade=%s,
                    shift_plan=%s, shift_plan_remaining=%s,
                    shift_plan_completed=GREATEST(shift_plan_completed, %s),
                    loss_breakdown_seconds=%s, loss_quality_seconds=%s,
                    loss_setup_seconds=%s,     loss_material_seconds=%s,
                    loss_others_seconds=%s,    loss_speed_seconds=%s,
                    loss_change_over_seconds=%s,
                    loss_breakdown=%s, loss_quality=%s, loss_setup=%s,
                    loss_material=%s,  loss_others=%s,  loss_speed=%s,
                    loss_change_over=%s, total_loss=%s,
                    ct1=%s,ct2=%s,ct3=%s,ct4=%s,ct5=%s,
                    ct6=%s,ct7=%s,ct8=%s,ct9=%s,ct10=%s,
                    ct11=%s,ct12=%s,ct13=%s,ct14=%s,ct15=%s,
                    ct16=%s,ct17=%s,ct18=%s,ct19=%s,ct20=%s,
                    ct_avg_20=%s, min_ct=%s, max_ct=%s, std_dev_ct=%s,
                    updated_at=NOW(), timestamp=NOW()
                WHERE id=%s
            """, (
                self.ok_shift, self.ng_shift,
                self._cur_model, self._cur_model_name,
                min(99.99, ct_d["ct_avg_20"]), self._cur_status_name,
                min(99.99, oee["avail"]),  min(99.99, oee["perf"]),
                min(99.99, oee["qual"]),   min(99.99, oee["overall"]),
                oee["grade"],
                _shift_plan, max(0, _shift_plan - planned), planned,
                int(self._loss["breakdown"]),   int(self._loss["quality"]),
                int(self._loss["setup"]),        int(self._loss["material"]),
                int(self._loss["others"]),       int(self._loss["speed"]),
                int(self._loss["change_over"]),
                fmt(self._loss["breakdown"]),    fmt(self._loss["quality"]),
                fmt(self._loss["setup"]),        fmt(self._loss["material"]),
                fmt(self._loss["others"]),       fmt(self._loss["speed"]),
                fmt(self._loss["change_over"]),  fmt(total_loss),
                ct_d["ct1"],  ct_d["ct2"],  ct_d["ct3"],  ct_d["ct4"],  ct_d["ct5"],
                ct_d["ct6"],  ct_d["ct7"],  ct_d["ct8"],  ct_d["ct9"],  ct_d["ct10"],
                ct_d["ct11"], ct_d["ct12"], ct_d["ct13"], ct_d["ct14"], ct_d["ct15"],
                ct_d["ct16"], ct_d["ct17"], ct_d["ct18"], ct_d["ct19"], ct_d["ct20"],
                ct_d["ct_avg_20"], ct_d["min_ct"], ct_d["max_ct"], ct_d["std_dev_ct"],
                self._shift_id,
            ))
            self._db.commit()
            cur.close()
            self._reg_force_db_exact = False   # one-shot exact-write consumed

            # Flush buffered CT log entries
            self._flush_ct_log()

            # Periodic orphan cleanup
            try:
                cur2 = self._db.cursor()
                cur2.execute(f"""
                    UPDATE {self.cfg['table_name']}
                    SET is_shift_completed = true, updated_at = NOW()
                    WHERE is_shift_completed = false
                      AND id != %s
                      AND (timestamp IS NULL OR timestamp < NOW() - INTERVAL '30 seconds')
                """, (self._shift_id,))
                self._db.commit()
                cur2.close()
            except Exception:
                self._safe_rollback()

        except Exception as e:
            print(f"[DB] Write error: {e}")
            traceback.print_exc()
            self._db_ok = False
            self._safe_rollback()

    def _safe_rollback(self):
        try: self._db.rollback()
        except: pass

    # ----------------------------------------------------------
    # SUB-MACHINE POLLER  +  DYNAMIC RELOAD
    # ----------------------------------------------------------

    def _spawn_sub_thread(self, sub: dict) -> None:
        """Start one poller thread for `sub` and register it.
        Idempotent — no-op if a thread for this sub_id already exists.
        Caller (`_reload_subs_loop`) calls `_stop_sub_thread` first when it
        wants a hot-restart with new config."""
        sid = sub["id"]
        if sid in self._sub_workers:
            return
        stop_event = threading.Event()
        t = threading.Thread(
            target=self._run_submachine_poller,
            args=(sub, stop_event),
            daemon=True,
            name=f"sub-{sid}",
        )
        self._sub_workers[sid] = {
            "stop":   stop_event,
            "thread": t,
            # Snapshot EVERY watched key (not just the first three) so the
            # reload watcher compares like-for-like; otherwise the SA fields
            # read as perpetually "changed" and the poller respawns every tick.
            "cfg_snapshot": {k: sub.get(k) for k in _SUB_WATCH_KEYS},
        }
        t.start()
        self._sub_threads.append(t)

    def _stop_sub_thread(self, sub_id: int) -> None:
        """Signal the poller for `sub_id` to exit and remove it from the
        registry.  The loop checks its stop_event every 100 ms so the
        thread dies quickly without needing a join."""
        w = self._sub_workers.pop(sub_id, None)
        if w:
            w["stop"].set()
            print(f"[SUB-RELOAD] stop signal → sub-{sub_id}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # PY / sensor check thread (own PLC connection)
    # ──────────────────────────────────────────────────────────────
    def _py_check_loop(self) -> None:
        """Background thread that runs all the PLC-heavy poka-yoke /
        sensor-health checks.

        2026-05-30 -- STRUCTURAL FIX.  Previously this thread opened
        its OWN Type4E session to the main PLC (.150 for FI), giving
        TWO concurrent sessions on the same Mitsubishi E-series CPU.
        Under load the PLC would cross-route responses ("socket
        bleed-through"), making FI's D101 reads come back as value=1
        for hundreds of polls in a row -- which the rate-clamp /
        shift-rollover paths then interpreted as either garbage drops
        OR false L110 rising edges, firing repeated [SHIFT-ROLLOVER]
        archives and stalling the FI count after 2-3 real increments.

        Fix: share self._plc (the main session) with the main poll
        loop.  Both threads access it via _PLCLockProxy, which acquires
        self._plc_lock before every method call so requests are
        serialised on ONE socket.  ONE session = no bleed-through.
        Cost: PY-CHECK reads briefly block the main read while in
        progress, but pymcprotocol reads are ~5-10 ms each so the
        impact on pulse polling is negligible compared to the count
        correctness this restores."""
        # Wait for main connection to be up first.
        for _ in range(30):
            if self._plc_ok and self._plc is not None:
                break
            if self._stop.wait(1.0):
                return
        if not (self._plc_ok and self._plc is not None):
            print(f"[PY-CHECK] main PLC connection never came up -- "
                  f"bypass detection disabled, pulse counting still "
                  f"active.", flush=True)
            return
        print(f"[PY-CHECK] sharing main PLC connection "
              f"({self.cfg['plc_ip']}:{self.cfg['plc_port']}) via "
              f"lock-proxy (no separate session = no bleed-through)",
              flush=True)

        # Run loop at 0.5 sec cadence (preserved from earlier).
        while not self._stop.wait(0.5):
            tick_exc = None
            try:
                if self._is_in_gap_period():
                    continue
                in_break, _ = self._is_break()
                self.poka.reload_rules_from_db(self.cfg["line_id"])
                self.poka.reload_py_configs(self.cfg["line_id"])
            except Exception as exc:
                tick_exc = exc

            # Each poka call gets its own try-except so a failure in
            # one path does NOT silently kill the others (preserved
            # from the 2026-05-20 critical fix).  PLC arg is now the
            # shared self._plc (lock-proxy) instead of a private socket.
            if not in_break:
                try:
                    self.poka.check_d_registers(self._plc, self._cur_shift or "")
                except Exception as exc:
                    tick_exc = tick_exc or exc
                    print(f"[PY-CHECK] check_d_registers error: {str(exc)[:80]}", flush=True)
                # Only after a cycle completed.  This used to run on every
                # 0.5 s tick, which meant a PLC read per PY register twice a
                # second on the SAME socket the count is polled through —
                # load that showed up as read timeouts and the frame-desync
                # behind the stretched cycle times.  Comparing once per part
                # is both what the operator asked for and far less traffic.
                if getattr(self, "_py_check_due", False):
                    self._py_check_due = False
                    try:
                        self.poka.check_py_bypass(
                            self._plc, self._cur_shift or "", self._cur_model)
                    except Exception as exc:
                        tick_exc = tick_exc or exc
                        print(f"[PY-CHECK] check_py_bypass error: {str(exc)[:80]}", flush=True)
            try:
                _gap = self._is_in_gap_period()
            except Exception:
                _gap = False
            self.poka.sensors_should_track = (
                bool(getattr(self, "is_running", False))
                and not in_break
                and not _gap
            )
            try:
                self.poka.track_sensors_health(self._plc)
            except Exception as exc:
                tick_exc = tick_exc or exc
                print(f"[PY-CHECK] track_sensors_health error: {str(exc)[:80]}", flush=True)

            # The MAIN poll loop now owns reconnects -- if the shared
            # socket drops, _connect_plc() in the main loop restores it
            # and the next tick here simply resumes using the new
            # session (the _PLCLockProxy wraps whatever self._plc is at
            # call time).  We log socket-style errors but no longer try
            # to reconnect from this thread (which would have raced the
            # main loop's reconnect anyway).
            if tick_exc is not None:
                msg = str(tick_exc)[:80]
                if ("timed out" in msg.lower()
                        or "connection" in msg.lower()
                        or "forcibly closed" in msg.lower()):
                    # Main loop will reconnect; just let next tick retry.
                    pass

        print(f"[PY-CHECK] thread stopped", flush=True)


    def _reload_subs_loop(self) -> None:
        """Watcher thread: every 30 s diffs `mes_plc_configs` against the
        in-memory worker registry.

          • new sub_id in DB           → spawn poller (no restart needed)
          • sub_id gone from DB        → signal stop, drop from registry
          • plc_ip / port / count_bit changed → hot-restart with fresh config

        Lets admins add / edit / remove sub-machines via AdminPanel and have
        them go live within 30 s, with no Phase 2 restart."""
        # First reload starts faster than 30 s so a freshly-added sub
        # doesn't have to wait the full window after collector launch.
        sleep_s = 10
        while True:
            try:
                current_subs   = load_submachines(self.cfg.get("main_plc_id")) or []
                current_by_id  = {s["id"]: s for s in current_subs}
                current_ids    = set(current_by_id)
                known_ids      = set(self._sub_workers)

                # 1. Newly-added sub-machines → spawn
                for sid in current_ids - known_ids:
                    sub = current_by_id[sid]
                    print(
                        f"[SUB-RELOAD] +new sub id={sid} "
                        f"{sub.get('plc_ip')}:{sub.get('plc_port')} "
                        f"bit={sub.get('count_bit')} ({sub.get('machine_name')})",
                        flush=True,
                    )
                    self._spawn_sub_thread(sub)

                # 2. Removed sub-machines → stop
                for sid in known_ids - current_ids:
                    print(f"[SUB-RELOAD] -sub id={sid} no longer in DB", flush=True)
                    self._stop_sub_thread(sid)

                # 3. Existing sub-machines with changed wiring → hot-restart
                for sid in current_ids & known_ids:
                    new = current_by_id[sid]
                    old = self._sub_workers[sid]["cfg_snapshot"]
                    # 2026-08-24 — watch the Semi-Auto capture fields too, so
                    # editing the fetch / verdict bits in the admin panel goes
                    # live without a manual restart.  The key set is shared with
                    # the cfg_snapshot builder (_SUB_WATCH_KEYS) so the two can
                    # never drift out of sync and cause a respawn loop.
                    _changed = [k for k in _SUB_WATCH_KEYS
                                if str(old.get(k) or "") != str(new.get(k) or "")]
                    if _changed:
                        print(
                            f"[SUB-RELOAD] ~sub id={sid} config changed "
                            + ", ".join(f"{k}={old.get(k)}→{new.get(k)}"
                                        for k in _changed)
                            + " — hot restart",
                            flush=True,
                        )
                        self._stop_sub_thread(sid)
                        self._spawn_sub_thread(new)
            except Exception as exc:
                print(f"[SUB-RELOAD] error: {exc}", flush=True)
            time.sleep(sleep_s)
            sleep_s = 30   # steady-state cadence after first tick

    def _run_submachine_poller(self, sub: dict, stop_event=None):
        """One thread per sub-machine. Polls its count bit on a dedicated
        MC4E connection and writes a row to mes_submachine_ct_log on every
        rising edge. Shares no mutable state with the main loop — only
        reads parent attributes (shift, model, part_code, status).

        `stop_event` is a per-sub threading.Event() that the reload loop
        flips when this sub is removed/changed in mes_plc_configs.  When
        it fires the loop exits cleanly within ~100 ms.  Falls back to the
        legacy engine-wide `self._sub_stop` if not given (backward compat)."""
        if stop_event is None:
            stop_event = self._sub_stop
        sub_id    = sub["id"]
        plc_ip    = sub["plc_ip"]
        plc_port  = int(sub["plc_port"] or 5002)
        # count_bit must be configured in admin — no hardcoded fallback.
        count_bit = (sub["count_bit"] or "").strip()
        # 2026-05-23 — Also read NG bit for mes_pulse_log audit.
        # If ng_bit isn't configured in mes_plc_configs, just skip NG
        # tracking for this sub (no error).
        ng_bit_addr = (sub.get("ng_bit") or "").strip()
        # 2026-05-30 — Sub-machine REGISTER-MIRROR mode (operator redesign,
        # "sab ko laga, semi-auto chhod ke").  When count_mode='register'
        # this sub mirrors its OK/NG DATA REGISTER into mes_submachine_ct_log
        # ROWS instead of edge-counting count_bit — because the sub-machine
        # dashboard counts rows per shift.  Each +1 in the register = one new
        # cycle row; a +N jump = N rows (each its own seq, CT split evenly)
        # so per-part history stays complete.  Bit-mode subs NEVER touch any
        # register code below → zero behaviour change for every existing sub.
        sub_reg_mode = (sub.get("count_mode") or "bit").lower() == "register"
        sub_ok_reg   = (sub.get("ok_data_register") or "").strip()
        sub_ng_reg   = (sub.get("ng_data_register") or "").strip()
        # A register sub may legitimately have NO count_bit (register IS the
        # count source).  Only bail when there's neither a usable bit nor a
        # register to mirror.
        if not count_bit and not (sub_reg_mode and sub_ok_reg):
            print(f"[SUB {sub_id}] SKIP — ok_bit_address not configured in "
                  f"mes_plc_configs (machine_name={sub.get('machine_name')})",
                  flush=True)
            return
        name      = sub["machine_name"] or f"sub_{sub_id}"
        line_id   = self.cfg["line_id"]
        tag       = f"[SUB {sub_id} {name}]"
        # Persist per-sub OK/NG edge state for mes_pulse_log writes.
        last_sub_ok_ts = None   # last OK rise timestamp (for CT delta)
        last_sub_ng_ts = None   # last NG rise timestamp (for NG inter-arrival)
        last_ng_bit    = 0      # NG edge detector state
        # 2026-05-27 — Unified any-pulse-to-any-pulse anchor (same
        # model as Final Inspection).  Advances on EITHER L108 (OK)
        # OR L109 (NG) rising edge; CT for any new pulse = now - anchor.
        # Cycle's bit_type just labels which pulse closed it.
        last_any_pulse_sub_dt = None
        # 2026-05-24 — Hydrate last timestamps from DB so the FIRST row
        # after collector restart still has a meaningful ct_seconds
        # (delta from the previous shift's last edge, not NULL).
        try:
            _hc = _db_conn()
            _hcur = _hc.cursor()
            _l6tbl = (self._L6_TABLE_MAP.get(sub_id, ("","",))[0]
                      or "mes_l6_upper_rail")
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE bit_type='OK' AND record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_sub_ok_ts = _r[0] if isinstance(_r[0], datetime) else None
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE bit_type='NG' AND record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_sub_ng_ts = _r[0] if isinstance(_r[0], datetime) else None
            # 2026-05-27 — Hydrate the unified anchor too: most recent
            # pulse of ANY bit_type on this machine today.
            _hcur.execute(
                f"SELECT MAX(ts) FROM {_l6tbl} "
                f"WHERE record_date=CURRENT_DATE"
            )
            _r = _hcur.fetchone()
            if _r and _r[0]:
                last_any_pulse_sub_dt = (_r[0]
                    if isinstance(_r[0], datetime) else None)
            _hcur.close(); _hc.close()
            if last_sub_ok_ts or last_sub_ng_ts:
                print(f"[SUB {sub_id}] hydrated: last_ok={last_sub_ok_ts} "
                      f"last_ng={last_sub_ng_ts} "
                      f"last_any={last_any_pulse_sub_dt}", flush=True)
        except Exception as _e:
            print(f"[SUB {sub_id}] hydrate failed: {_e}", flush=True)

        # 2026-05-23 — CHATTER GUARD for sub-machine count_bit.
        # Bug found: Semi-Auto (sub_plc_id=12, M5700) produced 35 phantom
        # cycles in one shift with CT=0.4s.  The PLC ladder pulses M5700
        # ~400ms after the real cycle completion (likely a "data ready"
        # ack from the SA controller, not a new cycle).  Previous gate
        # of `ct >= 0.3` let these through, inflating Semi-Auto count
        # from 521 (real) to 557 (phantom +36).
        # New rule: reject any cycle shorter than max(2.0s, ideal_ct*0.2).
        # • ideal_ct=15 → 3.0s floor       (kills 0.4s chatter)
        # • ideal_ct=30 → 6.0s floor       (Ball Guide safe)
        # • Anything < this is impossible physical CT and almost always
        #   electrical chatter / double-pulse from the ladder.
        try:
            _sub_ideal = float(sub.get("ideal_ct") or 15.0)
        except Exception:
            _sub_ideal = 15.0
        # 2026-06-05 — RESTORED the 2026-05-23 chatter floor.  The 2026-05-28
        # "pure pass-through" (_sub_min_ct=0.0) re-introduced the Semi-Auto 0.4s
        # phantom: M5700's ~400ms data-ready ACK pulse was logged as a cycle.
        # Reject sub-floor cycles: max(2.0s, ideal*0.2) — ideal 15 -> 3.0s,
        # ideal 30 -> 6.0s.  Nothing real is below this (impossible physical
        # CT), so ONLY the ACK / double-pulse chatter drops; real cycles stay.
        _sub_min_ct = max(2.0, _sub_ideal * 0.2)
        print(f"{tag} chatter floor RESTORED = {_sub_min_ct:.1f}s "
              f"(ideal={_sub_ideal:.1f}s) -> drops sub-floor ACK/double-pulse",
              flush=True)

        # ── Semi-Auto data capture config (optional, separate trigger) ──
        sa_enabled   = bool(sub.get("sa_enabled"))
        sa_fetch_bit = (sub.get("sa_fetch_bit") or "").strip() if sa_enabled else ""
        sa_part_addr = (sub.get("sa_part_code_addr") or "").strip() if sa_enabled else ""
        sa_part_len  = int(sub.get("sa_part_code_len") or 0)
        sa_data_addr = (sub.get("sa_data_addr") or "").strip() if sa_enabled else ""
        sa_data_len  = int(sub.get("sa_data_len") or 0)
        sa_time_addr = (sub.get("sa_time_addr") or "").strip() if sa_enabled else ""
        sa_time_len  = int(sub.get("sa_time_len") or 0)
        sa_names     = sub.get("sa_register_names") or []
        sa_scales    = sub.get("sa_register_scales") or []
        # 2026-08-06 — SHIFT-WISE capture.  Operator spec: "jaise baaki
        # machines mein D-bit se data lete aur agli shift se pehle usko reset
        # karte hain, waise hi semi auto mein bhi".  When BOTH addresses are
        # configured the PLC accumulates the whole shift itself, raises
        # `sa_shift_data_bit` when the block is ready, and we clear it with
        # `sa_shift_reset_bit` before the next shift.  The trigger bit then
        # becomes the shift bit instead of the per-cycle fetch bit; with both
        # blank everything behaves exactly as before.
        # 2026-08-19 — SEAT SLIDER trace: which SA data register carries the
        # station verdict, and what its OK / NG values are.  Blank register =
        # no verdict logging and no Final bit for this machine.
        sa_ok_bit   = (sub.get("sa_ok_bit") or "").strip() if sa_enabled else ""
        sa_ng_bit_c = (sub.get("sa_ng_bit") or "").strip() if sa_enabled else ""
        sa_res_reg  = (sub.get("sa_result_register") or "").strip() if sa_enabled else ""
        try:    sa_res_ok = int(sub.get("sa_result_ok_value") or 1)
        except Exception: sa_res_ok = 1
        try:    sa_res_ng = int(sub.get("sa_result_ng_value") or 2)
        except Exception: sa_res_ng = 2
        sa_shift_bit   = (sub.get("sa_shift_data_bit")  or "").strip() if sa_enabled else ""
        sa_reset_bit   = (sub.get("sa_shift_reset_bit") or "").strip() if sa_enabled else ""
        sa_shift_mode  = bool(sa_shift_bit)
        sa_trigger_bit = sa_shift_bit if sa_shift_mode else sa_fetch_bit
        # 2026-08-24 — separate NG read trigger.  The OK fetch bit above fires
        # the capture for a good part; some ladders never raise it on an NG
        # part, so its data was lost.  When configured, this bit's rising edge
        # ALSO fires the same capture and the verdict comes out NG.  Blank =
        # exactly the old behaviour.  Semi-Auto only (never touches counting).
        sa_ng_trig     = (sub.get("sa_ng_trigger_bit") or "").strip() if sa_enabled else ""
        sa_active    = (sa_enabled and sa_trigger_bit
                         and sa_data_addr and sa_data_len > 0)
        last_sa_bit  = 0
        last_sa_ng_bit = 0
        # 2026-05-22 — SA-as-cycle fallback.  Some sub-machines' PLC
        # ladders don't drive L108 — the cycle-complete signal is the
        # `sa_fetch_bit` itself (e.g. Semi-Auto on 192.168.10.152
        # only pulses M5700, never L108).  When count_bit stays 0 all
        # day but sa_fetch_bit fires N times, treat sa_fetch_bit
        # rising edges AS cycle completions for ct_log purposes.
        # `last_sa_edge_ts` is the previous sa_fetch_bit edge so we
        # can compute CT = delta between fetches.
        last_sa_edge_ts = None

        def _sa_reg_addr(base: str, offset: int) -> str:
            """Compute the i-th register address from a base.  "D5801"
            + offset=2 → "D5803".  Falls back to "{base}+offset" if the
            base doesn't match the expected letters+digits pattern."""
            import re as _re_addr
            m = _re_addr.match(r"([A-Za-z]+)(\d+)", base or "")
            if not m: return f"{base}+{offset}"
            return f"{m.group(1)}{int(m.group(2)) + offset}"

        print(f"{tag} starting poller @ {plc_ip}:{plc_port} bit={count_bit}", flush=True)
        if sa_active:
            if sa_shift_mode:
                _sa_mode_txt = (f"SHIFT-WISE trigger={sa_shift_bit} "
                                f"reset={sa_reset_bit or 'off'}")
            else:
                _sa_mode_txt = f"per-cycle fetch={sa_fetch_bit}"
            print(f"{tag} Semi-Auto ENABLED: {_sa_mode_txt} "
                  f"part={sa_part_addr},{sa_part_len} "
                  f"data={sa_data_addr},{sa_data_len} "
                  f"time={sa_time_addr or '(server)'}", flush=True)

        plc = None
        last_bit        = 0
        last_ok_reg     = None    # register-mirror: last OK register value seen
        last_ng_reg     = None    # register-mirror: last NG register value seen
        last_edge_ts    = None
        cycle_seq_today = 0
        last_date       = None
        last_shift      = None    # seq resets when shift flips (A → B → OT …)
        next_reconnect  = 0.0
        poll_count      = 0
        last_heartbeat  = time.time()
        # 2026-05-29 — Realtime connection-state tracking.  Operator
        # request: when PLC is physically switched OFF, the collector
        # log should IMMEDIATELY show "DISCONNECTED" (not silently
        # repeat "connect failed").  When PLC comes back ON, show
        # "RECONNECTED after Ns offline".  These are state-transition
        # events; the in-between retries are throttled to once per 30s
        # so the log isn't spammed during a long outage.
        is_alive            = False     # last known healthy state
        offline_since       = None      # ts when we first noticed dead
        last_retry_log_ts   = 0.0       # throttle for failure spam

        def _mark_offline(reason: str):
            nonlocal is_alive, offline_since, last_retry_log_ts
            if is_alive:
                is_alive = False
                offline_since = time.time()
                last_retry_log_ts = 0.0
                print(f"{tag} *** DISCONNECTED *** ({reason}) — was healthy "
                      f"a moment ago; PLC switched off / network drop / "
                      f"MC slot lost.  Retrying every ~5s silently; will "
                      f"log RECONNECTED when back.", flush=True)
            else:
                # Already offline — throttle the retry-failure spam
                _now = time.time()
                if _now - last_retry_log_ts >= 30:
                    _down = (_now - offline_since) if offline_since else 0
                    print(f"{tag} still offline ({_down:.0f}s, {reason})",
                          flush=True)
                    last_retry_log_ts = _now
            try:
                _st = self._machine_status.setdefault(sub_id, {})
                _st.update({"name": name, "plc_ok": False, "ts": time.time()})
            except Exception:
                pass

        def _mark_online():
            nonlocal is_alive, offline_since
            if not is_alive:
                _down = (time.time() - offline_since) if offline_since else 0
                if offline_since is not None:
                    print(f"{tag} *** RECONNECTED *** after {_down:.0f}s "
                          f"offline (Type3E)", flush=True)
                else:
                    # First-ever connect (fresh boot, never was online)
                    print(f"{tag} connected (Type3E)", flush=True)
                is_alive = True
                offline_since = None
            try:
                _st = self._machine_status.setdefault(sub_id, {})
                _st.update({"name": name, "plc_ok": True, "ts": time.time()})
            except Exception:
                pass

        def _connect() -> bool:
            nonlocal plc
            try:
                if plc:
                    try: plc.close()
                    except: pass
                # 2026-05-24 — Type3E for sub-machines.
                # Operator test confirmed: Lock Bar (192.168.10.181) +
                # Lower Rail (192.168.10.182) are Q-series CPUs that
                # silently return 0 on Type4E reads.  Type3E is the
                # baseline MELSEC frame — supported by BOTH Q-series
                # AND iQ-R, so universal.  Other sub-machines (Upper
                # Rail, Semi-Auto, Ball Guide) work fine on Type3E too
                # (no functional difference for the read calls we use).
                plc = pymcprotocol.Type3E()
                # 2026-05-29 — Socket timeout 3.0s (default was 2.0s,
                # first attempt at 1.5s rejected slow PLCs).  Live
                # probe of Sub 8 (192.168.10.190) showed TCP handshake
                # at 517ms — Q-series CPUs handle multiple connection
                # slots slowly when one died unclean.  3.0s covers
                # those quirks while still failing fast when the PLC
                # is truly offline.  next_reconnect (below) is 2s so
                # full retry cycle = ~3s on success, ~5s on failure.
                plc.soc_timeout = 3.0
                plc.connect(plc_ip, plc_port)
                # Connection probe.  In register-mirror mode the count
                # source is a WORD register (count_bit may be blank), so
                # probe that; otherwise probe the count bit as before.
                if sub_reg_mode and sub_ok_reg:
                    plc.batchread_wordunits(headdevice=sub_ok_reg, readsize=1)
                else:
                    plc.batchread_bitunits(headdevice=count_bit, readsize=1)
                _mark_online()
                return True
            except Exception as e:
                plc = None
                _mark_offline(str(e))
                return False

        def _reload_cycle_seq(d, shift):
            """Max cycle_seq written for this sub-machine, date AND shift.
            Per-shift scoping keeps 'cycle #1' always meaning the first
            part of the currently-running shift, matching how the main
            PLC dashboard restarts counts at each shift boundary."""
            try:
                c = _db_conn()
                cur = c.cursor()
                cur.execute(
                    "SELECT COALESCE(MAX(cycle_seq), 0) "
                    "FROM mes_submachine_ct_log "
                    "WHERE sub_plc_id = %s "
                    "  AND record_date = %s "
                    "  AND shift_name  = %s",
                    (sub_id, d, shift),
                )
                seq = cur.fetchone()[0] or 0
                cur.close()
                c.close()
                return int(seq)
            except Exception as e:
                print(f"{tag} reload seq failed: {e}", flush=True)
                return 0

        # ── AUTO L110 shift-reset pulse (per-sub, 2026-05-30) ─────────
        # This sub fires ONE L110 pulse on ITS OWN connection each time the
        # engine's shift-reset epoch advances (real shift end or manual test
        # flag).  ONLY register-mode subs WITH a shift_reset_bit participate,
        # so semi-auto (bit mode) and every other bit-mode sub are completely
        # excluded -> zero regression.  Seed `done` to the CURRENT epoch so a
        # sub thread that (re)starts mid-day does NOT replay past shift ends —
        # it only pulses on the NEXT real epoch.  Non-blocking state machine,
        # a mirror of the FI one in _maybe_pulse_fi_shift_reset.
        sub_reset_bit        = (sub.get("shift_reset_bit") or "").strip()
        sub_reset_enabled    = bool(sub_reg_mode and sub_reset_bit)
        sub_reset_epoch_done = self._shift_reset_epoch
        sub_reset_on_since   = None
        _l110_fail_count     = 0   # 2026-06-02 throttle/give-up for L110 write fails
        if sub_reset_enabled:
            print(f"{tag} L110 auto shift-reset ARMED "
                  f"(bit={sub_reset_bit}, epoch base={sub_reset_epoch_done})",
                  flush=True)

        # ── Semi-Auto data capture ─────────────────────────────────────
        # Called from BOTH the register-mirror path and the bit-mode path.
        # It was originally inline below the bit-mode counting code, which
        # register-mirror subs never reach (they `continue` earlier), so the
        # capture silently died for every SA machine.  Keeping it in one
        # function means the two paths can never drift apart again.
        def _sa_capture():
            nonlocal last_sa_bit, last_sa_ng_bit, last_sa_edge_ts, cycle_seq_today
            # ── Semi-Auto data capture ──────────────────────────────
            # Independent rising-edge tracker on sa_fetch_bit (e.g.
            # M5700).  On each 0→1, do three parallel reads (part code,
            # data block, optional PLC time), apply scaling, INSERT one
            # row into mes_submachine_data_log.  Failures here never
            # affect the cycle-count loop above — we just log and move on.
            if sa_active and plc is not None:
                try:
                    sa_bits = plc.batchread_bitunits(headdevice=sa_trigger_bit, readsize=1)
                    sa_cur  = 1 if int(sa_bits[0]) else 0
                except Exception as e:
                    sa_cur = 0
                    # Throttle so a dead bit doesn't flood the log
                    if poll_count % 100 == 0:
                        print(f"{tag} SA trigger bit ({sa_trigger_bit}) read err: {e}", flush=True)
                # 2026-08-24 — separate NG read trigger.  When the OK fetch bit
                # never pulses on an NG part, this bit's rising edge fires the
                # very same capture so the NG cycle's data is still read; the
                # verdict then resolves NG (via sa_ng_bit if driven, else
                # defaulted below).  Blank sa_ng_trig = nothing changes.
                sa_ng_cur = 0
                if sa_ng_trig:
                    try:
                        _ngb = plc.batchread_bitunits(headdevice=sa_ng_trig, readsize=1)
                        sa_ng_cur = 1 if int(_ngb[0]) else 0
                    except Exception as e:
                        sa_ng_cur = 0
                        if poll_count % 100 == 0:
                            print(f"{tag} SA NG trigger bit ({sa_ng_trig}) read err: {e}", flush=True)
                _ok_edge = (sa_cur == 1 and last_sa_bit == 0)
                _ng_edge = (sa_ng_cur == 1 and last_sa_ng_bit == 0)
                # True only when the NG trigger fired this tick and the OK fetch
                # bit did not — used to default a blank verdict to NG and to
                # keep the shift-reset pulse off the NG path.
                _fired_by_ng = _ng_edge and not _ok_edge
                if _ok_edge or _ng_edge:
                    try:
                        # Part code (byte-reversed ASCII, low|high per register)
                        part_code = ""
                        if sa_part_addr and sa_part_len > 0:
                            regs = plc.batchread_wordunits(
                                headdevice=sa_part_addr, readsize=sa_part_len)
                            chars = []
                            for r in regs:
                                hi = r & 0xFF
                                lo = (r >> 8) & 0xFF
                                if hi: chars.append(chr(hi))
                                if lo: chars.append(chr(lo))
                            part_code = ("".join(chars)
                                            .strip().strip("\x00").rstrip(":"))

                        # Data block (raw int registers)
                        raw_data = list(plc.batchread_wordunits(
                            headdevice=sa_data_addr, readsize=sa_data_len))

                        # 2026-08-26 — NG-fetch parts are captured BEFORE the
                        # load cell is read; this station leaves the PART-CODE
                        # barcode sitting in the load/force registers while the
                        # dedicated part-code register (sa_part_addr) reads
                        # blank.  Recover it: if part_code came back empty and
                        # the data block is really printable ASCII (register
                        # values in 0x2000-0x7F7F, e.g. 12336 = 0x3030 = "00"),
                        # decode it INTO the part code — so traceability and the
                        # Final SA-NG reject can match this part — and drop the
                        # bogus "load" values (there is no real load here).
                        if (not part_code) and raw_data:
                            _nz  = [v for v in raw_data if isinstance(v, int) and v]
                            _asc = [v for v in _nz if 0x2000 <= v <= 0x7F7F]
                            if _nz and len(_asc) >= max(3, len(_nz) * 0.6):
                                _bc = []
                                for _r in raw_data:
                                    for _b in (_r & 0xFF, (_r >> 8) & 0xFF):
                                        if 32 <= _b < 127:
                                            _bc.append(chr(_b))
                                part_code = "".join(_bc).strip().rstrip(":")
                                raw_data = []   # no real load in this capture
                                print(f"{tag} SA barcode recovered from load "
                                      f"regs -> part={part_code!r}", flush=True)

                        # PLC time (optional — 6 registers: yr, mo, dy, hr, min, sec)
                        ts_plc = None
                        if sa_time_addr and sa_time_len >= 6:
                            try:
                                t = plc.batchread_wordunits(
                                    headdevice=sa_time_addr, readsize=sa_time_len)
                                yr, mo, dy, hr, mn, sc = (
                                    int(t[0]), int(t[1]), int(t[2]),
                                    int(t[3]), int(t[4]), int(t[5]),
                                )
                                if 0 <= yr < 100: yr += 2000
                                ts_plc = datetime(yr, mo, dy, hr, mn, sc)
                            except Exception:
                                ts_plc = None

                        # Decorate each value with its label + scaled form
                        data_values = []
                        for i, raw in enumerate(raw_data):
                            try: raw_i = int(raw)
                            except Exception: raw_i = 0
                            label = sa_names[i]  if i < len(sa_names)  else f"data_{i+1}"
                            try: scale = float(sa_scales[i]) if i < len(sa_scales) else 1.0
                            except Exception: scale = 1.0
                            try: scaled = round(raw_i * scale, 4)
                            except Exception: scaled = None
                            data_values.append({
                                "register": _sa_reg_addr(sa_data_addr, i),
                                "label":    str(label),
                                "raw":      raw_i,
                                "scaled":   scaled,
                            })

                        _sa_sql = (
                            "INSERT INTO mes_submachine_data_log "
                            "(sub_plc_id, line_id, record_date, shift_name, "
                            " cycle_seq, ts_plc, ts_server, part_code, "
                            " model_number, model_name, data_values) "
                            "VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s)")
                        _sa_params = (sub_id, line_id, today, cur_shift,
                                      cycle_seq_today, ts_plc, part_code or None,
                                      self._cur_model, self._cur_model_name,
                                      psycopg2.extras.Json(data_values))
                        # DB-up: writes now.  DB-down: durable-buffered (JSONB
                        # data_values serialized via .adapted, re-wrapped on replay).
                        # 2026-09-01 — GATE (operator: "only value + decision based
                        # rows table me jaaye").  Skip the capture write entirely
                        # when the force block came back EMPTY (data not read — e.g.
                        # a YFG comms/frame-desync glitch).  An empty capture used to
                        # still land as a "—" row and, via the NG-fetch verdict below,
                        # a phantom NG that FALSELY rejected the part at Final.
                        if data_values:
                            _buffered_exec_own(_sa_sql, _sa_params)
                        print(f"{tag} SA{' [SHIFT]' if sa_shift_mode else ''} "
                              f"#{cycle_seq_today} "
                              f"part={part_code!r} vals={len(data_values)} "
                              f"ts_plc={ts_plc.isoformat() if ts_plc else '(server)'}",
                              flush=True)

                        # ── SEAT SLIDER verdict trace (2026-08-19) ──
                        # The configured register says OK / NG for THIS part.
                        # We only log it here; the Final Inspection loop reads
                        # the log back when the same part code arrives and
                        # raises its bit.  Counting and OEE are untouched by
                        # design — operator asked for "bit + log only".
                        # 2026-08-25 — NG-fetch parts often carry NO part_code
                        # at capture time (the ladder assigns the code on the
                        # OK path only).  The old `and part_code` gate then
                        # dropped the whole verdict block, so an NG caught by
                        # the NG-fetch bit was captured to data_log but NEVER
                        # logged as NG on the Quality page.  Let a NG-fetch
                        # capture through even without a code — OK path is
                        # unchanged (still requires part_code as before).
                        if (sa_ok_bit or sa_ng_bit_c or sa_res_reg) and (part_code or _fired_by_ng):
                            try:
                                _verdict_raw = None
                                _res = None
                                if sa_ok_bit or sa_ng_bit_c:
                                    # 2026-08-24 — verdict from two bits: read
                                    # both at the moment the part is captured and
                                    # let whichever is ON decide.  NG wins if the
                                    # ladder ever raises both, because shipping a
                                    # bad part is the costlier mistake.
                                    _okv = _ngv = None
                                    try:
                                        if sa_ok_bit:
                                            _okv = int(plc.batchread_bitunits(
                                                headdevice=sa_ok_bit, readsize=1)[0])
                                        if sa_ng_bit_c:
                                            _ngv = int(plc.batchread_bitunits(
                                                headdevice=sa_ng_bit_c, readsize=1)[0])
                                    except Exception as _be:
                                        print(f"{tag} SA verdict bit read failed "
                                              f"({sa_ok_bit}/{sa_ng_bit_c}): {_be}",
                                              flush=True)
                                    if _ngv == 1:   _res = "NG"
                                    elif _okv == 1: _res = "OK"
                                    # raw_value keeps what was actually seen so the
                                    # Quality page can show why a verdict is blank.
                                    _verdict_raw = ((1 if _okv else 0)
                                                    + (2 if _ngv else 0))
                                    if _res is None:
                                        print(f"{tag} SA verdict: neither bit ON "
                                              f"(OK[{sa_ok_bit or '-'}]={_okv} "
                                              f"NG[{sa_ng_bit_c or '-'}]={_ngv})",
                                              flush=True)
                                else:
                                    for _dv in data_values:
                                        if str(_dv.get("register", "")).upper() == sa_res_reg.upper():
                                            _verdict_raw = _dv.get("raw")
                                            break
                                    if _verdict_raw is None:
                                        print(f"{tag} SA verdict reg {sa_res_reg} not in "
                                              f"the captured block ({sa_data_addr}+{sa_data_len}) "
                                              f"-- check config", flush=True)
                                    else:
                                        _res = ("NG" if int(_verdict_raw) == sa_res_ng else
                                                "OK" if int(_verdict_raw) == sa_res_ok else None)
                                        if _res is None:
                                            print(f"{tag} SA verdict {sa_res_reg}={_verdict_raw} "
                                                  f"matches neither OK({sa_res_ok}) nor "
                                                  f"NG({sa_res_ng})", flush=True)
                                # Fired by the NG fetching bit → the part IS NG
                                # by definition (that bit is the NG-condition
                                # trigger, the parallel of the OK fetch bit), so
                                # record NG regardless of what the OK/NG verdict
                                # bits happened to read at that instant.
                                if _fired_by_ng:
                                    _res = "NG"
                                    if _verdict_raw is None:
                                        _verdict_raw = 2
                                    print(f"{tag} SA NG via NG-fetch bit "
                                          f"{sa_ng_trig}", flush=True)
                                # 2026-09-01 — GATE (operator: "only value + decision
                                # based rows table me jaaye").  Record a verdict ONLY
                                # when the force block was actually read (value) AND a
                                # verdict was decided (decision).  Previously this
                                # logged "either way" and the NG-fetch trigger forced
                                # result=NG even on an EMPTY read -> phantom NG ->
                                # false reject at Final.  Empty read / undecided => skip.
                                if data_values and _res is not None:
                                    _buffered_exec_own(
                                        "INSERT INTO mes_sa_fi_quality_log "
                                        "(record_date, shift_name, line_id, line_name, station, "
                                        " plc_id, machine_name, part_code, result, raw_value, "
                                        " cycle_seq) "
                                        "VALUES (%s,%s,%s,%s,'SEMI',%s,%s,%s,%s,%s,%s) "
                                        "ON CONFLICT DO NOTHING",
                                        (today, cur_shift, line_id,
                                         self.cfg.get("line_name"), sub_id,
                                         sub.get("machine_name"), part_code,
                                         _res,
                                         (int(_verdict_raw)
                                          if _verdict_raw is not None else None),
                                         cycle_seq_today))
                                    if _res == "NG":
                                        _src = (f"NG bit {sa_ng_bit_c}" if sa_ng_bit_c
                                                else f"{sa_res_reg}={_verdict_raw}")
                                        print(f"{tag} *** SA NG *** part={part_code} "
                                              f"({_src}) -- Final will be signalled "
                                              f"when this part arrives", flush=True)
                            except Exception as _ve:
                                # Never let the trace break the SA capture path.
                                print(f"{tag} SA verdict log failed: {_ve}", flush=True)

                        # 2026-08-06 — SHIFT RESET.  The block has been stored,
                        # so hand the PLC its reset bit (~2 s ON) and it zeroes
                        # the shift's data before the next shift starts.  Same
                        # protocol as the register-mode L110 pulse above, just
                        # scoped to the Semi-Auto block.  Best-effort: a failed
                        # pulse is logged but never blocks the poll loop, and
                        # the next shift's D-bit edge will store data anyway.
                        if sa_shift_mode and sa_reset_bit and not _fired_by_ng:
                            try:
                                plc.batchwrite_bitunits(
                                    headdevice=sa_reset_bit, values=[1])
                                time.sleep(2.0)
                                plc.batchwrite_bitunits(
                                    headdevice=sa_reset_bit, values=[0])
                                print(f"{tag} SA shift-reset pulsed "
                                      f"{sa_reset_bit} (2s ON)", flush=True)
                            except Exception as e:
                                print(f"{tag} SA shift-reset ({sa_reset_bit}) "
                                      f"failed: {e}", flush=True)
                    except Exception as e:
                        print(f"{tag} SA capture failed: {e}", flush=True)

                    # 2026-05-22 — SA-AS-CYCLE FALLBACK.
                    # If count_bit (L108) hasn't fired today (cycle_seq_today
                    # still 0 — meaning L108 is genuinely dead on this PLC),
                    # treat THIS sa_fetch_bit rising edge as the cycle
                    # complete signal and write a ct_log row too.
                    # Semi-Auto's PLC 192.168.10.152 only drives M5700, not
                    # L108 — without this fallback the sub-machine page
                    # showed "Waiting for cycle data" every morning until
                    # an L108 edge happened to slip through (rare/never).
                    # 2026-08-06 — never in SHIFT-WISE mode: that trigger fires
                    # once per SHIFT, so treating it as a cycle completion would
                    # invent one bogus 8-hour "cycle" per shift.
                    if cycle_seq_today == 0 and not sa_shift_mode:
                        now_ts_sa = time.time()
                        ct_sa = (now_ts_sa - last_sa_edge_ts) if last_sa_edge_ts else 0.0
                        # 2026-05-23 — same chatter-guard as count_bit branch.
                        if last_sa_edge_ts is not None and ct_sa >= _sub_min_ct:
                            # Net out break overlaps (mirrors count_bit branch)
                            ts_start_sa = datetime.fromtimestamp(last_sa_edge_ts)
                            ts_end_sa   = datetime.fromtimestamp(now_ts_sa)
                            try:
                                brk = self._break_overlap_seconds(ts_start_sa, ts_end_sa)
                            except Exception:
                                brk = 0.0
                            ct_sa_net = max(0.0, ct_sa - brk)
                            candidate_sa_seq = cycle_seq_today + 1
                            shift_sa = self._shift_label()
                            if shift_sa.startswith("GAP"):
                                shift_sa = "GAP"
                            try:
                                c_sa = _db_conn()
                                cur_sa = c_sa.cursor()
                                cur_sa.execute("""
                                    INSERT INTO mes_submachine_ct_log
                                        (sub_plc_id, line_id, record_date,
                                         shift_name, cycle_seq,
                                         ts_start, ts_end, ct_seconds,
                                         model_number, model_name, part_code)
                                    VALUES
                                        (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                                """, (
                                    sub_id, line_id, today,
                                    shift_sa, candidate_sa_seq,
                                    ts_start_sa, ts_end_sa, round(ct_sa_net, 3),
                                    self._cur_model, self._cur_model_name,
                                ))
                                c_sa.commit()
                                cur_sa.close(); c_sa.close()
                                cycle_seq_today = candidate_sa_seq
                                print(f"{tag} SA-as-cycle #{cycle_seq_today} "
                                      f"CT={ct_sa_net:.2f}s (fallback — L108 "
                                      f"silent, using sa_fetch_bit as cycle)",
                                      flush=True)
                            except Exception as e_sa:
                                print(f"{tag} SA-as-cycle insert failed: "
                                      f"{e_sa}", flush=True)
                        last_sa_edge_ts = now_ts_sa
                last_sa_bit = sa_cur
                last_sa_ng_bit = sa_ng_cur

        while not stop_event.is_set():
            now = time.time()

            if plc is None:
                if now < next_reconnect:
                    time.sleep(0.5)
                    continue
                if not _connect():
                    # 2026-05-29 — Tightened reconnect cadence 5s -> 2s
                    # per operator request.  Sub-machine PLC power
                    # outage was leaving collector in 5-min recovery
                    # window even after ping was back from operator's
                    # PC.  With 1.5s soc_timeout + 2s back-off, every
                    # failed attempt cycle = ~3.5s, so worst case
                    # recovery from "ping back" to "connected" is < 4s.
                    next_reconnect = now + 2
                    continue

            # 2026-05-31 — record_date must be SHIFT-ANCHORED (exactly like
            # the main FI, which uses self._cur_shift_record_date), NOT the
            # raw calendar date.  A crosses-midnight shift (e.g. evening B
            # 18:30 -> 03:00) otherwise SPLITS at midnight: rows written
            # after 00:00 land under the NEXT calendar day with the SAME
            # shift_name, so the graph query (record_date=today AND
            # shift_name=B) drags in LAST night's overnight-B rows and the
            # dashboard shows the previous shift.  Anchoring to the shift's
            # start date keeps the whole shift in ONE (record_date,shift)
            # bucket and makes every sub-machine graph reset per shift just
            # like the main dashboard.  Counting is register-based and is
            # NOT affected by this date label (it only tags the row).
            cur_shift = self._shift_label()
            today    = getattr(self, "_cur_shift_record_date", None) or date.today()
            if cur_shift.startswith("GAP"):
                cur_shift = "GAP"
            # Reset the seq counter on date OR shift transition so the
            # frontend shows "cycle #1" at the start of every shift.
            if last_date != today or last_shift != cur_shift:
                last_date       = today
                last_shift      = cur_shift
                cycle_seq_today = _reload_cycle_seq(today, cur_shift)
                last_edge_ts    = None
                last_bit        = 0
                # Register-mirror: drop the per-shift baseline so the new
                # shift re-seeds from the freshly-reset register instead of
                # carrying the previous shift's high value forward.
                last_ok_reg     = None
                last_ng_reg     = None
                print(f"{tag} day={today} shift={cur_shift} "
                      f"resume seq={cycle_seq_today}", flush=True)

            # ── AUTO L110 shift-reset pulse (non-blocking, per-sub) ──
            # plc is guaranteed connected here (we continue'd above on a
            # failed reconnect).  One step per poll, never sleeps.  Inert
            # unless this sub is register-mode WITH a shift_reset_bit.
            if sub_reset_enabled:
                if sub_reset_on_since is not None:
                    if (time.monotonic() - sub_reset_on_since
                            >= self._L110_PULSE_SEC):
                        try:
                            plc.batchwrite_bitunits(
                                headdevice=sub_reset_bit, values=[0])
                            sub_reset_on_since = None
                            print(f"{tag} [L110-PULSE] {sub_reset_bit} -> 0 "
                                  f"(pulse end, epoch {sub_reset_epoch_done})",
                                  flush=True)
                        except Exception as _e:
                            print(f"{tag} [L110-PULSE] {sub_reset_bit} OFF "
                                  f"failed: {_e}; retry next poll", flush=True)
                elif self._shift_reset_epoch > sub_reset_epoch_done:
                    try:
                        plc.batchwrite_bitunits(
                            headdevice=sub_reset_bit, values=[1])
                        sub_reset_on_since   = time.monotonic()
                        sub_reset_epoch_done = self._shift_reset_epoch
                        _l110_fail_count     = 0
                        print(f"{tag} [L110-PULSE] {sub_reset_bit} -> 1 "
                              f"(shift-reset epoch {sub_reset_epoch_done}); "
                              f"hold {self._L110_PULSE_SEC:.0f}s then 0",
                              flush=True)
                    except Exception as _e:
                        # 2026-06-03 — RECONNECT (conn error) + throttle + give up.
                        # A dropped / half-open sub socket (WinError 10054) made
                        # this retry the SAME dead socket every poll forever, so
                        # the sub never reset and every new shift inherited the old
                        # count (only a full restart cleared it).  Now: on a
                        # connection error force a reconnect (fresh socket so the
                        # next attempt can succeed = self-heal, no restart); log the
                        # 1st + every 60th; give up after N so an un-writable bit
                        # (0x0055) can never spam / stay stuck.
                        _l110_fail_count += 1
                        _es = str(_e)
                        _isconn = ("10054" in _es or "10053" in _es
                                   or "forcibly" in _es or "closed" in _es
                                   or "WinError" in _es)
                        if _isconn:
                            try: plc.close()
                            except Exception: pass
                            plc = None                 # force reconnect -> fresh socket
                            next_reconnect = time.time() + 1
                        if _l110_fail_count == 1 or _l110_fail_count % 60 == 0:
                            print(f"{tag} [L110-PULSE] {sub_reset_bit} ON failed "
                                  f"x{_l110_fail_count}: {_e}"
                                  f"{' (reconnecting)' if _isconn else ''}",
                                  flush=True)
                        if _l110_fail_count >= 30:
                            sub_reset_epoch_done = self._shift_reset_epoch
                            print(f"{tag} [L110-PULSE] {sub_reset_bit} ON failed "
                                  f"{_l110_fail_count}x — GIVING UP this shift "
                                  f"(is {sub_reset_bit} writable on this PLC?)",
                                  flush=True)
                        if plc is None:
                            continue                   # go reconnect now

            # ─── REGISTER-MIRROR sub-machine count (2026-05-30) ──────────
            # Gated entirely behind count_mode='register'.  A bit-mode sub
            # NEVER enters here — it falls straight through to the L108 edge
            # path below, byte-for-byte unchanged.  Here we read the OK (and
            # optional NG) DATA REGISTER and turn every +1 into one ct_log
            # ROW (the sub dashboard counts rows/shift).  A +N jump writes N
            # rows, each its own seq with CT split evenly, so per-part history
            # stays complete.  Register reset/decrement (shift wipe) only
            # rebaselines — existing rows are never deleted, and the next
            # shift re-seeds from ~0 via the shift-change reset above.
            if sub_reg_mode and sub_ok_reg:
                # 2026-06-04 — ATOMIC DUAL READ (anti-swap).  When NG is the
                # register immediately after OK (Upper Rail: D101 OK + D102 NG),
                # read BOTH in ONE batchread_wordunits(readsize=2): the two
                # values arrive in a SINGLE MC response, so they can NEVER cross.
                # The old staggered two-read pattern frame-desynced and returned
                # each other's value -> production (D101) landed in D102/NG and
                # got logged as NG@0.0s (live: 417 phantom NG/day on Upper Rail,
                # D101 stuck low while D102 carried the real count).  Registers
                # that are NOT contiguous keep the original two separate reads.
                _ng_now = None
                _contig = False
                if sub_ng_reg:
                    import re as _re_reg
                    _mo = _re_reg.match(r"([A-Za-z]+)(\d+)", str(sub_ok_reg))
                    _mn = _re_reg.match(r"([A-Za-z]+)(\d+)", str(sub_ng_reg))
                    if (_mo and _mn
                            and _mo.group(1).upper() == _mn.group(1).upper()
                            and int(_mn.group(2)) == int(_mo.group(2)) + 1):
                        _contig = True
                if _contig:
                    # ONE response: res[0]=OK (D101), res[1]=NG (D102). No cross.
                    try:
                        _rb = plc.batchread_wordunits(headdevice=sub_ok_reg,
                                                      readsize=2)
                        _ok_now = self._sub_reg_count(_rb[0]) if _rb else None
                        _ng_now = self._sub_reg_count(_rb[1]) if (_rb and len(_rb) >= 2) else None
                        poll_count += 1
                    except Exception as e:
                        _mark_offline(f"reg poll: {e}")
                        try: plc.close()
                        except: pass
                        plc = None
                        next_reconnect = time.time() + 1
                        continue
                else:
                    try:
                        _rok = plc.batchread_wordunits(headdevice=sub_ok_reg,
                                                       readsize=1)
                        _ok_now = self._sub_reg_count(_rok[0]) if _rok else None
                        poll_count += 1
                    except Exception as e:
                        _mark_offline(f"reg poll: {e}")
                        try: plc.close()
                        except: pass
                        plc = None
                        next_reconnect = time.time() + 1
                        continue
                    # NG register read SEPARATELY (staggered) — non-contiguous
                    # NG only; the contiguous branch above already set _ng_now.
                    if sub_ng_reg:
                        try:
                            _rng = plc.batchread_wordunits(headdevice=sub_ng_reg,
                                                           readsize=1)
                            _ng_now = self._sub_reg_count(_rng[0]) if _rng else None
                        except Exception:
                            _ng_now = None

                # 2026-06-02 — OK-correlated NG wipe flag.  A real shift wipe
                # resets OK *and* NG together; a D102 junk-low hits NG only.  So
                # the sub NG only rebaselines DOWN when OK also dropped this poll.
                _sub_wipe = False
                _shift_r = self._shift_label()
                if _shift_r.startswith("GAP"):
                    _shift_r = "GAP"

                # ── OK register → ct_log rows ──
                if _ok_now is not None:
                    if last_ok_reg is None:
                        # Seed to the CURRENT register (not to the row count)
                        # so a mid-shift restart never dumps phantom rows and
                        # a shift-boundary race (register not yet zeroed when
                        # our clock-shift flips) self-corrects on the reset.
                        last_ok_reg = _ok_now
                        print(f"{tag} [REG-MIRROR] OK {sub_ok_reg} seed="
                              f"{_ok_now} (shift {_shift_r}, rows continue "
                              f"from seq={cycle_seq_today})", flush=True)
                    elif _ok_now > last_ok_reg:
                        _delta = _ok_now - last_ok_reg
                        if _delta > 200:
                            # No real sub makes 200 parts in one 0.1s poll —
                            # garbage read / register wrap.  Rebaseline only;
                            # the count re-syncs on the next clean increment.
                            print(f"{tag} [REG-MIRROR] OK {sub_ok_reg} "
                                  f"implausible +{_delta} "
                                  f"({last_ok_reg}->{_ok_now}) — garbage/wrap, "
                                  f"rebaselined, no rows", flush=True)
                            last_ok_reg = _ok_now
                        else:
                            _now_dt = datetime.now()
                            _ct_tot = ((_now_dt - last_sub_ok_ts).total_seconds()
                                       if last_sub_ok_ts else 0.0)
                            # 2026-05-31 — clock-jump guard (host time-sync
                            # excursion -> absurd sub CT spike).  Register-based
                            # count is unaffected; only stored CT is corrected.
                            _ct_tot = self._ct_clock_guard(_ct_tot, tag=tag)
                            _ct_per = (_ct_tot / _delta) if _delta > 1 else _ct_tot
                            _ok_pc  = (self._cur_part_code or "").strip().rstrip(":") or None
                            _wrote  = 0
                            for _i in range(_delta):
                                _row_end   = (_now_dt - timedelta(
                                                  seconds=_ct_per * (_delta - 1 - _i))
                                              if _delta > 1 else _now_dt)
                                _row_start = _row_end - timedelta(
                                                  seconds=max(_ct_per, 1.0))
                                # 2026-05-30 — cycle_seq PINNED to the
                                # D-register value (last_ok_reg+_i+1 is
                                # the new register count for this row)
                                # so the UI per-machine "OK: N" tracks
                                # the PLC absolute count exactly.
                                _cand = int(last_ok_reg) + _i + 1
                                try:
                                    c2 = _db_conn(); cur2 = c2.cursor()
                                    cur2.execute("""
                                        INSERT INTO mes_submachine_ct_log
                                            (sub_plc_id, line_id, record_date,
                                             shift_name, cycle_seq,
                                             ts_start, ts_end, ct_seconds,
                                             model_number, model_name, part_code)
                                        VALUES
                                            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                                    """, (
                                        sub_id, line_id, today, _shift_r, _cand,
                                        _row_start, _row_end, round(_ct_per, 3),
                                        self._cur_model, self._cur_model_name,
                                    ))
                                    c2.commit(); cur2.close(); c2.close()
                                    cycle_seq_today = _cand
                                    _wrote += 1
                                    try:
                                        self._write_machine_log(
                                            machine_id  = sub_id,
                                            bit_type    = "OK",
                                            bit_address = sub_ok_reg,
                                            ts          = _row_end,
                                            ct_seconds  = _ct_per,
                                            part_code   = _ok_pc,
                                            counter_val_override = _cand,
                                        )
                                    except Exception:
                                        pass
                                except Exception as e:
                                    print(f"{tag} [REG-MIRROR] OK row insert "
                                          f"failed (seq held {cycle_seq_today}"
                                          f"): {e}", flush=True)
                                    break
                            if _wrote:
                                last_sub_ok_ts = _now_dt
                                self._last_submachine_pulse_ts = time.time()
                                # Advance the baseline ONLY by rows that
                                # actually committed — a mid-batch DB failure
                                # leaves the rest pending for the next poll.
                                last_ok_reg += _wrote
                                print(f"{tag} [REG-MIRROR] OK {sub_ok_reg} "
                                      f"+{_wrote}/{_delta} -> seq="
                                      f"{cycle_seq_today} ct/part="
                                      f"{_ct_per:.1f}s", flush=True)
                    elif _ok_now < last_ok_reg:
                        print(f"{tag} [REG-MIRROR] OK {sub_ok_reg} reset "
                              f"{last_ok_reg}->{_ok_now} (shift wipe) — "
                              f"rebaseline, rows untouched", flush=True)
                        last_ok_reg = _ok_now
                        _sub_wipe   = True   # OK reset -> real wipe; NG may reset too

                # ── NG register → ct_log rows (is_ng=True) ──
                if _ng_now is not None and sub_ng_reg:
                    if last_ng_reg is None:
                        last_ng_reg = _ng_now
                    elif _ng_now > last_ng_reg:
                        _dn = _ng_now - last_ng_reg
                        if _dn > 200:
                            print(f"{tag} [REG-MIRROR] NG {sub_ng_reg} "
                                  f"implausible +{_dn} — garbage/wrap, "
                                  f"rebaselined, no rows", flush=True)
                            last_ng_reg = _ng_now
                        else:
                            _now_dt = datetime.now()
                            _ng_pc  = (self._cur_part_code or "").strip().rstrip(":") or None
                            _wrote_n = 0
                            for _i in range(_dn):
                                # 2026-05-30 — NG cycle_seq + counter_val
                                # PINNED to the NG D-register value so UI
                                # NG count matches the PLC NG register.
                                _cand = int(last_ng_reg) + _i + 1
                                try:
                                    c2 = _db_conn(); cur2 = c2.cursor()
                                    cur2.execute("""
                                        INSERT INTO mes_submachine_ct_log
                                            (sub_plc_id, line_id, record_date,
                                             shift_name, cycle_seq,
                                             ts_start, ts_end, ct_seconds,
                                             model_number, model_name,
                                             part_code, is_ng)
                                        VALUES
                                            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                    """, (
                                        sub_id, line_id, today, _shift_r, _cand,
                                        _now_dt - timedelta(seconds=60), _now_dt,
                                        0.0, self._cur_model,
                                        self._cur_model_name, _ng_pc, True,
                                    ))
                                    c2.commit(); cur2.close(); c2.close()
                                    cycle_seq_today = _cand
                                    _wrote_n += 1
                                    try:
                                        self._write_machine_log(
                                            machine_id  = sub_id,
                                            bit_type    = "NG",
                                            bit_address = sub_ng_reg,
                                            ts          = _now_dt,
                                            ct_seconds  = 0.0,
                                            part_code   = _ng_pc,
                                            counter_val_override = _cand,
                                        )
                                    except Exception:
                                        pass
                                except Exception as e:
                                    print(f"{tag} [REG-MIRROR] NG row insert "
                                          f"failed: {e}", flush=True)
                                    break
                            if _wrote_n:
                                last_ng_reg += _wrote_n
                                print(f"{tag} [REG-MIRROR] NG {sub_ng_reg} "
                                      f"+{_wrote_n}/{_dn} -> seq="
                                      f"{cycle_seq_today}", flush=True)
                    elif _ng_now < last_ng_reg:
                        last_ng_reg = _ng_now   # 2026-06-02 EXACT mirror down (D102)

                # Heartbeat (register subs skip the bit-path heartbeat below).
                if time.time() - last_heartbeat >= 30:
                    print(f"{tag} heartbeat (REG) polls={poll_count} "
                          f"ok_reg={_ok_now} ng_reg={_ng_now} "
                          f"seq_today={cycle_seq_today}", flush=True)
                    last_heartbeat = time.time()
                    try:    # publish live register value for the status table
                        _st = self._machine_status.setdefault(sub_id, {})
                        _st.update({"name": name, "val": _ok_now,
                                    # cumulative NG count (guarded), not the raw
                                    # bouncing register read.
                                    "ng_val": (last_ng_reg
                                               if last_ng_reg is not None
                                               else _ng_now),
                                    "ts": time.time()})
                    except Exception:
                        pass

                # 2026-08-22 — register-mirror subs `continue` here, which used to
                # skip the Semi-Auto capture block further down entirely.  Every
                # SA-enabled machine is count_mode='register', so the load/force
                # block stopped being recorded the day they were switched over
                # (last row 2026-07-25) even though the PLC kept pulsing M5700.
                _sa_capture()
                time.sleep(0.1)
                continue
            # ─── end register-mirror; bit-mode subs fall through ─────────

            try:
                bits = plc.batchread_bitunits(headdevice=count_bit, readsize=1)
                cur_bit = 1 if int(bits[0]) else 0
                poll_count += 1
            except Exception as e:
                # Mid-poll failure (PLC just went offline or LAN drop)
                # — surface via the same DISCONNECTED transition log as
                # connect-failures.  Loud once, silent retries after.
                _mark_offline(f"poll: {e}")
                try: plc.close()
                except: pass
                plc = None
                # 2026-05-29 — Tightened poll-error back-off 3s -> 1s
                # so mid-poll disconnects recover as fast as the LAN
                # allows.  Pair with 1.5s soc_timeout above.
                next_reconnect = time.time() + 1
                continue

            # 2026-05-28 - NG bit DISABLED (same as Final Inspection).
            # Phantom reads from collector's socket vs zero from external
            # monitor - can't reconcile.  ng_bit=0 always until ladder
            # confirmed.
            cur_ng_bit = 0
            # if ng_bit_addr:
            #     try:
            #         _ngb = plc.batchread_bitunits(headdevice=ng_bit_addr, readsize=1)
            #         cur_ng_bit = 1 if int(_ngb[0]) else 0
            #     except Exception:
            #         cur_ng_bit = 0
            if cur_ng_bit == 1 and last_ng_bit == 0:
                _ng_now_dt = datetime.now()
                # 2026-05-28 - PURE PASS-THROUGH.  Every L109 rise =
                # +1 NG row.  No gap filter, no garbage skip.  Whatever
                # PLC says, store it.
                _ng_ct_delta = ((_ng_now_dt - last_sub_ng_ts).total_seconds()
                                if last_sub_ng_ts else 0.0)
                _ng_pc = (self._cur_part_code or "").strip().rstrip(":") or None
                # Write to new per-machine table
                try:
                    self._write_machine_log(
                        machine_id   = sub_id,
                        bit_type     = "NG",
                        bit_address  = ng_bit_addr,
                        ts           = _ng_now_dt,
                        ct_seconds   = _ng_ct_delta,
                        part_code    = _ng_pc,
                    )
                except Exception:
                    pass
                # Also write to LEGACY mes_submachine_ct_log with is_ng=true
                # so the existing dashboard / wallboard charts pick it up.
                # 2026-05-28 — Window bumped 30s → 60s (matches Final
                # Inspection no-caps policy; still safely under the
                # camera CMS 416-range limit for sub-machine TS files).
                try:
                    _ts_start_ng = _ng_now_dt - timedelta(seconds=60)
                    _shift_ng = self._shift_label()
                    if _shift_ng.startswith("GAP"): _shift_ng = "GAP"
                    _c2 = _db_conn()
                    _cur2 = _c2.cursor()
                    _cur2.execute("""
                        INSERT INTO mes_submachine_ct_log
                            (sub_plc_id, line_id, record_date, shift_name,
                             cycle_seq, ts_start, ts_end, ct_seconds,
                             model_number, model_name, part_code, is_ng)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        sub_id, line_id, _ng_now_dt.date(), _shift_ng,
                        cycle_seq_today + 1,
                        _ts_start_ng, _ng_now_dt, round(_ng_ct_delta, 3),
                        self._cur_model, self._cur_model_name,
                        _ng_pc, True,
                    ))
                    _c2.commit()
                    _cur2.close(); _c2.close()
                    cycle_seq_today += 1
                    print(f"{tag} NG #{cycle_seq_today} ct={_ng_ct_delta:.2f}s "
                          f"pc={_ng_pc}", flush=True)
                except Exception as _ne:
                    print(f"{tag} NG legacy-insert failed: {_ne}", flush=True)
                last_sub_ng_ts        = _ng_now_dt
                # 2026-05-27 — Anchor still tracked for future use, but
                # NG ct above no longer reads from it.
                last_any_pulse_sub_dt = _ng_now_dt
            last_ng_bit = cur_ng_bit

            # Heartbeat every 30 s so we can see if the poller is healthy
            # even when M100 has been idle
            if time.time() - last_heartbeat >= 30:
                print(f"{tag} heartbeat polls={poll_count} bit={cur_bit} "
                      f"seq_today={cycle_seq_today}", flush=True)
                last_heartbeat = time.time()
                try:    # publish live bit state for the status table
                    _st = self._machine_status.setdefault(sub_id, {})
                    _st.update({"name": name, "val": cur_bit,
                                "ts": time.time()})
                except Exception:
                    pass

            if cur_bit == 1 and last_bit == 0:
                now_dt = datetime.now()
                now_ts = now_dt.timestamp()

                # 2026-05-24 — Per-machine L6 table (replaces pulse_log).
                # Every count_bit rise on this sub-machine writes one row
                # to the machine's own mes_l6_* table.  Zero gating.
                # 2026-05-27 — REVERTED to OK-to-OK CT.  Any-pulse
                # broke sub-machine charts (showed 100s+ for the first
                # pulse after long idle).  Each sub-machine independent
                # of NG events now.
                try:
                    # 2026-06-04 — First-part-of-shift anchor (same as main).
                    # last_sub_ok_ts is NOT reset at shift change, so the first
                    # sub OK of a shift would audit now - (prev shift's last OK)
                    # = phantom (and a huge historical clip window).  Floor it
                    # at this shift's start.  No-op for every later part.
                    _anchor_ok = last_sub_ok_ts
                    _sst_a = getattr(self, "_shift_start_ts", None)
                    if _sst_a:
                        _ss_dt_a = datetime.fromtimestamp(_sst_a)
                        if _anchor_ok is None or _anchor_ok < _ss_dt_a:
                            _anchor_ok = _ss_dt_a
                    _ct_delta = ((now_dt - _anchor_ok).total_seconds()
                                 if _anchor_ok else 0.0)
                    # 2026-05-31 — clock-jump guard (see _ct_clock_guard).
                    _ct_delta = self._ct_clock_guard(_ct_delta, tag=tag)
                    _ok_pc = (self._cur_part_code or "").strip().rstrip(":") or None
                    # PURE PASS-THROUGH - whatever PLC gave, store.
                    self._write_machine_log(
                        machine_id   = sub_id,
                        bit_type     = "OK",
                        bit_address  = count_bit,
                        ts           = now_dt,
                        ct_seconds   = _ct_delta,
                        part_code    = _ok_pc,
                    )
                    last_sub_ok_ts        = now_dt
                    last_any_pulse_sub_dt = now_dt
                except Exception:
                    pass


                # 2026-05-27 — REVERTED to OK-to-OK for ct_log raw_ct.
                # Sub-machine charts use OK-only model so post-NG OK
                # doesn't show artificial spike from rare NG events.
                # 2026-06-04 — First-part-of-shift anchor (same as main).
                # last_edge_ts is reset to None at every shift change, so the
                # first pulse only anchored (no CT) — and any carried value
                # would be the previous shift's stale edge (phantom).  Seed it
                # from THIS shift's start so the first charted part naps from
                # shift start, just like the main machine's Part #1.  Floor a
                # carried value at shift start too.  No-op for every later part.
                _sst_e = getattr(self, "_shift_start_ts", None)
                if last_edge_ts is None and _sst_e and _sst_e < now_ts:
                    last_edge_ts = _sst_e
                elif last_edge_ts is not None and _sst_e and last_edge_ts < _sst_e:
                    last_edge_ts = _sst_e
                if last_edge_ts is None:
                    last_edge_ts = now_ts
                else:
                    raw_ct = now_ts - last_edge_ts
                    # 2026-05-31 — clock-jump guard FIRST: a host time-sync
                    # excursion makes (now_ts - last_edge_ts) absurd; clamp to
                    # ideal so the chart doesn't spike and the cycle still
                    # commits (chatter check below then passes).  A plausible
                    # raw_ct flows through untouched and break-nets as before.
                    raw_ct = self._ct_clock_guard(raw_ct, tag=tag)
                    # 2026-05-16 — net out any break time that fell
                    # inside [last_edge, now].
                    ts_start_dt = datetime.fromtimestamp(last_edge_ts)
                    brk_sec     = self._break_overlap_seconds(ts_start_dt, now_dt)
                    ct = max(0.0, raw_ct - brk_sec)
                    if brk_sec > 0.5:
                        print(f"{tag} cycle spanned {brk_sec:.0f}s of "
                              f"break time — raw={raw_ct:.1f}s, "
                              f"net={ct:.1f}s", flush=True)
                    # 2026-05-24 — chatter-guard on RAW CT, not net.
                    # Earlier bug: if a cycle's window happened to
                    # overlap a scheduled break entirely, net=0 and the
                    # cycle was CHATTER-DROPPED even though it was a
                    # real machine cycle (operator working through
                    # break).  Symptom: Lock Bar today produced 9 OK
                    # cycles 12:13–12:20 but mes_submachine_ct_log
                    # showed 0 because every cycle was lunch-overlap.
                    # New rule: chatter is judged on wall-clock raw_ct
                    # (a true ladder double-pulse is sub-second
                    # regardless of break alignment).  Store the
                    # break-netted ct so the chart still doesn't
                    # spike during real breaks.
                    if raw_ct < _sub_min_ct:
                        print(f"{tag} CHATTER-DROP raw_ct={raw_ct:.2f}s "
                              f"< {_sub_min_ct:.2f}s — true ladder "
                              f"double-pulse.", flush=True)
                        last_edge_ts = now_ts
                    elif raw_ct >= _sub_min_ct:
                        # Commit FIRST, then bump the counter — otherwise a
                        # failed insert leaves a gap (row #N missing but seq
                        # advanced to N+1). Keeps "cycles count" and
                        # "last cycle_seq" in the UI header identical.
                        candidate_seq = cycle_seq_today + 1
                        ts_start = ts_start_dt
                        ts_end   = now_dt
                        shift    = self._shift_label()
                        if shift.startswith("GAP"):
                            shift = "GAP"
                        try:
                            c2 = _db_conn()
                            cur2 = c2.cursor()
                            # part_code is intentionally NULL for sub-machines:
                            # the code scanner is only on the FINAL/main machine.
                            # Sub-machine cycles are identified by (sub_plc_id,
                            # cycle_seq, ts_start, ts_end) only — model context
                            # is still recorded for filtering, but the part_code
                            # field is reserved for the part actually scanned at
                            # the line's final station.
                            cur2.execute("""
                                INSERT INTO mes_submachine_ct_log
                                    (sub_plc_id, line_id, record_date,
                                     shift_name, cycle_seq,
                                     ts_start, ts_end, ct_seconds,
                                     model_number, model_name, part_code)
                                VALUES
                                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                            """, (
                                sub_id, line_id, today,
                                shift, candidate_seq,
                                ts_start, ts_end, round(ct, 3),
                                self._cur_model, self._cur_model_name,
                            ))
                            c2.commit()
                            cur2.close()
                            c2.close()
                            cycle_seq_today = candidate_seq
                            print(f"{tag} #{cycle_seq_today} "
                                  f"CT={ct:.2f}s shift={shift}", flush=True)
                            # 2026-05-29 — Share this pulse timestamp with the
                            # main collector so _update_status can use sub-
                            # machine activity as a "line is alive" witness
                            # (SUB-MACHINE TRUTH OVERRIDE).  GIL-safe atomic
                            # float assignment; readers tolerate ms drift.
                            self._last_submachine_pulse_ts = time.time()
                        except Exception as e:
                            print(f"{tag} insert failed (seq held at "
                                  f"{cycle_seq_today}): {e}", flush=True)
                        last_edge_ts = now_ts

            # Semi-Auto capture now lives in _sa_capture() (defined above the
            # loop) so BOTH counting modes can run it — see the note there.
            _sa_capture()

            last_bit = cur_bit
            time.sleep(0.1)   # 100 ms — catch brief pulses reliably

        if plc:
            try: plc.close()
            except: pass
        print(f"{tag} poller stopped", flush=True)

    # ----------------------------------------------------------
    # MAIN RUN LOOP
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # LIVE STATUS TABLE  (operator health board, 2026-05-31)
    # ----------------------------------------------------------
    #   One aligned row per machine (FI main + every sub) printed every
    #   ~15 s so the operator can eyeball "sab shi h ya nhi" at a glance.
    #   Columns: time | machine | PLC | DB | register | value | status |
    #            plan | actual | +/-CT | model | video | loadcell | part.
    #   OBSERVE-ONLY: reads existing engine/sub state, opens its own short
    #   DB reads, runs in its own daemon thread -> never perturbs counting,
    #   breaks, semi-auto or the 30 ms pulse cadence.  Pure ASCII output so
    #   it can never raise UnicodeEncodeError on a cp1252 Windows console.
    @staticmethod
    def _tick(flag) -> str:
        return "[x]" if flag else "[ ]"

    def _latest_sub_row(self, sub_id, shift=None) -> dict:
        """Best-effort latest row for one sub from mes_submachine_ct_log
        (today, scoped to the current shift when given so ACTUAL/part reflect
        THIS shift, not a stale earlier-today row).  Throwaway connection —
        never shares the main cursor."""
        try:
            c = _db_conn(); cur = c.cursor()
            if shift and not str(shift).startswith("GAP"):
                cur.execute(
                    "SELECT cycle_seq, ct_seconds, part_code, model_name, ts_end "
                    "FROM mes_submachine_ct_log "
                    "WHERE sub_plc_id=%s AND record_date=CURRENT_DATE "
                    "  AND shift_name=%s "
                    "ORDER BY ts_end DESC NULLS LAST LIMIT 1",
                    (sub_id, shift))
            else:
                cur.execute(
                    "SELECT cycle_seq, ct_seconds, part_code, model_name, ts_end "
                    "FROM mes_submachine_ct_log "
                    "WHERE sub_plc_id=%s AND record_date=CURRENT_DATE "
                    "ORDER BY ts_end DESC NULLS LAST LIMIT 1",
                    (sub_id,))
            r = cur.fetchone()
            cur.close(); c.close()
            if r:
                return {"seq": r[0], "ct": r[1], "part": r[2],
                        "model": r[3], "ts": r[4]}
        except Exception:
            pass
        return {}

    def _print_status_table(self) -> None:
        now      = datetime.now()
        ideal_ct = float(self.cfg.get("ideal_ct") or 0.0)
        shift    = getattr(self, "_cur_shift", None)

        # PLAN mirrors the engine heartbeat EXACTLY (shift-to-now realtime
        # plan, capped at shift total) by reading the value it stashed, so the
        # table and the "Plan:" heartbeat never disagree.  ACTUAL = ok_shift
        # (same as the heartbeat's "OK:").
        plan   = int(getattr(self, "_last_plan_display", 0) or 0)
        actual = int(getattr(self, "ok_shift", 0) or 0)

        # CT  = the ACTUAL cycle time of the last completed cycle ("is cycle
        #       me mera ct kitna aaya").
        # SPD = the engine's running SPEED-LOSS accumulator — operator spec:
        #       cycle CT > set CT  ->  add (ct - set) to speed loss;
        #       cycle CT < set CT  ->  subtract (set - ct), floored at 0.
        #       That is exactly CycleTimeTracker.speed_loss, fed on every OK
        #       pulse (register mode included), so we read it straight.
        _ctt     = getattr(self, "ct", None)
        last_ct  = "-"
        spd_loss = "-"
        try:
            if _ctt is not None:
                _cl = getattr(_ctt, "cycle_times", []) or []
                if _cl:
                    last_ct = f"{float(_cl[-1]):.1f}s"
                spd_loss = f"{float(getattr(_ctt, 'speed_loss', 0.0) or 0.0):.1f}s"
        except Exception:
            pass

        # "video saved" tick — a clip webhook landed within the last few
        # cycles AND the line is running (idle line => no parts => n/a, "-").
        running = (getattr(self, "_cur_status", 0) == 1)
        _vts    = getattr(self, "_last_video_saved_ts", 0.0) or 0.0
        _vfresh = (time.time() - _vts) <= max(2.5 * ideal_ct, 45.0)
        vid_box = self._tick(_vfresh) if running else " - "

        main_reg = (self.cfg.get("ok_data_register")
                    or self.cfg.get("ok_bit") or "-")
        main_val = getattr(self, "_last_ok_reg_value", None)
        main_val = "-" if main_val is None else str(main_val)
        # 2026-06-02 — NG read shown alongside OK (operator: "ng ko bhi har
        # baar read kar, har machine pe").  NGREG = NG register/bit address,
        # NGVAL = live NG register value (held stable by the junk-drop guard).
        main_ng_reg = (self.cfg.get("ng_data_register")
                       or self.cfg.get("ng_bit") or "-")
        # Show the CUMULATIVE NG count (ng_shift), not the raw register read —
        # D102 bounces (junk-low), but the count must read correct everywhere.
        main_ng_val = getattr(self, "ng_shift", None)
        main_ng_val = "-" if main_ng_val is None else str(main_ng_val)

        H = ("TIME", "MACHINE", "PLC", "DB", "REG", "VALUE", "NGREG", "NGVAL",
             "STATUS", "PLAN", "ACTUAL", "CT", "SPDLOSS", "MODEL", "VID",
             "PARTCODE")

        def _row(t, name, plc, db, reg, val, ngreg, ngval, status, pl, ac, ct,
                 spd, model, vid, part):
            return (f"{t:<8.8} {name:<16.16} {plc:^4} {db:^4} {reg:<6.6} "
                    f"{val:>8.8} {ngreg:<6.6} {ngval:>6.6} {status:<9.9} "
                    f"{pl:>5.5} {ac:>6.6} {ct:>7.7} {spd:>8.8} {model:<12.12} "
                    f"{vid:^4} {part:<14.14}")

        bar = "=" * 138
        sep = "-" * 138
        out = ["", bar,
               f"  COLLECTOR STATUS  |  {self.cfg.get('line_name','?')}"
               f"  |  {now.strftime('%Y-%m-%d %H:%M:%S')}"
               f"  |  shift={shift or '-'}",
               sep, _row(*H), sep]

        # FI main row — fully live from engine state
        out.append(_row(
            now.strftime("%H:%M:%S"),
            self.cfg.get("line_name", "FI-MAIN"),
            self._tick(getattr(self, "_plc_ok", False)),
            self._tick(getattr(self, "_db_ok", False)),
            str(main_reg), main_val,
            str(main_ng_reg), main_ng_val,
            getattr(self, "_cur_status_name", "-") or "-",
            str(plan), str(actual), last_ct, spd_loss,
            getattr(self, "_cur_model_name", "-") or "-",
            vid_box,
            getattr(self, "_cur_part_code", "") or "-",
        ))

        # sub-machine rows — plc_ok from live shared dict, value/part from DB
        for sub in (self.submachines or []):
            sid    = sub.get("id")
            st     = self._machine_status.get(sid) or {}
            plc_b  = self._tick(st.get("plc_ok")) if ("plc_ok" in st) else " ? "
            reg    = (sub.get("ok_data_register") or sub.get("count_bit") or "-")
            last   = self._latest_sub_row(sid, shift)
            # VALUE = live register/bit state the sub-poller published; fall
            # back to the last logged shift seq until the first publish lands.
            live_v = st.get("val")
            if live_v is not None:
                sval = str(live_v)
            else:
                _s   = last.get("seq")
                sval = "-" if _s is None else str(_s)
            # ACTUAL = this shift's logged count (separate from the live VALUE).
            _act   = last.get("seq")
            sact   = "-" if _act is None else str(_act)
            # CT = the last logged cycle's actual ct_seconds for this sub.
            # SPDLOSS is engine-tracked only for FI main (no per-sub
            # accumulator), so subs show "-".
            _sct   = last.get("ct")
            try:
                sct = "-" if _sct is None else f"{float(_sct):.1f}s"
            except Exception:
                sct = "-"
            spart  = last.get("part") or "-"
            smodel = last.get("model") or (getattr(self, "_cur_model_name", "-") or "-")
            # NG read for this sub (where an NG register/bit is configured;
            # else "-").  ng_val is the live value the sub-poller published.
            sng_reg = (sub.get("ng_data_register") or sub.get("ng_bit") or "-")
            _ngv    = st.get("ng_val")
            sng_val = "-" if _ngv is None else str(_ngv)
            out.append(_row(
                now.strftime("%H:%M:%S"),
                sub.get("machine_name") or f"sub_{sid}",
                plc_b,
                self._tick(getattr(self, "_db_ok", False)),
                str(reg), sval,
                str(sng_reg), sng_val,
                getattr(self, "_cur_status_name", "-") or "-",
                "-", sact, sct, "-",
                smodel, " - ", spart,
            ))
        out.append(bar)
        print("\n".join(out), flush=True)

    def _status_table_loop(self) -> None:
        """Dedicated daemon thread: render the status board every ~15 s.
        Wrapped so a render error can never kill the thread or the engine."""
        time.sleep(8)   # let the boot banner / first poll print first
        while True:
            try:
                self._print_status_table()
            except Exception as _e:
                try:
                    print(f"[STATUS-TABLE] render skipped: {_e}", flush=True)
                except Exception:
                    pass
            time.sleep(15)

    def run(self):
        print(f"\n{'='*60}")
        print(f"  {self.cfg['line_name']} — Collector Engine")
        print(f"{'='*60}")
        print(f"  PLC   : {self.cfg['plc_ip']}:{self.cfg['plc_port']}")
        print(f"  Table : {self.cfg['table_name']}")
        print(f"  Ctrl+C to stop")
        print(f"{'='*60}\n")

        self._connect_plc()
        self._connect_db()
        self._last_db_check = time.time()

        # Launch sub-machine pollers — one thread per sub from initial DB snapshot
        for _sub in self.submachines:
            self._spawn_sub_thread(_sub)

        # Background watcher: picks up newly-added / removed / edited
        # sub-machines from mes_plc_configs without needing a restart.
        threading.Thread(
            target=self._reload_subs_loop,
            daemon=True,
            name="sub-reloader",
        ).start()
        print(
            "[ENGINE] Sub-machine reloader started — "
            "AdminPanel adds/edits/deletes go live within ~30 s without restart",
            flush=True,
        )

        # ── Poka-yoke / sensor check thread (2026-05-12 fix) ─────────
        # Each PY-check pass does 20-40 sequential PLC bit reads
        # (~30-50 ms each = 1-2 s total).  When this ran inline in the
        # main loop, the 30 ms pulse-poll cadence was killed for that
        # entire window → L108/L109 rising edges missed → "OK count
        # 222 vs machine counter 650" symptom.
        #
        # Solution: run PY checks in their own thread with its own
        # PLC connection (Mitsubishi Q03/Q06 supports 4-8 concurrent
        # MC4E TCP clients — we use 1 main + this 1 PY + sub-machines
        # are on different IPs, so ≤3 connections on the main PLC).
        # If the dedicated connection fails to open, the thread silently
        # exits and we lose nothing — bypass detection just won't run.
        threading.Thread(
            target=self._py_check_loop,
            daemon=True,
            name="py-check",
        ).start()
        print(
            "[ENGINE] PY/sensor check thread started — runs independently "
            "of main pulse poll loop, can no longer starve L108/L109 reads",
            flush=True,
        )

        # ── Live status-table thread (2026-05-31) ────────────────────
        # Observe-only per-machine health board every ~15 s.  Own thread +
        # own short DB reads -> never perturbs the pulse cadence/counting.
        threading.Thread(
            target=self._status_table_loop,
            daemon=True,
            name="status-table",
        ).start()
        print(
            "[ENGINE] Status-table thread started — per-machine health board "
            "prints every ~15 s (observe-only, zero count impact)",
            flush=True,
        )

        # One-time migration: add current_shift_row_id to mes_lines if missing
        if self._db_ok:
            try:
                _mc = self._db.cursor()
                _mc.execute(
                    "ALTER TABLE mes_lines ADD COLUMN IF NOT EXISTS current_shift_row_id INTEGER"
                )
                self._db.commit()
                _mc.close()
            except Exception:
                self._safe_rollback()

        while True:
            try:
                now = time.time()

                # Reconnect if lost — aggressive 2-sec retry instead
                # of the old "1 second window every 30 sec" pattern
                # which gave a 30-sec dead window on every PLC blip.
                if not self._plc_ok:
                    if now - getattr(self, "_last_reconnect_try", 0) >= 2:
                        self._last_reconnect_try = now
                        if self._connect_plc():
                            died = getattr(self, "_plc_died_at", now)
                            print(f"[PLC] Reconnected after {now - died:.1f}s")
                if not self._db_ok and now % 10 < 1:
                    self._ensure_db_connection()

                # 2026-07-16 — hot-reload plan/slot/model config every 60s so
                # admin plan/hourly/model edits apply WITHOUT a collector
                # restart.  Placed at the loop TOP (unconditional) so a no-
                # shift / GAP / reconnect state can never skip it.  Only swaps
                # self.cfg config values — never touches runtime counters.
                self._maybe_reload_line_config()

                # ── Auto-clear expired OT ──
                # Clear OT only once its window has GENUINELY PASSED, so the
                # dashboard returns to normal and the shift transitions to GAP.
                # 2026-06-19 BUGFIX: the old guard `not _is_in_ot_window` also
                # matched the time BEFORE the window starts, so arming OT during
                # the running shift (the normal case) was auto-cleared on the
                # very next poll (~2 s) — operator: "OT daalte hi 2 sec me off".
                # Fix: when not in the window, _get_current_shift() returns the
                # raw clock shift; only when that is no longer the OT shift are
                # the shift AND its OT window both over → safe to clear.  Arming
                # OT mid-shift now sticks: it kicks in at shift end and auto-
                # clears only after the OT window truly ends.  (Main shift logic
                # untouched — this only narrows WHEN the OT flag is cleared.)
                _ot = self._check_ot_active()
                _ot_expired = False
                if _ot and not self._is_in_ot_window(_ot):
                    _clk_shift, _ = self._get_current_shift()
                    _ot_expired = (_clk_shift != _ot)
                if _ot and _ot_expired:
                    try:
                        _oc = self._db.cursor()
                        _oc.execute(
                            "UPDATE mes_lines SET ot_active_shift = NULL WHERE id = %s",
                            (self.cfg["line_id"],)
                        )
                        self._db.commit()
                        _oc.close()
                        print(f"[OT] Auto-cleared expired OT for shift {_ot}")
                    except Exception:
                        self._safe_rollback()

                # Shift detection
                shift_name, record_date = self._get_current_shift()
                # 2026-05-29 - Track shift's record_date so midnight-
                # crossing shifts (e.g. B 18:30-03:15) write all pulses
                # under the shift START date, not pulse wall-clock date.
                # Without this, Shift B splits across 2 record_dates and
                # frontend chart shows discontinuity at midnight.
                self._cur_shift_record_date = record_date

                # ── Shift change ──
                if shift_name and shift_name != self._cur_shift:
                    print(f"\n[SHIFT] {self._cur_shift or 'None'} -> {shift_name}")
                    # Don't mark shift completed if OT is about to bring it back.
                    # Only mark completed if the OLD shift is NOT the OT active shift.
                    ot_active = self._check_ot_active()
                    should_complete = (ot_active != self._cur_shift) if self._cur_shift else True
                    if self._shift_id and self._db_ok and should_complete:
                        try:
                            cur = self._db.cursor()
                            cur.execute(
                                f"UPDATE {self.cfg['table_name']} "
                                f"SET is_shift_completed=true WHERE id=%s",
                                (self._shift_id,))
                            self._db.commit()
                            cur.close()
                        except Exception:
                            self._safe_rollback()

                    # 2026-05-30 — AUTO L110 shift-reset pulse trigger.  A
                    # REAL shift just ended (should_complete = not merely OT
                    # folding it back).  Bump the shift-reset epoch so every
                    # register machine fires ONE L110 pulse (=1 ~3s ->0) on
                    # its own connection — FI in this loop, subs in their
                    # threads.  Skip GAP/None old-shift (a fresh boot or a
                    # between-shift idle must not pulse).  Semi-auto and
                    # bit-mode machines are excluded downstream by the
                    # count_mode=register AND shift_reset_bit guard.
                    _old_shift = self._cur_shift
                    if (should_complete and _old_shift
                            and not _old_shift.startswith("GAP")):
                        self._shift_reset_epoch += 1
                        print(f"[SHIFT-RESET] shift {_old_shift} ended -> "
                              f"L110 pulse epoch {self._shift_reset_epoch} "
                              f"(register machines reset; semi-auto/bit "
                              f"untouched).", flush=True)

                    # 2026-06-16 — ALSO pulse L110 at the START of each real
                    # shift (entering a real shift from a GAP), not only at its
                    # end.  Operator: register ko shift START pe bhi zero karo,
                    # taaki pichhli shift ka count kabhi nayi me bleed na ho —
                    # 12-Jun A=3596 carry-over ka root cause yahi tha (end-reset
                    # miss ho gaya to A ne pichhla count inherit kar liya).  A
                    # start pulse is a guaranteed SECOND register-zero before the
                    # new shift counts.  Guard on _old_shift being a GAP so a
                    # fresh boot (_old_shift=None) NEVER fires a spurious pulse,
                    # and a real->GAP end never double-fires here.  The archive
                    # at this edge snapshots the GAP (~0) and is GREATEST-upserted
                    # => harmless; the real point is the register zero.
                    # 2026-07-03 — but NOT when this GAP->real edge is an OT
                    # RESUMPTION of the same shift.  OT continues the pre-OT
                    # register total (the completed row is reopened with its
                    # existing counts), so re-zeroing the register here makes
                    # the mirror read a e.g. 1842->0 "drop", honor it (L110
                    # active) and force-exact-write ok_count=0 onto the reopened
                    # row — the exact root cause of ync 07-02 A ok_count=0.
                    # A genuine (non-OT) GAP->A start still pulses as before
                    # (_check_ot_active() returns "" != "A").
                    if (_old_shift and _old_shift.startswith("GAP")
                            and shift_name and not shift_name.startswith("GAP")
                            and self._check_ot_active() != shift_name):
                        self._shift_reset_epoch += 1
                        print(f"[SHIFT-RESET] shift {shift_name} starting -> "
                              f"L110 pulse epoch {self._shift_reset_epoch} "
                              f"(start-of-shift register zero; semi-auto/bit "
                              f"untouched).", flush=True)

                    self._cur_shift     = shift_name
                    self._last_ok_state = 0
                    self._last_ng_state = 0

                    # 2026-05-28 - Reset raw_cycle_seq per shift.  Use
                    # the shift's record_date (handles midnight cross
                    # for Shift B).  Without this, Shift B post-midnight
                    # would re-start cycle_seq from 1 every restart.
                    try:
                        _sh = shift_name if not shift_name.startswith("GAP") else "GAP"
                        _rc = self._db.cursor()
                        _rc.execute(
                            f"SELECT COALESCE(MAX(cycle_seq), 0) "
                            f"FROM {self.cfg['table_name']}_ct_log "
                            f"WHERE record_date=%s AND shift_name=%s",
                            (record_date, _sh,)
                        )
                        _max = _rc.fetchone()[0] or 0
                        self._raw_cycle_seq = int(_max)
                        _rc.close()
                        print(f"[SHIFT] raw_cycle_seq reset for shift "
                              f"{shift_name} (date={record_date}): "
                              f"starts from {self._raw_cycle_seq + 1}",
                              flush=True)
                    except Exception as _e:
                        print(f"[SHIFT] cycle_seq reset failed: {_e}", flush=True)
                        self._safe_rollback()

                    if self._db_ok:
                        self._shift_id = self._get_or_create_shift(
                            shift_name, record_date)
                        if self._shift_id:
                            try:
                                _rc = self._db.cursor()
                                _rc.execute(
                                    "UPDATE mes_lines SET current_shift_row_id=%s WHERE id=%s",
                                    (self._shift_id, self.cfg["line_id"]),
                                )
                                self._db.commit()
                                _rc.close()
                            except Exception:
                                self._safe_rollback()

                # ── DB came back mid-shift — register the shift now ──
                if (shift_name and self._db_ok and not self._shift_id):
                    print(f"[SHIFT] DB reconnected mid-shift — registering {shift_name}")
                    self._shift_id = self._get_or_create_shift(
                        shift_name, record_date)
                    if self._shift_id:
                        try:
                            _rc = self._db.cursor()
                            _rc.execute(
                                "UPDATE mes_lines SET current_shift_row_id=%s WHERE id=%s",
                                (self._shift_id, self.cfg["line_id"]),
                            )
                            self._db.commit()
                            _rc.close()
                        except Exception:
                            self._safe_rollback()

                if not shift_name:
                    time.sleep(5)
                    continue

                # Read PLC
                plc = self._read_plc()

                # Final compare-fetch: when configured, fire the Semi-Auto-NG
                # reject on this bit's rising edge (correct cycle).  No-op when
                # blank, so counting and every other line are untouched.
                self._check_fi_fetch()

                # Model change — INSTANT MATCH for valid models only.
                # 2026-05-29 — Per-line validity gate.  D6048 reads
                # share the same TCP socket as part_code/D1001 reads
                # and occasionally return ASCII bleed-through (381,
                # 382, 191, 173, 12336…) that are NOT in this line's
                # mes_model_mappings (line 2 valid set: 9,10,14,15,16).
                # Earlier "instant pass-through" wrote those garbage
                # numbers to ync_dashboard_complete.current_model_number,
                # surfacing on the wallboard as "Model#381" / "Model#382"
                # flapping every few seconds.  Now: only accept models
                # that exist in self.cfg["models"] (loaded from
                # mes_model_mappings WHERE line_id=2).  Anything else
                # is rejected silently — last-known-good model holds.
                # Operator must add the model to admin if a genuinely
                # new one is wired in the PLC.
                m = plc["model"]
                if m != self._cur_model and m > 0:
                    if m in self.cfg["models"]:
                        # 2026-06-01 — DEBOUNCE guard vs D6048 socket bleed.
                        # The model word shares the TCP socket with
                        # part_code/D1001 and intermittently bleeds a
                        # *valid-looking* neighbour value: on line 2 the true
                        # 15 (INR LH) showed up as 16 (OTR) for the odd read,
                        # and the old "instant switch" latched every bleed —
                        # flapping the wallboard model between INR LH and OTR.
                        # The same bleed hits the count registers too, but
                        # those have garbage filters; the model path had none.
                        #
                        # Fix = vote-with-decay + a wall-clock floor.  The TRUE
                        # PLC value is always the plurality of reads, so a
                        # bleed's net evidence hovers near zero and never
                        # reaches _MIN_VOTES; a genuine changeover (new value
                        # dominates the reads) climbs monotonically and commits
                        # once it has also been pending >= _MIN_SECS.  DECAYING
                        # (not zeroing) on each re-sight of the current model
                        # means an occasional stray bleed DURING a real change
                        # can't stall it forever.  Counting / status / video /
                        # part-code paths are all untouched.
                        _MIN_VOTES = 5
                        _MIN_SECS  = 8.0
                        _now = time.time()
                        if self._cur_model not in self.cfg["models"]:
                            # Startup sentinel (init _cur_model = 1, not a real
                            # model) — first valid read commits instantly, same
                            # as before, so boot-time display is unchanged.
                            self._cur_model        = m
                            self._cur_model_name   = self.cfg["models"][m]
                            self._model_candidate  = None
                            self._model_cand_votes = 0
                            print(f"[MODEL] -> {self._cur_model_name} "
                                  f"(D6048={m}, instant switch)")
                        else:
                            if m == getattr(self, "_model_candidate", None):
                                self._model_cand_votes = getattr(
                                    self, "_model_cand_votes", 0) + 1
                            else:
                                # Different value than we were tracking — start
                                # a fresh candidate + dwell clock, hold current.
                                self._model_candidate   = m
                                self._model_cand_votes  = 1
                                self._model_cand_since  = _now
                            if (self._model_cand_votes >= _MIN_VOTES and
                                    _now - getattr(self, "_model_cand_since",
                                                   _now) >= _MIN_SECS):
                                self._cur_model        = m
                                self._cur_model_name   = self.cfg["models"][m]
                                self._model_candidate  = None
                                self._model_cand_votes = 0
                                print(f"[MODEL] -> {self._cur_model_name} "
                                      f"(D6048={m}, confirmed: {_MIN_VOTES}+ "
                                      f"reads over {_MIN_SECS:.0f}s)")
                    else:
                        # Throttle the rejection log so socket bleeds
                        # don't spam the console.
                        _mlast = getattr(self, "_model_reject_last_log", 0)
                        _mcount = getattr(self, "_model_reject_count", 0) + 1
                        self._model_reject_count = _mcount
                        if time.time() - _mlast >= 10:
                            print(f"[MODEL-REJECT] D6048={m} not in "
                                  f"mes_model_mappings for line "
                                  f"{self.cfg['line_id']} "
                                  f"(valid={sorted(self.cfg['models'].keys())}) "
                                  f"— rejected x{_mcount} in last "
                                  f"{time.time() - _mlast:.0f}s, "
                                  f"holding {self._cur_model_name!r}",
                                  flush=True)
                            self._model_reject_last_log = time.time()
                            self._model_reject_count = 0
                elif m == self._cur_model:
                    # Current (committed) model re-seen on the wire — DECAY any
                    # pending candidate's evidence (don't zero it, so a stray
                    # bleed during a real change is tolerated).  Because the
                    # true value is the plurality of reads, an alternating
                    # bleed's candidate can never net up to _MIN_VOTES.
                    if getattr(self, "_model_candidate", None) is not None:
                        self._model_cand_votes = getattr(
                            self, "_model_cand_votes", 0) - 1
                        if self._model_cand_votes <= 0:
                            self._model_candidate  = None
                            self._model_cand_votes = 0

                # Status & loss
                self._update_status(plc["status"])
                self.poka.check_override(plc["status"], self._cur_shift or "")

                # Per-process bit-pulse edge counting (fast poll, ~30 ms).
                # Catches every L108-style rising edge so the 60-s window
                # logged to mes_machine_process_log accumulates the full
                # cycle count.  Word-type processes are skipped here —
                # they're sampled once-per-60s by _sample_machine_processes.
                self._poll_machine_process_pulses()

                # 2026-05-29 - Capture register-mode delta so counter
                # increment matches the actual jump (e.g. D5100 went
                # 42->47 in one poll = +5 OK in one row).  Bit mode
                # always passes delta=1.
                self._pending_ok_delta = int(plc.get("ok_delta") or 1)
                self._pending_ng_delta = int(plc.get("ng_delta") or 1)

                # 2026-05-31 — Graph-point backfill (register mode, one-shot).
                # After a restart/reconnect snap, write a cycle row for every
                # part the PLC counted while we were down so the chart + count
                # never diverge.  Runs BEFORE _update_counts so the DB-max it
                # reads is the pre-restart high; this poll's own +1 (if any)
                # is written by _update_counts and lands ABOVE the backfilled
                # range (no overlap).  Self-guards on shift/GAP/cap/positive;
                # no-op on bit-mode / semi-auto (never gets armed).
                if getattr(self, "_do_reg_backfill_to", None) is not None:
                    self._reg_backfill_after_resync()

                # Count pulses
                new_ok, new_ng = self._update_counts(
                    plc["ok_bit"], plc["ng_bit"])

                # 2026-05-30 — Register-mirror shift rollover.  Reads the
                # per-machine shift_reset_bit (throttled ~1s, separate from
                # the register reads = staggered) and, on its rising edge,
                # archives the closing OK/NG count to mes_shift_count_archive
                # BEFORE the PLC-driven register reset (interlock).  No-op
                # unless count_mode=register AND shift_reset_bit is set.
                #
                # 2026-05-30 — AUTO L110 pulse.  First check the manual test
                # flag (may bump the epoch), then drive the FI L110 bit on
                # its own connection.  The pulse runs BEFORE the archive read
                # below so the archive catches the rising edge inside the same
                # shift-end window.
                self._check_l110_test_flag()
                self._maybe_pulse_fi_shift_reset()
                self._apply_bit_commands()      # MES-queued bits (PY bypass)
                self._maybe_archive_and_reset_shift()

                # 2026-05-23 — SWITCH MODEL (Option C).
                # Each L108 rise = 1 OK row.  Each L109 rise (passing the
                # ladder-echo filter in _update_counts) = 1 NG row.  They
                # are INDEPENDENT — both may fire in the same poll, in
                # which case both rows get written.  Cycles never merge.
                _now = datetime.now()

                # 2026-05-26 — SWITCH MODEL ct_log writes.
                # Each L108 rise → its own OK row.  Each L109-dwell-pass
                # → its own NG row.  Both may fire in the same poll, in
                # which case BOTH rows get written.  Operator: "first
                # pulse of any one bit = one count, not continuous
                # monitor".
                if new_ok > 0:
                    # 2026-05-27 — ct_log OK write MOVED to the raw
                    # L108 rising-edge handler inside _update_counts
                    # (same as NG).  This block now only feeds the
                    # avg/min/max stats panel via `self.ct.on_pulse()`
                    # and clears the chart stash flag.  Counter still
                    # increments here for clean OEE math.
                    self.ct.on_pulse(time.time())
                    ct_s = self.ct.stats()
                    if ct_s["list"]:
                        self.poka.check_cycle_fast(
                            ct_s["list"][-1], self._cur_shift or "")
                    self._raw_ok_already_logged_this_press = False
                    self._last_ct_for_chart_ok = None
                # 2026-05-27 — NG ct_log write MOVED to the raw L109
                # rising-edge handler inside _update_counts.  Skipping
                # the duplicate here that used to fire on counter
                # increment.  The raw handler already appended the row
                # before this point in the same poll iteration.
                if new_ng > 0:
                    # Reset flag so next press starts fresh.  Counter
                    # bumped → counter telemetry, but chart/audit rows
                    # already in flight.
                    self._raw_ng_already_logged_this_press = False
                    self._last_ct_for_chart_ng = None

                if new_ok > 0:
                    self.poka.on_ok_pulse(
                        plc.get("sensor_ok"), self._cur_shift or "")
                    self.poka.on_ok_clears_ng()

                if new_ng > 0:
                    self.poka.on_ng_pulse(self._cur_shift or "")

                # PY / sensor checks no longer run inline — moved to a
                # dedicated thread (_py_check_loop) on collector startup
                # so the main pulse-poll loop stays at full 33 Hz.  The
                # thread holds its own PLC connection, never blocks here.

                # Speed loss check
                if now - self._last_speed_chk >= self.SPEED_CHECK_INTERVAL:
                    in_break, _ = self._is_break()
                    if not in_break and self._cur_status == 1:
                        added = self.ct.check_continuous(now)
                        if added > 0:
                            self._loss["speed"] += added
                    self._last_speed_chk = now

                # Break log + periodic break-config refresh.
                #
                # Admins change zone breaks via Production Admin Panel
                # WHILE the collector is running.  Without this reload
                # the new break window is invisible until tomorrow.
                if now - self._last_break_reload > 60:
                    self._reload_breaks_from_db()
                    self._last_break_reload = now

                in_break, bname = self._is_break()
                if in_break and now - self._last_break_log > 60:
                    print(f"[BREAK] {bname}")
                    self._last_break_log = now

                # Hourly update
                self._update_hourly(new_ok, new_ng)

                # Backfill plans every 30 seconds
                if now - self._last_plan_calc > 30:
                    if (self._shift_id and self._cur_shift
                            and not self._cur_shift.startswith("GAP")):
                        self._backfill_past_slots(self._cur_shift)
                        self._refresh_all_slot_plans()
                    self._last_plan_calc = now

                # Machine-process config reload every 30 s (admin edits go live)
                if now - self._last_process_reload > 30:
                    self._reload_machine_processes()
                    self._last_process_reload = now

                # Machine-process PLC sample + log every 60 s — drives
                # the Process Graphs page's bars/lines.
                if now - self._last_process_sample > 60:
                    self._sample_machine_processes()
                    self._last_process_sample = now

                # Dashboard DB write every 2 seconds
                if now - self._last_db_write > self.DB_UPDATE_INTERVAL:
                    if self._db_ok and self._shift_id:
                        self._write_dashboard()
                    # 2026-06-16 — drain the durable write-buffer once the DB
                    # is back (exactly-once replay via the dedup ledger).
                    if self._db_ok and _QUEUE is not None and _QUEUE.has_pending():
                        self._replay_queue()
                    self._last_db_write = now

                # Console heartbeat — throttled to 15 s (was every 1 s,
                # which buried the real OK/DB-WRITE events in noise).
                if now - self._last_display > 15:
                    working_seconds = self._working_seconds()
                    _disp_scfg   = self.cfg["shifts"].get(self._cur_shift, {})
                    _disp_plan   = _disp_scfg.get("total_plan", 0) if not (self._cur_shift or "").startswith("GAP") else 0
                    planned      = min(_disp_plan, int(working_seconds / self.cfg["ideal_ct"])) if _disp_plan > 0 else 0
                    # Stash for the status-table thread so its PLAN column shows
                    # the SAME number as this heartbeat (no recompute divergence).
                    self._last_plan_display = planned
                    oee          = self._oee()
                    cts          = self.ct.stats()
                    total_l      = sum(self._loss.values())

                    def fmt(s):
                        s = int(s)
                        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

                    in_break, _ = self._is_break()
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] "
                        f"Shift:{self._cur_shift or '---':6s} | "
                        f"{'Break' if in_break else '':8s} | "
                        f"{self._cur_status_name[:10]:10s} | "
                        f"OK:{self.ok_shift:4d} | "
                        f"Plan:{planned:4d} | "
                        f"OEE:{oee['overall']:5.1f}% | "
                        f"Loss:{fmt(total_l)} | "
                        f"CT:{cts['avg']:5.2f}s"
                    )
                    self._last_display = now

                # 2026-05-23 — 10 ms (100 Hz) poll.  Operator: "poll mtt
                # krr listen krr rise ke liye" — MC4E is a request/response
                # protocol so true event subscription isn't available, but
                # tight polling at 100 Hz with retry-once on each read is
                # effectively continuous listening.  Each loop reads four
                # registers (status / ok / ng / model) totaling ~20-40 ms
                # over LAN, so the 10 ms target sleeps to ~0 ms most of
                # the time — the loop body itself is the throttle.  This
                # guarantees we sample more than once during the PLC's
                # multi-hundred-ms L108 HIGH window even under packet
                # retransmits, so no rising edge is ever silently lost.
                time.sleep(0.01)

            except KeyboardInterrupt:
                print("\nStopped by user")
                break
            except Exception as e:
                print(f"\n[ERROR] {type(e).__name__}: {e}")
                time.sleep(2)

        if self._plc:
            try: self._plc.close()
            except: pass
        if self._db:
            try: self._db.close()
            except: pass
        try:
            if hasattr(self, "_lock") and self._lock:
                self._lock.release()
        except Exception:
            pass
        print("Collector stopped")
