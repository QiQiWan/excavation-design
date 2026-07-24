from __future__ import annotations

from pathlib import Path

from app.schemas.domain import Project
from app.services.design_core_v387 import build_design_core_workflow
from app.version import SOFTWARE_VERSION, RESULT_PIPELINE_VERSION


def test_design_core_is_quality_assurance_not_a_second_primary_flow() -> None:
    project = Project(name="single-flow")
    overview = build_design_core_workflow(project)
    assert overview["presentationRole"] == "quality_assurance"
    assert overview["primaryWorkflowStageCount"] == 6
    assert overview["primaryWorkflow"] == [
        "basis", "input", "scheme", "calculation", "reinforcement", "deliverables",
    ]
    assert overview["evidenceDomainCount"] == 9
    assert overview["evidenceGrouping"]["scheme"] == ["D3_SCHEME_SEARCH", "D4_RETAINING_DESIGN"]
    assert overview["evidenceGrouping"]["deliverables"] == ["D7_DRAWINGS", "D8_REPORT", "D9_REVIEW_ISSUE"]


def test_frontend_renders_one_primary_navigation_and_on_demand_assurance_drawer() -> None:
    root = Path(__file__).resolve().parents[3]
    workspace = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    panel = (root / "apps/web/src/components/DesignCoreWorkflowPanel.tsx").read_text(encoding="utf-8")
    styles = (root / "apps/web/src/app/styles.css").read_text(encoding="utf-8")

    assert workspace.count('<nav className="coreStageNav"') == 1
    assert '<DesignCoreWorkflowPanel' in workspace
    assert 'assuranceOpen ?' in workspace
    assert '质量与追溯' in workspace
    assert '设计主流程" resetKey' not in workspace
    assert '设计质量与追溯中心' in panel
    assert '辅助检查，不承担流程导航' in panel
    assert "{ id: 'overview', label: '质量总览' }" in panel
    assert 'V3.87 设计主流程' not in panel
    assert '.coreAssuranceDrawer' in styles
    assert '.designCoreStage.current' in styles


def test_version_identifies_single_flow_patch() -> None:
    assert SOFTWARE_VERSION == "3.87.11"
    assert "single-primary-design-flow" in RESULT_PIPELINE_VERSION
