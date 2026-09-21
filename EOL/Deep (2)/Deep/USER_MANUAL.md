# EOL MES + Camera CMS — User Manual

**Plant**: Toyota Boshoku Device India, Bawal — YNC SS Line (Line ID 2)
**Maintainer**: Vivek Kumar
**Last updated**: 2026-05-07

---

## 1. Purpose & Methodology (what & why)

**What was needed**

The YNC seat-slider line had to track production, downtime, quality, and
process metrics in real time, so operators on the floor and managers in
the office both see the same numbers without anyone manually
re-entering data. We also wanted a dedicated camera system that records
every cycle so any defect can be replayed.

**Approach used**

- **One PLC** (Mitsubishi Q-series, MC4E protocol on TCP) is the
  ground-truth source. It sets bits like *L108 = OK*, *L109 = NG* and
  word registers like *D6005 = status code*, *D6048 = current model
  number*.
- A small Python service (the **Collector**) sits on the server,
  polls the PLC every 30 ms, debounces the bits, and writes finished
  cycles into PostgreSQL. The collector is the only program that talks
  to the PLC — everything else just reads the DB.
- A **FastAPI backend** turns the DB rows into clean JSON for the
  browser.
- A **React + Vite frontend** is the actual screen — the dashboard the
  operator looks at, the admin panel where setups are changed, and the
  fullscreen TV view on the shop floor.
- The **Camera CMS** is a parallel mini-app: it owns the cameras,
  records continuous `.ts` video per camera, and on demand cuts a clip
  of any cycle a user clicks on. It pulls Zone/Line/Machine names from
  the MES so both systems show the same hierarchy.

The whole stack is intentionally on one PC for now (192.168.10.185)
to keep the network surface small. Everything is reachable over LAN,
so any PC on the factory network can open the dashboard in a browser.

---

## 2. End-to-End Workflow (PLC → screen)

```
   PLC                Collector              PostgreSQL
   ─────              ──────────             ──────────
   L108 / L109   ─►   reads bit       ─►    ync_dashboard_complete
   D6005 status  ─►   reads word      ─►    ync_dashboard_complete_ct_log
   D6048 model   ─►   reads word      ─►    ync_status_log
   M-bit (sub)   ─►   edge count      ─►    ync_seatslider
                                              │
                                              ▼
                                        FastAPI (port 8080)
                                          GET /api/lines/2/realtime
                                          GET /api/lines/2/ct-history
                                          POST /api/auth/login
                                              │
                                              ▼
                                     React Frontend (port 5656)
                                          Dashboard / Fullscreen
                                          Admin Panel / Process Graphs
```

For video, the chain is parallel:

```
   Hikvision RTSP camera  ─►  ffmpeg (24×7)  ─►  F:\CameraCMS_Videos\<cam>\<shift>.ts
                                                       │
                                                       ▼ (on user click)
                                          ffmpeg -ss/-to extract clip  ─►  MP4 player
```

---

## 3. Directory Structure

Root project: **`D:\EOL\EOL\Deep (2)\Deep\`**

| Path | Purpose |
|------|---------|
| `Phase2\` | **MES backend** (FastAPI). The brains. |
| `Phase2\main.py` | App entrypoint, DB migrations, CMS proxy endpoints. |
| `Phase2\auth.py` | JWT login + role checks (admin / supervisor / operator etc.) |
| `Phase2\database.py` | Single Postgres connection helper. |
| `Phase2\collector_engine.py` | The PLC poller class — the only file that reads MC4E bits. |
| `Phase2\collectors\collector_ync_l6.py` | One-line bootstrap that launches the engine for **Line 2 (YNC-SS)**. |
| `Phase2\routers\*.py` | One file per page area — `lines.py`, `breakdowns.py`, `users.py`, `quality.py`, etc. |
| `mes-frontend\` | **MES React frontend** (Vite). |
| `mes-frontend\src\pages\Dashboard.jsx` | Live shop-floor dashboard. |
| `mes-frontend\src\pages\Fullscreen.jsx` | TV display (no login required). |
| `mes-frontend\src\pages\AdminPanel.jsx` | All admin pages: users, lines, machines, cameras, breakdown rules, etc. |
| `mes-frontend\src\pages\Historical.jsx` | Past-shift analysis. |
| `mes-frontend\src\pages\ProcessGraphs.jsx` | Sub-machine cycle pulse graphs. |
| `mes-frontend\src\context\AuthContext.jsx` | Per-tab session storage + JWT. |
| `mes-frontend\src\api\client.jsx` | One axios wrapper used by every page. |
| `Camera CMS\` | **Camera CMS** (Flask + React, separate stack). |
| `Camera CMS\backend\api_server.py` | Flask API on **port 5000** + RecordingManager auto-start. |
| `Camera CMS\backend\recorder_engine.py` | ffmpeg-based per-camera continuous .ts recorder. |
| `Camera CMS\backend\cycle_events.py` | Append-only `cycles.csv` log of every cycle event. |
| `Camera CMS\backend\plc_poller.py` | (Scaffold) — reads L108/L109/M-bit at 30 Hz, fires cycle events. Activates after hardware test. |
| `Camera CMS\backend\settings_config.py` | Stores the user-chosen video storage path. |
| `Camera CMS\backend\mes_sync.py` | One-way pull of zones/lines/machines from MES Postgres. |
| `Camera CMS\backend\bin\ffmpeg.exe` | Bundled static ffmpeg used for record + clip extraction. |
| `Camera CMS\backend\zones.json` | CMS-local Zone/Line/Machine tree (rebuilt by `mes_sync`). |
| `Camera CMS\backend\users.json` | Local user store for CMS. Default: `admin / admin123`. |
| `Camera CMS\backend\cycles.csv` | Append-only cycle log. Columns: cycle_seq, machine_id, camera_id, start_ts, end_ts, duration_s, status, shift_id, ts_file, ts_offset_s, ts_end_offset, notes. |
| `Camera CMS\frontend\` | CMS React UI. |
| `Camera CMS\frontend\src\pages\masters\CameraMaster.jsx` | Camera CRUD with "Mounted On" column. |
| `Camera CMS\frontend\src\pages\config\CameraConfig.jsx` | Bind machine ↔ camera ↔ PLC. |
| `Camera CMS\frontend\src\pages\config\SystemSettings.jsx` | Storage path + Sync from MES button. |
| `start_everything.bat` | **The one launcher.** Starts MES + collector + CMS + both frontends. |
| `stop_everything.bat` | Kills every Python / Node / ffmpeg process. |
| `kill-all.ps1` | PowerShell variant for selective cleanup. |

---

## 4. Network Configuration (IP-by-IP)

Server PC: **192.168.10.185** (also accessible as `127.0.0.1` from the host).

| IP | Port | Service | Notes |
|----|------|---------|-------|
| 192.168.10.185 | 8080 | MES Backend (FastAPI) | All `/api/*` for the MES side. |
| 192.168.10.185 | 5656 | MES Frontend (Vite dev) | Operator dashboard, admin, Fullscreen. |
| 192.168.10.185 | 5000 | Camera CMS API (Flask) | Camera + cycle endpoints. |
| 192.168.10.185 | 8050 | Camera CMS MJPEG Streams | Live preview tiles. |
| 192.168.10.185 | 5173 | Camera CMS Frontend (Vite dev) | CMS admin portal. |
| **192.168.10.210** | 5432 | PostgreSQL (`energydb`) | All MES + CMS persistent data. User `postgres / tbdi@123`. |
| **192.168.10.150** | 5002 | Main PLC (Mitsubishi Q-series, MC4E) | YNC-SS line. Bits L108=OK, L109=NG. |
| 192.168.10.190 | 5002 | Sub-PLC #1 — *Upper Rail Greasing M/c* | M100 cycle pulse |
| 192.168.10.191 | 5002 | Sub-PLC #2 — *Lock Bar Insert M/c* | X3 cycle pulse |
| 192.168.10.192 | 5002 | Sub-PLC #3 — *Lower Rail Greasing M/c* | M100 cycle pulse |
| 192.168.10.115 | 554 | Hikvision RTSP camera (final station) | Default user `admin / admin123` |

Storage:
- Cycle videos go to **`F:\CameraCMS_Videos\`** (external 4 TB HDD).
- Path is configurable from CMS → Configuration → System Settings.

---

## 5. Page → Data → Database Mapping

Each MES page only knows about a few API endpoints, which in turn read
specific tables. The most important mappings:

| MES Page | API endpoint | DB table(s) |
|----------|-------------|-------------|
| Dashboard (live OEE, plan vs actual, hourly grid) | `GET /api/lines/2/realtime` | `ync_dashboard_complete`, `mes_lines` |
| Cycle-time graph (508 cycles plotted) | `GET /api/lines/2/ct-history` | `ync_dashboard_complete_ct_log` |
| Loss Distribution panel | `GET /api/lines/2/hourly-loss-breakdown` | `ync_status_log` |
| B-shift / A-shift timeline strip | `GET /api/lines/2/status-log` | `ync_status_log` |
| Fullscreen TV | same as Dashboard, **no token** | same tables |
| Quality Dashboard / Poka-Yoke counts | `GET /api/poka-yoke/live/2` | `mes_poka_yoke_events`, `mes_py_master`, `mes_py_assignments` |
| Maintenance Dashboard (breakdowns) | `GET /api/breakdowns/pending-production` | `mes_breakdowns` |
| Process Graphs (sub-machine pulses) | `GET /api/submachines/{id}/hourly` | `mes_machine_process_log` |
| Production Hourly History | `GET /api/lines/2/production_history` | `ync_hourly_production` |
| Admin Panel → Cameras list | `GET /api/cms/cameras` (proxy) | (via CMS) `cameras.json` |
| AI Assistant chat | `POST /api/ai/chat` | reads any of the above on demand |

Camera CMS pages and their stores:

| CMS Page | Stored in |
|----------|-----------|
| Camera Master | `cameras.json` (Fernet-encrypted creds) |
| PLC Master | `plcs.json` |
| Zone / Line / Machine Masters | `zones.json` (synced from MES Postgres) |
| Camera Config (bindings) | `camera_config_bindings.json` |
| Shift Config | `shifts.json` |
| Cycle Monitor / Reports | `cycles.csv` (one row per cycle event) |
| System Settings | `settings.json` |

---

## 6. How to Start the System (production launch)

**One file does it all.**

1. Plug in the F:\ external HDD.
2. Double-click **`D:\EOL\EOL\Deep (2)\Deep\start_everything.bat`**.
3. Six command-prompt windows open in this order:
   1. **MES-API** — uvicorn on `:8080`
   2. **MES-Collector** — PLC poll loop, prints `[STATUS] IDLE → RUNNING` etc.
   3. **MES-Frontend** — Vite on `:5656`
   4. **CMS-API** — Flask on `:5000` (also brings up RecordingManager)
   5. **CMS-Streams** — MJPEG on `:8050`
   6. **CMS-Frontend** — Vite on `:5173`
4. Browser auto-opens to <http://127.0.0.1:5656/>.
5. Login: `admin / admin123`.

To stop everything cleanly: double-click **`stop_everything.bat`**.

If a single service hangs, just close its window; the others keep running.
The collector singleton lock (in DB table `mes_collector_locks`) auto-
releases after 30 s of no heartbeat, so a crashed collector can be
restarted safely without manual cleanup.

**Verification ports** (LAN-visible):
- MES dashboard: <http://192.168.10.185:5656>
- CMS portal:    <http://192.168.10.185:5173>

---

## 7. User Roles & Access

There are six roles. Every login JWT carries the role; the backend
enforces `require_role(...)` per endpoint and the frontend hides /
shows pages based on the same check (so an operator never even sees an
admin button).

### Role colour scheme (top-banner accent on dashboard)

| Role | Colour | Used on |
|------|--------|---------|
| **Admin** / Plant Head | Blue | Admin Panel chrome, "ADMIN" badge |
| **Production** | Green | Production dashboard, hourly grid header |
| **Maintenance** | Red | Breakdown banner, MTTR/MTBF tiles |
| **Quality** | Amber / Yellow | Poka-yoke live, NG counter pills |
| **Operator** | Grey | Default tile, light-mode shop floor |
| **Department** (generic) | Slate | Department-portal landing |

The dashboard layout uses these colours as the four corner accents (TL
= Production green, TR = Quality amber, BL = Maintenance red, BR =
Admin blue) so anyone walking past the screen can tell at a glance
which department's number is healthy.

### Page access matrix

| Page | Admin | Plant Head | Production | Maintenance | Quality | Operator | Department |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Login                       | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Dashboard (production view) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fullscreen TV               | public — no login |
| Cycle-Time History          | ✓ | ✓ | ✓ | ✓ | ✓ | read-only | ✓ |
| Loss Distribution           | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hourly Production Grid      | ✓ | ✓ | ✓ | ✓ | ✓ | read-only | ✓ |
| Process Graphs (sub-machines) | ✓ | ✓ | ✓ | ✓ | ✓ | read-only | ✓ |
| Quality Dashboard / Poka-Yoke | ✓ | ✓ | view-only | view-only | ✓ | view-only | ✓ |
| Maintenance Dashboard       | ✓ | ✓ | view-only | ✓ | view-only | view-only | ✓ |
| Breakdown raise / fill      | ✓ | ✓ | ✓ (Production half) | ✓ (Maintenance half) | ✗ | ✗ | ✗ |
| CAPA / Deviations           | ✓ | ✓ | ✗ | ✓ (Maint) | ✓ (Qual) | ✗ | ✓ (own dept only) |
| Admin Panel — Users         | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Admin Panel — Lines / Plants| ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Admin Panel — Cameras list  | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Admin Panel — Slip Threshold| ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| AI Assistant chat           | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| Camera CMS portal           | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |

The Admin user can also issue per-user, per-page overrides via
**Admin Panel → Users → Permissions Matrix** (none / read / full
columns × every page). Those overrides win over the default role
mapping above.

### Default seeded credentials

| User | Password | Role |
|------|----------|------|
| `admin` | `admin123` | admin |
| `supervisor` | `super123` | supervisor / plant_head |
| `operator` | `oper123` | operator |

Change these immediately in production via Admin Panel → Users →
Change Password.

---

*End of manual.*  For internal questions, contact Vivek Kumar
(Manufacturing & Automation Engineer, TBDI Bawal).
