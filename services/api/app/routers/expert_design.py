from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.services.design_expert import build_expert_design_review
from app.services.design_pipeline import evaluate_design_pipeline
from app.services.wall_vertical_length_optimizer import apply_wall_vertical_length_candidate
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/expert-design", tags=["expert-design"])


class ApplyVerticalWallLengthPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str = Field(alias="candidateId")
    mode: str = Field(default="balanced", pattern="^(conservative|balanced|economic)$")
    recalculate: bool = True


@router.get("/review")
def expert_review(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_expert_design_review(repo.require(project_id), mode=mode)


@router.get("/pipeline")
def design_pipeline(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return evaluate_design_pipeline(repo.require(project_id))


@router.post("/apply-vertical-wall-length")
def apply_vertical_wall_length(
    project_id: str,
    payload: ApplyVerticalWallLengthPayload = Body(...),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    try:
        result = apply_wall_vertical_length_candidate(project, payload.candidate_id, mode=payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    calculation = None
    if payload.recalculate:
        if not project.calculation_cases:
            project.calculation_cases = build_default_construction_cases(project)
        calculation = run_calculation(
            project,
            project.calculation_cases[-1] if project.calculation_cases else None,
            auto_repair=False,
            include_candidate_comparison=False,
        )
        project.calculation_results.append(calculation)
        if project.retaining_system:
            item = project.retaining_system.layout_summary.get("wallVerticalLengthOptimization")
            if isinstance(item, dict):
                item["recomputeRequired"] = False
                item["calculationResultId"] = calculation.id
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["calculationState"] = {
            "requiresRecalculation": False,
            "reason": "围护墙竖向长度优化已重新计算",
        }
    repo.save(project)
    return {**result, "recalculated": calculation is not None, "calculationResult": calculation}
