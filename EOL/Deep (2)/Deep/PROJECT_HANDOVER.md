# EOL MES + Camera CMS — Complete Technical Handover

**Plant:** Toyota Boshoku Device India, Bawal — seat-slider lines (YNC/YSD/YHB/YCA/Y17/YMC/YRA/YJC/YFG/YWD…)
**Maintainer:** Vivek Yadav (Manufacturing & Automation Engineer)
**Server PC:** 192.168.10.185 · **DB:** PostgreSQL `energydb` @ 192.168.10.210:5432

---

## 0. Big picture

Four independent stacks on one PC, all over LAN. **Collector = the only PLC talker and the only OEE calculator.** Everything else reads Postgres.

```
 Mitsubishi Q PLC (MC4E/TCP :5002)
   L108=OK  L109=NG  D6005=status  D6048=model  D5004=part_code
        │  poll ~30 ms  (pymcprotocol Type4E)
        ▼
 COLLECTOR  collector_engine.py (8817 lines) — 1 process per line
   • debounces edges, computes CT + OEE, writes {line}_dashboard + {line}_ct_log
   • webhook POST /api/plc-edge → Camera CMS on every OK/NG part
        │  Postgres (energydb)
        ▼
 MES BACKEND  FastAPI main.py + 36 routers  :8080
        │  JSON
        ▼
 MES FRONTEND  React 19 + Vite  :5656   (Dashboard, Fullscreen TV, Admin, Maintenance, Quality…)

 CAMERA CMS (separate)  Flask :5555 + React :5575
   ffmpeg 24×7 .ts recording → cuts per-part MP4 clip on webhook
```

Launch everything: `start_everything.bat` · stop: `stop_everything.bat` · login `admin/admin123`.

---

## 1. Collector engine (the heart) — `Phase2/collector_engine.py`

Single class `CollectorEngine` (line 1986). `run()` (8227) polls at ~30 Hz and spawns 4 daemon threads: sub-machine pollers (one per upstream PLC), sub-reloader (hot-adds machines every 30 s), PY-check (0.5 s), status-table (15 s).

**Counting (`_update_counts`, 3792):** rising-edge detect on L108/L109 with a **5 s cross-type chatter-reject** (kills PLC double-pulse phantoms), **NG ladder-echo defer**, **NG-stuck auto-flip**, **L108 stuck watchdog** (180 s + 3 NG witnesses ⇒ force reconnect on half-open socket). Two count modes:
- **Bit mode:** each L108/L109 rise = 1 part.
- **Register mode (Final Inspection):** exact-mirror a D-register (`ok_shift = live value`); virtual rising edges emitted on +delta so audit/ct_log/webhook still fire; **rate-clamp** rejects physically-impossible spikes (garbage accepts ZERO, not 1); peak-tracking + `mes_shift_count_archive` so a PLC reset never loses the closing count.

**Cycle time:** `CT = now − _last_any_pulse_dt` (one anchor for both OK & NG), floored at shift-start (kills phantom ~1 h first-part CT), **break-aware** (subtracts scheduled break overlap when gap ≥ 3×ideal_ct), clock-jump guarded. Written to `{line}_ct_log` (chart) + `mes_l6_final_inspection` (audit).

**Status (`_update_status`, 5017):** decode D6005; **mask off bit 4 (remote-active, dec 16)**; **L108-truth override** (PLC says IDLE but L108 fired in last 180 s ⇒ force RUNNING); **sub-machine truth override** (any sub pulsed in 90 s ⇒ RUNNING); **45 s IDLE dwell** to suppress blips.

**OEE (`_oee`, 5408):** `avail = (plan − downtime_losses)/plan`, `perf = (run − speed_loss)/run`, `qual = ok/(ok+ng)`, `overall = a·p·q`. (2026-05-15 fix split speed-loss out of availability so perf is no longer pinned at 100 %.) Grades EXCELLENT/GOOD/AVERAGE/FAIR/POOR.

**Shift/OT (`_get_current_shift`, 3365):** OT window > production shift > GAP. On shift end **and** start, bumps `_shift_reset_epoch` ⇒ FI pulses **L110 for 5 s** to zero the PLC register (operator: "register ko START pe bhi zero karo").

**Resilience:** DB-down ⇒ boots from pickled config cache + local file-lock; failed event writes go to durable per-line `wbuf_line{N}.jsonl` and replay **exactly-once** via `mes_collector_replay_log` (rec_id PK). Cross-PC singleton via `mes_collector_locks` (10 s heartbeat, 30 s stale).

`collectors/collector_*.py` = tiny per-line bootstrap dicts (line_id, PLC IP, bits, ideal_ct…). `_run_all_collectors.bat` auto-discovers them and runs each in a never-die window; drop a new file → next boot picks it up. `provisioner.py` generates the dashboard table (150+ cols) + collector script when a line is added.

---

## 2. MES backend — `Phase2/main.py` + 36 routers (:8080)

**main.py:** registers all routers; 8 startup daemon workers (manpower alerts, kanban auto-fire, shift-report mailer @ shift-end+90 s, OEE-drop alarm, PM reminder Mon/Sat, weld test-gen, network SNMP poller, machine-master ICMP ping); 50+ idempotent boot migrations (skip if DB down). Also **`POST /api/ai/chat`** = text-to-SQL assistant (Claude Haiku, SELECT-only, 8 s statement_timeout, 3 tool-iterations, 5-min schema cache); CMS proxy endpoints (7 h token cache); Excel import/export for poka-yoke/models; `/api/audit`, `/api/health`, `/api/export/data`.

**Router map (one-liners):**

| Router | What it owns |
|---|---|
| `lines` (4649 ln) | `/realtime` (dashboard feed, surfaces collector-computed OEE + garbage-model guard + takt), `/ct-history`, `/historical` (slot recon via Hamilton largest-remainder), `/cycle-video` & `/archive-video` (proxy CMS, two-path: time-window→by-part), collector `provision`/`stop`/`restart`, `/part-search`, 3-layer Excel-import guard |
| `submachines` | per-cycle sub CT, hourly (rescaled by main_ideal/sub_ideal), semi-auto data log, sub video proxy |
| `poka_yoke` (4920 ln) | PY rules/master/model-master/assignments, events, `/live/{line}` health (auto-ack sweep), bypass episodes (60 s merge). Encoding: 0=PASS, 1=OFF/ON… (D-reg vs X-bit) |
| `quality` | Deviations (8D, PENDING_QA→…→CLOSED), 4M-change, NCR+Pareto, in-process inspection, first/last-piece, PPM = rejected/produced×1e6, control-plan PDFs |
| `breakdowns` | OPEN→RESOLVED→CLOSED state machine, split production/maintenance halves, master log, MTTR/MTBF/LTTR |
| `breakdown_mail` | escalation ladder, 30 s daemon, idempotent per (breakdown,level) |
| `capa` | 3-tier thresholds (GLOBAL/LINE/MACHINE), SINGLE_LIMIT + MONTHLY_LIMIT triggers, Pareto must-file |
| `maintenance_kpi` | MTBF/MTTR/availability vs targets, CSV export |
| `maintenance_logbook` | daily sheet per (date,shift) TBDI/MAINT/F/008 |
| `pm` / `pm_mail` | PM check-sheets (flat model, per-month fill) + Mon/Sat reminder mail |
| `weld` | live weld current/voltage + synthetic test-gen (WELD_TEST_GEN=0 to disable) |
| `manpower` | skill↔process allocation, skill-mismatch/unallocated/escalation email engine |
| `kanban` | FG parts, monthly plan, model→FG link, 3 auto-fire windows (noon, A-end, B-end) |
| `heijunka` | production leveling: distribute monthly plan across working days |
| `five_s` | 5-pillar daily audit + photo (BYTEA) |
| `pdca` | A3 tracker (PLAN→DO→CHECK→ACT), auto A3-numbering |
| `anything_wrong` | consolidated open-problems board (reads all sources, skips missing tables) |
| `store_dispatch` | material master + GRN/issue + stock (computed) + FG lots + truck loads cascade |
| `shift_calc` | forward (parts→shifts) / reverse (days→parts) OT calculator |
| `reports` | shift Excel/PDF + scheduled email |
| `operators` | badge login/logout sessions + productivity |
| `wallboard` | 65" TV feeders (multi-machine CT, KPI, histogram) |
| `network` | switch SNMP poller + ARP discovery + device/cable JSON |
| `machine_master` | Excel machine list + live IP/camera ping |
| `non_production` | NPD marking day/shift/slot |
| `machines`/`zones`/`plants`/`departments`/`users`/`config`/`status_schema` | hierarchy + PLC/status/shift/model config CRUD |
| `cms_control`/`cms_sync` | MES↔CMS video on/off + zone sync (loopback) |

**Auth (`auth.py`):** JWT HS256; **in-memory session registry** (server restart = everyone logged out), no timer expiry; per-username brute-force lockout (8 fails/5 min → 15 min); roles admin/plant_head(=admin)/production/operator/department + **per-user page-permission overrides** (none/read/full) via `mes_user_page_permissions`. `mes_admin` stores bcrypt hashes.

**DB (`database.py`):** single `DB_CONFIG`, pool 2..30, `connect_timeout=5`, fast TCP `db_reachable()` probe.

---

## 3. MES frontend — `mes-frontend/` (React 19 + Vite, :5656)

Fetch wrapper `src/api/client.jsx` (relative paths, Vite proxies `/api`→8080, `/cms-api`→5555; 401→login; mutations broadcast `ap-config-changed`). Auth in `AuthContext.jsx` (**sessionStorage = per-tab login**, `canAccess`/`canWrite`). Inline styles + `whiteTheme.js` tokens; role themes (admin blue / maint red / quality amber / prod green).

35 pages. Key: **Dashboard** (3 s realtime poll, 10 s submachines, clock-recomputed plan every 1 s), **Fullscreen** TV (anon fallback, outlier-filtered CT histogram), **ProcessGraphs** (15 s silent poll, bit-spike SVG), **Historical** (part-search + archive-video-probe→by-part fallback + breakdown log), **MaintenanceDashboard** (1 Hz ANDON tick, 60 s KPI, closure form split prod/maint), **QualityDashboard** (10 s PY zone tiles + deviation toast), **AdminPanel** (~10 k lines, hash `#section/tab`: Plants/Zones-wizard/Lines/Machines/Processes/Status/Poka-Yoke-5-tabs/Users-permission-matrix/Departments/Mail/CAPA/KPI/OEE-alarm/Manpower). `AIAssistant.jsx` → `/api/ai/chat`.

---

## 4. Camera CMS (live copy = sibling `New folder (2)/…/backend` :5555, ref copy = `Deep/Camera CMS` :5000)

Flask `api_server.py` auto-starts `RecordingManager` → one `CameraRecorder` (ffmpeg, RTSP→MPEG-TS) per bound camera, rolling `.ts` per shift, reloads bindings every 30 s. Clip cut = `ffmpeg -ss/-to -c copy` (byte-cut, cached). **`/api/plc-edge`** (unauth, localhost) receives collector webhook → background worker waits 1.5 s for .ts flush, computes offsets, cuts that part's MP4, appends `cycles.csv` (atomic seq reserve). `plc_poller.py` is a **disabled scaffold** (collector is the single PLC source). `mes_sync.py` one-way pulls zones/lines/machines from MES Postgres, preserves camera bindings. Camera creds Fernet-encrypted (`secret.key`). Frontend (React 19 + Tailwind, :5575/:5173): Zone/Line/Machine/Camera/PLC masters, camera binding, shifts, system settings, camera grid, cycle monitor, CT reports.

---

## 5. Auxiliary

`Phase1/` = SQL baseline migrations. `Phase3/` = experimental collector variant. `POKA-YOKE/` (Express+React 5000/3000) + `pokayoke_dashboard/` (Flask) = PY master tooling. `New folder/YNC-DASHBOARD-170226/` = archived Vue single-line dashboard. `sample_schema.sql` = full schema reference. `EOL_MES_User_Manual.pdf/.docx`, `MES_Data_Flow.pptx/.xlsx` = docs.

---

## 6. Security notes (fix when ready)

- `Phase2/.env` holds **live Anthropic/Google/OpenAI keys + O365 SMTP password** — verify not committed (`git ls-files`), rotate + `git rm --cached` if so (`.gitignore` does list `.env*`).
- DB pass `tbdi@123` and JWT default secret are literal fallbacks in source — set `DB_PASS` / `MES_JWT_SECRET` env in prod.
- AI chat runs LLM SQL (SELECT-guarded, 8 s timeout); `/api/plc-edge` is unauthenticated (localhost-trust); default camera admin/admin123.
