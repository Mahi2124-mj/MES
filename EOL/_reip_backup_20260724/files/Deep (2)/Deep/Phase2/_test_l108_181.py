"""Quick L108 test — Lock Bar @ 192.168.10.181:5002 — 60 seconds.
Exits cleanly after 60s so we don't compete with the running collector
for the Mitsubishi 2-client slot longer than necessary."""
import pymcprotocol
import time

plc = pymcprotocol.Type3E()
TARGET_IP   = "192.168.10.181"
TARGET_PORT = 5002
RUN_SECONDS = 60

try:
    plc.connect(TARGET_IP, TARGET_PORT)
    print(f"[CONNECTED] {TARGET_IP}:{TARGET_PORT}", flush=True)

    previous_state = 0
    start_time     = None
    rises          = 0
    falls          = 0
    test_start     = time.time()

    while time.time() - test_start < RUN_SECONDS:
        try:
            l108 = plc.batchread_bitunits(headdevice="L108", readsize=1)[0]
        except Exception as e:
            print(f"[READ-ERR] {e}", flush=True)
            time.sleep(0.5)
            continue

        if l108 == 1 and previous_state == 0:
            start_time = time.time()
            rises += 1
            print(f"L108 ON  @ {time.strftime('%H:%M:%S')}.{int((start_time%1)*1000):03d}",
                  flush=True)
        elif l108 == 0 and previous_state == 1:
            end_time     = time.time()
            on_duration  = end_time - (start_time or end_time)
            falls += 1
            print(f"L108 OFF @ {time.strftime('%H:%M:%S')}.{int((end_time%1)*1000):03d}"
                  f"  on_duration={on_duration:.3f}s", flush=True)
            print("----------------------", flush=True)

        previous_state = l108
        time.sleep(0.05)

    print(f"\n[DONE] {TARGET_IP} — {RUN_SECONDS}s window: "
          f"rises={rises}, falls={falls}", flush=True)

except Exception as e:
    print(f"[FATAL] {TARGET_IP}: {e}", flush=True)

finally:
    try:
        plc.close()
        print(f"[CLOSED] {TARGET_IP}", flush=True)
    except Exception:
        pass
