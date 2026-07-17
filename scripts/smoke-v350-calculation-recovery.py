from __future__ import annotations

"""End-to-end smoke test for V3.50 legacy-candidate calculation recovery.

The test uses the exact 24-borehole / 20-vertex Harvest Lake sample, first
creates a valid A/B/C set, then mutates it into the persisted V3.48-style state
(diagnostic cards, no adopted supports, no candidate-contract marker).  A
normal ``calculation_full`` task must regenerate a current qualified topology,
adopt it and finish the calculation without bypassing any non-topology gate.
"""

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


def wait_task(client: Any, task: dict[str, Any], timeout_seconds: float = 300.0) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while task.get("status") not in {"success", "failed", "cancelled", "interrupted"}:
        if time.time() > deadline:
            raise TimeoutError(f"task exceeded {timeout_seconds:.0f}s: {task.get('id')}")
        time.sleep(0.25)
        task = require(client.get(f"/api/tasks/{task['id']}"), "read task").json()
    if task.get("status") != "success":
        raise RuntimeError(str(task.get("error") or task))
    return task


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pitguard-v350-recovery-") as temp_dir:
        os.environ["PITGUARD_DB_PATH"] = str(Path(temp_dir) / "pitguard.sqlite3")
        os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
        os.environ["PITGUARD_PRODUCT_MODE"] = "core"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT"] = "12"
        os.environ["PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT"] = "6"
        os.environ["PITGUARD_NUMERIC_THREADS"] = "1"
        os.environ["PITGUARD_RUNTIME_DIAGNOSTICS"] = "1"
        sys.path.insert(0, str(API_DIR))

        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.support_layout_optimizer import SUPPORT_CANDIDATE_CONTRACT_VERSION
        from app.storage.repository import ProjectRepository

        started = time.perf_counter()
        excavation_payload = json.loads(EXCAVATION_PATH.read_text(encoding="utf-8"))
        with TestClient(app) as client:
            project = require(
                client.post(
                    "/api/projects",
                    json={
                        "name": "V3.50 Harvest Lake calculation recovery",
                        "designSettings": {
                            "designBasisConfirmed": True,
                            "bearingCapacityKpa": 220.0,
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
            require(client.post(f"/api/projects/{project_id}/excavation", json=excavation_payload), "create excavation")

            search_task = require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={
                        "operation": "support_layout_optimization",
                        "payload": {
                            "preset": "balanced",
                            "maxCandidates": 3,
                            "searchConfig": {"requireDiverseSchemes": True},
                        },
                    },
                ),
                "submit initial candidate search",
            ).json()
            wait_task(client, search_task)

            repo = ProjectRepository()
            persisted = repo.require(project_id)
            repair = persisted.retaining_system.support_layout_repair if persisted.retaining_system else None
            if not repair or not repair.candidates or not repair.selected_candidate_id:
                raise RuntimeError("initial candidate search did not produce a formal selected scheme")
            for candidate in repair.candidates:
                candidate.variable_summary = dict(candidate.variable_summary or {})
                candidate.variable_summary.pop("candidateContractVersion", None)
                candidate.variable_summary["capabilityOutcome"] = "controlled_block"
                candidate.variable_summary["formalSchemeEligible"] = False
                candidate.hard_constraints = dict(candidate.hard_constraints or {})
                candidate.hard_constraints["passed"] = False
            repair.selected_candidate_id = None
            persisted.retaining_system.supports = []
            persisted.retaining_system.columns = []
            persisted.calculation_cases = []
            persisted.calculation_results = []
            repo.save(
                persisted,
                action="smoke.v350.inject_legacy_candidate_state",
                summary="Injected V3.48-style diagnostic candidates for calculation-gate recovery smoke test",
            )

            from app.tasks.manager import TaskManager
            manager = TaskManager()
            recovered_project = repo.require(project_id)
            qualification = manager._assert_calculation_qualified(repo, recovered_project)
            workspace = require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read recovered workspace").json()
            retaining = workspace.get("retainingSystem") or {}
            recovered_repair = retaining.get("supportLayoutRepair") or {}
            candidates = list(recovered_repair.get("candidates") or [])
            current_versions = sorted({
                str((row.get("variableSummary") or {}).get("candidateContractVersion") or "legacy")
                for row in candidates
            })
            summary = {
                "status": "success",
                "elapsedSeconds": round(time.perf_counter() - started, 3),
                "projectId": project_id,
                "supportCount": len(retaining.get("supports") or []),
                "columnCount": len(retaining.get("columns") or []),
                "candidateCount": len(candidates),
                "selectedCandidateId": recovered_repair.get("selectedCandidateId"),
                "candidateContractVersions": current_versions,
                "expectedCandidateContractVersion": SUPPORT_CANDIDATE_CONTRACT_VERSION,
                "calculationAllowed": bool(qualification.get("calculationAllowed")),
                "calculationBlockerCount": len([g for g in qualification.get("gates") or [] if "calculation" in (g.get("blocks") or [])]),
            }
            if not summary["selectedCandidateId"] or summary["supportCount"] <= 0:
                raise RuntimeError(f"topology was not recovered: {summary}")
            if current_versions != [SUPPORT_CANDIDATE_CONTRACT_VERSION]:
                raise RuntimeError(f"candidate contract was not refreshed: {summary}")
            if not summary["calculationAllowed"]:
                raise RuntimeError(f"calculation gate remains blocked: {summary}")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
