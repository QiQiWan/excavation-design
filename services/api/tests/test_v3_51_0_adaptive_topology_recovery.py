from __future__ import annotations

import json
from pathlib import Path

from app.schemas.domain import (
    DesignSettings,
    Point2D,
    Polyline2D,
    Project,
    SupportLayoutRepairSummary,
)
from app.services.design_qualification import build_design_qualification
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.support_layout_optimizer import (
    SUPPORT_CANDIDATE_CONTRACT_VERSION,
    _progressive_search_values,
    optimize_support_layout_candidates,
)


ROOT = Path(__file__).resolve().parents[3]


def _stepped_project() -> Project:
    payload = json.loads(
        (ROOT / "packages/sample-data/actual-project/actual_project_excavation_payload.json").read_text(
            encoding="utf-8"
        )
    )
    outline = Polyline2D(
        points=[Point2D(**item) for item in payload["outline"]["points"]],
        closed=True,
    )
    excavation = make_excavation_model(
        "harvest-lake-v351-regression",
        outline,
        0.0,
        -12.0,
    )
    excavation.support_axis_offset = 1.0
    settings = DesignSettings(supportMinStationSeparationM=4.0)
    project = Project(
        name="harvest-lake-v351-regression",
        excavation=excavation,
        designSettings=settings,
    )
    project.retaining_system = auto_diaphragm_wall(excavation, settings=settings)
    return project


def test_progressive_search_places_half_metre_refinements_before_extremes() -> None:
    assert _progressive_search_values(4.0, 6.5, 5.0, count=5) == [
        5.0,
        4.5,
        5.5,
        4.0,
        6.5,
    ]
    assert _progressive_search_values(4.0, 6.5, 4.0, count=5) == [
        4.0,
        4.5,
        5.0,
        5.5,
        6.5,
    ]


def test_failed_candidate_reports_exact_topology_control() -> None:
    project = _stepped_project()
    _, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        topology_family="direct_grid",
        search_config={
            "spacingMinM": 4.0,
            "spacingMaxM": 4.0,
            "preferredSpacingM": 4.0,
            "columnSpanMinM": 18.0,
            "columnSpanMaxM": 18.0,
            "maxTrials": 3,
            "candidatePoolLimit": 3,
        },
    )

    assert candidates
    assert all(not candidate.hard_constraints.get("passed") for candidate in candidates)
    first = candidates[0]
    qualification = first.variable_summary["topologyQualification"]
    assert first.hard_constraints["blockingCategories"] == ["support_station_cluster"]
    assert first.hard_constraints["hardFailureKeys"] == ["supportStationsMeetMinimumSeparation"]
    assert qualification["controlMetrics"]["supportStationClusterCount"] == 3
    assert "support_station_cluster" in first.constructability_note

    project.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
        candidateCount=len(candidates),
        candidates=candidates,
        status="manual_review",
        summary="controlled topology regression",
    )
    design_qualification = build_design_qualification(
        project,
        diagnostics={"archetype": "elongated_stepped_strip"},
        systems={"controlledBlock": True, "options": []},
    )
    topology_gate = next(
        gate for gate in design_qualification["gates"] if gate["code"] == "Q-TOPOLOGY"
    )
    assert topology_gate["evidence"]["candidateBlockingCategories"] == [
        "support_station_cluster"
    ]
    assert topology_gate["evidence"]["candidateControls"][0]["controlMetrics"][
        "supportStationClusterCount"
    ] == 3
    assert "support_station_cluster" in topology_gate["message"]


def test_adaptive_search_recovers_formal_candidate_on_stepped_outline() -> None:
    project = _stepped_project()
    selected_system, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        topology_family="direct_grid",
        search_config={
            "spacingMinM": 4.0,
            "spacingMaxM": 6.5,
            "preferredSpacingM": 4.0,
            "maxTrials": 3,
            "candidatePoolLimit": 6,
            "coreMode": True,
            "requireDiverseSchemes": False,
        },
    )

    feasible = [candidate for candidate in candidates if candidate.hard_constraints.get("passed")]
    assert selected_system is not None
    assert feasible
    assert all(candidate.target_spacing == 4.5 for candidate in feasible)
    assert len(selected_system.supports) == 168
    assert all(
        candidate.variable_summary.get("candidateContractVersion")
        == SUPPORT_CANDIDATE_CONTRACT_VERSION
        for candidate in feasible
    )
    assert all(
        candidate.variable_summary["topologyQualification"]["controlMetrics"][
            "supportStationClusterCount"
        ]
        == 0
        for candidate in feasible
    )
