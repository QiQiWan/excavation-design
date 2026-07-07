from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import CoordinateSystem, DesignSettings, Project, UnitSystem
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1)
    location: str | None = None
    unit_system: UnitSystem | None = Field(default=None, alias="unitSystem")
    coordinate_system: CoordinateSystem | None = Field(default=None, alias="coordinateSystem")
    design_settings: DesignSettings | None = Field(default=None, alias="designSettings")


@router.post("", response_model=Project)
def create_project(payload: ProjectCreate, repo: ProjectRepository = Depends(get_repository)) -> Project:
    project = Project(
        name=payload.name,
        location=payload.location,
        unit_system=payload.unit_system or UnitSystem(),
        coordinate_system=payload.coordinate_system or CoordinateSystem(),
        design_settings=payload.design_settings or DesignSettings(),
    )
    return repo.create(project)


@router.get("", response_model=list[Project])
def list_projects(repo: ProjectRepository = Depends(get_repository)) -> list[Project]:
    return repo.list()


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> Project:
    return repo.require(project_id)


@router.put("/{project_id}", response_model=Project)
def update_project(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> Project:
    project = repo.require(project_id)
    data = project.model_dump(mode="json", by_alias=True)
    immutable = {"id", "createdAt"}
    for key, value in payload.items():
        if key not in immutable:
            data[key] = value
    return repo.save(Project.model_validate(data))


@router.delete("/{project_id}")
def delete_project(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, bool]:
    deleted = repo.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"deleted": True}
