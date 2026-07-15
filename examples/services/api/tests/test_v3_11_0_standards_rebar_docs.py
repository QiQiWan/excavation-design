from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.compliance.assurance import evaluate_project_assurance
from app.reports.docx_report import export_docx_report
from app.schemas.domain import ExcavationModel, Point2D, Polyline2D, Project, RetainingSystem, SupportElement
from app.services.rebar_export import export_rebar_detailing_package
from app.services.standards_matrix import build_online_documentation, build_standards_process_matrix


def _project() -> Project:
    support = SupportElement(
        code="S1-L01",
        level_index=1,
        elevation=-3.0,
        start=Point2D(x=0.0, y=5.0),
        end=Point2D(x=20.0, y=5.0),
        design_axial_force=6500.0,
    )
    excavation = ExcavationModel(
        name="v3.11-test",
        outline=Polyline2D(points=[
            Point2D(x=0.0, y=0.0), Point2D(x=20.0, y=0.0),
            Point2D(x=20.0, y=10.0), Point2D(x=0.0, y=10.0),
        ], closed=True),
        top_elevation=0.0,
        bottom_elevation=-10.0,
        depth=10.0,
    )
    return Project(name="v3.11", excavation=excavation, retaining_system=RetainingSystem(supports=[support]))


def test_process_standard_matrix_covers_all_workflow_steps_and_mandatory_codes() -> None:
    matrix = build_standards_process_matrix(_project())
    assert [row["workflowStep"] for row in matrix["steps"]] == [
        "settings", "boreholes", "geology", "excavation", "retaining", "calculation", "assurance", "export",
    ]
    catalog = {row["code"]: row for row in matrix["catalog"]}
    assert catalog["GB 55003-2021"]["level"] == "mandatory_all"
    assert catalog["GB 55008-2021"]["level"] == "mandatory_all"
    calculation = next(row for row in matrix["steps"] if row["workflowStep"] == "calculation")
    codes = {row["code"] for row in calculation["standardRefs"]}
    assert {"JGJ 120-2012", "GB 50007-2011", "GB 50009-2012", "GB 55003-2021", "GB 55008-2021"}.issubset(codes)
    assert calculation["keyCalculations"]


def test_online_documentation_contains_formula_assumption_verification_and_file_guide() -> None:
    docs = build_online_documentation()
    assert {chapter["id"] for chapter in docs["chapters"]} == {"workflow", "principles", "standards", "deliverables"}
    assert len(docs["calculationPrinciples"]) >= 6
    for item in docs["calculationPrinciples"]:
        assert item["inputs"] and item["method"] and item["outputs"]
        assert item["equations"] and item["assumptions"] and item["verification"]
        assert item["standards"]
    assert any("钢筋" in item["file"] for item in docs["fileGuide"])


def test_rebar_package_is_zip_with_human_and_machine_readable_outputs(tmp_path: Path) -> None:
    path = export_rebar_detailing_package(_project(), tmp_path)
    assert path.suffix == ".zip"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert any(name.endswith("rebar_detailing_schedules.xlsx") for name in names)
        assert any(name.endswith("00_machine_data/rebar_detailing_full.json") for name in names)
        assert any(name.endswith("10_schedules/rebar_mark_schedule.csv") for name in names)
        assert any(name.endswith("20_checks/signoff_checklist.csv") for name in names)
        assert any(name.endswith("90_guidance/README_USAGE.md") for name in names)
        manifest_name = next(name for name in names if name.endswith("package_manifest.json"))
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["packageType"] == "rebar_detailing_zip"
        assert manifest["humanReadablePrimary"] == "rebar_detailing_schedules.xlsx"


def test_report_contains_standard_matrix_and_formula_section(tmp_path: Path) -> None:
    path = export_docx_report(_project(), tmp_path)
    assert path.exists() and path.stat().st_size > 0
    from docx import Document
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "设计流程—关键计算—规范条文对应矩阵" in text
    assert "关键计算原理、公式与复核点" in text


def test_assurance_uses_project_specific_completion_fields() -> None:
    assurance = evaluate_project_assurance(_project())
    assert 0 <= assurance["capabilityCompleteness"] <= 100
    assert 0 <= assurance["moduleOverallCompleteness"] <= 100
    assert "engineeringCheckStatus" in assurance
    assert "officialIssueGateAllowed" in assurance


def test_global_matrix_numerical_quality_diagnostics_are_auditable() -> None:
    import numpy as np
    from app.calculation.global_coupled import _matrix_equilibrium_diagnostics

    k = np.array([[12.0, -4.0], [-4.0, 9.0]], dtype=float)
    f = np.array([8.0, 5.0], dtype=float)
    u = np.linalg.solve(k, f)
    diagnostics = _matrix_equilibrium_diagnostics(k, f, u)
    assert diagnostics["status"] == "pass"
    assert diagnostics["relativeResidual"] <= 1.0e-12
    assert diagnostics["matrixSymmetryError"] <= 1.0e-12
    assert diagnostics["equation"] == "K u = F"

    regularized = _matrix_equilibrium_diagnostics(k, f, u, regularization=1.0)
    assert regularized["status"] == "manual_review"


def test_rebar_download_endpoint_returns_zip_instead_of_project_json() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'rebar zip endpoint'}).json()
        response = client.get(f"/api/projects/{project['id']}/export/rebar-detailing-package?mode=balanced")
    assert response.status_code == 200
    assert response.headers.get('content-type', '').startswith('application/zip')
    assert '.zip' in response.headers.get('content-disposition', '')
    assert response.content[:2] == b'PK'
