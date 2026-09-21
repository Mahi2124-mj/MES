@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ================================================================
echo    Phase2 Backend - Setup (installs deps to global Python)
echo  ================================================================
echo.

REM --- Detect a usable Python -------------------------------------------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3.12"
)
if not defined PY (
    echo  [ERROR] Python not found.
    echo          Install Python 3.12 from https://www.python.org/downloads/
    echo          During install, tick "Add Python to PATH".
    pause
    exit /b 1
)

echo  [OK] Using: %PY%
%PY% --version

echo.
echo  Installing requirements.txt to global Python (user-site) ...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo  [ERROR] pip install failed.
    pause
    exit /b 1
)

echo.
echo  ================================================================
echo    Setup complete.
echo    Run:  python main.py
echo      or: run.bat
echo    Backend will listen on http://0.0.0.0:8080
echo  ================================================================
pause
