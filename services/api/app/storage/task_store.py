from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage.database import DEFAULT_DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteTaskStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("PITGUARD_DB_PATH", DEFAULT_DB_PATH))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
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
            connection.execute("CREATE INDEX IF NOT EXISTS idx_task_records_status_updated ON task_records(status, updated_at ASC)")
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
                (task["id"], task["projectId"], task["operation"], task["status"], task["updatedAt"], json.dumps(task, ensure_ascii=False, separators=(",", ":"))),
            )
            connection.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT data FROM task_records WHERE id = ?", (task_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self, project_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if project_id:
                rows = connection.execute("SELECT data FROM task_records WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?", (project_id, limit)).fetchall()
            else:
                rows = connection.execute("SELECT data FROM task_records ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued task for the external worker."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, data FROM task_records WHERE status = 'queued' ORDER BY updated_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            task = json.loads(row["data"])
            now = _now()
            task["status"] = "running"
            task["currentStep"] = "外部计算工作进程已领取任务"
            task["updatedAt"] = now
            task["heartbeatAt"] = now
            logs = list(task.get("logs") or [])
            logs.append(f"[{now}] 外部计算工作进程已原子领取任务。")
            task["logs"] = logs[-500:]
            cursor = connection.execute(
                "UPDATE task_records SET status='running', updated_at=?, data=? WHERE id=? AND status='queued'",
                (now, json.dumps(task, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )
            connection.commit()
            return task if cursor.rowcount == 1 else None

    def mark_running_interrupted(self, reason: str = "External worker restarted") -> int:
        now = _now()
        count = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT id, data FROM task_records WHERE status='running'").fetchall()
            for row in rows:
                task = json.loads(row["data"])
                task["status"] = "interrupted"
                task["currentStep"] = "工作进程重启导致任务中断，可重新提交"
                task["error"] = task.get("error") or reason
                task["updatedAt"] = now
                task["finishedAt"] = now
                logs = list(task.get("logs") or [])
                logs.append(f"[{now}] {reason}")
                task["logs"] = logs[-500:]
                connection.execute(
                    "UPDATE task_records SET status='interrupted', updated_at=?, data=? WHERE id=?",
                    (now, json.dumps(task, ensure_ascii=False, separators=(",", ":")), row["id"]),
                )
                count += 1
            connection.commit()
        return count

    def delete_by_project(self, project_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM task_records WHERE project_id = ?", (project_id,))
            connection.commit()
            return int(cursor.rowcount or 0)
