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
from app.services.design_basis import build_design_basis
from app.services.section_catalog import load_steel_support_catalog
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit one PitGuard project against V3.46 P0-P2 closure.")
    parser.add_argument("--project-id")
    parser.add_argument("--db", default=str(ROOT / "runtime" / "pitguard.sqlite3"))
    parser.add_argument("--output", default=str(ROOT / "runtime" / "diagnostics" / "p0-p2-project-audit.json"))
    args = parser.parse_args()
    os.environ["PITGUARD_DB_PATH"] = args.db
    repo = ProjectRepository(SQLiteProjectStore(args.db), default_actor="v3.46-audit")
    project_id = args.project_id
    if not project_id:
        summaries = repo.list_summaries()
        if not summaries:
            raise SystemExit("No project found")
        project_id = summaries[0].id
    project = repo.require(project_id)
    info = repo.store.get_payload_info(project_id) or {}
    status = build_core_workspace_status(project, info)
    latest = project.calculation_results[-1] if project.calculation_results else None
    report = {
        "projectId": project.id,
        "projectName": project.name,
        "p0": {
            "designBasis": build_design_basis(project),
            "blockers": status.get("blockers"),
            "storage": status.get("storage"),
            "verificationEvidence": (status.get("verificationDistribution") or {}).get("evidenceCoverage"),
            "runtimeDiagnosticsDirectory": str(Path(args.db).parent / "diagnostics"),
        },
        "p1": {
            "schemeComparison": status.get("schemeComparison"),
            "steelSectionCatalog": {
                "version": load_steel_support_catalog().get("catalogVersion"),
                "profileCount": len(load_steel_support_catalog().get("profiles") or []),
            },
            "rebarConstructability": ((project.retaining_system.rebar_design_scheme or {}).get("constructability") if project.retaining_system else None),
        },
        "p2": {
            "analysisModel": (status.get("designBasis") or {}).get("analysisModel"),
            "adverseScenarios": status.get("adverseScenarios"),
            "latestAnalysisContract": (latest.design_iteration_summary.get("analysisModelContract") if latest else None),
            "globalSystemCount": len((latest.report_diagram_data.get("globalCoupledSystems") or [])) if latest else 0,
            "localNodeCheckCount": sum(1 for row in (latest.checks or []) if "LOCAL-NODE" in str(row.get("ruleId") or "")) if latest else 0,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "ok", "projectId": project.id, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
