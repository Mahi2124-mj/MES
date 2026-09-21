#!/usr/bin/env python3
"""
_collector_watchdog.py  — MES collector silent-death detector
=============================================================
Problem this catches (2026-07-05): a per-line collector can hang / die
while the dashboard still shows "Live" with hours-old frozen data and NO
alert fires (the singleton-lock heartbeat runs in a separate thread, so the
lock keeps beating even when the main poll loop is dead).

This tool derives "is the collector actually working?" from the ONLY thing
that matters — is fresh data landing in the DB — instead of the lock.

For every provisioned line it compares now() against the latest write in the
line's dashboard table for TODAY.  If the current shift is active (not GAP,
not shift-completed) and the last write is older than --threshold seconds,
the line is flagged STALE (collector hung/dead).

Usage:
    python _collector_watchdog.py                 # one-shot report
    python _collector_watchdog.py --threshold 90  # custom staleness (s)
    python _collector_watchdog.py --loop 60       # re-check every 60 s
    python _collector_watchdog.py --email         # also e-mail on STALE
                                                  #   (SMTP_* + WATCHDOG_TO env)

Read-only: never writes to the DB or touches any collector.  Safe to run
anywhere, anytime, alongside the live system.
"""
import os
import sys
import time
import argparse
from datetime import datetime

# Load Phase2/.env so SMTP_* (+ optional WATCHDOG_TO) are available when this
# runs as its own window from start_everything.bat.  Best-effort — no-op if
# python-dotenv isn't installed or the file is absent.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

# Reuse the canonical DB config (single source of truth — no duplicated creds)
try:
    from database import DB_CONFIG
except Exception:
    DB_CONFIG = {
        "host":     os.getenv("DB_HOST", "192.168.10.210"),
        "port":     int(os.getenv("DB_PORT", "5432") or 5432),
        "database": os.getenv("DB_NAME", "energydb"),
        "user":     os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASS", "tbdi@123"),
        "connect_timeout": 5,
    }

import psycopg2
import psycopg2.extras


def _connect(retries: int = 20, delay: float = 0.4):
    """Connect with a short retry so a transient 'too many clients' burst
    (see the connection-exhaustion note) doesn't make the watchdog itself
    the thing that fails."""
    last = None
    for _ in range(retries):
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = True
            return c
        except psycopg2.OperationalError as e:
            last = e
            if "too many clients" in str(e):
                time.sleep(delay)
                continue
            raise
    raise last


def check(threshold_s: int):
    """Return (stale, ok, unknown) lists of per-line dicts."""
    stale, ok, unknown = [], [], []
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Provisioned lines only (have a dashboard table).
    cur.execute("""SELECT id, line_name, db_table_name, collector_status
                     FROM mes_lines
                    WHERE db_table_name IS NOT NULL AND COALESCE(is_active, true)
                    ORDER BY id""")
    lines = cur.fetchall()
    for ln in lines:
        tbl = ln["db_table_name"]
        rec = {"line_id": ln["id"], "line": ln["line_name"],
               "collector_status": ln["collector_status"]}
        # Table might not exist yet on a half-provisioned line.
        cur.execute("SELECT to_regclass(%s) r", (tbl,))
        if cur.fetchone()["r"] is None:
            rec["reason"] = "dashboard table missing"
            unknown.append(rec)
            continue
        cur.execute(f'''SELECT shift_name,
                               COALESCE(is_shift_completed, false) AS done,
                               EXTRACT(EPOCH FROM (now() - "timestamp")) AS age_s,
                               "timestamp" AS last_write
                          FROM "{tbl}"
                         WHERE record_date = CURRENT_DATE
                         ORDER BY "timestamp" DESC LIMIT 1''')
        row = cur.fetchone()
        if not row:
            rec["reason"] = "no row for today (idle / not started)"
            unknown.append(rec)
            continue
        age = float(row["age_s"] or 0)
        rec.update(age_s=round(age), shift=row["shift_name"],
                   last_write=str(row["last_write"])[:19])
        active = (not row["done"]) and not str(row["shift_name"] or "").startswith("GAP")
        if active and age > threshold_s:
            stale.append(rec)
        else:
            ok.append(rec)
    conn.close()
    return stale, ok, unknown


def _email(stale, threshold_s):
    to = os.getenv("WATCHDOG_TO") or os.getenv("NOTIFY_EMAIL")
    host = os.getenv("SMTP_HOST"); user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASS"); port = int(os.getenv("SMTP_PORT", "587") or 587)
    if not (to and host and user and pw):
        print("[EMAIL] skipped — set SMTP_HOST/SMTP_USER/SMTP_PASS + WATCHDOG_TO")
        return
    import smtplib
    from email.mime.text import MIMEText
    body = "Collector STALE (no DB write) — likely hung/dead:\n\n" + "\n".join(
        f"  Line {s['line_id']} {s['line']}: last write {s.get('last_write')} "
        f"({s.get('age_s')}s ago, shift {s.get('shift')})" for s in stale)
    msg = MIMEText(body)
    msg["Subject"] = f"[MES WATCHDOG] {len(stale)} collector(s) STALE >{threshold_s}s"
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(); s.login(user, pw)
            s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
        print(f"[EMAIL] sent to {to}")
    except Exception as e:
        print(f"[EMAIL] failed: {e}")


def run_once(threshold_s: int, do_email: bool):
    stale, ok, unknown = check(threshold_s)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n===== COLLECTOR WATCHDOG {now}  (stale threshold {threshold_s}s) =====")
    if stale:
        print(f"\n  [STALE/DEAD] ({len(stale)}):")
        for s in stale:
            print(f"     Line {s['line_id']:>2} {s['line']:<14} last write {s['last_write']} "
                  f"= {s['age_s']}s ago  (shift {s['shift']}, mes_lines={s['collector_status']})")
    else:
        print("\n  [OK] no stale collectors")
    if ok:
        print(f"\n  [LIVE] ({len(ok)}): " +
              ", ".join(f"{o['line']}({o.get('age_s','?')}s)" for o in ok))
    if unknown:
        print(f"\n  [IDLE/UNKNOWN] ({len(unknown)}): " +
              ", ".join(f"{u['line']}[{u.get('reason','')}]" for u in unknown))
    if do_email and stale:
        _email(stale, threshold_s)
    return len(stale)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=120,
                    help="seconds without a DB write before a line is STALE (default 120)")
    ap.add_argument("--loop", type=int, default=0,
                    help="re-check every N seconds (0 = one-shot)")
    ap.add_argument("--email", action="store_true",
                    help="e-mail on STALE (needs SMTP_* + WATCHDOG_TO env)")
    a = ap.parse_args()
    if a.loop <= 0:
        sys.exit(1 if run_once(a.threshold, a.email) else 0)
    while True:
        try:
            run_once(a.threshold, a.email)
        except Exception as e:
            print(f"[WATCHDOG] check failed (will retry): {e}")
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
