"""
Production → Line Rejection  (2026-11-11)

Zone- and line-wise view of the rejections booked in the TBDI Red Bin portal
(database `redbin`, table "RedBinRecord").  MES only READS the portal data:
every query runs on a read-only session (see redbin_lock._redbin_conn), and
the defect photos are streamed read-only from the portal's own upload folder.

    GET /filters          zones + their lines, shifts, statuses
    GET /summary          KPIs, zone → line totals, top defects / causes, daily
    GET /records          the rejection list (newest first)
    GET /record/{id}      one record with everything linked to it
    GET /photo/{att_id}   a defect photo (image only) of a rejection record
    GET /sheet            one MES line's month for the Control Board's
                          Defect Recording Sheet (2026-11-11, see sheet())

Times: the portal stores UTC (timestamp without zone); everything here is
returned and filtered in IST.  Test entries (isTest) are left out unless
include_test=1.
"""
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from auth import get_current_user
from database import get_conn, dict_cursor
from routers.redbin_lock import _redbin_conn, _ist

router = APIRouter(prefix="/api/line-rejection", tags=["line-rejection"])

IST = timedelta(hours=5, minutes=30)

# The portal keeps photos in <portal>/storage/uploads/<32-hex> (no extension).
PORTAL_UPLOAD_DIR = os.environ.get(
    "REDBIN_UPLOAD_DIR",
    "/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/red bin portal/storage/uploads")
_FILE_RE = re.compile(r"^/api/attachments/file/([a-f0-9]{32})$")
_IMG_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
              "gif": "image/gif", "webp": "image/webp"}

STATUSES = ["REJECTED", "RED_BIN_ENTRY_LINE", "LOCKED", "QUALITY_RED_BIN_AREA",
            "REWORK", "WAITING_UNLOCK", "SCRAPPED", "UNLOCKED", "BACK_TO_LINE",
            "SUCCESS"]
# KPI groups
G_OPEN = ("REJECTED", "RED_BIN_ENTRY_LINE", "LOCKED", "QUALITY_RED_BIN_AREA")
G_REWORK = ("REWORK", "WAITING_UNLOCK")
G_SCRAP = ("SCRAPPED",)
G_BACK = ("UNLOCKED", "BACK_TO_LINE", "SUCCESS")


def _conn():
    try:
        return _redbin_conn()
    except Exception as e:
        raise HTTPException(503, f"Red Bin database not reachable: {e}")


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _d(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"bad date: {s}")


def _where(date_from, date_to, zone_id, line_id, shift, status, include_test,
           q=None):
    """Shared WHERE for summary + list.  Dates are IST calendar days."""
    w, p = [], []
    df, dt = _d(date_from), _d(date_to)
    if df:
        w.append('r."dateTime" >= %s')
        p.append(datetime.combine(df, datetime.min.time()) - IST)
    if dt:
        w.append('r."dateTime" < %s')
        p.append(datetime.combine(dt + timedelta(days=1), datetime.min.time()) - IST)
    if zone_id:
        w.append('r."zoneId" = %s'); p.append(zone_id)
    if line_id:
        w.append('r."productionLineId" = %s'); p.append(line_id)
    if shift:
        w.append('s.name = %s'); p.append(shift)
    if status:
        sts = [x for x in status.split(",") if x in STATUSES]
        if sts:
            w.append('r.status::text = ANY(%s)'); p.append(sts)
    if not include_test:
        w.append('NOT r."isTest"')
    if q and q.strip():
        like = f"%{q.strip()}%"
        w.append('(r."redBinNumber" ILIKE %s OR r.barcode ILIKE %s OR r."batchCode" ILIKE %s'
                 ' OR r."partNameFree" ILIKE %s OR pt."partCode" ILIKE %s OR pt."partName" ILIKE %s'
                 ' OR r."rejectionWhy" ILIKE %s OR r.operator ILIKE %s OR r."lineLeader" ILIKE %s'
                 ' OR d.name ILIKE %s OR mc.name ILIKE %s)')
        p.extend([like] * 11)
    return (" WHERE " + " AND ".join(w)) if w else "", p


_FROM = '''
  FROM "RedBinRecord" r
  JOIN "Zone" z            ON z.id  = r."zoneId"
  LEFT JOIN "ProductionLine" l ON l.id = r."productionLineId"
  LEFT JOIN "Shift" s      ON s.id  = r."shiftId"
  LEFT JOIN "Machine" mc   ON mc.id = r."machineId"
  LEFT JOIN "ModelMaster" mm ON mm.id = r."modelId"
  LEFT JOIN "Part" pt      ON pt.id = r."partId"
  LEFT JOIN "Defect" d     ON d.id  = r."defectId"
  LEFT JOIN "RejectionReason" rr ON rr.id = r."reasonId"
'''


def _zone_sort(code):
    m = re.search(r"\d+", code or "")
    return (int(m.group()) if m else 999, code or "")


# ── Defect Recording Sheet classes (shared by /sheet and /record) ──────────
# Row block of the sheet: the Final Inspection rows take the rejections booked
# at a final station; everything else is an in-process defect.
_FI_RE = re.compile(r"final|\beol\b|gaug", re.I)


def _section(machine):
    return "fi" if machine and _FI_RE.search(machine) else "proc"


# "DUE TO" column of the sheet, from the portal's cause (4M):
#   Material / Supplier -> CHILD PART (the incoming part was bad)
#   Handling            -> DEFECT DUE TO ABNORMAL HANDLING row
#   anything else (Machine, Man, Method, Measurement, Power Cut, ...) -> PROCESS
def _due(reason):
    r = (reason or "").lower()
    if "handling" in r:
        return "handling"
    if "material" in r or "supplier" in r:
        return "child"
    return "process"


DUE_LABEL = {"process": "Process", "child": "Child Part", "handling": "Abnormal Handling"}
SECTION_LABEL = {"proc": "In-process", "fi": "Final Inspection"}


# ── MES line → Red Bin line ────────────────────────────────────────────────
# The portal's lines are the physical lines of each zone ("Zone 1 - Seat
# Slider" / "Line-7"); an MES line is a model line on one of them (Y17-SS and
# YFG-SS both run on Seat Slider Line-7).  Zone from the MES zone / line name,
# line from a Red Bin line of the same name (YWD, 2UA) or the line number in
# the MES line code, table name or line name.
_ZONE_KEYS = [
    (re.compile(r"thin\s*rec", re.I), "thin recliner"),
    (re.compile(r"loop\s*pipe", re.I), "loop pipe"),
    (re.compile(r"sub[\s-]*assembly|-sa-|pin\s*stacking|nut\s*welding", re.I), "sub assembly"),
    (re.compile(r"gear\s*lifter", re.I), "gear lifter"),
    (re.compile(r"press\s*shop|tool\s*room", re.I), "press shop"),
    (re.compile(r"recliner", re.I), "recliner"),
    (re.compile(r"seat\s*slider|-ss\b", re.I), "seat slider"),
]
_NUM_RES = [("line_code", re.compile(r"(?:^|[^A-Z])L-?(\d+)\b", re.I)),
            ("line_code", re.compile(r"^LP-?(\d+)$", re.I)),
            ("db_table_name", re.compile(r"_l(\d+)(?:_|$)", re.I)),
            ("line_name", re.compile(r"line[\s-]*(\d+)", re.I))]
_RB_NUM = re.compile(r"^line[\s-]*(\d+)$", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _bare_zone(name):
    return re.sub(r"^zone\s*\d+\s*-\s*", "", name or "", flags=re.I).strip().lower()


def _map_line(ml, rb_zones, rb_lines):
    """MES line dict -> (Red Bin zone, Red Bin line) or (None, None)."""
    text = f"{ml.get('zone_name') or ''} {ml.get('line_name') or ''}"
    key = next((k for rx, k in _ZONE_KEYS if rx.search(text)), None)
    zone = next((z for z in rb_zones if _bare_zone(z["name"]) == key), None) if key else None
    if not zone:
        return None, None
    lines = [l for l in rb_lines if l["zoneId"] == zone["id"]]
    mes_norm = _norm(ml.get("line_name"))
    first = re.split(r"[\s_-]+", (ml.get("line_name") or "").strip().lower())[0]
    # 1. a Red Bin line with the same name (YWD Recliner, 2UA Recliner)
    for l in lines:
        if _norm(l["name"]) == mes_norm:
            return zone, l
    # 2. a named (not numbered) Red Bin line starting with the MES line's first word (YWD-SS -> YWD Line)
    for l in lines:
        if not _RB_NUM.match(l["name"].strip()) and first and \
                re.split(r"[\s_-]+", l["name"].strip().lower())[0] == first:
            return zone, l
    # 3. the line number
    for field, rx in _NUM_RES:
        m = rx.search(ml.get(field) or "")
        if not m:
            continue
        n = int(m.group(1))
        for l in lines:
            mm = _RB_NUM.match(l["name"].strip())
            if mm and int(mm.group(1)) == n:
                return zone, l
        break
    # 4. a zone with a single line (Gear Lifter)
    if len(lines) == 1:
        return zone, lines[0]
    return zone, None


@router.get("/filters")
def filters(user=Depends(get_current_user)):
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT id, code, name FROM "Zone" WHERE active ORDER BY code')
        zones = _rows(cur)
        cur.execute('SELECT id, name, "zoneId" FROM "ProductionLine" WHERE active ORDER BY name')
        lines = _rows(cur)
        cur.execute('SELECT name FROM "Shift" WHERE active ORDER BY name')
        shifts = [r[0] for r in cur.fetchall()]
    finally:
        c.close()
    for z in zones:
        z["lines"] = [{"id": l["id"], "name": l["name"]} for l in lines if l["zoneId"] == z["id"]]
    zones.sort(key=lambda z: _zone_sort(z["code"]))
    return {"zones": zones, "shifts": shifts, "statuses": STATUSES}


@router.get("/summary")
def summary(date_from: Optional[str] = None, date_to: Optional[str] = None,
            zone_id: Optional[str] = None, line_id: Optional[str] = None,
            shift: Optional[str] = None, status: Optional[str] = None,
            include_test: int = 0, q: Optional[str] = None,
            user=Depends(get_current_user)):
    where, p = _where(date_from, date_to, zone_id, line_id, shift, status,
                      include_test, q)
    # The zone → line cards are the navigation, so they ignore the zone/line
    # pick (every zone stays clickable); KPIs, charts and lists honour it.
    where_all, p_all = _where(date_from, date_to, None, None, shift, status,
                              include_test, q)
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(f'''
            SELECT r."zoneId" zone_id, z.code zone_code, z.name zone_name,
                   r."productionLineId" line_id, l.name line_name,
                   r.status::text status, count(*) n, COALESCE(sum(r.qty),0) qty
            {_FROM}{where_all}
            GROUP BY 1,2,3,4,5,6''', p_all)
        grp = _rows(cur)
        cur.execute(f'''
            SELECT d.name, d.category, count(*) n, COALESCE(sum(r.qty),0) qty
            {_FROM}{where} {"AND" if where else "WHERE"} d.name IS NOT NULL
            GROUP BY 1,2 ORDER BY qty DESC, n DESC LIMIT 8''', p)
        top_defects = _rows(cur)
        cur.execute(f'''
            SELECT rr.name, count(*) n, COALESCE(sum(r.qty),0) qty
            {_FROM}{where} {"AND" if where else "WHERE"} rr.name IS NOT NULL
            GROUP BY 1 ORDER BY qty DESC, n DESC LIMIT 8''', p)
        top_reasons = _rows(cur)
        cur.execute(f'''
            SELECT to_char(r."dateTime" + interval '5 hours 30 minutes', 'YYYY-MM-DD') AS day,
                   count(*) n, COALESCE(sum(r.qty),0) qty
            {_FROM}{where} GROUP BY 1 ORDER BY 1''', p)
        daily = _rows(cur)
    finally:
        c.close()

    def bucket():
        return {"records": 0, "qty": 0, "open": 0, "rework": 0, "scrap": 0, "back": 0}

    def add(b, row):
        b["records"] += row["n"]; b["qty"] += int(row["qty"])
        st = row["status"]
        k = ("open" if st in G_OPEN else "rework" if st in G_REWORK
             else "scrap" if st in G_SCRAP else "back")
        b[k] += int(row["qty"])

    kpi = bucket()
    zones = {}
    for row in grp:
        if (not zone_id or row["zone_id"] == zone_id) and \
           (not line_id or row["line_id"] == line_id):
            add(kpi, row)
        z = zones.setdefault(row["zone_id"], {"id": row["zone_id"], "code": row["zone_code"],
                                               "name": row["zone_name"], **bucket(), "lines": {}})
        add(z, row)
        lk = row["line_id"] or "-"
        ln = z["lines"].setdefault(lk, {"id": row["line_id"], "name": row["line_name"] or "—",
                                        **bucket()})
        add(ln, row)
    zl = sorted(zones.values(), key=lambda z: _zone_sort(z["code"]))
    for z in zl:
        z["lines"] = sorted(z["lines"].values(), key=lambda l: (-l["qty"], l["name"]))
    for r in top_defects + top_reasons + daily:
        r["qty"] = int(r["qty"])
    return {"kpi": kpi, "zones": zl, "top_defects": top_defects,
            "top_reasons": top_reasons, "daily": daily}


@router.get("/records")
def records(date_from: Optional[str] = None, date_to: Optional[str] = None,
            zone_id: Optional[str] = None, line_id: Optional[str] = None,
            shift: Optional[str] = None, status: Optional[str] = None,
            include_test: int = 0, q: Optional[str] = None,
            limit: int = Query(500, ge=1, le=3000),
            user=Depends(get_current_user)):
    where, p = _where(date_from, date_to, zone_id, line_id, shift, status,
                      include_test, q)
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(f'''
            SELECT r.id, r."redBinNumber" rb_no, r."dateTime" at, s.name shift,
                   z.code zone_code, z.name zone_name, l.name line_name,
                   mc.name machine, mm.name model,
                   COALESCE(pt."partName", r."partNameFree") part_name,
                   pt."partCode" part_code, r.qty, d.name defect, d.category defect_cat,
                   rr.name reason, r."rejectionWhy" why, r.operator, r."lineLeader" line_leader,
                   r.status::text status, r."currentLocation" location, r.source,
                   r."isTest" is_test,
                   (SELECT count(*) FROM "Attachment" a WHERE a."redBinRecordId" = r.id) photos,
                   (SELECT a.id FROM "Attachment" a WHERE a."redBinRecordId" = r.id
                      ORDER BY a."uploadedAt" LIMIT 1) photo_id
            {_FROM}{where}
            ORDER BY r."dateTime" DESC LIMIT %s''', p + [limit + 1])
        rows = _rows(cur)
    finally:
        c.close()
    more = len(rows) > limit
    rows = rows[:limit]
    for r in rows:
        r["at"] = _ist(r["at"])
    return {"rows": rows, "more": more}


@router.get("/record/{rid}")
def record(rid: str, user=Depends(get_current_user)):
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(f'''
            SELECT r.id, r."redBinNumber" rb_no, r."dateTime" at, s.name shift,
                   z.code zone_code, z.name zone_name, l.name line_name,
                   mc.name machine, mm.name model,
                   pt."partCode" part_code, pt."partName" part_name, r."partNameFree" part_name_free,
                   r.customer, r.qty, r."batchCode" batch_code, r.barcode,
                   r."manualIdNote" manual_id_note,
                   d.name defect, d.category defect_cat, rr.name reason,
                   r."rejectionWhy" why, r."isRepeatedProblem" repeated,
                   r."rejectionPlanType" plan_type, r."rejectionPlanRemark" plan_remark,
                   r.operator, r."lineLeader" line_leader,
                   r.status::text status, r."currentLocation" location, r.source,
                   r."isTest" is_test, u.name created_by,
                   r."createdAt" created_at, r."updatedAt" updated_at
            {_FROM}
            LEFT JOIN "User" u ON u.id = r."createdById"
            WHERE r.id = %s''', (rid,))
        recs = _rows(cur)
        if not recs:
            raise HTTPException(404, "Record not found")
        rec = recs[0]
        for k in ("at", "created_at", "updated_at"):
            rec[k] = _ist(rec[k])

        cur.execute('SELECT id, "fileName" file_name, category, "uploadedAt" at '
                    '  FROM "Attachment" WHERE "redBinRecordId" = %s ORDER BY "uploadedAt"', (rid,))
        photos = _rows(cur)
        for a in photos:
            a["at"] = _ist(a["at"])
            ext = (a["file_name"] or "").rsplit(".", 1)[-1].lower()
            a["is_image"] = ext in _IMG_TYPES

        cur.execute('SELECT status::text status, note, "userName" user_name, at '
                    '  FROM "TimelineEvent" WHERE "redBinRecordId" = %s ORDER BY at, id', (rid,))
        timeline = _rows(cur)

        cur.execute('SELECT "unitCode" unit_code, decision, "decidedAt" decided_at,'
                    ' "decidedByName" decided_by, station, "defectName" defect,'
                    ' "whatWasDone" what_done, "finalInspectionResult" fi_result,'
                    ' "finalQualityStatus" fq_status, "reworkedAt" reworked_at,'
                    ' "reworkedByName" reworked_by'
                    '  FROM "RedBinUnit" WHERE "redBinRecordId" = %s ORDER BY "unitCode"', (rid,))
        units = _rows(cur)

        cur.execute('SELECT "childPartName" name, decision, qty, station, "unitCode" unit_code,'
                    ' supplier, "overrideRemark" remark, "decidedByName" decided_by,'
                    ' "decidedAt" decided_at, "addedFrom" added_from'
                    '  FROM "ChildPartLink" WHERE "redBinRecordId" = %s'
                    ' ORDER BY "unitCode" NULLS LAST, decision NULLS LAST, "childPartName"', (rid,))
        child = _rows(cur)

        cur.execute('SELECT w."reworkNumber" rework_no, w."doneAt" at, u.name done_by,'
                    ' w."whatWasDone" what_done, w.remarks, w."finalInspectionResult" fi_result,'
                    ' w."finalQualityStatus" fq_status'
                    '  FROM "ReworkRecord" w LEFT JOIN "User" u ON u.id = w."doneById"'
                    ' WHERE w."redBinRecordId" = %s', (rid,))
        rework = _rows(cur)

        cur.execute('SELECT e.at, e."scannedBatch" scanned, u.name done_by'
                    '  FROM "UnlockEvent" e LEFT JOIN "User" u ON u.id = e."doneById"'
                    ' WHERE e."redBinRecordId" = %s ORDER BY e.at', (rid,))
        unlocks = _rows(cur)

        cur.execute('SELECT at, "scannedBatch" scanned, reason FROM "EolLockedEvent"'
                    ' WHERE "redBinRecordId" = %s ORDER BY at', (rid,))
        eol = _rows(cur)

        cur.execute('SELECT q."qprNumber" qpr_no, q.status::text status, q."raisedAt" at'
                    '  FROM "QprRecordLink" k JOIN "Qpr" q ON q.id = k."qprId"'
                    ' WHERE k."redBinRecordId" = %s ORDER BY q."raisedAt"', (rid,))
        qprs = _rows(cur)
    finally:
        c.close()

    for lst, keys in ((timeline, ["at"]), (units, ["decided_at", "reworked_at"]),
                      (child, ["decided_at"]), (rework, ["at"]), (unlocks, ["at"]),
                      (eol, ["at"]), (qprs, ["at"])):
        for r in lst:
            for k in keys:
                r[k] = _ist(r[k])
    # how the Defect Recording Sheet books it
    rec["section"] = SECTION_LABEL[_section(rec["machine"])]
    rec["due_to"] = DUE_LABEL[_due(rec["reason"])]
    return {"record": rec, "photos": photos, "timeline": timeline, "units": units,
            "child_parts": child, "rework": rework, "unlocks": unlocks,
            "eol_locks": eol, "qprs": qprs}


@router.get("/sheet")
def sheet(line_id: int, month: str, include_test: int = 0,
          user=Depends(get_current_user)):
    """One MES line's month for the Defect Recording Sheet (Control Board).

    Every rejection booked in the Red Bin portal on the MES line's physical
    line that month (all statuses: a part counts as rejected even if it later
    went back to the line), each with its sheet day, shift, row block
    (section: proc / fi) and DUE TO (process / child / handling), plus last
    month's quantity per shift / section / defect / due-to for the LAST MONTH
    REJECTION column.  The sheet itself lays the defects out on its rows.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise HTTPException(400, "month must be YYYY-MM")
    y, mo = int(month[:4]), int(month[5:])
    if not 1 <= mo <= 12:
        raise HTTPException(400, "month must be YYYY-MM")
    m1 = date(y, mo, 1)
    m2 = date(y + (mo == 12), mo % 12 + 1, 1)
    m0 = date(y - (mo == 1), (mo - 2) % 12 + 1, 1)

    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT l.id, l.line_name, l.line_code, l.db_table_name, z.zone_name
                         FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id""")
        mes = [dict(r) for r in cur.fetchall()]
    ml = next((m for m in mes if m["id"] == line_id), None)
    if not ml:
        raise HTTPException(404, "MES line not found")

    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT id, code, name FROM "Zone"')
        rb_zones = _rows(cur)
        cur.execute('SELECT id, name, "zoneId" FROM "ProductionLine"')
        rb_lines = _rows(cur)
        zone, line = _map_line(ml, rb_zones, rb_lines)
        out = {"line_id": line_id, "line_name": ml["line_name"], "month": month,
               "rb_zone": zone["name"] if zone else None,
               "rb_line": line["name"] if line else None,
               "shared_with": [], "records": [], "last_month": []}
        if not line:
            return out
        out["shared_with"] = sorted(
            m["line_name"] for m in mes
            if m["id"] != line_id and (_map_line(m, rb_zones, rb_lines)[1] or {}).get("id") == line["id"])
        test = "" if include_test else 'AND NOT r."isTest"'
        cur.execute(f'''
            SELECT r.id, r."redBinNumber" rb_no, r."dateTime" at, s.name shift,
                   mc.name machine, mm.name model,
                   COALESCE(pt."partName", r."partNameFree") part_name, pt."partCode" part_code,
                   r.qty, d.name defect, d.category defect_cat, rr.name reason,
                   r."rejectionWhy" why, r.status::text status, r."currentLocation" location,
                   r.operator, r."lineLeader" line_leader,
                   (SELECT count(*) FROM "Attachment" a WHERE a."redBinRecordId" = r.id) photos
            {_FROM}
            WHERE r."productionLineId" = %s {test}
              AND r."dateTime" >= %s AND r."dateTime" < %s
            ORDER BY r."dateTime"''',
            (line["id"], datetime.combine(m0, datetime.min.time()) - IST,
             datetime.combine(m2, datetime.min.time()) - IST))
        rows = _rows(cur)
    finally:
        c.close()

    last = {}
    for r in rows:
        r["at"] = _ist(r["at"])               # "YYYY-MM-DD HH:MM:SS" IST
        r["qty"] = int(r["qty"] or 0)
        r["section"] = _section(r["machine"])
        r["due"] = _due(r["reason"])
        if r["at"][:7] == month:
            r["day"] = int(r["at"][8:10])
            out["records"].append(r)
        else:
            k = (r["shift"], r["section"], r["defect"], r["due"])
            last[k] = last.get(k, 0) + r["qty"]
    out["last_month"] = [{"shift": k[0], "section": k[1], "defect": k[2], "due": k[3], "qty": q}
                         for k, q in last.items()]
    return out


@router.get("/photo/{att_id}")
def photo(att_id: str, user=Depends(get_current_user)):
    """A rejection record's photo, read from the portal's upload folder."""
    if not re.fullmatch(r"[a-z0-9]{10,40}", att_id or ""):
        raise HTTPException(404, "Not found")
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT "fileName", "filePath" FROM "Attachment"'
                    ' WHERE id = %s AND "redBinRecordId" IS NOT NULL', (att_id,))
        row = cur.fetchone()
    finally:
        c.close()
    if not row:
        raise HTTPException(404, "Not found")
    file_name, file_path = row
    m = _FILE_RE.match(file_path or "")
    ctype = _IMG_TYPES.get((file_name or "").rsplit(".", 1)[-1].lower())
    if not m or not ctype:
        raise HTTPException(404, "Not an image")
    full = os.path.join(PORTAL_UPLOAD_DIR, m.group(1))
    if not os.path.isfile(full):
        raise HTTPException(404, "Photo file missing")
    return FileResponse(full, media_type=ctype,
                        headers={"Cache-Control": "private, max-age=86400"})
