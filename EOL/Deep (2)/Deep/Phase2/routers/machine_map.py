"""
routers/machine_map.py
======================
Machine Map — one row per machine, MES next to Maintenance.

Answers "what is this machine called, and what number / zone does it carry,
in MES and in the maintenance app?"

  MES          mes_plc_configs  + mes_lines + mes_zones   (energydb)
  Maintenance  maintenance_machines                       (maintenance_db)

maintenance_db is only ever READ (READ ONLY transaction).  The only writes are
the admin buttons below, and they touch MES alone:

  POST /apply  {plc_config_id, field: "name" | "machine_no"}
      copies the paired maintenance machine's name or machine_no onto the MES
      machine (mes_plc_configs).  The server finds the maintenance row itself,
      by IP — the client never supplies the value.
  POST /undo   {change_id}
      puts the previous value back.

Every write is logged in mes_machine_map_changes (old value, new value, who,
when), which is also what /undo restores from.

MATCHING
--------
The two lists share no key, so rows are paired by PLC IP (trimmed, exact).
  Matched        one MES machine <-> one maintenance machine, same name
  Name differs   paired by IP, but the names differ (case/spacing ignored)
  Shared PLC     one IP serves several MES machines (e.g. two stations, one PLC)
  Multiple rows  the IP appears on more than one maintenance row
  MES only       no IP, or the IP is not in maintenance
  Maint. only    maintenance machine whose IP no MES machine uses
"""
import re
from collections import defaultdict
from datetime import datetime
from typing import Literal

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user, require_admin
from database import get_conn, dict_cursor
from routers.andon import _maint_conn

router = APIRouter(prefix="/api/machine-map", tags=["machine-map"])


def _ip(v) -> str:
    return (v or "").strip()


def _norm_name(v) -> str:
    """Compare names ignoring case, spacing, punctuation, full-width characters
    and the word "M/c" / "Machine" ("Final Inspection" == "Final Inspection M/c")."""
    s = (v or "").lower()
    s = s.replace("　", " ").replace("＆", "&").replace("×", "x")
    s = re.sub(r"m/c|\bmachine\b|\bmc\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _clean_name(v) -> str:
    """Maintenance name as it should be stored in MES: one line, normal spaces."""
    s = (v or "").replace("　", " ").replace("＆", "&")
    s = re.sub(r"\s*\n\s*", " / ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def maint_ip_column(cur) -> str:
    """The PLC-IP column of maintenance_machines.  The maintenance app renamed
    `ip` to `plc_ip` on 29-Oct 20:13 (and added camera_ip / ok_bit / ng_bit /
    model_bit); accept either so a rename on their side cannot 500 this page."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'maintenance_machines' AND column_name IN ('plc_ip', 'ip')""")
    cols = {(r["column_name"] if isinstance(r, dict) else r[0]) for r in cur.fetchall()}
    return "plc_ip" if "plc_ip" in cols else "ip"


def _maint_rows():
    with _maint_conn() as mconn:
        try:
            mcur = mconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            mcur.execute("SET TRANSACTION READ ONLY")
            ipc = maint_ip_column(mcur)
            mcur.execute(f"""
                SELECT id, zone_name, line_name, machine_no, machine_name,
                       {ipc} AS ip, serial_no, is_active
                  FROM maintenance_machines
                 ORDER BY zone_name, line_name, serial_no NULLS LAST, machine_no""")
            return mcur.fetchall()
        finally:
            mconn.rollback()


@router.get("")
def machine_map(user=Depends(get_current_user)):
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("""
            SELECT z.id   AS zone_id,   z.zone_code, z.zone_name,
                   l.id   AS line_id,   l.line_code, l.line_name, l.is_active AS line_active,
                   p.id   AS machine_id, p.machine_seq, p.machine_name, p.machine_no,
                   p.plc_ip, p.parent_plc_id
              FROM mes_plc_configs p
              JOIN mes_lines l ON l.id = p.line_id
              LEFT JOIN mes_zones z ON z.id = l.zone_id
             ORDER BY z.id NULLS LAST, l.line_name, p.machine_seq NULLS LAST, p.id""")
        mes = cur.fetchall()
        # Latest manual change per machine that can still be undone.
        cur.execute("""
            SELECT DISTINCT ON (plc_config_id, field)
                   id, plc_config_id, field, old_value, new_value, changed_by, changed_at
              FROM mes_machine_map_changes
             WHERE undone_at IS NULL AND changed_by NOT LIKE 'auto:%%'
             ORDER BY plc_config_id, field, changed_at DESC""")
        undo = defaultdict(list)
        for ch in cur.fetchall():
            undo[ch["plc_config_id"]].append({
                "id": ch["id"], "field": ch["field"], "old_value": ch["old_value"],
                "new_value": ch["new_value"], "changed_by": ch["changed_by"],
                "changed_at": ch["changed_at"].isoformat(timespec="seconds") if ch["changed_at"] else None,
            })

    maint = _maint_rows()

    by_ip = defaultdict(list)
    for m in maint:
        if _ip(m["ip"]):
            by_ip[_ip(m["ip"])].append(m)
    mes_ip_count = defaultdict(int)
    for p in mes:
        if _ip(p["plc_ip"]):
            mes_ip_count[_ip(p["plc_ip"])] += 1

    rows, used = [], set()
    for p in mes:
        ip = _ip(p["plc_ip"])
        hits = by_ip.get(ip, []) if ip else []
        mm = hits[0] if len(hits) == 1 else None
        if not hits:
            status = "MES only"
        elif len(hits) > 1:
            status = "Multiple rows"
        elif mes_ip_count[ip] > 1:
            status = "Shared PLC"
        elif _norm_name(p["machine_name"]) != _norm_name(mm["machine_name"]):
            status = "Name differs"
        else:
            status = "Matched"
        for h in hits:
            used.add(h["id"])
        rows.append({
            "status":          status,
            "mes_zone_id":     p["zone_id"],
            "mes_zone_code":   p["zone_code"],
            "mes_zone":        p["zone_name"],
            "mes_line_id":     p["line_id"],
            "mes_line_code":   p["line_code"],
            "mes_line":        p["line_name"],
            "mes_line_active": p["line_active"],
            "mes_machine_id":  p["machine_id"],
            "mes_seq":         p["machine_seq"],
            "mes_machine":     p["machine_name"],
            "mes_machine_no":  p["machine_no"],
            "mes_ip":          ip,
            "mes_parent_id":   p["parent_plc_id"],
            "mm_id":           mm["id"] if mm else None,
            "mm_zone":         mm["zone_name"] if mm else (hits[0]["zone_name"] if hits else None),
            "mm_line":         mm["line_name"] if mm else (hits[0]["line_name"] if hits else None),
            "mm_machine_no":   mm["machine_no"] if mm else (" / ".join(h["machine_no"] for h in hits) or None),
            "mm_machine":      mm["machine_name"] if mm else (" / ".join(h["machine_name"] or "" for h in hits) or None),
            "mm_ip":           _ip(mm["ip"]) if mm else (ip if hits else None),
            "mm_serial":       mm["serial_no"] if mm else None,
            "mm_active":       mm["is_active"] if mm else None,
            # What the admin buttons may do on this row.
            "can_copy_name":   bool(mm) and mes_ip_count[ip] == 1
                               and _clean_name(mm["machine_name"]) != (p["machine_name"] or ""),
            "can_copy_no":     bool(mm) and (mm["machine_no"] or "") != (p["machine_no"] or ""),
            "undo":            undo.get(p["machine_id"], []),
        })

    for m in maint:
        if m["id"] in used:
            continue
        rows.append({
            "status": "Maint. only",
            "mes_zone_id": None, "mes_zone_code": None, "mes_zone": None,
            "mes_line_id": None, "mes_line_code": None, "mes_line": None, "mes_line_active": None,
            "mes_machine_id": None, "mes_seq": None, "mes_machine": None, "mes_machine_no": None,
            "mes_ip": None, "mes_parent_id": None,
            "mm_id":         m["id"],
            "mm_zone":       m["zone_name"],
            "mm_line":       m["line_name"],
            "mm_machine_no": m["machine_no"],
            "mm_machine":    m["machine_name"],
            "mm_ip":         _ip(m["ip"]) or None,
            "mm_serial":     m["serial_no"],
            "mm_active":     m["is_active"],
            "can_copy_name": False, "can_copy_no": False, "undo": [],
        })

    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mes_total":    len(mes),
        "maint_total":  len(maint),
        "counts":       dict(counts),
        "rows":         rows,
    }


class ApplyBody(BaseModel):
    plc_config_id: int
    field: Literal["name", "machine_no"]


class UndoBody(BaseModel):
    change_id: int


def _who(user) -> str:
    return str(user.get("username") or user.get("name") or user.get("id") or "admin")


@router.post("/apply")
def apply_from_maintenance(body: ApplyBody, user=Depends(require_admin)):
    """Copy the paired maintenance machine's name or machine_no onto MES."""
    maint = _maint_rows()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SET LOCAL lock_timeout = '3s'")
        cur.execute("SELECT id, machine_name, machine_no, plc_ip FROM mes_plc_configs WHERE id = %s FOR UPDATE",
                    (body.plc_config_id,))
        p = cur.fetchone()
        if not p:
            raise HTTPException(404, "Machine not found")
        ip = _ip(p["plc_ip"])
        hits = [m for m in maint if ip and _ip(m["ip"]) == ip]
        if len(hits) != 1:
            raise HTTPException(409, "No single maintenance machine has this IP")
        mm = hits[0]
        if body.field == "name":
            cur.execute("SELECT count(*) AS n FROM mes_plc_configs WHERE TRIM(plc_ip) = %s", (ip,))
            if cur.fetchone()["n"] > 1:
                raise HTTPException(409, "Shared PLC: the MES stations would all get the same name")
            old, new, col = p["machine_name"], _clean_name(mm["machine_name"]), "machine_name"
            if not new:
                raise HTTPException(409, "Maintenance name is empty")
            if len(new) > 100:
                raise HTTPException(409, "Maintenance name is longer than 100 characters")
        else:
            old, new, col = p["machine_no"], (mm["machine_no"] or "").strip(), "machine_no"
            if not new:
                raise HTTPException(409, "Maintenance machine no. is empty")
        if (old or "") == new:
            return {"ok": True, "unchanged": True, "value": new}
        cur.execute(f"UPDATE mes_plc_configs SET {col} = %s, updated_at = NOW() WHERE id = %s",
                    (new, p["id"]))
        cur.execute("""INSERT INTO mes_machine_map_changes
                           (plc_config_id, field, old_value, new_value, changed_by)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (p["id"], body.field, old, new, _who(user)))
        cid = cur.fetchone()["id"]
    return {"ok": True, "change_id": cid, "old_value": old, "new_value": new}


@router.post("/undo")
def undo_change(body: UndoBody, user=Depends(require_admin)):
    """Put back the value a change replaced, if nothing has changed it since."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SET LOCAL lock_timeout = '3s'")
        cur.execute("SELECT * FROM mes_machine_map_changes WHERE id = %s FOR UPDATE", (body.change_id,))
        ch = cur.fetchone()
        if not ch:
            raise HTTPException(404, "Change not found")
        if ch["undone_at"] is not None:
            raise HTTPException(409, "Already undone")
        col = "machine_name" if ch["field"] == "name" else "machine_no"
        cur.execute(f"SELECT {col} AS v FROM mes_plc_configs WHERE id = %s FOR UPDATE", (ch["plc_config_id"],))
        cur_row = cur.fetchone()
        if not cur_row:
            raise HTTPException(404, "Machine not found")
        if (cur_row["v"] or "") != (ch["new_value"] or ""):
            raise HTTPException(409, "The value was changed again since; not undone")
        cur.execute(f"UPDATE mes_plc_configs SET {col} = %s, updated_at = NOW() WHERE id = %s",
                    (ch["old_value"], ch["plc_config_id"]))
        cur.execute("UPDATE mes_machine_map_changes SET undone_at = NOW(), undone_by = %s WHERE id = %s",
                    (_who(user), ch["id"]))
    return {"ok": True, "restored": ch["old_value"]}
