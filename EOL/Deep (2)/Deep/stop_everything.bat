@echo off
setlocal
title  EOL Unified STOP

REM ============================================================
REM   EOL Unified STOP
REM
REM   Kills every service launched by start_everything.bat:
REM     1. Port-bound services (8080, 5656, 5555, 5575) — fast
REM        kill via netstat lookup.
REM     2. MES Collector (no port — kill by command-line match
REM        via a SINGLE PowerShell call so we don't trigger the
REM        endpoint-protection agent's per-process scan loop).
REM     3. Releases the Postgres collector lock so the next
REM        start_everything.bat can re-acquire cleanly.
REM
REM   2026-05-15 — DO NOT use `taskkill /IM` or `tasklist /V`.
REM   The box's Defender/MsSense agent wraps name-based lookups
REM   with deep scanning, freezing the window for 2-5 minutes
REM   per call.  netstat + one WMI shot are both kernel-fast.
REM ============================================================

echo.
echo  ===============================================================
echo    EOL Unified STOP
echo  ===============================================================
echo.

REM ─── 1. Kill port-bound services ──────────────────────────────
echo  [1/3]  Closing service ports (8080, 5656, 5555, 5575)...
for %%P in (8080 5656 5555 5575 5000 8050 5173) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        taskkill /PID %%a /F /T >nul 2>nul
    )
)
echo                Done.

REM ─── 2. Kill MES Collector (port-less Python script) ─────────
REM
REM ONE PowerShell call (Get-WmiObject is faster than Get-Process
REM here because it filters by CommandLine in a single WMI shot
REM instead of enumerating + asking each process for its argv).
echo  [2/3]  Stopping MES Collector (command-line match)...
powershell -NoProfile -Command "Get-WmiObject Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*collector_ync*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
echo                Done.

REM ─── 3. Release Postgres collector lock ──────────────────────
REM
REM Bounded with timeouts so we never hang if DB is unreachable.
REM Without this, the next start_everything.bat sees a leftover
REM lock row and the new collector refuses to start.
echo  [3/3]  Releasing collector lock in energydb...
where python >nul 2>nul && (
    python -c "import psycopg2; c=psycopg2.connect(host='192.168.10.210',port=5432,user='postgres',password='tbdi@123',dbname='energydb',connect_timeout=5,options='-c lock_timeout=3000 -c statement_timeout=3000'); cur=c.cursor(); cur.execute('DELETE FROM mes_collector_locks'); cur.execute(\"UPDATE mes_lines SET collector_pid=NULL, collector_status='stopped' WHERE collector_status='running'\"); c.commit(); c.close()" 2>nul
)
echo                Done.

echo.
echo  ===============================================================
echo    All MES + CMS services stopped.
echo  ===============================================================
echo.
timeout /t 1 /nobreak >nul
exit /b 0
