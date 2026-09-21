#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# pm_agent.py — MES "Product Manager" audit agent
#
# Operator ask: "ek agent bana for product manager role jo mere is pure platform
# ko login kare har id se, monitor kare kitna sahi hai kitna galat, aur ye sab
# gap mujhe 0 karne hain — aur alert ke bhi capable ho."
#
# WHAT IT DOES (read-only, hourly)
#   1. EVERY USER      — for each active mes_admin account, resolve what that
#                        account can actually reach (/api/auth/me) and flag the
#                        ones that would land on a dead end (no pages at all,
#                        no lines assigned) — the "ss blank screen" class of bug.
#   2. EVERY PAGE      — call the real API each page depends on and record
#                        status + latency; anything non-200 or slow is a gap.
#   3. DATA SANITY     — business rules that silently rot: a LINE shown as a
#                        number instead of its name, leaders with no lines,
#                        lines whose video is dead, a running line reporting
#                        nothing, the shift/midnight date handling.
#   4. SYSTEM HEALTH   — API / CMS / DB latency, CMS thread pile-up, clip
#                        archive coverage.
#   5. VIDEO           — clip coverage, cameras, recorders, shift-change drops,
#                        archiver capacity, disk.  Rules and their thresholds,
#                        causes and suggested actions live in video_rules.json,
#                        so the agent is retrained by editing that file.
#
# ALERTS
#   New or newly-cleared gaps are pushed into the operator's own MES Inbox
#   (mes_push_inbox via routers.push.send_to_user), tagged 'pm_audit'.  State is
#   remembered between runs so a standing problem is NOT re-alerted every hour —
#   it alerts when it APPEARS and again when it CLEARS.
#
# SAFETY — enforced, not just promised.  The agent has NO permission to change,
# stop or restart anything:
#   • Database: every connection is opened read-only by the server
#     (default_transaction_read_only) and every query passes a SELECT-only guard.
#   • HTTP: the session refuses any method other than GET / HEAD.
#   • No shell, no process control, no file deletes.  It writes only its own
#     files in this folder, plus the Inbox alert.
#   • Self-check: before every run it parses its own source and refuses to run
#     if a forbidden call appears (process control, shell, HTTP writes, …).
#   • No passwords are read or sent — tokens are minted server-side.
#
#   Run once:   python3 pmagent/pm_agent.py --once
#   15-min run (video + collectors): python3 pmagent/pm_agent.py --once --video-only
#   Test run:   add --dry-run  (no alerts, no state/history, status.dryrun.json)
#   Live view:  http://<host>:8098/   (dashboard.html)
# ─────────────────────────────────────────────────────────────────────────────
import argparse, csv, json, os, re, statistics, sys, threading, time
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("MES_BACKGROUND", "0")   # never join the API's background election

import psycopg2
import psycopg2.extras
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))

BASE        = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
CMS_BASE    = os.environ.get("CMS_BASE", "http://127.0.0.1:5555")
STATUS_PATH = os.path.join(HERE, "status.json")
DRYRUN_PATH = os.path.join(HERE, "status.dryrun.json")
STATE_PATH  = os.path.join(HERE, "alert_state.json")
RULES_PATH  = os.path.join(HERE, "video_rules.json")
HISTORY_PATH = os.path.join(HERE, "video_history.json")
OWN_FILES   = {"status.json", "status.dryrun.json", "alert_state.json", "video_history.json"}
CLIPS_ROOT  = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/clips"
VIDEO_ROOT  = "/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data"
VIDEOS_DIR  = os.path.join(VIDEO_ROOT, "videos")
CMS_SHIFTS  = os.path.join(ROOT, "..", "New folder (2)", "New folder (2)", "backend", "shifts.json")
MATURE_MIN  = 45        # same as the tracker: a cycle is judged 45 min after it ends

SLOW_MS = 2000          # anything slower than this is a warning
DB = dict(dbname="energydb", user="postgres", password="tbdi@123",
          host="127.0.0.1", port=5432)

# Endpoints that are heavy or produce files — never called by the audit.
SKIP_RE = re.compile(r"(export|template|\.csv|shift-excel|shift-pdf|/video|discover|"
                     r"binfilling|dev-action|vapid|auto-login)", re.I)

# ── safety guards ────────────────────────────────────────────────────────────
_SQL_START = re.compile(r"^\s*(SELECT|WITH)\b", re.I)
_SQL_BAD = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE|COPY|"
    r"VACUUM|ANALYZE|REINDEX|CLUSTER|LOCK|SET|RESET|BEGIN|COMMIT|ROLLBACK|CALL|DO|"
    r"NOTIFY|LISTEN)\b|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|set_config|"
    r"advisory|lo_import|lo_export|lo_unlink|dblink|pg_read_file|pg_ls_dir|nextval|setval",
    re.I)


def _sql_problem(sql):
    """Why this SQL is not allowed for a read-only agent, or '' if it is fine."""
    s = re.sub(r"'[^']*'", "''", str(sql))          # ignore text inside literals
    if not _SQL_START.match(s):
        return "not a SELECT"
    m = _SQL_BAD.search(s)
    return f"contains {m.group(0)}" if m else ""


class _ReadOnlyCursor(psycopg2.extras.RealDictCursor):
    def execute(self, query, vars=None):
        why = _sql_problem(query)
        if why:
            raise PermissionError(f"read-only agent: query refused ({why})")
        return super().execute(query, vars)


class _GetOnlySession(requests.Session):
    """HTTP session that can only read."""
    def request(self, method, url, *args, **kwargs):
        if str(method).upper() not in ("GET", "HEAD"):
            raise PermissionError(f"read-only agent: HTTP {method} refused")
        return super().request(method, url, *args, **kwargs)


def _write_own(path, obj):
    """The ONLY file write: the agent's own JSON files in its own folder."""
    path = os.path.abspath(path)
    if os.path.dirname(path) != HERE or os.path.basename(path) not in OWN_FILES:
        raise PermissionError(f"read-only agent: write to {path} refused")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def db():
    # The server itself enforces read-only for every transaction on this
    # connection; a statement timeout keeps a slow query from lingering.
    c = psycopg2.connect(**DB, options="-c default_transaction_read_only=on "
                                       "-c statement_timeout=20000")
    c.set_session(readonly=True, autocommit=True)
    return c


def dc(conn):
    return conn.cursor(cursor_factory=_ReadOnlyCursor)


def q(sql, params=None):
    """One read-only query on its own connection, closed afterwards."""
    with closing(db()) as c:
        cur = dc(c)
        cur.execute(sql, params)
        return list(cur.fetchall())


# Self-check: parse our own source and refuse to run if anything could change,
# stop or restart something.  Names are checked on calls and imports only, so
# plain text (comments, alert wording) never trips it.
_BAD_IMPORTS = {"subprocess", "signal", "shutil", "multiprocessing", "ctypes", "pty",
                "psutil", "socket", "asyncio", "paramiko", "pexpect", "telnetlib",
                "ftplib", "smtplib", "importlib"}
_BAD_OS = {"system", "popen", "kill", "killpg", "remove", "unlink", "rmdir", "removedirs",
           "rename", "renames", "truncate", "ftruncate", "chmod", "chown", "symlink", "link",
           "mkdir", "makedirs", "fork", "forkpty", "abort", "_exit", "setuid", "setgid",
           "spawnl", "spawnle", "spawnlp", "spawnv", "spawnve", "spawnvp", "execl",
           "execle", "execlp", "execv", "execve", "execvp", "execvpe", "replace"}
_BAD_METHODS = {"post", "put", "patch", "delete", "kill", "terminate", "send_signal",
                "restart", "reboot", "shutdown", "Popen", "check_output", "check_call"}
_BAD_BUILTINS = {"exec", "eval", "compile", "__import__", "breakpoint"}
_ONLY_IN = {"os.replace": {"_write_own"}, "dump": {"_write_own"},
            "request": {"_GetOnlySession"}, "send_to_user": {"_send_alerts"}}
_SHELL_BAD = re.compile(r"\b(systemctl|kill|pkill|killall|restart|reboot|shutdown|rm|mv|"
                        r"sudo|service|crontab|curl|wget)\b")


def self_check():
    import ast
    problems = []
    try:
        tree = ast.parse(open(os.path.abspath(__file__), encoding="utf-8").read())
    except Exception as e:
        return [f"cannot read own source: {e}"]

    def _const(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
        return None

    class V(ast.NodeVisitor):
        def __init__(self):
            self.ctx = []

        def _scoped(self, node):
            self.ctx.append(node.name)
            self.generic_visit(node)
            self.ctx.pop()
        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

        def _mod(self, name, node):
            root = name.split(".")[0]
            if root in _BAD_IMPORTS or (root == "routers" and name != "routers.push"):
                problems.append(f"line {node.lineno}: import {name}")

        def visit_Import(self, node):
            for n in node.names:
                self._mod(n.name, node)

        def visit_ImportFrom(self, node):
            self._mod(node.module or "", node)
            for n in node.names:
                if (node.module or "") == "os" and n.name in _BAD_OS:
                    problems.append(f"line {node.lineno}: from os import {n.name}")

        def visit_Call(self, node):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            key = f"os.{name}" if owner == "os" else name
            inside = set(self.ctx)
            if key in _ONLY_IN:
                if not inside & _ONLY_IN[key]:
                    problems.append(f"line {node.lineno}: {key}() outside {'/'.join(_ONLY_IN[key])}")
            elif owner == "os" and name in _BAD_OS:
                problems.append(f"line {node.lineno}: os.{name}()")
            elif name in _BAD_METHODS or (isinstance(f, ast.Name) and name in _BAD_BUILTINS):
                problems.append(f"line {node.lineno}: {name}()")
            if name == "open" and "_write_own" not in inside:
                mode = node.args[1] if len(node.args) > 1 else \
                    next((k.value for k in node.keywords if k.arg == "mode"), None)
                ms = _const(mode) if mode is not None else "r"
                if ms is None or set(ms) & set("wax+"):
                    problems.append(f"line {node.lineno}: open() for writing")
            if name == "execute" and node.args:
                sql = _const(node.args[0])
                if sql is not None and _sql_problem(sql):
                    problems.append(f"line {node.lineno}: SQL {_sql_problem(sql)}")
            self.generic_visit(node)

    V().visit(tree)
    try:
        for i, ln in enumerate(open(os.path.join(HERE, "run_audit.sh")), 1):
            code = ln.split("#", 1)[0]
            m = _SHELL_BAD.search(code)
            if m:
                problems.append(f"run_audit.sh line {i}: {m.group(0)}")
    except Exception:
        pass
    return problems


def mint(username, role, uid):
    from auth import create_token          # type: ignore
    return create_token(username, role, uid)


def discover_routes():
    """Param-free GET routes, read from the router source so the audit follows
    the code instead of a list that goes stale."""
    out = []
    rd = os.path.join(ROOT, "Phase2", "routers")
    for fn in sorted(os.listdir(rd)):
        if not fn.endswith(".py"):
            continue
        try:
            src = open(os.path.join(rd, fn), encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        m = re.search(r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)', src)
        prefix = m.group(1) if m else ""
        for mm in re.finditer(r'@router\.get\(\s*["\']([^"\']*)["\']', src):
            p = prefix + mm.group(1)
            if "{" in p or SKIP_RE.search(p):
                continue
            out.append(p)
    return sorted(set(out))


class Audit:
    def __init__(self):
        self.gaps = []          # {id, sev, area, what, detail[, cause, action]}
        self.muted = []
        self.checks = 0
        self.t0 = time.time()

    def gap(self, gid, sev, area, what, detail="", cause="", action=""):
        g = {"id": gid, "sev": sev, "area": area, "what": what, "detail": detail}
        if cause or action:
            g["cause"], g["action"] = cause, action
        self.gaps.append(g)

    def ok(self):
        self.checks += 1


# ── 1. every user ────────────────────────────────────────────────────────────
def check_users(a, s):
    users, rows = [], []
    with db() as c:
        cur = dc(c)
        cur.execute("SELECT id, username, role FROM mes_admin ORDER BY id")
        rows = cur.fetchall()
    for u in rows:
        a.ok()
        tok = mint(u["username"], u["role"], u["id"])
        rec = {"id": u["id"], "user": u["username"], "role": u["role"],
               "pages": 0, "lines": 0, "ms": None, "state": "ok"}
        try:
            t = time.time()
            r = s.get(f"{BASE}/api/auth/me", headers={"Authorization": f"Bearer {tok}"},
                      timeout=25)
            rec["ms"] = int((time.time() - t) * 1000)
            if r.status_code != 200:
                rec["state"] = f"HTTP {r.status_code}"
                a.gap(f"user:{u['id']}:me", "critical", "User",
                      f"{u['username']} — /auth/me returns {r.status_code}")
                users.append(rec); continue
            me = r.json()
            perms = me.get("permissions") or {}
            granted = [k for k, v in perms.items() if v in ("read", "full")]
            rec["pages"] = len(granted)
            rec["lines"] = len(me.get("assigned_lines") or [])
            # A non-admin with grants but no landing page is the blank-screen bug.
            if u["role"] not in ("admin", "plant_head"):
                if granted and "dashboard" not in granted:
                    rec["state"] = "no dashboard"
                    a.gap(f"user:{u['id']}:nodash", "info", "User",
                          f"{u['username']} has no Dashboard grant",
                          f"lands on '{granted[0]}' instead — by design if intended")
                if rec["lines"] == 0 and u["role"] in ("operator", "leader",
                                                       "shift_incharge", "production"):
                    rec["state"] = "no lines"
                    a.gap(f"user:{u['id']}:nolines", "warning", "User",
                          f"{u['username']} ({u['role']}) has NO lines assigned",
                          "their dashboard will be empty — assign in Admin → Users → Assign Lines")
            if rec["ms"] and rec["ms"] > SLOW_MS:
                a.gap(f"user:{u['id']}:slow", "warning", "User",
                      f"{u['username']} login check slow", f"{rec['ms']}ms")
        except Exception as e:
            rec["state"] = f"ERR {type(e).__name__}"
            a.gap(f"user:{u['id']}:err", "critical", "User",
                  f"{u['username']} — auth failed", str(e)[:90])
        users.append(rec)
    return users


# ── 2. every page's API ──────────────────────────────────────────────────────
def check_endpoints(a, s, routes):
    tok = mint("admin", "admin", 1)
    h = {"Authorization": f"Bearer {tok}"}
    out = []
    # Paced + confirmed.  Firing 150 requests back-to-back made a single genuine
    # 500 knock over the NEXT request too, so the audit reported 19 "critical"
    # endpoints when only 3 were actually broken — an agent that cries wolf is
    # worse than no agent.  Small gap between calls, and any failure is RETRIED
    # once before it is allowed to become a gap.
    def _hit(path):
        t = time.time()
        r = s.get(BASE + path, headers=h, timeout=30)
        return r.status_code, int((time.time() - t) * 1000)

    for p in routes:
        a.ok()
        rec = {"path": p, "status": 0, "ms": None}
        status = ms = None
        err = None
        for attempt in (1, 2):
            try:
                status, ms = _hit(p)
                err = None
                if status < 500:
                    break                      # good enough, no retry needed
            except Exception as e:
                err = e
                status, ms = 0, None
            if attempt == 1:
                time.sleep(1.0)                # let the API breathe, then confirm
        rec["status"], rec["ms"] = status or 0, ms
        if err is not None:
            rec["note"] = type(err).__name__
            a.gap(f"ep:{p}:err", "critical", "Page/API",
                  f"{p} failed twice", str(err)[:90])
        elif status >= 500:
            a.gap(f"ep:{p}", "critical", "Page/API",
                  f"{p} → HTTP {status} (confirmed twice)")
        elif status in (400, 404, 422):
            rec["note"] = "needs params"       # not a fault: these want query args
        elif ms and ms > SLOW_MS:
            a.gap(f"ep:{p}:slow", "warning", "Page/API",
                  f"{p} is slow", f"{ms}ms (>{SLOW_MS}ms)")
        out.append(rec)
        time.sleep(0.15)                       # pacing — never hammer production
    return out


# ── 3. data sanity ───────────────────────────────────────────────────────────
def check_data(a, s):
    tok = mint("admin", "admin", 1)
    h = {"Authorization": f"Bearer {tok}"}
    notes = []

    # 3a. every line must expose a NAME, never a bare number
    try:
        a.ok()
        r = s.get(f"{BASE}/api/lines/", headers=h, timeout=25)
        lines = r.json() if r.status_code == 200 else []
        bad = [l for l in lines
               if not str(l.get("line_name") or "").strip()
               or re.fullmatch(r"(?i)line\s*#?\d+", str(l.get("line_name") or "").strip())]
        if bad:
            a.gap("data:linename", "critical", "Data",
                  f"{len(bad)} line(s) have no proper name",
                  ", ".join(str(l.get("id")) for l in bad[:8]))
        notes.append(f"lines: {len(lines)}")
    except Exception as e:
        a.gap("data:lines", "critical", "Data", "/api/lines/ unreadable", str(e)[:90])

    # 3b. leaders must have lines, else their dashboard is blank
    try:
        a.ok()
        r = s.get(f"{BASE}/api/leaders", headers=h, timeout=25)
        if r.status_code == 200:
            lds = r.json().get("leaders", [])
            nol = [l for l in lds if not l.get("line_count")]
            if nol:
                a.gap("data:leaderlines", "warning", "Data",
                      f"{len(nol)} of {len(lds)} leaders have NO lines assigned",
                      ", ".join(l["username"] for l in nol[:8]))
            notes.append(f"leaders: {len(lds)}")
    except Exception:
        pass

    # 3c. (removed) opening a recent clip per line made the server cut clips on
    # demand every hour.  Video is now judged from the Video Coverage data in
    # check_video() below, which adds no video load.

    # 3d. clip archive coverage — low coverage means slow video for operators
    try:
        a.ok()
        cov_have = cov_tot = 0
        with db() as c:
            cur = dc(c)
            cur.execute("""SELECT l.id, l.db_table_name FROM mes_lines l
                            WHERE COALESCE(l.is_active,TRUE)
                              AND l.db_table_name IS NOT NULL AND l.db_table_name <> ''""")
            lines_c = list(cur.fetchall())
        for ln in lines_c:
            t = ln["db_table_name"]
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t):
                continue
            try:
                with db() as c3:                 # own connection per line, so a
                    cur3 = dc(c3)                # missing ct_log cannot abort the sweep
                    cur3.execute(f"""SELECT cycle_seq, record_date, shift_name
                                       FROM {t}_ct_log
                                      WHERE ts > now() - interval '10 min'
                                        AND ts < now() - interval '30 sec'""")
                    rows = cur3.fetchall()
            except Exception:
                continue
            for r2 in rows:
                cov_tot += 1
                p = os.path.join(CLIPS_ROOT, str(r2["record_date"]),
                                 f"line_{ln['id']}", r2["shift_name"] or "A",
                                 "main", f"cycle_{r2['cycle_seq']}.mp4")
                if os.path.exists(p):
                    cov_have += 1
        pct = int(cov_have * 100 / max(cov_tot, 1))
        notes.append(f"clip coverage {pct}%")
        if cov_tot and pct < 50:
            a.gap("data:coverage", "warning", "Video",
                  f"clip pre-render coverage only {pct}%",
                  f"{cov_have}/{cov_tot} recent cycles ready — opens will render on demand")
    except Exception:
        pass
    return notes


# ── 4. system health ─────────────────────────────────────────────────────────
def check_system(a, s):
    tok = mint("admin", "admin", 1)
    h = {"Authorization": f"Bearer {tok}"}
    sysinfo = {}
    for name, url in (("MES-API", f"{BASE}/api/lines/"),
                      ("CMS", f"{CMS_BASE}/api/cameras/health")):
        a.ok()
        try:
            t = time.time()
            r = s.get(url, headers=h, timeout=20)
            ms = int((time.time() - t) * 1000)
            sysinfo[name] = {"ms": ms, "status": r.status_code}
            if ms > 3000:
                a.gap(f"sys:{name}:slow", "warning", "System",
                      f"{name} slow to answer", f"{ms}ms")
        except Exception as e:
            sysinfo[name] = {"ms": None, "status": 0}
            a.gap(f"sys:{name}:down", "critical", "System",
                  f"{name} not answering", str(e)[:80])
    try:
        load = os.getloadavg()
        cores = os.cpu_count() or 1
        sysinfo["load"] = round(load[0], 1)
        sysinfo["cores"] = cores
        if load[0] > cores:
            a.gap("sys:load", "warning", "System",
                  f"CPU load {load[0]:.0f} above {cores} cores")
    except Exception:
        pass
    # CMS thread pile-up (the wedge signature)
    try:
        pid = _pid_listening(5555)
        if pid:
            n = len(os.listdir(f"/proc/{pid}/task"))
            sysinfo["cms_threads"] = n
            if n > 500:
                a.gap("sys:cmsthreads", "critical", "System",
                      f"CMS thread pile-up: {n}", "video will wedge — restart CMS")
    except Exception:
        pass
    return sysinfo


def _pid_listening(port):
    """PID of the process listening on a TCP port, read from /proc (no shell)."""
    tail = f":{port:04X}"
    inodes = set()
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for ln in open(fn).read().splitlines()[1:]:
                p = ln.split()
                if len(p) > 9 and p[1].endswith(tail) and p[3] == "0A":
                    inodes.add(p[9])
        except Exception:
            pass
    if not inodes:
        return None
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if not open(f"/proc/{pid}/comm").read().startswith("python"):
                continue
            for fd in os.listdir(f"/proc/{pid}/fd"):
                ln = os.readlink(f"/proc/{pid}/fd/{fd}")
                if ln.startswith("socket:[") and ln[8:-1] in inodes:
                    return pid
        except Exception:
            continue
    return None


# ── 5. video ─────────────────────────────────────────────────────────────────
_REASONS = {
    "no_camera": "no camera assigned", "cms_down": "CMS down",
    "network_down": "network down", "camera_offline": "camera offline",
    "camera_hung": "camera hung", "shift_wipe": "footage deleted at shift change",
    "clip_failed": "clip not cut (camera was recording)", "unknown": "not tracked",
}
# reason of a missing clip -> cause text in video_rules.json
_REASON_CAUSE = {
    "no_camera": "fi_no_camera", "cms_down": "cms_down", "network_down": "segment_down",
    "camera_offline": "camera_offline", "camera_hung": "camera_hung",
    "shift_wipe": "shift_change_loss", "clip_failed": "archiver_capacity",
    "unknown": "tracker_stale",
}
_TS_NAME = re.compile(r"^cam_(.+)_(\d{13})\.ts$")


def load_rules():
    """video_rules.json — thresholds, causes/actions, mute list."""
    try:
        r = json.load(open(RULES_PATH, encoding="utf-8"))
    except Exception as e:
        print(f"[pm-agent] video_rules.json unreadable ({e}) — video checks skipped", flush=True)
        return None
    r.setdefault("thresholds", {})
    r.setdefault("causes", {})
    r.setdefault("mute", [])
    return r


def _recorders():
    """Camera recorders seen from /proc: ffmpeg processes holding a camera .ts
    open for WRITING (clip renders open it read-only).  Reads fd links only —
    never a command line, which would hold camera credentials."""
    rec = {}            # camera_id -> [(pid, path, deleted)]
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if open(f"/proc/{pid}/comm").read().strip() != "ffmpeg":
                continue
            fds = os.listdir(f"/proc/{pid}/fd")
        except Exception:
            continue
        for fd in fds:
            try:
                p = os.readlink(f"/proc/{pid}/fd/{fd}")
            except Exception:
                continue
            deleted = p.endswith(" (deleted)")
            path = p[:-10] if deleted else p
            if not (path.startswith(VIDEOS_DIR + "/cam_") and path.endswith(".ts")):
                continue
            try:
                m = re.search(r"flags:\s*(\d+)", open(f"/proc/{pid}/fdinfo/{fd}").read())
                if not m or int(m.group(1), 8) & 3 == 0:
                    continue
            except Exception:
                continue
            nm = _TS_NAME.match(os.path.basename(path))
            if nm:
                rec.setdefault(nm.group(1), []).append((int(pid), path, deleted))
    return rec


def _median_baseline(samples, now_ts, th):
    """Normal level for a zone: median of the same time of day (±1 h) when
    there are enough samples, else of everything kept; None while learning."""
    keep = [s for s in samples if now_ts - s[0] <= th.get("baseline_days", 7) * 86400]
    tod = lambda t: (t + 19800) % 86400                          # IST seconds of day
    near = [s[1] for s in keep if min(abs(tod(s[0]) - tod(now_ts)),
                                      86400 - abs(tod(s[0]) - tod(now_ts))) <= 3600]
    if len(near) >= th.get("baseline_same_time_samples", 6):
        return statistics.median(near), len(keep)
    if len(keep) >= th.get("baseline_min_samples", 12):
        return statistics.median(s[1] for s in keep), len(keep)
    return None, len(keep)


def check_video(a, rules, learn=True):
    th, causes = rules["thresholds"], rules["causes"]
    now = datetime.now().astimezone()
    v = {"ran_at": now.strftime("%Y-%m-%d %H:%M:%S")}

    def vgap(gid, sev, what, detail="", cause_key=""):
        c = causes.get(cause_key) or {}
        a.gap(gid, sev, "Video", what, detail, c.get("cause", ""), c.get("action", ""))

    # V1. the tracker itself is alive (watch the watcher)
    a.ok()
    try:
        r = q("""SELECT last_sample, last_eval, last_agent, COALESCE(last_error,'') AS last_error,
                        now() AS now FROM mes_vcov_agent WHERE id = 1""")
    except Exception as e:
        r = []
        vgap("video:tracker:unreadable", "critical", "Video Coverage data unreadable",
             str(e)[:100], "tracker_stale")
    v["tracker"] = {}
    if r:
        r = r[0]
        for key, col, lim, sev, label in (
                ("sample", "last_sample", th.get("tracker_sample_stale_min", 5), "critical", "camera check"),
                ("eval", "last_eval", th.get("tracker_eval_stale_min", 10), "critical", "cycle check"),
                ("agent", "last_agent", th.get("tracker_agent_stale_min", 15), "warning", "Video Agent")):
            m = (r["now"] - r[col]).total_seconds() / 60 if r[col] else None
            v["tracker"][key] = round(m, 1) if m is not None else None
            if m is None or m > lim:
                vgap(f"video:tracker:{key}", sev, f"Video Coverage {label} has stopped",
                     f"last run {m:.0f} min ago" if m is not None else "never ran", "tracker_stale")
        if r["last_error"]:
            vgap("video:tracker:error", "warning", "Video Coverage tracker reports an error",
                 r["last_error"][:120], "tracker_error")

    # V2/V3. coverage over the last judged hour (same window as the tracker)
    win = (MATURE_MIN + 75, MATURE_MIN)
    a.ok()
    cov = q("""SELECT h.line_id, l.line_name, COALESCE(z.zone_name, 'Unzoned') AS zone,
                      (h.machine_key = 'main') AS fi,
                      sum(h.cycles)::int AS n, sum(h.clips)::int AS c
                 FROM mes_vcov_hourly h
                 JOIN mes_lines l ON l.id = h.line_id
                 LEFT JOIN mes_zones z ON z.id = l.zone_id
                WHERE h.hour_ts >= now() - make_interval(mins => %s)
                  AND h.hour_ts <  now() - make_interval(mins => %s)
                GROUP BY 1, 2, 3, 4""", win)
    why = q("""SELECT m.line_id, (m.machine_key = 'main') AS fi, m.reason, count(*)::int AS n
                 FROM mes_vcov_missing m
                WHERE m.ts_end >= now() - make_interval(mins => %s)
                  AND date_trunc('hour', m.ts_start) >= now() - make_interval(mins => %s)
                  AND date_trunc('hour', m.ts_start) <  now() - make_interval(mins => %s)
                GROUP BY 1, 2, 3""", (win[0] + 60, win[0], win[1]))
    reasons, by_line = {}, {}
    for w in why:
        reasons[w["reason"]] = reasons.get(w["reason"], 0) + w["n"]
        by_line.setdefault((w["line_id"], w["fi"]), {})[w["reason"]] = w["n"]

    fi_rows, zones = [], {}
    for c in cov:
        if c["fi"]:
            pct = c["c"] * 100.0 / c["n"] if c["n"] else 100.0
            fi_rows.append({"line_id": c["line_id"], "line": c["line_name"], "zone": c["zone"],
                            "cycles": c["n"], "clips": c["c"], "pct": round(pct, 1)})
        else:
            z = zones.setdefault(c["zone"], {"zone": c["zone"], "cycles": 0, "clips": 0})
            z["cycles"] += c["n"]
            z["clips"] += c["c"]
    fi_rows.sort(key=lambda x: x["pct"])
    for f in fi_rows:
        a.ok()
        if f["cycles"] < th.get("fi_min_cycles", 20) or f["pct"] >= th.get("fi_critical_pct", 90):
            continue
        rs = by_line.get((f["line_id"], True)) or {}
        top = max(rs, key=rs.get) if rs else "unknown"
        vgap(f"video:fi:{f['line_id']}", "info" if top == "no_camera" else "critical",
             f"{f['line']} Final Inspection video {f['pct']:.0f}% ({f['clips']}/{f['cycles']})",
             f"main reason: {_REASONS.get(top, top)} ({rs.get(top, 0)} of {sum(rs.values())} missing)",
             _REASON_CAUSE.get(top, ""))
    v["fi"] = fi_rows
    fi_n = sum(f["cycles"] for f in fi_rows)
    fi_c = sum(f["clips"] for f in fi_rows)
    sub_n = sum(z["cycles"] for z in zones.values())
    sub_c = sum(z["clips"] for z in zones.values())
    v["fi_pct"] = round(fi_c * 100.0 / fi_n, 1) if fi_n else None
    v["sub_pct"] = round(sub_c * 100.0 / sub_n, 1) if sub_n else None
    v["totals"] = {"fi_cycles": fi_n, "fi_clips": fi_c, "sub_cycles": sub_n, "sub_clips": sub_c}

    try:
        hist = json.load(open(HISTORY_PATH)) if os.path.exists(HISTORY_PATH) else {}
    except Exception:
        hist = {}
    hz = hist.setdefault("zone", {})
    now_ts = int(time.time())
    zone_out = []
    for z in sorted(zones.values(), key=lambda x: -x["cycles"]):
        a.ok()
        pct = z["clips"] * 100.0 / z["cycles"] if z["cycles"] else 100.0
        base, nsamp = _median_baseline(hz.get(z["zone"], []), now_ts, th)
        z.update(pct=round(pct, 1), baseline=None if base is None else round(base, 1),
                 samples=nsamp)
        zone_out.append(z)
        if z["cycles"] < th.get("zone_min_cycles", 100):
            continue
        if z["clips"] == 0:
            vgap(f"video:zone0:{z['zone']}", "warning",
                 f"{z['zone']}: no sub-machine clips at all",
                 f"{z['cycles']} cycles in the last judged hour, 0 clips", "zone_no_clips")
        elif base is not None and pct < base - th.get("zone_drop_points", 15):
            vgap(f"video:zone:{z['zone']}", "warning",
                 f"{z['zone']} sub-machine video {pct:.0f}%, normal is {base:.0f}%",
                 f"{z['clips']}/{z['cycles']} in the last judged hour", "zone_low")
        if learn:
            hz.setdefault(z["zone"], []).append([now_ts, round(pct, 1), z["cycles"]])
    v["zones"] = zone_out
    if learn:
        keep_s = (th.get("baseline_days", 7) + 1) * 86400
        for k in list(hz):
            hz[k] = [s for s in hz[k] if now_ts - s[0] <= keep_s][-2000:]
        _write_own(HISTORY_PATH, hist)

    # V4. cameras: network segments, and needed cameras that are down
    a.ok()
    cams = q("""SELECT DISTINCT ON (camera_id) camera_id, ip, state, from_ts
                  FROM mes_vcov_cam_state
                 WHERE to_ts > now() - interval '3 minutes'
                 ORDER BY camera_id, to_ts DESC""")
    state = {c["camera_id"]: c for c in cams}
    counts = {}
    seg = {}
    for c in cams:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
        net = ".".join(str(c["ip"] or "").split(".")[:3]) or "?"
        s_ = seg.setdefault(net, {"total": 0, "offline": 0, "since": None})
        s_["total"] += 1
        if c["state"] == "camera_offline":
            s_["offline"] += 1
            s_["since"] = min(filter(None, (s_["since"], c["from_ts"])))
    v["cameras"] = counts
    down_nets = set()
    for net, s_ in sorted(seg.items()):
        if s_["offline"] >= th.get("segment_min_cameras", 3) and \
                s_["offline"] * 100 >= th.get("segment_offline_pct", 80) * s_["total"]:
            down_nets.add(net)
            vgap(f"video:net:{net}", "critical", f"Network segment {net}.x down",
                 f"{s_['offline']} of {s_['total']} cameras offline since "
                 f"{s_['since'].astimezone():%H:%M}", "segment_down")
    for f in q("""SELECT kind, fkey, camera_id, message FROM mes_vcov_findings
                   WHERE closed_at IS NULL AND kind IN ('CAMERA_DOWN', 'CMS_DOWN')"""):
        a.ok()
        if f["kind"] == "CMS_DOWN":
            vgap("video:cms", "critical", "CMS is not answering", f["message"], "cms_down")
            continue
        st = state.get(f["camera_id"]) or {}
        net = ".".join(str(st.get("ip") or "").split(".")[:3])
        if net in down_nets:
            continue                    # already reported once as a segment
        vgap(f"video:cam:{f['camera_id']}", "warning", f["message"], "",
             "camera_hung" if st.get("state") == "camera_hung" else "camera_offline")
    down_cams = {g["id"][10:] for g in a.gaps if g["id"].startswith("video:cam:")}

    # V5. recorders (from /proc)
    a.ok()
    rec = _recorders()
    stale_s = th.get("recorder_stale_min", 3) * 60
    stuck = dup = gone = 0
    for cam, lst in sorted(rec.items()):
        if len(lst) > 1:
            dup += 1
            vgap(f"video:recdup:{cam}", "warning", f"Camera {cam} has {len(lst)} recorders",
                 f"pids {', '.join(str(x[0]) for x in lst)}", "recorder_duplicate")
        if any(x[2] for x in lst):
            gone += 1
            vgap(f"video:recdeleted:{cam}", "warning",
                 f"Recorder for camera {cam} is writing to a deleted file", "", "recorder_deleted_file")
            continue
        try:
            age = time.time() - max(os.stat(x[1]).st_mtime for x in lst)
        except Exception:
            continue
        if age > stale_s and cam not in down_cams:
            stuck += 1
            vgap(f"video:recstuck:{cam}", "warning", f"Recorder for camera {cam} is not writing",
                 f"footage file unchanged for {age / 60:.0f} min", "recorder_stuck")
    v["recorders"] = {"cameras": len(rec), "duplicates": dup, "stuck": stuck, "deleted_file": gone}

    # V6. why clips are missing
    miss = sum(reasons.values())
    failed = reasons.get("clip_failed", 0)
    v["reasons"] = dict(sorted(reasons.items(), key=lambda x: -x[1]))
    a.ok()
    if miss >= th.get("archiver_min_missing", 100) and \
            failed * 100 >= th.get("archiver_share_pct", 50) * miss:
        fi_f = sum(n.get("clip_failed", 0) for (lid, fi), n in by_line.items() if fi)
        vgap("video:archiver", "warning",
             f"Archiver is not keeping up: {failed * 100 // miss}% of missing clips were never cut",
             f"{failed} of {miss} missing in the last judged hour "
             f"(Final Inspection {fi_f}, sub-machines {failed - fi_f})", "archiver_capacity")
    a.ok()
    nocam = q("""SELECT l.line_name, m.machine_name, count(*)::int AS n
                   FROM mes_vcov_missing m JOIN mes_lines l ON l.id = m.line_id
                  WHERE m.reason = 'no_camera' AND m.ts_end > now() - interval '24 hours'
                  GROUP BY 1, 2 ORDER BY 3 DESC""")
    if nocam:
        vgap("video:nocam", "info", f"{len(nocam)} machines produce cycles but have no camera",
             ", ".join(f"{x['line_name']} · {x['machine_name']} ({x['n']})" for x in nocam[:5])
             + (f" … +{len(nocam) - 5} more" if len(nocam) > 5 else ""), "no_camera_machines")

    # V7. disk
    a.ok()
    try:
        st_ = os.statvfs(VIDEO_ROOT)
        free_tb = st_.f_bavail * st_.f_frsize / 1e12
        v["disk_free_tb"] = round(free_tb, 2)
        if free_tb < th.get("disk_crit_tb", 1.5):
            vgap("video:disk", "critical", f"Video disk almost full: {free_tb:.1f} TB free", "", "disk_low")
        elif free_tb < th.get("disk_warn_tb", 3):
            vgap("video:disk", "warning", f"Video disk low: {free_tb:.1f} TB free", "", "disk_low")
    except Exception:
        pass

    # V8. cameras lost at the last CMS shift change
    try:
        starts = [s_["start"] for s_ in json.load(open(CMS_SHIFTS)).get("shifts", [])]
    except Exception:
        starts = []
    cand = []
    for d in (0, 1):
        day = (now - timedelta(days=d)).date()
        for hm in starts:
            h_, m_ = map(int, hm.split(":"))
            t = datetime(day.year, day.month, day.day, h_, m_).astimezone()
            age = (now - t).total_seconds() / 60
            if th.get("shift_check_after_min", 15) <= age <= th.get("shift_check_window_min", 180):
                cand.append(t)
    v["shift"] = None
    if cand:
        a.ok()
        t = max(cand)
        b_t = t - timedelta(minutes=5)
        a_t = t + timedelta(minutes=th.get("shift_check_after_min", 15))
        r = q("""SELECT count(DISTINCT camera_id) FILTER (WHERE from_ts <= %(b)s + interval '90 seconds'
                                                           AND to_ts >= %(b)s - interval '90 seconds') AS before,
                        count(DISTINCT camera_id) FILTER (WHERE from_ts <= %(a)s + interval '90 seconds'
                                                           AND to_ts >= %(a)s - interval '90 seconds') AS after
                   FROM mes_vcov_cam_state
                  WHERE state = 'recording' AND to_ts >= %(b)s - interval '90 seconds'
                    AND from_ts <= %(a)s + interval '90 seconds'""", {"b": b_t, "a": a_t})[0]
        v["shift"] = {"start": f"{t:%H:%M}", "before": r["before"], "after": r["after"]}
        lost = (r["before"] or 0) - (r["after"] or 0)
        if r["before"] and lost >= th.get("shift_loss_cameras", 5):
            vgap(f"video:shift:{t:%Y-%m-%dT%H:%M}", "warning",
                 f"{lost} cameras stopped recording after the {t:%H:%M} shift change",
                 f"{r['before']} recording at {b_t:%H:%M}, {r['after']} at {a_t:%H:%M}",
                 "shift_change_loss")
    return v


def apply_mutes(a, rules):
    """Muted items stay visible but never alert."""
    today = date.today().isoformat()
    mutes = [m for m in (rules or {}).get("mute", [])
             if m.get("id") and (not m.get("until") or str(m["until"]) >= today)]
    if not mutes:
        return
    keep = []
    for g in a.gaps:
        m = next((m for m in mutes if g["id"].startswith(m["id"])), None)
        if m:
            a.muted.append(dict(g, muted_until=m.get("until"), note=m.get("note", "")))
        else:
            keep.append(g)
    a.gaps = keep


# ── 4. collectors (every 15 min) ─────────────────────────────────────────────
# Finds every collector that is down, hung or not delivering data.  Read-only:
# /proc, the tail of each collector log, and SELECTs.  General rules, not one
# probe per past incident — each rule catches a whole class:
#   running · log alive · DB row fresh · main count moves while the line makes
#   parts · no DB-write error storm · no restart loop · main PLC reachable ·
#   held count not far below what the PLC reads · sub-machines online and
#   counting like their siblings · one collector per line.
COLLECTORS_DIR = os.path.join(ROOT, "Phase2", "collectors")
LOGS_DIR = os.path.join(ROOT, "logs")
COL = {"tail_bytes": 2_000_000, "window_s": 900, "log_silent_s": 180, "db_stale_s": 300,
       "producing_parts": 5, "stuck_gap": 10, "noise_band": 60, "err_storm": 5,
       "reconnect_storm": 10, "restarts": 3, "plc_off_pct": 80, "sub_sibling_parts": 10}
_TBL = re.compile(r"^[a-z0-9_]+$")
_CFG_LINE = re.compile(r'"line_id"\s*:\s*(\d+)')
_CFG_TABLE = re.compile(r'"table_name"\s*:\s*"([a-z0-9_]+)"')
_ROW = re.compile(r"^(\d\d):(\d\d):(\d\d) (.+?)\s+\[(x| )\]\s+\[(x| )\]\s+(.*)$")
_NOISE = re.compile(r"^\[REG-NOISE\] (\S+): filtered \d+ garbage read\(s\).*?junk values (\d+)\.\.(\d+)")
_SUB_SEG = re.compile(r"(?=\[SUB \d+ )")
_SUB_OFF = re.compile(r"^\[SUB (\d+) [^\]]*\] still offline \([^,]*, (.*)$")
_SUB_ACT = re.compile(r"\[SUB (\d+) [^\]]*\] \[REG-MIRROR\] OK")
_NET = re.compile(r"timed out|unreachable|no route|refused|reset by peer|broken pipe|"
                  r"not connected|errno 1(01|10|11|13)", re.I)
_ERRS = (("hourly write", re.compile(r"\[HOURLY\] Write error: (.+)")),
         ("ct_log flush", re.compile(r"\[CT_LOG\] Flush error: (.+)")),
         ("traceback", re.compile(r"^Traceback \(most recent call last\)")),
         ("DB reconnect", re.compile(r"^\[DB\] Reconnected")),
         ("restart", re.compile(r"^=== collector_\S+ exited rc=")))


def _collector_files():
    """{collector name: (line_id, table)} from collectors/collector_*.py — the
    same glob start_everything.sh launches."""
    out = {}
    try:
        names = sorted(os.listdir(COLLECTORS_DIR))
    except OSError:
        return out
    for fn in names:
        if not (fn.startswith("collector_") and fn.endswith(".py")):
            continue
        try:
            src = open(os.path.join(COLLECTORS_DIR, fn), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        ml, mt = _CFG_LINE.search(src), _CFG_TABLE.search(src)
        out[fn[:-3]] = (int(ml.group(1)) if ml else None, mt.group(1) if mt else None)
    return out


def _collector_procs():
    """{collector name: [pid, …]} for every running `python -u collector_X.py`."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as f:
                parts = [p for p in f.read().split(b"\0") if p]
        except OSError:
            continue
        if (len(parts) >= 3 and parts[-2] == b"-u" and parts[-3].endswith(b"python")
                and parts[-1].startswith(b"collector_") and parts[-1].endswith(b".py")):
            out.setdefault(parts[-1].decode()[:-3], []).append(int(d))
    return out


def _collector_tail(path, now_s):
    """Parse the last COL['tail_bytes'] of a collector log, limited to the last
    COL['window_s'] by the clock on the status-table rows."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - COL["tail_bytes"]))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError:
        return None
    start = None
    for i, ln in enumerate(lines):
        m = _ROW.match(ln)
        if m:
            ts = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if (now_s - ts) % 86400 <= COL["window_s"]:
                start = i
                break
    t = {"main": [], "noise": [], "sub_off": {}, "sub_act": {}, "errs": {}, "err_text": {},
         "in_window": start is not None}
    if start is None:
        return t
    want_main = False
    for i, ln in enumerate(lines[start:]):
        if ln.startswith("TIME     MACHINE"):
            want_main = True
            continue
        m = _ROW.match(ln)
        if m:
            if want_main:
                t["main"].append({"plc": m.group(5) == "x", "db": m.group(6) == "x",
                                  "toks": m.group(7).split()})
                want_main = False
            continue
        m = _NOISE.match(ln)
        if m:
            t["noise"].append((m.group(1), int(m.group(2)), int(m.group(3))))
            continue
        for seg in _SUB_SEG.split(ln):
            mm = _SUB_OFF.match(seg)
            if mm:        # reason runs to the end of this message, e.g. "[Errno 113] No route to host)"
                t["sub_off"][int(mm.group(1))] = (i, mm.group(2).strip().rstrip(")").strip())
        for mm in _SUB_ACT.finditer(ln):
            t["sub_act"][int(mm.group(1))] = i
        for name, rx in _ERRS:
            mm = rx.search(ln)
            if mm:
                t["errs"][name] = t["errs"].get(name, 0) + 1
                if mm.groups():
                    t["err_text"][name] = mm.group(1).strip()[:110]
    return t


def check_collectors(a):
    files = _collector_files()
    procs = _collector_procs()
    now = datetime.now()
    now_s = now.hour * 3600 + now.minute * 60 + now.second
    summary = {"total": len(files), "running": 0, "problem_lines": 0, "rows": []}

    with closing(db()) as c:
        cur = dc(c)
        cur.execute("SELECT l.id, l.line_name, COALESCE(l.is_active, TRUE) AS active, "
                    "z.zone_name FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id")
        lines = {r["id"]: r for r in cur.fetchall()}
        cur.execute("SELECT id, line_id, machine_name, plc_ip FROM mes_plc_configs "
                    "WHERE parent_plc_id IS NOT NULL")
        subs = {r["id"]: r for r in cur.fetchall()}
        cur.execute("SELECT sub_plc_id, count(*) AS n FROM mes_submachine_ct_log "
                    "WHERE record_date >= current_date - 1 "
                    "AND ts_end > now() - interval '15 minutes' GROUP BY 1")
        sub_parts = {r["sub_plc_id"]: int(r["n"]) for r in cur.fetchall()}

        by_line = {}
        for cname, (lid, tbl) in files.items():
            by_line.setdefault(lid, []).append(cname)
            ln = lines.get(lid) or {}
            lname = ln.get("line_name") or f"line {lid}"
            tag = f"{lname} ({cname})"
            gid = f"collector:{cname}"
            before = len(a.gaps)
            row = {"collector": cname, "line": lname, "running": bool(procs.get(cname))}

            def cg(kind, sev, what, detail="", cause="", action=""):
                a.gap(f"{gid}:{kind}", sev, "Collector", f"{tag}: {what}", detail, cause, action)

            if lid is not None and not ln.get("active", True):
                row["note"] = "line inactive"
                summary["rows"].append(row)
                continue
            if not procs.get(cname):
                cg("down", "critical", "collector not running",
                   f"no process 'python -u {cname}.py'",
                   "process exited and its restart loop is gone",
                   "start this collector (operator)")
                summary["rows"].append(row)
                continue
            summary["running"] += 1
            a.ok()

            log = os.path.join(LOGS_DIR, f"collector_{cname}.log")
            try:
                silent = time.time() - os.path.getmtime(log)
            except OSError:
                silent = None
            row["log_silent_s"] = round(silent) if silent is not None else None
            if silent is None or silent > COL["log_silent_s"]:
                cg("log_silent", "critical",
                   "collector hung — no log output" + (f" for {silent/60:.0f} min" if silent else ""),
                   f"{log} not written", "process alive but not looping",
                   "check this collector; restart only it (operator)")

            has_d = has_ct = False
            if tbl and _TBL.match(tbl):
                cur.execute("SELECT to_regclass(%s) IS NOT NULL AS d, to_regclass(%s) IS NOT NULL AS c",
                            (tbl, tbl + "_ct_log"))
                r = cur.fetchone()
                has_d, has_ct = bool(r["d"]), bool(r["c"])
            if has_d:
                try:
                    cur.execute(f"SELECT EXTRACT(EPOCH FROM now() - max(updated_at))::int AS age "
                                f"FROM {tbl} WHERE record_date >= current_date - 1")
                    age = (cur.fetchone() or {}).get("age")
                except Exception:
                    age = None
                row["db_age_s"] = age
                if age is None or age > COL["db_stale_s"]:
                    cg("db_stale", "critical",
                       "dashboard not updated" + (f" for {age // 60} min" if age else ""),
                       f"{tbl}.updated_at", "main loop stuck or DB writes failing",
                       "check this collector's log (operator)")
            main_parts = None
            if has_ct:
                cur.execute(f"SELECT count(*) AS n FROM {tbl}_ct_log WHERE record_date >= current_date - 1 "
                            f"AND ts > now() - interval '15 minutes'")
                main_parts = int(cur.fetchone()["n"])
            row["parts_15m"] = main_parts
            line_subs = [s for s in subs.values() if s["line_id"] == lid]
            sib_max = max([sub_parts.get(s["id"], 0) for s in line_subs] or [0])
            row["sub_parts_15m"] = sib_max
            # A line making no parts anywhere is most likely just not running:
            # its unreachable PLCs are expected, so they stay info (status page
            # only) instead of going into the 15-minute report.
            producing = (sib_max >= COL["producing_parts"]
                         or (main_parts or 0) >= COL["producing_parts"])
            idle_sev = "warning" if producing else "info"

            t = _collector_tail(log, now_s) or {}
            errs = t.get("errs", {})
            main = t.get("main", [])
            held = None
            if main and len(main[-1]["toks"]) > 1 and main[-1]["toks"][1].isdigit():
                held = int(main[-1]["toks"][1])
            reg = main[-1]["toks"][0] if main and main[-1]["toks"] else None
            noise = [n for n in t.get("noise", []) if n[0] == reg]

            if noise and held is not None:
                lo, hi = noise[-1][1], noise[-1][2]
                if (hi - held >= COL["stuck_gap"] and hi - lo <= COL["noise_band"]
                        and lo > held and noise[-1][2] >= noise[0][2]):
                    cg("count_stuck", "critical", f"count stuck — PLC {reg} reads {hi}, MES holds {held}",
                       "the noise filter rejects the real, rising count as garbage",
                       "bad first read after a restart/reconnect; filter defends it",
                       "restart only this collector (operator)")
            elif (main_parts == 0 and sib_max >= COL["producing_parts"]):
                cg("not_counting", "critical",
                   f"main count not moving — sub-machines made {sib_max} parts in 15 min, main 0",
                   "no cycle rows in 15 min while the line produces",
                   "stuck count, DB write failure or main PLC not read", "check this collector (operator)")

            bad_db = [k for k in ("hourly write", "ct_log flush") if errs.get(k, 0) >= COL["err_storm"]]
            if bad_db or errs.get("DB reconnect", 0) >= COL["reconnect_storm"] or errs.get("traceback", 0) >= COL["err_storm"]:
                k = bad_db[0] if bad_db else ("traceback" if errs.get("traceback", 0) >= COL["err_storm"] else "DB reconnect")
                cg("db_errors", "critical", f"DB writes failing — {errs.get(k, 0)}× {k} in 15 min",
                   t.get("err_text", {}).get(k, "") or ", ".join(f"{e} {n}" for e, n in errs.items()),
                   "every failed write drops the DB connection; counts/graph freeze",
                   "fix the error shown (config/column); then restart only this collector")
            if errs.get("restart", 0) >= COL["restarts"]:
                cg("restart_loop", "warning", f"restarted {errs['restart']}× in 15 min",
                   "collector keeps exiting", "crash at start-up", "check the log (operator)")
            if len(main) >= 4:
                off = sum(1 for m in main if not m["plc"]) * 100 // len(main)
                dboff = sum(1 for m in main if not m["db"]) * 100 // len(main)
                row["plc_off_pct"] = off
                if off >= COL["plc_off_pct"]:
                    cg("plc_offline", idle_sev, f"main PLC unreachable {off}% of the time"
                       + ("" if producing else " (line not producing)"),
                       "status table shows PLC [ ]", "network / PLC side", "check PLC network")
                if dboff >= COL["plc_off_pct"] and not bad_db:
                    cg("db_offline", "critical", f"collector cannot write to DB {dboff}% of the time",
                       "status table shows DB [ ]", "DB write errors", "check this collector's log (operator)")

            cfg_bad, net_off, idle = [], [], []
            for sid, (i_off, why) in t.get("sub_off", {}).items():
                if i_off < t.get("sub_act", {}).get(sid, -1):
                    continue            # came back online after that message
                s = subs.get(sid) or {}
                label = f"{sid} {str(s.get('machine_name') or '')[:28].strip()} {s.get('plc_ip') or ''}".strip()
                (net_off if (not why or _NET.search(why)) else cfg_bad).append((label, why))
            offline_ids = set(t.get("sub_off", {}))
            for s in line_subs:
                if (sid := s["id"]) not in offline_ids and sub_parts.get(sid, 0) == 0 \
                        and sib_max >= COL["sub_sibling_parts"]:
                    idle.append(f"{sid} {str(s.get('machine_name') or '')[:28].strip()}")
            if cfg_bad:
                cg("sub_config", "warning", f"{len(cfg_bad)} sub-machine(s) offline — config error",
                   "; ".join(f"{l}: {w}" for l, w in cfg_bad)[:300],
                   "PLC answers but the configured register/bit is rejected",
                   "fix the register in Admin → PLC config")
            if net_off:
                cg("sub_offline", idle_sev, f"{len(net_off)} sub-machine(s) offline"
                   + ("" if producing else " (line not producing)"),
                   "; ".join(l for l, _ in net_off)[:300], "network / PLC side", "check PLC network")
            if idle:
                cg("sub_idle", "warning",
                   f"{len(idle)} sub-machine(s) not counting while siblings made ≥{COL['sub_sibling_parts']} parts",
                   "; ".join(idle)[:300], "register/bit not counting or wrong address",
                   "check the station's count register")

            row["problems"] = [g["id"].split(":")[-1] for g in a.gaps[before:]]
            if row["problems"]:
                summary["problem_lines"] += 1
            summary["rows"].append(row)

        for lid, names in by_line.items():
            live = [n for n in names if procs.get(n)]
            if lid is not None and len(live) > 1:
                lname = (lines.get(lid) or {}).get("line_name") or f"line {lid}"
                a.gap(f"collector:line{lid}:duplicate", "warning", "Collector",
                      f"{lname}: {len(live)} collectors write the same line ({', '.join(live)})",
                      "both update the same dashboard row", "duplicate collector file",
                      "keep one (operator)")
    return summary


def report_collectors(ac, admin_ids, dry=False):
    """Every 15-min run: ONE Inbox report listing every collector problem now
    open (not only the changes).  When all are fine again, one 'all OK';
    nothing while they stay fine."""
    try:
        prev = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {}
    except Exception:
        prev = {}
    had = any(k.startswith("collector:") for k in prev)
    probs = sorted([g for g in ac.gaps if g["sev"] in ("critical", "warning")],
                   key=lambda g: 0 if g["sev"] == "critical" else 1)
    msgs = []
    if probs:
        crit = sum(1 for g in probs if g["sev"] == "critical")
        n_lines = len({g["what"].split(":")[0] for g in probs})
        body = " · ".join(_gap_line(g) for g in probs[:8])
        if len(probs) > 8:
            body += f" … +{len(probs) - 8} more"
        msgs.append((f"Collectors — {n_lines} line(s) need attention ({crit} critical)", body, "/"))
    elif had:
        msgs.append(("Collectors — all OK again",
                     "Every collector is running and delivering data.", "/"))
    sent = 0
    if dry:
        for t, b, u in msgs:
            print(f"[pm-agent] (dry run, not sent) {t}: {b[:600]}", flush=True)
    else:
        for t, b, u in msgs:
            try:
                sent += _send_alerts(admin_ids, t, b, u)
            except Exception as e:
                print(f"[pm-agent] collector report failed: {e}")
        state = {k: v for k, v in prev.items() if not k.startswith("collector:")}
        state.update({g["id"]: {"sev": g["sev"], "what": g["what"]} for g in ac.gaps})
        try:
            _write_own(STATE_PATH, state)
        except Exception as e:
            print(f"[pm-agent] state not saved: {e}")
    return {"problems": len(probs), "alerts_sent": sent,
            "would_send": len(msgs) if dry else None}


# ── 5. Bin Filling (BinVision, every 15 min) ────────────────────────────────
# Read-only: the station's CSVs/heartbeats on disk and GETs to :8090.  Only
# clips that already exist are opened, so the check never starts a new cut.
BV_LOGS = "/home/server/Bin-Filling/station/logs"
BV_BASE = os.environ.get("BINVISION_BASE", "http://127.0.0.1:8090")
BV = {"target": 10, "video_sample": 6, "video_slow_s": 3.0, "clip_grace_s": 300,
      "recount_grace_s": 900, "hb": {"live5m5": 90, "binclips": 120, "recover": 300},
      "rec_max_s": 180}


def _bv_csv(name):
    p = os.path.join(BV_LOGS, name)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _bv_secs(v):
    try:
        t = datetime.strptime((v or "").strip(), "%H:%M:%S")
    except ValueError:
        return None
    return t.hour * 3600 + t.minute * 60 + t.second


def _bv_recount(rec, start, end):
    """Same rule as the dashboard: best overlap above half the bin's span."""
    a, b = _bv_secs(start), _bv_secs(end)
    if a is None or b is None or b <= a:
        return None
    best, best_ov = None, 0
    for r in rec:
        ra, rb = _bv_secs(r.get("start")), _bv_secs(r.get("end"))
        tot = (r.get("total") or "").strip()
        if ra is None or rb is None or rb <= ra or not tot.isdigit():
            continue
        ov = min(b, rb) - max(a, ra)
        if ov > 0.5 * (b - a) and ov > best_ov:
            best, best_ov = int(tot), ov
    return best


def check_binfill(ab, s):
    now = datetime.now()
    days = ([f"{now - timedelta(days=1):%Y%m%d}"] if now.hour < 8 else []) + [f"{now:%Y%m%d}"]
    out = {"days": days, "bins": 0, "short": [], "not_counted": [], "auto_updates": [],
           "videos": {"checked": 0, "ok": 0, "slow": 0, "failed": 0, "avg_s": None},
           "jobs": {}, "counting": None}

    def bg(kind, sev, what, detail="", cause="", action=""):
        ab.gap(f"binfill:{kind}", sev, "Bin Filling", what, detail, cause, action)

    try:
        st = s.get(f"{BV_BASE}/api/counting/status", timeout=10).json()
        out["counting"] = st.get("state")
    except Exception as e:
        bg("down", "critical", "Bin Filling dashboard not answering",
           f"{BV_BASE}: {type(e).__name__}", "binvision service down", "check binvision.service")
        return out
    ab.ok()
    live = out["counting"] in ("LIVE", "WARMING")

    flagged = []
    for d in days:
        rows = [r for r in _bv_csv(f"live_5m5_{d}.csv") if (r.get("bin") or "").strip().isdigit()]
        rec = _bv_csv(f"recount_{d}.csv")
        ver = {r.get("start", ""): r for r in _bv_csv(f"verify_{d}.csv")}
        out["bins"] += len(rows)
        for r in _bv_csv(f"auto_update_{d}.csv"):
            out["auto_updates"].append({"day": d, "bin": r.get("bin"), "start": r.get("start"),
                                        "live": r.get("live_total"), "recount": r.get("recount_total"),
                                        "result": r.get("result"), "at": r.get("at")})
        for r in rows:
            raw = (r.get("total") or "").strip()
            tot = int(raw) if raw.isdigit() else None
            if tot is not None and tot >= BV["target"]:
                continue
            try:
                end_dt = datetime.strptime(f"{d} {r['end']}", "%Y%m%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            rc = _bv_recount(rec, r.get("start"), r.get("end"))
            v = ver.get(r.get("start", ""))
            b = {"day": d, "bin": int(r["bin"]), "start": r.get("start", ""), "end": r.get("end", ""),
                 "live": tot, "recount": rc, "age_s": int((now - end_dt).total_seconds()),
                 "operator": (v or {}).get("human_total") if v else None,
                 "clip": f"bin_{int(r['bin']):03d}_{raw + 'of10' if tot is not None else 'nocount'}_"
                         f"{r.get('end', '').replace(':', '')}.mp4"}
            b["clip_ok"] = os.path.exists(os.path.join(BV_LOGS, "clips", b["clip"]))
            flagged.append(b)
            (out["short"] if tot is not None else out["not_counted"]).append(b)

    # short bins: cleared as false / confirmed / still waiting for the recount
    cleared = [b for b in out["short"] if b["operator"] is None and b["recount"] is not None
               and b["recount"] >= BV["target"]]
    confirmed = [b for b in out["short"] if b["operator"] is None and b["recount"] is not None
                 and b["recount"] < BV["target"]]
    wait_s = [b for b in out["short"] if b["operator"] is None and b["recount"] is None
              and b["age_s"] > BV["recount_grace_s"]]
    out["short_summary"] = {"total": len(out["short"]), "false_cleared": len(cleared),
                            "confirmed": len(confirmed), "operator_decided":
                            sum(1 for b in out["short"] if b["operator"] is not None)}
    if confirmed:
        bg("short_confirmed", "warning",
           f"{len(confirmed)} short bin(s) confirmed by the recount — real shorts",
           ", ".join(f"bin {b['bin']} {b['end']} ({b['recount']}/10)" for b in confirmed[-8:]),
           "live count and recount agree the bin is short", "check the bin on the line")
    if wait_s:
        bg("short_wait", "warning", f"{len(wait_s)} short bin(s) not recounted after 15 min",
           ", ".join(f"bin {b['bin']} {b['end']}" for b in wait_s[-8:]),
           "recount job behind or failing", "check the recount job")

    # not-counted bins: counted from the recording / need a manual count
    manual = [b for b in out["not_counted"] if b["operator"] is None and b["recount"] is None
              and b["age_s"] > BV["recount_grace_s"]]
    out["not_counted_summary"] = {"total": len(out["not_counted"]),
                                  "counted_from_recording": sum(1 for b in out["not_counted"]
                                                                if b["recount"] is not None and b["operator"] is None),
                                  "operator_counted": sum(1 for b in out["not_counted"] if b["operator"] is not None),
                                  "need_manual": len(manual)}
    if manual:
        bg("need_manual", "warning",
           f"{len(manual)} bin(s) need a manual count — the recording gave no clean count",
           ", ".join(f"bin {b['bin']} {b['end']}" for b in manual[-10:]),
           "the bin was not clearly visible in the recording either",
           "count them from the video on the Bin Filling page")

    # videos: every flagged bin needs a clip; open a sample and time it
    missing = [b for b in flagged if not b["clip_ok"] and b["age_s"] > BV["clip_grace_s"]]
    if missing:
        bg("clip_missing", "warning", f"{len(missing)} flagged bin video(s) not cut yet",
           ", ".join(f"bin {b['bin']}" for b in missing[-10:]), "clip job behind or down",
           "check the clip job")
    sample = [b for b in flagged if b["clip_ok"]]
    sample = (sample[:2] + sample[-(BV["video_sample"] - 2):]) if len(sample) > BV["video_sample"] else sample
    times, fails = [], []
    for b in sample:
        key = f"{b['day']} {b['start']}"
        t0 = time.time()
        try:
            j = s.get(f"{BV_BASE}/api/bins/clip/status",
                      params={"bin": b["bin"], "key": key}, timeout=15).json()
            if not j.get("ready"):
                fails.append(f"bin {b['bin']}: {str(j.get('detail') or 'not ready')[:60]}")
                continue
            r = s.get(f"{BV_BASE}/api/bins/clip", params={"bin": b["bin"], "key": key},
                      timeout=15, stream=True)
            if r.status_code != 200:
                fails.append(f"bin {b['bin']}: HTTP {r.status_code}")
                r.close()
                continue
            got = len(r.raw.read(65536) or b"")
            r.close()
            if got < 1000:
                fails.append(f"bin {b['bin']}: empty video")
                continue
            times.append(time.time() - t0)
        except Exception as e:
            fails.append(f"bin {b['bin']}: {type(e).__name__}")
    ab.ok()
    slow = [t for t in times if t > BV["video_slow_s"]]
    out["videos"] = {"checked": len(sample), "ok": len(times), "slow": len(slow),
                     "failed": len(fails), "avg_s": round(sum(times) / len(times), 2) if times else None}
    if fails:
        bg("video_fail", "critical", f"{len(fails)} of {len(sample)} bin videos do not open",
           "; ".join(fails[:6]), "the dashboard cannot serve the clip",
           "check the Bin Filling dashboard")
    if slow:
        bg("video_slow", "warning", f"{len(slow)} bin video(s) slow to open (>{BV['video_slow_s']:.0f}s)",
           f"slowest {max(slow):.1f}s", "dashboard or disk busy", "check the dashboard load")

    # the station's jobs (only while counting is on)
    for job, mx in BV["hb"].items():
        try:
            age = int(time.time() - json.load(open(os.path.join(BV_LOGS, f"_hb_{job}.json")))["t"])
        except Exception:
            age = None
        out["jobs"][job] = age
        if live and (age is None or age > mx):
            bg(f"job_{job}", "critical", f"Bin Filling {job} job stopped"
               + (f" ({age}s silent)" if age is not None else ""),
               f"heartbeat _hb_{job}.json", "the job exited or hung", "check the Bin Filling jobs")
    try:
        newest = max((e.stat().st_mtime for e in os.scandir(os.path.join(BV_LOGS, "recordings"))
                      if e.name.startswith("rec_")), default=None)
        rec_age = int(time.time() - newest) if newest else None
    except OSError:
        rec_age = None
    out["jobs"]["recorder"] = rec_age
    if live and (rec_age is None or rec_age > BV["rec_max_s"]):
        bg("job_recorder", "critical", "Bin Filling recorder stopped"
           + (f" ({rec_age}s since last footage)" if rec_age is not None else ""),
           "newest recording segment", "camera or recorder down", "check the camera and recorder")
    return out


def report_binfill(ab, admin_ids, info, dry=False):
    """Every 15-min run: one Inbox report while anything needs attention, plus
    every automatic count update since the last report."""
    try:
        prev = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {}
    except Exception:
        prev = {}
    seen = set((prev.get("binfill:_auto_seen") or {}).get("keys", []))
    autos = (info or {}).get("auto_updates", [])
    fresh = [u for u in autos if f"{u['day']} {u['bin']} {u['start']}" not in seen]
    probs = sorted([g for g in ab.gaps if g["sev"] in ("critical", "warning")],
                   key=lambda g: 0 if g["sev"] == "critical" else 1)
    had = any(k.startswith("binfill:") and k != "binfill:_auto_seen" for k in prev)
    words = {"counted_from_recording": "not counted → {r}/10 from recording",
             "false_short_cleared": "false short cleared ({l} → {r}/10)",
             "short_confirmed": "short confirmed ({l} → {r}/10)",
             "no_clean_count": "no clean recount — needs a manual count"}
    lines = [_gap_line(g) for g in probs[:6]]
    if fresh:
        lines.append("Auto-updated: " + ", ".join(
            f"bin {u['bin']} " + words.get(u["result"], u["result"] or "").format(
                l=u.get("live") or "-", r=u.get("recount") or "-") for u in fresh[:8])
            + (f" … +{len(fresh) - 8} more" if len(fresh) > 8 else ""))
    msgs = []
    if probs or fresh:
        crit = sum(1 for g in probs if g["sev"] == "critical")
        head = (f"{len(probs)} need attention" + (f" ({crit} critical)" if crit else "")
                if probs else f"{len(fresh)} count(s) updated automatically")
        msgs.append((f"Bin Filling — {head}", " · ".join(lines), "/bin-filling"))
    elif had:
        msgs.append(("Bin Filling — all OK again", "Videos open, and no bin is waiting.", "/bin-filling"))
    sent = 0
    if dry:
        for t, b, u in msgs:
            print(f"[pm-agent] (dry run, not sent) {t}: {b[:700]}", flush=True)
    else:
        for t, b, u in msgs:
            try:
                sent += _send_alerts(admin_ids, t, b, u)
            except Exception as e:
                print(f"[pm-agent] bin filling report failed: {e}")
        state = {k: v for k, v in prev.items() if not k.startswith("binfill:")}
        state.update({g["id"]: {"sev": g["sev"], "what": g["what"]} for g in ab.gaps})
        keep = [f"{u['day']} {u['bin']} {u['start']}" for u in autos]
        state["binfill:_auto_seen"] = {"sev": "info", "what": "auto updates reported",
                                       "keys": keep[-500:]}
        try:
            _write_own(STATE_PATH, state)
        except Exception as e:
            print(f"[pm-agent] state not saved: {e}")
    return {"problems": len(probs), "auto_new": len(fresh), "alerts_sent": sent,
            "would_send": len(msgs) if dry else None}


def _run_binfill(ab, s):
    try:
        return check_binfill(ab, s)
    except Exception as e:
        ab.gap("binfill:check:error", "warning", "Bin Filling", "Bin Filling checks could not finish",
               f"{type(e).__name__}: {str(e)[:100]}")
        return None


def _run_collectors(ac):
    try:
        return check_collectors(ac)
    except Exception as e:
        ac.gap("collector:check:error", "warning", "Collector", "Collector checks could not finish",
               f"{type(e).__name__}: {str(e)[:100]}")
        return None


# ── alerts ───────────────────────────────────────────────────────────────────
def _send_alerts(admin_ids, title, body, url):
    """The agent's one write outside its folder: an Inbox notification."""
    from routers.push import send_to_user     # type: ignore
    n = 0
    for uid in admin_ids:
        send_to_user(uid, title, body, url=url, tag="pm_audit")
        n += 1
    return n


def _gap_line(g):
    s = f"[{g['sev'][:4].upper()}] {g['what']}"
    return s + (f" → {g['action']}" if g.get("action") else "")


def alert(a, admin_ids, scope=None, dry=False, exclude=("collector:", "binfill:")):
    """Alert on CHANGE only: a gap that just appeared, or one that just cleared.
    With a scope ('video:'), only gaps of that scope are compared; the rest of
    the remembered state is carried over untouched.  `exclude` keys are never
    compared here — collector / Bin Filling problems have their own 15-min reports."""
    try:
        prev = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {}
    except Exception:
        prev = {}
    inscope = ((lambda k: k.startswith(scope)) if scope else (lambda k: True))
    if exclude:
        _base = inscope
        inscope = lambda k: _base(k) and not k.startswith(exclude)   # noqa: E731
    carried = {k: v for k, v in prev.items() if not inscope(k)}
    prev_s = {k: v for k, v in prev.items() if inscope(k)}
    now = {g["id"]: g for g in a.gaps}
    new = [g for k, g in now.items() if k not in prev_s]
    gone = [k for k in prev_s if k not in now]

    label = "PM Audit · Video" if scope == "video:" else "PM Audit"
    msgs = []
    if new:
        crit = [g for g in new if g["sev"] == "critical"]
        warn = [g for g in new if g["sev"] == "warning"]
        head = f"{len(crit)} critical, {len(warn)} warning" if crit else f"{len(new)} new"
        body = " · ".join(_gap_line(g) for g in new[:6])
        if len(new) > 6:
            body += f" … +{len(new)-6} more"
        url = "/video-coverage" if all(g["area"] == "Video" for g in new) else "/"
        msgs.append((f"{label} — {head} gap(s)", body, url))
    if gone:
        body = " · ".join(str((prev_s.get(k) or {}).get("what", k)) for k in gone[:6])
        if len(gone) > 6:
            body += f" … +{len(gone)-6} more"
        url = "/video-coverage" if all(k.startswith("video:") for k in gone) else "/"
        msgs.append((f"{label} — {len(gone)} gap(s) cleared", body, url))

    sent = 0
    if dry:
        for t, b, u in msgs:
            print(f"[pm-agent] (dry run, not sent) {t}: {b[:300]}", flush=True)
    else:
        for t, b, u in msgs:
            try:
                sent += _send_alerts(admin_ids, t, b, u)
            except Exception as e:
                print(f"[pm-agent] alert failed: {e}")
        state = dict(carried)
        state.update({k: {"sev": v["sev"], "what": v["what"]} for k, v in now.items()})
        try:
            _write_own(STATE_PATH, state)
        except Exception as e:
            print(f"[pm-agent] state not saved: {e}")
    return {"new": len(new), "cleared": len(gone), "alerts_sent": sent,
            "would_send": len(msgs) if dry else None}


def admin_user_ids():
    with db() as c:
        cur = dc(c)
        cur.execute("SELECT id FROM mes_admin WHERE role IN ('admin','plant_head')")
        return [r["id"] for r in cur.fetchall()]


def _counts(gaps):
    return {sev: sum(1 for g in gaps if g["sev"] == sev) for sev in ("critical", "warning", "info")}


def _run_video(a, rules, dry):
    if rules is None:
        a.gap("video:rules", "warning", "Video", "video_rules.json is missing or invalid",
              "video checks skipped this run")
        return None
    try:
        return check_video(a, rules, learn=not dry)
    except Exception as e:
        a.gap("video:check:error", "warning", "Video", "Video checks could not finish",
              f"{type(e).__name__}: {str(e)[:100]}")
        return None


def run_once(video_only=False, dry=False):
    problems = self_check()
    if problems:
        print("[pm-agent] SAFETY SELF-CHECK FAILED — refusing to run:", flush=True)
        for p in problems:
            print("   ", p, flush=True)
        if not dry:
            try:
                _send_alerts(admin_user_ids(), "PM Audit — agent refused to run",
                             "Safety self-check failed: " + "; ".join(problems[:4]), "/")
            except Exception:
                pass
        return None

    a = Audit()
    s = _GetOnlySession()
    rules = load_rules()
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    out_path = DRYRUN_PATH if dry else STATUS_PATH

    if video_only:
        # The 15-minute run: video + collectors.
        print("[pm-agent] video checks…", flush=True)
        vid = _run_video(a, rules, dry)
        apply_mutes(a, rules)
        admins = admin_user_ids()
        al = alert(a, admins, scope="video:", dry=dry)
        print("[pm-agent] collector checks…", flush=True)
        ac = Audit()
        col = _run_collectors(ac)
        apply_mutes(ac, rules)
        cr = report_collectors(ac, admins, dry=dry)
        a.checks += ac.checks
        print("[pm-agent] bin filling checks…", flush=True)
        ab = Audit()
        bf = _run_binfill(ab, s)
        apply_mutes(ab, rules)
        br = report_binfill(ab, admins, bf, dry=dry)
        a.checks += ab.checks
        ac.gaps += ab.gaps
        ac.muted += ab.muted
        try:
            snap = json.load(open(STATUS_PATH)) if os.path.exists(STATUS_PATH) else {}
        except Exception:
            snap = {}
        fresh = ("video:", "collector:", "binfill:")
        snap["gaps"] = [g for g in snap.get("gaps", []) if not g["id"].startswith(fresh)] + a.gaps + ac.gaps
        snap["muted"] = [g for g in snap.get("muted", []) if not g["id"].startswith(fresh)] + a.muted + ac.muted
        snap["gaps"].sort(key=lambda g: sev_rank.get(g["sev"], 3))
        snap["counts"] = _counts(snap["gaps"])
        snap["video"] = vid
        snap["video_ran_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap["video_took_s"] = round(time.time() - a.t0, 1)
        snap["video_alerts"] = al
        snap["collectors"] = col
        snap["collectors_ran_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap["collector_report"] = cr
        snap["binfill"] = bf
        snap["binfill_ran_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap["binfill_report"] = br
        snap["dry_run"] = dry
        a.gaps = a.gaps + ac.gaps
        a.muted = a.muted + ac.muted
    else:
        routes = discover_routes()
        print(f"[pm-agent] auditing {len(routes)} endpoints…", flush=True)
        users = check_users(a, s)
        eps   = check_endpoints(a, s, routes)
        notes = check_data(a, s)
        sysi  = check_system(a, s)
        vid   = _run_video(a, rules, dry)
        if vid:
            notes.append(f"video: Final Inspection {vid.get('fi_pct')}%, "
                         f"sub-machines {vid.get('sub_pct')}%")
        apply_mutes(a, rules)
        al    = alert(a, admin_user_ids(), dry=dry)
        a.gaps.sort(key=lambda g: sev_rank.get(g["sev"], 3))
        snap = {
            "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "took_s": round(time.time() - a.t0, 1),
            "checks": a.checks,
            "counts": _counts(a.gaps),
            "gaps": a.gaps,
            "muted": a.muted,
            "users": users,
            "endpoints": sorted(eps, key=lambda e: -(e["ms"] or 0))[:40],
            "endpoint_total": len(eps),
            "notes": notes,
            "system": sysi,
            "alerts": al,
            "video": vid,
            "video_ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": dry,
        }
        # Collector problems belong to the 15-minute run — keep its last result.
        try:
            old = json.load(open(STATUS_PATH)) if os.path.exists(STATUS_PATH) else {}
        except Exception:
            old = {}
        snap["gaps"] += [g for g in old.get("gaps", []) if g["id"].startswith(("collector:", "binfill:"))]
        snap["muted"] += [g for g in old.get("muted", []) if g["id"].startswith(("collector:", "binfill:"))]
        snap["gaps"].sort(key=lambda g: sev_rank.get(g["sev"], 3))
        snap["counts"] = _counts(snap["gaps"])
        for k in ("collectors", "collectors_ran_at", "collector_report",
                  "binfill", "binfill_ran_at", "binfill_report"):
            if k in old:
                snap[k] = old[k]
    _write_own(out_path, snap)

    c = snap["counts"]
    print(f"[pm-agent] {a.checks} checks in {round(time.time() - a.t0, 1)}s → "
          f"{len(a.gaps)} gap(s) this run; overall {c['critical']} critical, "
          f"{c['warning']} warning, {c['info']} info"
          + (" (DRY RUN — nothing sent or saved)" if dry else f" (alerts sent: {al['alerts_sent']})"),
          flush=True)
    for g in sorted(a.gaps, key=lambda g: sev_rank.get(g["sev"], 3))[:25]:
        print(f"   [{g['sev'][:4].upper()}] {g['area']:10s} {g['what']}"
              + (f" — {g['detail']}" if g["detail"] else "")
              + (f"\n{'':19s}cause: {g['cause']}\n{'':19s}action: {g['action']}" if g.get("action") else ""))
    for g in a.muted:
        print(f"   [MUTED] {g['what']} (until {g.get('muted_until') or '—'})")
    return snap


def serve(port):
    handler = partial(SimpleHTTPRequestHandler, directory=HERE)
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever,
                     daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single audit and exit")
    ap.add_argument("--video-only", action="store_true",
                    help="the 15-minute run: video + collector + Bin Filling checks")
    ap.add_argument("--dry-run", action="store_true",
                    help="no alerts, no state or history; status goes to status.dryrun.json")
    ap.add_argument("--self-check", action="store_true", help="only run the safety self-check")
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--serve", action="store_true", help="keep the live view up")
    a = ap.parse_args()
    if a.self_check:
        p = self_check()
        print("self-check OK — no forbidden calls" if not p else "self-check FAILED:\n  " + "\n  ".join(p))
        sys.exit(1 if p else 0)
    if a.serve:
        serve(a.port)
        print(f"[pm-agent] LIVE VIEW → http://127.0.0.1:{a.port}/", flush=True)
    run_once(video_only=a.video_only, dry=a.dry_run)
    if a.serve:
        while True:
            time.sleep(3600)
            run_once(video_only=a.video_only, dry=a.dry_run)


if __name__ == "__main__":
    main()
