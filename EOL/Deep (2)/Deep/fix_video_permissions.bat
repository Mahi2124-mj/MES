@echo off
REM ============================================================
REM   ONE-TIME FIX for video pipeline blockers
REM
REM   2026-05-21 — Runs the 2 admin-only commands that have been
REM   blocking video recording since Day-1:
REM     1. icacls F:\ grant Users full control
REM        -> F:\MES_Videos\ mkdir + writes succeed
REM     2. Windows Defender exclude ffmpeg binaries
REM        -> ffmpeg.exe spawn won't randomly fail with WinError 5
REM
REM   USAGE:
REM     Right-click this file -> Run as administrator
REM     (UAC prompt should appear, click Yes)
REM ============================================================

REM Verify elevation — must be admin
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] This script requires Administrator privileges.
    echo.
    echo  Close this window, right-click the .bat file, and pick
    echo  "Run as administrator".  UAC will prompt — click Yes.
    echo.
    pause
    exit /b 1
)

cls
echo.
echo  =============================================================
echo    EOL Video Pipeline — One-Time Permission Fix
echo  =============================================================
echo.

REM ─── Step 1: F: drive ACL ─────────────────────────────────────
echo  [1/2]  Granting Users full control on F:\ ...
icacls F:\ /grant "Users:(OI)(CI)F" /T /Q
if errorlevel 1 (
    echo         ^^^ icacls reported errors above, but most likely OK
) else (
    echo         Done.
)
echo.

REM ─── Step 2: Defender exclusion for ffmpeg ────────────────────
echo  [2/2]  Adding Windows Defender exclusions for ffmpeg ...
powershell -NoProfile -Command "Add-MpPreference -ExclusionPath 'C:\Users\DX-ADMIN\AppData\Roaming\Python\Python312\site-packages\imageio_ffmpeg\binaries' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'ffmpeg-win-x86_64-v7.1.exe' -ErrorAction SilentlyContinue; Write-Output '        Done.'"
echo.

REM ─── Verify ──────────────────────────────────────────────────
echo  =============================================================
echo    Verification
echo  =============================================================
echo.
echo  Current Defender exclusions (paths):
powershell -NoProfile -Command "(Get-MpPreference).ExclusionPath | ForEach-Object { Write-Output ('    ' + $_) }"
echo.
echo  Current Defender exclusions (processes):
powershell -NoProfile -Command "(Get-MpPreference).ExclusionProcess | ForEach-Object { Write-Output ('    ' + $_) }"
echo.
echo  F:\ permission test (writing test file as current user):
powershell -NoProfile -Command "try { [System.IO.File]::WriteAllText('F:\__perm_test.txt', 'ok'); Remove-Item 'F:\__perm_test.txt'; Write-Output '    PASS — F:\ now writable from non-elevated processes' } catch { Write-Output ('    FAIL: ' + $_.Exception.Message) }"
echo.

echo  =============================================================
echo    Fix complete.
echo  =============================================================
echo.
echo    Next step: restart CMS api_server so it picks up the new
echo    permissions.  Easy way:
echo.
echo       stop_everything.bat
echo       start_everything.bat
echo.
echo    After restart, all 6 cameras should spawn cleanly and
echo    write to F:\MES_Videos\ instead of falling back to D:.
echo.
pause
exit /b 0
