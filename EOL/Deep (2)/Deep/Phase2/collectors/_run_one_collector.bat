@echo off
REM ============================================================
REM  PER-COLLECTOR never-die loop.   %1 = collector script name
REM  (e.g. collector_yca_l5.py).  Generalized 2026-07-01 from the
REM  original single-YNC _run_collector.bat so the dynamic
REM  supervisor (_run_all_collectors.bat) can run ONE of these per
REM  generated collector.  Same never-die behaviour:
REM    - runs the collector; on ANY exit, waits 5s and relaunches
REM    - STOP cleanly: create STOP.flag next to this .bat
REM    - clears THIS line's stale lock (per-line, safe) before each
REM      launch so a fast restart never waits ~30s for the lock
REM  Collector python code is NOT modified — launcher resilience only.
REM ============================================================
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

REM Quote the SET value so the literal "(2)" in the path doesn't get
REM eaten by cmd's parenthesis-block parser (same guard as the original).
set "PY_EXE=D:\EOL\EOL\Deep (2)\Deep\Phase2\.venv\Scripts\python.exe"
set "STOP_FLAG=%~dp0STOP.flag"
set "COLLECTOR=%~1"
set "CNAME=%~n1"

if "%COLLECTOR%"=="" (
    echo  [FATAL] no collector script passed to _run_one_collector.bat
    pause
    exit /b 1
)
title MES-Collector-%CNAME%
set "ATTEMPT=0"

:restart
set /a ATTEMPT+=1
echo.
echo ============================================================
echo  %COLLECTOR%  launch attempt #%ATTEMPT%  at %DATE% %TIME%
echo ============================================================
echo.

REM Per-line stale-lock cleanup (scoped to THIS collector's line_id).
"%PY_EXE%" _clear_stale_lock.py "%COLLECTOR%"

REM Run the collector; append output to its own live log.
"%PY_EXE%" -u "%COLLECTOR%" >> "%~dp0..\logs\collector_%CNAME%.log" 2>&1
set RC=%errorlevel%

echo.
echo ============================================================
echo  %COLLECTOR% exited rc=%RC% at %DATE% %TIME% (attempt #%ATTEMPT%)
echo ============================================================

if exist "%STOP_FLAG%" (
    echo.
    echo  STOP.flag present - auto-restart SUPPRESSED for %CNAME%.
    echo  Delete "%STOP_FLAG%" to re-arm, then relaunch.
    echo.
    pause
    exit /b 0
)

echo.
echo  Auto-restart in 5 seconds.  Close window to abort.
echo.
ping 127.0.0.1 -n 6 >nul
goto restart
