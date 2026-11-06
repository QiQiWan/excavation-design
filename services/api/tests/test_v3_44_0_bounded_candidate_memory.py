from __future__ import annotations

import json
from pathlib import Path

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.runtime_diagnostics import append_event
from app.services.runtime_resource_policy import adaptive_resource_policy
from app.services.support_layout_optimizer import _candidate_seed_system
from app.services.support_layout_repair import adopt_support_layout_candidate, auto_repair_support_layout


def _project() -> Project:
    excavation = make_excavation_model(
        "bounded-memory",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=52, y=0),
                Point2D(x=52, y=24), Point2D(x=0, y=24),
            ],
            closed=True,
        ),
        0.0,
        -11.0,
    )
    return Project(name="bounded-memory", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def test_candidate_seed_excludes_historical_payloads() -> None:
    project = _project()
    assert project.retaining_system is not None
    project.retaining_system.layout_summary = {
        "autoRepair": {"blob": "x" * 2_000_000},
        "supportOptimizationCandidates": [{"blob": "y" * 2_000_000}],
    }
    project.retaining_system.rebar_design_scheme = {"bars": [{"blob": "z" * 1_000_000}]}
    seed = _candidate_seed_system(project)
    assert seed.layout_summary == {}
    assert seed.support_layout_repair is None
    assert seed.rebar_design_scheme == {}
    assert seed.supports == []
    assert len(seed.diaphragm_walls) == len(project.retaining_system.diaphragm_walls)


def test_candidate_adoption_does_not_rerun_search(monkeypatch) -> None:
    project = _project()
    repair = auto_repair_support_layout(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={"maxTrials": 18, "candidatePoolLimit": 6, "requireDiverseSchemes": True},
    )
    assert repair.candidates
    assert project.retaining_system is not None
    project.retaining_system.layout_summary["autoRepair"] = {"blob": "x" * 500_000}
    project.retaining_system.layout_summary["supportOptimizationCandidates"] = [{"blob": "y" * 500_000}]

    import app.services.support_layout_repair as repair_module

    def forbidden(*_args, **_kwargs):
        raise AssertionError("candidate adoption must not rerun the optimizer")

    monkeypatch.setattr(repair_module, "optimize_support_layout_candidates", forbidden)
    selected = repair.candidates[-1]
    adopted = adopt_support_layout_candidate(project, selected.id)
    assert adopted.status != "fail"
    assert adopted.selected_candidate_id == selected.id
    assert len(adopted.candidates) <= 3
    assert "autoRepair" not in project.retaining_system.layout_summary
    assert "supportOptimizationCandidates" not in project.retaining_system.layout_summary
    assert project.retaining_system.layout_summary["supportOptimization"]["selectedCandidateId"] == selected.id


def test_default_worker_budget_is_bounded(monkeypatch) -> None:
    import app.services.runtime_resource_policy as resource

    gib = 1024**3
    monkeypatch.delenv("PITGUARD_WORKER_DEFAULT_HARD_CAP_MB", raising=False)
    monkeypatch.delenv("PITGUARD_WORKER_RSS_HARD_LIMIT_MB", raising=False)
    monkeypatch.delenv("PITGUARD_WORKER_MEMORY_MAX_MB", raising=False)
    monkeypatch.setattr(resource, "runtime_memory_snapshot", lambda: {
        "hostTotalBytes": 32 * gib,
        "hostAvailableBytes": 24 * gib,
        "cgroupLimitBytes": None,
        "cgroupCurrentBytes": None,
        "effectiveTotalBytes": 32 * gib,
        "effectiveAvailableBytes": 24 * gib,
        "processRssBytes": 512 * 1024**2,
        "processEffectiveBytes": 600 * 1024**2,
        "cpuCount": 16,
        "loadAverage1m": 0.0,
        "loadAverage5m": 0.0,
        "loadAverage15m": 0.0,
        "diskRoot": ".",
        "diskTotalBytes": 100 * gib,
        "diskUsedBytes": 20 * gib,
        "diskFreeBytes": 80 * gib,
    })
    policy = adaptive_resource_policy(role="worker")
    assert int(policy["workerHardLimitBytes"]) <= 6 * gib
    assert int(policy["workerSoftLimitBytes"]) < int(policy["workerHardLimitBytes"])
    assert policy["recommendedHeavyConcurrency"] == 1


def test_runtime_diagnostics_written_under_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_RUNTIME_DIAGNOSTICS", "1")
    append_event("candidate-search", "test-event", candidatePoolSize=3)
    path = tmp_path / "diagnostics" / "candidate-search.jsonl"
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "test-event"
    assert record["candidatePoolSize"] == 3


def test_startup_migration_removes_duplicate_candidate_payloads_without_hydration(tmp_path: Path) -> None:
    import sqlite3

    from app.storage.database import SQLiteProjectStore

    db_path = tmp_path / "legacy.sqlite3"
    store = SQLiteProjectStore(db_path)
    project = _project().model_dump(mode="json", by_alias=True)
    store.upsert(project)
    duplicate = {"candidates": [{"id": "legacy", "blob": "x" * 500_000}]}
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT data, workspace_data FROM projects WHERE id = ?", (project["id"],)).fetchone()
        full = json.loads(row[0])
        workspace = json.loads(row[1])
        for payload in (full, workspace):
            retaining = payload.setdefault("retainingSystem", {})
            summary = retaining.setdefault("layoutSummary", {})
            summary["autoRepair"] = duplicate
            summary["supportOptimizationCandidates"] = duplicate["candidates"]
        full_raw = json.dumps(full, ensure_ascii=False, separators=(",", ":"))
        workspace_raw = json.dumps(workspace, ensure_ascii=False, separators=(",", ":"))
        conn.execute(
            "UPDATE projects SET data=?, workspace_data=?, payload_bytes=?, workspace_bytes=? WHERE id=?",
            (full_raw, workspace_raw, len(full_raw.encode()), len(workspace_raw.encode()), project["id"]),
        )
        conn.commit()
    SQLiteProjectStore._initialized_paths.discard(str(db_path.resolve()))
    migrated = SQLiteProjectStore(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT data, workspace_data, payload_bytes, workspace_bytes FROM projects WHERE id = ?",
            (project["id"],),
        ).fetchone()
    full_after = json.loads(row[0])
    workspace_after = json.loads(row[1])
    for payload in (full_after, workspace_after):
        summary = ((payload.get("retainingSystem") or {}).get("layoutSummary") or {})
        assert "autoRepair" not in summary
        assert "supportOptimizationCandidates" not in summary
    assert row[2] < len(full_raw.encode())
    assert row[3] < len(workspace_raw.encode())
