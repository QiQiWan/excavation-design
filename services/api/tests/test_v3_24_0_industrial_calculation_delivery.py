from __future__ import annotations

import hashlib
import json

import pytest

from app.schemas.domain import Project
from app.services.benchmark_cases import BENCHMARK_CASES, build_benchmark_project
from app.services.calculation_assurance import assess_calculation_result, audit_calculation_inputs, verify_current_calculation_contract
from app.services.delivery_package import export_coordinated_delivery_package
from app.services.delivery_release import build_release_certificate, evaluate_delivery_release_readiness
from app.services.design_pipeline import evaluate_design_pipeline
from app.version import SOFTWARE_VERSION


@pytest.fixture(scope="module")
def calculated_project() -> Project:
    return build_benchmark_project(BENCHMARK_CASES[0])


def test_calculation_contract_freezes_inputs_and_adopted_design(calculated_project: Project) -> None:
    result = calculated_project.calculation_results[-1]
    assurance = result.calculation_assurance

    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 25, 0)
    assert result.calculation_contract_id.startswith("calc-contract-")
    assert len(result.input_snapshot_hash or "") == 64
    assert len(result.adopted_design_snapshot_hash or "") == 64
    assert len(result.result_hash or "") == 64
    assert assurance["stageCoverage"]["complete"] is True
    assert assurance["numericalQuality"]["fallbackCount"] >= 0
    assert verify_current_calculation_contract(calculated_project, result)["current"] is True

    # Export/visualisation controls are deliberately excluded from the immutable
    # structural calculation snapshot.
    export_limit = calculated_project.design_settings.reinforcement_full_geometry_max_bars
    calculated_project.design_settings.reinforcement_full_geometry_max_bars = 12000
    try:
        assert verify_current_calculation_contract(calculated_project, result)["current"] is True
    finally:
        calculated_project.design_settings.reinforcement_full_geometry_max_bars = export_limit

    original = calculated_project.design_settings.surcharge
    calculated_project.design_settings.surcharge = float(original) + 1.0
    try:
        stale = verify_current_calculation_contract(calculated_project, result)
        assert stale["current"] is False
        assert stale["currentInputSnapshotHash"] != stale["storedAdoptedDesignSnapshotHash"]
    finally:
        calculated_project.design_settings.surcharge = original
    assert verify_current_calculation_contract(calculated_project, result)["current"] is True

    contract_a = dict(assurance["contract"])
    contract_b = dict(contract_a)
    contract_b["createdAt"] = "2099-01-01T00:00:00+00:00"
    contract_b["adoptedAt"] = "2099-01-01T00:01:00+00:00"
    audit = assurance["inputAudit"]
    hash_a = assess_calculation_result(calculated_project, calculated_project.calculation_cases[-1], result, input_audit=audit, contract=contract_a)["resultHash"]
    hash_b = assess_calculation_result(calculated_project, calculated_project.calculation_cases[-1], result, input_audit=audit, contract=contract_b)["resultHash"]
    assert hash_a == hash_b == result.result_hash


def test_input_audit_detects_invalid_stage_sequence_and_support_reference(calculated_project: Project) -> None:
    case = calculated_project.calculation_cases[-1].model_copy(deep=True)
    assert len(case.stages) >= 2
    case.stages[1].excavation_elevation = case.stages[0].excavation_elevation + 1.0
    case.stages[1].active_support_ids.append("missing-support-v324")

    audit = audit_calculation_inputs(calculated_project, case)
    stage_issue = next(row for row in audit["issues"] if row["code"] == "INPUT-STAGES")
    assert stage_issue["status"] == "fail"
    assert stage_issue["evidence"]["elevationReversals"]
    assert "missing-support-v324" in stage_issue["evidence"]["invalidSupportReferences"][case.stages[1].id]


def test_design_pipeline_exposes_calculation_and_release_baselines(calculated_project: Project) -> None:
    pipeline = evaluate_design_pipeline(calculated_project)
    analysis = next(row for row in pipeline["stages"] if row["stageId"] == "P4_ANALYSIS")
    issue = next(row for row in pipeline["stages"] if row["stageId"] == "P8_REVIEW_ISSUE")

    assert analysis["evidence"]["calculationContract"]["current"] is True
    assert analysis["evidence"]["inputSnapshotHash"]
    assert analysis["evidence"]["resultHash"]
    assert analysis["evidence"]["stageCoverage"]["complete"] is True
    assert issue["status"] == "blocked"
    assert issue["evidence"]["releaseReadiness"]["allowed"] is False


def test_construction_delivery_is_blocked_before_artifact_generation(tmp_path) -> None:
    project = Project(name="unreleased")
    readiness = evaluate_delivery_release_readiness(project, issue_mode="construction")
    assert readiness["allowed"] is False
    assert readiness["failCount"] >= 1

    with pytest.raises(ValueError, match="blocked by release baseline"):
        export_coordinated_delivery_package(project, tmp_path, issue_mode="construction", include_ifc_profiles=False)
    assert list(tmp_path.iterdir()) == []


def test_release_certificate_binds_calculation_and_artifact_content_root() -> None:
    project = Project(name="certificate")
    readiness = {
        "status": "pass",
        "snapshotHash": "snapshot-a",
        "calculationResultId": "result-a",
        "calculationContractId": "contract-a",
        "inputSnapshotHash": "input-a",
        "resultHash": "result-hash-a",
        "checks": [{"code": "REL", "status": "pass"}],
    }
    artifacts = [
        {"file": "a.ifc", "sha256": hashlib.sha256(b"a").hexdigest(), "sizeBytes": 1},
        {"file": "b.pdf", "sha256": hashlib.sha256(b"b").hexdigest(), "sizeBytes": 1},
    ]
    certificate = build_release_certificate(
        project,
        issue_mode="construction",
        release_grade="controlled",
        readiness=readiness,
        artifacts=artifacts,
    )
    assert certificate["calculationContractId"] == "contract-a"
    assert certificate["calculationResultHash"] == "result-hash-a"
    assert len(certificate["contentRootHash"]) == 64
    assert len(certificate["certificateHash"]) == 64
    assert certificate["artifactCountInRoot"] == 2
