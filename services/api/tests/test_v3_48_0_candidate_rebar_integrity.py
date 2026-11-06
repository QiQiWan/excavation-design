from __future__ import annotations

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import Point2D, Polyline2D, Project, ReinforcementGroup, SupportElement
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates


def _base_project() -> Project:
    excavation = make_excavation_model(
        "v348",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=160, y=0), Point2D(x=160, y=33), Point2D(x=0, y=33)], closed=True),
        0.0,
        -16.0,
    )
    project = Project(name="v348", excavation=excavation)
    project.retaining_system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="hybrid_diagonal"),
    )
    return project


def test_optimizer_does_not_force_label_only_duplicates() -> None:
    project = _base_project()
    _, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="clean_support_layout",
        search_config={"requireDiverseSchemes": True, "coreMode": True},
    )
    assert candidates
    signatures = [candidate.variable_summary.get("actualGeometrySignature") for candidate in candidates]
    assert all(signature for signature in signatures)
    assert len({str(signature) for signature in signatures}) == len(signatures)
    for candidate in candidates[1:]:
        assert float(candidate.variable_summary.get("minimumGeometryDeltaToSelected", 0.0)) >= 0.0


def test_legacy_longitudinal_only_support_is_resolved_from_applied_scheme() -> None:
    project = _base_project()
    assert project.retaining_system is not None
    support = SupportElement(
        code="SP-L1-01",
        levelIndex=1,
        elevation=-3.0,
        start=Point2D(x=4, y=10),
        end=Point2D(x=156, y=10),
    )
    support.reinforcement = [
        ReinforcementGroup(
            name="支撑纵筋",
            barType="longitudinal",
            diameter=32,
            count=16,
            grade="HRB400",
            locationDescription="legacy applied object retained longitudinal group only",
        )
    ]
    project.retaining_system.supports = [support]
    project.retaining_system.rebar_design_scheme = {
        "supportSchemes": [{
            "hostId": support.id,
            "hostCode": support.code,
            "status": "warning",
            "longitudinal": {"count": 16, "diameterMm": 32, "grade": "HRB400"},
            "endZones": {"lengthM": 1.8, "stirrupDiameterMm": 14, "stirrupSpacingMm": 100},
            "middleZone": {"lengthM": 148.4, "stirrupDiameterMm": 12, "stirrupSpacingMm": 180},
        }]
    }
    payload = build_rebar_ifc_visualization(project, max_bars=180)
    expected = {"longitudinal", "distribution", "stirrup", "tie", "additional"}
    assert expected.issubset(set(payload["summary"]["supportBarTypesPresent"]))
    assert payload["summary"]["supportBarTypesMissing"] == []
    contract = payload["supportContracts"][0]
    assert set(contract["synthesizedBarTypes"]) == {"distribution", "stirrup", "tie", "additional"}
    assert contract["status"] == "complete"


def test_support_contract_reports_missing_types_without_applied_scheme() -> None:
    project = _base_project()
    assert project.retaining_system is not None
    support = SupportElement(
        code="SP-L1-02",
        levelIndex=1,
        elevation=-3.0,
        start=Point2D(x=4, y=14),
        end=Point2D(x=156, y=14),
    )
    support.reinforcement = [ReinforcementGroup(name="纵筋", barType="longitudinal", diameter=28, count=12, grade="HRB400", locationDescription="legacy")]
    project.retaining_system.supports = [support]
    project.retaining_system.rebar_design_scheme = {}
    payload = build_rebar_ifc_visualization(project, max_bars=100)
    assert set(payload["summary"]["supportBarTypesMissing"]) == {"distribution", "stirrup", "tie", "additional"}
    assert payload["supportContracts"][0]["status"] == "incomplete"


def test_calculation_trace_numeric_reader_accepts_structured_evidence() -> None:
    from app.services.calculation_trace import _as_numeric

    assert _as_numeric({"designValue": {"value": 123.45, "governingMember": "SP-01"}}) == 123.45
    assert _as_numeric({"range": {"minimum": -8.0, "maximum": 10.0}}) == 10.0
    assert _as_numeric([{"value": 2.0}, {"value": -5.0}]) == -5.0
