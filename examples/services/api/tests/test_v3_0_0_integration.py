from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "services/api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.wale_beam import support_spring_stiffness
from app.main import app
from app.schemas.domain import (
    ExcavationModel,
    ExcavationSegment,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    SectionDefinition,
    SupportElement,
)
from app.services.design_service import auto_supports
from app.services.support_layout import SupportLayoutConfig, TARGET_MAIN_SUPPORT_SPACING_M
from app.storage.task_store import SQLiteTaskStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard-v3-test.sqlite3"))
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "V3 Integration", "location": "Test Site"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_excavation(client: TestClient, project_id: str) -> None:
    response = client.post(
        f"/api/projects/{project_id}/excavation",
        json={
            "name": "Rectangular pit",
            "topElevation": 0,
            "bottomElevation": -12,
            "outline": {
                "closed": True,
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 60, "y": 0},
                    {"x": 60, "y": 30},
                    {"x": 0, "y": 30},
                ],
            },
        },
    )
    assert response.status_code == 200, response.text


def test_project_list_is_compact_and_detail_history_is_bounded(client: TestClient):
    project_id = _create_project(client)
    listing = client.get("/api/projects")
    assert listing.status_code == 200, listing.text
    row = next(item for item in listing.json() if item["id"] == project_id)
    assert row["name"] == "V3 Integration"
    assert "boreholes" not in row
    assert "calculationResults" not in row
    assert row["calculationResultCount"] == 0

    detail = client.get(f"/api/projects/{project_id}?result_history_limit=0")
    assert detail.status_code == 200, detail.text
    assert detail.json()["calculationResults"] == []


def test_geometry_consistency_uses_closed_excavation_wall_topology(client: TestClient):
    project_id = _create_project(client)
    _create_excavation(client, project_id)
    response = client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall")
    assert response.status_code == 200, response.text

    consistency = client.get(f"/api/projects/{project_id}/geometry-consistency")
    assert consistency.status_code == 200, consistency.text
    data = consistency.json()
    assert data["consistent"] is True
    assert data["outlineClosed"] is True
    assert data["segmentCount"] == 4
    assert data["wallCount"] >= 4
    assert data["missingWallSegments"] == []
    assert len(data["excavationGeometryHash"]) == 64
    assert len(data["wallGeometryHash"]) == 64


def test_support_layout_config_is_local_and_persisted_in_summary():
    outline = Polyline2D(
        closed=True,
        points=[Point2D(x=0, y=0), Point2D(x=80, y=0), Point2D(x=80, y=30), Point2D(x=0, y=30)],
    )
    excavation = ExcavationModel(
        name="Long pit",
        outline=outline,
        top_elevation=0,
        bottom_elevation=-12,
        depth=12,
        segments=[],
    )
    dense = auto_supports(
        excavation,
        layout_config=SupportLayoutConfig(target_main_support_spacing_m=3.2, column_max_unbraced_span_m=12.0),
    )
    sparse = auto_supports(
        excavation,
        layout_config=SupportLayoutConfig(target_main_support_spacing_m=5.8, column_max_unbraced_span_m=24.0),
    )
    assert len(dense.supports) > len(sparse.supports)
    assert dense.layout_summary["targetMainSupportSpacing_m"] == pytest.approx(3.2)
    assert dense.layout_summary["columnMaxUnbracedSpan_m"] == pytest.approx(12.0)
    assert sparse.layout_summary["targetMainSupportSpacing_m"] == pytest.approx(5.8)
    assert sparse.layout_summary["columnMaxUnbracedSpan_m"] == pytest.approx(24.0)
    assert TARGET_MAIN_SUPPORT_SPACING_M == pytest.approx(5.0)


def test_support_spring_stiffness_depends_on_member_length_and_projection():
    face = ExcavationSegment(
        name="S1",
        start=Point2D(x=0, y=0),
        end=Point2D(x=20, y=0),
        length=20,
        outward_normal=Point2D(x=0, y=-1),
        midpoint=Point2D(x=10, y=0),
        chainage=0,
    )

    def make_support(code: str, end: Point2D) -> SupportElement:
        return SupportElement(
            code=code,
            level_index=1,
            elevation=-2,
            start=Point2D(x=10, y=0),
            end=end,
            span_length=((end.x - 10) ** 2 + end.y**2) ** 0.5,
            start_face_code="S1",
            section=SectionDefinition(width=1.0, height=1.0, name="1000x1000 RC"),
            material=MaterialDefinition(name="Concrete", grade="C35"),
        )

    short_normal = make_support("S-short", Point2D(x=10, y=10))
    long_normal = make_support("S-long", Point2D(x=10, y=30))
    diagonal = make_support("S-diagonal", Point2D(x=30, y=20))
    k_short, projection_short = support_spring_stiffness(short_normal, face)
    k_long, _ = support_spring_stiffness(long_normal, face)
    k_diagonal, projection_diagonal = support_spring_stiffness(diagonal, face)
    assert k_short > k_long
    assert k_short > k_diagonal
    assert projection_short == pytest.approx(1.0)
    assert projection_diagonal < 1.0


def test_task_records_persist_in_sqlite(tmp_path):
    store = SQLiteTaskStore(tmp_path / "tasks.sqlite3")
    record = {
        "id": "task-test",
        "projectId": "project-test",
        "operation": "calculation_full",
        "title": "test",
        "status": "queued",
        "progress": 0,
        "currentStep": "queued",
        "logs": [],
        "result": None,
        "error": None,
        "createdAt": "2026-07-11T00:00:00+00:00",
        "updatedAt": "2026-07-11T00:00:00+00:00",
        "finishedAt": None,
        "cancelRequested": False,
    }
    store.upsert(record)
    records = store.list(project_id="project-test")
    assert len(records) == 1
    assert records[0]["id"] == "task-test"
