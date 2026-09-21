@echo off
title CMS-Frontend
cd /d "%~dp0"
echo === CMS Frontend (Vite :5575) ===
echo.
call npm run dev -- --host 0.0.0.0
echo.
echo === Vite exited (rc=%errorlevel%) ===
pause >nul
