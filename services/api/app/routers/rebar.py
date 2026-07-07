from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services.rebar_detailing import build_rebar_detailing
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/rebar", tags=["rebar"])


@router.get("/detailing")
def get_rebar_detailing(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_rebar_detailing(repo.require(project_id))
