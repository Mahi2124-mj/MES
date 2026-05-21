"""
routers/n8n_bridge.py
=====================
AI Incident Intelligence bridge — connects the MES breakdown workflow to
the n8n AI Self-Healing automation on proxusss.app.n8n.cloud.

How it works
------------
1. When a breakdown is RESOLVED (line back to running), breakdowns.py calls
   _fire_n8n() as a FastAPI BackgroundTask — non-blocking, fire-and-forget.
2. n8n receives the incident payload, runs GPT-4o-mini triage + auto-
   remediation, and stores the result in Neon.tech (incidents table).
3. The MES frontend calls GET /api/n8n/analysis/{br_id} to display the AI
   root-cause + remediation for that specific breakdown.
4. GET /api/n8n/analyses lists the latest AI analyses for the overview panel.

Endpoints
---------
POST /api/n8n/trigger/{br_id}    — manually (re-)trigger analysis for a BD
GET  /api/n8n/analysis/{br_id}   — get AI result for one breakdown
GET  /api/n8n/analyses           — recent AI analyses (for overview panel)
GET  /api/n8n/status             — health-check: is Neon reachable?

Required env vars (add to Phase2/.env)
---------------------------------------
N8N_WEBHOOK_URL   https://proxusss.app.n8n.cloud/webhook/acac7c43-3f89-4252-8698-00bcb7812fba
NEON_DB_URL       postgresql://neondb_owner:<pass>@<host>/neondb?sslmode=require
"""

import os
import requests as _requests
import psycopg2
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from database import get_conn, dict_cursor
from auth import get_current_user

router = APIRouter(prefix="/api/n8n", tags=["n8n-ai"])

# ── Config ────────────────────────────────────────────────────────────────
N8N_WEBHOOK_URL = os.getenv(
    "N8N_WEBHOOK_URL",
    "https://proxusss.app.n8n.cloud/webhook/acac7c43-3f89-4252-8698-00bcb7812fba",
)
NEON_DB_URL = os.getenv("NEON_DB_URL", "")


# ── Helpers ───────────────────────────────────────────────────────────────
def _severity_from_minutes(mins: float) -> str:
    if mins >= 60:  return "critical"
    if mins >= 30:  return "high"
    if mins >= 10:  return "medium"
    return "low"


def _get_breakdown(br_id: int) -> Optional[dict]:
    """Fetch the breakdown row with line + zone names."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT b.id, b.line_id, b.zone_id, b.reason,
                   b.started_at, b.ended_at, b.state, b.shift_name,
                   b.production_data, b.maintenance_data,
                   l.line_name, l.line_code,
                   z.zone_name, z.zone_code
              FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
              LEFT JOIN mes_zones z ON z.id = b.zone_id
             WHERE b.id = %s
        """, (br_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def _build_payload(bd: dict) -> dict:
    """Map a mes_breakdowns row → n8n webhook payload."""
    started       = bd.get("started_at") or datetime.utcnow()
    ended         = bd.get("ended_at")   or datetime.utcnow()
    downtime_min  = (ended - started).total_seconds() / 60.0
    severity      = _severity_from_minutes(downtime_min)

    prod         = bd.get("production_data") or {}
    machine_no   = prod.get("machine_no",   "unknown")
    machine_name = prod.get("machine_name", bd.get("line_code", "unknown"))
    reason_text  = (bd.get("reason") or
                    f"Breakdown on {bd.get('line_name', 'line')}")

    return {
        "service":          f"{bd.get('line_code', 'line')}_{machine_no}",
        "host":             "192.168.10.210",
        "severity":         severity,
        "message":          reason_text,
        "metrics": {
            "downtime_minutes":   round(downtime_min, 1),
            "cpu_usage":          0.0,
            "memory_usage":       0.0,
            "disk_usage":         0.0,
            "network_latency_ms": 0.0,
            "error_rate":         0.0,
            "mes_breakdown_id":   bd["id"],   # key for reverse lookup
            "line_id":            bd.get("line_id"),
            "zone_id":            bd.get("zone_id"),
        },
        "mes_breakdown_id": bd["id"],
        "line_name":        bd.get("line_name",  ""),
        "zone_name":        bd.get("zone_name",  ""),
        "machine_no":       machine_no,
        "machine_name":     machine_name,
        "shift_name":       bd.get("shift_name", ""),
        "timestamp":        started.isoformat(),
    }


def _fire_n8n(br_id: int) -> None:
    """Synchronous fire-and-forget — always called via BackgroundTasks
    so it runs in a thread and never blocks the response."""
    if not N8N_WEBHOOK_URL:
        print("[n8n-bridge] N8N_WEBHOOK_URL not set — skipping")
        return
    bd = _get_breakdown(br_id)
    if not bd:
        print(f"[n8n-bridge] breakdown {br_id} not found — skipping")
        return
    payload = _build_payload(bd)
    try:
        resp = _requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
        print(f"[n8n-bridge] breakdown {br_id} → n8n → HTTP {resp.status_code}")
    except Exception as exc:
        print(f"[n8n-bridge] breakdown {br_id} → n8n FAILED: {exc}")


def _neon_conn():
    """Open a direct psycopg2 connection to Neon.tech."""
    if not NEON_DB_URL:
        raise HTTPException(503, "NEON_DB_URL not configured — add it to .env")
    try:
        return psycopg2.connect(NEON_DB_URL)
    except Exception as exc:
        raise HTTPException(503, f"Cannot reach Neon DB: {exc}")


def _row_to_dict(cur, row) -> dict:
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ── REST endpoints ────────────────────────────────────────────────────────

@router.post("/trigger/{br_id}", status_code=202)
def trigger_analysis(br_id: int, bg: BackgroundTasks,
                     user=Depends(get_current_user)):
    """Manually (re-)trigger AI analysis for a specific breakdown.
    Returns 202 immediately; the n8n call happens in the background."""
    bd = _get_breakdown(br_id)
    if not bd:
        raise HTTPException(404, "Breakdown not found")
    bg.add_task(_fire_n8n, br_id)
    return {"ok": True, "breakdown_id": br_id,
            "message": "AI analysis queued — check /analysis/{br_id} in ~60s"}


@router.get("/analysis/{br_id}")
def get_analysis(br_id: int, user=Depends(get_current_user)):
    """Return the AI root-cause + remediation for one breakdown.
    Queries Neon.tech incidents table by mes_breakdown_id.
    Returns 404 if analysis hasn't finished yet (retry after ~60s)."""
    conn = _neon_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, service, severity, category,
                       root_cause, recommended_action,
                       auto_remediated, remediation_result,
                       detected_at, resolved_at,
                       (metrics_snapshot->>'mes_breakdown_id')::int AS mes_breakdown_id
                  FROM incidents
                 WHERE (metrics_snapshot->>'mes_breakdown_id')::int = %s
                 ORDER BY created_at DESC LIMIT 1
            """, (br_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404,
                    "AI analysis not available yet — n8n may still be processing "
                    "(typical ~30-60s). Try again shortly.")
            return _row_to_dict(cur, row)
    finally:
        conn.close()


@router.get("/analyses")
def list_analyses(limit: int = 20, user=Depends(get_current_user)):
    """Latest AI analyses from Neon (most recent first).
    Used by the AI Overview panel on the Maintenance Dashboard."""
    conn = _neon_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, service, severity, category,
                       root_cause, recommended_action,
                       auto_remediated, remediation_result,
                       detected_at, resolved_at,
                       (metrics_snapshot->>'mes_breakdown_id')::int AS mes_breakdown_id,
                       (metrics_snapshot->>'downtime_minutes')::float AS downtime_minutes,
                       metrics_snapshot->>'line_name'  AS line_name,
                       metrics_snapshot->>'machine_no' AS machine_no
                  FROM incidents
                 ORDER BY created_at DESC
                 LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [_row_to_dict(cur, r) for r in rows]
    finally:
        conn.close()


@router.get("/status")
def status(user=Depends(get_current_user)):
    """Health-check: verify Neon DB is reachable and incidents table exists."""
    conn = _neon_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM incidents")
            count = cur.fetchone()[0]
        return {"ok": True, "incidents_in_neon": count,
                "webhook_url": N8N_WEBHOOK_URL}
    finally:
        conn.close()
