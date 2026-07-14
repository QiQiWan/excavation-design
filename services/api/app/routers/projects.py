from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import CoordinateSystem, DesignSettings, Project, ProjectSummary, UnitSystem
from app.storage.repository import ProjectRepository, get_repository
from app.services.design_scheme_ledger import build_project_dashboard, build_design_scheme_ledger
from app.tasks.manager import task_manager
from app.geometry.consistency import geometry_consistency_summary

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


@router.get("", response_model=list[ProjectSummary])
def list_projects(repo: ProjectRepository = Depends(get_repository)) -> list[ProjectSummary]:
    return repo.list_summaries()


@router.get("/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    result_history_limit: int = 1,
    repo: ProjectRepository = Depends(get_repository),
) -> Project:
    project = repo.require(project_id)
    limit = max(0, min(int(result_history_limit), 20))
    if limit == 0:
        project.calculation_results = []
    elif len(project.calculation_results) > limit:
        project.calculation_results = project.calculation_results[-limit:]
    return project




@router.get("/{project_id}/geometry-consistency")
def get_geometry_consistency(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return geometry_consistency_summary(repo.require(project_id))


@router.get("/{project_id}/dashboard")
def get_project_dashboard(project_id: str, mode: str = "balanced", repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_project_dashboard(repo.require(project_id), mode=mode)


@router.get("/{project_id}/design-scheme-ledger")
def get_design_scheme_ledger(project_id: str, mode: str = "balanced", repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_design_scheme_ledger(repo.require(project_id), mode=mode)


DESIGN_AFFECTING_KEYS = {
    "unitSystem", "coordinateSystem", "designSettings", "boreholes", "strata",
    "geologicalModel", "excavation", "retainingSystem", "calculationCases",
}


@router.put("/{project_id}", response_model=Project)
def update_project(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> Project:
    project = repo.require(project_id)
    data = project.model_dump(mode="json", by_alias=True)
    immutable = {"id", "createdAt"}
    changed_design_keys: list[str] = []
    for key, value in payload.items():
        if key in immutable:
            continue
        if key in DESIGN_AFFECTING_KEYS and data.get(key) != value:
            changed_design_keys.append(key)
        data[key] = value
    updated = Project.model_validate(data)
    if changed_design_keys:
        updated.calculation_results = []
        if any(key != "calculationCases" for key in changed_design_keys):
            updated.calculation_cases = []
        updated.advanced_engineering.pop("latestSuite", None)
        updated.advanced_engineering["requiresRecalculation"] = True
        updated.advanced_engineering["invalidationReason"] = {
            "type": "design_input_changed",
            "keys": changed_design_keys,
        }
        message = "设计输入已变更，原计算结果与正式发行状态已失效，请重新建立工况并计算。"
        if not updated.messages or updated.messages[-1] != message:
            updated.messages.append(message)
    return repo.save(updated)


@router.delete("/{project_id}")
def delete_project(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    # Resolve first so a repeated delete returns a clear 404 and does not hide a
    # client-side selection/state bug.
    project = repo.require(project_id)
    cleanup = task_manager.delete_project_records(project_id)
    deleted = repo.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {
        "deleted": True,
        "projectId": project_id,
        "projectName": project.name,
        **cleanup,
    }
