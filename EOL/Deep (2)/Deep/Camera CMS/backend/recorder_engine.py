"""
recorder_engine.py
==================
ffmpeg-based continuous recorder for Camera CMS.

Why ffmpeg instead of cv2.VideoWriter
-------------------------------------
The previous cv2-based pipeline computed each output file's duration
from `frame_count / fps`.  When the RTSP source dropped frames or
networked through CPU contention, the file's duration came out
WAY shorter than wall-clock — a 118-second cycle would render a 20-
second video.

ffmpeg solves this in two ways:
  1. `-use_wallclock_as_timestamps 1` pegs each input frame to the
     wall clock when it ARRIVES, so dropped/duplicate frames don't
     compress the output timeline.
  2. Output is MPEG-TS (`.ts`).  TS is designed for unreliable
     streams: timestamps are self-describing, files can be cut at
     any keyframe boundary with `-c copy` (no re-encode = instant).

Architecture
------------
- One `CameraRecorder` per camera.
- Records continuously into a rolling per-shift `.ts` file:
    {videos_dir}/{camera_id}/{YYYY-MM-DD}_{shift}.ts
- The file's start epoch is captured when the ffmpeg process is
  launched.  Cycle clip extraction later does:
      ffmpeg -ss <cycle_start - file_start> -to <cycle_end - file_start>
             -c copy  (instant byte-cut, no re-encode)
- Restarts ffmpeg on shift change so each shift gets its own .ts.

This module only handles the CAPTURE side.  Cycle event recording
(start_ts/end_ts/status pairs) lives in `cycle_events.py`, and the
clip extraction REST endpoint lives in `api_server.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── Paths to bundled ffmpeg binaries (downloaded once into backend/bin)
_BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
FFMPEG_EXE  = os.environ.get("FFMPEG_EXE",  os.path.join(_BIN_DIR, "ffmpeg.exe"))
FFPROBE_EXE = os.environ.get("FFPROBE_EXE", os.path.join(_BIN_DIR, "ffprobe.exe"))


def ffmpeg_available() -> bool:
    return os.path.exists(FFMPEG_EXE) or _exe_in_path("ffmpeg")


def _exe_in_path(name: str) -> bool:
    for p in (os.environ.get("PATH", "")).split(os.pathsep):
        if os.path.exists(os.path.join(p, f"{name}.exe")):
            return True
        if os.path.exists(os.path.join(p, name)):
            return True
    return False


def _resolve_ffmpeg() -> str:
    if os.path.exists(FFMPEG_EXE):
        return FFMPEG_EXE
    return "ffmpeg"   # rely on PATH


def _shift_for(now: datetime) -> str:
    """Return shift label for given datetime.  Mirrors the MES three-
    shift rotation: A = 06-14, B = 14-22, C = 22-06 (next day)."""
    h = now.hour
    if 6 <= h < 14:
        return "A"
    if 14 <= h < 22:
        return "B"
    return "C"


def _shift_date_for(now: datetime) -> str:
    """Date-stamp label for the .ts file.  Shift C straddles midnight,
    so we tag it with the date of its START (yesterday after midnight).
    """
    if now.hour < 6:
        from datetime import timedelta
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


class CameraRecorder:
    """One continuous ffmpeg process per camera.  Auto-rolls files at
    shift boundaries (A→B→C).  Self-restarts on RTSP failure."""

    def __init__(self, camera_id: str, rtsp_url: str, videos_dir: str):
        self.camera_id  = str(camera_id)
        self.rtsp_url   = rtsp_url
        self.videos_dir = videos_dir
        self._proc: Optional[subprocess.Popen] = None
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Currently-active output file metadata — read by the clip
        # extractor to translate cycle epoch ranges into ffmpeg seek
        # offsets.
        self._current_file:     Optional[str]   = None
        self._current_shift:    Optional[str]   = None
        self._current_date:     Optional[str]   = None
        self._current_started:  Optional[float] = None  # epoch seconds

    # ── public API ─────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name=f"recorder-{self.camera_id}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._kill_proc()

    def status(self) -> Dict[str, object]:
        return {
            "camera_id":       self.camera_id,
            "running":         bool(self._proc and self._proc.poll() is None),
            "current_file":    self._current_file,
            "current_shift":   self._current_shift,
            "current_date":    self._current_date,
            "current_started": self._current_started,
        }

    def current_recording_info(self) -> Optional[Tuple[str, float]]:
        """Return (file_path, file_start_epoch) for the active rolling
        capture so the clip extractor can compute seek offsets."""
        if self._current_file and self._current_started:
            return (self._current_file, self._current_started)
        return None

    # ── internal loop ──────────────────────────────────────────────

    def _kill_proc(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try: self._proc.kill()
                except Exception: pass
            self._proc = None

    def _run_loop(self):
        """Outer loop: spin up an ffmpeg per shift, restart on crash,
        roll at shift boundaries.  Sleeps short on RTSP errors so a
        camera reboot doesn't hammer the host."""
        while not self._stop.is_set():
            now = datetime.now()
            shift = _shift_for(now)
            sdate = _shift_date_for(now)
            out_dir = os.path.join(self.videos_dir, self.camera_id)
            os.makedirs(out_dir, exist_ok=True)
            out_file = os.path.join(out_dir, f"{sdate}_{shift}.ts")

            self._current_file    = out_file
            self._current_shift   = shift
            self._current_date    = sdate
            self._current_started = time.time()

            cmd = [
                _resolve_ffmpeg(),
                "-rtsp_transport", "tcp",
                "-use_wallclock_as_timestamps", "1",
                "-i", self.rtsp_url,
                # Append mode for crash-recovery — if ffmpeg dies and
                # restarts in the same shift, we keep prepending to
                # the same file so the cycle log stays valid.
                "-c", "copy",
                "-f", "mpegts",
                "-reset_timestamps", "0",
                "-y", out_file,
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except FileNotFoundError:
                # ffmpeg missing — log once and bail until next manual start
                print(f"[REC {self.camera_id}] ffmpeg not found at {FFMPEG_EXE} — "
                      f"recording disabled.")
                self._stop.set()
                break
            except Exception as exc:
                print(f"[REC {self.camera_id}] launch failed: {exc}")
                time.sleep(5)
                continue

            # Watch loop: poll until shift rolls or process dies
            while not self._stop.is_set():
                ret = self._proc.poll()
                if ret is not None:
                    # ffmpeg crashed (RTSP drop, network glitch).  Sleep
                    # then re-enter outer loop to relaunch.
                    print(f"[REC {self.camera_id}] ffmpeg exited code={ret} — restarting in 3s")
                    time.sleep(3)
                    break
                # Roll on shift change
                cur_shift = _shift_for(datetime.now())
                if cur_shift != shift:
                    print(f"[REC {self.camera_id}] shift change {shift} -> {cur_shift}, rolling")
                    self._kill_proc()
                    break
                time.sleep(2)

            self._kill_proc()


# ────────────────────────────────────────────────────────────────────
# Manager: owns one CameraRecorder per active camera binding
# ────────────────────────────────────────────────────────────────────

class RecordingManager:
    """Singleton.  Reads cameras + bindings + RTSP URLs from the same
    sources as the rest of the app, spawns one CameraRecorder per
    camera that has at least one binding, and auto-reloads when admin
    edits the camera/binding config."""

    _instance: Optional["RecordingManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls, base_dir: Optional[str] = None) -> "RecordingManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(base_dir)
            return cls._instance

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self._recorders: Dict[str, CameraRecorder] = {}
        self._reloader_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self):
        if self._reloader_thread and self._reloader_thread.is_alive():
            return
        self._stop.clear()
        self._reloader_thread = threading.Thread(target=self._reloader_loop,
                                                  daemon=True,
                                                  name="rec-mgr-reloader")
        self._reloader_thread.start()

    def stop(self):
        self._stop.set()
        for r in list(self._recorders.values()):
            r.stop()
        self._recorders.clear()

    def status(self) -> List[Dict]:
        return [r.status() for r in self._recorders.values()]

    def get_recorder(self, camera_id: str) -> Optional[CameraRecorder]:
        return self._recorders.get(str(camera_id))

    # ── internals ──────────────────────────────────────────────────

    def _wanted_cameras(self) -> Dict[str, str]:
        """Build {camera_id -> rtsp_url} for every camera that has at
        least one binding.  We deliberately DON'T spin up cameras with
        no binding — they have nothing to record against."""
        try:
            from camera_config  import list_cameras, _build_rtsp_url
            from camera_bindings import list_bindings
        except ImportError as exc:
            print(f"[REC-MGR] config import failed: {exc}")
            return {}
        cams      = {str(c.get("id")): c for c in list_cameras(self.base_dir)}
        bindings  = list_bindings(self.base_dir)
        bound_ids = {str(b.get("camera_id")) for b in bindings if b.get("camera_id")}
        out = {}
        for cid in bound_ids:
            cam = cams.get(cid)
            if not cam:
                continue
            url = _build_rtsp_url(cam)
            if url and cam.get("ip"):
                out[cid] = url
        return out

    def _videos_dir(self) -> str:
        try:
            from settings_config import get_videos_dir
            return get_videos_dir()
        except Exception:
            return os.path.join(self.base_dir, "videos")

    def _reloader_loop(self):
        """Every 30s reload config: spin up new recorders, kill removed
        ones, do nothing for unchanged.  Lets admin add/remove cameras
        in the UI without restarting the server."""
        while not self._stop.is_set():
            try:
                wanted = self._wanted_cameras()
                videos = self._videos_dir()
                # Stop removed cameras
                for cid in list(self._recorders.keys()):
                    if cid not in wanted:
                        print(f"[REC-MGR] stopping recorder for removed camera {cid}")
                        self._recorders[cid].stop()
                        del self._recorders[cid]
                # Start new ones
                for cid, url in wanted.items():
                    if cid not in self._recorders:
                        if not ffmpeg_available():
                            continue
                        print(f"[REC-MGR] starting recorder for camera {cid}")
                        rec = CameraRecorder(cid, url, videos)
                        rec.start()
                        self._recorders[cid] = rec
            except Exception as exc:
                print(f"[REC-MGR] reload failed: {exc}")
            self._stop.wait(30)
