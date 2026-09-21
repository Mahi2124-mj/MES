# ════════════════════════════════════════════════════════════════
# routers/sixsigma.py   →  /api/sixsigma/...
# ════════════════════════════════════════════════════════════════
"""6 Sigma page — Ball Guide station dual-camera clip review (Seat Slider only).
#
The Ball Guide machine (Seat Slider lines) has TWO cameras. Both cut a clip on
the SAME production cycle, so every cycle produces two synchronized clips (one
per camera). Clips are retained for 40 days.

This router is purely ADDITIVE (new table, no collector / seat-slider changes):
    mes_sixsigma_config  — per-line ball-guide 2-camera + retention config

It also mirrors the two cameras into `mes_process_cameras` (role='external', the
same table the recliner/CMS clip pipeline reads) so the existing per-cycle clip
recorder records them — i.e. same-cycle capture reuses the proven pipeline.
Actual RTSP capture + per-camera clip files are produced CMS-side; the camera
RTSP URLs entered here are stored so the CMS can be pointed at them.
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user, require_admin
from ddl_once import once

router = APIRouter(prefix="/api/sixsigma", tags=["sixsigma"])

DEFAULT_MACHINE = "Ball Guide"
DEFAULT_RETENTION = 40


@once
def _ensure(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_sixsigma_config (
                     line_id        INTEGER PRIMARY KEY,
                     machine_name   TEXT NOT NULL DEFAULT 'Ball Guide',
                     cam1_name      TEXT,
                     cam1_url       TEXT,
                     cam2_name      TEXT,
                     cam2_url       TEXT,
                     retention_days INTEGER NOT NULL DEFAULT 40,
                     updated_by     TEXT,
                     updated_at     TIMESTAMPTZ NOT NULL DEFAULT now())""")


def _ss_lines(cur):
    """Seat Slider lines only (that's where the Ball Guide machine lives)."""
    cur.execute("""
        SELECT l.id, l.line_name, l.line_code, l.db_table_name, z.zone_name
          FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
         WHERE COALESCE(l.is_active, TRUE)
           AND (z.zone_name ILIKE '%seat%slider%' OR z.zone_name ILIKE '%seat_slider%'
                OR l.line_name ILIKE '%-SS' OR l.line_name ILIKE '%_SS')
         ORDER BY l.line_name""")
    return cur.fetchall()


@router.get("/config")
def get_config(user=Depends(get_current_user)):
    """All saved ball-guide configs + the Seat Slider lines to choose from."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT c.*, l.line_name, l.line_code
                         FROM mes_sixsigma_config c
                         JOIN mes_lines l ON l.id = c.line_id
                        ORDER BY l.line_name""")
        configs = cur.fetchall()
        for c in configs:
            if c.get("updated_at"):
                c["updated_at"] = c["updated_at"].isoformat()
        lines = _ss_lines(cur)
        conn.commit()
        return {"configs": configs, "lines": lines,
                "default_machine": DEFAULT_MACHINE, "default_retention": DEFAULT_RETENTION}


class ConfigBody(BaseModel):
    line_id: int
    machine_name: str = DEFAULT_MACHINE
    cam1_name: Optional[str] = "Camera 1"
    cam1_url:  Optional[str] = None
    cam2_name: Optional[str] = "Camera 2"
    cam2_url:  Optional[str] = None
    retention_days: int = DEFAULT_RETENTION


@router.post("/config")
def save_config(body: ConfigBody, admin=Depends(require_admin)):
    if not body.line_id:
        raise HTTPException(400, "line_id required")
    rd = body.retention_days if (body.retention_days and body.retention_days > 0) else DEFAULT_RETENTION
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""INSERT INTO mes_sixsigma_config
                         (line_id, machine_name, cam1_name, cam1_url, cam2_name, cam2_url,
                          retention_days, updated_by, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
                       ON CONFLICT (line_id) DO UPDATE SET
                         machine_name=EXCLUDED.machine_name,
                         cam1_name=EXCLUDED.cam1_name, cam1_url=EXCLUDED.cam1_url,
                         cam2_name=EXCLUDED.cam2_name, cam2_url=EXCLUDED.cam2_url,
                         retention_days=EXCLUDED.retention_days,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (body.line_id, (body.machine_name or DEFAULT_MACHINE).strip(),
                     (body.cam1_name or "Camera 1").strip(), (body.cam1_url or "").strip() or None,
                     (body.cam2_name or "Camera 2").strip(), (body.cam2_url or "").strip() or None,
                     rd, admin.get("username")))
        # Mirror the two cameras into mes_process_cameras (external role) so the
        # existing per-cycle clip pipeline records them. Best-effort — never fail
        # the save if that table's shape differs.
        try:
            cur.execute("DELETE FROM mes_process_cameras WHERE line_id=%s AND role='external' AND camera_id IN (%s,%s)",
                        (body.line_id, (body.cam1_url or "").strip(), (body.cam2_url or "").strip()))
            for url in ((body.cam1_url or "").strip(), (body.cam2_url or "").strip()):
                if url:
                    cur.execute("""INSERT INTO mes_process_cameras
                                     (line_id, machine_id, process_id, camera_id, role, is_active)
                                   VALUES (%s, NULL, NULL, %s, 'external', true)""",
                                (body.line_id, url))
        except Exception:
            pass
        conn.commit()
        return {"ok": True, "retention_days": rd}


@router.delete("/config")
def delete_config(line_id: int = Query(...), admin=Depends(require_admin)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("DELETE FROM mes_sixsigma_config WHERE line_id=%s", (line_id,))
        conn.commit()
        return {"ok": True}


@router.get("/clips")
def get_clips(line_id: int = Query(...),
              date: Optional[str] = Query(None),
              shift: Optional[str] = Query(None),
              limit: int = Query(30, ge=1, le=100),
              user=Depends(get_current_user)):
    """Recent cycles for the line's Ball Guide station, each with the two camera
    clip URLs. Reuses the existing /cycle-video proxy; the second camera is
    requested with cam=2 (honoured once the CMS emits per-camera files)."""
    rec_date = date or datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure(cur)
        cur.execute("""SELECT c.*, l.db_table_name FROM mes_sixsigma_config c
                         JOIN mes_lines l ON l.id=c.line_id WHERE c.line_id=%s""", (line_id,))
        cfg = cur.fetchone()
        if not cfg:
            raise HTTPException(404, "This line has no Ball Guide 6-Sigma config yet")
        tbl = (cfg["db_table_name"] or "") + "_ct_log"
        cur.execute("SELECT to_regclass(%s) AS r", (tbl,))
        if not cur.fetchone()["r"]:
            return {"config": _cfg_public(cfg), "cycles": []}
        cols = "cycle_seq, ts, COALESCE(is_ng,false) AS is_ng"
        cur.execute(f"""SELECT column_name FROM information_schema.columns
                         WHERE table_name=%s AND column_name='part_code'""", (tbl,))
        has_part = cur.fetchone() is not None
        if has_part:
            cols += ", part_code"
        q = f"SELECT {cols} FROM {tbl} WHERE ts::date = %s"
        params = [rec_date]
        if shift:
            cur.execute(f"""SELECT column_name FROM information_schema.columns
                             WHERE table_name=%s AND column_name='shift_name'""", (tbl,))
            if cur.fetchone():
                q += " AND shift_name = %s"; params.append(shift)
        q += " ORDER BY ts DESC LIMIT %s"; params.append(limit)
        cur.execute(q, params)
        rows = cur.fetchall()
        cycles = []
        for r in rows:
            seq = r["cycle_seq"]
            base = f"/api/lines/{line_id}/cycle-video?cycle_seq={seq}&date={rec_date}"
            if shift:
                base += f"&shift={shift}"
            ng = 1 if r.get("is_ng") else 0
            cycles.append({
                "cycle_seq": seq,
                "ts": r["ts"].isoformat() if r.get("ts") else None,
                "is_ng": bool(r.get("is_ng")),
                "part_code": r.get("part_code"),
                "cam1_url": f"{base}&ng={ng}&cam=1",
                "cam2_url": f"{base}&ng={ng}&cam=2",
            })
        conn.commit()
        return {"config": _cfg_public(cfg), "cycles": cycles}


def _cfg_public(cfg):
    return {
        "line_id": cfg["line_id"], "machine_name": cfg.get("machine_name"),
        "cam1_name": cfg.get("cam1_name"), "cam2_name": cfg.get("cam2_name"),
        "retention_days": cfg.get("retention_days", DEFAULT_RETENTION),
    }
