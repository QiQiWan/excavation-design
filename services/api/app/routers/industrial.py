from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.industrial_readiness import (
    evaluate_industrial_readiness,
    execute_full_industrial_closure,
    run_geometry_qualification_suite,
)
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/industrial", tags=["industrial-readiness"])


@router.get("/readiness")
def get_industrial_readiness(
    project_id: str,
    include_detailing: bool = Query(False, alias="includeDetailing"),
    run_qualification: bool = Query(False, alias="runQualification"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    return evaluate_industrial_readiness(
        project,
        include_detailing=include_detailing,
        run_qualification=run_qualification,
    )


@router.post("/qualification")
def run_industrial_qualification(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    result = run_geometry_qualification_suite()
    project.advanced_engineering["qualificationSuite"] = result
    repo.save(
        project,
        action="industrial.qualification",
        summary=f"Geometry qualification suite completed: {result.get('status')}",
    )
    return result


@router.post("/closure")
def execute_industrial_closure(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    try:
        result = execute_full_industrial_closure(project, top_n=3)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo.save(
        project,
        action="industrial.closure",
        summary=f"P0-P3 industrial closure evaluated: {result.get('status')}",
    )
    return result
