from __future__ import annotations

from copy import deepcopy

import numpy as np

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.calculation.numerical_conditioning import solve_scaled_symmetric
from app.calculation.opensees_benchmark import run_independent_reference_benchmark_suite, run_opensees_planar_benchmark_suite
from app.calculation.transfer_node_spatial import solve_spatial_node_rotations
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig


L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.71 conditioned coupling",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    project = Project(name="V3.71 conditioned coupling", excavation=excavation, retainingSystem=system)
    project.calculation_cases = build_default_construction_cases(project)
    return project


def test_scaled_solver_reduces_condition_number_and_preserves_solution() -> None:
    matrix = np.array([[1.0e12, 0.0], [0.0, 1.0]], dtype=float)
    load = np.array([1.0e12, 1.0], dtype=float)
    displacement, diagnostics = solve_scaled_symmetric(matrix, load)
    assert displacement is not None
    assert np.allclose(displacement, [1.0, 1.0], rtol=1.0e-12, atol=1.0e-12)
    assert diagnostics["rawConditionNumber"] >= 1.0e11
    assert diagnostics["scaledConditionNumber"] <= 1.0 + 1.0e-12
    assert diagnostics["blocked"] is False


def test_scaled_solver_blocks_rank_deficient_stiffness_matrix() -> None:
    matrix = np.array([[1.0, 1.0], [1.0, 1.0]], dtype=float)
    displacement, diagnostics = solve_scaled_symmetric(matrix, np.array([1.0, 1.0]))
    assert displacement is None
    assert diagnostics["blocked"] is True
    assert diagnostics["rankDeficient"] is True


def test_v371_full_chain_has_conditioning_coupling_sensitivity_and_spatial_detailing() -> None:
    project = _project()
    run_calculation(project, project.calculation_cases[0], auto_repair=False, include_candidate_comparison=False)
    frame = project.advanced_engineering["concaveTransferFrameAnalysis"]
    iteration = project.advanced_engineering["wallWaleTransferReactionIteration"]
    spatial = project.advanced_engineering["concaveTransferSpatialAnalysis"]
    detailing = project.advanced_engineering["concaveTransferAutoDetailing"]
    data = project.advanced_engineering["transferEngineeringDataAssurance"]

    assert frame["status"] == "pass"
    assert frame["maximumScaledConditionNumber"] < frame["maximumRawConditionNumber"]
    assert frame["maximumNodeStiffnessRatio"] > 1.0
    assert frame["sensitivity"]["status"] == "pass"
    assert frame["sensitivity"]["maximumRelativeChange"] < 0.10
    assert iteration["status"] == "pass"
    assert 1 <= iteration["iterationCount"] <= 8
    assert spatial["status"] in {"pass", "warning"}
    assert spatial["maximumTorsionKnm"] > 0.0
    assert spatial["maximumInPlaneEccentricMomentKnm"] > 0.0
    assert spatial["maximumScaledConditionNumber"] > 0.0
    assert spatial["maximumRelativeEquilibriumResidual"] < 1.0e-8
    assert detailing["status"] == "pass"
    assert detailing["metrics"]["designedTransferBeamCount"] == len(project.retaining_system.ring_beams)
    assert detailing["metrics"]["maximumTorsionKnm"] > 0.0
    assert detailing["metrics"]["haunchRequiredNodeCount"] > 0
    assert data["formalDataReady"] is False


def test_reduced_spatial_node_solver_recovers_applied_moment() -> None:
    members = [
        {
            "beamCode": "X", "axis": (1.0, 0.0),
            "torsionStiffness": 1.0e5, "bendingStiffness": 8.0e5,
            "outOfPlaneBendingStiffness": 8.0e5, "inPlaneBendingStiffness": 8.0e5,
            "effectiveLength": 4.0, "rigidZoneLength": 0.2,
        },
        {
            "beamCode": "Y", "axis": (0.0, 1.0),
            "torsionStiffness": 1.2e5, "bendingStiffness": 9.0e5,
            "outOfPlaneBendingStiffness": 9.0e5, "inPlaneBendingStiffness": 9.0e5,
            "effectiveLength": 3.5, "rigidZoneLength": 0.2,
        },
    ]
    result = solve_spatial_node_rotations([400.0, -250.0, 180.0], members)
    assert result["status"] in {"pass", "warning"}
    assert result["blocked"] is False
    assert result["relativeEquilibriumResidual"] < 1.0e-10
    assert len(result["members"]) == 2
    recovered = np.asarray(result["recoveredMomentVectorKnm"], dtype=float)
    assert np.allclose(recovered, [400.0, -250.0, 180.0], rtol=1.0e-10, atol=1.0e-10)


def test_external_benchmark_reports_availability_honestly() -> None:
    result = run_opensees_planar_benchmark_suite()
    assert result["status"] in {"pass", "partial", "unavailable"}
    assert result["caseCount"] == 3
    if result["status"] == "unavailable":
        assert result["unavailableCount"] == result["caseCount"]
        assert "no external-software equivalence claim" in result["message"]
    else:
        spatial = next(case for case in result["cases"] if case["name"] == "spatial_reduced_rotational_node")
        assert spatial["status"] in {"pass", "unavailable"}


def test_independent_reference_benchmark_covers_planar_and_spatial_models() -> None:
    result = run_independent_reference_benchmark_suite()
    assert result["status"] == "pass"
    assert result["caseCount"] == 3
    assert result["maximumRelativeDisplacementError"] < 1.0e-8
