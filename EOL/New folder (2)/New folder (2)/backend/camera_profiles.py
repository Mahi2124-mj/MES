#!/usr/bin/env python3
"""Read a camera's ONVIF stream profiles (READ-ONLY).

2026-09-24 — ~86 % of these cameras record HEVC, and a browser cannot play it,
so every single clip has to be transcoded: measured 2.27 s on NVENC / 1.42 s on
the CPU versus 0.16 s for a plain stream copy of an H.264 source.  That 9-14x is
why the archiver cannot keep up with ~120 k cycles a day.

Before reconfiguring any camera, check what it ALREADY offers: if one of its
profiles is H.264, pointing the recorder at that profile's RTSP path is a config
change on our side and nothing changes on the camera.

Credentials come from camera_config (Fernet) and are never printed.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

TIMEOUT = 8.0
_NS = ('xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
       'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
       'xmlns:tt="http://www.onvif.org/ver10/schema"')


def _security(user: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    return (
        '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/'
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
        f"{created}</Created></UsernameToken></Security></s:Header>")


def _post(ip: str, port: int, path: str, body: str) -> Optional[str]:
    req = urllib.request.Request(
        f"http://{ip}:{port}{path}", data=body.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return None


def _call(ip: str, user: str, pwd: str, action: str, port: int) -> Optional[str]:
    body = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope {_NS}>'
            f"{_security(user, pwd)}<s:Body>{action}</s:Body></s:Envelope>")
    for p in ("/onvif/media_service", "/onvif/Media", "/onvif/device_service",
              "/onvif/services"):
        out = _post(ip, port, p, body)
        if out and ("Response" in out or "Fault" not in out):
            return out
    return None


def profiles(ip: str, user: str, pwd: str, port: int = 80) -> List[Dict]:
    """[{token, name, codec, width, height, fps, bitrate}] — read-only."""
    xml = _call(ip, user, pwd, "<trt:GetProfiles/>", port)
    if not xml:
        return []
    out = []
    for block in re.findall(r"<[a-zA-Z]*:?Profiles\b.*?</[a-zA-Z]*:?Profiles>", xml, re.S):
        def one(tag):
            m = re.search(rf"<[a-zA-Z]*:?{tag}>(.*?)</[a-zA-Z]*:?{tag}>", block, re.S)
            return m.group(1).strip() if m else ""
        tok = re.search(r'token="([^"]+)"', block)
        out.append({
            "token": tok.group(1) if tok else "",
            "name": one("Name"),
            "codec": one("Encoding"),
            "width": one("Width"),
            "height": one("Height"),
            "fps": one("FrameRateLimit"),
            "bitrate": one("BitrateLimit"),
        })
    return out


def stream_uri(ip: str, user: str, pwd: str, token: str, port: int = 80) -> str:
    """The RTSP path the camera itself advertises for that profile."""
    action = ("<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
              "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
              f"</trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken>"
              "</trt:GetStreamUri>")
    xml = _call(ip, user, pwd, action, port) or ""
    m = re.search(r"<[a-zA-Z]*:?Uri>(rtsp://[^<]+)</[a-zA-Z]*:?Uri>", xml)
    if not m:
        return ""
    uri = m.group(1)
    return re.sub(r"rtsp://[^@/]*@", "rtsp://", uri)      # never echo credentials


if __name__ == "__main__":
    import argparse
    import camera_config

    ap = argparse.ArgumentParser(description="Show a camera's ONVIF profiles")
    ap.add_argument("target", help="camera id or ip")
    ap.add_argument("--port", type=int, default=80)
    a = ap.parse_args()
    cam = next((c for c in camera_config.list_cameras()
                if str(c.get("id")) == a.target or str(c.get("ip")) == a.target), None)
    if not cam:
        raise SystemExit(f"camera not found: {a.target}")
    ip, user, pwd = cam.get("ip"), cam.get("username"), cam.get("password")
    ps = profiles(ip, user, pwd, a.port)
    if not ps:
        raise SystemExit("no ONVIF answer (try --port 8899)")
    for p in ps:
        uri = stream_uri(ip, user, pwd, p["token"], a.port)
        print(f"  {p['name'][:18]:<18} {p['codec']:<6} {p['width']}x{p['height']} "
              f"@{p['fps']}fps {p['bitrate']}kbps  path={uri.split('/',3)[-1] if uri else '?'}")
