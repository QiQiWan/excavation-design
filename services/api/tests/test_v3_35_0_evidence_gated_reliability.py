from __future__ import annotations

from types import SimpleNamespace

from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.runtime_observability import RuntimeObservability, normalize_observability_path
from app.services.support_deep_design import evaluate_support_deep_design
from app.services.support_layout import SupportLayoutConfig
from app.version import SOFTWARE_VERSION, version_manifest


def _project() -> Project:
    points = [Point2D(x=0, y=0), Point2D(x=60, y=0), Point2D(x=60, y=20), Point2D(x=0, y=20)]
    excavation = make_excavation_model("evidence", Polyline2D(points=points, closed=True), 0.0, -12.0)
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation), SupportLayoutConfig(topology_strategy="hybrid_diagonal"))
    return Project(name="evidence", excavation=excavation, retainingSystem=retaining)


def _stage_for_all_supports(project: Project, force: float = 50.0):
    return SimpleNamespace(
        support_forces=[
            SimpleNamespace(
                support_id=support.id,
                axial_force_design=force,
                effective_axial_force=None,
                axial_force=force,
            )
            for support in project.retaining_system.supports
        ]
    )


def test_screening_pass_does_not_claim_calculation_or_formal_readiness_without_evidence() -> None:
    project = _project()
    result = evaluate_support_deep_design(project)
    assert result["calculationReady"] is False
    assert result["formalDesignReady"] is False
    assert result["evidenceGrade"] in {"C", "D"}
    assert result["metrics"]["stagedCalculationCoverageRatio"] == 0.0
    assert result["metrics"]["tributaryScreeningMemberCount"] > 0


def test_current_run_stage_envelope_is_used_directly_and_covers_all_supports() -> None:
    project = _project()
    result = evaluate_support_deep_design(
        project,
        stage_results_override=[_stage_for_all_supports(project)],
        calculation_current_override=True,
    )
    assert result["evidence"]["forceEnvelope"]["source"] == "current_calculation_run"
    assert result["metrics"]["stagedCalculationMemberCount"] == len(project.retaining_system.supports)
    assert result["metrics"]["stagedCalculationCoverageRatio"] == 1.0
    assert result["calculationReady"] is result["screeningPass"]


def test_stale_historical_result_is_ignored_for_member_demand() -> None:
    project = _project()
    project.calculation_results.append(CalculationResult(
        projectId=project.id,
        caseId="missing-case",
        supportTopologyHash="stale-topology",
    ))
    result = evaluate_support_deep_design(project, include_members=True)
    assert result["evidence"]["forceEnvelope"]["staleCalculationIgnored"] is True
    assert result["metrics"]["stagedCalculationMemberCount"] == 0
    assert all(row["screeningDemandSource"] != "staged_calculation_envelope" for row in result["memberChecks"])


def test_runtime_observability_normalizes_dynamic_ids_and_bounds_cardinality() -> None:
    assert normalize_observability_path("/api/projects/project-12345678/tasks/task-abcdef12") == "/api/projects/:project_id/tasks/:task_id"
    obs = RuntimeObservability(path_limit=64)
    for index in range(100):
        obs.begin()
        obs.record(f"/custom/path-{index}", 200, 5.0)
    snapshot = obs.snapshot()
    assert snapshot["pathCardinality"] <= snapshot["pathAggregationBound"] + 1
    assert snapshot["activeRequestCount"] == 0
    assert snapshot["maximumConcurrentRequestCount"] >= 1


def test_v335_version_contract_is_synchronized() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 35, 0)
    manifest = version_manifest()
    major, minor, _patch = SOFTWARE_VERSION.split(".")
    assert f"v{major}.{minor}" in manifest["ruleSetVersion"]
    assert manifest["exportSchemaVersion"] == f"{major}.{minor}"
