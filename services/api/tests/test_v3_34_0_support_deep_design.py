from __future__ import annotations

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_deep_design import evaluate_support_deep_design, optimize_support_deep_design
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.version import SOFTWARE_VERSION


def _project(length: float = 60.0, width: float = 20.0, depth: float = 12.0) -> Project:
    points = [Point2D(x=0, y=0), Point2D(x=length, y=0), Point2D(x=length, y=width), Point2D(x=0, y=width)]
    excavation = make_excavation_model("deep-design", Polyline2D(points=points, closed=True), 0.0, -depth)
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="hybrid_diagonal"),
    )
    return Project(name="deep-design", excavation=excavation, retainingSystem=retaining)


def test_deep_design_reports_member_stability_construction_effects_and_connectivity() -> None:
    project = _project()
    result = evaluate_support_deep_design(project, include_members=True)
    assert result["metrics"]["supportCount"] == len(project.retaining_system.supports)
    assert result["metrics"]["maximumInteractionUtilization"] >= 0.0
    assert result["metrics"]["maximumSlenderness"] > 0.0
    assert result["metrics"]["maximumConstructionEffectRatio"] >= 0.0
    assert result["metrics"]["maximumSupportForceCoefficientOfVariation"] >= 0.0
    assert result["metrics"]["maximumSupportForcePeakToMeanRatio"] >= 1.0
    assert result["model"]["interaction"].startswith("eta")
    assert result["governingMembers"]
    assert all("thermalKn" in row and "gapClosureKn" in row for row in result["governingMembers"])


def test_bounded_deep_design_iteration_upgrades_section_or_columns_without_changing_topology() -> None:
    project = _project(length=80.0, width=26.0, depth=18.0)
    # Deliberately weaken one adopted member to trigger a bounded section upgrade.
    support = project.retaining_system.supports[0]
    support.section.width = 0.30
    support.section.height = 0.30
    before_ids = [item.id for item in project.retaining_system.supports]
    result = optimize_support_deep_design(project, max_iterations=3)
    assert result["iterationCount"] >= 1
    assert result["topologyChanged"] is False
    assert [item.id for item in project.retaining_system.supports] == before_ids
    assert support.id in result["changedSupportIds"]
    assert support.section.width >= 0.30
    assert project.retaining_system.layout_summary["supportDeepDesign"]["iterationCount"] >= 1


def test_candidate_optimizer_embeds_deep_design_metrics_and_terms() -> None:
    project = _project(length=90.0, width=22.0, depth=15.0)
    _system, candidates = optimize_support_layout_candidates(project, max_candidates=2, preset="clean_support_layout")
    assert candidates
    candidate = candidates[0]
    assert "memberUtilization" in candidate.objective_terms
    assert "bucklingRisk" in candidate.objective_terms
    assert "forceUniformity" in candidate.objective_terms
    assert "supportMaximumInteractionUtilization" in candidate.metrics
    assert "supportDeepDesignHardPass" in candidate.hard_constraints
    assert "deepDesignScreening" in candidate.variable_summary


def test_v334_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 34, 0)
