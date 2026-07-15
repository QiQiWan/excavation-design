from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.storage.repository import ProjectRepository, get_repository
from app.tasks.manager import task_manager

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
    from app.services.rebar_detailing import build_rebar_detailing
    return build_rebar_detailing(repo.require(project_id), mode=mode)


@router.get("/deep-detailing")
def get_deep_detailing(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.rebar_detailing import build_rebar_detailing
    detailing = build_rebar_detailing(repo.require(project_id), mode=mode)
    return {
        "projectId": project_id,
        "mode": mode,
        "deepDetailing": detailing.get("deepDetailing", {}),
        "summary": detailing.get("summary", {}),
    }


@router.get("/design-scheme")
def get_rebar_design_scheme(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
    return build_rebar_design_scheme(repo.require(project_id), mode=mode)


@router.post("/apply-design-scheme")
def apply_design_scheme(
    project_id: str,
    payload: ApplyRebarSchemePayload = Body(default=ApplyRebarSchemePayload()),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme
    project = repo.require(project_id)
    scheme = apply_rebar_design_scheme(project, mode=payload.mode)
    recalculated = False
    queued_task = None
    if payload.recalculate and bool(scheme.get("requiresRecalculation")):
        if str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")).strip().lower() == "external":
            repo.save(project)
            queued_task = task_manager.submit(project.id, "calculation_full", {"topN": 0})
        else:
            from app.calculation.engine import build_default_construction_cases, run_calculation
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
        "recalculationQueued": queued_task is not None,
        "calculationTask": queued_task.as_dict(include_logs=False) if queued_task else None,
    }
