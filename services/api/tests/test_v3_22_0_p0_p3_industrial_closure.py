from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.schemas.domain import MonitoringRecord, Project
from app.services.industrial_readiness import evaluate_industrial_readiness, run_geometry_qualification_suite
from app.services.monitoring_calibration import monitoring_control_summary
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.tasks.manager import TaskRecord


def test_geometry_qualification_suite_closes_general_shape_p0() -> None:
    suite = run_geometry_qualification_suite()
    assert suite["status"] == "pass"
    assert suite["passedCount"] == suite["caseCount"] == 5
    assert all(row["crossingCount"] == 0 for row in suite["cases"])
    assert all(row["outsideCount"] == 0 for row in suite["cases"])


def test_monitoring_control_builds_alert_and_digital_twin_snapshot() -> None:
    project = Project(name="monitoring")
    project.monitoring_records = [
        MonitoringRecord(
            record_type="wall_displacement",
            object_code="W-01",
            timestamp="2026-07-14T08:00:00+08:00",
            measured_value=8.0,
            unit="mm",
        ),
        MonitoringRecord(
            record_type="wall_displacement",
            object_code="W-01",
            timestamp="2026-07-15T08:00:00+08:00",
            measured_value=14.0,
            unit="mm",
        ),
    ]
    result = monitoring_control_summary(project)
    assert result["verifiedRecordCount"] == 2
    assert result["alertsEvaluated"] is True
    assert result["series"][0]["projected24h"] == pytest.approx(20.0)
    assert result["highestLevel"] in {"watch", "warning", "alarm"}
    assert result["digitalTwin"]["observedObjectCount"] == 1
    assert result["thresholdPolicy"]["statutory"] is False


def test_project_store_revision_conflict_audit_and_restore(tmp_path) -> None:
    repo = ProjectRepository(SQLiteProjectStore(tmp_path / "projects.sqlite3"))
    project = repo.create(Project(name="R0"), actor="designer")
    assert repo.revision(project.id) == 1

    project.name = "R1"
    repo.save(project, expected_revision=1, actor="designer", action="project.rename")
    assert repo.revision(project.id) == 2

    with pytest.raises(HTTPException) as exc_info:
        project.name = "stale-write"
        repo.save(project, expected_revision=1, actor="checker")
    assert exc_info.value.status_code == 409

    revisions = repo.revisions(project.id)
    assert [row["revision"] for row in revisions[:2]] == [2, 1]
    audit = repo.audit_events(project.id)
    assert any(row["action"] == "project.rename" for row in audit)

    restored = repo.restore_revision(project.id, 1, actor="reviewer")
    assert restored.name == "R0"
    assert restored.advanced_engineering["restoredFromRevision"] == 1
    assert repo.revision(project.id) == 3


def test_industrial_readiness_returns_all_p0_p3_phases_without_crashing() -> None:
    project = Project(name="empty")
    result = evaluate_industrial_readiness(project)
    assert [phase["phaseId"] for phase in result["phases"]] == ["P0", "P1", "P2", "P3"]
    assert result["status"] == "fail"
    assert result["officialIssueEligible"] is False
    assert result["boundary"]


def test_task_record_persists_retry_provenance_and_payload() -> None:
    task = TaskRecord(
        id="task-a",
        project_id="project-a",
        operation="industrial_closure",
        title="closure",
        payload={"topN": 3},
        attempt=2,
        parent_task_id="task-parent",
        heartbeat_at="2026-07-14T00:00:00+00:00",
    )
    restored = TaskRecord.from_dict(task.as_dict(include_logs=True))
    assert restored.payload == {"topN": 3}
    assert restored.attempt == 2
    assert restored.parent_task_id == "task-parent"
    assert restored.heartbeat_at is not None


def test_project_defined_monitoring_thresholds_and_projection_are_applied() -> None:
    project = Project(name="project-thresholds")
    project.design_settings.monitoring_threshold_source = "project_defined"
    project.design_settings.monitoring_wall_displacement_warning_mm = 10.0
    project.design_settings.monitoring_wall_displacement_alarm_mm = 15.0
    project.design_settings.monitoring_projection_hours = 48.0
    project.monitoring_records = [
        MonitoringRecord(
            record_type="wall_displacement",
            object_code="W-01",
            timestamp="2026-07-14T08:00:00+08:00",
            measured_value=6.0,
            unit="mm",
        ),
        MonitoringRecord(
            record_type="wall_displacement",
            object_code="W-01",
            timestamp="2026-07-15T08:00:00+08:00",
            measured_value=9.0,
            unit="mm",
        ),
    ]
    result = monitoring_control_summary(project)
    row = result["series"][0]
    assert row["projectionHours"] == 48.0
    assert row["projected24h"] == pytest.approx(15.0)
    assert row["thresholds"]["warning"] == 10.0
    assert row["thresholds"]["alarm"] == 15.0
    assert result["highestLevel"] == "alarm"
    assert result["thresholdPolicy"]["type"] == "project_defined"


def test_sqlite_online_backup_is_integrity_checked(tmp_path) -> None:
    store = SQLiteProjectStore(tmp_path / "projects.sqlite3")
    repo = ProjectRepository(store)
    repo.create(Project(name="backup-source"), actor="designer")
    result = store.backup(tmp_path / "backups")
    assert result["status"] == "pass"
    assert result["integrityCheck"].lower() == "ok"
    assert result["projectCount"] == 1
    assert len(result["sha256"]) == 64
    assert (tmp_path / "backups" / result["filename"]).exists()


def test_optional_api_key_rbac_protects_runtime_and_backup(monkeypatch, tmp_path) -> None:
    import json
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "secure.sqlite3"))
    monkeypatch.setenv("PITGUARD_BACKUP_DIR", str(tmp_path / "secure-backups"))
    monkeypatch.setenv("PITGUARD_API_KEYS", json.dumps({
        "viewer-secret": {"role": "viewer", "actor": "viewer", "keyId": "viewer-1"},
        "admin-secret": {"role": "admin", "actor": "admin", "keyId": "admin-1"},
    }))
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/system/metrics").status_code == 401
    assert client.get("/api/system/metrics", headers={"X-PitGuard-Key": "viewer-secret"}).status_code == 200
    assert client.post("/api/system/backup", headers={"X-PitGuard-Key": "viewer-secret"}).status_code == 403
    backup = client.post("/api/system/backup", headers={"X-PitGuard-Key": "admin-secret"})
    assert backup.status_code == 200
    assert backup.json()["status"] == "pass"
