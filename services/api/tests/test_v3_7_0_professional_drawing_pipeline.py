from __future__ import annotations

import json
from pathlib import Path

import ezdxf
from pypdf import PdfReader

from app.drawings.professional_dxf import ProfessionalDxfWriter
from app.drawings.professional_pdf import export_professional_batch_pdf
from app.quality.construction_issue_gate import validate_dxf_file
from app.quality.drawing_completeness import evaluate_drawing_completeness
from app.schemas.domain import Project
from app.services.rebar_fabrication import build_rebar_fabrication_package


def test_professional_dxf_is_r2018_mm_and_has_paper_space(tmp_path: Path) -> None:
    path = tmp_path / "S-00.dxf"
    writer = ProfessionalDxfWriter()
    writer.line("S-WALL", 0, 0, 20, 0)
    writer.line("S-WALL", 20, 0, 20, 10)
    writer.dim_line("S-DIMS", 0, 0, 20, 0, "20000", 1.5)
    writer.title_block("S-00", "围护与支撑总平面", "1:100", project_name="测试基坑", template={"issueMode": "review", "activePaperSize": "A1", "activeOrientation": "landscape"})
    writer.write(path)

    report = validate_dxf_file(path)
    assert report["status"] == "pass", report
    doc = ezdxf.readfile(path)
    assert doc.dxfversion == "AC1032"
    assert int(doc.header["$INSUNITS"]) == 4
    assert "PIT_CN" in doc.styles
    assert any(entity.dxftype() == "DIMENSION" for entity in doc.modelspace())
    paper = [layout for layout in doc.layouts if layout.name.lower() not in {"model", "layout1"}]
    assert paper
    assert any(entity.dxftype() == "VIEWPORT" for entity in paper[0])


def test_fabrication_splits_long_bars_and_checks_clear_spacing() -> None:
    project = Project(name="fabrication")
    bars = [
        {
            "barId": "B1",
            "barMark": "W1-V01",
            "hostType": "diaphragm_wall",
            "hostCode": "W1",
            "hostId": "wall-1",
            "groupId": "G1",
            "barType": "longitudinal",
            "diameterMm": 32,
            "grade": "HRB400",
            "shapeCode": "00",
            "cutLengthM": 23.5,
            "subIndex": 1,
            "points": [{"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 0, "z": -23.5}],
        },
        {
            "barId": "B2",
            "barMark": "W1-V02",
            "hostType": "diaphragm_wall",
            "hostCode": "W1",
            "hostId": "wall-1",
            "groupId": "G1",
            "barType": "longitudinal",
            "diameterMm": 32,
            "grade": "HRB400",
            "shapeCode": "00",
            "cutLengthM": 23.5,
            "subIndex": 2,
            "points": [{"x": 0.2, "y": 0, "z": 0}, {"x": 0.2, "y": 0, "z": -23.5}],
        },
    ]
    result = build_rebar_fabrication_package(project, [], bars)
    assert result["summary"]["hardFailureCount"] == 0
    assert result["summary"]["splitBarCount"] == 2
    assert result["summary"]["maxPieceLengthM"] <= 12.0
    assert result["summary"]["mechanicalCouplerCount"] > 0
    assert result["geometricSpacingChecks"][0]["status"] == "pass"


def test_professional_pdf_has_one_standard_page_per_sheet(tmp_path: Path) -> None:
    project = Project(name="中文施工图测试", location="测试场地")
    manifest = {
        "sheets": [
            {"sheetNo": "G-00", "title": "图纸目录与总说明", "paperSize": "A1", "orientation": "landscape", "scale": "NTS", "renderer": "general_notes"},
            {"sheetNo": "D-01", "title": "支撑—围檩节点大样", "paperSize": "A3", "orientation": "landscape", "scale": "1:20", "renderer": "detail_support_wale"},
        ]
    }
    path = tmp_path / "formal.pdf"
    export_professional_batch_pdf(project, path, manifest, {}, {}, {"approvalValid": False, "currentSnapshotHash": "TEST"})
    reader = PdfReader(str(path))
    assert len(reader.pages) == 2
    assert path.stat().st_size > 4000


def test_completeness_gate_reports_missing_mandatory_drawings(tmp_path: Path) -> None:
    project = Project(name="gate")
    detailing = {
        "fabrication": {
            "transportLimitM": 12.0,
            "embeddedItemCollisionStatus": "not_applicable",
            "summary": {"maxPieceLengthM": 11.5, "duplicateSourceBarIdCount": 0},
        },
        "geometrySummary": {"omittedBarCount": 0},
    }
    (tmp_path / "drawing_register.csv").write_text("sheet,title\n", encoding="utf-8")
    (tmp_path / "90_schedules").mkdir()
    for name in (
        "rebar_schedule.csv", "rebar_bending_schedule.csv", "fabrication_bbs.csv",
        "fabrication_segments.csv", "geometric_rebar_spacing_checks.csv", "shop_drawing_checklist.csv",
    ):
        (tmp_path / "90_schedules" / name).write_text("x\n", encoding="utf-8")
    result = evaluate_drawing_completeness(project, detailing, tmp_path, "construction")
    assert result["status"] == "fail"
    assert result["blockerCount"] >= 4
    assert any(item["code"] == "MASTER_PLAN" and item["status"] == "fail" for item in result["checks"])
