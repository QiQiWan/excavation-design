from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "pitguard.sqlite3"


class SQLiteProjectStore:
    """Small SQLite-backed JSON document store for local-first MVP projects."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("PITGUARD_DB_PATH", DEFAULT_DB_PATH))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def upsert(self, project: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, updated_at, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    project["id"],
                    project.get("name", "Untitled"),
                    project.get("updatedAt") or project.get("updated_at"),
                    json.dumps(project, ensure_ascii=False),
                ),
            )
            conn.commit()

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY updated_at DESC").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def list_summaries(self) -> list[dict[str, Any]]:
        """Return compact project metadata without hydrating multi-megabyte results."""
        query = """
            SELECT
                id, name, updated_at,
                json_extract(data, '$.location') AS location,
                json_extract(data, '$.createdAt') AS created_at,
                CASE WHEN json_type(data, '$.excavation') IS NOT NULL AND json_type(data, '$.excavation') != 'null' THEN 1 ELSE 0 END AS has_excavation,
                CASE WHEN json_type(data, '$.retainingSystem') IS NOT NULL AND json_type(data, '$.retainingSystem') != 'null' THEN 1 ELSE 0 END AS has_retaining_system,
                COALESCE(json_array_length(json_extract(data, '$.calculationCases')), 0) AS calculation_case_count,
                COALESCE(json_array_length(json_extract(data, '$.calculationResults')), 0) AS calculation_result_count,
                json_extract(data, '$.calculationResults[#-1].id') AS latest_calculation_id,
                json_extract(data, '$.calculationResults[#-1].governingValues.governingCheckStatus') AS governing_status,
                json_extract(data, '$.calculationResults[#-1].reportDiagramData.geometryConsistency.consistent') AS geometry_consistent
            FROM projects
            ORDER BY updated_at DESC
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(query).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                rows = conn.execute("SELECT id, name, updated_at, data FROM projects ORDER BY updated_at DESC").fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            data = json.loads(row["data"])
            results = data.get("calculationResults") or []
            latest = results[-1] if results else {}
            summaries.append({
                "id": row["id"], "name": row["name"], "updated_at": row["updated_at"],
                "location": data.get("location"), "created_at": data.get("createdAt"),
                "has_excavation": bool(data.get("excavation")),
                "has_retaining_system": bool(data.get("retainingSystem")),
                "calculation_case_count": len(data.get("calculationCases") or []),
                "calculation_result_count": len(results),
                "latest_calculation_id": latest.get("id"),
                "governing_status": (latest.get("governingValues") or {}).get("governingCheckStatus"),
                "geometry_consistent": ((latest.get("reportDiagramData") or {}).get("geometryConsistency") or {}).get("consistent"),
            })
        return summaries

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def delete(self, project_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects")
            conn.commit()
