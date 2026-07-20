from __future__ import annotations

from typing import Any

from app.schemas.domain import Project
from app.services.core_engineering_presentation import build_core_standard_guidance, build_scheme_comparison, build_stability_distribution, build_verification_distribution
from app.services.design_basis import build_design_basis
from app.services.engineering_templates import ensure_design_basis_defaults
from app.services.section_catalog import load_steel_support_catalog
from app.services.enterprise_library import list_enterprise_libraries, validate_enterprise_library
from app.services.adverse_scenario_execution import scenario_catalog
from app.services.runtime_diagnostics import append_event
from app.services.deepening_readiness import build_deepening_readiness, calculation_readiness
from app.services.construction_stages import build_construction_stage_workspace
from app.services.design_intake import build_design_intake


def _stage(key: str, title: str, ready: bool, active: bool, message: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": "done" if ready else "active" if active else "pending",
        "message": message,
    }


def build_core_workspace_status(project: Project, storage_info: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_design_basis_defaults(project)
    design_basis = build_design_basis(project)
    basis_ready = bool(design_basis.get("confirmed"))
    excavation_ready = bool(project.excavation and project.excavation.outline and len(project.excavation.outline.points) >= 3)
    geology_ready = bool(project.strata or project.boreholes)
    # Concept scheme generation only needs the excavation geometry.  Geological
    # evidence remains a calculation gate instead of falsely blocking design
    # exploration.
    input_ready = excavation_ready

    system = project.retaining_system
    wall_count = len(system.diaphragm_walls or []) if system else 0
    support_count = len(system.supports or []) if system else 0
    candidate_count = len((system.support_layout_repair.candidates or [])) if system and system.support_layout_repair else 0
    scheme_ready = wall_count > 0 and support_count > 0

    calculation_state = dict(project.advanced_engineering.get("calculationState") or {})
    requires_recalculation = bool(
        calculation_state.get("requiresRecalculation")
        or project.advanced_engineering.get("requiresRecalculation")
    )
    latest = project.calculation_results[-1] if project.calculation_results else None
    calculation_gate = calculation_readiness(project)
    construction_stages = build_construction_stage_workspace(project)
    calculation_ready = bool(calculation_gate.get("valid") and basis_ready)
    fail_count = int((latest.check_summary or {}).get("fail", 0) or 0) if latest else 0
    warning_count = int((latest.check_summary or {}).get("warning", 0) or 0) if latest else 0
    max_displacement = getattr(latest.governing_values, "max_displacement", None) if latest else None
    max_axial = getattr(latest.governing_values, "max_support_axial_force", None) if latest else None

    rebar = getattr(system, "rebar_design_scheme", None) if system else None
    rebar_ready = bool(rebar)
    stored_rebar_diagnostics = dict((rebar or {}).get("diagnostics") or {}) if isinstance(rebar, dict) else {}
    topology_status = str((stored_rebar_diagnostics.get("supportTopology") or {}).get("status") or "pass")
    deepening_readiness = build_deepening_readiness(
        project,
        checks=list((rebar or {}).get("checks") or []) if isinstance(rebar, dict) else [],
        section_change_count=int(stored_rebar_diagnostics.get("sectionChangeCount") or 0),
        topology_status=topology_status,
        scheme_applied=rebar_ready,
    )
    deliverable_ready = bool(deepening_readiness.get("canIssueConstructionDrawings"))
    design_intake = build_design_intake(
        project,
        calculation_current=calculation_ready,
        detailing_ready=bool(deepening_readiness.get("canEnterDetailing")),
        deliverable_ready=deliverable_ready,
    )

    stages = [
        _stage(
            "basis", "设计基准", basis_ready, True,
            "等级、规范和荷载组合已确认" if basis_ready else "确认工程等级、场地标准和荷载组合",
        ),
        _stage(
            "input", "轮廓与深度", input_ready, basis_ready,
            (
                f"闭合轮廓 {len(project.excavation.outline.points)} 点，开挖深度 {float(project.excavation.depth):.2f} m"
                if excavation_ready else "录入闭合轮廓、坑顶和最终坑底标高"
            ),
        ),
        _stage(
            "scheme", "快速方案", scheme_ready, input_ready,
            f"围护墙 {wall_count}，支撑 {support_count}，候选 {candidate_count}",
        ),
        _stage(
            "calculation", "计算验算", calculation_ready, scheme_ready,
            "计算合同、阶段结果与当前设计快照一致" if calculation_ready else str((calculation_gate.get("messages") or ["等待当前方案完整计算"])[0]),
        ),
        _stage(
            "reinforcement", "配筋深化", bool(deepening_readiness.get("canEnterDetailing")), calculation_ready,
            str(deepening_readiness.get("headline")) if rebar_ready else "等待计算内力包络",
        ),
        _stage(
            "deliverables", "成果交付", deliverable_ready, rebar_ready,
            "可生成审查成果" if deliverable_ready else "完成前述步骤后生成成果",
        ),
    ]
    next_stage = next((item for item in stages if item["status"] != "done"), stages[-1])
    blockers: list[str] = []
    if not basis_ready:
        blockers.append("设计基准尚未确认")
    if not excavation_ready:
        blockers.append("缺少闭合基坑轮廓")
    # Only surface the blocker that affects the *next* decision.  Geology is
    # deferred until a scheme exists, preventing a specialist-data gap from
    # overwhelming the early design brief.
    if scheme_ready and not calculation_ready and not geology_ready:
        blockers.append("方案已形成；正式计算前请导入钻孔或统一地层数据")
    if scheme_ready and geology_ready and not calculation_ready:
        blockers.extend(str(item) for item in list(calculation_gate.get("messages") or [])[:3])
    for row in (list(deepening_readiness.get("blockers") or [])[:5] if calculation_ready else []):
        message = f"{row.get('title')}：{row.get('requiredAction')}"
        if message not in blockers:
            blockers.append(message)

    info = storage_info or {}
    standard_guidance = build_core_standard_guidance()
    stability_distribution = build_stability_distribution(project)
    verification_distribution = build_verification_distribution(project)
    scheme_comparison = build_scheme_comparison(project)
    latest_report = dict(getattr(latest, "report_diagram_data", None) or {}) if latest else {}
    adverse_bundle = latest_report.get("adverseScenarioScreening") or latest_report.get("adverseScenarios") or {}
    adverse_scenarios = list((adverse_bundle.get("scenarios") if isinstance(adverse_bundle, dict) else adverse_bundle) or [])
    formal_adverse = dict(project.advanced_engineering.get("formalAdverseScenarioSuite") or {})
    p3_detailing = dict(project.advanced_engineering.get("p3DetailingClosure") or {})
    enterprise_validation = validate_enterprise_library(project)
    section_catalog = load_steel_support_catalog()
    append_event(
        "verification-coverage",
        "core_workspace_status",
        projectId=project.id,
        calculatedCoverage=(verification_distribution.get("summary") or {}).get("evidenceCoverage"),
        verificationRecordCount=len(verification_distribution.get("records") or []),
        adverseScenarioCount=len(adverse_scenarios),
        calculationCurrent=calculation_ready,
    )

    return {
        "projectId": project.id,
        "mode": "core",
        "stages": stages,
        "nextStage": next_stage["key"],
        "nextAction": {
            "basis": "确认最小设计任务书",
            "input": "录入轮廓与最终开挖深度",
            "scheme": "生成 A/B/C 快速方案",
            "calculation": "运行核心设计与计算",
            "reinforcement": "生成配筋方案",
            "deliverables": "生成审查成果包",
        }.get(next_stage["key"], "继续设计"),
        "blockers": blockers,
        "summary": {
            "wallCount": wall_count,
            "supportCount": support_count,
            "candidateCount": candidate_count,
            "failCount": fail_count,
            "warningCount": warning_count,
            "maximumDisplacementMm": max_displacement,
            "maximumSupportAxialForceKn": max_axial,
            "calculationCurrent": calculation_ready,
            "rebarReady": rebar_ready,
            "deliverableReady": deliverable_ready,
            "deepeningBlockerCount": int(deepening_readiness.get("blockerCount") or 0),
            "deepeningWarningCount": int(deepening_readiness.get("warningCount") or 0),
        },
        "standards": standard_guidance,
        "designBasis": design_basis,
        "designIntake": design_intake,
        "macroStages": design_intake.get("macroStages") or [],
        "stabilityDistribution": stability_distribution,
        "verificationDistribution": verification_distribution,
        "schemeComparison": scheme_comparison,
        "adverseScenarios": adverse_scenarios,
        "formalAdverseScenarioSuite": formal_adverse,
        "adverseScenarioCatalog": scenario_catalog(project),
        "enterpriseLibraries": list_enterprise_libraries(),
        "enterpriseLibraryValidation": enterprise_validation,
        "p3DetailingClosure": p3_detailing,
        "calculationReadiness": calculation_gate,
        "constructionStages": construction_stages,
        "deepeningReadiness": deepening_readiness,
        "sectionCatalog": {
            "version": section_catalog.get("catalogVersion"),
            "profileCount": len(section_catalog.get("profiles") or []),
            "boundary": section_catalog.get("boundary"),
        },
        "storage": {
            "payloadBytes": int(info.get("payloadBytes") or 0),
            "workspaceBytes": int(info.get("workspaceBytes") or 0),
            "externalBytes": int(info.get("externalBytes") or 0),
            "artifactCount": int(info.get("artifactCount") or 0),
            "storageStatus": info.get("storageStatus") or "normal",
        },
    }
