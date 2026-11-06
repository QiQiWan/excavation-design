from __future__ import annotations

import hashlib

import numpy as np

from app.calculation.nonlinear_geotechnical import representative_horizontal_spring
from app.calculation.spatial_frame_6dof import beam_local_stiffness_3d
from app.rules.gb50017.steel_support_rules import steel_pipe_buckling_curve
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.statutory_workflow import record_statutory_evidence, evaluate_statutory_workflow
from app.services.support_layout import SupportLayoutConfig
from app.services.verification_matrix_v377 import run_v377_verification_matrix


def _project() -> Project:
    outline = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
    excavation = make_excavation_model(
        "V3.77 assurance",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in outline], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    return Project(name="V3.77 assurance", excavation=excavation, retainingSystem=system)


def test_six_dof_beam_kernel_is_symmetric_with_six_rigid_modes() -> None:
    matrix = beam_local_stiffness_3d(2.06e8, 7.92e7, 0.025, 8.5e-5, 7.8e-5, 1.5e-4, 6.0)
    assert np.allclose(matrix, matrix.T, rtol=0.0, atol=1.0e-8)
    eigenvalues = np.linalg.eigvalsh((matrix + matrix.T) * 0.5)
    threshold = max(np.max(np.abs(eigenvalues)), 1.0) * 1.0e-10
    assert int(np.sum(np.abs(eigenvalues) <= threshold)) >= 6


def test_steel_buckling_reduction_decreases_with_length() -> None:
    factors = [
        steel_pipe_buckling_curve(outer_diameter_m=0.609, wall_thickness_m=0.016, length_m=length)["stabilityReductionFactor"]
        for length in (3.0, 6.0, 9.0, 12.0)
    ]
    assert factors == sorted(factors, reverse=True)


def test_soil_spring_default_is_never_formal() -> None:
    row = representative_horizontal_spring([], excavation_depth_m=16.0, allow_default=True)
    assert row["source"] == "default_screening"
    assert row["formalUseAllowed"] is False


def test_verification_matrix_reports_external_availability_honestly() -> None:
    result = run_v377_verification_matrix()
    assert result["internalReferenceStatus"] == "pass"
    assert result["externalReferenceStatus"] in {"pass", "partial", "unavailable"}
    assert result["status"] in {"pass", "warning"}
    assert result["formalExternalBenchmarkReady"] == (result["externalReferenceStatus"] == "pass")


def test_statutory_workflow_requires_project_confirmation_and_real_evidence() -> None:
    project = _project()
    project.design_settings.hazardous_work_classification = "large_scale_hazardous"
    initial = evaluate_statutory_workflow(project)
    assert initial["formalIssueEligible"] is False
    digest = hashlib.sha256(b"approved-source-data").hexdigest()
    project.advanced_engineering["artifactStorage"] = {
        "artifacts": [{"artifactId": "GEO-001", "sha256": digest, "logicalBytes": 20, "storedBytes": 20}]
    }
    record = record_statutory_evidence(
        project,
        evidence_type="design_source_data",
        artifact_id="GEO-001",
        artifact_sha256=digest,
        verifier="licensed-geotechnical-reviewer",
    )
    assert record["recordHash"]
    updated = evaluate_statutory_workflow(project)
    assert "design_source_data" not in updated["missingRequiredEvidence"]
    assert "special_construction_plan" in updated["missingRequiredEvidence"]


def test_online_documentation_exposes_v373_to_v381_contracts() -> None:
    docs = build_online_documentation()
    chapter_ids = {row["id"] for row in docs["chapters"]}
    assert {"accuracy", "compliance"} <= chapter_ids
    assert [row["level"] for row in docs["analysisLevels"]] == ["L0", "L1", "L2", "L3"]
    assert len(docs["releaseRoadmap"]) >= 13
    assert docs["releaseRoadmap"][-1]["version"] == "3.87.4"
    assert docs["statutoryWorkflow"]["evidence"]


def test_full_calculation_exposes_v377_assurance_domains() -> None:
    from app.calculation.engine import build_default_construction_cases, run_calculation
    project = _project()
    project.calculation_cases = build_default_construction_cases(project)
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    assert result.analysis_assurance["schema"] == "pitguard-analysis-assurance-v1"
    assert result.geotechnical_assurance["schema"] == "pitguard-nonlinear-geotechnical-assurance-v1"
    assert result.geotechnical_assurance["formalUseAllowed"] is False
    assert result.spatial_verification["schema"] == "pitguard-global-6dof-verification-v1"
    assert result.spatial_verification["status"] in {"pass", "warning", "fail"}
    assert result.verification_matrix["schema"] == "pitguard-v3.77-verification-matrix-v1"
    assert result.statutory_workflow_assurance["schema"] == "pitguard-statutory-workflow-assurance-v2"
    assert result.result_catalog["schema"] == "pitguard-result-catalog-v3"
    assert result.result_completeness["schema"] == "pitguard-result-completeness-v2"
    assert result.formal_report_gate.allowed_for_official_issue is False
    assert project.advanced_engineering["lastCalculationTransaction"]["resourceCleanup"]["garbageCollectionCompleted"] is True
