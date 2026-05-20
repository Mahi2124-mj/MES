import pymcprotocol
import time

plc = pymcprotocol.Type4E()
plc.connect('192.168.10.150', 5002)

count = 0
last_state = 0
while True:
    ok = plc.batchread_bitunits(headdevice='L108', readsize=1)
    if ok[0] == 1 and last_state == 0:
        count += 1
        print(f"Pulse {count} detected at {time.strftime('%H:%M:%S')}")
    last_state = ok[0]
    time.sleep(0.01)