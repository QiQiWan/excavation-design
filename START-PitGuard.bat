@echo off
setlocal
cd /d "%~dp0"
set "BASE_PY=C:\Users\76836\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not exist "%BASE_PY%" goto :nobase
if exist ".venv\Scripts\python.exe" goto :venvready
echo [PitGuard] Creating isolated environment .venv ...
"%BASE_PY%" -m venv .venv
if errorlevel 1 goto :fail

:venvready
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
"%VENV_PY%" -c "import fastapi" 2>nul
if errorlevel 1 goto :installbackend
goto :backendready

:installbackend
echo [PitGuard] Installing backend dependencies into .venv (first run only, needs internet)...
"%VENV_PY%" -m pip install "fastapi>=0.128,<0.129" "uvicorn[standard]>=0.48,<0.49" "pydantic>=2.13,<2.14" "python-multipart>=0.0.29,<0.1" "numpy>=2.3,<2.4" "scipy>=1.14,<2" "shapely>=2.1,<2.2" "python-docx>=1.2,<1.3" "openpyxl>=3.1,<3.2" "matplotlib>=3.10,<3.11" "fonttools>=4.60,<5" "meshio>=5.3,<6" "ezdxf>=1.4,<1.5" "reportlab>=4.4,<4.5" -i https://mirrors.aliyun.com/pypi/simple --timeout 60 --retries 3
if errorlevel 1 goto :fail

:backendready
set "PYTHON_BIN=%VENV_PY%"
echo [PitGuard] Python: %PYTHON_BIN%
echo [PitGuard] Starting backend / worker / frontend (first run installs frontend deps, needs internet)...
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0start-windows.ps1"
if errorlevel 1 goto :fail
exit /b 0

:nobase
echo [PitGuard] Base Python not found: %BASE_PY%
echo Edit BASE_PY in this file, or set PYTHON_BIN to a real python.exe.
pause
exit /b 1

:fail
echo.
echo [PitGuard] Startup failed. Read the message above and runtime\*.log
echo Common causes: no internet for pip/npm, port 8002 or 5173 already in use.
pause
exit /b 1
