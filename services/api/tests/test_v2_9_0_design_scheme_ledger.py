from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_CSV = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"


def _wait(client: TestClient, task_id: str) -> dict:
    task = {}
    for _ in range(80):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"success", "failed", "cancelled"}:
            return task
        time.sleep(0.1)
    return task


def _project(client: TestClient) -> str:
    r = client.post("/api/projects", json={"name": "V3.0 design scheme ledger", "location": "test"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    with SAMPLE_CSV.open("rb") as f:
        assert client.post(f"/api/projects/{pid}/boreholes/import-csv", files={"file": (SAMPLE_CSV.name, f, "text/csv")}).status_code == 200
    assert client.post(f"/api/projects/{pid}/geology/build-model").status_code == 200
    excavation = {"name": "Rect pit", "topElevation": 0, "bottomElevation": -12, "outline": {"closed": True, "points": [{"x": 0, "y": 0}, {"x": 70, "y": 0}, {"x": 70, "y": 34}, {"x": 0, "y": 34}]}}
    assert client.post(f"/api/projects/{pid}/excavation", json=excavation).status_code == 200
    assert client.post(f"/api/projects/{pid}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{pid}/design/auto-supports").status_code == 200
    assert client.post(f"/api/projects/{pid}/calculation/build-cases").status_code == 200
    assert client.post(f"/api/projects/{pid}/calculation/run").status_code == 200
    return pid


def test_v3_0_0_dashboard_scheme_snapshot_and_export() -> None:
    client = TestClient(app)
    pid = _project(client)

    dashboard = client.get(f"/api/projects/{pid}/dashboard").json()
    assert dashboard["dashboardVersion"] == "3.2.0"
    assert "deliveryGate" in dashboard
    assert "currentKpis" in dashboard

    analysis = client.get(f"/api/projects/{pid}/wall-optimization/length-redundancy?mode=balanced").json()
    assert analysis["candidates"]
    cid = analysis["candidates"][0]["candidateId"]
    apply = client.post(f"/api/projects/{pid}/wall-optimization/apply-length-candidate", json={"candidateId": cid, "mode": "balanced"})
    assert apply.status_code == 200, apply.text

    ledger_pending = client.get(f"/api/projects/{pid}/design-scheme-ledger").json()
    assert ledger_pending["ledgerVersion"] == "3.2.0"
    assert ledger_pending["schemeSnapshots"][-1]["status"] == "pending_recalculation"
    assert ledger_pending["deliveryGate"]["recomputeRequired"] is True

    assert client.post(f"/api/projects/{pid}/calculation/run").status_code == 200
    ledger_closed = client.get(f"/api/projects/{pid}/design-scheme-ledger").json()
    assert ledger_closed["schemeSnapshots"][-1]["status"] == "closed"
    assert ledger_closed["deliveryGate"]["recomputeRequired"] is False

    task_response = client.post(f"/api/projects/{pid}/tasks", json={"operation": "export_design_scheme_ledger", "payload": {"mode": "balanced"}})
    assert task_response.status_code == 200, task_response.text
    task = _wait(client, task_response.json()["id"])
    assert task["status"] == "success", task
    assert task["result"]["filename"].endswith("_design_scheme_ledger_v3_0_0.json")
