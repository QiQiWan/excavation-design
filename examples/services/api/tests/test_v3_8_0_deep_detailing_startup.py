from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.drawings.cad_export import _write_node_hardware_detail
from app.quality.construction_issue_gate import validate_dxf_file
from app.schemas.domain import Point2D, Project, RetainingSystem, SupportElement, SupportWaleNode
from app.services.deep_detailing import build_deep_detailing_package


def _project_with_node() -> Project:
    support = SupportElement(
        code="S1-L01",
        level_index=1,
        elevation=-3.0,
        start=Point2D(x=1.0, y=5.0),
        end=Point2D(x=19.0, y=5.0),
        design_axial_force=6500.0,
    )
    node = SupportWaleNode(
        code="N-S1-A",
        support_id=support.id,
        support_code=support.code,
        level_index=1,
        elevation=-3.0,
        location=Point2D(x=1.0, y=5.0),
        face_code="F1",
    )
    return Project(name="deep-detailing", retaining_system=RetainingSystem(supports=[support], support_nodes=[node]))


def test_deep_detailing_generates_node_hardware_hoisting_and_couplers() -> None:
    project = _project_with_node()
    fabrication = {
        "spliceRecords": [
            {
                "spliceId": "SP-1",
                "sourceBarId": "B1",
                "barMark": "W1-V01",
                "hostCode": "W1",
                "spliceType": "mechanical_coupler",
                "diameterMm": 32,
                "staggerGroup": 1,
                "couplerSpec": "直螺纹套筒 D32",
            }
        ]
    }
    cage_segments = [{"segmentId": "W1-CAGE-01", "hostCode": "W1", "lengthM": 11.5, "estimatedCageWeightT": 8.0, "liftingPointCount": 4}]
    result = build_deep_detailing_package(project, bars=[], cage_segments=cage_segments, fabrication=fabrication)
    assert result["summary"]["bearingPlateCount"] == 1
    assert result["summary"]["stiffenerSetCount"] == 1
    assert result["summary"]["weldCount"] == 1
    assert result["summary"]["couplerCount"] == 1
    assert result["cageHoisting"][0]["liftingBarDiameterMm"] >= 25
    assert result["nodeHardware"]["bearingPlates"][0]["drawingRef"] == "D-10"


def test_deep_detailing_dxf_is_valid_r2018(tmp_path: Path) -> None:
    project = _project_with_node()
    deep = build_deep_detailing_package(project, bars=[], cage_segments=[], fabrication={"spliceRecords": []})
    path = tmp_path / "D-10.dxf"
    _write_node_hardware_detail(project, path, {"deepDetailing": deep})
    report = validate_dxf_file(path)
    assert report["status"] == "pass", report
    assert report["dxfVersion"] == "AC1032"


def test_python_environment_checker_prints_exact_install_command(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "dependency-test"
version = "0.0.0"
dependencies = ["definitely-missing-pitguard-package>=9.9,<10"]
""".strip(),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[3] / "scripts" / "check-python-env.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--pyproject", str(pyproject), "--format", "json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["status"] == "fail"
    assert "definitely-missing-pitguard-package>=9.9,<10" in report["missingRequirements"]
    assert "pip install" in report["installCommand"]
    assert "definitely-missing-pitguard-package" in report["installCommand"]
