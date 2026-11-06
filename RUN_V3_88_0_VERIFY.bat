@echo off
setlocal
cd /d "%~dp0"
python scripts\verify-release-consistency.py
if errorlevel 1 goto :fail
python scripts\verify-v388-integrity.py
if errorlevel 1 goto :fail
python -m compileall -q services\api\app
if errorlevel 1 goto :fail
python -c "import fastapi, pydantic, pytest, httpx"
if errorlevel 1 (
  echo [PitGuard] Install dependencies: python -m pip install "pytest>=9,<10" "httpx>=0.28,<0.29"
  goto :fail
)
set "PYTHONPATH=%CD%\services\api"
python -m pytest -q verification\tests
if errorlevel 1 goto :fail
echo [PitGuard] Agent backend regression passed. Frontend build separately: cd apps\web ^&^& npm ci ^&^& npm run build
echo [PitGuard] No formal construction issue or engineering approval is implied.
exit /b 0
:fail
echo [PitGuard] Verification failed. Check the output above.
exit /b 1
