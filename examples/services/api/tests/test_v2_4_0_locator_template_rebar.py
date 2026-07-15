from __future__ import annotations

import zipfile
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.domain import Project
from app.services.benchmark_cases import run_benchmark_case_isolated as run_benchmark_case
from app.services.rebar_detailing import build_rebar_detailing
from app.drawings.cad_export import export_construction_cad_package


def test_v2_4_0_cad_template_endpoint_roundtrip() -> None:
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'template smoke'}).json()
    pid = project['id']
    default = client.get(f'/api/projects/{pid}/cad-template')
    assert default.status_code == 200
    assert default.json()['sheetPrefix']
    updated = client.put(f'/api/projects/{pid}/cad-template', json={'enterpriseName': 'Example Institute', 'sheetPrefix': 'KJ', 'designer': 'WQW', 'layerStandard': {'support': 'ECJTU_SUPPORT'}})
    assert updated.status_code == 200
    data = updated.json()
    assert data['enterpriseName'] == 'Example Institute'
    assert data['sheetPrefix'] == 'KJ'
    assert data['layerStandard']['support'] == 'ECJTU_SUPPORT'


def test_v2_4_0_individual_bar_geometry_from_public_benchmark() -> None:
    result = run_benchmark_case('URBAN-TOPDOWN-32M-WALL-5SUPPORT', persist=False)
    project = Project.model_validate(result['project'])
    detailing = build_rebar_detailing(project)
    assert detailing['summary']['individualBarCount'] > 0
    first = detailing['individualBars'][0]
    assert first['points'] and len(first['segments']) >= 1
    assert first['cutLengthM'] >= first['centerlineLengthM']


def test_v2_4_0_cad_package_contains_template_and_individual_geometry(tmp_path) -> None:
    result = run_benchmark_case('HZ-30M-SOFT-CLAY-9310', persist=False)
    project = Project.model_validate(result['project'])
    project.cad_template = {'sheetPrefix': 'FD', 'enterpriseName': 'Regression Institute', 'layerStandard': {'highlight': 'REG_HIGHLIGHT'}}
    path = export_construction_cad_package(project, tmp_path)
    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert 'S-08_individual_rebar_geometry.dxf' in names
        assert 'individual_bar_geometry.csv' in names
        manifest = zf.read('enterprise_template_manifest.json').decode('utf-8')
        assert 'Regression Institute' in manifest
        assert 'REG_HIGHLIGHT' in manifest
