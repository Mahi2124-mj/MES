"""
routers/pm_mail.py
==================
PM (Preventive Maintenance) reminder mail — SERVER-SIDE (2026-06-17).

Unlike the reference module (which fired reminders from the browser on
Saturdays/Mondays via localStorage), this is a proper backend worker:

  • pm_mail_worker()  — daemon thread (started in main.py).  Every ~30 min:
        - MONDAY  → send "this week" PM reminder (PMs due Mon-Sun this week)
        - SATURDAY→ send "next week" PM reminder
    Idempotent per (type, week-start) via pm_mail_log.  Honors
    pm_mail_config.auto_enabled.
  • POST /api/pm/send-reminder?type=current-week|next-week  — manual send
    button on the PM panel (sends even with 0 due PMs).

Recipient comes from pm_mail_config (set in the PM panel).  SMTP send is
reused from breakdown_mail (_send_email reads SMTP_* from .env).
"""
import time
import threading
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from database import get_conn, dict_cursor
from auth import get_current_user
from routers.breakdown_mail import _send_email, _split_addrs
from ddl_once import once

mail_router = APIRouter(prefix="/api/pm", tags=["pm-mail"])


def _week_window(which: str):
    """Monday-start week.  which = 'current-week' | 'next-week'."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())     # this week's Monday
    if which == "next-week":
        monday = monday + timedelta(days=7)
    return monday, monday + timedelta(days=6)


@once
def _ensure_pm_mail_log():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS pm_mail_log (
            period_key TEXT PRIMARY KEY,
            sent_at    TIMESTAMPTZ DEFAULT NOW(),
            count      INTEGER,
            status     TEXT,
            error      TEXT)""")
        conn.commit()


def _format_pm_reminder(label: str, start: date, end: date, rows: list):
    subject = f"[PM Reminder · {label}] {len(rows)} PM(s) due {start.isoformat()} to {end.isoformat()}"
    if rows:
        body_rows = "".join(
            f"<tr><td style='padding:5px 12px;border-bottom:1px solid #eef2f7;'>{i+1}</td>"
            f"<td style='padding:5px 12px;border-bottom:1px solid #eef2f7;font-weight:700;'>{r.get('machine_name') or r.get('machine_no') or '—'}</td>"
            f"<td style='padding:5px 12px;border-bottom:1px solid #eef2f7;'>{r.get('zone') or '—'} / {r.get('line') or '—'}</td>"
            f"<td style='padding:5px 12px;border-bottom:1px solid #eef2f7;font-family:monospace;'>{r.get('due_date')}</td>"
            f"<td style='padding:5px 12px;border-bottom:1px solid #eef2f7;'>{r.get('status') or 'Pending'}</td></tr>"
            for i, r in enumerate(rows))
        table = (f"<table style='border-collapse:collapse;width:100%;font-size:13px;margin-top:10px;'>"
                 f"<tr style='background:#f1f5f9;'><th style='padding:6px 12px;text-align:left;'>#</th>"
                 f"<th style='padding:6px 12px;text-align:left;'>Machine</th>"
                 f"<th style='padding:6px 12px;text-align:left;'>Zone / Line</th>"
                 f"<th style='padding:6px 12px;text-align:left;'>Due</th>"
                 f"<th style='padding:6px 12px;text-align:left;'>Status</th></tr>{body_rows}</table>")
    else:
        table = "<p style='color:#16a34a;font-weight:700;margin-top:10px;'>No PM pending in this window ✓</p>"
    html = f"""<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <div style="border-left:5px solid #1e40af;padding:18px 22px;background:#fff;">
    <h2 style="margin:0 0 4px;color:#1e40af;">🛠 Preventive Maintenance — {label}</h2>
    <div style="font-size:12px;color:#64748b;">PMs due {start.isoformat()} to {end.isoformat()}</div>
    {table}
    <p style="margin-top:18px;font-size:12px;color:#94a3b8;">Automated PM reminder — Production Monitoring System.</p>
  </div></body></html>"""
    return subject, html


def _auto_enabled() -> bool:
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT auto_enabled FROM pm_mail_config WHERE id=1")
            row = cur.fetchone()
            return bool(row["auto_enabled"]) if row else True
    except Exception:
        return False


def _send_pm_reminder(which: str, *, manual: bool = False) -> dict:
    start, end = _week_window(which)
    label = "This Week" if which == "current-week" else "Next Week"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT recipient, cc FROM pm_mail_config WHERE id=1")
        cfg = cur.fetchone() or {}
        recipient = (cfg.get("recipient") or "").strip()
        cc = (cfg.get("cc") or "").strip()
        cur.execute("SELECT zone, line, machine_no, machine_name, due_date, status "
                    "FROM pm_schedule WHERE due_date BETWEEN %s AND %s AND status<>'Done' "
                    "ORDER BY due_date, machine_name", (start, end))
        rows = cur.fetchall()
    for r in rows:
        if r.get("due_date"):
            r["due_date"] = r["due_date"].isoformat()
    if not recipient:
        return {"sent": False, "reason": "no recipient configured", "count": len(rows)}
    if not rows and not manual:
        return {"sent": False, "reason": "no PM due in window", "count": 0}
    subject, html = _format_pm_reminder(label, start, end, rows)
    _send_email(subject, html, _split_addrs(recipient), _split_addrs(cc))
    return {"sent": True, "count": len(rows),
            "start": start.isoformat(), "end": end.isoformat(), "recipient": recipient}


def _maybe_send(which: str):
    start, _ = _week_window(which)
    key = f"{which}|{start.isoformat()}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pm_mail_log WHERE period_key=%s", (key,))
        if cur.fetchone():
            return                                       # already sent this period
    try:
        res = _send_pm_reminder(which)
        if res.get("sent"):
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO pm_mail_log(period_key,count,status) VALUES(%s,%s,'OK') "
                            "ON CONFLICT (period_key) DO NOTHING", (key, res.get("count", 0)))
                conn.commit()
            print(f"[PM-MAIL] sent {which} reminder — {res.get('count')} PM(s)", flush=True)
    except Exception as e:
        print(f"[PM-MAIL] {which} send failed: {e}", flush=True)


def pm_mail_worker():
    """Daemon: Monday -> this-week reminder, Saturday -> next-week reminder."""
    try:
        _ensure_pm_mail_log()
    except Exception as e:
        print(f"[PM-MAIL] log table ensure failed: {e}", flush=True)
    print("[PM-MAIL] Worker started — Mon=this-week / Sat=next-week reminders", flush=True)
    while True:
        try:
            if _auto_enabled():
                wd = date.today().weekday()              # Mon=0 .. Sun=6
                if wd == 0:
                    _maybe_send("current-week")
                elif wd == 5:
                    _maybe_send("next-week")
        except Exception as e:
            print(f"[PM-MAIL] worker tick error: {e}", flush=True)
        time.sleep(1800)                                 # every 30 min


@mail_router.post("/send-reminder")
def send_reminder(type: str = Query("current-week"),
                  user=Depends(get_current_user)):
    """Manual send from the PM panel (sends even with 0 due PMs)."""
    if type not in ("current-week", "next-week"):
        type = "current-week"
    return _send_pm_reminder(type, manual=True)
