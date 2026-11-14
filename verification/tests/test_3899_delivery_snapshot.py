"""导出证据不能改变它所签署的设计快照。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.schemas.domain import Project  # noqa: E402
from app.services.review_workflow import project_snapshot_hash  # noqa: E402


def _project() -> Project:
    return Project(name="快照证据")


def test_delivery_registration_does_not_change_snapshot() -> None:
    project = _project()
    baseline = project_snapshot_hash(project)
    project.delivery_packages.append({
        "issueMode": "review",
        "path": "exports/demo_coordinated_delivery_review.zip",
        "createdAt": "2026-11-14T14:09:56+00:00",
    })
    assert project_snapshot_hash(project) == baseline


def test_export_manifest_does_not_change_snapshot() -> None:
    project = _project()
    baseline = project_snapshot_hash(project)
    project.advanced_engineering["coordinatedDeliveryManifest"] = {
        "schema": "pitguard-export-manifest-v387",
        "exportType": "coordinated_delivery:review",
        "fileName": "demo.zip",
        "fileSize": 123,
        "sha256": "0" * 64,
        "generatedAt": "2026-11-14T14:09:56+00:00",
        "metadata": {"rebarMode": "balanced"},
    }
    project.advanced_engineering["calculationReportManifest"] = {
        "schema": "pitguard-export-manifest-v387",
        "exportType": "docx_calculation_report",
        "fileName": "report.docx",
    }
    assert project_snapshot_hash(project) == baseline


def test_storage_index_does_not_change_snapshot() -> None:
    """落盘索引（含 updatedAt）属于账本，不是设计状态。"""
    project = _project()
    baseline = project_snapshot_hash(project)
    project.advanced_engineering["artifactStorage"] = {
        "artifactCount": 1,
        "artifacts": [{"artifactId": "artifact-1", "createdAt": "2026-11-14T14:00:00+00:00"}],
        "updatedAt": "2026-11-14T14:17:50+00:00",
        "totalLogicalBytes": 1024,
    }
    assert project_snapshot_hash(project) == baseline
    project.advanced_engineering["artifactStorage"]["updatedAt"] = "2026-11-14T15:00:00+00:00"
    assert project_snapshot_hash(project) == baseline


def test_design_change_still_changes_snapshot() -> None:
    """排除证据字段不能把真实设计改动也一起屏蔽掉。"""
    project = _project()
    baseline = project_snapshot_hash(project)
    project.design_settings.surcharge = float(project.design_settings.surcharge or 0) + 5.0
    assert project_snapshot_hash(project) != baseline
