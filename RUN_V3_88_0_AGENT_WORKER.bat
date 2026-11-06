@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\services\api"
set "PITGUARD_AGENT_WORKER=external"
python -m app.agents.worker --loop
if errorlevel 1 (
  echo [PitGuard] Agent worker failed. Check the Python environment and writable runtime database directory.
  exit /b 1
)
