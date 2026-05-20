"""
routers/anything_wrong.py
=========================
Single consolidated alert dashboard — "Genchi Genbutsu" board.

Aggregates every open problem on the shop floor onto ONE screen so the
supervisor / Section Incharge doesn't have to hop between Maintenance,
Quality, Manpower, Store and Process Graphs to see what's wrong.

Sources rolled up:
  - Open breakdown tickets        (mes_breakdowns state=OPEN)
  - Pending manpower alerts       (mes_manpower_alerts resolved_at IS NULL)
  - Poka-Yoke active bypasses     (mes_py_bypass_events without ack)
  - Low / out of stock materials  (computed from mes_store_grn − issues)
  - Skill mismatches on shift     (mes_manpower_allocations skill_match_flag=false)
  - Lines below target OEE        (today's roll-up < per-line kpi_target)
  - Ready FG awaiting load        (mes_dispatch_lots status='READY' > N hrs)

All endpoints are read-only.  Frontend polls every 30 s.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Query
from database import get_conn, dict_cursor
from auth import get_current_user

router = APIRouter(prefix="/api/anything-wrong", tags=["anything-wrong"])


def _safe_fetch(cur, sql: str, params: tuple = ()) -> List[dict]:
    """Run a SELECT; on missing-table errors return [] so the board still
    renders even when one source's table hasn't been created yet."""
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception as exc:
        # rollback is mandatory in psycopg2 after a failed statement
        try: cur.connection.rollback()
        except Exception: pass
        print(f"[ANYTHING-WRONG] source skipped: {exc}")
        return []


@router.get("/summary")
def summary(line_id: Optional[int] = Query(None),
             user=Depends(get_current_user)) -> Dict[str, Any]:
    """Counts only — fast roll-up for the top tile band."""
    counts = {
        "breakdowns": 0,
        "manpower_alerts": 0,
        "py_bypass": 0,
        "low_stock": 0,
        "skill_mismatch": 0,
        "ready_lots_stale": 0,
    }
    with get_conn() as conn:
        cur = dict_cursor(conn)
        line_clause = " AND line_id = %s" if line_id else ""
        params: tuple = (line_id,) if line_id else ()

        rows = _safe_fetch(cur, f"""SELECT COUNT(*) AS n FROM mes_breakdowns
                                     WHERE state = 'OPEN'{line_clause}""", params)
        if rows: counts["breakdowns"] = rows[0]["n"]

        rows = _safe_fetch(cur, f"""SELECT COUNT(*) AS n FROM mes_manpower_alerts
                                     WHERE resolved_at IS NULL{line_clause}""", params)
        if rows: counts["manpower_alerts"] = rows[0]["n"]

        rows = _safe_fetch(cur, """
            SELECT COUNT(*) AS n
              FROM mes_manpower_allocations
             WHERE removed_at IS NULL AND skill_match_flag = FALSE
               AND shift_date = CURRENT_DATE
        """ + (" AND line_id = %s" if line_id else ""),
                            params)
        if rows: counts["skill_mismatch"] = rows[0]["n"]

        # Low-stock — compute SUM(grn) − SUM(issues) ≤ min_stock
        try:
            cur.execute("""
                SELECT COUNT(*) AS n FROM (
                    SELECT m.id,
                           COALESCE(SUM(g.qty),0) - COALESCE(SUM(i.qty),0) AS bal,
                           m.min_stock
                      FROM mes_materials m
                 LEFT JOIN mes_store_grn    g ON g.material_id = m.id
                 LEFT JOIN mes_store_issues i ON i.material_id = m.id
                     WHERE m.is_active = TRUE
                  GROUP BY m.id, m.min_stock
                ) x
                WHERE x.min_stock > 0 AND x.bal <= x.min_stock
            """)
            r = cur.fetchone()
            if r: counts["low_stock"] = r["n"]
        except Exception:
            try: conn.rollback()
            except Exception: pass

        try:
            cur.execute("""
                SELECT COUNT(*) AS n FROM mes_dispatch_lots
                 WHERE status='READY' AND packed_at < NOW() - INTERVAL '2 hours'
            """)
            r = cur.fetchone()
            if r: counts["ready_lots_stale"] = r["n"]
        except Exception:
            try: conn.rollback()
            except Exception: pass

        try:
            cur.execute("""
                SELECT COUNT(*) AS n FROM mes_py_bypass_events
                 WHERE acknowledged_at IS NULL""")
            r = cur.fetchone()
            if r: counts["py_bypass"] = r["n"]
        except Exception:
            try: conn.rollback()
            except Exception: pass

    counts["total"] = sum(counts.values())
    return counts


@router.get("/items")
def items(line_id: Optional[int] = Query(None),
           limit: int = Query(50, ge=1, le=200),
           user=Depends(get_current_user)) -> Dict[str, Any]:
    """Detailed item list — one section per source.  Each item has:
       severity, source, title, detail, fired_at, line_id."""
    out: Dict[str, List[dict]] = {
        "breakdowns": [], "manpower_alerts": [],
        "skill_mismatch": [], "low_stock": [],
        "ready_lots_stale": [], "py_bypass": [],
    }
    with get_conn() as conn:
        cur = dict_cursor(conn)
        # ── Breakdowns ──
        sql = """SELECT b.id, b.line_id, l.line_name, b.machine_name,
                        b.started_at, b.serial_no
                   FROM mes_breakdowns b
              LEFT JOIN mes_lines l ON l.id = b.line_id
                  WHERE b.state = 'OPEN'"""
        params: list = []
        if line_id: sql += " AND b.line_id = %s"; params.append(line_id)
        sql += " ORDER BY b.started_at DESC LIMIT %s"
        params.append(limit)
        for r in _safe_fetch(cur, sql, tuple(params)):
            ln_label = r.get('line_name') or f"Line #{r['line_id']}"
            mach = r.get('machine_name') or r.get('serial_no') or '?'
            out["breakdowns"].append({
                "id":       r["id"],
                "severity": "high",
                "title":    f"BREAKDOWN — {mach}",
                "detail":   f"{ln_label} · open since {r['started_at']:%H:%M}",
                "fired_at": r["started_at"],
                "line_id":  r["line_id"],
            })

        # ── Manpower alerts ──
        sql = """SELECT a.*, p.process_name, o.full_name AS operator_name
                   FROM mes_manpower_alerts a
              LEFT JOIN mes_processes p ON p.id = a.process_id
              LEFT JOIN mes_operators o ON o.id = a.operator_id
                  WHERE a.resolved_at IS NULL"""
        params = []
        if line_id: sql += " AND a.line_id = %s"; params.append(line_id)
        sql += " ORDER BY a.fired_at DESC LIMIT %s"
        params.append(limit)
        for r in _safe_fetch(cur, sql, tuple(params)):
            kind = r.get("alert_kind") or "?"
            sev  = "high" if kind == "ESCALATION" else "medium"
            title = {
                "UNALLOCATED": "Manpower UNALLOCATED",
                "SKILL_MISMATCH": "Manpower skill mismatch",
                "ESCALATION": "Manpower alert ESCALATED",
            }.get(kind, f"Manpower {kind}")
            out["manpower_alerts"].append({
                "id":       r["id"],
                "severity": sev,
                "title":    title,
                "detail":   r.get("context_text") or f"Line #{r['line_id']} · {r.get('process_name') or ''}",
                "fired_at": r["fired_at"],
                "line_id":  r["line_id"],
            })

        # ── Skill mismatch (current shift, active) ──
        sql = """SELECT a.id, a.line_id, p.process_name, o.full_name,
                        p.required_skill_level, o.skill_level AS op_skill,
                        a.allocated_at
                   FROM mes_manpower_allocations a
                   JOIN mes_processes  p ON p.id = a.process_id
                   JOIN mes_operators  o ON o.id = a.operator_id
                  WHERE a.removed_at IS NULL
                    AND a.skill_match_flag = FALSE
                    AND a.shift_date = CURRENT_DATE"""
        params = []
        if line_id: sql += " AND a.line_id = %s"; params.append(line_id)
        sql += " ORDER BY a.allocated_at DESC LIMIT %s"
        params.append(limit)
        for r in _safe_fetch(cur, sql, tuple(params)):
            out["skill_mismatch"].append({
                "id":       r["id"],
                "severity": "medium",
                "title":    f"Skill mismatch — {r['process_name']}",
                "detail":   f"{r['full_name']} (L{r['op_skill']}) on L{r['required_skill_level']}+ process",
                "fired_at": r["allocated_at"],
                "line_id":  r["line_id"],
            })

        # ── Low stock ──
        try:
            cur.execute("""
                SELECT m.id, m.code, m.name, m.uom, m.min_stock,
                       COALESCE(SUM(g.qty),0) - COALESCE(SUM(i.qty),0) AS balance
                  FROM mes_materials m
             LEFT JOIN mes_store_grn    g ON g.material_id = m.id
             LEFT JOIN mes_store_issues i ON i.material_id = m.id
                 WHERE m.is_active = TRUE
              GROUP BY m.id
                HAVING m.min_stock > 0
                   AND (COALESCE(SUM(g.qty),0) - COALESCE(SUM(i.qty),0)) <= m.min_stock
              ORDER BY (COALESCE(SUM(g.qty),0) - COALESCE(SUM(i.qty),0))
                 LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                bal = float(r["balance"] or 0)
                out["low_stock"].append({
                    "id":       r["id"],
                    "severity": "high" if bal <= 0 else "medium",
                    "title":    f"{'OUT' if bal<=0 else 'LOW'} stock — {r['code']}",
                    "detail":   f"{r['name']} · bal {bal:.1f} {r['uom']} (min {float(r['min_stock']):.0f})",
                    "fired_at": datetime.now(),
                    "line_id":  None,
                })
        except Exception:
            try: conn.rollback()
            except Exception: pass

        # ── Ready FG lots > 2 hours old ──
        try:
            cur.execute("""
                SELECT lt.id, lt.lot_no, lt.line_id, l.line_name,
                       p.code AS material_code, p.name AS material_name,
                       lt.qty_packed, lt.packed_at,
                       EXTRACT(EPOCH FROM (NOW() - lt.packed_at))/3600.0 AS hrs_ago
                  FROM mes_dispatch_lots lt
                  JOIN mes_materials p ON p.id = lt.material_id
             LEFT JOIN mes_lines l ON l.id = lt.line_id
                 WHERE lt.status = 'READY'
                   AND lt.packed_at < NOW() - INTERVAL '2 hours'
              ORDER BY lt.packed_at LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                out["ready_lots_stale"].append({
                    "id":       r["id"],
                    "severity": "low" if r["hrs_ago"] < 6 else "medium",
                    "title":    f"Ready lot waiting — {r['material_code']}",
                    "detail":   f"{r['lot_no']} · {r['qty_packed']} pcs · {r['hrs_ago']:.1f} hrs ago",
                    "fired_at": r["packed_at"],
                    "line_id":  r["line_id"],
                })
        except Exception:
            try: conn.rollback()
            except Exception: pass

        # ── PY bypass active ──
        try:
            cur.execute("""
                SELECT id, line_id, py_no, py_name, fired_at, alert_level
                  FROM mes_py_bypass_events
                 WHERE acknowledged_at IS NULL
              ORDER BY fired_at DESC LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                out["py_bypass"].append({
                    "id":       r["id"],
                    "severity": "high",
                    "title":    f"PY bypass — {r.get('py_no') or '?'}",
                    "detail":   r.get("py_name") or "Sensor mismatch",
                    "fired_at": r["fired_at"],
                    "line_id":  r["line_id"],
                })
        except Exception:
            try: conn.rollback()
            except Exception: pass

    return out
