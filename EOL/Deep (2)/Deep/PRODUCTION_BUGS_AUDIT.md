# EOL MES — Production-Risk Bug Audit (2026-07-05)

Senior-engineer audit of the whole stack (collector, FastAPI backend, React frontend, infra/ops).
Ordered by severity. **[VERIFIED]** = confirmed live by direct test this session.

---

## 🔴 SHOWSTOPPERS — fix before anything else (security)

### S1. `/static/.env` serves ALL secrets unauthenticated **[VERIFIED — HTTP 200]**
`main.py:83` — `app.mount("/static", StaticFiles(directory=os.path.dirname(__file__)))` mounts the **backend source dir**. Anyone on the LAN can `GET`:
- `/static/.env` → live `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, O365 `SMTP_PASS`
- `/static/database.py` → DB password `tbdi@123`
- `/static/auth.py` → JWT default secret
- `/static/collector_engine.py`, `main.py`, … → full source
**Fix:** mount a dedicated `static/` subfolder with ONLY public assets (never `__file__` dir). Then **rotate every exposed secret** (all 3 API keys, SMTP password, DB password, JWT secret).

### S2. Every user's password stored + returned in CLEARTEXT **[VERIFIED]**
`routers/users.py:72, 97, 113, 130` — `mes_admin.password_plain` holds the raw password; `GET /api/users/` returns it in JSON. Combined with S1 (DB pass leak) or any admin bug → every user's real password exfiltrated; password-reuse makes it plant-wide.
**Fix:** drop the `password_plain` column + all reads/writes. bcrypt hash already exists for login. Resets → random temp password + force-change.

### S3. Insecure default JWT secret, ~10-year expiry
`auth.py:31,38` — falls back to a hardcoded public secret if `MES_JWT_SECRET` unset; tokens valid ~10 years. With S1 leaking a real token, that's a decade-valid admin credential.
**Fix:** fail startup if `MES_JWT_SECRET` unset/default; strong random secret; real expiry (e.g. 12h).

### S4. AI chat `/api/ai/chat` SELECT-guard bypassable → writes / RCE
`main.py:1366-1374` — only checks `sql.upper().startswith("SELECT")` on a **read-write superuser** connection. psycopg2 executes multi-statement: `SELECT 1; UPDATE mes_admin …` passes. `SELECT …; COPY(…) TO PROGRAM '…'` = RCE on the DB host (role is superuser `postgres`).
**Fix:** dedicated **read-only DB role** + `SET TRANSACTION READ ONLY`; reject any `;`.

### S5. SQL injection — Excel import `prefix` (first-order) + `db_table_name` (second-order)
`routers/lines.py:1036/1072` builds `{prefix}_plan` from request `Hour` (only `:`→`_`) spliced raw into `UPDATE {table} SET …`. `db_table_name` stored unvalidated (`create_line`/`update_line`) then interpolated into 40+ queries across lines/poka_yoke/wallboard/reports/kanban/quality. Admin-gated, but a crafted value = persistent injection re-run on every poll.
**Fix:** validate at write boundary — `re.fullmatch(r"hour_\d{1,2}_\d{2}_\d{1,2}_\d{2}(_ot)?", prefix)` and `r"[a-z_][a-z0-9_]{0,62}"` for table names; `psycopg2.sql.Identifier` at read sites.

---

## 🟠 DATA CORRECTNESS — wrong numbers on the dashboard

### D1. Phantom counts: register-mirror NOT gated on RUNNING / GAP / break
`collector_engine.py` — main FI mirror sets `ok_shift = live register` regardless of status; only `_update_counts` (bit path) uses `_should_record_pulse()`. **Sub-machine register path (7450-7607) has NO gate at all** (only `_delta>200` filter) → idle/noise/GAP increments written as real parts. **This is the YRA "50 phantom" root on the MES side.** True root is the PLC emitting the phantom signal.
**Fix:** gate register count-climb on `status==RUNNING and not GAP and not break` (rebaseline, don't dump). Ideally per-line opt-in flag so the 11 healthy lines are untouched. **Real fix = PLC ladder/wiring** (D101 only on genuine part-complete) + reduce shared-socket bleed.

### D2. Register count carries previous shift forward if L110 reset fails
`collector_engine.py:5835` (`_reset_counts` no-ops in register mode) + L110 "give up after 30 fails". Correctness depends 100% on the physical register being zeroed; a missed L110 pulse → new shift inherits old count, no software fallback (documented incident: 12-Jun A=3596). **This is the "50 held / won't drop to 0" behaviour.**
**Fix:** software backstop on confirmed shift boundary — if register hasn't dropped and no L110 ack, force re-seed to 0; alert loudly when L110 gives up.

### D3. `_flush_ct_log` bypasses the durable write-buffer → loses rows on a DB blink
`collector_engine.py:6204-6224` clears `_ct_pending_log` BEFORE a raw `executemany`; on any DB error it rolls back and the rows are **permanently lost** (never reach the exactly-once replay ledger). A 2s DB hiccup silently drops a batch of per-part cycle rows.
**Fix:** route through `_buffered_exec`; clear pending only after confirmed success/enqueue.

### D4. Status decode paints junk as RUNNING/BREAKDOWN
`collector_engine.py:5053-5096` — `raw & 0x0F`; raw=48 (nibble 0 + flags) forced RUNNING, raw=50 → BREAKDOWN with no plausibility/debounce. Socket-contention junk becomes a real status, corrupting availability/loss. (Seen live on YRA: raw=48/50.)
**Fix:** debounce ≥2 polls before committing a status change (like the model vote); require L108-edge corroboration for the flag-set case.

### D5. part_code control-char garbage leaks to DB via the sub path
`collector_engine.py:6194-6202` appends any non-zero byte incl. 0x01-0x1F; sub-worker (7480/7560) writes `_cur_part_code` raw into `mes_submachine_ct_log.part_code` (main path has a guard, sub path doesn't). (Seen live: part_code `\x16` = model 22 bleeding in.)
**Fix:** sanitize once in `_read_part_code` — keep only `0x20..0x7E`.

### D6. Frontend "Live" badge lies on frozen data
`Fullscreen.jsx:964-981/3312`, `Dashboard.jsx:463-512` — badge/KPIs set from any HTTP 200; never checks row timestamp or `collector_status`. A dead collector serving a stale row shows green "Live" forever (**exactly today's 2-hour YRA freeze, invisible on the TV**). Clock-based `plan` keeps climbing while `actual` is frozen → looks like underperformance, not a dead feed.
**Fix:** derive staleness client-side (`collector_status !== 'running'` or `now - last_write > 5min`) → show "STALE / Collector down" banner. (Backend `_collector_watchdog.py` added this session catches it server-side.)

---

## 🟠 AVAILABILITY / OPS — why things silently die

### O1. Collector "alive while dead": heartbeat in a daemon thread + `collector_pid` never written
`collector_engine.py:484-488,549-582` heartbeats `mes_collector_locks` every 10s regardless of main-loop progress → a hung main loop keeps the lock "fresh", status "running", TV "Live". Also the engine **never writes `collector_pid`**, so `lines.py:1152-1170`'s `is_process_alive(NULL)` flips healthy collectors to "stopped". **This is today's YRA class.**
**Fix:** gate heartbeat on `now - last_progress < 60s` (main loop stamps progress); drive status from lock heartbeat freshness, not the broken PID check; write `collector_pid=os.getpid()`.

### O2. DB connection exhaustion → "too many clients" locks EVERYONE out **[VERIFIED this session]**
`collector_engine.py:49-50,325-345,557` — collectors use raw `psycopg2.connect` (no pool): a fresh connection **per sub-machine write** + a new connection every 10s per heartbeat × ~12 collectors → 479 TIME_WAIT observed, energydb hit 94-100/100. App connects as **superuser `postgres`**, so `superuser_reserved_connections` gives ZERO admin protection → even recovery `psql` is refused.
**Fix:** reuse `self._db` for collector writes (kill connect-per-write); piggyback heartbeat on it; create a **non-superuser** app/collector role; raise `max_connections` or add pgbouncer.

### O3. `_clear_stale_lock.py` has NO `connect_timeout` → hangs the collector restart
`collectors/_clear_stale_lock.py:50-53` — runs before every collector launch (`_run_one_collector.bat:42`); with a slow/saturated DB it blocks indefinitely, so the never-die wrapper is stuck **before python even starts** — silent no-restart. (**Directly caused the wrapper-stuck I hit restarting YRA today.**) The `.bat` lock-clears correctly use `connect_timeout=5`; this one was missed.
**Fix:** add `connect_timeout=5, options='-c lock_timeout=3000 -c statement_timeout=3000'`.

### O4. Unbounded collector logs on the app disk (691 MB seen) → freeze + disk-full cascade
`_run_one_collector.bat:45` appends via `>>` with no rotation; `logs/collector_live.log` = 691 MB, per-line logs 40-56 MB, ~1 GB total on **D:** (same disk as video + the collector's DB-down JSON write-buffer). A huge append handle can stall the writer (**this is the "corrupt 15 MB log froze the collector" class — the YRA log I rotated today was 15.6 MB**).
**Fix:** rotate via Python `RotatingFileHandler` (10 MB × 5) or nightly truncation; move logs off D:.

### O5. Video recording / archive / logs all on D:, no free-space guard
`VideoArchiver/video_archiver.py`, CMS recorder — no `shutil.disk_usage` check before writing. D: full → recording fails, archiver fails, AND the collector's `wbuf_*.jsonl` durability queue can't flush = cascading silent data loss. External-HDD unplug silently falls back to local disk with only a console print (`plc_monitor.py:35-67`).
**Fix:** free-space precondition + alert below threshold; loud DB/email alarm on HDD-fallback.

### O6. MES-API and CMS-API wrappers have NO restart loop
`_run_mes_api.bat`, `_run_cms_api.bat` run the server then `pause`. Collectors/archiver auto-restart; the two API servers don't → a single uvicorn/Flask crash = permanent dashboard/counting outage until a human reruns `start_everything.bat`.
**Fix:** give both wrappers the never-die `:loop … goto loop` structure + STOP.flag check.

### O7. Frontend: no ErrorBoundary anywhere → white-screen on any render throw
`App.jsx` — zero error boundaries across ~35 pages. One bad null-access/chart-math throw white-screens the whole SPA until someone physically reloads the TV.
**Fix:** wrap routes in an ErrorBoundary with a fallback + `setTimeout(reload, 15s)` self-heal.

---

## 🟡 MEDIUM (fix in the next pass)

- **Segregation of duties:** operators can hit approval/close/dispatch/master-data endpoints gated only by `get_current_user` — `quality.py` approve/reject/close deviation (282/304/353), 4M/NCR close, `store_dispatch.py` dispatch_load (873), poka-yoke master CRUD + bulk imports (`main.py:1452/1520/1562`), `kanban.py` upsert_manual_log. **Add role checks.**
- **Unauthenticated endpoints:** `/api/ping` (`main.py:1069`, internal port-scanner/SSRF), `poka_yoke.py:1459 /sensor-sweep/update` (unauth write + disk churn), ~15 `get_current_user_optional` endpoints serving prod data + email config to anonymous LAN callers. **Add auth / subnet restriction.**
- **Per-request DDL** (`_ensure_*`) on hot/anonymous GETs (esp. `lines.py:2425` not cached) → ACCESS EXCLUSIVE locks racing the collector + pool pressure. **Move to startup migrations.**
- **N+1 on anonymous wallboard** (`wallboard.py:262-284`, ~96 serial round-trips/poll) — pool pressure. **Collapse to GROUP BY.**
- **Blocking work in sync endpoints:** `reports.py:509` (matplotlib+20s SMTP), `network.py:454 /discover` (254 blocking pings, no debounce). **Background tasks + in-progress lock.**
- **Racy doc-number gen** (`quality.py:62-73` COUNT+1 → UNIQUE collide 500). **Sequence / retry.**
- **Non-idempotent imports** (poka-yoke/models/Excel — re-run duplicates; no UNIQUE(record_date,shift_name)). **ON CONFLICT + constraints.**
- **Unbounded queries / no LIMIT cap** (ct-history, historical, ng-list, breakdowns `limit=99999999`). **Cap limits + date spans.**
- **`getattr(user,'username')` on a dict** (`lines.py:3348,4147`) always → "operator" → wrong authorship.
- **`_active_sessions` in-memory** — correct only at `workers=1`; unbounded growth (slow leak). **Shared store before scaling.**
- **Collector threading:** unbounded `threading.Timer` per OK edge (H2), unguarded `self._plc` swap on reconnect (H4), shared-socket 2s stall on the 30 ms loop (H3), cursor leak in `_ensure_db_connection` (2398), outer `except Exception` masks a persistent logic error as live-but-not-counting.
- **Frontend:** out-of-order poll responses overwrite fresh data (no AbortController); JWT in `<video>` `?token=` URLs (logs/history leak); `AuthContext` clears session on any non-OK (incl 5xx) not just 401.

---

## 🟢 LOW / hygiene
- DB superuser password `tbdi@123` hardcoded in 16 git-tracked files incl. `USER_MANUAL.md` and `.bat`s (rotating = 16 edits). Drive from `DB_PASS` env, delete literals. (`.env` itself is NOT git-tracked — good.)
- `kill-all.ps1` kills ALL `python.exe` system-wide (not just MES venv) — filter by path.
- STOP.flag / relaunch race can double-launch a collector on fast stop→start.
- `mes_collector_locks` steal check + insert not atomic (30s race). Make it one conditional UPSERT.
- Watchdog/FI-monitor/archiver windows not in `stop_everything.bat` kill list → orphan across restarts.
- Broad `except: pass` hiding data errors; raw DB error text leaked to clients (`lines.py:800`).
- No last-admin protection in `users.py` delete/demote → can lock everyone out.

---

## Fix order (highest leverage first)
1. **S1** stop serving source at `/static` + **rotate all secrets** (live leak, verified).
2. **S2** drop `password_plain`; **S3** mandatory JWT secret.
3. **O3** one-line `connect_timeout` (trivial, stops restart hangs); **O2** non-superuser role + reuse connections (the lockout outage).
4. **O1** progress-gated heartbeat + write `collector_pid` (the "alive while dead" class) — server watchdog already added.
5. **S4/S5** identifier + AI-SQL validation (closes injection classes at the source).
6. **D1/D2** phantom-count gating + L110 reset backstop (per-line flag); **D6/O7** frontend staleness banner + ErrorBoundary.
7. **O4/O5** log rotation + disk free-space guard; **O6** never-die on API wrappers.
