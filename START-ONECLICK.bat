@echo off
setlocal
cd /d "%~dp0"
title PitGuard One-Click Start

echo ============================================
echo  PitGuard one-click start
echo ============================================

netstat -ano | findstr "127.0.0.1:8002" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [1/3] Starting backend...
  start "PitGuard Backend" /min cmd /c %~dp0runtime\run-backend.cmd
) else (
  echo [1/3] Backend already running, skipped.
)

netstat -ano | findstr "127.0.0.1:5173" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [2/3] Starting frontend...
  start "PitGuard Frontend" /min cmd /c %~dp0runtime\run-frontend.cmd
) else (
  echo [2/3] Frontend already running, skipped.
)

echo [3/3] Waiting for the web server...
set /a tries=0
:wait
curl -s -o NUL http://127.0.0.1:5173/ >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% GEQ 30 goto timeout
timeout /t 2 >nul
goto wait

:ready
start "" "http://127.0.0.1:5173/projects/project-73960a1e250b"
echo.
echo Done. The workspace is open in your browser.
echo Assistant pet sits at the bottom-right corner; drag it anywhere, double-click to reset.
goto end

:timeout
echo Web server did not respond within 60 seconds.
echo Check the two minimized console windows for errors.

:end
timeout /t 4 >nul
endlocal
