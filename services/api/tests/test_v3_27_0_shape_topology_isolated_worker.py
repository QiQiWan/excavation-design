from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig, plan_shape_diagnostics
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.storage.task_store import SQLiteTaskStore
from app.tasks.manager import TaskManager
from app.version import SOFTWARE_VERSION


def _project(name: str, raw: list[tuple[float, float]]) -> Project:
    project = Project(name=name)
    project.excavation = make_excavation_model(
        name,
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in raw], closed=True),
        0.0,
        -16.0,
    )
    project.retaining_system = auto_supports(
        project.excavation,
        auto_diaphragm_wall(project.excavation),
        SupportLayoutConfig(topology_strategy="hybrid_diagonal"),
    )
    return project


def test_long_strip_terminal_faces_are_closed_by_parallel_corner_families() -> None:
    project = _project("long-strip", [(0, 0), (160, 0), (160, 33), (0, 33)])
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics["waleSupportBayFailCount"] == 0
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["cornerBraceParallelismIssueCount"] == 0
    assert quality.metrics["cornerBraceEndpointCongestionCount"] == 0
    diagnostics = project.retaining_system.layout_summary["planShapeDiagnostics"]
    assert diagnostics["classification"] == "slender_quadrilateral"
    # Four corners need at least three independent braces per level for a 33 m end face.
    assert sum(item.support_role == "corner_diagonal" for item in project.retaining_system.supports) >= 4 * 3 * 3


@pytest.mark.parametrize(
    ("name", "raw", "classification"),
    [
        ("l-shape", [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)], "orthogonal_concave_corridor"),
        ("u-shape", [(0, 0), (80, 0), (80, 50), (55, 50), (55, 20), (25, 20), (25, 50), (0, 50)], "orthogonal_concave_corridor"),
        ("near-square", [(0, 0), (30, 0), (30, 30), (0, 30)], "near_square_quadrilateral"),
    ],
)
def test_non_axial_plan_families_are_safely_blocked_without_arbitrary_diagonals(name, raw, classification) -> None:
    project = _project(name, raw)
    quality = evaluate_support_layout_quality(project)
    diagnostics = project.retaining_system.layout_summary["planShapeDiagnostics"]
    assert diagnostics["classification"] == classification
    assert quality.status == "fail"
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["supportToSupportTerminalCount"] == 0
    assert quality.metrics["unsupportedInternalEndpointCount"] == 0
    assert all(item.support_role == "main_strut" for item in project.retaining_system.supports)
    fail_categories = {item.category for item in quality.issues if item.severity == "fail"}
    assert fail_categories == {"wale_support_bay"}




def test_optimizer_returns_real_long_strip_alternatives_and_single_controlled_block() -> None:
    long_project = _project("long-strip-optimizer", [(0, 0), (160, 0), (160, 33), (0, 33)])
    _, long_candidates = optimize_support_layout_candidates(
        long_project, max_candidates=3, preset="clean_support_layout"
    )
    assert len(long_candidates) >= 2
    assert all(item.hard_constraints.get("passed") for item in long_candidates)
    assert all(int(item.metrics.get("waleSupportBayFailCount", 0) or 0) == 0 for item in long_candidates)
    assert len({str((item.variable_summary or {}).get("geometryFingerprint")) for item in long_candidates}) >= 2

    l_project = _project(
        "l-shape-optimizer",
        [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)],
    )
    _, l_candidates = optimize_support_layout_candidates(
        l_project, max_candidates=3, preset="clean_support_layout"
    )
    # V3.48+ retains up to three geometry-distinct diagnostic schemes so the
    # engineer can compare controlled alternatives. None may be promoted as a
    # formal solution while the transfer system remains incomplete.
    assert 1 <= len(l_candidates) <= 3
    assert all(item.variable_summary.get("capabilityOutcome") == "controlled_block" for item in l_candidates)
    assert all(not bool(item.hard_constraints.get("passed")) for item in l_candidates)
    assert all(int(item.metrics.get("supportCrossingCount", 0) or 0) == 0 for item in l_candidates)
    assert len({str((item.variable_summary or {}).get("geometryFingerprint")) for item in l_candidates}) == len(l_candidates)

def test_shape_diagnostics_expose_topology_recommendation() -> None:
    rows = [Point2D(x=x, y=y) for x, y in [(0, 0), (100, 0), (100, 25), (0, 25)]]
    diagnostics = plan_shape_diagnostics(rows)
    assert diagnostics["slenderPlan"] is True
    assert diagnostics["orthogonalPlan"] is True
    assert diagnostics["recommendedTopology"] == "short_span_direct_grid_with_terminal_parallel_braces"


def test_external_task_mode_deduplicates_and_supports_atomic_worker_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "task.sqlite3"))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "external")
    manager = TaskManager()
    first = manager.submit("project-1", "calculation_full", {"topN": 0})
    second = manager.submit("project-1", "calculation_full", {"topN": 0})
    assert first.id == second.id
    assert manager.metrics()["taskExecutionMode"] == "external"
    claimed = SQLiteTaskStore().claim_next()
    assert claimed is not None
    assert claimed["id"] == first.id
    assert claimed["status"] == "running"
    assert SQLiteTaskStore().claim_next() is None


def test_production_uses_separate_api_and_calculation_worker() -> None:
    root = Path(__file__).resolve().parents[3]
    deployment = (root / "scripts" / "build-and-start-production.sh").read_text(encoding="utf-8")
    restart = (root / "restart-production.sh").read_text(encoding="utf-8")
    workspace = (root / "apps" / "web" / "src" / "pages" / "ProjectWorkspace.tsx").read_text(encoding="utf-8")
    manager = (root / "services" / "api" / "app" / "tasks" / "manager.py").read_text(encoding="utf-8")
    design_router = (root / "services" / "api" / "app" / "routers" / "design.py").read_text(encoding="utf-8")
    expert_router = (root / "services" / "api" / "app" / "routers" / "expert_design.py").read_text(encoding="utf-8")
    rebar_router = (root / "services" / "api" / "app" / "routers" / "rebar.py").read_text(encoding="utf-8")
    assert "pitguard-worker" in deployment
    assert "PITGUARD_TASK_EXECUTION_MODE=external" in deployment
    assert "PITGUARD_TASK_EXECUTION_MODE=worker" in deployment
    assert "app.tasks.worker_daemon" in deployment
    assert "WORKER_MEMORY_MAX_MB" in deployment
    assert "CPUQuota=$WORKER_CPU_QUOTA" in deployment
    assert "MemoryMax=6500M" not in deployment
    assert "PITGUARD_API_MEMORY_MAX:-4G" in deployment
    assert "OOMScoreAdjust=-500" in deployment
    assert "CPUWeight=20" in deployment
    assert "pitguard-worker" in restart
    assert "正在由独立计算进程运行当前方案" in workspace
    assert "support_layout_optimization" in workspace
    assert "正在由独立进程生成 A/B/C" in workspace
    assert '"support_layout_optimization"' in manager
    assert "_run_support_layout_optimization" in manager
    assert "_require_embedded_support_optimization" in design_router
    assert 'task_manager.submit(project.id, "calculation_full"' in expert_router
    assert '"rebar_design"' in rebar_router


def test_v327_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 27, 0)
