#!/usr/bin/env python
r"""
video_archiver.py - per-cycle clip archiver for MES line 2 (Final Inspection).

WHAT
  After each production cycle, cut a short MP4 (exactly the cycle window) from
  the live camera TS via NF2's proven /api/submachine/clip endpoint and store it
  under  D:\VideoArchive\<record_date>\<shift>\<machine>\<seq>_<part>_<OK|NG>.mp4 .
  Keeps EXACTLY 2 days (today + yesterday by shift-anchored record_date); older
  date folders are purged. Index table mes_video_archive lets the MES API/UI
  locate each clip.

ZERO-REGRESSION (by construction)
  * Standalone process. Reads MES DB + GETs NF2 (same call the live UI already
    makes on-demand). NEVER writes collector/count tables, never talks to PLC,
    never pulses L110/L108, never touches semi-auto counting logic.
  * Writes ONLY:  (a) new mp4 files under D:\VideoArchive  - which is OUTSIDE
    D:\MES_Videos, so NF2's shift-boundary cleanup + the live TS are untouched,
    and our retention never sees a .ts;  (b) the additive index table
    mes_video_archive (nothing else references it).
  * The live on-demand route /api/lines/{id}/cycle-video is NOT touched.
  * Single-instance pg advisory lock so two copies never run.

MODES
  dry [hours]  one pass, NO DB writes: cut recent cycles (default lookback 3h,
               override with [hours]) and print the index rows it WOULD write.
  once         one real pass: ensure table -> archive new cycles -> retention.
  run          forever loop (never-die): a once-pass every POLL_SEC.
  retention    purge only (delete > 2-day-old clips + index rows).
"""
import os, sys, time, traceback, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, date
import psycopg2

# ----- config (env-overridable) -------------------------------------------
DB = dict(host="192.168.10.210", database="energydb",
          user="postgres", password="tbdi@123")
NF2          = os.environ.get("CYCLE_VIDEO_BASE_URL", "http://127.0.0.1:5555")
ARCHIVE_ROOT = os.environ.get("VIDEO_ARCHIVE_ROOT", r"D:\VideoArchive")
LINE_ID      = int(os.environ.get("ARCHIVE_LINE_ID", "2"))
POLL_SEC     = int(os.environ.get("ARCHIVE_POLL_SEC", "8"))
RETAIN_DAYS  = int(os.environ.get("ARCHIVE_RETAIN_DAYS", "2"))   # today + (RETAIN_DAYS-1)
MAX_CLIP_SEC = float(os.environ.get("ARCHIVE_MAX_CLIP_SEC", "45"))  # cap idle 'cycles' (real cycles <31s)
MIN_CLIP_SEC = float(os.environ.get("ARCHIVE_MIN_CLIP_SEC", "3"))
ATTEMPT_HRS  = float(os.environ.get("ARCHIVE_ATTEMPT_HRS", "3"))  # don't chase dead TS
BATCH        = int(os.environ.get("ARCHIVE_BATCH", "200"))
SKIP_GAP     = os.environ.get("ARCHIVE_SKIP_GAP", "1") != "0"
HTTP_TIMEOUT = int(os.environ.get("ARCHIVE_HTTP_TIMEOUT", "90"))
TZ           = "+05:30"   # plant local (IST); NF2 strips tz for TS matching


def _retain_days():
    """Live retention_days from the NF2 video_config.json (admin-set in the
    Camera Master panel); falls back to the ARCHIVE_RETAIN_DAYS env default.
    Keeps Store-A (NF2) and Store-B (archive) on the SAME N."""
    try:
        import json
        with open(r"D:\EOL\EOL\New folder (2)\New folder (2)\backend\video_config.json") as f:
            return max(1, int(json.load(f).get("retention_days", RETAIN_DAYS)))
    except Exception:
        return RETAIN_DAYS


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def _safe(s, n=48):
    return "".join(ch if (ch.isalnum() or ch in "-._") else "_"
                   for ch in str(s if s not in (None, "") else "NA"))[:n]


def _iso(dt):
    """ISO8601 with IST offset; handles naive (main) + aware (sub) datetimes."""
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + TZ


def discover_machines(cur):
    """line-2 machines -> [{role, plc_id, name, camera, seq}] (main first)."""
    cur.execute("""select id, machine_name, nf2_camera_id, parent_plc_id,
                          machine_seq
                   from mes_plc_configs
                   where line_id=%s and coalesce(nf2_camera_id,'')<>''
                   order by coalesce(parent_plc_id,0), coalesce(machine_seq,9999), id""",
                (LINE_ID,))
    out = []
    for mid, name, cam, parent, seq in cur.fetchall():
        out.append(dict(role=("main" if parent is None else "sub"),
                        plc_id=mid, name=name, camera=cam.strip(),
                        seq=(0 if parent is None else (seq or 0))))
    return out


def cut_clip(camera, start_dt, end_dt, out_path):
    """GET NF2 clip for [start,end] -> out_path. Returns (status, bytes)."""
    # clamp window to <= MAX_CLIP_SEC and >= MIN_CLIP_SEC
    span = (end_dt - start_dt).total_seconds()
    if span > MAX_CLIP_SEC:
        start_dt = end_dt - timedelta(seconds=MAX_CLIP_SEC)
    elif span < MIN_CLIP_SEC:
        start_dt = end_dt - timedelta(seconds=MIN_CLIP_SEC)
    url = f"{NF2}/api/submachine/clip?" + urllib.parse.urlencode(
        {"camera_id": camera, "ts_start": _iso(start_dt), "ts_end": _iso(end_dt)})
    tmp = out_path + ".part"
    nb = 0
    try:
        with urllib.request.urlopen(urllib.request.Request(url),
                                    timeout=HTTP_TIMEOUT) as resp, open(tmp, "wb") as f:
            status = resp.getcode()
            while True:
                b = resp.read(65536)
                if not b:
                    break
                f.write(b); nb += len(b)
    except urllib.error.HTTPError as he:
        try: os.path.exists(tmp) and os.remove(tmp)
        except Exception: pass
        return (he.code, 0)            # 416 = TS gone, 503 = recorder gap
    except Exception as ex:
        try: os.path.exists(tmp) and os.remove(tmp)
        except Exception: pass
        return (f"ERR:{type(ex).__name__}", 0)
    if nb < 2000:                       # empty/stub clip = no usable TS
        try: os.remove(tmp)
        except Exception: pass
        return (status, nb)
    os.replace(tmp, out_path)           # atomic publish
    return (status, nb)


def _rel(path):
    return os.path.relpath(path, ARCHIVE_ROOT)


def fetch_main_cycles(cur, after_id, lookback_hrs):
    cur.execute(f"""select id, ts, record_date, shift_name, ct_value, cycle_seq,
                           part_code, is_ng
                    from ync_dashboard_complete_ct_log
                    where id > %s and ct_value is not null
                      and ts >= now() - interval '{lookback_hrs} hours'
                      {"and shift_name <> 'GAP'" if SKIP_GAP else ""}
                    order by id limit %s""", (after_id, BATCH))
    rows = []
    for rid, ts, rdate, shift, ctv, seq, part, isng in cur.fetchall():
        start = ts - timedelta(seconds=float(ctv))
        rows.append(dict(src="ync_dashboard_complete_ct_log", sid=rid,
                         rdate=rdate, shift=shift, seq=seq, part=part, isng=isng,
                         start=start, end=ts, machine="Final Inspection",
                         mseq=0, camera=None))   # camera filled from machine map
    return rows


def fetch_sub_cycles(cur, after_id, lookback_hrs, sub_ids):
    if not sub_ids:
        return []
    cur.execute(f"""select id, sub_plc_id, record_date, shift_name, cycle_seq,
                           ts_start, ts_end, part_code, is_ng
                    from mes_submachine_ct_log
                    where id > %s and sub_plc_id = any(%s)
                      and ts_end >= now() - interval '{lookback_hrs} hours'
                      {"and shift_name <> 'GAP'" if SKIP_GAP else ""}
                    order by id limit %s""", (after_id, list(sub_ids), BATCH))
    rows = []
    for rid, spid, rdate, shift, seq, tss, tse, part, isng in cur.fetchall():
        rows.append(dict(src="mes_submachine_ct_log", sid=rid, sub_plc_id=spid,
                         rdate=rdate, shift=shift, seq=seq, part=part, isng=isng,
                         start=tss, end=tse))
    return rows


def out_path_for(rdate, shift, mseq, machine, seq, part, isng):
    tag = "NG" if isng else "OK"
    mdir = f"{mseq}_{_safe(machine, 40)}"     # mseq prefix: disambiguate same-named machines
    d = os.path.join(ARCHIVE_ROOT, str(rdate), _safe(shift, 8), mdir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{seq}_{_safe(part)}_{tag}.mp4")


# ----- table / watermark / retention --------------------------------------
def ensure_table(cur):
    cur.execute("""
        create table if not exists mes_video_archive (
            id           bigserial primary key,
            line_id      integer not null,
            machine_name varchar(120),
            machine_seq  integer,
            camera_id    varchar(120),
            record_date  date not null,
            shift_name   varchar(16),
            cycle_seq    integer,
            part_code    varchar(120),
            is_ng        boolean,
            source_table varchar(48) not null,
            source_id    bigint not null,
            clip_path    text,
            bytes        bigint,
            status       varchar(16),
            created_at   timestamp default now(),
            unique (source_table, source_id)
        )""")
    cur.execute("""create index if not exists ix_mva_lookup
                   on mes_video_archive(record_date, shift_name, machine_name, cycle_seq)""")


def watermark(cur, src):
    """Resume point: max archived source_id, else newest id older than the
    attempt window (so a cold start does not hammer 416 on dead TS)."""
    cur.execute("select coalesce(max(source_id),0) from mes_video_archive "
                "where source_table=%s", (src,))
    wm = cur.fetchone()[0]
    if wm:
        return wm
    tscol = "ts_end" if src == "mes_submachine_ct_log" else "ts"
    cur.execute(f"select coalesce(max(id),0) from {src} "
                f"where {tscol} < now() - interval '{ATTEMPT_HRS} hours'")
    return cur.fetchone()[0]


def index_insert(cur, r, clip_rel, status, nbytes):
    cur.execute("""insert into mes_video_archive
        (line_id, machine_name, machine_seq, camera_id, record_date, shift_name,
         cycle_seq, part_code, is_ng, source_table, source_id, clip_path, bytes, status)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (source_table, source_id) do nothing""",
        (LINE_ID, r["machine"], r.get("mseq"), r["camera"], r["rdate"], r["shift"],
         r["seq"], r["part"], r["isng"], r["src"], r["sid"], clip_rel, nbytes, status))


def run_retention(cur=None):
    """Delete date folders + index rows older than today-(RETAIN_DAYS-1)."""
    keep_from = date.today() - timedelta(days=_retain_days() - 1)
    removed = []
    if os.path.isdir(ARCHIVE_ROOT):
        for nm in os.listdir(ARCHIVE_ROOT):
            p = os.path.join(ARCHIVE_ROOT, nm)
            if not os.path.isdir(p):
                continue
            try:
                d = datetime.strptime(nm, "%Y-%m-%d").date()
            except ValueError:
                continue          # skip _smoketest / _camtest / non-date dirs
            if d < keep_from:
                # NG-aware: keep *_NG.mp4 PERMANENTLY; delete only OK clips,
                # then prune now-empty subdirs but never an NG file's folder.
                ng_kept = 0
                for wroot, _wd, wfiles in os.walk(p, topdown=False):
                    for wf in wfiles:
                        if wf.lower().endswith("_ng.mp4"):
                            ng_kept += 1
                            continue
                        try: os.remove(os.path.join(wroot, wf))
                        except OSError: pass
                    try:
                        if not os.listdir(wroot):
                            os.rmdir(wroot)
                    except OSError:
                        pass
                if ng_kept == 0:
                    try: os.rmdir(p)            # date folder fully drained
                    except OSError: pass
                    removed.append(nm)
                else:
                    removed.append(f"{nm}(kept {ng_kept} NG)")
    if cur is not None:
        # keep NG index rows forever; only OK rows expire
        cur.execute("delete from mes_video_archive "
                    "where record_date < %s and coalesce(is_ng,false) = false", (keep_from,))
    if removed:
        log(f"[retention] removed date folders: {removed}  (keep >= {keep_from})")
    return removed


# ----- one pass ------------------------------------------------------------
def one_pass(dry=False, lookback_hrs=None):
    lookback = lookback_hrs if lookback_hrs is not None else ATTEMPT_HRS
    c = psycopg2.connect(**DB); c.autocommit = False
    cur = c.cursor()
    if not dry:
        # single-instance guard
        cur.execute("select pg_try_advisory_lock(hashtext('mes_video_archiver_line2'))")
        if not cur.fetchone()[0]:
            log("another archiver holds the lock - exiting this pass")
            c.close(); return (0, 0, 0)
        ensure_table(cur); c.commit()

    machines = discover_machines(cur)
    cam_main = next((m["camera"] for m in machines if m["role"] == "main"), None)
    sub_map  = {m["plc_id"]: m for m in machines if m["role"] == "sub"}

    wm_main = (watermark(cur, "ync_dashboard_complete_ct_log")
               if not dry else _cold_wm(cur, "ync_dashboard_complete_ct_log", "ts", lookback))
    wm_sub  = (watermark(cur, "mes_submachine_ct_log")
               if not dry else _cold_wm(cur, "mes_submachine_ct_log", "ts_end", lookback))

    rows = fetch_main_cycles(cur, wm_main, lookback)
    for r in rows:
        r["camera"] = cam_main
    subs = fetch_sub_cycles(cur, wm_sub, lookback, list(sub_map.keys()))
    for r in subs:
        m = sub_map.get(r["sub_plc_id"], {})
        r["camera"]  = m.get("camera")
        r["machine"] = m.get("name", f"sub{r['sub_plc_id']}")
        r["mseq"]    = m.get("seq", 0)
    allrows = rows + subs
    allrows.sort(key=lambda r: r["end"].replace(tzinfo=None))  # IST wall-clock, mixed naive/aware

    n_ok = n_no = n_err = 0
    for r in allrows:
        if not r.get("camera"):
            continue
        op = out_path_for(r["rdate"], r["shift"], r.get("mseq", 0), r["machine"],
                          r["seq"], r["part"], r["isng"])
        tag = "NG" if r["isng"] else "OK"
        if dry:
            span = (r["end"] - r["start"]).total_seconds()
            log(f"[DRY] {r['machine'][:24]:24} {r['shift']} seq{r['seq']} {tag} "
                f"{span:5.1f}s  -> {_rel(op)}")
            n_ok += 1
            continue
        if os.path.exists(op):                  # already archived on disk
            n_ok += 1
            index_insert(cur, r, _rel(op), "ok", os.path.getsize(op)); continue
        status, nb = cut_clip(r["camera"], r["start"], r["end"], op)
        if isinstance(status, int) and nb >= 2000:
            n_ok += 1
            index_insert(cur, r, _rel(op), "ok", nb)
        else:
            n_no += (1 if status in (416, 503) or nb < 2000 else 0)
            n_err += (1 if not (status in (416, 503) or nb < 2000) else 0)
            index_insert(cur, r, None, ("no_ts" if (status in (416, 503) or nb < 2000)
                                        else "err"), 0)
        if not dry:
            c.commit()

    if not dry:
        run_retention(cur); c.commit()
        cur.execute("select pg_advisory_unlock(hashtext('mes_video_archiver_line2'))")
        c.commit()
    c.close()
    log(f"pass done: ok={n_ok} no_ts={n_no} err={n_err}  "
        f"(main_wm>{wm_main} sub_wm>{wm_sub} lookback={lookback}h){' [DRY]' if dry else ''}")
    return (n_ok, n_no, n_err)


def _cold_wm(cur, src, tscol, lookback_hrs):
    """dry-mode watermark: start just before the lookback window."""
    cur.execute(f"select coalesce(max(id),0) from {src} "
                f"where {tscol} < now() - interval '{lookback_hrs} hours'")
    return cur.fetchone()[0]


# ----- main ----------------------------------------------------------------
def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()
    log(f"video_archiver mode={mode} root={ARCHIVE_ROOT} nf2={NF2} "
        f"retain={RETAIN_DAYS}d cap={MAX_CLIP_SEC}s skip_gap={SKIP_GAP}")
    if mode == "dry":
        hrs = float(sys.argv[2]) if len(sys.argv) > 2 else ATTEMPT_HRS
        one_pass(dry=True, lookback_hrs=hrs)
    elif mode == "once":
        one_pass(dry=False)
    elif mode == "retention":
        c = psycopg2.connect(**DB); cur = c.cursor()
        run_retention(cur); c.commit(); c.close()
    elif mode == "run":
        log("never-die loop start")
        while True:
            try:
                one_pass(dry=False)
            except Exception:
                log("PASS ERROR:\n" + traceback.format_exc())
            time.sleep(POLL_SEC)
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
