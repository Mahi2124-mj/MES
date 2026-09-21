#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# mes_loadtest.py — MES multi-user load emulator
#
# Operator ask: "ek emulator bana ke chala — har user se ek new tab khule, kam
# se kam 20 ek saath video on karein, har user ka time note ho ki kitna laga
# aur KYUN laga. Mujhe time loss nahi chahiye, permanent solution chahiye."
#
# WHAT IT DOES
#   Spawns N virtual users (default 20) in parallel.  Each one repeats the exact
#   request sequence a real browser tab performs:
#       1. auth check            GET /api/auth/me
#       2. dashboard load        GET /api/lines/           (the page's first call)
#       3. live polls            GET /api/lines/{id}/realtime   (what the tiles poll)
#       4. video preflight       GET cycle-video  Range: bytes=0-0
#                                (the browser's own probe before it plays)
#       5. video load            GET cycle-video  (full body = "video chalu")
#   Every phase is timed separately, so a slow tab can be attributed to a PHASE
#   instead of a guess — that is the "kyun laga" half of the ask.
#
# WHY HTTP-LEVEL AND NOT 20 REAL BROWSERS
#   The server under test is this same box.  Twenty real Chrome instances would
#   burn CPU/GPU on the box we are measuring and pollute the numbers with their
#   own decode/render cost.  These virtual users issue byte-for-byte the same
#   requests (including the Range preflight), so the SERVER-side truth — which
#   is what "video load hone me time kyun lag raha hai" is really asking — is
#   measured cleanly, and 20+ of them cost almost nothing on the client side.
#
# LIVE VIEW
#   Writes status.json every second and serves it, plus dashboard.html, on
#   :8099 — open http://<host>:8099/ to watch every virtual user live.
#
#   Run:  python3 loadtest/mes_loadtest.py --users 20 --duration 120
# ─────────────────────────────────────────────────────────────────────────────
import argparse, json, os, random, statistics, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))

BASE = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
STATUS_PATH = os.path.join(HERE, "status.json")

NO_CAMERA = []          # lines skipped because no camera is bound to them
_lock = threading.Lock()
STATE = {
    "started_at": None, "base": BASE, "users": 0, "duration": 0,
    "running": True, "elapsed": 0,
    "agents": {},           # user_id -> live row
    "samples": [],          # completed video samples
}


def mint_token(username, role, uid):
    """Server-side token mint — no password is read or sent anywhere."""
    from auth import create_token           # type: ignore
    return create_token(username, role, uid)


def pick_targets():
    """Lines that actually have footage, and a recent cycle for each, so the
    emulator plays REAL video instead of 404-ing on empty lines."""
    import psycopg2, psycopg2.extras
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1", port=5432)
    c.autocommit = True
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Only lines that actually have a CAMERA bound.  A line with no camera has
    # no video at all, so including it would measure "video missing" instead of
    # "how long video takes" — the sub-assembly lines (33,34,37..41) are in that
    # state and were the entire 404 share of the first run.  They are reported
    # separately as a provisioning gap, not a performance number.
    cur.execute("""SELECT l.id, l.line_name, l.db_table_name,
                          NULLIF(TRIM(pc.nf2_camera_id),'') AS cam
                     FROM mes_lines l
                LEFT JOIN mes_plc_configs pc ON pc.line_id = l.id
                      AND ((l.dashboard_plc_id IS NOT NULL AND pc.id = l.dashboard_plc_id)
                        OR (l.dashboard_plc_id IS NULL AND pc.parent_plc_id IS NULL))
                    WHERE l.db_table_name IS NOT NULL AND l.db_table_name <> ''
                      AND COALESCE(l.is_active, TRUE) = TRUE
                    ORDER BY l.id""")
    out = []
    global NO_CAMERA
    NO_CAMERA = []
    for r in cur.fetchall():
        t = r["db_table_name"]
        if not t.replace("_", "").isalnum():
            continue
        if not r.get("cam"):
            NO_CAMERA.append(r["line_name"] + " (no camera bound)")
            continue
        # A camera can be CONFIGURED and still deliver nothing — the sub-assembly
        # lines are in exactly that state (PLC/camera unreachable), so every one
        # of their cycles 404s.  Treat "zero clips archived recently" as proof
        # the feed is dead and leave the line out of the timing run; it is a
        # provisioning fault, not a speed measurement.
        try:
            import glob as _g
            _hits = 0
            for _d in _g.glob(os.path.join(
                    "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips",
                    "*", f"line_{r['id']}", "*", "main", "*.mp4")):
                if time.time() - os.path.getmtime(_d) < 1800:
                    _hits += 1
                    if _hits >= 3:
                        break
            if _hits < 3:
                NO_CAMERA.append(r["line_name"] + " (camera set but no footage)")
                continue
        except Exception:
            pass
        try:
            # Keep to the last few minutes: the rolling .ts only holds footage
            # since the recorder last started, so older cycles 404 and would
            # measure "no video" instead of "how long video takes".
            cur.execute(f"""SELECT cycle_seq, shift_name, record_date
                              FROM {t}_ct_log
                             WHERE ts > now() - interval '8 min'
                               AND ct_value IS NOT NULL
                             ORDER BY id DESC LIMIT 20""")
            rows = cur.fetchall()
            if rows:
                out.append({"line_id": r["id"], "line_name": r["line_name"],
                            "cycles": [int(x["cycle_seq"]) for x in rows]})
        except Exception:
            continue
    c.close()
    return out


def classify(phase_ms, status, clip_source, err):
    """The 'kyun' column — turn a slow sample into a named cause."""
    if err:
        return f"ERROR: {err}"
    if status == 502:
        return "502 — upstream clip service failed/timed out"
    if status == 404:
        return "404 — no footage for that cycle"
    if status in (200, 206):
        # The endpoint reports HOW it answered in X-Clip-Source: "archive" (a
        # pre-rendered clip read off disk), "cache" (the CMS temp cache), or
        # nothing when it had to cut the clip live.  Classify on THAT first —
        # judging by elapsed time alone mislabelled archive reads of a big clip
        # as "render on demand" and hid the real cache-hit rate.
        src = (clip_source or "").lower()
        if src in ("archive", "cache"):
            if phase_ms > 2000:
                return f"{src} hit — but slow transfer (>2s)"
            return f"fast — pre-rendered {src} hit"
        if phase_ms > 15000:
            return "SLOW — render exceeded the 15s upstream timeout budget"
        if phase_ms > 5000:
            return "slow — cold render (clip cut on demand)"
        if phase_ms > 2000:
            return "render on demand (2-5s)"
        return "fast — rendered under 2s"
    return f"HTTP {status}"


def agent(uid, token, targets, stop_at, think):
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    row = {"user": uid, "state": "starting", "loops": 0, "last": {}, "err": 0}
    with _lock:
        STATE["agents"][uid] = row

    while time.time() < stop_at and STATE["running"]:
        tgt = random.choice(targets)
        lid = tgt["line_id"]
        seq = random.choice(tgt["cycles"])
        sample = {"user": uid, "line": tgt["line_name"], "cycle": seq, "t": time.time()}
        try:
            # 1 auth
            row["state"] = "auth"
            t0 = time.time(); s.get(f"{BASE}/api/auth/me", timeout=20)
            sample["auth_ms"] = int((time.time() - t0) * 1000)

            # 2 dashboard first paint
            row["state"] = "dashboard"
            t0 = time.time(); s.get(f"{BASE}/api/lines/", timeout=30)
            sample["dash_ms"] = int((time.time() - t0) * 1000)

            # 3 live tile polls
            row["state"] = "realtime"
            t0 = time.time()
            for _ in range(3):
                s.get(f"{BASE}/api/lines/{lid}/realtime", timeout=20)
            sample["realtime_ms"] = int((time.time() - t0) * 1000)

            # 4 video preflight (browser's Range probe)
            row["state"] = "video preflight"
            vurl = f"{BASE}/api/lines/{lid}/cycle-video?cycle_seq={seq}&ng=0&r={seq}"
            t0 = time.time()
            pf = s.get(vurl, headers={"Range": "bytes=0-0"}, timeout=60)
            sample["preflight_ms"] = int((time.time() - t0) * 1000)

            # 5 video load — the thing the operator watches spin
            row["state"] = "video"
            t0 = time.time(); first = None; nbytes = 0
            r = s.get(vurl, stream=True, timeout=90)
            for chunk in r.iter_content(64 * 1024):
                if first is None:
                    first = int((time.time() - t0) * 1000)
                nbytes += len(chunk)
            total = int((time.time() - t0) * 1000)
            sample.update({
                "status": r.status_code,
                "ttfb_ms": first if first is not None else total,
                "video_ms": total,
                "bytes": nbytes,
                "clip_source": r.headers.get("X-Clip-Source") or r.headers.get("X-Video-Source") or "",
            })
            sample["total_ms"] = (sample["auth_ms"] + sample["dash_ms"] +
                                  sample["realtime_ms"] + sample["preflight_ms"] + total)
            sample["why"] = classify(total, r.status_code, sample["clip_source"], None)
        except Exception as e:
            sample["status"] = 0
            sample["why"] = classify(0, 0, "", f"{type(e).__name__}: {str(e)[:60]}")
            sample["total_ms"] = sample.get("total_ms", 0)
            row["err"] += 1

        row["loops"] += 1
        row["last"] = sample
        row["state"] = "think"
        with _lock:
            STATE["samples"].append(sample)
            if len(STATE["samples"]) > 4000:
                del STATE["samples"][:1000]
        time.sleep(think * random.uniform(0.6, 1.4))

    row["state"] = "done"


def writer(stop_at):
    while STATE["running"] and time.time() < stop_at + 2:
        with _lock:
            done = [s for s in STATE["samples"] if s.get("video_ms") is not None]
            vids = [s["video_ms"] for s in done if s.get("status") in (200, 206)]
            tots = [s["total_ms"] for s in done if s.get("total_ms")]
            why = {}
            for s in done:
                why[s.get("why", "?")] = why.get(s.get("why", "?"), 0) + 1
            snap = {
                "base": STATE["base"], "users": STATE["users"],
                "elapsed": int(time.time() - STATE["started_at"]),
                "duration": STATE["duration"],
                "running": STATE["running"],
                "agents": sorted(STATE["agents"].values(), key=lambda a: a["user"]),
                "totals": {
                    "samples": len(done),
                    "ok": sum(1 for s in done if s.get("status") in (200, 206)),
                    "failed": sum(1 for s in done if s.get("status") not in (200, 206)),
                    "video_avg_ms": int(statistics.mean(vids)) if vids else 0,
                    "video_p50_ms": int(statistics.median(vids)) if vids else 0,
                    "video_p95_ms": int(sorted(vids)[int(len(vids) * .95)]) if len(vids) > 5 else (max(vids) if vids else 0),
                    "video_max_ms": max(vids) if vids else 0,
                    "page_avg_ms": int(statistics.mean(tots)) if tots else 0,
                },
                "why": sorted(why.items(), key=lambda kv: -kv[1]),
                "recent": done[-25:][::-1],
            }
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, STATUS_PATH)
        time.sleep(1)


def serve(port):
    handler = partial(SimpleHTTPRequestHandler, directory=HERE)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--duration", type=int, default=120, help="seconds")
    ap.add_argument("--think", type=float, default=3.0, help="seconds between loops")
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()

    print(f"[loadtest] resolving lines with recent footage…", flush=True)
    targets = pick_targets()
    if not targets:
        print("[loadtest] no lines with recent cycles — is production running?")
        return
    print(f"[loadtest] {len(targets)} lines usable: "
          f"{', '.join(t['line_name'] for t in targets[:8])}…", flush=True)
    if NO_CAMERA:
        print(f"[loadtest] SKIPPED (no camera bound — provisioning gap, not perf): "
              f"{', '.join(NO_CAMERA)}", flush=True)

    token = mint_token("admin", "admin", 1)
    STATE.update({"started_at": time.time(), "users": a.users, "duration": a.duration})
    serve(a.port)
    print(f"[loadtest] LIVE VIEW → http://127.0.0.1:{a.port}/  (dashboard.html)", flush=True)

    stop_at = time.time() + a.duration
    threading.Thread(target=writer, args=(stop_at,), daemon=True).start()
    threads = []
    for i in range(1, a.users + 1):
        t = threading.Thread(target=agent, args=(i, token, targets, stop_at, a.think), daemon=True)
        t.start(); threads.append(t)
        time.sleep(0.15)          # ramp so all 20 don't hit in the same millisecond
    for t in threads:
        t.join()
    STATE["running"] = False
    time.sleep(1.5)

    with _lock:
        done = [s for s in STATE["samples"] if s.get("video_ms") is not None]
        vids = [s["video_ms"] for s in done if s.get("status") in (200, 206)]
    print("\n================ RESULT ================")
    print(f"users={a.users}  samples={len(done)}  ok={len(vids)}  failed={len(done)-len(vids)}")
    if vids:
        print(f"video  avg={int(statistics.mean(vids))}ms  p50={int(statistics.median(vids))}ms  "
              f"p95={int(sorted(vids)[int(len(vids)*.95)]) if len(vids)>5 else max(vids)}ms  max={max(vids)}ms")
    why = {}
    for s in done:
        why[s.get("why", "?")] = why.get(s.get("why", "?"), 0) + 1
    print("\nWHY (cause → count):")
    for k, v in sorted(why.items(), key=lambda kv: -kv[1]):
        print(f"   {v:5d}  {k}")
    print(f"\nstatus.json → {STATUS_PATH}")


if __name__ == "__main__":
    main()
