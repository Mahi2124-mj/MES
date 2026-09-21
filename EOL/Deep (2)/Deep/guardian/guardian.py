#!/usr/bin/env python3
"""guardian.py — keeps the collectors and the camera pipeline alive by itself.

Operator: "bot ko laga de — agar beech me koi collector ya CMS broke ya
overflow ho to vo khud thik kare, auto heal kare.  No data loss in any cost
until stopped by hard coded.  stop_everything se sab band ho, usse band nahi
to vo apna kaam karta rahe.  Har ghante notification bheje — kya chal raha
hai, kya kya dekha, kya fix karne ki zyada need hai."

Reports go to the MES Inbox only (the app turns that into a phone
notification).  An SMS channel was tried and removed at the operator's
request — Inbox is enough and costs nothing.

HOW IT STOPS
    `Phase2/collectors/STOP.flag` is the hard stop.  stop_everything.sh drops
    that file FIRST, before it kills anything, precisely so the never-die
    loops do not fight it — this guardian obeys the same flag and goes idle.
    Nothing else silences it: not a restart, not a crash, not a reboot (it is
    a systemd --user service with linger on).

THE RULE THAT SHAPES EVERY HEAL: never cost a cycle.
    A collector that is WRITING is never touched, however unhappy it looks.
    A heal only runs when the thing is already not working — a dead heartbeat,
    a wedged CMS, recorders that are not recording.  When the fault is upstream
    (the plant network down, the PLCs unreachable) NOTHING is restarted,
    because a restart cannot fix a cable and each one risks the recovery.

WHAT IT CAN REPAIR
    collector dead      heartbeat older than STALE_S while the line is marked
                        running -> restart that ONE line through the same
                        endpoint the admin button uses
    CMS wedged          thread pile-up past CMS_THREAD_LIMIT, or /health not
                        answering -> CMS restart with the 90 s quiet window
    recorders orphaned  ffmpeg holding a camera whose parent CMS is gone ->
                        reap, so the live recorder can take the RTSP session
    nothing recording   cameras reachable but no .ts growing -> CMS restart
    log overflow        any log past LOG_LIMIT_MB -> rotate now

    Every repair has a cooldown, and a cap per hour, so a thing that keeps
    breaking gets reported instead of restarted forever.
"""
import argparse, json, os, re, subprocess, sys, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))

STOP_FLAG   = os.path.join(ROOT, "Phase2", "collectors", "STOP.flag")

# 2026-09-19 — HARD-CODED: the guardian restarts NOTHING and kills NOTHING.
# Operator: "kisi ko bhi restart ki permission mt de … CMS sirf hard coded hi
# band ho sakta hai, chahe kuch bhi ho."  Every CMS restart silences every
# camera for ~96 s.  The three heal actions below report what they WOULD have
# done and return without acting.  This is a constant, not a setting, on
# purpose — no env var, flag file or config row can turn it back on.
ALLOW_RESTART = False
STATE_FILE  = os.path.join(HERE, "guardian_state.json")
STATUS_FILE = os.path.join(HERE, "status.json")
VIDEOS      = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/videos"

CHECK_EVERY       = int(os.environ.get("GUARDIAN_INTERVAL", "60"))
REPORT_EVERY      = int(os.environ.get("GUARDIAN_REPORT_EVERY", "3600"))
STALE_S           = int(os.environ.get("GUARDIAN_STALE_S", "180"))
CMS_THREAD_LIMIT  = int(os.environ.get("GUARDIAN_CMS_THREADS", "560"))
LOG_LIMIT_MB      = int(os.environ.get("GUARDIAN_LOG_MB", "800"))
GREETING          = os.environ.get("GUARDIAN_GREETING", "Manoj sir")

COOLDOWN = {"collector": 600, "cms": 1800, "reap": 900, "logs": 1800}
MAX_PER_HOUR = {"collector": 6, "cms": 2, "reap": 4, "logs": 2}


def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def db():
    import psycopg2
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1", port=5432, connect_timeout=5)
    c.autocommit = True
    return c


def dc(c):
    import psycopg2.extras
    return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def token():
    from auth import create_token            # type: ignore
    return create_token("admin", "admin", 1)


# ── state (cooldowns + per-hour caps + what to report) ─────────────────────
def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"last": {}, "hourly": {}, "seen": [], "healed": [], "since": time.time()}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE_FILE)


def may_heal(st, kind):
    now = time.time()
    if now - st["last"].get(kind, 0) < COOLDOWN.get(kind, 900):
        return False, "cooldown"
    recent = [t for t in st["hourly"].get(kind, []) if now - t < 3600]
    st["hourly"][kind] = recent
    if len(recent) >= MAX_PER_HOUR.get(kind, 4):
        return False, f"already healed {len(recent)}x this hour — reporting instead"
    return True, ""


def mark_heal(st, kind):
    now = time.time()
    st["last"][kind] = now
    st["hourly"].setdefault(kind, []).append(now)


# ── what a person would look at ────────────────────────────────────────────
def upstream_down():
    """True when the fault is outside this box — then we restart NOTHING."""
    try:
        with db() as c:
            cur = dc(c)
            cur.execute("""SELECT NULLIF(TRIM(pc.plc_ip),'') ip FROM mes_plc_configs pc
                            WHERE pc.parent_plc_id IS NULL
                              AND NULLIF(TRIM(pc.plc_ip),'') IS NOT NULL LIMIT 6""")
            ips = [r["ip"] for r in cur.fetchall()]
    except Exception:
        return False
    if not ips:
        return False
    reachable = sum(1 for ip in ips
                    if sh(f"timeout 3 bash -c 'echo > /dev/tcp/{ip}/502' 2>/dev/null && echo y") == "y")
    return reachable == 0


def survey():
    """One pass: every fact the heals and the report are based on."""
    s = {"ts": time.time(), "collectors": [], "cms": {}, "logs": [], "notes": []}
    try:
        with db() as c:
            cur = dc(c)
            cur.execute("""SELECT l.id, l.line_name, l.collector_status,
                                  EXTRACT(epoch FROM (now()-cl.heartbeat_at))::int age
                             FROM mes_lines l
                        LEFT JOIN mes_collector_locks cl ON cl.line_id = l.id
                            WHERE COALESCE(l.is_active,TRUE)
                              AND l.db_table_name IS NOT NULL AND l.db_table_name<>''
                         ORDER BY l.line_name""")
            s["collectors"] = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        s["notes"].append(f"DB not reachable: {str(e)[:70]}")

    pid = (re.findall(r"pid=(\d+)", sh("ss -ltnp 2>/dev/null | grep ':5555 '")) or [None])[0]
    s["cms"] = {
        "pid": pid,
        "threads": int(sh(f"ls /proc/{pid}/task 2>/dev/null | wc -l") or 0) if pid else 0,
        "health": sh("curl -s -o /dev/null -w '%{http_code}' --max-time 8 "
                     "http://127.0.0.1:5555/api/cameras/health"),
        "recorders": int(sh("pgrep -c ffmpeg") or 0),
        "ts_fresh": int(sh(f"find {VIDEOS} -maxdepth 1 -name '*.ts' -mmin -3 2>/dev/null | wc -l") or 0),
    }
    for d in ("logs", "Phase2/logs"):
        for line in (sh(f"find '{os.path.join(ROOT, d)}' -maxdepth 1 -name '*.log' "
                        f"-size +{LOG_LIMIT_MB}M -printf '%s %p\\n' 2>/dev/null") or "").split("\n"):
            if line.strip():
                sz, _, path = line.partition(" ")
                s["logs"].append({"path": path, "mb": int(int(sz) / 1048576)})
    return s


# ── the repairs ────────────────────────────────────────────────────────────
def heal_collector(line):
    """Restart ONE line through the same endpoint the admin button uses."""
    if not ALLOW_RESTART:
        return False, f"{line['line_name']}: needs attention — NOT restarted (auto-restart disabled)"
    import requests
    r = requests.post(f"http://127.0.0.1:8080/api/lines/{line['id']}/restart",
                      headers={"Authorization": f"Bearer {token()}"}, timeout=180)
    ok = r.status_code < 400
    return ok, f"{line['line_name']}: restart {'ok' if ok else 'failed ' + str(r.status_code)}"


def heal_cms():
    if not ALLOW_RESTART:
        return False, "CMS needs attention — NOT restarted (auto-restart disabled)"
    py = os.path.join(ROOT, "Phase2", ".venv-linux", "bin", "python")
    sh(f"cd '{ROOT}' && CMS_QUIET_SECONDS=90 VIDEO_ALLOW_UDP=0 "
       f"timeout 420 '{py}' Phase2/restart_cms.py", timeout=460)
    return True, "CMS restarted with the 90 s quiet window"


def heal_reap():
    if not ALLOW_RESTART:
        return False, "orphan recorders seen — NOT killed (auto-restart disabled)"
    cms = (re.findall(r"pid=(\d+)", sh("ss -ltnp 2>/dev/null | grep ':5555 '")) or [""])[0]
    if not cms:
        return False, "CMS not found — nothing reaped"
    killed = 0
    for pid in (sh("pgrep ffmpeg") or "").split():
        cl = sh(f"tr '\\0' ' ' < /proc/{pid}/cmdline 2>/dev/null")
        if "rtsp" not in cl:
            continue
        if sh(f"awk '{{print $4}}' /proc/{pid}/stat 2>/dev/null") == cms:
            continue
        m = re.search(r"(/\S+\.ts)", cl)
        if m and sh(f"find '{m.group(1)}' -mmin -3 2>/dev/null"):
            continue                       # still writing — leave it alone
        sh(f"kill {pid}")
        killed += 1
    return killed > 0, f"reaped {killed} orphan recorder(s)"


def heal_logs():
    sh(f"'{os.path.join(ROOT, 'rotate_logs.sh')}'", timeout=300)
    return True, "logs rotated"


# ── the hourly note ────────────────────────────────────────────────────────
def report(st, s, upstream):
    ok_c = sum(1 for c in s["collectors"]
               if c.get("age") is not None and c["age"] <= STALE_S)
    total_c = len(s["collectors"])
    # The same observation repeats every minute while a fault stands — collapse
    # it, or an hour of "plant network down" fills the note with one sentence
    # sixty times and buries everything else.
    def _uniq(xs):
        out = []
        for x in xs:
            if x not in out:
                out.append(x)
        return out

    healed = _uniq(st.get("healed", []))[-12:]
    seen = _uniq(st.get("seen", []))[-12:]

    lines = [f"Namaste {GREETING},", ""]
    lines.append(f"Abhi: collectors {ok_c}/{total_c} chal rahe hain, "
                 f"CMS {'theek' if s['cms']['health'] in ('200','401') else 'jawab nahi de raha'} "
                 f"({s['cms']['threads']} threads), {s['cms']['recorders']} recorder, "
                 f"{s['cms']['ts_fresh']} camera abhi record kar rahe hain.")
    if upstream:
        lines.append("")
        lines.append("⚠ Plant network neeche hai — PLC/camera tak nahi pahunch pa rahe. "
                     "Maine kuch restart NAHI kiya, kyunki restart se cable theek nahi hota.")
    lines.append("")
    if healed:
        lines.append("Maine jo khud theek kiya:")
        lines += [f"  • {h}" for h in healed]
    else:
        lines.append("Is ghante kuch theek karna nahi pada.")
    if seen:
        lines.append("")
        lines.append("Jo dekha:")
        lines += [f"  • {x}" for x in seen]

    need = []
    if upstream:
        need.append("Plant switch / network — IT ko bolna padega, MES se theek nahi hoga")
    stale = [c["line_name"] for c in s["collectors"]
             if c.get("collector_status") == "running"
             and (c.get("age") is None or c["age"] > STALE_S)]
    if stale:
        need.append(f"Ye lines data nahi de rahin: {', '.join(stale[:6])}")
    if s["logs"]:
        need.append(f"{len(s['logs'])} log file bahut badi ho gayi hai")
    if need:
        lines.append("")
        lines.append("Aapke dhyan ki zaroorat:")
        lines += [f"  ‼ {n}" for n in need]
    lines.append("")
    lines.append("— MES Guardian")
    return "\n".join(lines), bool(need)


def _cfg(key):
    """A value out of mes_mail_config, or None."""
    try:
        with db() as c:
            cur = dc(c)
            cur.execute("SELECT value FROM mes_mail_config WHERE key=%s", (key,))
            row = cur.fetchone()
        v = (row or {}).get("value")
        return (str(v).strip() or None) if v else None
    except Exception:
        return None



# ── who gets the bot's alerts ──────────────────────────────────────────────
# 2026-09-17 — this used to be `role IN ('admin','plant_head')`, and there is
# exactly ONE admin account on this install.  So every alert the bots ever
# raised — 142 of them — landed in that one inbox, and anyone signed in as
# themselves (section_incharge, production_incharge …) saw nothing and
# concluded the bot was not working.  It was working; nobody could see it.
#
# Now: `guardian_alert_to` in mes_mail_config, a comma-separated list of
# usernames, wins outright when set.  With nothing set it falls back to the
# roles that actually own the plant, so the report reaches a person by default
# instead of one service account.
DEFAULT_ALERT_ROLES = ("admin", "plant_head", "production_incharge",
                       "section_incharge", "shift_incharge")


def alert_recipients():
    """(ids, how) — who to notify and why, so the log can say it."""
    try:
        with db() as c:
            cur = dc(c)
            cur.execute("SELECT value FROM mes_mail_config WHERE key='guardian_alert_to'")
            row = cur.fetchone()
            names = [n.strip() for n in str((row or {}).get("value") or "").split(",")
                     if n.strip()]
            if names:
                cur.execute("SELECT id FROM mes_admin WHERE username = ANY(%s)", (names,))
                ids = [r["id"] for r in cur.fetchall()]
                if ids:
                    return ids, f"configured list ({', '.join(names)})"
            cur.execute("SELECT id FROM mes_admin WHERE role = ANY(%s)",
                        (list(DEFAULT_ALERT_ROLES),))
            return [r["id"] for r in cur.fetchall()], "senior roles"
    except Exception:
        return [], "lookup failed"

def notify(title, body):
    sent = []
    try:
        from routers.push import send_to_user      # type: ignore
        ids, how = alert_recipients()
        for uid in ids:
            send_to_user(uid, title, body, url="/", tag="guardian")
        sent.append(f"inbox x{len(ids)} ({how})")
    except Exception as e:
        print(f"[guardian] inbox failed: {e}", flush=True)
    # Email, when the stack has a configured sender.  No SMS gateway exists on
    # this install; if one is added, it plugs in right here.
    try:
        from mailer import send_mail               # type: ignore
        with db() as c:
            cur = dc(c)
            cur.execute("SELECT value FROM mes_mail_config WHERE key='guardian_to'")
            row = cur.fetchone()
        if row and row.get("value"):
            send_mail(row["value"], title, body)
            sent.append("email")
    except Exception:
        pass
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    st = load_state()
    last_report = st.get("last_report", 0)
    print(f"[guardian] watching every {CHECK_EVERY}s, report every "
          f"{REPORT_EVERY//60} min, hard stop = {STOP_FLAG}", flush=True)

    while True:
        if os.path.exists(STOP_FLAG):
            print("[guardian] STOP.flag present — standing down", flush=True)
            json.dump({"state": "stopped", "reason": "STOP.flag",
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      open(STATUS_FILE, "w"))
            if a.once:
                return
            time.sleep(CHECK_EVERY)
            continue

        s = survey()
        upstream = upstream_down()
        st.setdefault("seen", []); st.setdefault("healed", [])

        if upstream:
            st["seen"].append("plant network down — heals suppressed")
        else:
            # 1. collectors that should be running but are not writing
            for c in s["collectors"]:
                if c.get("collector_status") != "running":
                    continue
                age = c.get("age")
                if age is not None and age <= STALE_S:
                    continue
                st["seen"].append(f"{c['line_name']}: heartbeat "
                                  f"{'missing' if age is None else str(age)+'s old'}")
                ok, why = may_heal(st, "collector")
                if not ok:
                    st["seen"].append(f"{c['line_name']}: not restarted ({why})")
                    continue
                done, msg = heal_collector(c)
                mark_heal(st, "collector")
                st["healed"].append(msg)
                print(f"[guardian] HEAL {msg}", flush=True)

            # 2. CMS wedged or silent
            cms = s["cms"]
            wedged = cms["threads"] > CMS_THREAD_LIMIT or cms["health"] not in ("200", "401")
            if wedged:
                st["seen"].append(f"CMS unhealthy (threads {cms['threads']}, "
                                  f"health {cms['health'] or 'no answer'})")
                ok, why = may_heal(st, "cms")
                if ok:
                    done, msg = heal_cms()
                    mark_heal(st, "cms")
                    st["healed"].append(msg)
                    print(f"[guardian] HEAL {msg}", flush=True)
                else:
                    st["seen"].append(f"CMS not restarted ({why})")

            # 3. orphan recorders holding camera sessions
            if cms["recorders"] > 0 and cms["ts_fresh"] * 2 < cms["recorders"]:
                ok, _ = may_heal(st, "reap")
                if ok:
                    done, msg = heal_reap()
                    if done:
                        mark_heal(st, "reap")
                        st["healed"].append(msg)
                        print(f"[guardian] HEAL {msg}", flush=True)

        # 4. logs (safe whatever the network is doing)
        if s["logs"]:
            st["seen"].append(f"{len(s['logs'])} log file over {LOG_LIMIT_MB} MB")
            ok, _ = may_heal(st, "logs")
            if ok:
                done, msg = heal_logs()
                mark_heal(st, "logs")
                st["healed"].append(msg)

        st["seen"] = st["seen"][-60:]
        st["healed"] = st["healed"][-60:]

        now = time.time()
        if now - last_report >= REPORT_EVERY:
            body, urgent = report(st, s, upstream)
            title = ("MES Guardian — dhyan dijiye" if urgent
                     else "MES Guardian — sab theek chal raha hai")
            ch = notify(title, body)
            print(f"[guardian] report sent via {ch or 'nothing'}", flush=True)
            last_report = now
            st["last_report"] = now
            st["seen"], st["healed"] = [], []

        json.dump({"state": "watching", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "collectors_ok": sum(1 for c in s["collectors"]
                                        if c.get("age") is not None and c["age"] <= STALE_S),
                   "collectors_total": len(s["collectors"]),
                   "cms": s["cms"], "upstream_down": upstream,
                   "healed_recent": st["healed"][-8:], "seen_recent": st["seen"][-8:]},
                  open(STATUS_FILE, "w"), indent=1)
        save_state(st)
        if a.once:
            return
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
