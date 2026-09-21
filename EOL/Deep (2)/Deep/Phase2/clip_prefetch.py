# ════════════════════════════════════════════════════════════════
# clip_prefetch.py — warm the cycle-video cache for ACTIVE lines
# ════════════════════════════════════════════════════════════════
"""
Why this exists
---------------
Clicking a CT-graph dot used to pay ~0.5-2 s of ffmpeg extraction before a
single byte moved, because the clip for that cycle had never been rendered.
On the shop floor the operator almost always reviews a RECENT cycle, so those
few clips can be rendered *before* anyone clicks.

This worker walks every line that is actually producing, takes its last few
cycles, and asks our own `/api/lines/{id}/cycle-video` for each one with
`Range: bytes=0-0`.  That drives the normal path end-to-end — the CMS renders
the clip and caches it under `cycclip_<md5(camera|start|end)>.mp4` — while
transferring a single byte back.  A later real click on the same dot then hits
a finished file: extraction time drops to zero.

Deliberate limits (this must never compete with a real request):
  * only lines with a cycle in the last PRODUCING_WINDOW_S — idle/停 lines cost
    nothing,
  * only the newest CYCLES_PER_LINE cycles,
  * every (line, date, cycle) is prefetched ONCE (bounded memory),
  * MAX_PARALLEL workers, and a short per-request timeout,
  * the CMS keeps a 10-minute stale sweep on that cache, so there is no point
    reaching further back than a few minutes of production.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from database import get_conn, dict_cursor

# ── tunables (env-overridable, sane defaults) ───────────────────────────
INTERVAL_S        = int(os.getenv("CLIP_PREFETCH_INTERVAL",  "60"))
CYCLES_PER_LINE   = int(os.getenv("CLIP_PREFETCH_CYCLES",    "3"))
PRODUCING_WINDOW_S= int(os.getenv("CLIP_PREFETCH_WINDOW",    "600"))
MAX_PARALLEL      = int(os.getenv("CLIP_PREFETCH_PARALLEL",  "4"))
REQ_TIMEOUT_S     = int(os.getenv("CLIP_PREFETCH_TIMEOUT",   "45"))
SELF_BASE         = os.getenv("CLIP_PREFETCH_BASE", "http://127.0.0.1:8080")
ENABLED           = os.getenv("CLIP_PREFETCH", "1") not in ("0", "false", "no")

_done: "OrderedDict[str, float]" = OrderedDict()   # key -> ts, bounded LRU
_DONE_MAX = 4000
_stats = {"warmed": 0, "failed": 0, "rounds": 0}


def _mark(key: str) -> bool:
    """True if this key is new (and records it); False if already prefetched."""
    if key in _done:
        return False
    _done[key] = time.time()
    while len(_done) > _DONE_MAX:
        _done.popitem(last=False)
    return True


def _recent_cycles():
    """[(line_id, record_date, shift_name, cycle_seq)] for lines producing now."""
    out = []
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT id, db_table_name FROM mes_lines
            WHERE COALESCE(is_active, true) AND COALESCE(db_table_name,'') <> ''
            ORDER BY id
        """)
        lines = cur.fetchall()
        for ln in lines:
            tbl = f"{ln['db_table_name']}_ct_log"
            try:
                cur.execute("SELECT to_regclass(%s) AS t", (tbl,))
                if not cur.fetchone()["t"]:
                    continue
                # newest cycles, and only if the line is actually running
                cur.execute(
                    f"""SELECT cycle_seq, record_date, shift_name, ts
                          FROM {tbl}
                         WHERE record_date = CURRENT_DATE
                           AND ts > now() - (%s || ' seconds')::interval
                         ORDER BY ts DESC LIMIT %s""",
                    (PRODUCING_WINDOW_S, CYCLES_PER_LINE))
                for r in cur.fetchall():
                    out.append((ln["id"], r["record_date"], r["shift_name"], r["cycle_seq"]))
            except Exception:
                conn.rollback()   # a missing/odd table must not kill the round
                continue
    return out


def _warm(line_id: int, rec_date, shift: str, cycle_seq: int) -> None:
    """Render+cache one clip via our own endpoint, pulling back only 1 byte."""
    url = (f"{SELF_BASE}/api/lines/{line_id}/cycle-video"
           f"?cycle_seq={cycle_seq}&date={rec_date}&shift={shift or ''}")
    try:
        r = requests.get(url, headers={"Range": "bytes=0-0"},
                         timeout=REQ_TIMEOUT_S, stream=True)
        r.close()
        if r.status_code < 400:
            _stats["warmed"] += 1
        else:
            _stats["failed"] += 1
    except Exception:
        _stats["failed"] += 1


def _loop() -> None:
    # let the API and the CMS finish coming up before the first round
    time.sleep(45)
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="clipwarm")
    while True:
        try:
            todo = [c for c in _recent_cycles()
                    if _mark(f"{c[0]}|{c[1]}|{c[3]}")]
            if todo:
                list(pool.map(lambda c: _warm(*c), todo))
            _stats["rounds"] += 1
            if _stats["rounds"] % 10 == 1:
                print(f"[CLIP-PREFETCH] round {_stats['rounds']}: "
                      f"+{len(todo)} queued, warmed={_stats['warmed']} "
                      f"failed={_stats['failed']}", flush=True)
        except Exception as e:
            print(f"[CLIP-PREFETCH] round error: {e}", flush=True)
        time.sleep(INTERVAL_S)


def start_clip_prefetch() -> None:
    """Fire the background warmer (no-op when CLIP_PREFETCH=0)."""
    if not ENABLED:
        print("[CLIP-PREFETCH] disabled (CLIP_PREFETCH=0)", flush=True)
        return
    threading.Thread(target=_loop, daemon=True, name="clip-prefetch").start()
    print(f"[CLIP-PREFETCH] started — {CYCLES_PER_LINE} newest cycles/line every "
          f"{INTERVAL_S}s for lines producing within {PRODUCING_WINDOW_S}s",
          flush=True)
