#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────
# restart_api.py — restart ONLY the MES-API (:8080) uvicorn.
#
# Preserves the running process's runtime env (DB_HOST, WELD_TEST_GEN,
# COLLECTOR_CACHE_DIR, IMAGEIO_FFMPEG_EXE, … — these come from
# start_everything.sh, NOT .env) by copying /proc/<pid>/environ, then
# SIGTERMs the old pid and relaunches uvicorn detached.
#
# Does NOT touch collectors / CMS / frontend.  Use this to pick up any
# MES-API code change (new router, auth, clip-archive, .env edit).
#
#   Run:   python3 restart_api.py     (from the Phase2 dir)
# ───────────────────────────────────────────────────────────────────────
import os, re, sys, time, signal, socket, subprocess, resource
import urllib.request, urllib.error

PORT    = 8080
HERE    = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, ".venv-linux", "bin", "python")
LOG     = os.path.join(HERE, "logs", "MES-API.log")


def pids_on_port(port):
    """EVERY pid listening on the port, parent supervisor included.

    2026-09-17 — this used to return only the FIRST pid.  That was fine while
    uvicorn ran a single process, but with --workers the supervisor AND each
    worker hold the socket, so SIGTERMing one pid left the rest listening: the
    relaunched instance could not bind, died silently, and the health probe
    still passed because the OLD instance was answering.  The restart reported
    success and changed nothing — code edits appeared to have no effect for
    hours.  Collect them all and wait for the port to actually free.
    """
    pids = []
    try:
        out = subprocess.check_output(["ss", "-ltnp"], text=True)
        for line in out.splitlines():
            if f":{port} " in line and "pid=" in line:
                pids += [int(x) for x in re.findall(r"pid=(\d+)", line)]
    except Exception as e:
        print("WARN pids_on_port:", e)
    return sorted(set(pids))


def pid_on_port(port):
    p = pids_on_port(port)
    return p[0] if p else None


def read_environ(pid):
    env = {}
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for kv in f.read().split(b"\0"):
                if b"=" in kv:
                    k, v = kv.split(b"=", 1)
                    env[k.decode(errors="replace")] = v.decode(errors="replace")
    except Exception as e:
        print("WARN cannot read /proc environ (running as same user?):", e)
    return env


def port_free(port):
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", port)); s.close(); return True
    except OSError:
        return False
    finally:
        try: s.close()
        except Exception: pass


def probe():
    """GET /api/devices/list — 401/403 = router loaded, 404 = not, None = down."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/devices/list", timeout=2)
        return 200
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def main():
    if not os.path.exists(VENV_PY):
        sys.exit(f"[FATAL] venv python not found: {VENV_PY}")

    pid = pid_on_port(PORT)
    env = dict(os.environ)
    cwd = HERE

    if pid:
        print(f"Found MES-API pid {pid} on :{PORT}")
        penv = read_environ(pid)
        if penv:
            env.update(penv)
            print(f"  carried {len(penv)} env vars from the running process")
            # 2026-09-12 — let the shell OVERRIDE tuning vars that the carried
            # env would otherwise pin forever (e.g. reverting CLIP_PREWARM_* after
            # a bad value hung the CMS).  Only these opt-in keys; DB creds etc.
            # still come from penv.
            for _k in list(os.environ):
                if _k.startswith("CLIP_PREWARM") or _k.startswith("VIDEO_"):
                    env[_k] = os.environ[_k]
                    print(f"  shell override: {_k}={os.environ[_k]}")
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            pass
        victims = pids_on_port(PORT)
        print(f"Stopping {len(victims)} process(es) on :{PORT}: "
              + ", ".join(str(v) for v in victims))
        for v in victims:
            try: os.kill(v, signal.SIGTERM)
            except Exception: pass
        print("Sent SIGTERM; waiting for :8080 to free…")
        for _ in range(40):
            if port_free(PORT):
                break
            time.sleep(0.5)
        else:
            print("Still busy after 20s — SIGKILL")
            for v in pids_on_port(PORT) or victims:
                try: os.kill(v, signal.SIGKILL)
                except Exception: pass
            time.sleep(2)
        # Refuse to relaunch onto a port that is still held — otherwise the new
        # instance dies on bind and the old one keeps serving stale code.
        # Judge by who still HOLDS the socket, not by whether we can bind it —
        # a just-closed listener leaves the port in TIME_WAIT for a moment and
        # bind() fails even though nothing is serving any more.
        still = pids_on_port(PORT)
        if still:
            print(f"ERROR :{PORT} is STILL held by {still} — not relaunching")
            sys.exit(1)
    else:
        print(f"No process on :{PORT}; starting fresh")

    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (65536, 65536))
    except Exception as e:
        print("WARN could not raise NOFILE:", e)

    # 2026-09-17 — serve on several workers.  One worker is capped near a
    # single core by the GIL, and with 30 supervisors on the page even
    # /api/auth/me took seconds while 31 of 64 cores sat idle; 4 workers took
    # video-open p50 from 3115 ms to 72 ms and "under 2 s" from 19% to 99%.
    # Safe only because bg_leader.py elects ONE process to run the clip
    # archiver, the alarm sweeps and the mail workers — without it each worker
    # would run every one of them.  DB_POOL_MAX is per-process, so it is divided
    # among the workers to stay inside the server's max_connections (see
    # database.py).  Both numbers must ALSO be set in start_everything.sh, which
    # is the boot path — change one and a reboot silently reverts it.
    workers = os.environ.get("MES_API_WORKERS", "4")
    env.setdefault("DB_POOL_MAX", "40")
    logf = open(LOG, "ab")
    proc = subprocess.Popen(
        [VENV_PY, "-u", "-m", "uvicorn", "main:app", "--host", "0.0.0.0",
         "--port", str(PORT), "--workers", workers],
        cwd=cwd, env=env, stdout=logf, stderr=logf, start_new_session=True,
    )
    print(f"Relaunched MES-API pid {proc.pid}  (cwd={cwd})  logs -> {LOG}")

    print("Waiting for it to come up…")
    for _ in range(60):
        time.sleep(1)
        c = probe()
        if c in (200, 401, 403):
            print(f"✓ MES-API up and device router loaded (HTTP {c} on /api/devices/list)")
            return
        if c == 404:
            print("✗ MES-API up but /api/devices/list is 404 — router import failed, check the log")
            return
    print("✗ MES-API did not answer in 60s — check logs/MES-API.log")


if __name__ == "__main__":
    main()
