from __future__ import annotations

from copy import deepcopy

import pytest

from app.calculation import engine
from app.calculation.engine import build_default_construction_cases, run_calculation
from app.calculation.opensees_benchmark import (
    run_independent_reference_benchmark_suite,
    run_opensees_planar_benchmark_suite,
)
from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig


L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.72 workflow stability",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(
            topology_strategy="zoned_direct",
            concave_transfer_template="junction_hub_frame",
        ),
    )
    project = Project(name="V3.72 workflow stability", excavation=excavation, retainingSystem=system)
    project.calculation_cases = build_default_construction_cases(project)
    return project




def test_transaction_working_set_does_not_duplicate_historical_results() -> None:
    project = _project()
    history = CalculationResult(projectId=project.id, caseId=project.calculation_cases[0].id)
    project.calculation_results = [history]
    trial = engine._calculation_trial(project)
    assert trial.calculation_results is not project.calculation_results
    assert trial.calculation_results[0] is history
    assert trial.retaining_system is not project.retaining_system
    assert trial.calculation_cases is not project.calculation_cases
    assert trial.advanced_engineering is not project.advanced_engineering

def test_calculation_transaction_rolls_back_trial_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    before_name = project.name
    before_support_count = len(project.retaining_system.supports)

    def _failing_impl(trial: Project, *args, **kwargs):
        trial.name = "mutated trial"
        trial.retaining_system.supports.clear()
        raise RuntimeError("deliberate transactional rollback probe")

    monkeypatch.setattr(engine, "_run_calculation_impl", _failing_impl)
    with pytest.raises(RuntimeError, match="transactional rollback probe"):
        run_calculation(project, project.calculation_cases[0], auto_repair=False)

    assert project.name == before_name
    assert len(project.retaining_system.supports) == before_support_count
    failure = project.advanced_engineering["lastCalculationFailure"]
    assert failure["status"] == "rolled_back"
    assert failure["projectMutationCommitted"] is False


def test_full_result_contains_execution_health_completeness_and_catalog() -> None:
    project = _project()
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)

    execution = result.calculation_execution
    assert execution["schema"] == "pitguard-calculation-execution-v2"
    assert execution["transactionStatus"] == "committed"
    assert execution["phaseCount"] >= 6
    assert execution["totalDurationSeconds"] > 0.0
    assert execution["engineeringStatus"] in {"pass", "warning", "manual_review", "fail"}
    assert execution["deliveryStatus"] in {"pass", "warning", "manual_review", "fail"}
    assert execution["bottleneckPhase"]["durationSharePercent"] > 0.0
    coupling_phase = next(item for item in execution["phases"] if item["phaseId"] == "coupling_member_design")
    assert coupling_phase["status"] == "fail"
    assert coupling_phase["metrics"]["engineeringSupportFailCount"] >= 1
    assert execution["engineeringStatus"] == "fail"
    assert {item["phaseId"] for item in execution["phases"]} >= {
        "input_preflight",
        "staged_wall_wale_solver",
        "coupling_member_design",
        "stability_scenarios",
        "quality_delivery_gate",
        "result_evidence_freeze",
    }

    health = result.numerical_health
    assert health["schema"] == "pitguard-numerical-health-v1"
    assert health["globalSystemCount"] > 0
    assert health["maximumScaledConditionNumber"] > 0.0
    assert health["maximumRelativeResidual"] >= 0.0
    assert health["reactionIteration"]["status"] == "pass"
    assert health["reactionIteration"]["iterationCount"] >= 1
    assert health["reactionIteration"]["relaxationHistory"]

    completeness = result.result_completeness
    assert completeness["schema"] == "pitguard-result-completeness-v2"
    assert 0.0 <= completeness["engineeringCompletenessPercent"] <= 100.0
    assert 0.0 <= completeness["formalIssueCompletenessPercent"] <= 100.0
    assert completeness["engineeringCompletenessPercent"] > completeness["formalIssueCompletenessPercent"]
    assert 0.0 <= completeness["engineeringReadinessPercent"] <= 100.0
    assert 0.0 <= completeness["formalIssueReadinessPercent"] <= 100.0
    assert completeness["formalIssueReadinessPercent"] <= 49.0
    assert "geology" in completeness["criticalBlockingDomains"]
    assert completeness["domainCount"] >= 13

    catalog = result.result_catalog
    assert catalog["schema"] == "pitguard-result-catalog-v3"
    assert len(catalog["stageMatrix"]) == len(project.calculation_cases[0].stages)
    assert catalog["criticalStages"]
    assert catalog["wallEnvelopes"]
    assert catalog["supportEnvelopes"]
    assert catalog["transferBeamEnvelopes"]
    assert catalog["waleEnvelopes"]
    assert catalog["columnFoundationEnvelopes"]
    assert catalog["stabilityModes"]
    assert catalog["reinforcementInventory"]["totalGroupCount"] > 0
    assert catalog["blockingCheckLedger"]
    assert catalog["ruleStatusCounts"]
    assert catalog["counts"]["blockingChecks"] == len(catalog["blockingCheckLedger"])


def test_adaptive_reaction_iteration_exposes_stability_diagnostics() -> None:
    project = _project()
    run_calculation(project, project.calculation_cases[0], auto_repair=False)
    iteration = project.advanced_engineering["wallWaleTransferReactionIteration"]
    assert iteration["schema"] == "pitguard-wall-wale-transfer-iteration-v2"
    assert iteration["converged"] is True
    assert iteration["status"] == "pass"
    assert len(iteration["relaxationHistory"]) == iteration["iterationCount"]
    assert iteration["finalForceRelativeResidual"] <= iteration["forceTolerance"]
    assert iteration["finalDisplacementRelativeResidual"] <= iteration["displacementTolerance"]
    assert iteration["finalForceAbsoluteResidualKn"] >= 0.0
    assert iteration["finalDisplacementAbsoluteResidualMm"] >= 0.0
    assert iteration["convergenceQuality"] in {"strong", "acceptable"}


def test_calculation_transaction_commits_successful_trial() -> None:
    project = _project()
    before = deepcopy(project.advanced_engineering)
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    transaction = project.advanced_engineering["lastCalculationTransaction"]
    assert transaction["status"] == "committed"
    assert transaction["calculationResultId"] == result.id
    assert transaction["resultHash"] == result.result_hash
    assert project.advanced_engineering != before


def test_independent_reference_passes_and_external_reference_is_not_faked() -> None:
    independent = run_independent_reference_benchmark_suite()
    assert independent["status"] == "pass"
    assert independent["passCount"] == independent["caseCount"] == 3
    assert independent["maximumRelativeDisplacementError"] < 1.0e-8

    external = run_opensees_planar_benchmark_suite()
    assert external["status"] in {"pass", "partial", "unavailable"}
    if external["status"] == "unavailable":
        assert external["unavailableCount"] == 3
        assert external["passCount"] == 0
        assert all(item["status"] == "unavailable" for item in external["cases"])


def test_stability_metric_semantics_separate_safety_factors_from_risk_ratios() -> None:
    project = _project()
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    detailed = result.stability_detailed_result
    assert detailed is not None
    assert detailed.min_safety_factor is not None
    assert detailed.min_safety_factor > 0.0
    available_safety = [
        value for value in (
            detailed.embedment_factor,
            detailed.heave_factor,
            detailed.confined_uplift_factor,
            detailed.seepage_factor,
            detailed.overall_stability_factor,
        ) if value is not None and value < 900.0
    ]
    assert available_safety
    assert detailed.min_safety_factor == min(available_safety)
    assert detailed.controlling_safety_mode in {"embedment", "base_heave", "confined_uplift", "seepage", "overall"}
    assert detailed.layered_seepage_risk_index is not None
    assert detailed.dewatering_control_ratio is not None
    assert result.design_review_summary.stability_status == "pass"
    assert result.design_review_summary.strength_status == "fail"
    assert "strength_verification" in result.result_completeness["criticalBlockingDomains"]
    assert "stability" not in result.result_completeness["criticalBlockingDomains"]

    modes = result.result_catalog["stabilityModes"]
    safety_rows = [row for row in modes if row["metricType"] == "safety_factor" and row["available"]]
    risk_rows = [row for row in modes if row["metricType"] == "risk_ratio" and row["available"]]
    assert safety_rows and risk_rows
    assert sum(bool(row["controlling"]) for row in safety_rows) == 1
    positive_risk = any((row.get("utilization") or 0.0) > 0.0 for row in risk_rows)
    assert sum(bool(row["controlling"]) for row in risk_rows) == (1 if positive_risk else 0)
    assert all(row["direction"] == "larger_is_better" for row in safety_rows)
    assert all(row["direction"] == "smaller_is_better" for row in risk_rows)
    assert all(row["value"] >= 0.0 for row in risk_rows)

    stage_factors = [
        row["minimumStabilityFactor"]
        for row in result.result_catalog["stageMatrix"]
        if row["minimumStabilityFactor"] is not None
    ]
    assert stage_factors and min(stage_factors) > 0.0
