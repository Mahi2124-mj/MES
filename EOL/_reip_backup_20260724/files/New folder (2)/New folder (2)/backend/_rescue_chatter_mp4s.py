"""
Rescue script — re-extracts MP4s that were clobbered by chatter cycles
BEFORE the chatter-guard was activated.

Scans today's ct_log for cycles where the PREVIOUS cycle shares the same
part_code AND was significantly longer.  For each such "long-then-short"
pair, the longer cycle's MP4 is missing/wrong; we re-extract from the live
TS file (when still present).

Run from the backend directory:
  cd backend
  python _rescue_chatter_mp4s.py
"""
import os, re, glob, sys, subprocess, math
from datetime import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\EOL\EOL\Deep (2)\Deep\Phase2")
from imageio_ffmpeg import get_ffmpeg_exe

# We need the MES Postgres connection — reuse Phase2/database.py
from database import get_conn, dict_cursor   # type: ignore

FFMPEG    = get_ffmpeg_exe()
VIDEOS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos"))


def find_ts_covering(camera_id: str, cycle_start: dt, cycle_end: dt):
    """Return (ts_path, ts_start_dt) for the TS file that contains the cycle."""
    pat = os.path.join(VIDEOS_DIR, f"cam_{camera_id}_*.ts")
    best = None
    for f in glob.glob(pat):
        m = re.search(r"_(\d{13})\.ts$", os.path.basename(f))
        if not m:
            continue
        ts_start = dt.fromtimestamp(int(m.group(1)) / 1000.0)
        try:
            mtime = dt.fromtimestamp(os.path.getmtime(f))
        except OSError:
            continue
        if ts_start <= cycle_start and mtime >= cycle_end:
            # Latest qualifying file wins (handles overlapping TS windows)
            if best is None or ts_start > best[1]:
                best = (f, ts_start)
    return best


def extract_clip(ts_file: str, ts_start: dt, cycle_start: dt,
                 cycle_dur_s: float, out_path: str) -> bool:
    ss = max(0.0, (cycle_start - ts_start).total_seconds())
    dur = max(1.0, float(math.ceil(cycle_dur_s)))
    input_ss  = max(0.0, ss - 1.5)
    output_ss = max(0.0, ss - input_ss)
    cmd = [
        FFMPEG, "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",
        "-ss", f"{input_ss:.3f}",
        "-i", ts_file,
        "-ss", f"{output_ss:.3f}",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an", "-vsync", "cfr",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    return r.returncode == 0 and os.path.exists(out_path)


def main():
    # Find every long-then-short chatter pair from today
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            WITH ordered AS (
                SELECT cycle_seq, ts, ct_value, part_code,
                       LAG(ts)        OVER (ORDER BY ts) prev_ts,
                       LAG(ct_value)  OVER (ORDER BY ts) prev_ct,
                       LAG(part_code) OVER (ORDER BY ts) prev_pc,
                       LAG(cycle_seq) OVER (ORDER BY ts) prev_seq
                FROM ync_dashboard_complete_ct_log
                WHERE record_date = CURRENT_DATE
            )
            SELECT prev_seq AS long_seq, prev_ts AS long_ts,
                   prev_ct AS long_ct,
                   cycle_seq AS short_seq, ct_value AS short_ct,
                   part_code
            FROM ordered
            WHERE prev_ct IS NOT NULL
              AND prev_pc = part_code
              AND prev_ct > ct_value + 5                       -- long >> short
              AND prev_ct > 15                                 -- not just tiny noise
              AND (ts - prev_ts) < INTERVAL '20 seconds'
            ORDER BY prev_ts
        """)
        pairs = cur.fetchall()

    if not pairs:
        print("No chatter pairs found today — nothing to rescue.")
        return

    print(f"Found {len(pairs)} chatter pairs from today.\n")

    # MAIN-line PLC is 192.168.10.150 → camera bound is Panasonic Default
    camera_id = "cam_panasonic_default"

    rescued, missing_ts, failed = 0, 0, 0
    for p in pairs:
        long_seq   = p["long_seq"]
        long_ts    = p["long_ts"]
        long_ct    = float(p["long_ct"])
        part_code  = p["part_code"]

        # Cycle start = end_ts - duration
        cycle_end   = (long_ts.replace(tzinfo=None)
                       if hasattr(long_ts, "tzinfo") else long_ts)
        cycle_start = cycle_end.replace(microsecond=0) - \
                      __import__("datetime").timedelta(seconds=long_ct)
        # Find TS
        match = find_ts_covering(camera_id, cycle_start, cycle_end)
        safe_part = re.sub(r"[^A-Za-z0-9._-]", "_", part_code).strip("_")
        out_path  = os.path.join(VIDEOS_DIR, "YNC-SS", f"{safe_part}.mp4")

        if not match:
            print(f"  [MISS] seq={long_seq} pc={safe_part} ct={long_ct}s "
                  f"@ {cycle_start.strftime('%H:%M:%S')} — TS rotated out")
            missing_ts += 1
            continue

        ts_file, ts_start = match
        ok = extract_clip(ts_file, ts_start, cycle_start, long_ct, out_path)
        if ok:
            sz = os.path.getsize(out_path)
            print(f"  [OK]   seq={long_seq} pc={safe_part} ct={long_ct}s "
                  f"-> {sz//1024} KB from {os.path.basename(ts_file)}")
            rescued += 1
        else:
            print(f"  [FAIL] seq={long_seq} pc={safe_part}")
            failed += 1

    print(f"\nRescued: {rescued}   TS-missing: {missing_ts}   Failed: {failed}")


if __name__ == "__main__":
    main()
