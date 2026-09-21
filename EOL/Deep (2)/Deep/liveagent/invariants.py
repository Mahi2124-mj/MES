"""invariants.py — rules that must hold, whatever the bug of the week is.

Written 2026-09-17 after the operator pointed out, correctly, that the agent
was only ever learning the last thing THEY had found:

    power cut missed      -> I added a recording probe
    switches down missed  -> I added a network-device probe
    4% clip coverage      -> I added a coverage probe

Three patches for three specific bugs.  That is whack-a-mole, not a bot.  The
checks below are deliberately written to know nothing about any particular
bug — each states a property the system should always have, so a fault nobody
has seen yet still trips one.

Every check returns problems in the same shape selfheal uses, and every check
is registered in CHECKS so the agent can report what it actually ran.  Adding
a rule is one function plus one line.
"""

import collections
import json
import os
import re
import time

import requests

# Rendering is checked in a real browser; keep it optional so a missing browser
# or a syntax slip in that module can never take the other invariants down.
try:
    import render_check
except Exception:                                    # pragma: no cover
    render_check = None

# The standing rule: no backend error belongs in the logs at all.  Kept optional
# for the same reason as render_check.
try:
    import log_watch
except Exception:                                    # pragma: no cover
    log_watch = None


def _p(sev, area, what, why, fix):
    return {"sev": sev, "area": area, "what": what, "why": why, "fix": fix}


# ══════════════════════════════════════════════════════════════════════════
# 1. The same request must give the same answer, whichever worker serves it
# ══════════════════════════════════════════════════════════════════════════
def workers_agree(base, token, **_):
    """Hit read-only endpoints repeatedly and flag ones whose answer changes.

    This is the rule that would have caught the Network panel flicker without
    anyone knowing about `_snapshot`: the API runs 4 uvicorn workers, module
    level state is per-process, and only the background leader fills it — so
    three workers answered `devices: []` and the panel flickered between the
    full map and "nothing configured".  Nobody had to know the cause; the
    ANSWERS DISAGREED, and that alone is always wrong for a read-only GET.
    """
    out = []
    h = {"Authorization": f"Bearer {token}"}
    probes = ["/api/network/status", "/api/lines/", "/api/zones/",
              "/api/clip-archive/days", "/api/quality/kpi", "/api/capa/",
              "/api/breakdowns/active", "/api/maintenance-kpi/"]
    for path in probes:
        sizes, codes = [], []
        for _ in range(6):
            try:
                r = requests.get(base + path, headers=h, timeout=40)
                codes.append(r.status_code)
                sizes.append(len(r.content))
            except Exception:
                codes.append(0)
                sizes.append(-1)
            time.sleep(0.05)
        ok = [s for s, c in zip(sizes, codes) if c == 200]
        if len(set(codes)) > 1:
            out.append(_p("critical", "Consistency",
                          f"{path} har baar alag jawab de raha hai",
                          f"6 requests me HTTP codes {sorted(set(codes))} mile — "
                          "kuch API workers ke paas data hai, kuch ke paas nahi",
                          "endpoint ka data process ki memory me to nahi rakha? "
                          "usse saanjha karo (dekho routers/network.py ka snapshot file)"))
        elif ok and (max(ok) - min(ok)) > max(64, min(ok) * 0.5):
            out.append(_p("critical", "Consistency",
                          f"{path} ka jawab worker-dar-worker badal raha hai",
                          f"response size {min(ok)}..{max(ok)} bytes ke beech ghoom raha hai — "
                          "ek hi read request har worker pe alag data de rahi hai",
                          "per-process memory me rakha state saanjha karo"))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 2. Anything the UI offers as a choice must lead somewhere
# ══════════════════════════════════════════════════════════════════════════
def no_dead_ends(base, token, **_):
    """Walk every drill-down the archive browser offers and flag empty leaves.

    Generalises the 296-empty-folder bug: the rule is not "clip_browser had a
    bug", it is "a list the user can click must not be empty behind it".
    """
    out = []
    h = {"Authorization": f"Bearer {token}"}

    def g(p):
        return requests.get(base + p, headers=h, timeout=60).json()

    try:
        days = g("/api/clip-archive/days").get("days") or []
    except Exception:
        return out
    dead = []
    for day in days[:2]:
        try:
            for L in g(f"/api/clip-archive/lines?date={day}").get("lines") or []:
                shifts = g(f"/api/clip-archive/shifts?date={day}"
                           f"&line_id={L['line_id']}").get("shifts") or []
                if not shifts:
                    dead.append(f"{day} {L['name']}: koi shift nahi")
                for sh in shifts:
                    mc = g(f"/api/clip-archive/machines?date={day}"
                           f"&line_id={L['line_id']}&shift={sh}").get("machines") or []
                    if not mc:
                        dead.append(f"{day} {L['name']}/{sh}: koi machine nahi")
                    for m in mc:
                        if m.get("clips", 1) == 0:
                            dead.append(f"{day} {L['name']}/{sh}/{m.get('name')}: 0 clips")
        except Exception:
            continue
    if dead:
        out.append(_p("warning", "Dead end",
                      f"{len(dead)} jagah list me option hai par peeche kuch nahi",
                      "operator wahan tak click karke khali haath lautega: "
                      + "; ".join(dead[:4]),
                      "jo list khali hai use dikhao mat"))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 3. Every route the app exposes must answer
# ══════════════════════════════════════════════════════════════════════════
def routes_answer(base, token, root=None, **_):
    """Discover parameter-free GETs from the router source and call them all.

    Source-discovered, so it never goes stale as routes are added — a new
    endpoint that 500s is caught the day it ships, not when someone opens
    that screen.  (Same approach pm_agent uses; kept here so the 30-minute
    agent catches it too.)
    """
    out = []
    h = {"Authorization": f"Bearer {token}"}
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rdir = os.path.join(root, "Phase2", "routers")
    routes = []
    try:
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(rdir, fn), encoding="utf-8", errors="replace").read()
            m = re.search(r"APIRouter\((.*?)\)", src, re.S)
            pref = ""
            if m:
                pm = re.search(r'prefix\s*=\s*["\']([^"\']+)', m.group(1))
                if pm:
                    pref = pm.group(1)
            for r in re.findall(r'@router\.get\(\s*["\']([^"\']+)', src):
                full = pref + r
                if "{" in full:                    # needs an id — skip
                    continue
                routes.append(full)
    except Exception:
        return out

    # A crash and a slow answer are different faults and must not share a
    # label — the first version called /api/network/discover a "server error"
    # because it took 42.8 s, which sent the reader hunting for a traceback
    # that does not exist.  Time each call and classify on what actually
    # happened.
    broken, slow = [], []
    for path in sorted(set(routes)):
        t0 = time.time()
        try:
            r = requests.get(base + path, headers=h, timeout=90)
            ms = int((time.time() - t0) * 1000)
            if r.status_code >= 500:
                broken.append((path, r.status_code))
            elif ms > 10000:
                slow.append((path, ms))
        except Exception as exc:
            ms = int((time.time() - t0) * 1000)
            if "Timeout" in type(exc).__name__:
                slow.append((path, ms))
            else:
                broken.append((path, type(exc).__name__))
        time.sleep(0.05)
    if broken:
        out.append(_p("critical", "Route",
                      f"{len(broken)} endpoint server error de rahe hain",
                      "ye app ke apne route hain aur andar se crash kar rahe hain: "
                      + "; ".join(f"{p} -> {c}" for p, c in broken[:5]),
                      "MES-API log me in paths ka traceback dekho"))
    if slow:
        slow.sort(key=lambda x: -x[1])
        out.append(_p("warning", "Route",
                      f"{len(slow)} endpoint bahut slow hain (10s se upar)",
                      "ye crash nahi hue, bas bahut der lagate hain — itni der ek API "
                      "worker inhi pe atka rehta hai: "
                      + "; ".join(f"{p} {ms/1000:.0f}s" for p, ms in slow[:4]),
                      "in endpoints pe ek upper bound lagao, warna kuch click "
                      "saare workers ko rok sakte hain"))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 4. What the plant produced must still have its evidence
# ══════════════════════════════════════════════════════════════════════════
def evidence_retained(base, token, db=None, dc=None, **_):
    """Cycles produced vs clips kept, per completed line/shift.

    The rule: if the system recorded that something happened, the proof it
    promises should still exist.  A 4%-covered shift is not a slow page, it is
    evidence that is gone for good.
    """
    out = []
    h = {"Authorization": f"Bearer {token}"}
    try:
        days = requests.get(base + "/api/clip-archive/days",
                            headers=h, timeout=40).json().get("days") or []
        if len(days) < 2:
            return out
        day = days[1]
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
                    continue
                r = requests.get(f"{base}/api/clip-archive/clips?date={day}"
                                 f"&line_id={L['line_id']}&shift={sh}&machine=main",
                                 headers=h, timeout=60).json()
                pct, ran = r.get("coverage_pct"), r.get("cycles_total")
                if pct is None or not ran or ran < 50:
                    continue
                if pct < 60:
                    worst.append((pct, L["name"], sh, r.get("total", 0), ran))
        except Exception:
            continue
    if worst:
        worst.sort()
        out.append(_p("critical" if worst[0][0] < 25 else "warning", "Evidence",
                      f"{len(worst)} line/shift ki video coverage kam hai ({day})",
                      "in cycles ki footage ab kahin nahi hai — recording window nikal "
                      "gaya to clip ban hi nahi sakti. "
                      + "; ".join(f"{n} {s} {g}/{t} ({p}%)" for p, n, s, g, t in worst[:4]),
                      "clip_archive ki speed ya .ts retention badhao"))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 5. Numbers a screen shows must be explainable
# ══════════════════════════════════════════════════════════════════════════
def numbers_explainable(base, token, **_):
    """Flag a count shown to the operator that silently hides its denominator.

    The "#870 … #783, #12" confusion was this: the page said "77 videos" while
    1,879 cycles had run.  A count with no denominator invites exactly that
    misreading, so the archive list must carry both numbers.
    """
    out = []
    h = {"Authorization": f"Bearer {token}"}
    try:
        days = requests.get(base + "/api/clip-archive/days",
                            headers=h, timeout=40).json().get("days") or []
        if not days:
            return out
        lines = requests.get(f"{base}/api/clip-archive/lines?date={days[0]}",
                             headers=h, timeout=40).json().get("lines") or []
        if not lines:
            return out
        L = lines[0]
        shifts = requests.get(f"{base}/api/clip-archive/shifts?date={days[0]}"
                              f"&line_id={L['line_id']}", headers=h,
                              timeout=40).json().get("shifts") or []
        if not shifts:
            return out
        r = requests.get(f"{base}/api/clip-archive/clips?date={days[0]}"
                         f"&line_id={L['line_id']}&shift={shifts[0]}&machine=main",
                         headers=h, timeout=60).json()
        if "cycles_total" not in r:
            out.append(_p("warning", "Clarity",
                          "Video Archive ki ginti apna denominator nahi bata rahi",
                          "page sirf 'N videos' dikhata hai — kitni cycles chali thin ye nahi, "
                          "isliye missing clips 'number match nahi ho raha' jaise dikhte hain",
                          "/clips me cycles_total + coverage_pct wapas lao"))
    except Exception:
        pass
    return out


# ══════════════════════════════════════════════════════════════════════════
# 6. Every background worker that should run, runs — exactly once
# ══════════════════════════════════════════════════════════════════════════
def workers_running(base, token, root=None, **_):
    """Each background worker must appear exactly once in the current boot.

    Guards both directions of the bg_leader change: a worker that silently did
    not start, and one that started in every uvicorn worker (duplicate mails,
    duplicate alerts, N times the clip rendering).
    """
    out = []
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log = os.path.join(root, "Phase2", "logs", "MES-API.log")
    if not os.path.exists(log):
        return out
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 400_000))
            tail = fh.read().decode("utf-8", "replace")
    except Exception:
        return out
    # only this boot
    marks = [m.start() for m in re.finditer(r"\[BG\] pid \d+ is THE background leader", tail)]
    if not marks:
        return out
    tail = tail[marks[-1]:]
    expected = {"CLIP-ARCHIVE] on": "clip archiver",
                "MANPOWER-ALERT] Worker started": "manpower alerts",
                "OEE-ALARM] Worker started": "OEE alarm",
                "BD-MAIL] Worker started": "breakdown mail",
                "POKA-DIGEST] Worker started": "poka-yoke digest",
                "SLOT-REPORT] Worker started": "hourly slot report"}
    missing, dup = [], []
    for token_, label in expected.items():
        n = tail.count(token_)
        if n == 0:
            missing.append(label)
        elif n > 1:
            dup.append(f"{label} x{n}")
    if missing:
        out.append(_p("warning", "Worker",
                      f"{len(missing)} background worker is boot me chalu hi nahi hua",
                      "ye kaam chup-chaap ruka hua hai: " + ", ".join(missing),
                      "MES-API log me iska start message dhundho"))
    if dup:
        out.append(_p("critical", "Worker",
                      f"{len(dup)} background worker ek se zyada baar chal raha hai",
                      "har uvicorn worker apni copy chala raha hai — duplicate mail, "
                      "duplicate alert, N guna clip rendering: " + ", ".join(dup),
                      "bg_leader.is_leader() se gate karo (Phase2/bg_leader.py)"))
    return out


# The registry.  Adding a rule = one function + one line here.
CHECKS = [
    ("workers agree across API processes", workers_agree),
    ("no dead ends in drill-downs",        no_dead_ends),
    ("every route answers",                routes_answer),
    ("evidence retained for production",   evidence_retained),
    ("numbers show their denominator",     numbers_explainable),
    ("background workers run exactly once", workers_running),
]

if log_watch is not None:
    # Reads what the services actually wrote since the last pass and reports
    # EVERY 5xx, traceback and poisoned transaction — the general rule, rather
    # than one probe per bug someone happened to notice.
    CHECKS.append(("no backend errors in the logs", log_watch.no_backend_errors))

if render_check is not None:
    # The only rule here that looks at the SCREEN rather than the API: controls
    # the operator can actually reach, text that is not cut off, a backdrop that
    # still sits on its data, fonts that really loaded, and printed totals that
    # match the API.  All five were real defects on the PEFF sheet in one day and
    # not one of them changed an API response.
    CHECKS.append(("what the operator sees holds together",
                   render_check.page_renders_correctly))


def run_all(base, token, root=None):
    """Run every invariant.  Returns (problems, ran, failed_check_names)."""
    problems, failed = [], []
    for name, fn in CHECKS:
        try:
            found = fn(base=base, token=token, root=root) or []
            problems.extend(found)
            if found:
                failed.append(name)
        except Exception as exc:
            problems.append(_p("warning", "Agent",
                               f"Invariant check chala nahi: {name}",
                               str(exc)[:140], "liveagent/invariants.py dekho"))
    return problems, len(CHECKS), failed
