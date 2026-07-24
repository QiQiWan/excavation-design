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
    return build_rebar_detailing(repo.require_workspace_with_latest_calculation(project_id), mode=mode)


@router.get("/deep-detailing")
def get_deep_detailing(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.rebar_detailing import build_rebar_detailing
    detailing = build_rebar_detailing(repo.require_workspace_with_latest_calculation(project_id), mode=mode)
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
    from app.services.rebar_scheme_optimizer import build_rebar_design_scheme, rebar_scheme_is_current
    project = repo.require_workspace_with_latest_calculation(project_id)
    stored = project.retaining_system.rebar_design_scheme if project.retaining_system and isinstance(project.retaining_system.rebar_design_scheme, dict) else {}
    if rebar_scheme_is_current(project, stored, mode):
        return stored
    return build_rebar_design_scheme(project, mode=mode)


@router.post("/apply-design-scheme")
def apply_design_scheme(
    project_id: str,
    payload: ApplyRebarSchemePayload = Body(default=ApplyRebarSchemePayload()),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme, rebar_scheme_is_current
    project = repo.require_with_latest_calculation(project_id)
    execution_mode = str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")).strip().lower()
    if payload.recalculate and execution_mode == "external":
        # Never build a multi-thousand-row reinforcement preview inside the API
        # process.  The isolated worker owns generation, section updates and any
        # required recalculation; the API only queues and returns immediately.
        queued_task = task_manager.submit(
            project.id,
            "rebar_design",
            {"mode": payload.mode, "apply": True, "recalculate": True},
        )
        stored = project.retaining_system.rebar_design_scheme if project.retaining_system and isinstance(project.retaining_system.rebar_design_scheme, dict) else {}
        if not rebar_scheme_is_current(project, stored, payload.mode):
            stored = {}
        return {
            "projectId": project.id,
            "mode": payload.mode,
            "scheme": stored or {"projectId": project.id, "mode": payload.mode, "status": "queued", "summary": {}, "checks": []},
            "retainingSystem": project.retaining_system,
            "recalculated": False,
            "recalculationCount": 0,
            "recalculationQueued": True,
            "calculationTask": queued_task.as_dict(include_logs=False),
        }
    scheme = apply_rebar_design_scheme(project, mode=payload.mode)
    recalculation_count = 0
    queued_task = None
    if payload.recalculate and bool(scheme.get("requiresRecalculation")):
        if execution_mode != "external":
            from app.services.intelligent_design_closure import run_intelligent_design_closure
            from app.services.calculation_state import mark_calculation_state_current
            from app.services.construction_stages import select_calculation_case_for_run
            while bool(scheme.get("requiresRecalculation")) and recalculation_count < 3:
                recalculation_count += 1
                case, _stage_selection = select_calculation_case_for_run(project)
                if not project.calculation_cases or project.calculation_cases[-1].id != case.id:
                    project.calculation_cases = [case]
                result, _closure = run_intelligent_design_closure(
                    project,
                    case,
                    auto_repair=False,
                )
                project.calculation_results.append(result)
                mark_calculation_state_current(project, result.id)
                # Rebuild and reapply against the updated stiffness and force
                # envelope until stable, with a bounded three-round closure.
                scheme = apply_rebar_design_scheme(project, mode=payload.mode)
    repo.save(project)
    return {
        "projectId": project.id,
        "mode": payload.mode,
        "scheme": scheme,
        "retainingSystem": project.retaining_system,
        "recalculated": recalculation_count > 0,
        "recalculationCount": recalculation_count,
        "recalculationQueued": queued_task is not None,
        "calculationTask": queued_task.as_dict(include_logs=False) if queued_task else None,
    }
