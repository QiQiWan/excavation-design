from __future__ import annotations

from typing import Any

from app.schemas.domain import Project
from app.services.collision_service import evaluate_model_collisions
from app.services.monitoring_calibration import monitoring_summary
from app.services.node_local_analysis import evaluate_node_local_response
from app.services.review_workflow import review_status
from app.services.serviceability_service import evaluate_long_term_serviceability
from app.services.support_topology_graph import analyze_support_topology


def build_advanced_engineering_suite(project: Project, mode: str = "balanced") -> dict[str, Any]:
    serviceability = evaluate_long_term_serviceability(project, mode)
    topology = analyze_support_topology(project)
    collisions = evaluate_model_collisions(project, mode)
    nodes = evaluate_node_local_response(project)
    review = review_status(project)
    monitoring = monitoring_summary(project)
    modules = {
        "serviceability": serviceability, "topology": topology, "collisions": collisions,
        "nodeLocal": nodes, "monitoring": monitoring, "review": review,
    }
    statuses = [m.get("status") for m in (serviceability, topology, collisions, nodes)]
    current_snapshot = review.get("currentSnapshotHash")
    current_construction_revision = next((r for r in reversed(project.drawing_revisions) if r.issue_status == "construction" and r.snapshot_hash == current_snapshot), None)
    overall = "fail" if "fail" in statuses else "warning" if "warning" in statuses or not review.get("approvalValid") else "pass"
    return {
        "status": overall,
        "summary": {
            "moduleCount": 8,
            "engineeringFailModules": sum(s == "fail" for s in statuses),
            "engineeringWarningModules": sum(s == "warning" for s in statuses),
            "approvalValid": review.get("approvalValid"),
            "monitoringRecordCount": monitoring.get("recordCount", 0),
        },
        **modules,
        "formalDrawings": {
            "status": "construction_ready" if overall != "fail" and review.get("approvalValid") and current_construction_revision else "review_only",
            "supportsBatchPdf": True, "dwgConversion": "external_oda_or_autocad", "revisionTracking": True,
            "constructionRevisionValid": bool(current_construction_revision),
            "currentConstructionRevision": current_construction_revision.revision if current_construction_revision else None,
            "requiresApprovedSnapshotRevision": True,
        },
        "ux": {"compactByDefault": True, "keyboardNavigation": True, "commandPalette": True, "autosaveDraft": True, "reducedMotion": True},
    }
