"""log_watch.py — the standing rule: no backend error belongs in the logs.

Operator: "ye error to chahiye hi nhi bhai … backend pe error ek bhi nhi chahiye."

Every other check here asks a specific question.  This one asks the general one:
read what the services actually wrote since the last pass, and report ANY server
error — whatever it is, whether or not anyone thought to probe for it.  That is
the difference between finding the bug you went looking for and finding the one
you did not know about.

What it counts as an error:
  • any 5xx an HTTP service logged
  • any Python traceback / "Exception in ASGI application"
  • a transaction that got poisoned ("current transaction is aborted"), which is
    how one broken route takes an unrelated background worker down with it

4xx is deliberately NOT an error here: 401/404 are ordinary answers.  A 4xx
STORM is a different matter and is reported separately, because thousands of
404s on one route means something is asking for what does not exist.

It reads only the TAIL that is new since the previous run, so a 200 MB log costs
nothing, and it groups by signature so one broken route is one finding rather
than five hundred.
"""

import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGDIR = os.path.join(ROOT, "logs")
STATE = os.path.join(HERE, "log_watch_state.json")

# Services worth reading.  Collector logs are excluded on purpose: they are
# per-line chatter and already have their own checks.
WATCH = ["MES-API.log", "CMS-API.log"]

# Two folders hold a file by these names and WHICH ONE IS LIVE CHANGES: a
# restart via Phase2/restart_api.py points the API at Phase2/logs/, while the
# usual launcher writes to logs/.  Watching the wrong one makes this check
# report "clean" while 502s pour into the other file — which is exactly what it
# did the first time.  So never trust a fixed path: ask the running processes
# which file they actually hold open, and fall back to whichever copy was
# written most recently.
CANDIDATE_DIRS = [os.path.join(ROOT, "logs"), os.path.join(ROOT, "Phase2", "logs")]


def _live_logs():
    """Resolve each watched name to the file the running services write NOW."""
    open_files = set()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            fddir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fddir):
                    try:
                        tgt = os.readlink(os.path.join(fddir, fd))
                    except OSError:
                        continue
                    if tgt.endswith(".log"):
                        open_files.add(tgt)
            except OSError:
                continue
    except OSError:
        pass

    out = []
    for name in WATCH:
        held = [f for f in open_files if os.path.basename(f) == name]
        if held:
            out.append(max(held, key=lambda f: os.path.getsize(f) if os.path.exists(f) else 0))
            continue
        # nobody admits to holding it — take the most recently written copy
        cands = [os.path.join(d, name) for d in CANDIDATE_DIRS
                 if os.path.exists(os.path.join(d, name))]
        if cands:
            out.append(max(cands, key=os.path.getmtime))
    return out

MAX_TAIL = 8 * 1024 * 1024          # never read more than 8 MB in one pass

_5XX = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE) ([^ ?"]+)[^"]*" (5\d\d)')
_4XX = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE) ([^ ?"]+)[^"]*" (4\d\d)')
_EXC = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)): ", re.M)
_ABORTED = "current transaction is aborted"
_ASGI = "Exception in ASGI application"

# /api/lines/12/cycle-video and /api/lines/19/cycle-video are the same route.
_NUM = re.compile(r"/\d+")


def _p(sev, area, what, why, fix):
    return {"sev": sev, "area": area, "what": what, "why": why, "fix": fix}


def _load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(st):
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh)
        os.replace(tmp, STATE)
    except Exception:
        pass


def _new_text(path, st):
    """Return only what was appended since the last pass."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", None
    prev = st.get(path, {})
    start = prev.get("size", 0)
    # Rotation (copytruncate) makes the file shorter than we left it.
    if start > size:
        start = 0
    if start == size:
        return "", {"size": size}
    start = max(start, size - MAX_TAIL)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(start)
            return fh.read(), {"size": size}
    except OSError:
        return "", {"size": size}


def no_backend_errors(base=None, token=None, root=None, **_):
    """Read what the services logged since last time; report every 5xx,
    traceback and poisoned transaction."""
    out = []
    st = _load_state()
    paths = _live_logs()
    if not paths:
        return [_p("warning", "Agent", "Koi live log file nahi mili",
                   " / ".join(CANDIDATE_DIRS),
                   "MES-API / CMS-API chal rahe hain ya nahi, wo dekho")]

    for path in paths:
        name = os.path.basename(path)
        text, newstate = _new_text(path, st)
        if newstate:
            st[path] = newstate
        if not text:
            continue

        # ── 5xx, grouped by route ───────────────────────────────────────
        hits = Counter()
        for route, code in _5XX.findall(text):
            hits[(_NUM.sub("/{id}", route), code)] += 1
        for (route, code), n in hits.most_common(6):
            out.append(_p("critical", "Backend error",
                          f"{name}: {route} ne {n} baar HTTP {code} diya",
                          "server error hai — operator ko page/khaali data dikhta hai",
                          f"MES-API log me is route ka traceback dekho ({name})"))

        # ── tracebacks, grouped by exception type ───────────────────────
        exc = Counter(_EXC.findall(text))
        for kind, n in exc.most_common(5):
            out.append(_p("critical", "Backend error",
                          f"{name}: {kind} {n} baar",
                          "handler crash kar raha hai, jawab galat ya adhoora jayega",
                          "traceback ke last frame se route pakdo"))
        n_asgi = text.count(_ASGI)
        if n_asgi and not exc:
            out.append(_p("critical", "Backend error",
                          f"{name}: {n_asgi} ASGI exception",
                          "request handler beech me toota",
                          "log me 'Exception in ASGI application' ke aage ka traceback"))

        # ── poisoned transactions ───────────────────────────────────────
        n_abort = text.count(_ABORTED)
        if n_abort:
            out.append(_p("critical", "Backend error",
                          f"{name}: {n_abort} baar transaction aborted",
                          "ek route ki galti se DUSRE background worker mar rahe hain "
                          "— asli wajah usse pehle wali query hai",
                          "us connection par pehle kaunsi query failed hui, wo dekho"))

        # ── a 4xx STORM is not an error but is not normal either ────────
        four = Counter()
        for route, code in _4XX.findall(text):
            four[_NUM.sub("/{id}", route)] += 1
        for route, n in four.most_common(3):
            if n >= 500:
                out.append(_p("warning", "Backend error",
                              f"{name}: {route} par {n} baar 4xx",
                              "itni baar 'nahi mila' ka matlab hai koi wo maang raha hai "
                              "jo hai hi nahi",
                              "kaun call kar raha hai aur kyun, wo dekho"))

    _save_state(st)
    return out


if __name__ == "__main__":
    found = no_backend_errors()
    if not found:
        print("log watch: clean (no new backend errors since last pass)")
    for f in found:
        print(f"[{f['sev']}] {f['what']}\n    {f['why']}")
