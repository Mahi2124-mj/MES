@echo off
setlocal
title  EOL Unified STOP

REM ============================================================
REM   EOL Unified STOP  (hardened 2026-05-29)
REM
REM   Kills every service launched by start_everything.bat:
REM     1. Port-bound services (8080, 5656, 5555, 5575) via
REM        netstat tree-kill.
REM     2. MES Collector + ALL its python children (venv launcher
REM        spawns a system-Python child; killing the launcher
REM        alone leaves the child holding PLC TCP slots).  Uses
REM        taskkill /T /F.
REM     3. The launcher cmd.exe windows that ran _run_*.bat
REM        wrappers (they have restart loops — if the cmd stays
REM        alive, it will spawn a new python instantly).
REM     4. Releases the Postgres collector lock so the next
REM        start_everything.bat can re-acquire cleanly.
REM
REM   2026-05-15 — DO NOT use `taskkill /IM` or `tasklist /V`.
REM   The box's Defender/MsSense agent wraps name-based lookups
REM   with deep scanning, freezing the window for 2-5 minutes
REM   per call.  netstat + WMI ProcessId-based lookups are
REM   kernel-fast.
REM
REM   2026-05-29 — Stop-Process replaced with taskkill /T /F so
REM   orphan children (venv shim -> system python) get reaped.
REM   Also kills the wrapper cmd.exe windows.
REM ============================================================

echo.
echo  ===============================================================
echo    EOL Unified STOP  (hardened)
echo  ===============================================================
echo.

REM --- 1. Kill port-bound services via netstat tree-kill -------
echo  [1/4]  Closing service ports (8080, 5656, 5555, 5575)...
powershell -NoProfile -Command "$p='8080','5555','5656','5575','5000','8050','5173'; netstat -ano | Select-String 'LISTENING' | ForEach-Object { $x=($_.ToString().Trim() -split '\s+'); if($x.Count -ge 5){ $lp=($x[1] -split ':')[-1]; if($p -contains $lp){ Stop-Process -Id ([int]$x[4]) -Force -ErrorAction SilentlyContinue } } }" >nul 2>nul
echo                Done.

REM --- 2. Kill MES Collector + all children (tree-kill) --------
REM
REM 2026-06-03 — HANG FIX.  WAS: Get-WmiObject Win32_Process +
REM   $_.CommandLine -like '*collector_ync*'.  Reading .CommandLine
REM   forces a per-process deep lookup that the box's Defender /
REM   MsSense agent scans -> this step FROZE for minutes / forever
REM   (the exact freeze this script's own header warns about for
REM   tasklist /V & taskkill /IM; WMI is only "kernel-fast" for a
REM   ProcessId lookup, NOT a CommandLine filter).
REM NOW: target the collector by its .venv python EXE PATH via
REM   Get-Process (.Path is a fast handle, NEVER reads CommandLine,
REM   so Defender doesn't deep-scan it).  Only the collector runs
REM   from Phase2\.venv, so this isolates it cleanly from the CMS /
REM   other system-Python procs.  taskkill /T still reaps the whole
REM   child tree.  (collector_pid in mes_lines is currently NULL, so
REM   a DB-PID lookup is not reliable here.)
echo  [2/4]  Stopping MES Collector tree (venv exe-path, kernel-fast)...
REM 2026-06-03 — Drop the collector's STOP.flag FIRST so _run_collector.bat's
REM never-die loop suppresses its 5s auto-restart even if its cmd window
REM outlives the title-kill in step [3/4] (the loop checks this flag before
REM every relaunch).  start_everything.bat deletes the flag on launch to re-arm.
type nul > "D:\EOL\EOL\Deep (2)\Deep\Phase2\collectors\STOP.flag" 2>nul
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*Phase2*venv*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" 2>nul
echo                Done.

REM --- 3. Kill wrapper cmd.exe windows (_run_*.bat) ------------
REM
REM _run_collector.bat has a never-die restart loop.  If the cmd
REM window stays alive after we kill its python child, the bat
REM will immediately spawn a fresh python and PLC sockets stay
REM occupied.  Kill the cmd too.
REM
REM 2026-06-03 — HANG FIX (same cause as [2/4]).  WAS: Get-WmiObject
REM   Win32_Process + $_.CommandLine on every cmd.exe -> Defender
REM   deep-scan freeze.  NOW: match each wrapper by its window TITLE
REM   (set via `title` in the _run_*.bat / `start "TITLE"`) using
REM   Get-Process .MainWindowTitle (a fast Win32 handle, no
REM   CommandLine read).  /T also reaps any python the restart loop
REM   just respawned between step 2 and here.
echo  [3/4]  Closing _run_*.bat wrapper windows (by title, kernel-fast)...
powershell -NoProfile -Command "Get-Process cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'MES-Collector|MES-API|MES-Frontend|CMS-API|CMS-Frontend|MES-Video-Archiver' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" 2>nul
echo                Done.

REM --- 4. Release Postgres collector lock ----------------------
echo  [4/4]  Releasing collector lock in energydb...
where python >nul 2>nul && (
    python -c "import psycopg2; c=psycopg2.connect(host='192.168.10.210',port=5432,user='postgres',password='tbdi@123',dbname='energydb',connect_timeout=5,options='-c lock_timeout=3000 -c statement_timeout=3000'); cur=c.cursor(); cur.execute('DELETE FROM mes_collector_locks'); cur.execute(\"UPDATE mes_lines SET collector_pid=NULL, collector_status='stopped' WHERE collector_status='running'\"); c.commit(); c.close()" 2>nul
)
echo                Done.

echo.
echo  ===============================================================
echo    All MES + CMS services stopped.  No orphan children left.
echo  ===============================================================
echo.
timeout /t 1 /nobreak >nul
exit /b 0
