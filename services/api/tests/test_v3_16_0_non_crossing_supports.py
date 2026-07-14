from __future__ import annotations

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.calculation.engine import _support_topology_hash
from app.schemas.domain import (
    CalculationCase,
    CalculationResult,
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
from app.services.support_layout import _nearest_face_hit, make_column_elements
from app.services.support_layout_optimizer import _hard_constraints
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def _member(
    code: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    role: str = "main_strut",
    start_face: str | None = None,
    end_face: str | None = None,
) -> SupportElement:
    return SupportElement(
        code=code,
        levelIndex=1,
        elevation=-4.0,
        start=Point2D(x=start[0], y=start[1]),
        end=Point2D(x=end[0], y=end[1]),
        startFaceCode=start_face,
        endFaceCode=end_face,
        supportRole=role,
        spanLength=((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5,
        sectionType="rc_rectangular",
        section=SectionDefinition(width=1.0, height=1.0, name="1000x1000 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )


def _square_project() -> Project:
    project = Project(name="non-crossing")
    project.excavation = make_excavation_model(
        "pit",
        Polyline2D(
            points=[
                Point2D(x=0.0, y=0.0),
                Point2D(x=20.0, y=0.0),
                Point2D(x=20.0, y=20.0),
                Point2D(x=0.0, y=20.0),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    return project


def test_internal_point_is_not_misclassified_as_retaining_wall_endpoint() -> None:
    project = _square_project()
    assert _nearest_face_hit(Point2D(x=10.0, y=10.0), project.excavation, tolerance=0.35) is None
    assert _nearest_face_hit(Point2D(x=10.0, y=0.0), project.excavation, tolerance=0.35) is not None


def test_non_ring_crossing_is_hard_failure_and_diagnostics_identify_it() -> None:
    project = _square_project()
    a = _member("SP-A", (2.0, 2.0), (18.0, 18.0), role="corner_diagonal")
    b = _member("SP-B", (18.0, 2.0), (2.0, 18.0), role="corner_diagonal")
    project.retaining_system = RetainingSystem(
        supports=[a, b],
        columns=make_column_elements(project.excavation, [a, b]),
    )
    quality = evaluate_support_layout_quality(project)
    crossing = [issue for issue in quality.issues if issue.category == "support_crossing" and issue.severity == "fail"]
    assert crossing
    assert int(quality.metrics.get("nonRingCrossingCount", 0)) == 1

    checks = [
        {
            "ruleId": "QUALITY-SUPPORT_CROSSING",
            "status": "fail",
            "message": crossing[0].message,
        }
    ]
    diagnostics = build_calculation_diagnostics(project, CalculationCase(name="case", stages=[]), [], checks)
    codes = {item["code"] for item in diagnostics["rootCauses"]}
    assert "NON_RING_SUPPORT_CROSSING" in codes


def test_ty_node_with_column_is_valid_for_quality_and_optimizer_hard_constraints() -> None:
    project = _square_project()
    main = _member("SP-M", (0.0, 10.0), (20.0, 10.0), start_face="S4", end_face="S2")
    branch = _member(
        "SP-T",
        (10.0, 0.0),
        (10.0, 10.0),
        role="secondary_strut",
        start_face="S1",
    )
    columns = make_column_elements(project.excavation, [main, branch])
    assert any({main.code, branch.code}.issubset(set(column.support_codes)) for column in columns)
    project.retaining_system = RetainingSystem(supports=[main, branch], columns=columns)

    quality = evaluate_support_layout_quality(project)
    assert not [issue for issue in quality.issues if issue.category == "support_crossing" and issue.severity == "fail"]
    hard = _hard_constraints(project, quality.metrics, project.retaining_system)
    assert hard["endpointsOnWaleOrRingNodes"] is True
    assert hard["missingEndpointRatio"] == 0.0


def test_generated_non_ring_system_has_no_proper_crossings() -> None:
    # V3.20 contract: a concave return-wall pit may remain blocked when a
    # crossing-free direct wall-to-wall system cannot satisfy the wale-bay hard
    # limit.  The generator must not hide the problem by silently introducing
    # an ordinary support-to-support T/Y tie.
    project = Project(name="concave-non-crossing")
    points = [
        Point2D(x=-30.0, y=-20.0),
        Point2D(x=30.0, y=-20.0),
        Point2D(x=30.0, y=-4.0),
        Point2D(x=6.0, y=-4.0),
        Point2D(x=6.0, y=20.0),
        Point2D(x=-30.0, y=20.0),
    ]
    project.excavation = make_excavation_model("concave", Polyline2D(points=points, closed=True), 0.0, -16.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics.get("nonRingCrossingCount") == 0
    assert quality.metrics.get("supportOutsideExcavationCount") == 0
    if quality.status == "fail":
        assert any(issue.category == "wale_support_bay" for issue in quality.issues)
        assert not any(issue.category == "support_crossing" for issue in quality.issues)


def test_repository_invalidates_results_from_old_algorithm_contract(tmp_path) -> None:
    project = _square_project()
    main = _member("SP-M", (0.0, 10.0), (20.0, 10.0), start_face="S4", end_face="S2")
    project.retaining_system = RetainingSystem(
        supports=[main],
        columns=make_column_elements(project.excavation, [main]),
    )
    topology_hash = _support_topology_hash(project)
    project.calculation_results = [
        CalculationResult(
            projectId=project.id,
            caseId="legacy-case",
            supportTopologyHash=topology_hash,
            designIterationSummary={
                "algorithmVersion": "3.15.0-legacy",
                "ruleSetVersion": "2026.06-legacy",
            },
        )
    ]
    repo = ProjectRepository(SQLiteProjectStore(tmp_path / "migration.sqlite3"))
    repo.save(project)
    loaded = repo.require(project.id)
    assert loaded.calculation_results == []
    state = dict(loaded.advanced_engineering.get("calculationState") or {})
    archive = list(loaded.advanced_engineering.get("invalidatedCalculationArchive") or [])
    assert state.get("status") == "invalidated"
    assert state.get("requiresRecalculation") is True
    assert len(archive) >= 1
