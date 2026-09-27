"""logs.py — read-only log viewer for the whole stack.

Operator: "ek log view page bhi bana de for all log by filters, also by code
400 402 404 etc — aur ye page assignable ho baaki pages ki tarah."

Reading a log on this box is not as simple as `tail`:
  * the live logs are in `logs/` (project root), while `Phase2/logs/` still
    holds stale copies from July — reading the wrong one has already sent me
    chasing an error that was two months old;
  * MES-API.log reached 144 GB once, and any command that walks the whole file
    hangs.  Everything here reads the TAIL through a bounded seek, never the
    file, so size cannot matter;
  * nothing is ever written, moved or deleted — this module only reads.

Filters: file, free text, severity, HTTP status code (the operator's 400 / 404
/ 500 case), and how far back to look.
"""

from __future__ import annotations

import os
import socket
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user

router = APIRouter(prefix="/api/logs", tags=["logs"])

#  2026-09-27 — ROOT was one level short: dirname(dirname(routers/logs.py)) is
#  Phase2/, so the directory this file labelled "live" was Phase2/logs — the
#  STALE 17-Sep copies — while the running services write to <project>/logs.
#  The viewer was therefore serving ten-day-old logs as live, which is exactly
#  the trap its own header warns about.  PHASE2 keeps pointing at Phase2/ so
#  the stale copies stay reachable, clearly marked.
PHASE2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT   = os.path.dirname(PHASE2)

# Where logs live.  `logs/` first — that is the live one.
LOG_DIRS = [
    ("live",   os.path.join(ROOT, "logs")),
    ("phase2", os.path.join(PHASE2, "logs")),
    ("agent",  os.path.join(ROOT, "liveagent")),
    ("guardian", os.path.join(ROOT, "guardian")),
]

# Never read more than this from the end of a file, whatever is asked for.
MAX_TAIL_BYTES = 8 * 1024 * 1024
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# " ... HTTP/1.1" 404 -" and  '"GET /x" 500' and uvicorn's ' - 404 Not Found'
_CODE_RE = re.compile(r'(?:HTTP/[\d.]+"?\s+|\s-\s)(\d{3})(?:\s|$|\D)')
_LEVELS = {
    "error":   re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback|Exception)\b", re.I),
    "warning": re.compile(r"\b(WARN|WARNING)\b", re.I),
    "info":    re.compile(r"\b(INFO)\b"),
}


def _files() -> list[dict]:
    out = []
    for area, d in LOG_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".log"):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({
                "id": f"{area}/{name}",
                "area": area,
                "name": name,
                "size_mb": round(st.st_size / 1048576, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(st.st_mtime)),
                "age_min": int((time.time() - st.st_mtime) / 60),
            })
    # freshest first — the log someone needs is almost always the live one
    out.sort(key=lambda f: f["age_min"])
    return out


def _resolve(file_id: str) -> str:
    area, _, name = (file_id or "").partition("/")
    if not name or not _NAME_RE.match(name) or not name.endswith(".log"):
        raise HTTPException(400, "bad log file")
    for a, d in LOG_DIRS:
        if a == area:
            p = os.path.join(d, name)
            # belt and braces: the resolved path must stay inside its folder
            if os.path.realpath(p).startswith(os.path.realpath(d) + os.sep) \
               and os.path.isfile(p):
                return p
    raise HTTPException(404, "log file not found")


@router.get("/files")
def list_files(user=Depends(get_current_user)):
    """Every log the viewer can open, freshest first."""
    return {"files": _files()}


@router.get("/tail")
def tail(file: str = Query(..., description="id from /files, e.g. live/MES-API.log"),
         lines: int = Query(400, ge=1, le=5000),
         q: Optional[str] = Query(None, description="free text (case-insensitive)"),
         level: Optional[str] = Query(None, description="error|warning|info"),
         code: Optional[str] = Query(None, description="HTTP status, e.g. 404 or 4xx"),
         minutes: Optional[int] = Query(None, ge=1, le=10080,
                                        description="only lines from the last N minutes"),
         user=Depends(get_current_user)):
    """Filtered tail of one log file.

    Reads backwards from the end in bounded chunks, so a 144 GB file costs the
    same as a small one.  `scanned` in the reply says how many lines were
    actually looked at — when it equals the cap, the answer is "the newest
    matches", not "all of them", and the UI says so.
    """
    path = _resolve(file)
    size = os.path.getsize(path)
    # How much of the tail to read.  With no filter, `lines` lines are roughly
    # `lines * 400` bytes.  WITH a filter almost everything read gets thrown
    # away — asking for 50 x 404 out of a log that is 95% HTTP 200 needs far
    # more than 50 lines of input — so widen the window when filtering, up to
    # the hard cap.  Without this the viewer answered "7 matches" on a file
    # holding 876 of them, which reads as "there are only 7".
    filtering = bool(q or level or code)
    want = min(MAX_TAIL_BYTES,
               max(4 * 1024 * 1024 if filtering else 256 * 1024, lines * 400))
    with open(path, "rb") as fh:
        fh.seek(max(0, size - want))
        raw = fh.read().decode("utf-8", "replace")
    rows = raw.split("\n")
    if size > want and rows:
        rows = rows[1:]                       # drop the half line at the seek

    code_re = None
    if code:
        c = code.strip().lower()
        if re.fullmatch(r"\d{3}", c):
            code_re = re.compile(rf"(?:HTTP/[\d.]+\"?\s+|\s-\s|\s){c}(?:\s|$|\D)")
        elif re.fullmatch(r"\dxx", c):
            code_re = re.compile(rf"(?:HTTP/[\d.]+\"?\s+|\s-\s|\s){c[0]}\d\d(?:\s|$|\D)")
        else:
            raise HTTPException(400, "code must be like 404 or 4xx")

    lvl_re = _LEVELS.get((level or "").lower())
    needle = (q or "").lower().strip()
    cutoff = None
    if minutes:
        cutoff = time.time() - minutes * 60

    # Timestamps appear in a few shapes across these logs; match the common ones.
    ts_re = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})"
                       r"|\[(\d{2})/(\w{3})/(\d{4}) (\d{2}:\d{2}:\d{2})\]")

    undated = {"n": 0}

    def in_window(line: str) -> bool:
        if cutoff is None:
            return True
        m = ts_re.search(line)
        if not m:
            # uvicorn's access lines carry no timestamp, so a time filter
            # cannot judge them.  Keeping them is right — hiding real matches
            # would be worse — but the count is reported so the reader is not
            # told "45 in the last 2 minutes" about lines that could be hours
            # old.  Without this the filter looked like it had worked.
            undated["n"] += 1
            return True
        try:
            if m.group(1):
                t = time.mktime(time.strptime(f"{m.group(1)} {m.group(2)}",
                                              "%Y-%m-%d %H:%M:%S"))
            else:
                t = time.mktime(time.strptime(
                    f"{m.group(3)} {m.group(4)} {m.group(5)} {m.group(6)}",
                    "%d %b %Y %H:%M:%S"))
        except Exception:
            return True
        return t >= cutoff

    hits, scanned = [], 0
    for line in reversed(rows):                # newest first
        scanned += 1
        if not line.strip():
            continue
        if needle and needle not in line.lower():
            continue
        if lvl_re and not lvl_re.search(line):
            continue
        if code_re and not code_re.search(line):
            continue
        if not in_window(line):
            continue
        hits.append(line[:2000])
        if len(hits) >= lines:
            break

    return {"file": file, "size_mb": round(size / 1048576, 1),
            "scanned": scanned, "returned": len(hits),
            "truncated": size > want,
            "undated": undated["n"] if cutoff is not None else 0,
            "lines": hits}


@router.get("/codes")
def code_summary(file: str = Query(...),
                 minutes: int = Query(60, ge=1, le=10080),
                 user=Depends(get_current_user)):
    """How many of each HTTP status in the recent tail — the quick 'what is
    failing' view before you go reading individual lines."""
    path = _resolve(file)
    size = os.path.getsize(path)
    want = min(MAX_TAIL_BYTES, 4 * 1024 * 1024)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - want))
        raw = fh.read().decode("utf-8", "replace")
    counts: dict[str, int] = {}
    for line in raw.split("\n"):
        m = _CODE_RE.search(line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return {"file": file,
            "codes": sorted(({"code": k, "count": v} for k, v in counts.items()),
                            key=lambda x: -x["count"])}


# ─────────────────────────────────────────────────────────────────────────────
# COLLECTOR LIVE READS  (2026-09-27)
# ─────────────────────────────────────────────────────────────────────────────
# Operator: *"collector ki log viewer me ek tab or add kr de jisse m collector ki
# live read value dekh pau sab collectors ki"*.
#
# Every collector already prints a COLLECTOR STATUS block with exactly those
# values — per machine: PLC connected, DB connected, the count register and the
# value it just read, the NG register and its value, status, plan, actual, CT,
# speed loss, model, video flag, part code.  So this parses the LAST block out
# of each collector log rather than adding anything to the collectors: no
# collector change, no extra PLC traffic, and the numbers are exactly what the
# collector acted on.
_STATUS_HDR = "COLLECTOR STATUS"
_COL_HDR    = "TIME     MACHINE"


def _parse_status_block(text: str):
    """Last COLLECTOR STATUS block in `text` -> (meta, [row, …]).

    Columns are sliced by the offsets of the header words, because a machine
    name contains spaces and the numeric columns are right-aligned — splitting
    on whitespace would mis-align both.
    """
    i = text.rfind(_STATUS_HDR)
    if i < 0:
        return None, []
    block = text[i:]
    lines = block.splitlines()
    head = lines[0] if lines else ""
    parts = [p.strip() for p in head.split("|")]
    meta = {"line_name": parts[1] if len(parts) > 1 else "",
            "at": parts[2] if len(parts) > 2 else "",
            "shift": (parts[3].split("=")[-1] if len(parts) > 3 else "")}

    hdr_i = next((n for n, l in enumerate(lines) if l.startswith(_COL_HDR)), None)
    if hdr_i is None:
        return meta, []
    hdr = lines[hdr_i]
    names = ["TIME", "MACHINE", "PLC", "DB", "REG", "VALUE", "NGREG", "NGVAL",
             "STATUS", "PLAN", "ACTUAL", "CT", "SPDLOSS", "MODEL", "VID", "PARTCODE"]
    pos, at = [], 0
    for nm in names:
        j = hdr.find(nm, at)
        if j < 0:
            return meta, []
        pos.append(j)
        at = j + len(nm)
    pos.append(len(hdr) + 400)          # last column runs to end of line

    rows = []
    for l in lines[hdr_i + 1:]:
        if not l.strip() or set(l.strip()) <= {"-", "="}:
            if rows:
                break                   # the block's closing rule
            continue
        if not l[:8].replace(":", "").isdigit():
            break                       # left the table
        cells = [l[pos[k]:pos[k + 1]].strip() for k in range(len(names))]
        r = dict(zip([n.lower() for n in names], cells))
        #  The PLC and DB columns are two "[x]" / "[ ]" flags whose header
        #  words do not line up with the brackets, so header-offset slicing
        #  splits them ("[x]  [" / "x]").  Read them as the first two bracket
        #  tokens on the row instead; every other column slices correctly.
        flags = re.findall(r"\[[x ]\]", l[:pos[4]])
        if len(flags) >= 2:
            r["plc"], r["db"] = flags[0], flags[1]
        r["plc_ok"] = r.get("plc") == "[x]"
        r["db_ok"]  = r.get("db") == "[x]"
        #  MACHINE is fixed width and may contain spaces; it ends at the first
        #  flag bracket.
        b = l.find("[")
        if b > 9:
            r["machine"] = l[9:b].strip()
        rows.append(r)
    return meta, rows


#  2026-09-27 — operator: *"sare machine idle kyu dikha rehe h jbki jo andon
#  set h vo value aani chahiye maintenance wale se"*.
#
#  Measured before changing anything: the collectors were right — the PLCs
#  themselves were reporting status 0 and the last cycle was 5 minutes old, so
#  IDLE was the truth.  What IDLE cannot tell you is WHY a line is stopped:
#  today the status register produced only IDLE / RUNNING / BREAK /
#  MODEL_SETUP and BREAKDOWN twice, while the reason a line is actually down
#  (Maintenance, Quality, Toolroom, Material…) is raised on the Andon, which
#  lives in maintenance_db.  So the live view now carries the Andon beside the
#  status instead of leaving a bare IDLE.
#
#  andon_history.line_id is NULL on every row, so lines are matched by NAME
#  with separators stripped (YNC_SS -> YNC-SS).  That matches 10 of the 18
#  Andon lines outright; three more are aliased below.  PRESS_1/2/3, SA_LPS and
#  TWOUA_GEARLIFT have no unambiguous MES line, so they are left unmatched
#  rather than guessed.
_ANDON_ALIAS = {
    "LOOPPIPE1": "LOOPPIPELINE1",
    "LOOPPIPE2": "LOOPPIPELINE2",
    "YWDRC":     "YWDRECLINER",
}


def _norm_line(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (v or "")).upper()


def _andon_by_line():
    """{normalised line name: {...}} — the Andon each line is showing now, or
    the last one it raised today.  Never raises: a maintenance_db hiccup just
    means the column is blank."""
    out = {}
    try:
        from routers.andon import _maint_conn
        with _maint_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT line, display_name, priority, started_at, ended_at
                  FROM andon_history
                 WHERE started_at >= CURRENT_DATE - 1
                 ORDER BY started_at DESC""")
            for line, name, prio, st, en in cur.fetchall():
                k = _norm_line(line)
                k = _ANDON_ALIAS.get(k, k)
                if not k:
                    continue
                rec = out.get(k)
                #  An OPEN call always wins over a closed one, whatever the order.
                if rec and not rec["open"] and en is None:
                    rec = None
                if rec is None:
                    out[k] = {"call": name, "priority": prio,
                              "open": en is None,
                              "started_at": st.isoformat() if st else None,
                              "ended_at": en.isoformat() if en else None,
                              "today": 0}
                out[k]["today"] = out[k].get("today", 0) + 1
    except Exception:
        return {}
    return out


@router.get("/collector-reads")
def collector_reads(user=Depends(get_current_user)):
    """The latest live read every collector printed, one entry per collector,
    with the Andon its line is showing."""
    import glob as _glob
    andon = _andon_by_line()
    out = []
    now = time.time()
    seen = set()
    for area, d in LOG_DIRS:
        if not os.path.isdir(d):
            continue
        for path in _glob.glob(os.path.join(d, "*collector_*.log")):
            base = os.path.basename(path)
            #  logs/ is the live directory and is listed first, so a stale
            #  Phase2/logs/ copy of the same collector is skipped.
            if base in seen:
                continue
            seen.add(base)
            try:
                size = os.path.getsize(path)
                with open(path, "rb") as fh:
                    if size > 200_000:
                        fh.seek(-200_000, os.SEEK_END)
                    raw = fh.read().replace(b"\0", b"")
                text = raw.decode("utf-8", "replace")
                meta, rows = _parse_status_block(text)
                if meta is None:
                    continue
                fault = ""
                fi = text.rfind("[FAULT] machine")
                if fi >= 0:
                    fault = text[fi:text.find("\n", fi)].replace("[FAULT] ", "").strip()
                out.append({
                    "collector": base.replace("collector_collector_", "")
                                     .replace("collector_", "").replace(".log", ""),
                    "log": f"{area}/{base}",
                    "line_name": meta.get("line_name"),
                    "andon": andon.get(_norm_line(meta.get("line_name"))),
                    "printed_at": meta.get("at"),
                    "shift": meta.get("shift"),
                    "age_s": round(now - os.path.getmtime(path), 1),
                    "machines": rows,
                    "fault_watch": fault,
                })
            except Exception as exc:
                out.append({"collector": base, "log": f"{area}/{base}",
                            "error": str(exc)[:120], "machines": []})
    out.sort(key=lambda r: (r.get("line_name") or "", r.get("collector") or ""))
    return {"collectors": out, "count": len(out),
            "andon_lines": len(andon),
            "note": ("Parsed from each collector's own COLLECTOR STATUS block — "
                     "these are the values the collector actually read, not a "
                     "fresh PLC read by this API.  STATUS is what the PLC's own "
                     "status register says; the Andon column is the call raised "
                     "on the maintenance side, which is where the REASON for a "
                     "stop lives.")}


# ─────────────────────────────────────────────────────────────────────────────
# PORTS  (2026-09-27)
# ─────────────────────────────────────────────────────────────────────────────
# Operator: *"log viewer me port and their status and their services in a new tab
# called ports jisme mere system k sare ports ho with description and activeness
# time"*.  Read-only by their choice — nothing here can stop or start anything.
#
# Listening sockets come from /proc/net/tcp{,6} and are matched to a PID through
# /proc/<pid>/fd socket inodes.  That resolves every process running as this
# user (all the MES/plant services); sockets owned by root or another user
# cannot be matched without privileges and are reported honestly as
# "not identified" rather than guessed.
#
# NOTE: a process command line can carry a DB password or token, so only the
# program and its script name are ever returned — never the full argv.
_PORT_INFO = {
    8080: ("MES-API",            "The MES application API (uvicorn). Everything the app does goes through it.", True),
    5656: ("MES Frontend",       "Serves the built app and proxies /api to 8080. This is what the panels and the tunnel open.", True),
    5700: ("Landing Page",       "tbdi.in landing page (same server, different docroot).", True),
    5555: ("CMS-API",            "Camera service: recorders, clip cutting, per-cycle video.", True),
    5575: ("CMS Frontend",       "The camera system's own web UI.", True),
    8090: ("BinVision",          "Bin-filling counter, embedded in MES as the Bin Filling tab.", True),
    5432: ("PostgreSQL",         "The database: energydb and maintenance_db.", True),
    9965: ("Maintenance App",    "Maintenance / Andon application (maintenance.tbdi.in).", True),
    5173: ("DQMP SQA Frontend",  "Quality (SQA) application front end.", False),
    8000: ("DQMP SQA Backend",   "Quality (SQA) application API.", False),
    3000: ("Node App",           "Node service on this box.", False),
    5758: ("Node App",           "Node service on this box.", False),
    5959: ("Node App",           "Node service on this box.", False),
    5053: ("cloudflared DNS",    "DNS-over-HTTPS resolver used by the tunnel client.", False),
    3389: ("Remote Desktop",     "RDP. Anyone who reaches it can try to log in to this machine.", False),
    3390: ("Remote Desktop",     "gnome-remote-desktop, second RDP port.", False),
    5939: ("TeamViewer",         "Remote support agent.", False),
    27018:("MongoDB",            "MongoDB instance.", False),
    25:   ("SMTP",               "Local mail submission.", False),
    631:  ("CUPS",               "Printing service.", False),
    53:   ("DNS",                "Local DNS resolver.", False),
    7070: ("Unidentified",       "Not identified — belongs to another user or a vendor service.", False),
    8892: ("Unidentified",       "Not identified — belongs to another user or a vendor service.", False),
}
_MES_PORTS = [8080, 5656, 5555, 8090, 5432, 5700, 5575, 9965]


def _listening_sockets():
    """{port: {addrs:set, v6:bool}} for every LISTEN socket."""
    out = {}
    for path, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            lines = open(path).read().splitlines()[1:]
        except Exception:
            continue
        for ln in lines:
            p = ln.split()
            if len(p) < 10 or p[3] != "0A":          # 0A = LISTEN
                continue
            hexip, hexport = p[1].rsplit(":", 1)
            port = int(hexport, 16)
            if v6:
                addr = "::" if set(hexip) <= {"0"} else "[v6]"
            else:
                b = [int(hexip[i:i + 2], 16) for i in (6, 4, 2, 0)]
                addr = ".".join(str(x) for x in b)
            rec = out.setdefault(port, {"addrs": set(), "inodes": set()})
            rec["addrs"].add(addr)
            rec["inodes"].add(p[9])
    return out


def _inode_to_pid():
    import glob as _g
    m = {}
    for d in _g.glob("/proc/[0-9]*/fd/*"):
        try:
            t = os.readlink(d)
        except Exception:
            continue
        if t.startswith("socket:["):
            m[t[8:-1]] = int(d.split("/")[2])
    return m


def _proc_brief(pid: int):
    """(display name, uptime seconds) — program + script only, never full argv."""
    try:
        argv = [a for a in open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0") if a]
        parts = [a.decode("utf-8", "replace") for a in argv]
    except Exception:
        return None, None
    prog = os.path.basename(parts[0]) if parts else "?"

    def _script_of(ps):
        for i, a in enumerate(ps[1:], start=1):
            if a.endswith((".py", ".js", ".mjs", ".sh")):
                return os.path.basename(a)
            if a == "-m" and len(ps) > i + 1:
                return ps[i + 1]
            #  a bare path with no known extension (several node apps here)
            if a.startswith("/") and not a.startswith("-") and os.sep in a:
                return os.path.basename(a)
        return ""

    script = _script_of(parts)
    if not script:
        #  A uvicorn worker is spawned through multiprocessing, so its own argv
        #  is `python -c from multiprocessing...` with no script in it.  The
        #  parent still carries the real one.
        try:
            ppid = int(open(f"/proc/{pid}/stat").read().split(") ", 1)[1].split()[1])
            pargv = [a.decode("utf-8", "replace")
                     for a in open(f"/proc/{ppid}/cmdline", "rb").read().split(b"\0") if a]
            script = _script_of(pargv)
        except Exception:
            pass
    name = f"{prog} {script}".strip()
    up = None
    try:
        clk = os.sysconf("SC_CLK_TCK")
        starttime = int(open(f"/proc/{pid}/stat").read().split(") ", 1)[1].split()[19])
        boot_up = float(open("/proc/uptime").read().split()[0])
        up = round(boot_up - starttime / clk, 1)
    except Exception:
        pass
    return name, up


@router.get("/ports")
def list_ports(user=Depends(get_current_user)):
    """Every TCP port this machine is listening on, with the service behind it.

    Read-only.  `lan_open` is the one worth scanning: a socket bound to 0.0.0.0
    is reachable from any PC on the plant network, while 127.0.0.1 can only be
    reached from the server itself.
    """
    socks = _listening_sockets()
    ino2pid = _inode_to_pid()
    now_rows = []
    for port, rec in socks.items():
        pid = None
        for ino in rec["inodes"]:
            if ino in ino2pid:
                pid = ino2pid[ino]
                break
        pname, up = _proc_brief(pid) if pid else (None, None)
        addrs = sorted(rec["addrs"])
        lan_open = any(a in ("0.0.0.0", "::") for a in addrs)
        label, desc, is_mes = _PORT_INFO.get(port, (None, None, False))
        if not label:
            label = pname or "Unidentified"
            desc = ("Not in the known-services list — identified from the process."
                    if pname else
                    "Not identified: the socket belongs to another user, which this "
                    "service cannot inspect without privileges.")
        #  Does it actually answer?  Probe the address it is really bound to —
        #  127.0.0.53 (systemd-resolved) never answers on 127.0.0.1 and would
        #  otherwise be reported as down.
        probe = "127.0.0.1"
        for a in addrs:
            if a not in ("0.0.0.0", "::", "[v6]"):
                probe = a
                break
        alive = False
        for host in ([probe] if probe != "127.0.0.1" else ["127.0.0.1"]):
            try:
                _s = socket.create_connection((host, port), timeout=0.4)
                _s.close()
                alive = True
                break
            except Exception:
                pass
        now_rows.append({
            "port": port, "service": label, "description": desc,
            "process": pname, "pid": pid,
            "uptime_s": up, "bind": addrs,
            "lan_open": lan_open, "responding": alive,
            "is_mes": bool(is_mes),
        })
    now_rows.sort(key=lambda r: (not r["is_mes"],
                                 _MES_PORTS.index(r["port"]) if r["port"] in _MES_PORTS else 999,
                                 r["port"]))
    return {
        "ports": now_rows,
        "count": len(now_rows),
        "lan_open": sum(1 for r in now_rows if r["lan_open"]),
        "identified": sum(1 for r in now_rows if r["pid"]),
        "note": ("Uptime is how long the process holding the port has been running. "
                 "A port bound to 0.0.0.0 is reachable from every PC on the plant "
                 "network; 127.0.0.1 is reachable only from this server."),
    }
