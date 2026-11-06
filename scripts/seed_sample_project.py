from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "packages/sample-data/projects"
OUTPUT_DIR = ROOT / "sample-output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

sample_db = OUTPUT_DIR / "sample.sqlite3"
if sample_db.exists():
    sample_db.unlink()
os.environ["PITGUARD_DB_PATH"] = str(sample_db)
sys.path.insert(0, str(ROOT / "services/api"))

from app.main import app  # noqa: E402


def require_ok(response, label: str):
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text}")
    return response


def write_response(path: Path, response) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def main() -> None:
    client = TestClient(app)
    response = require_ok(client.post("/api/projects", json={"name": "PitGuard 完整流程示例项目", "location": "示例城市 CBD 深基坑"}), "create project")
    project_id = response.json()["id"]

    sample_csv = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"
    with sample_csv.open("rb") as f:
        require_ok(client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (sample_csv.name, f, "text/csv")}), "import boreholes")

    require_ok(client.post(f"/api/projects/{project_id}/geology/build-model?grid_size=10"), "build geology")

    sample_vtu = ROOT / "packages/sample-data/vtu/sample.vtu"
    with sample_vtu.open("rb") as f:
        require_ok(client.post(f"/api/projects/{project_id}/geology/import-vtu", files={"file": (sample_vtu.name, f, "application/octet-stream")}), "import vtu")

    require_ok(
        client.post(
            f"/api/projects/{project_id}/excavation",
            json={
                "name": "示例矩形深基坑",
                "topElevation": 0.0,
                "bottomElevation": -12.0,
                "outline": {"closed": True, "points": [{"x": 5, "y": 5}, {"x": 55, "y": 5}, {"x": 55, "y": 35}, {"x": 5, "y": 35}]},
            },
        ),
        "create excavation",
    )
    require_ok(client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall"), "auto wall")
    require_ok(client.post(f"/api/projects/{project_id}/design/auto-supports"), "auto supports")
    require_ok(client.post(f"/api/projects/{project_id}/calculation/build-cases"), "build cases")
    calc = require_ok(client.post(f"/api/projects/{project_id}/calculation/run"), "run calculation").json()
    assurance = require_ok(client.get(f"/api/projects/{project_id}/assurance/gap-analysis"), "assurance gap analysis").json()

    project_json = require_ok(client.post(f"/api/projects/{project_id}/export/json"), "export json")
    project_ifc = require_ok(client.post(f"/api/projects/{project_id}/export/ifc"), "export ifc")
    project_docx = require_ok(client.post(f"/api/projects/{project_id}/export/report"), "export docx")

    json_path = SAMPLE_DIR / "sample_full_project.json"
    ifc_path = SAMPLE_DIR / "sample_full_project.ifc"
    docx_path = SAMPLE_DIR / "sample_full_project_calculation_report.docx"
    write_response(json_path, project_json)
    write_response(ifc_path, project_ifc)
    write_response(docx_path, project_docx)

    # Also copy to sample-output for quick inspection without modifying packaged sample data manually.
    shutil.copy2(json_path, OUTPUT_DIR / json_path.name)
    shutil.copy2(ifc_path, OUTPUT_DIR / ifc_path.name)
    shutil.copy2(docx_path, OUTPUT_DIR / docx_path.name)

    checks = calc.get("checks", [])
    summary = {
        "project_id": project_id,
        "stage_result_count": len(calc.get("stageResults", [])),
        "check_count": len(checks),
        "pass_count": sum(1 for c in checks if c.get("status") == "pass"),
        "warning_count": sum(1 for c in checks if c.get("status") == "warning"),
        "fail_count": sum(1 for c in checks if c.get("status") == "fail"),
        "manual_review_count": sum(1 for c in checks if c.get("status") == "manual_review"),
        "completion_percent": assurance.get("completionPercent"),
        "closed_loop_complete": assurance.get("closedLoopComplete"),
        "assurance": assurance,
        "json": str(json_path.relative_to(ROOT)),
        "ifc": str(ifc_path.relative_to(ROOT)),
        "docx": str(docx_path.relative_to(ROOT)),
    }
    (SAMPLE_DIR / "sample_full_project_summary.json").write_text(__import__("json").dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(__import__("json").dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
