import pymcprotocol, time
plc = pymcprotocol.Type3E()
plc.connect("192.168.10.181", 5002)
prev, rises = 0, 0
t0 = time.time()
while time.time() - t0 < 30:
    try:
        v = plc.batchread_bitunits(headdevice="L108", readsize=1)[0]
        if v == 1 and prev == 0:
            rises += 1
            print(f"ON @ {time.strftime('%H:%M:%S')}  rises={rises}", flush=True)
        prev = v
    except: pass
    time.sleep(0.05)
plc.close()
print(f"30s: rises={rises}")
