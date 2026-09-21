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
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user

router = APIRouter(prefix="/api/logs", tags=["logs"])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where logs live.  `logs/` first — that is the live one.
LOG_DIRS = [
    ("live",   os.path.join(ROOT, "logs")),
    ("phase2", os.path.join(ROOT, "Phase2", "logs")),
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
