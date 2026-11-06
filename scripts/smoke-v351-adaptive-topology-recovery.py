from __future__ import annotations

"""Close the observed stepped-outline topology block and run a formal calculation."""

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
    with tempfile.TemporaryDirectory(prefix="pitguard-v351-topology-") as temp_dir:
        temp = Path(temp_dir)
        os.environ["PITGUARD_DB_PATH"] = str(temp / "pitguard.sqlite3")
        os.environ["PITGUARD_ARTIFACT_ROOT"] = str(temp / "artifacts")
        os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
        os.environ["PITGUARD_PRODUCT_MODE"] = "core"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT"] = "12"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT"] = "6"
        os.environ["PITGUARD_NUMERIC_THREADS"] = "1"
        sys.path.insert(0, str(API_DIR))

        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.support_layout_optimizer import SUPPORT_CANDIDATE_CONTRACT_VERSION

        excavation_payload = json.loads(EXCAVATION_PATH.read_text(encoding="utf-8"))
        excavation_payload["bottomElevation"] = -12.0
        started = time.perf_counter()
        with TestClient(app) as client:
            project = require(
                client.post(
                    "/api/projects",
                    json={
                        "name": "V3.51 stepped topology recovery",
                        "designSettings": {
                            "designBasisConfirmed": True,
                            "bearingCapacityKpa": 220.0,
                            "supportMinStationSeparationM": 4.0,
                        },
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
            require(
                client.post(f"/api/projects/{project_id}/excavation", json=excavation_payload),
                "create excavation",
            )

            search_started = time.perf_counter()
            search_task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={
                        "operation": "support_layout_optimization",
                        "payload": {
                            "preset": "balanced",
                            "maxCandidates": 3,
                            "topologyFamily": "direct_grid",
                            "searchConfig": {
                                "spacingMinM": 4.0,
                                "spacingMaxM": 6.5,
                                "preferredSpacingM": 4.0,
                                "maxTrials": 3,
                                "requireDiverseSchemes": False,
                            },
                        },
                    },
                ),
                "submit adaptive topology search",
            ).json()
            search_task = wait_task(client, search_task, 180.0)
            search_seconds = time.perf_counter() - search_started

            workspace = require(
                client.get(f"/api/projects/{project_id}?profile=workspace"),
                "read selected topology",
            ).json()
            retaining = workspace.get("retainingSystem") or {}
            repair = retaining.get("supportLayoutRepair") or {}
            candidates = list(repair.get("candidates") or [])
            selected_id = repair.get("selectedCandidateId")
            selected = next((item for item in candidates if item.get("id") == selected_id), None)
            if not selected:
                raise RuntimeError(f"adaptive search did not select a formal candidate: {repair}")
            qualification = (selected.get("variableSummary") or {}).get("topologyQualification") or {}
            controls = qualification.get("controlMetrics") or {}
            if not bool((selected.get("hardConstraints") or {}).get("passed")):
                raise RuntimeError(f"selected candidate failed hard constraints: {selected}")
            if float(selected.get("targetSpacing") or 0.0) != 4.5:
                raise RuntimeError(f"expected 4.5 m transition candidate, got: {selected.get('targetSpacing')}")
            if int(controls.get("supportStationClusterCount") or 0) != 0:
                raise RuntimeError(f"station clustering remains: {controls}")

            calculation_started = time.perf_counter()
            calculation_task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={"operation": "calculation_full", "payload": {"topN": 0}},
                ),
                "submit formal calculation",
            ).json()
            calculation_task = wait_task(client, calculation_task, 300.0)
            calculation_seconds = time.perf_counter() - calculation_started

            calculated = require(
                client.get(f"/api/projects/{project_id}?profile=workspace"),
                "read calculated workspace",
            ).json()
            calculated_retaining = calculated.get("retainingSystem") or {}
            results = list(calculated.get("calculationResults") or [])
            summary = {
                "status": "success",
                "elapsedSeconds": round(time.perf_counter() - started, 3),
                "candidateSearchSeconds": round(search_seconds, 3),
                "calculationSeconds": round(calculation_seconds, 3),
                "projectId": project_id,
                "candidateCount": len(candidates),
                "selectedCandidateId": selected_id,
                "selectedTargetSpacingM": selected.get("targetSpacing"),
                "candidateContractVersion": (selected.get("variableSummary") or {}).get(
                    "candidateContractVersion"
                ),
                "supportStationClusterCount": controls.get("supportStationClusterCount"),
                "supportCount": len(calculated_retaining.get("supports") or []),
                "columnCount": len(calculated_retaining.get("columns") or []),
                "calculationResultCount": len(results),
                "taskLogTail": list(calculation_task.get("logs") or [])[-8:],
            }
            if summary["candidateContractVersion"] != SUPPORT_CANDIDATE_CONTRACT_VERSION:
                raise RuntimeError(f"candidate contract mismatch: {summary}")
            if summary["supportCount"] <= 0 or not results:
                raise RuntimeError(f"formal calculation did not close: {summary}")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
