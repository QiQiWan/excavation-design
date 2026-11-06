from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
CSV_PATH = ROOT / "packages" / "sample-data" / "boreholes" / "sample_boreholes.csv"


def require(response, label: str):
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:1000]}")
    return response


def wait_task(client, task: dict, label: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while task["status"] not in {"success", "failed", "cancelled", "interrupted"}:
        if time.time() > deadline:
            raise TimeoutError(f"{label} exceeded {timeout:.0f} seconds")
        time.sleep(0.2)
        task = require(client.get(f"/api/tasks/{task['id']}"), f"read {label}").json()
    if task["status"] != "success":
        raise RuntimeError(task.get("error") or f"{label} status={task['status']}")
    return task


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pitguard-p3-smoke-") as temp_dir:
        os.environ["PITGUARD_DB_PATH"] = str(Path(temp_dir) / "pitguard.sqlite3")
        os.environ["PITGUARD_ARTIFACT_ROOT"] = str(Path(temp_dir) / "artifacts")
        os.environ["PITGUARD_RUNTIME_DIR"] = str(Path(temp_dir) / "runtime")
        os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
        os.environ["PITGUARD_PRODUCT_MODE"] = "core"
        os.environ.setdefault("PITGUARD_NUMERIC_THREADS", "1")
        sys.path.insert(0, str(API_DIR))

        from fastapi.testclient import TestClient
        from app.main import app

        started = time.perf_counter()
        with TestClient(app) as client:
            project = require(client.post("/api/projects", json={"name": "V3.47 P3 smoke", "designSettings": {"designBasisConfirmed": True, "bearingCapacityKpa": 180}}), "create project").json()
            project_id = project["id"]
            with CSV_PATH.open("rb") as stream:
                require(client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (CSV_PATH.name, stream, "text/csv")}), "import boreholes")
            require(client.post(f"/api/projects/{project_id}/excavation", json={
                "name": "P3 smoke excavation", "topElevation": 0.0, "bottomElevation": -10.0,
                "outline": {"closed": True, "points": [{"x": 5.0, "y": 5.0}, {"x": 45.0, "y": 5.0}, {"x": 45.0, "y": 25.0}, {"x": 5.0, "y": 25.0}]},
            }), "create excavation")
            core = wait_task(client, require(client.post(f"/api/projects/{project_id}/tasks", json={"operation": "core_design", "payload": {"maxCandidates": 3, "rebarMode": "balanced"}}), "submit core").json(), "core design")
            formal = wait_task(client, require(client.post(f"/api/projects/{project_id}/tasks", json={"operation": "formal_adverse_scenarios", "payload": {"codes": ["DEWATERING_FAILURE", "OVEREXCAVATION"]}}), "submit formal scenarios").json(), "formal scenarios")
            p3 = wait_task(client, require(client.post(f"/api/projects/{project_id}/tasks", json={"operation": "p3_detailing_closure", "payload": {"mode": "balanced", "topNodeCount": 3}}), "submit P3 detailing").json(), "P3 detailing")
            status = require(client.get(f"/api/projects/{project_id}/design/core-status"), "read core status").json()
            result = {
                "status": "success",
                "elapsedSeconds": round(time.perf_counter() - started, 3),
                "projectId": project_id,
                "coreTask": core.get("result"),
                "formalScenarioSummary": status.get("formalAdverseScenarioSuite", {}).get("summary"),
                "formalArtifact": formal.get("result", {}).get("artifact"),
                "p3Summary": status.get("p3DetailingClosure", {}).get("summary"),
                "p3Artifact": p3.get("result", {}).get("artifact"),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
