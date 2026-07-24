#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_core_v387 import build_parameter_confirmation, ensure_parameter_provenance
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.storage.database import CANDIDATE_PREVIEW_SCHEMA, _compact_candidate_plan_geometry
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def main() -> None:
    started = time.perf_counter()
    excavation = make_excavation_model(
        "V3.87.2 L-shape integrity",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    project = Project(name="V3.87.2 L-shape integrity", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
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
            "columnCount": len(original.get("columns") or []),
            "transferBeamCount": len(original.get("transferBeams") or []),
            "transferZoneCount": len(original.get("transferZones") or []),
            "compactPreviewSchema": compact.get("previewSchema"),
            "previewIntegrity": compact.get("previewIntegrity"),
            "compactSupportCount": len(compact.get("supports") or []),
            "compactColumnCount": len(compact.get("columns") or []),
            "compactTransferBeamCount": len(compact.get("transferBeams") or []),
            "compactTransferZoneCount": len(compact.get("transferZones") or []),
            "closedGeometryRetained": (
                len(compact.get("transferBeams") or []) == len(original.get("transferBeams") or [])
                and len(compact.get("supports") or []) == len(original.get("supports") or [])
                and bool(compact.get("transferZones"))
            ),
        })

    ensure_parameter_provenance(project)
    parameter_governance = build_parameter_confirmation(project)
    surcharge = next(row for row in parameter_governance["records"] if row["parameterKey"] == "design.surcharge")
    importance = next(row for row in parameter_governance["records"] if row["parameterKey"] == "design.importance_factor")

    main_source = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    active_styles = (ROOT / "apps/web/src/app/styles.css").read_text(encoding="utf-8")
    result_viewer = (ROOT / "apps/web/src/viewers/ResultViewer.tsx").read_text(encoding="utf-8")
    candidate_sanitizer = (ROOT / "apps/web/src/drawing/candidateGeometry.ts").read_text(encoding="utf-8")
    router = (ROOT / "services/api/app/routers/design_core.py").read_text(encoding="utf-8")

    payload = {
        "schema": "pitguard-v3872-integrity-evaluation-v1",
        "version": version_manifest(),
        "durationSeconds": round(time.perf_counter() - started, 3),
        "candidateCount": len(candidates),
        "candidates": rows,
        "frontend": {
            "singleStylesheetEntry": "import './app/styles.css';" in main_source and "import './styles.css';" not in main_source,
            "designCoreCssPresent": ".designCorePanel{" in active_styles and ".designCoreStages{" in active_styles,
            "statusTextDeclarationCount": result_viewer.count("function statusText("),
            "sharedCandidateSanitizer": "phantom members at" in candidate_sanitizer,
        },
        "parameters": {
            "softwareSuggestionFormalEligible": surcharge["sourceEligibleForFormalDesign"],
            "standardWithoutReferenceFormalEligible": importance["sourceEligibleForFormalDesign"],
            "formalBlockerCount": parameter_governance["formalBlockerCount"],
        },
        "api": {
            "bundleSchemaPresent": "pitguard-design-core-bundle-v3872" in router,
            "readEndpointSaveRemoved": "design_core.initialize_parameter_provenance" not in router,
        },
    }
    payload["passed"] = bool(
        len(candidates) == 3
        and all(row["closedGeometryRetained"] for row in rows)
        and all(row["compactPreviewSchema"] == CANDIDATE_PREVIEW_SCHEMA for row in rows)
        and all(row["previewIntegrity"]["status"] == "complete" for row in rows)
        and payload["frontend"]["singleStylesheetEntry"]
        and payload["frontend"]["designCoreCssPresent"]
        and payload["frontend"]["statusTextDeclarationCount"] == 1
        and payload["frontend"]["sharedCandidateSanitizer"]
        and not payload["parameters"]["softwareSuggestionFormalEligible"]
        and not payload["parameters"]["standardWithoutReferenceFormalEligible"]
        and payload["api"]["bundleSchemaPresent"]
        and payload["api"]["readEndpointSaveRemoved"]
    )
    output = ROOT / "docs/releases/V3_87_2_INTEGRITY_EVALUATION.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
