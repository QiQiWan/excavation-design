from __future__ import annotations

import json
import re
from pathlib import Path

from app.main import app
from app.schemas.domain import Project
from app.services.design_core_v387 import build_design_core_workflow
from app.services.standards_matrix import build_online_documentation
from app.version import version_manifest


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    panel = (root / "apps/web/src/components/DesignCoreWorkflowPanel.tsx").read_text(encoding="utf-8")
    styles = (root / "apps/web/src/app/styles.css").read_text(encoding="utf-8")
    overview = build_design_core_workflow(Project(name="v3873-single-flow-evaluation"))
    docs = build_online_documentation()

    checks = {
        "singlePrimaryNavigation": workspace.count('<nav className="coreStageNav"') == 1,
        "persistentSecondFlowRemoved": '<PanelErrorBoundary title="设计主流程"' not in workspace,
        "assuranceDrawerOnDemand": 'assuranceOpen ?' in workspace and 'coreAssuranceDrawer' in workspace,
        "assuranceButtonPresent": '质量与追溯' in workspace,
        "assuranceTitleUpdated": '设计质量与追溯中心' in panel and 'V3.87 设计主流程' not in panel,
        "sixQualityGroups": len(re.findall(r"\{ key: '(?:basis|input|scheme|calculation|reinforcement|deliverables)'", panel)) == 6,
        "backendRoleIsAssurance": overview.get("presentationRole") == "quality_assurance",
        "backendPrimaryStageCount": overview.get("primaryWorkflowStageCount") == 6,
        "backendEvidenceDomainCount": overview.get("evidenceDomainCount") == 9,
        "drawerStylesPresent": '.coreAssuranceDrawer' in styles and '.designCoreStage.current' in styles,
        "onlineDocsSingleFlow": any(row.get("title") == "六阶段单一设计主流程" for row in docs.get("chapters", [])),
    }
    payload = {
        "schema": "pitguard-v3873-single-primary-flow-evaluation-v1",
        "version": version_manifest(),
        "routeCount": len(app.routes),
        "primaryWorkflow": overview.get("primaryWorkflow"),
        "evidenceGrouping": overview.get("evidenceGrouping"),
        "checks": checks,
        "passed": sum(1 for value in checks.values() if value),
        "total": len(checks),
        "status": "pass" if all(checks.values()) else "fail",
        "limitations": [
            "当前环境未安装前端 node_modules，未执行 Vitest、tsc -b 和 Vite 生产构建。",
            "本补丁仅调整流程信息架构和加载策略，不修改结构计算、配筋和导出算法。",
        ],
    }
    output = root / "docs/releases/V3_87_3_SINGLE_FLOW_EVALUATION.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
