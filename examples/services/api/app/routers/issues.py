from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services.issue_center import build_issue_center, locate_issue
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/issues", tags=["issues"])


@router.get("")
def get_issue_center(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_issue_center(repo.require(project_id))



@router.get("/locate/{issue_id}")
def locate_issue_endpoint(project_id: str, issue_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return locate_issue(repo.require(project_id), issue_id)
