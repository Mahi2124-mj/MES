"""
plc_poller.py
=============
PLC bit poller — drives cycle events from Mitsubishi MC4E PLC bits
configured via the CMS portal.

DESIGN (per the EOL spec):
  Main machine (final inspection):
      L108  -> OK pulse  -> end-of-cycle, status = OK
      L109  -> NG pulse  -> end-of-cycle, status = NG
      Cycle's start_ts = the previous cycle's end_ts.  For the very
      first cycle of a shift we use shift-start.

  Sub-machine (each upstream station):
      M-bit -> single-shot count pulse -> append a COUNT event
      For sub-machines we do NOT cut a separate clip; the rolling
      .ts file keeps recording, and the cycle CSV row stores the
      pulse timestamp + a +/- target-time window so the user can
      view that segment later.

CONFIG SOURCE:
  This module DOES NOT read PLC IPs / bits from a hardcoded list.
  Everything comes from `camera_config_bindings.json` (extended) +
  `plcs.json` so the admin can add / rename / change without restarting
  this service.  The BindingExtension format expected:
      {
        "id": "bind_...",
        "machine_id": "...",
        "camera_id":  "...",
        "plc_id":     "...",
        "machine_role": "main" | "sub",     # NEW
        "ok_bit":      "L108",              # NEW (main only)
        "ng_bit":      "L109",              # NEW (main only)
        "count_bit":   "M100",              # NEW (sub only)
        "target_time": 30
      }

STATUS:
  This module is a SCAFFOLD — class structure + edge-detection +
  cycle_events integration are all ready, but the actual MC4E read
  call uses pymcprotocol (already used by MES collector_engine.py).
  Hardware-test required before enabling in api_server.py.

ENABLE:
  Once hardware-tested, add to api_server.py top-level:
      from plc_poller import PlcPoller
      PlcPoller.get(BASE_DIR).start()
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class PlcPoller:
    """One thread polls every configured PLC bit at ~30Hz, detects
    rising edges, and fires cycle events for the bound machine."""

    _instance: Optional["PlcPoller"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls, base_dir: Optional[str] = None) -> "PlcPoller":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(base_dir)
            return cls._instance

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Last-known bit value per (plc_id, bit_addr) so we can detect
        # 0->1 rising edges between polls.  Resets on PLC reconnect.
        self._last_bit: Dict[Tuple[str, str], int] = {}
        # Per-machine: last event timestamp.  For main-machine the next
        # cycle's start_ts comes from this; for sub-machine it's the
        # window center.
        self._last_event_ts: Dict[str, datetime] = {}
        # PLC connection cache: {plc_id: pymcprotocol.Type4E}
        self._plc_conn: Dict[str, object] = {}
        self._plc_ok:   Dict[str, bool]   = {}

    # ── public API ─────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name="plc-poller")
        self._thread.start()

    def stop(self):
        self._stop.set()
        for plc_id, conn in list(self._plc_conn.items()):
            try: conn.close()  # type: ignore
            except Exception: pass
        self._plc_conn.clear()
        self._plc_ok.clear()

    # ── internals ──────────────────────────────────────────────────

    def _load_bindings(self) -> List[Dict]:
        try:
            from camera_bindings import list_bindings
            return list_bindings(self.base_dir)
        except Exception:
            return []

    def _load_plcs(self) -> Dict[str, Dict]:
        try:
            from plc_config import list_plcs
            return {str(p.get("id")): p for p in list_plcs(self.base_dir)}
        except Exception:
            return {}

    def _connect_plc(self, plc: Dict) -> Optional[object]:
        """Lazy-connect, cached.  Mirrors the MES collector_engine.py
        approach.  Returns None on failure (will be retried)."""
        try:
            import pymcprotocol  # type: ignore
        except ImportError:
            print("[PLC-POLLER] pymcprotocol not installed — skipping all PLCs.")
            return None
        plc_id = str(plc.get("id"))
        if self._plc_ok.get(plc_id) and plc_id in self._plc_conn:
            return self._plc_conn[plc_id]
        try:
            mc = pymcprotocol.Type4E()
            mc.connect(plc.get("ip"), int(plc.get("port") or 5002))
            self._plc_conn[plc_id] = mc
            self._plc_ok[plc_id]   = True
            return mc
        except Exception as exc:
            self._plc_ok[plc_id] = False
            return None

    def _read_bit(self, mc, addr: str) -> Optional[int]:
        """Read one bit-unit; return None on hiccup."""
        try:
            v = mc.batchread_bitunits(headdevice=addr, readsize=1)
            return int(v[0]) if v else 0
        except Exception:
            return None

    def _on_main_event(self, binding: Dict, status: str, now: datetime):
        """Main machine OK or NG pulse — close the running cycle."""
        machine_id = binding.get("machine_id")
        camera_id  = binding.get("camera_id")
        if not machine_id or not camera_id:
            return
        try:
            from cycle_events import append_cycle
            from recorder_engine import RecordingManager
        except Exception:
            return
        rec = RecordingManager.get(self.base_dir).get_recorder(camera_id)
        info = rec.current_recording_info() if rec else None
        if not info:
            return
        ts_file, ts_started = info
        start_ts = self._last_event_ts.get(machine_id) or datetime.fromtimestamp(ts_started)
        append_cycle(
            machine_id       = machine_id,
            camera_id        = camera_id,
            start_ts         = start_ts,
            end_ts           = now,
            status           = status,
            shift_id         = (rec._current_date or "") + "_" + (rec._current_shift or ""),
            ts_file          = ts_file,
            ts_file_start_ep = ts_started,
            base_dir         = self.base_dir,
        )
        self._last_event_ts[machine_id] = now

    def _on_sub_event(self, binding: Dict, now: datetime):
        """Sub-machine M-bit pulse — single-shot count event.  Cycle
        window = [now - target_time, now]."""
        from datetime import timedelta
        machine_id = binding.get("machine_id")
        camera_id  = binding.get("camera_id")
        try: target = float(binding.get("target_time") or 30)
        except (TypeError, ValueError): target = 30.0
        if not machine_id or not camera_id:
            return
        try:
            from cycle_events import append_cycle
            from recorder_engine import RecordingManager
        except Exception:
            return
        rec = RecordingManager.get(self.base_dir).get_recorder(camera_id)
        info = rec.current_recording_info() if rec else None
        if not info:
            return
        ts_file, ts_started = info
        start_ts = now - timedelta(seconds=target)
        append_cycle(
            machine_id       = machine_id,
            camera_id        = camera_id,
            start_ts         = start_ts,
            end_ts           = now,
            status           = "COUNT",
            shift_id         = (rec._current_date or "") + "_" + (rec._current_shift or ""),
            ts_file          = ts_file,
            ts_file_start_ep = ts_started,
            base_dir         = self.base_dir,
        )
        self._last_event_ts[machine_id] = now

    def _process_binding(self, binding: Dict, plcs: Dict[str, Dict], now: datetime):
        plc = plcs.get(str(binding.get("plc_id") or ""))
        if not plc or not plc.get("enabled"):
            return
        mc = self._connect_plc(plc)
        if mc is None:
            return
        plc_id = str(plc.get("id"))
        role = (binding.get("machine_role") or "main").lower()

        if role == "main":
            for status, key in (("OK", "ok_bit"), ("NG", "ng_bit")):
                bit = binding.get(key) or (plc.get("bit_address") if status == "OK" else None)
                if not bit:
                    continue
                v = self._read_bit(mc, bit)
                if v is None:
                    continue
                k = (plc_id, bit)
                prev = self._last_bit.get(k, 0)
                self._last_bit[k] = v
                if prev == 0 and v == 1:   # rising edge
                    self._on_main_event(binding, status, now)
        else:
            bit = binding.get("count_bit") or plc.get("bit_address")
            if not bit:
                return
            v = self._read_bit(mc, bit)
            if v is None:
                return
            k = (plc_id, bit)
            prev = self._last_bit.get(k, 0)
            self._last_bit[k] = v
            if prev == 0 and v == 1:
                self._on_sub_event(binding, now)

    def _run_loop(self):
        """30Hz scan over all enabled bindings."""
        SCAN_INTERVAL = 0.033   # 30 Hz, fast enough for a 50-100ms PLC pulse
        next_reload = 0.0
        bindings: List[Dict] = []
        plcs:     Dict[str, Dict] = {}
        while not self._stop.is_set():
            now_t = time.time()
            if now_t >= next_reload:
                bindings    = self._load_bindings()
                plcs        = self._load_plcs()
                next_reload = now_t + 30   # pick up admin edits every 30s
            now = datetime.now()
            for b in bindings:
                if self._stop.is_set():
                    break
                try:
                    self._process_binding(b, plcs, now)
                except Exception as exc:
                    print(f"[PLC-POLLER] {b.get('id')} error: {exc}")
            self._stop.wait(SCAN_INTERVAL)
