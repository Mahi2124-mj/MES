"""5-min L108/L109 monitor — independent of collector.
Polls both bits at 30ms cadence, captures every rise/fall with timestamps.
Reports: rises count, pulse widths (min/avg/max), total ON time, gaps."""
import pymcprotocol, time, sys
from datetime import datetime

PLC_IP   = "192.168.30.150"
PLC_PORT = 5002
DURATION = 300  # 5 minutes
POLL_MS  = 30

def main():
    plc = pymcprotocol.Type3E()
    try:
        plc.connect(PLC_IP, PLC_PORT)
        print(f"[CONNECTED] {PLC_IP}:{PLC_PORT}", flush=True)
    except Exception as e:
        print(f"[CONNECT-FAIL] {e}")
        sys.exit(1)

    last_l108 = 0
    last_l109 = 0
    rise_ts_108 = None
    rise_ts_109 = None
    events_108 = []  # list of (rise_ts, fall_ts, width_s)
    events_109 = []
    last_print = time.time()
    read_errors = 0
    polls = 0

    start = time.time()
    print(f"[START] monitoring for {DURATION}s at {POLL_MS}ms polls", flush=True)

    while time.time() - start < DURATION:
        try:
            bits = plc.batchread_bitunits(headdevice="L108", readsize=2)
            cur_108 = 1 if int(bits[0]) else 0
            cur_109 = 1 if int(bits[1]) else 0
            polls += 1
        except Exception as e:
            read_errors += 1
            time.sleep(0.05)
            continue

        now = time.time()
        now_dt = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # L108 edges
        if last_l108 == 0 and cur_108 == 1:
            rise_ts_108 = now
            print(f"[L108-RISE]  {now_dt}", flush=True)
        elif last_l108 == 1 and cur_108 == 0:
            if rise_ts_108:
                w = now - rise_ts_108
                events_108.append((rise_ts_108, now, w))
                print(f"[L108-FALL]  {now_dt}  held {w*1000:.0f}ms", flush=True)
                rise_ts_108 = None
        # L109 edges
        if last_l109 == 0 and cur_109 == 1:
            rise_ts_109 = now
            print(f"[L109-RISE]  {now_dt}", flush=True)
        elif last_l109 == 1 and cur_109 == 0:
            if rise_ts_109:
                w = now - rise_ts_109
                events_109.append((rise_ts_109, now, w))
                print(f"[L109-FALL]  {now_dt}  held {w*1000:.0f}ms", flush=True)
                rise_ts_109 = None

        # heartbeat every 30 s
        if now - last_print >= 30:
            elapsed = now - start
            print(f"[HB] elapsed={elapsed:.0f}s polls={polls} errors={read_errors} "
                  f"L108_rises={len(events_108)} L109_rises={len(events_109)}",
                  flush=True)
            last_print = now

        last_l108 = cur_108
        last_l109 = cur_109
        time.sleep(POLL_MS / 1000.0)

    try: plc.close()
    except: pass

    # Final summary
    print("\n" + "="*70)
    print(f"=== MONITOR DONE — {DURATION}s, {polls} polls, {read_errors} errors ===")
    print("="*70)
    def summary(name, evs):
        if not evs:
            print(f"\n{name}: ZERO rises in {DURATION}s")
            return
        widths = [e[2] for e in evs]
        total_on = sum(widths)
        print(f"\n{name}: {len(evs)} rises")
        print(f"  total ON time:  {total_on:.2f}s")
        print(f"  avg pulse width: {sum(widths)/len(widths)*1000:.0f}ms")
        print(f"  min/max width:   {min(widths)*1000:.0f}ms / {max(widths)*1000:.0f}ms")
        print(f"  first rise:      {datetime.fromtimestamp(evs[0][0]).strftime('%H:%M:%S.%f')[:-3]}")
        print(f"  last rise:       {datetime.fromtimestamp(evs[-1][0]).strftime('%H:%M:%S.%f')[:-3]}")
        # Inter-rise gaps
        if len(evs) > 1:
            gaps = [evs[i+1][0] - evs[i][0] for i in range(len(evs)-1)]
            print(f"  inter-rise gap:  min {min(gaps):.1f}s / avg {sum(gaps)/len(gaps):.1f}s / max {max(gaps):.1f}s")

    summary("L108 (OK)", events_108)
    summary("L109 (NG)", events_109)
    print()

if __name__ == "__main__":
    main()
