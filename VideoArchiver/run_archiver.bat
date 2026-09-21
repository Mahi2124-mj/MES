@echo off
REM ============================================================
REM  MES Video Archiver - never-die wrapper (line 2 / Final Inspection)
REM  Restarts `video_archiver.py run` if it ever exits. ZERO touch to
REM  the collector / MES-API / NF2 - separate process, own table+folder.
REM ============================================================
set "PY=D:\EOL\EOL\Deep (2)\Deep\Phase2\.venv\Scripts\python.exe"
set "SCRIPT=D:\EOL\VideoArchiver\video_archiver.py"
set "LOG=D:\EOL\VideoArchiver\archiver.log"

:loop
echo [%date% %time%] starting archiver run-loop >> "%LOG%"
"%PY%" "%SCRIPT%" run >> "%LOG%" 2>&1
echo [%date% %time%] archiver exited (code %errorlevel%), restart in 5s >> "%LOG%"
timeout /t 5 /nobreak >nul
goto loop
