@echo off
title CMS-API
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
echo === CMS API (Flask :5555) ===
echo Python: C:\Program Files\Python312\python.exe
echo.
set "PY312=C:\PROGRA~1\PYTHON~1\python.exe"
if not exist "%PY312%" (
    echo [FATAL] Python 3.12 not found at C:\Program Files\Python312
    pause
    exit /b 1
)
"%PY312%" api_server.py
echo.
echo === API exited (rc=%errorlevel%) ===
pause >nul
