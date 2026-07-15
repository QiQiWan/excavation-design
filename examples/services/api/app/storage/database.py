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


class ProjectPayloadTooLarge(RuntimeError):
    """Raised before a full project JSON document is copied into the API heap."""

    def __init__(self, project_id: str, payload_bytes: int, limit_bytes: int) -> None:
        self.project_id = project_id
        self.payload_bytes = int(payload_bytes)
        self.limit_bytes = int(limit_bytes)
        super().__init__(
            f"Project {project_id} payload is {payload_bytes / 1048576:.1f} MB; "
            f"the API full-load limit is {limit_bytes / 1048576:.1f} MB. "
            "Open the workspace profile or run storage compaction before a full load."
        )


def _process_role() -> str:
    return str(os.getenv("PITGUARD_PROCESS_ROLE", "api")).strip().lower() or "api"


def _api_full_load_limit_bytes() -> int:
    try:
        value = float(os.getenv("PITGUARD_API_FULL_PROJECT_LIMIT_MB", "96"))
    except (TypeError, ValueError):
        value = 96.0
    return max(16, min(2048, int(value))) * 1024 * 1024


def _compact_result_for_workspace(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep = {
        "id", "projectId", "caseId", "supportTopologyHash", "inputSnapshotHash",
        "adoptedDesignSnapshotHash", "calculationContractId", "resultHash",
        "calculationAssurance", "deliveryReadiness", "governingValues", "warnings",
        "checkSummary", "designIterationSummary", "designReviewSummary",
        "supportLayoutQuality", "ifcCompatibility", "formalReportGate", "standards",
        "professionalReviewRequired", "calculatedAt",
    }
    compact = {key: value[key] for key in keep if key in value}
    # Preserve the schema while excluding stage arrays, diagrams and duplicated
    # candidate calculations from the project-opening payload.
    compact.setdefault("stageResults", [])
    compact.setdefault("checks", [])
    compact.setdefault("optimizationActions", [])
    compact.setdefault("reportDiagramData", {})
    compact.setdefault("drawingSheets", [])
    compact.setdefault("supportLayoutRepair", None)
    compact.setdefault("stabilityDetailedResult", None)
    return compact


def _downsample_surface_grid(grid: Any, max_axis: int = 64) -> Any:
    if not isinstance(grid, dict):
        return grid
    xs = list(grid.get("xValues") or [])
    ys = list(grid.get("yValues") or [])
    zs = list(grid.get("zValues") or [])
    if len(xs) <= max_axis and len(ys) <= max_axis:
        return grid
    x_step = max(1, (len(xs) + max_axis - 1) // max_axis)
    y_step = max(1, (len(ys) + max_axis - 1) // max_axis)
    x_idx = list(range(0, len(xs), x_step))
    y_idx = list(range(0, len(ys), y_step))
    if xs and x_idx[-1] != len(xs) - 1:
        x_idx.append(len(xs) - 1)
    if ys and y_idx[-1] != len(ys) - 1:
        y_idx.append(len(ys) - 1)
    sampled_rows: list[list[Any]] = []
    for yi in y_idx:
        row = zs[yi] if yi < len(zs) and isinstance(zs[yi], list) else []
        sampled_rows.append([row[xi] if xi < len(row) else None for xi in x_idx])
    return {
        **grid,
        "xValues": [xs[i] for i in x_idx],
        "yValues": [ys[i] for i in y_idx],
        "zValues": sampled_rows,
        "workspaceDownsampled": True,
        "sourceShape": [len(ys), len(xs)],
    }


def _workspace_limit_bytes() -> int:
    try:
        value = float(os.getenv("PITGUARD_WORKSPACE_PAYLOAD_LIMIT_MB", "24"))
    except (TypeError, ValueError):
        value = 24.0
    return max(4, min(256, int(value))) * 1024 * 1024


def _aggressively_compact_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    bounded = dict(workspace)
    geological = bounded.get("geologicalModel")
    if isinstance(geological, dict):
        geo = dict(geological)
        geo["surfaces"] = []
        geo["volumes"] = []
        geo["vtuMesh"] = None
        warnings = list(geo.get("warnings") or [])
        warnings.append("工作区采用轻量地质摘要；进入地质页后按需读取完整模型。")
        geo["warnings"] = warnings
        bounded["geologicalModel"] = geo
    excavation = bounded.get("excavation")
    if isinstance(excavation, dict):
        exc = dict(excavation)
        exc["drawingLayers"] = []
        bounded["excavation"] = exc
    retaining = bounded.get("retainingSystem")
    if isinstance(retaining, dict):
        ret = dict(retaining)
        repair = ret.get("supportLayoutRepair")
        if isinstance(repair, dict):
            rep = dict(repair)
            candidates = []
            for candidate in list(rep.get("candidates") or [])[:8]:
                if not isinstance(candidate, dict):
                    continue
                item = dict(candidate)
                item["planGeometry"] = {}
                item["deltaGeometry"] = {}
                item["lineAdjustments"] = list(item.get("lineAdjustments") or [])[:32]
                item["fullCalculation"] = {}
                candidates.append(item)
            rep["candidates"] = candidates
            rep["candidateFullCalculations"] = []
            ret["supportLayoutRepair"] = rep
        bounded["retainingSystem"] = ret
    bounded["monitoringRecords"] = []
    advanced = dict(bounded.get("advancedEngineering") or {})
    workspace_meta = dict(advanced.get("workspaceStorage") or {})
    workspace_meta["aggressivelyCompacted"] = True
    workspace_meta["workspaceLimitBytes"] = _workspace_limit_bytes()
    bounded["advancedEngineering"] = {
        key: value for key, value in advanced.items()
        if key in {
            "calculationState", "requiresRecalculation", "invalidationReason",
            "wallLengthOptimization", "supportDesignerAudit", "planShapeDiagnostics",
            "industrialReadiness", "workspaceStorage",
        }
    }
    bounded["advancedEngineering"]["workspaceStorage"] = workspace_meta
    return bounded


def _compact_project_for_workspace(project: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded project payload for opening the web workspace.

    The full engineering snapshot remains in ``projects.data`` and immutable
    revisions.  This projection deliberately excludes result matrices, raw VTU
    meshes, repeated candidate calculations and detailed manufacturing caches.
    """
    workspace = dict(project)
    results = list(project.get("calculationResults") or [])
    workspace["calculationResults"] = [_compact_result_for_workspace(results[-1])] if results else []
    workspace["messages"] = list(project.get("messages") or [])[-100:]
    workspace["monitoringRecords"] = list(project.get("monitoringRecords") or [])[-500:]
    workspace["calibrationRuns"] = list(project.get("calibrationRuns") or [])[-20:]
    workspace["drawingRevisions"] = list(project.get("drawingRevisions") or [])[-50:]

    geological = project.get("geologicalModel")
    if isinstance(geological, dict):
        compact_geo = dict(geological)
        compact_geo["vtuMesh"] = None
        surfaces = []
        for surface in list(geological.get("surfaces") or [])[:64]:
            if not isinstance(surface, dict):
                continue
            item = dict(surface)
            item["grid"] = _downsample_surface_grid(surface.get("grid"))
            surfaces.append(item)
        compact_geo["surfaces"] = surfaces
        compact_geo["volumes"] = list(geological.get("volumes") or [])[:128]
        workspace["geologicalModel"] = compact_geo

    retaining = project.get("retainingSystem")
    if isinstance(retaining, dict):
        compact_retaining = dict(retaining)
        repair = retaining.get("supportLayoutRepair")
        if isinstance(repair, dict):
            compact_repair = dict(repair)
            candidates = []
            for candidate in list(repair.get("candidates") or [])[:12]:
                if not isinstance(candidate, dict):
                    continue
                item = dict(candidate)
                item["fullCalculation"] = {}
                candidates.append(item)
            compact_repair["candidates"] = candidates
            compact_repair["candidateFullCalculations"] = []
            compact_retaining["supportLayoutRepair"] = compact_repair
        rebar_scheme = compact_retaining.get("rebarDesignScheme")
        if isinstance(rebar_scheme, dict):
            rebar_compact = dict(rebar_scheme)
            for key in ("bars", "barInstances", "fullGeometry", "manufacturingRows", "bbsRows"):
                if key in rebar_compact:
                    rebar_compact[key] = []
            compact_retaining["rebarDesignScheme"] = rebar_compact
        workspace["retainingSystem"] = compact_retaining

    advanced = dict(project.get("advancedEngineering") or {})
    omitted: list[str] = []
    for key in (
        "latestSuite", "industrialDetailing", "qualificationSuite",
        "detailGeometryPatches", "fullRebarGeometry", "manufacturingData",
        "renderCache", "ifcEntityCache", "calculationResultArchive",
    ):
        if key in advanced:
            advanced.pop(key, None)
            omitted.append(f"advancedEngineering.{key}")
    advanced["workspaceStorage"] = {
        "profile": "workspace",
        "fullCalculationResultCount": len(results),
        "omittedPaths": omitted,
    }
    workspace["advancedEngineering"] = advanced
    return workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_summary_payload(project: dict[str, Any], revision: int = 0) -> dict[str, Any]:
    results = list(project.get("calculationResults") or [])
    latest = results[-1] if results else {}
    return {
        "id": project.get("id"),
        "revision": revision,
        "name": project.get("name", "Untitled"),
        "location": project.get("location"),
        "createdAt": project.get("createdAt") or project.get("created_at"),
        "updatedAt": project.get("updatedAt") or project.get("updated_at"),
        "hasExcavation": bool(project.get("excavation")),
        "hasRetainingSystem": bool(project.get("retainingSystem")),
        "calculationCaseCount": len(project.get("calculationCases") or []),
        "calculationResultCount": len(results),
        "latestCalculationId": latest.get("id"),
        "governingStatus": (latest.get("governingValues") or {}).get("governingCheckStatus"),
        "geometryConsistent": ((latest.get("reportDiagramData") or {}).get("geometryConsistency") or {}).get("consistent"),
    }


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
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("PRAGMA cache_size=-32768")
        conn.execute("PRAGMA mmap_size=0")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA journal_size_limit=67108864")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '{}',
                    workspace_data TEXT NOT NULL DEFAULT '{}',
                    payload_bytes INTEGER NOT NULL DEFAULT 0,
                    workspace_bytes INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                )
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "revision" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            if "content_hash" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            if "summary" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN summary TEXT NOT NULL DEFAULT '{}'")
            if "workspace_data" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN workspace_data TEXT NOT NULL DEFAULT '{}'")
            if "payload_bytes" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN payload_bytes INTEGER NOT NULL DEFAULT 0")
            if "workspace_bytes" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN workspace_bytes INTEGER NOT NULL DEFAULT 0")
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
            self._backfill_workspace_columns(conn)
            conn.commit()

    def _backfill_workspace_columns(self, conn: sqlite3.Connection) -> None:
        """Create safe workspace projections for databases produced by older releases.

        The transformation is executed by SQLite JSON1 so a legacy multi-hundred
        megabyte document is never copied into the API Python heap during startup.
        The latest full result is intentionally omitted for the initial backfill;
        the next normal save writes a compact current-result summary.
        """
        conn.execute(
            "UPDATE projects SET payload_bytes = length(CAST(data AS BLOB)) "
            "WHERE payload_bytes <= 0"
        )
        try:
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    json_remove(
                        data,
                        '$.calculationResults',
                        '$.geologicalModel.vtuMesh',
                        '$.geologicalModel.surfaces',
                        '$.geologicalModel.volumes',
                        '$.retainingSystem.supportLayoutRepair.candidateFullCalculations',
                        '$.retainingSystem.rebarDesignScheme.bars',
                        '$.retainingSystem.rebarDesignScheme.barInstances',
                        '$.retainingSystem.rebarDesignScheme.fullGeometry',
                        '$.advancedEngineering.latestSuite',
                        '$.advancedEngineering.industrialDetailing',
                        '$.advancedEngineering.qualificationSuite',
                        '$.advancedEngineering.detailGeometryPatches',
                        '$.advancedEngineering.fullRebarGeometry',
                        '$.advancedEngineering.manufacturingData',
                        '$.advancedEngineering.renderCache',
                        '$.advancedEngineering.ifcEntityCache',
                        '$.advancedEngineering.calculationResultArchive',
                        '$.monitoringRecords'
                    ),
                    '$.calculationResults', json('[]'),
                    '$.geologicalModel.surfaces', json('[]'),
                    '$.geologicalModel.volumes', json('[]'),
                    '$.monitoringRecords', json('[]'),
                    '$.advancedEngineering.workspaceStorage',
                    json_object('profile', 'workspace', 'legacyBackfill', 1)
                )
                WHERE (workspace_data IS NULL OR workspace_data = '' OR workspace_data = '{}')
                  AND json_valid(data)
                """
            )
            # Remove repeated full candidate calculations from each candidate.
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    workspace_data,
                    '$.retainingSystem.supportLayoutRepair.candidates',
                    COALESCE((
                        SELECT json_group_array(json(json_remove(value, '$.fullCalculation')))
                        FROM json_each(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates')
                    ), json('[]'))
                )
                WHERE json_type(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') = 'array'
                  AND json_array_length(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') > 0
                """
            )
        except sqlite3.OperationalError:
            # JSON1 is built into supported Python/SQLite releases.  Retaining a
            # minimal payload still keeps the service alive on unusual builds.
            conn.execute(
                "UPDATE projects SET workspace_data = json_object("
                "'id', id, 'name', name, 'updatedAt', updated_at, "
                "'advancedEngineering', json_object('workspaceStorage', json_object('profile','minimal'))) "
                "WHERE workspace_data IS NULL OR workspace_data = '' OR workspace_data = '{}'"
            )
        conn.execute(
            "UPDATE projects SET workspace_bytes = length(CAST(workspace_data AS BLOB)) "
            "WHERE workspace_bytes <= 0 OR workspace_bytes IS NULL"
        )
        # If a legacy candidate preview is still unusually large, remove only
        # preview/delta geometry.  Engineering metrics and the complete snapshot
        # remain available in projects.data for the isolated worker.
        try:
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    workspace_data,
                    '$.retainingSystem.supportLayoutRepair.candidates',
                    COALESCE((
                        SELECT json_group_array(json(json_remove(
                            value, '$.fullCalculation', '$.planGeometry',
                            '$.deltaGeometry', '$.lineAdjustments'
                        )))
                        FROM json_each(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates')
                    ), json('[]')),
                    '$.advancedEngineering.workspaceStorage.aggressivelyCompacted', 1
                )
                WHERE workspace_bytes > ?
                  AND json_type(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') = 'array'
                """,
                (_workspace_limit_bytes(),),
            )
            conn.execute(
                "UPDATE projects SET workspace_bytes = length(CAST(workspace_data AS BLOB)) "
                "WHERE workspace_bytes > ?",
                (_workspace_limit_bytes(),),
            )
        except sqlite3.OperationalError:
            pass

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
        workspace_project = _compact_project_for_workspace(project)
        workspace_encoded = _canonical_json(workspace_project)
        payload_bytes = len(encoded.encode("utf-8"))
        workspace_bytes = len(workspace_encoded.encode("utf-8"))
        if workspace_bytes > _workspace_limit_bytes():
            workspace_project = _aggressively_compact_workspace(workspace_project)
            workspace_encoded = _canonical_json(workspace_project)
            workspace_bytes = len(workspace_encoded.encode("utf-8"))
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
            summary_encoded = json.dumps(_project_summary_payload(project, revision), ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO projects (id, name, updated_at, revision, content_hash, summary, workspace_data, payload_bytes, workspace_bytes, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    revision=excluded.revision,
                    content_hash=excluded.content_hash,
                    summary=excluded.summary,
                    workspace_data=excluded.workspace_data,
                    payload_bytes=excluded.payload_bytes,
                    workspace_bytes=excluded.workspace_bytes,
                    data=excluded.data
                """,
                (project["id"], project.get("name", "Untitled"), updated_at, revision, content_hash, summary_encoded, workspace_encoded, payload_bytes, workspace_bytes, encoded),
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
            retain = max(10, int(os.getenv("PITGUARD_REVISION_RETENTION", "30")))
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
        # Summaries are persisted separately so the project list never asks
        # SQLite JSON1 to parse multi-megabyte calculation payloads.
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, updated_at, revision, summary, payload_bytes, workspace_bytes FROM projects ORDER BY updated_at DESC").fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(str(row["summary"] or "{}"))
            except json.JSONDecodeError:
                item = {}
            output.append({
                "id": item.get("id") or row["id"],
                "revision": int(item.get("revision") or row["revision"] or 0),
                "name": item.get("name") or row["name"],
                "location": item.get("location"),
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt") or row["updated_at"],
                "has_excavation": bool(item.get("hasExcavation")),
                "has_retaining_system": bool(item.get("hasRetainingSystem")),
                "calculation_case_count": int(item.get("calculationCaseCount") or 0),
                "calculation_result_count": int(item.get("calculationResultCount") or 0),
                "latest_calculation_id": item.get("latestCalculationId"),
                "governing_status": item.get("governingStatus"),
                "geometry_consistent": item.get("geometryConsistent"),
                "payload_bytes": int(row["payload_bytes"] or 0),
                "workspace_bytes": int(row["workspace_bytes"] or 0),
                "storage_status": (
                    "large" if int(row["payload_bytes"] or 0) >= 96 * 1024 * 1024
                    else "elevated" if int(row["payload_bytes"] or 0) >= 32 * 1024 * 1024
                    else "normal"
                ),
            })
        return output

    def get_payload_info(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, revision, updated_at, payload_bytes, workspace_bytes, "
                "length(CAST(data AS BLOB)) AS measured_payload_bytes, "
                "length(CAST(workspace_data AS BLOB)) AS measured_workspace_bytes "
                "FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        payload_bytes = int(row["payload_bytes"] or row["measured_payload_bytes"] or 0)
        workspace_bytes = int(row["workspace_bytes"] or row["measured_workspace_bytes"] or 0)
        return {
            "id": row["id"],
            "name": row["name"],
            "revision": int(row["revision"] or 0),
            "updatedAt": row["updated_at"],
            "payloadBytes": payload_bytes,
            "workspaceBytes": workspace_bytes,
            "compressionRatio": round(workspace_bytes / max(payload_bytes, 1), 6),
            "apiFullLoadLimitBytes": _api_full_load_limit_bytes(),
            "fullLoadAllowed": _process_role() != "api" or payload_bytes <= _api_full_load_limit_bytes(),
            "processRole": _process_role(),
        }

    def get_workspace_json(self, project_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_data, payload_bytes, workspace_bytes, revision, updated_at "
                "FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        payload = str(row["workspace_data"] or "{}")
        metadata = {
            "revision": int(row["revision"] or 0),
            "updatedAt": row["updated_at"],
            "payloadBytes": int(row["payload_bytes"] or 0),
            "workspaceBytes": int(row["workspace_bytes"] or len(payload.encode("utf-8"))),
        }
        return payload, metadata

    def get_workspace(self, project_id: str) -> dict[str, Any] | None:
        result = self.get_workspace_json(project_id)
        return json.loads(result[0]) if result else None

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            meta = conn.execute(
                "SELECT payload_bytes, length(CAST(data AS BLOB)) AS measured FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if meta is None:
                return None
            payload_bytes = int(meta["payload_bytes"] or meta["measured"] or 0)
            if _process_role() == "api" and payload_bytes > _api_full_load_limit_bytes():
                raise ProjectPayloadTooLarge(project_id, payload_bytes, _api_full_load_limit_bytes())
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
