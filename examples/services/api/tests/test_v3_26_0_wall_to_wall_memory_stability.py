from __future__ import annotations

import math

import pytest
from pathlib import Path

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import _available_topology_strategies, _hard_constraints
from app.services.industrial_readiness import run_geometry_qualification_suite
from app.calculation.engine import run_calculation
from app.version import SOFTWARE_VERSION


def _rect_project() -> Project:
    project = Project(name="v326-wall-to-wall")
    points = [Point2D(x=0, y=0), Point2D(x=80, y=0), Point2D(x=80, y=20), Point2D(x=0, y=20)]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -16.0)
    project.retaining_system = auto_supports(
        project.excavation,
        auto_diaphragm_wall(project.excavation),
        SupportLayoutConfig(topology_strategy="hybrid_diagonal"),
    )
    return project


def test_generated_non_ring_supports_have_two_wall_bearings() -> None:
    project = _rect_project()
    assert project.retaining_system is not None
    bad = [
        support.code
        for support in project.retaining_system.supports
        if support.support_role != "ring_strut" and not (support.start_face_code and support.end_face_code)
    ]
    assert bad == []
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics["supportToSupportTerminalCount"] == 0
    assert quality.metrics["unsupportedInternalEndpointCount"] == 0
    assert quality.metrics["directWallToWallSupportRatio"] == 1.0


def test_support_ending_on_main_strut_is_a_hard_failure_even_with_column() -> None:
    project = _rect_project()
    system = project.retaining_system
    assert system is not None
    main = next(item for item in system.supports if item.support_role == "main_strut")
    stub = main.model_copy(deep=True)
    stub.id = "support-stub"
    stub.code = "GS-STUB"
    stub.support_role = "secondary_strut"
    stub.end = Point2D(x=(main.start.x + main.end.x) / 2.0, y=(main.start.y + main.end.y) / 2.0)
    stub.span_length = math.hypot(stub.end.x - stub.start.x, stub.end.y - stub.start.y)
    stub.end_face_code = None
    stub.end_wall_connection = None
    stub.load_path_class = "supported_frame_node"
    system.supports.append(stub)
    # A column at the intersection does not supply an in-plane horizontal reaction.
    if system.columns:
        system.columns[0].location = stub.end
        system.columns[0].support_codes = [main.code, stub.code]
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics["supportToSupportTerminalCount"] >= 1
    assert any(item.category == "support_to_support_terminal" and item.severity == "fail" for item in quality.issues)
    hard = _hard_constraints(project, quality.metrics, system)
    assert hard["passed"] is False
    assert hard["supportNoSupportToSupportTerminal"] is False


def test_optimizer_does_not_offer_bidirectional_ty_topology() -> None:
    project = _rect_project()
    strategies = _available_topology_strategies(project)
    assert strategies == ["direct_grid", "hybrid_diagonal"]
    assert "bidirectional_grid" not in strategies


def test_calculation_and_deployment_defaults_limit_memory_pressure() -> None:
    root = Path(__file__).resolve().parents[3]
    workspace = (root / "apps" / "web" / "src" / "pages" / "ProjectWorkspace.tsx").read_text(encoding="utf-8")
    task_manager = (root / "services" / "api" / "app" / "tasks" / "manager.py").read_text(encoding="utf-8")
    deployment = (root / "scripts" / "build-and-start-production.sh").read_text(encoding="utf-8")
    assert "'calculation_full', { topN: 0 }" in workspace
    assert 'PITGUARD_TASK_WORKERS' in task_manager
    assert 'PITGUARD_HEAVY_TASK_CONCURRENCY' in task_manager
    assert 'malloc_trim' in task_manager
    assert 'PITGUARD_CALCULATION_RESULT_RETENTION' in deployment
    assert 'PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB' in task_manager
    assert 'PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB' in deployment
    assert 'MALLOC_ARENA_MAX=2' in deployment
    assert 'MemoryHigh=' in deployment and 'MemoryMax=' in deployment


def test_v326_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 26, 0)


def test_invalid_support_to_support_terminal_is_blocked_before_solver() -> None:
    project = _rect_project()
    system = project.retaining_system
    assert system is not None
    main = next(item for item in system.supports if item.support_role == "main_strut")
    stub = main.model_copy(deep=True)
    stub.id = "support-invalid-load-path"
    stub.code = "INVALID-TY"
    stub.support_role = "secondary_strut"
    stub.end = Point2D(x=(main.start.x + main.end.x) / 2.0, y=(main.start.y + main.end.y) / 2.0)
    stub.span_length = math.hypot(stub.end.x - stub.start.x, stub.end.y - stub.start.y)
    stub.end_face_code = None
    stub.end_wall_connection = None
    system.supports.append(stub)
    with pytest.raises(ValueError, match="水平支撑传力路径不成立"):
        run_calculation(project, auto_repair=False)


def test_general_shape_qualification_safely_blocks_unsupported_axial_topologies() -> None:
    suite = run_geometry_qualification_suite()
    assert suite["status"] == "pass"
    assert suite["controlledBlockCount"] >= 1
    controlled = [row for row in suite["cases"] if row["outcome"] == "controlled_block"]
    assert controlled
    assert all(row["calculationReady"] is False for row in controlled)
    assert all(row["supportToSupportTerminalCount"] == 0 for row in controlled)
    assert all(row["crossingCount"] == 0 for row in controlled)
