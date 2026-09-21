"""V2 monitor — reads L108 and L109 in SEPARATE calls (matches collector behaviour).
V1 used batch read which may have had bit-ordering issue.  Run 2 min."""
import pymcprotocol, time
from datetime import datetime

PLC_IP, PLC_PORT = "192.168.10.150", 5002
DURATION = 120  # 2 min
POLL_MS = 30

plc = pymcprotocol.Type3E()
plc.connect(PLC_IP, PLC_PORT)
print(f"[CONNECTED] {PLC_IP}:{PLC_PORT}", flush=True)

last_108, last_109 = 0, 0
rises_108, rises_109 = 0, 0
rise_ts_108, rise_ts_109 = None, None
start = time.time()
polls = 0
err = 0
print(f"[START] {DURATION}s, separate L108 + L109 reads at {POLL_MS}ms", flush=True)

while time.time() - start < DURATION:
    try:
        b108 = int(plc.batchread_bitunits(headdevice="L108", readsize=1)[0])
        b109 = int(plc.batchread_bitunits(headdevice="L109", readsize=1)[0])
        polls += 1
    except Exception as e:
        err += 1
        time.sleep(0.05)
        continue

    now = time.time()
    now_dt = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    if last_108 == 0 and b108 == 1:
        rises_108 += 1
        rise_ts_108 = now
        print(f"[L108-RISE]  {now_dt}", flush=True)
    elif last_108 == 1 and b108 == 0 and rise_ts_108:
        print(f"[L108-FALL]  {now_dt}  held {(now-rise_ts_108)*1000:.0f}ms", flush=True)
        rise_ts_108 = None
    if last_109 == 0 and b109 == 1:
        rises_109 += 1
        rise_ts_109 = now
        print(f"[L109-RISE]  {now_dt}  *** NG ***", flush=True)
    elif last_109 == 1 and b109 == 0 and rise_ts_109:
        print(f"[L109-FALL]  {now_dt}  held {(now-rise_ts_109)*1000:.0f}ms", flush=True)
        rise_ts_109 = None
    last_108, last_109 = b108, b109
    time.sleep(POLL_MS/1000.0)

plc.close()
print(f"\n=== DONE === polls={polls} err={err}")
print(f"L108 rises: {rises_108}")
print(f"L109 rises: {rises_109}")
