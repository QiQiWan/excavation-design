#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.storage.database import _compact_candidate_plan_geometry
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def main() -> None:
    started = time.perf_counter()
    excavation = make_excavation_model(
        "V3.87.1 L-shape preview",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    project = Project(name="V3.87.1 L-shape preview", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    _, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        search_config={
            "requireDiverseSchemes": True,
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
        },
    )
    rows = []
    for candidate in candidates:
        original = candidate.plan_geometry or {}
        compact = _compact_candidate_plan_geometry(original)
        rows.append({
            "rank": candidate.rank,
            "candidateId": candidate.id,
            "template": candidate.variable_summary.get("transferSystemTemplate"),
            "supportCount": len(original.get("supports") or []),
            "transferBeamCount": len(original.get("transferBeams") or []),
            "transferZoneCount": len(original.get("transferZones") or []),
            "compactPreviewSchema": compact.get("previewSchema"),
            "compactTransferBeamCount": len(compact.get("transferBeams") or []),
            "compactTransferZoneCount": len(compact.get("transferZones") or []),
            "closedGeometryRetained": len(compact.get("transferBeams") or []) == len(original.get("transferBeams") or []) and bool(compact.get("transferZones")),
        })
    result_viewer = (ROOT / "apps/web/src/viewers/ResultViewer.tsx").read_text(encoding="utf-8")
    main_source = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    payload = {
        "schema": "pitguard-v3871-ui-topology-evaluation-v1",
        "version": version_manifest(),
        "durationSeconds": round(time.perf_counter() - started, 3),
        "candidateCount": len(candidates),
        "candidates": rows,
        "frontend": {
            "supplementalStylesLoaded": "import './styles.css';" in main_source,
            "statusTextDeclarationCount": result_viewer.count("function statusText("),
            "optimizationStatusFunctionPresent": "function optimizationStatusText(" in result_viewer,
            "panelBoundaryPresent": (ROOT / "apps/web/src/app/PanelErrorBoundary.tsx").exists(),
        },
        "passed": len(candidates) == 3 and all(row["closedGeometryRetained"] for row in rows) and result_viewer.count("function statusText(") == 1,
    }
    output = ROOT / "docs/releases/V3_87_1_UI_TOPOLOGY_EVALUATION.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
