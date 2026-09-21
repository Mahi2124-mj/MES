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
        conn.commit()


# ── helpers ──────────────────────────────────────────────────────────────────
def _mail_addrs():
    """Quality recipients — the same Admin → Mail Config list the existing
    bypass mail uses, so there is no second list to maintain."""
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
    to, cc = _mail_addrs()
    people = to + cc
    if not people:
        # No quality address configured (Admin → Mail Config → bypass).  Do NOT
        # stamp the case: leave it unmailed so it goes out for real as soon as
        # an address exists, instead of looking notified when nobody was told.
        print(f"[PYB] {len(cases)} case(s) waiting but no quality mail address "
              f"is configured (Admin → Mail Config → bypass)", flush=True)
        return 0
    sent = 0
    for case in cases:
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
        cur.execute("""SELECT * FROM mes_py_bypass_cases
                        WHERE line_id = ANY(%s)
                          AND (closed_at IS NULL
                               OR detected_at > now() - make_interval(hours => %s))
                        ORDER BY detected_at DESC LIMIT 500""",
                    (list(allowed), hours))
        rows = [dict(r) for r in cur.fetchall()]
    if status:
        rows = [r for r in rows if (r.get("status") or "") == status.upper()]
    open_rows = [r for r in rows if not r.get("closed_at")]
    counts = {"open": len(open_rows),
              "waiting": sum(1 for r in open_rows if r["status"] == "WAITING"),
              "approved": sum(1 for r in rows if r["status"] == "APPROVED"),
              "rejected": sum(1 for r in rows if r["status"] == "REJECTED"),
              "bit_on": sum(1 for r in open_rows if r.get("bit_state"))}
    return {"open": open_rows, "recent": [r for r in rows if r.get("closed_at")],
            "counts": counts, "reminder_min": REMINDER_MIN, "escalate_min": ESCALATE_MIN}


class _BitCfg(BaseModel):
    line_id:     int
    bit_addr:    str = ""
    machine_key: str = ""
    active:      bool = True
    note:        Optional[str] = None


@router.get("/bits")
def list_bits(user=Depends(require_admin)):
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT l.id AS line_id, l.line_name,
                              COALESCE(z.zone_name,'') AS zone_name,
                              b.bit_addr, b.active, b.note, b.updated_by, b.updated_at
                         FROM mes_lines l
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                         LEFT JOIN mes_py_bypass_bits b
                                ON b.line_id = l.id AND b.machine_key = ''
                        WHERE COALESCE(l.is_active, TRUE)
                        ORDER BY z.zone_name NULLS LAST, l.line_name""")
        return {"lines": [dict(r) for r in cur.fetchall()]}


@router.put("/bits")
def set_bit(cfg: _BitCfg, user=Depends(require_admin)):
    _ensure_schema()
    bit = (cfg.bit_addr or "").strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        if not bit:
            cur.execute("DELETE FROM mes_py_bypass_bits WHERE line_id=%s AND machine_key=%s",
                        (cfg.line_id, cfg.machine_key or ""))
        else:
            cur.execute("""INSERT INTO mes_py_bypass_bits
                               (line_id, machine_key, bit_addr, active, note, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (line_id, machine_key) DO UPDATE SET
                               bit_addr = EXCLUDED.bit_addr, active = EXCLUDED.active,
                               note = EXCLUDED.note, updated_by = EXCLUDED.updated_by,
                               updated_at = now()""",
                        (cfg.line_id, cfg.machine_key or "", bit, cfg.active, cfg.note,
                         user.get("username")))
        conn.commit()
    return {"ok": True, "line_id": cfg.line_id, "bit_addr": bit}


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
