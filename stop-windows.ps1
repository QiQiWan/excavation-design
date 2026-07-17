$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Manifest = Join-Path $RootDir "runtime\processes.json"
if (-not (Test-Path $Manifest)) {
  Write-Host "[PitGuard] No process manifest was found. Close any older PitGuard API, worker and frontend windows manually." -ForegroundColor Yellow
  exit 0
}

$state = Get-Content $Manifest -Raw | ConvertFrom-Json
@($state.frontendPid, $state.workerPid, $state.backendPid) | ForEach-Object {
  $processId = [int]($_)
  if ($processId -le 0) { return }
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($process) {
    Write-Host "[PitGuard] Stopping PID $processId ($($process.ProcessName))"
    Stop-Process -Id $processId -ErrorAction SilentlyContinue
  }
}
Remove-Item $Manifest -Force -ErrorAction SilentlyContinue
Write-Host "[PitGuard] Recorded PitGuard processes have been stopped." -ForegroundColor Green
