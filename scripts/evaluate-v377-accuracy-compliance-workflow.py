#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.support_layout import SupportLayoutConfig
from app.services.verification_matrix_v377 import run_v377_verification_matrix
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.77 implementation evaluation",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    project = Project(name="V3.77 implementation evaluation", excavation=excavation, retainingSystem=system)
    project.calculation_cases = build_default_construction_cases(project)
    return project


def _blocking_categories(result: Any) -> list[str]:
    gate = result.formal_report_gate
    if gate is None:
        return []
    return sorted({str(item.category) for item in gate.blocking_items})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "releases" / "V3_77_0_IMPLEMENTATION_EVALUATION.json",
    )
    args = parser.parse_args()

    project = _project()
    started = time.perf_counter()
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    elapsed = time.perf_counter() - started
    verification = run_v377_verification_matrix()
    docs = build_online_documentation()

    execution = dict(result.calculation_execution or {})
    health = dict(result.numerical_health or {})
    completeness = dict(result.result_completeness or {})
    catalog = dict(result.result_catalog or {})
    analysis = dict(result.analysis_assurance or {})
    geotech = dict(result.geotechnical_assurance or {})
    spatial = dict(result.spatial_verification or {})
    statutory = dict(result.statutory_workflow_assurance or {})
    transaction = dict((project.advanced_engineering or {}).get("lastCalculationTransaction") or {})
    transfer = dict((project.retaining_system.layout_summary or {}).get("transferSystem") or {})

    payload = {
        "schema": "pitguard-v3.77-implementation-evaluation-v1",
        "release": version_manifest(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "elapsedSeconds": round(elapsed, 3),
        },
        "project": {
            "shape": "L orthogonal concave",
            "depthM": 16.0,
            "topologyClass": transfer.get("topologyClass"),
            "stageCount": len(project.calculation_cases[0].stages),
            "supportCount": len(project.retaining_system.supports),
            "transferBeamCount": len(project.retaining_system.ring_beams),
            "columnCount": len(project.retaining_system.columns),
        },
        "result": {
            "engineeringStatus": execution.get("engineeringStatus"),
            "deliveryStatus": execution.get("deliveryStatus"),
            "transactionStatus": transaction.get("status"),
            "numericalHealthStatus": health.get("status"),
            "maxScaledConditionNumber": health.get("maximumScaledConditionNumber"),
            "maxRelativeResidual": health.get("maximumRelativeResidual"),
            "analysisAssuranceStatus": analysis.get("status"),
            "analysisFormalIssueEligible": analysis.get("formalIssueEligible"),
            "fallbackCount": analysis.get("fallbackCount"),
            "lowLevelDomains": [row.get("domain") for row in analysis.get("lowLevelDomains") or []],
            "geotechnicalAssuranceStatus": geotech.get("status"),
            "geotechnicalFormalUseAllowed": geotech.get("formalUseAllowed"),
            "missingGeotechnicalParameters": geotech.get("missingCriticalParameters") or [],
            "spatialVerificationStatus": spatial.get("status"),
            "spatialScaledConditionNumber": (spatial.get("numericalGate") or {}).get("scaledConditionNumber"),
            "spatialRelativeResidual": spatial.get("relativeEquilibriumResidual"),
            "spatialMaxDifference": (spatial.get("planarComparison") or {}).get("maximumRelativeDifference"),
            "verificationMatrixStatus": verification.get("status"),
            "externalBenchmarkStatus": verification.get("externalReferenceStatus"),
            "statutoryWorkflowStatus": statutory.get("status"),
            "statutoryFormalIssueEligible": statutory.get("formalIssueEligible"),
            "missingStatutoryEvidence": statutory.get("missingRequiredEvidence") or [],
            "formalGateStatus": result.formal_report_gate.status if result.formal_report_gate else None,
            "formalIssueAllowed": bool(result.formal_report_gate and result.formal_report_gate.allowed_for_official_issue),
            "engineeringCompletenessPercent": completeness.get("engineeringCompletenessPercent"),
            "engineeringReadinessPercent": completeness.get("engineeringReadinessPercent"),
            "formalIssueReadinessPercent": completeness.get("formalIssueReadinessPercent"),
            "resultCatalogSchema": catalog.get("schema"),
            "resultDomainCount": completeness.get("domainCount"),
            "uncertaintyCaseCount": len(geotech.get("uncertaintyCases") or []),
            "analysisDomainCount": len(analysis.get("domains") or []),
            "statutoryRequirementCount": len(statutory.get("requirements") or []),
            "resourceCleanup": transaction.get("resourceCleanup") or {},
            "blockingCategories": _blocking_categories(result),
        },
        "verification": {
            "status": verification.get("status"),
            "internalReferenceStatus": verification.get("internalReferenceStatus"),
            "externalReferenceStatus": verification.get("externalReferenceStatus"),
            "formalExternalBenchmarkReady": verification.get("formalExternalBenchmarkReady"),
            "caseCount": verification.get("caseCount"),
        },
        "onlineDocumentation": {
            "version": docs.get("version"),
            "chapterIds": [row.get("id") for row in docs.get("chapters") or []],
            "analysisLevelCount": len(docs.get("analysisLevels") or []),
            "complianceSemanticCount": len(docs.get("complianceSemantics") or []),
            "roadmapCount": len(docs.get("releaseRoadmap") or []),
        },
        "limitations": [
            "Synthetic project intentionally has no verified boreholes, groundwater evidence, statutory evidence or licensed professional approval.",
            "OpenSeesPy availability is reported by the runtime verification matrix; no unavailable external equivalence is asserted.",
            "Nonlinear geotechnical assurance is a diagnostic and uncertainty layer and does not replace continuum finite-element analysis.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
