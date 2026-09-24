#!/usr/bin/env python3
"""Reboot a camera whose video stack has hung.

2026-09-24 — operator rule: "mujhe camera hung nhi chahiye koi bhi".  These
cameras allow ONE RTSP session.  When a recorder is killed and reconnected too
quickly (restart, shift rotation, reboot) the camera keeps the dead session and
then refuses EVERY new TCP connection — it answers ping, nothing else.  Until
now the only cure was a person power-cycling it.

Most of them still answer on their management port, so we can ask the camera to
reboot itself:

    34567  XM / Sofia (DVRIP)   — the common port on this plant's cameras
    80     ONVIF device service — fallback where the web stack still answers
    8899   ONVIF (XM's alternate port)

Credentials come from camera_config (Fernet-encrypted at rest); they are never
printed or logged here.  Nothing in this module touches the MES, the collectors
or the CMS recorders — the only side effect is the camera rebooting.
"""

from __future__ import annotations

import hashlib
import json
import socket
import struct
from typing import Dict, Optional, Tuple

SOFIA_PORT = 34567
ONVIF_PORTS = (80, 8899)
CONNECT_TIMEOUT = 4.0
IO_TIMEOUT = 6.0

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


# ── XM / Sofia (DVRIP) ───────────────────────────────────────────────────────
def _sofia_hash(password: str) -> str:
    """XM's own 8-char digest of the MD5 — what the DVRIP login expects."""
    md5 = hashlib.md5(password.encode("utf-8")).digest()
    return "".join(_CHARS[(md5[i] + md5[i + 1]) % 62] for i in range(0, 16, 2))


def _sofia_packet(session: int, seq: int, msgid: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode() + b"\x0a\x00"
    head = struct.pack("<BBBBiiBBHi", 0xFF, 0x01, 0, 0, session, seq, 0, 0,
                       msgid, len(body))
    return head + body


def _sofia_recv(sock: socket.socket) -> Tuple[int, dict]:
    head = b""
    while len(head) < 20:
        chunk = sock.recv(20 - len(head))
        if not chunk:
            raise OSError("closed while reading header")
        head += chunk
    session = struct.unpack("<i", head[4:8])[0]
    length = struct.unpack("<i", head[16:20])[0]
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    try:
        data = json.loads(body.rstrip(b"\x00\x0a").decode("utf-8", "ignore"))
    except Exception:
        data = {}
    return session, data


def sofia_reboot(ip: str, user: str, password: str,
                 port: int = SOFIA_PORT) -> Tuple[bool, str]:
    """Log in on the DVRIP port and ask the camera to restart itself."""
    try:
        with socket.create_connection((ip, port), CONNECT_TIMEOUT) as s:
            s.settimeout(IO_TIMEOUT)
            s.sendall(_sofia_packet(0, 0, 1000, {
                "EncryptType": "MD5",
                "LoginType": "DVRIP-Web",
                "UserName": user,
                "PassWord": _sofia_hash(password),
            }))
            session, reply = _sofia_recv(s)
            ret = int(reply.get("Ret") or 0)
            if ret not in (100, 515):          # 100 = OK, 515 = already logged in
                return False, f"login refused (Ret={ret or '?'})"
            sid = reply.get("SessionID") or session
            if isinstance(sid, str):
                session = int(sid, 16) if sid.startswith("0x") else int(sid or 0)
            s.sendall(_sofia_packet(session, 1, 1450, {
                "Name": "OPMachine",
                "SessionID": sid if isinstance(sid, str) else f"0x{session:08X}",
                "OPMachine": {"Action": "Reboot"},
            }))
            try:
                _, ack = _sofia_recv(s)
                ret = int(ack.get("Ret") or 0)
            except Exception:
                # A camera that reboots immediately drops the socket before the
                # acknowledgement — that is a success, not a failure.
                return True, "reboot sent (camera closed the link)"
            if ret in (100, 515):
                return True, "reboot accepted"
            return False, f"reboot refused (Ret={ret or '?'})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ── ONVIF fallback ───────────────────────────────────────────────────────────
_ONVIF_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
    "<s:Header>{sec}</s:Header>"
    '<s:Body><SystemReboot xmlns="http://www.onvif.org/ver10/device/wsdl"/>'
    "</s:Body></s:Envelope>"
)


def onvif_reboot(ip: str, user: str, password: str,
                 port: int = 80) -> Tuple[bool, str]:
    """ONVIF SystemReboot with a WS-UsernameToken digest."""
    import base64
    import os as _os
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone

    nonce = _os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    sec = (
        '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/'
        '2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"<UsernameToken><Username>{user}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created></UsernameToken></Security>"
    )
    body = _ONVIF_BODY.format(sec=sec).encode()
    for path in ("/onvif/device_service", "/onvif/services"):
        req = urllib.request.Request(
            f"http://{ip}:{port}{path}", data=body,
            headers={"Content-Type": 'application/soap+xml; charset=utf-8'})
        try:
            with urllib.request.urlopen(req, timeout=IO_TIMEOUT) as r:
                if b"SystemRebootResponse" in r.read():
                    return True, f"reboot accepted ({path})"
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False, f"auth refused on {path}"
        except Exception:
            continue
    return False, "no ONVIF endpoint answered"


# ── what the CMS calls ───────────────────────────────────────────────────────
def port_open(ip: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except Exception:
        return False


def revive_camera(camera: Dict, log=print) -> Tuple[bool, str]:
    """Try to reboot this camera through whichever management port answers.

    `camera` is a camera_config entry (already decrypted by load_config).
    Returns (rebooted, reason).  Never raises.
    """
    ip = str(camera.get("ip") or "").strip()
    user = str(camera.get("username") or "").strip()
    pwd = str(camera.get("password") or "")
    cid = str(camera.get("id") or ip)
    if not ip or not user:
        return False, "no ip/username in the camera config"

    if port_open(ip, SOFIA_PORT):
        ok, why = sofia_reboot(ip, user, pwd)
        log(f"[REVIVE] {cid} {ip} sofia:{SOFIA_PORT} -> {why}")
        if ok:
            return True, f"sofia: {why}"
    for p in ONVIF_PORTS:
        if not port_open(ip, p):
            continue
        ok, why = onvif_reboot(ip, user, pwd, p)
        log(f"[REVIVE] {cid} {ip} onvif:{p} -> {why}")
        if ok:
            return True, f"onvif:{p}: {why}"
    return False, "no management port answered — needs a power-cycle"


if __name__ == "__main__":                                   # manual use
    import argparse
    import camera_config

    ap = argparse.ArgumentParser(description="Reboot a hung camera by id or ip")
    ap.add_argument("target", help="camera id or ip")
    ap.add_argument("--dry-run", action="store_true",
                    help="only report which management ports answer")
    a = ap.parse_args()

    cams = camera_config.list_cameras()
    cam = next((c for c in cams
                if str(c.get("id")) == a.target or str(c.get("ip")) == a.target), None)
    if not cam:
        raise SystemExit(f"camera not found: {a.target}")
    ip = cam.get("ip")
    if a.dry_run:
        for p in (SOFIA_PORT, *ONVIF_PORTS, 554):
            print(f"  tcp/{p}: {'open' if port_open(ip, p) else 'dead'}")
        raise SystemExit(0)
    ok, why = revive_camera(cam)
    print(("REBOOT SENT: " if ok else "NOT REBOOTED: ") + why)


# ── XM/Sofia config: read (and optionally set) the encoder ───────────────────
# 2026-09-24 — ~86 % of these cameras record HEVC.  A browser cannot play HEVC,
# so every clip is transcoded: 2.27 s on NVENC / 1.42 s on the CPU against
# 0.16 s for a stream copy of H.264.  That 9-14x is why the clip archiver cannot
# keep up with ~120 k cycles a day.  These are XM cameras with ONVIF disabled,
# so their encoder lives in the Sofia config "Simplify.Encode":
#   [{MainFormat:{Video:{Compression, Resolution, FPS, BitRate...}},
#     ExtraFormat:{...}}]      ExtraFormat = the SUB stream we record.
# Read first, change nothing until the operator approves.
def sofia_config(ip: str, user: str, password: str, name: str = "Simplify.Encode",
                 port: int = SOFIA_PORT):
    """GET a Sofia config block.  Returns (ok, payload_or_error)."""
    try:
        with socket.create_connection((ip, port), CONNECT_TIMEOUT) as s:
            s.settimeout(IO_TIMEOUT)
            s.sendall(_sofia_packet(0, 0, 1000, {
                "EncryptType": "MD5", "LoginType": "DVRIP-Web",
                "UserName": user, "PassWord": _sofia_hash(password)}))
            session, reply = _sofia_recv(s)
            if int(reply.get("Ret") or 0) not in (100, 515):
                return False, f"login refused (Ret={reply.get('Ret')})"
            sid = reply.get("SessionID") or f"0x{session:08X}"
            sess_i = int(sid, 16) if isinstance(sid, str) and sid.startswith("0x") else session
            s.sendall(_sofia_packet(sess_i, 1, 1042, {"Name": name, "SessionID": sid}))
            _, cfg = _sofia_recv(s)
            if name in cfg:
                return True, cfg[name]
            return False, f"no '{name}' in reply (Ret={cfg.get('Ret')})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def sofia_set_config(ip: str, user: str, password: str, name: str, value,
                     port: int = SOFIA_PORT):
    """SET a Sofia config block.  Only called for an operator-approved change."""
    try:
        with socket.create_connection((ip, port), CONNECT_TIMEOUT) as s:
            s.settimeout(IO_TIMEOUT)
            s.sendall(_sofia_packet(0, 0, 1000, {
                "EncryptType": "MD5", "LoginType": "DVRIP-Web",
                "UserName": user, "PassWord": _sofia_hash(password)}))
            session, reply = _sofia_recv(s)
            if int(reply.get("Ret") or 0) not in (100, 515):
                return False, f"login refused (Ret={reply.get('Ret')})"
            sid = reply.get("SessionID") or f"0x{session:08X}"
            sess_i = int(sid, 16) if isinstance(sid, str) and sid.startswith("0x") else session
            s.sendall(_sofia_packet(sess_i, 1, 1040,
                                    {"Name": name, "SessionID": sid, name: value}))
            _, ack = _sofia_recv(s)
            ret = int(ack.get("Ret") or 0)
            return (ret in (100, 515)), f"Ret={ret}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
