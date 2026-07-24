from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.schemas.domain import ExternalCollaborationRecord
from app.services.design_core_v387 import (
    add_external_collaboration,
    build_delivery_quality,
    build_design_core_workflow,
    build_member_envelopes,
    build_parameter_confirmation,
    build_reinforcement_closure,
    build_release_qualification,
    build_rule_evidence,
    build_scheme_search_assurance,
    confirm_parameter_records,
    prepare_design_snapshot,
    update_design_review_request,
)
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/design-core", tags=["design-core-v387"])


def _actor(request: Request) -> str:
    identity = getattr(request.state, "pitguard_identity", None)
    return str(getattr(identity, "actor", None) or "designer")


@router.get("")
def design_core_overview(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_design_core_workflow(repo.require(project_id))


@router.get("/parameters")
def parameter_governance(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    # Read endpoints must not advance the project revision. Missing provenance is
    # projected in memory and becomes persistent only through an explicit
    # confirmation or design mutation. This prevents dashboard refreshes from
    # creating optimistic-lock conflicts.
    return build_parameter_confirmation(project)


@router.get("/bundle")
def design_core_bundle(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    """Return the complete design-core dashboard from one project hydration.

    V3.87.1 loaded seven panels concurrently. In React strict mode this could
    issue fourteen requests and repeatedly hydrate the same large project,
    causing slow layout, races and avoidable database pressure. The bundle keeps
    each sub-payload independent while sharing one authoritative project read.
    """
    project = repo.require(project_id)
    parameters = build_parameter_confirmation(project)
    return {
        "schema": "pitguard-design-core-bundle-v3872",
        "overview": build_design_core_workflow(project),
        "parameters": parameters,
        "rules": build_rule_evidence(project),
        "schemes": build_scheme_search_assurance(project),
        "reinforcement": build_reinforcement_closure(project),
        "delivery": build_delivery_quality(project),
        "collaboration": {
            "records": [row.model_dump(mode="json", by_alias=True) for row in project.external_collaboration_records],
            "reviewRequests": [row.model_dump(mode="json", by_alias=True) for row in project.design_review_requests],
            "boundary": "外部资料只触发设计复核，不承担施工计划、现场快照或偏差事件管理。",
        },
    }


@router.patch("/parameters/confirm")
def confirm_parameters(
    project_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    updates = list(payload.get("updates") or [])
    if not updates:
        raise HTTPException(status_code=422, detail={"code": "PARAMETER_CONFIRMATION_UPDATES_REQUIRED"})
    result = confirm_parameter_records(project, updates, actor=_actor(request))
    repo.save(project, action="design_core.confirm_parameters", summary="Confirm parameter sources and formal-design eligibility")
    return {"update": result, "governance": build_parameter_confirmation(project)}


@router.get("/rules")
def rule_evidence(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_rule_evidence(repo.require(project_id))


@router.get("/schemes")
def scheme_assurance(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_scheme_search_assurance(repo.require(project_id))


@router.get("/member-envelopes")
def member_envelopes(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_member_envelopes(repo.require(project_id))


@router.get("/reinforcement-closure")
def reinforcement_closure(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_reinforcement_closure(repo.require(project_id))


@router.get("/delivery-quality")
def delivery_quality(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_delivery_quality(repo.require(project_id))


@router.post("/design-snapshots")
def create_design_snapshot(
    project_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    purpose = str(payload.get("purpose") or "internal_review")
    if purpose not in {"internal_review", "external_review", "approval", "construction"}:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SNAPSHOT_PURPOSE"})
    result = prepare_design_snapshot(project, purpose=purpose, actor=_actor(request), persist=True)
    repo.save(project, action="design_core.create_design_snapshot", summary=f"Create unified design snapshot for {purpose}")
    return result


@router.get("/design-snapshots")
def list_design_snapshots(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return {"snapshots": [row.model_dump(mode="json", by_alias=True) for row in project.design_snapshots]}


@router.get("/collaboration")
def list_collaboration(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return {
        "records": [row.model_dump(mode="json", by_alias=True) for row in project.external_collaboration_records],
        "reviewRequests": [row.model_dump(mode="json", by_alias=True) for row in project.design_review_requests],
        "boundary": "外部资料只触发设计复核，不承担施工计划、现场快照或偏差事件管理。",
    }


@router.post("/collaboration")
def create_collaboration(
    project_id: str,
    payload: ExternalCollaborationRecord,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    result = add_external_collaboration(project, payload.model_dump(mode="json", by_alias=False))
    repo.save(project, action="design_core.add_external_collaboration", summary="Add external design-reference record")
    return result


@router.patch("/review-requests/{request_id}")
def patch_review_request(
    project_id: str,
    request_id: str,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    try:
        result = update_design_review_request(project, request_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo.save(project, action="design_core.update_review_request", summary="Update design review request")
    return result


@router.get("/release-qualification")
def release_qualification(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_release_qualification(repo.require(project_id))
