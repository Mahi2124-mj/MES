"""selfheal.py — the live agent's PID / backlog / HTTP-error detectors, and the
small set of repairs it is allowed to perform on its own.

Written 2026-09-17 after a power cut that the agent completely missed.  It
reported "video ok 16/31, p50 54ms" and looked healthy, because it only ever
replayed OLD cycles — which still serve fine from the clip archive.  Meanwhile
no camera had recorded a single frame for four hours and 30 of 34 collectors
could not reach their PLC.  Everything the agent measured was downstream of a
failure it had no probe for.

So these checks look at the things a person would look at:

  * PIDs      — is every service actually running, is anything orphaned or
                duplicated, is anything a zombie
  * backlog   — is the clip archive keeping up, is CMS piling up threads
  * HTTP      — do the endpoints the UI calls answer, and what does a 404 or a
                500 actually MEAN (missing row vs broken route)
  * recording — is any camera writing to disk RIGHT NOW
  * upstream  — can we even reach the PLCs and cameras

THE RULE FOR HEALING: only repair what is broken INSIDE this box, and only
when the cause is understood.  When the plant network is down, restarting CMS
does nothing except churn — so `plant_network_down` suppresses every heal.  A
repair that cannot explain itself is not a repair, it is a reboot loop.
"""

import json, os, re, subprocess, time

HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT   = os.path.dirname(HERE)
VIDEOS = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/videos"
CLIPS  = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips"
HEAL_STATE = os.path.join(HERE, "heal_state.json")

# A heal may not repeat inside its cooldown, however often the symptom is seen.
COOLDOWN_S = {"reap_orphan_recorders": 900, "restart_cms": 3600}

# 2026-09-19 — HARD-CODED: the live agent restarts NOTHING and kills NOTHING.
# Operator: "kisi ko bhi restart ki permission mt de … CMS sirf hard coded hi
# band ho sakta hai, chahe kuch bhi ho."  The agent still finds and reports
# every problem; run_heals() only says what it WOULD have done.  A constant on
# purpose — nothing the caller passes (enabled=True included) can override it.
ALLOW_RESTART = False


def sh(cmd, timeout=20):
    try:
        return subprocess.check_output(cmd, shell=True, text=True,
                                       stderr=subprocess.DEVNULL,
                                       timeout=timeout).strip()
    except Exception:
        return ""


def _p(sev, area, what, why, fix, heal=None):
    d = {"sev": sev, "area": area, "what": what, "why": why, "fix": fix}
    if heal:
        d["heal"] = heal
    return d


def _pid_on_port(port):
    out = sh(f"ss -ltnp 2>/dev/null | grep ':{port} '")
    return sorted(set(re.findall(r"pid=(\d+)", out)))


# ── 1. PIDs ──────────────────────────────────────────────────────────────────
def check_pids():
    """Services present, plus orphaned / duplicated / defunct processes."""
    out = []

    for name, port in (("MES-API", 8080), ("CMS-API", 5555), ("Frontend", 5656)):
        if not _pid_on_port(port):
            out.append(_p("critical", "Service",
                          f"{name} (:{port}) chal hi nahi raha",
                          "port pe koi process listen nahi kar raha — service gir gayi ya boot pe start hi nahi hui",
                          f"start_everything.sh chalao, ya {name} ka log dekho"))

    n_coll = int(sh("pgrep -cf 'collector_[a-z0-9_]*[.]py'") or 0)
    if n_coll == 0:
        out.append(_p("critical", "Service", "Ek bhi collector chal nahi raha",
                      "poori collector fleet band hai — koi production data record nahi hoga",
                      "start_everything.sh --with-collectors --force"))
    elif n_coll < 20:
        out.append(_p("warning", "Service", f"Sirf {n_coll} collector chal rahe hain",
                      "poori fleet ~30 ki hoti hai — kuch collector crash hue ya start nahi hue",
                      "logs/collector_*.log dekho"))

    # Orphaned camera recorders: ffmpeg pulling RTSP whose parent is no longer
    # the CMS that owns it.  A CMS restart leaves these behind (the ffmpeg gets
    # reparented) and they keep holding the camera's RTSP session, so the NEW
    # recorder can be refused by single-session cameras.
    cms = (_pid_on_port(5555) or [""])[0]
    orph, owned = [], 0
    for pid in (sh("pgrep ffmpeg") or "").split():
        cl = sh(f"tr '\\0' ' ' < /proc/{pid}/cmdline 2>/dev/null")
        if "rtsp" not in cl:
            continue
        ppid = sh(f"awk '{{print $4}}' /proc/{pid}/stat 2>/dev/null")
        if cms and ppid == cms:
            owned += 1
        else:
            orph.append(pid)
    if orph:
        out.append(_p("warning", "Process",
                      f"{len(orph)} orphan camera recorder chal rahe hain (CMS ke bachche nahi)",
                      "CMS restart hone par purane ffmpeg reparent ho jate hain; ye camera ka RTSP session "
                      "pakde rehte hain aur CPU khate hain, jabki nayi recording alag process kar raha hai",
                      f"in {len(orph)} orphan ko band karo — CMS wale {owned} recorder chalte rahenge",
                      heal="reap_orphan_recorders"))

    z = int(sh("ps -eo stat --no-headers | grep -c '^Z'") or 0)
    if z > 5:
        out.append(_p("warning", "Process", f"{z} zombie process pade hain",
                      "koi parent apne khatam ho chuke bachchon ko reap nahi kar raha",
                      "parent process dhundho (ps -eo stat,ppid | grep '^Z')"))
    return out


# ── 2. Backlog ───────────────────────────────────────────────────────────────
def check_backlog():
    out = []

    cms = (_pid_on_port(5555) or [""])[0]
    if cms:
        th = int(sh(f"ls /proc/{cms}/task 2>/dev/null | wc -l") or 0)
        if th > 560:
            out.append(_p("critical", "Backlog", f"CMS me {th} thread jam gaye hain",
                          "CMS Flask ko thread-per-request pe chalata hai; request nikal nahi rahin "
                          "isliye thread jama ho rahe hain — ~650 pe poora wedge ho jata hai",
                          "CMS_QUIET_SECONDS=90 python3 Phase2/restart_cms.py",
                          heal="restart_cms"))
        elif th > 400:
            out.append(_p("warning", "Backlog", f"CMS threads {th} (normal ~250)",
                          "thread dhire-dhire jama ho rahe hain — abhi wedge nahi hua",
                          "nazar rakho; 560 paar hua to CMS restart"))

    # Clip archive: how much of the last half hour actually got pre-rendered.
    # A miss is not a small thing — that cycle's video is cut live on click and
    # takes tens of seconds instead of milliseconds.
    made = int(sh(f"find {CLIPS} -name '*.mp4' -mmin -30 2>/dev/null | wc -l") or 0)
    enc  = int(sh("pgrep -c ffmpeg") or 0)
    if made == 0 and enc > 0:
        out.append(_p("warning", "Backlog", "Aadhe ghante se ek bhi clip archive nahi hui",
                      "encoder chal rahe hain par koi clip complete nahi hui — render atka hua hai",
                      "clip_archive lanes + GPU dekho (nvidia-smi)"))
    return out


# ── 3. HTTP errors (404 / 5xx) ───────────────────────────────────────────────
def check_http(base, token, lines):
    """Hit what the UI hits and explain the failures.

    A 404 is not automatically a bug — an unprovisioned line legitimately has
    no realtime row.  What matters is telling those two apart, so the operator
    is not sent hunting for a broken route that was never there.
    """
    import requests
    out, seen = [], {}
    h = {"Authorization": f"Bearer {token}"}

    probes = [("/api/lines/", "line list"), ("/api/zones/", "zone list"),
              ("/api/auth/me", "session check"), ("/api/push/inbox", "Inbox")]
    for ln in lines[:12]:
        probes.append((f"/api/lines/{ln['id']}/realtime", f"{ln['line_name']} realtime"))
        probes.append((f"/api/lines/{ln['id']}/wallboard-summary", f"{ln['line_name']} supervisor page"))

    for path, label in probes:
        try:
            r = requests.get(base + path, headers=h, timeout=30)
            code = r.status_code
        except Exception as e:
            out.append(_p("critical", "HTTP", f"{label} ka jawab hi nahi aaya",
                          f"request fail hui: {str(e)[:70]}",
                          "MES-API zinda hai ya nahi dekho"))
            continue
        if code == 404:
            seen.setdefault("404", []).append(label)
        elif code >= 500:
            out.append(_p("critical", "HTTP", f"{label} pe {code} aa raha hai",
                          "endpoint andar se crash ho raha hai — missing column/table ya code error",
                          f"MES-API log me is request ka traceback dekho ({path})"))
        time.sleep(0.12)                      # pace it; a burst creates its own errors

    if seen.get("404"):
        names = ", ".join(seen["404"][:6]) + (" …" if len(seen["404"]) > 6 else "")
        out.append(_p("warning", "HTTP", f"{len(seen['404'])} jagah 404 mila",
                      "in lines ka data hi nahi hai — ya to line provision nahi hui "
                      "(per-line table nahi bani), ya aaj us line pe koi shift row nahi bani",
                      f"Admin → Production me in lines ka setup dekho: {names}"))
    return out


# ── 4. Recording + upstream network ──────────────────────────────────────────
def check_recording_and_network(cameras_json=None):
    """The probe the agent was missing: is anything being recorded AT ALL, and
    if not, is that because the cameras are unreachable?"""
    out = []
    fresh = int(sh(f"find {VIDEOS} -maxdepth 1 -name '*.ts' -mmin -3 2>/dev/null | wc -l") or 0)

    # Sample one camera per configured subnet before blaming the software.
    subnets, reach, dead = {}, [], []
    cj = cameras_json or os.path.join(
        ROOT, "..", "..", "New folder (2)", "New folder (2)", "backend", "cameras.json")
    try:
        raw = open(cj, encoding="utf-8", errors="replace").read()
        for ip in sorted(set(re.findall(r"192\.168\.\d+\.\d+", raw))):
            subnets.setdefault(ip.rsplit(".", 1)[0], ip)
    except Exception:
        pass
    for net, ip in subnets.items():
        ok = sh(f"timeout 4 bash -c 'echo > /dev/tcp/{ip}/554' 2>/dev/null && echo y") == "y"
        (reach if ok else dead).append(net)

    network_down = bool(subnets) and not reach

    if network_down:
        out.append(_p("critical", "Network",
                      f"Koi bhi camera network reachable nahi ({len(dead)} subnet)",
                      "server ka apna link theek hai par cameras/PLC tak packet nahi pahunch raha — "
                      "plant ka switch ya un devices ki power gayi hui hai",
                      "IT/electrical ko bolo — MES se ye theek nahi hoga"))
    elif dead:
        out.append(_p("warning", "Network",
                      f"{len(dead)} camera subnet reachable nahi: {', '.join(sorted(dead))}",
                      "in subnets tak route nahi ja raha (baaki chal rahe hain) — "
                      "gateway in subnets ko forward nahi kar raha",
                      "IT ko bolo: in subnets ki routing/switch dekhe"))

    if fresh == 0:
        out.append(_p("critical", "Recording", "Ek bhi camera record nahi kar raha",
                      ("cameras hi reachable nahi hain — recorder connect nahi kar pa raha"
                       if network_down else
                       "cameras reachable hain par CMS recorder file nahi likh raha"),
                      ("pehle network theek karao" if network_down else
                       "CMS_QUIET_SECONDS=90 python3 Phase2/restart_cms.py"),
                      heal=None if network_down else "restart_cms"))

    # The failure that started all this: CMS decided at boot that the video
    # disk was missing, because it started before the disk finished mounting.
    if not network_down and fresh == 0:
        log = os.path.join(ROOT, "logs", "CMS-API.log")
        if os.path.exists(log) and "custom path NOT writable" in sh(f"tail -4000 '{log}' 2>/dev/null | grep -a 'VIDEO-PATH' | tail -5"):
            writable = os.path.isdir(VIDEOS) and os.access(VIDEOS, os.W_OK)
            out.append(_p("critical", "Recording",
                          "CMS ne boot pe video disk ko 'not writable' maan liya tha",
                          "boot pe CMS disk mount hone se PEHLE chalu ho gaya, aur wahi faisla pakde baitha hai"
                          + (" — disk ab mount hai aur likhi ja sakti hai" if writable else ""),
                          "CMS restart karo (disk ab available hai)",
                          heal="restart_cms" if writable else None))
    return out, network_down


# ── heals ────────────────────────────────────────────────────────────────────
def _state():
    try:
        return json.load(open(HEAL_STATE))
    except Exception:
        return {}


def _remember(k):
    s = _state(); s[k] = time.time()
    json.dump(s, open(HEAL_STATE, "w"))


def _reap_orphan_recorders():
    cms = (_pid_on_port(5555) or [""])[0]
    if not cms:
        return "CMS hi nahi mila — kuch nahi chheda"
    killed = 0
    for pid in (sh("pgrep ffmpeg") or "").split():
        cl = sh(f"tr '\\0' ' ' < /proc/{pid}/cmdline 2>/dev/null")
        if "rtsp" not in cl:
            continue
        if sh(f"awk '{{print $4}}' /proc/{pid}/stat 2>/dev/null") == cms:
            continue                      # CMS ka apna recorder — kabhi mat chhedo
        out = re.search(r"(/\S+\.ts)", cl)
        if out and sh(f"find '{out.group(1)}' -mmin -3 2>/dev/null"):
            continue                      # abhi bhi likh raha hai — chhodo
        sh(f"kill {pid}")
        killed += 1
    return f"{killed} orphan recorder band kiye (CMS ke apne recorder chalte rahe)"


def _restart_cms():
    py = os.path.join(ROOT, "Phase2", ".venv-linux", "bin", "python")
    sh(f"cd '{ROOT}' && CMS_QUIET_SECONDS=90 VIDEO_ALLOW_UDP=0 "
       f"timeout 420 '{py}' Phase2/restart_cms.py", timeout=460)
    return "CMS restart kiya (90s quiet window ke saath)"


HEALS = {"reap_orphan_recorders": _reap_orphan_recorders, "restart_cms": _restart_cms}


def run_heals(problems, network_down, enabled=True):
    """Apply the repairs the problems asked for.  Returns what was done."""
    done = []
    if not ALLOW_RESTART:
        return [{"action": key, "result": "NOT done — auto-restart disabled by operator; "
                                          "a person must decide"}
                for key in dict.fromkeys(p["heal"] for p in problems if p.get("heal"))]
    if not enabled:
        return done
    if network_down:
        return [{"action": "—", "result": "Network down hai — kuch heal nahi kiya, "
                                          "warna bekaar restart loop banta"}]
    state, now = _state(), time.time()
    for key in dict.fromkeys(p["heal"] for p in problems if p.get("heal")):
        last = state.get(key, 0)
        cd = COOLDOWN_S.get(key, 1800)
        if now - last < cd:
            done.append({"action": key,
                         "result": f"cooldown — {int((cd - (now - last)) / 60)} min baad dobara koshish"})
            continue
        try:
            res = HEALS[key]()
        except Exception as e:
            res = f"fail: {str(e)[:80]}"
        _remember(key)
        done.append({"action": key, "result": res})
    return done


# ── 5. The Network panel's own inventory ─────────────────────────────────────
def check_network_devices(base, token):
    """Ask MES what it knows about the plant network hardware.

    2026-09-17 — the operator found "all 22 switches down" on the Network panel
    and asked why the agent had not said so.  Fair: the agent sampled ONE camera
    per subnet and concluded "cameras unreachable", but never looked at the
    device inventory the MES already maintains.  Sampling tells you something is
    wrong; the switch list tells you WHERE — and a whole switch being dark is a
    different, larger fault than a few cameras timing out.

    Also surfaces duplicate-IP config, which no amount of pinging would reveal:
    two devices sharing an address answer intermittently depending on who won
    the last ARP, which looks like a flaky camera rather than a config error.
    """
    import requests
    out = []
    h = {"Authorization": f"Bearer {token}"}

    try:
        devs = requests.get(base + "/api/network/status", headers=h, timeout=40).json()
        devs = devs.get("devices", devs if isinstance(devs, list) else [])
    except Exception as exc:
        return [_p("warning", "Network", "Network panel ka data nahi mila",
                   f"/api/network/status fail: {str(exc)[:70]}",
                   "MES-API log dekho")], 0

    hw = [d for d in devs if d.get("kind") != "area"]
    down = [d for d in hw if str(d.get("status", "")).lower() in ("down", "offline", "dead")]
    sw_down = [d for d in down if d.get("kind") == "switch"]

    if hw and len(down) == len(hw):
        names = ", ".join(str(d.get("name")) for d in sw_down[:6])
        out.append(_p("critical", "Network",
                      f"Network panel: saare {len(hw)} device down hain ({len(sw_down)} switch)",
                      "har line ka switch chup hai — ek-do camera ka masla nahi, poora plant "
                      f"network hi neeche hai ({names}…)",
                      "IT/electrical ko bolo — switch ki power aur uplink dekhe"))
    elif sw_down:
        names = ", ".join(str(d.get("name")) for d in sw_down[:6])
        out.append(_p("critical", "Network",
                      f"{len(sw_down)} switch down hain: {names}",
                      "in switches ke peeche ki saari machine aur camera kat gayi hain",
                      "in switches ki power/uplink check karao"))
    elif down:
        out.append(_p("warning", "Network", f"{len(down)} network device down hain",
                      "inke peeche ka data ya video nahi aayega",
                      "Network panel me dekho kaun se device hain"))

    try:
        cf = requests.get(base + "/api/network/ip-conflicts", headers=h, timeout=40).json()
        cf = cf.get("conflicts", cf if isinstance(cf, list) else [])
    except Exception:
        cf = []
    if cf:
        worst = cf[0]
        out.append(_p("warning", "Network", f"{len(cf)} IP conflict configured hain",
                      "ek hi IP do ya teen device pe set hai — dono kabhi-kabhi hi jawab denge, "
                      f"jo flaky camera/PLC jaisa dikhta hai. Jaise {worst.get('ip')}: "
                      f"{str(worst.get('msg',''))[:80]}",
                      "Network panel → IP conflicts me har ek ko alag IP do"))

    # /devices and /cables complete the panel.  A cable pointing at a device
    # that no longer exists, or a device with no IP, is a map that will mislead
    # whoever is tracing a fault at 2 a.m. — worth saying out loud.
    try:
        allpanel = requests.get(base + "/api/network/devices", headers=h, timeout=40).json()
        allpanel = allpanel if isinstance(allpanel, list) else allpanel.get("devices", [])
    except Exception:
        allpanel = []
    noip = [d for d in allpanel
            if d.get("kind") in ("switch", "plc", "camera") and not d.get("ip")]
    if noip:
        out.append(_p("warning", "Network",
                      f"{len(noip)} device panel pe hain par unka IP set nahi",
                      "inka status kabhi check nahi ho sakta — map pe dikhte hain, "
                      "monitor me aate hi nahi",
                      "Network panel me in devices ka IP bharo"))

    try:
        cables = requests.get(base + "/api/network/cables", headers=h, timeout=40).json()
        cables = cables if isinstance(cables, list) else cables.get("cables", [])
    except Exception:
        cables = []
    known = {d.get("id") for d in allpanel}
    if known:
        broken = [c for c in cables
                  if (c.get("from") and c["from"] not in known)
                  or (c.get("to") and c["to"] not in known)]
        if broken:
            out.append(_p("warning", "Network",
                          f"{len(broken)} cable aise device se juda hai jo map pe hai hi nahi",
                          "device hata diya gaya par uska cable reh gaya — map galat raasta dikhayega",
                          "Network panel me ye cables hatao ya sahi device se jodo"))

    return out, len(sw_down)


# ── 6. Video Archive — walk the whole browse tree like a person would ────────
def check_video_archive(base, token):
    """Open Historical → Video Archive the way an operator does and find the
    places it leads nowhere.

    2026-09-17 — the archive is a directory tree (date → line → shift → machine
    → cycle) and the browser used to list a folder whether or not it held any
    clip.  296 empty machine folders across the archive meant the operator could
    pick a machine and get nothing.  This check walks the tree through the API
    and reports any level that offers a choice with nothing behind it, plus
    whether a clip actually plays.
    """
    import requests, urllib.parse
    out = []
    h = {"Authorization": f"Bearer {token}"}

    def g(path):
        r = requests.get(base + path, headers=h, timeout=60)
        r.raise_for_status()
        return r.json()

    try:
        days = g("/api/clip-archive/days").get("days") or []
    except Exception as exc:
        return [_p("critical", "Video Archive", "Video Archive khul hi nahi raha",
                   f"/api/clip-archive/days fail: {str(exc)[:70]}",
                   "MES-API log dekho")]

    if not days:
        return [_p("critical", "Video Archive", "Archive me ek bhi din nahi hai",
                   "koi clip save nahi hui — archiver band hai ya retention ne sab uda diya",
                   "clip_archive worker aur ARCHIVE_ROOT ki disk dekho")]

    day = days[0]
    dead, machines, clips = [], 0, 0
    try:
        lines = g(f"/api/clip-archive/lines?date={day}").get("lines") or []
        for L in lines:
            shifts = g(f"/api/clip-archive/shifts?date={day}"
                       f"&line_id={L['line_id']}").get("shifts") or []
            if not shifts:
                dead.append(f"{L['name']}: line dikhti hai par koi shift nahi")
                continue
            for s in shifts:
                mc = g(f"/api/clip-archive/machines?date={day}&line_id={L['line_id']}"
                       f"&shift={urllib.parse.quote(str(s))}").get("machines") or []
                if not mc:
                    dead.append(f"{L['name']} / {s}: koi machine nahi")
                    continue
                for m in mc:
                    machines += 1
                    n = m.get("clips", 0)
                    clips += n
                    if n == 0:
                        dead.append(f"{L['name']} / {s} / {m.get('name')}: 0 clips")
    except Exception as exc:
        out.append(_p("warning", "Video Archive", "Archive ka poora tree nahi ghoom paya",
                      str(exc)[:110], "clip_browser ka log dekho"))

    if dead:
        out.append(_p("warning", "Video Archive",
                      f"{len(dead)} jagah archive me kuch hai hi nahi",
                      "operator wahan tak pahunch kar khali haath lautega: "
                      + "; ".join(dead[:4]),
                      "clip_browser khali folder chhupata hai — agar phir bhi dikh rahe "
                      "hain to MES-API restart hua ya nahi dekho"))
    else:
        # Only worth saying the tree is clean if we actually found clips in it.
        if machines and clips == 0:
            out.append(_p("warning", "Video Archive",
                          f"{machines} machine dikh rahi hain par ek bhi clip nahi",
                          "folder bane hue hain, clip koi nahi — archiver render nahi kar paya",
                          "clip_archive worker + GPU dekho"))

    # Does a clip actually PLAY?  Listing is not playing.
    try:
        for L in (lines or [])[:1]:
            shifts = g(f"/api/clip-archive/shifts?date={day}"
                       f"&line_id={L['line_id']}").get("shifts") or []
            for s in shifts[:1]:
                mc = g(f"/api/clip-archive/machines?date={day}&line_id={L['line_id']}"
                       f"&shift={urllib.parse.quote(str(s))}").get("machines") or []
                for m in mc[:1]:
                    cl = g(f"/api/clip-archive/clips?date={day}&line_id={L['line_id']}"
                           f"&shift={urllib.parse.quote(str(s))}"
                           f"&machine={urllib.parse.quote(str(m['machine']))}")
                    c0 = (cl.get("clips") or [None])[0]
                    if not c0:
                        continue
                    u = (f"{base}/api/clip-archive/video?date={day}&line_id={L['line_id']}"
                         f"&shift={urllib.parse.quote(str(s))}"
                         f"&machine={urllib.parse.quote(str(m['machine']))}"
                         f"&cycle_seq={c0['cycle_seq']}&ng={'true' if c0.get('ng') else 'false'}")
                    t0 = time.time()
                    rr = requests.get(u, headers=h, timeout=90, stream=True)
                    first = next(rr.iter_content(65536), b"")
                    ms = int((time.time() - t0) * 1000)
                    if rr.status_code not in (200, 206) or not first:
                        out.append(_p("critical", "Video Archive",
                                      "Archive se video chal nahi rahi",
                                      f"{L['name']} / {s} / cycle {c0['cycle_seq']} pe "
                                      f"HTTP {rr.status_code} — clip list me hai par serve nahi ho rahi",
                                      "clip file disk pe hai ya nahi dekho (ARCHIVE_ROOT)"))
                    elif ms > 2000:
                        out.append(_p("warning", "Video Archive",
                                      f"Archive video {ms}ms me khuli (2s se upar)",
                                      "pre-rendered clip hai, phir bhi der lagi — disk ya API load",
                                      "server load dekho"))
    except Exception as exc:
        out.append(_p("warning", "Video Archive", "Archive playback test nahi ho paya",
                      str(exc)[:110], "clip_browser /video dekho"))

    return out


# ── 7. Archive COVERAGE — does the video actually cover the production? ──────
def check_archive_coverage(base, token):
    """How many of a shift's cycles actually have video.

    2026-09-17 — the operator opened Video Archive for YHB-SS / 16 Sep / A shift
    and saw "#870, #869, #867 ... #783, #12, #11" and asked why the cycle and
    clip numbers did not match.  They did not match because **77 of 1,879
    cycles had a clip — 4%**.  The dead-end check added earlier could not see
    this: every folder it walked was non-empty and every clip it opened played.
    The tree was healthy; the EVIDENCE was missing.

    This is not cosmetic.  The archiver can only cut a clip while the rolling
    .ts still covers that moment (CLIP_ARCHIVE_WINDOW_MIN, 42 min).  A cycle
    that ages out of that window has no footage anywhere, ever — so low
    coverage means that shift's video evidence is permanently gone, and nobody
    finds out until someone goes looking for a specific part weeks later.

    Checked per line for the most recent COMPLETED shift, because the running
    shift is legitimately still filling in.
    """
    import requests
    out = []
    h = {"Authorization": f"Bearer {token}"}
    try:
        days = requests.get(base + "/api/clip-archive/days",
                            headers=h, timeout=40).json().get("days") or []
        if len(days) < 2:
            return out
        day = days[1]                     # yesterday: its shifts are finished
        lines = requests.get(f"{base}/api/clip-archive/lines?date={day}",
                             headers=h, timeout=40).json().get("lines") or []
    except Exception:
        return out

    worst = []
    for L in lines:
        try:
            shifts = requests.get(f"{base}/api/clip-archive/shifts?date={day}"
                                  f"&line_id={L['line_id']}", headers=h,
                                  timeout=40).json().get("shifts") or []
            for sh in shifts:
                if sh in ("GAP", "UNKNOWN"):
                    continue              # not production shifts
                r = requests.get(f"{base}/api/clip-archive/clips?date={day}"
                                 f"&line_id={L['line_id']}&shift={sh}&machine=main",
                                 headers=h, timeout=60).json()
                pct, ran = r.get("coverage_pct"), r.get("cycles_total")
                if pct is None or not ran or ran < 50:
                    continue              # too few cycles to judge
                if pct < 60:
                    worst.append((pct, L["name"], sh, r.get("total", 0), ran))
        except Exception:
            continue

    if worst:
        worst.sort()
        head = "; ".join(f"{n} {sh} {got}/{ran} ({p}%)" for p, n, sh, got, ran in worst[:4])
        sev = "critical" if worst[0][0] < 25 else "warning"
        out.append(_p(sev, "Video Archive",
                      f"{len(worst)} line/shift me video coverage kam hai ({day})",
                      "in cycles ki footage ab kahin nahi hai — recording window "
                      f"nikal gaya to clip ban hi nahi sakti. {head}",
                      "clip_archive ki speed badhao (PARALLEL/GPU lanes) ya "
                      "CLIP_ARCHIVE_WINDOW_MIN badhao — lekin asli limit ye hai ki "
                      "rolling .ts kitni der rakhi jaati hai"))
    return out
