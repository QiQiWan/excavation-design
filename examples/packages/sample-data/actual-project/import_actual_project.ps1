param(
    [string]$ApiBase = "http://127.0.0.1:8002"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Csv = Join-Path $Root "actual_project_boreholes_24x6layers.csv"
$ExcavationJson = Join-Path $Root "actual_project_excavation_payload.json"

Write-Host "[1/7] Create project"
$projectBody = @{
    name = "Actual engineering case - 24 boreholes"
    location = "Local engineering coordinates"
    designSettings = @{
        autoCenterExcavationOnGeology = $false
        groundwaterLevel = -20.0
        surcharge = 20.0
        minimumSegmentLength = 0.5
        supportLevelDepthsM = @(0.0, 4.0, 7.2, 10.3, 13.3)
    }
} | ConvertTo-Json -Depth 8
$project = Invoke-RestMethod -Method Post -Uri "$ApiBase/api/projects" -ContentType "application/json" -Body $projectBody
$ProjectId = $project.id
Write-Host "Project ID: $ProjectId"

Write-Host "[2/7] Import boreholes"
$importText = & curl.exe -sS -X POST -F "file=@$Csv" "$ApiBase/api/projects/$ProjectId/boreholes/import-csv"
$import = $importText | ConvertFrom-Json
if (-not $import.success) { throw "Borehole import failed: $($import.errors -join '; ')" }
Write-Host "Imported boreholes=$($import.boreholeCount), layers=$($import.layerCount), strata=$($import.stratumCount)"
if ($import.warnings.Count -gt 0) { Write-Warning ($import.warnings -join "`n") }

Write-Host "[3/7] Build geological model"
Invoke-RestMethod -Method Post -Uri "$ApiBase/api/projects/$ProjectId/geology/build-model?grid_size=10" | Out-Null

Write-Host "[4/7] Create excavation outline"
$payload = Get-Content -Raw -Encoding UTF8 $ExcavationJson
Invoke-RestMethod -Method Post -Uri "$ApiBase/api/projects/$ProjectId/excavation" -ContentType "application/json" -Body $payload | Out-Null

Write-Host "[5/7] Generate diaphragm wall"
Invoke-RestMethod -Method Post -Uri "$ApiBase/api/projects/$ProjectId/design/auto-diaphragm-wall" | Out-Null

Write-Host "[6/7] Generate supports at source-project levels"
Invoke-RestMethod -Method Post -Uri "$ApiBase/api/projects/$ProjectId/design/auto-supports" | Out-Null

Write-Host "[7/7] Apply source wall toe elevation -32.8 m"
$retaining = Invoke-RestMethod -Method Get -Uri "$ApiBase/api/projects/$ProjectId/design/retaining-system"
foreach ($wall in $retaining.diaphragmWalls) {
  $wall.bottomElevation = -32.8
  $wall.bottomElevationSource = "imported"
  $wall.bottomElevationLocked = $true
  $wall.sourceBottomElevation = -32.8
}
$retainingJson = $retaining | ConvertTo-Json -Depth 100
Invoke-RestMethod -Method Put -Uri "$ApiBase/api/projects/$ProjectId/design/retaining-system" -ContentType "application/json" -Body $retainingJson | Out-Null

Write-Host "Done. Open: http://127.0.0.1:5173/projects/$ProjectId"
Write-Host "Applied: wall toe=-32.8 m; support depths=0.0,4.0,7.2,10.3,13.3 m."
Write-Host "Important: verify gravel modulus/permeability, actual source support plan geometry, obstacles and construction access before calculation."
