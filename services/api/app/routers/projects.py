from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    from app.services.engineering_templates import enforce_safety_target_floors

    enforce_safety_target_floors(project, actor="project.create")
    return repo.create(project)


@router.get("", response_model=list[ProjectSummary])
def list_projects(repo: ProjectRepository = Depends(get_repository)) -> list[ProjectSummary]:
    return repo.list_summaries()


@router.get("/{project_id}")
def get_project(
    project_id: str,
    profile: Literal["workspace", "full"] = Query(default="workspace"),
    result_history_limit: int = 1,
    repo: ProjectRepository = Depends(get_repository),
) -> Response:
    if profile == "workspace":
        stored = repo.store.get_workspace_json(project_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        payload, metadata = stored
        return Response(
            content=payload,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-PitGuard-Project-Profile": "workspace",
                "X-PitGuard-Project-Revision": str(metadata.get("revision", 0)),
                "X-PitGuard-Full-Payload-Bytes": str(metadata.get("payloadBytes", 0)),
                "X-PitGuard-Workspace-Payload-Bytes": str(metadata.get("workspaceBytes", 0)),
            },
        )
    project = repo.require(project_id)
    limit = max(0, min(int(result_history_limit), 20))
    if limit == 0:
        project.calculation_results = []
    elif len(project.calculation_results) > limit:
        project.calculation_results = project.calculation_results[-limit:]
    # Full profile is intentionally explicit and protected by the repository
    # payload limit.  It should be used by isolated workers, not project open.
    return Response(
        content=project.model_dump_json(by_alias=True),
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-PitGuard-Project-Profile": "full"},
    )


@router.get("/{project_id}/storage-health")
def get_project_storage_health(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    info = repo.store.get_payload_info(project_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    payload_mb = float(info["payloadBytes"]) / 1048576.0
    workspace_mb = float(info["workspaceBytes"]) / 1048576.0
    limit_mb = float(info.get("apiFullLoadLimitBytes") or 0) / 1048576.0
    workspace_limit_mb = float(info.get("workspaceLimitBytes") or 0) / 1048576.0
    info["status"] = str(info.get("storageStatus") or "normal")
    info["message"] = (
        f"完整快照 {payload_mb:.1f} MB；当前动态全量加载预算 {limit_mb:.1f} MB；"
        f"网页工作区 {workspace_mb:.1f}/{workspace_limit_mb:.1f} MB。"
        "完整快照大小不再直接阻断网页设计，重型操作由独立worker按资源策略执行。"
    )
    return info




@router.get("/{project_id}/geometry-consistency")
def get_geometry_consistency(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return geometry_consistency_summary(repo.require_workspace(project_id))


@router.get("/{project_id}/dashboard")
def get_project_dashboard(project_id: str, mode: str = "balanced", repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_project_dashboard(repo.require_workspace(project_id), mode=mode)


@router.get("/{project_id}/design-scheme-ledger")
def get_design_scheme_ledger(project_id: str, mode: str = "balanced", repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_design_scheme_ledger(repo.require(project_id), mode=mode)


DESIGN_AFFECTING_KEYS = {
    "unitSystem", "coordinateSystem", "designSettings", "boreholes", "strata",
    "geologicalModel", "excavation", "retainingSystem", "calculationCases",
    "designControlStages", "designScenarios",
}


def _build_updated_project(
    project: Project,
    payload: dict[str, Any],
    *,
    actor: str,
) -> tuple[Project, list[str]]:
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
    from app.services.engineering_templates import enforce_safety_target_floors

    enforcement = enforce_safety_target_floors(updated, actor=actor)
    if enforcement.get("adjusted"):
        updated.messages.append("低于项目安全底线的目标值已由服务端提升，并记录于安全目标审计。")
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
        candidate_affecting_keys = {"unitSystem", "coordinateSystem", "designSettings", "excavation", "retainingSystem"}
        if candidate_affecting_keys.intersection(changed_design_keys):
            from app.services.support_candidate_contract import archive_and_clear_stale_candidates

            archive_and_clear_stale_candidates(
                updated,
                reason=f"project design input changed: {', '.join(changed_design_keys)}",
            )
        message = "设计输入已变更，原计算结果与正式发行状态已失效，请重新建立工况并计算。"
        if not updated.messages or updated.messages[-1] != message:
            updated.messages.append(message)
    return updated, changed_design_keys


def _apply_project_patch(
    project_id: str,
    payload: dict[str, Any],
    repo: ProjectRepository,
    *,
    expected_revision: int | None,
    actor: str,
) -> Project:
    project = repo.require(project_id)
    updated, changed_design_keys = _build_updated_project(project, payload, actor=actor)
    return repo.save(
        updated,
        expected_revision=expected_revision,
        actor=actor,
        action="project.update",
        summary="Project updated" + (f"; invalidated: {', '.join(changed_design_keys)}" if changed_design_keys else ""),
    )


@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    payload: dict[str, Any],
    repo: ProjectRepository = Depends(get_repository),
    expected_revision: int | None = Query(default=None, alias="expectedRevision", ge=0),
    actor: str = Query(default="system", min_length=1, max_length=80),
) -> Project:
    # Preserve direct service-level calls used by engineering regression tests.
    if not isinstance(expected_revision, (int, type(None))):
        expected_revision = None
    if not isinstance(actor, str):
        actor = "system"
    try:
        return _apply_project_patch(project_id, payload, repo, expected_revision=expected_revision, actor=actor)
    except TypeError:
        # Compatibility repositories used by embedded deployments may expose a
        # minimal ``save(project)`` signature. They must still receive the same
        # invalidation and safety-floor enforcement as the primary repository.
        project = repo.require(project_id)
        updated, _changed_design_keys = _build_updated_project(project, payload, actor=actor)
        return repo.save(updated)


@router.patch("/{project_id}/workspace")
def update_project_workspace(
    project_id: str,
    payload: dict[str, Any],
    repo: ProjectRepository = Depends(get_repository),
    expected_revision: int | None = Query(default=None, alias="expectedRevision", ge=0),
    actor: str = Query(default="web-user", min_length=1, max_length=80),
) -> Response:
    """Persist a partial edit and return the bounded workspace JSON directly.

    This avoids a second GET, response-model reconstruction and duplicate JSON
    serialization for ordinary editor saves.
    """
    _apply_project_patch(project_id, payload, repo, expected_revision=expected_revision, actor=actor)
    stored = repo.store.get_workspace_json(project_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    body, metadata = stored
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "X-PitGuard-Project-Profile": "workspace",
            "X-PitGuard-Project-Revision": str(metadata.get("revision", 0)),
        },
    )


@router.get("/{project_id}/storage-revision")
def get_storage_revision(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    revision = repo.revision(project_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"projectId": project_id, "revision": revision}


@router.get("/{project_id}/storage-revisions")
def list_storage_revisions(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    repo: ProjectRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return repo.revisions(project_id, limit=limit)


@router.get("/{project_id}/audit-events")
def list_project_audit_events(
    project_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    repo: ProjectRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return repo.audit_events(project_id, limit=limit)


@router.post("/{project_id}/storage-revisions/{revision}/restore", response_model=Project)
def restore_storage_revision(
    project_id: str,
    revision: int,
    actor: str = Query(default="system", min_length=1, max_length=80),
    repo: ProjectRepository = Depends(get_repository),
) -> Project:
    return repo.restore_revision(project_id, revision, actor=actor)


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
