#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("PITGUARD_PROCESS_ROLE", "worker")

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.services.calculation_trace import build_calculation_trace
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def _candidate_summary(project: Any) -> dict[str, Any]:
    repair = getattr(getattr(project, "retaining_system", None), "support_layout_repair", None)
    candidates = list(getattr(repair, "candidates", None) or [])
    rows: list[dict[str, Any]] = []
    signatures: list[str] = []
    for candidate in candidates[:6]:
        summary = dict(candidate.variable_summary or {})
        signature = summary.get("actualGeometrySignature") or {}
        signatures.append(json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str))
        rows.append({
            "candidateId": candidate.id,
            "rank": candidate.rank,
            "topologyFamily": summary.get("topologyFamily"),
            "capabilityOutcome": summary.get("capabilityOutcome"),
            "formalSchemeEligible": summary.get("formalSchemeEligible", bool((candidate.hard_constraints or {}).get("passed"))),
            "hardPassed": bool((candidate.hard_constraints or {}).get("passed")),
            "supportCount": candidate.support_count,
            "columnCount": candidate.column_count,
            "targetSpacingM": candidate.target_spacing,
            "columnSpanM": candidate.column_max_span,
            "geometryDelta": summary.get("minimumGeometryDeltaToSelected"),
            "actualGeometrySignature": signature,
        })
    return {
        "candidateCount": len(candidates),
        "displayedCount": len(rows),
        "distinctGeometryCount": len(set(signatures)),
        "duplicateGeometryCount": max(0, len(signatures) - len(set(signatures))),
        "formalEligibleCount": sum(1 for row in rows if row["formalSchemeEligible"] and row["hardPassed"]),
        "diagnosticOnlyCount": sum(1 for row in rows if row["capabilityOutcome"] == "controlled_block"),
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PitGuard V3.48 candidate geometry, calculation trace and horizontal-support rebar integrity.")
    parser.add_argument("--project-id")
    parser.add_argument("--db", default=str(ROOT / "runtime" / "pitguard.sqlite3"))
    parser.add_argument("--output", default=str(ROOT / "runtime" / "diagnostics" / "design-rebar-integrity-audit.json"))
    parser.add_argument("--max-bars", type=int, default=1200)
    args = parser.parse_args()

    os.environ["PITGUARD_DB_PATH"] = args.db
    repo = ProjectRepository(SQLiteProjectStore(args.db), default_actor="v3.48-integrity-audit")
    project_id = args.project_id
    if not project_id:
        summaries = repo.list_summaries()
        if not summaries:
            raise SystemExit("No project found")
        project_id = summaries[0].id
    project = repo.require(project_id)

    visualization_error = None
    try:
        visualization = build_rebar_ifc_visualization(project, max_bars=max(100, args.max_bars))
    except Exception as exc:  # audit must still report the failure
        visualization = {"summary": {}, "supportContracts": []}
        visualization_error = f"{type(exc).__name__}: {exc}"

    trace_error = None
    try:
        trace = build_calculation_trace(project)
    except Exception as exc:
        trace = {"entries": []}
        trace_error = f"{type(exc).__name__}: {exc}"

    summary = dict(visualization.get("summary") or {})
    contracts = list(visualization.get("supportContracts") or [])
    report = {
        "projectId": project.id,
        "projectName": project.name,
        "candidateIntegrity": _candidate_summary(project),
        "calculationTrace": {
            "status": "ok" if trace_error is None else "failed",
            "entryCount": len(trace.get("entries") or []),
            "error": trace_error,
        },
        "horizontalSupportRebar": {
            "status": "ok" if visualization_error is None else "failed",
            "error": visualization_error,
            "typesPresent": summary.get("supportBarTypesPresent") or [],
            "typesExpected": summary.get("supportBarTypesExpected") or [],
            "typesMissing": summary.get("supportBarTypesMissing") or [],
            "completeSupportCount": summary.get("supportContractCompleteCount", 0),
            "incompleteSupportCount": summary.get("supportContractIncompleteCount", 0),
            "contractCount": len(contracts),
            "incompleteContracts": [row for row in contracts if row.get("status") != "complete"][:50],
        },
        "calculationState": dict((project.advanced_engineering or {}).get("calculationState") or {}),
        "recommendations": [
            "A/B/C 正式比选只允许使用硬约束通过且实际几何签名不同的候选。",
            "受控阻断候选可展示真实差异，但必须禁用采用、完整计算和正式出图。",
            "水平支撑正式可视化应同时包含 longitudinal、distribution、stirrup、tie、additional 五类钢筋。",
            "若支撑截面因配筋调整发生变化，应执行一次受控重算并重新生成配筋，禁止保留旧内力包络。",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "ok", "projectId": project.id, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
