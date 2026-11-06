from __future__ import annotations

import os
from typing import Any, Literal

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


def _prioritize_rebar_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"fail": 0, "warning": 1, "manual_review": 1, "preliminary": 2, "pass": 3}
    return sorted(rows, key=lambda row: (priority.get(str(row.get("status") or "manual_review"), 2), str(row.get("hostCode") or row.get("hostId") or "")))


def _compact_rebar_scheme_for_read(scheme: dict[str, Any], *, detail: str, check_limit: int) -> dict[str, Any]:
    if detail == "full":
        result = dict(scheme)
        result["readProfile"] = "full"
        result["listSummary"] = {
            "checks": {"total": len(list(scheme.get("checks") or [])), "returned": len(list(scheme.get("checks") or [])), "truncated": False},
            "wallZones": {"total": len(list(scheme.get("wallZones") or [])), "returned": len(list(scheme.get("wallZones") or [])), "truncated": False},
            "supportSchemes": {"total": len(list(scheme.get("supportSchemes") or [])), "returned": len(list(scheme.get("supportSchemes") or [])), "truncated": False},
            "beamNodeSchemes": {"total": len(list(scheme.get("beamNodeSchemes") or [])), "returned": len(list(scheme.get("beamNodeSchemes") or [])), "truncated": False},
        }
        return result

    result = dict(scheme)
    all_checks = [dict(row) for row in list(scheme.get("checks") or []) if isinstance(row, dict)]
    prioritized = _prioritize_rebar_checks(all_checks)
    limit = max(0, min(int(check_limit), 500))
    selected = prioritized[:limit]
    constructability = result.get("constructability")
    if isinstance(constructability, dict):
        compact_constructability = dict(constructability)
        constructability_checks = [dict(row) for row in list(constructability.get("checks") or []) if isinstance(row, dict)]
        compact_constructability["checks"] = _prioritize_rebar_checks(constructability_checks)[: min(24, limit)]
        compact_constructability["checksTruncated"] = len(constructability_checks) > len(compact_constructability["checks"])
        compact_constructability["checkCount"] = len(constructability_checks)
        result["constructability"] = compact_constructability
    result["checks"] = selected
    result["readProfile"] = "summary"
    result["listSummary"] = {
        "checks": {"total": len(all_checks), "returned": len(selected), "truncated": len(selected) < len(all_checks)},
        "wallZones": {"total": len(list(scheme.get("wallZones") or [])), "returned": len(list(scheme.get("wallZones") or [])), "truncated": False},
        "supportSchemes": {"total": len(list(scheme.get("supportSchemes") or [])), "returned": len(list(scheme.get("supportSchemes") or [])), "truncated": False},
        "beamNodeSchemes": {"total": len(list(scheme.get("beamNodeSchemes") or [])), "returned": len(list(scheme.get("beamNodeSchemes") or [])), "truncated": False},
    }
    return result


def _resolve_rebar_scheme(project_id: str, mode: str, repo: ProjectRepository) -> dict[str, Any]:
    from app.services.rebar_scheme_optimizer import build_rebar_design_scheme, rebar_scheme_is_current
    project = repo.require_workspace_with_latest_calculation(project_id)
    stored = project.retaining_system.rebar_design_scheme if project.retaining_system and isinstance(project.retaining_system.rebar_design_scheme, dict) else {}
    if rebar_scheme_is_current(project, stored, mode):
        return dict(stored)
    return dict(build_rebar_design_scheme(project, mode=mode))


@router.get("/design-scheme")
def get_rebar_design_scheme(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    detail: Literal["summary", "full"] = Query("summary"),
    check_limit: int = Query(60, alias="checkLimit", ge=0, le=500),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    scheme = _resolve_rebar_scheme(project_id, mode, repo)
    return _compact_rebar_scheme_for_read(scheme, detail=detail, check_limit=check_limit)


@router.get("/design-scheme/checks")
def get_rebar_design_checks(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    scheme = _resolve_rebar_scheme(project_id, mode, repo)
    rows = [dict(row) for row in list(scheme.get("checks") or []) if isinstance(row, dict)]
    if status:
        accepted = {item.strip() for item in status.split(",") if item.strip()}
        rows = [row for row in rows if str(row.get("status") or "") in accepted]
    query = str(q or "").strip().lower()
    if query:
        rows = [row for row in rows if query in str(row).lower()]
    rows = _prioritize_rebar_checks(rows)
    total = len(rows)
    page = rows[offset: offset + limit]
    return {
        "projectId": project_id,
        "mode": mode,
        "offset": offset,
        "limit": limit,
        "total": total,
        "returned": len(page),
        "hasMore": offset + len(page) < total,
        "rows": page,
    }


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
