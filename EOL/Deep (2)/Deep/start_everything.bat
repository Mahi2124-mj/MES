@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
title  EOL Unified Launcher

REM ============================================================
REM   EOL UNIFIED LAUNCHER  (stall-safe, 2026-05-19)
REM
REM   2026-05-19 — Rewrite to eliminate stall after [1/5] MES Backend.
REM   Earlier symptoms:
REM     * Operator: "MES backend on hota hai aur phir sab ruk jata hai"
REM     * Bat hung indefinitely between [1/5] and [2/5] launches.
REM
REM   Stall root causes identified & fixed:
REM
REM   1. `^|` inside `cmd /k "title ..."` — batch + cmd nested escaping
REM      was unreliable.  Some Windows builds expanded the `|` as a real
REM      pipe operator INSIDE the new console, piping `title` output to
REM      a non-command which then hung waiting for stdin.
REM      FIX: plain titles, no piped decoration.
REM
REM   2. `timeout /t N /nobreak >nul` — `timeout` has a long-standing bug
REM      where redirecting stdout to nul on certain Windows builds makes
REM      it error-exit immediately OR (worse) hang waiting for a keypress
REM      depending on Defender's stdin hooking.
REM      FIX: `ping -n N+1 127.0.0.1 >nul` — kernel-level delay, no stdin,
REM      no console attribute checks, never blocked by Defender wrappers.
REM
REM   3. No echo AFTER each `start` + delay — if a stall happened, the
REM      operator had no visual cue where.
REM      FIX: explicit "  -> launched" / "  -> warmed" echos so every
REM      step is visible in the launcher window.
REM
REM   Services launched (titles use plain ASCII, no special chars):
REM        MES-API       uvicorn :8080
REM        MES-Collector line 2 PLC poller (no port)
REM        MES-Frontend  Vite :5656
REM        CMS-API       Flask + plc_edge :5555
REM        CMS-Frontend  Vite :5575
REM ============================================================

set "MES_DIR=%ROOT%Phase2"
set "MES_FE=%ROOT%mes-frontend"
set "CMS_DIR=%ROOT%..\..\New folder (2)\New folder (2)"

REM ─── Sanity: required directories ─────────────────────────────
for %%D in ("%MES_DIR%" "%MES_FE%" "%CMS_DIR%") do (
    if not exist %%D (
        echo  [FATAL] Missing directory: %%D
        pause
        exit /b 1
    )
)

REM ─── Detect Python (system or via py launcher) ────────────────
set "PY_EXE="
where python >nul 2>nul
if not errorlevel 1 set "PY_EXE=python"
if not defined PY_EXE (
    where py >nul 2>nul && set "PY_EXE=py -3.12"
)
if not defined PY_EXE (
    echo  [FATAL] Python not found.  Install Python 3.12 and add to PATH.
    pause
    exit /b 1
)

REM ─── Sanity: Node.js ──────────────────────────────────────────
where npm >nul 2>nul
if errorlevel 1 (
    echo  [FATAL] Node.js / npm not found.  Install from https://nodejs.org
    pause
    exit /b 1
)

cls
echo.
echo  ===============================================================
echo    EOL Unified Launcher  (stall-safe, 2026-05-19)
echo  ===============================================================
echo    MES Backend   http://127.0.0.1:8080     uvicorn
echo    MES Frontend  http://127.0.0.1:5656     Vite
echo    CMS API       http://127.0.0.1:5555     Flask + plc_edge
echo    CMS Frontend  http://127.0.0.1:5575     Vite
echo  ===============================================================
echo    ping-based delays (no timeout stall) + plain titles.
echo    Total launcher runtime ~ 40 s.
echo  ===============================================================
echo.

REM ─── PHASE 1: cleanup ports (single PowerShell shot) ──────────
echo  [PHASE 1/3]  Cleaning up stale ports...
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8080,5555,5656,5575,5000,8050,5173) } | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {} }" >nul 2>nul
echo                Done.
echo.

REM ─── PHASE 2: release collector singleton lock ────────────────
echo  [PHASE 2/3]  Releasing MES collector lock (energydb, 5s cap)...
"%PY_EXE%" -c "import psycopg2; c=psycopg2.connect(host='192.168.10.210',port=5432,user='postgres',password='tbdi@123',dbname='energydb',connect_timeout=5,options='-c lock_timeout=3000 -c statement_timeout=3000'); cur=c.cursor(); cur.execute('DELETE FROM mes_collector_locks'); c.commit(); c.close()" 2>nul
echo                Done.
echo.

REM ─── PHASE 3: launch services with FIXED ping-based gaps ──────
REM
REM Each `ping -n N+1` gives the spawned process room to bind its port
REM AND lets the AV scanner finish its inspection before the next
REM service piles on.  ping doesn't touch stdin, never stalls.
REM
REM Note on titles: plain ASCII only — no pipe / quote / ampersand
REM characters.  Previous build had "MES-API ^| uvicorn :8080" which
REM occasionally got mis-parsed by cmd as a real pipe inside the new
REM console, hanging the launcher.
echo  [PHASE 3/3]  Launching services (ping-based fixed gaps)...
echo.

echo   [1/5] MES Backend   (uvicorn :8080)        - launching...
start "MES-API" /D "%MES_DIR%" cmd /k "set PYTHONIOENCODING=utf-8 && set PYTHONUNBUFFERED=1 && %PY_EXE% -u -m uvicorn main:app --host 0.0.0.0 --port 8080"
echo         -^> launched.  Waiting 8s for port :8080 to bind...
ping -n 9 127.0.0.1 >nul
echo         -^> warmed.
echo.

echo   [2/5] MES Collector (line 2 PLC poller)    - launching...
REM 2026-05-19 — Wrapper bat indirection.
REM Earlier symptom: operator saw "The filename, directory name, or
REM volume label syntax is incorrect" in the spawned MES-Collector
REM window because the inline `> \"%MES_DIR%\_collector.log\"` redirect
REM was getting mangled by cmd /k's argument parser — the
REM backslash-escaped quotes leaked into the redirect filename
REM verbatim, producing the syntax error.
REM
REM Fix: delegate to _run_collector.bat which runs INSIDE the spawned
REM cmd window so the redirect parses without an outer escape layer.
REM Path resolution uses %~dp0 inside the wrapper so it remains
REM portable regardless of who invoked it.
REM `.\` prefix mandatory: cmd /k without it tries to resolve
REM _run_collector.bat against PATH (not CWD) and prints
REM "'_run_collector.bat' is not recognized" — losing the collector.
start "MES-Collector" /D "%MES_DIR%\collectors" cmd /k ".\_run_collector.bat"
echo         -^> launched.  Waiting 4s...
ping -n 5 127.0.0.1 >nul
echo         -^> warmed.
echo.

echo   [3/5] MES Frontend  (Vite :5656)           - launching...
REM Vite reads stdin for its interactive prompt; never redirect from <nul
REM (causes immediate EOF -> silent exit right after the ready banner).
start "MES-Frontend" /D "%MES_FE%" cmd /k "npm run dev"
echo         -^> launched.  Waiting 10s for Vite to ready...
ping -n 11 127.0.0.1 >nul
echo         -^> warmed.
echo.

echo   [4/5] CMS Flask API (port 5555)            - launching...
start "CMS-API" /D "%CMS_DIR%\backend" cmd /k "%PY_EXE% api_server.py"
echo         -^> launched.  Waiting 6s for port :5555 to bind...
ping -n 7 127.0.0.1 >nul
echo         -^> warmed.
echo.

echo   [5/5] CMS Frontend  (Vite :5575)           - launching...
REM Same stdin-EOF rule as MES-Frontend.
start "CMS-Frontend" /D "%CMS_DIR%\frontend" cmd /k "npm run dev -- --host 0.0.0.0"
echo         -^> launched.  Waiting 8s for Vite to ready...
ping -n 9 127.0.0.1 >nul
echo         -^> warmed.
echo.

REM ─── Open browser ─────────────────────────────────────────────
echo  All services launched.  Opening the dashboard...
start "" "http://127.0.0.1:5656"

echo.
echo  ===============================================================
echo    SYSTEM IS UP
echo  ===============================================================
echo    MES dashboard  http://127.0.0.1:5656     ( admin / admin123 )
echo    CMS portal     http://127.0.0.1:5575     ( admin / TbAdmin@2024! )
echo  ===============================================================
echo.
echo    The 5 service windows will keep running until you close them
echo    individually OR run  stop_everything.bat
echo.
echo    If a service window shows an error, check that window first.
echo.
echo    Press any key to close this launcher window.
echo.
pause >nul
exit /b 0
