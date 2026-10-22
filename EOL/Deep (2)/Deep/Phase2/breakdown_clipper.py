#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
breakdown_clipper.py — cut the camera clip of every auto breakdown slip.

2026-10-12 (operator spec + decisions).  Reads, READ-ONLY, the two slip tables
in maintenance_db and saves one MP4 per closed slip for the Maintenance MES:

    maintenance_auto_breakdown_slip -> <DISK>/Maintenance_breakdown_video/YYYY/MM/DD/
    toolroom_auto_breakdown_slip    -> <DISK>/Toolroom_breakdown_video/YYYY/MM/DD/
    file name: <id>__<machine>__<START>__<END>.mp4   (START/END = YYYYMMDD-HHMM00)

Rules:
  * EVERY slip, old and new, no filter (operator 12-Oct 12:15: "koi bhi filter
    mt lga ... jitni video h vo yha dal de").  Slips are looked at for
    LOOKBACK_DAYS; a slip whose footage is already gone is logged
    "no recording" once.
  * A slip is cut once it is CLOSED (bd_ok_time filled) AND machine_no is
    filled: the machine decides the camera.
  * No zone is excluded (Press Shop / Thin Recliner included; a machine with
    no camera is logged "no camera").
  * Window = (start - 60 s) .. (end + 60 s); YYYY/MM/DD = bd_start_date.  Dates
    come only from bd_start_date / bd_end_date (slip_date is a SHIFT date).
  * H.264 yuv420p, <= 1280x720, ~1.2 Mbps, faststart; written as .mp4.part and
    renamed only after it is verified.  One file per slip (+ __cam2.. when the
    machine has more than one camera).
  * Clips older than 30 days are deleted (only files this service names).

Footage hold: the CMS keeps .ts footage 48 h, but production sometimes fills
machine_no days later.  While a closed slip waits for machine_no, the .ts
segments of every camera on its line that cover the breakdown are HARD-LINKED
into HOLD_ROOT (same disk: nothing is copied, and space is only used once the
CMS deletes its own name for the file).  The clip is cut from them when
machine_no arrives; the hold is dropped after the clip is saved or after
HOLD_DAYS.

Footage timing follows the CMS and the MES archiver: a segment's content
starts at mtime - probed duration (routers/clip_archive.py _ts_window).

Run:  .venv-linux/bin/python -u breakdown_clipper.py          (loop, every 60 s)
      .venv-linux/bin/python -u breakdown_clipper.py --once   (one pass)
Unit: ~/.config/systemd/user/breakdown-clipper.service
"""
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from database import DB_CONFIG  # noqa: E402  single source of DB credentials


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


DISK = os.environ.get("BDCLIP_DISK", "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735")
VIDEO_ROOT = os.environ.get("BDCLIP_VIDEO_ROOT", os.path.join(DISK, "eol-data", "videos"))
HOLD_ROOT = os.environ.get("BDCLIP_HOLD_ROOT", os.path.join(DISK, "eol-data", "breakdown_hold"))
# (folder key, table, clip root).  The folder key only names the hold/state
# entries; the other system tells the two apart by the root folder.
SOURCES = (
    ("maintenance", "maintenance_auto_breakdown_slip",
     os.environ.get("BDCLIP_MAINT_ROOT", os.path.join(DISK, "Maintenance_breakdown_video"))),
    ("toolroom", "toolroom_auto_breakdown_slip",
     os.environ.get("BDCLIP_TOOLROOM_ROOT", os.path.join(DISK, "Toolroom_breakdown_video"))),
)
STATE_DIR = os.environ.get("BDCLIP_STATE_DIR", os.path.join(HERE, "cache", "breakdown_clipper"))
LOG_FILE = os.environ.get("BDCLIP_LOG", os.path.join(os.path.dirname(HERE), "logs", "breakdown_clipper.log"))
FFMPEG = os.environ.get("BDCLIP_FFMPEG", "/usr/bin/ffmpeg")
FFPROBE = os.environ.get("BDCLIP_FFPROBE", "/usr/bin/ffprobe")
MAINT_DB = os.environ.get("MAINT_DB_NAME", "maintenance_db")
MES_DB = DB_CONFIG.get("database") or "energydb"

INTERVAL_S = _f("BDCLIP_INTERVAL_S", 60)
PAD_S = _f("BDCLIP_PAD_S", 60)
SETTLE_S = _f("BDCLIP_SETTLE_S", 120)        # footage past end+PAD must be on disk first
GAP_WAIT_S = _f("BDCLIP_GAP_WAIT_S", 900)    # footage with a hole: give it this long to fill
HOLD_DAYS = _f("BDCLIP_HOLD_DAYS", 7)
RETRY_DAYS = _f("BDCLIP_RETRY_DAYS", 3)      # no camera / no recording: retry this long
RETENTION_DAYS = _f("BDCLIP_RETENTION_DAYS", 30)
LOOKBACK_DAYS = _f("BDCLIP_LOOKBACK_DAYS", 30)      # = clip retention
ENC_THREADS = int(_f("BDCLIP_THREADS", 3))
MAX_W, MAX_H = 1280, 720
FULL_COVER = 0.98
EXCLUDE_ZONES = {z.strip().upper() for z in
                 os.environ.get("BDCLIP_EXCLUDE_ZONES", "").split(",") if z.strip()}
EXCLUDE_LINE_PREFIXES = tuple(p.strip().upper() for p in
                              os.environ.get("BDCLIP_EXCLUDE_LINES", "").split(",") if p.strip())

log = logging.getLogger("breakdown_clipper")
_STOP = {"flag": False, "child": None}


def _setup_logging() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=5)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


# ── database (read-only) ─────────────────────────────────────────────────────

def _connect(dbname: str):
    cfg = {k: v for k, v in DB_CONFIG.items() if k not in ("database", "dbname")}
    cfg["dbname"] = dbname
    cfg["application_name"] = "breakdown_clipper"
    # The server itself refuses any write in this session.
    cfg["options"] = "-c default_transaction_read_only=on -c TimeZone=Asia/Kolkata"
    conn = psycopg2.connect(**cfg)
    conn.set_session(readonly=True, autocommit=True)
    return conn


SLIP_COLS = ("id", "zone", "line", "machine_no", "bd_start_date", "bd_start_time",
             "bd_end_date", "bd_ok_time", "created_at")


def fetch_slips(conn, table: str, from_date: date, since: datetime = None) -> list:
    """Slips that started on/after from_date (or have no start yet).  `since`
    (optional, BDCLIP_SINCE) additionally drops slips created before it."""
    cur = conn.cursor()
    sql = (f"SELECT {', '.join(SLIP_COLS)} FROM {table} "
           f"WHERE (bd_start_date IS NULL OR bd_start_date >= %s)")
    args = [from_date]
    if since:
        sql += " AND created_at >= %s"
        args.append(since)
    cur.execute(sql + " ORDER BY id", args)
    return [dict(zip(SLIP_COLS, r)) for r in cur.fetchall()]


_TOK = re.compile(r"[a-z0-9]+")
_NAME_STOP = {"machine", "the", "and", "with", "for", "line", "station"}


def _tokens(s) -> set:
    return {t for t in _TOK.findall((s or "").lower())
            if len(t) >= 3 and t not in _NAME_STOP and not t.isdigit()}


def load_cameras(mconn, econn):
    """machine_no -> {line, name, cams[]} and line -> cams[].

    maintenance_machines.ip is the machine's PLC; the MES machine on that PLC
    carries the camera (mes_plc_configs.nf2_camera_id).  When one PLC serves
    several MES machines with different cameras, only those whose name shares
    words with the maintenance machine name are kept (L1_LP_02 'YLM Pipe
    Bending' -> the two Bending machines, not the Karakuri shelf); the
    MAG-welding recliner stations keep both cameras (main + __cam2)."""
    mc = mconn.cursor()
    mc.execute("SELECT machine_no, line_name, machine_name, ip FROM maintenance_machines "
               "WHERE is_active IS NOT FALSE")
    machines = mc.fetchall()
    ec = econn.cursor()
    ec.execute("SELECT id, machine_name, TRIM(plc_ip), TRIM(nf2_camera_id) FROM mes_plc_configs "
               "WHERE COALESCE(TRIM(plc_ip),'') <> '' AND COALESCE(TRIM(nf2_camera_id),'') <> '' "
               "ORDER BY id")
    by_ip = {}
    for pid, name, ip, cam in ec.fetchall():
        by_ip.setdefault(ip, []).append((pid, name, cam))
    by_machine, by_line = {}, {}
    for mno, line, mname, ip in machines:
        key = (mno or "").strip().upper()
        if not key:
            continue
        hits = by_ip.get((ip or "").strip(), [])
        if len({h[2] for h in hits}) > 1:
            want = _tokens(mname)
            scored = [(len(want & _tokens(h[1])), h) for h in hits]
            best = max(s for s, _h in scored)
            if best > 0:
                hits = [h for s, h in scored if s == best]
        cams = []
        for _pid, _n, cam in hits:
            if cam not in cams:
                cams.append(cam)
        by_machine[key] = {"line": line, "name": mname, "cams": cams}
        lk = (line or "").strip().upper()
        for c in cams:
            lst = by_line.setdefault(lk, [])
            if c not in lst:
                lst.append(c)
    return by_machine, by_line


# ── slip times and names ─────────────────────────────────────────────────────

_HM = re.compile(r"^\s*(\d{1,2})[:.](\d{2})")


def _hm(v):
    m = _HM.match(str(v or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if h <= 23 and mi <= 59 else None


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def slip_window(s: dict):
    """(start, end, problem).  problem None = usable; 'open' = not ended yet."""
    sd, st = _as_date(s.get("bd_start_date")), _hm(s.get("bd_start_time"))
    if not sd or not st:
        return None, None, "open"
    start = datetime(sd.year, sd.month, sd.day, *st)
    ot = _hm(s.get("bd_ok_time"))
    if not ot:
        return start, None, "open"
    ed = _as_date(s.get("bd_end_date"))
    if ed:
        end = datetime(ed.year, ed.month, ed.day, *ot)
    else:
        end = datetime(sd.year, sd.month, sd.day, *ot)
        if ot < st:
            end += timedelta(days=1)
    if end <= start:
        return start, end, "end time is not after start time"
    if end - start > timedelta(hours=24):
        return start, end, "breakdown longer than 24 h"
    return start, end, None


def safe_machine(m: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", (m or "").strip())


def clip_name(sid, machine: str, start: datetime, end: datetime, n: int = 1) -> str:
    base = f"{int(sid)}__{safe_machine(machine)}__{start:%Y%m%d-%H%M}00__{end:%Y%m%d-%H%M}00"
    return base + (f"__cam{n}" if n > 1 else "") + ".mp4"


def day_dir(root: str, start: datetime) -> str:
    return os.path.join(root, f"{start:%Y}", f"{start:%m}", f"{start:%d}")


def existing_clips(root: str, sid, start: datetime) -> list:
    d = day_dir(root, start)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    pre = f"{int(sid)}__"
    return sorted(os.path.join(d, n) for n in names if n.startswith(pre) and n.endswith(".mp4"))


def excluded(s: dict) -> bool:
    zone = (s.get("zone") or "").strip().upper()
    line = (s.get("line") or "").strip().upper()
    return zone in EXCLUDE_ZONES or any(line.startswith(p) for p in EXCLUDE_LINE_PREFIXES)


# ── footage ──────────────────────────────────────────────────────────────────

_SEG_RE = re.compile(r"^cam_(.+)_(\d{13})\.ts$")
_PROBE: dict = {}
_LS: dict = {}


def _listdir(d: str) -> list:
    if d not in _LS:
        try:
            _LS[d] = os.listdir(d)
        except OSError:
            _LS[d] = []
    return _LS[d]


def probe_ts(path: str):
    """{cs, ce, dur, t0, w, h} — cs/ce wall-clock epoch of the content."""
    st = os.stat(path)
    key = (st.st_size, st.st_mtime)
    c = _PROBE.get(path)
    if c and c[0] == key:
        return c[1]
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "format=start_time,duration:stream=width,height",
                        "-of", "json", path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    j = json.loads((r.stdout or b"{}").decode("utf-8", "ignore") or "{}")
    fmt = j.get("format") or {}
    strm = (j.get("streams") or [{}])[0]
    dur = float(fmt.get("duration") or 0)
    info = None
    if dur > 0:
        info = {"cs": st.st_mtime - dur, "ce": st.st_mtime, "dur": dur,
                "t0": float(fmt.get("start_time") or 0),
                "w": int(strm.get("width") or 0), "h": int(strm.get("height") or 0)}
    if len(_PROBE) > 20000:
        _PROBE.clear()
    _PROBE[path] = (key, info)
    return info


def list_segments(cam: str, dirs: list, a: float, b: float) -> list:
    pre = f"cam_{cam}_"
    out = {}
    for d in dirs:
        for name in _listdir(d):
            if not (name.startswith(pre) and name.endswith(".ts")):
                continue
            m = _SEG_RE.match(name)
            if not m or m.group(1) != cam or int(m.group(2)) / 1000.0 > b + 60:
                continue
            p = os.path.join(d, name)
            try:
                if os.path.getmtime(p) < a - 60:
                    continue
            except OSError:
                continue
            out.setdefault(name, p)     # same name in videos + hold = same file
    return list(out.values())


def footage_pieces(cam: str, dirs: list, ws: datetime, we: datetime) -> list:
    """Non-overlapping pieces of footage covering as much of [ws, we] as exists."""
    a, b = ws.timestamp(), we.timestamp()
    segs = []
    for p in list_segments(cam, dirs, a, b):
        try:
            info = probe_ts(p)
        except Exception:
            info = None
        if info and info["ce"] > a and info["cs"] < b:
            segs.append((info["cs"], p, info))
    segs.sort(key=lambda x: x[0])
    pieces, cursor = [], a
    for cs, p, info in segs:
        s, e = max(cs, cursor), min(info["ce"], b)
        if e - s < 0.5:
            continue
        pieces.append({"path": p, "in": s - cs, "out": e - cs, "dur": info["dur"],
                       "t0": info["t0"], "w": info["w"], "h": info["h"], "s": s, "e": e})
        cursor = e
        if cursor >= b - 0.01:
            break
    return pieces


def coverage(pieces: list, ws: datetime, we: datetime) -> float:
    span = (we - ws).total_seconds()
    return sum(p["e"] - p["s"] for p in pieces) / span if span > 0 else 0.0


# ── encode + verify ──────────────────────────────────────────────────────────

def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def out_size(w: int, h: int):
    if not (w and h):
        return 1280, 720
    f = min(1.0, MAX_W / w, MAX_H / h)
    return _even(w * f), _even(h * f)


def moov_first(path: str) -> bool:
    """faststart check: the moov box comes before mdat."""
    with open(path, "rb") as fh:
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                return False
            size, typ = struct.unpack(">I4s", hdr)
            hl = 8
            if size == 1:
                size, hl = struct.unpack(">Q", fh.read(8))[0], 16
            if typ == b"moov":
                return True
            if typ == b"mdat" or size < hl:
                return False
            fh.seek(size - hl, 1)


def _die_with_parent() -> None:
    """ffmpeg is killed with this process, so a restarted service never finds an
    orphan still writing the same .part file."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def _run(cmd: list, timeout: float) -> int:
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         preexec_fn=_die_with_parent)
    _STOP["child"] = p
    try:
        _out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise RuntimeError(f"ffmpeg timed out after {int(timeout)} s")
    finally:
        _STOP["child"] = None
    if p.returncode != 0:
        tail = (err or b"").decode("utf-8", "ignore").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg exit {p.returncode}: {tail[0][:200]}")
    return p.returncode


def verify(path: str, expect_s: float) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "format=duration:stream=codec_name,pix_fmt,width,height",
                        "-of", "json", path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    j = json.loads((r.stdout or b"{}").decode("utf-8", "ignore") or "{}")
    s = (j.get("streams") or [{}])[0]
    dur = float((j.get("format") or {}).get("duration") or 0)
    size = os.path.getsize(path)
    problems = []
    if s.get("codec_name") != "h264":
        problems.append(f"codec {s.get('codec_name')}")
    if s.get("pix_fmt") != "yuv420p":
        problems.append(f"pix_fmt {s.get('pix_fmt')}")
    if int(s.get("width") or 0) > MAX_W or int(s.get("height") or 0) > MAX_H:
        problems.append(f"size {s.get('width')}x{s.get('height')}")
    if size < 40 * 1024:
        problems.append(f"file only {size} bytes")
    if not (expect_s * 0.9 - 3 <= dur <= expect_s * 1.1 + 5):
        problems.append(f"duration {dur:.0f} s, expected {expect_s:.0f} s")
    if not moov_first(path):
        problems.append("index not at the front (no faststart)")
    if not problems:
        # It must decode: the first seconds and the last seconds.
        for ss in (0.0, max(0.0, dur - 5)):
            d = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-ss", f"{ss:.2f}", "-i", path,
                                "-t", "5", "-f", "null", "-"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            if d.returncode != 0:
                problems.append("does not decode")
                break
    return {"ok": not problems, "problems": problems, "dur": dur, "size": size,
            "w": s.get("width"), "h": s.get("height")}


def encode(pieces: list, part: str, list_path: str) -> None:
    with open(list_path, "w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n")
        for pc in pieces:
            fh.write("file '%s'\n" % pc["path"].replace("'", "'\\''"))
            if pc["in"] > 0.05:
                fh.write("inpoint %.3f\n" % (pc["t0"] + pc["in"]))
            if pc["out"] < pc["dur"] - 0.05:
                fh.write("outpoint %.3f\n" % (pc["t0"] + pc["out"]))
    w, h = out_size(pieces[0]["w"], pieces[0]["h"])
    cmd = ["nice", "-n", "19", "ionice", "-c", "3",
           FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
           "-f", "concat", "-safe", "0", "-i", list_path,
           "-map", "0:v:0", "-an", "-sn", "-dn",
           "-vf", f"scale={w}:{h}:flags=bicubic:out_range=tv,format=yuv420p",
           "-fps_mode", "cfr", "-r", "25",
           "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level:v", "4.0",
           "-b:v", "1200k", "-maxrate", "1500k", "-bufsize", "3000k", "-g", "50",
           "-threads", str(ENC_THREADS), "-color_range", "tv",
           "-movflags", "+faststart", "-f", "mp4", part]
    covered = sum(p["e"] - p["s"] for p in pieces)
    try:
        _run(cmd, timeout=max(600.0, covered * 3))
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def cut_one(pieces: list, dest: str) -> dict:
    """Write dest via dest.part; True only after verification."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    tmpdir = os.path.join(STATE_DIR, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    list_path = os.path.join(tmpdir, os.path.basename(dest) + ".ffconcat")
    encode(pieces, part, list_path)
    v = verify(part, sum(p["e"] - p["s"] for p in pieces))
    if not v["ok"]:
        try:
            os.remove(part)
        except OSError:
            pass
        raise RuntimeError("verify failed: " + "; ".join(v["problems"]))
    os.chmod(part, 0o644)
    os.replace(part, dest)
    return v


# ── footage hold ─────────────────────────────────────────────────────────────

def hold_dir(folder: str, sid) -> str:
    return os.path.join(HOLD_ROOT, folder, str(int(sid)))


def ensure_hold(folder: str, sid, cams: list, ws: datetime, we: datetime) -> int:
    d = hold_dir(folder, sid)
    os.makedirs(d, exist_ok=True)
    n = 0
    for cam in cams:
        for pc in footage_pieces(cam, [VIDEO_ROOT, d], ws, we):
            dst = os.path.join(d, os.path.basename(pc["path"]))
            if os.path.abspath(pc["path"]) == os.path.abspath(dst) or os.path.exists(dst):
                n += 1
                continue
            try:
                os.link(pc["path"], dst)
                n += 1
            except FileExistsError:
                n += 1
            except OSError as exc:
                log.info(f"WARN hold link failed {pc['path']}: {exc}")
    _LS.pop(d, None)
    return n


def drop_hold(folder: str, sid) -> None:
    d = hold_dir(folder, sid)
    if os.path.isdir(d) and os.path.dirname(os.path.dirname(d)) == HOLD_ROOT:
        shutil.rmtree(d, ignore_errors=True)


def sweep_holds(keep: set) -> None:
    """Remove hold folders no pending slip needs any more."""
    for folder, _t, _r in SOURCES:
        base = os.path.join(HOLD_ROOT, folder)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if n.isdigit() and f"{folder}:{int(n)}" not in keep:
                drop_hold(folder, n)


# ── retention ────────────────────────────────────────────────────────────────

_CLIP_RE = re.compile(r"^\d+__[A-Za-z0-9_-]*__\d{8}-\d{6}__\d{8}-\d{6}(__cam\d+)?\.mp4(\.part)?$")


def retention(now: datetime) -> int:
    """Delete this service's clips older than RETENTION_DAYS (by start date),
    plus .part leftovers older than 12 h.  Nothing else is touched."""
    cutoff = (now - timedelta(days=RETENTION_DAYS)).date()
    removed = 0
    for _folder, _t, root in SOURCES:
        for y in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            yd = os.path.join(root, y)
            if not (y.isdigit() and len(y) == 4 and os.path.isdir(yd)):
                continue
            for m in sorted(os.listdir(yd)):
                md = os.path.join(yd, m)
                if not (m.isdigit() and len(m) == 2 and os.path.isdir(md)):
                    continue
                for d in sorted(os.listdir(md)):
                    dd = os.path.join(md, d)
                    if not (d.isdigit() and len(d) == 2 and os.path.isdir(dd)):
                        continue
                    try:
                        day = date(int(y), int(m), int(d))
                    except ValueError:
                        continue
                    for n in os.listdir(dd):
                        p = os.path.join(dd, n)
                        if not _CLIP_RE.match(n):
                            continue
                        old = day < cutoff
                        stale_part = n.endswith(".part") and time.time() - os.path.getmtime(p) > 12 * 3600
                        if old or stale_part:
                            try:
                                os.remove(p)
                                removed += 1
                            except OSError:
                                pass
                    if day < cutoff:
                        try:
                            os.rmdir(dd)          # only if now empty
                        except OSError:
                            pass
                try:
                    os.rmdir(md)
                except OSError:
                    pass
            try:
                os.rmdir(yd)
            except OSError:
                pass
    return removed


# ── state ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(os.path.join(STATE_DIR, "state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st.setdefault("slips", {})
    return st


def save_state(st: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, "state.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, default=str)
    os.replace(tmp, p)


def _report(ent: dict, folder: str, s: dict, start, end, result: str, key: str = None) -> None:
    """One log line per slip per change of result."""
    k = key or result
    if ent.get("last") == k:
        return
    ent["last"] = k
    fmt = "%Y-%m-%d %H:%M"
    log.info(f"{folder} | id={s.get('id')} | machine={(s.get('machine_no') or '').strip() or '-'} | "
             f"start={start.strftime(fmt) if start else '-'} | end={end.strftime(fmt) if end else '-'} | "
             f"{result}")


# ── one slip ─────────────────────────────────────────────────────────────────

def process_slip(folder: str, root: str, s: dict, now: datetime, cams_by_machine: dict,
                 cams_by_line: dict, st: dict, pending_holds: set) -> None:
    key = f"{folder}:{int(s['id'])}"
    ent = st["slips"].setdefault(key, {})
    ent["seen"] = now.isoformat(timespec="seconds")
    if ent.get("final"):
        return
    start, end, problem = slip_window(s)
    if excluded(s):
        ent["final"] = "excluded"
        _report(ent, folder, s, start, end, "skipped: Press Shop / Thin Recliner are not clipped")
        return
    if problem == "open":
        _report(ent, folder, s, start, end, "waiting: breakdown still open")
        return
    if problem:
        _report(ent, folder, s, start, end, f"skipped: {problem}")
        return
    ws, we = start - timedelta(seconds=PAD_S), end + timedelta(seconds=PAD_S)
    have = existing_clips(root, s["id"], start)
    if have and ent.get("st") != "cutting":
        ent.update(final="done", path=have[0])
        _report(ent, folder, s, start, end, f"skipped: clip already exists {have[0]}")
        return
    if (now - we).total_seconds() < SETTLE_S:
        _report(ent, folder, s, start, end, "waiting: breakdown just ended, footage settling")
        return
    if time.time() < float(ent.get("next_try") or 0):
        return
    age_days = (now - end).total_seconds() / 86400.0
    machine = (s.get("machine_no") or "").strip()

    if not machine:
        if age_days > HOLD_DAYS:
            drop_hold(folder, s["id"])
            ent["final"] = "no machine_no"
            _report(ent, folder, s, start, end,
                    f"skipped: machine_no not filled within {HOLD_DAYS:g} days")
            return
        line_cams = cams_by_line.get((s.get("line") or "").strip().upper()) or []
        if not line_cams:
            _report(ent, folder, s, start, end,
                    "waiting: machine_no not filled (line has no camera, nothing to hold)")
            return
        n = ensure_hold(folder, s["id"], line_cams, ws, we)
        ent["held"] = n
        pending_holds.add(key)
        _report(ent, folder, s, start, end,
                f"waiting: machine_no not filled; footage held ({n} files, {len(line_cams)} cameras)",
                key="waiting: machine_no, held" if n else "waiting: machine_no, nothing to hold")
        return

    info = cams_by_machine.get(machine.upper())
    cams = (info or {}).get("cams") or []
    if not cams:
        why = "machine not in the machine list" if not info else "no camera mapped to this machine"
        if age_days > RETRY_DAYS:
            ent["final"] = "no camera"
            drop_hold(folder, s["id"])
            _report(ent, folder, s, start, end, f"skipped: {why} (gave up after {RETRY_DAYS:g} days)")
        else:
            pending_holds.add(key)
            _report(ent, folder, s, start, end, f"skipped: {why} (retrying)")
        return

    hd = hold_dir(folder, s["id"])
    dirs = [VIDEO_ROOT] + ([hd] if os.path.isdir(hd) else [])
    found = []
    for cam in cams:
        pcs = footage_pieces(cam, dirs, ws, we)
        if pcs:
            found.append((cam, pcs, coverage(pcs, ws, we)))
    if not found:
        if age_days > RETRY_DAYS:
            ent["final"] = "no footage"
            drop_hold(folder, s["id"])
            _report(ent, folder, s, start, end,
                    f"skipped: no recording for this time on {', '.join(cams)} (gave up)")
        else:
            pending_holds.add(key)
            _report(ent, folder, s, start, end,
                    f"skipped: no recording for this time on {', '.join(cams)} (retrying)")
        return
    if found[0][2] < FULL_COVER and (now - we).total_seconds() < GAP_WAIT_S:
        pending_holds.add(key)
        _report(ent, folder, s, start, end,
                f"waiting: footage incomplete ({found[0][2] * 100:.0f}%)", key="waiting: footage gap")
        return

    # Cut: main camera first, then __cam2.. — the slip counts as done only
    # after all of them, so a crash in between re-cuts every file.
    ent["st"] = "cutting"
    save_state(st)
    saved, errors = [], []
    for n, (cam, pcs, cov) in enumerate(found, start=1):
        dest = os.path.join(day_dir(root, start), clip_name(s["id"], machine, start, end, n))
        t0 = time.time()
        try:
            v = cut_one(pcs, dest)
            saved.append(f"{dest} ({v['dur'] / 60:.1f} min, {v['size'] / 1e6:.1f} MB, "
                         f"{v['w']}x{v['h']}, {time.time() - t0:.0f} s"
                         + (f", footage {cov * 100:.0f}%" if cov < FULL_COVER else "") + ")")
        except Exception as exc:
            if _STOP["flag"]:
                raise
            errors.append(f"{cam}: {exc}")
            if n == 1:
                break
    if not saved:
        ent["st"] = ""
        ent["fails"] = int(ent.get("fails") or 0) + 1
        ent["next_try"] = time.time() + min(3600, 60 * 2 ** ent["fails"])
        _report(ent, folder, s, start, end, "failed: " + "; ".join(errors),
                key=f"failed:{ent['fails'] > 1}")
        return
    ent.update(st="done", final="done", path=saved[0].split(" (")[0])
    drop_hold(folder, s["id"])
    pending_holds.discard(key)
    _report(ent, folder, s, start, end, "saved " + " + ".join(saved)
            + (f" | not saved: {'; '.join(errors)}" if errors else ""))


# ── run ──────────────────────────────────────────────────────────────────────

_NTP = {"t": 0.0, "warned": 0.0}


def _check_clock() -> None:
    if time.time() - _NTP["t"] < 600:
        return
    _NTP["t"] = time.time()
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
        synced = r.stdout.strip() == "yes"
    except Exception:
        synced = True
    if not synced and time.time() - _NTP["warned"] > 3600:
        _NTP["warned"] = time.time()
        log.info("WARN server clock is not NTP-synchronised; clip times follow the server clock")


def run_once(now: datetime = None, slips: dict = None) -> dict:
    """One pass.  `slips` ({folder: [rows]}) replaces the DB read in tests."""
    _LS.clear()
    st = load_state()
    st.pop("since", None)                 # 12-Oct: no go-live filter any more
    since = os.environ.get("BDCLIP_SINCE")
    since = datetime.fromisoformat(since) if since else None
    now = now or datetime.now()
    _check_clock()
    mconn = _connect(MAINT_DB)
    try:
        econn = _connect(MES_DB)
        try:
            cams_by_machine, cams_by_line = load_cameras(mconn, econn)
        finally:
            econn.close()
        if slips is None:
            lb = (now - timedelta(days=LOOKBACK_DAYS)).date()
            slips = {folder: fetch_slips(mconn, table, lb, since) for folder, table, _r in SOURCES}
    finally:
        mconn.close()
    pending_holds = set()
    for folder, _table, root in SOURCES:
        if not os.path.isdir(root):
            log.info(f"WARN clip folder missing, skipped this run: {root}")
            continue
        for s in slips.get(folder, []):
            if _STOP["flag"]:
                break
            try:
                process_slip(folder, root, s, now, cams_by_machine, cams_by_line, st, pending_holds)
            except Exception as exc:
                if _STOP["flag"]:
                    break
                log.info(f"{folder} | id={s.get('id')} | failed: {type(exc).__name__}: {exc}")
    # A hold stays only while its slip still waits; forget slips not seen for 10 days.
    keep = set(pending_holds)
    for k, e in st["slips"].items():
        if not e.get("final") and e.get("held"):
            keep.add(k)
    sweep_holds(keep)
    cutoff = (now - timedelta(days=10)).isoformat(timespec="seconds")
    st["slips"] = {k: e for k, e in st["slips"].items() if (e.get("seen") or "") >= cutoff}
    if time.time() - float(st.get("retention_at") or 0) > 3600:
        n = retention(now)
        st["retention_at"] = time.time()
        if n:
            log.info(f"retention: removed {n} clip file(s) older than {RETENTION_DAYS:g} days")
    save_state(st)
    return st


def _on_term(_sig, _frm):
    _STOP["flag"] = True
    p = _STOP.get("child")
    if p and p.poll() is None:
        try:
            p.terminate()
        except Exception:
            pass


def main() -> int:
    _setup_logging()
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another breakdown_clipper is already running; exiting")
        return 0
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    once = "--once" in sys.argv
    log.info(f"breakdown_clipper started (every {INTERVAL_S:g} s; hold {HOLD_DAYS:g} d; "
             f"keep clips {RETENTION_DAYS:g} d; excluded zones {sorted(EXCLUDE_ZONES)})")
    while not _STOP["flag"]:
        t0 = time.time()
        if not os.path.isdir(VIDEO_ROOT):
            log.info(f"WARN footage disk not mounted ({VIDEO_ROOT}); waiting")
        else:
            try:
                os.makedirs(HOLD_ROOT, exist_ok=True)
                run_once()
            except Exception as exc:
                log.info(f"run failed: {type(exc).__name__}: {exc}")
        if once:
            break
        while not _STOP["flag"] and time.time() - t0 < INTERVAL_S:
            time.sleep(1)
    log.info("breakdown_clipper stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
