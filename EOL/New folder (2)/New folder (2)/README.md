# Toyota Boshoku EMS Console

React-based master setup UI backed by a Python API and the existing JSON/Python business logic already present in this workspace.

## What This Rebuild Includes

- React frontend with a real multi-page EMS shell
- Dedicated masters for Line, Zone, Machine, and Camera
- Theme-customizable UI from the sidebar
- Python API layer exposing the existing JSON structure and CRUD flows
- Existing recorder/cycle logic left intact for downstream use

## Folder Direction

- `frontend/src/features/dashboard` contains the dashboard workspace
- `frontend/src/features/masters` contains `LineMasterPage`, `ZoneMasterPage`, `MachineMasterPage`, and `CameraMasterPage`
- `frontend/src/components` contains reusable layout, hierarchy, table, and theme components
- `api_server.py` exposes the React app data endpoints
- `zones.json`, `cameras.json`, `users.json`, and `cycles.csv` remain the source data files

## Install

### Python

```bash
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

## Quick Start on Windows

- Double-click `start_all.bat`
- Or start the services separately:
  - `start_api.bat`
  - `start_frontend.bat`

## Manual Run

### 1. Start the API

```bash
python api_server.py
```

API health endpoint:

- http://127.0.0.1:5000/api/health

### 2. Start the React frontend

```bash
cd frontend
npm run dev
```

Frontend:

- http://127.0.0.1:5173

## Current Master Pages

- Dashboard
- Line Master
- Zone Master
- Machine Master
- Camera Master

## Notes

- The React frontend talks to Flask through Vite proxy configuration.
- Existing recorder and cycle files are still available for future live monitoring pages.
- The old Dash dashboard files may still exist in the workspace, but the primary UI path is now React + Flask.
