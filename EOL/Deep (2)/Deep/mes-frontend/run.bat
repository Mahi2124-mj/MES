@echo off
setlocal
cd /d "%~dp0"

REM --- Check Node.js / npm ----------------------------------------------
where npm >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] Node.js / npm not found in PATH.
    echo          Install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)

REM --- First-time install if node_modules missing -----------------------
if not exist "node_modules" (
    echo.
    echo  node_modules not found - running npm install (one-time) ...
    call npm install
    if errorlevel 1 (
        echo  [ERROR] npm install failed.
        pause
        exit /b 1
    )
)

echo.
echo  ================================================================
echo    MES Frontend  ^|  http://localhost:5656  (Ctrl+C to stop)
echo    Backend proxy:  /api -^> :8080   /cms-api -^> :5555
echo  ================================================================
echo.

call npm run dev
