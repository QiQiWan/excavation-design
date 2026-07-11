from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.domain import Project
from app.calculation.engine import run_calculation
from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.services.rebar_detailing import build_individual_rebar_geometry


def _load_uploaded_case() -> Project:
    path = Path('/mnt/data/pit_v260_work/project-9a9a65d20bc2.json')
    if not path.exists():
        pytest.skip('uploaded case JSON is not available in this runtime')
    return Project.model_validate(json.loads(path.read_text(encoding='utf-8')))


def test_v2_6_0_uploaded_case_calculation_finishes_without_reoptimizing_historical_payload() -> None:
    project = _load_uploaded_case()
    result = run_calculation(project, project.calculation_cases[0])
    assert result.governing_values.max_displacement is not None
    assert result.check_summary


def test_v2_6_0_rebar_visualization_uses_polyline_shapes() -> None:
    project = _load_uploaded_case()
    data = build_rebar_ifc_visualization(project, max_bars=180)
    assert data['bars']
    assert any(len(bar.get('points') or []) > 2 for bar in data['bars'])
    assert any(bar.get('shapeKind') in {'vertical_lap_polyline', 'horizontal_hooked_polyline', 'closed_stirrup_rectangle'} or 'polyline' in str(bar.get('shapeKind')) for bar in data['bars'])


def test_v2_6_0_individual_rebar_geometry_keeps_lap_or_hook_polyline() -> None:
    project = _load_uploaded_case()
    data = build_individual_rebar_geometry(project, max_bars=120)
    assert data['bars']
    assert any(len(bar.get('points') or []) > 2 for bar in data['bars'])
