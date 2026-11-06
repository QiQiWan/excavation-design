from __future__ import annotations

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import (
    MaterialDefinition,
    Point2D,
    Polyline2D,
    Project,
    RetainingSystem,
    SectionDefinition,
    SupportElement,
    SupportLayoutOptimizationCandidate,
)
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import make_column_elements
from app.services.support_layout_optimizer import (
    OBJECTIVE_WEIGHTS,
    _cleanliness_sort_key,
    preset_objective_weights,
)


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


def _project() -> Project:
    project = Project(name="clean-topology")
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


def test_valid_ty_node_is_counted_as_plan_junction_without_becoming_crossing() -> None:
    project = _project()
    main = _member("SP-M", (0.0, 10.0), (20.0, 10.0), start_face="S4", end_face="S2")
    branch = _member("SP-T", (10.0, 0.0), (10.0, 10.0), role="secondary_strut", start_face="S1")
    project.retaining_system = RetainingSystem(
        supports=[main, branch],
        columns=make_column_elements(project.excavation, [main, branch]),
        replacementPath=[{"levelIndex": 1, "action": "basement_slab_replacement"}],
    )

    quality = evaluate_support_layout_quality(project)

    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["internalJunctionCount"] == 1
    assert quality.metrics["highDegreeJunctionCount"] == 0
    assert quality.metrics["sameLevelPlanIntersectionPointCount"] == 1
    assert quality.metrics["planIntersectionComplexity"] == 1.0


def test_illegal_x_crossing_dominates_plan_intersection_complexity() -> None:
    project = _project()
    first = _member("SP-A", (2.0, 2.0), (18.0, 18.0), role="corner_diagonal")
    second = _member("SP-B", (18.0, 2.0), (2.0, 18.0), role="corner_diagonal")
    project.retaining_system = RetainingSystem(supports=[first, second], columns=[])

    quality = evaluate_support_layout_quality(project)

    assert quality.metrics["supportCrossingCount"] == 1
    assert quality.metrics["internalJunctionCount"] == 0
    assert quality.metrics["planIntersectionComplexity"] >= 100.0


def test_clean_layout_is_default_primary_weight_and_has_dedicated_preset() -> None:
    assert OBJECTIVE_WEIGHTS["supportCrossing"] == 80.0
    assert OBJECTIVE_WEIGHTS["junctionComplexity"] >= 60.0
    clean = preset_objective_weights("clean_support_layout")
    assert clean["supportCrossing"] == 80.0
    assert clean["junctionComplexity"] == 80.0


def test_candidate_order_prioritizes_plan_cleanliness_before_aggregate_score() -> None:
    clean = SupportLayoutOptimizationCandidate(
        score=70.0,
        crossingCount=0,
        junctionCount=1,
        metrics={
            "supportCrossingCount": 0,
            "planIntersectionComplexity": 1.0,
            "highDegreeJunctionCount": 0,
            "internalJunctionCount": 1,
        },
    )
    congested = SupportLayoutOptimizationCandidate(
        score=98.0,
        crossingCount=0,
        junctionCount=4,
        metrics={
            "supportCrossingCount": 0,
            "planIntersectionComplexity": 8.0,
            "highDegreeJunctionCount": 2,
            "internalJunctionCount": 4,
        },
    )

    assert _cleanliness_sort_key(clean) < _cleanliness_sort_key(congested)
