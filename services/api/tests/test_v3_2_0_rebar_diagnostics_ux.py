from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.calculation.engine import synchronize_calculation_case_supports
from app.drawings.cad_export import export_construction_cad_package
from app.rules.gb50010.rc_section_rules import design_rectangular_flexure
from app.schemas.domain import Project
from app.services.benchmark_cases import run_benchmark_case
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme


@pytest.fixture(scope="module")
def benchmark_project() -> Project:
    result = run_benchmark_case("URBAN-TOPDOWN-32M-WALL-5SUPPORT", repo=None, persist=False)
    return Project.model_validate(result["project"])


def test_v3_2_calculation_case_stale_support_ids_are_synchronized(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    case = project.calculation_cases[-1].model_copy(deep=True)
    case.support_topology_hash = "obsolete-topology"
    for stage in case.stages:
        if stage.active_support_ids:
            stage.active_support_ids = [f"obsolete-{index}" for index, _ in enumerate(stage.active_support_ids)]
            stage.support_topology_hash = "obsolete-topology"
    synchronized, audit = synchronize_calculation_case_supports(project, case)
    current_ids = {item.id for item in project.retaining_system.supports}
    assert audit["synchronized"] is True
    assert audit["before"]["staleSupportCount"] > 0
    assert synchronized.support_topology_hash != "obsolete-topology"
    assert all(set(stage.active_support_ids).issubset(current_ids) for stage in synchronized.stages)
    assert any(stage.active_support_ids for stage in synchronized.stages)


def test_wall_to_wall_grid_avoids_unsupported_midspan_secondary_reactions(benchmark_project: Project) -> None:
    ret = benchmark_project.retaining_system
    assert ret is not None
    secondary = [item for item in ret.supports if item.support_role == "secondary_strut"]
    assert secondary == []
    assert all(
        item.support_role == "ring_strut" or (item.start_face_code and item.end_face_code)
        for item in ret.supports
    )
    max_corner_width = max(
        value
        for item in ret.supports
        if item.support_role == "corner_diagonal"
        for value in (item.start_tributary_width or 0.0, item.end_tributary_width or 0.0)
    )
    assert max_corner_width <= 12.0
    assert max(abs(item.design_axial_force or 0.0) for item in ret.supports) < 30_000.0

    scheme = build_rebar_design_scheme(benchmark_project, mode="balanced")
    assert scheme["summary"]["failCount"] == 0
    assert scheme["diagnostics"]["canIssueConstructionDrawings"] is False
    assert scheme["diagnostics"]["supportTopology"]["status"] == "pass"
    assert scheme["diagnostics"]["supportTopology"]["secondaryGridSupportCount"] == len(secondary)


def test_v3_2_node_bearing_rounding_does_not_create_false_failure(benchmark_project: Project) -> None:
    nodes = benchmark_project.retaining_system.support_nodes
    assert nodes
    checked = [node for node in nodes if node.bearing_plate and node.bearing_plate.bearing_capacity]
    assert checked
    assert all(node.bearing_plate.check_status != "fail" for node in checked)
    for node in checked:
        plate = node.bearing_plate
        assert plate.plate_width * plate.plate_height == pytest.approx(plate.bearing_area, rel=1e-6)
        if plate.check_status == "pass":
            assert (plate.bearing_stress or 0.0) <= (plate.bearing_capacity or 0.0)


def test_v3_2_high_moment_flexure_no_longer_plateaus() -> None:
    moderate = design_rectangular_flexure(moment_design_knm_per_m=1_000.0, thickness_m=1.0)
    high = design_rectangular_flexure(moment_design_knm_per_m=8_000.0, thickness_m=1.0)
    assert high.required_as > moderate.required_as
    assert high.design_regime == "double_reinforced_required"
    assert high.compression_rebar_required > 0.0
    assert high.section_capacity_exceeded is True


def test_v3_2_rebar_mode_uses_existing_uls_force_without_double_factoring(benchmark_project: Project) -> None:
    scheme = build_rebar_design_scheme(benchmark_project, mode="balanced")
    support_by_id = {item.id: item for item in benchmark_project.retaining_system.supports}
    rows = [row for row in scheme["supportSchemes"] if row.get("hostId") in support_by_id]
    assert rows
    row = rows[0]
    source = support_by_id[row["hostId"]]
    assert row["axialForceDesignKn"] == pytest.approx(abs(source.design_axial_force or 0.0), abs=0.01)
    assert row["targetUtilization"] == pytest.approx(0.88)


def test_v3_2_cad_package_contains_grid_detail_diagnostics_and_issue_mode(benchmark_project: Project, tmp_path: Path) -> None:
    review = export_construction_cad_package(benchmark_project, tmp_path, scope="full", rebar_mode="balanced", issue_mode="review")
    with zipfile.ZipFile(review) as zf:
        names = set(zf.namelist())
        assert "40_details/D-08_bidirectional_grid_node_detail.dxf" not in names
        assert "40_details/D-02_corner_brace_node_detail.dxf" in names
        assert "90_schedules/design_diagnostic_summary.json" in names
        assert "90_schedules/design_diagnostic_summary.csv" in names
        assert "REVIEW_ONLY_审查版.txt" in names
        diagnostic = json.loads(zf.read("90_schedules/design_diagnostic_summary.json"))
        assert diagnostic["canIssueConstructionDrawings"] is False
        package = json.loads(zf.read("drawing_package_manifest.json"))
        assert package["issueMode"] == "review"
        assert package["reviewWatermark"] is True

    with pytest.raises(ValueError, match="施工图正式发行门禁未通过"):
        export_construction_cad_package(
            benchmark_project,
            tmp_path,
            scope="details",
            rebar_mode="balanced",
            issue_mode="construction",
        )
