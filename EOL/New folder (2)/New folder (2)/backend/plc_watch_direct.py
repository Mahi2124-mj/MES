"""
Direct PLC bit watcher — no HTTP auth needed.
Reads PLC config from plcs.json and polls bits directly via MC3E/Modbus.
Run for 2 minutes and print every state change.
"""
import sys, os, time, struct, socket, json, re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from plc_config import list_plcs

_MC_DEVICE_CODES = {
    "X":0x9C,"Y":0x9D,"M":0x90,"L":0x92,"F":0x93,"V":0x94,
    "B":0xA0,"T":0xC0,"C":0xC1,"D":0xA8,"W":0xB4,"R":0xAF,
}

def _parse_device(addr):
    addr = (addr or "").strip().upper()
    m = re.match(r"^([A-Z]+)(\d+)$", addr)
    if m: return m.group(1), int(m.group(2))
    m = re.match(r"^(\d+)$", addr)
    if m: return "M", int(m.group(1))
    raise ValueError(f"Cannot parse: {addr!r}")

def read_mc3e_bit(ip, port, dev_code, dev_num, timeout=1.5):
    req = bytes([0x50,0x00,0x00,0xFF,0xFF,0x03,0x00,0x0C,0x00,0x10,0x00,
                 0x01,0x04,0x01,0x00]) + struct.pack("<I",dev_num)[:3] + bytes([dev_code]) + struct.pack("<H",1)
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(req)
        resp = b""
        s.settimeout(timeout)
        try:
            while len(resp) < 12:
                chunk = s.recv(64)
                if not chunk: break
                resp += chunk
        except socket.timeout: pass
    if len(resp) < 12: raise RuntimeError(f"MC3E short response ({len(resp)} bytes)")
    end_code = struct.unpack_from("<H", resp, 9)[0]
    if end_code != 0: raise RuntimeError(f"MC3E error 0x{end_code:04X}")
    return bool(resp[11] & 0x01)

def read_modbus_coil(ip, port, address, unit_id=1, timeout=1.5):
    import random
    tid = random.randint(0, 0xFFFF)
    req = struct.pack(">HHHBBHH", tid, 0, 6, unit_id, 1, address, 1)
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(req)
        resp = s.recv(1024)
    if len(resp) < 10: raise RuntimeError("Short Modbus response")
    if resp[7] & 0x80: raise RuntimeError(f"Modbus exception {resp[8]}")
    return bool(resp[9] & 0x01)

def read_bit(ip, port, bit_addr):
    dev_type, dev_num = _parse_device(bit_addr)
    if dev_type in _MC_DEVICE_CODES:
        return read_mc3e_bit(ip, port, _MC_DEVICE_CODES[dev_type], dev_num)
    return read_modbus_coil(ip, port, dev_num)

# ─── Main loop ────────────────────────────────────────────────────────────────
print("=" * 70)
print("  PLC BIT DIRECT MONITOR  —  2 minutes")
print("=" * 70)

plcs = [p for p in list_plcs(BASE_DIR) if p.get("enabled")]
if not plcs:
    print("No enabled PLCs found in plcs.json!")
    sys.exit(1)

print(f"Monitoring {len(plcs)} PLC(s):")
for p in plcs:
    bits = [b.strip() for b in str(p.get("bit_address","")).split(",") if b.strip()]
    print(f"  • {p.get('description') or p.get('ip')}  {p['ip']}:{p.get('port',502)}  bits={bits}")
print()
print(f"{'Time':<10} {'PLC':<20} {'Bit':<8} {'Value':<6} {'Event'}")
print("-" * 70)

prev = {}
rising_total = 0
start = time.time()

while time.time() - start < 120:
    for plc in plcs:
        ip   = str(plc.get("ip","")).strip()
        port = int(plc.get("port", 502) or 502)
        label = (plc.get("description") or ip)[:18]
        bits_raw = str(plc.get("bit_address",""))
        bits = [b.strip() for b in bits_raw.split(",") if b.strip()]

        for bit in bits:
            key = f"{plc['id']}|{bit}"
            try:
                val = read_bit(ip, port, bit)
            except Exception as e:
                val = None
                err_key = key + "|err"
                if prev.get(err_key) != str(e):
                    t = datetime.now().strftime("%H:%M:%S")
                    print(f"{t:<10} {label:<20} {bit:<8} {'ERR':<6} {e}")
                    prev[err_key] = str(e)
                continue

            prev_val = prev.get(key, "__INIT__")
            if prev_val != val:
                t = datetime.now().strftime("%H:%M:%S")
                if prev_val == "__INIT__":
                    status = "OFFLINE→ON" if val else "initial: OFF"
                    color = "\033[32m" if val else "\033[90m"
                elif val is True and prev_val is False:
                    rising_total += 1
                    color = "\033[92m"  # bright green
                    status = f">>> RISING EDGE #{rising_total} — CYCLE TRIGGERED <<<"
                elif val is False and prev_val is True:
                    color = "\033[33m"  # yellow
                    status = "Falling edge (ON → OFF)"
                else:
                    color = "\033[36m"
                    status = f"{prev_val} → {val}"
                reset = "\033[0m"
                vstr = "ON " if val else "OFF"
                print(f"{t:<10} {label:<20} {bit:<8} {color}{vstr:<6}{reset} {color}{status}{reset}")
                prev[key] = val

    time.sleep(0.5)

elapsed = int(time.time() - start)
print()
print("=" * 70)
print(f"  DONE — {rising_total} rising edge(s) detected in {elapsed}s")
print(f"  Each rising edge triggers a new cycle (rotate or start)")
print("=" * 70)
