from __future__ import annotations

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import Point2D, Polyline2D, Project, ReinforcementGroup, SupportElement
from app.services.core_engineering_presentation import build_verification_distribution
from app.services.core_workspace import build_core_workspace_status
from app.services.design_basis import build_design_basis
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model


def _project() -> Project:
    excavation = make_excavation_model(
        "v345",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=30, y=0), Point2D(x=30, y=18), Point2D(x=0, y=18)], closed=True),
        0.0,
        -10.0,
    )
    project = Project(name="v345", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True
    project.design_settings.bearing_capacity_kpa = 180
    return project


def test_design_basis_precedes_core_workflow() -> None:
    project = _project()
    result = build_core_workspace_status(project)
    assert [row["key"] for row in result["stages"]] == [
        "basis", "input", "scheme", "calculation", "reinforcement", "deliverables"
    ]
    assert result["designBasis"]["confirmed"] is True
    assert len(result["designBasis"]["loadCombinations"]) == 3


def test_unconfirmed_basis_blocks_core_progress() -> None:
    project = _project()
    project.design_settings.design_basis_confirmed = False
    result = build_core_workspace_status(project)
    assert result["nextStage"] == "basis"
    assert "设计基准尚未确认" in result["blockers"]


def test_verification_matrix_lists_all_professional_categories() -> None:
    result = build_verification_distribution(_project())
    categories = {row["category"] for row in result["records"]}
    assert {"strength", "stiffness", "stability"}.issubset(categories)
    labels = {row["label"] for row in result["records"]}
    assert "围护墙抗弯承载力" in labels
    assert "围护墙最大水平位移" in labels
    assert "坑底抗隆起稳定" in labels
    assert all(row["status"] in {"manual_review", "not_applicable"} for row in result["records"] if row["source"] == "required_check_matrix")
    assert result["summary"]["overall"]["reserveThreshold"] == 1.1


def test_support_rebar_visualization_reserves_all_bar_families() -> None:
    project = _project()
    assert project.retaining_system is not None
    support = SupportElement(code="S1", levelIndex=1, elevation=-2.0, start=Point2D(x=1, y=9), end=Point2D(x=29, y=9))
    support.reinforcement = [
        ReinforcementGroup(name="纵筋", grade="HRB400", locationDescription="test", barType="longitudinal", diameter=25, count=12),
        ReinforcementGroup(name="侧面分布筋", grade="HRB400", locationDescription="test", barType="distribution", diameter=12, spacing=250),
        ReinforcementGroup(name="端部箍筋", grade="HRB400", locationDescription="test", barType="stirrup", diameter=12, spacing=100),
        ReinforcementGroup(name="跨中箍筋", grade="HRB400", locationDescription="test", barType="stirrup", diameter=12, spacing=180),
        ReinforcementGroup(name="拉结筋", grade="HRB400", locationDescription="test", barType="tie", diameter=12, spacing=400),
        ReinforcementGroup(name="附加筋", grade="HRB400", locationDescription="test", barType="additional", diameter=18, count=4),
    ]
    project.retaining_system.supports = [support]
    data = build_rebar_ifc_visualization(project, max_bars=200)
    types = {row["barType"] for row in data["bars"] if row["hostType"] == "internal_support"}
    assert {"longitudinal", "distribution", "stirrup", "tie", "additional"}.issubset(types)


def test_design_basis_contract_contains_material_and_load_values() -> None:
    basis = build_design_basis(_project())
    assert basis["summary"]["concreteGrade"] == "C35"
    assert basis["summary"]["rebarGrade"] == "HRB400"
    assert basis["summary"]["gammaG"] >= 1.0


def test_global_rebar_sampling_keeps_support_detail_families() -> None:
    project = _project()
    assert project.retaining_system is not None
    supports = []
    for support_index in range(12):
        support = SupportElement(
            code=f"S{support_index + 1}",
            levelIndex=1,
            elevation=-2.0,
            start=Point2D(x=1, y=1 + support_index),
            end=Point2D(x=29, y=1 + support_index),
        )
        support.reinforcement = [
            ReinforcementGroup(name="纵筋", grade="HRB400", locationDescription="test", barType="longitudinal", diameter=25, count=12),
            ReinforcementGroup(name="侧面分布筋", grade="HRB400", locationDescription="test", barType="distribution", diameter=12, spacing=250),
            ReinforcementGroup(name="箍筋", grade="HRB400", locationDescription="test", barType="stirrup", diameter=12, spacing=150),
            ReinforcementGroup(name="拉结筋", grade="HRB400", locationDescription="test", barType="tie", diameter=12, spacing=400),
            ReinforcementGroup(name="节点附加筋", grade="HRB400", locationDescription="test", barType="additional", diameter=18, count=4),
        ]
        supports.append(support)
    project.retaining_system.supports = supports
    data = build_rebar_ifc_visualization(project, max_bars=40)
    assert set(data["summary"]["supportBarTypesPresent"]) == {
        "longitudinal", "distribution", "stirrup", "tie", "additional"
    }
