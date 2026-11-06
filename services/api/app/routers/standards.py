from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services.standards_matrix import build_online_documentation, build_standards_process_matrix
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(tags=["standards-documentation"])


@router.get("/api/standards/catalog")
def standards_catalog() -> dict:
    data = build_standards_process_matrix()
    return {"catalog": data["catalog"], "precedence": data["precedence"], "usageNote": data["usageNote"]}


@router.get("/api/standards/process-matrix")
def standards_process_matrix() -> dict:
    return build_standards_process_matrix()


@router.get("/api/projects/{project_id}/standards/process-matrix")
def project_standards_process_matrix(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_standards_process_matrix(repo.require(project_id))


@router.get("/api/documentation")
def online_documentation() -> dict:
    return build_online_documentation()
