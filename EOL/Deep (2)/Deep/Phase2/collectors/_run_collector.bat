@echo off
REM ============================================================
REM MES Collector launcher with NEVER-DIE auto-restart loop.
REM ============================================================
REM Operator request 2026-05-29: "kuch bhi jaye jab tk manual
REM collector band na ho off nhi hona chahiye".
REM
REM How it works:
REM   - python collector_ync_l6.py runs in a loop.
REM   - If it exits for ANY reason (crash, Stop-Process, taskkill,
REM     OS reboot's friend, PLC disconnect chain, etc.) we wait 5
REM     seconds and relaunch automatically.  No human needed.
REM   - Before each relaunch we clear the cross-process DB lock
REM     so a fresh start never gets blocked by the previous
REM     instance's stale heartbeat row.
REM   - To STOP cleanly: close this cmd window OR create the file
REM     STOP.flag next to this .bat.  Loop checks it before every
REM     restart.  Delete the flag to re-arm.
REM
REM Collector python code is NOT modified.  This is launcher
REM resilience only.
REM ============================================================
title MES-Collector
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

REM Quote the SET value so the literal "(2)" in the path doesn't get
REM eaten by cmd's parenthesis-block parser.  Without "..." quoting,
REM `set PY_EXE=D:\...Deep (2)\...` truncates at the open paren and
REM the variable ends up empty, leaving plain `D:\EOL\EOL\Deep` to
REM be executed as a command (the very error we kept hitting).
set "PY_EXE=D:\EOL\EOL\Deep (2)\Deep\Phase2\.venv\Scripts\python.exe"
set "STOP_FLAG=%~dp0STOP.flag"
set "ATTEMPT=0"

:restart
set /a ATTEMPT+=1
echo.
echo ============================================================
echo  MES Collector launch attempt #%ATTEMPT% at %DATE% %TIME%
echo ============================================================
echo.

REM Stale-lock cleanup so a fresh start never trips the singleton
REM guard with the previous PID's leftover heartbeat row.
"%PY_EXE%" _clear_stale_lock.py

REM 2026-05-30 — Redirect engine output to a live log file so the running
REM state is observable (FI register reads, [COUNT-DIAG], [REG-...] etc).
REM Append (>>) so we keep history across auto-restarts.  Mirror to console
REM via PowerShell tee is too brittle inside this never-die loop, so we
REM just write to file; use Get-Content -Wait or tail to follow.
"%PY_EXE%" -u collector_ync_l6.py >> "%~dp0..\logs\collector_live.log" 2>&1
set RC=%errorlevel%

echo.
echo ============================================================
echo  Collector exited rc=%RC% at %DATE% %TIME% (attempt #%ATTEMPT%)
echo ============================================================

REM Use the pre-stored STOP_FLAG variable instead of inline %~dp0
REM expansion — the literal "(2)" in the path breaks cmd's IF block
REM parser when expanded inline (saw `was unexpected at this time`).
if exist "%STOP_FLAG%" (
    echo.
    echo  STOP.flag present - auto-restart SUPPRESSED.
    echo  Delete "%STOP_FLAG%" to re-arm the loop, then relaunch.
    echo.
    pause
    exit /b 0
)

echo.
echo  Auto-restart in 5 seconds.  Close window to abort.
echo  ^(Or create STOP.flag in this folder to suppress future restarts.^)
echo.
REM Use ping instead of `timeout` because `timeout` immediately
REM returns when stdin has been redirected (e.g. when launched via
REM Start-Process -RedirectStandardOutput).  ping always sleeps
REM regardless of stdin state — 6 pings at 1s spacing = ~5 seconds.
ping 127.0.0.1 -n 6 >nul
goto restart
