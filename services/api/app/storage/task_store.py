from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.storage.database import DEFAULT_DB_PATH


class SQLiteTaskStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("PITGUARD_DB_PATH", DEFAULT_DB_PATH))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_records (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_task_records_project_updated ON task_records(project_id, updated_at DESC)")
            connection.commit()

    def upsert(self, task: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_records (id, project_id, operation, status, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id=excluded.project_id,
                    operation=excluded.operation,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (task["id"], task["projectId"], task["operation"], task["status"], task["updatedAt"], json.dumps(task, ensure_ascii=False)),
            )
            connection.commit()

    def list(self, project_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if project_id:
                rows = connection.execute("SELECT data FROM task_records WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?", (project_id, limit)).fetchall()
            else:
                rows = connection.execute("SELECT data FROM task_records ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["data"]) for row in rows]
