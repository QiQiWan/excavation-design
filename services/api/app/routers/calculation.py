from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException

from app.schemas.domain import CalculationCase, CalculationResult
from app.storage.repository import ProjectRepository, get_repository
from app.services.calculation_trace import build_calculation_trace
from app.services.wall_length_optimizer import mark_wall_length_recalculated
from app.services.calculation_state import mark_calculation_state_current
from app.services.calculation_assurance import audit_calculation_inputs, build_calculation_contract, assess_calculation_result, verify_current_calculation_contract
from app.quality.formal_gate import build_formal_report_gate
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility

router = APIRouter(prefix="/api/projects/{project_id}/calculation", tags=["calculation"])


def _require_embedded_heavy_execution() -> None:
    if str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")).strip().lower() == "external":
        raise HTTPException(
            status_code=409,
            detail=(
                "生产环境中的完整计算由独立 pitguard-worker 进程执行。"
                "请通过 /api/projects/{project_id}/tasks 提交 calculation_full 或 candidate_comparison 任务。"
            ),
        )


@router.post("/build-cases", response_model=list[CalculationCase])
def build_cases(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> list[CalculationCase]:
    from app.calculation.engine import build_default_construction_cases
    from app.services.calculation_state import invalidate_calculation_state
    from app.services.construction_stages import validate_calculation_case
    project = repo.require_for_calculation(project_id)
    existing = project.calculation_cases[-1] if project.calculation_cases else None
    if existing and (existing.locked or existing.source == "user_defined"):
        validation = validate_calculation_case(project, existing)
        if not validation["valid"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CONSTRUCTION_STAGE_VALIDATION_FAILED",
                    "message": "项目设计控制工况已锁定，且与当前方案不一致；系统不会静默覆盖。",
                    "validation": validation,
                },
            )
        return list(project.calculation_cases)
    try:
        cases = build_default_construction_cases(project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project.calculation_cases = cases
    invalidate_calculation_state(
        project,
        reason="设计控制工况已按当前围护方案重新生成",
        rebuild_cases=False,
        preserve_cases=True,
    )
    repo.save(project, action="calculation.build_cases", summary="Rebuilt default construction stages and invalidated old results")
    return cases


@router.get("/construction-stages")
def construction_stages(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.construction_stages import build_construction_stage_workspace

    return build_construction_stage_workspace(repo.require_workspace(project_id))


@router.put("/construction-stages")
def update_construction_stages(
    project_id: str,
    case: CalculationCase,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.calculation_state import invalidate_calculation_state
    from app.services.construction_stages import (
        build_construction_stage_workspace,
        normalize_user_calculation_case,
        validate_calculation_case,
    )

    project = repo.require_for_calculation(project_id)
    existing = project.calculation_cases[-1] if project.calculation_cases else None
    normalized = normalize_user_calculation_case(project, case)
    normalized.revision = (
        max(int(case.revision or 1), int(existing.revision or 1)) + 1
        if existing and existing.id == case.id
        else max(1, int(case.revision or 1))
    )
    validation = validate_calculation_case(project, normalized)
    if not validation["valid"]:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONSTRUCTION_STAGE_VALIDATION_FAILED",
                "message": "设计控制工况存在硬错误，未保存。",
                "validation": validation,
            },
        )
    project.calculation_cases = [normalized]
    from app.services.workflow_v381 import migrate_legacy_stages
    migrate_legacy_stages(project, force=True)
    invalidate_calculation_state(
        project,
        reason="用户设计控制工况已更新",
        rebuild_cases=False,
        preserve_cases=True,
    )
    project.messages.append("设计控制工况已保存；原计算结果已失效，请运行当前方案完整计算。")
    repo.save(project, action="calculation.update_construction_stages", summary="Saved user-defined construction stages")
    return build_construction_stage_workspace(project)


@router.post("/construction-stages/reset")
def reset_construction_stages(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.calculation.engine import build_default_construction_cases
    from app.services.calculation_state import invalidate_calculation_state
    from app.services.construction_stages import build_construction_stage_workspace

    project = repo.require(project_id)
    try:
        project.calculation_cases = build_default_construction_cases(project)
        from app.services.workflow_v381 import migrate_legacy_stages
        migrate_legacy_stages(project, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_calculation_state(
        project,
        reason="设计控制工况已恢复为当前方案推荐值",
        rebuild_cases=False,
        preserve_cases=True,
    )
    project.messages.append("设计控制工况已恢复为推荐值；原计算结果已失效，请重新计算。")
    repo.save(project, action="calculation.reset_construction_stages", summary="Reset construction stages to generated defaults")
    return build_construction_stage_workspace(project)


@router.post("/run", response_model=CalculationResult)
def run(project_id: str, case_id: str | None = None, repo: ProjectRepository = Depends(get_repository)) -> CalculationResult:
    _require_embedded_heavy_execution()
    from app.calculation.engine import run_calculation
    from app.services.intelligent_design_closure import run_intelligent_design_closure
    from app.services.construction_stages import select_calculation_case_for_run, validate_calculation_case
    project = repo.require_for_calculation(project_id)
    case = None
    stage_selection: dict = {}
    if case_id:
        case = next((c for c in project.calculation_cases if c.id == case_id), None)
        if case is None:
            raise HTTPException(status_code=404, detail=f"Calculation case not found: {case_id}")
        if case.locked or case.source == "user_defined":
            validation = validate_calculation_case(project, case)
            if not validation["valid"]:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "CONSTRUCTION_STAGE_VALIDATION_FAILED",
                        "message": "锁定设计控制工况与当前方案不一致，未启动计算。",
                        "validation": validation,
                    },
                )
            stage_selection = {
                "source": "user_defined", "preserved": True, "caseId": case.id,
                "stageCount": len(case.stages), "validation": validation,
            }
    else:
        try:
            case, stage_selection = select_calculation_case_for_run(project)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not project.calculation_cases or project.calculation_cases[-1].id != case.id:
            project.calculation_cases = [case]
    try:
        if project.design_settings.auto_intelligent_design_closure_enabled:
            result, _closure = run_intelligent_design_closure(
                project,
                case,
                auto_repair=not bool(stage_selection.get("preserved")),
            )
        else:
            result = run_calculation(project, case, auto_repair=not bool(stage_selection.get("preserved")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result.design_iteration_summary = dict(result.design_iteration_summary or {})
    result.design_iteration_summary["constructionStageSelection"] = stage_selection
    project.calculation_results.append(result)
    mark_calculation_state_current(project, result.id)
    mark_wall_length_recalculated(project, result.id)
    repo.save(project)
    return result


@router.post("/diagnose-and-repair")
def diagnose_and_repair(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    _require_embedded_heavy_execution()
    from app.services.intelligent_design_closure import run_intelligent_design_closure
    """Run topology preflight, synchronize construction stages and recalculate.

    The response is intentionally compact so the UI can explain the root cause
    before loading the full calculation result from the project history.
    """
    project = repo.require_for_calculation(project_id)
    try:
        result, closure = run_intelligent_design_closure(project, None, auto_repair=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project.calculation_results.append(result)
    mark_calculation_state_current(project, result.id)
    mark_wall_length_recalculated(project, result.id)
    repo.save(project)
    diagnostics = dict((result.design_iteration_summary or {}).get("calculationDiagnostics") or {})
    return {
        "projectId": project.id,
        "resultId": result.id,
        "checkSummary": result.check_summary,
        "governingValues": result.governing_values.model_dump(mode="json", by_alias=True),
        "diagnostics": diagnostics,
        "intelligentDesignClosure": closure,
    }


@router.post("/intelligent-closure/action")
def apply_intelligent_closure_action(
    project_id: str,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    """Apply an explicit engineer-selected strengthening action and recalculate."""
    _require_embedded_heavy_execution()
    from app.services.construction_stages import select_calculation_case_for_run
    from app.services.intelligent_design_closure import apply_intervention_action, run_intelligent_design_closure

    project = repo.require_for_calculation(project_id)
    action_id = str(payload.get("actionId") or "").strip()
    if not action_id:
        raise HTTPException(status_code=422, detail="缺少 actionId。")
    strategy = str(payload.get("strategy") or "") or None
    if action_id.startswith("run-strategy:"):
        strategy = action_id.split(":", 1)[1]
    try:
        applied = apply_intervention_action(project, action_id, payload.get("value"))
        case, stage_selection = select_calculation_case_for_run(project)
        result, closure = run_intelligent_design_closure(
            project,
            case,
            auto_repair=not bool(stage_selection.get("preserved")),
            strategy=strategy,
            max_iterations=payload.get("maxIterations"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result.design_iteration_summary = dict(result.design_iteration_summary or {})
    result.design_iteration_summary["constructionStageSelection"] = stage_selection
    project.calculation_results.append(result)
    mark_calculation_state_current(project, result.id)
    mark_wall_length_recalculated(project, result.id)
    repo.save(project, action="calculation.intelligent_closure_action", summary=f"Apply intelligent closure action {action_id}")
    return {
        "projectId": project.id,
        "resultId": result.id,
        "appliedAction": applied,
        "checkSummary": result.check_summary,
        "intelligentDesignClosure": closure,
        "refreshProject": True,
    }


@router.post("/run-candidate-comparison")
def run_candidate_comparison(project_id: str, top_n: int = 3, repo: ProjectRepository = Depends(get_repository)) -> list[dict]:
    _require_embedded_heavy_execution()
    from app.calculation.engine import run_candidate_comparison_for_project
    project = repo.require_for_calculation(project_id)
    try:
        comparison = run_candidate_comparison_for_project(project, top_n=top_n)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if project.calculation_results:
        latest = project.calculation_results[-1]
        if project.retaining_system and project.retaining_system.support_layout_repair:
            latest.support_layout_repair = project.retaining_system.support_layout_repair
        latest.report_diagram_data = dict(latest.report_diagram_data or {})
        latest.report_diagram_data["candidateFullCalculationComparison"] = comparison
        latest.formal_report_gate = build_formal_report_gate(
            project,
            latest.support_layout_quality,
            evaluate_ifc_model_compatibility(project),
            latest_result=latest,
        )
    repo.save(project)
    return comparison


@router.get("/latest-evidence")
def latest_calculation_evidence(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require_workspace_with_latest_calculation(project_id)
    latest = project.calculation_results[-1] if project.calculation_results else None
    evidence = dict((project.advanced_engineering or {}).get("workspaceCalculationEvidence") or {})
    return {
        "projectId": project.id,
        "evidence": evidence,
        "result": latest.model_dump(mode="json", by_alias=True) if latest else None,
    }


@router.get("/results", response_model=list[CalculationResult])
def results(
    project_id: str,
    limit: int = 5,
    repo: ProjectRepository = Depends(get_repository),
) -> list[CalculationResult]:
    result_limit = max(0, min(int(limit), 20))
    history = repo.require(project_id).calculation_results
    if result_limit == 0:
        return []
    return history[-result_limit:]


@router.get("/checks")
def checks(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    checks_list: list[dict] = []
    if project.calculation_results:
        latest = project.calculation_results[-1]
        checks_list.extend(latest.checks or [])
    else:
        checks_list.append({
            "ruleId": "PITGUARD-CALC-NOT-RUN",
            "objectId": project_id,
            "status": "manual_review",
            "message": "尚未运行计算，无法形成规范筛查结果。",
        })
    checks_list.append({
        "ruleId": "PITGUARD-PROFESSIONAL-REVIEW",
        "objectId": project_id,
        "status": "manual_review",
        "message": "自动筛查不替代注册岩土/结构工程师对规范适用性、公式条件、参数来源和施工图构造的复核。",
    })
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict] = []
    for item in checks_list:
        key = (
            str(item.get("ruleId")),
            str(item.get("objectId")),
            str(item.get("status")),
            str(item.get("calculatedValue")),
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    summary = {
        "pass": sum(1 for c in unique if c.get("status") == "pass"),
        "fail": sum(1 for c in unique if c.get("status") == "fail"),
        "warning": sum(1 for c in unique if c.get("status") == "warning"),
        "manualReview": sum(1 for c in unique if c.get("status") == "manual_review"),
        "manual_review": sum(1 for c in unique if c.get("status") == "manual_review"),
    }
    return {"checks": unique, "summary": summary, "professionalReviewRequired": True}


@router.get("/trace")
def calculation_trace(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_calculation_trace(repo.require_workspace_with_latest_calculation(project_id))


@router.get("/assurance")
def calculation_assurance(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require_workspace_with_latest_calculation(project_id)
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest is not None and latest.calculation_assurance:
        payload = dict(latest.calculation_assurance)
        payload["contractVerification"] = verify_current_calculation_contract(project, latest)
        return payload
    case = project.calculation_cases[-1] if project.calculation_cases else None
    if case is None:
        raise HTTPException(status_code=409, detail="No calculation case is available for assurance audit")
    input_audit = audit_calculation_inputs(project, case)
    if latest is None:
        return {
            "status": "fail",
            "eligibleForEngineeringUse": False,
            "eligibleForOfficialIssue": False,
            "contract": build_calculation_contract(project, case),
            "inputAudit": input_audit,
            "issues": [{"code": "RESULT-MISSING", "status": "fail", "message": "尚未运行计算。"}],
        }
    return assess_calculation_result(project, case, latest, input_audit=input_audit)
