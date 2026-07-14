from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.drawing_rules import build_drawing_plan, get_effective_drawing_rule_set
from app.drawings.cad_export import export_construction_cad_package
from app.schemas.domain import Project
from app.services import delivery_package as delivery
from app.services.benchmark_cases import run_benchmark_case_isolated


@pytest.fixture(scope="module")
def project() -> Project:
    result = run_benchmark_case_isolated("URBAN-TOPDOWN-32M-WALL-5SUPPORT", persist=False)
    return Project.model_validate(result["project"])


def test_v313_drawing_plan_contains_engineering_sheet_system(project: Project) -> None:
    plan = build_drawing_plan(project, get_effective_drawing_rule_set(project))
    renderers = {sheet["renderer"] for sheet in plan["sheets"]}
    assert {
        "drawing_reference_matrix",
        "wall_panel_layout",
        "excavation_section",
        "longitudinal_section",
        "construction_stage_sequence",
        "drawing_quality_summary",
    }.issubset(renderers)
    numbers = [sheet["sheetNo"] for sheet in plan["sheets"]]
    assert len(numbers) == len(set(numbers))


def test_v313_cad_package_has_per_sheet_quality_and_traceability(project: Project, tmp_path: Path) -> None:
    package = export_construction_cad_package(project, tmp_path, scope="full", rebar_mode="balanced", issue_mode="review")
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        assert "90_schedules/drawing_sheet_quality.json" in names
        assert "90_schedules/drawing_model_calculation_standard_matrix.csv" in names
        quality = json.loads(zf.read("90_schedules/drawing_sheet_quality.json"))
        assert quality["failCount"] == 0
        assert quality["score"] >= 95.0
        assert quality["duplicateSheetNumbers"] == []


def test_v313_coordinated_delivery_has_release_control_and_hash_verifier(project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def formal_stub(_project: Project, out: Path, **_: object) -> Path:
        path = Path(out) / "formal.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("batch_plot.pdf", b"%PDF-1.4\n")
            zf.writestr("drawing_register.csv", "sheet,title\nG-00,notes\n")
            zf.writestr("drawing_sheet_quality.json", json.dumps({"status": "pass", "score": 100, "passCount": 1, "warningCount": 0, "failCount": 0, "sheets": []}))
            zf.writestr("drawing_completeness.json", json.dumps({"status": "warning", "checks": []}))
            zf.writestr("construction_issue_gate.json", json.dumps({"status": "fail", "checks": []}))
            zf.writestr("drawing_model_calculation_standard_matrix.csv", "sheet,calculation,standard\n")
        return path

    def file_stub(name: str, content: bytes = b"data"):
        def _stub(_project: Project, out: Path, **_: object) -> Path:
            path = Path(out) / name
            path.write_bytes(content)
            return path
        return _stub

    monkeypatch.setattr(delivery, "export_formal_drawing_package", formal_stub)
    monkeypatch.setattr(delivery, "export_docx_report", file_stub("report.docx"))
    monkeypatch.setattr(delivery, "export_rebar_detailing_package", file_stub("rebar.zip"))
    monkeypatch.setattr(delivery, "export_design_scheme_ledger", file_stub("ledger.json", b"{}"))
    monkeypatch.setattr(delivery, "export_wall_length_redundancy_report", file_stub("wall.json", b"{}"))

    package = delivery.export_coordinated_delivery_package(project, tmp_path, issue_mode="review", include_ifc_profiles=False)
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        required = {
            "00_release/release_manifest.json",
            "00_release/issue_transmittal.csv",
            "00_release/deliverable_register.csv",
            "00_release/acceptance_matrix.csv",
            "00_release/index.html",
            "10_drawings/quick_review/drawing_sheet_quality.json",
            "90_audit/deliverable_relationship_matrix.csv",
            "90_audit/SHA256SUMS.txt",
            "90_audit/verify_delivery_package.py",
        }
        assert required.issubset(names)
        manifest = json.loads(zf.read("00_release/release_manifest.json"))
        assert manifest["artifactCount"] >= 8
        assert manifest["drawingSheetQuality"]["status"] == "pass"
        assert manifest["releaseGrade"] == "development_only"
