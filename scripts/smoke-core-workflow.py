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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pitguard-core-smoke-") as temp_dir:
        os.environ["PITGUARD_DB_PATH"] = str(Path(temp_dir) / "pitguard.sqlite3")
        os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
        os.environ["PITGUARD_PRODUCT_MODE"] = "core"
        os.environ.setdefault("PITGUARD_NUMERIC_THREADS", "1")
        sys.path.insert(0, str(API_DIR))

        from fastapi.testclient import TestClient
        from app.main import app

        started = time.perf_counter()
        with TestClient(app) as client:
            project = require(client.post("/api/projects", json={"name": "V3.46 core smoke", "designSettings": {"designBasisConfirmed": True, "bearingCapacityKpa": 180}}), "create project").json()
            project_id = project["id"]
            with CSV_PATH.open("rb") as stream:
                require(
                    client.post(
                        f"/api/projects/{project_id}/boreholes/import-csv",
                        files={"file": (CSV_PATH.name, stream, "text/csv")},
                    ),
                    "import boreholes",
                )
            require(
                client.post(
                    f"/api/projects/{project_id}/excavation",
                    json={
                        "name": "Core smoke excavation",
                        "topElevation": 0.0,
                        "bottomElevation": -10.0,
                        "outline": {
                            "closed": True,
                            "points": [
                                {"x": 5.0, "y": 5.0},
                                {"x": 45.0, "y": 5.0},
                                {"x": 45.0, "y": 25.0},
                                {"x": 5.0, "y": 25.0},
                            ],
                        },
                    },
                ),
                "create excavation",
            )
            task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={"operation": "core_design", "payload": {"maxCandidates": 3, "rebarMode": "balanced"}},
                ),
                "submit core design",
            ).json()
            deadline = time.time() + 120
            while task["status"] not in {"success", "failed", "cancelled", "interrupted"}:
                if time.time() > deadline:
                    raise TimeoutError("core design smoke task exceeded 120 seconds")
                time.sleep(0.2)
                task = require(client.get(f"/api/tasks/{task['id']}"), "read task").json()
            if task["status"] != "success":
                raise RuntimeError(task.get("error") or f"core design status={task['status']}")

            workspace = require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read workspace").json()
            storage = require(client.get(f"/api/projects/{project_id}/storage-health"), "storage health").json()
            retaining = workspace.get("retainingSystem") or {}
            repair = retaining.get("supportLayoutRepair") or {}
            calculations = workspace.get("calculationResults") or []
            latest = calculations[-1] if calculations else {}
            summary = {
                "status": "success",
                "elapsedSeconds": round(time.perf_counter() - started, 3),
                "projectId": project_id,
                "candidateCount": len(repair.get("candidates") or []),
                "selectedCandidateId": repair.get("selectedCandidateId"),
                "supportCount": len(retaining.get("supports") or []),
                "columnCount": len(retaining.get("columns") or []),
                "calculationResultCount": len(calculations),
                "rebarCheckCount": len(((retaining.get("rebarDesignScheme") or {}).get("checks") or [])),
                "failCount": ((latest.get("checkSummary") or {}).get("fail") or 0),
                "warningCount": ((latest.get("checkSummary") or {}).get("warning") or 0),
                "payloadBytes": storage.get("payloadBytes"),
                "workspaceBytes": storage.get("workspaceBytes"),
                "externalBytes": storage.get("externalBytes"),
                "totalLogicalBytes": storage.get("totalLogicalBytes"),
                "artifactCount": storage.get("artifactCount"),
                "storageStatus": storage.get("storageStatus"),
                "taskLogs": task.get("logs", [])[-5:],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
