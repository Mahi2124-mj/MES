#!/usr/bin/env python3
"""
Loop Pipe cameras: switch the time display (OSD) in the recorded video on or off.

The cameras are Panasonic bullets (192.168.37.x). The camera's own setting
"TimeTitleAttribute.EncodeBlend" puts the clock into the video stream. This
script changes ONLY that one setting. Nothing else on the camera changes, the
camera does not reboot, and recording continues.

Usage (run it yourself; it asks for the camera login, nothing is stored):
    python3 loop_pipe_time_osd.py status            # show the setting on all Loop Pipe cameras
    python3 loop_pipe_time_osd.py off  192.168.37.51 # switch the time off on one camera
    python3 loop_pipe_time_osd.py off               # switch the time off on all Loop Pipe cameras
    python3 loop_pipe_time_osd.py restore           # put back the settings saved before "off"

Before the first change on each camera, its full overlay settings are saved to
~/loop_pipe_osd_backup/<ip>.json, so "restore" can undo the change exactly.
If the login fails, the run stops at once so the account is not locked.
"""
import getpass
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

LOOP_PIPE_CAMERAS = [
    ("192.168.37.51", "Line 1  YLM Bending Machine-01"),
    ("192.168.37.52", "Line 1  YLM Bending Machine-02"),
    ("192.168.37.53", "Line 1  Both Karakuri Middle Shelf"),
    ("192.168.37.54", "Line 1  Slit Cut / Squaring Machine"),
    ("192.168.37.31", "Line 2  Slit Cut / Squaring Machine"),
    ("192.168.37.32", "Line 2  YLM Bending Machine-01"),
    ("192.168.37.34", "Line 2  YLM Bending Machine-02 / Karakuri"),
    ("192.168.37.71", "Line 3  Both Karakuri Middle Shelf"),
    ("192.168.37.72", "Line 3  YLM Bending Machine-01"),
    ("192.168.37.73", "Line 3  YLM Bending Machine-02"),
    ("192.168.37.74", "Line 3  Slit Cut / Squaring Machine"),
]
OSD_PATH = "/CGI/Preview/prevcover/channels/0"
BACKUP_DIR = os.path.expanduser("~/loop_pipe_osd_backup")
LOGIN_ERRORS = {"206": "user is locked", "207": "user is blacklisted",
                "208": "user is already logged in elsewhere"}


class LoginFailed(Exception):
    pass


def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def call(ip, method, path, body=None, session=None, timeout=8):
    headers = {"If-Modified-Since": "0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if session:
        headers["Cookie"] = f"webSessionID={session}; Bitstream=0"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://{ip}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace").strip()
    return json.loads(raw) if raw else None


def login(ip, user, password):
    hello = call(ip, "GET", f"/CGI/Security/sessionLogin?timestamp:{int(time.time() * 1000)}")
    if not hello or str(hello.get("statusValue")) != "200":
        raise LoginFailed(f"login start refused: {hello}")
    answer = call(ip, "POST", "/CGI/Security/SelfExt/userCheck", {
        "Username": user,
        "Password": md5(md5(password) + hello["salt"]),
        "Sessionid": hello["sessionID"],
    })
    status = str((answer or {}).get("statusValue"))
    if status != "200":
        reason = LOGIN_ERRORS.get(status, "wrong username or password")
        raise LoginFailed(f"{reason} (status {status})")
    return answer["sessionID"]


def logout(ip, session):
    try:
        call(ip, "POST", "/CGI/Security/Lougout", session=session, timeout=4)
    except Exception:
        pass


def time_shown(osd):
    return bool(osd.get("TimeTitleAttribute", {}).get("EncodeBlend"))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "off", "restore"):
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    names = dict(LOOP_PIPE_CAMERAS)
    ips = sys.argv[2:] or [ip for ip, _ in LOOP_PIPE_CAMERAS]

    user = input("Camera username [admin]: ").strip() or "admin"
    password = getpass.getpass("Camera password (not shown): ")
    os.makedirs(BACKUP_DIR, exist_ok=True)

    done, failed = 0, 0
    for ip in ips:
        label = f"{ip:15} {names.get(ip, ''):42}"
        try:
            session = login(ip, user, password)
        except LoginFailed as e:
            print(f"{label} LOGIN FAILED: {e}")
            print("Stopped here so the camera account does not get locked. Check the login and run again.")
            sys.exit(2)
        except Exception as e:
            print(f"{label} NOT REACHABLE: {type(e).__name__} {e}")
            failed += 1
            continue
        try:
            osd = call(ip, "GET", OSD_PATH, session=session)
            if not osd or "TimeTitleAttribute" not in osd:
                print(f"{label} could not read the overlay settings: {osd}")
                failed += 1
                continue
            backup = os.path.join(BACKUP_DIR, f"{ip}.json")

            if mode == "status":
                print(f"{label} time in video: {'ON' if time_shown(osd) else 'OFF'}")
            elif mode == "off":
                if not os.path.exists(backup):
                    with open(backup, "w") as fh:
                        json.dump(osd, fh, indent=1)
                if not time_shown(osd):
                    print(f"{label} time already OFF")
                else:
                    osd["TimeTitleAttribute"]["EncodeBlend"] = False   # the only change
                    call(ip, "PUT", OSD_PATH, osd, session=session)
                    now = call(ip, "GET", OSD_PATH, session=session)
                    print(f"{label} time in video: {'still ON - not changed' if time_shown(now) else 'OFF (done)'}")
            elif mode == "restore":
                if not os.path.exists(backup):
                    print(f"{label} no backup saved, skipped")
                    continue
                with open(backup) as fh:
                    call(ip, "PUT", OSD_PATH, json.load(fh), session=session)
                now = call(ip, "GET", OSD_PATH, session=session)
                print(f"{label} restored, time in video: {'ON' if time_shown(now) else 'OFF'}")
            done += 1
        except Exception as e:
            print(f"{label} ERROR: {type(e).__name__} {e}")
            failed += 1
        finally:
            logout(ip, session)
    print(f"\nFinished: {done} camera(s) OK, {failed} with problems. Backups: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
