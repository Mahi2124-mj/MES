# ───────────────────────────────────────────────────────────────────────
# push.py   (/api/push)   2026-09-02
# ───────────────────────────────────────────────────────────────────────
# Web Push (VAPID) so the Android APK / installed PWA gets NATIVE phone
# notifications + vibration even when the app is closed — used to alert a
# person when a new escalation lands at their level.
#
#   • Frontend registers a service worker, subscribes with the VAPID public
#     key, and POSTs the subscription here (one row per browser/device).
#   • send_to_user(user_id, title, body, url) fans the message out to all of
#     that user's subscriptions via pywebpush; dead subs (404/410) are pruned.
#     Sends run in a background thread so callers never block.
#
# Purely additive — its own table, never touches counting/collector columns.
# ───────────────────────────────────────────────────────────────────────
import os
import json
import threading
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/push", tags=["push"])

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Phase2/
_KEYS_PATH = os.path.join(_HERE, "vapid_keys.json")

try:
    _K = json.load(open(_KEYS_PATH))
    _PRIV = _K["private_key"]
    _PUB = _K["public_key"]
    _SUBJECT = _K.get("subject", "mailto:admin@tbdi.in")
except Exception as e:            # keep the app up even if keys are missing
    _K, _PRIV, _PUB, _SUBJECT = None, None, None, "mailto:admin@tbdi.in"
    print("[push] WARN could not load vapid_keys.json:", e)


# ── schema ──────────────────────────────────────────────────────────────
@once
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_push_subscriptions (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER,
                endpoint   TEXT UNIQUE NOT NULL,
                p256dh     TEXT NOT NULL,
                auth       TEXT NOT NULL,
                user_agent TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_ok    TIMESTAMPTZ,
                fail_count INTEGER NOT NULL DEFAULT 0
            )""")
        # 2026-09-06 — per-user notification inbox. The native Android app's
        # WebView can't do Web Push (no Notification/PushManager API), so it
        # POLLS /api/push/pending for new rows here and fires a LOCAL
        # notification. Every send_to_user() writes here regardless of whether
        # a web-push subscription exists. Additive; never touches counting.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_push_inbox (
                id         BIGSERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                title      TEXT,
                body       TEXT,
                url        TEXT,
                tag        TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_push_inbox_user_time "
                    "ON mes_push_inbox (user_id, created_at)")
        # 2026-09-15 — Inbox page needs read/unread. Additive column.
        cur.execute("ALTER TABLE mes_push_inbox ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ")


# ── core send (threaded fan-out) ─────────────────────────────────────────
def _deliver(sub_row, payload):
    from pywebpush import webpush, WebPushException
    sub_info = {"endpoint": sub_row["endpoint"],
                "keys": {"p256dh": sub_row["p256dh"], "auth": sub_row["auth"]}}
    try:
        webpush(subscription_info=sub_info, data=payload,
                vapid_private_key=_PRIV, vapid_claims={"sub": _SUBJECT}, ttl=600)
        with get_conn() as c:
            c.cursor().execute(
                "UPDATE mes_push_subscriptions SET last_ok=now(), fail_count=0 WHERE id=%s",
                (sub_row["id"],))
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        with get_conn() as c:
            if code in (404, 410):        # subscription gone — prune it
                c.cursor().execute("DELETE FROM mes_push_subscriptions WHERE id=%s", (sub_row["id"],))
            else:
                c.cursor().execute("UPDATE mes_push_subscriptions SET fail_count=fail_count+1 WHERE id=%s", (sub_row["id"],))
    except Exception as ex:
        print("[push] deliver error:", str(ex)[:120])


def _fan_out(subs, payload):
    for s in subs:
        _deliver(s, payload)


def send_to_user(user_id, title, body, url=None, tag=None):
    """Store the notification for the native app to poll AND web-push it to any
    browser/PWA subscriptions.  Non-blocking."""
    if user_id is None:
        return 0
    try:
        _ensure_tables()
        # Inbox row — the native app polls /api/push/pending and fires a LOCAL
        # notification from it (its WebView has no Web Push). Independent of the
        # web-push fan-out below, so it works even without VAPID keys.
        try:
            with get_conn() as conn:
                conn.cursor().execute(
                    "INSERT INTO mes_push_inbox (user_id, title, body, url, tag) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (user_id, title or "TBDI MES", body or "",
                     url or "/my-escalations", tag or "escalation"))
        except Exception as ex:
            print("[push] inbox store error:", str(ex)[:120])
        # Web Push fan-out (real browser / PWA only)
        if not _PRIV:
            return 0
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT id, endpoint, p256dh, auth FROM mes_push_subscriptions WHERE user_id=%s", (user_id,))
            subs = cur.fetchall()
        if not subs:
            return 0
        payload = json.dumps({
            "title": title or "TBDI MES",
            "body":  body or "",
            "url":   url or "/my-escalations",
            "tag":   tag or "escalation",
        })
        threading.Thread(target=_fan_out, args=(subs, payload), daemon=True).start()
        return len(subs)
    except Exception as ex:
        print("[push] send_to_user error:", str(ex)[:120])
        return 0


# ── routing helper (2026-09-15) ───────────────────────────────────────────
# Who gets a line's alert (Inbox notifications). Default: the users ASSIGNED to
# that line (mes_operator_lines) — its operators / leaders / incharges. If none
# are assigned, fall back to admins / plant-heads so the alert is never lost.
# (Operator asked for a sensible default, adjustable later.)
def _line_recipients(line_id):
    ids = set()
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT admin_id FROM mes_operator_lines WHERE line_id=%s", (line_id,))
            for r in cur.fetchall():
                if r.get("admin_id"):
                    ids.add(r["admin_id"])
            if not ids:
                cur.execute("SELECT id FROM mes_admin WHERE role IN ('admin','plant_head')")
                for r in cur.fetchall():
                    ids.add(r["id"])
    except Exception as ex:
        print("[push] _line_recipients error:", str(ex)[:120])
    return ids


def send_to_line(line_id, title, body, url=None, tag=None):
    """Fan an alert to everyone responsible for a line (see _line_recipients)."""
    n = 0
    for uid in _line_recipients(line_id):
        try:
            send_to_user(uid, title, body, url, tag)
            n += 1
        except Exception:
            pass
    return n


# ── endpoints ────────────────────────────────────────────────────────────
@router.get("/vapid-public")
def vapid_public(user=Depends(get_current_user)):
    if not _PUB:
        raise HTTPException(status_code=503, detail="push not configured")
    return {"public_key": _PUB}


class SubKeys(BaseModel):
    p256dh: str
    auth:   str


class SubBody(BaseModel):
    endpoint: str
    keys:     SubKeys


@router.post("/subscribe")
def subscribe(body: SubBody, request: Request, user=Depends(get_current_user)):
    _ensure_tables()
    ua = (request.headers.get("user-agent") or "")[:400]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_push_subscriptions (user_id, endpoint, p256dh, auth, user_agent)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (endpoint) DO UPDATE SET
                user_id    = EXCLUDED.user_id,
                p256dh     = EXCLUDED.p256dh,
                auth       = EXCLUDED.auth,
                user_agent = EXCLUDED.user_agent,
                fail_count = 0
        """, (user.get("id"), body.endpoint, body.keys.p256dh, body.keys.auth, ua))
    return {"ok": True}


@router.get("/pending")
def pending(since: float = 0, user=Depends(get_current_user)):
    """Native-app notification poller. Returns this user's inbox rows created
    AFTER `since` (epoch ms). First call (since=0) returns no items plus the
    server `now`, which the app adopts as its baseline so a backlog is never
    replayed; subsequent polls pass the last `now` back as `since`."""
    import time as _t
    _ensure_tables()
    uid = user.get("id")
    items = []
    if since and since > 0:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute(
                "SELECT id, title, body, url, tag, "
                "(extract(epoch from created_at)*1000)::bigint AS ts "
                "FROM mes_push_inbox "
                "WHERE user_id=%s AND created_at > to_timestamp(%s/1000.0) "
                "ORDER BY created_at ASC LIMIT 50",
                (uid, since))
            items = cur.fetchall()
    return {"items": items, "now": int(_t.time() * 1000)}


# ── Inbox (2026-09-15) ────────────────────────────────────────────────────
# The "My Escalations" page is being reworked into a NOTIFICATIONS INBOX
# (alerts / notifications / reminders — OEE drop, manpower pending, loss line,
# shift compile, etc.). Operator does NOT want the NG shift-end escalations in
# this inbox, so those tags are hidden here.
_INBOX_HIDE_TAGS = ["escalation"]


@router.get("/inbox")
def inbox(limit: int = 60, user=Depends(get_current_user)):
    """This user's notification inbox — every send_to_user() row EXCEPT the NG
    shift-end escalations, newest first, with read/unread."""
    _ensure_tables()
    uid = user.get("id")
    lim = min(max(int(limit), 1), 200)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, title, body, url, tag, "
            "       (extract(epoch from created_at)*1000)::bigint AS ts, "
            "       (read_at IS NOT NULL) AS is_read "
            "FROM mes_push_inbox "
            "WHERE user_id=%s AND COALESCE(tag,'') <> ALL(%s) "
            "ORDER BY created_at DESC LIMIT %s",
            (uid, _INBOX_HIDE_TAGS, lim))
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) AS n FROM mes_push_inbox "
            "WHERE user_id=%s AND read_at IS NULL AND COALESCE(tag,'') <> ALL(%s)",
            (uid, _INBOX_HIDE_TAGS))
        unread = cur.fetchone()["n"]
    return {"items": rows, "unread": unread}


class MarkRead(BaseModel):
    ids: Optional[List[int]] = None
    all: bool = False


@router.post("/inbox/read")
def inbox_read(body: MarkRead, user=Depends(get_current_user)):
    """Mark inbox items read — either a list of ids, or all (body.all=true)."""
    _ensure_tables()
    uid = user.get("id")
    with get_conn() as conn:
        cur = conn.cursor()
        if body.all:
            cur.execute("UPDATE mes_push_inbox SET read_at=now() "
                        "WHERE user_id=%s AND read_at IS NULL", (uid,))
        elif body.ids:
            cur.execute("UPDATE mes_push_inbox SET read_at=now() "
                        "WHERE user_id=%s AND read_at IS NULL AND id = ANY(%s)",
                        (uid, [int(i) for i in body.ids]))
    return {"ok": True}


class UnsubBody(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
def unsubscribe(body: UnsubBody, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        conn.cursor().execute("DELETE FROM mes_push_subscriptions WHERE endpoint=%s", (body.endpoint,))
    return {"ok": True}


@router.post("/test")
def test_push(user=Depends(get_current_user)):
    n = send_to_user(user.get("id"),
                     "TBDI MES — Test",
                     "Test notification. Notifications are working.",
                     url="/my-escalations", tag="test")
    return {"ok": True, "sent_to": n}
