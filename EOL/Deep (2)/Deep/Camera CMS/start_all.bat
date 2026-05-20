@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

REM ============================================================
REM   Toyota Boshoku Camera CMS - Start All Services
REM ============================================================
REM
REM Storage path:
REM   Recorded cycle videos go to %VIDEOS_DIR% below.  Default is the
REM   external HDD on F:\.  If F:\ is not present, recorder will fall
REM   back to settings.json (set via UI) or backend\videos\.
REM
REM   Edit the line below to point to a different drive/folder, OR
REM   leave it blank and configure via the UI:
REM     Configuration -> System Settings -> Video Storage Path
REM ============================================================
set "VIDEOS_DIR=F:\CameraCMS_Videos"

REM Auto-create the videos folder if the drive is reachable.  Silent on
REM failure so the launcher continues even when the HDD isn't plugged.
if not "%VIDEOS_DIR%"=="" (
    if not exist "%VIDEOS_DIR%" mkdir "%VIDEOS_DIR%" >nul 2>nul
)

echo.
echo  =====================================================
echo    Toyota Boshoku CMS - Start All Services
echo  =====================================================
echo    Video storage : %VIDEOS_DIR%
echo  =====================================================
echo.

REM --- Check Node.js -------------------------------------------------
where npm >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] Node.js / npm not found in PATH.
    echo          Please install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)

REM --- Detect Python executable --------------------------------------
REM
REM Why we VALIDATE the .venv: a virtual env created on another machine
REM (or under another user account) holds a pyvenv.cfg that points to a
REM specific python.exe path.  When we copy/move the project, that path
REM no longer exists -> .venv\Scripts\python.exe loads but every command
REM dies with "did not find executable at 'C:\Users\OTHER\...'".
REM
REM We use GOTO instead of nested if/else because cmd.exe parses the
REM whole nested block at once and `errorlevel` inside the block uses
REM the OUTER level, not the result of the venv probe just above.
set "PY_EXE="
if not exist ".venv\Scripts\python.exe" goto :CHECK_SYSTEM_PY
REM The .venv launcher (when its pyvenv.cfg points at a missing host python)
REM actually exits with code 0 even though it prints an error to stderr.
REM So `if errorlevel 1` doesn't catch it.  Instead we capture stdout and
REM check for the sentinel string we asked python to print.
".venv\Scripts\python.exe" -c "print('VENV_OK')" > "%TEMP%\tb_pyck.out" 2>&1
findstr /B /C:"VENV_OK" "%TEMP%\tb_pyck.out" >nul
if errorlevel 1 (
    echo  [WARN] .venv\Scripts\python.exe is broken
    echo         ^(created on another machine - pyvenv.cfg path stale^).
    echo         Falling back to system Python.
    del "%TEMP%\tb_pyck.out" >nul 2>nul
    goto :CHECK_SYSTEM_PY
)
del "%TEMP%\tb_pyck.out" >nul 2>nul
set "PY_EXE=%ROOT%.venv\Scripts\python.exe"
echo  [OK] Using virtual environment: .venv
goto :PY_DETECTED

:CHECK_SYSTEM_PY
where python >nul 2>nul
if errorlevel 1 goto :CHECK_PYTHON3
set "PY_EXE=python"
echo  [OK] Using system Python
goto :PY_DETECTED

:CHECK_PYTHON3
where python3 >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python 3.9+ and add to PATH.
    pause
    exit /b 1
)
set "PY_EXE=python3"
echo  [OK] Using python3

:PY_DETECTED

echo.
echo  [1/6] Installing Python requirements...
"%PY_EXE%" -m pip install -r "%ROOT%backend\requirements.txt" --quiet
if errorlevel 1 (
    echo  [ERROR] Failed to install Python requirements.
    pause
    exit /b 1
)
echo        Done.

echo.
echo  [2/6] Installing frontend dependencies...
pushd "%ROOT%frontend"
call npm install --silent
set "NPM_ERR=%errorlevel%"
popd
if not "%NPM_ERR%"=="0" (
    echo  [ERROR] npm install failed.
    pause
    exit /b 1
)
echo        Done.

REM ============================================================
REM   Service launches.
REM
REM   Pattern: `start "TitleBar" /D "<workdir>" cmd /k <command>`
REM
REM   The /D flag sets the new console's working directory BEFORE
REM   parsing the command, so we don't need a `pushd "..." && ...`
REM   chain inside the cmd /k argument.  That chain was the bug in
REM   the previous version: nested quotes around a path with spaces
REM   ("Camera CMS") got chopped by cmd's quote parser, leaving the
REM   frontend window with cwd = "D:\EOL\EOL\Deep" and npm dying
REM   with "package.json not found".
REM ============================================================

REM <nul on each start: when start_all.bat is invoked with its stdin
REM redirected (e.g. piped to a log file via `bat > log 2>&1`), the
REM spawned cmd /k would otherwise inherit the closed stdin handle and
REM die with "Input redirection is not supported, exiting the process
REM immediately."  Feeding <nul to the start command detaches the new
REM window's stdin so the bat works in BOTH double-click mode AND
REM logged-pipeline mode.

echo.
echo  [3/5] Starting Flask REST API on port 5000...
start "TB-API" /D "%ROOT%backend" cmd /k ""%PY_EXE%" api_server.py" <nul
timeout /t 3 /nobreak >nul

echo.
echo  [4/5] Starting MJPEG Camera Streams + cycle recorder on port 8050...
start "TB-Streams" /D "%ROOT%backend" cmd /k ""%PY_EXE%" dashboard_legacy.py" <nul
timeout /t 3 /nobreak >nul

REM NOTE: recorder.py is a STANDALONE CLI tool (cv2.imshow window with
REM s/e/q keys for manual cycle control during development).  It is
REM NOT used in production — dashboard_legacy.py handles all real-time
REM cycle recording via the UI / API.  We deliberately do NOT launch
REM recorder.py here; doing so just opens an unwanted live-camera
REM popup window on the host machine.

echo.
echo  [5/5] Starting React frontend on port 5173...
start "TB-Frontend" /D "%ROOT%frontend" cmd /k "npm run dev -- --host 0.0.0.0" <nul
timeout /t 4 /nobreak >nul

REM --- Open browser --------------------------------------------------
start "" "http://127.0.0.1:5173"

echo.
echo  =====================================================
echo    All services started!
echo  =====================================================
echo    Frontend  :  http://127.0.0.1:5173
echo    API       :  http://127.0.0.1:5000
echo    Streams   :  http://127.0.0.1:8050
echo    Videos    :  %VIDEOS_DIR%
echo  =====================================================
echo.
echo  Default login credentials:
echo    admin      / admin123
echo    supervisor / super123
echo    operator   / oper123
echo.
echo  Press any key to exit this window (services keep running).
pause >nul
