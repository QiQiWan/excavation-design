#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("PITGUARD_PROCESS_ROLE", "worker")

from app.services.core_workspace import build_core_workspace_status
from app.services.enterprise_library import validate_enterprise_library
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit one PitGuard project against V3.47 P3-1 to P3-3 closure.")
    parser.add_argument("--project-id")
    parser.add_argument("--db", default=str(ROOT / "runtime" / "pitguard.sqlite3"))
    parser.add_argument("--output", default=str(ROOT / "runtime" / "diagnostics" / "p3-project-audit.json"))
    args = parser.parse_args()
    os.environ["PITGUARD_DB_PATH"] = args.db
    repo = ProjectRepository(SQLiteProjectStore(args.db), default_actor="v3.47-p3-audit")
    project_id = args.project_id
    if not project_id:
        summaries = repo.list_summaries()
        if not summaries:
            raise SystemExit("No project found")
        project_id = summaries[0].id
    project = repo.require(project_id)
    info = repo.store.get_payload_info(project_id) or {}
    status = build_core_workspace_status(project, info)
    formal = dict(status.get("formalAdverseScenarioSuite") or {})
    detailing = dict(status.get("p3DetailingClosure") or {})
    report = {
        "projectId": project.id,
        "projectName": project.name,
        "p3_1_formalAdverseScenarios": {
            "catalog": status.get("adverseScenarioCatalog"),
            "summary": formal.get("summary"),
            "scenarioResults": formal.get("summaries"),
            "errors": formal.get("errors"),
            "artifact": formal.get("artifact"),
        },
        "p3_2_enterpriseResources": {
            "validation": validate_enterprise_library(project),
            "libraries": status.get("enterpriseLibraries"),
            "selection": (status.get("designBasis") or {}).get("enterprise", {}).get("selection"),
        },
        "p3_3_detailingClosure": {
            "status": detailing.get("status"),
            "summary": detailing.get("summary"),
            "controllingChecks": detailing.get("controllingChecks"),
            "nodeTemplateAssignments": detailing.get("nodeTemplateAssignments"),
            "artifact": detailing.get("artifact"),
        },
        "storage": status.get("storage"),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "ok", "projectId": project.id, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
