from __future__ import annotations

from copy import deepcopy

import pytest

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project
from app.quality.formal_gate import build_formal_report_gate
from app.routers.projects import _build_updated_project
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project, SupportLayoutRepairSummary
from app.services.calculation_state import invalidate_calculation_state
from app.services.concave_transfer_delivery import save_concave_transfer_detailing_approval
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_candidate_contract import (
    candidate_is_current,
    candidate_set_state,
    support_candidate_source_hash,
)
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.services.support_layout_repair import adopt_support_layout_candidate
from app.services.support_topology_contract import support_topology_hash


L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TRANSFER_TEMPLATES = [
    "compact_elbow_ring",
    "balanced_elbow_ring",
    "extended_elbow_ring",
]


def _project(*, transfer_template: str = "none") -> Project:
    excavation = make_excavation_model(
        "V3.66 L shape",
        Polyline2D(
            points=[Point2D(x=x, y=y) for x, y in L_SHAPE],
            closed=True,
        ),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(
            topology_strategy="zoned_direct",
            concave_transfer_template=transfer_template,
        ),
    )
    return Project(name="V3.66 L shape", excavation=excavation, retainingSystem=system)


@pytest.fixture(scope="module")
def optimized_project() -> Project:
    project = _project(transfer_template="balanced_elbow_ring")
    best, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TRANSFER_TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 12,
            "candidatePoolLimit": 6,
        },
    )
    assert best is not None
    state = candidate_set_state(project, candidates)
    project.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
        candidateSourceHash=state["currentSourceHash"],
        candidateState=state["state"],
        formalCandidateCount=state["formalCandidateCount"],
        controlledCandidateCount=state["controlledCandidateCount"],
        staleCandidateCount=state["staleCandidateCount"],
        comparisonEligibility=state,
        candidateCount=len(candidates),
        bestCandidateId=candidates[0].id,
        selectedCandidateId=candidates[0].id,
        candidates=candidates,
        status="warning",
        summary="V3.66 formal concave candidates",
    )
    return project


def test_p0_candidate_source_hash_changes_with_geometry_and_stale_candidate_is_rejected(optimized_project: Project) -> None:
    project = deepcopy(optimized_project)
    candidate = project.retaining_system.support_layout_repair.candidates[0]
    original_hash = support_candidate_source_hash(project)
    assert candidate_is_current(project, candidate)

    project.excavation.outline.points[1].x += 1.0
    assert support_candidate_source_hash(project) != original_hash
    assert not candidate_is_current(project, candidate)

    result = adopt_support_layout_candidate(project, candidate.id)
    assert result.status == "fail"
    assert result.candidate_state == "stale"
    assert "已阻止采用" in result.summary


def test_p0_geometry_invalidation_archives_and_clears_candidate_workspace(optimized_project: Project) -> None:
    project = deepcopy(optimized_project)
    old_count = len(project.retaining_system.support_layout_repair.candidates)
    project.excavation.outline.points[2].y += 0.5

    state = invalidate_calculation_state(
        project,
        reason="excavation geometry changed in V3.66 regression",
        rebuild_cases=False,
        invalidate_candidates=True,
    )
    repair = project.retaining_system.support_layout_repair
    assert state["invalidatedCandidateCount"] == old_count
    assert repair.candidates == []
    assert repair.candidate_state == "not_generated"
    assert repair.stale_candidate_count == old_count
    archive = project.advanced_engineering["staleSupportCandidateArchive"]
    assert archive[-1]["candidateCount"] == old_count



def test_p0_generic_design_settings_patch_clears_stale_candidates(optimized_project: Project) -> None:
    project = deepcopy(optimized_project)
    settings = project.design_settings.model_dump(mode="json", by_alias=True)
    settings["surcharge"] = float(settings.get("surcharge") or 20.0) + 5.0
    updated, changed = _build_updated_project(project, {"designSettings": settings}, actor="v3.66-regression")
    assert changed == ["designSettings"]
    repair = updated.retaining_system.support_layout_repair
    assert repair.candidates == []
    assert repair.candidate_state == "not_generated"
    assert repair.stale_candidate_count == 3
    assert updated.advanced_engineering["staleSupportCandidateArchive"][-1]["candidateCount"] == 3

def test_p1_legacy_zoned_direct_remains_controlled_without_transfer_template() -> None:
    project = _project()
    quality = evaluate_support_layout_quality(project)
    audit = project.retaining_system.layout_summary["transferSystem"]
    assert len(project.retaining_system.supports) == 36
    assert len(project.retaining_system.ring_beams) == 0
    assert quality.status == "fail"
    assert quality.metrics["waleSupportBayFailCount"] == 9
    assert audit["calculationReady"] is False


def test_p1_concave_ring_closes_every_level_and_every_wall_face() -> None:
    project = _project(transfer_template="balanced_elbow_ring")
    quality = evaluate_support_layout_quality(project)
    audit = project.retaining_system.layout_summary["transferSystem"]

    assert audit["required"] is True
    assert audit["calculationReady"] is True
    assert audit["officialIssueReady"] is False
    assert audit["ringClosed"] is True
    assert audit["faceCoverageComplete"] is True
    assert all(row["closed"] for row in audit["ringClosureByLevel"])
    assert all(row["componentCount"] == 1 for row in audit["ringClosureByLevel"])
    assert all(row["invalidDegreeNodeCount"] == 0 for row in audit["ringClosureByLevel"])
    assert set(audit["coveredFaceCountByLevel"].values()) == {audit["requiredFaceCount"]}
    assert audit["zoneGraph"]["schema"] == "support-zone-graph-v2"
    assert len(project.retaining_system.supports) == 72
    assert len(project.retaining_system.ring_beams) == 18
    assert quality.status == "warning"
    assert quality.score == 84.0
    assert quality.metrics["waleSupportBayFailCount"] == 0
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["supportOutsideExcavationCount"] == 0
    assert quality.metrics["unsupportedInternalEndpointCount"] == 0


def test_p2_optimizer_returns_three_formal_transfer_system_alternatives(optimized_project: Project) -> None:
    project = optimized_project
    repair = project.retaining_system.support_layout_repair
    candidates = repair.candidates
    assert len(candidates) == 3
    assert {row.variable_summary["transferSystemTemplate"] for row in candidates} == set(TRANSFER_TEMPLATES)
    assert all(row.hard_constraints["passed"] for row in candidates)
    assert all(row.variable_summary["formalSchemeEligible"] for row in candidates)
    assert all(candidate_is_current(project, row) for row in candidates)
    assert all(row.plan_geometry["transferBeams"] for row in candidates)
    assert repair.formal_candidate_count == 3
    assert repair.comparison_eligibility["comparisonAllowed"] is True


def test_p2_complete_comparison_rejects_stale_or_diagnostic_candidate_sets(optimized_project: Project) -> None:
    stale = deepcopy(optimized_project)
    stale.excavation.outline.points[0].x -= 0.25
    with pytest.raises(ValueError, match="候选来源已过期"):
        run_candidate_comparison_for_project(stale, top_n=3)

    diagnostic = _project()
    _best, rows = optimize_support_layout_candidates(
        diagnostic,
        max_candidates=3,
        preset="balanced",
        search_config={"maxTrials": 3},
    )
    state = candidate_set_state(diagnostic, rows)
    diagnostic.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
        candidateSourceHash=state["currentSourceHash"],
        candidateState=state["state"],
        comparisonEligibility=state,
        candidateCount=len(rows),
        candidates=rows,
        status="fail",
    )
    with pytest.raises(ValueError, match="诊断试案不能进入完整比选"):
        run_candidate_comparison_for_project(diagnostic, top_n=3)


def test_p3_topology_hash_includes_transfer_beam_geometry() -> None:
    project = _project(transfer_template="balanced_elbow_ring")
    before = support_topology_hash(project)
    project.retaining_system.ring_beams[0].axis.points[0].x += 0.125
    after = support_topology_hash(project)
    assert before != after


def test_p3_formal_gate_keeps_concave_detailing_as_hard_block() -> None:
    project = _project(transfer_template="balanced_elbow_ring")
    quality = evaluate_support_layout_quality(project)
    gate = build_formal_report_gate(project, quality, None)
    categories = {item.category for item in gate.blocking_items}
    assert gate.allowed_for_official_issue is False
    assert "shape_transfer_stage_analysis" in categories
    assert gate.summary["transferSystemCalculationReady"] is True
    assert gate.summary["transferSystemOfficialIssueReady"] is False



def test_p3_signed_detailing_evidence_unlocks_only_the_matching_topology() -> None:
    project = _project(transfer_template="balanced_elbow_ring")
    project.calculation_cases = build_default_construction_cases(project)
    run_calculation(
        project,
        project.calculation_cases[0],
        auto_repair=False,
        include_candidate_comparison=False,
    )
    quality = evaluate_support_layout_quality(project)
    with pytest.raises(ValueError):
        save_concave_transfer_detailing_approval(
            project,
            evidence={
                "frameAnalysisStatus": "pass",
                "nodeDetailingStatus": "pass",
                "stageReviewStatus": "approved",
                "reactionIterationStatus": "pass",
                "spatialEffectStatus": "pass",
                "torsionDetailingStatus": "pass",
            },
            reviewer="registered-reviewer",
            notes="Legacy V3.66 evidence must remain blocked by V3.71 data and credential gates.",
            evidence_refs=["calc:ring-frame-001", "drawing:transfer-node-001"],
        )
    gate = build_formal_report_gate(project, quality, None)
    assert "shape_transfer_detailing" in {item.category for item in gate.blocking_items}
    assert gate.summary["transferSystemOfficialIssueReady"] is False

def test_p3_concave_transfer_scheme_runs_full_calculation_but_preserves_delivery_gate() -> None:
    project = _project(transfer_template="balanced_elbow_ring")
    project.calculation_cases = build_default_construction_cases(project)
    result = run_calculation(
        project,
        project.calculation_cases[0],
        auto_repair=False,
        include_candidate_comparison=False,
    )
    assert result.support_topology_hash == support_topology_hash(project)
    assert len(result.stage_results) > 0
    assert result.governing_values.max_support_axial_force > 0
    assert result.formal_report_gate is not None
    categories = {item.category for item in result.formal_report_gate.blocking_items}
    assert "shape_transfer_detailing" in categories
    assert result.formal_report_gate.allowed_for_official_issue is False
