# start-backend.ps1
# Safe backend launcher: kill anything on 8080, kill stray uvicorn,
# clean pycache, then start uvicorn in foreground.

Write-Host "=== MES Backend Launcher ===" -ForegroundColor Cyan

# Step 1: kill anything on port 8080
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "Port 8080 is HELD - killing..." -ForegroundColor Yellow
    $pids = $conn.OwningProcess | Sort-Object -Unique
    foreach ($p in $pids) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Host "  killed PID $p" -ForegroundColor Green
    }
}
else {
    Write-Host "Port 8080 is free." -ForegroundColor Green
}

# Step 2: kill any stray uvicorn processes
$strays = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*uvicorn*" }
if ($strays) {
    Write-Host "Killing stray uvicorn processes..." -ForegroundColor Yellow
    foreach ($s in $strays) {
        Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  killed PID $($s.ProcessId)" -ForegroundColor Green
    }
}

# Step 3: clean __pycache__
$caches = @(
    "D:\EOL\EOL\Deep (2)\Deep\Phase2\__pycache__",
    "D:\EOL\EOL\Deep (2)\Deep\Phase2\routers\__pycache__"
)
foreach ($c in $caches) {
    if (Test-Path $c) {
        Remove-Item -Recurse -Force $c -ErrorAction SilentlyContinue
    }
}

# Step 4: wait for kernel TCP release
Start-Sleep -Seconds 2

# Step 5: start uvicorn (foreground, no --reload)
Write-Host ""
Write-Host "Starting uvicorn on http://0.0.0.0:8080 ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop cleanly." -ForegroundColor Gray
Write-Host ""

Set-Location "D:\EOL\EOL\Deep (2)\Deep\Phase2"
python -m uvicorn main:app --host 0.0.0.0 --port 8080
