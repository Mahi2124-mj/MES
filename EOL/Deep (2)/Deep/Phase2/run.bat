@echo off
setlocal
cd /d "%~dp0"

REM ─────────────────────────────────────────────────────────────────
REM  Phase2 Backend launcher
REM  Auto-kills any stale instance still bound to port 8080 before
REM  starting fresh.  This avoids the daily Errno 10048 ("only one
REM  usage of each socket address") that happens when the previous
REM  run didn't shut down cleanly (laptop sleep / Ctrl+C while uvicorn
REM  was mid-write / SSH disconnect, etc).
REM ─────────────────────────────────────────────────────────────────

REM --- Pick the best Python: prefer 'python' on PATH, fallback to 'py -3.12' --
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3.12"
)
if not defined PY (
    echo  [ERROR] Python not found. Run setup.bat first or install Python 3.12.
    pause
    exit /b 1
)

REM --- Port-8080 sweep: find any LISTENING socket and kill the owner --
set "OLD_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:"[0:]:8080 .*LISTENING"') do (
    set "OLD_PID=%%a"
)

if defined OLD_PID (
    echo.
    echo  [INFO] Found stale Phase2 instance on port 8080 ^(PID %OLD_PID%^).
    echo  [INFO] Killing it...
    taskkill /F /PID %OLD_PID% >nul 2>nul
    REM Give Windows a moment to release the socket from TIME_WAIT.
    timeout /t 2 /nobreak >nul
)

echo.
echo  ================================================================
echo    Phase2 Backend  ^|  http://0.0.0.0:8080  (Ctrl+C to stop)
echo  ================================================================
echo.

%PY% main.py
