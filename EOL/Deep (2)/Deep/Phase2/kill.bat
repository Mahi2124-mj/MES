@echo off
REM Kill any stuck Python/Node/uvicorn processes from Phase2 + mes-frontend
echo.
echo  Killing python.exe, node.exe, uvicorn ...
taskkill /F /IM python.exe   2>nul
taskkill /F /IM python3.exe  2>nul
taskkill /F /IM uvicorn.exe  2>nul
taskkill /F /IM node.exe     2>nul
taskkill /F /IM npm.exe      2>nul

echo.
echo  Killing anything listening on configured ports (8080, 5656, 5555, 5173, 5575, 5000)...
for %%P in (8080 5656 5555 5173 5575 5000) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        taskkill /PID %%a /F >nul 2>nul
    )
)
echo.
echo  Done.
echo.
pause
