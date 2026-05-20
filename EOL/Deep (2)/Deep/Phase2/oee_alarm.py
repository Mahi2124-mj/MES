"""
oee_alarm.py
============
Background watcher that fires an email when a line's Overall OEE
stays below a configured threshold for a sustained window.

Why it's separate from breakdown_mail
-------------------------------------
breakdown_mail fires on a *binary* event (status = BREAKDOWN).  OEE
drops are *gradual* — a line at 30 % OEE for 15 min is a different
ops problem than a single 5-min breakdown.  This watcher catches the
former.

Per-line config lives in `mes_oee_alarm_config`:
    line_id          int  PRIMARY KEY part
    threshold_pct    float        (default 60)
    sustain_minutes  int          (default 10)
    cooldown_minutes int          (default 60   — don't spam, re-fire only after this gap)
    to_addresses     text
    cc_addresses     text
    is_active        bool
    last_fired_at    timestamp    (book-keeping)

The watcher polls the dashboard table every 30 s, computes per-line
"how long has overall_oee been below threshold continuously", and
fires an email when the sustain window is met.  After firing it
records last_fired_at and refuses to re-fire within cooldown_minutes
so a really bad shift doesn't generate a flood.

SMTP config reuses the SMTP_* env vars used by breakdown_mail.
"""
from __future__ import annotations

import os
import smtplib
import threading
import traceback
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from database import get_conn, dict_cursor


_STOP   = threading.Event()
_THREAD: Optional[threading.Thread] = None

# In-memory: per-line earliest tick at which overall_oee was below
# threshold (carries across DB writes, lost on restart — that's fine,
# the worst case is one delayed alert after a service restart).
_below_since: dict[int, datetime] = {}


def _ensure_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_oee_alarm_config (
                line_id          INTEGER PRIMARY KEY,
                threshold_pct    REAL    NOT NULL DEFAULT 60.0,
                sustain_minutes  INTEGER NOT NULL DEFAULT 10,
                cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                to_addresses     TEXT    NOT NULL DEFAULT '',
                cc_addresses     TEXT    NOT NULL DEFAULT '',
                is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                last_fired_at    TIMESTAMP
            )
        """)
        conn.commit()


def _send(subject: str, html: str, to: list[str], cc: list[str]) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not (smtp_user and smtp_pass):
        print("[OEE-ALARM] SMTP_USER/PASS not configured, skipping send.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to)
    if cc: msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
        srv.ehlo(); srv.starttls(); srv.login(smtp_user, smtp_pass)
        srv.sendmail(smtp_user, to + cc, msg.as_string())


def _tick() -> None:
    """One pass over every active alarm config."""
    try:
        _ensure_table()
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""
                SELECT c.*, l.line_name, l.db_table_name
                  FROM mes_oee_alarm_config c
                  JOIN mes_lines l ON l.id = c.line_id
                 WHERE c.is_active = TRUE
            """)
            cfgs = cur.fetchall()
    except Exception as exc:
        print(f"[OEE-ALARM] DB error loading config: {exc}")
        return

    now = datetime.now()
    for cfg in cfgs:
        line_id   = int(cfg["line_id"])
        threshold = float(cfg["threshold_pct"] or 60)
        sustain   = float(cfg["sustain_minutes"] or 10)
        cooldown  = float(cfg["cooldown_minutes"] or 60)
        table     = cfg["db_table_name"]
        to_list   = [a.strip() for a in (cfg.get("to_addresses") or "").split(",") if a.strip()]
        cc_list   = [a.strip() for a in (cfg.get("cc_addresses") or "").split(",") if a.strip()]
        if not to_list:
            continue

        # Read latest non-GAP row
        try:
            with get_conn() as conn:
                c2 = dict_cursor(conn)
                c2.execute(f"""
                    SELECT id, shift_name, overall_oee, ok_count, ng_count,
                           shift_plan, current_model_name, timestamp
                      FROM {table}
                     WHERE record_date = CURRENT_DATE
                       AND shift_name NOT LIKE 'GAP%'
                     ORDER BY id DESC LIMIT 1
                """)
                row = c2.fetchone()
        except Exception as exc:
            print(f"[OEE-ALARM] read fail line {line_id}: {exc}")
            continue
        if not row:
            _below_since.pop(line_id, None)
            continue

        oee = float(row.get("overall_oee") or 0)
        if oee >= threshold:
            # Recovered — clear the streak so the NEXT drop fires fresh.
            _below_since.pop(line_id, None)
            continue

        # Below threshold — note the start of the streak (if first time).
        if line_id not in _below_since:
            _below_since[line_id] = now
            continue

        # How long has it been below?
        elapsed_min = (now - _below_since[line_id]).total_seconds() / 60.0
        if elapsed_min < sustain:
            continue

        # Sustain window hit.  Check cooldown.
        last_fired = cfg.get("last_fired_at")
        if last_fired and (now - last_fired).total_seconds() / 60.0 < cooldown:
            continue

        # Fire.
        line_name = cfg.get("line_name") or f"Line #{line_id}"
        ok        = int(row.get("ok_count") or 0)
        ng        = int(row.get("ng_count") or 0)
        plan      = int(row.get("shift_plan") or 0)
        shift     = row.get("shift_name") or "—"
        model     = row.get("current_model_name") or "—"

        subject = (f"[MES · OEE drop] {line_name} — Shift {shift} — "
                   f"OEE {oee:.1f}% (target {threshold:.0f}%)")
        html = f"""
        <div style="font-family:Arial;color:#0f172a;border-left:5px solid #d97706;
                    padding:18px 22px;background:#fff;">
          <h2 style="margin:0 0 6px;color:#d97706;">⚠ OEE BELOW TARGET</h2>
          <p style="margin:0 0 12px;color:#475569;font-size:13px;">
            <b>{line_name}</b> overall OEE has been below
            <b>{threshold:.0f}%</b> for the last
            <b>{int(elapsed_min)} minute(s)</b>.
          </p>
          <table style="border-collapse:collapse;font-size:13px;">
            <tr><td style="padding:4px 12px;color:#64748b;">Current OEE</td>
                <td style="padding:4px 12px;font-weight:bold;color:#d97706;">{oee:.1f}%</td></tr>
            <tr><td style="padding:4px 12px;color:#64748b;">Shift</td>
                <td style="padding:4px 12px;">{shift}</td></tr>
            <tr><td style="padding:4px 12px;color:#64748b;">Model</td>
                <td style="padding:4px 12px;">{model}</td></tr>
            <tr><td style="padding:4px 12px;color:#64748b;">Plan / Actual</td>
                <td style="padding:4px 12px;">{plan} / {ok + ng}</td></tr>
            <tr><td style="padding:4px 12px;color:#64748b;">OK / NG</td>
                <td style="padding:4px 12px;color:#059669;">{ok}</td></tr>
            <tr><td style="padding:4px 12px;color:#64748b;">NG</td>
                <td style="padding:4px 12px;color:#dc2626;">{ng}</td></tr>
          </table>
          <p style="margin-top:14px;font-size:11px;color:#94a3b8;">
            Cooldown {cooldown:.0f} min — no re-alert until OEE recovers above target
            for at least one tick, then drops again.
          </p>
        </div>"""

        try:
            _send(subject, html, to_list, cc_list)
            with get_conn() as conn:
                c3 = conn.cursor()
                c3.execute("""UPDATE mes_oee_alarm_config
                                  SET last_fired_at = NOW()
                                WHERE line_id = %s""", (line_id,))
                conn.commit()
            print(f"[OEE-ALARM] Fired for line {line_id} ({line_name}) "
                  f"at OEE={oee:.1f}% below {threshold:.0f}% for {elapsed_min:.0f} min")
        except Exception as exc:
            print(f"[OEE-ALARM] send fail line {line_id}: {exc}")
            traceback.print_exc()


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _tick()
        except Exception as exc:
            print(f"[OEE-ALARM] loop error: {exc}")
        _STOP.wait(30)


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, daemon=True, name="oee-alarm")
    _THREAD.start()
    print("[OEE-ALARM] Worker started — checks every 30 s")
