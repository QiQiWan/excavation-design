from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.services.cad_template import normalize_cad_template, update_project_cad_template, validate_cad_template
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/cad-template", tags=["cad-template"])


@router.get("")
def get_cad_template(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    return normalize_cad_template(project)


@router.put("")
def put_cad_template(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    update_project_cad_template(project, payload)
    repo.save(project)
    return normalize_cad_template(project)



@router.get("/validation")
def get_cad_template_validation(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    return validate_cad_template(project)
