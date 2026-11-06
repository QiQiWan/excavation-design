from __future__ import annotations

"""Bounded-memory smoke test for the 230 m stepped-strip sample project.

The script uses a temporary database by default and exercises candidate search
plus candidate adoption in one process so ``ru_maxrss`` captures a conservative
upper bound. It does not modify the user's project database.
"""

import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
SAMPLE_DIR = ROOT / "packages" / "sample-data" / "actual-project"
CSV_PATH = SAMPLE_DIR / "actual_project_boreholes_24x6layers.csv"
EXCAVATION_PATH = SAMPLE_DIR / "actual_project_excavation_payload.json"


def _require(response: Any, label: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:1200]}")
    return response


def _wait(client: Any, task: dict[str, Any], timeout_seconds: float = 240.0) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while task.get("status") not in {"success", "failed", "cancelled", "interrupted"}:
        if time.time() > deadline:
            raise TimeoutError(f"task exceeded {timeout_seconds:.0f}s: {task.get('id')}")
        time.sleep(0.2)
        task = _require(client.get(f"/api/tasks/{task['id']}"), "read task").json()
    if task.get("status") != "success":
        raise RuntimeError(str(task.get("error") or task))
    return task


def _peak_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return round(value / 1048576.0, 2)
    if os.name == "posix":
        return round(value / 1024.0, 2)
    return round(value / 1048576.0, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded candidate-search memory smoke test")
    parser.add_argument("--keep-runtime", action="store_true", help="copy diagnostics into runtime/v344-memory-smoke")
    args = parser.parse_args()

    temp = tempfile.TemporaryDirectory(prefix="pitguard-v344-memory-")
    temp_root = Path(temp.name)
    os.environ["PITGUARD_DB_PATH"] = str(temp_root / "pitguard.sqlite3")
    os.environ["PITGUARD_TASK_EXECUTION_MODE"] = "embedded"
    os.environ["PITGUARD_PRODUCT_MODE"] = "core"
    os.environ["PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT"] = "9"
    os.environ["PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT"] = "6"
    os.environ["PITGUARD_RUNTIME_DIAGNOSTICS"] = "1"
    os.environ["PITGUARD_NUMERIC_THREADS"] = "1"
    sys.path.insert(0, str(API_DIR))

    from fastapi.testclient import TestClient
    from app.main import app

    excavation_payload = json.loads(EXCAVATION_PATH.read_text(encoding="utf-8"))
    started = time.perf_counter()
    with TestClient(app) as client:
        project = _require(client.post("/api/projects", json={"name": "V3.44 candidate-memory smoke"}), "create project").json()
        project_id = project["id"]
        with CSV_PATH.open("rb") as stream:
            _require(
                client.post(
                    f"/api/projects/{project_id}/boreholes/import-csv",
                    files={"file": (CSV_PATH.name, stream, "text/csv")},
                ),
                "import boreholes",
            )
        _require(client.post(f"/api/projects/{project_id}/excavation", json=excavation_payload), "create excavation")
        current = _require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read project before basis").json()
        settings = dict(current.get("designSettings") or {})
        settings["designBasisConfirmed"] = True
        _require(client.patch(f"/api/projects/{project_id}/workspace?actor=v348-smoke", json={"designSettings": settings}), "confirm design basis")
        task = _require(
            client.post(
                f"/api/projects/{project_id}/tasks",
                json={
                    "operation": "support_layout_optimization",
                    "payload": {"preset": "balanced", "maxCandidates": 3, "searchConfig": {"requireDiverseSchemes": True}},
                },
            ),
            "submit candidate search",
        ).json()
        _wait(client, task)
        workspace = _require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read workspace").json()
        repair = ((workspace.get("retainingSystem") or {}).get("supportLayoutRepair") or {})
        candidates = list(repair.get("candidates") or [])
        formal_candidates = [
            row for row in candidates
            if bool((row.get("hardConstraints") or {}).get("passed"))
            and (row.get("variableSummary") or {}).get("formalSchemeEligible", True) is not False
        ]
        if formal_candidates:
            adoption = _require(
                client.post(
                    f"/api/projects/{project_id}/tasks",
                    json={
                        "operation": "adopt_support_candidate",
                        "payload": {"candidateId": formal_candidates[0]["id"]},
                    },
                ),
                "submit candidate adoption",
            ).json()
            _wait(client, adoption)
        workspace = _require(client.get(f"/api/projects/{project_id}?profile=workspace"), "read adopted workspace").json()
        storage = _require(client.get(f"/api/projects/{project_id}/storage-health"), "read storage").json()
        retaining = workspace.get("retainingSystem") or {}
        summary = {
            "status": "success",
            "elapsedSeconds": round(time.perf_counter() - started, 3),
            "candidateCount": len(candidates),
            "formalCandidateCount": len(formal_candidates),
            "diagnosticCandidateCount": len(candidates) - len(formal_candidates),
            "currentSupportCount": len(retaining.get("supports") or []),
            "currentColumnCount": len(retaining.get("columns") or []),
            "firstCandidateSupportCount": int(candidates[0].get("supportCount") or 0) if candidates else 0,
            "firstCandidateColumnCount": int(candidates[0].get("columnCount") or 0) if candidates else 0,
            "payloadMb": round(float(storage.get("payloadBytes") or 0) / 1048576.0, 2),
            "workspaceMb": round(float(storage.get("workspaceBytes") or 0) / 1048576.0, 2),
            "peakRssMb": _peak_rss_mb(),
            "diagnosticsDirectory": str(temp_root / "diagnostics"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.keep_runtime:
        target = ROOT / "runtime" / "v344-memory-smoke"
        if target.exists():
            shutil.rmtree(target)
        source = temp_root / "diagnostics"
        destination = target / "diagnostics"
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
        print(f"Diagnostics copied to: {target}")
    temp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
