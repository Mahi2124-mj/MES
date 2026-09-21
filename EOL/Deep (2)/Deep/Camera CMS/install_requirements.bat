@echo off
setlocal
cd /d "%~dp0"

echo.
echo  Installing all dependencies...
echo ─────────────────────────────────────────

REM ─── Detect Python ────────────────────────────────────────────────
if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
    echo  [Python] Using virtual environment: .venv
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=python"
        echo  [Python] Using system Python
    ) else (
        where python3 >nul 2>nul
        if not errorlevel 1 (
            set "PY_EXE=python3"
            echo  [Python] Using python3
        ) else (
            echo  [ERROR] Python not found. Install Python 3.9+ from https://python.org
            pause
            exit /b 1
        )
    )
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
