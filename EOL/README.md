# EOL Manufacturing Execution System (MES) + Camera Management System (CMS)

End-of-Line inspection MES for **Toyota Boshoku Device India** — seat-slider
production line.  Combines real-time PLC polling, video recording from IP
cameras, cycle-clip extraction, breakdown / CAPA workflow, and live shop-
floor dashboards.

---

## Repository layout

```
EOL/
├── Deep (2)/Deep/                          # MES side
│   ├── Phase2/                             # FastAPI backend (uvicorn :8080)
│   │   ├── collector_engine.py             # PLC poller core
│   │   ├── routers/                        # API endpoints
│   │   │   ├── breakdowns.py               # Breakdown slip workflow
│   │   │   ├── capa.py                     # CAPA filings
│   │   │   ├── lines.py                    # Line config + cycle video proxy
│   │   │   ├── submachines.py              # Sub-machine cycle clips
│   │   │   ├── wallboard.py                # KPI rollup
│   │   │   └── ... (~20 more routers)
│   │   ├── database.py                     # Postgres pool
│   │   ├── auth.py                         # JWT auth
│   │   └── collectors/                     # Per-line collector scripts
│   ├── mes-frontend/                       # React + Vite frontend (:5656)
│   │   └── src/pages/
│   │       ├── Fullscreen.jsx              # Main dashboard
│   │       ├── MaintenanceDashboard.jsx    # Maintenance + breakdown UI
│   │       └── WallboardLeft.jsx           # Shop-floor TV view
│   ├── POKA-YOKE/                          # JSON-only config app
│   ├── start_everything.bat                # One-click launcher
│   └── stop_everything.bat                 # One-click killer
│
└── New folder (2)/New folder (2)/          # CMS side
    ├── backend/                            # Flask api_server (:5555)
    │   ├── api_server.py                   # Main entry, REST endpoints
    │   ├── plc_monitor.py                  # Camera recorder + cycle extractor
    │   ├── recorder.py                     # ffmpeg wrapper
    │   ├── shifts_config.py                # Shift schedule
    │   └── zone_config.py                  # Zone / line / camera mapping
    └── frontend/                           # React + Vite admin (:5575)
```

---

## Tech stack

| Layer | Stack |
|---|---|
| MES Backend | Python 3.12 · FastAPI · uvicorn · psycopg2 · pymcprotocol |
| MES Frontend | React 18 · Vite · Chart.js |
| CMS Backend | Python 3.12 · Flask · imageio_ffmpeg · psutil |
| CMS Frontend | React 18 · Vite |
| Database | PostgreSQL 16 |
| Cameras | Panasonic i-PRO RTSP (H.265 1080p) |
| PLC | Mitsubishi Q-series via MC4E protocol |
| GPU | NVIDIA RTX A2000 12GB (NVENC for cycle clip encode) |

---

## Quick start

### Prerequisites

- Python 3.12, Node.js 20+
- PostgreSQL 16 reachable on LAN (default `192.168.10.210:5432`)
- ffmpeg (bundled via `imageio_ffmpeg`)
- NVIDIA driver ≥ 551.76 (for NVENC; falls back to Intel QSV / libx264)

### First-time setup

```cmd
# 1. Clone
git clone https://github.com/<your-id>/EOL.git
cd EOL

# 2. MES backend deps
cd "Deep (2)\Deep\Phase2"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd ..\..\..

# 3. MES frontend deps
cd "Deep (2)\Deep\mes-frontend"
npm install
cd ..\..\..

# 4. CMS backend deps
cd "New folder (2)\New folder (2)\backend"
pip install -r requirements.txt  # (uses same python)
cd ..\..\..

# 5. CMS frontend deps
cd "New folder (2)\New folder (2)\frontend"
npm install
cd ..\..\..

# 6. Set up secrets (copy templates)
copy "Deep (2)\Deep\Phase2\.env.example" "Deep (2)\Deep\Phase2\.env"
# edit .env with your DB credentials
copy "New folder (2)\New folder (2)\backend\.env.example" "New folder (2)\New folder (2)\backend\.env"

# 7. Launch everything
"Deep (2)\Deep\start_everything.bat"
```

After launch, open:
- MES dashboard: http://127.0.0.1:5656 (admin / admin123)
- CMS portal: http://127.0.0.1:5575 (admin / TbAdmin@2024!)

---

## Daily operations

### Start / stop

```cmd
# Start everything
"Deep (2)\Deep\start_everything.bat"

# Stop everything
"Deep (2)\Deep\stop_everything.bat"
```

### Service ports

| Port | Service |
|---|---|
| 8080 | MES Backend (FastAPI) |
| 5555 | CMS API (Flask) |
| 5656 | MES Frontend (Vite) |
| 5575 | CMS Frontend (Vite) |

### Key DB tables

| Table | Purpose |
|---|---|
| `mes_lines` | Line config (1 row per production line) |
| `mes_zones` | Plant zones |
| `mes_breakdowns` | All breakdowns (entire plant) |
| `mes_capa` | CAPA filings |
| `ync_dashboard_complete` | YNC line shift rollup |
| `ync_dashboard_complete_ct_log` | Per-cycle log |

---

## Critical files (don't touch without understanding)

| File | Why |
|---|---|
| `Deep (2)/Deep/Phase2/collector_engine.py` | PLC poll loop — 30ms cycle, OK/NG dwell-debounce |
| `New folder (2)/New folder (2)/backend/plc_monitor.py` | Camera recording + cycle clip extraction |
| `Deep (2)/Deep/start_everything.bat` | Stall-safe launcher (don't edit timeouts blindly) |
| `Deep (2)/Deep/Phase2/auth.py` | JWT secret — change in production |

---

## Documentation

- **Architecture diagram**: see `docs/MES_Traffic_Flow.png`
- **DB schema reference**: see `docs/MES_Maintenance_DB_Schema.pdf`
- **VMS workstation BoM**: see `docs/VMS_Workstation_BoM_Final.pdf`
- **Setup guide**: see `docs/EOL_Setup_Guide.pdf`

---

## License

Proprietary — Toyota Boshoku Device India internal use only.

---

## Maintainers

- Manufacturing/Automation Engineer (owner): owns end-to-end
- Maintenance team: breakdown / CAPA workflow
- IT: server / network / backup
