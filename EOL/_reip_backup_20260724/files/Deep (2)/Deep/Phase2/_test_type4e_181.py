"""Test: same Lock Bar PLC with Type4E (what collector uses) — 30 sec.
If this reads 0 always while Type3E sees toggles, PLC is Q-series and
needs Type3E.  Collector hardcoded to Type4E."""
import pymcprotocol, time

plc = pymcprotocol.Type4E()
IP, PORT = "192.168.10.181", 5002

try:
    plc.connect(IP, PORT)
    print(f"[CONNECTED Type4E] {IP}", flush=True)
    prev, rises = 0, 0
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            v = plc.batchread_bitunits(headdevice="L108", readsize=1)[0]
        except Exception as e:
            print(f"[READ-ERR] {e}", flush=True)
            time.sleep(0.5); continue
        if v == 1 and prev == 0:
            rises += 1
            print(f"L108 ON  @ {time.strftime('%H:%M:%S')}  rises={rises}", flush=True)
        elif v == 0 and prev == 1:
            print(f"L108 OFF @ {time.strftime('%H:%M:%S')}", flush=True)
        prev = v
        time.sleep(0.05)
    print(f"\n[DONE Type4E] {IP} 30s rises={rises}", flush=True)
except Exception as e:
    print(f"[FATAL Type4E] {e}", flush=True)
finally:
    try: plc.close()
    except: pass
