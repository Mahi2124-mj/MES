"""
routers/quality.py
==================
Quality department workflows.

Two flows wired here:

  1. Online Deviations
     ─────────────────────────────────
     When Maintenance can't fix a PY fault within 24h they raise an
     Online Deviation against the breakdown.  Maintenance fills the
     upper half (Non-Conformance / Root Cause / Containment + Permanent
     Corrective Action tables); Quality Sec Head approves / rejects.
     QA Head can grant date / qty extensions.  Mirrors the paper
     Deviation Form 1:1.

  2. 4M Change Intimation Note
     ─────────────────────────────────
     Production raises Part A (Man / Machine / Material / Method / Tool
     / Others changes); Quality fills Part B (acceptance + IFM /
     marking / control + day-wise OK/NG counts + retroactive check).

(An earlier draft also had a "Verification queue" where Quality
 approved every Maintenance breakdown closure.  Operator clarified that
 Quality does NOT routinely approve breakdowns — only Deviations need
 their nod.  The mes_quality_verifications table is kept dormant for
 future use; no endpoints surface it.)

Endpoints (all under /api/quality)
----------------------------------
GET    /deviations                  Filterable list
GET    /deviations/{id}             Single deviation
POST   /deviations                  Maintenance raises a new one
PUT    /deviations/{id}             Edit (only while PENDING_QA)
POST   /deviations/{id}/approve     Quality approves
POST   /deviations/{id}/reject      Quality rejects with remarks
POST   /deviations/{id}/extend      QA Head adds extension row
POST   /deviations/{id}/close       Final closure (after all CA done)

GET    /4m-changes                  Filterable list
POST   /4m-changes                  Production raises Part A
PUT    /4m-changes/{id}             Edit fields (any field, both halves)
POST   /4m-changes/{id}/close       QA closes the change

GET    /kpi                         Top-of-dashboard counters
"""
from datetime import date, datetime, timedelta
from typing  import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from psycopg2.extras import Json

from database import get_conn, dict_cursor
from auth     import get_current_user, require_admin

router = APIRouter(prefix="/api/quality", tags=["quality"])


# ── Helpers ───────────────────────────────────────────────────────────
def _next_seq_no(prefix: str, table: str, col: str, conn) -> str:
    """Generate next document number like DEV-2026-0001 by counting rows
    for the current year."""
    yr = datetime.now().year
    cur = dict_cursor(conn)
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {table} "
        f"WHERE {col} LIKE %s",
        (f"{prefix}-{yr}-%",),
    )
    n = (cur.fetchone() or {}).get("n", 0) + 1
    return f"{prefix}-{yr}-{n:04d}"


def _enrich_breakdown_meta(row: dict) -> dict:
    """Deep-extract zone/line/machine fields from a breakdown row so
    Quality dashboards can render without extra joins."""
    pd = row.get("production_data") or {}
    return {
        "zone_id":      row.get("zone_id"),
        "zone_name":    row.get("zone_name") or pd.get("zone"),
        "line_id":      row.get("line_id"),
        "line_name":    row.get("line_name"),
        "machine_no":   pd.get("machine_no"),
        "machine_name": pd.get("machine_name"),
    }


# NOTE: Earlier draft also exposed /verifications/pending and
# /verifications/{id}/decide endpoints (Quality approving Maintenance's
# breakdown closures).  Operator clarified that Quality does NOT
# routinely approve every breakdown — only Deviations need their nod.
# Those endpoints are removed; the mes_quality_verifications table is
# left in place dormant in case the workflow comes back later.


# ═════════════════════════════════════════════════════════════════════
# 1. ONLINE DEVIATIONS
# ═════════════════════════════════════════════════════════════════════
class DeviationCreate(BaseModel):
    breakdown_id:        Optional[int] = None
    line_id:             Optional[int] = None
    line_name:           Optional[str] = None
    zone_id:             Optional[int] = None
    zone_name:           Optional[str] = None
    machine_no:          Optional[str] = None
    machine_name:        Optional[str] = None
    category:            Optional[str] = None
    process_name:        Optional[str] = None
    process_no:          Optional[str] = None
    srv_no:              Optional[str] = None
    deviation_qty:       Optional[int] = None
    deviation_upto_qty:  Optional[int] = None
    deviation_upto_date: Optional[str] = None  # ISO date
    initiated_by:        Optional[str] = None
    reason:              Optional[str] = None
    requirement:         Optional[str] = None
    observation:         Optional[str] = None
    root_cause_occurrence: Optional[str] = None
    root_cause_detection:  Optional[str] = None
    potential_consequences: Optional[str] = None
    hod_production:      Optional[str] = None
    hod_production_note: Optional[str] = None
    containment_actions: Optional[List[Dict[str, Any]]] = None
    permanent_actions:   Optional[List[Dict[str, Any]]] = None


class DeviationUpdate(DeviationCreate):
    pass


class DeviationApprove(BaseModel):
    hod_quality:      Optional[str] = None
    hod_quality_note: Optional[str] = None


class DeviationReject(BaseModel):
    hod_quality:      Optional[str] = None
    rejection_reason: str


class DeviationExtension(BaseModel):
    from_qty_date:  Optional[str] = None
    to_qty_date:    Optional[str] = None
    reason:         Optional[str] = None
    hod_concerned:  Optional[str] = None
    sign:           Optional[str] = None
    hod_quality:    Optional[str] = None
    hod_operation:  Optional[str] = None
    decision:       Optional[str] = None  # APPROVED / REJECTED


class DeviationClose(BaseModel):
    closure_remarks:     Optional[str] = None
    hod_concerned_close: Optional[str] = None
    hod_quality_close:   Optional[str] = None


def _dev_dict(r) -> dict:
    return dict(r) if r else None


@router.get("/deviations")
def list_deviations(days: int = Query(60, ge=1, le=730),
                    status: Optional[str] = Query(None),
                    line_id: Optional[int] = Query(None),
                    user=Depends(get_current_user)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    where = ["d.created_at >= %s"]
    params: list = [cutoff]
    if status:
        where.append("d.status = %s"); params.append(status.upper())
    if line_id is not None:
        where.append("d.line_id = %s"); params.append(line_id)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT d.*, ru.username AS raised_by_username,
                   au.username AS approved_by_username
              FROM mes_quality_deviations d
              LEFT JOIN mes_admin ru ON ru.id = d.raised_by_user_id
              LEFT JOIN mes_admin au ON au.id = d.approved_by_user_id
             WHERE {' AND '.join(where)}
             ORDER BY d.created_at DESC
             LIMIT 500
        """, params)
        return [dict(r) for r in cur.fetchall()]


@router.get("/deviations/{did}")
def get_deviation(did: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT d.*, ru.username AS raised_by_username,
                   au.username AS approved_by_username
              FROM mes_quality_deviations d
              LEFT JOIN mes_admin ru ON ru.id = d.raised_by_user_id
              LEFT JOIN mes_admin au ON au.id = d.approved_by_user_id
             WHERE d.id = %s
        """, (did,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Deviation not found")
        return dict(row)


@router.post("/deviations", status_code=201)
def create_deviation(body: DeviationCreate, user=Depends(get_current_user)):
    """Maintenance raises a new deviation against a breakdown.  Auto-
    generates dev_no = DEV-YYYY-NNNN (year-scoped sequence)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        dev_no = _next_seq_no("DEV", "mes_quality_deviations", "dev_no", conn)
        cur.execute("""
            INSERT INTO mes_quality_deviations
                (dev_no, breakdown_id, line_id, line_name, zone_id, zone_name,
                 machine_no, machine_name, category, process_name, process_no,
                 srv_no, deviation_qty, deviation_upto_qty, deviation_upto_date,
                 initiated_by, initiated_at, reason, requirement, observation,
                 root_cause_occurrence, root_cause_detection, potential_consequences,
                 hod_production, hod_production_note,
                 containment_actions, permanent_actions, raised_by_user_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING_QA')
            RETURNING *
        """, (
            dev_no, body.breakdown_id, body.line_id, body.line_name,
            body.zone_id, body.zone_name, body.machine_no, body.machine_name,
            body.category, body.process_name, body.process_no, body.srv_no,
            body.deviation_qty, body.deviation_upto_qty, body.deviation_upto_date,
            body.initiated_by or user.get("username"),
            body.reason, body.requirement, body.observation,
            body.root_cause_occurrence, body.root_cause_detection,
            body.potential_consequences,
            body.hod_production, body.hod_production_note,
            Json(body.containment_actions or []),
            Json(body.permanent_actions or []),
            user["id"],
        ))
        row = cur.fetchone()
        conn.commit()
    return dict(row)


@router.put("/deviations/{did}")
def update_deviation(did: int, body: DeviationUpdate,
                     user=Depends(get_current_user)):
    """Edit a deviation while it's still PENDING_QA — once approved /
    rejected / closed, edits are blocked."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT status FROM mes_quality_deviations WHERE id = %s", (did,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Deviation not found")
        if row["status"] != "PENDING_QA":
            raise HTTPException(409, "Deviation is locked — only PENDING_QA edits allowed")

        # Sparse update — only fields the caller sent.
        fields, params = [], []
        body_dict = body.model_dump(exclude_none=True)
        for k, v in body_dict.items():
            if k in ("containment_actions", "permanent_actions"):
                fields.append(f"{k} = %s"); params.append(Json(v))
            else:
                fields.append(f"{k} = %s"); params.append(v)
        if not fields:
            return {"ok": True, "noop": True}
        fields.append("updated_at = NOW()")
        params.append(did)
        cur.execute(
            f"UPDATE mes_quality_deviations SET {', '.join(fields)} WHERE id = %s "
            f"RETURNING *", params,
        )
        out = cur.fetchone()
        conn.commit()
    return dict(out)


@router.post("/deviations/{did}/approve")
def approve_deviation(did: int, body: DeviationApprove,
                      user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            UPDATE mes_quality_deviations
               SET status = 'APPROVED', approved_at = NOW(),
                   approved_by_user_id = %s,
                   hod_quality = COALESCE(%s, hod_quality),
                   hod_quality_note = COALESCE(%s, hod_quality_note),
                   updated_at = NOW()
             WHERE id = %s AND status = 'PENDING_QA'
            RETURNING *
        """, (user["id"], body.hod_quality, body.hod_quality_note, did))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Deviation not pending or not found")
        conn.commit()
    return dict(row)


@router.post("/deviations/{did}/reject")
def reject_deviation(did: int, body: DeviationReject,
                     user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            UPDATE mes_quality_deviations
               SET status = 'REJECTED', approved_at = NOW(),
                   approved_by_user_id = %s,
                   hod_quality = COALESCE(%s, hod_quality),
                   hod_quality_note = %s,
                   updated_at = NOW()
             WHERE id = %s AND status = 'PENDING_QA'
            RETURNING *
        """, (user["id"], body.hod_quality, body.rejection_reason, did))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Deviation not pending or not found")
        conn.commit()
    return dict(row)


@router.post("/deviations/{did}/extend")
def extend_deviation(did: int, body: DeviationExtension,
                     user=Depends(get_current_user)):
    """QA Head appends a new extension row to the deviation's extensions
    JSONB array.  Sets status to EXTENDED if currently APPROVED."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT extensions, status FROM mes_quality_deviations WHERE id = %s", (did,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Deviation not found")
        ext_list = row["extensions"] or []
        ext_list.append({**body.model_dump(exclude_none=True),
                         "added_by": user.get("username"),
                         "added_at": datetime.utcnow().isoformat()})
        new_status = "EXTENDED" if row["status"] in ("APPROVED", "EXTENDED") else row["status"]
        cur.execute("""
            UPDATE mes_quality_deviations
               SET extensions = %s, status = %s, updated_at = NOW()
             WHERE id = %s
            RETURNING *
        """, (Json(ext_list), new_status, did))
        out = cur.fetchone()
        conn.commit()
    return dict(out)


@router.post("/deviations/{did}/close")
def close_deviation(did: int, body: DeviationClose,
                    user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            UPDATE mes_quality_deviations
               SET status = 'CLOSED', closed_at = NOW(),
                   closure_remarks = COALESCE(%s, closure_remarks),
                   hod_concerned_close = COALESCE(%s, hod_concerned_close),
                   hod_quality_close   = COALESCE(%s, hod_quality_close),
                   updated_at = NOW()
             WHERE id = %s AND status IN ('APPROVED', 'EXTENDED')
            RETURNING *
        """, (body.closure_remarks, body.hod_concerned_close,
              body.hod_quality_close, did))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Only APPROVED or EXTENDED deviations can be closed")
        conn.commit()
    return dict(row)


# ═════════════════════════════════════════════════════════════════════
# 2. 4M CHANGE INTIMATION NOTE
# ═════════════════════════════════════════════════════════════════════
class FourMCreate(BaseModel):
    zone_id:           Optional[int] = None
    zone_name:         Optional[str] = None
    line_id:           Optional[int] = None
    line_name:         Optional[str] = None
    part_name:         Optional[str] = None
    model:             Optional[str] = None
    shift_name:        Optional[str] = None
    issue_date:        Optional[str] = None
    start_batch_code:  Optional[str] = None
    end_batch_code:    Optional[str] = None
    originator_name:   Optional[str] = None
    changing_points:   Optional[Dict[str, Any]] = None
    change_details:    Optional[str] = None


class FourMUpdate(FourMCreate):
    qa_engineer:           Optional[str] = None
    change_acceptance:     Optional[str] = None
    ifm_required:          Optional[str] = None
    confirmation_marking:  Optional[str] = None
    control_next_station:  Optional[str] = None
    qty_status_log:        Optional[List[Dict[str, Any]]] = None
    qty_produced:          Optional[int] = None
    ng_qty:                Optional[int] = None
    retroactive_check_status: Optional[str] = None
    comments:              Optional[str] = None
    termination_date:      Optional[str] = None
    qa_sign:               Optional[str] = None


@router.get("/4m-changes")
def list_4m_changes(days: int = Query(60, ge=1, le=730),
                    status: Optional[str] = Query(None),
                    user=Depends(get_current_user)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    where = ["created_at >= %s"]
    params: list = [cutoff]
    if status:
        where.append("status = %s"); params.append(status.upper())
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT * FROM mes_quality_4m_changes
             WHERE {' AND '.join(where)}
             ORDER BY created_at DESC LIMIT 500
        """, params)
        return [dict(r) for r in cur.fetchall()]


@router.post("/4m-changes", status_code=201)
def create_4m(body: FourMCreate, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        note_no = _next_seq_no("4M", "mes_quality_4m_changes", "note_no", conn)
        cur.execute("""
            INSERT INTO mes_quality_4m_changes
                (note_no, zone_id, zone_name, line_id, line_name,
                 part_name, model, shift_name, issue_date,
                 start_batch_code, end_batch_code, originator_name,
                 originator_user_id, changing_points, change_details, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
            RETURNING *
        """, (
            note_no, body.zone_id, body.zone_name, body.line_id, body.line_name,
            body.part_name, body.model, body.shift_name, body.issue_date,
            body.start_batch_code, body.end_batch_code,
            body.originator_name or user.get("username"), user["id"],
            Json(body.changing_points or {}), body.change_details,
        ))
        row = cur.fetchone()
        conn.commit()
    return dict(row)


@router.put("/4m-changes/{nid}")
def update_4m(nid: int, body: FourMUpdate, user=Depends(get_current_user)):
    fields, params = [], []
    body_dict = body.model_dump(exclude_none=True)
    for k, v in body_dict.items():
        if k in ("changing_points", "qty_status_log"):
            fields.append(f"{k} = %s"); params.append(Json(v))
        else:
            fields.append(f"{k} = %s"); params.append(v)
    if not fields:
        return {"ok": True, "noop": True}
    fields.append("updated_at = NOW()")
    params.append(nid)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            f"UPDATE mes_quality_4m_changes SET {', '.join(fields)} WHERE id = %s "
            f"RETURNING *", params,
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "4M Change Note not found")
        conn.commit()
    return dict(row)


@router.post("/4m-changes/{nid}/close")
def close_4m(nid: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            UPDATE mes_quality_4m_changes
               SET status = 'CLOSED', closed_by_user_id = %s, updated_at = NOW()
             WHERE id = %s
            RETURNING *
        """, (user["id"], nid))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "4M Change Note not found")
        conn.commit()
    return dict(row)


# ═════════════════════════════════════════════════════════════════════
# 3. KPI counters (top of Quality Dashboard)
# ═════════════════════════════════════════════════════════════════════
# Quality only owns Deviations + 4M Change Notes (operator clarified
# that breakdown closures DO NOT need a Quality nod — only Deviations
# do).  So this endpoint stays focused on those two workflows + a
# PY-fail tally for situational awareness.
@router.get("/kpi")
def kpi(user=Depends(get_current_user)):
    today = date.today()
    month_start = today.replace(day=1)
    with get_conn() as conn:
        cur = dict_cursor(conn)

        cur.execute("""
            SELECT
              SUM(CASE WHEN status = 'PENDING_QA' THEN 1 ELSE 0 END) AS pending_dev,
              SUM(CASE WHEN status IN ('APPROVED','EXTENDED') THEN 1 ELSE 0 END) AS open_dev,
              SUM(CASE WHEN status = 'CLOSED' AND closed_at >= %s THEN 1 ELSE 0 END) AS closed_dev_month
            FROM mes_quality_deviations
        """, (month_start,))
        d = cur.fetchone() or {}

        cur.execute("""
            SELECT COUNT(*) AS open_4m
              FROM mes_quality_4m_changes
             WHERE status = 'OPEN'
        """)
        m = cur.fetchone() or {}

        # PY-fail count today.  PY events with rule_type = 'SENSOR_BYPASS'
        # are the production-floor PY-fail signal.
        cur.execute("""
            SELECT COUNT(*) AS n
              FROM mes_poka_yoke_events
             WHERE rule_type = 'SENSOR_BYPASS'
               AND ts_event >= %s
        """, (today,))
        py = (cur.fetchone() or {}).get("n", 0)

    # NCR counters (added 2026-05-13)
    open_ncr = ncr_qty_today = ncr_open_qty = 0
    try:
        cur.execute("""
            SELECT
              SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END)              AS open_ncr,
              SUM(CASE WHEN raised_at::date = %s THEN qty_rejected ELSE 0 END) AS qty_today,
              SUM(CASE WHEN status='OPEN' THEN qty_rejected ELSE 0 END)   AS open_qty
              FROM mes_quality_ncr
        """, (today,))
        n = cur.fetchone() or {}
        open_ncr     = int(n.get("open_ncr")  or 0)
        ncr_qty_today= int(n.get("qty_today") or 0)
        ncr_open_qty = int(n.get("open_qty")  or 0)
    except Exception:
        # Table doesn't exist yet (first deploy) — fine, return zeros
        pass

    # Sprint-2 counters (in-process inspection + first/last piece + PPM)
    insp_today = insp_ng_today = flp_today = flp_ng_today = 0
    try:
        with get_conn() as conn2:
            cur2 = dict_cursor(conn2)
            try:
                cur2.execute("""
                    SELECT
                      COUNT(*)                                              AS n,
                      SUM(CASE WHEN status='NG' THEN 1 ELSE 0 END)::int     AS ng
                      FROM mes_quality_inspection_log
                     WHERE ts_measured::date = %s
                """, (today,))
                r = cur2.fetchone() or {}
                insp_today    = int(r.get("n")  or 0)
                insp_ng_today = int(r.get("ng") or 0)
            except Exception:
                pass
            try:
                cur2.execute("""
                    SELECT
                      COUNT(*)                                              AS n,
                      SUM(CASE WHEN status='NG' THEN 1 ELSE 0 END)::int     AS ng
                      FROM mes_quality_first_last_piece
                     WHERE ts_checked::date = %s
                """, (today,))
                r = cur2.fetchone() or {}
                flp_today    = int(r.get("n")  or 0)
                flp_ng_today = int(r.get("ng") or 0)
            except Exception:
                pass
    except Exception:
        pass

    return {
        "pending_deviations":      int(d.get("pending_dev")     or 0),
        "open_deviations":         int(d.get("open_dev")        or 0),
        "closed_deviations_month": int(d.get("closed_dev_month") or 0),
        "open_4m_changes":         int(m.get("open_4m")         or 0),
        "py_fails_today":          int(py),
        "open_ncr":                open_ncr,
        "ncr_qty_today":           ncr_qty_today,
        "ncr_open_qty":            ncr_open_qty,
        "inspections_today":       insp_today,
        "inspections_ng_today":    insp_ng_today,
        "flp_checks_today":        flp_today,
        "flp_ng_today":            flp_ng_today,
    }


# ════════════════════════════════════════════════════════════════════════
# NCR (Non-Conformance Report) + Defect Pareto
# ────────────────────────────────────────────────────────────────────────
# Sprint 1 of the Quality expansion (2026-05-13).  Every defective part
# the QA inspector finds is logged here.  Drives the Defect Pareto chart
# auto-rolled by defect_type.  Disposition options match standard
# automotive practice: REWORK / SCRAP / ACCEPT_AS_IS / RETURN_TO_VENDOR.
#
# Two tables:
#   mes_quality_ncr_defect_types — admin-configurable defect catalog
#                                  (seeded with common automotive defects)
#   mes_quality_ncr              — the actual NCR records
# ════════════════════════════════════════════════════════════════════════

def _ensure_ncr_tables(conn) -> None:
    """Idempotent schema bootstrap — runs on first NCR endpoint hit."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_ncr_defect_types (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR(120) UNIQUE NOT NULL,
            category   VARCHAR(60),
            is_active  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_ncr (
            id               SERIAL PRIMARY KEY,
            ncr_number       VARCHAR(40) UNIQUE NOT NULL,
            line_id          INTEGER NOT NULL,
            zone_id          INTEGER,
            shift_name       VARCHAR(10),
            part_code        VARCHAR(80),
            part_name        VARCHAR(200),
            defect_type_id   INTEGER,
            defect_type_name VARCHAR(120),
            defect_category  VARCHAR(60),
            qty_rejected     INTEGER NOT NULL DEFAULT 1,
            root_cause       TEXT,
            disposition      VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            status           VARCHAR(20) NOT NULL DEFAULT 'OPEN',
            raised_by        VARCHAR(80) NOT NULL,
            raised_at        TIMESTAMP NOT NULL DEFAULT NOW(),
            closed_by        VARCHAR(80),
            closed_at        TIMESTAMP,
            notes            TEXT,
            created_at       TIMESTAMP DEFAULT NOW(),
            updated_at       TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mes_quality_ncr_line_date
            ON mes_quality_ncr (line_id, raised_at DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mes_quality_ncr_defect
            ON mes_quality_ncr (defect_type_id, raised_at DESC)
    """)
    # Seed common automotive defect types (1-shot, never overwrites)
    cur.execute("SELECT COUNT(*) FROM mes_quality_ncr_defect_types")
    if cur.fetchone()[0] == 0:
        seeds = [
            ("Bent / Deformed",          "Physical"),
            ("Scratch / Mark",           "Surface"),
            ("Dent",                     "Surface"),
            ("Wrong Part",               "Mis-assembly"),
            ("Missing Part",             "Mis-assembly"),
            ("Wrong Orientation",        "Mis-assembly"),
            ("Loose Fastener",           "Assembly"),
            ("Over-torque",              "Assembly"),
            ("Under-torque",             "Assembly"),
            ("Crimping Issue",           "Assembly"),
            ("Weld Quality",             "Welding"),
            ("Dimensional Out of Spec",  "Dimensional"),
            ("Surface Finish",           "Surface"),
            ("Color / Paint",            "Surface"),
            ("Material Issue",           "Material"),
            ("Other",                    "Other"),
        ]
        for n, c in seeds:
            cur.execute(
                "INSERT INTO mes_quality_ncr_defect_types (name, category) "
                "VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (n, c),
            )
    conn.commit()
    cur.close()


# ── Schemas ───────────────────────────────────────────────────────────

class NCRCreate(BaseModel):
    line_id:        int
    zone_id:        Optional[int] = None
    shift_name:     Optional[str] = None
    part_code:      Optional[str] = None
    part_name:      Optional[str] = None
    defect_type_id: int
    qty_rejected:   int   = 1
    notes:          Optional[str] = None

class NCRUpdate(BaseModel):
    root_cause:   Optional[str] = None
    disposition:  Optional[str] = None      # REWORK / SCRAP / ACCEPT_AS_IS / RETURN_TO_VENDOR / PENDING
    notes:        Optional[str] = None
    qty_rejected: Optional[int] = None
    defect_type_id: Optional[int] = None

class NCRClose(BaseModel):
    root_cause:  str
    disposition: str       # REWORK / SCRAP / ACCEPT_AS_IS / RETURN_TO_VENDOR — no PENDING on close
    notes:       Optional[str] = None

class DefectTypeCreate(BaseModel):
    name:     str
    category: Optional[str] = None

class DefectTypeUpdate(BaseModel):
    name:      Optional[str]  = None
    category:  Optional[str]  = None
    is_active: Optional[bool] = None


_VALID_DISPO = {"REWORK", "SCRAP", "ACCEPT_AS_IS", "RETURN_TO_VENDOR", "PENDING"}


# ── Defect-type catalog (admin) ────────────────────────────────────────

@router.get("/ncr/defect-types")
def list_defect_types(active_only: bool = True, user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = dict_cursor(conn)
        if active_only:
            cur.execute(
                "SELECT * FROM mes_quality_ncr_defect_types "
                " WHERE is_active = TRUE ORDER BY category, name"
            )
        else:
            cur.execute(
                "SELECT * FROM mes_quality_ncr_defect_types "
                " ORDER BY is_active DESC, category, name"
            )
        return cur.fetchall()


@router.post("/ncr/defect-types", status_code=201)
def create_defect_type(body: DefectTypeCreate, admin=Depends(require_admin)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = dict_cursor(conn)
        try:
            cur.execute("""
                INSERT INTO mes_quality_ncr_defect_types (name, category)
                VALUES (%s, %s) RETURNING *
            """, (name, (body.category or "Other").strip()))
            row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Insert failed (duplicate name?): {e}")
        return row


@router.put("/ncr/defect-types/{type_id}")
def update_defect_type(type_id: int, body: DefectTypeUpdate, admin=Depends(require_admin)):
    sets, params = [], []
    if body.name      is not None: sets.append("name = %s");      params.append(body.name.strip())
    if body.category  is not None: sets.append("category = %s");  params.append(body.category.strip())
    if body.is_active is not None: sets.append("is_active = %s"); params.append(bool(body.is_active))
    if not sets:
        return {"ok": True, "noop": True}
    params.append(type_id)
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = conn.cursor()
        cur.execute(
            f"UPDATE mes_quality_ncr_defect_types SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        conn.commit()
    return {"ok": True}


# ── NCR records ────────────────────────────────────────────────────────

@router.get("/ncr")
def list_ncr(
    days:    int = Query(60, ge=1, le=365),
    line_id: Optional[int] = None,
    status:  Optional[str] = None,
    user=Depends(get_current_user),
):
    cutoff = date.today() - timedelta(days=days)
    where  = ["raised_at >= %s"]
    params: List[Any] = [cutoff]
    if line_id is not None:
        where.append("line_id = %s"); params.append(line_id)
    if status:
        where.append("status = %s");  params.append(status.upper())
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT n.*,
                   l.line_name,
                   z.zone_name
              FROM mes_quality_ncr n
              LEFT JOIN mes_lines l ON l.id = n.line_id
              LEFT JOIN mes_zones z ON z.id = n.zone_id
             WHERE {' AND '.join(where)}
             ORDER BY n.raised_at DESC
        """, params)
        return cur.fetchall()


@router.post("/ncr", status_code=201)
def create_ncr(body: NCRCreate, user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = dict_cursor(conn)
        # Resolve defect type to denormalise name + category at write time
        # so historical NCRs survive defect-catalog edits.
        cur.execute(
            "SELECT name, category FROM mes_quality_ncr_defect_types WHERE id = %s",
            (body.defect_type_id,),
        )
        dt = cur.fetchone()
        if not dt:
            raise HTTPException(404, f"defect_type_id {body.defect_type_id} not found")

        # Auto-resolve zone_id from line if not supplied
        zone_id = body.zone_id
        if zone_id is None and body.line_id:
            cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s", (body.line_id,))
            r = cur.fetchone()
            if r: zone_id = r["zone_id"]

        ncr_no = _next_seq_no("NCR", "mes_quality_ncr", "ncr_number", conn)
        cur.execute("""
            INSERT INTO mes_quality_ncr
                (ncr_number, line_id, zone_id, shift_name,
                 part_code, part_name,
                 defect_type_id, defect_type_name, defect_category,
                 qty_rejected, notes,
                 raised_by, raised_at)
            VALUES (%s,%s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s, NOW())
            RETURNING *
        """, (
            ncr_no, body.line_id, zone_id, body.shift_name,
            body.part_code, body.part_name,
            body.defect_type_id, dt["name"], dt["category"],
            max(1, int(body.qty_rejected or 1)), body.notes,
            user.get("username") or "unknown",
        ))
        row = cur.fetchone()
        conn.commit()
        return row


@router.put("/ncr/{ncr_id}")
def update_ncr(ncr_id: int, body: NCRUpdate, user=Depends(get_current_user)):
    sets, params = [], []
    if body.root_cause   is not None: sets.append("root_cause = %s");   params.append(body.root_cause)
    if body.notes        is not None: sets.append("notes = %s");        params.append(body.notes)
    if body.qty_rejected is not None: sets.append("qty_rejected = %s"); params.append(max(1, int(body.qty_rejected)))
    if body.disposition  is not None:
        d = body.disposition.upper()
        if d not in _VALID_DISPO:
            raise HTTPException(400, f"disposition must be one of {sorted(_VALID_DISPO)}")
        sets.append("disposition = %s"); params.append(d)
    if body.defect_type_id is not None:
        with get_conn() as c2:
            cur2 = dict_cursor(c2)
            cur2.execute(
                "SELECT name, category FROM mes_quality_ncr_defect_types WHERE id = %s",
                (body.defect_type_id,),
            )
            dt = cur2.fetchone()
        if not dt:
            raise HTTPException(404, f"defect_type_id {body.defect_type_id} not found")
        sets.append("defect_type_id = %s");   params.append(body.defect_type_id)
        sets.append("defect_type_name = %s"); params.append(dt["name"])
        sets.append("defect_category = %s");  params.append(dt["category"])
    if not sets:
        return {"ok": True, "noop": True}
    sets.append("updated_at = NOW()")
    params.append(ncr_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE mes_quality_ncr SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        conn.commit()
    return {"ok": True}


@router.post("/ncr/{ncr_id}/close")
def close_ncr(ncr_id: int, body: NCRClose, user=Depends(get_current_user)):
    d = (body.disposition or "").upper()
    if d not in _VALID_DISPO - {"PENDING"}:
        raise HTTPException(400, "disposition required on close (REWORK/SCRAP/ACCEPT_AS_IS/RETURN_TO_VENDOR)")
    if not (body.root_cause or "").strip():
        raise HTTPException(400, "root_cause required on close")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE mes_quality_ncr
               SET status      = 'CLOSED',
                   root_cause  = %s,
                   disposition = %s,
                   notes       = COALESCE(%s, notes),
                   closed_by   = %s,
                   closed_at   = NOW(),
                   updated_at  = NOW()
             WHERE id = %s
        """, (body.root_cause.strip(), d, body.notes,
              user.get("username") or "unknown", ncr_id))
        conn.commit()
    return {"ok": True}


@router.delete("/ncr/{ncr_id}")
def void_ncr(ncr_id: int, admin=Depends(require_admin)):
    """Soft-delete — status flipped to VOID so Pareto excludes it but
    the row stays for audit trail."""
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_quality_ncr SET status='VOID', updated_at=NOW() WHERE id = %s",
            (ncr_id,),
        )
        conn.commit()
    return {"ok": True}


# ── Defect Pareto (auto-aggregated) ────────────────────────────────────

@router.get("/ncr/pareto")
def ncr_pareto(
    days:    int = Query(30, ge=1, le=365),
    line_id: Optional[int] = None,
    user=Depends(get_current_user),
):
    """Top-N defect aggregation for the Pareto chart.
    VOID rows excluded.  Returns sorted descending by total qty so the
    frontend can render bars + cumulative % line directly."""
    cutoff = date.today() - timedelta(days=days)
    where  = ["status <> 'VOID'", "raised_at >= %s"]
    params: List[Any] = [cutoff]
    if line_id is not None:
        where.append("line_id = %s"); params.append(line_id)
    with get_conn() as conn:
        _ensure_ncr_tables(conn)
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT defect_type_id,
                   COALESCE(defect_type_name, '—')  AS defect_type_name,
                   COALESCE(defect_category, '—')   AS defect_category,
                   COUNT(*)                          AS ncr_count,
                   SUM(qty_rejected)                 AS total_qty
              FROM mes_quality_ncr
             WHERE {' AND '.join(where)}
             GROUP BY defect_type_id, defect_type_name, defect_category
             ORDER BY total_qty DESC NULLS LAST, ncr_count DESC
        """, params)
        rows = cur.fetchall()
        total_qty = sum(int(r["total_qty"] or 0) for r in rows) or 1
        # Add cumulative % for Pareto's classic 80/20 line
        cum = 0
        out = []
        for r in rows:
            q = int(r["total_qty"] or 0)
            cum += q
            out.append({
                "defect_type_id":    r["defect_type_id"],
                "defect_type_name":  r["defect_type_name"],
                "defect_category":   r["defect_category"],
                "ncr_count":         int(r["ncr_count"] or 0),
                "total_qty":         q,
                "share_pct":         round(100.0 * q / total_qty, 1),
                "cumulative_pct":    round(100.0 * cum / total_qty, 1),
            })
        return {
            "days":      days,
            "line_id":   line_id,
            "total_qty": total_qty,
            "buckets":   out,
        }


# ════════════════════════════════════════════════════════════════════════
# Sprint-2 (2026-05-13) — In-Process Inspection (Hourly Patrol)
# ────────────────────────────────────────────────────────────────────────
# QC inspector picks samples every hour and records measurement vs
# tolerance.  Two tables: characteristic catalog (admin-configured per
# part) + measurement log.  Status auto-computed from tolerance band.
# ════════════════════════════════════════════════════════════════════════

def _ensure_inspection_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_inspection_chars (
            id           SERIAL PRIMARY KEY,
            part_code    VARCHAR(80) NOT NULL,
            char_name    VARCHAR(120) NOT NULL,
            target       DOUBLE PRECISION,
            lower_tol    DOUBLE PRECISION,
            upper_tol    DOUBLE PRECISION,
            unit         VARCHAR(20),
            freq_hours   INTEGER NOT NULL DEFAULT 1,
            gauge        VARCHAR(80),
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMP DEFAULT NOW(),
            UNIQUE (part_code, char_name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_inspection_log (
            id             SERIAL PRIMARY KEY,
            line_id        INTEGER NOT NULL,
            zone_id        INTEGER,
            part_code      VARCHAR(80),
            char_id        INTEGER,
            char_name      VARCHAR(120),
            target         DOUBLE PRECISION,
            lower_tol      DOUBLE PRECISION,
            upper_tol      DOUBLE PRECISION,
            unit           VARCHAR(20),
            measured       DOUBLE PRECISION NOT NULL,
            status         VARCHAR(10) NOT NULL DEFAULT 'OK',  -- OK / NG
            inspector      VARCHAR(80) NOT NULL,
            shift_name     VARCHAR(10),
            ts_measured    TIMESTAMP NOT NULL DEFAULT NOW(),
            notes          TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mes_quality_inspection_log_line_ts
            ON mes_quality_inspection_log (line_id, ts_measured DESC)
    """)
    conn.commit()
    cur.close()


class InspectionCharCreate(BaseModel):
    part_code:  str
    char_name:  str
    target:     Optional[float] = None
    lower_tol:  Optional[float] = None
    upper_tol:  Optional[float] = None
    unit:       Optional[str]   = None
    freq_hours: int             = 1
    gauge:      Optional[str]   = None

class InspectionCharUpdate(BaseModel):
    char_name:  Optional[str]   = None
    target:     Optional[float] = None
    lower_tol:  Optional[float] = None
    upper_tol:  Optional[float] = None
    unit:       Optional[str]   = None
    freq_hours: Optional[int]   = None
    gauge:      Optional[str]   = None
    is_active:  Optional[bool]  = None

class InspectionLogCreate(BaseModel):
    line_id:    int
    char_id:    int
    measured:   float
    shift_name: Optional[str] = None
    notes:      Optional[str] = None


@router.get("/inspection-chars")
def list_inspection_chars(part_code: Optional[str] = None,
                           active_only: bool = True,
                           user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_inspection_tables(conn)
        cur = dict_cursor(conn)
        where, params = [], []
        if active_only: where.append("is_active = TRUE")
        if part_code:   where.append("part_code = %s"); params.append(part_code)
        sql = "SELECT * FROM mes_quality_inspection_chars"
        if where: sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY part_code, char_name"
        cur.execute(sql, params)
        return cur.fetchall()


@router.post("/inspection-chars", status_code=201)
def create_inspection_char(body: InspectionCharCreate,
                            admin=Depends(require_admin)):
    if not body.part_code.strip() or not body.char_name.strip():
        raise HTTPException(400, "part_code and char_name required")
    with get_conn() as conn:
        _ensure_inspection_tables(conn)
        cur = dict_cursor(conn)
        try:
            cur.execute("""
                INSERT INTO mes_quality_inspection_chars
                    (part_code, char_name, target, lower_tol, upper_tol,
                     unit, freq_hours, gauge)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (body.part_code.strip(), body.char_name.strip(),
                  body.target, body.lower_tol, body.upper_tol,
                  body.unit, max(1, body.freq_hours), body.gauge))
            row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, f"Insert failed: {e}")
        return row


@router.put("/inspection-chars/{char_id}")
def update_inspection_char(char_id: int, body: InspectionCharUpdate,
                            admin=Depends(require_admin)):
    sets, params = [], []
    for f in ("char_name","target","lower_tol","upper_tol","unit",
              "freq_hours","gauge","is_active"):
        v = getattr(body, f)
        if v is not None:
            sets.append(f"{f} = %s"); params.append(v)
    if not sets:
        return {"ok": True, "noop": True}
    params.append(char_id)
    with get_conn() as conn:
        _ensure_inspection_tables(conn)
        cur = conn.cursor()
        cur.execute(
            f"UPDATE mes_quality_inspection_chars SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        conn.commit()
    return {"ok": True}


@router.get("/inspection-log")
def list_inspection_log(days:    int = Query(7, ge=1, le=365),
                         line_id: Optional[int] = None,
                         status:  Optional[str] = None,
                         user=Depends(get_current_user)):
    cutoff = date.today() - timedelta(days=days)
    where, params = ["l.ts_measured >= %s"], [cutoff]
    if line_id is not None:
        where.append("l.line_id = %s"); params.append(line_id)
    if status:
        where.append("l.status = %s");  params.append(status.upper())
    with get_conn() as conn:
        _ensure_inspection_tables(conn)
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT l.*, ln.line_name, z.zone_name
              FROM mes_quality_inspection_log l
              LEFT JOIN mes_lines ln ON ln.id = l.line_id
              LEFT JOIN mes_zones z  ON z.id  = l.zone_id
             WHERE {' AND '.join(where)}
             ORDER BY l.ts_measured DESC
             LIMIT 500
        """, params)
        return cur.fetchall()


@router.post("/inspection-log", status_code=201)
def create_inspection_log(body: InspectionLogCreate,
                           user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_inspection_tables(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM mes_quality_inspection_chars WHERE id = %s",
                    (body.char_id,))
        ch = cur.fetchone()
        if not ch:
            raise HTTPException(404, f"char_id {body.char_id} not found")
        # Status auto from tolerance band — NG only if outside.
        m = float(body.measured)
        ok = True
        if ch.get("lower_tol") is not None and m < float(ch["lower_tol"]):
            ok = False
        if ch.get("upper_tol") is not None and m > float(ch["upper_tol"]):
            ok = False
        status = "OK" if ok else "NG"
        # Auto-resolve zone from line
        cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s", (body.line_id,))
        ln = cur.fetchone(); zone_id = (ln or {}).get("zone_id")
        cur.execute("""
            INSERT INTO mes_quality_inspection_log
                (line_id, zone_id, part_code, char_id, char_name,
                 target, lower_tol, upper_tol, unit,
                 measured, status, inspector, shift_name, notes)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s)
            RETURNING *
        """, (body.line_id, zone_id, ch.get("part_code"),
              body.char_id, ch.get("char_name"),
              ch.get("target"), ch.get("lower_tol"), ch.get("upper_tol"),
              ch.get("unit"),
              m, status, user.get("username") or "unknown",
              body.shift_name, body.notes))
        row = cur.fetchone()
        conn.commit()
        return row


# ════════════════════════════════════════════════════════════════════════
# Sprint-2 — First-Piece / Last-Piece Inspection
# ────────────────────────────────────────────────────────────────────────
# Mandatory at model change.  Quality inspector logs result + which
# characteristics were verified (free-form JSON checklist).
# ════════════════════════════════════════════════════════════════════════

def _ensure_flp_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_first_last_piece (
            id            SERIAL PRIMARY KEY,
            line_id       INTEGER NOT NULL,
            zone_id       INTEGER,
            part_code     VARCHAR(80),
            part_name     VARCHAR(200),
            model         VARCHAR(80),
            piece_type    VARCHAR(10) NOT NULL,   -- FIRST / LAST
            checked_chars JSONB,                  -- free-form {char_name: measured | OK/NG}
            status        VARCHAR(10) NOT NULL DEFAULT 'OK', -- OK / NG
            inspector     VARCHAR(80) NOT NULL,
            shift_name    VARCHAR(10),
            ts_checked    TIMESTAMP NOT NULL DEFAULT NOW(),
            notes         TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mes_quality_flp_line_ts
            ON mes_quality_first_last_piece (line_id, ts_checked DESC)
    """)
    conn.commit()
    cur.close()


class FLPCreate(BaseModel):
    line_id:       int
    part_code:     Optional[str]  = None
    part_name:     Optional[str]  = None
    model:         Optional[str]  = None
    piece_type:    str            = "FIRST"   # FIRST or LAST
    checked_chars: Optional[Dict[str, Any]] = None
    status:        str            = "OK"      # OK or NG
    shift_name:    Optional[str]  = None
    notes:         Optional[str]  = None


@router.get("/first-last-piece")
def list_flp(days:    int = Query(30, ge=1, le=365),
              line_id: Optional[int] = None,
              user=Depends(get_current_user)):
    cutoff = date.today() - timedelta(days=days)
    where, params = ["p.ts_checked >= %s"], [cutoff]
    if line_id is not None:
        where.append("p.line_id = %s"); params.append(line_id)
    with get_conn() as conn:
        _ensure_flp_tables(conn)
        cur = dict_cursor(conn)
        cur.execute(f"""
            SELECT p.*, ln.line_name, z.zone_name
              FROM mes_quality_first_last_piece p
              LEFT JOIN mes_lines ln ON ln.id = p.line_id
              LEFT JOIN mes_zones z  ON z.id  = p.zone_id
             WHERE {' AND '.join(where)}
             ORDER BY p.ts_checked DESC
             LIMIT 500
        """, params)
        return cur.fetchall()


@router.post("/first-last-piece", status_code=201)
def create_flp(body: FLPCreate, user=Depends(get_current_user)):
    pt = (body.piece_type or "FIRST").upper()
    if pt not in ("FIRST", "LAST"):
        raise HTTPException(400, "piece_type must be FIRST or LAST")
    st = (body.status or "OK").upper()
    if st not in ("OK", "NG"):
        raise HTTPException(400, "status must be OK or NG")
    with get_conn() as conn:
        _ensure_flp_tables(conn)
        cur = dict_cursor(conn)
        cur.execute("SELECT zone_id FROM mes_lines WHERE id = %s", (body.line_id,))
        ln = cur.fetchone(); zone_id = (ln or {}).get("zone_id")
        cur.execute("""
            INSERT INTO mes_quality_first_last_piece
                (line_id, zone_id, part_code, part_name, model,
                 piece_type, checked_chars, status,
                 inspector, shift_name, notes)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s)
            RETURNING *
        """, (body.line_id, zone_id, body.part_code, body.part_name, body.model,
              pt, Json(body.checked_chars or {}), st,
              user.get("username") or "unknown",
              body.shift_name, body.notes))
        row = cur.fetchone()
        conn.commit()
        return row


# ════════════════════════════════════════════════════════════════════════
# Sprint-2 — PPM (Parts-Per-Million) Tracking
# ────────────────────────────────────────────────────────────────────────
# PPM = (NCR rejected qty / total produced) * 1,000,000.
# Produced count comes from each line's `<db_table>_ct_log` table
# (one row per cycle).  Returns per-line + overall over the window.
# ════════════════════════════════════════════════════════════════════════

@router.get("/ppm")
def ppm(days:    int = Query(30, ge=1, le=365),
         line_id: Optional[int] = None,
         user=Depends(get_current_user)):
    cutoff = date.today() - timedelta(days=days)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # 1. Lines (filtered if requested) + their per-line table name
        if line_id is not None:
            cur.execute("""
                SELECT l.id, l.line_name, l.db_table_name, z.zone_name
                  FROM mes_lines l
                  LEFT JOIN mes_zones z ON z.id = l.zone_id
                 WHERE l.id = %s
            """, (line_id,))
        else:
            cur.execute("""
                SELECT l.id, l.line_name, l.db_table_name, z.zone_name
                  FROM mes_lines l
                  LEFT JOIN mes_zones z ON z.id = l.zone_id
                 WHERE l.db_table_name IS NOT NULL AND l.db_table_name <> ''
                 ORDER BY l.line_name
            """)
        lines_rows = cur.fetchall()

        # 2. NCR rejected per line (window)
        cur.execute("""
            SELECT line_id, COALESCE(SUM(qty_rejected),0)::int AS rejected
              FROM mes_quality_ncr
             WHERE status <> 'VOID'
               AND raised_at >= %s
             GROUP BY line_id
        """, (cutoff,))
        ncr_by_line = {r["line_id"]: int(r["rejected"]) for r in cur.fetchall()}

        out = []
        total_prod = total_rej = 0
        for ln in lines_rows:
            tbl = (ln.get("db_table_name") or "").strip()
            produced = 0
            if tbl:
                ct_tbl = tbl + "_ct_log"
                try:
                    cur.execute("SELECT to_regclass(%s) AS r", (ct_tbl,))
                    if (cur.fetchone() or {}).get("r"):
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {ct_tbl} WHERE ts >= %s",
                            (cutoff,),
                        )
                        produced = int((cur.fetchone() or {}).get("n") or 0)
                except Exception:
                    produced = 0
            rejected = int(ncr_by_line.get(ln["id"], 0))
            ppm_val  = round((rejected / produced) * 1_000_000, 1) if produced else 0.0
            total_prod += produced
            total_rej  += rejected
            out.append({
                "line_id":    ln["id"],
                "line_name":  ln["line_name"],
                "zone_name":  ln["zone_name"],
                "produced":   produced,
                "rejected":   rejected,
                "ppm":        ppm_val,
            })

        overall_ppm = round((total_rej / total_prod) * 1_000_000, 1) if total_prod else 0.0
        # Sort by PPM desc so worst lines bubble up
        out.sort(key=lambda x: x["ppm"], reverse=True)
        return {
            "days":            days,
            "line_id":         line_id,
            "total_produced":  total_prod,
            "total_rejected":  total_rej,
            "overall_ppm":     overall_ppm,
            "lines":           out,
        }


# ════════════════════════════════════════════════════════════════════════
# Sprint-2 — Control Plan Viewer (per-part PDF)
# ────────────────────────────────────────────────────────────────────────
# Admin uploads PDF per part_code (gauges, measurements, frequencies,
# reactions).  Operator fetches the active version by part.  PDFs are
# stored in-row as BYTEA — matches the 5S photo pattern.
# ════════════════════════════════════════════════════════════════════════

def _ensure_control_plan_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mes_quality_control_plans (
            id           SERIAL PRIMARY KEY,
            part_code    VARCHAR(80) NOT NULL,
            part_name    VARCHAR(200),
            version      VARCHAR(20),
            file_name    VARCHAR(255) NOT NULL,
            file_data    BYTEA NOT NULL,
            file_size    INTEGER,
            mime_type    VARCHAR(80) DEFAULT 'application/pdf',
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            uploaded_by  VARCHAR(80) NOT NULL,
            uploaded_at  TIMESTAMP DEFAULT NOW(),
            notes        TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mes_quality_control_plans_part
            ON mes_quality_control_plans (part_code, is_active)
    """)
    conn.commit()
    cur.close()


@router.get("/control-plans")
def list_control_plans(part_code:   Optional[str] = None,
                        active_only: bool = True,
                        user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_control_plan_tables(conn)
        cur = dict_cursor(conn)
        where, params = [], []
        if active_only: where.append("is_active = TRUE")
        if part_code:   where.append("part_code = %s"); params.append(part_code)
        sql = """SELECT id, part_code, part_name, version, file_name,
                        file_size, mime_type, is_active,
                        uploaded_by, uploaded_at, notes
                   FROM mes_quality_control_plans"""
        if where: sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY part_code, uploaded_at DESC"
        cur.execute(sql, params)
        return cur.fetchall()


@router.post("/control-plans", status_code=201)
async def upload_control_plan(part_code: str = Form(...),
                                part_name: Optional[str] = Form(None),
                                version:   Optional[str] = Form(None),
                                notes:     Optional[str] = Form(None),
                                file:      UploadFile = File(...),
                                admin=Depends(require_admin)):
    pc = (part_code or "").strip()
    if not pc:
        raise HTTPException(400, "part_code is required")
    raw = await file.read()
    if not raw or len(raw) > 20 * 1024 * 1024:  # 20 MB max
        raise HTTPException(400, "File must be 1 byte – 20 MB")
    mime = file.content_type or "application/pdf"
    with get_conn() as conn:
        _ensure_control_plan_tables(conn)
        cur = dict_cursor(conn)
        # Deactivate prior active versions for this part — so the latest
        # upload becomes the operator's "current" control plan.
        cur.execute(
            "UPDATE mes_quality_control_plans SET is_active = FALSE "
            " WHERE part_code = %s AND is_active = TRUE",
            (pc,),
        )
        cur.execute("""
            INSERT INTO mes_quality_control_plans
                (part_code, part_name, version, file_name, file_data,
                 file_size, mime_type, uploaded_by, notes)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s)
            RETURNING id, part_code, part_name, version, file_name,
                      file_size, mime_type, is_active, uploaded_by,
                      uploaded_at, notes
        """, (pc, part_name, version, file.filename, raw,
              len(raw), mime,
              admin.get("username") or "admin", notes))
        row = cur.fetchone()
        conn.commit()
        return row


@router.get("/control-plans/{cp_id}/download")
def download_control_plan(cp_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _ensure_control_plan_tables(conn)
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT file_name, file_data, mime_type "
            "  FROM mes_quality_control_plans WHERE id = %s",
            (cp_id,),
        )
        r = cur.fetchone()
        if not r or not r.get("file_data"):
            raise HTTPException(404, "control plan not found")
    return Response(
        bytes(r["file_data"]),
        media_type=r.get("mime_type") or "application/pdf",
        headers={"Content-Disposition": f'inline; filename="{r["file_name"]}"'},
    )


@router.delete("/control-plans/{cp_id}")
def delete_control_plan(cp_id: int, admin=Depends(require_admin)):
    with get_conn() as conn:
        _ensure_control_plan_tables(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_quality_control_plans WHERE id = %s",
                    (cp_id,))
        conn.commit()
    return {"ok": True}
