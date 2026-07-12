param(
    [ValidateSet("fast", "full-isolated")]
    [string]$Mode = "fast"
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
if (-not $env:PITGUARD_NUMERIC_THREADS) { $env:PITGUARD_NUMERIC_THREADS = "1" }
$env:OPENBLAS_NUM_THREADS = $env:PITGUARD_NUMERIC_THREADS
$env:OMP_NUM_THREADS = $env:PITGUARD_NUMERIC_THREADS
$env:MKL_NUM_THREADS = $env:PITGUARD_NUMERIC_THREADS
$env:NUMEXPR_NUM_THREADS = $env:PITGUARD_NUMERIC_THREADS
$env:VECLIB_MAXIMUM_THREADS = $env:PITGUARD_NUMERIC_THREADS

function Invoke-IsolatedTests([string]$Label, [string[]]$Nodes) {
    Write-Host "Running $($Nodes.Count) $Label backend test groups in isolated processes."
    foreach ($node in $Nodes) {
        Write-Host "`n==> $node"
        python -m pytest -q $node --maxfail=1
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

Push-Location (Join-Path $RootDir "services/api")
try {
    python -m compileall -q app tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Mode -eq "fast") {
        $nodes = @(
            "tests/test_v3_6_0_support_topology_scheme_ux.py",
            "tests/test_v3_5_0_concave_recovery_drawing_intelligence.py",
            "tests/test_v3_4_0_drawing_rule_engine.py",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_advanced_suite_covers_eight_tracks",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_monitoring_calibration_is_applied_to_next_calculation_inputs",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_review_enforces_separation_of_duties_and_reject_comment",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_design_setting_update_invalidates_old_results",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_construction_issue_requires_current_snapshot_revision",
            "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_formal_package_contains_geometry_pdf_quality_and_revision_files",
            "tests/test_v3_2_0_rebar_diagnostics_ux.py",
            "tests/test_v3_1_0_rebar_cad.py",
            "tests/test_v3_0_0_integration.py",
            "tests/test_mvp.py::test_health",
            "tests/test_mvp.py::test_project_crud",
            "tests/test_v2_1_0_tasks_issues.py",
            "tests/test_v2_2_0_trace_and_cad.py"
        )
        Invoke-IsolatedTests "fast-gate" $nodes
    else {
        $nodes = python -m pytest --collect-only -q |
            Where-Object { $_ -match '^tests[/\\].*::' }
        if (-not $nodes) { throw "No pytest nodes collected." }
        Invoke-IsolatedTests "full-gate" $nodes
    }
}
finally {
    Pop-Location
}
