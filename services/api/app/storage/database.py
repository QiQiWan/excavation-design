from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "pitguard.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteProjectStore:
    """SQLite project store with WAL, immutable revisions and audit events."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("PITGUARD_DB_PATH", DEFAULT_DB_PATH))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL
                )
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "revision" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            if "content_hash" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_revisions (
                    project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    revision INTEGER,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_revisions_updated ON project_revisions(project_id, revision DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_project_created ON audit_events(project_id, created_at DESC)")
            conn.commit()

    def upsert(
        self,
        project: dict[str, Any],
        *,
        expected_revision: int | None = None,
        actor: str = "system",
        action: str = "project.save",
        summary: str = "Project snapshot saved",
    ) -> int:
        encoded = _canonical_json(project)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        updated_at = str(project.get("updatedAt") or project.get("updated_at") or _now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT revision, content_hash FROM projects WHERE id = ?", (project["id"],)).fetchone()
            current_revision = int(current["revision"] or 0) if current else 0
            if expected_revision is not None and expected_revision != current_revision:
                conn.rollback()
                raise RuntimeError(f"Project revision conflict: expected {expected_revision}, current {current_revision}")
            if current and str(current["content_hash"] or "") == content_hash:
                conn.rollback()
                return current_revision
            revision = current_revision + 1
            conn.execute(
                """
                INSERT INTO projects (id, name, updated_at, revision, content_hash, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    revision=excluded.revision,
                    content_hash=excluded.content_hash,
                    data=excluded.data
                """,
                (project["id"], project.get("name", "Untitled"), updated_at, revision, content_hash, encoded),
            )
            conn.execute(
                """
                INSERT INTO project_revisions(project_id, revision, updated_at, content_hash, actor, action, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project["id"], revision, updated_at, content_hash, actor, action, encoded),
            )
            conn.execute(
                """
                INSERT INTO audit_events(id, project_id, revision, actor, action, summary, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"audit-{uuid4().hex[:16]}", project["id"], revision, actor, action, summary, "{}", _now()),
            )
            retain = max(10, int(os.getenv("PITGUARD_REVISION_RETENTION", "100")))
            conn.execute(
                """
                DELETE FROM project_revisions
                WHERE project_id = ? AND revision NOT IN (
                    SELECT revision FROM project_revisions WHERE project_id = ? ORDER BY revision DESC LIMIT ?
                )
                """,
                (project["id"], project["id"], retain),
            )
            conn.commit()
            return revision

    def append_audit(
        self,
        project_id: str | None,
        *,
        action: str,
        summary: str,
        actor: str = "system",
        revision: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"audit-{uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events(id, project_id, revision, actor, action, summary, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, project_id, revision, actor, action, summary, json.dumps(metadata or {}, ensure_ascii=False), _now()),
            )
            conn.commit()
        return event_id

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY updated_at DESC").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def list_summaries(self) -> list[dict[str, Any]]:
        query = """
            SELECT
                id, name, updated_at, revision,
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
                rows = conn.execute("SELECT id, name, updated_at, revision, data FROM projects ORDER BY updated_at DESC").fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            data = json.loads(row["data"])
            results = data.get("calculationResults") or []
            latest = results[-1] if results else {}
            summaries.append({
                "id": row["id"], "name": row["name"], "updated_at": row["updated_at"], "revision": row["revision"],
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
        return json.loads(row["data"]) if row else None

    def get_revision_number(self, project_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT revision FROM projects WHERE id = ?", (project_id,)).fetchone()
        return int(row["revision"]) if row else None

    def list_revisions(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, revision, updated_at, content_hash, actor, action FROM project_revisions WHERE project_id = ? ORDER BY revision DESC LIMIT ?",
                (project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_revision(self, project_id: str, revision: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM project_revisions WHERE project_id = ? AND revision = ?", (project_id, revision)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_audit_events(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(str(item.get("metadata") or "{}"))
            result.append(item)
        return result

    def delete(self, project_id: str, *, actor: str = "system") -> bool:
        revision = self.get_revision_number(project_id)
        self.append_audit(project_id, action="project.delete", summary="Project deleted", revision=revision, actor=actor)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.execute("DELETE FROM project_revisions WHERE project_id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0


    def backup(self, destination_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Create an online-consistent SQLite backup and verify its integrity."""
        backup_dir = Path(destination_dir or os.getenv("PITGUARD_BACKUP_DIR", self.db_path.parent / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"pitguard_{stamp}_{uuid4().hex[:8]}.sqlite3"
        with self._connect() as source, sqlite3.connect(destination, timeout=30.0) as target:
            source.execute("PRAGMA wal_checkpoint(PASSIVE)")
            source.backup(target)
            target.commit()
        with sqlite3.connect(destination, timeout=10.0) as check_conn:
            integrity = str(check_conn.execute("PRAGMA integrity_check").fetchone()[0])
            project_count = int(check_conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
            revision_count = int(check_conn.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0])
            audit_count = int(check_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        retention = max(1, int(os.getenv("PITGUARD_BACKUP_RETENTION", "20")))
        backups = sorted(backup_dir.glob("pitguard_*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in backups[retention:]:
            stale.unlink(missing_ok=True)
        return {
            "status": "pass" if integrity.lower() == "ok" else "fail",
            "path": str(destination),
            "filename": destination.name,
            "sizeBytes": destination.stat().st_size,
            "sha256": digest,
            "integrityCheck": integrity,
            "projectCount": project_count,
            "revisionCount": revision_count,
            "auditEventCount": audit_count,
            "retention": retention,
            "createdAt": _now(),
        }

    def list_backups(self, destination_dir: str | os.PathLike[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        backup_dir = Path(destination_dir or os.getenv("PITGUARD_BACKUP_DIR", self.db_path.parent / "backups"))
        if not backup_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(backup_dir.glob("pitguard_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)[:max(1, min(limit, 100))]:
            stat = path.stat()
            rows.append({
                "filename": path.name,
                "path": str(path),
                "sizeBytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return rows

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM project_revisions")
            conn.commit()
