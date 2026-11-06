from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.calculation.global_coupled import _fast_matrix_condition
from app.calculation.wale_beam import analyze_wale_continuous_beam
from app.schemas.domain import (
    CalculationCase,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    Project,
    RetainingSystem,
    SectionDefinition,
    SupportElement,
)
from app.services.calculation_diagnostics import build_calculation_diagnostics
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.services.support_layout import make_column_elements, repair_wale_support_bays, wale_support_bay_audit
from app.version import SOFTWARE_VERSION


def _support(code: str, x: float, level: int = 1, elevation: float = -4.0) -> SupportElement:
    return SupportElement(
        code=code,
        levelIndex=level,
        elevation=elevation,
        start=Point2D(x=x, y=0.0),
        end=Point2D(x=x, y=10.0),
        startFaceCode="S1",
        endFaceCode="S3",
        startWallConnection=Point2D(x=x, y=0.0),
        endWallConnection=Point2D(x=x, y=10.0),
        spanLength=10.0,
        sectionType="rc_rectangular",
        section=SectionDefinition(width=0.8, height=0.8, name="800x800 RC"),
        material=MaterialDefinition(name="Concrete", grade="C35"),
    )


def test_closed_perimeter_wale_uses_corner_joint_end_restraint_and_static_balance() -> None:
    excavation = make_excavation_model(
        "rect",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=20, y=0), Point2D(x=20, y=10), Point2D(x=0, y=10)], closed=True),
        0.0,
        -12.0,
    )
    segment = excavation.segments[0]
    analysis = analyze_wale_continuous_beam(
        pressure_line_load=100.0,
        segment=segment,
        supports=[_support("SP-1", 5.0), _support("SP-2", 15.0)],
        face_code="S1",
        stage_id="stage-test",
    )
    assert analysis.internal_force is not None
    assert "closed-perimeter" in analysis.internal_force.method
    assert analysis.internal_force.points[0].deflection == pytest.approx(0.0)
    assert analysis.internal_force.points[-1].deflection == pytest.approx(0.0)
    assert analysis.internal_force.max_moment <= 1250.0 * 1.01
    assert sum(item.reaction for item in analysis.reactions) == pytest.approx(100.0 * 20.0, rel=1e-6)


def test_actual_outline_wale_bay_preflight_adds_targeted_corner_fans() -> None:
    payload_path = Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project" / "actual_project_excavation_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    excavation = make_excavation_model(
        payload["name"],
        Polyline2D(points=[Point2D(**item) for item in payload["outline"]["points"]], closed=True),
        0.0,
        -16.6,
        0.5,
    )
    project = Project(name="actual-topology")
    project.excavation = excavation
    project.retaining_system = auto_supports(excavation, auto_diaphragm_wall(excavation))
    # V3.15 moves the V3.14 calculation preflight into Step 5, so the one-click
    # retaining design already leaves the screen with a valid direct wale-bay
    # topology.  A second repair call must be idempotent.
    after_generation = wale_support_bay_audit(project.excavation, project.retaining_system.supports)
    preflight = project.retaining_system.layout_summary["strengthTopologyPreflight"]["waleSupportBay"]
    assert preflight["auditAfter"]["status"] != "fail"
    assert after_generation["status"] == "pass"
    assert float(after_generation["maxBayM"]) <= float(after_generation["hardMaxBayM"])
    repair = repair_wale_support_bays(project)
    assert repair["changed"] is False
    assert repair["addedSupportCount"] == 0


def test_diagnostics_distinguish_wale_bay_repair_from_concave_wall_repair() -> None:
    project = Project(name="diagnostic")
    case = CalculationCase(name="case", stages=[])
    diagnostics = build_calculation_diagnostics(
        project,
        case,
        [],
        [],
        topology_preflight={
            "changed": True,
            "addedSupportCount": 4,
            "concaveReturnRepair": {"changed": False},
            "waleSupportBayRepair": {
                "changed": True,
                "addedSupportCount": 4,
                "failingFaces": ["S10"],
                "auditBefore": {"maxBayM": 14.0},
                "auditAfter": {"maxBayM": 7.0},
            },
        },
    )
    codes = {item["code"] for item in diagnostics["rootCauses"]}
    assert "WALE_SUPPORT_BAY_REPAIRED" in codes
    assert "UNRESTRAINED_CONCAVE_RETURN_WALL_REPAIRED" not in codes
    assert diagnostics["strengthDesignLoop"]["waleBayBeforeM"] == 14.0
    assert diagnostics["strengthDesignLoop"]["waleBayAfterM"] == 7.0


def test_condition_number_uses_bounded_cost_symmetric_method() -> None:
    import numpy as np

    condition, method = _fast_matrix_condition(np.diag([1.0, 2.0, 10.0]))
    assert condition == pytest.approx(10.0)
    assert method == "symmetric_eigenvalue_ratio"


def test_online_documentation_exposes_strength_driven_design_loop() -> None:
    docs = build_online_documentation()
    names = {item["name"] for item in docs["calculationPrinciples"]}
    assert "强度驱动的方案—构件联合迭代" in names
    wale = next(item for item in docs["calculationPrinciples"] if item["name"] == "围檩、支撑与全局耦合")
    assert "闭合环向多跨包络" in wale["method"]
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 19, 0)


def test_non_ring_crossing_fails_even_with_column_and_ty_node_passes() -> None:
    excavation = make_excavation_model(
        "grid-node",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=20, y=0), Point2D(x=20, y=20), Point2D(x=0, y=20)], closed=True),
        0.0,
        -12.0,
    )
    first = SupportElement(
        code="DB-L1-A", levelIndex=1, elevation=-4.0,
        start=Point2D(x=2.0, y=2.0), end=Point2D(x=14.0, y=14.0),
        supportRole="corner_diagonal", spanLength=16.971,
        sectionType="rc_rectangular", section=SectionDefinition(width=1.2, height=1.2, name="1200x1200 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )
    second = SupportElement(
        code="DB-L1-B", levelIndex=1, elevation=-4.0,
        start=Point2D(x=14.0, y=2.0), end=Point2D(x=2.0, y=14.0),
        supportRole="corner_diagonal", spanLength=16.971,
        sectionType="rc_rectangular", section=SectionDefinition(width=1.2, height=1.2, name="1200x1200 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )
    crossing_columns = make_column_elements(excavation, [first, second])
    project = Project(name="crossing-quality")
    project.excavation = excavation
    project.retaining_system = RetainingSystem(supports=[first, second], columns=crossing_columns)
    quality = evaluate_support_layout_quality(project)
    assert [item for item in quality.issues if item.category == "support_crossing" and item.severity == "fail"]

    main = SupportElement(
        code="SP-L1-M", levelIndex=1, elevation=-4.0,
        start=Point2D(x=2.0, y=10.0), end=Point2D(x=18.0, y=10.0),
        startFaceCode="S4", endFaceCode="S2", supportRole="main_strut", spanLength=16.0,
        sectionType="rc_rectangular", section=SectionDefinition(width=1.2, height=1.2, name="1200x1200 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )
    branch = SupportElement(
        code="SP-L1-T", levelIndex=1, elevation=-4.0,
        start=Point2D(x=10.0, y=2.0), end=Point2D(x=10.0, y=10.0),
        startFaceCode="S1", supportRole="secondary_strut", spanLength=8.0,
        sectionType="rc_rectangular", section=SectionDefinition(width=1.0, height=1.0, name="1000x1000 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )
    ty_columns = make_column_elements(excavation, [main, branch])
    shared = [item for item in ty_columns if {main.code, branch.code}.issubset(set(item.support_codes))]
    assert shared
    assert shared[0].location.x == pytest.approx(10.0, abs=1e-3)
    assert shared[0].location.y == pytest.approx(10.0, abs=1e-3)

    project.retaining_system = RetainingSystem(supports=[main, branch], columns=ty_columns)
    quality = evaluate_support_layout_quality(project)
    assert not [item for item in quality.issues if item.category == "support_crossing" and item.severity == "fail"]

