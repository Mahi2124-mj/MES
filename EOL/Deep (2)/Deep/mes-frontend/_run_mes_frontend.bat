@echo off
title MES-Frontend
cd /d "%~dp0"
echo === MES Frontend (Vite :5656) ===
echo.
call npm run dev
echo.
echo === Vite exited (rc=%errorlevel%) ===
pause >nul
