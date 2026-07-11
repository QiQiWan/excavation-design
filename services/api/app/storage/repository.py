from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.schemas.domain import Project, ProjectSummary
from app.storage.database import SQLiteProjectStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    def __init__(self, store: SQLiteProjectStore | None = None) -> None:
        self.store = store or SQLiteProjectStore()

    def create(self, project: Project) -> Project:
        self.save(project)
        return project

    def save(self, project: Project) -> Project:
        project.updated_at = _utc_now()
        self.store.upsert(project.model_dump(mode="json", by_alias=True))
        return project

    def list(self) -> list[Project]:
        return [Project.model_validate(item) for item in self.store.list()]

    def list_summaries(self) -> list[ProjectSummary]:
        return [ProjectSummary.model_validate(item) for item in self.store.list_summaries()]

    def get(self, project_id: str) -> Project | None:
        data = self.store.get(project_id)
        return Project.model_validate(data) if data else None

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def delete(self, project_id: str) -> bool:
        return self.store.delete(project_id)

    def update_partial(self, project_id: str, patch: dict[str, Any]) -> Project:
        project = self.require(project_id)
        data = project.model_dump(mode="json", by_alias=True)
        for key, value in patch.items():
            if value is not None:
                data[key] = value
        updated = Project.model_validate(data)
        return self.save(updated)


def get_repository() -> ProjectRepository:
    return ProjectRepository()
