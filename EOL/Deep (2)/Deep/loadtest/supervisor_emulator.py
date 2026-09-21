#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# supervisor_emulator.py — replays the real SUPERVISOR page, line by line
#
# Operator ask: "sab line ke is supervisor wale page pe jaake video chala chala
# ke dekh — same user pe 20 min, fir alag alag user pe.  Uski summary bana.
# Sab 2 sec ke under chahiye, isse upar nahi.  Emulator live dikhna chahiye."
#
# WHAT EACH VIRTUAL SUPERVISOR DOES (exactly what WallboardLeft.jsx does)
#     open a line's /{id}/SUPERVISOR page:
#         GET /api/lines/{id}/wallboard-summary      header + KPI tiles
#         GET /api/lines/{id}/wallboard-cycles       the cycle dots
#         GET /api/lines/{id}/over-target-history    the over-target panel
#     then TAP DOTS to watch video:
#         GET /api/lines/{id}/cycle-video            main (Final Inspection)
#         GET /api/submachines/{sid}/cycle-video     a sub-machine dot
#     …then move to the NEXT line and do it again, round-robin over every line.
#
# TWO PHASES
#   1. SAME user   — every virtual supervisor signed in as one account.
#   2. DIFFERENT   — each virtual supervisor on a different real MES account,
#                    so per-account scoping/permissions are exercised too.
#
# THE TARGET
#   "video 2 second ke under".  A browser starts playing at the FIRST BYTES, so
#   `open_ms` (time to first byte of the clip) is the number that matches what a
#   supervisor actually feels; the full download is reported alongside it.
#
#   Run:  python3 loadtest/supervisor_emulator.py --users 12 --phase1-min 20 --phase2-min 10
#   Live: http://<host>:8099/supervisor.html
# ─────────────────────────────────────────────────────────────────────────────
import argparse, json, os, random, statistics, sys, threading, time
from collections import defaultdict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))

BASE = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
STATUS = os.path.join(HERE, "sup_status.json")
TARGET_MS = 2000                     # the operator's bar: video opens under 2 s
CLIPS = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips"

_lock = threading.Lock()
S = {"phase": "-", "users": 0, "started": 0, "running": True,
     "agents": {}, "samples": [], "skipped": []}


def mint(username, role, uid):
    from auth import create_token           # type: ignore
    return create_token(username, role, uid)


def db():
    import psycopg2
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1", port=5432)
    c.autocommit = True
    return c


def dc(c):
    import psycopg2.extras
    return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def load_lines():
    """Every line whose supervisor page has real video behind it."""
    import re, glob
    out, skipped = [], []
    with db() as c:
        cur = dc(c)
        cur.execute("""SELECT l.id, l.line_name, l.db_table_name,
                              NULLIF(TRIM(pc.nf2_camera_id),'') AS cam
                         FROM mes_lines l
                    LEFT JOIN mes_plc_configs pc ON pc.line_id = l.id
                          AND ((l.dashboard_plc_id IS NOT NULL AND pc.id = l.dashboard_plc_id)
                            OR (l.dashboard_plc_id IS NULL AND pc.parent_plc_id IS NULL))
                        WHERE COALESCE(l.is_active,TRUE)
                          AND l.db_table_name IS NOT NULL AND l.db_table_name <> ''
                        ORDER BY l.line_name""")
        rows = cur.fetchall()
    for r in rows:
        if not r["cam"]:
            skipped.append(f"{r['line_name']} (no camera)")
            continue
        # 2026-09-17 — count clips from the last 30 min first, but fall back to
        # ANY archived clip for this line.  Run between shifts the fresh count
        # is zero for every line and the whole run aborted with "no lines with
        # video", even though a supervisor reviewing after the shift is exactly
        # the person who opens yesterday's cycles.
        clips = glob.glob(os.path.join(CLIPS, "*", f"line_{r['id']}", "*", "main", "*.mp4"))
        fresh = sum(1 for f in clips if time.time() - os.path.getmtime(f) < 1800)
        if fresh < 3 and len(clips) < 3:
            skipped.append(f"{r['line_name']} (no footage)")
            continue
        out.append({"id": r["id"], "name": r["line_name"], "tbl": r["db_table_name"]})
    return out, skipped


def recent_cycles(tbl, n=12):
    import re
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tbl):
        return []
    try:
        with db() as c:
            cur = dc(c)
            # Live window first — what a supervisor watching the running line taps.
            cur.execute(f"""SELECT cycle_seq FROM {tbl}_ct_log
                             WHERE ts > now() - interval '8 min'
                               AND ts < now() - interval '40 sec'
                             ORDER BY id DESC LIMIT %s""", (n,))
            rows = [int(r["cycle_seq"]) for r in cur.fetchall()]
            if rows:
                return rows
            # Nothing running (between shifts) — fall back to the newest cycles
            # this line has, which is what post-shift review actually opens.
            cur.execute(f"""SELECT cycle_seq FROM {tbl}_ct_log
                             ORDER BY id DESC LIMIT %s""", (n,))
            return [int(r["cycle_seq"]) for r in cur.fetchall()]
    except Exception:
        return []


def accounts(limit):
    """Real MES accounts for phase 2 — supervisors/operators first."""
    with db() as c:
        cur = dc(c)
        cur.execute("""SELECT id, username, role FROM mes_admin
                        ORDER BY CASE role WHEN 'shift_incharge' THEN 0
                                           WHEN 'production_incharge' THEN 1
                                           WHEN 'section_incharge' THEN 2
                                           WHEN 'operator' THEN 3
                                           WHEN 'production' THEN 4
                                           ELSE 5 END, id""")
        return cur.fetchall()[:limit]


# ── every OTHER page a supervisor opens, not just the wallboard ──────────────
# 2026-09-17 — operator: "har page and video check kar, time nikal".  The run
# used to measure the supervisor page alone, so a slow Historical or Comments
# History never showed up.  Each entry is (label, path-template); {L} is a line
# id and {D} today's date.  Anything needing a POST is left out on purpose —
# this is a read-only measurement, it must not write to production.
PAGE_CATALOGUE = [
    ("Dashboard",         "/api/lines/{L}/realtime"),
    ("Realtime (wall)",   "/api/lines/{L}/realtime"),
    ("Line list",         "/api/lines/"),
    ("Zones",             "/api/zones/"),
    ("Inbox",             "/api/push/inbox"),
    ("Comments History",  "/api/lines/0/comments-history?date_from={D}&date_to={D}"),
    ("Comments summary",  "/api/lines/0/comments-summary?date_from={D}&date_to={D}"),
    ("Pareto",            "/api/lines/0/comments-pareto?date_from={D}&date_to={D}"),
    ("Hourly loss",       "/api/lines/{L}/hourly-loss-breakdown?date={D}&shift=A"),
    ("Video Archive days","/api/clip-archive/days"),
    ("Video Archive lines","/api/clip-archive/lines?date={D}"),
    ("Over-target",       "/api/lines/{L}/over-target-history"),
    ("CT histogram",      "/api/lines/{L}/ct-histogram"),
    ("Model counts",      "/api/lines/{L}/model-counts"),
    ("Sub-machines",      "/api/lines/{L}/submachines"),
    ("NG list",           "/api/lines/{L}/ng-list?date={D}&slot_label=08:30-09:30"),
    ("Shift Compile",     "/api/shift-compile/overview"),
    ("Manpower",          "/api/manpower/alerts"),
    ("Poka-Yoke live",    "/api/poka-yoke/live/{L}"),
    ("5S",                "/api/5s/items"),
    ("Quality deviations","/api/quality/deviations"),
    ("Quality KPI",       "/api/quality/kpi"),
    ("Breakdowns",        "/api/breakdowns/active"),
    ("Andon history",     "/api/andon/history"),
    ("Maintenance KPI",   "/api/maintenance-kpi/"),
    ("PM dashboard",      "/api/pm/dashboard"),
    ("CAPA",              "/api/capa/"),
    ("Operators",         "/api/operators/shift-summary?line_id={L}&date={D}&shift=A"),
    ("Leaders",           "/api/leaders/"),
    ("Breakdown history", "/api/breakdowns/history"),
    ("CAPA pending",      "/api/capa/pending"),
    ("Shift Compile hist","/api/shift-compile/historical"),
]


def page_table():
    """Per-page timing table — slowest first, so the worst page is on top.

    The caller (writer()) already holds _lock while it builds the snapshot, and
    threading.Lock is NOT reentrant — taking it again here deadlocked the
    publisher thread.  The run kept going but sup_status.json stopped updating,
    so the live view silently froze on the previous run numbers.
    """
    rows = list(S.get("pages") or [])
    by = {}
    for r in rows:
        b = by.setdefault(r["page"], {"page": r["page"], "n": 0, "ms": [],
                                      "bad": 0, "why": r["why"]})
        b["n"] += 1
        b["ms"].append(r["ms"])
        if r["status"] != 200:
            b["bad"] += 1
            b["why"] = r["why"]
    out = []
    for b in by.values():
        v = sorted(b["ms"])
        out.append({"page": b["page"], "n": b["n"],
                    "p50": v[len(v) // 2],
                    "p95": v[max(0, int(len(v) * 0.95) - 1)],
                    "max": v[-1],
                    "bad": b["bad"],
                    "why": b["why"] if b["bad"] else "OK",
                    "under2s": round(sum(1 for x in v if x <= TARGET_MS) * 100 / len(v))})
    return sorted(out, key=lambda x: -x["p50"])


def page_visit(s, tok, label, path, line_id):
    """Open one page's API and time it.  Never raises."""
    url = BASE + path.replace("{L}", str(line_id)).replace(
        "{D}", time.strftime("%Y-%m-%d"))
    t0 = time.time()
    try:
        r = s.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        code = r.status_code
        # drain so the timing includes the whole body, like a browser
        _ = len(r.content)
    except Exception as e:
        return {"page": label, "ms": int((time.time() - t0) * 1000),
                "status": 0, "why": type(e).__name__}
    ms = int((time.time() - t0) * 1000)
    why = "OK" if code == 200 else (
        "not provisioned / no data" if code == 404 else
        "needs a query param" if code == 422 else
        "server error" if code >= 500 else f"HTTP {code}")
    return {"page": label, "ms": ms, "status": code, "why": why}


def supervisor_visit(s, tok, line, rec):
    """One full supervisor-page visit: open the page, then watch video."""
    h = {"Authorization": f"Bearer {tok}"}
    out = {"line": line["name"], "line_id": line["id"], "t": time.time()}
    # ---- open the page (the three calls WallboardLeft fires on mount)
    t0 = time.time()
    for path in (f"/api/lines/{line['id']}/wallboard-summary",
                 f"/api/lines/{line['id']}/wallboard-cycles",
                 f"/api/lines/{line['id']}/over-target-history"):
        try:
            s.get(BASE + path, headers=h, timeout=30)
        except Exception:
            pass
    out["page_ms"] = int((time.time() - t0) * 1000)

    # ---- tap a dot → play the clip
    seqs = rec.get(line["id"]) or []
    if not seqs:
        out["status"] = 0
        out["why"] = "no recent cycle to tap"
        return out
    seq = random.choice(seqs)
    out["cycle"] = seq
    url = f"{BASE}/api/lines/{line['id']}/cycle-video?cycle_seq={seq}&ng=0&r={seq}"
    try:
        t0 = time.time(); first = None; nb = 0
        r = s.get(url, headers=h, stream=True, timeout=90)
        for chunk in r.iter_content(64 * 1024):
            if first is None:
                first = int((time.time() - t0) * 1000)
            nb += len(chunk)
        total = int((time.time() - t0) * 1000)
        out.update({"status": r.status_code,
                    "open_ms": first if first is not None else total,
                    "full_ms": total, "bytes": nb,
                    "src": r.headers.get("X-Clip-Source") or r.headers.get("X-Video-Source") or ""})
        if r.status_code not in (200, 206):
            out["why"] = f"HTTP {r.status_code}"
        elif out["open_ms"] <= TARGET_MS:
            out["why"] = f"OK — opened in {out['open_ms']}ms"
        else:
            out["why"] = f"OVER TARGET — {out['open_ms']}ms (>{TARGET_MS}ms)"
    except Exception as e:
        out["status"] = 0
        out["why"] = f"{type(e).__name__}"
    return out


def agent(uid, label, tok, lines, rec, stop_at, think):
    s = requests.Session()
    row = {"user": uid, "label": label, "state": "start", "visits": 0, "last": {}}
    with _lock:
        S["agents"][uid] = row
    i = uid                                  # stagger the starting line per user
    while time.time() < stop_at and S["running"]:
        line = lines[i % len(lines)]
        # Round-robin through the page catalogue so 30 users cover every page
        # many times over, while still doing the supervisor+video flow below.
        plabel, ppath = PAGE_CATALOGUE[i % len(PAGE_CATALOGUE)]
        pr = page_visit(s, tok, plabel, ppath, line["id"])
        with _lock:
            S.setdefault("pages", []).append(pr)
        i += 1
        row["state"] = f"{line['name']}"
        v = supervisor_visit(s, tok, line, rec)
        v["user"] = label
        v["phase"] = S["phase"]
        row["visits"] += 1
        row["last"] = v
        with _lock:
            S["samples"].append(v)
            if len(S["samples"]) > 20000:
                del S["samples"][:5000]
        row["state"] = "think"
        time.sleep(think * random.uniform(0.7, 1.3))
    row["state"] = "done"


def summarise(rows):
    vid = [r for r in rows if r.get("open_ms") is not None and r.get("status") in (200, 206)]
    o = [r["open_ms"] for r in vid]
    f = [r["full_ms"] for r in vid]
    p = [r["page_ms"] for r in rows if r.get("page_ms") is not None]
    under = sum(1 for x in o if x <= TARGET_MS)
    return {
        "visits": len(rows), "videos": len(vid),
        "failed": sum(1 for r in rows if r.get("status") not in (200, 206)),
        "open_avg": int(statistics.mean(o)) if o else 0,
        "open_p50": int(statistics.median(o)) if o else 0,
        "open_p95": int(sorted(o)[int(len(o) * .95)]) if len(o) > 5 else (max(o) if o else 0),
        "open_max": max(o) if o else 0,
        "under_2s": under, "under_pct": int(under * 100 / len(o)) if o else 0,
        "full_avg": int(statistics.mean(f)) if f else 0,
        "page_avg": int(statistics.mean(p)) if p else 0,
    }


def writer(total_end):
    while S["running"]:
        with _lock:
            rows = list(S["samples"])
            phases = {}
            for ph in ("1 — same user", "2 — different users"):
                sub = [r for r in rows if r.get("phase") == ph]
                if sub:
                    phases[ph] = summarise(sub)
            per_line = {}
            for r in rows:
                if r.get("open_ms") is None:
                    continue
                per_line.setdefault(r["line"], []).append(r["open_ms"])
            lines_tbl = sorted(
                [{"line": k,
                  "n": len(v),
                  "p50": int(statistics.median(v)),
                  "max": max(v),
                  "under_pct": int(sum(1 for x in v if x <= TARGET_MS) * 100 / len(v))}
                 for k, v in per_line.items()],
                key=lambda x: x["under_pct"])
            why = defaultdict(int)
            for r in rows:
                if r.get("why"):
                    key = r["why"].split("—")[0].strip() if "—" in r["why"] else r["why"]
                    why[key] += 1
            snap = {
                "phase": S["phase"], "users": S["users"],
                "elapsed": int(time.time() - S["started"]),
                "remaining": max(0, int(total_end - time.time())),
                "running": S["running"], "target_ms": TARGET_MS,
                "overall": summarise(rows) if rows else {},
                "phases": phases, "lines": lines_tbl,
                "why": sorted(why.items(), key=lambda kv: -kv[1]),
                "agents": sorted(S["agents"].values(), key=lambda a: a["user"]),
                "recent": [r for r in rows[-24:]][::-1],
                "skipped": S["skipped"],
                "pages": page_table(),
            }
        tmp = STATUS + ".tmp"
        json.dump(snap, open(tmp, "w"))
        os.replace(tmp, STATUS)
        time.sleep(1)


def run_phase(name, users, minutes, lines, think, pick_token):
    S["phase"] = name
    S["agents"] = {}
    rec = {l["id"]: recent_cycles(l["tbl"]) for l in lines}
    stop_at = time.time() + minutes * 60
    ts = []
    print(f"\n[phase] {name} — {users} users, {minutes} min, {len(lines)} lines", flush=True)
    for i in range(users):
        label, tok = pick_token(i)
        t = threading.Thread(target=agent, args=(i, label, tok, lines, rec, stop_at, think),
                             daemon=True)
        t.start(); ts.append(t)
        time.sleep(0.2)
    # refresh the cycle list every 2 min so taps stay on fresh dots
    while time.time() < stop_at and S["running"]:
        time.sleep(min(120, max(1, stop_at - time.time())))
        for l in lines:
            c = recent_cycles(l["tbl"])
            if c:
                rec[l["id"]] = c
        try:
            if os.getloadavg()[0] > (os.cpu_count() or 64) + 4:
                print("[phase] load too high — ending this phase early", flush=True)
                break
        except Exception:
            pass
    for t in ts:
        t.join(timeout=90)


def serve(port):
    handler = partial(SimpleHTTPRequestHandler, directory=HERE)
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever,
                     daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=12)
    ap.add_argument("--phase1-min", type=int, default=20)
    ap.add_argument("--phase2-min", type=int, default=10)
    ap.add_argument("--think", type=float, default=6.0)
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()

    lines, skipped = load_lines()
    S["skipped"] = skipped
    if not lines:
        print("no lines with video — aborting"); return
    print(f"[sup-emulator] {len(lines)} supervisor pages: "
          f"{', '.join(l['name'] for l in lines)}", flush=True)
    if skipped:
        print(f"[sup-emulator] skipped (no camera/footage): {', '.join(skipped)}", flush=True)

    S["users"] = a.users
    S["started"] = time.time()
    serve(a.port)
    total_end = time.time() + (a.phase1_min + a.phase2_min) * 60 + 30
    threading.Thread(target=writer, args=(total_end,), daemon=True).start()
    print(f"[sup-emulator] LIVE → http://127.0.0.1:{a.port}/supervisor.html", flush=True)

    # ── phase 1 — everyone on ONE account
    one = mint("admin", "admin", 1)
    run_phase("1 — same user", a.users, a.phase1_min, lines, a.think,
              lambda i: ("admin", one))

    # ── phase 2 — each virtual supervisor on a DIFFERENT real account
    accs = accounts(a.users)
    toks = [(x["username"], mint(x["username"], x["role"], x["id"])) for x in accs]
    if toks:
        run_phase("2 — different users", min(a.users, len(toks)), a.phase2_min, lines,
                  a.think, lambda i: toks[i % len(toks)])

    S["running"] = False
    time.sleep(1.5)

    with _lock:
        rows = list(S["samples"])
    print("\n══════════════ SUMMARY ══════════════")
    for ph in ("1 — same user", "2 — different users"):
        sub = [r for r in rows if r.get("phase") == ph]
        if not sub:
            continue
        m = summarise(sub)
        print(f"\nPHASE {ph}")
        print(f"  visits={m['visits']}  videos={m['videos']}  failed={m['failed']}")
        print(f"  video OPEN (first byte): avg={m['open_avg']}ms p50={m['open_p50']}ms "
              f"p95={m['open_p95']}ms max={m['open_max']}ms")
        print(f"  UNDER {TARGET_MS}ms: {m['under_2s']}/{m['videos']} = {m['under_pct']}%")
        print(f"  page open avg={m['page_avg']}ms   full download avg={m['full_avg']}ms")
    m = summarise(rows)
    print(f"\nOVERALL  videos={m['videos']} failed={m['failed']}  "
          f"open p50={m['open_p50']}ms p95={m['open_p95']}ms  "
          f"under-2s={m['under_pct']}%")
    print("\nPER LINE (worst first):")
    per = defaultdict(list)
    for r in rows:
        if r.get("open_ms") is not None:
            per[r["line"]].append(r["open_ms"])
    for k, v in sorted(per.items(), key=lambda kv: sum(1 for x in kv[1] if x <= TARGET_MS) / len(kv[1])):
        u = int(sum(1 for x in v if x <= TARGET_MS) * 100 / len(v))
        print(f"   {k:20s} n={len(v):4d}  p50={int(statistics.median(v)):5d}ms  "
              f"max={max(v):6d}ms  under-2s={u:3d}%")
    if S["skipped"]:
        print(f"\nSKIPPED (no camera/footage): {', '.join(S['skipped'])}")


if __name__ == "__main__":
    main()
