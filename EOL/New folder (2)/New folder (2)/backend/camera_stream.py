"""
camera_stream.py — Persistent per-camera RTSP capture threads.

Each camera gets ONE background thread that keeps the RTSP connection alive
and continuously refreshes a frame buffer.  Flask endpoints just memcpy the
latest JPEG — no RTSP handshake per HTTP request, no 5-second delays.
"""
from __future__ import annotations

import cv2
import subprocess
import threading
import time
import logging
from typing import Dict, Optional

log = logging.getLogger(__name__)

# JPEG encode quality for snapshot/stream frames
_JPEG_QUALITY = 80
# Seconds to wait before reconnecting after a failure
_RECONNECT_DELAY = 3
# Maximum idle seconds before a stream self-terminates (0 = never)
_MAX_IDLE = 0


class CameraStream:
    """
    Maintains a persistent RTSP connection in a daemon thread.
    Thread-safe frame buffer accessible via get_jpeg().
    """

    def __init__(self, camera_id: str, rtsp_url: str):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url

        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None   # latest encoded JPEG
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.error_count = 0

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"cam-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        log.info("CameraStream started: %s", self.camera_id)

    def stop(self) -> None:
        self._running = False
        log.info("CameraStream stopped: %s", self.camera_id)

    def get_jpeg(self) -> Optional[bytes]:
        """Return the latest JPEG frame bytes, or None if no frame yet."""
        with self._lock:
            return self._jpeg

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ffmpeg_exe() -> str:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            return get_ffmpeg_exe()
        except Exception:
            return "ffmpeg"

    def _capture_loop(self) -> None:
        """
        Use FFmpeg subprocess for ultra-low-latency RTSP capture.
        FFmpeg decodes H.264 in native C and outputs raw JPEG frames.
        Each frame is parsed from stdout and stored in the buffer.
        """
        ffmpeg = self._ffmpeg_exe()

        while self._running:
            proc = None
            try:
                log.info("CameraStream[%s]: starting FFmpeg …", self.camera_id)

                cmd = [
                    ffmpeg,
                    "-loglevel", "quiet",
                    # ── low-latency input ───────────────────────────────
                    "-fflags", "nobuffer+discardcorrupt",
                    "-flags", "low_delay",
                    "-rtsp_transport", "tcp",
                    "-avioflags", "direct",
                    "-probesize", "32",
                    "-analyzeduration", "0",
                    # ── input ───────────────────────────────────────────
                    "-i", self.rtsp_url,
                    # ── output: JPEG frames on stdout ───────────────────
                    "-vf", "fps=20",           # 20 fps — smooth but not CPU-heavy
                    "-q:v", str(_JPEG_QUALITY // 3 + 1),  # ~q4 ≈ 85% quality
                    "-f", "mjpeg",
                    "-flush_packets", "1",
                    "pipe:1",
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.connected = True
                self.error_count = 0
                log.info("CameraStream[%s]: FFmpeg started (pid=%d)", self.camera_id, proc.pid)

                buf = b""
                while self._running:
                    chunk = proc.stdout.read(32768)
                    if not chunk:
                        break   # FFmpeg exited
                    buf += chunk
                    # Extract all complete JPEG frames from buffer
                    while True:
                        s = buf.find(b"\xff\xd8")   # JPEG SOI
                        if s == -1:
                            buf = b""
                            break
                        e = buf.find(b"\xff\xd9", s + 2)   # JPEG EOI
                        if e == -1:
                            buf = buf[s:]   # keep partial frame
                            break
                        jpeg = buf[s : e + 2]
                        buf  = buf[e + 2:]
                        with self._lock:
                            self._jpeg = jpeg   # store latest frame

            except Exception as exc:
                log.error("CameraStream[%s]: FFmpeg exception: %s", self.camera_id, exc)
            finally:
                self.connected = False
                if proc is not None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except Exception:
                        pass

            if self._running:
                log.info("CameraStream[%s]: reconnecting in %ds", self.camera_id, _RECONNECT_DELAY)
                time.sleep(_RECONNECT_DELAY)


# ── Global stream registry ────────────────────────────────────────────────────

_registry: Dict[str, CameraStream] = {}
_reg_lock = threading.Lock()


def get_stream(camera_id: str, rtsp_url: str) -> CameraStream:
    """Return (and auto-start) a CameraStream for the given camera."""
    with _reg_lock:
        if camera_id not in _registry or not _registry[camera_id].is_alive():
            s = CameraStream(camera_id, rtsp_url)
            _registry[camera_id] = s
            s.start()
        return _registry[camera_id]


def stop_all() -> None:
    """Gracefully stop all streams (call on app shutdown)."""
    with _reg_lock:
        for s in _registry.values():
            s.stop()
        _registry.clear()
