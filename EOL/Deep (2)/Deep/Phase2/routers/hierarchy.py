# ───────────────────────────────────────────────────────────────────────
# hierarchy.py   (/api/hierarchy)   2026-09-13
# ───────────────────────────────────────────────────────────────────────
# "My Team" — every senior sees the JUNIORS down their leg, with how ACTIVE
# each is (last panel/app use) and, for leaders, how many lines they handle.
#
# Hierarchy = the per-zone ESCALATION CHAIN (mes_zone_escalation, higher
# level_no = more senior) PLUS the shopfloor users (role rank below the viewer)
# assigned to the viewer's zones' lines.  admin/plant_head see everyone.
# Activeness = mes_device_registry.last_seen vs the Timer module's
# inactive_user_hours threshold.
# ───────────────────────────────────────────────────────────────────────
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from routers.timer_config import get_timer_config
from ddl_once import once

router = APIRouter(prefix="/api/hierarchy", tags=["hierarchy"])

# role → seniority rank (higher = more senior)
_RANK = {
    "operator": 1, "production": 2, "leader": 2,
    "shift_incharge": 3, "production_incharge": 3, "quality_incharge": 3, "department": 3,
    "section_incharge": 4, "plant_head": 5, "admin": 6,
}


def _seconds_idle(cur):
    """username → seconds since last panel/app use (DB-side, tz-safe)."""
    cur.execute("""SELECT last_user AS uname,
                          EXTRACT(EPOCH FROM (now() - MAX(last_seen)))::bigint AS idle
                     FROM mes_device_registry
                    WHERE last_user IS NOT NULL AND last_user <> ''
                    GROUP BY last_user""")
    return {r["uname"]: r["idle"] for r in cur.fetchall()}


@router.get("/team")
def team(user=Depends(get_current_user)):
    cfg = get_timer_config()
    inactive_h = int(cfg.get("inactive_user_hours") or 8)
    thresh = inactive_h * 3600
    role, uid = user.get("role"), user.get("id")
    is_head = role in ("admin", "plant_head")
    my_rank = _RANK.get(role, 0)

    with get_conn() as conn:
        cur = dict_cursor(conn)
        idle = _seconds_idle(cur)

        # zones the viewer oversees + their chain level per zone
        if is_head:
            cur.execute("SELECT id AS zone_id, zone_name FROM mes_zones ORDER BY id")
            zrows = cur.fetchall()
            zones = {r["zone_id"]: r["zone_name"] for r in zrows}
            my_levels = {}
        else:
            cur.execute("SELECT zone_id, level_no FROM mes_zone_escalation WHERE admin_id=%s", (uid,))
            my_levels = {r["zone_id"]: r["level_no"] for r in cur.fetchall()}
            cur.execute("""SELECT DISTINCT l.zone_id FROM mes_operator_lines ol
                             JOIN mes_lines l ON l.id = ol.line_id
                            WHERE ol.admin_id=%s AND l.zone_id IS NOT NULL""", (uid,))
            line_zones = [r["zone_id"] for r in cur.fetchall()]
            zids = set(my_levels) | set(line_zones)
            if not zids:
                return {"zones": [], "members": [], "inactive_hours": inactive_h}
            cur.execute("SELECT id AS zone_id, zone_name FROM mes_zones WHERE id = ANY(%s)", (list(zids),))
            zones = {r["zone_id"]: r["zone_name"] for r in cur.fetchall()}
        zone_ids = list(zones.keys())
        if not zone_ids:
            return {"zones": [], "members": [], "inactive_hours": inactive_h}

        members = {}   # admin_id → {id, username, role, zones:set}

        def _add(aid, uname, urole, zid):
            m = members.setdefault(aid, {"id": aid, "username": uname, "role": urole, "zones": set()})
            if zid is not None:
                m["zones"].add(zid)

        # 1) escalation-chain members below the viewer's level (their sub-chain)
        cur.execute("""SELECT e.zone_id, e.level_no, a.id, a.username, a.role
                         FROM mes_zone_escalation e JOIN mes_admin a ON a.id = e.admin_id
                        WHERE e.zone_id = ANY(%s)""", (zone_ids,))
        for r in cur.fetchall():
            if r["id"] == uid:
                continue
            if not is_head:
                lvl = my_levels.get(r["zone_id"])
                if lvl is not None and r["level_no"] >= lvl:
                    continue   # same or more senior in this zone
            _add(r["id"], r["username"], r["role"], r["zone_id"])

        # 2) shopfloor users assigned to the viewer's zones' lines, rank below viewer
        cur.execute("""SELECT DISTINCT a.id, a.username, a.role, l.zone_id
                         FROM mes_operator_lines ol
                         JOIN mes_admin a ON a.id = ol.admin_id
                         JOIN mes_lines l ON l.id = ol.line_id
                        WHERE l.zone_id = ANY(%s)""", (zone_ids,))
        for r in cur.fetchall():
            if r["id"] == uid:
                continue
            if not is_head and _RANK.get(r["role"], 0) >= my_rank:
                continue
            _add(r["id"], r["username"], r["role"], r["zone_id"])

        # line counts (all assigned lines) for each member
        line_n = {}
        if members:
            cur.execute("""SELECT admin_id, COUNT(DISTINCT line_id) AS n
                             FROM mes_operator_lines WHERE admin_id = ANY(%s)
                            GROUP BY admin_id""", (list(members.keys()),))
            line_n = {r["admin_id"]: r["n"] for r in cur.fetchall()}

        out = []
        for m in members.values():
            idl = idle.get(m["username"])
            active = (idl is not None and idl <= thresh)
            out.append({
                "id": m["id"], "username": m["username"], "role": m["role"],
                "rank": _RANK.get(m["role"], 0),
                "lines": int(line_n.get(m["id"], 0)),
                "zones": sorted(zones.get(z, str(z)) for z in m["zones"]),
                "active": active,
                "idle_seconds": (int(idl) if idl is not None else None),
                "never_seen": idl is None,
            })
        out.sort(key=lambda x: (-x["rank"], not x["active"], x["username"]))
        return {"zones": [{"id": z, "name": n} for z, n in zones.items()],
                "members": out, "inactive_hours": inactive_h}


# ── Org tree (whole plant) — for the Escalation Hierarchy tree canvas ──────
@once
def _ensure_photo(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS mes_user_photo (
                     admin_id   INTEGER PRIMARY KEY REFERENCES mes_admin(id) ON DELETE CASCADE,
                     photo      TEXT,
                     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


def _current_shift(rotation_weeks):
    """Which shift (A/B) is 'current' given the rotation cadence (1 or 2 weeks)."""
    wk = date.today().isocalendar()[1]
    return "A" if ((wk // max(1, int(rotation_weeks or 1))) % 2 == 0) else "B"


# role → org rank (higher = more senior).  Plant Head → Production Head →
# Section Head → Shift Incharge → Leader → Operator.
_TREE_RANK = {
    "plant_head": 5, "admin": 5,
    "production_incharge": 4,
    "section_incharge": 3, "quality_incharge": 3,
    "shift_incharge": 2,
    "leader": 1,
    "operator": 0, "production": 0, "department": 0,
}


@router.get("/tree")
def org_tree(user=Depends(get_current_user)):
    """Whole-plant org tree as a proper node graph: each PERSON is one node,
    parented to the nearest senior who shares a zone (a head covering 2 zones is
    still one node with all its reports under it).  Plant Head at the root."""
    cfg = get_timer_config()
    inactive_h = int(cfg.get("inactive_user_hours") or 8)
    rot = int(cfg.get("shift_rotation_weeks") or 1)
    thresh = inactive_h * 3600
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_photo(cur)
        idle = _seconds_idle(cur)
        cur.execute("SELECT admin_id, photo FROM mes_user_photo")
        photos = {r["admin_id"]: r["photo"] for r in cur.fetchall()}

        # every user that belongs in the org chart
        cur.execute("""SELECT id, username, role FROM mes_admin
                        WHERE role IN ('plant_head','admin','production_incharge',
                                       'section_incharge','quality_incharge',
                                       'shift_incharge','leader','operator','production')
                        ORDER BY id""")
        U = {}
        for r in cur.fetchall():
            U[r["id"]] = {"id": r["id"], "name": r["username"], "role": r["role"],
                          "rank": _TREE_RANK.get(r["role"], 0), "zones": set(),
                          "zone_names": set(), "children": []}

        # zones each user belongs to (via assigned lines)
        cur.execute("""SELECT DISTINCT ol.admin_id, l.zone_id, z.zone_name
                         FROM mes_operator_lines ol
                         JOIN mes_lines l ON l.id = ol.line_id
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE l.zone_id IS NOT NULL""")
        for r in cur.fetchall():
            u = U.get(r["admin_id"])
            if u:
                u["zones"].add(r["zone_id"])
                if r["zone_name"]:
                    u["zone_names"].add(r["zone_name"])

        # parent = nearest higher rank sharing a zone (or a global senior with no
        # zone scope, e.g. Plant Head / Production Head).  Deterministic (min id).
        by_rank = {}
        for u in U.values():
            by_rank.setdefault(u["rank"], []).append(u)
        ranks = sorted(by_rank)
        top = ranks[-1] if ranks else 0
        roots = []
        for u in U.values():
            if u["rank"] >= top:
                roots.append(u); continue
            parent = None
            for r in [x for x in ranks if x > u["rank"]]:   # closest higher first
                cands = [q for q in by_rank[r]
                         if (not q["zones"]) or (not u["zones"]) or (q["zones"] & u["zones"])]
                if cands:
                    parent = min(cands, key=lambda q: q["id"]); break
            (parent["children"] if parent else roots).append(u)

        def serialize(u):
            idl = idle.get(u["name"])
            return {"id": u["id"], "name": u["name"], "role": u["role"],
                    "photo": photos.get(u["id"]),
                    "zones": sorted(u["zone_names"]),
                    "active": (idl is not None and idl <= thresh),
                    "idle_seconds": (int(idl) if idl is not None else None),
                    "children": [serialize(c) for c in
                                 sorted(u["children"], key=lambda c: (-c["rank"], c["name"]))]}

        tree = [serialize(u) for u in sorted(roots, key=lambda c: (-c["rank"], c["name"]))]

    return {"tree": tree, "inactive_hours": inactive_h,
            "shift_rotation_weeks": rot, "current_shift": _current_shift(rot)}


@router.get("/person/{uid}")
def person_detail(uid: int, user=Depends(get_current_user)):
    """One person's card for the org tree: details + their pending escalations
    (where they hold the current level) + open manpower alerts on their lines."""
    cfg = get_timer_config()
    thresh = int(cfg.get("inactive_user_hours") or 8) * 3600
    cur_shift = _current_shift(cfg.get("shift_rotation_weeks") or 1)
    with get_conn() as conn:
        cur = dict_cursor(conn)
        _ensure_photo(cur)
        cur.execute("SELECT id, username, role, last_login FROM mes_admin WHERE id=%s", (uid,))
        u = cur.fetchone()
        if not u:
            raise HTTPException(404, "User not found")
        cur.execute("SELECT photo FROM mes_user_photo WHERE admin_id=%s", (uid,))
        pr = cur.fetchone()
        idl = _seconds_idle(cur).get(u["username"])

        cur.execute("""SELECT l.id AS line_id, l.line_name, z.zone_name
                         FROM mes_operator_lines ol
                         JOIN mes_lines l ON l.id = ol.line_id
                         LEFT JOIN mes_zones z ON z.id = l.zone_id
                        WHERE ol.admin_id=%s ORDER BY l.line_code""", (uid,))
        lr = cur.fetchall()
        line_ids = [r["line_id"] for r in lr]

        # pending escalations where THIS user holds the current level
        cur.execute("""SELECT s.id, s.record_date, s.shift_name, s.current_level,
                              s.summary, l.line_name, z.zone_name
                         FROM mes_shift_escalation s
                         JOIN mes_zone_escalation ce
                              ON ce.zone_id=s.zone_id AND ce.level_no=s.current_level
                         LEFT JOIN mes_lines l ON l.id = s.line_id
                         LEFT JOIN mes_zones z ON z.id = s.zone_id
                        WHERE s.status='open' AND ce.admin_id=%s
                        ORDER BY s.record_date DESC, l.line_name""", (uid,))
        escalations = [{
            "id": r["id"], "line": r["line_name"], "zone": r["zone_name"],
            "date": r["record_date"].isoformat() if r["record_date"] else None,
            "shift": r["shift_name"], "level": r["current_level"], "summary": r["summary"],
        } for r in cur.fetchall()]

        alerts = []
        if line_ids:
            try:
                cur.execute("""SELECT a.id, a.alert_kind, a.shift_date, a.shift_name,
                                      a.context_text, a.fired_at, l.line_name
                                 FROM mes_manpower_alerts a
                                 LEFT JOIN mes_lines l ON l.id = a.line_id
                                WHERE a.resolved_at IS NULL AND a.line_id = ANY(%s)
                                ORDER BY a.fired_at DESC LIMIT 30""", (line_ids,))
                alerts = [{
                    "id": r["id"], "kind": r["alert_kind"], "line": r["line_name"],
                    "shift": r["shift_name"],
                    "date": r["shift_date"].isoformat() if r["shift_date"] else None,
                    "text": r["context_text"],
                    "fired_at": r["fired_at"].isoformat() if r["fired_at"] else None,
                } for r in cur.fetchall()]
            except Exception:
                alerts = []

    return {
        "id": u["id"], "name": u["username"], "role": u["role"],
        "photo": (pr["photo"] if pr else None),
        "last_login": u["last_login"].isoformat() if u["last_login"] else None,
        "active": (idl is not None and idl <= thresh),
        "idle_seconds": (int(idl) if idl is not None else None),
        "lines": [r["line_name"] for r in lr],
        "zones": sorted({r["zone_name"] for r in lr if r["zone_name"]}),
        "current_shift": cur_shift,
        "escalations": escalations, "alerts": alerts,
    }


class SetRoleBody(BaseModel):
    user_id: int
    role: str      # 'shift_incharge' | 'leader' | 'operator'


@router.post("/set-role")
def set_role(body: SetRoleBody, user=Depends(get_current_user)):
    """Section incharge & above can move a shopfloor user between
    operator / leader / shift_incharge (e.g. assign a shift incharge).
    Cannot touch admins / plant heads / section+ roles."""
    if user.get("role") not in ("admin", "plant_head", "section_incharge",
                                "production_incharge"):
        raise HTTPException(403, "Section incharge and above only")
    if body.role not in ("shift_incharge", "leader", "operator"):
        raise HTTPException(400, "Role must be shift_incharge, leader or operator")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM mes_admin WHERE id=%s", (body.user_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "User not found")
        if r[0] in ("admin", "plant_head", "section_incharge", "production_incharge"):
            raise HTTPException(400, "Cannot change this user's role here")
        cur.execute("UPDATE mes_admin SET role=%s WHERE id=%s", (body.role, body.user_id))
        conn.commit()
    return {"ok": True, "id": body.user_id, "role": body.role}


class PhotoBody(BaseModel):
    user_id: int
    photo: Optional[str] = None      # data: URL, or null to clear


@router.post("/photo")
def set_photo(body: PhotoBody, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "plant_head", "section_incharge",
                                "shift_incharge", "production_incharge"):
        raise HTTPException(403, "Not allowed")
    if body.photo and len(body.photo) > 900000:
        raise HTTPException(400, "Image too large — please use a smaller photo")
    with get_conn() as conn:
        cur = conn.cursor()
        _ensure_photo(cur)
        if not body.photo:
            cur.execute("DELETE FROM mes_user_photo WHERE admin_id=%s", (body.user_id,))
        else:
            cur.execute("""INSERT INTO mes_user_photo (admin_id, photo, updated_at)
                           VALUES (%s,%s,now())
                           ON CONFLICT (admin_id) DO UPDATE SET
                             photo=EXCLUDED.photo, updated_at=now()""",
                        (body.user_id, body.photo))
        conn.commit()
    return {"ok": True}
