@echo off
setlocal
cd /d "%~dp0"
python scripts\verify-release-consistency.py
if errorlevel 1 goto :fail
python -c "import pytest; import fastapi; import pydantic"
if errorlevel 1 (
  echo [PitGuard] Missing test dependencies. Install: python -m pip install "pytest>=9,<10" "httpx>=0.28,<0.29" "cryptography>=44,<47"
  goto :fail
)
set "PYTHONPATH=%CD%\services\api"
python -m pytest -q verification\tests
if errorlevel 1 goto :fail
echo [PitGuard] P1-P4 verification passed. This is not a formal engineering sign-off.
exit /b 0
:fail
echo [PitGuard] Verification failed. Check the error output above.
exit /b 1
