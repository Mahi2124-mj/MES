#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# live_agent.py — the live "virtual supervisor" that watches the MES and says WHY
#
# Operator ask: "ek automation bana jo har 30 min me system check kare aur gap
# nikale — ek live user type jo meri problem ki summary banaye.  Koi video nahi
# chali to KYUN nahi chali, kya reason hai, kya band hai, kya stuck hai.  Ek live
# agent jo mere system ke saath run ho aur stop ho."
#
# HOW IT DIFFERS FROM pm_agent.py
#   pm_agent audits the PLATFORM (every user, every route, hourly).
#   This one behaves like ONE REAL SUPERVISOR every 30 minutes: it opens each
#   line's supervisor page, taps a dot to play the video, and when something
#   does not work it DIAGNOSES THE CAUSE instead of just recording a failure.
#
# THE DIAGNOSIS CHAIN (this is the point of the agent)
#   video did not play →  walk the chain and name the first broken link:
#       1. is a camera even bound to this line?          → "camera configured nahi"
#       2. is the recorder process alive for it?          → "recorder band hai"
#       3. is its .ts file still growing?                 → "camera se stream nahi aa rahi"
#       4. is the cycle inside the recorded window?       → "us cycle ka footage nahi (window ke bahar)"
#       5. is the clip pre-rendered?                      → "clip abhi bana nahi — on-demand"
#       6. is the CMS up / wedged?                        → "CMS wedge — restart chahiye"
#   line showing nothing → when did its collector last write a cycle?
#   page slow           → which of the three supervisor APIs is slow.
#
# OUTPUT
#   • status.json + dashboard.html (live view, default :8097)
#   • a plain-language summary line per problem
#   • MES Inbox alert on CHANGE only (new problem / cleared), tag 'live_agent'
#
# RUNS WITH THE SYSTEM
#   Installed as a systemd --user service, so it starts and stops with the box.
#       python3 liveagent/live_agent.py --interval 30      (minutes)
# ─────────────────────────────────────────────────────────────────────────────
import argparse, glob, json, os, re, statistics, subprocess, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests

import selfheal
import invariants

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))

BASE      = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
CMS_BASE  = os.environ.get("CMS_BASE", "http://127.0.0.1:5555")
VIDEOS    = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/videos"
CLIPS     = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips"
STATUS    = os.path.join(HERE, "status.json")
STATE     = os.path.join(HERE, "alert_state.json")
SLOW_MS   = 2000


def db():
    import psycopg2
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1", port=5432)
    c.autocommit = True
    return c


def dc(c):
    import psycopg2.extras
    return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def mint(u, r, i):
    from auth import create_token          # type: ignore
    return create_token(u, r, i)


def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


# ── the diagnosis chain ──────────────────────────────────────────────────────
def why_no_video(line, cycle_seq, http_status, cms_ok, cms_threads):
    """Return (cause, fix) in plain language — the first broken link wins."""
    cam = (line.get("cam") or "").strip()
    if not cam:
        return ("Is line pe camera bind hi nahi hai",
                "Admin → Production → camera mapping me is line ka camera set karo")

    # recorder process alive for this camera?
    alive = sh(f"pgrep -fc {cam!r}") or "0"
    try:
        alive = int(alive)
    except Exception:
        alive = 0
    ts_files = sorted(glob.glob(os.path.join(VIDEOS, f"*{cam}*.ts")),
                      key=lambda p: os.path.getmtime(p), reverse=True)
    if alive == 0 and not ts_files:
        return ("Camera ka recorder chal hi nahi raha",
                "CMS restart karo: CMS_QUIET_SECONDS=90 python3 Phase2/restart_cms.py")

    # is the recording still growing?
    if ts_files:
        age = time.time() - os.path.getmtime(ts_files[0])
        if age > 120:
            return (f"Camera se stream band hai — recording {int(age)}s se nahi badhi",
                    "Camera reachable hai ya nahi dekho (RTSP), zarurat pade to power-cycle")
    else:
        return ("Camera ka koi recording file nahi mili",
                "Recorder start nahi hua — CMS restart karo")

    if not cms_ok:
        return ("CMS (video service) jawab nahi de raha",
                "CMS wedge — restart: CMS_QUIET_SECONDS=90 python3 Phase2/restart_cms.py")
    if cms_threads and cms_threads > 500:
        return (f"CMS wedge ho raha hai ({cms_threads} threads)",
                "Watchdog khud restart karega; warna manually restart_cms.py")

    if http_status == 404:
        return ("Us cycle ka footage recording window ke bahar hai",
                "Recorder restart hone ke baad purane cycles ka footage nahi rehta — "
                "naye cycles chalenge")
    if http_status in (502, 0):
        return ("Video service ne jawab nahi diya (timeout/502)",
                "CMS load dekho; clip on-demand ban raha tha aur time out ho gaya")
    return (f"Video nahi chali (HTTP {http_status})", "Log dekho: Phase2/logs/MES-API.log")


def clip_ready(line_id, rec_date, shift, seq):
    p = os.path.join(CLIPS, str(rec_date), f"line_{line_id}", shift or "A",
                     "main", f"cycle_{seq}.mp4")
    return os.path.exists(p)


# ── one round ────────────────────────────────────────────────────────────────
def round_once(s):
    t0 = time.time()
    tok = mint("admin", "admin", 1)
    h = {"Authorization": f"Bearer {tok}"}
    problems, lines_out = [], []

    # service health first — it explains everything downstream
    def ping(url, hdr=True):
        try:
            t = time.time()
            r = s.get(url, headers=h if hdr else {}, timeout=20)
            return r.status_code, int((time.time() - t) * 1000)
        except Exception:
            return 0, None
    api_st, api_ms = ping(f"{BASE}/api/lines/")
    cms_st, cms_ms = ping(f"{CMS_BASE}/api/cameras/health")
    cms_ok = cms_st in (200, 401)
    cms_pid = sh("ss -ltnp 2>/dev/null | grep ':5555 ' | grep -oP 'pid=\\K[0-9]+' | head -1")
    cms_threads = 0
    if cms_pid:
        try:
            cms_threads = len(os.listdir(f"/proc/{cms_pid}/task"))
        except Exception:
            pass
    if api_st != 200:
        problems.append({"sev": "critical", "area": "Service",
                         "what": "MES-API jawab nahi de raha",
                         "why": f"HTTP {api_st}", "fix": "python3 Phase2/restart_api.py"})
    if not cms_ok:
        problems.append({"sev": "critical", "area": "Service",
                         "what": "CMS (video) jawab nahi de raha",
                         "why": f"HTTP {cms_st}",
                         "fix": "CMS_QUIET_SECONDS=90 python3 Phase2/restart_cms.py"})
    if cms_threads > 500:
        problems.append({"sev": "critical", "area": "Service",
                         "what": f"CMS wedge ({cms_threads} threads)",
                         "why": "thread pile-up — video sab jagah ruk jayegi",
                         "fix": "watchdog restart karega; warna restart_cms.py"})

    # every line: is it producing, does its page open, does video play
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
        lines = [dict(r) for r in cur.fetchall()]

    for ln in lines:
        rec = {"line": ln["line_name"], "id": ln["id"], "producing": None,
               "page_ms": None, "video_ms": None, "video": "—"}
        tbl = ln["db_table_name"]
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tbl):
            continue
        # is the collector writing cycles?
        last = None
        try:
            with db() as c2:
                cur2 = dc(c2)
                cur2.execute(f"""SELECT cycle_seq, record_date, shift_name, ts
                                   FROM {tbl}_ct_log ORDER BY id DESC LIMIT 1""")
                last = cur2.fetchone()
        except Exception:
            rec["producing"] = "no ct_log table"
        if last:
            age = (time.time() - last["ts"].timestamp()) if last.get("ts") else 9e9
            rec["producing"] = f"{int(age)}s ago"
            if age > 900:
                problems.append({"sev": "warning", "area": "Production",
                                 "what": f"{ln['line_name']} se {int(age/60)} min se koi cycle nahi aaya",
                                 "why": "collector ya PLC se data nahi aa raha",
                                 "fix": "collector log dekho: logs/collector_*.log; PLC reachable hai?"})
        elif rec["producing"] is None:
            rec["producing"] = "no cycles"

        # supervisor page open
        pt = time.time()
        for p in (f"/api/lines/{ln['id']}/wallboard-summary",
                  f"/api/lines/{ln['id']}/wallboard-cycles"):
            try:
                s.get(BASE + p, headers=h, timeout=30)
            except Exception:
                pass
        rec["page_ms"] = int((time.time() - pt) * 1000)
        if rec["page_ms"] > SLOW_MS * 2:
            problems.append({"sev": "warning", "area": "Speed",
                             "what": f"{ln['line_name']} ka supervisor page slow ({rec['page_ms']}ms)",
                             "why": "wallboard-summary/cycles bhaari hain",
                             "fix": "in endpoints ki query optimise karni hogi"})

        # play a video like a supervisor tapping a dot
        if last:
            seq = last["cycle_seq"]
            url = (f"{BASE}/api/lines/{ln['id']}/cycle-video"
                   f"?cycle_seq={seq}&ng=0&r={seq}")
            st, ms, src = 0, None, ""
            try:
                vt = time.time()
                r = s.get(url, headers=h, stream=True, timeout=60)
                first = None
                for chunk in r.iter_content(64 * 1024):
                    if first is None:
                        first = int((time.time() - vt) * 1000)
                    break                       # first byte is "video chal gayi"
                r.close()
                st, ms = r.status_code, first or int((time.time() - vt) * 1000)
                src = r.headers.get("X-Clip-Source") or ""
            except Exception:
                st, ms = 0, None
            rec["video_ms"], rec["src"] = ms, src
            if st in (200, 206):
                rec["video"] = "OK" if (ms or 0) <= SLOW_MS else f"slow {ms}ms"
                if (ms or 0) > SLOW_MS:
                    ready = clip_ready(ln["id"], last["record_date"], last["shift_name"], seq)
                    problems.append({
                        "sev": "warning", "area": "Video",
                        "what": f"{ln['line_name']} ka video {ms}ms me khula (2s se upar)",
                        "why": ("clip pehle se bana hua tha, transfer slow" if ready
                                else "clip pehle se bana nahi tha — click pe render hua"),
                        "fix": ("archive coverage badhao (clip_archive lanes)" if not ready
                                else "server load dekho")})
            else:
                rec["video"] = f"FAIL {st}"
                why, fix = why_no_video(ln, seq, st, cms_ok, cms_threads)
                problems.append({"sev": "critical", "area": "Video",
                                 "what": f"{ln['line_name']} ki video nahi chali",
                                 "why": why, "fix": fix})
        lines_out.append(rec)

    # ── 2026-09-17 — the checks that a power cut proved were missing ────────
    # Everything above replays OLD cycles, which keep serving fine from the clip
    # archive long after live capture has died.  These look at the machine
    # itself: processes, backlog, HTTP errors, and whether anything is actually
    # being RECORDED right now.
    heal_log = []
    n_checks, failed_checks = 0, []
    try:
        rec_problems, network_down = selfheal.check_recording_and_network()
        problems.extend(rec_problems)
        # The Network panel's device inventory — switches, not just cameras.
        # A dark switch is a bigger, more specific fault than "a camera timed
        # out", and it also tells the heals to stand down.
        net_problems, switches_down = selfheal.check_network_devices(BASE, tok)
        problems.extend(net_problems)
        if switches_down:
            network_down = True
        problems.extend(selfheal.check_pids())
        problems.extend(selfheal.check_backlog())
        problems.extend(selfheal.check_http(BASE, tok, lines))
        # Walk Historical → Video Archive end to end, like a person browsing it.
        problems.extend(selfheal.check_video_archive(BASE, tok))
        # Tree healthy is not the same as evidence present — measure how much
        # of yesterday's production actually has video behind it.
        problems.extend(selfheal.check_archive_coverage(BASE, tok))
        # Rules that know nothing about any particular bug — see invariants.py.
        inv, n_checks, failed_checks = invariants.run_all(BASE, tok, ROOT)
        problems.extend(inv)
        heal_log = selfheal.run_heals(problems, network_down,
                                      enabled=os.environ.get("AGENT_AUTOHEAL", "1") != "0")
    except Exception as exc:
        problems.append({"sev": "warning", "area": "Agent",
                         "what": "Self-check poora nahi chala",
                         "why": str(exc)[:160],
                         "fix": "liveagent/selfheal.py dekho"})

    vids = [l["video_ms"] for l in lines_out if l.get("video_ms")]
    snap = {
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "took_s": round(time.time() - t0, 1),
        "service": {"api_ms": api_ms, "api": api_st, "cms_ms": cms_ms,
                    "cms": cms_st, "cms_threads": cms_threads},
        "counts": {"critical": sum(1 for p in problems if p["sev"] == "critical"),
                   "warning": sum(1 for p in problems if p["sev"] == "warning"),
                   "lines": len(lines_out),
                   "video_ok": sum(1 for l in lines_out if l["video"] == "OK"),
                   "video_fail": sum(1 for l in lines_out if l["video"].startswith("FAIL"))},
        "video_p50": int(statistics.median(vids)) if vids else 0,
        "problems": sorted(problems, key=lambda p: 0 if p["sev"] == "critical" else 1),
        "lines": lines_out,
        "healed": heal_log,
        "checks": {"ran": n_checks, "failed": failed_checks},
    }
    json.dump(snap, open(STATUS + ".tmp", "w"), indent=1)
    os.replace(STATUS + ".tmp", STATUS)
    alert(snap)
    return snap


def alert(snap):
    """Inbox alerts that keep reminding until the problem is actually gone.

    2026-09-17 — this used to fire once, when a problem first appeared, and
    then go quiet.  Operator: "ye reminder + fix mujhe har time chahiye".  A
    one-shot alert is a notification, not a reminder: the 4%-coverage problem
    had been true for days and nobody was being told any more.

    So each open problem now carries its age and is re-sent on a backoff —
    first sighting, then after an hour, then daily — with "X din se khula hai"
    in the text.  A problem that clears sends one closing note and is dropped.
    The backoff is what keeps a standing fault visible without turning into
    spam every 30 minutes.
    """
    key = lambda p: f"{p['area']}|{p['what']}"
    now = {key(p): p for p in snap["problems"]}
    try:
        prev = json.load(open(STATE)) if os.path.exists(STATE) else {}
    except Exception:
        prev = {}

    t = time.time()
    state, new, remind = {}, [], []
    for k, p in now.items():
        old = prev.get(k) or {}
        first = old.get("first_seen", t)
        last  = old.get("last_alert", 0)
        age_h = (t - first) / 3600
        # first sighting -> now; then 1 h; then once a day
        due = 3600 if age_h < 24 else 86400
        if not old:
            new.append((p, 0))
            last = t
        elif t - last >= due:
            remind.append((p, age_h))
            last = t
        state[k] = {"sev": p["sev"], "first_seen": first, "last_alert": last}

    gone = [k for k in prev if k not in now]

    if new or remind or gone:
        try:
            from routers.push import send_to_user      # type: ignore
            with db() as c:
                cur = dc(c)
                cur.execute("SELECT id FROM mes_admin WHERE role IN ('admin','plant_head','production_incharge','section_incharge','shift_incharge')")
                ids = [r["id"] for r in cur.fetchall()]

            def line(p, age_h):
                aged = ""
                if age_h >= 24:
                    aged = f" [{int(age_h // 24)} din se khula]"
                elif age_h >= 1:
                    aged = f" [{int(age_h)} ghante se khula]"
                return f"{p['what']} → {p['why']}{aged}\n   ✔ {p['fix']}"

            if new:
                body = "\n".join(line(p, a) for p, a in new[:4])
                if len(new) > 4:
                    body += f"\n… +{len(new) - 4} aur"
                for uid in ids:
                    send_to_user(uid, f"Live Agent — {len(new)} nayi problem", body,
                                 url="/", tag="live_agent")
            if remind:
                crit = [x for x in remind if x[0]["sev"] == "critical"]
                pick = (crit or remind)[:4]
                body = "\n".join(line(p, a) for p, a in pick)
                for uid in ids:
                    send_to_user(uid,
                                 f"Live Agent — {len(remind)} problem abhi tak theek nahi hui",
                                 body, url="/", tag="live_agent")
            if gone:
                for uid in ids:
                    send_to_user(uid, f"Live Agent — {len(gone)} problem theek ho gayi",
                                 "Pehle report ki gayi dikkat ab nahi hai.",
                                 url="/", tag="live_agent")
        except Exception as e:
            print(f"[live-agent] alert failed: {e}", flush=True)

    json.dump(state, open(STATE, "w"))


class _AgentHandler(SimpleHTTPRequestHandler):
    """Serve the dashboard at "/" instead of a directory listing.

    The bare port is the address people are given, and it was answering with
    Python's file index — the dashboard is one click away but nobody should
    have to know the filename.
    """

    def do_GET(self):                                  # noqa: N802
        if self.path in ("/", ""):
            self.path = "/dashboard.html"
        return super().do_GET()

    def log_message(self, *args):
        pass                                           # keep the journal clean


def serve(port):
    handler = partial(_AgentHandler, directory=HERE)
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever,
                     daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="minutes between rounds")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    serve(a.port)
    print(f"[live-agent] LIVE → http://127.0.0.1:{a.port}/  (every {a.interval} min)", flush=True)
    s = requests.Session()
    while True:
        try:
            snap = round_once(s)
            c = snap["counts"]
            print(f"[live-agent] {snap['ran_at']} — {c['critical']} critical, "
                  f"{c['warning']} warning · video ok {c['video_ok']}/{c['lines']} "
                  f"· p50 {snap['video_p50']}ms ({snap['took_s']}s)", flush=True)
            for p in snap["problems"][:8]:
                print(f"    [{p['sev'][:4].upper()}] {p['what']} → {p['why']}", flush=True)
        except Exception as e:
            print(f"[live-agent] round failed: {type(e).__name__}: {e}", flush=True)
        if a.once:
            return
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
