from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig, plan_shape_diagnostics
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.version import SOFTWARE_VERSION


def pts(raw: list[tuple[float, float]]) -> list[Point2D]:
    return [Point2D(x=x, y=y) for x, y in raw]


@pytest.mark.parametrize(
    ("expected", "raw"),
    [
        ("slender_rectangle", [(0, 0), (100, 0), (100, 25), (0, 25)]),
        ("near_square_rectangle", [(0, 0), (30, 0), (30, 30), (0, 30)]),
        ("trapezoid", [(0, 0), (60, 0), (50, 25), (5, 25)]),
        ("parallelogram", [(0, 0), (60, 0), (70, 25), (10, 25)]),
        ("triangle", [(0, 0), (50, 0), (20, 35)]),
        ("l_shape", [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]),
        ("u_shape", [(0, 0), (80, 0), (80, 50), (55, 50), (55, 20), (25, 20), (25, 50), (0, 50)]),
        ("t_shape", [(0, 0), (80, 0), (80, 20), (50, 20), (50, 60), (30, 60), (30, 20), (0, 20)]),
        ("z_shape", [(0, 0), (50, 0), (50, 40), (80, 40), (80, 60), (30, 60), (30, 20), (0, 20)]),
        ("h_shape", [(0, 0), (20, 0), (20, 25), (60, 25), (60, 0), (80, 0), (80, 60), (60, 60), (60, 35), (20, 35), (20, 60), (0, 60)]),
    ],
)
def test_exact_plan_archetypes(expected: str, raw: list[tuple[float, float]]) -> None:
    diagnostics = plan_shape_diagnostics(pts(raw))
    assert diagnostics["archetype"] == expected
    assert diagnostics["engineeringScheme"]["name"]
    assert diagnostics["supportedTopologyFamilies"]
    assert len(diagnostics["designWorkflow"]) == 7


def test_circle_and_ellipse_classification() -> None:
    circle = [Point2D(x=20 * math.cos(i * math.tau / 16), y=20 * math.sin(i * math.tau / 16)) for i in range(16)]
    ellipse = [Point2D(x=35 * math.cos(i * math.tau / 20), y=18 * math.sin(i * math.tau / 20)) for i in range(20)]
    assert plan_shape_diagnostics(circle)["archetype"] == "circle"
    assert plan_shape_diagnostics(ellipse)["archetype"] == "ellipse"


def project_for(name: str, raw: list[tuple[float, float]], strategy: str) -> Project:
    excavation = make_excavation_model(name, Polyline2D(points=pts(raw), closed=True), 0.0, -16.0)
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy=strategy),
    )
    return Project(name=name, excavation=excavation, retainingSystem=retaining)


def test_near_square_uses_closed_ring_and_radial_supports() -> None:
    project = project_for("square", [(0, 0), (30, 0), (30, 30), (0, 30)], "balanced_grid")
    quality = evaluate_support_layout_quality(project)
    assert project.retaining_system.layout_summary["planShapeDiagnostics"]["archetype"] == "near_square_rectangle"
    assert project.retaining_system.ring_beams
    assert all(item.support_role == "ring_strut" for item in project.retaining_system.supports)
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["waleSupportBayFailCount"] == 0


def test_concave_zones_generate_only_wall_to_wall_preliminary_supports_and_remain_controlled() -> None:
    project = project_for("L", [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)], "zoned_direct")
    diagnostics = project.retaining_system.layout_summary["planShapeDiagnostics"]
    preflight = project.retaining_system.layout_summary["strengthTopologyPreflight"]
    quality = evaluate_support_layout_quality(project)
    assert diagnostics["archetype"] == "l_shape"
    assert diagnostics["zoneCount"] >= 2
    assert all(item.load_path_class == "wall_to_wall" for item in project.retaining_system.supports)
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["supportToSupportTerminalCount"] == 0
    assert preflight["shapeTransferSystemRequired"] is True
    assert preflight["shapeTransferSystemComplete"] is False
    assert preflight["calculationReady"] is False


def test_concave_optimizer_returns_one_controlled_preliminary_scheme() -> None:
    project = project_for("T", [(0, 0), (80, 0), (80, 20), (50, 20), (50, 60), (30, 60), (30, 20), (0, 20)], "zoned_direct")
    _, candidates = optimize_support_layout_candidates(project, max_candidates=3, preset="clean_support_layout")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.variable_summary["capabilityOutcome"] == "controlled_block"
    assert candidate.hard_constraints["shapeTransferSystemRequired"] is True
    assert candidate.hard_constraints["shapeTransferSystemComplete"] is False
    assert int(candidate.metrics.get("supportCrossingCount", 0) or 0) == 0


def test_shape_intelligence_is_exposed_in_api_and_workspace() -> None:
    root = Path(__file__).resolve().parents[3]
    router = (root / "services" / "api" / "app" / "routers" / "design.py").read_text(encoding="utf-8")
    workspace = (root / "apps" / "web" / "src" / "pages" / "ProjectWorkspace.tsx").read_text(encoding="utf-8")
    client = (root / "apps" / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert '"/plan-shape-diagnostics"' in router
    assert '"/auto-supports-by-shape"' in router
    assert "getPlanShapeDiagnostics" in client
    assert "autoSupportsByShape" in client
    assert "平面形状识别与支撑体系决策" in workspace
    assert "识别形状并生成围护体系" in workspace


def test_v328_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 28, 0)
