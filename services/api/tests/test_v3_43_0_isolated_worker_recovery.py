from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.runtime_resource_policy import runtime_memory_snapshot
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.storage.database import SQLiteProjectStore
from app.storage.task_store import SQLiteTaskStore
from app.tasks.manager import TaskManager


def _project() -> Project:
    excavation = make_excavation_model(
        "isolated-worker",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=48, y=0),
                Point2D(x=48, y=22), Point2D(x=0, y=22),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    return Project(name="isolated-worker", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def _heartbeat(path: Path) -> None:
    path.write_text(
        json.dumps({
            "status": "idle",
            "taskId": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "rssMb": 10,
            "systemAvailableMemoryMb": 4096,
        }),
        encoding="utf-8",
    )


def test_resource_snapshot_reports_real_memory() -> None:
    snapshot = runtime_memory_snapshot()
    assert int(snapshot["effectiveTotalBytes"]) > 0
    assert int(snapshot["effectiveAvailableBytes"]) > 0
    assert int(snapshot["processRssBytes"]) > 0


def test_external_mode_rejects_tasks_without_worker(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "no-worker.sqlite3"))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "external")
    monkeypatch.setenv("PITGUARD_WORKER_HEARTBEAT_PATH", str(tmp_path / "missing-heartbeat.json"))
    manager = TaskManager()
    with pytest.raises(RuntimeError, match="worker"):
        manager.ensure_worker_available()


def test_fresh_process_worker_claims_and_finishes_task(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "worker.sqlite3"
    heartbeat = tmp_path / "worker-heartbeat.json"
    store = SQLiteProjectStore(db_path)
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    _heartbeat(heartbeat)

    monkeypatch.setenv("PITGUARD_DB_PATH", str(db_path))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "external")
    monkeypatch.setenv("PITGUARD_WORKER_HEARTBEAT_PATH", str(heartbeat))
    manager = TaskManager()
    task = manager.submit(project.id, "export_json", {})
    assert task.status == "queued"

    api_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update({
        "PITGUARD_DB_PATH": str(db_path),
        "PITGUARD_TASK_EXECUTION_MODE": "worker",
        "PITGUARD_PROCESS_ROLE": "worker",
        "PITGUARD_WORKER_EXIT_AFTER_TASK": "true",
        "PITGUARD_WORKER_HEARTBEAT_PATH": str(heartbeat),
        "PYTHONPATH": str(api_dir),
    })
    completed = subprocess.run(
        [sys.executable, "-m", "app.tasks.worker_daemon"],
        cwd=api_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    persisted = SQLiteTaskStore(db_path).get(task.id)
    assert persisted is not None
    assert persisted["status"] == "success"
    assert persisted["result"]["filename"].endswith(".json")


def test_core_candidate_search_has_bounded_trials() -> None:
    project = _project()
    progress: list[int] = []
    _system, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        search_config={"coreMode": True, "requireDiverseSchemes": True},
        progress_callback=lambda index, total, _family: progress.append(index),
    )
    assert progress
    assert max(progress) <= 12
    assert len(candidates) <= 3


def test_local_start_scripts_use_external_worker() -> None:
    root = Path(__file__).resolve().parents[3]
    windows = (root / "start-windows.ps1").read_text(encoding="utf-8")
    linux = (root / "start-linux-dev.sh").read_text(encoding="utf-8")
    assert "PITGUARD_TASK_EXECUTION_MODE=external" in windows
    assert "run-worker-supervisor.py" in windows
    assert "PITGUARD_TASK_EXECUTION_MODE=external" in linux
    assert "run-worker-supervisor.py" in linux


def _running_task(task_id: str, *, heartbeat_at: str | None = None) -> dict:
    now = heartbeat_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": task_id,
        "projectId": "project-worker-recovery",
        "operation": "rebar_design",
        "status": "running",
        "progress": 45,
        "currentStep": "配筋闭合",
        "createdAt": now,
        "updatedAt": now,
        "heartbeatAt": now,
        "logs": [],
        "payload": {},
    }


def test_normal_fresh_worker_cycle_does_not_interrupt_running_task(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "normal-cycle.sqlite3"
    heartbeat = tmp_path / "worker-heartbeat.json"
    task_id = "task-normal-cycle"
    SQLiteTaskStore(db_path).upsert(_running_task(task_id))
    heartbeat.write_text(json.dumps({
        "status": "completed", "taskId": "task-previous", "updatedAt": datetime.now(timezone.utc).isoformat(),
        "pid": 999999, "rssMb": 10,
    }), encoding="utf-8")
    monkeypatch.setenv("PITGUARD_DB_PATH", str(db_path))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "worker")
    monkeypatch.setenv("PITGUARD_WORKER_HEARTBEAT_PATH", str(heartbeat))
    manager = TaskManager()
    assert manager.recover_external_worker() == 0
    assert SQLiteTaskStore(db_path).get(task_id)["status"] == "running"


def test_dead_prior_worker_interrupts_only_its_matching_task(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "dead-worker.sqlite3"
    heartbeat = tmp_path / "worker-heartbeat.json"
    owned = "task-owned-by-dead-worker"
    unrelated = "task-fresh-unrelated"
    store = SQLiteTaskStore(db_path)
    store.upsert(_running_task(owned))
    store.upsert(_running_task(unrelated))
    heartbeat.write_text(json.dumps({
        "status": "running", "taskId": owned, "updatedAt": datetime.now(timezone.utc).isoformat(),
        "pid": 999999, "rssMb": 10,
    }), encoding="utf-8")
    monkeypatch.setenv("PITGUARD_DB_PATH", str(db_path))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "worker")
    monkeypatch.setenv("PITGUARD_WORKER_HEARTBEAT_PATH", str(heartbeat))
    manager = TaskManager()
    assert manager.recover_external_worker() == 1
    assert store.get(owned)["status"] == "interrupted"
    assert store.get(unrelated)["status"] == "running"
