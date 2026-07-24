from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.drawings.formal_issue import export_formal_drawing_package
from app.schemas.domain import MonitoringRecord, Project
from app.services.advanced_suite import build_advanced_engineering_suite
from app.services.benchmark_cases import run_benchmark_case_isolated
from app.services.monitoring_calibration import calibrate_from_monitoring
from app.services.issue_center import build_issue_center
from app.services.review_workflow import review_status, transition_review


def _install_review_credential_registry(tmp_path: Path, monkeypatch, holder: str = "approver-D") -> dict[str, object]:
    registry_path = tmp_path / "verified-professional-credentials.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": "pitguard-professional-credential-registry-v1",
                "credentials": [
                    {
                        "registryRecordId": "TEST-ADVANCED-STRUCT-0001",
                        "licenseType": "registered_structural_engineer",
                        "licenseNumber": "TEST-ADVANCED-0001",
                        "holderName": holder,
                        "jurisdiction": "TEST",
                        "organization": "PitGuard synthetic test fixture",
                        "status": "verified",
                        "validUntil": "2099-12-31",
                        "verificationSource": "synthetic-unit-test-registry",
                        "verificationReference": "TEST-ONLY-NOT-A-REAL-LICENSE",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PITGUARD_PROFESSIONAL_CREDENTIAL_REGISTRY", str(registry_path))
    return {
        "licenseType": "registered_structural_engineer",
        "licenseNumber": "TEST-ADVANCED-0001",
        "holderName": holder,
        "jurisdiction": "TEST",
        "verified": False,
    }


def _approve_project(project: Project, tmp_path: Path, monkeypatch, *, holder: str = "approver-D") -> None:
    credential = _install_review_credential_registry(tmp_path, monkeypatch, holder)
    transition_review(project, "designer", "designer-A", "submit")
    transition_review(project, "checker", "checker-B", "accept")
    transition_review(project, "reviewer", "reviewer-C", "accept")
    transition_review(
        project,
        "approver",
        holder,
        "approve",
        credential=credential,
        digital_signature_hash="c" * 64,
    )


@pytest.fixture(scope="module")
def benchmark_project() -> Project:
    result = run_benchmark_case_isolated("URBAN-TOPDOWN-32M-WALL-5SUPPORT", persist=False)
    return Project.model_validate(result["project"])


def test_v3_3_advanced_suite_covers_eight_tracks(benchmark_project: Project) -> None:
    suite = build_advanced_engineering_suite(benchmark_project, mode="balanced")
    assert suite["summary"]["moduleCount"] == 8
    assert suite["serviceability"]["summary"]["maxEstimatedCrackWidthMm"] <= suite["serviceability"]["summary"]["crackWidthLimitMm"]
    assert suite["topology"]["summary"]["levelCount"] >= 1
    assert suite["collisions"]["summary"]["hardCollisionCount"] == 0
    assert suite["nodeLocal"]["summary"]["nodeCount"] >= 1
    assert suite["formalDrawings"]["supportsBatchPdf"] is True
    assert suite["ux"]["keyboardNavigation"] is True


def test_v3_3_monitoring_calibration_is_applied_to_next_calculation_inputs(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    latest = project.calculation_results[-1]
    support = project.retaining_system.supports[0]
    project.monitoring_records.extend([
        MonitoringRecord(recordType="wall_displacement", measuredValue=float(latest.governing_values.max_displacement or 1.0) * 1.15, unit="mm"),
        MonitoringRecord(recordType="support_axial_force", objectId=support.id, measuredValue=float(support.design_axial_force or 1.0) * 0.90, unit="kN"),
        MonitoringRecord(recordType="settlement", measuredValue=float(latest.governing_values.max_displacement or 1.0) * 0.35 * 1.08, unit="mm"),
        MonitoringRecord(recordType="groundwater", measuredValue=project.design_settings.groundwater_level + 0.3, unit="m"),
        MonitoringRecord(recordType="wall_displacement", measuredValue=float(latest.governing_values.max_displacement or 1.0) * 1.10, unit="mm"),
    ])
    run = calibrate_from_monitoring(project, apply=True)
    factors = project.advanced_engineering["calibrationFactors"]
    assert run.applied is True
    assert run.sample_count >= 5
    assert factors["calibrationRunId"] == run.id
    assert factors["groundwaterOffsetM"] == pytest.approx(0.3)
    assert project.calculation_results == []
    assert "previousFactors" in factors


def test_v3_3_review_approval_becomes_stale_after_design_change(
    benchmark_project: Project,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = benchmark_project.model_copy(deep=True)
    _approve_project(project, tmp_path, monkeypatch)
    approved = review_status(project)
    assert approved["approvalValid"] is True
    project.design_settings.temperature_range_c += 5.0
    stale = review_status(project)
    assert stale["status"] == "stale"
    assert stale["approvalValid"] is False


def test_v3_3_formal_package_contains_geometry_pdf_quality_and_revision_files(
    benchmark_project: Project,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = benchmark_project.model_copy(deep=True)
    _approve_project(project, tmp_path, monkeypatch)
    path = export_formal_drawing_package(project, tmp_path, issue_mode="review", rebar_mode="balanced")
    assert path.exists() and path.stat().st_size > 10_000
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "PitGuard_drawing_issue_preview.pdf" in names
        assert "advanced_engineering_suite.json" in names
        assert "review_workflow.json" in names
        assert "drawing_revisions.csv" in names
        assert "plot_publish_manifest.json" in names
        assert "drawing_rule_set.json" in names
        assert "drawing_plan.json" in names
        assert "CAD/drawing_rule_set.json" in names
        assert "CAD/90_schedules/drawing_rule_decisions.csv" in names
        plot = json.loads(zf.read("plot_publish_manifest.json"))
        assert plot["pdfContainsGeometryPreviews"] is True
        assert "support_level_plans" in plot["geometryPreviewTypes"]
        assert plot["drawingRuleSetHash"]
        assert plot["drawingPlanHash"]
        assert any(name.startswith("CAD/50_quality/") for name in names)
        assert any(name.startswith("CAD/60_monitoring/") for name in names)


def test_v3_3_issue_center_exposes_advanced_engineering_modules(benchmark_project: Project) -> None:
    center = build_issue_center(benchmark_project)
    module_ids = {row["id"] for row in center["moduleLedger"]}
    assert {"M17", "M18", "M19", "M20", "M21", "M22", "M23", "M24"}.issubset(module_ids)
    assert center["maturity"]["systemModuleCompletion"] == 100.0


def test_v3_3_review_enforces_separation_of_duties_and_reject_comment(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    transition_review(project, "designer", "same-person", "submit")
    with pytest.raises(ValueError, match="Separation of duties"):
        transition_review(project, "checker", "same-person", "accept")
    with pytest.raises(ValueError, match="requires a review comment"):
        transition_review(project, "checker", "checker-B", "reject")


def test_v3_3_revision_codes_continue_after_z(benchmark_project: Project) -> None:
    from app.drawings.formal_issue import create_drawing_revision
    from app.schemas.domain import DrawingRevision
    from app.services.review_workflow import project_snapshot_hash
    project = benchmark_project.model_copy(deep=True)
    snapshot = project_snapshot_hash(project)
    project.drawing_revisions = [DrawingRevision(revision=chr(ord("A") + index), description=f"revision {index}", author="designer", snapshotHash=snapshot) for index in range(26)]
    item = create_drawing_revision(project, "revision 26", [], "designer")
    assert project.drawing_revisions[25].revision == "Z"
    assert item.revision == "AA"


def test_v3_3_monitoring_csv_supports_bilingual_headers() -> None:
    from app.routers.advanced import _monitoring_record_from_row
    record = _monitoring_record_from_row({"类型": "墙体位移", "监测值": "12.5", "单位": "mm", "对象编号": "W-01", "标高": "-8.0"}, "sample.csv")
    assert record.record_type == "wall_displacement"
    assert record.measured_value == pytest.approx(12.5)
    assert record.object_code == "W-01"
    assert record.elevation == pytest.approx(-8.0)
    assert record.source == "sample.csv"


def test_v3_3_design_setting_update_invalidates_old_results(benchmark_project: Project) -> None:
    from app.routers.projects import update_project

    class Repo:
        def __init__(self, project: Project): self.project = project
        def require(self, _project_id: str) -> Project: return self.project
        def save(self, project: Project) -> Project: self.project = project; return project

    project = benchmark_project.model_copy(deep=True)
    assert project.calculation_results
    settings = project.design_settings.model_dump(mode="json", by_alias=True)
    settings["temperatureRangeC"] = float(settings["temperatureRangeC"]) + 3.0
    updated = update_project(project.id, {"designSettings": settings}, Repo(project))
    assert updated.calculation_results == []
    assert updated.calculation_cases == []
    assert updated.advanced_engineering["requiresRecalculation"] is True
    assert updated.advanced_engineering["invalidationReason"]["keys"] == ["designSettings"]


def test_v3_3_stale_approval_can_be_resubmitted(
    benchmark_project: Project,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = benchmark_project.model_copy(deep=True)
    _approve_project(project, tmp_path, monkeypatch)
    project.design_settings.temperature_range_c += 1.0
    assert review_status(project)["status"] == "stale"
    submitted = transition_review(project, "designer", "designer-A", "submit", "design updated")
    assert submitted["status"] == "submitted"
    assert submitted["approvedSnapshotHash"] is None


def test_v3_3_construction_issue_requires_current_snapshot_revision(
    benchmark_project: Project,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.drawings.formal_issue import create_drawing_revision
    project = benchmark_project.model_copy(deep=True)
    _approve_project(project, tmp_path, monkeypatch)
    before = build_advanced_engineering_suite(project)
    assert before["formalDrawings"]["constructionRevisionValid"] is False
    item = create_drawing_revision(project, "approved construction issue", ["S-00", "R-02"], "designer-A", "construction")
    after = build_advanced_engineering_suite(project)
    assert item.revision == "A"
    assert after["formalDrawings"]["constructionRevisionValid"] is True
    assert after["formalDrawings"]["currentConstructionRevision"] == "A"
