$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiDir = Join-Path $RootDir "services\api"
$WebDir = Join-Path $RootDir "apps\web"
$RuntimeDir = Join-Path $RootDir "runtime"
$DbPath = if ($env:PITGUARD_DB_PATH) { $env:PITGUARD_DB_PATH } else { Join-Path $RuntimeDir "pitguard.sqlite3" }
$BackendPort = if ($env:PITGUARD_BACKEND_PORT) { $env:PITGUARD_BACKEND_PORT } else { "8000" }
$FrontendPort = if ($env:PITGUARD_FRONTEND_PORT) { $env:PITGUARD_FRONTEND_PORT } else { "5173" }
$InstallDeps = if ($env:PITGUARD_INSTALL_DEPS) { $env:PITGUARD_INSTALL_DEPS } else { "1" }
$NumericThreads = if ($env:PITGUARD_NUMERIC_THREADS) { $env:PITGUARD_NUMERIC_THREADS } else { "1" }
$BackendLog = Join-Path $RuntimeDir "backend.log"
$FrontendLog = Join-Path $RuntimeDir "frontend.log"
$CheckScriptPath = Join-Path $RuntimeDir "check_backend_modules.py"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Set-Content -Path $BackendLog -Value ""
Set-Content -Path $FrontendLog -Value ""

function Require-Command($Name, $Hint) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    Write-Host "[PitGuard] $Hint" -ForegroundColor Red
    exit 1
  }
}

Require-Command "npm" "Node.js/npm was not found. Install Node.js 20+ or activate the environment that provides npm."

$PythonExe = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } elseif (Get-Command "python" -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command "py" -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $PythonExe) {
  Write-Host "[PitGuard] Python was not found. Activate your Conda/system environment or set PYTHON_BIN." -ForegroundColor Red
  exit 1
}

$PythonPath = (& $PythonExe -c "import sys; print(sys.executable)" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $PythonPath) {
  Write-Host "[PitGuard] Failed to run Python. Check PYTHON_BIN or activate the intended environment." -ForegroundColor Red
  exit 1
}
$PythonVersion = (& $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>&1 | Out-String).Trim()
Write-Host "[PitGuard] Using current Python: $PythonPath"
Write-Host "[PitGuard] Python version: $PythonVersion"
Write-Host "[PitGuard] Startup policy: use the current Python environment only; do not create or activate services/api/.venv."

$LocalVenv = Join-Path $ApiDir ".venv"
if ($PythonPath.ToLowerInvariant().StartsWith($LocalVenv.ToLowerInvariant())) {
  Write-Host "[PitGuard] Warning: the current Python points to services/api/.venv. The script did not activate it; deactivate it if this is not intended." -ForegroundColor Yellow
}

$CheckCode = @'
modules = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "pydantic": "pydantic",
    "multipart": "python-multipart",
    "numpy": "numpy",
    "shapely": "shapely",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "meshio": "meshio",
}
missing = []
for import_name, package_name in modules.items():
    try:
        __import__(import_name)
    except Exception:
        missing.append(package_name)
print(" ".join(missing))
'@
Set-Content -Path $CheckScriptPath -Value $CheckCode -Encoding ASCII

function Get-MissingBackendModules {
  $Output = & $PythonExe $CheckScriptPath 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[PitGuard] Backend module diagnostic script failed:" -ForegroundColor Red
    if ($null -ne $Output) { Write-Host (($Output | Out-String).Trim()) }
    return "__CHECK_FAILED__"
  }
  if ($null -eq $Output) { return "" }
  return (($Output | Out-String).Trim())
}

function Install-BackendPackages($MissingModules) {
  $Packages = @($MissingModules -split "\s+" | Where-Object { $_ })
  if ($Packages.Count -eq 0) { return }
  Write-Host "[PitGuard] Installing the locked backend project dependencies into the CURRENT Python environment. No virtual environment will be created."
  & $PythonExe -m pip install -e $ApiDir 2>&1 | Tee-Object -FilePath $BackendLog -Append
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[PitGuard] pip install failed in the current environment. Last backend log lines:" -ForegroundColor Red
    Get-Content $BackendLog -Tail 80
    exit 1
  }
}

$MissingModules = Get-MissingBackendModules
if ($MissingModules -eq "__CHECK_FAILED__") { exit 1 }
if ($MissingModules) {
  Write-Host "[PitGuard] Missing backend modules: $MissingModules" -ForegroundColor Yellow
  if ($InstallDeps -eq "0") {
    Write-Host "[PitGuard] PITGUARD_INSTALL_DEPS=0, so dependencies will not be installed automatically." -ForegroundColor Red
    Write-Host "[PitGuard] Run manually: $PythonExe -m pip install fastapi 'uvicorn[standard]' pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio"
    exit 1
  }
  Install-BackendPackages $MissingModules
} else {
  Write-Host "[PitGuard] Backend Python modules look available in the current environment."
}

$PostMissingModules = Get-MissingBackendModules
if ($PostMissingModules -eq "__CHECK_FAILED__") { exit 1 }
if ($PostMissingModules) {
  Write-Host "[PitGuard] Backend dependency check still failed: $PostMissingModules" -ForegroundColor Red
  Write-Host "[PitGuard] Check pip permissions or activate the intended Python environment, then rerun this script."
  exit 1
}

function Test-FrontendDependencies {
  $Required = @("vite", "typescript", "react", "react-dom", "three", "zustand", "@vitejs/plugin-react")
  $Missing = @()
  foreach ($Name in $Required) {
    $ModulePath = Join-Path (Join-Path $WebDir "node_modules") $Name
    if (-not (Test-Path $ModulePath)) { $Missing += $Name }
  }
  return $Missing
}

$MissingFrontend = Test-FrontendDependencies
if (-not (Test-Path (Join-Path $WebDir "node_modules")) -or $MissingFrontend.Count -gt 0) {
  if ($MissingFrontend.Count -gt 0) { Write-Host "[PitGuard] Missing frontend modules: $($MissingFrontend -join ' ')" -ForegroundColor Yellow }
  Write-Host "[PitGuard] Installing frontend dependencies from package-lock.json with npm ci..."
  Push-Location $WebDir
  npm ci 2>&1 | Tee-Object -FilePath $FrontendLog -Append
  if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "[PitGuard] npm install failed. Last frontend log lines:" -ForegroundColor Red
    Get-Content $FrontendLog -Tail 80
    exit 1
  }
  Pop-Location
} else {
  Write-Host "[PitGuard] Frontend node_modules look complete."
}

$env:PITGUARD_DB_PATH = $DbPath
$env:PITGUARD_NUMERIC_THREADS = $NumericThreads
$ExistingPythonPath = if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" }
$env:PYTHONPATH = "$ApiDir$ExistingPythonPath"

$BackendScript = @"
cd /d "$ApiDir"
set "PITGUARD_DB_PATH=$DbPath"
set "PITGUARD_NUMERIC_THREADS=$NumericThreads"
set "PYTHONPATH=$ApiDir;%PYTHONPATH%"
"$PythonPath" -m uvicorn app.main:app --reload --host 127.0.0.1 --port $BackendPort 1>>"$BackendLog" 2>>&1
"@
$BackendCmdPath = Join-Path $RuntimeDir "run-backend.cmd"
Set-Content -Path $BackendCmdPath -Value $BackendScript -Encoding Default

Write-Host "[PitGuard] Starting API at http://127.0.0.1:$BackendPort"
$BackendProcess = Start-Process cmd.exe -ArgumentList "/k", "`"$BackendCmdPath`"" -PassThru -WindowStyle Normal

Write-Host "[PitGuard] Waiting for backend health check..."
$HealthOk = $false
for ($i = 0; $i -lt 25; $i++) {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 1 | Out-Null
    $HealthOk = $true
    break
  } catch {
    if ($BackendProcess.HasExited) {
      Write-Host "[PitGuard] Backend process exited during startup. Last backend log lines:" -ForegroundColor Red
      Get-Content $BackendLog -Tail 80
      exit 1
    }
    Start-Sleep -Seconds 1
  }
}

if (-not $HealthOk) {
  Write-Host "[PitGuard] Backend did not pass health check. Last backend log lines:" -ForegroundColor Red
  Get-Content $BackendLog -Tail 80
  exit 1
}

$FrontendScript = @"
cd /d "$WebDir"
set "VITE_API_BASE_URL=http://127.0.0.1:$BackendPort"
npm run dev -- --host 127.0.0.1 --port $FrontendPort 1>>"$FrontendLog" 2>>&1
"@
$FrontendCmdPath = Join-Path $RuntimeDir "run-frontend.cmd"
Set-Content -Path $FrontendCmdPath -Value $FrontendScript -Encoding Default

Write-Host "[PitGuard] Starting web UI at http://127.0.0.1:$FrontendPort"
Start-Process cmd.exe -ArgumentList "/k", "`"$FrontendCmdPath`"" -WindowStyle Normal | Out-Null
Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:$FrontendPort"

Write-Host ""
Write-Host "PitGuard is running with the current shell environment." -ForegroundColor Green
Write-Host "Backend API : http://127.0.0.1:$BackendPort/health"
Write-Host "Diagnostics : http://127.0.0.1:$BackendPort/api/system/diagnostics"
Write-Host "Frontend UI : http://127.0.0.1:$FrontendPort"
Write-Host "Database    : $DbPath"
Write-Host "Numeric thr.: $NumericThreads"
Write-Host "Backend log : $BackendLog"
Write-Host "Frontend log: $FrontendLog"
Write-Host "Close the two service windows to stop PitGuard."
