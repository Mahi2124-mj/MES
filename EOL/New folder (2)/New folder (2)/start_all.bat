@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

echo.
echo  =====================================================
echo    Toyota Boshoku  ^|  Camera EMS Portal
echo    Starting All Services...
echo  =====================================================
echo.

REM ─── Python detect (global only — old .venv was tied to Py 3.14 / dead user)
set "PY="
where python >nul 2>nul && set "PY=python" && echo  [OK] Python  :  system python
if not defined PY (
    where py >nul 2>nul && set "PY=py -3.12" && echo  [OK] Python  :  via py launcher (3.12)
)
if not defined PY (
    where python3 >nul 2>nul && set "PY=python3" && echo  [OK] Python  :  python3
)
if not defined PY (
    echo  [ERROR] Python not found. Install Python 3.12 from https://python.org
    pause & exit /b 1
)

REM ─── Node.js detect ───────────────────────────────────────────────
where npm >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] Node.js / npm not found. Install from https://nodejs.org
    pause & exit /b 1
)
echo  [OK] Node.js :  found

echo.
echo  -------------------------------------------------------
echo  [1/5]  Installing Python packages...
echo  -------------------------------------------------------
"%PY%" -m pip install -r "%ROOT%backend\requirements.txt" --quiet --no-warn-script-location
"%PY%" -m pip install imageio-ffmpeg --quiet --no-warn-script-location
echo        Done.

echo.
echo  -------------------------------------------------------
echo  [2/5]  Installing frontend packages...
echo  -------------------------------------------------------
pushd "%ROOT%frontend"
call npm install --silent
set _ERR=%errorlevel%
popd
if %_ERR% neq 0 ( echo  [ERROR] npm install failed & pause & exit /b 1 )
echo        Done.

REM ─── Kill leftover processes from previous session ────────────────
echo.
echo  -------------------------------------------------------
echo  [3/5]  Cleaning up stale processes...
echo  -------------------------------------------------------
taskkill /IM "ffmpeg-win-x86_64-v7.1.exe" /F >nul 2>nul
taskkill /IM "ffmpeg.exe" /F >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5555 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5575 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>nul
)
echo        Done.

echo.
echo  -------------------------------------------------------
echo  [4/5]  Starting Flask API  (port 5555)...
echo  -------------------------------------------------------
start "TB-API" cmd /k "title TB-API  ^|  Flask API && "%PY%" "%ROOT%backend\api_server.py""
timeout /t 5 /nobreak >nul
echo        Running.

echo.
echo  -------------------------------------------------------
echo  [5/5]  Starting React Frontend  (port 5575)...
echo  -------------------------------------------------------
start "TB-Frontend" cmd /k "title TB-Frontend  ^|  React Dev && pushd "%ROOT%frontend" && npm run dev -- --host 0.0.0.0"
timeout /t 5 /nobreak >nul
echo        Running.

REM ─── Open browser ─────────────────────────────────────────────────
start "" "http://127.0.0.1:5575"

echo.
echo  =====================================================
echo    All Services Started!
echo  =====================================================
echo.
echo    Frontend   :  http://127.0.0.1:5575
echo    API        :  http://127.0.0.1:5555
echo    Camera     :  192.168.10.115:554  (RTSP)
echo    PLC        :  192.168.10.150:5002 (MC Protocol)
echo.
echo  -------------------------------------------------------
echo    Login Credentials:
echo      admin       /  TbAdmin@2024!
echo      supervisor  /  TbSuper@2024!
echo      operator    /  TbOper@2024!
echo  -------------------------------------------------------
echo.
echo  Press any key to close this window.
echo  (Services will keep running in the background)
pause >nul
