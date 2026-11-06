@echo off
setlocal
cd /d "%~dp0"
python scripts\run-v388-agent-demo.py --candidates 3
if errorlevel 1 exit /b 1
echo [PitGuard] Synthetic-only demonstration completed. Results in verification-output\agent-v388-demo
