from __future__ import annotations

from fastapi import APIRouter, Depends

from app.compliance.assurance import evaluate_project_assurance
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/assurance", tags=["assurance"])


@router.get("/gap-analysis")
def gap_analysis(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_project_assurance(repo.require(project_id))
