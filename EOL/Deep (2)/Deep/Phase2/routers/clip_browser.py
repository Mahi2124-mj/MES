"""
Historical clip browser (2026-08-19, operator request: "kal ki video bhi dekhni
h ... historical data ke under, shift and cycle serial ke saath").

READ-ONLY browser over the clip archive that routers/clip_archive.py already
writes.  That archiver stores every cycle as

    {ARCHIVE_ROOT}/{date}/line_{line}/{shift}/{machine}/cycle_{seq}[_ng].mp4

which is exactly a date -> line -> shift -> machine -> cycle tree, so this
module only has to LIST that tree and join each clip back to its ct_log row for
the time / cycle-time / part-code / NG flag the operator reads on screen.

Deliberately additive:
  * new file, new prefix (/api/clip-archive) — no existing route changes shape;
  * nothing here writes, deletes or re-encodes.  Every handler is a directory
    listing plus a SELECT, so a bug here cannot corrupt data or the archiver;
  * playback re-uses clip_archive.clip_path() + clip_archive.serve(), so the
    path layout stays defined in exactly ONE place.  If the layout ever changes
    again, this browser follows automatically.

Retention is unchanged (CLIP_ARCHIVE_RETAIN_DAYS, 30d): this only exposes what
is already on disk.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from database import get_conn, dict_cursor

router = APIRouter(prefix="/api/clip-archive", tags=["clip-archive"])


# ── strict input validation ────────────────────────────────────────────────
# Every value below becomes a path component, so each is matched against a
# whitelist BEFORE it is joined.  Traversal ("..", "/", NUL) cannot survive
# these patterns, which is why the handlers can join paths directly.
_DATE_RE    = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHIFT_RE   = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_MACHINE_RE = re.compile(r"^(main|sub_[A-Za-z0-9_-]{1,32})$")
_LINEDIR_RE = re.compile(r"^line_(.+)$")
_CLIP_RE    = re.compile(r"^cycle_(\d+)(_ng)?\.mp4$")


def _archive_root() -> str:
    """Import lazily so this module never breaks API boot if the archiver is
    disabled or its optional deps are missing."""
    from routers.clip_archive import ARCHIVE_ROOT
    return ARCHIVE_ROOT


def _listdir(path: str) -> list[str]:
    """Directory entries, or [] for anything unreadable.  A browser must never
    500 just because a folder was swept by retention mid-request."""
    try:
        return os.listdir(path)
    except OSError:
        return []


def _machine_clip_count(path: str) -> int:
    """How many .mp4 a machine folder holds.  0 for anything unreadable."""
    try:
        with os.scandir(path) as it:
            return sum(1 for e in it if e.name.endswith(".mp4"))
    except OSError:
        return 0


def _shift_has_clips(line_dir: str, shift: str) -> bool:
    sp = os.path.join(line_dir, shift)
    return any(_machine_clip_count(os.path.join(sp, f))
               for f in _listdir(sp) if _MACHINE_RE.match(f))


def _line_has_clips(date_dir: str, line_folder: str) -> bool:
    lp = os.path.join(date_dir, line_folder)
    return any(_shift_has_clips(lp, sh)
               for sh in _listdir(lp) if _SHIFT_RE.match(sh))


def _need(value: str, pattern: re.Pattern, label: str) -> str:
    if not value or not pattern.match(value):
        raise HTTPException(400, f"Invalid {label}")
    return value


def _check_token(token: Optional[str], request: Optional[Request]) -> None:
    """Auth mirrors /{line_id}/archive-video in lines.py: a token is validated
    IF supplied, otherwise anonymous read is allowed.  Deviating here would
    either lock the page out or hand it a weaker rule than the video route it
    sits next to; keeping the two identical is the point.
    """
    from auth import SECRET_KEY, ALGORITHM
    from jose import jwt as jose_jwt, JWTError as JoseJWTError

    jwt_token = token
    if request is not None:
        hdr = request.headers.get("authorization", "")
        if hdr.lower().startswith("bearer "):
            jwt_token = hdr[7:]
    if jwt_token:
        try:
            jose_jwt.decode(jwt_token, SECRET_KEY, algorithms=[ALGORITHM])
        except JoseJWTError:
            raise HTTPException(401, "Invalid or expired token")


# ── level 1: which days are on disk ────────────────────────────────────────
@router.get("/days")
def list_days(token: Optional[str] = Query(None), request: Request = None):
    """Dates that still have clips, newest first (retention drops old ones)."""
    _check_token(token, request)
    days = sorted((d for d in _listdir(_archive_root()) if _DATE_RE.match(d)),
                  reverse=True)
    return {"days": days}


# ── level 2: lines that recorded on a day ──────────────────────────────────
@router.get("/lines")
def list_lines(date: str = Query(...),
               token: Optional[str] = Query(None), request: Request = None):
    _check_token(token, request)
    _need(date, _DATE_RE, "date")

    # 2026-09-17 — only list lines that have at least one playable clip that
    # day.  An empty line_* folder used to appear, then offered no shifts —
    # the same dead end as the empty machine folders, one level higher.
    date_dir = os.path.join(_archive_root(), date)
    ids: list[int] = []
    for name in _listdir(date_dir):
        m = _LINEDIR_RE.match(name)
        if m and m.group(1).isdigit() and _line_has_clips(date_dir, name):
            ids.append(int(m.group(1)))

    names: dict[int, str] = {}
    if ids:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT id, line_name, line_code FROM mes_lines "
                        "WHERE id = ANY(%s)", (ids,))
            for r in cur.fetchall():
                names[r["id"]] = r["line_name"] or r["line_code"] or "Unknown line"

    return {"lines": [{"line_id": i, "name": names.get(i, f"Line {i}")}
                      for i in sorted(ids)]}


# ── level 3: shifts recorded for that line/day ─────────────────────────────
@router.get("/shifts")
def list_shifts(date: str = Query(...), line_id: int = Query(...),
                token: Optional[str] = Query(None), request: Request = None):
    _check_token(token, request)
    _need(date, _DATE_RE, "date")

    base = os.path.join(_archive_root(), date, f"line_{line_id}")
    shifts = sorted(s for s in _listdir(base) if _SHIFT_RE.match(s))

    # 2026-09-17 — drop shifts whose machine folders are all empty.  /machines
    # now hides empty folders, so without this the operator picks a shift and
    # gets an empty machine list — the dead end simply moves up one level.
    return {"shifts": [s for s in shifts if _shift_has_clips(base, s)]}


# ── level 4: machines recorded for that line/day/shift ─────────────────────
@router.get("/machines")
def list_machines(date: str = Query(...), line_id: int = Query(...),
                  shift: str = Query(...),
                  token: Optional[str] = Query(None), request: Request = None):
    _check_token(token, request)
    _need(date, _DATE_RE, "date")
    _need(shift, _SHIFT_RE, "shift")

    base = os.path.join(_archive_root(), date, f"line_{line_id}", shift)
    folders = sorted(f for f in _listdir(base) if _MACHINE_RE.match(f))

    # 2026-09-17 — only offer machines that actually HAVE clips.  The folder is
    # created when the archiver first considers a machine, so 296 of them sit
    # empty across the archive; they still appeared in the dropdown and picking
    # one gave "no clips" — a dead end the operator has to back out of.  Count
    # once here and carry the number through, so the UI can show it too.
    counts = {f: _machine_clip_count(os.path.join(base, f)) for f in folders}
    folders = [f for f in folders if counts[f] > 0]

    # "main" is the line's own Final Inspection; "sub_<plc id>" needs its name.
    sub_ids = [int(f[4:]) for f in folders
               if f.startswith("sub_") and f[4:].isdigit()]
    sub_names: dict[int, str] = {}
    main_name = "Final Inspection"
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if sub_ids:
            cur.execute("SELECT id, machine_name FROM mes_plc_configs "
                        "WHERE id = ANY(%s)", (sub_ids,))
            for r in cur.fetchall():
                sub_names[r["id"]] = r["machine_name"] or f"Sub {r['id']}"
        cur.execute("SELECT machine_name FROM mes_plc_configs "
                    "WHERE line_id = %s AND parent_plc_id IS NULL LIMIT 1",
                    (line_id,))
        row = cur.fetchone()
        if row and row.get("machine_name"):
            main_name = row["machine_name"]

    out = []
    for f in folders:
        if f == "main":
            out.append({"machine": "main", "name": main_name, "is_main": True,
                        "clips": counts.get(f, 0)})
        else:
            sid = int(f[4:]) if f[4:].isdigit() else None
            out.append({"machine": f,
                        "name": sub_names.get(sid, f),
                        "is_main": False,
                        "clips": counts.get(f, 0)})
    # main first, then subs by name — matches how the dashboard lists them.
    out.sort(key=lambda m: (not m["is_main"], m["name"]))
    return {"machines": out}


# ── level 5: the clips themselves, with their ct_log metadata ──────────────
@router.get("/clips")
def list_clips(date: str = Query(...), line_id: int = Query(...),
               shift: str = Query(...), machine: str = Query(...),
               cycle_seq: Optional[int] = Query(None,
                   description="jump straight to one cycle serial"),
               part_code: Optional[str] = Query(None,
                   description="substring match on the scanned part code"),
               ng_only: bool = Query(False),
               page: int = Query(1, ge=1),
               page_size: int = Query(200, ge=1, le=1000),
               token: Optional[str] = Query(None), request: Request = None):
    """One page of cycles for a day/line/shift/machine.

    The clip LIST comes from disk (that is the truth about what can be played);
    the per-cycle detail comes from ct_log.  A clip with no matching ct_log row
    still appears — it just shows blank metadata — so a video is never hidden
    from the operator by a database gap.
    """
    _check_token(token, request)
    _need(date, _DATE_RE, "date")
    _need(shift, _SHIFT_RE, "shift")
    _need(machine, _MACHINE_RE, "machine")

    folder = os.path.join(_archive_root(), date,
                          f"line_{line_id}", shift, machine)

    # seq -> {ng: filename} (an OK and an Alarm clip can share a cycle_seq)
    found: dict[int, dict[bool, str]] = {}
    for name in _listdir(folder):
        m = _CLIP_RE.match(name)
        if not m:
            continue
        found.setdefault(int(m.group(1)), {})[bool(m.group(2))] = name

    seqs = sorted(found, reverse=True)          # newest cycle first
    if cycle_seq is not None:
        seqs = [s for s in seqs if s == cycle_seq]

    # Metadata for the whole shift in one query, then filter/paginate.  Reading
    # every row for one shift is a few thousand at most; per-clip queries would
    # be hundreds of round-trips for the same page.
    meta: dict[int, dict] = {}
    is_main = (machine == "main")
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if is_main:
            cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s",
                        (line_id,))
            row = cur.fetchone()
            tbl = (row or {}).get("db_table_name")
            if tbl:
                cur.execute("SELECT to_regclass(%s) AS t", (f"{tbl}_ct_log",))
                if cur.fetchone()["t"]:
                    cur.execute(
                        f"SELECT cycle_seq, ts, ct_value, part_code, is_ng "
                        f"FROM {tbl}_ct_log "
                        f"WHERE record_date = %s AND shift_name = %s",
                        (date, shift))
                    for r in cur.fetchall():
                        meta[r["cycle_seq"]] = {
                            "ts":        r["ts"].isoformat() if r["ts"] else None,
                            "ct":        float(r["ct_value"]) if r["ct_value"] is not None else None,
                            "part_code": r["part_code"],
                            "is_ng":     r["is_ng"],
                        }
        elif machine.startswith("sub_") and machine[4:].isdigit():
            cur.execute(
                "SELECT cycle_seq, ts_end, ct_seconds, part_code, is_ng "
                "FROM mes_submachine_ct_log "
                "WHERE sub_plc_id = %s AND line_id = %s "
                "AND record_date = %s AND shift_name = %s",
                (int(machine[4:]), line_id, date, shift))
            for r in cur.fetchall():
                meta[r["cycle_seq"]] = {
                    "ts":        r["ts_end"].isoformat() if r["ts_end"] else None,
                    "ct":        float(r["ct_seconds"]) if r["ct_seconds"] is not None else None,
                    "part_code": r["part_code"],
                    "is_ng":     r["is_ng"],
                }

    if part_code:
        needle = part_code.strip().lower()
        seqs = [s for s in seqs
                if needle in ((meta.get(s, {}).get("part_code") or "").lower())]
    if ng_only:
        seqs = [s for s in seqs
                if True in found[s] or meta.get(s, {}).get("is_ng")]

    total = len(seqs)
    start = (page - 1) * page_size
    window = seqs[start:start + page_size]

    clips = []
    for s in window:
        variants = found[s]
        # Prefer the NG clip when both exist: that is the one an operator
        # opening a historical alarm actually wants to see.
        ng = True if True in variants else False
        fname = variants[ng]
        full = os.path.join(folder, fname)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = None
        m = meta.get(s, {})
        clips.append({
            "cycle_seq": s,
            "ng":        ng,
            "has_ok":    False in variants,
            "has_ng":    True in variants,
            "size":      size,
            "ts":        m.get("ts"),
            "ct":        m.get("ct"),
            "part_code": m.get("part_code"),
            # Fall back to the filename flag whenever ct_log cannot answer —
            # both when the row is missing AND when its is_ng column is SQL
            # NULL.  `m.get("is_ng", ng)` only covered the first case: a NULL
            # column returns the key with value None, so the default never
            # applied and a cycle_<n>_ng.mp4 clip was badged OK.
            "is_ng":     ng if m.get("is_ng") is None else m["is_ng"],
        })

    # 2026-09-17 — report how many cycles actually RAN, not just how many have
    # video.  The archiver can only cut a clip while the rolling .ts still holds
    # that moment (CLIP_ARCHIVE_WINDOW_MIN, 42 min); on a busy shift it falls
    # behind and the rest age out with their footage gone for good.  The page
    # then showed a sparse, jumpy list — "#870, #869, #867 … #783, #12, #11" —
    # with no hint that 1,967 of 2,044 cycles simply have no video.  Operator,
    # rightly: "cycle aur clip number match nahi ho rahe".  Now the page can say
    # "77 of 2044 cycles have video" instead of leaving the gaps unexplained.
    cycles_total = len(meta) or None
    return {"date": date, "line_id": line_id, "shift": shift,
            "machine": machine, "total": total, "page": page,
            "page_size": page_size, "clips": clips,
            "cycles_total": cycles_total,
            "coverage_pct": (round(total * 100 / cycles_total)
                             if cycles_total else None)}


# ── playback ───────────────────────────────────────────────────────────────
@router.get("/video")
def get_video(date: str = Query(...), line_id: int = Query(...),
              shift: str = Query(...), machine: str = Query(...),
              cycle_seq: int = Query(...), ng: bool = Query(False),
              token: Optional[str] = Query(None), request: Request = None):
    """Stream one archived clip.

    The path is rebuilt with clip_archive.clip_path() rather than assembled
    here, so this route cannot drift from the archiver's layout, and served
    with clip_archive.serve() for the Range support the scrub bar needs.
    """
    _check_token(token, request)
    _need(date, _DATE_RE, "date")
    _need(shift, _SHIFT_RE, "shift")
    _need(machine, _MACHINE_RE, "machine")

    from routers.clip_archive import clip_path, serve

    if machine == "main":
        kind, owner_id = "line", line_id
    else:
        if not machine[4:].isdigit():
            raise HTTPException(400, "Invalid machine")
        kind, owner_id = "sub", int(machine[4:])

    path = clip_path(kind, owner_id, date, cycle_seq, ng,
                     shift=shift, line_id=line_id)
    if not os.path.isfile(path):
        # Fall back to the other NG variant before giving up — the caller may
        # have guessed the flag, and a playable clip beats a 404.
        alt = clip_path(kind, owner_id, date, cycle_seq, not ng,
                        shift=shift, line_id=line_id)
        if not os.path.isfile(alt):
            raise HTTPException(404, "No archived clip for this cycle")
        path = alt

    return serve(path, request)
