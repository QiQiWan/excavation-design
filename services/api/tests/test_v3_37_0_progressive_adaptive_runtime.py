from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.progressive_design import (
    build_progressive_design_session,
    merge_progressive_config,
    normalize_progressive_config,
    task_payload_from_progressive_config,
)
from app.storage.database import SQLiteProjectStore


def _project() -> Project:
    excavation = make_excavation_model(
        "progressive",
        Polyline2D(
            points=[Point2D(x=0, y=0), Point2D(x=72, y=0), Point2D(x=72, y=24), Point2D(x=0, y=24)],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    return Project(name="progressive", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def _candidate(candidate_id: str = "C-1") -> dict:
    return {
        "id": candidate_id,
        "rank": 1,
        "score": 82.5,
        "status": "warning",
        "hardConstraints": {"passed": True},
        "supportCount": 2,
        "columnCount": 1,
        "metrics": {"supportCrossingCount": 0},
        "planGeometry": {
            "outline": [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 10}, {"x": 0, "y": 10}],
            "supports": [
                {"id": "S-1", "role": "main_strut", "start": {"x": 5, "y": 0}, "end": {"x": 5, "y": 10}},
                {"id": "S-2", "role": "main_strut", "start": {"x": 15, "y": 0}, "end": {"x": 15, "y": 10}},
            ],
            "columns": [{"id": "C-1", "location": {"x": 10, "y": 5}}],
        },
        "fullCalculation": {"large": "x" * 2000},
    }


def test_adaptive_policy_respects_operator_96mb_ceiling(monkeypatch) -> None:
    import app.services.runtime_resource_policy as resource

    monkeypatch.setenv("PITGUARD_RESOURCE_POLICY_MODE", "adaptive")
    monkeypatch.setenv("PITGUARD_API_FULL_PROJECT_LIMIT_MB", "96")
    monkeypatch.delenv("PITGUARD_API_FULL_PROJECT_HARD_CAP_MB", raising=False)
    monkeypatch.setattr(resource, "runtime_memory_snapshot", lambda: {
        "hostTotalBytes": 64 * 1024**3,
        "hostAvailableBytes": 32 * 1024**3,
        "cgroupLimitBytes": None,
        "cgroupCurrentBytes": None,
        "effectiveTotalBytes": 64 * 1024**3,
        "effectiveAvailableBytes": 32 * 1024**3,
        "processRssBytes": 512 * 1024**2,
    })
    policy = resource.adaptive_resource_policy(role="api")
    assert policy["apiFullLoadLimitBytes"] == 96 * 1024**2
    assert policy["workspaceFirst"] is True
    assert policy["recommendedHeavyConcurrency"] >= 1


def test_progressive_session_has_general_eight_stage_decision_chain() -> None:
    project = _project()
    session = build_progressive_design_session(project, storage_info={"fullLoadAllowed": True, "workspaceLoadAllowed": True})
    assert [stage["code"] for stage in session["stages"]] == [
        "geometry_context",
        "engineering_context",
        "retaining_wall_strategy",
        "support_system_strategy",
        "topology_search",
        "candidate_screening",
        "stage_calculation",
        "detailing_release",
    ]
    assert session["systemOptions"]["options"]
    assert session["resourcePolicy"]["workspaceFirst"] is True


def test_progressive_configuration_drives_task_payload_without_shape_specific_branch() -> None:
    project = _project()
    current = normalize_progressive_config(project)
    updated = merge_progressive_config(current, {
        "currentStage": "topology_search",
        "decisions": {
            "supportSystemFamily": "hybrid_diagonal",
            "objectivePreset": "muck_path_priority",
            "candidateCount": 5,
        },
        "constraints": {
            "supportSpacingMinM": 3.5,
            "supportSpacingMaxM": 7.0,
            "preferredSupportSpacingM": 5.5,
            "columnServiceSpanMaxM": 20.0,
        },
        "action": "configured",
    })
    payload = task_payload_from_progressive_config(updated)
    assert payload["topologyFamily"] == "hybrid_diagonal"
    assert payload["preset"] == "muck_path_priority"
    assert payload["maxCandidates"] == 5
    assert payload["searchConfig"]["spacingMaxM"] == 7.0
    assert updated["history"][-1]["stage"] == "topology_search"


def test_candidate_preview_cache_survives_workspace_compaction(tmp_path) -> None:
    store = SQLiteProjectStore(tmp_path / "preview.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "id": "preview-project",
        "name": "Preview",
        "createdAt": now,
        "updatedAt": now,
        "calculationResults": [],
        "advancedEngineering": {},
        "retainingSystem": {"supportLayoutRepair": {"candidates": [_candidate()]}, "supports": [], "columns": []},
    }
    store.upsert(project)
    workspace = store.get_workspace("preview-project") or {}
    candidates = workspace["retainingSystem"]["supportLayoutRepair"]["candidates"]
    assert candidates[0]["planGeometry"]["supports"]
    bundle = store.get_candidate_preview_bundle("preview-project")
    assert bundle["source"] == "preview_cache"
    assert bundle["previews"][0]["planGeometry"]["outline"]


def test_low_memory_storage_maintenance_rebuilds_workspace_without_full_hydration(tmp_path, monkeypatch) -> None:
    import app.storage.database as database

    store = SQLiteProjectStore(tmp_path / "low-memory.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    document = {
        "id": "legacy-large",
        "name": "Legacy",
        "createdAt": now,
        "updatedAt": now,
        "calculationResults": [{"id": "R1", "stageResults": [{"v": "x" * 10000}]}],
        "geologicalModel": {"surfaces": [{"grid": {"zValues": [[1] * 50] * 50}}], "volumes": []},
        "monitoringRecords": [{"v": index} for index in range(100)],
        "advancedEngineering": {"renderCache": {"blob": "x" * 10000}},
        "retainingSystem": {"supportLayoutRepair": {"candidates": [_candidate()]}, "supports": [], "columns": []},
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO projects(id,name,updated_at,revision,content_hash,summary,workspace_data,payload_bytes,workspace_bytes,external_bytes,artifact_count,data)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("legacy-large", "Legacy", now, 1, digest, "{}", encoded, len(encoded.encode()), len(encoded.encode()), 0, 0, encoded),
        )
        conn.commit()

    monkeypatch.setattr(database, "adaptive_resource_policy", lambda role=None: {
        "mode": "adaptive",
        "role": role or "worker",
        "effectiveTotalBytes": 1024 * 1024**2,
        "effectiveAvailableBytes": 400 * 1024**2,
        "processRssBytes": 300 * 1024**2,
        "reserveBytes": 256 * 1024**2,
        "usableHeadroomBytes": 144 * 1024**2,
        "apiJsonAmplification": 5.5,
        "apiFullLoadLimitBytes": 32 * 1024**2,
        "workspaceLimitBytes": 16 * 1024**2,
        "workerSoftLimitBytes": 420 * 1024**2,
        "workerHardLimitBytes": 600 * 1024**2,
        "recommendedHeavyConcurrency": 1,
        "workspaceFirst": True,
        "workerFullHydrationAllowed": False,
    })
    result = store.compact_project_storage("legacy-large")
    assert result["mode"] == "workspace_projection_only"
    assert result["fullSnapshotExternalizationDeferred"] is True
    workspace = store.get_workspace("legacy-large") or {}
    candidate = workspace["retainingSystem"]["supportLayoutRepair"]["candidates"][0]
    assert candidate["planGeometry"]["supports"]
    assert workspace["calculationResults"] == []
    assert result["after"]["workspaceBytes"] < result["before"]["workspaceBytes"]


def test_candidate_batch_submission_uses_workspace_projection() -> None:
    from types import SimpleNamespace
    from app.tasks.manager import TaskManager

    candidates = [SimpleNamespace(id="A"), SimpleNamespace(id="B"), SimpleNamespace(id="C")]
    workspace_project = SimpleNamespace(
        retaining_system=SimpleNamespace(
            support_layout_repair=SimpleNamespace(candidates=candidates),
        ),
    )

    class FakeRepo:
        def require_workspace(self, project_id: str):
            assert project_id == "large-project"
            return workspace_project

        def require(self, project_id: str):  # pragma: no cover - proves the wrong path is not used
            raise AssertionError("full project hydration must not occur during API batch submission")

    manager = object.__new__(TaskManager)
    manager._repo = lambda: FakeRepo()  # type: ignore[method-assign]
    submitted: list[tuple[str, str, dict]] = []

    def submit(project_id: str, operation: str, payload: dict):
        submitted.append((project_id, operation, payload))
        return SimpleNamespace(id=f"task-{len(submitted)}")

    manager.submit = submit  # type: ignore[method-assign]
    tasks = manager.submit_candidate_batch("large-project", top_n=3, use_cache=True)
    assert len(tasks) == 3
    assert [row[2]["candidateId"] for row in submitted] == ["A", "B", "C"]
    assert all(row[1] == "candidate_scheme_calculation" for row in submitted)


def test_adaptive_policy_reduces_heavy_concurrency_and_blocks_compaction_on_low_resources(monkeypatch) -> None:
    import app.services.runtime_resource_policy as resource

    monkeypatch.setenv("PITGUARD_RESOURCE_POLICY_MODE", "adaptive")
    monkeypatch.delenv("PITGUARD_HEAVY_TASK_CONCURRENCY", raising=False)
    monkeypatch.setattr(resource, "runtime_memory_snapshot", lambda: {
        "hostTotalBytes": 16 * 1024**3,
        "hostAvailableBytes": 4 * 1024**3,
        "cgroupLimitBytes": 16 * 1024**3,
        "cgroupCurrentBytes": 12 * 1024**3,
        "effectiveTotalBytes": 16 * 1024**3,
        "effectiveAvailableBytes": 4 * 1024**3,
        "processRssBytes": 2 * 1024**3,
        "cpuCount": 8,
        "loadAverage1m": 7.6,
        "diskTotalBytes": 100 * 1024**3,
        "diskFreeBytes": 3 * 1024**3,
    })
    policy = resource.adaptive_resource_policy(role="worker")
    assert policy["recommendedHeavyConcurrency"] == 1
    assert policy["cpuLoadRatio"] >= 0.9
    assert policy["storageCompactionAllowed"] is False
    assert policy["diskUsableBytes"] < 1024 * 1024**3
