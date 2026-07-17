from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import os
import time
from collections import OrderedDict
from functools import lru_cache
from threading import Lock

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.schemas.domain import Project, ProjectSummary
from app.storage.database import ProjectPayloadTooLarge, SQLiteProjectStore
from app.storage.artifact_store import ProjectArtifactStore, rehydrate_latest_calculation_evidence

logger = logging.getLogger("pitguard.repository")


_WORKSPACE_CACHE_LOCK = Lock()
_WORKSPACE_CACHE: "OrderedDict[tuple[str, str, int, int], tuple[float, Project]]" = OrderedDict()

def _workspace_cache_limits() -> tuple[int, float, int, int]:
    try:
        maximum = max(1, min(16, int(os.getenv("PITGUARD_WORKSPACE_MODEL_CACHE_SIZE", "4"))))
    except (TypeError, ValueError):
        maximum = 4
    try:
        ttl = max(5.0, min(180.0, float(os.getenv("PITGUARD_WORKSPACE_MODEL_CACHE_TTL_SECONDS", "30"))))
    except (TypeError, ValueError):
        ttl = 30.0
    try:
        item_max = max(1, int(float(os.getenv("PITGUARD_WORKSPACE_CACHE_ITEM_MAX_MB", "24")) * 1048576))
    except (TypeError, ValueError):
        item_max = 24 * 1048576
    try:
        total_max = max(item_max, int(float(os.getenv("PITGUARD_WORKSPACE_CACHE_TOTAL_MAX_MB", "96")) * 1048576))
    except (TypeError, ValueError):
        total_max = 96 * 1048576
    return maximum, ttl, item_max, total_max

def _cached_workspace(db_path: str, project_id: str, revision: int, workspace_bytes: int) -> Project | None:
    key = (db_path, project_id, int(revision), int(workspace_bytes))
    maximum, ttl, _item_max, _total_max = _workspace_cache_limits()
    now = time.monotonic()
    with _WORKSPACE_CACHE_LOCK:
        item = _WORKSPACE_CACHE.get(key)
        if item is None:
            return None
        created, project = item
        if now - created > ttl:
            _WORKSPACE_CACHE.pop(key, None)
            return None
        _WORKSPACE_CACHE.move_to_end(key)
        while len(_WORKSPACE_CACHE) > maximum:
            _WORKSPACE_CACHE.popitem(last=False)
        return project

def _store_workspace_cache(db_path: str, project_id: str, revision: int, workspace_bytes: int, project: Project) -> None:
    key = (db_path, project_id, int(revision), int(workspace_bytes))
    maximum, _ttl, item_max, total_max = _workspace_cache_limits()
    with _WORKSPACE_CACHE_LOCK:
        stale = [item for item in _WORKSPACE_CACHE if item[0] == db_path and item[1] == project_id and item != key]
        for item in stale:
            _WORKSPACE_CACHE.pop(item, None)
        if int(workspace_bytes) > item_max:
            # A 94 MB JSON workspace can occupy several hundred megabytes as a
            # Pydantic graph. Re-reading the compact SQLite projection is safer
            # than pinning it in the long-lived API process.
            return
        _WORKSPACE_CACHE[key] = (time.monotonic(), project)
        _WORKSPACE_CACHE.move_to_end(key)
        while len(_WORKSPACE_CACHE) > maximum or sum(int(item[0][3]) for item in _WORKSPACE_CACHE.items()) > total_max:
            _WORKSPACE_CACHE.popitem(last=False)


def _migrate_loaded_project(project: Project) -> bool:
    """Apply lightweight version migrations and invalidate stale calculations."""
    from app.services.engineering_templates import ensure_design_basis_defaults

    migration = ensure_design_basis_defaults(project)
    changed = bool(migration.get("changedFields"))
    if not project.retaining_system or not project.calculation_results:
        return changed
    from app.calculation.engine import _case_support_audit, _support_topology_hash
    from app.services.calculation_state import invalidate_calculation_state
    from app.version import ALGORITHM_VERSION, RULE_SET_VERSION

    current_hash = _support_topology_hash(project)
    latest = project.calculation_results[-1]
    stored_hash = str(getattr(latest, "support_topology_hash", "") or "")
    iteration = dict(getattr(latest, "design_iteration_summary", {}) or {})
    stored_algorithm = str(iteration.get("algorithmVersion") or "")
    stored_rule_set = str(iteration.get("ruleSetVersion") or "")
    hash_mismatch = stored_hash != current_hash
    case_mismatch = any(_case_support_audit(project, case).get("requiresSynchronization") for case in project.calculation_cases)
    calculation_contract_mismatch = (
        not stored_algorithm
        or stored_algorithm != ALGORITHM_VERSION
        or not stored_rule_set
        or stored_rule_set != RULE_SET_VERSION
    )
    if not hash_mismatch and not case_mismatch and not calculation_contract_mismatch:
        return changed
    reasons: list[str] = []
    if hash_mismatch:
        reasons.append(
            "legacy calculation has no topology hash"
            if not stored_hash
            else "latest calculation topology hash differs from current retaining system"
        )
    if case_mismatch:
        reasons.append("construction stages reference stale support ids or topology")
    if calculation_contract_mismatch:
        reasons.append(
            "calculation algorithm/rule-set contract differs from the current version "
            f"({stored_algorithm or 'missing'} / {stored_rule_set or 'missing'} -> "
            f"{ALGORITHM_VERSION} / {RULE_SET_VERSION})"
        )
    invalidate_calculation_state(project, reason="; ".join(reasons), rebuild_cases=True)
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calculation_result_retention() -> int:
    try:
        return max(1, min(10, int(os.getenv("PITGUARD_CALCULATION_RESULT_RETENTION", "1"))))
    except (TypeError, ValueError):
        return 1


def _compact_calculation_history(project: Project) -> None:
    limit = _calculation_result_retention()
    if len(project.calculation_results) <= limit:
        return
    removed = project.calculation_results[:-limit]
    archive = list(project.advanced_engineering.get("calculationResultArchive", []) or [])
    for item in removed:
        archive.append({
            "id": item.id,
            "createdAt": getattr(item, "calculated_at", None),
            "calculationCaseId": getattr(item, "case_id", None),
            "resultHash": getattr(item, "result_hash", None),
            "supportTopologyHash": getattr(item, "support_topology_hash", None),
            "governingValues": item.governing_values.model_dump(mode="json", by_alias=True) if getattr(item, "governing_values", None) else {},
            "checkSummary": dict(getattr(item, "check_summary", {}) or {}),
        })
    project.advanced_engineering["calculationResultArchive"] = archive[-50:]
    project.calculation_results = project.calculation_results[-limit:]


class ProjectRepository:
    def __init__(self, store: SQLiteProjectStore | None = None, *, default_actor: str = "system") -> None:
        self.store = store or SQLiteProjectStore()
        self.default_actor = default_actor or "system"

    def create(self, project: Project, *, actor: str | None = None) -> Project:
        self.save(project, actor=actor, action="project.create", summary="Project created")
        return project

    def save(
        self,
        project: Project,
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
        action: str = "project.save",
        summary: str = "Project snapshot saved",
    ) -> Project:
        project.updated_at = _utc_now()
        _compact_calculation_history(project)
        try:
            revision = self.store.upsert(
                project.model_dump(mode="json", by_alias=True),
                expected_revision=expected_revision, actor=actor or self.default_actor, action=action, summary=summary,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project

    def list(self) -> list[Project]:
        projects: list[Project] = []
        for item in self.store.list():
            project = Project.model_validate(item)
            if _migrate_loaded_project(project):
                self.store.upsert(project.model_dump(mode="json", by_alias=True))
            projects.append(project)
        return projects

    def list_summaries(self) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for item in self.store.list_summaries():
            try:
                summaries.append(ProjectSummary.model_validate(item))
            except ValidationError as exc:
                # The project list is the application's initial route.  One
                # legacy or partially migrated summary must not take the whole
                # installation offline.  Preserve the project identity and
                # expose a conservative storage state while logging the exact
                # schema defect for maintenance.
                logger.warning("Recovering invalid project summary %s: %s", item.get("id"), exc)
                fallback = {
                    "id": str(item.get("id") or "unknown-project"),
                    "revision": int(item.get("revision") or 0),
                    "name": str(item.get("name") or "未命名项目"),
                    "location": item.get("location"),
                    "created_at": item.get("created_at") or item.get("createdAt"),
                    "updated_at": str(item.get("updated_at") or item.get("updatedAt") or _utc_now()),
                    "payload_bytes": int(item.get("payload_bytes") or item.get("payloadBytes") or 0),
                    "workspace_bytes": int(item.get("workspace_bytes") or item.get("workspaceBytes") or 0),
                    "external_bytes": int(item.get("external_bytes") or item.get("externalBytes") or 0),
                    "artifact_count": int(item.get("artifact_count") or item.get("artifactCount") or 0),
                    "storage_status": "elevated",
                }
                summaries.append(ProjectSummary.model_validate(fallback))
        return summaries

    def get(self, project_id: str) -> Project | None:
        try:
            data = self.store.get(project_id)
        except ProjectPayloadTooLarge as exc:
            storage = self.store.get_payload_info(exc.project_id) or {}
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "PROJECT_FULL_LOAD_BLOCKED",
                    "message": str(exc),
                    "projectId": exc.project_id,
                    "payloadBytes": exc.payload_bytes,
                    "limitBytes": exc.limit_bytes,
                    "workspaceBytes": storage.get("workspaceBytes"),
                    "workspaceLoadAllowed": storage.get("workspaceLoadAllowed", True),
                    "compactionRecommended": storage.get("compactionRecommended", False),
                    "resourcePolicy": storage.get("resourcePolicy", {}),
                    "recommendation": "Use the workspace profile; execute full calculations and exports in the isolated worker.",
                },
            ) from exc
        if not data:
            return None
        project = Project.model_validate(data)
        # Loading a project is now side-effect free.  Legacy migration and
        # history compaction are performed by explicit maintenance/worker
        # operations, avoiding a read -> multi-hundred-MB write amplification.
        if str(os.getenv("PITGUARD_MIGRATE_ON_FULL_LOAD", "0")).strip().lower() in {"1", "true", "yes"}:
            _migrate_loaded_project(project)
            _compact_calculation_history(project)
        return project

    def get_workspace(self, project_id: str) -> Project | None:
        metadata = self.store.get_workspace_metadata(project_id)
        if metadata is None:
            return None
        db_key = str(self.store.db_path)
        revision = int(metadata.get("revision") or 0)
        workspace_bytes = int(metadata.get("workspaceBytes") or 0)
        cached = _cached_workspace(db_key, project_id, revision, workspace_bytes)
        if cached is not None:
            return cached
        data = self.store.get_workspace(project_id)
        if not data:
            return None
        project = Project.model_validate(data)
        _store_workspace_cache(db_key, project_id, revision, workspace_bytes, project)
        return project

    def require_workspace(self, project_id: str) -> Project:
        project = self.get_workspace(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def get_workspace_with_latest_calculation(self, project_id: str) -> Project | None:
        """Return the bounded workspace plus authoritative latest stage evidence.

        Only calculation stage chunks and the latest result detail bundle are
        loaded.  Heavy geology, candidate, monitoring and manufacturing
        artifacts remain external, keeping interactive engineering gates both
        truthful and bounded.
        """
        data = self.store.get_workspace(project_id)
        if not data:
            return None
        hydrated = rehydrate_latest_calculation_evidence(data, ProjectArtifactStore())
        return Project.model_validate(hydrated)

    def require_workspace_with_latest_calculation(self, project_id: str) -> Project:
        project = self.get_workspace_with_latest_calculation(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def get_with_latest_calculation(self, project_id: str) -> Project | None:
        """Load the canonical project snapshot plus the latest stage evidence.

        Mutation routes must not save a downsampled workspace projection, since
        that could replace canonical geology/candidate data with UI previews.
        This path starts from ``projects.data`` and hydrates only the latest
        calculation when the API process has not already loaded all artifacts.
        """
        data = self.store.get(project_id)
        if not data:
            return None
        hydrated = rehydrate_latest_calculation_evidence(data, ProjectArtifactStore())
        return Project.model_validate(hydrated)

    def require_with_latest_calculation(self, project_id: str) -> Project:
        project = self.get_with_latest_calculation(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def delete(self, project_id: str, *, actor: str | None = None) -> bool:
        return self.store.delete(project_id, actor=actor or self.default_actor)

    def revision(self, project_id: str) -> int | None:
        return self.store.get_revision_number(project_id)

    def revisions(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if self.store.get_revision_number(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return self.store.list_revisions(project_id, limit=limit)

    def audit_events(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if self.store.get_revision_number(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return self.store.list_audit_events(project_id, limit=limit)

    def restore_revision(self, project_id: str, revision: int, *, actor: str | None = None) -> Project:
        self.require(project_id)
        snapshot = self.store.get_revision(project_id, revision)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"Project revision not found: {project_id}@{revision}")
        project = Project.model_validate(snapshot)
        project.calculation_results = []
        project.advanced_engineering["requiresRecalculation"] = True
        project.advanced_engineering["restoredFromRevision"] = revision
        project.messages.append(f"已恢复项目版本 R{revision}；为防止旧结果误用，计算结果已失效并要求重新计算。")
        return self.save(project, actor=actor or self.default_actor, action="project.restore_revision", summary=f"Restored revision R{revision}")

    def update_partial(self, project_id: str, patch: dict[str, Any]) -> Project:
        project = self.require(project_id)
        data = project.model_dump(mode="json", by_alias=True)
        for key, value in patch.items():
            if value is not None:
                data[key] = value
        updated = Project.model_validate(data)
        return self.save(updated)


@lru_cache(maxsize=8)
def shared_project_store(db_path: str | None = None) -> SQLiteProjectStore:
    """Reuse one schema-initialized store for each configured database path.

    Connections remain short-lived and thread-safe; only immutable store
    configuration is shared. The path key also keeps tests and maintenance
    commands isolated when they temporarily override PITGUARD_DB_PATH.
    """
    return SQLiteProjectStore(db_path)


def get_repository(request: Request) -> ProjectRepository:
    identity = getattr(request.state, "pitguard_identity", None)
    actor = str(getattr(identity, "actor", None) or "system")
    db_path = os.getenv("PITGUARD_DB_PATH") or None
    return ProjectRepository(store=shared_project_store(db_path), default_actor=actor)
