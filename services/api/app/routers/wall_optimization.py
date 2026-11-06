from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services.wall_length_optimizer import analyze_wall_length_redundancy, apply_wall_length_candidate
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/wall-optimization", tags=["wall-optimization"])


class ApplyWallLengthCandidatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str = Field(alias="candidateId")
    mode: str = "balanced"
    target_low: float = Field(default=2.0, alias="targetLow")
    target_high: float = Field(default=8.0, alias="targetHigh")


@router.get("/length-redundancy")
def get_wall_length_redundancy(
    project_id: str,
    mode: str = Query(default="balanced", pattern="^(conservative|balanced|economic)$"),
    target_low: float = Query(default=2.0, alias="targetLow"),
    target_high: float = Query(default=8.0, alias="targetHigh"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    return analyze_wall_length_redundancy(project, target_low=target_low, target_high=target_high, mode=mode)


@router.post("/apply-length-candidate")
def apply_length_candidate(project_id: str, payload: ApplyWallLengthCandidatePayload, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    try:
        result = apply_wall_length_candidate(project, payload.candidate_id, mode=payload.mode, target_low=payload.target_low, target_high=payload.target_high)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo.save(project)
    return result
