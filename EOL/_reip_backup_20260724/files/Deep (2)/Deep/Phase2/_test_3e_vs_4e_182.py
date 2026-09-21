"""Compare Type3E vs Type4E on Lower Rail @ 192.168.10.182 — 30s each."""
import pymcprotocol, time

def test(plc_cls, label, ip):
    plc = plc_cls()
    try:
        plc.connect(ip, 5002)
        print(f"[{label} CONNECTED] {ip}", flush=True)
        prev, rises = 0, 0
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                v = plc.batchread_bitunits(headdevice="L108", readsize=1)[0]
            except Exception as e:
                print(f"[{label} READ-ERR] {e}", flush=True)
                time.sleep(0.5); continue
            if v == 1 and prev == 0:
                rises += 1
                print(f"  {label} L108 ON  @ {time.strftime('%H:%M:%S')}  rises={rises}",
                      flush=True)
            prev = v
            time.sleep(0.05)
        print(f"[{label} DONE] rises={rises}\n", flush=True)
    except Exception as e:
        print(f"[{label} FATAL] {e}", flush=True)
    finally:
        try: plc.close()
        except: pass

IP = "192.168.10.182"
print("=== Lower Rail Type3E ===", flush=True)
test(pymcprotocol.Type3E, "Type3E", IP)
print("=== Lower Rail Type4E ===", flush=True)
test(pymcprotocol.Type4E, "Type4E", IP)
