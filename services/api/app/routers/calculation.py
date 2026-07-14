from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project
from app.schemas.domain import CalculationCase, CalculationResult
from app.storage.repository import ProjectRepository, get_repository
from app.services.calculation_trace import build_calculation_trace
from app.services.wall_length_optimizer import mark_wall_length_recalculated
from app.services.calculation_state import mark_calculation_state_current
from app.quality.formal_gate import build_formal_report_gate
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility

router = APIRouter(prefix="/api/projects/{project_id}/calculation", tags=["calculation"])


@router.post("/build-cases", response_model=list[CalculationCase])
def build_cases(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> list[CalculationCase]:
    project = repo.require(project_id)
    try:
        cases = build_default_construction_cases(project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project.calculation_cases = cases
    repo.save(project)
    return cases


@router.post("/run", response_model=CalculationResult)
def run(project_id: str, case_id: str | None = None, repo: ProjectRepository = Depends(get_repository)) -> CalculationResult:
    project = repo.require(project_id)
    case = None
    if case_id:
        case = next((c for c in project.calculation_cases if c.id == case_id), None)
        if case is None:
            raise HTTPException(status_code=404, detail=f"Calculation case not found: {case_id}")
    try:
        result = run_calculation(project, case)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project.calculation_results.append(result)
    mark_calculation_state_current(project, result.id)
    mark_wall_length_recalculated(project, result.id)
    repo.save(project)
    return result


@router.post("/diagnose-and-repair")
def diagnose_and_repair(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    """Run topology preflight, synchronize construction stages and recalculate.

    The response is intentionally compact so the UI can explain the root cause
    before loading the full calculation result from the project history.
    """
    project = repo.require(project_id)
    try:
        result = run_calculation(project, None, auto_repair=True)
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
    }


@router.post("/run-candidate-comparison")
def run_candidate_comparison(project_id: str, top_n: int = 3, repo: ProjectRepository = Depends(get_repository)) -> list[dict]:
    project = repo.require(project_id)
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
    return build_calculation_trace(repo.require(project_id))
