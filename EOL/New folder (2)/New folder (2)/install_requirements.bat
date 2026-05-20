@echo off
setlocal
cd /d "%~dp0"

echo.
echo  Installing all dependencies...
echo ─────────────────────────────────────────

REM ─── Detect Python (global only) ──────────────────────────────────
set "PY_EXE="
where python >nul 2>nul && set "PY_EXE=python" && echo  [Python] system python
if not defined PY_EXE (
    where py >nul 2>nul && set "PY_EXE=py -3.12" && echo  [Python] py launcher (3.12)
)
if not defined PY_EXE (
    where python3 >nul 2>nul && set "PY_EXE=python3" && echo  [Python] python3
)
if not defined PY_EXE (
    echo  [ERROR] Python not found. Install Python 3.12 from https://python.org
    pause
    exit /b 1
)

echo.
echo  [1/2] Installing Python packages...
"%PY_EXE%" -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo  [ERROR] pip install failed.
    echo          Check your internet connection or proxy settings.
    pause
    exit /b 1
)

echo.
echo  ─────────────────────────────────────────
echo.

REM ─── Check Node.js ────────────────────────────────────────────────
where npm >nul 2>nul
if errorlevel 1 (
    echo  [SKIP] npm not found — skipping frontend install.
    echo         Install Node.js 18+ from https://nodejs.org to enable frontend.
    echo.
    pause
    exit /b 0
)

echo  [2/2] Installing frontend npm packages...
call npm --prefix frontend install
if errorlevel 1 (
    echo.
    echo  [ERROR] npm install failed.
    pause
    exit /b 1
)

echo.
echo  ─────────────────────────────────────────
echo  All installations complete!
echo  Run start_all.bat to launch the system.
echo  ─────────────────────────────────────────
echo.
pause
