from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import ConstructionObstacle, ExcavationModel, Polyline2D
from app.services.excavation_service import center_excavation_on_geology, generate_excavation_segments, make_excavation_model, polygon_metrics
from app.storage.repository import ProjectRepository, get_repository
from app.geology.model_builder import ensure_geological_model_covers_excavation
from app.services.calculation_state import invalidate_calculation_state

router = APIRouter(prefix="/api/projects/{project_id}/excavation", tags=["excavation"])


class ExcavationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = "Main excavation"
    outline: Polyline2D
    top_elevation: float = Field(alias="topElevation")
    bottom_elevation: float = Field(alias="bottomElevation")
    obstacles: list[ConstructionObstacle] = Field(default_factory=list)
    support_axis_offset: float | None = Field(default=None, alias="supportAxisOffset")
    basement_wall_offset: float | None = Field(default=None, alias="basementWallOffset")
    drawing_layers: list[dict] = Field(default_factory=list, alias="drawingLayers")
    explicit_placement: bool = Field(default=False, alias="explicitPlacement")


@router.post("", response_model=ExcavationModel)
def create_excavation(project_id: str, payload: ExcavationPayload, repo: ProjectRepository = Depends(get_repository)) -> ExcavationModel:
    project = repo.require(project_id)
    excavation = make_excavation_model(payload.name, payload.outline, payload.top_elevation, payload.bottom_elevation, project.design_settings.minimum_segment_length)
    excavation.obstacles = payload.obstacles
    excavation.support_axis_offset = payload.support_axis_offset
    excavation.basement_wall_offset = payload.basement_wall_offset
    excavation.drawing_layers = payload.drawing_layers
    excavation.explicit_placement = payload.explicit_placement
    excavation = center_excavation_on_geology(excavation, project.geological_model, project.design_settings.auto_center_excavation_on_geology)
    project.excavation = excavation
    ensure_geological_model_covers_excavation(project)
    invalidate_calculation_state(project, reason="excavation geometry or elevation changed", rebuild_cases=bool(project.retaining_system))
    repo.save(project)
    return excavation


@router.put("", response_model=ExcavationModel)
def update_excavation(project_id: str, payload: ExcavationPayload, repo: ProjectRepository = Depends(get_repository)) -> ExcavationModel:
    return create_excavation(project_id, payload, repo)


@router.post("/generate-segments", response_model=ExcavationModel)
def generate_segments(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> ExcavationModel:
    project = repo.require(project_id)
    if project.excavation is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Project has no excavation")
    project.excavation.segments = generate_excavation_segments(project.excavation.outline)
    metrics = polygon_metrics(project.excavation.outline)
    project.excavation.area = metrics.area
    project.excavation.perimeter = metrics.perimeter
    repo.save(project)
    return project.excavation


@router.get("", response_model=ExcavationModel | None)
def get_excavation(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> ExcavationModel | None:
    return repo.require(project_id).excavation
