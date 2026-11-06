#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-fast}"
export PITGUARD_NUMERIC_THREADS="${PITGUARD_NUMERIC_THREADS:-1}"
export OPENBLAS_NUM_THREADS="$PITGUARD_NUMERIC_THREADS"
export OMP_NUM_THREADS="$PITGUARD_NUMERIC_THREADS"
export MKL_NUM_THREADS="$PITGUARD_NUMERIC_THREADS"
export NUMEXPR_NUM_THREADS="$PITGUARD_NUMERIC_THREADS"
export VECLIB_MAXIMUM_THREADS="$PITGUARD_NUMERIC_THREADS"

cd "$ROOT_DIR/services/api"

run_isolated_nodes() {
  local label="$1"
  shift
  local nodes=("$@")
  printf 'Running %d %s backend test groups in isolated processes.\n' "${#nodes[@]}" "$label"
  for node in "${nodes[@]}"; do
    printf '\n==> %s\n' "$node"
    PYTHONPATH=. pytest -q "$node" --maxfail=1
  done
}

if [[ "$MODE" == "fast" ]]; then
  python -m compileall -q app tests
  FAST_NODES=(
    "tests/test_v3_31_0_external_dataset_storage.py"
    "tests/test_v3_32_0_fast_interaction_progress.py"
    "tests/test_v3_30_0_project_open_memory_safety.py"
    "tests/test_v3_29_0_resilient_scheme_designer.py"
    "tests/test_v3_28_0_irregular_shape_intelligence.py"
    "tests/test_v3_27_0_shape_topology_isolated_worker.py"
    "tests/test_v3_26_0_wall_to_wall_memory_stability.py"
    "tests/test_v3_25_0_parallel_corner_brace_login_nginx.py"
    "tests/test_v3_24_0_industrial_calculation_delivery.py"
    "tests/test_v3_23_0_joint_clean_ifc_login.py"
    "tests/test_v3_22_0_p0_p3_industrial_closure.py"
    "tests/test_v3_21_0_clean_support_topology.py"
    "tests/test_v3_20_0_design_workflow_cage_support.py"
    "tests/test_v3_19_0_expert_wall_rebar.py"
    "tests/test_v3_18_0_fengshou_embedment_recovery.py"
    "tests/test_v3_17_0_project_delete_corner_geology.py"
    "tests/test_v3_16_0_non_crossing_supports.py"
    "tests/test_v3_15_0_general_shape_state_geology.py"
    "tests/test_v3_14_0_strength_driven_recovery.py"
    "tests/test_v3_11_0_standards_rebar_docs.py"
    "tests/test_v3_6_0_support_topology_scheme_ux.py"
    "tests/test_v3_5_0_concave_recovery_drawing_intelligence.py"
    "tests/test_v3_4_0_drawing_rule_engine.py"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_advanced_suite_covers_eight_tracks"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_monitoring_calibration_is_applied_to_next_calculation_inputs"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_review_enforces_separation_of_duties_and_reject_comment"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_design_setting_update_invalidates_old_results"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_construction_issue_requires_current_snapshot_revision"
    "tests/test_v3_3_0_advanced_engineering.py::test_v3_3_formal_package_contains_geometry_pdf_quality_and_revision_files"
    "tests/test_v3_2_0_rebar_diagnostics_ux.py"
    "tests/test_v3_1_0_rebar_cad.py"
    "tests/test_v3_0_0_integration.py"
    "tests/test_mvp.py::test_health"
    "tests/test_mvp.py::test_project_crud"
    "tests/test_v2_1_0_tasks_issues.py"
    "tests/test_v2_2_0_trace_and_cad.py"
  )
  run_isolated_nodes "fast-gate" "${FAST_NODES[@]}"
elif [[ "$MODE" == "full-isolated" ]]; then
  python -m compileall -q app tests
  mapfile -t TEST_NODES < <(
    PYTHONPATH=. pytest --collect-only -q \
      | sed -n '/^tests\/.*::/p'
  )
  if [[ ${#TEST_NODES[@]} -eq 0 ]]; then
    echo "No pytest nodes collected." >&2
    exit 2
  fi
  run_isolated_nodes "full-gate" "${TEST_NODES[@]}"
else
  echo "Usage: $0 [fast|full-isolated]" >&2
  exit 2
fi
