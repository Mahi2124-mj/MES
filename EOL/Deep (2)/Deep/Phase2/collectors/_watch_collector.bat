@echo off
REM ============================================================
REM  MES Collector LIVE OUTPUT watcher (read-only).
REM ============================================================
REM  The never-die launcher (_run_collector.bat) redirects the
REM  collector's output to ..\logs\collector_live.log so the
REM  engine stays observable across auto-restarts.  That keeps
REM  the launcher window itself blank by design.
REM
REM  Double-click THIS file to watch the live output (status
REM  table, heartbeats, counts) scroll in real time.  Closing
REM  this window only stops WATCHING - the collector keeps
REM  running untouched.  Open it again any time.
REM ============================================================
title MES-Collector LIVE OUTPUT
echo ============================================================
echo  Watching collector live output.  Close window to stop
echo  watching - collector keeps running.
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content 'D:\EOL\EOL\Deep (2)\Deep\Phase2\logs\collector_live.log' -Wait -Tail 40"
