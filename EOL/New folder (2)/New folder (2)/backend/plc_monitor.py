from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import pymcprotocol

from camera_bindings import list_bindings
from camera_config import get_camera_rtsp_url
from cycle_state import end_cycle, get_machine_state, get_next_cycle_num, load_state, rotate_cycle, start_cycle
from plc_config import list_plcs
from recorder import DEFAULT_VIDEOS_DIR, append_cycle_metadata, ensure_metadata_file
from shift_boundary import current_shift_label, is_at_boundary
from zone_config import all_machines_flat


# Persistent state file — survives CMS restart so a service bounce
# during the boundary minute doesn't fire the wipe twice.
SHIFT_STATE_FILE = "shift_state.json"


# ── Video save-root resolver ──────────────────────────────────────────────
# Reads `video_config.json` on EVERY call so admin changes from the
# Camera Master UI ("Video Save Path") take effect immediately for new
# recordings — no service restart needed.  Resolution order:
#   1. video_config.json → save_path (set via /api/config/video-path)
#      → use it if non-empty AND the directory exists or can be created.
#   2. Else fall back to `<base_dir>/videos` (the original default).
# The returned path is guaranteed to exist on return (mkdir -p semantics).
def _resolve_videos_root(base_dir: str) -> str:
    import json as _json
    cfg_path = os.path.join(base_dir, "video_config.json")
    fallback = os.path.join(base_dir, DEFAULT_VIDEOS_DIR)
    try:
        with open(cfg_path, "r") as f:
            sp = (_json.load(f) or {}).get("save_path", "").strip()
        if sp:
            try:
                os.makedirs(sp, exist_ok=True)
            except Exception as e:
                print(f"[VIDEO-PATH] custom path mkdir failed ({sp!r}: {e}) "
                      f"— falling back to {fallback}")
                sp = ""
            if sp:
                # Runtime writability probe — drives can become read-only
                # (USB removed, ACL change) AFTER an admin saved the path.
                # Better to fall back to local default than spam permission
                # errors on every cycle.
                probe = os.path.join(sp, ".write_test_eol")
                try:
                    with open(probe, "w") as _f:
                        _f.write("ok")
                    os.remove(probe)
                    return sp
                except Exception as e:
                    print(f"[VIDEO-PATH] custom path NOT writable ({sp!r}: {e}) "
                          f"— falling back to {fallback}.  "
                          f"Update the Video Save Path from Camera Master to a writable folder.")
    except Exception:
        pass
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _get_ffmpeg() -> str:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


# ── Hardware-accelerated H.264 encoder picker ─────────────────────────────
# 2026-05-19 — Offload H.264 re-encoding from CPU (libx264) to whatever
# hardware encoder is actually available on this box.  Probed ONCE at
# process start by encoding a tiny synthetic clip and checking the
# encoder didn't error out; result cached for the life of the process.
#
# Priority order is deliberate:
#   1. h264_nvenc  — NVIDIA NVENC (we have an RTX A2000 12 GB; once the
#                    driver is updated to >= 551.76 the API 12.2 mismatch
#                    goes away and this becomes the fastest path).
#   2. h264_qsv    — Intel Quick Sync on the iGPU (UHD 770 here).  Works
#                    on current driver, ~4-5× realtime, ~5% CPU per stream.
#   3. libx264     — CPU fallback (the current default).  Always works
#                    but pegs cores.
#
# Each encoder's "preset / quality" flag-set is bundled in the cache
# entry so call sites just splice it into the existing ffmpeg cmd list.
# Quality target is preserved across all three (~CRF 23 equivalent).
_HW_ENCODER_CACHE: list = []   # [(codec, [flags...])]


def _probe_encoder(ffmpeg: str, codec: str, extra: list) -> bool:
    """Encode 1 s of color-bars with the given codec.  True if it
    produced any output bytes."""
    import subprocess as _sp, tempfile as _tf, os as _os
    out = _tf.NamedTemporaryFile(suffix=".mp4", delete=False)
    out.close()
    try:
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", "smptebars=size=320x240:rate=10",
            "-t", "1",
            "-c:v", codec,
            *extra,
            "-pix_fmt", "yuv420p",
            "-an", out.name,
        ]
        r = _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=10)
        return r.returncode == 0 and _os.path.getsize(out.name) > 0
    except Exception:
        return False
    finally:
        try: _os.remove(out.name)
        except Exception: pass


def _pick_hw_encoder() -> tuple:
    """Return (codec, [flags]) of the fastest available H.264 encoder.
    Result cached so probe runs only once per process."""
    if _HW_ENCODER_CACHE:
        return _HW_ENCODER_CACHE[0]

    ffmpeg = _get_ffmpeg()
    # (codec, probe_flags, runtime_flags)  — keep probe & runtime separate
    # because some encoders accept different parameter sets in headless probe
    # vs real RTSP pipeline.
    candidates = [
        # NVIDIA NVENC — p4 = medium preset, cq 23 = CRF-equivalent quality
        ("h264_nvenc", ["-preset", "p4", "-cq", "23"],
                       ["-preset", "p4", "-cq", "23"]),
        # Intel Quick Sync — global_quality 23 = CRF-equivalent
        ("h264_qsv",   ["-preset", "veryfast", "-global_quality", "23"],
                       ["-preset", "veryfast", "-global_quality", "23"]),
        # CPU fallback (always works)
        ("libx264",    ["-preset", "ultrafast", "-crf", "23"],
                       ["-preset", "ultrafast", "-crf", "23"]),
    ]
    for codec, probe_flags, runtime_flags in candidates:
        if codec == "libx264" or _probe_encoder(ffmpeg, codec, probe_flags):
            print(f"[ENCODER] picked {codec!r} for H.264 re-encoding")
            _HW_ENCODER_CACHE.append((codec, runtime_flags))
            return _HW_ENCODER_CACHE[0]
    # Truly unreachable — libx264 always passes — but be defensive.
    _HW_ENCODER_CACHE.append(("libx264", ["-preset", "ultrafast", "-crf", "23"]))
    return _HW_ENCODER_CACHE[0]


# Maximum realistic cycle duration (seconds).  Any cycle longer than this
# is almost certainly a stale-state artefact from a crashed previous session.
_MAX_CYCLE_SECONDS = 300   # 5 minutes


class PlcMonitor:
    def __init__(self, base_dir: str, csv_path: str, poll_interval: float = 0.03):
        # poll_interval was 0.3s — way too slow.  L108 / L109 OK/NG pulses
        # on the Mitsubishi PLC are typically 50-100 ms wide, so a 300 ms
        # poll missed roughly half the rising edges.  Symptom: the dashboard
        # showed a clean 52 s cycle, but plc_monitor recorded only 17 s
        # because it saw an intermediate (false) edge.  30 ms matches the
        # MES collector's poll rate and catches every pulse.
        self.base_dir      = base_dir
        self.csv_path      = csv_path
        self.poll_interval = poll_interval

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Live status — read by /api/plc-live-status
        self._last_values:       Dict[str, bool] = {}
        self._last_change_times: Dict[str, str]  = {}
        self._connected_plcs:    Dict[str, bool] = {}

        # Persistent pymcprotocol connections per PLC id
        self._plc_conns: Dict[str, pymcprotocol.Type4E] = {}
        self._plc_lock  = threading.Lock()
        # Protects _ensure_camera_recording from spawning two ffmpegs on
        # the same camera when the startup pre-starter and the watchdog
        # tick both call it within ~200 ms.  Symptom of the race was a
        # garbled / corrupted MP4 — both ffmpegs decoded the same RTSP
        # stream and produced corrupt H.264 frames.
        self._cam_spawn_lock = threading.Lock()
        # Back-off table — PLC id → unix ts before which we don't retry.
        # Mitsubishi PLCs allow a small number of simultaneous MC slots;
        # if another process (e.g. Phase2 collector) is already holding
        # the slot, we stop spamming "Cannot connect..." every 300 ms
        # and only retry every 60 s.
        self._plc_next_retry: Dict[str, float] = {}

        # Continuous per-camera MPEG-TS recorders:
        #   camera_id -> {"proc": Popen, "ts_file": str, "record_start": datetime}
        self._camera_workers: Dict[str, Dict] = {}

        # Per-cycle markers:
        #   machine_id -> {"camera_id", "ts_file", "record_start", "start_dt", "cycle_number"}
        self._video_workers: Dict[str, Dict] = {}

        # Machine metadata cache (zones/lines)
        self._machine_meta_cache: Optional[Dict[str, Dict]] = None
        self._machine_meta_ts: float = 0.0

        # Cap parallel ffmpeg cycle extractions.  Each extraction is a
        # full x264 re-encode that pegs one CPU core.  On a multi-machine
        # line (e.g. YNC-SS has machine_17 + machine_24 firing together)
        # unbounded threads were piling up and cascading into timeouts
        # where no video made it to disk.  2 in flight keeps the encoder
        # responsive and the Flask / RTSP feeds smooth.
        self._extract_sem = threading.Semaphore(2)

        # Auto-discovered set of camera_ids that belong to a sub-machine
        # in the EOL admin (Phase2 mes_plc_configs.nf2_camera_id).
        # Populated by a background thread that polls Phase2 every 60 s
        # so admin can add a new sub-machine via UI alone — no JSON
        # edits to camera_config_bindings.json are needed.
        self._sub_cameras: set = set()
        self._sub_camera_lock = threading.Lock()
        threading.Thread(
            target=self._refresh_sub_cameras_loop,
            name="sub-cam-refresh", daemon=True,
        ).start()

        # Pre-create the videos root (custom path from UI if set, else default).
        _resolve_videos_root(base_dir)

    # ─── Thread lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._reset_stale_machine_states()
        # Prestart used to run synchronously at import time: every bound
        # camera spawned a blocking FFmpeg → RTSP handshake (2–10 s each)
        # so Flask couldn't accept requests for ~10–50 s on boot.
        # Move it to its own thread — recorders warm up in the background
        # while Flask is already serving. First few PLC triggers before
        # warm-up will fall through the existing "TS not ready" guard.
        threading.Thread(
            target=self._prestart_camera_recorders,
            name="cam-prestart", daemon=True,
        ).start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plc-monitor", daemon=True)
        self._thread.start()

    def _prestart_camera_recorders(self) -> None:
        """Start continuous TS recorders for all cameras bound in config.
        This ensures the TS file already has content when the first PLC cycle fires,
        avoiding the short-video problem caused by RTSP connection startup delay."""
        import glob as _glob
        try:
            bindings = list_bindings(self.base_dir)
        except Exception:
            return
        # 2026-05-18 — KILL ZOMBIE FFMPEG.
        # Previous CMS process may have left orphan ffmpeg recorders alive
        # (Windows doesn't kill child processes when parent dies unless
        # job-objects are configured).  Those zombies keep writing to old
        # TS files, double-recording from each camera and blocking the
        # OS file lock so subsequent os.remove() fails silently.  Net
        # result: stale TS files for cycle-clip extraction + cam_worker
        # pointing at the WRONG TS file.
        # Cleanup: enumerate all ffmpeg.exe processes whose command line
        # references our videos folder, kill them, then remove TS files.
        try:
            import psutil as _ps
            videos_abs_for_kill = _resolve_videos_root(self.base_dir)
            videos_marker = os.path.normcase(os.path.abspath(videos_abs_for_kill))
            killed_n = 0
            for p in _ps.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (p.info.get("name") or "").lower()
                    if not name.startswith("ffmpeg"):
                        continue
                    cmd = p.info.get("cmdline") or []
                    joined = os.path.normcase(" ".join(cmd))
                    if videos_marker in joined:
                        p.kill()
                        killed_n += 1
                except Exception:
                    continue
            if killed_n:
                print(f"[PRESTART] killed {killed_n} zombie ffmpeg "
                      f"processes from previous session")
                import time as _time
                _time.sleep(1.0)   # give OS a moment to release file locks
        except ImportError:
            # psutil missing → best effort; still try to remove TS files,
            # any locked ones will simply skip below.
            pass
        except Exception as _exc:
            print(f"[PRESTART] zombie-kill skipped: {_exc}")

        # Clean up orphaned TS files left by previous sessions
        videos_abs = _resolve_videos_root(self.base_dir)
        for old_ts in _glob.glob(os.path.join(videos_abs, "cam_*.ts")):
            try:
                os.remove(old_ts)
            except OSError:
                pass
        # Clean up orphaned _pending_*.mp4 files — these are stale
        # extraction temp files that never got renamed because their
        # ffmpeg was killed by a timeout and the subsequent os.remove()
        # was blocked by a Windows file lock.  They waste disk and show
        # up confusingly in the videos/ tree.
        pending_removed = 0
        for old_pending in _glob.glob(
                os.path.join(videos_abs, "**", "_pending_*.mp4"),
                recursive=True):
            try:
                os.remove(old_pending)
                pending_removed += 1
            except OSError:
                pass
        if pending_removed:
            print(f"[PLC] Startup cleanup: removed {pending_removed} orphan _pending_ files")
        started: set = set()
        for b in bindings:
            cid = str(b.get("camera_id", "")).strip()
            if cid and cid not in started:
                self._ensure_camera_recording(cid)
                started.add(cid)
                print(f"[PLC] Pre-started TS recorder for camera {cid}")

    def _reset_stale_machine_states(self) -> None:
        """
        On startup: if any machine's JSON state shows recording=True but the
        start_time is older than _MAX_CYCLE_SECONDS, the previous Flask session
        crashed mid-cycle.  Reset to avoid computing a huge duration when the
        next PLC trigger fires.
        """
        now = datetime.now()
        try:
            all_states = load_state(self.base_dir)
        except Exception:
            return
        for machine_id, state in all_states.items():
            if not state.get("recording"):
                continue
            start_str = state.get("start_time", "")
            if not start_str:
                end_cycle(machine_id, self.base_dir)
                print(f"[PLC] Reset stale recording state for {machine_id} (no start_time)")
                continue
            try:
                start_dt = datetime.fromisoformat(start_str)
                elapsed  = (now - start_dt).total_seconds()
                if elapsed > _MAX_CYCLE_SECONDS:
                    end_cycle(machine_id, self.base_dir)
                    print(
                        f"[PLC] Reset stale recording state for {machine_id} "
                        f"(was recording for {elapsed:.0f}s since {start_str})"
                    )
            except (ValueError, TypeError):
                end_cycle(machine_id, self.base_dir)
                print(f"[PLC] Reset stale recording state for {machine_id} (bad start_time)")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._plc_lock:
            for conn in self._plc_conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._plc_conns.clear()
        # Stop continuous camera recorders
        for cam in list(self._camera_workers.values()):
            self._kill_cam(cam)
        self._camera_workers.clear()
        self._video_workers.clear()

    # ─── Main poll loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                print(f"[PLC] Poll loop error: {exc}")
            self._stop.wait(self.poll_interval)

    def _poll_once(self) -> None:
        # ──────────────────────────────────────────────────────────────────────
        # PLC bit-polling has moved to the MES collector
        # (Phase2/collector_engine.py).  This monitor used to open its OWN
        # MC4E TCP connection to 192.168.10.150:5002 and read the same
        # L108/L109 bits the collector reads; Mitsubishi Q-series only
        # accept a couple of simultaneous TCP clients on that port, so
        # the two services fought for the slot and ~half the rising edges
        # silently dropped on whichever side got squeezed.
        #
        # The collector is now the only PLC client, and on every L108/L109
        # rising edge it POSTs us /api/plc-edge.  That handler in
        # api_server.py calls _trigger_binding() with the SAME flow this
        # loop used to trigger directly.  Net effect: zero PLC packets
        # from CMS, zero contention, no missed edges.
        #
        # We KEEP the camera-recorder watchdog (below) running, because it's
        # responsible for spawning ffmpeg per camera and rolling .ts files —
        # nothing to do with PLC polling.
        # ──────────────────────────────────────────────────────────────────────

        bindings = list_bindings(self.base_dir)

        # ── Camera recorder watchdog ──────────────────────────────────────────
        # Restart any dead recorders every ~10 polls (≈ 3 seconds).
        # If a recorder dies within 10 s of starting (i.e. the camera is
        # offline / RTSP unreachable) we exponentially back off retries
        # up to 5 min, and log only on state transitions — otherwise the
        # console was flooded with "died — restarting" every 3 s forever
        # and every attempt spawned a new orphan TS file.
        self._watchdog_tick = getattr(self, "_watchdog_tick", 0) + 1
        if not hasattr(self, "_cam_fail_state"):
            self._cam_fail_state: Dict[str, Dict] = {}
        if self._watchdog_tick >= 10:
            self._watchdog_tick = 0
            camera_ids = {str(b.get("camera_id", "")).strip() for b in bindings}
            now_ts = time.time()
            for cid in camera_ids:
                if not cid:
                    continue
                cam = self._camera_workers.get(cid)
                if cam and cam["proc"].poll() is not None:
                    # Measure how long this recorder survived before dying.
                    started_at = cam.get("record_start")
                    alive_s = 0.0
                    if started_at:
                        try:
                            alive_s = (datetime.now() - started_at).total_seconds()
                        except Exception:
                            pass
                    del self._camera_workers[cid]

                    st = self._cam_fail_state.setdefault(
                        cid, {"fails": 0, "next_try": 0.0, "announced": False})

                    if alive_s >= 30:
                        # Real recording that dropped → reset back-off, retry now.
                        st["fails"]    = 0
                        st["next_try"] = 0
                        st["announced"] = False
                        print(f"[PLC] Camera recorder {cid} died — restarting "
                              f"(was up {alive_s:.0f}s)")
                        self._ensure_camera_recording(cid)
                    else:
                        # Died almost immediately → camera probably offline.
                        st["fails"] += 1
                        # 5 s → 15 s → 60 s → 120 s → 300 s (cap)
                        cool_down = min(5 * (3 ** min(st["fails"] - 1, 4)), 300)
                        st["next_try"] = now_ts + cool_down
                        if not st["announced"]:
                            print(f"[PLC] Camera {cid} appears OFFLINE "
                                  f"(died after {alive_s:.0f}s). "
                                  f"Backing off retries to every {int(cool_down)}s.")
                            st["announced"] = True
                elif cam is None:
                    # No recorder active — either just never started, or
                    # waiting in back-off. Retry if cool-down elapsed.
                    st = self._cam_fail_state.get(cid)
                    if st and now_ts < st.get("next_try", 0):
                        continue
                    if self._ensure_camera_recording(cid):
                        # Successful spawn (doesn't yet mean RTSP connected)
                        if st:
                            st["announced"] = False

            # ── ZOMBIE REAPER (2026-05-18, fixed 2026-05-19) ──────────
            # Walk every ffmpeg process on the box; any whose command
            # line references "cam_<id>" but does NOT match the PID we
            # currently believe is its recorder gets killed.  Without
            # this, a missed `_kill_cam` (timeout, ignored SIGTERM, etc.)
            # leaves multiple ffmpegs writing to the same TS file,
            # corrupting both and producing 0-byte cycle clips.
            #
            # 2026-05-19 — CRITICAL BUG FIX.
            # The previous match condition (`cam_<cid>_` in cmdline) was
            # too greedy.  Cycle EXTRACTOR ffmpegs ALSO contain
            # `cam_<cid>_` because they read from `cam_<cid>_<ts>.ts`
            # as INPUT.  The reaper was killing every cycle extraction
            # mid-flight, producing:
            #   [PLC] ZOMBIE-REAPER killing extra ffmpeg PID=X for cam_Y
            #   [PLC] Extraction error: [WinError 5] Access is denied
            #   [PLC] Cycle #NNN done ... video=none
            # Result: ZERO cycle clips generated for 6+ hours.
            #
            # Fix: only reap ffmpegs that are RECORDERS — they have
            # `-i rtsp://` in their cmdline.  Extractors have `-i
            # <ts_file>` (a file path, not an rtsp URL).  This single
            # extra check makes the reaper kill ONLY real duplicate
            # recorders and leaves cycle extractors alone.
            try:
                import psutil as _ps
                live_pids = {cid: w["proc"].pid for cid, w
                              in self._camera_workers.items()}
                for p in _ps.process_iter(["pid", "name", "cmdline"]):
                    try:
                        name = (p.info.get("name") or "").lower()
                        if not name.startswith("ffmpeg"):
                            continue
                        cmd = " ".join(p.info.get("cmdline") or [])
                        # CRITICAL: only consider RECORDERS (rtsp input).
                        # Extractors read from a .ts file, not rtsp.
                        if "-i rtsp://" not in cmd:
                            continue
                        # Find which camera this ffmpeg is for
                        for cid in camera_ids:
                            if not cid:
                                continue
                            # Match "cam_<cid>_" in the cmdline (the TS
                            # filename our recorder always writes)
                            if f"cam_{cid}_" not in cmd:
                                continue
                            live_pid = live_pids.get(cid)
                            if live_pid != p.info["pid"]:
                                print(f"[PLC] ZOMBIE-REAPER killing extra "
                                      f"ffmpeg PID={p.info['pid']} for "
                                      f"{cid} (live PID={live_pid})")
                                p.kill()
                            break
                    except Exception:
                        continue
            except ImportError:
                pass        # psutil missing → reaper offline (best-effort)
            except Exception as _zexc:
                print(f"[PLC] zombie reaper warning: {_zexc}")
        # ─────────────────────────────────────────────────────────────────────

        # ── Shift-boundary cleanup for SUB cameras ───────────────────────────
        # User requirement: continuous TS for SUB cameras is scoped to one
        # shift only.  At each shift start (from shifts.json) we stop the
        # ffmpeg, delete the .ts file, and let the watchdog above respawn
        # a fresh recorder on the next tick.  MAIN cameras are skipped
        # entirely — their per-cycle MP4 (barcode-named) is the long-term
        # artefact and the rolling TS must keep flowing across the boundary
        # so we don't lose an in-flight cycle.
        try:
            self._check_shift_boundary(bindings)
        except Exception as exc:
            print(f"[PLC] shift-boundary check error: {exc}")
        # ─────────────────────────────────────────────────────────────────────

        # PLC bit polling intentionally removed — see big comment at the top
        # of this method.  Edges now arrive via the /api/plc-edge webhook
        # from the MES collector, which calls _trigger_binding() the same
        # way this loop used to.  Mark every CMS PLC as "connected" so
        # the admin dashboard's bit-watch widget keeps showing OK status
        # — connectivity is now a property of the collector, not us.
        for plc in (list_plcs(self.base_dir) or []):
            self._connected_plcs[str(plc.get("id", "")).strip()] = True

        # `bindings` is loaded above so the camera-recorder watchdog has
        # the up-to-date binding list.  We don't iterate over it here —
        # nothing to do until a webhook fires.
        return

    # ─── Part code from PLC word registers ──────────────────────────────────

    # Node-RED flow reads D5004 with 13 registers.  Each 16-bit register holds
    # two ASCII characters in byte-reversed order (low byte first, high byte second).
    _PART_CODE_ADDR = "D5004"
    _PART_CODE_LEN  = 13          # 13 registers → up to 26 ASCII chars

    def _read_part_code(self, plc_id: str) -> str:
        """Read part-code string from PLC word registers, matching the Node-RED
        byte-reversed ASCII conversion."""
        conn = self._plc_conns.get(plc_id)
        if conn is None:
            return ""
        try:
            regs = conn.batchread_wordunits(
                headdevice=self._PART_CODE_ADDR,
                readsize=self._PART_CODE_LEN,
            )
        except Exception as exc:
            print(f"[PLC] Part code read error ({plc_id}): {exc}")
            return ""
        # Byte-reversed ASCII: low byte first, high byte second (same as Node-RED)
        chars = []
        for reg in regs:
            high_byte = reg & 0xFF           # low byte of register → first char
            low_byte  = (reg >> 8) & 0xFF    # high byte of register → second char
            if high_byte > 0:
                chars.append(chr(high_byte))
            if low_byte > 0:
                chars.append(chr(low_byte))
        part_code = "".join(chars).strip().strip("\x00")
        if part_code:
            print(f"[PLC] Part code: {part_code!r}")
        return part_code

    # ─── Trigger binding (rising edge handler) ────────────────────────────────

    def _trigger_binding(self, binding: Dict,
                          edge_dt: Optional[datetime] = None) -> None:
        """When MES sends an edge webhook it carries the PLC-accurate
        timestamp (`epoch_ms`/`iso`).  The /api/plc-edge handler converts
        that to a datetime and passes it in here so cycle duration math
        uses the true PLC pulse times instead of HTTP-arrival-at-CMS,
        which can jitter by tens of seconds under load and was producing
        videos shorter than the chart's CT (52 s cycle → 32 s clip).
        Manual / legacy callers without an edge timestamp fall back to
        wall clock."""
        edge_dt = edge_dt or datetime.now()

        machine_id = str(binding.get("machine_id", "")).strip()
        camera_id  = str(binding.get("camera_id",  "")).strip()
        if not machine_id:
            return

        ensure_metadata_file(self.csv_path)
        current       = get_machine_state(machine_id, self.base_dir)
        next_cycle    = get_next_cycle_num(self.csv_path)
        current_cycle = int(current.get("cycle_number") or 0)
        if current_cycle >= next_cycle:
            next_cycle = current_cycle + 1

        meta = self._get_machine_meta(machine_id)
        plc_id = str(binding.get("plc_id", "")).strip()

        if current.get("recording"):
            # ── Cycle ENDED ───────────────────────────────────────────────────
            start_iso = current.get("start_time", "")
            end_dt    = edge_dt
            try:
                start_dt = datetime.fromisoformat(start_iso) if start_iso else end_dt
            except ValueError:
                start_dt = end_dt
            duration_s = max(0, int((end_dt - start_dt).total_seconds()))
            # 2026-05-14 diagnostic — operator reports 2 s videos for
            # 28 s cycles.  Log the exact PLC times we used so we can
            # see whether duration_s is wrong (start_dt/end_dt mismatch)
            # or downstream extraction is misreading the clip.
            print(f"[PLC-DURATION] {machine_id} #{current_cycle}: "
                  f"start_iso={start_iso!r} -> start_dt={start_dt.isoformat()} "
                  f"end_dt={end_dt.isoformat()} delta={duration_s}s "
                  f"edge_dt_was={edge_dt.isoformat()}")

            # Read part code from PLC at cycle-end (freshest value).
            # When called from /api/plc-edge, the webhook handler stuffs
            # the part_code MES already read into `self._next_part_code`
            # for the matching plc_id — we use that and skip a redundant
            # PLC read (CMS no longer holds a TCP slot, so a direct read
            # here would fail anyway).
            #
            # Two key shapes: legacy plc_id (from plcs.json) and the new
            # `ip:<addr>` key written by the IP-match path in /api/plc-edge.
            # Try both before falling back to a PLC read.
            ppc = getattr(self, "_next_part_code", {}) or {}
            override = ppc.pop(plc_id, None)
            if override is None:
                # New MES-driven binding carries plc_ip — try the IP key
                plc_ip_key = f"ip:{str(binding.get('plc_ip', '')).strip()}"
                if plc_ip_key != "ip:":
                    override = ppc.pop(plc_ip_key, None)
            if override is not None:
                part_code = override
            else:
                part_code = self._read_part_code(plc_id) if plc_id else ""

            # Pop cycle marker immediately (non-blocking)
            worker = self._video_workers.pop(machine_id, None)

            # Start next cycle + new marker right away.  Use the PLC-side
            # edge_dt so the next cycle's start_time matches the actual
            # pulse moment (this becomes the *start* of the upcoming cycle
            # whose duration we'll compute on the NEXT edge).
            state = rotate_cycle(machine_id, next_cycle, self.base_dir,
                                  start_time=edge_dt)
            print(f"[PLC] Started next cycle #{state.get('cycle_number')} for {machine_id}")
            self._start_video(machine_id, camera_id, next_cycle, marker_dt=edge_dt)

            # Per-binding extract policy.
            #   1) explicit binding flag wins (admin override)
            #   2) else auto-detect: if the camera is configured as
            #      `nf2_camera_id` for any Phase2 sub-machine, skip MP4
            #      and let /api/submachine/clip serve on-demand TS slices.
            #   3) else default = per-cycle MP4 (main-machine behaviour)
            if "extract_per_cycle" in binding:
                extract_per_cycle = bool(binding["extract_per_cycle"])
            else:
                with self._sub_camera_lock:
                    is_sub_cam = camera_id in self._sub_cameras
                extract_per_cycle = not is_sub_cam

            # Extract cycle video + write CSV in background
            t = threading.Thread(
                target=self._finalize_cycle,
                args=(worker, current_cycle, start_dt, end_dt,
                      duration_s, machine_id, meta, part_code,
                      extract_per_cycle),
                daemon=True,
            )
            t.start()

        else:
            # ── First trigger — mark cycle start ──────────────────────────────
            state = start_cycle(machine_id, next_cycle, self.base_dir,
                                 start_time=edge_dt)
            print(f"[PLC] Started cycle #{state.get('cycle_number')} for {machine_id} (first trigger)")
            self._start_video(machine_id, camera_id, next_cycle, marker_dt=edge_dt)

    # ─── Finalize cycle (background thread) ──────────────────────────────────

    def _finalize_cycle(
        self,
        worker: Optional[Dict],
        cycle_number: int,
        start_dt: datetime,
        end_dt: datetime,
        duration_s: int,
        machine_id: str,
        meta: Dict,
        part_code: str = "",
        extract_per_cycle: bool = True,
    ) -> None:
        """Extract cycle clip from the MPEG-TS rolling file, then write CSV row.
        Cycle = previous OK/NG pulse → next OK/NG pulse, full duration.
        No upper cap — even multi-minute slow cycles get the full clip."""
        file_rel = ""
        cycle_duration_s = (end_dt - start_dt).total_seconds()
        if not extract_per_cycle:
            # Sub-machine binding — keep the shift-long TS rolling but
            # don't write a per-cycle MP4. The Phase2 sub-machine UI
            # trims a slice from the TS via /api/submachine/clip on click.
            print(f"[PLC] Cycle #{cycle_number} ({machine_id}) "
                  f"extract_per_cycle=false → skipping MP4 (TS continues)")
        elif worker and worker.get("ts_file"):
            # Wait for the encoder to flush end_dt content into the TS file.
            # 25 s gives Windows' file-system cache time to refresh AND the
            # RTSP encoder time to write everything past end_dt.  Was 12 s
            # but the operator hit Errno-EOF mid-clip on Windows where the
            # read-handle's cached file-size lagged the writer's append.
            time.sleep(25)
            # Throttle concurrent ffmpeg jobs (2 at a time). When the line
            # is hot and many machines fire together, this prevents the
            # thread pile-up that caused every extraction to time out.
            with self._extract_sem:
                file_rel = self._extract_cycle(
                    ts_file=worker["ts_file"],
                    record_start=worker["record_start"],
                    start_dt=start_dt,
                    end_dt=end_dt,
                    cycle_number=cycle_number,
                    machine_id=machine_id,
                    ts_cycle_start=worker.get("ts_cycle_start"),
                    part_code=part_code,
                )

        try:
            append_cycle_metadata(
                csv_path=self.csv_path,
                cycle_number=cycle_number,
                start_dt=start_dt,
                end_dt=end_dt,
                relative_file_path=file_rel,
                machine_id=machine_id,
                machine_name=meta.get("machine_name", ""),
                line_name=meta.get("line_name", ""),
                zone_name=meta.get("zone_name", ""),
                tag="",
                part_code=part_code,
            )
            # 2026-05-19 — Operator-readable structured summary.
            # Earlier compact one-line form ("video=none" vs
            # "video=<path>") was hard to scan during shift review.  Now
            # each cycle prints a 4-line block with clear OK/FAIL status
            # so the operator can grep / eyeball "VIDEO MISSING" cases
            # without parsing the whole log.
            _video_ok = bool(file_rel) and not (file_rel or "").lower().startswith("none")
            _video_status = "OK    saved to " + (file_rel or "?") if _video_ok else "FAIL  no MP4 generated"
            _video_mark   = "[OK]" if _video_ok else "[--]"
            print(
                f"\n"
                f"+--- CYCLE #{cycle_number} -------------------------------------------\n"
                f"|  machine    : {meta.get('machine_name', '?')}\n"
                f"|  part_code  : {part_code or '(none)'}\n"
                f"|  duration   : {duration_s}s\n"
                f"|  video      : {_video_mark}  {_video_status}\n"
                f"+--------------------------------------------------------------"
            )
        except Exception as exc:
            print(f"[PLC] CSV write error: {exc}")

        # Clean up the TS file if nothing else needs it anymore
        if worker and worker.get("ts_file"):
            self._cleanup_old_ts(worker["ts_file"])

    # ─── Sub-camera auto-discovery ────────────────────────────────────────
    def _refresh_sub_cameras_loop(self) -> None:
        """Poll Phase2's /api/sub-cameras every 60 s and update the local
        set. New sub-machines added via Phase2 admin become 'known' here
        within a minute — no Flask restart, no JSON file edit."""
        import urllib.request, json as _json
        url = "http://127.0.0.1:8080/api/sub-cameras"
        while True:
            try:
                with urllib.request.urlopen(url, timeout=4) as resp:
                    body = _json.loads(resp.read().decode("utf-8"))
                ids = set(body.get("camera_ids") or [])
                with self._sub_camera_lock:
                    changed = (ids != self._sub_cameras)
                    if changed:
                        added   = ids - self._sub_cameras
                        removed = self._sub_cameras - ids
                        self._sub_cameras = ids
                if changed and (added or removed):
                    print(f"[SUB-CAM] auto-detected change "
                          f"+{sorted(added)} -{sorted(removed)} "
                          f"(now: {sorted(ids)})")
            except Exception:
                pass  # Phase2 not up yet — try again in 60 s
            time.sleep(60)

    # ─── Continuous per-camera MPEG-TS recorder ───────────────────────────────

    def _ensure_camera_recording(self, camera_id: str) -> Optional[Dict]:
        """
        Ensure a continuous H.264/MPEG-TS recording is running for this camera.
        Returns the camera worker dict, or None if unavailable.
        Each keyframe is forced every 1 second so we can seek accurately.

        Concurrency:  guarded by `_cam_spawn_lock` so the pre-starter on
        boot and the watchdog tick can't both Popen ffmpeg in the
        ~150 ms gap between the alive-check and the dict-write.  The
        old race produced two ffmpegs feeding the same RTSP stream,
        which corrupted both .ts files and made every clip render with
        FFT-style scrambled frames.
        """
        with self._cam_spawn_lock:
            # Check if still alive
            if camera_id in self._camera_workers:
                cam = self._camera_workers[camera_id]
                if cam["proc"].poll() is None:
                    return cam
                print(f"[PLC] Camera recorder for {camera_id} died, restarting...")
                del self._camera_workers[camera_id]

            rtsp_url = get_camera_rtsp_url(camera_id, self.base_dir)
            if not rtsp_url:
                return None

            videos_abs = _resolve_videos_root(self.base_dir)
            os.makedirs(videos_abs, exist_ok=True)
            ts_file = os.path.join(videos_abs, f"cam_{camera_id}_{int(time.time()*1000)}.ts")

            ffmpeg = _get_ffmpeg()
            # Capture stderr to a per-camera log file so silent RTSP/encoder failures
            # are visible. Without this, the recorder dies and we have no idea why.
            cam_log_path = os.path.join(videos_abs, f"_cam_{camera_id}.log")
            cmd = [
                ffmpeg, "-y",
                # RTSP robustness. Note: -stimeout was removed in ffmpeg 7.x and
                # replaced with -timeout. -reconnect flags are HTTP-only and will
                # make ffmpeg exit instantly if passed with RTSP input.
                "-rtsp_transport", "tcp",
                "-timeout",  "10000000",   # 10 s socket I/O timeout (microseconds)
                # 2026-05-18 — DECODER RESILIENCE.
                # Panasonic main-stream is HEVC (H.265), and the camera's
                # bitrate is high enough that RTSP-over-TCP occasionally
                # delivers packets out of order or with HEVC POC errors.
                # The decoder log was filled with:
                #   [hevc] Could not find ref with POC <n>
                #   [hevc] cu_qp_delta NN outside valid range
                #   [hevc] Skipping invalid undecodable NALU
                # → re-encoder fed partial frames → output looked grey.
                # Tighten the decoder so transient errors are concealed
                # locally instead of cascading through whole GOPs.
                "-err_detect",       "ignore_err",
                "-skip_frame",       "default",       # don't skip extra frames
                "-ec",               "favor_inter+guess_mvs+deblock",
                "-probesize",        "5000000",       # 5 MB → let decoder
                "-analyzeduration",  "5000000",       #   fully resolve stream params
                "-fflags",  "+genpts+discardcorrupt", # rebuild PTS, drop bad packets
                "-i", rtsp_url,
                # H.264 transcode — force keyframe every 1 s at 20 fps so
                # `-c copy` clip extraction lands within ±1 s of the cycle
                # boundary.  Earlier this was 2 s (-g 40) and we saw clips
                # drifting up to 4 s long for 28 s cycles, which the spec
                # forbids (+/- 1 s).  Trade-off: ~10-15 % larger .ts files,
                # acceptable on the F:\ external HDD.
                "-c:v", "libx264",
                # superfast (was ultrafast) — operator reported pixel
                # bursting / macroblocking on shop-floor TV.  ultrafast
                # disables rate-distortion optimisation entirely; at 1920
                # x1080 that produces visible blocks on motion edges even
                # at CRF 23.  superfast enables basic RDO, costs ~20-30%
                # more CPU on the encode but produces a clean picture.
                # Still real-time safe on this server.
                "-preset", "superfast",
                "-tune",   "zerolatency",
                # ── Shop-floor LED TV compatibility (2026-05-12) ──────
                # Without these two flags, recorded cycles fail to render
                # on Samsung Tizen / LG WebOS TV browsers:
                #   yuvj420p (PC full-range 0-255) → most TV decoders
                #                                    refuse, prefer yuv420p
                #                                    (tv-range 16-235).
                #   2304x1296 (camera native)      → exceeds H.264 Level
                #                                    4.0 (1920x1080) which
                #                                    TV hardware decoders
                #                                    enforce strictly.
                # 2026-05-18 — Sub-stream (704x576 / 4:3 from Panasonic
                # cameras) needs upscaling so the dashboard's 16:9 video
                # box looks crisp instead of letterboxed-tiny.  Lanczos
                # at output 1280x720 gives sharp text on TB labels +
                # cylinder markings while keeping the source aspect ratio
                # intact.  unsharp filter then adds a mild edge sharpen
                # so part numbers stay readable on the shop-floor TV.
                # If the camera ever returns >= 1280 wide we keep it as-is.
                # format=yuv420p MUST be chained inside -vf — a separate
                # -pix_fmt is ignored when -vf is also present, and would
                # leave the camera's native yuvj420p (PC full-range 0-255)
                # which most TV decoders reject.
                "-vf",
                "scale='if(gt(iw,1280),iw,1280)':'-2':flags=lanczos,"
                "unsharp=5:5:0.8:3:3:0.4,"
                "format=yuv420p",
                "-color_range", "tv",
                "-level", "4.0",           # 1080p @ ≤25 Mbps cap
                # CRF 23 — visually-lossless quality at 1080p.  Earlier
                # value was 28 which at 1920x1080 produced macroblocking
                # on machine details (small text on parts, etc.).  CRF 23
                # bumps bitrate ~3x but file sizes stay reasonable (15-20MB
                # for a 15s cycle vs the 100+ MB of the old 2304x1296 stream).
                "-crf", "23",
                "-r", "20",
                "-g", "20",           # keyframe every 20 frames = 1 s
                "-keyint_min", "20",
                "-sc_threshold", "0", # no scene-change keyframes (keeps interval strict)
                "-an",
                "-f", "mpegts",
                ts_file,
            ]
            record_start = datetime.now()
            try:
                cam_log = open(cam_log_path, "wb", buffering=0)   # overwrite, not append
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=cam_log,
                )
                cam = {
                    "proc":         proc,
                    "ts_file":      ts_file,
                    "record_start": record_start,
                    "write_start":  None,   # set by background thread once ffmpeg starts writing
                }
                self._camera_workers[camera_id] = cam
                # Background thread: detect when ffmpeg actually starts writing frames
                t = threading.Thread(
                    target=self._detect_write_start,
                    args=(cam, ts_file),
                    daemon=True,
                )
                t.start()
                print(f"[PLC] Continuous TS recording started: {ts_file}")
                return cam
            except Exception as exc:
                print(f"[PLC] Camera ffmpeg launch error: {exc}")
                return None

    def _detect_write_start(self, cam: Dict, ts_file: str) -> None:
        """Poll the TS file until ffmpeg has written at least 64 KB, then record write_start.
        This captures the exact wall-clock time when real video content starts flowing,
        eliminating the need to guess the RTSP startup delay when seeking later."""
        deadline = time.monotonic() + 60  # give up after 60 s
        while time.monotonic() < deadline:
            try:
                if os.path.exists(ts_file) and os.path.getsize(ts_file) >= 65536:
                    cam["write_start"] = datetime.now()
                    print(f"[PLC] write_start detected for {ts_file}")
                    return
            except OSError:
                pass
            time.sleep(0.2)
        print(f"[PLC] write_start timeout for {ts_file} — will use elapsed fallback")

    def _kill_cam(self, cam: Dict) -> None:
        proc = cam.get("proc")
        if not proc:
            return
        try:
            proc.stdin.write(b'q\n')
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # ─── Shift-end cleanup for SUB cameras ────────────────────────────────────

    def _shift_state_path(self) -> str:
        return os.path.join(self.base_dir, SHIFT_STATE_FILE)

    def _load_shift_state(self) -> Dict:
        """Persisted record of which shift we've already cleaned up for.
        Survives CMS restarts so a bounce during the boundary minute
        doesn't fire the wipe twice."""
        import json as _json
        p = self._shift_state_path()
        try:
            with open(p, "r", encoding="utf-8") as f:
                return _json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def _save_shift_state(self, data: Dict) -> None:
        import json as _json
        try:
            with open(self._shift_state_path(), "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2)
        except OSError as exc:
            print(f"[PLC] shift_state save failed: {exc}")

    def _check_shift_boundary(self, bindings: List[Dict]) -> None:
        """Called once per poll.  Throttled internally to fire at most
        once per shift, identified by current_shift_label()."""
        now = datetime.now()

        # Cheap minute-level throttle so we don't load JSON 30 times/sec.
        last_minute_checked = getattr(self, "_shift_check_minute", -1)
        cur_minute = now.hour * 60 + now.minute
        if cur_minute == last_minute_checked:
            return
        self._shift_check_minute = cur_minute

        if not is_at_boundary(now, self.base_dir):
            return

        label = current_shift_label(now, self.base_dir)
        state = self._load_shift_state()
        if state.get("last_fired_shift_label") == label:
            return    # already cleaned for this shift

        # 2026-05-14 — extended from "SUB cameras only" to "ALL cameras".
        # Operator requirement: at every shift start, rotate every camera's
        # TS file (MAIN + SUB) AND wipe all past-shift mp4s.  A TS file
        # that's been recording for 10+ hours accumulates PCR drift which
        # makes ffmpeg deep-seek emit empty / unplayable clips (the bug
        # that was producing 200-600 KB MP4s with 0:00 duration in the UI).
        # Rotating per shift keeps each TS file under ~8 hours = clean
        # keyframe alignment for every extraction.
        all_cam_ids: List[str] = []
        for b in bindings:
            cid = str(b.get("camera_id", "")).strip()
            if cid and cid not in all_cam_ids:
                all_cam_ids.append(cid)

        if not all_cam_ids:
            # Nothing to wipe — still record the label so we don't keep
            # checking the binding list every minute for the whole shift.
            state["last_fired_shift_label"] = label
            self._save_shift_state(state)
            return

        print(f"[PLC] Shift boundary reached ({label}) — rotating "
              f"{len(all_cam_ids)} camera TS file(s) + wiping past-shift mp4s")
        # _wipe_sub_camera_state name is now misleading — it really just
        # stops the recorder + deletes its TS for any camera ID passed in.
        # Works identically for MAIN and SUB; the watchdog tick respawns.
        self._wipe_sub_camera_state(all_cam_ids)
        self._wipe_past_shift_mp4s()

        state["last_fired_shift_label"] = label
        state["last_fired_at"] = now.isoformat(timespec="seconds")
        state["wiped_cameras"] = all_cam_ids
        self._save_shift_state(state)

    def _wipe_sub_camera_state(self, camera_ids: List[str]) -> None:
        """Stop ffmpeg + delete .ts for the given SUB cameras.  The
        watchdog tick (every 3 s) will respawn fresh recorders on its
        next pass — no need to call _ensure_camera_recording from here."""
        for cid in camera_ids:
            cam = self._camera_workers.pop(cid, None)
            ts_file = cam.get("ts_file") if cam else None
            if cam:
                try:
                    self._kill_cam(cam)
                except Exception as exc:
                    print(f"[PLC] shift-wipe kill failed for {cid}: {exc}")
            if ts_file:
                try:
                    os.remove(ts_file)
                    print(f"[PLC] shift-wipe deleted {ts_file}")
                except OSError as exc:
                    print(f"[PLC] shift-wipe delete failed for {ts_file}: {exc}")
            # Drop any in-flight cycle markers for machines bound to this
            # camera — they reference a TS file that no longer exists, so
            # the next clip extraction would point at thin air.
            stale_markers = [
                mid for mid, vw in self._video_workers.items()
                if vw.get("camera_id") == cid
            ]
            for mid in stale_markers:
                self._video_workers.pop(mid, None)

    def _wipe_past_shift_mp4s(self) -> None:
        """Delete every cycle .mp4 from the videos directory at shift boundary.
        Operator-requested clean slate per shift — combined with TS rotation
        this guarantees every clip the next shift sees is freshly extracted
        from a young TS file, no PCR-drift empties."""
        videos_root = os.path.join(self.base_dir, "videos")
        if not os.path.isdir(videos_root):
            return
        deleted = 0
        freed   = 0
        for root, _dirs, files in os.walk(videos_root):
            for f in files:
                if not f.lower().endswith(".mp4"):
                    continue
                p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(p)
                    os.remove(p)
                    deleted += 1
                    freed   += sz
                except OSError:
                    pass
        # Prune now-empty subdirectories so the tree stays tidy
        for root, _dirs, _files in os.walk(videos_root, topdown=False):
            if root == videos_root:
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
        mb = freed / 1024 / 1024
        print(f"[PLC] shift-wipe removed {deleted} mp4 ({mb:.1f} MB)")

    # ─── Per-cycle video marker ────────────────────────────────────────────────

    def _start_video(self, machine_id: str, camera_id: str, cycle_number: int,
                      marker_dt: Optional[datetime] = None) -> None:
        """Mark cycle start — the camera TS recorder is already running.
        `marker_dt`, when supplied, is the PLC-accurate edge time from the
        MES webhook (epoch_ms).  It anchors both the per-machine cycle
        marker AND the ts_cycle_start offset into the TS file, so the
        extracted clip starts exactly at the PLC pulse moment instead of
        whenever the HTTP webhook happened to reach CMS."""
        marker_dt = marker_dt or datetime.now()
        self._video_workers.pop(machine_id, None)

        if not camera_id:
            print(f"[PLC] No camera bound for {machine_id} — video skipped")
            return

        cam = self._ensure_camera_recording(camera_id)
        if not cam:
            print(f"[PLC] No RTSP URL for camera={camera_id!r} — video skipped")
            return

        write_start = cam.get("write_start")
        if write_start:
            # How many seconds of TS content exist at the moment this cycle
            # starts.  Both `marker_dt` (PLC pulse time) and `write_start`
            # (CMS-side recorder start) are wall-clock datetimes — they're
            # comparable as long as MES and CMS clocks are aligned (they
            # are: same machine, localhost webhook).
            ts_cycle_start: Optional[float] = max(0.0, (marker_dt - write_start).total_seconds())
        else:
            # write_start not yet known (camera just launched) — use None so
            # _extract_cycle falls back to the elapsed-14 heuristic
            ts_cycle_start = None

        self._video_workers[machine_id] = {
            "camera_id":      camera_id,
            "ts_file":        cam["ts_file"],
            "record_start":   cam["record_start"],
            "start_dt":       marker_dt,
            "cycle_number":   cycle_number,
            "ts_cycle_start": ts_cycle_start,   # seconds into TS file at cycle start
        }
        print(
            f"[PLC] Cycle #{cycle_number} marker set for {machine_id} "
            f"(ts_cycle_start={ts_cycle_start:.1f}s)" if ts_cycle_start is not None
            else f"[PLC] Cycle #{cycle_number} marker set for {machine_id} (ts_cycle_start=unknown)"
        )

    # ─── Cycle clip extraction ────────────────────────────────────────────────

    def _extract_cycle(
        self,
        ts_file: str,
        record_start: datetime,
        start_dt: datetime,
        end_dt: datetime,
        cycle_number: int,
        machine_id: str,
        ts_cycle_start: Optional[float] = None,
        part_code: str = "",
    ) -> str:
        """
        Cut [start_dt, end_dt] out of the rolling MPEG-TS file and write
        it as an MP4 named after the part code.  Operator spec is verbatim:
        from one OK/NG bit to the next OK/NG bit, no walls.
        """
        if not os.path.exists(ts_file):
            print(f"[PLC] TS file missing: {ts_file}")
            return ""

        # Seconds into the TS where the cycle starts.
        if ts_cycle_start is not None:
            ss = max(0.0, ts_cycle_start)
        else:
            ss = max(0.0, (start_dt - record_start).total_seconds())
        raw_duration = (end_dt - start_dt).total_seconds()

        # 2026-05-13 — operator spec change:
        #   "jo PLC se aayega voh as it is run hoga"
        # Clip length must match the PLC cycle duration EXACTLY, rounded
        # UP to the next whole second (15.7 s → 16 s, 200.0 s → 200 s).
        # No pre/post padding, no upper cap.
        #
        # Decoder safety note (rolled back the 2026-05-12 padding hack):
        #   The recorder forces a keyframe every 1 s (-g 20 @ 20 fps),
        #   and we're using input-side `-ss` with `-c copy`, so ffmpeg
        #   auto-snaps to the nearest preceding keyframe.  Worst-case
        #   the clip begins ~0–1 s before `ss`, which keeps HTML5/TV
        #   decoders happy without us padding explicitly.  The cycle
        #   END is set by `-t duration`; ffmpeg writes whole frames so
        #   the file may overshoot by a fraction of a GOP (≤1 s).
        import math as _math
        # Floor at 1 s: if two PLC pulses arrive within the same second
        # (fast machine, clock drift), ceil(0) = 0 would tell ffmpeg
        # `-t 0` and produce an empty file.  1 s is the smallest cycle
        # the operator would ever ask for and matches the TS keyframe
        # interval, so an off-by-one frame is harmless.
        #
        # 2026-05-14 — no upper cap: operator wants the clip length to
        # ALWAYS match cycle duration exactly, even 200 s cycles during
        # model change.  The earlier 19,710 s "stuck cycle" disaster is
        # now prevented structurally by per-shift TS rotation (a marker
        # that survives a shift boundary gets dropped automatically), so
        # raw_duration can't exceed one shift (~8 h) and in practice
        # never exceeds a few hundred seconds.
        duration = max(1.0, float(_math.ceil(raw_duration)))
        print(f"[PLC] #{cycle_number} extract: ss={ss:.1f}s "
              f"cycle={raw_duration:.1f}s -> clip={duration:.0f}s "
              f"(tight, no padding)  "
              f"ts={os.path.basename(ts_file)}")

        videos_abs = _resolve_videos_root(self.base_dir)
        safe_part = re.sub(r"[^A-Za-z0-9._-]", "_", part_code).strip("_") if part_code else ""
        if safe_part:
            file_name = f"{safe_part}.mp4"
        else:
            file_name = f"cycle_{cycle_number}_{machine_id}.mp4"

        # ── Structured folder path ──────────────────────────────────
        # Check video_config.json for custom save_path. If set, build:
        #   save_path / Zone / Line / Machine / Date / Shift / Slot / part.mp4
        # Also keep a flat copy in videos_abs for /api/video/by-part lookups.
        structured_abs = None
        try:
            _vcfg_path = os.path.join(self.base_dir, "video_config.json")
            custom_root = ""
            if os.path.exists(_vcfg_path) and os.path.getsize(_vcfg_path) > 0:
                import json as _json
                try:
                    with open(_vcfg_path) as _f:
                        _vcfg = _json.load(_f)
                    custom_root = (_vcfg.get("save_path", "") or "").strip()
                except (ValueError, OSError):
                    custom_root = ""  # corrupt/missing → skip structured save
                if custom_root:
                    os.makedirs(custom_root, exist_ok=True)
                    meta = self._get_machine_meta(machine_id)
                    # Sanitize every path segment: strip any separator / drive-unsafe chars
                    # so values like "Final Inspection M/c" don't accidentally spawn a
                    # nested "M\c" directory on Windows.
                    import re as _re_path
                    def _safe(seg: str) -> str:
                        s = _re_path.sub(r"[^A-Za-z0-9._-]+", "_",
                                         (seg or "").strip().replace(" ", "_"))
                        return s.strip("_") or "X"
                    zone_name  = _safe(meta.get("zone_name")    or "Unknown_Zone")
                    line_name  = _safe(meta.get("line_name")    or "Unknown_Line")
                    mach_name  = _safe(meta.get("machine_name") or machine_id)
                    date_str   = start_dt.strftime("%Y-%m-%d")
                    shift_name = _safe(self._get_current_shift_name(start_dt))
                    slot_label = _safe(self._get_slot_label(start_dt))
                    # Build: root/Zone/Line/Machine/Date/Shift/Slot/
                    sub_dir = os.path.join(
                        custom_root, zone_name, line_name, mach_name,
                        date_str, shift_name, slot_label
                    )
                    os.makedirs(sub_dir, exist_ok=True)
                    structured_abs = os.path.join(sub_dir, file_name)
        except Exception as exc:
            print(f"[PLC] Structured path error: {exc}")

        # ── Per-line folder on flat videos path ──────────────────────────
        # Organise flat copy as videos/<Line_Name>/<part>.mp4 instead of
        # videos/<part>.mp4 so admins can browse recordings by line.
        # Falls back to the root videos_abs if meta is missing.
        try:
            _meta = self._get_machine_meta(machine_id)
            _line_dir = (_meta.get("line_name") or "").strip().replace(" ", "_") or "Unknown_Line"
            # Strip any path-unsafe chars
            import re as _re_line
            _line_dir = _re_line.sub(r"[^A-Za-z0-9._-]", "_", _line_dir).strip("_") or "Unknown_Line"
        except Exception:
            _line_dir = "Unknown_Line"
        line_videos_dir = os.path.join(videos_abs, _line_dir)
        os.makedirs(line_videos_dir, exist_ok=True)

        file_abs = os.path.join(line_videos_dir, file_name)
        file_rel = f"{DEFAULT_VIDEOS_DIR}/{_line_dir}/{file_name}"
        tmp_name = f"_pending_cyc{cycle_number}_{int(time.time()*1000)}_{file_name}"
        tmp_abs = os.path.join(line_videos_dir, tmp_name)

        # 2026-05-18 — OVERWRITE GUARD.
        # PLC L108 occasionally chatters: one real 115 s cycle is followed
        # by a phantom 9 s pulse a few seconds later, with the SAME
        # part_code (scanner hasn't rescanned).  Without this guard, the
        # phantom's MP4 (named `{part_code}.mp4`) overwrites the real
        # cycle's clip, and the operator sees a 9 s video when the
        # dashboard shows 115 s.
        #
        # Rule: if an MP4 already exists for this part_code and its
        # duration is BIGGER than what we're about to extract, skip the
        # new write.  The first/longest video for a part wins.  Also
        # rename the new attempt to `{safe_part}_chatter_{cycle}.mp4`
        # so it's still on disk for diagnostics but doesn't clobber the
        # primary clip.
        try:
            if os.path.exists(file_abs):
                existing_size = os.path.getsize(file_abs)
                # Quick probe of existing duration via ffprobe header
                _probe_r = subprocess.run(
                    [ _get_ffmpeg(), "-hide_banner", "-i", file_abs ],
                    capture_output=True, timeout=5,
                )
                _existing_dur = 0.0
                import re as _re_dur2
                _m = _re_dur2.search(
                    r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)",
                    (_probe_r.stderr or b"").decode("utf-8", errors="replace"),
                )
                if _m:
                    _existing_dur = (int(_m.group(1))*3600
                                      + int(_m.group(2))*60
                                      + float(_m.group(3)))
                # If the existing clip is clearly longer than the new one
                # we're about to extract, treat the new one as chatter.
                if _existing_dur >= duration + 2.0:
                    chatter_name = f"{safe_part}_chatter_{cycle_number}.mp4"
                    file_abs = os.path.join(line_videos_dir, chatter_name)
                    file_rel = f"{DEFAULT_VIDEOS_DIR}/{_line_dir}/{chatter_name}"
                    tmp_name = f"_pending_chatter{cycle_number}_{int(time.time()*1000)}_{chatter_name}"
                    tmp_abs  = os.path.join(line_videos_dir, tmp_name)
                    print(f"[CHATTER-GUARD] {safe_part}: existing clip "
                          f"{_existing_dur:.1f}s >= new {duration:.0f}s+2 — "
                          f"new cycle #{cycle_number} saved as {chatter_name} "
                          f"(primary {safe_part}.mp4 kept intact)")
        except Exception as _cg_exc:
            # Best effort — if probe fails, fall through to normal write
            print(f"[CHATTER-GUARD] probe failed: {_cg_exc}")

        ffmpeg = _get_ffmpeg()
        # 2026-05-16 — Switched first-pass from `-c copy` to RE-ENCODE.
        # Operator reported "video plays 1 sec then stops" for cycles
        # extracted from long-running TS files (1000+ s into the file).
        # Symptom: 69 MB MP4 for 50 s cycle, ffprobe shows duration=50 s,
        # but HTML5 <video> stops at the first PTS discontinuity after
        # the initial keyframe.  Root cause: stream-copy preserves the
        # source TS's PCR drift verbatim, so the MP4 has non-monotonic
        # PTS that browsers refuse to decode past.  Re-encoding with
        # libx264 rebuilds clean monotonic timestamps + a fresh moov
        # atom — guaranteed playable.  Cost: ~real-time-on-ultrafast
        # (50 s clip ≈ 12-15 s of CPU); fine because the collector
        # already waits 25 s for the TS flush before kicking extraction.
        #
        # 2026-05-18 — CORRUPTED-VIDEO FIX.
        # Operator reported macroblocked / smeared playback (gray patches
        # with hint of structure — classic "decoded P-frame without I-frame
        # reference" pattern).  Two changes pin the cause:
        #
        # 1.  +igndts removed from fflags.  This was telling the demuxer
        #     to discard DTS on packets that had both DTS & PTS, which
        #     caused out-of-order frame delivery to libx264 — the encoder
        #     then re-encoded the visual garbage verbatim.  +genpts alone
        #     is sufficient to rebuild monotonic timestamps; igndts is
        #     only needed for sources that lie about DTS (our recorder
        #     produces clean DTS so it's harmful here).
        #
        # 2.  Hybrid seek: input-side `-ss` is rounded DOWN by 1.5 s so
        #     ffmpeg lands on the keyframe BEFORE the cycle start (TS
        #     keyframes are every 1 s, so 1.5 s of slack guarantees a
        #     valid I-frame is in the stream).  Then output-side `-ss`
        #     skips the pre-roll to the exact cycle boundary.  This way
        #     the encoder always sees a fully-decodable GOP at the start.
        #
        # 3.  -err_detect ignore_err keeps decoding on bit errors so a
        #     single corrupt packet in the TS doesn't cascade into a
        #     dropped section of the clip.
        # -------------------------------------------------------------
        # Seek hybrid:
        #   input_ss  = max(0, ss - 1.5)   → keyframe-aligned anchor
        #   output_ss = ss - input_ss      → exact-frame skip-forward
        # If ss is already < 1.5 s into the TS, just use input_ss=0 and
        # output_ss=ss (the file head is its own keyframe).
        _input_ss  = max(0.0, ss - 1.5)
        _output_ss = max(0.0, ss - _input_ss)
        # 2026-05-19 — Hardware-accelerated H.264 re-encode (NVENC > QSV > libx264).
        # Probe runs once at process start; result cached.  Auto-falls back
        # to libx264 if neither GPU encoder is available — never breaks recording.
        _hw_codec, _hw_flags = _pick_hw_encoder()
        cmd = [
            ffmpeg, "-y",
            "-fflags",     "+genpts+discardcorrupt",   # NO +igndts
            "-err_detect", "ignore_err",               # don't bail on bit errors
            "-ss", f"{_input_ss:.3f}",                 # input seek → keyframe before cycle
            "-i", ts_file,
            "-ss", f"{_output_ss:.3f}",                # output seek → exact cycle start
            "-t", f"{duration:.3f}",
            "-c:v", _hw_codec,
            *_hw_flags,                  # picked codec's preset/quality bundle
            "-pix_fmt", "yuv420p",       # max compatibility (browser HW decode)
            "-an",
            "-vsync", "cfr",             # constant frame rate → clean playback timing
            "-avoid_negative_ts", "make_zero",
            # +faststart relocates the MP4 moov atom to the start of the
            # file after writing.  Without it, an HTML5 <video> tag has
            # to download the entire file (100+ MB on long cycles) before
            # finding the metadata box at the end and starting playback.
            # With +faststart, the TV browser can start rendering from
            # the first keyframe as soon as moov + a few seconds of frames
            # arrive.  Cost: ~200 ms post-processing pass per clip.
            "-movflags", "+faststart",
            tmp_abs,
        ]
        snap_ts = ts_file   # nothing to clean up — extraction is single-stage
        # Subprocess timeout scales with clip length.  First pass is
        # stream-copy at input-seek (~150 ms regardless of duration).
        # The retry RE-ENCODES with output-seek though, which is much
        # heavier: ~real-time on libx264 ultrafast for the clip itself
        # plus a head-to-seek scan of the TS file.  Floor at 120 s with
        # 4× clip-length headroom: 16 s clip → 124 s, 200 s clip → 860 s.
        max_timeout = max(120, int(duration * 4) + 60)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=max_timeout)
                result_rc = proc.returncode
            except subprocess.TimeoutExpired:
                print(f"[PLC] Extraction timeout for #{cycle_number} — killing ffmpeg")
                proc.kill()
                proc.wait()
                # Windows sometimes keeps the handle briefly after kill —
                # retry a few times with a short sleep so the tmp file
                # doesn't linger as "_pending_..." taking up disk.
                for _ in range(5):
                    try:
                        os.remove(tmp_abs)
                        break
                    except OSError:
                        time.sleep(0.3)
                # Two-stage snapshot leftover — best-effort cleanup.
                if snap_ts != ts_file:
                    try:
                        if os.path.exists(snap_ts): os.remove(snap_ts)
                    except OSError: pass
                return ""
        except Exception as exc:
            print(f"[PLC] Extraction error: {exc}")
            for _ in range(5):
                try:
                    os.remove(tmp_abs)
                    break
                except OSError:
                    time.sleep(0.3)
            try:
                if os.path.exists(snap_ts): os.remove(snap_ts)
            except OSError: pass
            return ""

        # Snapshot served its purpose — ditch it (Windows file-lock retry).
        # Skip if we fell back to using ts_file directly (snap_ts == ts_file).
        if snap_ts != ts_file:
            for _ in range(3):
                try:
                    if os.path.exists(snap_ts): os.remove(snap_ts)
                    break
                except OSError:
                    time.sleep(0.2)

        # Success criteria are checked on the temp file — no other writer can
        # have touched it because the name is unique per call.
        # 2026-05-14 — tightened threshold + ffprobe duration check.  The
        # OLD `> 1024 bytes` check was passing tiny 200–600 KB files that
        # contained only a single keyframe header (TS-deep-seek bug), so
        # the UI played them and showed 0:00 duration.  The follow-up
        # `30 KB/sec` floor still let through PCR-drift clips that were
        # 2 s long but 2 MB in size (passing the size gate, failing the
        # duration we asked for).  Now we ALSO ffprobe the output and
        # require actual_duration ≥ 0.6 × requested.
        MIN_BYTES_PER_SEC = 30 * 1024
        min_required = max(60 * 1024, int(duration * MIN_BYTES_PER_SEC))
        size_actual = os.path.getsize(tmp_abs) if os.path.exists(tmp_abs) else 0

        def _probe_duration(path: str):
            """Return (duration_seconds, probe_succeeded).
            `imageio_ffmpeg` ships ffmpeg only — ffprobe sibling never
            exists, so the old `ffmpeg.replace("ffmpeg","ffprobe")` trick
            always failed and we treated every clip as 0 s long.  Now we
            use `ffmpeg -i <file>` (always present) and parse the
            "Duration: HH:MM:SS.ms" line from stderr — same info ffprobe
            would have given us.

            Returns probe_succeeded=False when the duration can't be read
            (corrupt MP4 metadata, ffmpeg crash) so the caller can decide
            whether to fall back to a size-only check rather than reject
            a perfectly playable 20 MB clip just because its moov atom
            is missing."""
            if not path or not os.path.exists(path):
                return 0.0, False
            try:
                result = subprocess.run(
                    [ffmpeg, "-hide_banner", "-i", path],
                    capture_output=True, text=True, timeout=10,
                )
                # ffmpeg -i prints "Duration: 00:00:15.12, start: ..." to
                # stderr and exits with rc=1 (no output specified).  That
                # rc is expected — we only care about the header parse.
                import re as _re_dur
                m = _re_dur.search(
                    r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)",
                    result.stderr or "",
                )
                if m:
                    sec = (int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + float(m.group(3)))
                    return sec, True
                return 0.0, False
            except Exception:
                return 0.0, False

        actual_dur, probe_ok = (_probe_duration(tmp_abs)
                                  if size_actual > 0 else (0.0, False))
        # 2026-05-18 — Acceptance rule, two regimes:
        #   • Short cycles (≤ 20 s, normal production) keep the strict 60%
        #     threshold so a 2-s-clip-for-15-s-cycle (PCR drift / bad seek)
        #     still gets discarded.
        #   • Long cycles (> 20 s, typically caused by break / setup /
        #     recorder crash mid-cycle) accept ≥ 30%.  Operator would
        #     rather have a 67-s clip of a 187-s cycle than NOTHING at all
        #     just because the recorder restarted mid-cycle.  Both regimes
        #     enforce a 5-s minimum so we never publish an empty stub.
        def _min_acceptable(d):
            return max(5.0, d * (0.6 if d <= 20.0 else 0.3))
        if probe_ok:
            dur_ok = actual_dur >= _min_acceptable(duration)
        else:
            dur_ok = True   # no signal → don't double-fail on top of size

        # If the first attempt produced a too-small file OR a too-short
        # clip, retry once with *output-side* seek + decoding flags.
        # Output-side seek forces ffmpeg to walk the file from the head
        # and emit frames properly; slower (~1–2 s vs 150 ms) but
        # reliable for the corrupted-PCR case that's currently producing
        # 2-second clips for 28-second cycles.
        if (result_rc == 0 and (0 < size_actual < min_required or not dur_ok)) or result_rc != 0:
            print(f"[PLC] #{cycle_number} first pass off-target "
                  f"(size={size_actual//1024}KB need={min_required//1024}KB, "
                  f"duration={actual_dur:.1f}s/{duration:.0f}s), retrying with re-encode")
            try: os.remove(tmp_abs)
            except OSError: pass
            # 2026-05-14 — retry now RE-ENCODES instead of stream-copy.
            # The first pass already tried stream-copy; if that produced a
            # 0 s / corrupted output it's almost always because the source
            # TS has PCR jumps that confuse `-c copy`.  Decoding to YUV and
            # re-encoding with libx264 rebuilds proper timestamps + moov
            # atom, so the resulting MP4 plays in HTML5 with correct
            # duration even when the input is mid-file corrupted.
            # `-preset ultrafast` keeps the cost ~real-time per second of
            # clip on a typical box; combined with output-side seek the
            # retry takes ~5-10 s for a 15 s cycle.
            retry_cmd = [
                ffmpeg, "-y",
                # 2026-05-18 — dropped +igndts (causes out-of-order
                # frames → garbled re-encode).  Output-side -ss already
                # forces full decode from head so seek is frame-accurate
                # regardless of keyframe spacing.
                "-fflags",     "+genpts+discardcorrupt",
                "-err_detect", "ignore_err",
                "-i", ts_file,
                "-ss", f"{ss:.3f}",          # output seek = frame-accurate
                "-t", f"{duration:.3f}",
                # 2026-05-19 — HW encode (NVENC→QSV→libx264) on retry path too.
                "-c:v", _hw_codec,
                *_hw_flags,
                "-pix_fmt", "yuv420p",
                "-an",
                "-vsync", "cfr",             # stable timing
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                tmp_abs,
            ]
            try:
                proc2 = subprocess.Popen(retry_cmd,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
                try:
                    proc2.wait(timeout=max_timeout)
                    result_rc = proc2.returncode
                except subprocess.TimeoutExpired:
                    proc2.kill(); proc2.wait()
                    print(f"[PLC] #{cycle_number} retry timed out")
                    for _ in range(5):
                        try: os.remove(tmp_abs); break
                        except OSError: time.sleep(0.3)
                    return ""
            except Exception as exc:
                print(f"[PLC] #{cycle_number} retry error: {exc}")
                for _ in range(5):
                    try: os.remove(tmp_abs); break
                    except OSError: time.sleep(0.3)
                return ""
            size_actual = os.path.getsize(tmp_abs) if os.path.exists(tmp_abs) else 0
            actual_dur, probe_ok = (_probe_duration(tmp_abs)
                                      if size_actual > 0 else (0.0, False))
            if probe_ok:
                dur_ok = actual_dur >= _min_acceptable(duration)
            else:
                dur_ok = True
            print(f"[PLC] #{cycle_number} retry produced "
                  f"{size_actual//1024}KB / "
                  f"{actual_dur:.1f}s{'' if probe_ok else ' (probe failed)'} "
                  f"(target {duration:.0f}s, "
                  f"min_acceptable={_min_acceptable(duration):.1f}s, "
                  f"dur_ok={dur_ok})")

        if (result_rc == 0 and os.path.exists(tmp_abs)
                and size_actual >= min_required and dur_ok):
            size_kb = size_actual // 1024
            try:
                # Atomic on the same filesystem; replaces any existing file.
                # Retry on Windows file-lock errors — ffmpeg sometimes holds
                # its output handle for a split second after exiting, which
                # was leaving files stuck as "_pending_..." on disk.
                last_exc = None
                for _ in range(5):
                    try:
                        os.replace(tmp_abs, file_abs)
                        last_exc = None
                        break
                    except OSError as _e:
                        last_exc = _e
                        time.sleep(0.3)
                if last_exc is not None:
                    raise last_exc
            except OSError as exc:
                print(f"[PLC] Rename {tmp_abs} -> {file_abs} failed: {exc}")
                for _ in range(5):
                    try:
                        os.remove(tmp_abs)
                        break
                    except OSError:
                        time.sleep(0.3)
                return ""
            # Copy to structured folder if configured
            if structured_abs:
                try:
                    import shutil
                    shutil.copy2(file_abs, structured_abs)
                    print(f"[PLC] Structured copy: {structured_abs}")
                except Exception as exc:
                    print(f"[PLC] Structured copy failed: {exc}")
            print(f"[PLC] Cycle video extracted: {file_rel} ({size_kb} KB)")
            return file_rel

        # Failed — clean up the temp file.  Also nuke any prior file at the
        # final path: if an old broken-tiny / wrong-duration clip is sitting
        # there from a previous failed extraction, by-part lookup would
        # still return it and play 0:00 (or 2 s for a 28 s cycle) in the
        # browser.  Deleting it forces a clean 404 (UI shows "no video"
        # instead of a broken player).
        try: os.remove(tmp_abs)
        except OSError: pass
        try:
            if os.path.exists(file_abs):
                stale_size = os.path.getsize(file_abs)
                stale_dur, stale_probe_ok = _probe_duration(file_abs)
                stale_bad = stale_size < min_required or (
                    stale_probe_ok and stale_dur < _min_acceptable(duration))
                if stale_bad:
                    os.remove(file_abs)
                    print(f"[PLC] Removed stale clip at {file_abs} "
                          f"(size={stale_size//1024}KB dur={stale_dur:.1f}s)")
        except OSError: pass
        print(f"[PLC] Cycle video extraction failed for #{cycle_number} "
              f"(rc={result_rc}, size={size_actual//1024}KB need={min_required//1024}KB, "
              f"dur={actual_dur:.1f}s need>={_min_acceptable(duration):.1f}s)")
        return ""

    # ─── TS file cleanup ─────────────────────────────────────────────────────

    def _cleanup_old_ts(self, ts_file: str) -> None:
        """Delete a TS file once it is no longer referenced by any live recorder
        or pending cycle worker.  Safe to call from a background thread."""
        if not ts_file or not ts_file.endswith(".ts"):
            return
        for cam in self._camera_workers.values():
            if cam.get("ts_file") == ts_file:
                return  # still the live recording
        for w in self._video_workers.values():
            if w.get("ts_file") == ts_file:
                return  # another cycle is still extracting from it
        try:
            if os.path.exists(ts_file):
                os.remove(ts_file)
                print(f"[PLC] Cleaned up old TS: {os.path.basename(ts_file)}")
        except OSError as exc:
            print(f"[PLC] TS cleanup error: {exc}")

    # ─── Shift/slot helpers for structured video folders ────────────────────

    def _get_current_shift_name(self, dt: datetime) -> str:
        """Determine shift name from shifts.json based on time."""
        try:
            from shifts_config import list_shifts
            shifts = list_shifts(self.base_dir)
            h, m = dt.hour, dt.minute
            t_min = h * 60 + m
            for s in shifts:
                ss = sum(int(x) * (60 if i == 0 else 1) for i, x in enumerate(s["start"].split(":")))
                se = sum(int(x) * (60 if i == 0 else 1) for i, x in enumerate(s["end"].split(":")))
                if se > ss:
                    if ss <= t_min < se:
                        return s.get("name", s.get("id", "Unknown"))
                else:  # crosses midnight
                    if t_min >= ss or t_min < se:
                        return s.get("name", s.get("id", "Unknown"))
        except Exception:
            pass
        # Fallback: simple A/B
        h = dt.hour
        if 6 <= h < 18:
            return "ShiftA"
        return "ShiftB"

    def _get_slot_label(self, dt: datetime) -> str:
        """Return hourly slot label like '08:30-09:30'."""
        h, m = dt.hour, dt.minute
        # Round down to nearest hour slot
        slot_start_h = h
        slot_start_m = 30 if m >= 30 else 0
        slot_end_h = slot_start_h + (1 if slot_start_m == 30 else 0)
        slot_end_m = 30 if slot_start_m == 0 else 0
        if slot_end_h >= 24:
            slot_end_h -= 24
        return f"{slot_start_h:02d}:{slot_start_m:02d}-{slot_end_h:02d}:{slot_end_m:02d}"

    # ─── pymcprotocol connection helpers ─────────────────────────────────────

    def _get_conn(self, plc_id: str, ip: str, port: int) -> Optional[pymcprotocol.Type4E]:
        with self._plc_lock:
            if plc_id in self._plc_conns:
                return self._plc_conns[plc_id]
        # Respect cool-down after previous failure so we don't spam the PLC
        # with connect attempts every 300 ms when another process owns the slot.
        now = time.time()
        next_retry = self._plc_next_retry.get(plc_id, 0.0)
        if now < next_retry:
            return None
        try:
            conn = pymcprotocol.Type4E()
            conn.connect(ip, port)
            with self._plc_lock:
                self._plc_conns[plc_id] = conn
            self._connected_plcs[plc_id] = True
            # Clear back-off on success
            self._plc_next_retry.pop(plc_id, None)
            print(f"[PLC] Connected to {plc_id} at {ip}:{port}")
            return conn
        except Exception as exc:
            # Throttle retries — 60 s cool-down, and only log on transition
            # from "ok" to "down" to keep the log clean.
            was_ok = self._connected_plcs.get(plc_id, True)
            self._plc_next_retry[plc_id] = now + 60
            if was_ok:
                print(f"[PLC] Cannot connect to {plc_id} at {ip}:{port}: {exc} — "
                      f"cool-down 60s (likely another process owns the MC slot)")
            return None

    def _drop_conn(self, plc_id: str) -> None:
        with self._plc_lock:
            conn = self._plc_conns.pop(plc_id, None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # ─── Public helpers for api_server ───────────────────────────────────────

    def get_ts_file(self, camera_id: str) -> Optional[str]:
        """Return the live TS file path for this camera, or None if not recording."""
        cam = self._camera_workers.get(camera_id)
        if cam and cam["proc"].poll() is None:
            return cam["ts_file"]
        return None

    # ─── Machine metadata helper ──────────────────────────────────────────────

    def _get_machine_meta(self, machine_id: str) -> Dict:
        """Resolve zone_name + line_name + machine_name for a given machine_id.

        Two sources, in order:
          1. zones.json via `all_machines_flat` — covers legacy local-only
             machines (created via the old MachineMaster CRUD).
          2. camera_config_bindings.json — covers MES-driven machines
             that use synthetic `mes:<plc_id>` ids.  The /api/mes/machine
             handler stuffs zone_name / line_name onto the binding at
             save time (see _sync_binding_from_machine in api_server.py)
             so plc_monitor can write videos under videos/<line>/ without
             a per-cycle MES round-trip.
        """
        now = time.time()
        if self._machine_meta_cache is None or (now - self._machine_meta_ts) > 30:
            cache: Dict[str, Dict] = {
                m["machine_id"]: m for m in all_machines_flat(self.base_dir)
            }
            try:
                for b in list_bindings(self.base_dir):
                    mid = str(b.get("machine_id", "")).strip()
                    if not mid or mid in cache:
                        continue
                    cache[mid] = {
                        "machine_id":   mid,
                        "machine_name": b.get("machine_name", ""),
                        "zone_name":    b.get("zone_name", ""),
                        "line_name":    b.get("line_name", ""),
                        "camera_id":    b.get("camera_id", ""),
                    }
            except Exception as exc:
                print(f"[PLC] meta-cache binding merge error: {exc}")
            self._machine_meta_cache = cache
            self._machine_meta_ts = now
        return self._machine_meta_cache.get(machine_id, {})
