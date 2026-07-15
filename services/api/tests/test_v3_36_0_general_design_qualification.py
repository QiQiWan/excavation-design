from __future__ import annotations

import math

from app.schemas.domain import Borehole, Point2D, Polyline2D, Project
from app.services.design_qualification import build_design_qualification, build_support_system_options
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.project_coordinate_audit import audit_project_coordinate_alignment


def _project(name: str, points: list[tuple[float, float]]) -> Project:
    excavation = make_excavation_model(
        name,
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in points], closed=True),
        0.0,
        -12.0,
    )
    return Project(name=name, excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def test_coordinate_audit_detects_unconfirmed_translation_without_auto_applying_it() -> None:
    project = _project("offset", [(-115, -14), (115, -14), (115, 14), (-115, 14)])
    project.boreholes = [
        Borehole(code="BH1", x=20, y=20, collarElevation=0.0, depth=50.0),
        Borehole(code="BH2", x=180, y=180, collarElevation=0.0, depth=50.0),
    ]
    result = audit_project_coordinate_alignment(project)
    assert result["status"] in {"warning", "manual_review"}
    assert result["requiresConfirmation"] is True
    assert result["suggestedTranslation"]["automaticApplicationAllowed"] is False
    assert result["centerOffsetM"] > 100.0


def test_system_option_catalog_is_general_across_multiple_plan_families() -> None:
    shapes = {
        "slender": [(0, 0), (80, 0), (80, 18), (0, 18)],
        "near_square": [(0, 0), (32, 0), (32, 30), (0, 30)],
        "l_shape": [(0, 0), (50, 0), (50, 18), (22, 18), (22, 45), (0, 45)],
        "shaft": [(20 * math.cos(i * math.pi / 4), 20 * math.sin(i * math.pi / 4)) for i in range(8)],
    }
    option_families: dict[str, set[str]] = {}
    for name, points in shapes.items():
        result = build_support_system_options(_project(name, points))
        assert result["options"]
        assert result["decisionBoundary"]
        option_families[name] = {row["family"] for row in result["options"]}
    assert "direct_grid" in option_families["slender"]
    assert "ring_radial" in option_families["near_square"]
    assert "zoned_direct" in option_families["l_shape"]
    assert "ring_radial" in option_families["shaft"]


def test_design_qualification_separates_workspace_degradation_from_engineering_failure() -> None:
    project = _project("workspace", [(0, 0), (60, 0), (60, 20), (0, 20)])
    project.boreholes = [
        Borehole(code="BH1", x=0, y=0, collarElevation=0.0, depth=30.0),
        Borehole(code="BH2", x=60, y=20, collarElevation=0.0, depth=30.0),
    ]
    result = build_design_qualification(project, storage_info={
        "payloadBytes": 120 * 1024 * 1024,
        "workspaceBytes": 2 * 1024 * 1024,
        "fullLoadAllowed": False,
    })
    storage = next(row for row in result["gates"] if row["code"] == "Q-STORAGE")
    assert storage["status"] == "pass"
    assert storage["blocks"] == ["interactive_full_load"]
    assert result["workspaceProfileRequired"] is True
    assert result["systemOptions"]["options"]


def test_controlled_diagnostic_mode_exposes_alternative_systems_instead_of_claiming_calculation_ready() -> None:
    project = _project("concave", [(0, 0), (60, 0), (60, 20), (25, 20), (25, 55), (0, 55)])
    options = build_support_system_options(project)
    assert options["options"]
    assert any(row["generationMode"] != "automatic" for row in options["options"])
    result = build_design_qualification(project, storage_info={"fullLoadAllowed": True})
    assert result["calculationAllowed"] is False
    assert result["interactionMode"] in {"diagnostic", "degraded"}


def test_workspace_qualification_remains_available_when_full_api_load_is_blocked(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException
    from app.routers.design import get_design_qualification
    from app.storage.database import SQLiteProjectStore
    from app.storage.repository import ProjectRepository

    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "api")
    monkeypatch.setenv("PITGUARD_RESOURCE_POLICY_MODE", "fixed")
    monkeypatch.setenv("PITGUARD_API_FULL_PROJECT_LIMIT_MB", "16")
    store = SQLiteProjectStore(tmp_path / "qualification.sqlite3")
    repo = ProjectRepository(store)
    project = _project("large-workspace", [(0, 0), (60, 0), (60, 20), (0, 20)])
    project.advanced_engineering["legacyLargePayload"] = "x" * (17 * 1024 * 1024)
    repo.create(project)

    try:
        repo.require(project.id)
    except HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("full API load should be blocked")

    qualification = get_design_qualification(project.id, repo)
    assert qualification["workspaceProfileRequired"] is True
    assert qualification["gates"]


def test_unimplemented_system_family_is_not_silently_downgraded_to_direct_struts() -> None:
    from app.services.support_layout_repair import auto_repair_support_layout

    project = _project("manual-system", [(0, 0), (40, 0), (40, 30), (0, 30)])
    result = auto_repair_support_layout(project, topology_family="center_island")
    assert result.status == "manual_review"
    assert "不会将未实现的体系降级为普通直撑" in result.summary


def test_generic_wale_target_repair_produces_visible_feasible_candidates_for_uploaded_outline(monkeypatch) -> None:
    from app.services.support_layout_repair import auto_repair_support_layout

    monkeypatch.setenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "16")
    points = [
        (-115, -14), (-99, -14), (-99, -12), (-39, -12), (-39, -16.5),
        (-13, -16.5), (-13, -13), (98, -13), (98, -14.5), (115, -14.5),
        (115, 14.5), (98, 14.5), (98, 13), (-13, 13), (-13, 16.5),
        (-39, 16.5), (-39, 12), (-99, 12), (-99, 14), (-115, 14),
    ]
    project = _project("uploaded-outline", points)
    result = auto_repair_support_layout(project, preset="balanced", topology_family="hybrid_diagonal")
    feasible = [candidate for candidate in result.candidates if candidate.hard_constraints.get("passed")]
    assert feasible
    assert result.status in {"pass", "warning"}
    assert all(candidate.plan_geometry.get("outline") for candidate in feasible[:3])
    assert all(candidate.plan_geometry.get("supports") for candidate in feasible[:3])
    assert all(int(candidate.metrics.get("waleSupportBayFailCount", 0) or 0) == 0 for candidate in feasible[:3])
    assert all(candidate.variable_summary.get("capabilityOutcome") != "controlled_block" for candidate in feasible[:3])
