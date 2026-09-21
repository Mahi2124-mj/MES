#!/usr/bin/env python3
# restart_serve_prod.py — restart the :5656 static server (serve_prod.py) so a
# code change to serve_prod.py takes effect (e.g. the new .apk download headers:
# Content-Type application/vnd.android.package-archive + Content-Disposition,
# which make the browser download the update cleanly instead of a blank tab).
#
# Brief (~1-2 s) blip for web/app users while it rebinds :5656 — run it at a
# calm moment.  Run:  python3 mes-frontend/restart_serve_prod.py
import os, re, sys, time, signal, subprocess

PORT = 5656
HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
VENV_PY = ("/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/"
           "D DRIVE/EOL/EOL/Deep (2)/Deep/Phase2/.venv-linux/bin/python")
SCRIPT = os.path.join(HERE, "serve_prod.py")
LOG = os.path.join(HERE, "serve_prod.log")


def pid_on_port(port):
    try:
        out = subprocess.check_output(["ss", "-ltnp"], text=True)
        for line in out.splitlines():
            if f":{port} " in line and "pid=" in line:
                m = re.search(r"pid=(\d+)", line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None


pid = pid_on_port(PORT)
if pid:
    print(f"Stopping serve_prod on :{PORT} (pid {pid})…")
    try: os.kill(pid, signal.SIGTERM)
    except Exception as e: print("  SIGTERM failed:", e)
    for _ in range(10):
        time.sleep(0.5)
        if pid_on_port(PORT) is None:
            break
    if pid_on_port(PORT) is not None:
        try: os.kill(pid, signal.SIGKILL); print("  SIGKILL")
        except Exception: pass
        time.sleep(1)
else:
    print(f"No serve_prod on :{PORT} — launching fresh")

py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
logf = open(LOG, "ab")
p = subprocess.Popen([py, SCRIPT, str(PORT), DIST], cwd=HERE, env=dict(os.environ),
                     stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                     start_new_session=True)
print(f"Relaunched serve_prod pid {p.pid}, waiting for :{PORT}…")
for _ in range(20):
    time.sleep(0.5)
    if pid_on_port(PORT) is not None:
        print(f"✓ serve_prod up on :{PORT} — .apk now downloads with proper headers")
        break
else:
    print("⚠ :5656 not up yet — check serve_prod.log")
