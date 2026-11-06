from __future__ import annotations

from pathlib import Path

from app.schemas.domain import BeamElement, ColumnElement, MaterialDefinition, Point2D, Polyline2D, Project, RetainingSystem, SectionDefinition
from app.routers.design_core import design_core_bundle, parameter_governance
from app.services.support_layout_optimizer import _geometry_fingerprint
from app.services.design_core_v387 import (
    build_parameter_confirmation,
    confirm_parameter_records,
    ensure_parameter_provenance,
)
from app.storage.database import (
    CANDIDATE_PREVIEW_SCHEMA,
    _compact_candidate_for_workspace,
    _compact_candidate_plan_geometry,
    _compact_result_for_workspace,
)


def _base_geometry() -> dict:
    return {
        "outline": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 8}, {"x": 0, "y": 8}],
        "supports": [
            {"id": "S-1", "role": "ring_strut", "start": {"x": 0, "y": 4}, "end": {"x": 3, "y": 4}},
            {"id": "S-2", "role": "ring_strut", "start": {"x": 10, "y": 4}, "end": {"x": 7, "y": 4}},
        ],
        "transferBeams": [
            {"id": "TR-1", "role": "transfer_ring_beam", "points": [{"x": 3, "y": 2}, {"x": 7, "y": 2}, {"x": 7, "y": 6}, {"x": 3, "y": 6}, {"x": 3, "y": 2}]}
        ],
        "layoutSummary": {
            "transferSystem": {
                "templateId": "compact_elbow_ring",
                "topologyClass": "closed_ring",
                "calculationReady": True,
                "readiness": {"geometryClosed": True, "loadPathClosed": True},
            }
        },
    }


def test_preview_v3_declares_integrity_and_truncation() -> None:
    complete = _compact_candidate_plan_geometry(_base_geometry())
    assert complete["previewSchema"] == CANDIDATE_PREVIEW_SCHEMA == "candidate-plan-v3"
    assert complete["previewIntegrity"]["status"] == "complete"
    assert complete["transferAudit"]["readiness"]["geometryClosed"] is True
    assert complete["renderedTransferBeamCount"] == complete["sourceTransferBeamCount"] == 1

    truncated = _compact_candidate_plan_geometry(_base_geometry(), max_supports=1)
    assert truncated["previewIntegrity"]["status"] == "warning"
    assert truncated["previewIntegrity"]["truncation"]["supports"] is True
    assert truncated["renderedSupportCount"] == 1
    assert truncated["sourceSupportCount"] == 2


def test_preview_v3_rejects_invalid_coordinates_instead_of_drawing_origin_phantoms() -> None:
    geometry = _base_geometry()
    geometry["supports"].append({"id": "BAD", "start": {"x": None, "y": 1}, "end": {"x": 4, "y": 4}})
    compact = _compact_candidate_plan_geometry(geometry)
    assert all(row["id"] != "BAD" for row in compact["supports"])
    assert compact["previewIntegrity"]["status"] == "incomplete"
    assert compact["previewIntegrity"]["invalidMemberCount"] >= 1


def test_workspace_keeps_compact_candidate_calculation_decision_evidence() -> None:
    summary = {
        "candidateId": "C-A",
        "maxSupportAxialForce": 1234.5,
        "maxDisplacement": 12.3,
        "decisionScore": 87.4,
        "recommendedByFullCalculation": True,
        "checkSummary": {"fail": 0, "warning": 2},
        "largeRows": [{"x": index} for index in range(1000)],
    }
    compact_result = _compact_result_for_workspace({
        "id": "R-1",
        "governingValues": {},
        "supportLayoutRepair": {"selectedCandidateId": "C-A", "candidateFullCalculations": [summary]},
        "calculationExecution": {"status": "completed", "phases": [{"phaseId": "P1", "label": "计算", "status": "pass", "durationSeconds": 1.2, "huge": "x" * 1000}]},
        "numericalHealth": {"status": "pass", "maximumRelativeResidual": 1e-12, "iterationHistory": list(range(1000))},
        "resultCompleteness": {"status": "warning", "engineeringReadinessPercent": 80, "domains": [{"domainId": "wall", "label": "墙", "status": "pass", "coveragePercent": 100, "evidence": {"count": 6, "large": list(range(1000))}}]},
        "resultCatalog": {"schema": "pitguard-result-catalog-v3", "counts": {"supportEnvelopes": 72}, "criticalStages": [{"stageId": "S1"}], "supportEnvelopes": [{"id": index} for index in range(1000)]},
        "reportDiagramData": {"candidateFullCalculationComparison": [summary]},
    })
    stored = compact_result["supportLayoutRepair"]["candidateFullCalculations"][0]
    assert stored["candidateId"] == "C-A"
    assert stored["maxSupportAxialForce"] == 1234.5
    assert "largeRows" not in stored
    assert compact_result["reportDiagramData"]["candidateFullCalculationComparison"][0]["decisionScore"] == 87.4
    assert compact_result["resultCatalog"]["workspaceSummaryOnly"] is True
    assert "supportEnvelopes" not in compact_result["resultCatalog"]
    assert compact_result["resultCatalog"]["counts"]["supportEnvelopes"] == 72
    assert "iterationHistory" not in compact_result["numericalHealth"]
    assert "large" not in compact_result["resultCompleteness"]["domains"][0]["evidence"]

    compact_candidate = _compact_candidate_for_workspace({
        "id": "C-A", "rank": 1, "planGeometry": _base_geometry(), "fullCalculation": summary,
    })
    assert compact_candidate["workspacePreviewAvailable"] is True
    assert compact_candidate["fullCalculation"]["recommendedByFullCalculation"] is True


def test_software_suggestion_cannot_be_bulk_promoted_to_formal_parameter() -> None:
    project = Project(name="parameter-source-guard")
    ensure_parameter_provenance(project)
    surcharge = next(row for row in project.parameter_provenance if row.parameter_key == "design.surcharge")
    assert surcharge.source_type == "software_suggestion"

    result = confirm_parameter_records(project, [{
        "parameterKey": "design.surcharge",
        "confirmationStatus": "confirmed",
        "formalDesignAllowed": True,
    }], actor="designer")
    assert result["rejectedCount"] == 1
    assert surcharge.formal_design_allowed is False
    governance = build_parameter_confirmation(project)
    row = next(item for item in governance["records"] if item["parameterKey"] == "design.surcharge")
    assert row["sourceEligibleForFormalDesign"] is False
    assert row["usableForFormalDesign"] is False

    result = confirm_parameter_records(project, [{
        "parameterKey": "design.surcharge",
        "sourceType": "owner_provided",
        "sourceReference": "建设单位设计条件单",
        "confirmationStatus": "confirmed",
        "formalDesignAllowed": True,
    }], actor="designer")
    assert result["rejectedCount"] == 0
    assert surcharge.formal_design_allowed is True




def test_geometry_fingerprint_distinguishes_column_layout_and_transfer_polyline() -> None:
    section = SectionDefinition(width=0.8, height=0.8, name="800x800")
    material = MaterialDefinition(name="Concrete", grade="C35")
    beam_a = BeamElement(
        code="TR-1", axis=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=5, y=0), Point2D(x=10, y=0)], closed=False),
        elevation=-3, section=section, material=material, beamRole="transfer_ring_beam", supportLevel=1,
    )
    beam_b = BeamElement(
        code="TR-1", axis=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=5, y=2), Point2D(x=10, y=0)], closed=False),
        elevation=-3, section=section, material=material, beamRole="transfer_ring_beam", supportLevel=1,
    )
    col_a = ColumnElement(code="C-1", location=Point2D(x=5, y=0), topElevation=0, bottomElevation=-12, section=section, material=material)
    col_b = ColumnElement(code="C-1", location=Point2D(x=6, y=0), topElevation=0, bottomElevation=-12, section=section, material=material)
    system_a = RetainingSystem(ringBeams=[beam_a], columns=[col_a])
    system_b = RetainingSystem(ringBeams=[beam_b], columns=[col_a])
    system_c = RetainingSystem(ringBeams=[beam_a], columns=[col_b])
    assert _geometry_fingerprint(system_a) != _geometry_fingerprint(system_b)
    assert _geometry_fingerprint(system_a) != _geometry_fingerprint(system_c)

def test_formal_source_requires_traceable_reference_and_dashboard_reads_do_not_save() -> None:
    project = Project(name="read-only-dashboard")
    ensure_parameter_provenance(project)
    importance = next(row for row in project.parameter_provenance if row.parameter_key == "design.importance_factor")
    assert importance.source_type == "standard_value"
    assert importance.source_reference is None
    governance = build_parameter_confirmation(project)
    row = next(item for item in governance["records"] if item["parameterKey"] == "design.importance_factor")
    assert row["sourceEligibleForFormalDesign"] is False
    assert "缺少可追溯" in row["formalEligibilityReason"]

    class ReadOnlyRepo:
        def __init__(self, value: Project):
            self.value = value
            self.save_count = 0
        def require(self, project_id: str) -> Project:
            assert project_id == "P-1"
            return self.value.model_copy(deep=True)
        def save(self, *args, **kwargs):
            self.save_count += 1
            raise AssertionError("GET endpoint must not save the project")

    repo = ReadOnlyRepo(project)
    assert parameter_governance("P-1", repo=repo)["schema"] == "pitguard-parameter-governance-v387"
    bundle = design_core_bundle("P-1", repo=repo)
    assert bundle["schema"] == "pitguard-design-core-bundle-v3872"
    assert repo.save_count == 0

def test_frontend_uses_one_design_core_bundle_and_one_css_entry() -> None:
    root = Path(__file__).resolve().parents[3]
    main = (root / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    panel = (root / "apps/web/src/components/DesignCoreWorkflowPanel.tsx").read_text(encoding="utf-8")
    client = (root / "apps/web/src/api/client.ts").read_text(encoding="utf-8")
    router = (root / "services/api/app/routers/design_core.py").read_text(encoding="utf-8")
    sanitizer = (root / "apps/web/src/drawing/candidateGeometry.ts").read_text(encoding="utf-8")
    assert "import './app/styles.css';" in main
    assert "import './styles.css';" not in main
    assert "getDesignCoreBundle" in panel
    assert "Promise.all" not in panel
    assert "getDesignCoreBundle" in client
    assert '@router.get("/bundle")' in router
    assert 'pitguard-design-core-bundle-v3872' in router
    assert 'phantom members at' in sanitizer
    for relative in (
        "components/SchemeComparisonPanel.tsx", "components/CoreEngineeringVisuals.tsx",
        "viewers/ResultViewer.tsx", "viewers/RetainingSystemViewer.tsx", "pages/CoreProjectWorkspace.tsx",
    ):
        assert "sanitizeCandidatePlanGeometry" in (root / "apps/web/src" / relative).read_text(encoding="utf-8")
