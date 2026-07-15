from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import os
from functools import lru_cache

from fastapi import HTTPException, Request

from app.schemas.domain import Project, ProjectSummary
from app.storage.database import ProjectPayloadTooLarge, SQLiteProjectStore




def _migrate_loaded_project(project: Project) -> bool:
    """Invalidate legacy/stale results when stored topology and stages diverge.

    V3.14 projects can contain A/B/C cards and a latest calculation produced for
    an earlier support topology.  Hydration now performs a lightweight topology
    audit so the UI never presents those values as current design evidence.
    """
    if not project.retaining_system or not project.calculation_results:
        return False
    from app.calculation.engine import _case_support_audit, _support_topology_hash
    from app.services.calculation_state import invalidate_calculation_state

    from app.version import ALGORITHM_VERSION, RULE_SET_VERSION

    current_hash = _support_topology_hash(project)
    latest = project.calculation_results[-1]
    stored_hash = str(getattr(latest, "support_topology_hash", "") or "")
    iteration = dict(getattr(latest, "design_iteration_summary", {}) or {})
    stored_algorithm = str(iteration.get("algorithmVersion") or "")
    stored_rule_set = str(iteration.get("ruleSetVersion") or "")
    # Results created before V3.15 do not carry a topology hash.  They cannot be
    # proven to match the current support system, so migrate them to the audit
    # archive and require one fresh calculation instead of displaying them as
    # current engineering evidence.
    hash_mismatch = stored_hash != current_hash
    case_mismatch = any(_case_support_audit(project, case).get("requiresSynchronization") for case in project.calculation_cases)
    calculation_contract_mismatch = (
        not stored_algorithm
        or stored_algorithm != ALGORITHM_VERSION
        or not stored_rule_set
        or stored_rule_set != RULE_SET_VERSION
    )
    if not hash_mismatch and not case_mismatch and not calculation_contract_mismatch:
        return False
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
        return [ProjectSummary.model_validate(item) for item in self.store.list_summaries()]

    def get(self, project_id: str) -> Project | None:
        try:
            data = self.store.get(project_id)
        except ProjectPayloadTooLarge as exc:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "PROJECT_FULL_LOAD_BLOCKED",
                    "message": str(exc),
                    "projectId": exc.project_id,
                    "payloadBytes": exc.payload_bytes,
                    "limitBytes": exc.limit_bytes,
                    "recommendation": "Use the workspace profile, run the isolated worker, or compact legacy project storage.",
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
        data = self.store.get_workspace(project_id)
        if not data:
            return None
        return Project.model_validate(data)

    def require_workspace(self, project_id: str) -> Project:
        project = self.get_workspace(project_id)
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
