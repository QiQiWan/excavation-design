from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import RetainingSystem, SupportLayoutRepairSummary
from app.geology.model_builder import ensure_geological_model_covers_excavation
from app.services.design_service import auto_diaphragm_wall, auto_supports, support_layout_config_from_settings
from app.services.support_layout import plan_shape_diagnostics
from app.services.support_layout_repair import adopt_support_layout_candidate, auto_repair_support_layout, set_support_optimization_locks
from app.services.calculation_state import invalidate_calculation_state
from app.services.support_scheme_designer_audit import audit_support_scheme_designer
from app.services.support_deep_design import evaluate_support_deep_design, optimize_support_deep_design
from app.services.calculation_resource_estimator import estimate_calculation_resources
from app.services.support_layout_import import import_support_layout_csv
from app.services.design_qualification import build_design_qualification, build_support_system_options
from app.services.progressive_design import build_progressive_design_session, merge_progressive_config, normalize_progressive_config
from app.services.design_workspace_bootstrap import build_design_workspace_bootstrap, invalidate_design_workspace_bootstrap
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/design", tags=["design"])


class OptimizeSupportsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    objective_weights: dict[str, float] = Field(default_factory=dict, alias="objectiveWeights")
    preset: str | None = None
    topology_family: str | None = Field(default=None, alias="topologyFamily")


class AdoptSupportCandidatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str = Field(alias="candidateId")


class SupportDeepDesignPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    max_iterations: int | None = Field(default=None, alias="maxIterations", ge=1, le=6)


class ConcaveTransferDetailingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    frame_analysis_status: str = Field(default="missing", alias="frameAnalysisStatus")
    node_detailing_status: str = Field(default="missing", alias="nodeDetailingStatus")
    stage_review_status: str = Field(default="missing", alias="stageReviewStatus")
    reaction_iteration_status: str = Field(default="missing", alias="reactionIterationStatus")
    spatial_effect_status: str = Field(default="missing", alias="spatialEffectStatus")
    torsion_detailing_status: str = Field(default="missing", alias="torsionDetailingStatus")
    reviewer: str = Field(min_length=1, max_length=120)
    notes: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    professional_credential: dict[str, Any] = Field(default_factory=dict, alias="professionalCredential")
    status: str = "approved"


class SupportLockItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    target_type: str = Field(default="support_line", alias="targetType")
    support_id: str | None = Field(default=None, alias="supportId")
    endpoint: str | None = None
    level_index: int | None = Field(default=None, alias="levelIndex")
    obstacle_id: str | None = Field(default=None, alias="obstacleId")
    locked: bool | None = None
    reason: str | None = None


class LockSupportLinesPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    support_ids: list[str] = Field(default_factory=list, alias="supportIds")
    lock_items: list[SupportLockItem] = Field(default_factory=list, alias="lockItems")
    level_indices: list[int] = Field(default_factory=list, alias="levelIndices")
    obstacle_ids: list[str] = Field(default_factory=list, alias="obstacleIds")
    locked: bool = True
    reason: str | None = None
    replace: bool = False




class ProgressiveDesignPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    current_stage: str | None = Field(default=None, alias="currentStage")
    decisions: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict, alias="resourcePolicy")
    action: str | None = None
    expected_version: int | None = Field(default=None, alias="expectedVersion")


def _require_embedded_support_optimization() -> None:
    if str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")).strip().lower() == "external":
        raise HTTPException(
            status_code=409,
            detail=(
                "生产环境中的支撑候选优化由独立 pitguard-worker 进程执行。"
                "请通过 /api/projects/{project_id}/tasks 提交 support_layout_optimization 任务。"
            ),
        )

def _require_excavation(project_id: str, repo: ProjectRepository):
    project = repo.require(project_id)
    if project.excavation is None:
        raise HTTPException(status_code=422, detail="Project has no excavation")
    return project


def _require_excavation_workspace(project_id: str, repo: ProjectRepository):
    """Load the bounded workspace projection for read-only design panels."""
    project = repo.require_workspace(project_id)
    if project.excavation is None:
        raise HTTPException(status_code=422, detail="Project has no excavation")
    return project




@router.get("/core-status")
def get_core_design_status(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    from app.services.core_workspace import build_core_workspace_status
    project = repo.require_workspace_with_latest_calculation(project_id)
    return build_core_workspace_status(project, repo.store.get_payload_info(project_id))


@router.get("/workspace-bootstrap")
def get_design_workspace_bootstrap(
    project_id: str,
    refresh: bool = False,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_design_workspace_bootstrap(repo, project_id, force=bool(refresh))


@router.get("/qualification")
def get_design_qualification(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    # Backward-compatible route backed by the shared single-flight snapshot.
    return dict(build_design_workspace_bootstrap(repo, project_id).get("qualification") or {})


@router.get("/progressive")
def get_progressive_design(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    # Backward-compatible route backed by the shared single-flight snapshot.
    return dict(build_design_workspace_bootstrap(repo, project_id).get("progressive") or {})


@router.put("/progressive")
def update_progressive_design(
    project_id: str,
    payload: ProgressiveDesignPatch,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require_workspace(project_id)
    current = normalize_progressive_config(project, repo.store.get_progressive_design_config(project_id))
    patch = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    expected = patch.pop("expectedVersion", None)
    merged = normalize_progressive_config(project, merge_progressive_config(current, patch))
    conflict_resolved = False
    try:
        stored = repo.store.save_progressive_design_config(
            project_id, merged, expected_version=expected,
        )
    except RuntimeError:
        # Browser panels can submit adjacent decisions before the refreshed
        # session version reaches every component.  Rebase the small patch on
        # the latest server state once, preserving unrelated decisions instead
        # of returning repeated 409 responses that stall the design workflow.
        latest = normalize_progressive_config(
            project, repo.store.get_progressive_design_config(project_id)
        )
        latest_version = int(latest.get("sessionVersion") or 0)
        rebased = normalize_progressive_config(project, merge_progressive_config(latest, patch))
        stored = repo.store.save_progressive_design_config(
            project_id, rebased, expected_version=latest_version,
        )
        conflict_resolved = True
    invalidate_design_workspace_bootstrap(project_id, db_path=str(repo.store.db_path))
    result = dict(build_design_workspace_bootstrap(repo, project_id, force=True).get("progressive") or {})
    result["conflictResolved"] = conflict_resolved
    return result


@router.get("/candidate-previews")
def get_candidate_previews(
    project_id: str,
    limit: int = 12,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    if repo.store.get_payload_info(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return repo.store.get_candidate_preview_bundle(project_id, limit=max(1, min(limit, 20)))


@router.get("/system-options")
def get_support_system_options(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return dict(build_design_workspace_bootstrap(repo, project_id).get("systemOptions") or {})

@router.get("/plan-shape-diagnostics")
def get_plan_shape_diagnostics(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return dict(build_design_workspace_bootstrap(repo, project_id).get("shapeDiagnostics") or {})


@router.get("/support-designer-audit")
def get_support_designer_audit(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = _require_excavation_workspace(project_id, repo)
    return audit_support_scheme_designer(project)


@router.get("/support-deep-design")
def get_support_deep_design(project_id: str, include_members: bool = False, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = _require_excavation_workspace(project_id, repo)
    return evaluate_support_deep_design(project, include_members=bool(include_members))


@router.post("/support-deep-design/optimize")
def run_support_deep_design(project_id: str, payload: SupportDeepDesignPayload | None = Body(default=None), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = _require_excavation(project_id, repo)
    result = optimize_support_deep_design(project, max_iterations=(payload.max_iterations if payload else None))
    invalidate_calculation_state(project, reason="support member/column deep-design iteration changed adopted member properties", rebuild_cases=False)
    repo.save(project)
    return result


@router.get("/calculation-resource-estimate")
def get_calculation_resource_estimate(
    project_id: str,
    candidate_count: int = 0,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = _require_excavation_workspace(project_id, repo)
    return estimate_calculation_resources(project, candidate_count=max(0, min(candidate_count, 3)))


@router.post("/auto-supports-by-shape")
def design_supports_by_shape(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    _require_embedded_support_optimization()
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    excavation = project.excavation
    diagnostics = plan_shape_diagnostics(
        list(excavation.outline.points),
        local_pit_count=len(excavation.local_pits or []),
        has_center_island=any(
            getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
            for item in (excavation.obstacles or [])
        ),
    )
    families = [str(item) for item in diagnostics.get("supportedTopologyFamilies", [])]
    selected = families[0] if families else "balanced_grid"
    if selected not in {"direct_grid", "hybrid_diagonal", "ring_radial", "zoned_direct"}:
        selected = "balanced_grid"
    if project.retaining_system is None or not project.retaining_system.diaphragm_walls:
        project.retaining_system = auto_diaphragm_wall(excavation, project.retaining_system, project.design_settings)
    project.retaining_system = auto_supports(
        excavation,
        project.retaining_system,
        layout_config=support_layout_config_from_settings(project.design_settings, topology_strategy=selected),
    )
    invalidate_calculation_state(project, reason=f"shape-aware support system regenerated using {selected}", rebuild_cases=True, invalidate_candidates=True)
    repo.save(project)
    return {
        "diagnostics": diagnostics,
        "selectedTopologyFamily": selected,
        "retainingSystem": project.retaining_system.model_dump(mode="json", by_alias=True),
    }


@router.post("/auto-diaphragm-wall", response_model=RetainingSystem)
def design_diaphragm_wall(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system, project.design_settings)
    invalidate_calculation_state(project, reason="diaphragm wall geometry regenerated", rebuild_cases=False, invalidate_candidates=True)
    repo.save(project)
    return project.retaining_system


@router.post("/auto-supports", response_model=RetainingSystem)
def design_supports(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    _require_embedded_support_optimization()
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    project.retaining_system = auto_supports(project.excavation, project.retaining_system, layout_config=support_layout_config_from_settings(project.design_settings))
    invalidate_calculation_state(project, reason="support system regenerated", rebuild_cases=True, invalidate_candidates=True)
    repo.save(project)
    return project.retaining_system




@router.post("/import-support-layout")
async def import_support_layout(
    project_id: str,
    file: UploadFile = File(...),
    replace: bool = True,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = _require_excavation(project_id, repo)
    try:
        result = import_support_layout_csv(project, await file.read(), replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_calculation_state(project, reason="engineer/reference support layout imported", rebuild_cases=True, invalidate_candidates=True)
    repo.save(project)
    return result


@router.post("/auto-repair-supports", response_model=SupportLayoutRepairSummary)
def repair_supports(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    _require_embedded_support_optimization()
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    result = auto_repair_support_layout(project)
    invalidate_calculation_state(project, reason="support topology automatically repaired", rebuild_cases=True)
    repo.save(project)
    return result



@router.post("/optimize-supports", response_model=SupportLayoutRepairSummary)
def optimize_supports(project_id: str, payload: OptimizeSupportsPayload | None = Body(default=None), repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    _require_embedded_support_optimization()
    project = _require_excavation(project_id, repo)
    ensure_geological_model_covers_excavation(project)
    result = auto_repair_support_layout(project, objective_weights=(payload.objective_weights if payload else None), preset=(payload.preset if payload else None), topology_family=(payload.topology_family if payload else None))
    invalidate_calculation_state(project, reason="support optimization candidate set regenerated and best topology applied", rebuild_cases=True)
    repo.save(project)
    return result


@router.post("/adopt-support-candidate", response_model=SupportLayoutRepairSummary)
def adopt_support_candidate(project_id: str, payload: AdoptSupportCandidatePayload, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    result = adopt_support_layout_candidate(project, payload.candidate_id)
    repo.save(project)
    return result


@router.post("/lock-support-lines", response_model=SupportLayoutRepairSummary)
def lock_support_lines(project_id: str, payload: LockSupportLinesPayload, repo: ProjectRepository = Depends(get_repository)) -> SupportLayoutRepairSummary:
    project = _require_excavation(project_id, repo)
    result = set_support_optimization_locks(
        project,
        support_ids=payload.support_ids,
        locked=payload.locked,
        reason=payload.reason,
        lock_items=[item.model_dump(mode="json", by_alias=True) for item in payload.lock_items],
        level_indices=payload.level_indices,
        obstacle_ids=payload.obstacle_ids,
        replace=payload.replace,
    )
    repo.save(project)
    return result

@router.get("/concave-transfer-detailing")
def get_concave_transfer_detailing(
    project_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    from app.services.concave_transfer_delivery import evaluate_concave_transfer_delivery

    project = repo.require_workspace(project_id)
    transfer_audit = dict(((project.retaining_system.layout_summary if project.retaining_system else {}) or {}).get("transferSystem") or {})
    return evaluate_concave_transfer_delivery(project, transfer_audit)


@router.put("/concave-transfer-detailing")
def put_concave_transfer_detailing(
    project_id: str,
    payload: ConcaveTransferDetailingPayload,
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    from app.services.concave_transfer_delivery import save_concave_transfer_detailing_approval

    project = repo.require(project_id)
    try:
        result = save_concave_transfer_detailing_approval(
            project,
            evidence={
                "frameAnalysisStatus": payload.frame_analysis_status,
                "nodeDetailingStatus": payload.node_detailing_status,
                "stageReviewStatus": payload.stage_review_status,
                "reactionIterationStatus": payload.reaction_iteration_status,
                "spatialEffectStatus": payload.spatial_effect_status,
                "torsionDetailingStatus": payload.torsion_detailing_status,
            },
            reviewer=payload.reviewer,
            notes=payload.notes,
            evidence_refs=payload.evidence_refs,
            professional_credential=payload.professional_credential,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo.save(
        project,
        action="design.concave_transfer_detailing",
        summary=f"Recorded concave transfer detailing approval by {payload.reviewer}",
    )
    return result


@router.get("/retaining-system", response_model=RetainingSystem | None)
def get_retaining_system(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem | None:
    return repo.require(project_id).retaining_system


@router.put("/retaining-system", response_model=RetainingSystem)
def update_retaining_system(project_id: str, payload: dict, repo: ProjectRepository = Depends(get_repository)) -> RetainingSystem:
    project = repo.require(project_id)
    previous = {
        wall.segment_id: wall
        for wall in (project.retaining_system.diaphragm_walls if project.retaining_system else [])
    }
    updated = RetainingSystem.model_validate(payload)
    for wall in updated.diaphragm_walls:
        old = previous.get(wall.segment_id)
        if old is None:
            continue
        changed = abs(float(wall.bottom_elevation) - float(old.bottom_elevation)) > 1.0e-6
        # A user/API edit of the toe elevation is a project control value.  Mark
        # it as manual and locked unless the caller supplied an explicit source
        # contract (for example the actual-project importer uses "imported").
        if changed and wall.bottom_elevation_source == "unknown":
            wall.source_bottom_elevation = float(old.bottom_elevation)
            wall.bottom_elevation_source = "manual"
            wall.bottom_elevation_locked = True
    project.retaining_system = updated
    ensure_geological_model_covers_excavation(project)
    invalidate_calculation_state(project, reason="retaining system edited", rebuild_cases=True, invalidate_candidates=True)
    repo.save(project)
    return project.retaining_system
