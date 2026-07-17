from __future__ import annotations

"""Run the exact Harvest Lake sample through candidate search and formal calculation."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
SAMPLE_DIR = ROOT / "packages" / "sample-data" / "actual-project"
CSV_PATH = SAMPLE_DIR / "actual_project_boreholes_24x6layers.csv"
EXCAVATION_PATH = SAMPLE_DIR / "actual_project_excavation_payload.json"


def require(response: Any, label: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:1600]}")
    return response


def wait_task(client: Any, task: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while task.get("status") not in {"success", "failed", "cancelled", "interrupted"}:
        if time.time() > deadline:
            raise TimeoutError(f"task exceeded {timeout_seconds:.0f}s: {(task.get('logs') or [])[-10:]}")
        time.sleep(0.25)
        task = require(client.get(f"/api/tasks/{task['id']}"), "read task").json()
    if task.get("status") != "success":
        raise RuntimeError(str(task.get("error") or task))
    return task


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pitguard-v350-full-calculation-") as temp_dir:
        os.environ["PITGUARD_DB_PATH"] = str(Path(temp_dir) / "pitguard.sqlite3")
        os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
        os.environ["PITGUARD_PRODUCT_MODE"] = "core"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT"] = "12"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT"] = "6"
        os.environ["PITGUARD_NUMERIC_THREADS"] = "1"
        sys.path.insert(0, str(API_DIR))

        from fastapi.testclient import TestClient
        from app.main import app

        excavation_payload = json.loads(EXCAVATION_PATH.read_text(encoding="utf-8"))
        with TestClient(app) as client:
            project = require(
                client.post(
                    "/api/projects",
                    json={
                        "name": "V3.50 Harvest Lake full calculation",
                        "designSettings": {"designBasisConfirmed": True, "bearingCapacityKpa": 220.0},
                    },
                ),
                "create project",
            ).json()
            project_id = project["id"]
            with CSV_PATH.open("rb") as stream:
                require(
                    client.post(
                        f"/api/projects/{project_id}/boreholes/import-csv",
                        files={"file": (CSV_PATH.name, stream, "text/csv")},
                    ),
                    "import boreholes",
                )
            require(client.post(f"/api/projects/{project_id}/excavation", json=excavation_payload), "create excavation")

            search_started = time.perf_counter()
            search_task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={
                        "operation": "support_layout_optimization",
                        "payload": {"preset": "balanced", "maxCandidates": 3, "searchConfig": {"requireDiverseSchemes": True}},
                    },
                ),
                "submit candidate search",
            ).json()
            search_task = wait_task(client, search_task, 180.0)
            search_elapsed = time.perf_counter() - search_started

            calculation_started = time.perf_counter()
            calculation_task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={"operation": "calculation_full", "payload": {"topN": 0}},
                ),
                "submit full calculation",
            ).json()
            calculation_task = wait_task(client, calculation_task, 300.0)
            calculation_elapsed = time.perf_counter() - calculation_started

            workspace = require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read calculated workspace").json()
            retaining = workspace.get("retainingSystem") or {}
            repair = retaining.get("supportLayoutRepair") or {}
            results = list(workspace.get("calculationResults") or [])
            summary = {
                "status": "success",
                "projectId": project_id,
                "candidateSearchSeconds": round(search_elapsed, 3),
                "calculationSeconds": round(calculation_elapsed, 3),
                "candidateCount": len(repair.get("candidates") or []),
                "selectedCandidateId": repair.get("selectedCandidateId"),
                "supportCount": len(retaining.get("supports") or []),
                "columnCount": len(retaining.get("columns") or []),
                "calculationResultCount": len(results),
                "latestCalculationResultId": results[-1].get("id") if results else None,
                "taskLogTail": list(calculation_task.get("logs") or [])[-8:],
            }
            if not summary["selectedCandidateId"] or summary["supportCount"] <= 0 or not results:
                raise RuntimeError(f"full calculation smoke did not close: {summary}")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
