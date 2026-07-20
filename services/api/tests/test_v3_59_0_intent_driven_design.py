from __future__ import annotations

from pathlib import Path

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.core_workspace import build_core_workspace_status
from app.services.design_intake import apply_guided_design_intake, build_design_intake
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model


def _project(*, with_scheme: bool = False) -> Project:
    excavation = make_excavation_model(
        "V3.59 最小设计任务书测试",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=36, y=0),
                Point2D(x=36, y=22), Point2D(x=0, y=22),
            ],
            closed=True,
        ),
        0.0,
        -11.5,
    )
    project = Project(name="V3.59 最小设计任务书测试", excavation=excavation)
    if with_scheme:
        retaining = auto_diaphragm_wall(excavation)
        project.retaining_system = auto_supports(excavation, retaining)
    return project


def test_guided_intake_translates_four_choices_without_inventing_specialist_evidence() -> None:
    project = _project()
    apply_guided_design_intake(
        project,
        goal="standard_design",
        environment_level="高",
        objective="safety_first",
        design_stage="permanent_combined",
    )

    settings = project.design_settings
    assert settings.design_intent_confirmed is True
    assert settings.design_basis_confirmed is True
    assert settings.design_intent_goal == "standard_design"
    assert settings.design_objective == "safety_first"
    assert settings.surrounding_environment_level == "高"
    assert settings.excavation_safety_level == "一级"
    assert settings.load_combination_policy == "conservative"
    assert settings.intelligent_closure_strategy == "stiffness_first"
    assert settings.bearing_capacity_kpa is None
    assert project.boreholes == [] and project.strata == []
    assert project.advanced_engineering["designIntake"]["source"] == "guided_recommendation"


def test_concept_scheme_only_requires_confirmed_brief_and_excavation_geometry() -> None:
    project = _project()
    apply_guided_design_intake(
        project,
        goal="quick_scheme",
        environment_level="一般",
        objective="balanced",
        design_stage="temporary",
    )

    intake = build_design_intake(project)
    assert intake["readiness"]["canGenerateConceptScheme"] is True
    assert intake["readiness"]["canRunCalculation"] is False
    assert intake["primaryAction"]["key"] == "generate_scheme"
    assert [row["key"] for row in intake["inputTiers"]["requiredNow"]] == []
    assert "geology_source" in [row["key"] for row in intake["inputTiers"]["beforeCalculation"]]

    status = build_core_workspace_status(project)
    input_stage = next(row for row in status["stages"] if row["key"] == "input")
    assert input_stage["status"] == "done"
    assert status["nextStage"] == "scheme"
    assert not any("钻孔" in row for row in status["blockers"])


def test_geology_becomes_next_action_only_after_scheme_exists() -> None:
    project = _project(with_scheme=True)
    apply_guided_design_intake(
        project,
        goal="standard_design",
        environment_level="较高",
        objective="balanced",
        design_stage="temporary",
    )

    intake = build_design_intake(project)
    assert intake["readiness"]["schemeReady"] is True
    assert intake["primaryAction"]["key"] == "import_geology"
    assert intake["macroStages"][0]["status"] == "done"
    assert intake["macroStages"][1]["status"] == "active"

    status = build_core_workspace_status(project)
    assert any("正式计算前" in row and "钻孔" in row for row in status["blockers"])


def test_frontend_defaults_to_guided_three_phase_flow_and_progressive_input_copy() -> None:
    root = Path(__file__).resolve().parents[3]
    workspace = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    basis = (root / "apps/web/src/components/DesignBasisPanel.tsx").read_text(encoding="utf-8")
    assert "简明流程" in workspace and "专业流程" in workspace
    assert "快速方案" in workspace and "计算与优化" in workspace and "配筋与交付" in workspace
    assert "正式计算前再补" in workspace
    assert "最小设计任务书" in basis
    assert "4 项确认" in basis
    assert "采用推荐值并继续" in basis
    assert "需要时展开专业设置" in basis
