from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_CSV = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"


def _create_project(client: TestClient) -> str:
    project = client.post("/api/projects", json={"name": "V2.8 wall length closed loop", "location": "test"}).json()
    project_id = project["id"]
    with SAMPLE_CSV.open("rb") as f:
        response = client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (SAMPLE_CSV.name, f, "text/csv")})
    assert response.status_code == 200, response.text
    assert client.post(f"/api/projects/{project_id}/geology/build-model").status_code == 200
    excavation = {
        "name": "Rect pit",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [{"x": 0, "y": 0}, {"x": 70, "y": 0}, {"x": 70, "y": 34}, {"x": 0, "y": 34}]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=excavation).status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    assert client.post(f"/api/projects/{project_id}/calculation/build-cases").status_code == 200
    assert client.post(f"/api/projects/{project_id}/calculation/run").status_code == 200
    return project_id


def _wait(client: TestClient, task_id: str) -> dict:
    task = {}
    for _ in range(80):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"success", "failed", "cancelled"}:
            return task
        time.sleep(0.1)
    return task


def test_v2_8_0_wall_length_redundancy_closed_loop() -> None:
    client = TestClient(app)
    project_id = _create_project(client)

    analysis_response = client.get(f"/api/projects/{project_id}/wall-optimization/length-redundancy?mode=balanced")
    assert analysis_response.status_code == 200, analysis_response.text
    analysis = analysis_response.json()
    assert analysis["uniformThickness"]["policy"].startswith("项目统一墙厚")
    assert analysis["summary"]["faceCount"] >= 4
    assert isinstance(analysis.get("issueSuggestions"), list)
    assert analysis["closedLoopStatus"]["status"] in {"analysis_complete", "candidate_ready", "manual_review_required"}
    assert analysis["candidates"]

    candidate_id = analysis["candidates"][0]["candidateId"]
    apply_response = client.post(f"/api/projects/{project_id}/wall-optimization/apply-length-candidate", json={"candidateId": candidate_id, "mode": "balanced"})
    assert apply_response.status_code == 200, apply_response.text
    assert apply_response.json()["recomputeRequired"] is True

    project_after_apply = client.get(f"/api/projects/{project_id}").json()
    layout = project_after_apply["retainingSystem"]["layoutSummary"]
    assert layout["wallLengthOptimizationRecomputeRequired"] is True
    assert layout["wallLengthOptimizationHistory"][-1]["candidateId"] == candidate_id

    issues = client.get(f"/api/projects/{project_id}/issues").json()
    assert any(item["category"] == "wall_length_redundancy" for item in issues["issues"])
    assert any("未复算" in item["message"] for item in issues["issues"] if item["category"] == "wall_length_redundancy")

    assert client.post(f"/api/projects/{project_id}/calculation/run").status_code == 200
    project_after_recalc = client.get(f"/api/projects/{project_id}").json()
    assert project_after_recalc["retainingSystem"]["layoutSummary"]["wallLengthOptimizationRecomputeRequired"] is False

    task_response = client.post(f"/api/projects/{project_id}/tasks", json={"operation": "export_wall_length_redundancy", "payload": {"mode": "balanced"}})
    assert task_response.status_code == 200, task_response.text
    task = _wait(client, task_response.json()["id"])
    assert task["status"] == "success", task
    assert task["result"]["filename"].endswith("_wall_length_redundancy_v3_0_0.json")
