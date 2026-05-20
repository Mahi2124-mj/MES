"""
routers/heijunka.py
===================
Production-leveling (Heijunka) schedule.

Per line × per day, the supervisor allocates which FG model(s) run in
which shift slot.  The Kanban monthly plan already has the demand mix
per FG; Heijunka turns that monthly demand into a smoothed daily plan
so peaks and troughs don't pile up at month-end.

Schema
------
  mes_heijunka_plan
    line_id, plan_date, shift_name, slot_seq, fg_part_id, qty_target

Each (line, date) typically has multiple slots per shift — operator can
say "Shift A: model X for 4 hrs (≈480 pcs), then model Y for 4.75 hrs
(≈570 pcs)".  Slots are numbered 1..N per (line, date, shift).

Auto-suggest helper
-------------------
Given a target month + line, the auto-suggest endpoint distributes the
monthly plan across the working days of the month proportional to each
FG's monthly_plan.  Operator can then edit individual slots.
"""
from __future__ import annotations

import calendar
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import require_admin, get_current_user

router = APIRouter(prefix="/api/heijunka", tags=["heijunka"])


# ════════════════════════════════════════════════════════════════════
#  Schema
# ════════════════════════════════════════════════════════════════════
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mes_heijunka_plan (
                id           SERIAL PRIMARY KEY,
                line_id      INTEGER NOT NULL,
                plan_date    DATE NOT NULL,
                shift_name   VARCHAR(10) NOT NULL,        -- 'A' | 'B' | 'C'
                slot_seq     INTEGER NOT NULL DEFAULT 1,
                fg_part_id   INTEGER NOT NULL REFERENCES mes_fg_parts(id),
                qty_target   INTEGER NOT NULL DEFAULT 0,
                notes        TEXT,
                created_at   TIMESTAMP DEFAULT NOW(),
                updated_at   TIMESTAMP DEFAULT NOW(),
                UNIQUE (line_id, plan_date, shift_name, slot_seq)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_heij_lookup ON mes_heijunka_plan (line_id, plan_date)")
        conn.commit()


# ════════════════════════════════════════════════════════════════════
#  CRUD
# ════════════════════════════════════════════════════════════════════
@router.get("/plan")
def list_plan(line_id: int = Query(...),
               date_from: str = Query(...),
               date_to:   str = Query(...),
               user=Depends(get_current_user)):
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT h.*, p.tbdi_part_no, p.tbdi_new_part_no, p.description,
                   p.model, p.packing_std_qty
              FROM mes_heijunka_plan h
              JOIN mes_fg_parts p ON p.id = h.fg_part_id
             WHERE h.line_id = %s
               AND h.plan_date BETWEEN %s AND %s
             ORDER BY h.plan_date, h.shift_name, h.slot_seq
        """, (line_id, date_from, date_to))
        return cur.fetchall()


class SlotUpsert(BaseModel):
    line_id:    int
    plan_date:  str           # YYYY-MM-DD
    shift_name: str           # 'A' | 'B' | 'C'
    slot_seq:   int = 1
    fg_part_id: int
    qty_target: int = 0
    notes:      Optional[str] = None


@router.post("/plan", status_code=201)
def upsert_slot(body: SlotUpsert, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_heijunka_plan
                (line_id, plan_date, shift_name, slot_seq,
                 fg_part_id, qty_target, notes, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (line_id, plan_date, shift_name, slot_seq) DO UPDATE
                SET fg_part_id = EXCLUDED.fg_part_id,
                    qty_target = EXCLUDED.qty_target,
                    notes      = EXCLUDED.notes,
                    updated_at = NOW()
            RETURNING id
        """, (body.line_id, body.plan_date, body.shift_name,
              max(1, body.slot_seq), body.fg_part_id,
              max(0, body.qty_target), body.notes))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id, "ok": True}


@router.delete("/plan/{slot_id}")
def delete_slot(slot_id: int, admin=Depends(require_admin)):
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mes_heijunka_plan WHERE id = %s", (slot_id,))
        conn.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════
#  Auto-suggest — distribute monthly plan across days
# ════════════════════════════════════════════════════════════════════
class AutoSuggestBody(BaseModel):
    line_id:    int
    year_month: str           # YYYY-MM
    overwrite:  bool = False  # if False, only insert missing days


@router.post("/auto-suggest")
def auto_suggest(body: AutoSuggestBody, admin=Depends(require_admin)):
    """For each working day of the month (Mon-Sat by default), proportionally
    distribute each FG's monthly shift-A/B plan as one slot per shift.
    Doesn't overwrite manually-edited slots unless `overwrite=True`."""
    _ensure_tables()
    import re
    if not re.match(r"\d{4}-\d{2}", body.year_month):
        raise HTTPException(400, "year_month must be YYYY-MM")
    yr, mo = map(int, body.year_month.split("-"))
    _, days_in_month = calendar.monthrange(yr, mo)
    # Working days: Mon-Sat (exclude Sunday)
    working_days = [date(yr, mo, d) for d in range(1, days_in_month + 1)
                    if date(yr, mo, d).weekday() != 6]
    n_days = len(working_days)
    if n_days == 0:
        return {"inserted": 0, "skipped": 0, "note": "no working days in month"}

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT mp.fg_part_id, mp.shift_a_plan, mp.shift_b_plan
              FROM mes_monthly_plan mp
              JOIN mes_fg_parts p ON p.id = mp.fg_part_id
             WHERE mp.year_month = %s
               AND p.line_id = %s
               AND p.is_active = TRUE
        """, (body.year_month, body.line_id))
        rows = cur.fetchall()

        inserted = skipped = 0
        cur2 = conn.cursor()
        for r in rows:
            fg_id  = r["fg_part_id"]
            plan_a = int(r["shift_a_plan"] or 0)
            plan_b = int(r["shift_b_plan"] or 0)
            # Spread plan equally across working days; remainder lands
            # in the first ceil(rem) days so totals match exactly.
            for shift_nm, total in (("A", plan_a), ("B", plan_b)):
                if total <= 0:
                    continue
                per_day = total // n_days
                rem     = total - per_day * n_days
                for idx, dd in enumerate(working_days):
                    qty = per_day + (1 if idx < rem else 0)
                    if qty <= 0:
                        continue
                    if not body.overwrite:
                        # Skip if a slot already exists for that
                        # (line, date, shift, slot=1)
                        cur2.execute("""SELECT 1 FROM mes_heijunka_plan
                                         WHERE line_id=%s AND plan_date=%s
                                           AND shift_name=%s AND slot_seq=1""",
                                      (body.line_id, dd, shift_nm))
                        if cur2.fetchone():
                            skipped += 1
                            continue
                    cur2.execute("""
                        INSERT INTO mes_heijunka_plan
                            (line_id, plan_date, shift_name, slot_seq,
                             fg_part_id, qty_target, notes, updated_at)
                        VALUES (%s,%s,%s,1,%s,%s,'auto-suggest',NOW())
                        ON CONFLICT (line_id, plan_date, shift_name, slot_seq) DO UPDATE
                            SET fg_part_id = EXCLUDED.fg_part_id,
                                qty_target = EXCLUDED.qty_target,
                                updated_at = NOW()
                    """, (body.line_id, dd, shift_nm, fg_id, qty))
                    inserted += 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped,
            "working_days": n_days, "fg_parts": len(rows)}


# ════════════════════════════════════════════════════════════════════
#  Daily board — today vs achieved (joins to dashboard count)
# ════════════════════════════════════════════════════════════════════
@router.get("/board")
def daily_board(line_id: int = Query(...),
                 on_date: Optional[str] = None,
                 user=Depends(get_current_user)):
    """Per-shift slot list with planned vs achieved (joined to today's
    ct_log via fg_part_id → mes_fg_model_link → model_number).  For now
    we attribute total day cycles to each slot pro-rata — replaceable
    with actual model-based slicing when sub-PLC model tracking lands."""
    _ensure_tables()
    d = on_date or date.today().isoformat()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT h.*, p.tbdi_part_no, p.tbdi_new_part_no, p.description,
                   p.model, p.packing_std_qty, p.customer_part_no
              FROM mes_heijunka_plan h
              JOIN mes_fg_parts p ON p.id = h.fg_part_id
             WHERE h.line_id = %s AND h.plan_date = %s
             ORDER BY h.shift_name, h.slot_seq
        """, (line_id, d))
        rows = cur.fetchall()

        # Total OK + NG cycles on the line for the day (proxy for "achieved")
        cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
        ln = cur.fetchone()
        ct_tbl = (ln["db_table_name"] + "_ct_log") if ln else None
        day_cycles_by_shift: dict = {}
        if ct_tbl:
            try:
                cur.execute(f"""
                    SELECT shift_name, COUNT(*) AS n
                      FROM {ct_tbl}
                     WHERE record_date = %s
                  GROUP BY shift_name
                """, (d,))
                day_cycles_by_shift = {r["shift_name"]: r["n"] for r in cur.fetchall()}
            except Exception:
                pass

    # Pro-rata achieved per slot
    slots_by_shift = {}
    plan_by_shift  = {}
    for r in rows:
        sh = r["shift_name"]
        slots_by_shift.setdefault(sh, []).append(r)
        plan_by_shift[sh] = plan_by_shift.get(sh, 0) + int(r["qty_target"] or 0)

    enriched = []
    for sh, slots in slots_by_shift.items():
        sh_total = day_cycles_by_shift.get(sh, 0) or 0
        sh_plan  = plan_by_shift.get(sh, 0) or 0
        for s in slots:
            share = (int(s["qty_target"] or 0) / sh_plan) if sh_plan > 0 else 0
            s = dict(s)
            s["achieved"] = int(sh_total * share)
            s["plan_pct"] = round((s["achieved"] / s["qty_target"]) * 100, 1) if s["qty_target"] > 0 else 0
            enriched.append(s)
    enriched.sort(key=lambda x: (x["shift_name"], x["slot_seq"]))
    return enriched
