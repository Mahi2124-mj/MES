# ════════════════════════════════════════════════════════════════
# routers/recliner.py   →  /api/recliner/...
# ════════════════════════════════════════════════════════════════
"""Recliner-zone config.

A recliner MACHINE runs 1..N production PROCESSES; each process has its own
OK/NG register (bit or word) and its own camera.  Machine total = sum of all
processes (OK, NG, total).  Cameras: each process → a dedicated camera, plus
an optional machine-level EXTERNAL camera that covers every process.

Additive tables (no collector / no seat-slider touch):
    mes_recliner_processes  — (line_id, process_no) → ok/ng register + mode
    mes_process_cameras     — process→camera (dedicated) or machine→camera (external)
The recliner collector profile reads these; the admin UI writes them.
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from typing import List
from database import get_conn, dict_cursor
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/recliner", tags=["recliner"])


# ── read ────────────────────────────────────────────────────────
@router.get("/lines")
def lines(user=Depends(get_current_user)):
    """All active lines with their zone + how many processes are configured."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT l.id, l.line_name, l.line_code, z.zone_name,
                   (SELECT count(*) FROM mes_recliner_processes p
                      WHERE p.line_id = l.id AND p.is_active) AS proc_count
            FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
            WHERE l.is_active
            ORDER BY z.zone_name, l.line_name
        """)
        return cur.fetchall()


@router.get("/line/{line_id}")
def line_config(line_id: int, user=Depends(get_current_user)):
    """A line's processes (each with its dedicated cameras) + external cameras."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, process_no, process_name, count_mode,
                   ok_register, ng_register, ideal_ct, machine_id
            FROM mes_recliner_processes
            WHERE line_id = %s AND is_active
            ORDER BY process_no
        """, (line_id,))
        procs = cur.fetchall()
        cur.execute("""
            SELECT id, process_id, camera_id, role, machine_id
            FROM mes_process_cameras
            WHERE line_id = %s AND is_active
            ORDER BY role, process_id
        """, (line_id,))
        by_proc, external = {}, []
        for c in cur.fetchall():
            if c["role"] == "external" or c["process_id"] is None:
                external.append(c)
            else:
                by_proc.setdefault(c["process_id"], []).append(c)
        for p in procs:
            p["cameras"] = by_proc.get(p["id"], [])
        return {"processes": procs, "external_cameras": external}


# ── write (admin) ───────────────────────────────────────────────
@router.put("/line/{line_id}/processes")
def save_processes(line_id: int,
                   processes: List[dict] = Body(...),
                   admin=Depends(require_admin)):
    """Replace a line's processes.  Each item:
       {process_no, process_name, count_mode['bit'|'register'],
        ok_register, ng_register, ideal_ct, machine_id?}"""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("UPDATE mes_recliner_processes SET is_active=false "
                    "WHERE line_id=%s", (line_id,))
        for p in processes:
            if p.get("process_no") is None:
                continue
            cur.execute("""
                INSERT INTO mes_recliner_processes
                    (line_id, machine_id, process_no, process_name, count_mode,
                     ok_register, ng_register, ideal_ct, is_active, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,now())
                ON CONFLICT (line_id, process_no) DO UPDATE SET
                    machine_id  = EXCLUDED.machine_id,
                    process_name= EXCLUDED.process_name,
                    count_mode  = EXCLUDED.count_mode,
                    ok_register = EXCLUDED.ok_register,
                    ng_register = EXCLUDED.ng_register,
                    ideal_ct    = EXCLUDED.ideal_ct,
                    is_active   = true,
                    updated_at  = now()
            """, (line_id, p.get("machine_id"), p["process_no"],
                  p.get("process_name"), (p.get("count_mode") or "register"),
                  (p.get("ok_register") or "").strip() or None,
                  (p.get("ng_register") or "").strip() or None,
                  p.get("ideal_ct")))
        conn.commit()
        return {"ok": True, "saved": len(processes)}


@router.put("/line/{line_id}/cameras")
def save_cameras(line_id: int,
                 cameras: List[dict] = Body(...),
                 admin=Depends(require_admin)):
    """Replace a line's camera bindings.  Each item:
       {process_id|null, camera_id, role['dedicated'|'external'], machine_id?}
       dedicated → process_id set;  external → process_id null (covers all)."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("DELETE FROM mes_process_cameras WHERE line_id=%s", (line_id,))
        saved = 0
        for c in cameras:
            cam = (c.get("camera_id") or "").strip()
            if not cam:
                continue
            role = (c.get("role") or "dedicated")
            cur.execute("""
                INSERT INTO mes_process_cameras
                    (line_id, machine_id, process_id, camera_id, role, is_active)
                VALUES (%s,%s,%s,%s,%s,true)
            """, (line_id, c.get("machine_id"),
                  (None if role == "external" else c.get("process_id")),
                  cam, role))
            saved += 1
        conn.commit()
        return {"ok": True, "saved": saved}
