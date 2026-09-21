$token = (Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/auth/login' -Method POST -ContentType 'application/json' -Body '{"username":"admin","password":"TbAdmin@2024!"}').data.token
if (-not $token) { Write-Host 'LOGIN FAILED - is Flask running?' -ForegroundColor Red; exit 1 }
Write-Host "=== PLC BIT MONITOR (2 min) ===" -ForegroundColor Cyan
Write-Host "Time`t`tPLC`t`t`tBit`tValue`tEvent"
Write-Host ('-' * 80)

$prev = @{}
$startTime = Get-Date
$risingCount = 0

while ((Get-Date) - $startTime -lt [TimeSpan]::FromMinutes(2)) {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/plc-live-status' `
            -Headers @{Authorization="Bearer $token"} -ErrorAction Stop
        $now = Get-Date -Format 'HH:mm:ss'
        foreach ($plc in $r.data) {
            $label = if ($plc.description) { $plc.description } else { $plc.ip }
            foreach ($b in $plc.bits) {
                $key = "$($plc.id)|$($b.bit)"
                $val = if ($b.value -eq $true) { 'ON' } elseif ($b.value -eq $false) { 'OFF' } else { '---' }
                $prevVal = $prev[$key]
                if ($prevVal -ne $val) {
                    if ($prevVal -eq 'OFF' -and $val -eq 'ON') {
                        Write-Host "$now`t$label`t$($b.bit)`t$val`t>>> RISING EDGE - CYCLE TRIGGERED <<<" -ForegroundColor Green
                        $risingCount++
                    } elseif ($prevVal -eq 'ON' -and $val -eq 'OFF') {
                        Write-Host "$now`t$label`t$($b.bit)`t$val`tFalling edge" -ForegroundColor Yellow
                    } elseif ($prevVal) {
                        Write-Host "$now`t$label`t$($b.bit)`t$val`t$prevVal -> $val" -ForegroundColor Cyan
                    } else {
                        # First read - just show initial state
                        $conn = if ($plc.connected) { "connected" } else { "OFFLINE" }
                        Write-Host "$now`t$label`t$($b.bit)`t$val`tInitial state ($conn)" -ForegroundColor Gray
                    }
                    $prev[$key] = $val
                }
            }
        }
    } catch {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')`tERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
    Start-Sleep -Milliseconds 500
}
Write-Host ""
Write-Host ('=' * 80)
Write-Host "=== SUMMARY: $risingCount rising edge(s) detected in 2 minutes ===" -ForegroundColor Cyan
