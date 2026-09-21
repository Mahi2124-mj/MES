"""
ui_timing.py — how long the UI actually made someone wait.

Every page open and every cycle-video open reports the milliseconds it took, so
"the video is slow" stops being an argument and becomes a number with a date on
it.  The Waiting Time page (admin only) reads this back as graphs + stats.

WHAT IS MEASURED
  page   — navigation to first render of a route
  video  — click on a cycle dot to the first frame playing

Both are measured in the BROWSER, because that is where the wait actually
happens: server time alone misses the queueing, the tunnel, and the decode.
The `source` field records where a clip came from (archive / render / cache) so
a slow number can be traced to a cause instead of guessed at.

WRITE PATH
Batched POSTs, fire-and-forget from the client.  This is telemetry: it must
never slow down or break the page it is measuring, so every failure here is
swallowed and the endpoint always answers 200.

RETENTION
Rows are pruned to UI_TIMING_RETAIN_DAYS (default 30).  At a few thousand
samples a day this table stays tiny; the index is on (ts) because every query
here is "recent window, grouped".
"""

import os
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from auth import get_current_user, get_current_user_optional, username_from_token
from database import get_conn, dict_cursor
from ddl_once import once

router = APIRouter(prefix="/api/ui-timing", tags=["ui-timing"])

RETAIN_DAYS = int(os.environ.get("UI_TIMING_RETAIN_DAYS", "30") or 30)

_schema_ready = False


@once
def _ensure_schema() -> None:
    """Create the table on first use.  Idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_ui_timing (
                id         BIGSERIAL PRIMARY KEY,
                ts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
                kind       TEXT         NOT NULL,          -- 'page' | 'video'
                name       TEXT         NOT NULL,          -- route path / machine name
                ms         INTEGER      NOT NULL,
                line_id    INTEGER,
                username   TEXT,
                source     TEXT,                           -- archive | render | cache
                detail     TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_ui_timing_ts   ON mes_ui_timing (ts DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_ui_timing_kind ON mes_ui_timing (kind, ts DESC)")
    _schema_ready = True


class TimingItem(BaseModel):
    kind:   str
    name:   str
    ms:     int
    line_id: Optional[int] = None
    source: Optional[str] = None
    detail: Optional[str] = None


class TimingBatch(BaseModel):
    items: List[TimingItem]
    # 2026-08-14 — sendBeacon cannot set an Authorization header, which is why
    # every row stored before today had username NULL and no per-user reporting
    # was possible.  The client now puts its token here instead.  Optional: wall
    # displays and kiosks still post anonymously, as designed.
    token: Optional[str] = None


@router.post("")
def record(batch: TimingBatch, user=Depends(get_current_user_optional)):
    """Store a batch of samples.  Never fails the caller.

    Anonymous is allowed on purpose: the wall displays and kiosks never log in,
    and their waits are exactly the ones worth knowing about.
    """
    try:
        _ensure_schema()
        uname = (user or {}).get("username") if isinstance(user, dict) else None
        # Header auth wins; fall back to the token the beacon carried in its body.
        if not uname:
            uname = username_from_token(batch.token)
        rows = []
        for it in batch.items[:200]:            # cap a rogue client
            kind = (it.kind or "").strip().lower()
            if kind not in ("page", "video", "dwell"):
                continue
            ms = int(it.ms)
            # 'dwell' is how long a page stayed OPEN, so its ceiling is a whole
            # shift, not a wait.  Anything past 12 h is a tab left open over
            # night, which says nothing about what the person actually did.
            cap = 12 * 3600_000 if kind == "dwell" else 600_000
            if ms < 0 or ms > cap:
                continue
            rows.append((kind, (it.name or "")[:120], ms, it.line_id,
                         uname, (it.source or None), (it.detail or None)))
        if rows:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT INTO mes_ui_timing (kind, name, ms, line_id, username, source, detail)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
        return {"ok": True, "stored": len(rows)}
    except Exception as exc:
        print(f"[UI-TIMING] record skipped: {exc}")
        return {"ok": False}


def _pctile(cur, kind: str, days: int):
    cur.execute("""
        SELECT count(*)                                              AS samples,
               round(avg(ms))                                        AS avg_ms,
               percentile_disc(0.5)  WITHIN GROUP (ORDER BY ms)      AS p50,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY ms)      AS p95,
               max(ms)                                               AS max_ms
        FROM mes_ui_timing
        WHERE kind = %s AND ts > now() - (%s || ' days')::interval
    """, (kind, days))
    return cur.fetchone() or {}


@router.get("/stats")
def stats(days: int = Query(7, ge=1, le=90),
          user=Depends(get_current_user)):
    """Everything the Waiting Time page draws, in one round-trip."""
    _ensure_schema()
    out = {"days": days}
    with get_conn() as conn:
        cur = dict_cursor(conn)

        for kind in ("page", "video"):
            out[f"{kind}_summary"] = _pctile(cur, kind, days)

        # Slowest offenders — what to actually go and fix.
        for kind in ("page", "video"):
            cur.execute("""
                SELECT name,
                       count(*)                                          AS samples,
                       round(avg(ms))                                    AS avg_ms,
                       percentile_disc(0.95) WITHIN GROUP (ORDER BY ms)  AS p95,
                       max(ms)                                           AS max_ms
                FROM mes_ui_timing
                WHERE kind = %s AND ts > now() - (%s || ' days')::interval
                GROUP BY name
                HAVING count(*) >= 3
                ORDER BY avg(ms) DESC
                LIMIT 15
            """, (kind, days))
            out[f"{kind}_slowest"] = cur.fetchall()

        # Day-by-day trend, so an improvement (or a regression) is visible.
        cur.execute("""
            SELECT ts::date AS day, kind,
                   count(*) AS samples, round(avg(ms)) AS avg_ms
            FROM mes_ui_timing
            WHERE ts > now() - (%s || ' days')::interval
            GROUP BY 1, 2 ORDER BY 1
        """, (days,))
        out["trend"] = cur.fetchall()

        # Where video clips came from — proves the archive is doing its job.
        cur.execute("""
            SELECT COALESCE(source, 'unknown') AS source,
                   count(*) AS samples, round(avg(ms)) AS avg_ms
            FROM mes_ui_timing
            WHERE kind = 'video' AND ts > now() - (%s || ' days')::interval
            GROUP BY 1 ORDER BY 2 DESC
        """, (days,))
        out["video_sources"] = cur.fetchall()

        # Distribution buckets for the histogram.
        cur.execute("""
            SELECT kind,
                   CASE WHEN ms <  500 THEN '0-0.5s'
                        WHEN ms < 1000 THEN '0.5-1s'
                        WHEN ms < 2000 THEN '1-2s'
                        WHEN ms < 3000 THEN '2-3s'
                        WHEN ms < 5000 THEN '3-5s'
                        WHEN ms <10000 THEN '5-10s'
                        ELSE '10s+' END AS bucket,
                   count(*) AS samples
            FROM mes_ui_timing
            WHERE ts > now() - (%s || ' days')::interval
            GROUP BY 1, 2
        """, (days,))
        out["buckets"] = cur.fetchall()

        cur.execute("""
            SELECT to_char(ts,'YYYY-MM-DD HH24:MI:SS') AS ts, kind, name, ms,
                   COALESCE(source,'') AS source, COALESCE(username,'') AS username
            FROM mes_ui_timing ORDER BY ts DESC LIMIT 60
        """)
        out["recent"] = cur.fetchall()

    return out


# ══════════════════════════════════════════════════════════════
# PER-USER ACTIVITY  (2026-08-14)
# ══════════════════════════════════════════════════════════════
# The Audit page could answer "what was CHANGED" (mes_audit_log) but not "who
# opened what, and for how long" — that lived in mes_ui_timing with no user
# attached.  These two endpoints join the halves:
#
#   /activity/summary  — one row per user: pages opened, time spent, changes made
#   /activity/detail   — one user's page-by-page trail
#
# Read-only and admin-gated: this is a record of what colleagues did, so it is
# not something an ordinary login should be able to browse.
# ══════════════════════════════════════════════════════════════

def _require_admin_user(user: dict) -> None:
    if (user or {}).get("role") not in ("admin", "plant_head"):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")


@router.get("/activity/summary")
def activity_summary(days: int = Query(7, ge=1, le=365),
                     user=Depends(get_current_user)):
    """One row per user for the window: what they opened, how long, what changed."""
    _require_admin_user(user)
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            WITH views AS (
                SELECT username,
                       count(*) FILTER (WHERE kind = 'page')            AS pages_opened,
                       count(DISTINCT name) FILTER (WHERE kind='page')  AS distinct_pages,
                       COALESCE(sum(ms) FILTER (WHERE kind='dwell'),0)  AS dwell_ms,
                       COALESCE(round(avg(ms) FILTER (WHERE kind='page')),0) AS avg_open_ms,
                       count(*) FILTER (WHERE kind = 'video')           AS videos_watched,
                       max(ts)                                          AS last_seen
                  FROM mes_ui_timing
                 WHERE username IS NOT NULL
                   AND ts > now() - (%s || ' days')::interval
                 GROUP BY username
            ), changes AS (
                SELECT username,
                       count(*) FILTER (WHERE action <> 'AUTH_LOGIN')   AS changes_made,
                       count(*) FILTER (WHERE action  = 'AUTH_LOGIN')   AS logins,
                       max(created_at)                                  AS last_action
                  FROM mes_audit_log
                 WHERE username IS NOT NULL
                   AND created_at > now() - (%s || ' days')::interval
                 GROUP BY username
            )
            SELECT COALESCE(v.username, c.username)          AS username,
                   COALESCE(v.pages_opened, 0)               AS "pagesOpened",
                   COALESCE(v.distinct_pages, 0)             AS "distinctPages",
                   COALESCE(v.dwell_ms, 0)                   AS "dwellMs",
                   COALESCE(v.avg_open_ms, 0)                AS "avgOpenMs",
                   COALESCE(v.videos_watched, 0)             AS "videosWatched",
                   COALESCE(c.changes_made, 0)               AS "changesMade",
                   COALESCE(c.logins, 0)                     AS logins,
                   GREATEST(COALESCE(v.last_seen, c.last_action),
                            COALESCE(c.last_action, v.last_seen)) AS "lastSeen"
              FROM views v
              FULL OUTER JOIN changes c ON c.username = v.username
             ORDER BY 9 DESC NULLS LAST
        """, (days, days))
        return {"days": days, "users": cur.fetchall()}


@router.get("/activity/detail")
def activity_detail(username: str = Query(...),
                    days: int = Query(7, ge=1, le=365),
                    limit: int = Query(200, ge=1, le=2000),
                    user=Depends(get_current_user)):
    """One user's trail: every page they opened, how long it stayed open, and
    every change they made — merged into a single time-ordered list."""
    _require_admin_user(user)
    _ensure_schema()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # Per-page roll-up: the question "which screens does this person live
        # in" is answered far better by totals than by a raw event list.
        cur.execute("""
            SELECT name                                        AS page,
                   count(*) FILTER (WHERE kind='page')         AS opens,
                   COALESCE(sum(ms) FILTER (WHERE kind='dwell'),0) AS "dwellMs",
                   COALESCE(round(avg(ms) FILTER (WHERE kind='page')),0) AS "avgOpenMs",
                   max(ts)                                     AS "lastOpened"
              FROM mes_ui_timing
             WHERE username = %s
               AND kind IN ('page','dwell')
               AND ts > now() - (%s || ' days')::interval
             GROUP BY name
             ORDER BY 3 DESC, 2 DESC
             LIMIT %s
        """, (username, days, limit))
        pages = cur.fetchall()

        cur.execute("""
            SELECT to_char(created_at,'YYYY-MM-DD HH24:MI:SS') AS ts,
                   action, entity_type AS "entityType",
                   entity_id AS "entityId", details
              FROM mes_audit_log
             WHERE username = %s
               AND created_at > now() - (%s || ' days')::interval
             ORDER BY created_at DESC
             LIMIT %s
        """, (username, days, limit))
        changes = cur.fetchall()

    return {"username": username, "days": days,
            "pages": pages, "changes": changes}


@router.post("/prune")
def prune(user=Depends(get_current_user)):
    """Drop samples past the retention window."""
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_ui_timing WHERE ts < now() - (%s || ' days')::interval",
                    (RETAIN_DAYS,))
        n = cur.rowcount
    return {"ok": True, "deleted": n}
