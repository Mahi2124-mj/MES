@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
title  EOL Unified Launcher

REM ============================================================
REM   EOL UNIFIED LAUNCHER  (wrapper-based, 2026-05-29)
REM
REM   Each service has its own _run_*.bat wrapper that handles
REM   its own python detection, venv activation, and stays open
REM   on exit for debugging.  This file just spawns those 5
REM   wrappers in separate console windows.  No quoting hell.
REM
REM   Wrappers:
REM     Phase2\_run_mes_api.bat              (MES uvicorn :8080)
REM     Phase2\collectors\_run_collector.bat (MES Collector)
REM     mes-frontend\_run_mes_frontend.bat   (MES Vite :5656)
REM     backend\_run_cms_api.bat             (CMS Flask :5555)
REM     frontend\_run_cms_frontend.bat       (CMS Vite :5575)
REM ============================================================

set "MES_DIR=%ROOT%Phase2"
set "MES_FE=%ROOT%mes-frontend"
set "CMS_DIR=%ROOT%..\..\New folder (2)\New folder (2)"

REM --- Sanity: required wrappers exist ------------------------
set "MISSING="
if not exist "%MES_DIR%\_run_mes_api.bat"                  set "MISSING=%MISSING% MES-API-wrapper"
if not exist "%MES_DIR%\collectors\_run_all_collectors.bat" set "MISSING=%MISSING% Collector-supervisor"
if not exist "%MES_FE%\_run_mes_frontend.bat"              set "MISSING=%MISSING% MES-FE-wrapper"
if not exist "%CMS_DIR%\backend\_run_cms_api.bat"          set "MISSING=%MISSING% CMS-API-wrapper"
if not exist "%CMS_DIR%\frontend\_run_cms_frontend.bat"    set "MISSING=%MISSING% CMS-FE-wrapper"
if defined MISSING (
    echo  [FATAL] Missing wrappers: %MISSING%
    pause
    exit /b 1
)

cls
echo.
echo  ===============================================================
echo    EOL Unified Launcher  (wrapper-based, 2026-05-29)
echo  ===============================================================
echo    MES Backend   http://127.0.0.1:8080     uvicorn
echo    MES Frontend  http://127.0.0.1:5656     Vite
echo    CMS API       http://127.0.0.1:5555     Flask + plc_edge
echo    CMS Frontend  http://127.0.0.1:5575     Vite
echo  ===============================================================
echo.

REM --- PHASE 1: cleanup stale services (ports + collector + cmd wrappers) ---
REM
REM 2026-05-29 — Hardened to use taskkill /T /F instead of Stop-Process
REM because .venv\Scripts\python.exe is a shim that spawns the real
REM Python as a child; killing the shim alone leaves the child holding
REM PLC TCP slots (caused "actively refused" on relaunch).
REM Also kills the _run_collector.bat cmd window because its restart
REM loop would otherwise spawn a fresh python instantly.
echo  [PHASE 1/4]  Cleaning up stale services...
echo                - port-bound (8080, 5555, 5575, 5000, 8050, 5173)   [5656 EXCLUDED — owned by Caddy]
REM 2026-06-18 HARDENING: 5656 removed from the kill list — Caddy now serves
REM the MES frontend (static dist) on 5656; killing it would take down the
REM reverse proxy (also holds :443/:8443) and re-expose the dev server.
powershell -NoProfile -Command "$p='8080','5555','5575','5000','8050','5173'; netstat -ano | Select-String 'LISTENING' | ForEach-Object { $x=($_.ToString().Trim() -split '\s+'); if($x.Count -ge 5){ $lp=($x[1] -split ':')[-1]; if($p -contains $lp){ Stop-Process -Id ([int]$x[4]) -Force -ErrorAction SilentlyContinue } } }" >nul 2>nul
echo                - collector python tree (parent + children)
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*Phase2*venv*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" >nul 2>nul
echo                - wrapper cmd.exe windows (_run_*.bat) by title
powershell -NoProfile -Command "Get-Process cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'MES-Collector|MES-API|MES-Frontend|CMS-API|CMS-Frontend|MES-Video-Archiver' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" >nul 2>nul
echo                Done.
echo.

REM --- PHASE 2: release collector singleton lock --------------
echo  [PHASE 2/4]  Releasing MES collector lock (energydb, 5s cap)...
"C:\PROGRA~1\PYTHON~1\python.exe" -c "import psycopg2; c=psycopg2.connect(host='192.168.10.210',port=5432,user='postgres',password='tbdi@123',dbname='energydb',connect_timeout=5,options='-c lock_timeout=3000 -c statement_timeout=3000'); cur=c.cursor(); cur.execute('DELETE FROM mes_collector_locks'); c.commit(); c.close()" 2>nul
echo                Done.
echo.

REM --- PHASE 3: brief PLC TCP-slot release window ------------
REM
REM Mitsubishi Q-series CPUs hold MC-protocol connection slots for
REM a few seconds after the OS-side socket closes (FIN handshake +
REM their own keepalive timeout).  Without this pause, the freshly
REM launched collector races the PLC and gets WinError 10061
REM (actively refused) on the first connect.
echo  [PHASE 3/4]  Waiting 5s for PLC TCP slots to release...
ping -n 2 127.0.0.1 >nul
echo                Done.
echo.

REM --- PHASE 4: launch service wrappers -----------------------
echo  [PHASE 4/4]  Launching 5 service windows...
echo.

echo   [1/5] MES-API ...
start "MES-API" /D "%MES_DIR%" cmd /k ".\_run_mes_api.bat"
ping -n 2 127.0.0.1 >nul

echo   [2/5] MES-Collectors (dynamic supervisor — ALL provisioned lines) ...
REM 2026-06-03 — clear the collector STOP.flag (dropped by stop_everything.bat)
REM so the never-die loops are re-armed for this run.
del "%MES_DIR%\collectors\STOP.flag" >nul 2>nul
REM 2026-07-01 — was ".\_run_collector.bat" (single YNC).  Now the dynamic
REM supervisor launches a never-die loop for EVERY collectors\collector_*.py,
REM so newly-provisioned lines auto-start on boot with no edit here.
start "MES-Collector" /D "%MES_DIR%\collectors" cmd /k ".\_run_all_collectors.bat"
ping -n 2 127.0.0.1 >nul

echo   [3/5] MES-Frontend (Caddy static dist on :5656 + :443) ...
REM 2026-06-18 HARDENING: the MES frontend is NO LONGER served via the Vite dev
REM server in production (it exposed /src, /package.json, /@vite/client).  Caddy
REM serves the production build  mes-frontend\dist  with security headers instead.
REM Start Caddy only if 5656 isn't already served (idempotent on re-run).  To work
REM on the frontend: run Vite on a DIFFERENT port, then `npm run build` to deploy.
REM   old: start "MES-Frontend" /D "%MES_FE%" cmd /k ".\_run_mes_frontend.bat"
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 5656 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -FilePath 'D:\caddy\run_caddy.bat' -WorkingDirectory 'D:\caddy' }" >nul 2>nul
ping -n 2 127.0.0.1 >nul

echo   [4/5] CMS-API ...
start "CMS-API" /D "%CMS_DIR%\backend" cmd /k ".\_run_cms_api.bat"
ping -n 2 127.0.0.1 >nul

echo   [5/5] CMS-Frontend ...
start "CMS-Frontend" /D "%CMS_DIR%\frontend" cmd /k ".\_run_cms_frontend.bat"
ping -n 2 127.0.0.1 >nul

REM --- 2026-06-01 ADDITIVE: per-cycle video archiver (line 2) -----
REM   Standalone never-die daemon.  Reads MES DB + GETs NF2's clip
REM   endpoint (same call the live UI makes), writes ONLY new mp4s
REM   under D:\VideoArchive + the additive mes_video_archive index.
REM   A pg advisory lock guarantees a single live instance, so even
REM   if this launches while one is already up, only one does work.
REM   Touches NOTHING in counting / collector / PLC / semi-auto.
echo   [+]   MES-Video-Archiver ...
start "MES-Video-Archiver" /D "D:\EOL\VideoArchiver" cmd /k ".\run_archiver.bat"
ping -n 2 127.0.0.1 >nul

REM --- 2026-06-01 ADDITIVE: read-only FI count monitor (line 2) ---
REM   Standalone never-die READ-ONLY diagnostic.  SELECT-only on
REM   energydb + tails the collector log; writes ONLY under
REM   D:\FI_Monitor\logs\fi_count_monitor.log.  NO MES write, NO PLC
REM   socket, NO collector touch.  Purpose: next time the Final count
REM   freezes, the log says WHY -- FROZEN vs BREAK vs IDLE, plus the
REM   raw D101 junk reads (->0/1/16) that the guard is rejecting.
echo   [+]   FI-Count-Monitor (read-only) ...
start "FI-Count-Monitor" /D "D:\FI_Monitor" cmd /k ".\_run_fi_monitor.bat"
ping -n 2 127.0.0.1 >nul

REM --- 2026-07-05 ADDITIVE: collector silent-death watchdog -------
REM   READ-ONLY monitor.  Every 60s it compares each provisioned line's
REM   last dashboard-table write vs now(); flags any line whose collector
REM   has gone stale/dead (hung main loop while the lock-heartbeat thread
REM   keeps beating -> the failure that hid YRA-SS being frozen ~2h with a
REM   "Live" dashboard on 2026-07-05).  Prints STALE lines in its window;
REM   e-mails too if WATCHDOG_TO (or NOTIFY_EMAIL) + SMTP_* are set in .env.
REM   NO DB writes, NO PLC, NO collector touch.
echo   [+]   MES-Collector-Watchdog (silent-death detector) ...
start "MES-Collector-Watchdog" /D "%MES_DIR%" cmd /k ".venv\Scripts\python.exe _collector_watchdog.py --loop 60 --email"
ping -n 2 127.0.0.1 >nul

echo.
echo  All 5 service windows launched.  Opening dashboard...
start "" "http://127.0.0.1:5656"

echo.
echo  ===============================================================
echo    SYSTEM IS UP
echo  ===============================================================
echo    MES dashboard  http://127.0.0.1:5656     ( admin / admin123 )
echo    CMS portal     http://127.0.0.1:5575     ( admin / TbAdmin@2024! )
echo  ===============================================================
echo.
echo    Check each service window if any errors.
echo    Press any key to close this launcher window.
echo.
pause >nul
exit /b 0
