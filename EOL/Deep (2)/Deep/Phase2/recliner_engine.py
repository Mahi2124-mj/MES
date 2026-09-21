# ════════════════════════════════════════════════════════════════
# recliner_engine.py  —  Recliner-zone collector profile
# ════════════════════════════════════════════════════════════════
"""ReclinerEngine subclasses the seat-slider CollectorEngine and REUSES all of
its plumbing unchanged — singleton lock, PLC/DB connection + reconnect, shift/
break/OT calendar, status decode + dwell, OEE, throttled dashboard write, the
DB-down write-buffer, everything.  The seat-slider engine is NOT modified.

Only the COUNT differs.  A recliner machine runs N production PROCESSES on one
PLC; each process has its own OK/NG data register.  We:
  • disable the base single main OK/NG (its register-mirror/desync guards stay
    dormant because ok_data_register is blank),
  • read every process register on the same socket each poll (register-mirror:
    the shift count for a process = the live register value, exact),
  • set the machine total  self.ok_shift / self.ng_shift = SUM over processes
    → the base loop writes that total to the dashboard and plots the combined
      cycle-time graph (one machine, both processes feed it — operator's spec),
  • per-process shift counts are upserted to mes_recliner_process_counts so the
    dashboard can show the 1-card / 2-process split,
  • each process increment fires a per-process camera edge (Phase 3 hook) so the
    process's dedicated camera + the machine's external camera each cut a clip.

Config (per line, from mes_recliner_processes via the provisioner):
    cfg["recliner_processes"] = [
        {"process_no": 1, "ok_register": "D101", "ng_register": "D102"},
        {"process_no": 2, "ok_register": "D601", "ng_register": "D602"},
    ]
"""

from collector_engine import CollectorEngine
from datetime import datetime
import time


class ReclinerEngine(CollectorEngine):

    def __init__(self, cfg):
        cfg = dict(cfg)
        # Force the register path and blank the single main OK/NG — recliner
        # has no one main count; we own it via the per-process sum below.  The
        # base register block self-skips on a blank register, so its garbage /
        # desync / rate-clamp guards never run against a bogus address.
        cfg["count_mode"] = "register"
        cfg["ok_data_register"] = ""
        cfg["ng_data_register"] = ""
        self._procs = list(cfg.get("recliner_processes") or [])
        super().__init__(cfg)
        self._proc_last = {}    # {(process_no, 'ok'|'ng'): last register value}
        self._proc_cnt  = {}    # {process_no: {'ok': shift_count, 'ng': shift_count}}
        self._proc_flush_ts = 0.0

    # ── read: base status/model + our processes ─────────────────
    def _read_plc(self):
        data = super()._read_plc()          # status, model, desync — main count skipped
        if self._plc_ok and self._plc and self._procs:
            try:
                self._read_processes(data)
            except Exception as e:
                # never let a process read crash the poll loop
                self._proc_read_err(e)
        return data

    def _read_processes(self, data):
        d_ok = d_ng = 0
        for p in self._procs:
            pno = p.get("process_no")
            cnt = self._proc_cnt.setdefault(pno, {"ok": 0, "ng": 0})
            for kind in ("ok", "ng"):
                reg = (p.get(f"{kind}_register") or "").strip()
                if not reg:
                    continue
                try:
                    v = self._plc.batchread_wordunits(headdevice=reg, readsize=1)
                    if not v:
                        continue
                    cur = self._reg_count(v[0])   # unsigned + desync-garbage reject
                    if cur is None:
                        continue                  # garbage read → hold last, skip
                except Exception:
                    continue
                key  = (pno, kind)
                last = self._proc_last.get(key)
                self._proc_last[key] = cur
                if last is None:
                    cnt[kind] = cur                     # seed to live register
                elif cur > last:
                    delta = cur - last
                    cnt[kind] = cur                     # exact register-mirror
                    if kind == "ok":
                        d_ok += delta
                    else:
                        d_ng += delta
                    self._fire_process_cam(pno, kind, cur, delta)   # Phase-3 hook
                # cur < last → PLC reset / bleed: hold the last accepted count

        # Machine total = sum of every process (OK, NG).  The base loop writes
        # this to the dashboard row and drives OEE / plan-vs-actual.
        self.ok_shift = sum(c["ok"] for c in self._proc_cnt.values())
        self.ng_shift = sum(c["ng"] for c in self._proc_cnt.values())

        # Tell the base loop a combined cycle happened this poll so it logs one
        # machine cycle-time row + fires its own downstream (OEE stats).  The
        # base's own video webhook stays suppressed (see _emit_edge_webhook).
        if d_ok > 0:
            data["ok_bit"] = 1
            data["ok_delta"] = d_ok
        if d_ng > 0:
            data["ng_bit"] = 1
            data["ng_delta"] = d_ng

        self._flush_process_counts()

    # ── per-process camera edge (Phase 3) ───────────────────────
    def _fire_process_cam(self, process_no, kind, reg_value, delta):
        """POST a per-process edge to the Camera CMS so the process's dedicated
        camera AND the machine external camera each cut a clip for this event.
        Filled in Phase 3 once the CMS per-process endpoint is finalised."""
        return

    def _emit_edge_webhook(self, bit_label, edge_epoch):
        """Suppress the base's single machine-level video webhook — recliner
        clips are per-process (see _fire_process_cam), not one combined clip."""
        return

    # ── per-process shift counts → DB (for the dashboard split) ──
    def _flush_process_counts(self):
        now = time.time()
        if now - self._proc_flush_ts < 2.0:
            return
        self._proc_flush_ts = now
        if not (self._db_ok and self._db) or not self._cur_shift \
                or self._cur_shift.startswith("GAP"):
            return
        rd = getattr(self, "_cur_shift_record_date", None) or datetime.now().date()
        lid = self.cfg.get("line_id")
        try:
            cur = self._db.cursor()
            for pno, c in self._proc_cnt.items():
                cur.execute("""
                    INSERT INTO mes_recliner_process_counts
                        (line_id, process_no, record_date, shift_name,
                         ok_count, ng_count, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (line_id, process_no, record_date, shift_name)
                    DO UPDATE SET ok_count = EXCLUDED.ok_count,
                                  ng_count = EXCLUDED.ng_count,
                                  updated_at = now()
                """, (lid, pno, rd, self._cur_shift, c["ok"], c["ng"]))
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except Exception:
                pass

    def _proc_read_err(self, exc):
        last = getattr(self, "_proc_err_ts", 0)
        if time.time() - last > 30:
            self._proc_err_ts = time.time()
            print(f"[RECLINER] process read error: {exc}", flush=True)
