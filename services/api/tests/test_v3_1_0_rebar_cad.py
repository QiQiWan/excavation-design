from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.drawings.cad_export import build_drawing_set_manifest, export_construction_cad_package
from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import Project
from app.services.benchmark_cases import run_benchmark_case
from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme, build_rebar_design_scheme


@pytest.fixture(scope="module")
def benchmark_project() -> Project:
    result = run_benchmark_case("URBAN-TOPDOWN-32M-WALL-5SUPPORT", repo=None, persist=False)
    return Project.model_validate(result["project"])


def test_v3_1_zone_rebar_scheme_has_traceable_zones_and_constructability(benchmark_project: Project) -> None:
    scheme = build_rebar_design_scheme(benchmark_project, mode="balanced")
    assert scheme["summary"]["zoneBasedDesign"] is True
    assert scheme["summary"]["drawingLinked"] is True
    assert scheme["wallZones"]
    assert scheme["supportSchemes"]
    assert scheme["beamNodeSchemes"]
    zone = scheme["wallZones"][0]
    assert zone["topElevation"] > zone["bottomElevation"]
    assert zone["envelopeSource"] in {"calculated_moment", "shear_displacement_proxy"}
    assert {item["face"] for item in zone["faces"]} == {"inner", "outer"}
    for face in zone["faces"]:
        assert face["providedAsMm2PerM"] > 0
        assert face["recommendedMinimumWallThicknessM"] >= 0.6
        assert face["constructabilityNote"]
    assert "R-02" in zone["drawingRefs"]
    assert "D-06" in zone["drawingRefs"]
    support = next(item for item in scheme["supportSchemes"] if item.get("section"))
    assert support["endZones"]["stirrupSpacingMm"] <= support["middleZone"]["stirrupSpacingMm"]
    assert "D-07" in support["drawingRefs"]


def test_v3_1_apply_scheme_updates_governing_member_rebar(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    scheme = apply_rebar_design_scheme(project, mode="balanced")
    assert project.retaining_system is not None
    assert project.retaining_system.rebar_design_scheme["projectId"] == project.id
    assert scheme["summary"]["wallZoneCount"] > 0
    assert all(wall.reinforcement for wall in project.retaining_system.diaphragm_walls)
    rc_supports = [item for item in project.retaining_system.supports if item.section_type == "rc_rectangular"]
    assert rc_supports and all(len(item.reinforcement) >= 4 for item in rc_supports[:8])



def test_v3_1_rebar_viewer_uses_applied_wall_zones(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    apply_rebar_design_scheme(project, mode="balanced")
    visualization = build_rebar_ifc_visualization(project, max_bars=500)
    assert visualization["summary"]["zoneLinked"] is True
    wall_bars = [item for item in visualization["bars"] if item["hostType"] == "diaphragm_wall"]
    assert wall_bars
    assert all(item.get("zoneId") for item in wall_bars)
    assert any(item.get("drawingRefs") for item in wall_bars)
    assert len({item["hostCode"] for item in wall_bars}) == len(project.retaining_system.diaphragm_walls)

def test_v3_1_manifest_and_full_cad_package_include_global_zone_and_detail_drawings(benchmark_project: Project, tmp_path: Path) -> None:
    manifest = build_drawing_set_manifest(benchmark_project)
    sheet_numbers = {item["sheetNo"] for item in manifest["sheets"]}
    assert {"G-00", "S-00", "R-01", "R-02", "D-00", "D-06", "D-07"}.issubset(sheet_numbers)
    assert any(item["category"] == "wall_rebar_elevation" for item in manifest["sheets"])

    package = export_construction_cad_package(benchmark_project, tmp_path, scope="full", rebar_mode="balanced")
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        required = {
            "00_general/G-00_drawing_index_general_notes.dxf",
            "10_plans/S-00_retaining_support_general_arrangement.dxf",
            "30_rebar/R-01_wall_rebar_general_arrangement.dxf",
            "30_rebar/R-02_wall_rebar_zone_elevation.dxf",
            "40_details/D-00_typical_detail_compilation.dxf",
            "40_details/D-06_wall_panel_joint_detail.dxf",
            "40_details/D-07_support_anchorage_splice_detail.dxf",
            "90_schedules/rebar_zone_schedule.csv",
            "90_schedules/support_rebar_schedule.csv",
            "S-08_individual_rebar_geometry.dxf",
        }
        assert required.issubset(names)
        assert any(name.startswith("30_rebar/walls/R-02-W") for name in names)
        drawing_manifest = json.loads(zf.read("drawing_set_manifest.json"))
        assert drawing_manifest["includedSheetCount"] >= 20
        assert len(drawing_manifest["includedFiles"]) >= drawing_manifest["includedSheetCount"]
        package_manifest = json.loads(zf.read("drawing_package_manifest.json"))
        assert package_manifest["scope"] == "full"
        assert package_manifest["rebarMode"] == "balanced"


def test_v3_1_cad_scope_exports_are_separated(benchmark_project: Project, tmp_path: Path) -> None:
    rebar_package = export_construction_cad_package(benchmark_project, tmp_path, scope="rebar", rebar_mode="economic")
    with zipfile.ZipFile(rebar_package) as zf:
        names = set(zf.namelist())
        assert "30_rebar/R-01_wall_rebar_general_arrangement.dxf" in names
        assert "10_plans/S-00_retaining_support_general_arrangement.dxf" not in names
        assert "40_details/D-06_wall_panel_joint_detail.dxf" not in names
