#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# py_bypass.py — PY bypass approval flow.
#
# Operator ask (2026-09-20): a PY bypass must show on the dashboard within a
# minute and mail the quality team at once; they approve or reject INSIDE the
# mail (no app login).  Approve → a deviation is created automatically.
# Reject → a bit is set on the machine, and that bit goes OFF by itself once
# the PY is OK again.
#
# HOW IT FITS IN — nothing in the poka-yoke module is modified.
#   • The collector already posts every bypass to /api/poka-yoke/events/ingest
#     and auto-acknowledges the event when the PLC publishes the expected value
#     again.  This module only READS mes_poka_yoke_events.
#   • One case per (line, PY) is opened while a bypass is unacknowledged, and
#     closed when it is acknowledged — that is the "PY is OK again" signal.
#   • The machine bit is never written from here: MES queues a command row and
#     the line's own collector writes it on its own PLC session (those PLCs
#     accept a single session, which the collector holds).
#
# TABLES (all created here, additive)
#   mes_py_bypass_cases    one row per bypass case + its decision
#   mes_py_bypass_bits     per line/machine bit address (Admin), blank = off
#   mes_plc_bit_commands   queue the collectors apply on their own session
# ─────────────────────────────────────────────────────────────────────────────
import html as _html
import json
import os
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from jose import jwt
from pydantic import BaseModel

from auth import ALGORITHM, SECRET_KEY, get_current_user, require_admin
from database import dict_cursor, get_conn
from ddl_once import once

router = APIRouter(prefix="/api/py-bypass", tags=["py-bypass"])

LOOP_S        = int(os.environ.get("PYB_LOOP_S", "20"))
REMINDER_MIN  = int(os.environ.get("PYB_REMINDER_MIN", "10"))
ESCALATE_MIN  = int(os.environ.get("PYB_ESCALATE_MIN", "20"))
LOOKBACK_H    = int(os.environ.get("PYB_LOOKBACK_H", "6"))
TOKEN_DAYS    = int(os.environ.get("PYB_TOKEN_DAYS", "14"))
_PUBLIC_BASE  = os.getenv("MES_PUBLIC_BASE", "https://mes.tbdi.in").rstrip("/")
_STARTED      = False


# ── schema ───────────────────────────────────────────────────────────────────
@once
def _ensure_schema():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_py_bypass_cases (
                id              SERIAL PRIMARY KEY,
                event_id        INTEGER,
                line_id         INTEGER NOT NULL,
                line_name       TEXT,
                zone_id         INTEGER,
                zone_name       TEXT,
                py_no           TEXT,
                py_name         TEXT,
                register_addr   TEXT,
                model_bit       INTEGER,
                shift_name      TEXT,
                actual_value    TEXT,
                expected_value  TEXT,
                detected_at     TIMESTAMPTZ NOT NULL,
                status          TEXT NOT NULL DEFAULT 'WAITING',
                decided_by      TEXT,
                decided_at      TIMESTAMPTZ,
                decision_source TEXT,
                deviation_id    INTEGER,
                deviation_no    TEXT,
                bit_addr        TEXT,
                bit_state       SMALLINT NOT NULL DEFAULT 0,
                mail_sent_at    TIMESTAMPTZ,
                reminder_at     TIMESTAMPTZ,
                escalated_at    TIMESTAMPTZ,
                closed_at       TIMESTAMPTZ,
                close_reason    TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        # one OPEN case per line + PY; history stays as closed rows
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_pyb_open
                       ON mes_py_bypass_cases (line_id, py_no) WHERE closed_at IS NULL""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_pyb_detected
                       ON mes_py_bypass_cases (detected_at DESC)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_py_bypass_bits (
                line_id     INTEGER NOT NULL,
                machine_key TEXT    NOT NULL DEFAULT '',
                bit_addr    TEXT    NOT NULL,
                active      BOOLEAN NOT NULL DEFAULT TRUE,
                note        TEXT,
                updated_by  TEXT,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (line_id, machine_key)
            )""")
        #  2026-09-27 — a machine has TWO bits: the STOP bit MES sets when
        #  quality rejects a bypass (stays on until the PY reads OK again) and
        #  a BYPASS bit the operator can turn on for that machine by hand.
        cur.execute("""ALTER TABLE mes_py_bypass_bits
                       ADD COLUMN IF NOT EXISTS bypass_bit_addr TEXT NOT NULL DEFAULT ''""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_py_manual_bypass (
                line_id     INTEGER NOT NULL,
                machine_key TEXT    NOT NULL DEFAULT '',
                is_on       BOOLEAN NOT NULL DEFAULT FALSE,
                reason      TEXT,
                turned_by   TEXT,
                turned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (line_id, machine_key)
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_plc_bit_commands (
                id          SERIAL PRIMARY KEY,
                line_id     INTEGER NOT NULL,
                bit_addr    TEXT    NOT NULL,
                value       SMALLINT NOT NULL,
                reason      TEXT,
                case_id     INTEGER,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                applied_at  TIMESTAMPTZ,
                applied_ok  BOOLEAN,
                attempts    INTEGER NOT NULL DEFAULT 0,
                error       TEXT
            )""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_plc_bit_cmd_pending
                       ON mes_plc_bit_commands (line_id) WHERE applied_at IS NULL""")
        #  2026-09-27 — approval recipients per LINE and per SHIFT (operator:
        #  "py bypass me per line per shift mail id for bypass approval").
        #  shift_name '' = every shift on that line.  Nothing is required here:
        #  a line with no row keeps using the Admin -> Mail Config bypass list,
        #  so existing behaviour is unchanged until someone fills this in.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_py_bypass_mail (
                line_id    INTEGER NOT NULL,
                shift_name TEXT    NOT NULL DEFAULT '',
                to_addrs   TEXT    NOT NULL DEFAULT '',
                cc_addrs   TEXT    NOT NULL DEFAULT '',
                updated_by TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (line_id, shift_name)
            )""")
        conn.commit()


# ── helpers ──────────────────────────────────────────────────────────────────
def _split_addrs(raw):
    out, seen = [], set()
    for e in (raw or "").replace(";", ",").split(","):
        e = e.strip()
        if e and e.lower() not in seen:
            seen.add(e.lower()); out.append(e)
    return out


def _mail_addrs(line_id=None, shift=None):
    """Who approves a bypass, most specific first (2026-09-27).

        line + that shift   →   line, every shift   →   Admin → Mail Config

    The global list stays the fallback, so a line nobody has configured keeps
    behaving exactly as before.
    """
    if line_id is not None:
        try:
            with get_conn() as conn:
                cur = dict_cursor(conn)
                cur.execute("""SELECT to_addrs, cc_addrs FROM mes_py_bypass_mail
                                WHERE line_id=%s AND shift_name IN (%s, '')
                                ORDER BY (shift_name = '') LIMIT 1""",
                            (int(line_id), str(shift or "")))
                r = cur.fetchone()
                conn.rollback()
            if r and _split_addrs(r["to_addrs"]):
                return _split_addrs(r["to_addrs"]), _split_addrs(r["cc_addrs"])
        except Exception:
            pass
    try:
        from routers.poka_yoke import _get_mail_addrs      # read-only import
        to, cc = _get_mail_addrs("bypass")
        return list(to or []), list(cc or [])
    except Exception:
        return [], []


def _smtp_send(subject, html_body, to_list, cc_list=None):
    """The app's own SMTP sender — same credentials and behaviour as every
    other MES mail; nothing new to configure."""
    from routers.quality import _smtp_send as _send    # reuse, do not duplicate
    return _send(subject, html_body, list(to_list or []), list(cc_list or []))


def _make_token(case_id: int, action: str, who: str) -> str:
    tok = jwt.encode(
        {"cid": int(case_id), "act": action, "who": who, "purpose": "py_bypass",
         "exp": int((datetime.utcnow() + timedelta(days=TOKEN_DAYS)).timestamp())},
        SECRET_KEY, algorithm=ALGORITHM)
    return tok.decode() if isinstance(tok, bytes) else tok


def _read_token(tok: str):
    data = jwt.decode(tok, SECRET_KEY, algorithms=[ALGORITHM])
    if data.get("purpose") != "py_bypass":
        raise ValueError("wrong token purpose")
    if data.get("act") not in ("approve", "reject"):
        raise ValueError("bad action")
    return int(data["cid"]), data["act"], (data.get("who") or "")


def _queue_bit(cur, case, value: int, reason: str):
    """Queue a bit write for the line's collector.  Returns the bit or None
    when the line has no bit configured (then nothing is written at all)."""
    cur.execute("""SELECT bit_addr FROM mes_py_bypass_bits
                    WHERE line_id = %s AND active AND COALESCE(bit_addr,'') <> ''
                    ORDER BY machine_key LIMIT 1""", (case["line_id"],))
    row = cur.fetchone()
    bit = (row or {}).get("bit_addr") if isinstance(row, dict) else (row[0] if row else None)
    if not bit:
        return None
    cur.execute("""INSERT INTO mes_plc_bit_commands (line_id, bit_addr, value, reason, case_id)
                   VALUES (%s,%s,%s,%s,%s)""",
                (case["line_id"], bit, int(value), reason[:200], case["id"]))
    return bit


def _case_mail_html(case, approve_url, reject_url, kind="new"):
    head = {"new": "Poka-Yoke bypass detected",
            "reminder": "Reminder — poka-yoke bypass still waiting",
            "escalation": "Escalation — poka-yoke bypass not answered"}[kind]
    colour = {"new": "#b45309", "reminder": "#b45309", "escalation": "#dc2626"}[kind]
    since = case["detected_at"]
    rows = [
        ("Line",        case.get("line_name") or case["line_id"]),
        ("Zone",        case.get("zone_name") or "—"),
        ("Poka-Yoke",   f'{case.get("py_name") or ""} ({case.get("py_no") or ""})'),
        ("Register",    case.get("register_addr") or "—"),
        ("Expected",    case.get("expected_value") or "—"),
        ("Actual",      case.get("actual_value") or "—"),
        ("Shift",       case.get("shift_name") or "—"),
        ("Detected at", since.strftime("%d %b %Y, %H:%M:%S") if hasattr(since, "strftime") else str(since)),
    ]
    tr = "".join(
        f'<tr><td style="padding:6px 10px;color:#64748b;font-size:13px">{_html.escape(str(k))}</td>'
        f'<td style="padding:6px 10px;color:#0f172a;font-size:13px;font-weight:600">'
        f'{_html.escape(str(v))}</td></tr>' for k, v in rows)
    return f"""<!doctype html><html><body style="margin:0;background:#f1f5f9;
      font-family:Arial,Helvetica,sans-serif;padding:24px">
  <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;
              border-radius:14px;overflow:hidden">
    <div style="background:{colour};color:#fff;padding:16px 22px;font-size:17px;font-weight:700">
      {head}</div>
    <div style="padding:18px 22px">
      <table style="width:100%;border-collapse:collapse">{tr}</table>
      <div style="margin:22px 0 8px;text-align:center">
        <a href="{approve_url}" style="display:inline-block;background:#16a34a;color:#fff;
           text-decoration:none;padding:12px 26px;border-radius:9px;font-weight:700;
           font-size:15px;margin:4px">&#10003; Approve bypass</a>
        <a href="{reject_url}" style="display:inline-block;background:#dc2626;color:#fff;
           text-decoration:none;padding:12px 26px;border-radius:9px;font-weight:700;
           font-size:15px;margin:4px">&#10007; Reject bypass</a>
      </div>
      <div style="font-size:12px;color:#64748b;line-height:1.6;margin-top:14px">
        <b>Approve</b> creates a deviation automatically; it closes when the poka-yoke is
        working again.<br>
        <b>Reject</b> sets the configured bit on the machine. That bit switches off by
        itself once the poka-yoke is OK.
      </div>
    </div>
  </div></body></html>"""


def _result_page(title, detail, colour, icon="✓"):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_html.escape(title)}</title></head>
<body style="margin:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;
             display:flex;min-height:100vh;align-items:center;justify-content:center">
  <div style="background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:34px 30px;
              max-width:400px;width:90%;text-align:center">
    <div style="width:64px;height:64px;border-radius:50%;background:{colour};margin:0 auto 16px;
                display:flex;align-items:center;justify-content:center;font-size:34px;color:#fff">{icon}</div>
    <div style="font-size:22px;font-weight:800;color:#0f172a">{_html.escape(title)}</div>
    <div style="font-size:14px;color:#64748b;margin-top:10px">{detail}</div>
    <div style="font-size:12px;color:#94a3b8;margin-top:22px">You can close this tab.</div>
  </div></body></html>"""


# ── worker ───────────────────────────────────────────────────────────────────
def _open_new_cases(cur):
    """A case per (line, PY) while its bypass event is unacknowledged."""
    cur.execute("""
        SELECT e.id, e.line_id, e.detected_at, e.shift_name, e.context_json,
               l.line_name, l.zone_id, COALESCE(z.zone_name,'') AS zone_name
          FROM mes_poka_yoke_events e
          JOIN mes_lines l ON l.id = e.line_id
          LEFT JOIN mes_zones z ON z.id = l.zone_id
         WHERE e.rule_type = 'SENSOR_BYPASS'
           AND NOT COALESCE(e.acknowledged, FALSE)
           AND e.detected_at > now() - make_interval(hours => %s)
         ORDER BY e.id""", (LOOKBACK_H,))
    opened = 0
    for e in cur.fetchall():
        try:
            ctx = json.loads(e.get("context_json") or "{}")
        except Exception:
            ctx = {}
        cur.execute("""
            INSERT INTO mes_py_bypass_cases
                (event_id, line_id, line_name, zone_id, zone_name, py_no, py_name,
                 register_addr, model_bit, shift_name, actual_value, expected_value,
                 detected_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            RETURNING id""",
            (e["id"], e["line_id"], e.get("line_name"), e.get("zone_id"), e.get("zone_name"),
             ctx.get("py_no"), ctx.get("py_name"), ctx.get("register") or ctx.get("registers_all"),
             ctx.get("model_bit"), e.get("shift_name"), ctx.get("actual"), ctx.get("expected"),
             e["detected_at"]))
        if cur.fetchone():
            opened += 1
    return opened


def _mail_cases(cur, kind):
    """kind: new | reminder | escalation.  One personal mail per recipient, so
    the case records WHO approved or rejected."""
    if kind == "new":
        cond, stamp = "mail_sent_at IS NULL", "mail_sent_at"
    elif kind == "reminder":
        cond = ("reminder_at IS NULL AND mail_sent_at IS NOT NULL AND "
                f"mail_sent_at < now() - interval '{REMINDER_MIN} minutes'")
        stamp = "reminder_at"
    else:
        cond = ("escalated_at IS NULL AND mail_sent_at IS NOT NULL AND "
                f"mail_sent_at < now() - interval '{ESCALATE_MIN} minutes'")
        stamp = "escalated_at"
    cur.execute(f"""SELECT * FROM mes_py_bypass_cases
                     WHERE status = 'WAITING' AND closed_at IS NULL AND {cond}
                     ORDER BY detected_at LIMIT 20""")
    cases = cur.fetchall()
    if not cases:
        return 0
    sent = 0
    for case in cases:
        #  2026-09-27 — recipients are resolved PER CASE now, so a line (and a
        #  shift inside it) can have its own approver.  Falls back to the global
        #  Admin → Mail Config bypass list.
        to, cc = _mail_addrs(case.get("line_id"), case.get("shift_name"))
        people = to + cc
        if not people:
            # Nobody configured for this line/shift and no global address.  Do
            # NOT stamp the case: leave it unmailed so it goes out for real as
            # soon as an address exists, instead of looking notified when
            # nobody was told.
            print(f"[PYB] case {case.get('id')} on line {case.get('line_id')} "
                  f"shift {case.get('shift_name')} is waiting but no approver "
                  f"mail address is configured", flush=True)
            continue
        ok = False
        if people:
            subject = (f"[MES] PY bypass — {case.get('line_name') or case['line_id']} · "
                       f"{case.get('py_name') or case.get('py_no') or ''}")
            if kind == "reminder":
                subject = "[Reminder] " + subject
            elif kind == "escalation":
                subject = "[ESCALATION] " + subject
            for who in people:
                try:
                    ap = f"{_PUBLIC_BASE}/api/py-bypass/act?token={_make_token(case['id'], 'approve', who)}"
                    rj = f"{_PUBLIC_BASE}/api/py-bypass/act?token={_make_token(case['id'], 'reject', who)}"
                    _smtp_send(subject, _case_mail_html(case, ap, rj, kind), [who])
                    ok = True
                except Exception as exc:
                    print(f"[PYB] mail to {who} failed: {str(exc)[:90]}", flush=True)
        cur.execute(f"UPDATE mes_py_bypass_cases SET {stamp} = now(), updated_at = now() "
                    f"WHERE id = %s", (case["id"],))
        sent += 1 if ok else 0
    return sent


def _close_cleared(cur):
    """PY OK again = the collector acknowledged the event.  Close the case,
    switch the bit off and close the auto deviation."""
    cur.execute("""
        SELECT c.* FROM mes_py_bypass_cases c
         WHERE c.closed_at IS NULL
           AND (c.event_id IS NULL
                OR EXISTS (SELECT 1 FROM mes_poka_yoke_events e
                            WHERE e.id = c.event_id AND COALESCE(e.acknowledged, FALSE)))""")
    closed = 0
    for case in cur.fetchall():
        if case.get("bit_state"):
            _queue_bit(cur, case, 0, f"PY {case.get('py_no')} OK again — bit off")
        if case.get("deviation_id"):
            cur.execute("""UPDATE mes_quality_deviations
                              SET status = 'CLOSED', closed_at = now(), updated_at = now(),
                                  closure_remarks = COALESCE(closure_remarks,'') ||
                                      'Closed automatically: poka-yoke working again.'
                            WHERE id = %s AND COALESCE(status,'') <> 'CLOSED'""",
                        (case["deviation_id"],))
        cur.execute("""UPDATE mes_py_bypass_cases
                          SET closed_at = now(), close_reason = 'PY OK again',
                              bit_state = 0, updated_at = now(),
                              status = CASE WHEN status = 'WAITING' THEN 'CLEARED' ELSE status END
                        WHERE id = %s""", (case["id"],))
        closed += 1
    return closed


def _tick():
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        opened = _open_new_cases(cur)
        conn.commit()
        closed = _close_cleared(cur)
        conn.commit()
        mailed = _mail_cases(cur, "new")
        conn.commit()
        _mail_cases(cur, "reminder")
        conn.commit()
        _mail_cases(cur, "escalation")
        conn.commit()
    if opened or closed or mailed:
        print(f"[PYB] {opened} new case(s), {mailed} mailed, {closed} closed", flush=True)
    return {"opened": opened, "mailed": mailed, "closed": closed}


def _loop():
    print(f"[PYB] PY bypass approval worker started (every {LOOP_S}s, "
          f"reminder {REMINDER_MIN}m, escalation {ESCALATE_MIN}m)", flush=True)
    while True:
        try:
            _tick()
        except Exception as exc:
            print(f"[PYB] tick error: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        time.sleep(LOOP_S)


def start():
    global _STARTED
    if _STARTED or os.environ.get("PYB_WORKER", "1") == "0":
        return
    _STARTED = True
    threading.Thread(target=_loop, name="py-bypass", daemon=True).start()


# ── decision (from the mail) ─────────────────────────────────────────────────
def _apply_decision(case_id: int, action: str, who: str):
    """Approve → auto deviation.  Reject → queue the machine bit ON.
    Guarded by status='WAITING', so a link works exactly once."""
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_py_bypass_cases WHERE id = %s", (case_id,))
        case = cur.fetchone()
        if not case:
            return {"ok": False, "detail": "This bypass case no longer exists."}
        if case["status"] != "WAITING" or case["closed_at"]:
            return {"ok": False, "already": True, "status": case["status"],
                    "py_name": case.get("py_name"), "line_name": case.get("line_name"),
                    "deviation_no": case.get("deviation_no")}

        out = {"ok": True, "py_name": case.get("py_name"), "line_name": case.get("line_name")}
        if action == "approve":
            from routers.quality import _next_seq_no
            dev_no = _next_seq_no("DEV", "mes_quality_deviations", "dev_no", conn)
            reason = (f"Poka-Yoke bypass approved from mail. PY {case.get('py_no')} "
                      f"{case.get('py_name')} on {case.get('line_name')}; register "
                      f"{case.get('register_addr')}, expected {case.get('expected_value')}, "
                      f"actual {case.get('actual_value')}; detected "
                      f"{case['detected_at']:%d %b %Y %H:%M}.")
            cur.execute("""
                INSERT INTO mes_quality_deviations
                    (dev_no, line_id, line_name, zone_id, zone_name, machine_name,
                     category, initiated_by, initiated_at, reason, requirement,
                     hod_quality, hod_quality_note, status, approved_at)
                VALUES (%s,%s,%s,%s,%s,%s,'POKA-YOKE BYPASS',%s, now(), %s,
                        'Valid until the poka-yoke is working again.', %s,
                        'Approved from bypass mail.', 'APPROVED', now())
                RETURNING id, dev_no""",
                (dev_no, case["line_id"], case.get("line_name"), case.get("zone_id"),
                 case.get("zone_name"), case.get("py_name"), who or "quality (mail)",
                 reason, who or "quality (mail)"))
            dev = cur.fetchone()
            if case.get("bit_state"):
                _queue_bit(cur, case, 0, "Bypass approved after reject — bit off")
            cur.execute("""UPDATE mes_py_bypass_cases
                              SET status='APPROVED', decided_by=%s, decided_at=now(),
                                  decision_source='mail', deviation_id=%s, deviation_no=%s,
                                  bit_state=0, updated_at=now()
                            WHERE id=%s""",
                        (who or "quality (mail)", dev["id"], dev["dev_no"], case_id))
            out.update(status="APPROVED", deviation_no=dev["dev_no"])
        else:
            bit = _queue_bit(cur, case, 1, f"PY {case.get('py_no')} bypass rejected")
            cur.execute("""UPDATE mes_py_bypass_cases
                              SET status='REJECTED', decided_by=%s, decided_at=now(),
                                  decision_source='mail', bit_addr=%s,
                                  bit_state=%s, updated_at=now()
                            WHERE id=%s""",
                        (who or "quality (mail)", bit, 1 if bit else 0, case_id))
            out.update(status="REJECTED", bit=bit)
        conn.commit()
        return out


# ── API ──────────────────────────────────────────────────────────────────────
@router.get("/act", response_class=HTMLResponse)
def act_page(token: str = Query(...)):
    """Landing page for a mail button.  It only RENDERS — mail scanners
    pre-fetch links, so the state change is the POST this page's JS fires."""
    try:
        _cid, act, _who = _read_token(token)
    except Exception:
        return HTMLResponse(_result_page("Link invalid or expired",
                            "Please act from the MES app.", "#dc2626", "✗"),
                            status_code=400)
    verb = "Approve" if act == "approve" else "Reject"
    tok = _html.escape(token)
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{verb} poka-yoke bypass</title></head>
<body style="margin:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;
             display:flex;min-height:100vh;align-items:center;justify-content:center">
  <div id="card" style="background:#fff;border:1px solid #e2e8f0;border-radius:16px;
       padding:34px 30px;max-width:400px;width:90%;text-align:center">
    <div style="width:34px;height:34px;border-radius:50%;border:3px solid #e2e8f0;
         border-top-color:#1e40af;margin:0 auto 14px;animation:s .7s linear infinite"></div>
    <div style="font-size:16px;font-weight:700;color:#0f172a">Recording your decision…</div>
  </div>
  <style>@keyframes s{{to{{transform:rotate(360deg)}}}}</style>
  <script>
    function show(c,i,t,d){{document.getElementById('card').innerHTML=
      '<div style="width:64px;height:64px;border-radius:50%;background:'+c+';margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:34px;color:#fff">'+i+'</div>'+
      '<div style="font-size:22px;font-weight:800;color:#0f172a">'+t+'</div>'+
      '<div style="font-size:14px;color:#64748b;margin-top:10px">'+d+'</div>'+
      '<div style="font-size:12px;color:#94a3b8;margin-top:22px">You can close this tab.</div>';}}
    fetch('/api/py-bypass/act/do',{{method:'POST',headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{token:"{tok}"}})}})
      .then(function(r){{return r.json();}})
      .then(function(j){{
        var line=(j.line_name||'')+' · '+(j.py_name||'');
        if(j.ok && j.status==='APPROVED'){{
          show('#16a34a','\\u2713','Bypass approved',
               line+'<br>Deviation '+(j.deviation_no||'')+' created. It closes when the poka-yoke is OK.');
        }} else if(j.ok && j.status==='REJECTED'){{
          show('#dc2626','\\u2717','Bypass rejected',
               line+(j.bit?('<br>Bit '+j.bit+' is being set on the machine.'):
                     '<br>No bit is configured for this line, so nothing was written.'));
        }} else if(j.already){{
          show('#64748b','\\u2139','Already '+(j.status||'').toLowerCase(),
               line+'<br>This case was already handled.');
        }} else {{
          show('#dc2626','\\u2717','Could not complete', (j.detail||'This link is no longer valid.'));
        }}
      }}).catch(function(){{show('#dc2626','\\u2717','Network error','Please try again.');}});
  </script></body></html>""")


class _ActBody(BaseModel):
    token: str


@router.post("/act/do")
def act_do(body: _ActBody):
    try:
        cid, act, who = _read_token(body.token)
    except Exception:
        raise HTTPException(400, "This link is invalid or expired.")
    return _apply_decision(cid, act, who)


@router.get("/cases")
def list_cases(hours: int = Query(24, ge=1, le=720),
               status: Optional[str] = None,
               user=Depends(get_current_user)):
    """Open + recent cases for the dashboard."""
    _ensure_schema()
    from routers.shift_compile import _accessible_lines
    with get_conn() as conn:
        cur = dict_cursor(conn)
        allowed = {r["id"] for r in _accessible_lines(cur, user)}
        if not allowed:
            return {"open": [], "recent": [], "counts": {}}
        #  2026-09-27 — the case row has no machine of its own; the PY master
        #  carries it (machine_name when filled, else the station code, e.g.
        #  SS_08 = Final Inspection).  Joined so the list can be grouped by
        #  zone -> line -> machine.  LEFT JOIN, so a PY missing from the master
        #  still shows.
        cur.execute("""SELECT c.*,
                              NULLIF(m.machine_name,'') AS machine_name,
                              m.station_code,
                              COALESCE(NULLIF(m.machine_name,''), m.station_code) AS machine_label
                         FROM mes_py_bypass_cases c
                         LEFT JOIN mes_py_master m ON m.py_no = c.py_no
                        WHERE c.line_id = ANY(%s)
                          AND (c.closed_at IS NULL
                               OR c.detected_at > now() - make_interval(hours => %s))
                        ORDER BY c.detected_at DESC LIMIT 500""",
                    (list(allowed), hours))
        rows = [dict(r) for r in cur.fetchall()]
        #  2026-09-27 — a bypass somebody switched on by hand belongs in the
        #  quality list too, otherwise a machine can sit bypassed with nothing
        #  on screen.  Kept as its OWN list so it never distorts the real case
        #  counts.
        cur.execute("""SELECT mb.line_id, mb.machine_key, mb.reason, mb.turned_by,
                              mb.turned_at, l.line_name, z.zone_name,
                              b.bypass_bit_addr
                         FROM mes_py_manual_bypass mb
                         LEFT JOIN mes_lines l ON l.id = mb.line_id
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                         LEFT JOIN mes_py_bypass_bits b
                                ON b.line_id = mb.line_id AND b.machine_key = mb.machine_key
                        WHERE mb.is_on AND mb.line_id = ANY(%s)
                        ORDER BY mb.turned_at DESC""", (list(allowed),))
        manual = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    if status:
        rows = [r for r in rows if (r.get("status") or "") == status.upper()]
    open_rows = [r for r in rows if not r.get("closed_at")]
    counts = {"open": len(open_rows),
              "waiting": sum(1 for r in open_rows if r["status"] == "WAITING"),
              "approved": sum(1 for r in rows if r["status"] == "APPROVED"),
              "rejected": sum(1 for r in rows if r["status"] == "REJECTED"),
              "bit_on": sum(1 for r in open_rows if r.get("bit_state"))}
    counts["manual_bypass"] = len(manual)
    return {"open": open_rows, "recent": [r for r in rows if r.get("closed_at")],
            "manual": manual,
            "counts": counts, "reminder_min": REMINDER_MIN, "escalate_min": ESCALATE_MIN}


# ── approval mail per line + shift ──────────────────────────────────────────
class _MailCfg(BaseModel):
    line_id:    int
    shift_name: str = ""          # "" = every shift on that line
    to_addrs:   str = ""
    cc_addrs:   str = ""


@router.get("/mail")
def get_bypass_mail(user=Depends(get_current_user)):
    """Per line + shift approver addresses, plus the global fallback in use."""
    _ensure_schema()
    from routers.shift_compile import _accessible_lines
    with get_conn() as conn:
        cur = dict_cursor(conn)
        lines = _accessible_lines(cur, user)
        allowed = {r["id"] for r in lines}
        rows = []
        if allowed:
            cur.execute("""SELECT m.*, l.line_name, l.zone_id, z.zone_name
                             FROM mes_py_bypass_mail m
                             LEFT JOIN mes_lines l ON l.id = m.line_id
                             LEFT JOIN mes_zones z ON z.id = l.zone_id
                            WHERE m.line_id = ANY(%s)
                            ORDER BY z.zone_name, l.line_name, m.shift_name""",
                        (list(allowed),))
            rows = [dict(r) for r in cur.fetchall()]
        try:
            cur.execute("""SELECT DISTINCT shift_name FROM mes_shift_configs
                            WHERE COALESCE(shift_name,'') <> '' ORDER BY shift_name""")
            shifts = [r["shift_name"] for r in cur.fetchall()]
        except Exception:
            shifts = []
        conn.rollback()
    g_to, g_cc = _mail_addrs()
    return {"rows": rows,
            "lines": [{"id": r["id"], "line_name": r.get("line_name"),
                       "zone_id": r.get("zone_id"), "zone_name": r.get("zone_name")}
                      for r in lines],
            "shifts": shifts or ["A", "B"],
            "fallback": {"to": g_to, "cc": g_cc}}


@router.put("/mail")
def set_bypass_mail(body: _MailCfg, user=Depends(get_current_user)):
    """Save (or clear) one line+shift row.  Empty To removes the row so that
    line/shift falls back to the next level."""
    if (user.get("role") or "") not in ("admin", "plant_head", "section_incharge",
                                        "production_incharge", "quality_head"):
        raise HTTPException(403, "You are not allowed to change bypass mail settings")
    _ensure_schema()
    sh = (body.shift_name or "").strip().upper()
    to = ", ".join(_split_addrs(body.to_addrs))
    cc = ", ".join(_split_addrs(body.cc_addrs))
    with get_conn() as conn:
        cur = conn.cursor()
        if not to:
            cur.execute("""DELETE FROM mes_py_bypass_mail
                            WHERE line_id=%s AND shift_name=%s""", (body.line_id, sh))
        else:
            cur.execute("""INSERT INTO mes_py_bypass_mail
                             (line_id, shift_name, to_addrs, cc_addrs, updated_by)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (line_id, shift_name) DO UPDATE
                             SET to_addrs=EXCLUDED.to_addrs,
                                 cc_addrs=EXCLUDED.cc_addrs,
                                 updated_by=EXCLUDED.updated_by,
                                 updated_at=now()""",
                        (body.line_id, sh, to, cc, user.get("username")))
        conn.commit()
    return {"ok": True, "line_id": body.line_id, "shift_name": sh,
            "cleared": not to}


# ── full history ────────────────────────────────────────────────────────────
@router.get("/history")
def bypass_history(date_from: Optional[str] = None,
                   date_to:   Optional[str] = None,
                   line_id:   Optional[int] = None,
                   zone_id:   Optional[int] = None,
                   shift:     Optional[str] = None,
                   status:    Optional[str] = None,
                   machine:   Optional[str] = None,
                   q:         Optional[str] = None,
                   page:      int = Query(1, ge=1),
                   page_size: int = Query(200, ge=1, le=1000),
                   user=Depends(get_current_user)):
    """Every bypass ever raised, with the deviation it generated and where that
    deviation stands (2026-09-27, operator: "kis py bypass ka kya deviation
    generate pending ya etc jo bhi complete history").

    `dev_status` is the deviation's OWN status; `outcome` is the one word that
    answers "what happened to this bypass".
    """
    _ensure_schema()
    from routers.shift_compile import _accessible_lines
    where, args = ["c.line_id = ANY(%s)"], []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        allowed = {r["id"] for r in _accessible_lines(cur, user)}
        if not allowed:
            return {"rows": [], "total": 0, "page": page, "page_size": page_size,
                    "counts": {}}
        args.append(list(allowed))
        if date_from:
            where.append("c.detected_at >= %s::date");              args.append(date_from)
        if date_to:
            where.append("c.detected_at < (%s::date + 1)");          args.append(date_to)
        if line_id:
            where.append("c.line_id = %s");                          args.append(int(line_id))
        if zone_id:
            where.append("c.zone_id = %s");                          args.append(int(zone_id))
        if shift:
            where.append("upper(COALESCE(c.shift_name,'')) = %s");   args.append(shift.upper())
        if status:
            where.append("upper(COALESCE(c.status,'')) = %s");       args.append(status.upper())
        if machine:
            where.append("COALESCE(NULLIF(m.machine_name,''), m.station_code) = %s")
            args.append(machine)
        if q and q.strip():
            where.append("(c.py_no ILIKE %s OR c.py_name ILIKE %s OR c.line_name ILIKE %s "
                         "OR c.deviation_no ILIKE %s)")
            args += [f"%{q.strip()}%"] * 4
        w = " AND ".join(where)

        cur.execute(f"""SELECT count(*) AS n FROM mes_py_bypass_cases c
                         LEFT JOIN mes_py_master m ON m.py_no = c.py_no
                        WHERE {w}""", args)
        total = int((cur.fetchone() or {}).get("n") or 0)

        cur.execute(f"""
            SELECT upper(COALESCE(c.status,'')) AS st, count(*) AS n
              FROM mes_py_bypass_cases c
              LEFT JOIN mes_py_master m ON m.py_no = c.py_no
             WHERE {w} GROUP BY 1""", args)
        counts = {r["st"] or "UNKNOWN": int(r["n"]) for r in cur.fetchall()}

        cur.execute(f"""
            SELECT c.id, c.line_id, c.line_name, c.zone_id, c.zone_name,
                   c.py_no, c.py_name, c.register_addr, c.bit_addr, c.bit_state,
                   c.model_bit, c.shift_name, c.actual_value, c.expected_value,
                   c.detected_at, c.status, c.decided_by, c.decided_at,
                   c.decision_source, c.deviation_id, c.deviation_no,
                   c.mail_sent_at, c.reminder_at, c.escalated_at,
                   c.closed_at, c.close_reason,
                   d.status     AS dev_status,
                   d.initiated_by AS dev_initiated_by,
                   d.approved_at  AS dev_approved_at,
                   d.closed_at    AS dev_closed_at,
                   d.deviation_upto_date AS dev_upto_date,
                   NULLIF(m.machine_name,'') AS machine_name,
                   m.station_code,
                   COALESCE(NULLIF(m.machine_name,''), m.station_code) AS machine_label,
                   CASE
                     WHEN upper(COALESCE(c.status,'')) = 'WAITING'  THEN 'Waiting for approval'
                     WHEN upper(COALESCE(c.status,'')) = 'REJECTED' THEN 'Rejected — machine bit set'
                     WHEN c.deviation_id IS NULL
                          AND upper(COALESCE(c.status,'')) = 'CLEARED'
                                                                    THEN 'Cleared on its own — no deviation'
                     WHEN c.deviation_id IS NULL                    THEN 'No deviation generated'
                     WHEN d.id IS NULL                              THEN 'Deviation missing'
                     WHEN d.closed_at IS NOT NULL                   THEN 'Deviation closed'
                     WHEN d.approved_at IS NOT NULL                 THEN 'Deviation approved'
                     ELSE 'Deviation pending'
                   END AS outcome
              FROM mes_py_bypass_cases c
              LEFT JOIN mes_py_master m ON m.py_no = c.py_no
              LEFT JOIN mes_quality_deviations d ON d.id = c.deviation_id
             WHERE {w}
             ORDER BY c.detected_at DESC
             LIMIT %s OFFSET %s""", args + [page_size, (page - 1) * page_size])
        rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "counts": counts}


class _BitCfg(BaseModel):
    line_id:         int
    bit_addr:        str = ""       # STOP bit — set when quality rejects
    bypass_bit_addr: str = ""       # BYPASS bit — turned on by hand
    machine_key:     str = ""       # "" = the whole line
    active:          bool = True
    note:            Optional[str] = None


class _ManualBypass(BaseModel):
    line_id:     int
    machine_key: str = ""
    on:          bool
    reason:      Optional[str] = None


@router.get("/bits")
def list_bits(user=Depends(require_admin)):
    """Every line, each of its machines, and the two bits that machine uses.

    2026-09-27 — this used to return one row per LINE (it only ever read
    `machine_key = ''`).  A line has several machines and the operator wants
    the stop bit and the bypass bit per machine, with the state of each.

    On state: there is **no live read-back of an arbitrary PLC bit** anywhere
    in this system — MES queues a write in `mes_plc_bit_commands` and the
    line's own collector applies it, because the PLC allows one session and
    the collector holds it.  So what is reported here is the truth we actually
    have: the LAST value MES wrote, whether the collector confirmed it, and
    when.  It is labelled that way in the UI rather than dressed up as a live
    reading.
    """
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT l.id AS line_id, l.line_name, l.zone_id,
                              COALESCE(z.zone_name,'') AS zone_name
                         FROM mes_lines l
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE COALESCE(l.is_active, TRUE)
                        ORDER BY z.zone_name NULLS LAST, l.line_name""")
        lines = [dict(r) for r in cur.fetchall()]

        #  Machines of a LINE = its stations in the machine master, the same
        #  way PY Config lists them (/api/py-config/meta/stations): the station
        #  code is parsed out of machine_no (SS_nn for a Seat-Slider line,
        #  RC_nn for a recliner) and the line is matched on its line_code
        #  prefix.  Measured 27-Sep: 11 lines, 81 stations — the PY master's own
        #  station_code only covers zone 1 and only ever says SS_08, which is
        #  why it is not used here.  A line with no stations simply shows the
        #  whole-line row.
        cur.execute("""
            SELECT l.id AS line_id, t.station_code,
                   (array_agg(t.machine_name ORDER BY t.sort_order))[1] AS machine_name,
                   min(t.sort_order) AS seq
              FROM mes_lines l
              JOIN LATERAL (
                   SELECT substring(mm.machine_no from '(SS_[0-9]+|RC_[0-9]+)') AS station_code,
                          mm.machine_name, mm.sort_order
                     FROM mes_machine_master mm
                    WHERE upper(mm.line) LIKE upper(split_part(l.line_code,'-',1)) || '%%'
                      AND mm.machine_no ~ CASE WHEN l.zone_id = 1
                                               THEN '_SS_[0-9]' ELSE '_RC_[0-9]' END
              ) t ON TRUE
             WHERE COALESCE(l.is_active, TRUE) AND t.station_code IS NOT NULL
             GROUP BY l.id, t.station_code
             ORDER BY l.id, min(t.sort_order)""")
        by_line: dict = {}
        for r in cur.fetchall():
            label = (r["machine_name"] or "").strip() or r["station_code"]
            by_line.setdefault(r["line_id"], []).append(
                {"key": r["station_code"], "label": f"{r['station_code']} · {label}"
                                                    if label != r["station_code"] else r["station_code"]})

        cur.execute("SELECT * FROM mes_py_bypass_bits")
        cfg = {(r["line_id"], r["machine_key"] or ""): dict(r) for r in cur.fetchall()}

        cur.execute("SELECT * FROM mes_py_manual_bypass")
        man = {(r["line_id"], r["machine_key"] or ""): dict(r) for r in cur.fetchall()}

        #  Last command MES sent for each (line, bit).
        cur.execute("""SELECT DISTINCT ON (line_id, bit_addr)
                              line_id, bit_addr, value, created_at, applied_at,
                              applied_ok, attempts, error, reason
                         FROM mes_plc_bit_commands
                        ORDER BY line_id, bit_addr, id DESC""")
        last = {(r["line_id"], (r["bit_addr"] or "").upper()): dict(r) for r in cur.fetchall()}

        #  A reject that is still holding its stop bit on.
        cur.execute("""SELECT line_id, py_no, py_name, bit_addr
                         FROM mes_py_bypass_cases
                        WHERE closed_at IS NULL AND bit_state = 1""")
        holding: dict = {}
        for r in cur.fetchall():
            holding.setdefault(r["line_id"], []).append(dict(r))
        conn.rollback()

    def state_of(line_id, bit):
        b = (bit or "").strip().upper()
        if not b:
            return None
        c = last.get((line_id, b))
        if not c:
            return {"bit": b, "known": False,
                    "text": "never written by MES"}
        return {"bit": b, "known": True, "value": int(c["value"]),
                "on": bool(int(c["value"])),
                "applied_ok": c["applied_ok"], "applied_at": c["applied_at"],
                "created_at": c["created_at"], "attempts": c["attempts"],
                "error": c["error"], "reason": c["reason"],
                "text": ("ON" if int(c["value"]) else "off") + (
                    "" if c["applied_ok"] else
                    (" — not yet applied" if c["applied_at"] is None else " — WRITE FAILED"))}

    out = []
    for ln in lines:
        machines = by_line.get(ln["id"]) or []
        rows = []
        for spec in ([{"key": "", "label": "Whole line"}] + machines):
            mk = spec["key"]
            c = cfg.get((ln["id"], mk)) or {}
            m = man.get((ln["id"], mk)) or {}
            rows.append({
                "machine_key":   mk,
                "machine_label": spec["label"],
                "bit_addr":        c.get("bit_addr") or "",
                "bypass_bit_addr": c.get("bypass_bit_addr") or "",
                "active":  c.get("active", True),
                "note":    c.get("note"),
                "updated_by": c.get("updated_by"), "updated_at": c.get("updated_at"),
                "stop_state":   state_of(ln["id"], c.get("bit_addr")),
                "bypass_state": state_of(ln["id"], c.get("bypass_bit_addr")),
                "manual_bypass_on": bool(m.get("is_on")),
                "manual_by": m.get("turned_by"), "manual_at": m.get("turned_at"),
                "manual_reason": m.get("reason"),
            })
        out.append({**ln, "line_id": ln["id"], "machines": rows,
                    "holding": holding.get(ln["id"], [])})
    return {"lines": out,
            "note": ("State is the last value MES wrote and whether the line's "
                     "collector confirmed it — this system has no live read-back "
                     "of an arbitrary PLC bit.")}


@router.put("/bits")
def set_bit(cfg: _BitCfg, user=Depends(require_admin)):
    _ensure_schema()
    bit = (cfg.bit_addr or "").strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        byp = (cfg.bypass_bit_addr or "").strip().upper()
        if not bit and not byp:
            cur.execute("DELETE FROM mes_py_bypass_bits WHERE line_id=%s AND machine_key=%s",
                        (cfg.line_id, cfg.machine_key or ""))
        else:
            cur.execute("""INSERT INTO mes_py_bypass_bits
                               (line_id, machine_key, bit_addr, bypass_bit_addr,
                                active, note, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (line_id, machine_key) DO UPDATE SET
                               bit_addr = EXCLUDED.bit_addr,
                               bypass_bit_addr = EXCLUDED.bypass_bit_addr,
                               active = EXCLUDED.active,
                               note = EXCLUDED.note, updated_by = EXCLUDED.updated_by,
                               updated_at = now()""",
                        (cfg.line_id, cfg.machine_key or "", bit, byp, cfg.active,
                         cfg.note, user.get("username")))
        conn.commit()
    return {"ok": True, "line_id": cfg.line_id, "machine_key": cfg.machine_key or "",
            "bit_addr": bit, "bypass_bit_addr": byp}


@router.post("/manual-bypass")
def manual_bypass(body: _ManualBypass, user=Depends(require_admin)):
    """Turn a machine's BYPASS bit on or off by hand.

    2026-09-27, operator: *"bypass bit bhi taki us machine ka bypass on kr
    saku"*.  MES cannot write the PLC directly (single session, held by the
    line's collector), so this queues the write exactly like a reject does and
    the collector applies it within ~5 s.  The manual bypass is also recorded
    so it shows up in the quality bypass list while it is on.
    """
    _ensure_schema()
    mk = (body.machine_key or "").strip()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT bypass_bit_addr FROM mes_py_bypass_bits
                        WHERE line_id=%s AND machine_key=%s""", (body.line_id, mk))
        r = cur.fetchone()
        bit = ((r or {}).get("bypass_bit_addr") or "").strip().upper()
        if not bit:
            raise HTTPException(400,
                f"No bypass bit is configured for this machine — set it first.")
        cur.execute("""INSERT INTO mes_plc_bit_commands (line_id, bit_addr, value, reason)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (body.line_id, bit, 1 if body.on else 0,
                     f"manual bypass {'ON' if body.on else 'OFF'} by "
                     f"{user.get('username')}" + (f" — {body.reason}" if body.reason else "")))
        cmd_id = (cur.fetchone() or {}).get("id")
        cur.execute("""INSERT INTO mes_py_manual_bypass
                         (line_id, machine_key, is_on, reason, turned_by, turned_at)
                       VALUES (%s,%s,%s,%s,%s, now())
                       ON CONFLICT (line_id, machine_key) DO UPDATE SET
                         is_on = EXCLUDED.is_on, reason = EXCLUDED.reason,
                         turned_by = EXCLUDED.turned_by, turned_at = now()""",
                    (body.line_id, mk, body.on, body.reason, user.get("username")))
        conn.commit()
    return {"ok": True, "bit_addr": bit, "on": body.on, "command_id": cmd_id,
            "detail": (f"{bit} queued {'ON' if body.on else 'OFF'} — the line's collector "
                       f"writes it within about 5 seconds.")}


@router.get("/commands")
def list_commands(line_id: Optional[int] = None, pending: bool = True,
                  user=Depends(require_admin)):
    """What the collectors still have to write (or recently wrote)."""
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""SELECT * FROM mes_plc_bit_commands
                         WHERE (%s IS NULL OR line_id = %s)
                           {'AND applied_at IS NULL' if pending else ''}
                         ORDER BY id DESC LIMIT 200""", (line_id, line_id))
        return {"commands": [dict(r) for r in cur.fetchall()]}
