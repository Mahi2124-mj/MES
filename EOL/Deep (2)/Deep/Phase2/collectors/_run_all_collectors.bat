@echo off
REM ============================================================
REM  MES COLLECTOR SUPERVISOR  (dynamic, 2026-07-01)
REM
REM  Discovers EVERY generated collector  (collectors\collector_*.py)
REM  and launches each in its OWN never-die loop window
REM  (_run_one_collector.bat).  Any new line provisioned via the admin
REM  panel drops a new collector_<line>.py here and is picked up
REM  AUTOMATICALLY on the next start — no edit to any .bat needed.
REM
REM  start_everything.bat calls THIS instead of the old single-line
REM  _run_collector.bat.  Boot flow already: kills stale collectors
REM  (Phase 1) + wipes mes_collector_locks (Phase 2) before we run,
REM  so every collector below acquires its per-line lock cleanly.
REM
REM  STOP ALL cleanly: create STOP.flag in this folder (each loop
REM  checks it) — same mechanism stop_everything.bat already uses.
REM ============================================================
title MES-Collector-Supervisor
cd /d "%~dp0"

set "FOUND="
echo.
echo  Launching all collectors in %~dp0 ...
echo.
for %%F in (collector_*.py) do (
    set "FOUND=1"
    echo   -^> %%F
    start "MES-Collector-%%~nF" /D "%~dp0" cmd /k ".\_run_one_collector.bat %%F"
    ping 127.0.0.1 -n 2 >nul
)

if not defined FOUND (
    echo  [WARN] no collector_*.py found in %~dp0
    echo         Provision a line from the admin panel first.
)

echo.
echo  All collector loop windows launched.  This supervisor window can be closed.
echo  (Each collector runs in its own MES-Collector-* window and auto-restarts.)
exit /b 0
