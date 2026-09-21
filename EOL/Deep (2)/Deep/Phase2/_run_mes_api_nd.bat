@echo off
REM ============================================================
REM  NEVER-DIE MES API  (uvicorn main:app  :8080)
REM  Retries on ANY exit — including a bind failure right after a
REM  fast restart (Windows holds the socket for a few seconds after
REM  a force-kill), so a quick handoff no longer leaves the API down.
REM  STOP cleanly: create  STOP_API.flag  next to this .bat.
REM  Mirrors the per-collector never-die launcher (_run_one_collector.bat).
REM ============================================================
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

set "PY_EXE=D:\EOL\EOL\Deep (2)\Deep\Phase2\.venv\Scripts\python.exe"
set "STOP_FLAG=%~dp0STOP_API.flag"
title MES-API-8080
set "ATTEMPT=0"

:restart
set /a ATTEMPT+=1
echo.
echo ============================================================
echo  MES API launch attempt #%ATTEMPT%  at %DATE% %TIME%
echo ============================================================
echo.

"%PY_EXE%" -u -m uvicorn main:app --host 0.0.0.0 --port 8080 >> "%~dp0logs\_api_run.log" 2>&1
set RC=%errorlevel%

echo.
echo  MES API exited rc=%RC% at %DATE% %TIME% (attempt #%ATTEMPT%)

if exist "%STOP_FLAG%" (
    echo  STOP_API.flag present - auto-restart SUPPRESSED.
    echo  Delete "%STOP_FLAG%" to re-arm, then relaunch.
    pause
    exit /b 0
)

echo  Auto-restart in 5 seconds (handles socket-still-closing on fast restart).
ping 127.0.0.1 -n 6 >nul
goto restart
