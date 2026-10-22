"""
routers/agents.py — Admin → Agents: on/off switch + live report for every MES agent, 2026-10-09.

Operator: "jitne bhi mere agent h un sab ka on off button with password ke sath,
password main khud set kar saku … admin me user ke baad ek module 'Agents' …
clean report bhi de har agent ki kon kya kar rha h or kitna busy h."

WHAT IT DOES
  * One registry of the agents that run on this box (user systemd units, cron
    jobs, and the Bin audit job inside BinVision).
  * A live report per agent: on/off, running now or idle, CPU and memory of its
    process tree (sampled from /proc), what it last did (its own status file /
    log tail), last and next run for cron agents.
  * ON / OFF needs the agent-control password, which the operator sets here
    (stored as a PBKDF2 hash).  Every change is written to an audit log.
      - user unit : ON = systemctl --user enable --now, OFF = disable --now
                    (so the choice also survives a reboot)
      - cron job  : OFF prefixes its crontab line(s) with "#AGENT-OFF# ",
                    ON removes that prefix; a run already going finishes
      - Bin audit : view only — it runs inside BinVision's counting pipeline
  * Nothing is switched by this code on its own; only an admin pressing the
    button with the right password.  Admin only.
"""
from datetime import datetime, timedelta
from typing import Optional
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import get_conn, dict_cursor
from auth import get_current_user
from ddl_once import once

router = APIRouter(prefix="/api/agents", tags=["agents"])

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HOME = os.path.expanduser("~")
UID = os.getuid()
OFF_MARK = "#AGENT-OFF# "
BACKUP_DIR = os.path.join(HOME, "agent_control_backups")
PBKDF2_ITER = 200_000
MAX_FAILS, LOCK_S = 5, 300

AGENTS = [
    {"key": "live-agent", "name": "Live Agent", "kind": "unit", "unit": "mes-live-agent.service",
     "what": "Checks the whole system every 30 min and explains why things fail.",
     "log": os.path.join(HOME, "live_agent.log"), "status": os.path.join(ROOT, "liveagent", "status.json"),
     "rule": "Kept off by operator rule since 19 Sep; turn on only when you decide."},
    {"key": "guardian", "name": "Guardian (auto-heal)", "kind": "unit", "unit": "mes-guardian.service",
     "what": "Auto-heals collectors and the camera pipeline; hourly report.",
     "log": None, "status": os.path.join(ROOT, "guardian", "status.json"),
     "rule": "Kept off by operator rule since 19 Sep; it restarts things when ON."},
    {"key": "pm-audit", "name": "PM audit agent", "kind": "cron", "match": "pmagent/run_audit.sh",
     "what": "Hourly read-only audit of the platform; video check every 15 min; alerts to the Inbox.",
     "log": os.path.join(HOME, "pm_agent.log"), "status": os.path.join(ROOT, "pmagent", "status.json"),
     "proc": "pm_agent.py"},
    {"key": "video-coverage", "name": "Video Coverage monitor", "kind": "unit", "unit": "vcov-monitor.service",
     "what": "Samples clip coverage, recording and hung cameras every 5 min.",
     "log": os.path.join(HOME, "vcov_monitor.log"), "status": None},
    {"key": "cms-watchdog", "name": "CMS watchdog", "kind": "cron", "match": "cms_watchdog.sh",
     "what": "Every 2 min checks the CMS is healthy and logs it. Observe only, never restarts.",
     "log": os.path.join(HOME, "cms_watchdog.log"), "status": None, "proc": "cms_watchdog.sh"},
    {"key": "chatbot", "name": "Local chatbot (LLM)", "kind": "unit", "unit": "ollama-mes.service",
     "what": "Runs the local language model behind the MES chatbot (CPU, port 11500).",
     "log": None, "status": None},
    {"key": "bin-audit", "name": "Bin audit agent", "kind": "view",
     "what": "Compares Bin Filling's bins with the line's own production and alerts on gaps.",
     "log": None, "status": "/home/server/Bin-Filling/station/logs/bin_audit_status.json",
     "heartbeat": "/home/server/Bin-Filling/station/logs/_hb_binaudit.json",
     "note": "Runs inside Bin Filling's counting; it starts and stops with counting."},
]
BY_KEY = {a["key"]: a for a in AGENTS}


# ── storage: password hash + audit log ─────────────────────────────────────
@once
def _ensure_tables() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS mes_agent_control (
                           key        VARCHAR(40) PRIMARY KEY,
                           value      TEXT,
                           updated_by VARCHAR(80),
                           updated_at TIMESTAMPTZ DEFAULT now())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS mes_agent_control_log (
                           id         SERIAL PRIMARY KEY,
                           at         TIMESTAMPTZ DEFAULT now(),
                           username   VARCHAR(80),
                           agent_key  VARCHAR(40),
                           action     VARCHAR(20),
                           ok         BOOLEAN,
                           detail     TEXT)""")
        conn.commit()


def _hash(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def _verify(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def _stored_hash():
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT value, updated_by, updated_at FROM mes_agent_control WHERE key='password'")
        r = cur.fetchone()
        conn.rollback()
    return dict(r) if r else None


def _audit(user, agent_key, action, ok, detail=""):
    try:
        _ensure_tables()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO mes_agent_control_log (username, agent_key, action, ok, detail)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (user.get("username"), agent_key, action, ok, (detail or "")[:500]))
            conn.commit()
    except Exception as exc:
        print(f"[AGENTS] audit write failed: {exc}", flush=True)


_fails = {}
_fails_lock = threading.Lock()


def _check_password(user, pw: str):
    """Raise unless pw matches.  5 wrong tries lock that login out for 5 min."""
    who = user.get("username") or "?"
    with _fails_lock:
        n, until = _fails.get(who, (0, 0))
        if until and time.time() < until:
            raise HTTPException(429, f"Too many wrong passwords. Try again in {int(until - time.time())} s.")
    st = _stored_hash()
    if not st or not st.get("value"):
        raise HTTPException(409, "Set the agent-control password first.")
    if not _verify(pw or "", st["value"]):
        with _fails_lock:
            n, _u = _fails.get(who, (0, 0))
            n += 1
            _fails[who] = (0, time.time() + LOCK_S) if n >= MAX_FAILS else (n, 0)
        raise HTTPException(403, "Wrong password.")
    with _fails_lock:
        _fails.pop(who, None)


def _admin_only(user):
    if (user.get("role") or "") != "admin":
        raise HTTPException(403, "Agents is for admin only")


# ── system access ──────────────────────────────────────────────────────────
def _user_env():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{UID}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{UID}/bus")
    return env


def _run(cmd, inp=None, timeout=20):
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout, env=_user_env())
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _unit_show(unit):
    rc, out, _e = _run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "SubState",
                        "-p", "MainPID", "-p", "UnitFileState", "-p", "ActiveEnterTimestamp", "--no-pager"])
    d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return d


def _crontab():
    rc, out, _e = _run(["crontab", "-l"])
    return out + "\n" if rc == 0 and out else ""


def _cron_lines(text, match):
    """[(index, line, is_off)] of the crontab lines that run this agent."""
    out = []
    for i, line in enumerate(text.splitlines()):
        core = line[len(OFF_MARK):] if line.startswith(OFF_MARK) else line
        if match in core and not core.lstrip().startswith("#"):
            out.append((i, core, line.startswith(OFF_MARK)))
    return out


def _cron_set(match, on):
    text = _crontab()
    lines = text.splitlines()
    hits = _cron_lines(text, match)
    if not hits:
        raise HTTPException(404, "This agent has no crontab line to switch.")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(os.path.join(BACKUP_DIR, f"crontab_{datetime.now():%Y%m%d_%H%M%S}.txt"), "w") as f:
        f.write(text)
    for i, core, _off in hits:
        lines[i] = core if on else OFF_MARK + core
    rc, _o, err = _run(["crontab", "-"], inp="\n".join(lines) + "\n")
    if rc != 0:
        raise HTTPException(500, f"crontab update failed: {err[:200]}")


def _next_run(sched: str):
    """Next run for the minute-only schedules used here ('*/15 * * * *', '17 * * * *')."""
    f = sched.split()
    if len(f) < 5 or any(x != "*" for x in f[1:5]):
        return None
    now = datetime.now().replace(second=0, microsecond=0)
    for k in range(1, 61 * 24):
        t = now + timedelta(minutes=k)
        m = f[0]
        if m == "*" or (m.startswith("*/") and t.minute % int(m[2:]) == 0) or (m.isdigit() and t.minute == int(m)):
            return t.isoformat(timespec="minutes")
    return None


# ── process tree, CPU, memory (straight from /proc; no psutil here) ─────────
_PAGE = os.sysconf("SC_PAGE_SIZE")
_HZ = os.sysconf("SC_CLK_TCK")


def _procs():
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                s = f.read()
            rest = s[s.rindex(")") + 2:].split()
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
            out[int(d)] = {"ppid": int(rest[1]), "cpu": int(rest[11]) + int(rest[12]),
                           "rss": int(rest[21]) * _PAGE, "cmd": cmd}
        except Exception:
            continue
    return out


def _tree(procs, root):
    kids, todo, seen = {}, [root], set()
    for pid, p in procs.items():
        kids.setdefault(p["ppid"], []).append(pid)
    while todo:
        p = todo.pop()
        if p in seen or p not in procs:
            continue
        seen.add(p)
        todo.extend(kids.get(p, []))
    return seen


def _busy(cpu_pct):
    if cpu_pct is None:
        return None
    return ("Idle" if cpu_pct < 3 else "Light" if cpu_pct < 40 else "Busy" if cpu_pct < 150 else "Very busy")


# ── per-agent "what is it doing" ───────────────────────────────────────────
def _tail(path, n=4):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 20000))
            lines = f.read().decode(errors="replace").splitlines()
        return [l.strip()[:220] for l in lines if l.strip()][-n:]
    except Exception:
        return []


def _mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
    except Exception:
        return None


def _json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _summary(a):
    """One plain sentence on what the agent last did, from its own files."""
    k = a["key"]
    st = _json(a["status"]) if a.get("status") else None
    try:
        if k == "pm-audit" and st:
            al = st.get("alerts") or {}
            n_al = len(al) if isinstance(al, (dict, list)) else 0
            return (f"Last full audit {st.get('ran_at')} — {st.get('checks')} checks in {st.get('took_s')} s, "
                    f"{st.get('endpoint_total')} endpoints; video check {st.get('video_ran_at')}; {n_al} alert groups.")
        if k == "live-agent" and st:
            return f"Last run {st.get('ran_at')} ({st.get('took_s')} s), {len(st.get('problems') or [])} problems found."
        if k == "guardian" and st:
            return (f"Last state '{st.get('state')}' at {st.get('at')} — collectors OK "
                    f"{st.get('collectors_ok')}/{st.get('collectors_total')}.")
        if k == "bin-audit" and st:
            hb = _json(a.get("heartbeat")) or {}
            return f"{st.get('at')} {st.get('line')}: {st.get('note') or 'no gap'} (now: {hb.get('doing', '—')} at {hb.get('wall', '—')})."
        if k == "video-coverage":
            t = _tail(a["log"], 1)
            return f"Last sample: {t[0]}" if t else None
        if k == "cms-watchdog":
            t = _tail(a["log"], 1)
            return f"Last log: {t[0]}" if t else None
        if k == "chatbot":
            with urllib.request.urlopen("http://127.0.0.1:11500/api/ps", timeout=2) as r:
                ms = json.loads(r.read().decode()).get("models") or []
            if not ms:
                return "No model loaded right now — idle, loads on the next chat question."
            m = ms[0]
            exp = str(m.get("expires_at") or "")
            until = ("kept loaded" if exp[:4].isdigit() and int(exp[:4]) > 2100
                     else f"unloads at {exp[:19].replace('T', ' ')}")
            return f"Model {m.get('name')} loaded ({round((m.get('size') or 0) / 1e9, 1)} GB), {until}."
    except Exception:
        pass
    return None


# ── report ─────────────────────────────────────────────────────────────────
_cache = {"t": 0.0, "data": None}
_cache_lock = threading.Lock()


def _report():
    with _cache_lock:
        if _cache["data"] and time.time() - _cache["t"] < 5:
            return _cache["data"]
    tab = _crontab()
    p0 = _procs()
    t0 = time.time()
    time.sleep(0.5)
    p1 = _procs()
    dt = max(0.1, time.time() - t0)
    rows = []
    for a in AGENTS:
        r = {"key": a["key"], "name": a["name"], "kind": a["kind"], "what": a["what"],
             "rule": a.get("rule"), "note": a.get("note"), "switchable": a["kind"] in ("unit", "cron")}
        pids = set()
        if a["kind"] == "unit":
            u = _unit_show(a["unit"])
            active = u.get("ActiveState") == "active"
            r.update(on=active, enabled=u.get("UnitFileState") == "enabled",
                     state=f"{u.get('ActiveState', '?')} ({u.get('SubState', '?')})",
                     since=u.get("ActiveEnterTimestamp") or None, unit=a["unit"])
            mp = int(u.get("MainPID") or 0)
            if mp:
                pids = _tree(p1, mp)
            r["running_now"] = active
        elif a["kind"] == "cron":
            hits = _cron_lines(tab, a["match"])
            on = any(not off for _i, _l, off in hits)
            scheds = [" ".join(l.split()[:5]) for _i, l, off in hits if not off]
            nxt = sorted(x for x in (_next_run(s) for s in scheds) if x)
            r.update(on=on, state="scheduled" if on else ("switched off" if hits else "not in crontab"),
                     schedule=[" ".join(l.split()[:5]) + ("  (--video-only)" if "--video-only" in l else "")
                               for _i, l, _o in hits],
                     next_run=nxt[0] if nxt else None)
            for pid, p in p1.items():
                if a.get("proc") and a["proc"] in p["cmd"] and "grep" not in p["cmd"]:
                    pids |= _tree(p1, pid)
            r["running_now"] = bool(pids)
        else:
            hb = _json(a.get("heartbeat")) or {}
            pid = int(hb.get("pid") or 0)
            alive = bool(pid) and pid in p1
            if alive:
                pids = _tree(p1, pid)
            r.update(on=alive, state=("running" if alive else "not running"), running_now=alive,
                     heartbeat=hb.get("wall"))
        if pids:
            cpu = sum(p1[p]["cpu"] - p0.get(p, p1[p])["cpu"] for p in pids if p in p1)
            r["cpu_pct"] = round(cpu / _HZ / dt * 100, 1)
            r["mem_mb"] = round(sum(p1[p]["rss"] for p in pids if p in p1) / 1048576)
            r["processes"] = len(pids)
        else:
            r["cpu_pct"], r["mem_mb"], r["processes"] = (0.0 if r.get("on") else None), None, 0
        r["busy"] = _busy(r["cpu_pct"]) if r["running_now"] else ("Waiting for next run" if r.get("on") else "Off")
        r["doing"] = _summary(a)
        r["last_log"] = _tail(a.get("log"), 3)
        r["last_activity"] = max([x for x in (_mtime(a.get("log")) if a.get("log") else None,
                                              _mtime(a.get("status")) if a.get("status") else None) if x] or [None])
        rows.append(r)
    data = {"generated_at": datetime.now().isoformat(timespec="seconds"), "agents": rows}
    with _cache_lock:
        _cache.update(t=time.time(), data=data)
    return data


def _clear():
    with _cache_lock:
        _cache.update(t=0.0, data=None)


# ── endpoints ──────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def list_agents(user=Depends(get_current_user)):
    _admin_only(user)
    data = dict(_report())
    st = _stored_hash()
    data["password_set"] = bool(st and st.get("value"))
    data["password_changed"] = {"by": st.get("updated_by"), "at": st["updated_at"].isoformat()} if st else None
    return data


class PasswordBody(BaseModel):
    current: str = ""
    new: str


@router.post("/password")
def set_password(body: PasswordBody, user=Depends(get_current_user)):
    """First time: no current password needed.  After that the current one is."""
    _admin_only(user)
    if len(body.new or "") < 4:
        raise HTTPException(400, "Use at least 4 characters.")
    st = _stored_hash()
    if st and st.get("value"):
        _check_password(user, body.current)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO mes_agent_control (key, value, updated_by, updated_at)
                       VALUES ('password', %s, %s, now())
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,
                         updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (_hash(body.new), user.get("username")))
        conn.commit()
    _audit(user, "-", "password", True, "set" if not st else "changed")
    return {"ok": True}


class ToggleBody(BaseModel):
    on: bool
    password: str


@router.post("/{key}/toggle")
def toggle(key: str, body: ToggleBody, user=Depends(get_current_user)):
    _admin_only(user)
    a = BY_KEY.get(key)
    if not a:
        raise HTTPException(404, "Unknown agent")
    if a["kind"] not in ("unit", "cron"):
        raise HTTPException(400, a.get("note") or "This agent can't be switched here.")
    try:
        _check_password(user, body.password)
    except HTTPException as exc:
        _audit(user, key, "on" if body.on else "off", False, exc.detail)
        raise
    try:
        if a["kind"] == "unit":
            rc, out, err = _run(["systemctl", "--user", "enable" if body.on else "disable", "--now", a["unit"]], timeout=60)
            if rc != 0:
                raise HTTPException(500, f"systemctl failed: {(err or out)[:200]}")
        else:
            _cron_set(a["match"], body.on)
    except HTTPException as exc:
        _audit(user, key, "on" if body.on else "off", False, exc.detail)
        raise
    _audit(user, key, "on" if body.on else "off", True, a.get("unit") or a.get("match"))
    _clear()
    row = next((r for r in _report()["agents"] if r["key"] == key), None)
    return {"ok": True, "agent": row}


@router.get("/log")
def action_log(limit: int = Query(50, ge=1, le=500), user=Depends(get_current_user)):
    _admin_only(user)
    _ensure_tables()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""SELECT at, username, agent_key, action, ok, detail FROM mes_agent_control_log
                        ORDER BY id DESC LIMIT %s""", (limit,))
        rows = [{**dict(r), "at": r["at"].isoformat(timespec="seconds")} for r in cur.fetchall()]
        conn.rollback()
    return {"rows": rows}


@router.get("/{key}/tail")
def agent_tail(key: str, lines: int = Query(40, ge=1, le=300), user=Depends(get_current_user)):
    _admin_only(user)
    a = BY_KEY.get(key)
    if not a:
        raise HTTPException(404, "Unknown agent")
    return {"key": key, "log": a.get("log"), "lines": _tail(a.get("log"), lines)}
