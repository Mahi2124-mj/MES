# ─────────────────────────────────────────────────────────────────
# kill-all.ps1
# Quick cleanup script — kill stuck MES processes + free ports.
#
# Usage:  .\kill-all.ps1
#
# Kills (in order):
#   1. Anything listening on 8080 / 5656 / 5555
#   2. Stray python.exe processes (uvicorn workers, collectors)
#   3. Cleans Python __pycache__ directories
# ─────────────────────────────────────────────────────────────────

Write-Host "=== MES Kill-All Cleanup ===" -ForegroundColor Cyan

# Step 1: Kill anything on each known MES port
@(
    @{port=8080; name="uvicorn backend"},
    @{port=5656; name="vite frontend"},
    @{port=5555; name="camera CMS"}
) | ForEach-Object {
    $port = $_.port; $name = $_.name
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "[$port] $name — killing PIDs..." -ForegroundColor Yellow
        $conn.OwningProcess | Sort-Object -Unique | ForEach-Object {
            try { Stop-Process -Id $_ -Force -ErrorAction Stop; Write-Host "  ✓ killed PID $_" -ForegroundColor Green }
            catch { Write-Host "  ✗ PID $_ : $($_.Exception.Message)" -ForegroundColor Red }
        }
    } else {
        Write-Host "[$port] $name — already free" -ForegroundColor Green
    }
}

# Step 2: Kill stray python.exe (multiprocessing workers, collectors)
Write-Host "`nKilling stray python.exe / pythonw.exe..." -ForegroundColor Yellow
$py = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue
if ($py) {
    $py | ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host "  ✓ killed PID $($_.ProcessId)" -ForegroundColor Green }
        catch {}
    }
} else {
    Write-Host "  none alive" -ForegroundColor Green
}

# Step 3: Wipe __pycache__ so freshly-edited collector code loads
Write-Host "`nCleaning __pycache__..." -ForegroundColor Yellow
@(
    "D:\EOL\EOL\Deep (2)\Deep\Phase2\__pycache__",
    "D:\EOL\EOL\Deep (2)\Deep\Phase2\collectors\__pycache__",
    "D:\EOL\EOL\Deep (2)\Deep\Phase2\routers\__pycache__",
    "D:\EOL\EOL\Deep (2)\Deep\Phase3\__pycache__"
) | ForEach-Object {
    if (Test-Path $_) {
        Remove-Item -Recurse -Force $_ -ErrorAction SilentlyContinue
        Write-Host "  ✓ removed $_" -ForegroundColor Green
    }
}

# Step 4: Verify
Start-Sleep -Seconds 1
Write-Host "`n=== Final port check ===" -ForegroundColor Cyan
@(8080, 5656, 5555) | ForEach-Object {
    $c = Get-NetTCPConnection -LocalPort $_ -ErrorAction SilentlyContinue
    if ($c) { Write-Host "  Port $_ : ⚠ STILL HELD by PID $($c.OwningProcess)" -ForegroundColor Red }
    else    { Write-Host "  Port $_ : ✓ FREE" -ForegroundColor Green }
}

Write-Host "`n=== Done.  Restart backend / collector / frontend now. ===" -ForegroundColor Cyan
