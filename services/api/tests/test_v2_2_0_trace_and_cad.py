from __future__ import annotations

import zipfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def _wait(client: TestClient, task_id: str) -> dict:
    task = {}
    for _ in range(60):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"success", "failed", "cancelled"}:
            return task
        time.sleep(0.1)
    return task


def test_v2_2_0_trace_endpoint_without_calculation() -> None:
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "trace smoke"}).json()
    response = client.get(f"/api/projects/{project['id']}/calculation/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["controlPathCompleteness"] == 0
    assert data["entries"] == []


def test_v2_2_0_issue_and_trace_export_tasks() -> None:
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "v2.2 export smoke"}).json()
    project_id = project["id"]
    for operation in ["export_issue_report", "export_trace"]:
        response = client.post(f"/api/projects/{project_id}/tasks", json={"operation": operation, "payload": {}})
        assert response.status_code == 200
        task = _wait(client, response.json()["id"])
        assert task["status"] == "success"
        assert task["result"]["filename"].endswith(".json")
        assert client.get(f"/api/tasks/{task['id']}/download").status_code == 200
