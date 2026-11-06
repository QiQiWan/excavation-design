$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiDir = Join-Path $RootDir "services\api"
$WebDir = Join-Path $RootDir "apps\web"
$RuntimeDir = Join-Path $RootDir "runtime"
$DbPath = if ($env:PITGUARD_DB_PATH) { $env:PITGUARD_DB_PATH } else { Join-Path $RuntimeDir "pitguard.sqlite3" }
$BackendPort = if ($env:PITGUARD_BACKEND_PORT) { $env:PITGUARD_BACKEND_PORT } else { "8002" }
$FrontendPort = if ($env:PITGUARD_FRONTEND_PORT) { $env:PITGUARD_FRONTEND_PORT } else { "5173" }
$InstallDeps = if ($env:PITGUARD_INSTALL_DEPS) { $env:PITGUARD_INSTALL_DEPS } else { "1" }
$NumericThreads = if ($env:PITGUARD_NUMERIC_THREADS) { $env:PITGUARD_NUMERIC_THREADS } else { "1" }
$BackendLog = Join-Path $RuntimeDir "backend.log"
$FrontendLog = Join-Path $RuntimeDir "frontend.log"
$EnvChecker = Join-Path $RootDir "scripts\check-python-env.py"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Set-Content -Path $BackendLog -Value ""
Set-Content -Path $FrontendLog -Value ""

function Stop-WithMessage([string]$Message, [string]$InstallCommand = "") {
  Write-Host "[PitGuard] $Message" -ForegroundColor Red
  if ($InstallCommand) {
    Write-Host ""
    Write-Host "[PitGuard] Python dependency installation command:" -ForegroundColor Yellow
    Write-Host "  $InstallCommand" -ForegroundColor Yellow
  }
  exit 1
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Stop-WithMessage "Node.js/npm was not found. Install Node.js 20+ or activate an environment that provides npm."
}
$PythonExe = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $PythonExe) { Stop-WithMessage "Python was not found. Activate the intended environment or set PYTHON_BIN." }

$PythonPath = (& $PythonExe -c "import sys; print(sys.executable)" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $PythonPath) { Stop-WithMessage "Failed to run Python." }
$PythonVersion = (& $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" | Out-String).Trim()
Write-Host "[PitGuard] Using current Python: $PythonPath"
Write-Host "[PitGuard] Python version: $PythonVersion"
Write-Host "[PitGuard] Backend port: $BackendPort"
Write-Host "[PitGuard] No virtual environment will be created automatically."

function Get-DependencyInstallCommand {
  $output = & $PythonExe $EnvChecker --format install-command 2>$null
  if ($null -eq $output) { return "" }
  return (($output | Out-String).Trim())
}
function Test-PythonDependencies {
  & $PythonExe $EnvChecker --format text
  return ($LASTEXITCODE -eq 0)
}

if (-not (Test-PythonDependencies)) {
  $InstallCommand = Get-DependencyInstallCommand
  if ($InstallDeps -eq "0") {
    Stop-WithMessage "PITGUARD_INSTALL_DEPS=0; missing packages will not be installed automatically." $InstallCommand
  }
  Write-Host "[PitGuard] Installing locked backend dependencies into the CURRENT Python environment..."
  & $PythonExe -m pip install -e $ApiDir 2>&1 | Tee-Object -FilePath $BackendLog -Append
  if ($LASTEXITCODE -ne 0) { Stop-WithMessage "pip install failed." $InstallCommand }
}
if (-not (Test-PythonDependencies)) {
  Stop-WithMessage "Backend dependency check still fails after installation." (Get-DependencyInstallCommand)
}

$RequiredFrontend = @("vite", "typescript", "react", "react-dom", "three", "zustand", "@vitejs/plugin-react")
$MissingFrontend = @()
foreach ($Name in $RequiredFrontend) {
  if (-not (Test-Path (Join-Path (Join-Path $WebDir "node_modules") $Name))) { $MissingFrontend += $Name }
}
if (-not (Test-Path (Join-Path $WebDir "node_modules")) -or $MissingFrontend.Count -gt 0) {
  if ($MissingFrontend.Count -gt 0) { Write-Host "[PitGuard] Missing frontend modules: $($MissingFrontend -join ' ')" -ForegroundColor Yellow }
  Push-Location $WebDir
  npm ci 2>&1 | Tee-Object -FilePath $FrontendLog -Append
  $NpmExit = $LASTEXITCODE
  Pop-Location
  if ($NpmExit -ne 0) { Stop-WithMessage "npm ci failed. Run 'cd apps/web && npm ci'." }
}

$env:PITGUARD_DB_PATH = $DbPath
$env:PITGUARD_NUMERIC_THREADS = $NumericThreads
$env:OPENBLAS_NUM_THREADS = $NumericThreads
$env:OMP_NUM_THREADS = $NumericThreads
$env:MKL_NUM_THREADS = $NumericThreads
$env:NUMEXPR_NUM_THREADS = $NumericThreads
$env:VECLIB_MAXIMUM_THREADS = $NumericThreads
$env:PYTHONPATH = "$ApiDir" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })

$BackendCmdPath = Join-Path $RuntimeDir "run-backend.cmd"
$BackendScript = @"
cd /d "$ApiDir"
set "PITGUARD_DB_PATH=$DbPath"
set "PITGUARD_NUMERIC_THREADS=$NumericThreads"
set "PYTHONPATH=$ApiDir;%PYTHONPATH%"
"$PythonPath" -m uvicorn app.main:app --reload --host 127.0.0.1 --port $BackendPort 1>>"$BackendLog" 2>>&1
"@
Set-Content -Path $BackendCmdPath -Value $BackendScript -Encoding Default
$BackendProcess = Start-Process cmd.exe -ArgumentList "/k", "`"$BackendCmdPath`"" -PassThru -WindowStyle Normal

$HealthOk = $false
for ($i = 0; $i -lt 30; $i++) {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 1 | Out-Null
    $HealthOk = $true
    break
  } catch {
    if ($BackendProcess.HasExited) {
      Get-Content $BackendLog -Tail 80
      Stop-WithMessage "Backend exited during startup." (Get-DependencyInstallCommand)
    }
    Start-Sleep -Seconds 1
  }
}
if (-not $HealthOk) {
  Get-Content $BackendLog -Tail 80
  Stop-WithMessage "Backend did not pass the health check." (Get-DependencyInstallCommand)
}

$FrontendCmdPath = Join-Path $RuntimeDir "run-frontend.cmd"
$FrontendScript = @"
cd /d "$WebDir"
set "VITE_API_BASE_URL=http://127.0.0.1:$BackendPort"
npm run dev -- --host 127.0.0.1 --port $FrontendPort 1>>"$FrontendLog" 2>>&1
"@
Set-Content -Path $FrontendCmdPath -Value $FrontendScript -Encoding Default
Start-Process cmd.exe -ArgumentList "/k", "`"$FrontendCmdPath`"" -WindowStyle Normal | Out-Null
Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:$FrontendPort"

Write-Host ""
Write-Host "PitGuard is running." -ForegroundColor Green
Write-Host "Backend API : http://127.0.0.1:$BackendPort/health"
Write-Host "API docs    : http://127.0.0.1:$BackendPort/docs"
Write-Host "Frontend UI : http://127.0.0.1:$FrontendPort"
Write-Host "Database    : $DbPath"
Write-Host "Python      : $PythonPath"
Write-Host "Close the two service windows to stop PitGuard."
