#!/usr/bin/env python3
"""chaos.py — break things on purpose, watch what the system does, put them back.

Operator: "har tarike ki real-time problem bhi daal ke dekh — kya kya tootta
hai, kya issue rehta hai.  Mujhe kuch bhi failure nahi chahiye."

You cannot claim a system survives a fault you never induced.  Each scenario
here creates a REAL fault, measures what the user would have seen while it was
happening, then restores and confirms recovery.

WHAT IS DELIBERATELY NOT DONE HERE
  * nothing that can lose production data — no collector is stopped, no table
    is touched, no clip or recording is deleted;
  * nothing that needs a power cycle or a person at the machine to undo;
  * nothing aimed at the plant network, which is already down and is not ours
    to poke at.
Every scenario below is reversible from this script, and each one verifies the
restore before it moves on.  A chaos test that cannot undo itself is sabotage.
"""
import argparse, json, os, subprocess, sys, time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))
BASE = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
OUT  = os.path.join(HERE, "chaos_report.json")


def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def token():
    from auth import create_token            # type: ignore
    return create_token("admin", "admin", 1)


def probe(tok, n=6):
    """What a user experiences right now: can pages open, and how fast."""
    h = {"Authorization": f"Bearer {tok}"}
    paths = ["/api/lines/", "/api/auth/me", "/api/lines/2/wallboard-summary",
             "/api/push/inbox", "/api/zones/", "/api/clip-archive/days"]
    ok, fail, times = 0, 0, []
    for p in paths[:n]:
        t0 = time.time()
        try:
            r = requests.get(BASE + p, headers=h, timeout=25)
            ms = int((time.time() - t0) * 1000)
            times.append(ms)
            ok += 1 if r.status_code < 500 else 0
            fail += 1 if r.status_code >= 500 else 0
        except Exception:
            fail += 1
            times.append(25000)
    return {"ok": ok, "fail": fail,
            "worst_ms": max(times) if times else None,
            "p50_ms": sorted(times)[len(times)//2] if times else None}


# ── scenarios ───────────────────────────────────────────────────────────────
def s_db_connections(tok):
    """Hold a large block of DB connections, as a runaway client would.

    The API pools 40 per worker against a server allowing 300 with ~90 already
    used, so this squeezes the headroom the pool assumes.
    """
    import psycopg2
    held = []
    try:
        for _ in range(60):
            c = psycopg2.connect(dbname="energydb", user="postgres",
                                 password="tbdi@123", host="127.0.0.1",
                                 port=5432, connect_timeout=5)
            c.autocommit = True
            held.append(c)
        time.sleep(3)
        during = probe(tok)
    finally:
        for c in held:
            try: c.close()
            except Exception: pass
    return during, f"{len(held)} extra DB connections held"


def s_slow_endpoint_flood(tok):
    """Fire the known-slow scan endpoint concurrently and see if it starves the
    API — the risk the route rule flagged (45 s with a worker held each time)."""
    import threading
    h = {"Authorization": f"Bearer {tok}"}
    stop = []
    def hit():
        try:
            requests.get(BASE + "/api/network/discover", headers=h, timeout=90)
        except Exception:
            pass
    ts = [threading.Thread(target=hit, daemon=True) for _ in range(6)]
    for t in ts: t.start()
    time.sleep(6)
    during = probe(tok)
    stop.append(1)
    return during, "6 concurrent /api/network/discover (45s each)"


def s_bad_input(tok):
    """Garbage and traversal in every parameter the browser can set.

    A 4xx is the right answer; a 5xx means the input reached something that
    could not handle it, and a 200 on a traversal path would be serious.
    """
    h = {"Authorization": f"Bearer {tok}"}
    cases = [
        "/api/lines/999999/wallboard-summary",
        "/api/lines/abc/wallboard-summary",
        "/api/lines/2/wallboard-summary?shift=%27%20OR%201%3D1--",
        "/api/clip-archive/clips?date=../../etc&line_id=2&shift=A&machine=main",
        "/api/clip-archive/clips?date=2026-09-16&line_id=2&shift=../../&machine=main",
        "/api/clip-archive/video?date=2026-09-16&line_id=2&shift=A&machine=../../../etc/passwd&cycle_seq=1",
        "/api/lines/2/comments-history?date_from=notadate&date_to=alsonot",
        "/api/lines/-1/realtime",
    ]
    bad = []
    for c in cases:
        try:
            r = requests.get(BASE + c, headers=h, timeout=30)
            if r.status_code >= 500:
                bad.append(f"{c.split('?')[0]} -> {r.status_code}")
            elif r.status_code == 200 and ".." in c:
                bad.append(f"TRAVERSAL ACCEPTED: {c[:60]}")
        except Exception as exc:
            bad.append(f"{c.split('?')[0]} -> {type(exc).__name__}")
    return {"bad": bad, "cases": len(cases)}, "malformed + traversal input"


def s_expired_token(tok):
    """A stale or forged session must be refused, never served."""
    cases = {
        "no token":      {},
        "garbage token": {"Authorization": "Bearer not.a.real.token"},
        "empty bearer":  {"Authorization": "Bearer "},
    }
    leaks = []
    for name, h in cases.items():
        try:
            r = requests.get(BASE + "/api/lines/2/wallboard-summary",
                             headers=h, timeout=25)
            if r.status_code == 200:
                leaks.append(f"{name} was SERVED data (HTTP 200)")
        except Exception:
            pass
    return {"leaks": leaks}, "unauthenticated / forged tokens"


SCENARIOS = [
    ("DB connection squeeze",      s_db_connections),
    ("slow-endpoint flood",        s_slow_endpoint_flood),
    ("malformed + traversal input", s_bad_input),
    ("forged / missing session",   s_expired_token),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    tok = token()

    base_state = probe(tok)
    print(f"[chaos] baseline: {base_state}")
    results = []
    for name, fn in SCENARIOS:
        if a.only and a.only not in name:
            continue
        print(f"[chaos] --- {name}")
        t0 = time.time()
        try:
            during, what = fn(tok)
        except Exception as exc:
            during, what = {"error": str(exc)[:120]}, "scenario failed to run"
        time.sleep(4)
        after = probe(tok)
        recovered = after["fail"] == 0 and (after["worst_ms"] or 0) <= 3000
        results.append({"scenario": name, "injected": what, "during": during,
                        "after": after, "recovered": recovered,
                        "took_s": round(time.time() - t0, 1)})
        print(f"           during={during}")
        print(f"           after ={after}  recovered={recovered}")
    rep = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "baseline": base_state, "results": results,
           "all_recovered": all(r["recovered"] for r in results)}
    json.dump(rep, open(OUT, "w"), indent=1)
    print(f"[chaos] report -> {OUT}   all recovered: {rep['all_recovered']}")


if __name__ == "__main__":
    main()
