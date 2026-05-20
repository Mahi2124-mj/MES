@echo off
title Poka Yoke Management System
cd /d "%~dp0"

echo Stopping any running servers...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":5000" ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":3000" ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 1 /nobreak >nul

echo.
echo Starting Poka Yoke UI...
echo Backend:  http://localhost:5000
echo Frontend: http://localhost:3000
echo.

start "PY Backend"  cmd /k "cd /d "%~dp0" && node server.js"
timeout /t 2 /nobreak >nul
start "PY Frontend" cmd /k "cd /d "%~dp0" && npx vite --port 3000"
timeout /t 5 /nobreak >nul
start http://localhost:3000
