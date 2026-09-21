#!/usr/bin/env python3
# Targeted restart of the EOL MES-API on :8080 (deploys backend code changes).
# It is a systemd-user ORPHAN (no auto-respawn), so we must kill + relaunch
# with the SAME argv/cwd/env captured from /proc. A cwd-guard makes sure we
# never touch the Maintenance_DX API on :8892 by mistake.
import os, signal, subprocess, time, sys
GUARD = "Deep (2)/Deep/Phase2"          # must appear in the target's cwd
try:
    out = subprocess.check_output(["ss", "-tlnp"]).decode()
except Exception as e:
    print("ss failed:", e); sys.exit(1)
pid = None
for line in out.splitlines():
    if ":8080" in line and "pid=" in line:
        pid = int(line.split("pid=")[1].split(",")[0]); break
if not pid:
    print("No process listening on :8080"); sys.exit(1)

cwd = os.readlink(f"/proc/{pid}/cwd")
if GUARD not in cwd:
    print(f"REFUSING: :8080 pid {pid} cwd={cwd!r} is not the EOL Phase2 (guard {GUARD!r})")
    sys.exit(2)
argv = [a.decode() for a in open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0") if a]
env  = {}
for kv in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
    if b"=" in kv:
        k, v = kv.split(b"=", 1); env[k.decode()] = v.decode(errors="replace")
print(f"target :8080 pid={pid}\n  cwd={cwd}\n  argv={argv}")

os.kill(pid, signal.SIGTERM)
for _ in range(24):
    time.sleep(0.25)
    if not os.path.exists(f"/proc/{pid}"): break
else:
    print("SIGTERM slow -> SIGKILL"); os.kill(pid, signal.SIGKILL); time.sleep(0.5)

log = open(os.path.join(cwd, "_api_8080.log"), "ab")
p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=log,
                     start_new_session=True)
print("relaunched :8080 -> new pid", p.pid)
# wait for it to bind
for _ in range(40):
    time.sleep(0.5)
    o = subprocess.run(["ss", "-tln"], capture_output=True, text=True).stdout
    if ":8080" in o:
        print("OK — :8080 is listening again"); break
else:
    print("WARNING — :8080 not listening yet; check _api_8080.log")
