from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import RetainingSystem, SupportLayoutRepairSummary
from app.geology.model_builder import ensure_geological_model_covers_excavation
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.support_layout_repair import adopt_support_layout_candidate, auto_repair_support_layout, set_support_optimization_locks
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/design", tags=["design"])


class OptimizeSupportsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    objective_weights: dict[str, float] = Field(default_factory=dict, alias="objectiveWeights")
    preset: str | None = None


class AdoptSupportCandidatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str = Field(alias="candidateId")


class SupportLockItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    target_type: str = Field(default="support_line", alias="targetType")
    support_id: str | None = Field(default=None, alias="supportId")
    endpoint: str | None = None
    level_index: int | None = Field(default=None, alias="levelIndex")
    obstacle_id: str | None = Field(default=None, alias="obstacleId")
    locked: bool | None = None
    reason: str | None = None


class LockSupportLinesPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    support_ids: list[str] = Field(default_factory=list, alias="supportIds")
    lock_items: list[SupportLockItem] = Field(default_factory=list, alias="lockItems")
    level_indices: list[int] = Field(default_factory=list, alias="levelIndices")
    obstacle_ids: list[str] = Field(default_factory=list, alias="obstacleIds")
    locked: bool = True
    reason: str | None = None
    replace: bool = False


def _require_excavation(project_id: str, repo: ProjectRepository):
    project = repo.require(project_id)
    if project.excavation is None:
        raise HTTPException(status_code=422, detail="Project has no excavation")
    return project


@router.post("/auto-diaphragm-wall", response_model=RetainingSystem)
def design_diaphragm_wall(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system)
    repo.save(project)
    return project.retaining_system


@router.post("/auto-supports", response_model=RetainingSystem)
def design_supports(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    project.retaining_system = auto_supports(project.excavation, project.retaining_system)
    repo.save(project)
    return project.retaining_system




@router.post("/auto-repair-supports", response_model=SupportLayoutRepairSummary)
def repair_supports(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    result = auto_repair_support_layout(project)
    repo.save(project)
    return result



@router.post("/optimize-supports", response_model=SupportLayoutRepairSummary)
def optimize_supports(project_id: str, payload: OptimizeSupportsPayload | None = Body(default=None), repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    result = auto_repair_support_layout(project, objective_weights=(payload.objective_weights if payload else None), preset=(payload.preset if payload else None))
    repo.save(project)
    return result


@router.post("/adopt-support-candidate", response_model=SupportLayoutRepairSummary)
def adopt_support_candidate(project_id: str, payload: AdoptSupportCandidatePayload, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    result = adopt_support_layout_candidate(project, payload.candidate_id)
    repo.save(project)
    return result


@router.post("/lock-support-lines", response_model=SupportLayoutRepairSummary)
def lock_support_lines(project_id: str, payload: LockSupportLinesPayload, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    result = set_support_optimization_locks(
        project,
        support_ids=payload.support_ids,
        locked=payload.locked,
        reason=payload.reason,
        lock_items=[item.model_dump(mode="json", by_alias=True) for item in payload.lock_items],
        level_indices=payload.level_indices,
        obstacle_ids=payload.obstacle_ids,
        replace=payload.replace,
    )
    repo.save(project)
    return result

@router.get("/retaining-system", response_model=RetainingSystem | None)
def get_retaining_system(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem | None:
    return repo.require(project_id).retaining_system


@router.put("/retaining-system", response_model=RetainingSystem)
def update_retaining_system(project_id: str, payload: dict, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    project = repo.require(project_id)
    project.retaining_system = RetainingSystem.model_validate(payload)
    repo.save(project)
    return project.retaining_system
