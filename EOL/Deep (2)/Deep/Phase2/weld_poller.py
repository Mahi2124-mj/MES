"""
weld_poller.py
==============
LIVE weld-current feed for the Weld Monitor.  2026-08-03.

MASTER-DRIVEN: every welding station lives as a row in `mes_weld_master`
(Quality admin → Weld Master).  This module reads that table and runs one
worker thread per active station, so adding a robot is a config change, not a
code change.  A station row says which analog card / channel the robot's
current shunt is wired to, where the robot sits (zone → line → machine) and
the acceptable current band.

First station wired (auto-seeded): **RC-01**, recliner zone, YMC line —
8-channel analog card at 192.168.31.59:502 over **Modbus TCP**.

Card map (probed 2026-08-03, holds for this card type):
  * 8 channels in INPUT REGISTERS (FC4) at base 2001, two registers each,
    32-bit IEEE-754 float, BIG-endian (hi,lo).  Channel N → base+(N-1)*2.
  * Only channel 8 is wired here; 1-7 read a constant 0.0.  The card's own
    config blocks agree (regs 83-90 = 15×7 then 11; 517-524 = 1000×7 then 8000).
  * Address 0 is unmapped (Modbus exception 2) — the map starts at 1.

Scaling: shunt is 60 mV = 600 A, so the float is millivolts and
current = mV × `mv_to_a` (10 by default).  Verified against a 150 s capture:
peak 373 A, welding average 276 A, plateau ~350-360 A — normal MAG values.

One row per WELD, not per sample.  That capture (383 Hz) showed ~4.5 s per
weld — 1.90 s arc-on, 2.73 s gap — in two flavours: long welds (~2.1 s) that
ramp, hold ~350 A and taper for crater fill, and short stitch welds (~1.3 s)
flat at ~355 A.  Per-weld rows keep the trend readable (one point = one weld)
and let the monitor plot against PART COUNT, which is captured from the line's
live counter at the moment the arc drops.

Kill switch: WELD_POLLER=0.  Everything else comes from the master table.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from datetime import datetime
from typing import Optional

from database import get_conn

_RESCAN_S = 60.0          # how often to pick up master edits
_STOP = threading.Event()


# ── Modbus TCP (only what this card needs: read N input registers) ─────────
class _Card:
    """Persistent Modbus-TCP connection to ONE analog card, shared by every
    channel configured on that card.

    The socket stays OPEN between reads: re-connecting per sample caps the
    rate near 5 Hz, a held connection sustains hundreds of Hz.  Any error
    closes it and the caller reconnects with backoff — this card is on the
    plant network, so a blip must never take the feed down.

    2026-09-21 — these PPI cards accept a SINGLE Modbus session.  Two rows on
    one card (RC-01 weld current on ch8 + the gas sensor on ch6) used to open
    two connections and lock each other out.  Now there is one socket per card
    (see _card_for) and each read is one request/response under the card's
    lock, so any number of channels share it.
    """

    def __init__(self, ip: str, port: int, unit: int):
        self.ip, self.port, self.unit = ip, port, unit
        self._s: Optional[socket.socket] = None
        self._tid = 0
        self.lock = threading.RLock()

    def close(self) -> None:
        with self.lock:
            if self._s is not None:
                try:
                    self._s.close()
                except Exception:
                    pass
                self._s = None

    def read_float(self, reg: int) -> float:
        """FC4, two input registers from `reg`, as one big-endian float."""
        with self.lock:
            try:
                if self._s is None:
                    s = socket.socket()
                    s.settimeout(2.0)
                    s.connect((self.ip, self.port))
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self._s = s
                s = self._s
                self._tid = (self._tid + 1) % 65535
                pdu = struct.pack(">BHH", 4, reg, 2)          # FC4, 2 registers
                s.sendall(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu)

                def _rd(n: int) -> bytes:
                    buf = b""
                    while len(buf) < n:
                        chunk = s.recv(n - len(buf))
                        if not chunk:
                            raise IOError("connection closed by card")
                        buf += chunk
                    return buf

                _rd(7)                                        # MBAP header
                fc = _rd(1)[0]
                if fc & 0x80:
                    raise IOError(f"modbus exception {_rd(1)[0]}")
                data = _rd(_rd(1)[0])
                hi, lo = struct.unpack(">HH", data[:4])
                return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
            except Exception:
                self.close()
                raise


_CARDS: dict = {}
_CARDS_LOCK = threading.Lock()


def _card_for(ip: str, port: int, unit: int) -> _Card:
    """The one shared connection object for this card."""
    key = (str(ip), int(port or 502), int(unit or 1))
    with _CARDS_LOCK:
        c = _CARDS.get(key)
        if c is None:
            c = _CARDS[key] = _Card(*key)
        return c


# ── helpers ───────────────────────────────────────────────────────────────
def _shift_now(default: str = "A") -> str:
    m = datetime.now().hour * 60 + datetime.now().minute
    return "A" if (8 * 60 + 30) <= m < (17 * 60 + 15) else "B"


def _line_table(line_id: Optional[int]) -> Optional[str]:
    if not line_id:
        return None
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT db_table_name FROM mes_lines WHERE id = %s", (line_id,))
            r = cur.fetchone()
            return (r[0] or "").strip() or None if r else None
    except Exception:
        return None


def _sub_plc_id(line_id: Optional[int], machine_name: Optional[str]) -> Optional[int]:
    """Resolve the station's MACHINE to its PLC row (mes_plc_configs.id).

    2026-08-03 — the part count must come from the welding machine's OWN PLC,
    not from the line total: a robot cell and the line counter run at different
    rates, so stamping welds with the line count lined the chart up against the
    wrong part.  mes_submachine_ct_log keys per-machine cycles by sub_plc_id,
    which is this id."""
    if not (line_id and machine_name):
        return None
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM mes_plc_configs "
                        "WHERE line_id = %s AND TRIM(machine_name) = TRIM(%s) LIMIT 1",
                        (line_id, machine_name))
            r = cur.fetchone()
            return int(r[0]) if r else None
    except Exception:
        return None


def _part_now(table: Optional[str], sub_plc_id: Optional[int]
              ) -> tuple[Optional[int], Optional[str]]:
    """Part count + model at the moment the arc drops.

    Preferred source is the welding MACHINE's own PLC — the newest cycle_seq
    that machine logged today.  That is the part the robot is actually welding,
    which is what makes PART COUNT meaningful as the chart's X axis.  Falls back
    to the line counter when the machine has no PLC row or has not cycled yet
    (e.g. first weld of a shift), so a weld is never dropped for want of a
    number.  Best-effort throughout: any failure just yields (None, None)."""
    if sub_plc_id:
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT cycle_seq, model_name FROM mes_submachine_ct_log "
                    "WHERE sub_plc_id = %s AND record_date = CURRENT_DATE "
                    "ORDER BY ts_end DESC NULLS LAST, id DESC LIMIT 1",
                    (sub_plc_id,))
                r = cur.fetchone()
                if r and r[0] is not None:
                    return int(r[0]), (r[1] or None)
        except Exception:
            pass
    if not table:
        return None, None
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT ok_count, current_model_name FROM {table} "
                        f"ORDER BY updated_at DESC LIMIT 1")
            r = cur.fetchone()
            if not r:
                return None, None
            return (int(r[0]) if r[0] is not None else None,
                    (r[1] or None))
    except Exception:
        return None, None


def _load_stations() -> list[dict]:
    try:
        with get_conn() as conn:
            from routers.weld import _ensure_weld_table, _ensure_master_table
            _ensure_weld_table(conn)
            _ensure_master_table(conn)
            cur = conn.cursor()
            cur.execute("""
                SELECT id, station, weld_type, zone, line_id, machine_name,
                       card_ip, card_port, unit_id, channel, base_register, mv_to_a,
                       current_min, current_max, on_threshold_a, gap_s,
                       min_weld_s, sample_hz,
                       COALESCE(signal, 'current') AS signal, unit, scale,
                       offset_val, sample_s
                FROM mes_weld_master WHERE is_active ORDER BY station
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in (cur.fetchall() or [])]
    except Exception as exc:
        print(f"[WELD-LIVE] master read failed: {exc}", flush=True)
        return None          # unknown ≠ "no stations": keep what is running


def _next_seq(station: str) -> int:
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(MAX(weld_seq), 0) FROM mes_weld_log "
                        "WHERE station = %s AND source = 'live'", (station,))
            r = cur.fetchone()
            return int((r[0] if r else 0) or 0)
    except Exception:
        return 0


def _store(cfg: dict, samples: list[float], dur: float, seq: int,
           line_table: Optional[str], sub_plc_id: Optional[int]) -> None:
    """One finished weld → one row.  `weld_current` is the ARC-ON AVERAGE (the
    number quality judges); peak and arc-on time get their own columns.
    `weld_voltage` stays NULL — this card carries only the current shunt.
    is_ng is set from the station's configured current band when one is set."""
    on = float(cfg.get("on_threshold_a") or 30)
    arc = [a for a in samples if a >= on]
    if not arc:
        return
    avg_a  = sum(arc) / len(arc)
    peak_a = max(arc)
    lo, hi = cfg.get("current_min"), cfg.get("current_max")
    is_ng = bool((lo is not None and avg_a < float(lo)) or
                 (hi is not None and avg_a > float(hi)))
    part_count, model = _part_now(line_table, sub_plc_id)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO mes_weld_log (weld_type, station, weld_seq, weld_current,"
                " weld_voltage, model, shift, is_ng, source, weld_peak_a, weld_duration_s,"
                " line_id, zone, machine_name, part_count, record_date)"
                " VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,'live',%s,%s,%s,%s,%s,%s,CURRENT_DATE)",
                (cfg.get("weld_type") or "robot", cfg["station"], seq,
                 round(avg_a, 1), model, _shift_now(), is_ng,
                 round(peak_a, 1), round(dur, 2),
                 cfg.get("line_id"), cfg.get("zone"), cfg.get("machine_name"),
                 part_count))
            conn.commit()
    except Exception as exc:
        print(f"[WELD-LIVE] {cfg['station']}: insert failed: {exc}", flush=True)


# ── one worker per station ────────────────────────────────────────────────
def _station_worker(cfg: dict, stop: Optional[threading.Event] = None) -> None:
    station = cfg["station"]
    ch      = int(cfg.get("channel") or 8)
    base    = int(cfg.get("base_register") or 2001)
    reg     = base + (ch - 1) * 2
    mv_to_a = float(cfg.get("mv_to_a") or 10.0)
    on_a    = float(cfg.get("on_threshold_a") or 30)
    gap_s   = float(cfg.get("gap_s") or 0.35)
    min_s   = float(cfg.get("min_weld_s") or 0.20)
    hz      = float(cfg.get("sample_hz") or 50)
    period  = 1.0 / max(hz, 1.0)

    line_table = _line_table(cfg.get("line_id"))
    sub_plc  = _sub_plc_id(cfg.get("line_id"), cfg.get("machine_name"))
    seq      = _next_seq(station)
    card = _card_for(str(cfg["card_ip"]), int(cfg.get("card_port") or 502),
                     int(cfg.get("unit_id") or 1))
    stop = stop or threading.Event()

    print(f"[WELD-LIVE] {station}: {cfg['card_ip']}:{cfg.get('card_port', 502)} "
          f"ch{ch} (FC4 reg {reg}) @{hz:.0f}Hz · {mv_to_a:g} A/mV · seq from {seq} · "
          f"part-count from {'machine PLC #'+str(sub_plc) if sub_plc else 'line counter'}",
          flush=True)

    samples: list[float] = []
    start = last_on = 0.0
    backoff = 1.0
    welds = 0
    last_report = time.time()

    while not _STOP.is_set() and not stop.is_set():
        try:
            amps = card.read_float(reg) * mv_to_a
            backoff = 1.0
            now = time.time()
            if amps >= on_a:
                if not samples:
                    start = now
                samples.append(amps)
                last_on = now
            elif samples and (now - last_on) >= gap_s:
                dur = last_on - start
                if dur >= min_s:
                    seq += 1
                    welds += 1
                    _store(cfg, samples, dur, seq, line_table, sub_plc)
                samples = []
            if now - last_report >= 600:
                print(f"[WELD-LIVE] {station}: {welds} welds in last 10 min", flush=True)
                welds = 0
                last_report = now
            time.sleep(period)
        except Exception as exc:
            if samples:                      # don't silently lose a weld in flight
                dur = max(0.0, last_on - start)
                if dur >= min_s:
                    seq += 1
                    _store(cfg, samples, dur, seq, line_table, sub_plc)
                samples = []
            card.close()
            print(f"[WELD-LIVE] {station}: {exc} — retry in {backoff:.0f}s", flush=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
    # The card connection is shared with the card's other channels: leave it open.


# ── one worker per SENSOR channel (gas, …) — 2026-09-21 ───────────────────
_SENSOR_TABLE_OK = False


def _ensure_sensor_table() -> None:
    global _SENSOR_TABLE_OK
    if _SENSOR_TABLE_OK:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS mes_gas_log (
                         id       BIGSERIAL PRIMARY KEY,
                         ts       TIMESTAMP NOT NULL DEFAULT now(),
                         card_ip  TEXT NOT NULL,
                         channel  INTEGER NOT NULL,
                         raw      REAL,
                         value    REAL)""")
        cur.execute("ALTER TABLE mes_gas_log ADD COLUMN IF NOT EXISTS station TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gas_log_ts ON mes_gas_log (ts)")
        conn.commit()
    _SENSOR_TABLE_OK = True


def _sensor_worker(cfg: dict, stop: Optional[threading.Event] = None) -> None:
    """A continuous analog signal on a card channel (the gas sensor): one
    reading every `sample_s` into mes_gas_log, value = raw x scale + offset.
    Reads through the card's shared connection, so it never competes with the
    weld-current channel on the same card."""
    stop = stop or threading.Event()
    station = cfg["station"]
    ch    = int(cfg.get("channel") or 1)
    base  = int(cfg.get("base_register") or 2001)
    reg   = base + (ch - 1) * 2
    scale = float(cfg["scale"]) if cfg.get("scale") is not None else 1.0
    off   = float(cfg.get("offset_val") or 0.0)
    every = max(1.0, float(cfg.get("sample_s") or 2.0))
    card = _card_for(str(cfg["card_ip"]), int(cfg.get("card_port") or 502),
                     int(cfg.get("unit_id") or 1))
    try:
        _ensure_sensor_table()
    except Exception as exc:
        print(f"[WELD-LIVE] {station}: sensor table not ready: {exc}", flush=True)
        return
    print(f"[WELD-LIVE] {station} (sensor): {cfg['card_ip']}:{cfg.get('card_port', 502)} "
          f"ch{ch} (FC4 reg {reg}) every {every:g}s · value = raw x {scale:g} + {off:g} "
          f"{cfg.get('unit') or ''}".rstrip(), flush=True)
    fails = 0
    while not _STOP.is_set() and not stop.is_set():
        t0 = time.monotonic()
        try:
            raw = card.read_float(reg)
            val = raw * scale + off
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO mes_gas_log (station, card_ip, channel, raw, value) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (station, str(cfg["card_ip"]), ch, round(raw, 4), round(val, 4)))
                conn.commit()
            fails = 0
            wait = every - (time.monotonic() - t0)
        except Exception as exc:
            fails += 1
            if fails in (1, 10) or fails % 300 == 0:
                print(f"[WELD-LIVE] {station} (sensor): read failed x{fails}: {exc}", flush=True)
            wait = min(30.0, every * (2 ** min(fails, 4)))
        stop.wait(max(0.2, wait))


# ── supervisor: keeps threads in sync with the master table ───────────────
def weld_poller_worker() -> None:
    """One thread per active master row; weld-current rows run _station_worker,
    gas/sensor rows run _sensor_worker.  2026-09-21 — a row whose settings
    change (channel, card, scale, …) is restarted within _RESCAN_S; before, an
    edit only took effect after the next API restart."""
    if os.environ.get("WELD_POLLER", "1") == "0":
        print("[WELD-LIVE] poller disabled (WELD_POLLER=0)", flush=True)
        return
    running: dict = {}          # station -> (thread, stop event, config signature)
    while not _STOP.is_set():
        rows = _load_stations()
        if rows is not None:
            seen = set()
            for cfg in rows:
                st = cfg["station"]
                seen.add(st)
                sig = tuple(sorted((k, str(v)) for k, v in cfg.items()))
                cur = running.get(st)
                if cur and cur[0].is_alive() and cur[2] == sig:
                    continue
                if cur:
                    cur[1].set()                    # changed or died: stop the old one
                    print(f"[WELD-LIVE] {st}: settings changed — restarting its worker",
                          flush=True)
                stop = threading.Event()
                kind = str(cfg.get("signal") or "current").lower()
                target = _sensor_worker if kind in ("gas", "sensor") else _station_worker
                th = threading.Thread(target=target, args=(cfg, stop),
                                      daemon=True, name=f"weld-{st}")
                th.start()
                running[st] = (th, stop, sig)
            for st in list(running):
                if st not in seen:                  # deleted or deactivated
                    running[st][1].set()
                    running.pop(st, None)
                    print(f"[WELD-LIVE] {st}: removed from the master — worker stopped",
                          flush=True)
        _STOP.wait(_RESCAN_S)


def start_weld_poller() -> None:
    threading.Thread(target=weld_poller_worker, daemon=True,
                     name="weld-live-supervisor").start()
