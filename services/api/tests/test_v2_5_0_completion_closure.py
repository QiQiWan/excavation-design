from __future__ import annotations

import zipfile
from functools import lru_cache
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.domain import Project
from app.services.benchmark_cases import run_benchmark_case_isolated as run_benchmark_case
from app.services.cad_template import validate_cad_template
from app.services.issue_center import build_issue_center, locate_issue
from app.services.rebar_detailing import build_rebar_detailing
from app.drawings.cad_export import export_construction_cad_package
from app.version import SOFTWARE_VERSION


@lru_cache(maxsize=1)
def _benchmark_payload() -> dict:
    return run_benchmark_case('URBAN-TOPDOWN-32M-WALL-5SUPPORT', persist=False)


def _benchmark_project() -> Project:
    return Project.model_validate(_benchmark_payload()['project'])


def test_v2_5_0_cad_template_validation_endpoint() -> None:
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'template validation'}).json()
    pid = project['id']
    res = client.get(f'/api/projects/{pid}/cad-template/validation')
    assert res.status_code == 200
    data = res.json()
    assert data['completion'] == 100.0
    assert data['status'] == 'pass'
    assert 'signatureWorkflow' in data['checkedItems']


def test_v2_5_0_rebar_shop_detailing_is_complete() -> None:
    project = _benchmark_project()
    detailing = build_rebar_detailing(project)
    assert detailing['summary']['shopDetailingCompletion'] == 100.0
    assert detailing['cageSegments']
    assert detailing['liftingPlan']
    assert detailing['spliceSchedule']
    assert detailing['bendRadiusChecks']
    assert detailing['coverConflictChecks']
    assert detailing['signoffChecklist']
    first = detailing['individualBars'][0]
    assert first['cageSegmentId']
    assert first['bendRadiusStatus'] == 'pass'
    assert first['coverStatus'] == 'pass'


def test_v2_5_0_cad_package_contains_shop_detailing_sheets(tmp_path) -> None:
    project = _benchmark_project()
    path = export_construction_cad_package(project, tmp_path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert 'S-09_lap_splice_layout.dxf' in names
    assert 'S-10_cage_segment_lifting_plan.dxf' in names
    assert 'S-11_cover_bend_check.dxf' in names
    assert 'S-12_shop_drawing_signoff_checklist.dxf' in names
    assert 'cage_segment_schedule.csv' in names
    assert 'splice_schedule.csv' in names
    assert 'cover_conflict_check.csv' in names
    assert 'shop_drawing_checklist.csv' in names


def test_v2_5_0_issue_locator_endpoint_contract() -> None:
    project = _benchmark_project()
    center = build_issue_center(project)
    assert center['maturity']['softwareVersion'] == SOFTWARE_VERSION
    issue_id = center['issues'][0]['id'] if center['issues'] else 'missing'
    located = locate_issue(project, issue_id)
    if center['issues']:
        assert located['status'] == 'located'
        assert len(located['viewCommands']) >= 5
