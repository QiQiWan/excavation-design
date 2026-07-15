from __future__ import annotations

from pathlib import Path

import ezdxf

from app.drawings.cad_export import _write_wall_joint_detail
from app.rules.enterprise.preliminary_design_rules import support_elevations
from app.schemas.domain import Project
from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
from app.services.standards_matrix import build_standards_process_matrix


def _sample_data_path() -> Path:
    return Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project" / "actual_project_boreholes_24x6layers.csv"


def test_actual_project_csv_imports_24_boreholes_and_anisotropic_permeability() -> None:
    path = _sample_data_path()
    result = parse_borehole_rows(read_csv_bytes(path.read_bytes()), source_file=path.name)
    assert result.success, result.errors
    assert result.borehole_count == 24
    assert result.layer_count == 144
    assert result.stratum_count == 12
    assert len(result.warnings) == 2  # repeated gravel warnings are compacted by risk
    gravel = next(item for item in result.strata if item.code == "144")
    assert gravel.parameters.elastic_modulus == 5625.0
    assert gravel.parameters.saturated_unit_weight == 25.0
    assert gravel.parameters.permeability_x == 0.1
    assert gravel.parameters.permeability_y == 0.0008
    assert gravel.parameters.permeability_z == 0.0008


def test_preliminary_support_levels_do_not_emit_false_bottom_clearance_warning() -> None:
    elevations, warnings = support_elevations(0.0, -16.6)
    assert elevations == [-1.5, -5.7, -9.9, -14.1]
    assert elevations[-1] - (-16.6) >= 2.0
    assert not any("坑底施工空间不足" in item for item in warnings)


def test_standards_matrix_links_each_calculation_to_its_own_standards() -> None:
    matrix = build_standards_process_matrix()
    calculation = next(item for item in matrix["steps"] if item["workflowStep"] == "calculation")
    assert calculation["calculationLinks"]
    for link in calculation["calculationLinks"]:
        assert link["calculation"]
        assert link["method"]
        assert link["clauseFocus"]
        assert link["output"]
        assert "standardRefs" in link
    wall_link = next(item for item in calculation["calculationLinks"] if "墙体" in item["calculation"])
    assert {item["code"] for item in wall_link["standardRefs"]} >= {"JGJ 120-2012", "GB 55003-2021"}


def test_d06_wall_joint_detail_contains_engineering_layers_and_entities(tmp_path: Path) -> None:
    path = tmp_path / "D-06_wall_joint.dxf"
    _write_wall_joint_detail(Project(name="actual-case-drawing"), path)
    doc = ezdxf.readfile(path)
    layer_names = {layer.dxf.name for layer in doc.layers}
    assert {"PIT_WATERSTOP", "PIT_REBAR_MAIN", "PIT_REBAR_DIST", "PIT_TABLE", "PIT_DIM"}.issubset(layer_names)
    modelspace = doc.modelspace()
    assert len(modelspace) >= 70
    text = "\n".join(str(getattr(entity.dxf, "text", "")) for entity in modelspace if entity.dxftype() in {"TEXT", "MTEXT"})
    assert "墙幅接头平面剖面" in text
    assert "止水" in text
    assert "钢筋笼" in text


def test_successful_layout_actions_are_design_evidence_not_warnings() -> None:
    from app.services.design_service import _partition_layout_messages

    evidence, warnings = _partition_layout_messages([
        "已将 44 条平面支撑中心线向坑内偏移，避免与围护墙重合。",
        "已跳过 1 条会与既有支撑无节点交叉的候选支撑；请复核局部布置。",
        "凹角相邻墙面 S2 未能自动形成有效法向对撑，需人工布置。",
    ])
    assert len(evidence) == 2
    assert warnings == ["凹角相邻墙面 S2 未能自动形成有效法向对撑，需人工布置。"]


def test_explicit_project_support_depths_override_enterprise_auto_levels() -> None:
    from app.schemas.domain import Polyline2D, Point2D
    from app.services.excavation_service import make_excavation_model
    from app.services.design_service import auto_supports
    from app.services.support_layout import SupportLayoutConfig

    excavation = make_excavation_model(
        name="actual",
        outline=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=30, y=0), Point2D(x=30, y=20), Point2D(x=0, y=20)], closed=True),
        top_elevation=0.0,
        bottom_elevation=-16.6,
    )
    system = auto_supports(
        excavation,
        layout_config=SupportLayoutConfig(support_level_depths_m=(0.0, 4.0, 7.2, 10.3, 13.3)),
    )
    assert sorted({support.level_index for support in system.supports}) == [1, 2, 3, 4, 5]
    assert sorted({support.elevation for support in system.supports}, reverse=True) == [0.0, -4.0, -7.2, -10.3, -13.3]
    assert any("项目明确指定的支撑深度" in item for item in system.layout_summary.get("designNotes", []))
