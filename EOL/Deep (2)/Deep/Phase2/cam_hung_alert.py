"""cam_hung_alert.py — Inbox alert when a camera stops recording (2026-09-24).

Operator rule ("mujhe camera hung nhi chahiye koi bhi"): no camera may sit in the
hung state.  The CMS now retries a stuck camera within seconds and even asks it
to reboot itself, but most of them drop their entire TCP stack when they lock up
(measured 24-Sep: 23 of 23 hung cameras had no management port open), so the
last resort is a person power-cycling it.  This watcher makes the system ASK for
that instead of waiting for the operator to notice missing video.

Source of truth is `mes_vcov_cam_state`, the same per-camera state the Video
Coverage page shows (written by routers/video_coverage.py):
    camera_hung      ping answers, no video      → power-cycle the camera
    camera_offline   no ping at all              → switch / PoE / cable
Only lines that are IN AN ACTIVE SHIFT are alerted, so idle lines never spam.
One alert per LINE (not per camera) with a long cooldown.  Never raises.
"""
import os
import threading
import time

SUSTAIN_MIN = int(os.getenv("CAM_HUNG_SUSTAIN_MIN", "15"))
COOLDOWN_MIN = int(os.getenv("CAM_HUNG_COOLDOWN_MIN", "360"))
MAX_NAMED = int(os.getenv("CAM_HUNG_MAX_NAMED", "4"))
_POLL_SEC = int(os.getenv("CAM_HUNG_POLL_SEC", "300"))

_last_fired: dict = {}          # line_id -> epoch seconds
_THREAD = None

_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (camera_id) camera_id, ip, state, from_ts
      FROM mes_vcov_cam_state
     ORDER BY camera_id, from_ts DESC
)
SELECT pc.line_id,
       l.line_name,
       COALESCE(NULLIF(TRIM(pc.machine_name), ''), 'camera') AS machine,
       latest.camera_id,
       latest.ip,
       latest.state,
       EXTRACT(EPOCH FROM (now() - latest.from_ts)) / 60.0 AS down_min
  FROM latest
  JOIN mes_plc_configs pc
    ON NULLIF(TRIM(pc.nf2_camera_id), '') = latest.camera_id
  JOIN mes_lines l
    ON l.id = pc.line_id
 WHERE latest.state IN ('camera_hung', 'camera_offline')
   AND latest.from_ts < now() - make_interval(mins => %s)
   AND COALESCE(l.is_active, TRUE)
   AND l.current_shift_row_id IS NOT NULL
 ORDER BY pc.line_id, down_min DESC
"""


def _tick() -> None:
    from database import get_conn, dict_cursor
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT to_regclass('mes_vcov_cam_state') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return                                  # coverage table not built yet
        cur.execute(_SQL, (SUSTAIN_MIN,))
        rows = cur.fetchall() or []
        conn.rollback()                             # read-only pass

    # One physical camera often serves several machines in mes_plc_configs
    # (e.g. YRA-SS 192.168.31.34 is mapped to 3 machines), so count CAMERAS,
    # not config rows — otherwise the alert reads "3 cameras" for one camera.
    by_line: dict = {}
    for r in rows:
        seen = by_line.setdefault(r["line_id"], {})
        key = r["ip"] or r["camera_id"]
        if key not in seen:
            seen[key] = dict(r, machines=1)
        else:
            seen[key]["machines"] += 1
    by_line = {lid: list(cams.values()) for lid, cams in by_line.items()}

    now = time.time()
    for lid, cams in by_line.items():
        if now - _last_fired.get(lid, 0) < COOLDOWN_MIN * 60:
            continue
        lname = cams[0]["line_name"]
        hung = [c for c in cams if c["state"] == "camera_hung"]
        offl = [c for c in cams if c["state"] == "camera_offline"]
        def _label(c):
            extra = c.get("machines", 1) - 1
            tail = f" +{extra} more m/c" if extra > 0 else ""
            return f"{c['machine']}{tail} ({c['ip']}, {int(c['down_min'])} min)"

        named = ", ".join(_label(c) for c in cams[:MAX_NAMED])
        more = f" +{len(cams) - MAX_NAMED} more" if len(cams) > MAX_NAMED else ""
        what = []
        if hung:
            what.append(f"{len(hung)} hung (answers ping, no video — power-cycle "
                        f"the camera)")
        if offl:
            what.append(f"{len(offl)} offline (no ping — check the switch/PoE)")
        body = (f"No video from {len(cams)} camera(s) on {lname} for over "
                f"{SUSTAIN_MIN} min: {named}{more}. "
                + "; ".join(what)
                + ". Cycles on these machines have no clip until it is fixed.")
        try:
            from routers.push import send_to_line
            send_to_line(lid, f"Camera not recording — {lname}", body,
                         url="/video-coverage", tag="camera_hung")
            _last_fired[lid] = now
            print(f"[CAM-HUNG] alerted line {lid} ({lname}) — "
                  f"{len(hung)} hung, {len(offl)} offline")
        except Exception as ex:
            print("[CAM-HUNG] notify failed:", str(ex)[:120])


def _loop() -> None:
    time.sleep(45)                                  # let the app finish booting
    while True:
        try:
            _tick()
        except Exception as ex:
            print("[CAM-HUNG] tick error:", str(ex)[:150])
        time.sleep(_POLL_SEC)


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _THREAD = threading.Thread(target=_loop, daemon=True, name="cam-hung-alert")
    _THREAD.start()
    print(f"[CAM-HUNG] watcher started (sustain {SUSTAIN_MIN}m, "
          f"cooldown {COOLDOWN_MIN}m, poll {_POLL_SEC}s)")
