@echo off
setlocal
cd /d "%~dp0"

echo.
echo  =====================================================
echo    Toyota Boshoku CMS — Stop All Services
echo  =====================================================
echo.

echo  Stopping known EMS ports and windows...
echo.

REM ─── Kill by window titles ───────────────────────────────────────
taskkill /FI "WINDOWTITLE eq TB-API*" /F /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq TB-Streams*" /F /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq TB-Recorder*" /F /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq TB-Frontend*" /F /T >nul 2>nul

REM ─── Kill by ports (fallback) ────────────────────────────────────
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8050" ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)

echo.
echo  All CMS services stopped.
echo.
pause
