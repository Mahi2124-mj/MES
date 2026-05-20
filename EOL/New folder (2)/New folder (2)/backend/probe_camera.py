"""
Comprehensive camera probe:
  1. TCP port check
  2. Raw RTSP DESCRIBE (shows exact camera response/error)
  3. OpenCV VideoCapture test with extended Panasonic path list
Run: python probe_camera.py
"""

import base64
import os
import socket
import sys
from typing import List, Tuple

CAMERA_IP   = os.getenv("CAMERA_IP",   "")
CAMERA_USER = os.getenv("CAMERA_USER", "")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")

# ─── Panasonic-specific RTSP paths (all known variants) ───────────────────────
RTSP_PATHS = [
    "/MediaInput/h264",
    "/MediaInput/h264/stream_1",
    "/MediaInput/h264/stream_2",
    "/MediaInput/mpeg4",
    "/MediaInput/mpeg4/stream_1",
    "/nphMpeg4/nil-640x480",
    "/nphMpeg4/nil-320x240",
    "/live",
    "/live/main",
    "/live/sub",
    "/live/ch1",
    "/live/ch00_0",
    "/live/ch00_1",
    "/stream",
    "/stream1",
    "/cam/realmonitor",
    "/h264/ch1/main/av_stream",
    "/",
]

RTSP_PORTS = [554, 8554, 10554]

SEP = "─" * 60


# ─── 1. TCP port check ────────────────────────────────────────────────────────
def check_ports() -> List[Tuple[int, bool]]:
    results = []
    for port in RTSP_PORTS + [80, 443, 8080]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((CAMERA_IP, port))
            s.close()
            results.append((port, True))
        except Exception:
            results.append((port, False))
    return results


# ─── 2. Raw RTSP DESCRIBE ─────────────────────────────────────────────────────
def raw_rtsp_describe(port: int, path: str) -> Tuple[bool, str]:
    """Send a bare RTSP DESCRIBE and return (connected, first_response_line)."""
    creds = base64.b64encode(f"{CAMERA_USER}:{CAMERA_PASS}".encode()).decode()
    request = (
        f"DESCRIBE rtsp://{CAMERA_IP}:{port}{path} RTSP/1.0\r\n"
        f"CSeq: 1\r\n"
        f"Authorization: Basic {creds}\r\n"
        f"Accept: application/sdp\r\n"
        f"User-Agent: PythonProbe/1.0\r\n"
        f"\r\n"
    )
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect((CAMERA_IP, port))
        s.sendall(request.encode())
        raw = s.recv(4096)
        s.close()
        text = raw.decode("utf-8", errors="replace")
        first_line = text.split("\r\n")[0].strip()
        return True, first_line
    except ConnectionRefusedError:
        return False, "Connection refused"
    except socket.timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


# ─── 3. OpenCV test ───────────────────────────────────────────────────────────
def cv2_test(url: str) -> Tuple[bool, str]:
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return False, "cv2 not installed"

    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return False, "cap.isOpened() = False"
    ok, _ = cap.read()
    cap.release()
    if ok:
        return True, "Frame read OK"
    return False, "Opened but no frame"


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print(SEP)
    print(f"  Camera Probe  →  {CAMERA_IP}  user={CAMERA_USER}")
    print(SEP)

    # 1. Port check
    print("\n[1/3] TCP port check")
    port_results = check_ports()
    open_ports = []
    for port, ok in port_results:
        mark = "OPEN  ✓" if ok else "CLOSED ✗"
        print(f"  Port {port:6d}  →  {mark}")
        if ok:
            open_ports.append(port)

    rtsp_candidates = [p for p in RTSP_PORTS if p in open_ports]
    if not rtsp_candidates:
        print("\n  ⚠ No RTSP port open. RTSP may be disabled on camera or")
        print("    blocked by a firewall. Try enabling RTSP in camera Config.")
        rtsp_candidates = RTSP_PORTS  # still probe in case firewall asymmetric

    # 2. Raw RTSP probe on each open RTSP port
    print(f"\n[2/3] Raw RTSP DESCRIBE (testing {len(RTSP_PATHS)} paths)")
    working_urls = []
    for port in rtsp_candidates:
        print(f"\n  Port {port}:")
        for path in RTSP_PATHS:
            connected, response = raw_rtsp_describe(port, path)
            is_ok = connected and response.startswith("RTSP/1.0 200")
            is_auth = connected and "401" in response
            mark = "✓ 200 OK" if is_ok else ("⚠ 401 Auth" if is_auth else response[:55])
            print(f"    {path:40s}  →  {mark}")
            if is_ok or is_auth:
                working_urls.append((port, path, response))

    # 3. OpenCV test on best candidates
    print(f"\n[3/3] OpenCV VideoCapture test")
    if working_urls:
        test_list = [
            f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{port}{path}"
            for port, path, _ in working_urls[:3]
        ]
    else:
        # fallback: try every path on first open/rtsp port
        first_port = rtsp_candidates[0] if rtsp_candidates else 554
        test_list = [
            f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{first_port}{path}"
            for path in RTSP_PATHS[:6]
        ]

    final_good = []
    for url in test_list:
        ok, msg = cv2_test(url)
        mark = "✓ WORKS" if ok else f"✗ {msg}"
        print(f"  {url}\n  → {mark}\n")
        if ok:
            final_good.append(url)

    # Summary
    print(SEP)
    if final_good:
        print("  ✓ WORKING RTSP URL(s):")
        for u in final_good:
            print(f"    {u}")
    else:
        print("  ✗ No working URL found via OpenCV.")
        print("  Hint: Check camera  Config → Video → RTSP  and enable the stream.")
        print("        Also try  Config → Network → Advanced → RTSP Port = 554")
    print(SEP)


if __name__ == "__main__":
    main()

