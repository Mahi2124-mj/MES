@echo off
REM ============================================================
REM   MES Collector wrapper — invoked by start_everything.bat.
REM
REM   2026-05-19 — Created to dodge the cmd /k nested-quote bug
REM   when start_everything.bat tried to one-line the collector
REM   launch with `> "path with (parens)\file.log"` redirect.
REM   Backslash-escaped quotes inside cmd /k's argument were
REM   getting passed through to the redirect filename, producing:
REM
REM       The filename, directory name, or volume label syntax
REM       is incorrect.
REM
REM   This file runs INSIDE the spawned cmd window so the redirect
REM   parses cleanly — no outer cmd /k quoting layer to fight.
REM ============================================================

title MES-Collector
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "LOG=%~dp0..\_collector.log"

echo Collector running. Live output: %LOG%
echo (this window will stay open even if collector exits — for inspection)
echo.

REM Detect Python (system or via py launcher) — same order as launcher
set "PY_EXE="
where python >nul 2>nul
if not errorlevel 1 set "PY_EXE=python"
if not defined PY_EXE (
    where py >nul 2>nul && set "PY_EXE=py -3.12"
)
if not defined PY_EXE (
    echo [FATAL] Python not found in this window.
    pause
    exit /b 1
)

%PY_EXE% -u collector_ync_l6.py > "%LOG%" 2>&1

echo.
echo ====================================================================
echo Collector process exited (rc=%errorlevel%).  Check %LOG% for details.
echo Press any key to close this window.
echo ====================================================================
pause >nul
exit /b %errorlevel%
