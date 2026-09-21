"""
routers/network.py
==================
Switch-network monitor backend.  2026-06-20.

Background SNMP poller (pysnmp v2c, read-only) over a small JSON device
registry → GET /api/network/status returns the live snapshot the Network
Panel renders.  Validated OIDs on DGS-1210-28P FW 6.32.008:
  reachable + sysUpTime (1.3.6.1.2.1.1.3.0)
  port up/total via ifOperStatus walk (1.3.6.1.2.1.2.2.1.8; 1=up)
  PoE total watts (pethMainPseConsumptionPower 1.3.6.1.2.1.105.1.3.1.1.4.1)
LLDP wire-map, per-port PoE watts and VLAN are phase-2 (need switches
interconnected / D-Link private MIB).  No writes to any switch.
"""
import os
import re
import json
import time
import socket
import asyncio
import threading
import subprocess
from typing import Optional, List

SCAN_SUBNET = "192.168.10"   # the plant /24 the Discover scan sweeps

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user, require_admin
from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, get_cmd, walk_cmd,
)

router = APIRouter(prefix="/api/network", tags=["network"])

_HERE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Phase2/
_DEV_FILE = os.path.join(_HERE, "network_devices.json")
_CAB_FILE = os.path.join(_HERE, "network_cables.json")
_POLL_EVERY = 20
POE_BUDGET = 193

# Seed registry = the seat-slider ring topology.  Only 192.168.30.7 has an IP
# so far (the switch the user enabled SNMP on); the rest are "unconfigured"
# until their mgmt IPs are assigned.  x/y drive the topology layout.
# Devices carry `slot` = the floor-plan box they sit in.  Only 192.168.30.7
# has an IP so far.  The PLATFORM boxes are switches too (core role).
DEFAULT_DEVICES = [
    {"id": "core_a", "name": "PLATFORM-L", "role": "core", "slot": "platform_l", "x": 20,  "y": 36,  "ip": "",            "community": "public", "vlan": "trunk", "poe_budget": POE_BUDGET},
    {"id": "ysd",    "name": "YSD",  "role": "line", "slot": "ysd", "x": 190, "y": 36,  "ip": "192.168.30.7","community": "public", "vlan": "10", "zone": "SS-1", "parent": "core_a", "poe_budget": POE_BUDGET},
    {"id": "yca",    "name": "YCA",  "role": "line", "slot": "yca", "x": 360, "y": 36,  "ip": "",            "community": "public", "vlan": "20", "zone": "SS-2", "parent": "ysd",    "poe_budget": POE_BUDGET},
    {"id": "ync",    "name": "YNC",  "role": "line", "slot": "ync", "x": 530, "y": 36,  "ip": "",            "community": "public", "vlan": "30", "zone": "SS-3", "parent": "yca",    "poe_budget": POE_BUDGET},
    {"id": "y17",    "name": "Y17",  "role": "line", "slot": "y17", "x": 700, "y": 36,  "ip": "",            "community": "public", "vlan": "40", "zone": "SS-4", "parent": "ync",    "poe_budget": POE_BUDGET},
    {"id": "core_b", "name": "PLATFORM-C", "role": "core", "slot": "platform_c", "x": 870, "y": 36,  "ip": "",            "community": "public", "vlan": "trunk", "parent": "y17", "poe_budget": POE_BUDGET},
    {"id": "yhb", "name": "YHB", "role": "downlink", "slot": "yhb", "x": 190, "y": 210, "ip": "", "community": "public", "vlan": "10", "zone": "SS-1", "parent": "ysd", "poe_budget": POE_BUDGET},
    {"id": "yra", "name": "YRA", "role": "downlink", "slot": "yra", "x": 360, "y": 210, "ip": "", "community": "public", "vlan": "20", "zone": "SS-2", "parent": "yca", "poe_budget": POE_BUDGET},
    {"id": "yjc", "name": "YJC", "role": "downlink", "slot": "yjc", "x": 530, "y": 210, "ip": "", "community": "public", "vlan": "30", "zone": "SS-3", "parent": "ync", "poe_budget": POE_BUDGET},
    {"id": "yfg", "name": "YFG", "role": "downlink", "slot": "yfg", "x": 700, "y": 210, "ip": "", "community": "public", "vlan": "40", "zone": "SS-4", "parent": "y17", "poe_budget": POE_BUDGET},
]
# Floor cables are keyed by SLOT/box id (the floor is the cabling source of
# truth).  SEED EMPTY — no invented cables.  The user draws the real runs
# (the blue lines on their layout) from the floor "Connect" mode; each saved
# cable persists to network_cables.json and is monitored live.
DEFAULT_CABLES = []

def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [dict(x) for x in default]

_file_lock = threading.Lock()

def _save(path, data):
    # Atomic write: serialise to a temp file then os.replace() so a crash or a
    # concurrent writer can never leave a half-written JSON that _load() rejects
    # → silent revert to DEFAULT_DEVICES (whole-topology wipe).  Serialised by a
    # lock so two PUTs can't interleave.
    import os
    with _file_lock:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)

_devices = _load(_DEV_FILE, DEFAULT_DEVICES)
_cables  = _load(_CAB_FILE, DEFAULT_CABLES)
_snapshot = {"devices": [], "cables": _cables, "generated_at": None, "demo": False}
_lock = threading.Lock()


# ── ARP auto-discovery (what IP sits where) ────────────────────────────
# Reads the server's OS ARP table (like Ubuntu `arp -a`) → {mac: ip}, then
# joins it per switch with the switch FDB (mac → port) so each port shows
# the device/IP learned on it.  Refreshed in a background thread.
_arp_cache = {}
_arp_lock = threading.Lock()
_dns_cache = {}            # ip -> reverse-DNS hostname (filled by Discover scan)


def _norm_mac(s: str):
    h = re.sub(r"[^0-9a-fA-F]", "", s or "").lower()
    if len(h) != 12:
        return None
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _refresh_arp():
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=12).stdout
    except Exception:
        return
    m = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", parts[0]):
            mac = _norm_mac(parts[1])
            if mac and mac != "ff:ff:ff:ff:ff:ff" and not mac.startswith("01:00:5e"):
                m[mac] = parts[0]
    if m:
        with _arp_lock:
            _arp_cache.clear()
            _arp_cache.update(m)


# ── Linux neighbour scan for IP-conflict detection (2026-08-01) ─────────
# The `arp -a` parsing above is Windows-format and yields nothing on this
# Linux box; for conflict detection we read `ip neigh` directly.  We keep a
# short rolling history of every (ip -> mac) seen so we can flag not just an
# instantaneous duplicate (one IP live on 2 MACs right now) but also ARP
# FLUX — an IP whose MAC keeps changing, the signature of two devices
# fighting for the same address (the intermittent-drop class of bug).
_neigh_flux = {}            # ip -> { mac: last_seen_epoch }
_neigh_lock = threading.Lock()
_NEIGH_FLUX_WINDOW = 900    # 15 min

def _scan_neigh(update_flux: bool = True):
    """Return {ip: set(macs)} from the OS neighbour table, and (optionally)
    fold it into the rolling flux history.  Read-only."""
    cur = {}
    try:
        out = subprocess.run(["ip", "neigh", "show"], capture_output=True,
                             text=True, timeout=8).stdout
    except Exception:
        out = ""
    for line in out.splitlines():
        m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+.*\blladdr\s+([0-9a-fA-F:]{17})", line)
        if not m:
            continue
        ip  = m.group(1)
        mac = _norm_mac(m.group(2))
        if not mac or mac == "ff:ff:ff:ff:ff:ff":
            continue
        cur.setdefault(ip, set()).add(mac)
    if update_flux and cur:
        now = time.time()
        with _neigh_lock:
            for ip, macs in cur.items():
                slot = _neigh_flux.setdefault(ip, {})
                for mac in macs:
                    slot[mac] = now
            for ip in list(_neigh_flux):          # prune stale
                _neigh_flux[ip] = {mac: ts for mac, ts in _neigh_flux[ip].items()
                                   if now - ts <= _NEIGH_FLUX_WINDOW}
                if not _neigh_flux[ip]:
                    del _neigh_flux[ip]
    return cur


def _arp_poller():
    while True:
        try:
            _refresh_arp()
            _scan_neigh(update_flux=True)   # 2026-08-01 — feed IP-conflict flux history
        except Exception as exc:
            print(f"[NET-ARP] error: {exc}", flush=True)
        time.sleep(90)


def _ping(ip: str) -> bool:
    """ICMP reachability for PC / endpoint nodes (Windows ping)."""
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", "700", ip],
                           capture_output=True, text=True, timeout=4)
        out = (r.stdout or "").lower()
        return r.returncode == 0 and "ttl=" in out
    except Exception:
        return False


# 2026-08-10 — SNMP UDP SOCKET LEAK.  Every SnmpEngine() opens its own UDP
# transport socket, and pysnmp only releases it when the dispatcher is closed.
# Both call sites below built a fresh engine per query and never closed it, so
# the API accumulated one UDP fd per SNMP poll — the switch poller runs every
# 90 s over every device, which is the steady ~13 fd/min that grew the process
# to 8,500 fds / 8.9 GB RSS.  `ss -tnp` showed almost nothing because these are
# UDP, not TCP, which is what made it hard to spot.
def _close_snmp_engine(eng) -> None:
    """Release an SnmpEngine's UDP transport.  Best-effort across pysnmp
    versions — the method name moved between releases."""
    for attr in ("close_dispatcher", "closeDispatcher"):
        fn = getattr(eng, attr, None)
        if callable(fn):
            try:
                fn()
                return
            except Exception:
                pass
    td = getattr(eng, "transport_dispatcher", None) or getattr(eng, "transportDispatcher", None)
    if td is not None:
        for attr in ("close_dispatcher", "closeDispatcher"):
            fn = getattr(td, attr, None)
            if callable(fn):
                try:
                    fn()
                    return
                except Exception:
                    pass


async def _snmp_sysinfo(ip: str):
    """Return (sysName, sysDescr) if the IP answers SNMP 'public', else None."""
    eng = SnmpEngine()
    try:
        tgt = await UdpTransportTarget.create((ip, 161), timeout=1, retries=0)
        ei, es, ix, vb = await get_cmd(eng, CommunityData("public", mpModel=1), tgt, ContextData(),
                                       ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0")),   # sysName
                                       ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")))   # sysDescr
        if ei or es:
            return None
        return (vb[0][1].prettyPrint(), vb[1][1].prettyPrint())
    except Exception:
        return None
    finally:
        _close_snmp_engine(eng)


async def _discover():
    """One-shot active scan of the /24: ping-sweep (alive + ARP), SNMP sweep
    (switches + sysName/sysDescr), reverse-DNS (names).  Returns a list the
    UI can one-click add as nodes — name + IP auto."""
    # 1) ping-sweep to populate the ARP table (catches silent hosts)
    sem = asyncio.Semaphore(60)
    async def png(i):
        async with sem:
            await asyncio.to_thread(_ping, f"{SCAN_SUBNET}.{i}")
    await asyncio.gather(*[png(i) for i in range(1, 255)])
    _refresh_arp()
    with _arp_lock:
        arp = dict(_arp_cache)                 # mac -> ip
    ip2mac = {ip: mac for mac, ip in arp.items()}

    # 2) SNMP sweep (which IPs are switches + their sysName/sysDescr)
    snmp = {}
    sem2 = asyncio.Semaphore(40)
    async def sn(i):
        ip = f"{SCAN_SUBNET}.{i}"
        async with sem2:
            r = await _snmp_sysinfo(ip)
        if r:
            snmp[ip] = r
    await asyncio.gather(*[sn(i) for i in range(1, 255)])

    # 3) reverse-DNS for every candidate (alive OR snmp) — plant /24 only
    #    (arp -a also returns other interfaces e.g. 192.168.1.x — drop those)
    ips = {ip for ip in (set(ip2mac.keys()) | set(snmp.keys()))
           if ip.startswith(SCAN_SUBNET + ".")}
    async def dns(ip):
        try:
            h = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout=1.5)
            return ip, h[0]
        except Exception:
            return ip, ""
    for ip, host in await asyncio.gather(*[dns(ip) for ip in ips]):
        _dns_cache[ip] = host

    out = []
    for ip in sorted(ips, key=lambda s: tuple(int(x) for x in s.split("."))):
        last = ip.split(".")[-1]
        is_sw = ip in snmp
        sysname, descr = snmp.get(ip, ("", ""))
        host = _dns_cache.get(ip, "")
        name = (sysname.strip() if sysname and sysname.strip() else "") or host or \
               (f"Switch-{last}" if is_sw else f"PC-{last}")
        out.append({
            "ip": ip,
            "mac": ip2mac.get(ip, ""),
            "kind": "switch" if is_sw else "pc",
            "name": name,
            "descr": (descr[:60] if is_sw else host),
        })
    return out


def _rebuild_snapshot_quick():
    """Refresh the snapshot WITHOUT doing SNMP — used right after an edit so
    the UI reflects added/moved/renamed nodes instantly.  Keeps the last
    known live status for unchanged switches; new / IP-changed switches show
    neutral ('unconfigured') until the next 20s poll fills real status in.
    Area nodes are never polled."""
    with _lock:
        old = {d.get("id"): d for d in _snapshot.get("devices", [])}
        src = [dict(d) for d in _devices]
    devs = []
    for d in src:
        rec = dict(d)
        rec.pop("community", None)
        if d.get("kind") == "area":
            rec["status"] = "area"
            rec["live"] = None
        else:
            prev = old.get(d.get("id"))
            if prev and prev.get("ip") == d.get("ip") and prev.get("status") in ("up", "warn", "down"):
                for k in ("status", "live", "portsUp", "portsTotal", "ports", "poe", "uptime", "snmp"):
                    if k in prev:
                        rec[k] = prev[k]
            else:
                rec["status"] = "unconfigured"
                rec["live"] = None
        devs.append(rec)
    with _lock:
        _snapshot["devices"] = devs
        _snapshot["cables"] = _cables
        _snapshot["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


async def _poll_one(ip: str, community: str) -> dict:
    eng = SnmpEngine()
    out = {"reachable": False}
    comm = CommunityData(community or "public", mpModel=1)
    try:
        tgt = await UdpTransportTarget.create((ip, 161), timeout=2, retries=1)
        ei, es, ix, vb = await get_cmd(eng, comm, tgt, ContextData(),
                                       ObjectType(ObjectIdentity("1.3.6.1.2.1.1.3.0")))
        if ei or es:
            # SNMP silent — fall back to ICMP so a live-but-unmanaged switch
            # (SNMP off, or a different community) is NOT shown as dead.
            out["snmp"] = False
            out["reachable"] = await asyncio.to_thread(_ping, ip)
            return out
        out["reachable"] = True
        out["snmp"] = True
        try: out["uptime"] = int(vb[0][1])
        except Exception: pass
        # PoE total consumption (W)
        try:
            ei, es, ix, vb = await get_cmd(eng, comm, tgt, ContextData(),
                                           ObjectType(ObjectIdentity("1.3.6.1.2.1.105.1.3.1.1.4.1")))
            if not ei and not es:
                out["poe"] = int(vb[0][1])
        except Exception: pass
        # ── per-port detail ──────────────────────────────────────────
        #   ifOperStatus (1=up) · ifHighSpeed (Mbps) · PoE-MIB pethPsePortEntry
        #   (col .3 = AdminEnable → PoE-capable, col .6 = DetectionStatus,
        #    3 = deliveringPower).  Ports 25-28 are the SFP cages on this model.
        oper, speed, poe_admin, poe_detect = {}, {}, {}, {}

        async def _walk(oid, store, conv=int):
            async for (e1, e2, _ix, vbs) in walk_cmd(eng, comm, tgt, ContextData(),
                                                     ObjectType(ObjectIdentity(oid)),
                                                     lexicographicMode=False):
                if e1 or e2:
                    break
                for n, v in vbs:
                    try:
                        store[int(str(n).split(".")[-1])] = conv(v)
                    except Exception:
                        pass

        await _walk("1.3.6.1.2.1.2.2.1.8", oper)            # ifOperStatus
        await _walk("1.3.6.1.2.1.31.1.1.1.15", speed)        # ifHighSpeed (Mbps)

        # PoE columns share the pethPsePortEntry subtree (…105.1.1.1.<col>.<grp>.<port>)
        async for (e1, e2, _ix, vbs) in walk_cmd(eng, comm, tgt, ContextData(),
                                                 ObjectType(ObjectIdentity("1.3.6.1.2.1.105.1.1.1")),
                                                 lexicographicMode=False):
            if e1 or e2:
                break
            for n, v in vbs:
                parts = str(n).split(".")
                try:
                    port = int(parts[-1]); col = int(parts[-3]); val = int(v)
                except Exception:
                    continue
                if col == 3:
                    poe_admin[port] = val
                elif col == 6:
                    poe_detect[port] = val

        ports, up = [], 0
        for p in sorted(oper):
            isup = oper[p] == 1
            if isup:
                up += 1
            ports.append({
                "port":    p,
                "up":      isup,
                "speed":   speed.get(p, 0),
                "poe":     poe_admin.get(p) == 1,         # PoE admin-enabled (TruthValue true(1); false(2) → off)
                "powered": poe_detect.get(p) == 3,        # delivering power right now
                "sfp":     p >= 25,
            })
        out["ports"] = ports
        out["portsUp"], out["portsTotal"] = up, len(ports)

        # ── auto-discovery: device/IP learned on each port (switch FDB ⋈ ARP) ──
        try:
            bp2if = {}
            async for (e1, e2, _ix, vbs) in walk_cmd(eng, comm, tgt, ContextData(),
                    ObjectType(ObjectIdentity("1.3.6.1.2.1.17.1.4.1.2")), lexicographicMode=False):
                if e1 or e2:
                    break
                for n, v in vbs:
                    try:
                        bp2if[int(str(n).split(".")[-1])] = int(v)   # basePort → ifIndex
                    except Exception:
                        pass

            port2macs = {}

            async def _fdbwalk(oid):
                async for (e1, e2, _ix, vbs) in walk_cmd(eng, comm, tgt, ContextData(),
                        ObjectType(ObjectIdentity(oid)), lexicographicMode=False):
                    if e1 or e2:
                        break
                    for n, v in vbs:
                        parts = str(n).split(".")
                        try:
                            mac = ":".join("%02x" % int(o) for o in parts[-6:])
                            bport = int(v)
                        except Exception:
                            continue
                        ifidx = bp2if.get(bport, bport)
                        port2macs.setdefault(ifidx, []).append(mac)

            await _fdbwalk("1.3.6.1.2.1.17.7.1.2.2.1.2")     # dot1qTpFdbPort (VLAN-aware)
            if not port2macs:
                await _fdbwalk("1.3.6.1.2.1.17.4.3.1.2")      # dot1dTpFdbPort (fallback)

            with _arp_lock:
                arp = dict(_arp_cache)
            for p in ports:
                macs = port2macs.get(p["port"], [])
                p["learned"] = [{"mac": mc, "ip": arp.get(mc, ""), "host": _dns_cache.get(arp.get(mc, ""), "")} for mc in macs]
        except Exception:
            pass
    except Exception as exc:
        out["error"] = str(exc)
        out["snmp"] = False
        try:
            out["reachable"] = await asyncio.to_thread(_ping, ip)
        except Exception:
            pass
    finally:
        # 2026-08-10 — release this engine's UDP transport (see the note on
        # _close_snmp_engine).  Without it every poll round stranded one fd per
        # device, forever.
        _close_snmp_engine(eng)
    return out


async def _poll_all() -> list:
    out = []
    with _lock:
        src = [dict(d) for d in _devices]   # copy under lock; awaits below never touch the live list
    for d in src:
        rec = dict(d)
        rec.pop("community", None)   # never leak community to the UI
        # Area / label nodes are floor-plan context only — never SNMP-polled.
        if d.get("kind") == "area":
            rec["status"] = "area"
            rec["live"] = None
            out.append(rec)
            continue
        # PC / endpoint nodes — monitored by ICMP ping (not SNMP).
        if d.get("kind") == "pc":
            ip = (d.get("ip") or "").strip()
            if not ip:
                rec["status"] = "unconfigured"
                rec["live"] = None
            else:
                alive = await asyncio.to_thread(_ping, ip)
                rec["status"] = "up" if alive else "down"
                rec["live"] = {"reachable": alive}
            out.append(rec)
            continue
        ip = (d.get("ip") or "").strip()
        if not ip:
            rec["status"] = "unconfigured"
            rec["live"] = None
            out.append(rec)
            continue
        live = await _poll_one(ip, d.get("community"))
        rec["live"] = live
        rec["snmp"] = bool(live.get("snmp"))
        if not live.get("reachable"):
            rec["status"] = "down"
        elif not live.get("snmp"):
            rec["status"] = "up"          # alive via ICMP ping, SNMP not managed
        else:
            budget = d.get("poe_budget") or POE_BUDGET   # fall back to sane default so an unbudgeted switch can still warn
            pct = (live.get("poe", 0) / budget * 100) if budget else 0
            rec["status"] = "warn" if pct >= 90 else "up"
            if live.get("portsUp") is not None:
                rec["portsUp"], rec["portsTotal"] = live["portsUp"], live["portsTotal"]
            if live.get("ports") is not None:
                rec["ports"] = live["ports"]
            if live.get("poe") is not None:
                rec["poe"] = live["poe"]
            if live.get("uptime") is not None:
                rec["uptime"] = live["uptime"]
        out.append(rec)
    return out


# 2026-09-17 — SHARE THE SNAPSHOT ACROSS UVICORN WORKERS.
# `_snapshot` is module-level, so it lives per PROCESS.  That was invisible
# while the API ran a single worker.  Now it runs four and only ONE of them
# (the background leader, see bg_leader.py) runs the poller — so three workers
# held an empty snapshot and /status answered `devices: []  demo: true` for
# roughly three of every four requests.  On screen the Network panel flickered
# between the full map and "nothing configured".
# The poller writes its result here and any worker whose own memory is empty
# reads it back, so there is still exactly one poller and no extra pings.
_SNAP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          ".network_snapshot.json")
_SNAP_MAX_AGE = 180          # seconds; older than this is stale, not shared state


def _publish_snapshot(snap: dict) -> None:
    try:
        tmp = _SNAP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, default=str)
        os.replace(tmp, _SNAP_FILE)      # atomic: a reader never sees a half file
    except Exception as exc:
        print(f"[NET-POLL] could not publish snapshot: {exc}", flush=True)


def _read_published_snapshot():
    """The leader's latest snapshot, or None when it is missing or stale."""
    try:
        if time.time() - os.path.getmtime(_SNAP_FILE) > _SNAP_MAX_AGE:
            return None
        with open(_SNAP_FILE, encoding="utf-8") as fh:
            snap = json.load(fh)
        return snap if snap.get("devices") else None
    except Exception:
        return None


def _poller():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("[NET-POLL] network poller started (every "
          f"{_POLL_EVERY}s; {sum(1 for d in _devices if d.get('ip'))} configured)")
    while True:
        try:
            devs = loop.run_until_complete(_poll_all())
            with _lock:
                _snapshot["devices"] = devs
                _snapshot["cables"] = _cables
                _snapshot["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _publish_snapshot(dict(_snapshot))
        except Exception as exc:
            print(f"[NET-POLL] error: {exc}", flush=True)
        time.sleep(_POLL_EVERY)


def start_network_poller():
    _rebuild_snapshot_quick()      # show the saved map immediately on boot
    _refresh_arp()                 # seed ARP table for auto-discovery
    threading.Thread(target=_arp_poller, name="net-arp", daemon=True).start()
    threading.Thread(target=_poller, name="net-poller", daemon=True).start()


# ── endpoints ────────────────────────────────────────────────────────
@router.get("/status")
def network_status(user=Depends(get_current_user)):
    with _lock:
        if _snapshot["devices"]:
            return dict(_snapshot)
    # This worker does not run the poller — serve what the leader published.
    shared = _read_published_snapshot()
    if shared:
        return shared
    return {"devices": [], "cables": _cables, "generated_at": None, "demo": True}

# 2026-09-17 — ONE scan at a time, and never an unbounded one.
# Measured: a full sweep takes ~45 s (254 pings + SNMP), and while the plant
# network is down every one of them times out.  pmaudit/chaos.py fired six of
# these at once and an ordinary page went from 25 ms to 18 SECONDS — a couple
# of impatient clicks on "Discover" can starve the whole API, because each
# request holds a worker for its entire duration.
# The lock makes concurrent callers share the one scan in flight instead of
# starting their own, and the timeout stops a dead network from pinning a
# worker indefinitely.
_DISCOVER_LOCK    = asyncio.Lock()
_DISCOVER_TIMEOUT = int(os.environ.get("NETWORK_DISCOVER_TIMEOUT", "60") or 60)


@router.get("/discover")
async def network_discover(admin=Depends(require_admin)):
    """Active scan of the plant /24 → devices found with name + IP auto
    (switches via SNMP sysName, hosts via reverse-DNS / ping)."""
    if _DISCOVER_LOCK.locked():
        raise HTTPException(
            409, "A network scan is already running — wait for it to finish.")
    async with _DISCOVER_LOCK:
        try:
            devs = await asyncio.wait_for(_discover(), timeout=_DISCOVER_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(
                504, f"Network scan did not finish in {_DISCOVER_TIMEOUT}s — "
                     "devices are not answering (check the switches).")
    return {"devices": devs, "count": len(devs)}

@router.get("/ip-conflicts")
def network_ip_conflicts(user=Depends(get_current_user)):
    """Detect IP address conflicts — READ-ONLY (nothing is written/changed).
      • config : the SAME IP assigned to 2+ devices across the configs
                 (PLCs, cameras, the network-builder registry).
      • live   : an IP the OS neighbour table currently resolves to 2+
                 different MACs — a real on-the-wire conflict.
      • flux   : an IP whose MAC CHANGED within the last 15 min (two devices
                 fighting for one address — the intermittent-drop signature)."""
    conflicts = []

    # ── 1) CONFIG-level: same IP configured on 2+ devices ────────────────
    ip_owners = {}      # ip -> set of "PLC:name" / "CAM:name" / "NET:name"
    def _own(ip, label):
        ip = (ip or "").strip()
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            ip_owners.setdefault(ip, set()).add(label)
    # (a) PLCs — mes_plc_configs is authoritative (the collectors talk to it).
    #     mes_machine_master is deliberately NOT used: it is stale/mis-seeded
    #     and produced dozens of phantom conflicts.
    try:
        from database import get_conn, dict_cursor
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT plc_ip, COALESCE(NULLIF(machine_name,''),'PLC') n "
                        "FROM mes_plc_configs WHERE COALESCE(plc_ip,'')<>''")
            for r in cur.fetchall(): _own(r["plc_ip"], f"PLC:{r['n']}")
    except Exception as exc:
        print(f"[NET-CONFLICT] db read failed: {exc}", flush=True)
    # (b) Cameras — the CMS's live cameras.json (authoritative camera IPs).
    for cj in (
        os.path.join(_HERE, "..", "..", "..", "New folder (2)", "New folder (2)", "backend", "cameras.json"),
        os.path.join(_HERE, "..", "..", "New folder (2)", "New folder (2)", "backend", "cameras.json"),
    ):
        if os.path.exists(cj):
            try:
                cams = json.load(open(cj, encoding="utf-8"))
                cams = cams.get("cameras", cams) if isinstance(cams, dict) else cams
                for cc in (cams or []):
                    _own(cc.get("ip"), f"CAM:{cc.get('name') or cc.get('id') or '?'}")
            except Exception as exc:
                print(f"[NET-CONFLICT] cameras.json read failed: {exc}", flush=True)
            break
    # (c) the network-builder's own registry.
    for d in _devices:
        _own(d.get("ip"), f"NET:{d.get('name') or d.get('id')}")
    for ip, owners in ip_owners.items():
        if len(owners) > 1:
            conflicts.append({
                "type": "config", "sev": "warn", "ip": ip, "dev": ip, "t": "config",
                "msg": f"IP assigned to {len(owners)} devices — " + ", ".join(sorted(owners)),
                "owners": sorted(owners),
            })

    # ── 2) LIVE wire-level: neighbour table (ip -> 2+ MACs right now) ─────
    cur_macs = _scan_neigh(update_flux=True)
    live_ips = set()
    for ip, macs in cur_macs.items():
        if len(macs) > 1:
            live_ips.add(ip)
            conflicts.append({
                "type": "live", "sev": "down", "ip": ip, "dev": ip, "t": "live",
                "msg": f"LIVE conflict — {ip} is answering on {len(macs)} MACs: " + ", ".join(sorted(macs)),
                "macs": sorted(macs),
            })

    # ── 3) FLUX: MAC changed within the window (even if 1 live now) ──────
    with _neigh_lock:
        flux = {ip: sorted(m) for ip, m in _neigh_flux.items() if len(m) > 1}
    for ip, macs in flux.items():
        if ip in live_ips:
            continue                            # already a hard live conflict
        conflicts.append({
            "type": "flux", "sev": "warn", "ip": ip, "dev": ip, "t": "flux",
            "msg": f"ARP flux — {ip} changed MAC ({len(macs)} seen in 15 min): " + ", ".join(macs),
            "macs": macs,
        })

    conflicts.sort(key=lambda c: {"down": 0, "warn": 1}.get(c["sev"], 2))
    return {
        "conflicts": conflicts,
        "config": sum(1 for c in conflicts if c["type"] == "config"),
        "live":   sum(1 for c in conflicts if c["type"] == "live"),
        "flux":   sum(1 for c in conflicts if c["type"] == "flux"),
        "count":  len(conflicts),
    }


@router.get("/devices")
def list_network_devices(user=Depends(get_current_user)):
    # strip community
    return [{k: v for k, v in d.items() if k != "community"} for d in _devices]

class DeviceUpsert(BaseModel):
    devices: List[dict]

@router.put("/devices")
def save_network_devices(body: DeviceUpsert, admin=Depends(require_admin)):
    """Replace the device registry (admin).  Keeps existing community if the
    incoming row omits it (so the UI never has to handle the secret)."""
    global _devices
    with _lock:
        old = {d.get("id"): d for d in _devices if d.get("id")}   # .get: never KeyError on a legacy id-less row
    merged, seen = [], set()
    for d in body.devices:
        row = dict(d)
        rid = row.get("id")
        if not rid or rid in seen:
            continue                       # id-less or duplicate → skip; both corrupt the id→device map + cable refs
        seen.add(rid)
        if not row.get("community") and rid in old:
            row["community"] = old[rid].get("community", "public")
        row.setdefault("community", "public")
        merged.append(row)
    dropped = len(body.devices) - len(merged)
    with _lock:
        _devices = merged
    _save(_DEV_FILE, merged)
    _rebuild_snapshot_quick()      # reflect the edit instantly (no 20s wait)
    return {"ok": True, "count": len(merged), "dropped": dropped}

@router.get("/cables")
def list_network_cables(user=Depends(get_current_user)):
    return _cables

class CableUpsert(BaseModel):
    cables: List[dict]

@router.put("/cables")
def save_network_cables(body: CableUpsert, admin=Depends(require_admin)):
    """Replace the floor cable list (admin) — used by the floor Connect mode."""
    global _cables
    new_cables = [dict(c) for c in body.cables]
    with _lock:
        _cables = new_cables
        _snapshot["cables"] = new_cables
    _save(_CAB_FILE, new_cables)
    return {"ok": True, "count": len(new_cables)}
