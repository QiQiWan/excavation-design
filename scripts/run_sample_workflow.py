from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

OUT = ROOT / "sample-output"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["PITGUARD_DB_PATH"] = str(OUT / "sample-workflow.sqlite3")

from app.main import app  # noqa: E402

client = TestClient(app)


def require(resp, label: str):
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} failed: HTTP {resp.status_code}: {resp.text}")
    return resp


def main() -> None:
    log: list[dict] = []
    create = require(
        client.post(
            "/api/projects",
            json={
                "name": "PitGuard 全流程示例：矩形深基坑",
                "location": "示例场地 / 本地坐标",
                "designSettings": {
                    "safetyGrade": "二级",
                    "environmentGrade": "严格",
                    "groundwaterLevel": -1.5,
                    "groundwaterLevelInside": -12.0,
                    "surcharge": 20.0,
                    "minimumSegmentLength": 0.5,
                    "ruleSet": "jgj120_gbt50010_gb50007_gb50009_v0_2",
                    "pressureMethod": "active",
                    "waterSoilMethod": "separate",
                },
            },
        ),
        "create project",
    )
    project_id = create.json()["id"]
    log.append({"step": "create_project", "projectId": project_id})

    sample_csv = ROOT / "packages" / "sample-data" / "boreholes" / "sample_boreholes.csv"
    with sample_csv.open("rb") as f:
        data = require(client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (sample_csv.name, f, "text/csv")}), "import boreholes").json()
    log.append({"step": "import_boreholes", "boreholeCount": data["boreholeCount"], "stratumCount": data["stratumCount"], "warnings": data["warnings"]})

    geology = require(client.post(f"/api/projects/{project_id}/geology/build-model?grid_size=10"), "build geology").json()
    log.append({"step": "build_geology", "surfaceCount": len(geology.get("surfaces", [])), "warnings": geology.get("warnings", [])})

    sample_vtu = ROOT / "packages" / "sample-data" / "vtu" / "sample.vtu"
    with sample_vtu.open("rb") as f:
        vtu = require(client.post(f"/api/projects/{project_id}/geology/import-vtu", files={"file": (sample_vtu.name, f, "application/xml")}), "import vtu").json()
    log.append({"step": "import_vtu", "summary": vtu.get("summary"), "suggestedMapping": vtu.get("suggestedMapping")})

    excavation_payload = {
        "name": "Main rectangular pit",
        "topElevation": 0.0,
        "bottomElevation": -12.0,
        "outline": {"closed": True, "points": [{"x": 5, "y": 5}, {"x": 55, "y": 5}, {"x": 55, "y": 35}, {"x": 5, "y": 35}]},
    }
    exc = require(client.post(f"/api/projects/{project_id}/excavation", json=excavation_payload), "create excavation").json()
    log.append({"step": "create_excavation", "depth": exc["depth"], "segmentCount": len(exc["segments"]), "area": exc.get("area")})

    ret = require(client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall"), "auto diaphragm wall").json()
    log.append({"step": "auto_diaphragm_wall", "wallCount": len(ret.get("diaphragmWalls", []))})
    ret = require(client.post(f"/api/projects/{project_id}/design/auto-supports"), "auto supports").json()
    log.append({"step": "auto_supports", "supportCount": len(ret.get("supports", []))})

    cases = require(client.post(f"/api/projects/{project_id}/calculation/build-cases"), "build cases").json()
    log.append({"step": "build_cases", "caseCount": len(cases), "stageCount": len(cases[0].get("stages", [])) if cases else 0})
    calc = require(client.post(f"/api/projects/{project_id}/calculation/run"), "run calculation").json()
    log.append({"step": "run_calculation", "governingValues": calc.get("governingValues"), "stageResults": len(calc.get("stageResults", []))})

    project = require(client.get(f"/api/projects/{project_id}"), "get project").json()
    (OUT / "full_flow_project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    for endpoint, filename in [("ifc-light", "full_flow_coordination_light.ifc"), ("ifc-analysis", "full_flow_analysis_model.ifc"), ("ifc-detailed", "full_flow_design_detailed.ifc"), ("report", "full_flow_report.docx"), ("json", "full_flow_export.json")]:
        resp = require(client.post(f"/api/projects/{project_id}/export/{endpoint}"), f"export {endpoint}")
        (OUT / filename).write_bytes(resp.content)
        log.append({"step": f"export_{endpoint}", "file": filename, "bytes": len(resp.content)})

    ifc_check = require(client.post(f"/api/projects/{project_id}/export/ifc-check?mode=coordination_light"), "export ifc-check").json()
    (OUT / "full_flow_ifc_check.json").write_text(json.dumps(ifc_check, ensure_ascii=False, indent=2), encoding="utf-8")
    log.append({"step": "export_ifc_check", "status": ifc_check.get("status"), "score": ifc_check.get("score")})

    checks = require(client.get(f"/api/projects/{project_id}/calculation/checks"), "get checks").json()
    (OUT / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    log.append({"step": "checks", "summary": checks.get("summary")})

    (OUT / "run_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"projectId": project_id, "outputDir": str(OUT), "steps": len(log), "governingValues": calc.get("governingValues")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
