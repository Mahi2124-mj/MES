@echo off
title MES-API
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
echo === MES API (uvicorn :8080) ===
echo Python: .venv\Scripts\python.exe
echo.
if not exist ".venv\Scripts\python.exe" (
    echo [FATAL] Phase2 venv missing.  Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u -m uvicorn main:app --host 0.0.0.0 --port 8080
echo.
echo === API exited (rc=%errorlevel%) ===
pause >nul
