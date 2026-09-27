# EOL / MES — System Architecture

End-of-Line **Manufacturing Execution System** for the plant: it reads every machine's
PLC in real time, stores production/quality data, records a video clip for each cycle,
runs poka-yoke and maintenance workflows, and shows it all on dashboards, an Android
app, and shop-floor panels.

> **Where the code lives:** the application tree is under `EOL/Deep (2)/Deep/`.
> Most paths below are relative to that folder. The live database is local
> (`127.0.0.1`); real secrets are **not** in git (copy `Phase2/.env.example` → `.env`).

---

## 1. Big picture — how data flows

```
   Machines / PLCs (16+ lines)
        │  (SLMP / Modbus, bit- or register-mode)
        ▼
   Collectors  (Phase2/collectors/*.py — 32 pollers)
        │  writes cycles, OK/NG, status
        ▼
   PostgreSQL  ── energydb  (production, source of truth)
        │        └ maintenance_db (andon, faults, poka-yoke)
        ▼
   MES API  (Phase2, FastAPI/uvicorn  :8080)  ──────► Camera CMS (:5555)
        │   REST for dashboards, reports, quality,          records cameras,
        │   maintenance, poka-yoke, notifications           cuts per-cycle clips
        ▼
   MES Frontend (React/Vite, served by serve_prod.py :5656)
        │   also proxies /api → :8080 and /cms-api → :5555
        ├──► Web browsers / shop-floor TV panels
        └──► Android app (Capacitor APK)

   Embedded:  BinVision bin-filling app (:8090) shown as a MES tab
              via a reverse proxy (Phase2/routers/binfilling.py).
   Watching:  background agents (PM audit / guardian / live agent).
```

---

## 2. Services & ports

| Service | Port | Tech | Role |
|---|---|---|---|
| **MES API** | 8080 | FastAPI + uvicorn (`Phase2/main.py`, multi-worker) | Core backend: all business REST APIs |
| **MES Frontend** | 5656 | `mes-frontend/serve_prod.py` serving `dist/` | Serves the built React UI; proxies `/api`→8080, `/cms-api`→5555 |
| **MES Frontend (aux)** | 5700 | second `serve_prod.py` | Maintenance / Attendance / Robot views |
| **Camera CMS API** | 5555 | Flask + `plc_edge` | Camera recording + per-cycle clip extraction |
| **CMS Frontend** | 5575 | Vite | CMS's own UI |
| **BinVision** | 8090 | separate app, own SQLite + vision pipeline | Bin-filling counter; embedded as MES "Bin Filling" tab |
| **PostgreSQL** | 5432 | Postgres | `energydb` + `maintenance_db` |

Boot order comes from `eol.service` → `eol_boot.sh` → `start_everything.sh`.

---

## 3. Component map (folders under `Deep/`)

| Folder | What it is |
|---|---|
| `Phase2/` | **The MES backend.** `main.py` (FastAPI app), `database.py` (Postgres pool), `routers/` (API), `collectors/` (PLC pollers), plus restart/util scripts |
| `mes-frontend/` | **The MES web UI** — React/Vite, pages in `src/pages/*.jsx`, built to `dist/`, served by `serve_prod.py`; Android APK built with Capacitor |
| `Camera CMS/` | Video recording + clip service (`:5555`); records RTSP cameras, extracts clips on demand and for the archive |
| `New folder/` , `New folder (2)/` | Older YNC Seat-Slider camera/video backend (`api_server.py`, `plc_monitor.py`) — legacy video store |
| `POKA-YOKE/` , `pokayoke_dashboard/` | Poka-yoke dashboard (Node) |
| `guardian/` | Self-healing service for collectors/CMS — **OFF by operator rule** unless explicitly enabled |
| `pmagent/` | PM audit agent (`pm_agent.py`) — hourly, **read-only** platform + video audit (currently ON) |
| `pmaudit/` | Feature / chaos audit tooling (`feature_audit.py`, `chaos.py`) |
| `liveagent/` | Live plant-health agent + self-heal (`live_agent.py`, `selfheal.py`, `invariants.py`) — **OFF by operator rule** |
| `landing/` | Landing page |
| `loadtest/` | Load-testing scripts |
| `logs/` | Runtime logs (hourly logrotate) |
| `Phase1/` , `Phase3/` | Earlier phases / experiments |
| `../../db/` (repo root) | DB structure clone + tooling added for this repo (see §6) |

---

## 4. Databases

- **`energydb`** (local, source of truth): all MES data.
  - Per-line cycle logs (`*_ct_log`, `mes_submachine_*_log`, quality logs) — large, transactional.
  - Config / master tables (`mes_lines`, `mes_processes`, `mes_model_mappings`,
    `mes_hourly_slots`, users, leaders, zone/page permissions, fault config…) — small.
  - Stored functions (e.g. `line_current_model()`).
- **`maintenance_db`**: physical `andon_history`, poka-yoke sensor faults, fault config,
  breakdown slips. The MES API reads it cross-DB.
- Connection defaults are in `Phase2/database.py` (host `127.0.0.1`, db `energydb`, user `postgres`).
- **Backups:** `db_backup.sh` writes daily full custom-format dumps to the data disk.
  `db/energydb_clone.sql` (via `db/clone_db.sh`) is a small **structure + functions + config-data**
  clone kept in git for quick recreation (large log tables: structure only).

---

## 5. API surface (`Phase2/routers/`, grouped)

- **Production / live dashboards:** `lines`, `submachines`, `wallboard`, `panel`, `recliner`
- **Reports:** `reports`, `peff`, `hourly_sync`, `shift_compile`, `shift_calc`, `non_production`
- **Quality / NG:** `quality`, `sa_fi_quality`, `anything_wrong`
- **Poka-yoke:** `poka_yoke`, `py_config`, `py_stations`, `py_bypass`
- **Video:** `clip_archive`, `clip_browser`, `clip_prewarm`, `clip_priority`, `video_coverage`, `cms_control`, `cms_sync`
- **Maintenance:** `faults`, `maintenance_kpi`, `maintenance_logbook`, `prod_breakdown_slips`, `breakdowns`, `breakdown_mail`, `andon`, `pm`, `pm_status`, `pm_mail`
- **Org / admin:** `users`, `operators`, `leaders`, `hierarchy`, `zones`, `plants`, `departments`, `machines`, `machine_master`, `device_registry`, `config`
- **Notifications:** `push`, `escalation`
- **Improvement boards:** `kanban`, `heijunka`, `pdca`, `capa`, `five_s`, `store_dispatch`
- **Other:** `sixsigma`, `weld`, `manpower`, `timer_config`, `network`, `logs`, `binfilling` (→ BinVision)

---

## 6. Operating the system

| Task | Command |
|---|---|
| Start everything | `bash start_everything.sh` |
| Stop everything | `bash stop_everything.sh` |
| Restart only MES API (:8080) | `python3 Phase2/restart_api.py` |
| Restart CMS (re-probe NVENC) | `python3 Phase2/restart_cms.py` |
| Daily DB backup (full data) | `bash db_backup.sh` |
| Refresh DB clone + push repo | `bash dbpush.sh "message"` (repo root) |

**Collectors:** 32 per-line pollers (`Phase2/collectors/`) driven by `collector_engine.py`;
they read PLCs in bit-mode or register-mode and write cycles to `energydb`.

---

## 7. Notes

- **Agents:** live agent and guardian are OFF by operator rule; the PM audit agent is ON and read-only.
- **Secrets:** the real `Phase2/.env` stays local (GitHub blocks the live API keys). Use `Phase2/.env.example`.
- **Video is heavy:** raw `.ts` footage and clips are **not** in git; only code + a small DB clone are versioned.
