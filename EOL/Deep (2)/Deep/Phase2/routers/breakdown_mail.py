"""
routers/breakdown_mail.py
=========================
Admin-managed escalation chain for breakdown emails.

Levels are fired by `breakdown_mail_worker` (started in main.py) — this
file only provides CRUD over the `mes_breakdown_mail_levels` table plus
a manual-send / test-send endpoint and a per-breakdown audit log.

Endpoints
---------
GET    /api/breakdown-mails/                 List levels (any user)
POST   /api/breakdown-mails/                 Create level (admin)
PUT    /api/breakdown-mails/{id}             Update level (admin)
DELETE /api/breakdown-mails/{id}             Delete level (admin)
POST   /api/breakdown-mails/{id}/test        Send a test email (admin)
GET    /api/breakdown-mails/log              Audit log (any user)
"""
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/breakdown-mails", tags=["breakdown-mails"])


# ── Models ────────────────────────────────────────────────────────────
class LevelUpsert(BaseModel):
    level_no:      int
    label:         Optional[str] = None
    delay_minutes: int  = 0
    to_addresses:  str  = ""
    cc_addresses:  str  = ""
    is_active:     bool = True


class TestSend(BaseModel):
    """Optional override of who to send the test to.  When omitted the
    test email goes to the level's own configured recipients."""
    to_addresses: Optional[str] = None
    cc_addresses: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────
def _split_addrs(s: Optional[str]) -> List[str]:
    if not s: return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _send_email(subject: str, html: str,
                to_list: List[str], cc_list: List[str]):
    """Synchronous SMTP send.  Reads SMTP_* from .env (same pattern as
    poka_yoke._send_bypass_email_async).  Raises on failure."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not (smtp_user and smtp_pass):
        raise RuntimeError("SMTP credentials not configured in .env (SMTP_USER / SMTP_PASS)")
    if not to_list:
        raise RuntimeError("No To addresses")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_list + cc_list, msg.as_string())


def _format_breakdown_mail(level: dict, br: dict) -> tuple[str, str]:
    """Build (subject, html) for a real breakdown escalation."""
    line   = br.get("line_name") or f"Line #{br.get('line_id')}"
    zone   = br.get("zone_name") or "—"
    shift  = br.get("shift_name") or "—"
    serial = br.get("serial_in_shift") or "—"
    started = br.get("started_at")
    started_s = started.strftime("%Y-%m-%d %H:%M:%S") if started else "—"

    if started:
        elapsed = datetime.utcnow() - started.replace(tzinfo=None) if hasattr(started, 'tzinfo') and started.tzinfo else datetime.utcnow() - started
        elapsed_min = max(0, int(elapsed.total_seconds() // 60))
    else:
        elapsed_min = 0

    label   = level.get("label") or f"Level {level['level_no']}"
    delay_m = level.get("delay_minutes") or 0

    subject = f"[BREAKDOWN · L{level['level_no']}] {line} — {zone} (#{serial}) · {elapsed_min} min"
    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <div style="border-left:5px solid #dc2626;padding:18px 22px;background:#fff;">
    <h2 style="margin:0 0 6px;color:#dc2626;">🚨 BREAKDOWN — Level {level['level_no']}: {label}</h2>
    <div style="font-size:12px;color:#64748b;margin-bottom:10px;">
      Auto-escalation fired {delay_m} minute(s) after breakdown started — line is still down.
    </div>
    <table style="border-collapse:collapse;width:100%;margin-top:8px;font-size:13px;">
      <tr><td style="padding:6px 12px;font-weight:700;background:#f8fafc;width:160px;">Line</td><td style="padding:6px 12px;">{line}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f1f5f9;">Zone</td><td style="padding:6px 12px;">{zone}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f8fafc;">Shift / S.No</td><td style="padding:6px 12px;">{shift} · #{serial}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f1f5f9;">Started</td><td style="padding:6px 12px;font-family:monospace;">{started_s}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f8fafc;">Elapsed</td><td style="padding:6px 12px;font-weight:700;color:#dc2626;">{elapsed_min} min</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f1f5f9;">Reason</td><td style="padding:6px 12px;">{br.get('reason') or '—'}</td></tr>
    </table>
    <p style="margin-top:18px;font-size:13px;color:#64748b;">
      Open the Maintenance Dashboard to triage this breakdown.<br/>
      Automated escalation — Production Monitoring System.
    </p>
  </div>
</body></html>"""
    return subject, html


# ── CRUD ──────────────────────────────────────────────────────────────
@router.get("/")
def list_levels(user=Depends(get_current_user)):
    """List all configured escalation levels (any user)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, level_no, label, delay_minutes,
                   to_addresses, cc_addresses, is_active,
                   created_at, updated_at
              FROM mes_breakdown_mail_levels
             ORDER BY level_no
        """)
        return cur.fetchall()


@router.post("/", status_code=201)
def create_level(body: LevelUpsert, admin=Depends(require_admin)):
    if body.level_no < 1:
        raise HTTPException(400, "level_no must be >= 1")
    if body.delay_minutes < 0:
        raise HTTPException(400, "delay_minutes cannot be negative")
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO mes_breakdown_mail_levels
                    (level_no, label, delay_minutes, to_addresses, cc_addresses, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (body.level_no, body.label, body.delay_minutes,
                  body.to_addresses or "", body.cc_addresses or "", body.is_active))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Create failed (duplicate level_no?): {e}")
    return {"id": new_id, "level_no": body.level_no}


@router.put("/{level_id}")
def update_level(level_id: int, body: LevelUpsert, admin=Depends(require_admin)):
    if body.level_no < 1:
        raise HTTPException(400, "level_no must be >= 1")
    if body.delay_minutes < 0:
        raise HTTPException(400, "delay_minutes cannot be negative")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_breakdown_mail_levels
               SET level_no=%s, label=%s, delay_minutes=%s,
                   to_addresses=%s, cc_addresses=%s, is_active=%s,
                   updated_at=NOW()
             WHERE id=%s
        """, (body.level_no, body.label, body.delay_minutes,
              body.to_addresses or "", body.cc_addresses or "",
              body.is_active, level_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Level not found")
        conn.commit()
    return {"ok": True}


@router.delete("/{level_id}")
def delete_level(level_id: int, admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_breakdown_mail_levels WHERE id=%s", (level_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Level not found")
        conn.commit()
    return {"ok": True}


@router.post("/{level_id}/test")
def send_test(level_id: int, body: TestSend, admin=Depends(require_admin)):
    """Send a test email to verify SMTP + addresses without waiting for a
    real breakdown.  If the body has its own To/Cc, those override the
    level's configured addresses (useful for one-off probes)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, level_no, label, delay_minutes, to_addresses, cc_addresses
              FROM mes_breakdown_mail_levels WHERE id=%s
        """, (level_id,))
        level = cur.fetchone()
        if not level:
            raise HTTPException(404, "Level not found")

    to_list = _split_addrs(body.to_addresses if body.to_addresses is not None else level["to_addresses"])
    cc_list = _split_addrs(body.cc_addresses if body.cc_addresses is not None else level["cc_addresses"])
    if not to_list:
        raise HTTPException(400, "No To addresses set on this level")

    subject = f"[TEST] Breakdown escalation L{level['level_no']} — {level['label'] or '—'}"
    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <div style="border-left:5px solid #2563eb;padding:18px 22px;background:#fff;">
    <h2 style="margin:0 0 8px;color:#2563eb;">📧 Test Email — Breakdown Escalation L{level['level_no']}</h2>
    <p>If you can read this, SMTP + addresses for this level are configured correctly.</p>
    <table style="border-collapse:collapse;font-size:13px;margin-top:8px;">
      <tr><td style="padding:6px 12px;font-weight:700;background:#f8fafc;">Label</td><td style="padding:6px 12px;">{level['label'] or '—'}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f1f5f9;">Delay</td><td style="padding:6px 12px;">{level['delay_minutes']} min after breakdown start</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f8fafc;">To</td><td style="padding:6px 12px;font-family:monospace;">{', '.join(to_list)}</td></tr>
      <tr><td style="padding:6px 12px;font-weight:700;background:#f1f5f9;">Cc</td><td style="padding:6px 12px;font-family:monospace;">{', '.join(cc_list) or '—'}</td></tr>
    </table>
  </div>
</body></html>"""
    try:
        _send_email(subject, html, to_list, cc_list)
    except Exception as e:
        raise HTTPException(500, f"Send failed: {e}")
    return {"ok": True, "to": to_list, "cc": cc_list}


@router.get("/log")
def list_log(breakdown_id: Optional[int] = Query(None),
             limit: int = Query(50, ge=1, le=500),
             user=Depends(get_current_user)):
    """Per-(breakdown, level) send audit log.  Optionally filter to a
    specific breakdown_id."""
    where = "TRUE"
    params: list = []
    if breakdown_id is not None:
        where = "breakdown_id = %s"
        params.append(breakdown_id)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT g.id, g.breakdown_id, g.level_id, g.sent_at, g.status,
                   g.to_addresses, g.cc_addresses, g.error,
                   l.level_no, l.label
              FROM mes_breakdown_mail_log g
              LEFT JOIN mes_breakdown_mail_levels l ON l.id = g.level_id
             WHERE {where}
             ORDER BY g.sent_at DESC
             LIMIT %s
        """, params + [limit])
        return cur.fetchall()


# ══════════════════════════════════════════════════════════════════════
# Background escalation worker
# ══════════════════════════════════════════════════════════════════════
# Polls every 30 s.  For each OPEN breakdown × ACTIVE level, if
#   (NOW - started_at) >= delay_minutes  AND  no log row yet
# → send the email and INSERT into mes_breakdown_mail_log (the unique
# constraint on (breakdown_id, level_id) makes this idempotent).
#
# When the line transitions to RUNNING the breakdown's state flips to
# RESOLVED via the collector — this query filters state='OPEN', so no
# further levels fire for that ticket.

POLL_INTERVAL_SEC = 30


def _worker_tick():
    """One pass — find pending (breakdown, level) pairs and send mails.
    Errors get logged with status='FAIL' so the worker doesn't retry the
    same one forever."""
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            # All pending (breakdown × level) pairs whose delay has elapsed
            # and which haven't been sent yet.
            cur.execute("""
                SELECT b.id           AS breakdown_id,
                       b.line_id, b.zone_id, b.shift_name, b.serial_in_shift,
                       b.started_at, b.reason,
                       l.line_name,
                       z.zone_name,
                       lvl.id            AS level_id,
                       lvl.level_no, lvl.label, lvl.delay_minutes,
                       lvl.to_addresses, lvl.cc_addresses
                  FROM mes_breakdowns b
                  CROSS JOIN mes_breakdown_mail_levels lvl
                  LEFT JOIN mes_lines l ON l.id = b.line_id
                  LEFT JOIN mes_zones z ON z.id = b.zone_id
                  LEFT JOIN mes_breakdown_mail_log g
                         ON g.breakdown_id = b.id AND g.level_id = lvl.id
                 WHERE b.state = 'OPEN'
                   AND lvl.is_active = TRUE
                   AND g.id IS NULL
                   AND b.started_at + (lvl.delay_minutes || ' minutes')::interval <= NOW()
                 ORDER BY b.id, lvl.level_no
            """)
            pending = cur.fetchall()
    except Exception as e:
        print(f"[BD-MAIL] tick query failed: {e}")
        return

    if not pending:
        return

    for row in pending:
        to_list = _split_addrs(row["to_addresses"])
        cc_list = _split_addrs(row["cc_addresses"])

        if not to_list:
            # Nothing to send — log skip so we don't keep retrying.
            _log_send(row["breakdown_id"], row["level_id"], "SKIPPED",
                      to_list, cc_list, "No To addresses")
            continue

        try:
            subject, html = _format_breakdown_mail(row, row)
            _send_email(subject, html, to_list, cc_list)
            _log_send(row["breakdown_id"], row["level_id"], "OK", to_list, cc_list, None)
            print(f"[BD-MAIL] sent L{row['level_no']} for breakdown #{row['breakdown_id']} "
                  f"({row['line_name']}) → {to_list}")
        except Exception as e:
            _log_send(row["breakdown_id"], row["level_id"], "FAIL", to_list, cc_list, str(e))
            print(f"[BD-MAIL] FAIL L{row['level_no']} for breakdown #{row['breakdown_id']}: {e}")


def _log_send(breakdown_id, level_id, status, to_list, cc_list, error):
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO mes_breakdown_mail_log
                    (breakdown_id, level_id, status, to_addresses, cc_addresses, error)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (breakdown_id, level_id) DO NOTHING
            """, (breakdown_id, level_id, status,
                  ", ".join(to_list), ", ".join(cc_list), error))
            conn.commit()
    except Exception as e:
        print(f"[BD-MAIL] log write failed: {e}")


def _worker_loop():
    import time as _time
    while True:
        try:
            _worker_tick()
        except Exception as e:
            print(f"[BD-MAIL] worker error: {e}")
        _time.sleep(POLL_INTERVAL_SEC)


_BD_MAIL_STARTED = False
def _start_worker():
    global _BD_MAIL_STARTED
    if _BD_MAIL_STARTED:
        return
    _BD_MAIL_STARTED = True
    import threading
    t = threading.Thread(target=_worker_loop, name="bd-mail", daemon=True)
    t.start()
    print(f"[BD-MAIL] Worker started — polling every {POLL_INTERVAL_SEC}s")


# Kick off the scheduler when this module is imported (once per process).
_start_worker()
