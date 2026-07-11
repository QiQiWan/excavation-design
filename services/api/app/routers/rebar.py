from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.services.rebar_detailing import build_rebar_detailing
from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme, build_rebar_design_scheme
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/rebar", tags=["rebar"])


class ApplyRebarSchemePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    mode: str = Field(default="balanced", pattern="^(conservative|balanced|economic)$")
    recalculate: bool = True


@router.get("/detailing")
def get_rebar_detailing(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_rebar_detailing(repo.require(project_id), mode=mode)


@router.get("/design-scheme")
def get_rebar_design_scheme(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_rebar_design_scheme(repo.require(project_id), mode=mode)


@router.post("/apply-design-scheme")
def apply_design_scheme(
    project_id: str,
    payload: ApplyRebarSchemePayload = Body(default=ApplyRebarSchemePayload()),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    scheme = apply_rebar_design_scheme(project, mode=payload.mode)
    recalculated = False
    if payload.recalculate and bool(scheme.get("requiresRecalculation")):
        if not project.calculation_cases:
            project.calculation_cases = build_default_construction_cases(project)
        result = run_calculation(
            project,
            project.calculation_cases[-1] if project.calculation_cases else None,
            auto_repair=False,
            include_candidate_comparison=False,
        )
        project.calculation_results.append(result)
        # Rebuild and reapply the final reinforcement using the updated member
        # stiffness, force envelope and node bearing checks.
        scheme = apply_rebar_design_scheme(project, mode=payload.mode)
        recalculated = True
    repo.save(project)
    return {
        "projectId": project.id,
        "mode": payload.mode,
        "scheme": scheme,
        "retainingSystem": project.retaining_system,
        "recalculated": recalculated,
    }
