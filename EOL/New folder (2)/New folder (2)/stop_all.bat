@echo off
setlocal
cd /d "%~dp0"

echo.
echo  =====================================================
echo    Toyota Boshoku  ^|  Camera EMS Portal
echo    Stopping All Services...
echo  =====================================================
echo.

REM ─── Kill by window title ─────────────────────────────────────────
echo  Stopping TB-API...
taskkill /FI "WINDOWTITLE eq TB-API*" /F /T >nul 2>nul

echo  Stopping TB-Frontend...
taskkill /FI "WINDOWTITLE eq TB-Frontend*" /F /T >nul 2>nul

REM ─── Kill by ports (fallback) ─────────────────────────────────────
echo  Releasing ports...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5555 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5575 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8050 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)

REM ─── Kill stale ffmpeg recorders ─────────────────────────────────
echo  Stopping ffmpeg recorders...
taskkill /IM "ffmpeg-win-x86_64-v7.1.exe" /F >nul 2>nul
taskkill /IM "ffmpeg.exe" /F >nul 2>nul

echo.
echo  =====================================================
echo    All Services Stopped.
echo  =====================================================
echo.
timeout /t 2 /nobreak >nul
