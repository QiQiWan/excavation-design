from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.geology.model_builder import (
    build_geological_model_from_boreholes,
    geological_coverage_audit,
    required_geological_design_bounds,
)
from app.geology.section import extract_representative_section
from app.schemas.domain import GeologicalModel, GeologicalSection
from app.services.vtu_import import parse_vtu
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/geology", tags=["geology"])


@router.post("/build-model", response_model=GeologicalModel)
def build_model(project_id: str, grid_size: float = 10.0, repo: ProjectRepository = Depends(get_repository)) -> GeologicalModel:
    project = repo.require(project_id)
    try:
        model = build_geological_model_from_boreholes(
            project.boreholes,
            grid_size=grid_size,
            required_bounds=required_geological_design_bounds(project),
            max_extrapolation_distance_m=project.design_settings.geology_max_extrapolation_distance_m,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project.geological_model = model
    repo.save(project)
    return model


@router.post("/import-vtu")
async def import_vtu(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    try:
        mesh = parse_vtu(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if project.geological_model is None:
        project.geological_model = GeologicalModel()
    project.geological_model.vtu_mesh = mesh
    project.geological_model.warnings = list(dict.fromkeys(project.geological_model.warnings + mesh.get("warnings", [])))
    repo.save(project)
    return mesh


@router.get("/model", response_model=GeologicalModel | None)
def get_model(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> GeologicalModel | None:
    return repo.require(project_id).geological_model


@router.get("/coverage")
def get_coverage(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return geological_coverage_audit(repo.require(project_id))


@router.get("/section", response_model=GeologicalSection)
def get_section(project_id: str, segment_id: str, repo: ProjectRepository = Depends(get_repository)) -> GeologicalSection:
    project = repo.require(project_id)
    try:
        return extract_representative_section(project, segment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
