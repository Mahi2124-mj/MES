"""
routers/five_s.py
=================
5S daily audit — per-line × per-day checklist with photo upload.

5S pillars (Japanese → English):
   Seiri    → Sort        (remove unneeded items)
   Seiton   → Set in Order (a place for everything)
   Seiso    → Shine       (clean and inspect)
   Seiketsu → Standardize (apply 5S consistently)
   Shitsuke → Sustain     (audit and improve)

Each line's audit row has 5 scores 0-5 (or N/A) + optional photo + remark
per pillar.  Audit owner = line incharge.  Quality dept can review the
monthly aggregate (mes_5s_audits).

Tables
------
  mes_5s_items     line-customisable checklist items per pillar
  mes_5s_audits    daily audit row (one per line × date)
  mes_5s_photos    optional photo URLs per audit row
"""
from __future__ import annotations

import io, os, re, base64
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/5s", tags=["5s"])

PILLARS = ["sort", "set_in_order", "shine", "standardize", "sustain"]
PILLAR_LABELS = {
    "sort":          "Sort (Seiri)",
    "set_in_order":  "Set in Order (Seiton)",
    "shine":         "Shine (Seiso)",
    "standardize":   "Standardize (Seiketsu)",
    "sustain":       "Sustain (Shitsuke)",
}


def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_5s_items (
                id           SERIAL PRIMARY KEY,
                line_id      INTEGER,    -- NULL = applies to all lines
                pillar       VARCHAR(20) NOT NULL,
                item_text    TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                created_at   TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_5s_audits (
                id              SERIAL PRIMARY KEY,
                line_id         INTEGER NOT NULL,
                audit_date      DATE NOT NULL,
                shift_name      VARCHAR(10),
                auditor         VARCHAR(120),
                sort_score        INTEGER,
                set_in_order_score INTEGER,
                shine_score       INTEGER,
                standardize_score INTEGER,
                sustain_score     INTEGER,
                sort_remark        TEXT,
                set_in_order_remark TEXT,
                shine_remark       TEXT,
                standardize_remark TEXT,
                sustain_remark     TEXT,
                total_score        INTEGER GENERATED ALWAYS AS (
                    COALESCE(sort_score,0) + COALESCE(set_in_order_score,0) +
                    COALESCE(shine_score,0) + COALESCE(standardize_score,0) +
                    COALESCE(sustain_score,0)
                ) STORED,
                created_at      TIMESTAMP DEFAULT NOW(),
                updated_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE (line_id, audit_date)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_5s_audit_lookup ON mes_5s_audits (line_id, audit_date)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_5s_photos (
                id            SERIAL PRIMARY KEY,
                audit_id      INTEGER NOT NULL REFERENCES mes_5s_audits(id) ON DELETE CASCADE,
                pillar        VARCHAR(20) NOT NULL,
                photo_data    BYTEA,           -- inline storage (small JPGs)
                photo_url     TEXT,            -- or external URL
                caption       TEXT,
                uploaded_at   TIMESTAMP DEFAULT NOW(),
                uploaded_by   VARCHAR(120)
            )
        """)
        # Default checklist items (one-time seed)
        cur.execute("SELECT COUNT(*) FROM mes_5s_items")
        if cur.fetchone()[0] == 0:
            DEFAULTS = [
                ("sort",         "No unused tools / materials at workstation",   1),
                ("sort",         "Scrap bin emptied / disposed properly",        2),
                ("set_in_order", "Tools in marked locations",                    3),
                ("set_in_order", "Materials within reach, labeled by part code", 4),
                ("shine",        "Machine surfaces wiped / oil leaks cleaned",   5),
                ("shine",        "Floor area swept, no debris under conveyor",   6),
                ("standardize",  "Visual control labels intact (red/yellow/green)", 7),
                ("standardize",  "Maintenance schedule visible at the line",     8),
                ("sustain",      "Operator following SOP without prompting",     9),
                ("sustain",      "Yesterday's 5S action items closed out",      10),
            ]
            for p, txt, ord_ in DEFAULTS:
                cur.execute("""
                    INSERT INTO mes_5s_items (line_id, pillar, item_text, display_order)
                    VALUES (NULL, %s, %s, %s)
                """, (p, txt, ord_))
        conn.commit()


# ════════════════════════════════════════════════════════════════════
#  Checklist items
# ════════════════════════════════════════════════════════════════════
@router.get("/items")
def list_items(line_id: Optional[int] = None, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if line_id is not None:
            cur.execute("""SELECT * FROM mes_5s_items
                            WHERE is_active = TRUE
                              AND (line_id IS NULL OR line_id = %s)
                            ORDER BY display_order, id""", (line_id,))
        else:
            cur.execute("""SELECT * FROM mes_5s_items
                            WHERE is_active = TRUE
                            ORDER BY pillar, display_order, id""")
        return cur.fetchall()


class ItemUpsert(BaseModel):
    pillar:         str
    item_text:      str
    line_id:        Optional[int] = None
    display_order:  int = 0
    is_active:      bool = True


@router.post("/items", status_code=201)
def upsert_item(body: ItemUpsert, admin=Depends(require_admin)):
    _ensure_tables()
    if body.pillar not in PILLARS:
        raise HTTPException(400, f"pillar must be one of {PILLARS}")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_5s_items (line_id, pillar, item_text, display_order, is_active)
            VALUES (%s,%s,%s,%s,%s)
            RETURNING id
        """, (body.line_id, body.pillar, body.item_text.strip(),
              body.display_order, body.is_active))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id, "ok": True}


@router.delete("/items/{item_id}")
def delete_item(item_id: int, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE mes_5s_items SET is_active = FALSE WHERE id = %s", (item_id,))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Audit (daily)
# ════════════════════════════════════════════════════════════════════
class AuditUpsert(BaseModel):
    line_id:    int
    audit_date: str           # YYYY-MM-DD
    shift_name: Optional[str] = None
    auditor:    Optional[str] = None
    sort_score:        Optional[int] = None
    set_in_order_score: Optional[int] = None
    shine_score:       Optional[int] = None
    standardize_score: Optional[int] = None
    sustain_score:     Optional[int] = None
    sort_remark:        Optional[str] = None
    set_in_order_remark: Optional[str] = None
    shine_remark:       Optional[str] = None
    standardize_remark: Optional[str] = None
    sustain_remark:     Optional[str] = None


def _clamp_score(v):
    if v is None: return None
    try:
        x = int(v)
        return max(0, min(5, x))
    except (ValueError, TypeError):
        return None


@router.get("/audits")
def list_audits(line_id: Optional[int] = None,
                 date_from: Optional[str] = None,
                 date_to:   Optional[str] = None,
                 user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        sql = """SELECT a.*, l.line_name,
                        (SELECT COUNT(*) FROM mes_5s_photos WHERE audit_id = a.id) AS photo_count
                   FROM mes_5s_audits a
              LEFT JOIN mes_lines l ON l.id = a.line_id
                  WHERE 1=1"""
        params: list = []
        if line_id is not None:
            sql += " AND a.line_id = %s"; params.append(line_id)
        if date_from:
            sql += " AND a.audit_date >= %s"; params.append(date_from)
        if date_to:
            sql += " AND a.audit_date <= %s"; params.append(date_to)
        sql += " ORDER BY a.audit_date DESC, a.line_id"
        cur.execute(sql, tuple(params))
        return cur.fetchall()


@router.post("/audits", status_code=201)
def upsert_audit(body: AuditUpsert, user=Depends(get_current_user)):
    _ensure_tables()
    auditor = body.auditor or (user.get("username") if isinstance(user, dict) else "operator")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_5s_audits
                (line_id, audit_date, shift_name, auditor,
                 sort_score, set_in_order_score, shine_score,
                 standardize_score, sustain_score,
                 sort_remark, set_in_order_remark, shine_remark,
                 standardize_remark, sustain_remark, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (line_id, audit_date) DO UPDATE
                SET shift_name          = COALESCE(EXCLUDED.shift_name, mes_5s_audits.shift_name),
                    auditor             = COALESCE(EXCLUDED.auditor, mes_5s_audits.auditor),
                    sort_score          = COALESCE(EXCLUDED.sort_score, mes_5s_audits.sort_score),
                    set_in_order_score  = COALESCE(EXCLUDED.set_in_order_score, mes_5s_audits.set_in_order_score),
                    shine_score         = COALESCE(EXCLUDED.shine_score, mes_5s_audits.shine_score),
                    standardize_score   = COALESCE(EXCLUDED.standardize_score, mes_5s_audits.standardize_score),
                    sustain_score       = COALESCE(EXCLUDED.sustain_score, mes_5s_audits.sustain_score),
                    sort_remark         = COALESCE(EXCLUDED.sort_remark, mes_5s_audits.sort_remark),
                    set_in_order_remark = COALESCE(EXCLUDED.set_in_order_remark, mes_5s_audits.set_in_order_remark),
                    shine_remark        = COALESCE(EXCLUDED.shine_remark, mes_5s_audits.shine_remark),
                    standardize_remark  = COALESCE(EXCLUDED.standardize_remark, mes_5s_audits.standardize_remark),
                    sustain_remark      = COALESCE(EXCLUDED.sustain_remark, mes_5s_audits.sustain_remark),
                    updated_at          = NOW()
            RETURNING id
        """, (body.line_id, body.audit_date, body.shift_name, auditor,
              _clamp_score(body.sort_score), _clamp_score(body.set_in_order_score),
              _clamp_score(body.shine_score), _clamp_score(body.standardize_score),
              _clamp_score(body.sustain_score),
              body.sort_remark, body.set_in_order_remark, body.shine_remark,
              body.standardize_remark, body.sustain_remark))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id, "ok": True}


# ════════════════════════════════════════════════════════════════════
#  Photo upload
# ════════════════════════════════════════════════════════════════════
@router.post("/audits/{audit_id}/photos", status_code=201)
async def upload_photo(audit_id: int,
                        pillar: str = Form(...),
                        caption: Optional[str] = Form(None),
                        file: UploadFile = File(...),
                        user=Depends(get_current_user)):
    _ensure_tables()
    if pillar not in PILLARS:
        raise HTTPException(400, f"pillar must be one of {PILLARS}")
    raw = await file.read()
    if not raw or len(raw) > 5 * 1024 * 1024:  # 5 MB max
        raise HTTPException(400, "Photo must be 1 byte – 5 MB")
    uploader = user.get("username") if isinstance(user, dict) else "operator"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_5s_photos (audit_id, pillar, photo_data, caption, uploaded_by)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (audit_id, pillar, raw, caption, uploader))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id, "ok": True}


@router.get("/audits/{audit_id}/photos")
def list_photos(audit_id: int, user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT id, pillar, caption, uploaded_at, uploaded_by,
                              (photo_data IS NOT NULL) AS has_inline
                         FROM mes_5s_photos
                        WHERE audit_id = %s
                        ORDER BY uploaded_at""", (audit_id,))
        return cur.fetchall()


@router.get("/photos/{photo_id}")
def get_photo(photo_id: int, user=Depends(get_current_user)):
    _ensure_tables()
    from fastapi.responses import Response
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT photo_data FROM mes_5s_photos WHERE id = %s", (photo_id,))
        r = cur.fetchone()
        if not r or not r[0]:
            raise HTTPException(404, "photo not found")
    return Response(bytes(r[0]), media_type="image/jpeg")


@router.delete("/photos/{photo_id}")
def delete_photo(photo_id: int, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_5s_photos WHERE id = %s", (photo_id,))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Monthly summary (Quality dept review)
# ════════════════════════════════════════════════════════════════════
@router.get("/summary")
def monthly_summary(year_month: str = Query(...),
                     line_id: Optional[int] = None,
                     user=Depends(get_current_user)):
    """Monthly average score per pillar + per line."""
    _ensure_tables()
    if not re.match(r"\d{4}-\d{2}", year_month):
        raise HTTPException(400, "year_month must be YYYY-MM")
    sql = """
        SELECT a.line_id, l.line_name,
               COUNT(*) AS audits,
               ROUND(AVG(a.sort_score)::numeric,        1) AS sort_avg,
               ROUND(AVG(a.set_in_order_score)::numeric, 1) AS set_in_order_avg,
               ROUND(AVG(a.shine_score)::numeric,       1) AS shine_avg,
               ROUND(AVG(a.standardize_score)::numeric, 1) AS standardize_avg,
               ROUND(AVG(a.sustain_score)::numeric,     1) AS sustain_avg,
               ROUND(AVG(a.total_score)::numeric,       1) AS total_avg
          FROM mes_5s_audits a
     LEFT JOIN mes_lines l ON l.id = a.line_id
         WHERE TO_CHAR(a.audit_date, 'YYYY-MM') = %s
    """
    params: list = [year_month]
    if line_id is not None:
        sql += " AND a.line_id = %s"; params.append(line_id)
    sql += " GROUP BY a.line_id, l.line_name ORDER BY total_avg DESC NULLS LAST"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(sql, tuple(params))
        return cur.fetchall()
