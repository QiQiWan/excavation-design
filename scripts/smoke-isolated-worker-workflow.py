from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
CSV_PATH = ROOT / "packages" / "sample-data" / "boreholes" / "sample_boreholes.csv"


def require(response, label: str):
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:1000]}")
    return response


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pitguard-isolated-smoke-") as temp_dir:
        temp = Path(temp_dir)
        db_path = temp / "pitguard.sqlite3"
        heartbeat = temp / "worker-heartbeat.json"
        supervisor_pid = temp / "worker-supervisor.pid"
        env = dict(os.environ)
        env.update({
            "PITGUARD_DB_PATH": str(db_path),
            "PITGUARD_TASK_EXECUTION_MODE": "external",
            "PITGUARD_PROCESS_ROLE": "api",
            "PITGUARD_PRODUCT_MODE": "core",
            "PITGUARD_NUMERIC_THREADS": "1",
            "PITGUARD_WORKER_HEARTBEAT_PATH": str(heartbeat),
            "PITGUARD_WORKER_SUPERVISOR_PID": str(supervisor_pid),
            "PYTHON_BIN": sys.executable,
            "PYTHONPATH": str(API_DIR),
        })
        os.environ.update(env)
        sys.path.insert(0, str(API_DIR))
        supervisor = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "run-worker-supervisor.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.time() + 20
            while not heartbeat.exists():
                if supervisor.poll() is not None:
                    raise RuntimeError((supervisor.stdout.read() if supervisor.stdout else "worker supervisor exited"))
                if time.time() > deadline:
                    raise TimeoutError("worker heartbeat not created")
                time.sleep(0.2)

            from fastapi.testclient import TestClient
            from app.main import app

            started = time.perf_counter()
            with TestClient(app) as client:
                project = require(client.post("/api/projects", json={"name": "isolated worker smoke", "designSettings": {"designBasisConfirmed": True, "bearingCapacityKpa": 180}}), "create project").json()
                project_id = project["id"]
                with CSV_PATH.open("rb") as stream:
                    require(client.post(
                        f"/api/projects/{project_id}/boreholes/import-csv",
                        files={"file": (CSV_PATH.name, stream, "text/csv")},
                    ), "import boreholes")
                require(client.post(f"/api/projects/{project_id}/excavation", json={
                    "name": "isolated excavation",
                    "topElevation": 0.0,
                    "bottomElevation": -10.0,
                    "outline": {"closed": True, "points": [
                        {"x": 5.0, "y": 5.0}, {"x": 45.0, "y": 5.0},
                        {"x": 45.0, "y": 25.0}, {"x": 5.0, "y": 25.0},
                    ]},
                }), "create excavation")
                task = require(client.post(f"/api/projects/{project_id}/tasks", json={
                    "operation": "core_design",
                    "payload": {"maxCandidates": 3, "rebarMode": "balanced"},
                }), "submit core task").json()
                deadline = time.time() + 120
                while task["status"] not in {"success", "failed", "cancelled", "interrupted"}:
                    if time.time() > deadline:
                        raise TimeoutError("isolated core task exceeded 120 seconds")
                    time.sleep(0.25)
                    task = require(client.get(f"/api/tasks/{task['id']}"), "poll task").json()
                if task["status"] != "success":
                    raise RuntimeError(task.get("error") or task["status"])
                metrics = require(client.get("/api/task-metrics"), "task metrics").json()
                print(json.dumps({
                    "status": task["status"],
                    "elapsedSeconds": round(time.perf_counter() - started, 3),
                    "taskExecutionMode": metrics.get("taskExecutionMode"),
                    "workerHeartbeat": metrics.get("workerHeartbeat"),
                    "lastLogs": task.get("logs", [])[-8:],
                }, ensure_ascii=False, indent=2))
        finally:
            supervisor.terminate()
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                supervisor.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
