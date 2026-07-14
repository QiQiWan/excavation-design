from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.calculation.engine import run_calculation
from app.schemas.domain import Point2D, Polyline2D, Project, SupportElement
from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig, repair_wale_support_bays
from app.services.wall_embedment_design import auto_design_wall_embedment
from app.version import SOFTWARE_VERSION


def _sample_root() -> Path:
    return Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project"


def _actual_project(*, support_levels: bool = True) -> Project:
    root = _sample_root()
    imported = parse_borehole_rows(
        read_csv_bytes((root / "actual_project_boreholes_24x6layers.csv").read_bytes()),
        source_file="actual_project_boreholes_24x6layers.csv",
    )
    assert imported.success, imported.errors
    payload = json.loads((root / "actual_project_excavation_payload.json").read_text(encoding="utf-8"))
    project = Project(name="Fengshou embedment regression")
    project.boreholes = imported.boreholes
    project.strata = imported.strata
    project.design_settings.groundwater_level = -20.0
    project.design_settings.surcharge = 20.0
    if support_levels:
        project.design_settings.support_level_depths_m = [0.0, 4.0, 7.2, 10.3, 13.3]
    project.excavation = make_excavation_model(
        payload["name"],
        Polyline2D(points=[Point2D(**item) for item in payload["outline"]["points"]], closed=True),
        0.0,
        -16.6,
        0.5,
    )
    base = auto_diaphragm_wall(project.excavation)
    project.retaining_system = auto_supports(
        project.excavation,
        base,
        SupportLayoutConfig(support_level_depths_m=(0.0, 4.0, 7.2, 10.3, 13.3) if support_levels else ()),
    )
    return project


def test_fengshou_shallow_wall_toe_is_deepened_to_common_passing_elevation() -> None:
    project = _actual_project()
    for wall in project.retaining_system.diaphragm_walls:
        wall.bottom_elevation = -27.0
        wall.bottom_elevation_source = "unknown"
        wall.bottom_elevation_locked = False
        wall.source_bottom_elevation = None

    audit = auto_design_wall_embedment(project)
    assert audit["status"] == "pass"
    assert audit["changed"] is True
    assert audit["beforeMinimumFactor"] < audit["screeningLimit"]
    assert audit["afterMinimumFactor"] >= audit["screeningLimit"]
    assert audit["afterBottomElevationM"] < audit["beforeBottomElevationM"]
    assert len({wall.bottom_elevation for wall in project.retaining_system.diaphragm_walls}) == 1
    assert all(wall.bottom_elevation_source == "auto_stability" for wall in project.retaining_system.diaphragm_walls)


def test_imported_locked_wall_toe_survives_one_click_wall_regeneration() -> None:
    project = _actual_project(support_levels=False)
    for wall in project.retaining_system.diaphragm_walls:
        wall.bottom_elevation = -32.8
        wall.bottom_elevation_source = "imported"
        wall.bottom_elevation_locked = True
        wall.source_bottom_elevation = -32.8

    regenerated = auto_diaphragm_wall(project.excavation, project.retaining_system)
    assert {wall.bottom_elevation for wall in regenerated.diaphragm_walls} == {-32.8}
    assert all(wall.bottom_elevation_locked for wall in regenerated.diaphragm_walls)
    assert all(wall.bottom_elevation_source == "imported" for wall in regenerated.diaphragm_walls)


def test_legacy_wale_repair_ty_tie_is_removed_by_default() -> None:
    project = _actual_project(support_levels=False)
    main = next(item for item in project.retaining_system.supports if item.support_role == "main_strut")
    legacy = SupportElement.model_validate(main.model_dump())
    legacy.id = "legacy-wall-to-support-tie"
    legacy.code = "SB-LEGACY-1"
    legacy.support_role = "secondary_strut"
    legacy.start_face_code = project.excavation.segments[0].name
    legacy.end_face_code = None
    legacy.end_wall_connection = None
    legacy.layout_note = "围檩超限跨中增补：墙面法向短撑止于主对撑 T/Y 节点。"
    project.retaining_system.supports.append(legacy)

    outcome = repair_wale_support_bays(project)
    assert outcome["changed"] is True
    assert outcome["removedLegacyTYSupportCount"] >= 1
    assert not any(item.id == legacy.id for item in project.retaining_system.supports)
    for brace in project.retaining_system.supports:
        if "墙—墙 V 形修复" not in (brace.layout_note or ""):
            continue
        assert brace.support_role == "secondary_strut"
        assert brace.start_face_code
        assert brace.end_face_code
        assert brace.start_face_code != brace.end_face_code


def test_fengshou_full_calculation_closes_twenty_embedment_failures() -> None:
    project = _actual_project()
    for wall in project.retaining_system.diaphragm_walls:
        wall.bottom_elevation = -27.0
        wall.bottom_elevation_source = "unknown"
        wall.bottom_elevation_locked = False
    result = run_calculation(project, auto_repair=True, include_candidate_comparison=False)
    checks = [item if isinstance(item, dict) else item.model_dump(by_alias=True) for item in result.checks]
    counts = Counter(item["status"] for item in checks)
    # This regression verifies wall-toe recovery. V3.26 deliberately keeps
    # unrelated wale/detailing failures visible rather than manufacturing T/Y
    # pseudo-supports to force the entire project to green.
    assert counts.get("pass", 0) > 0
    assert not [
        item for item in checks
        if item["status"] == "fail" and item.get("ruleId") == "JGJ120-2012-4.2-EMBEDMENT-STABILITY-SCREEN"
    ]
    embedment = result.design_iteration_summary["wallEmbedmentPreflight"]
    assert embedment["beforeMinimumFactor"] < embedment["screeningLimit"]
    assert embedment["afterMinimumFactor"] >= embedment["screeningLimit"]
    assert result.governing_values.stability_check_status == "pass"
    assert not any("墙面法向短撑" in (item.layout_note or "") for item in project.retaining_system.supports)


def test_v318_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 19, 0)
