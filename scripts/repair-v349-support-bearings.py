#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("PITGUARD_PROCESS_ROLE", "worker")

from app.services.support_layout import normalize_existing_support_wall_connections
from app.services.design_qualification import build_design_qualification
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair V3.48-and-earlier support endpoints attached to tangent return walls at concave corners."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--db", default=str(ROOT / "runtime" / "pitguard.sqlite3"))
    parser.add_argument("--apply", action="store_true", help="Persist the repair. Default is dry-run.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "runtime" / "diagnostics" / "v349-support-bearing-repair.json"),
    )
    args = parser.parse_args()

    os.environ["PITGUARD_DB_PATH"] = args.db
    repo = ProjectRepository(SQLiteProjectStore(args.db), default_actor="v3.49-support-bearing-repair")
    project = repo.require(args.project_id)
    before = build_design_qualification(project, topology_detail="full")
    repair = normalize_existing_support_wall_connections(project)
    after = build_design_qualification(project, topology_detail="full")

    saved = False
    if args.apply and repair.get("changed"):
        # Existing calculation evidence is no longer valid after a geometry repair.
        project.calculation_cases = []
        project.calculation_results = []
        repo.save(
            project,
            action="project.v349_support_bearing_repair",
            summary="Normalized support wall bearings at stepped/concave returns and invalidated stale calculations",
        )
        saved = True

    report = {
        "projectId": project.id,
        "projectName": project.name,
        "mode": "apply" if args.apply else "dry-run",
        "saved": saved,
        "repair": repair,
        "before": {
            "status": before.get("status"),
            "blockerCount": len(before.get("blockers") or []),
            "blockers": before.get("blockers") or [],
        },
        "after": {
            "status": after.get("status"),
            "blockerCount": len(after.get("blockers") or []),
            "blockers": after.get("blockers") or [],
        },
        "nextAction": (
            "重新生成 A/B/C 并执行当前方案计算。"
            if saved or not repair.get("changed")
            else "确认 dry-run 结果后使用 --apply 保存修复，再重新生成 A/B/C。"
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output), **report}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
