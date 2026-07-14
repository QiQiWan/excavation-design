from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.calculation.global_coupled import _replacement_slab_state
from app.calculation.engine import _rank_full_candidate_calculations
from app.main import app

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_CSV = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"


@pytest.fixture(scope="module")
def v36_project(tmp_path_factory: pytest.TempPathFactory):
    os.environ["PITGUARD_DB_PATH"] = str(tmp_path_factory.mktemp("v36") / "pitguard.sqlite3")
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "V3.6 topology and UX", "location": "regression"}).json()
        project_id = project["id"]
        with SAMPLE_CSV.open("rb") as handle:
            response = client.post(
                f"/api/projects/{project_id}/boreholes/import-csv",
                files={"file": (SAMPLE_CSV.name, handle, "text/csv")},
            )
        assert response.status_code == 200, response.text
        assert client.post(f"/api/projects/{project_id}/geology/build-model").status_code == 200
        excavation = {
            "name": "L-shaped V3.6 pit",
            "topElevation": 0,
            "bottomElevation": -12,
            "outline": {
                "closed": True,
                "points": [
                    {"x": 75, "y": 85}, {"x": 125, "y": 85}, {"x": 125, "y": 115},
                    {"x": 100, "y": 115}, {"x": 100, "y": 100}, {"x": 75, "y": 100},
                ],
            },
        }
        assert client.post(f"/api/projects/{project_id}/excavation", json=excavation).status_code == 200
        assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
        supports = client.post(f"/api/projects/{project_id}/design/auto-supports")
        assert supports.status_code == 200, supports.text
        yield client, project_id, supports.json()


def test_support_centrelines_are_offset_and_keep_wall_connection(v36_project) -> None:
    _client, _project_id, retaining = v36_project
    supports = retaining["supports"]
    assert supports
    checked = [item for item in supports if item.get("startWallConnection") and item.get("endWallConnection")]
    assert checked
    assert min(float(item.get("startWallClearanceM") or 0) for item in checked) >= 0.99
    assert min(float(item.get("endWallClearanceM") or 0) for item in checked) >= 0.99
    assert all(float(item.get("centerlineOffsetM") or 0) > 0 for item in checked)
    assert any(item["start"] != item["startWallConnection"] for item in checked)


def test_optimizer_returns_three_whole_scheme_topologies(v36_project) -> None:
    client, project_id, _retaining = v36_project
    response = client.post(f"/api/projects/{project_id}/design/optimize-supports", json={"preset": "balanced"})
    assert response.status_code == 200, response.text
    candidates = response.json().get("candidates", [])[:3]
    assert len(candidates) >= 3
    families = {str(item.get("variableSummary", {}).get("topologyFamily")) for item in candidates}
    # V3.22 keeps family diversity among structurally appropriate schemes.  An
    # L-shaped strip must include direct and hybrid force paths; a bidirectional
    # frame is reserved for near-square wide pits and is no longer injected only
    # to satisfy visual A/B/C diversity.
    assert {"hybrid_diagonal", "direct_grid"}.issubset(families)
    assert all(int(item.get("crossingCount") or 0) == 0 for item in candidates)
    assert all(item.get("planGeometry", {}).get("supports") for item in candidates)
    assert all(item.get("planGeometry", {}).get("supportElevations") for item in candidates)


def test_replacement_stiffness_uses_state_instead_of_zero() -> None:
    inactive = _replacement_slab_state("excavation", 30.0, {})
    assert inactive["status"] == "not_active"
    assert inactive["stiffness"] is None
    missing = _replacement_slab_state("replacement", 30.0, {})
    assert missing["status"] == "missing"
    assert missing["stiffness"] is None
    active = _replacement_slab_state("replacement", 30.0, {
        "effectiveWidthM": 6.0,
        "thicknessM": 0.25,
        "elasticModulusMpa": 30000.0,
        "connectionReduction": 0.65,
    })
    assert active["status"] == "active"
    assert float(active["stiffness"]) > 0.0


def test_result_viewer_uses_whole_scheme_cards_and_compact_details() -> None:
    source = (ROOT / "apps/web/src/viewers/ResultViewer.tsx").read_text(encoding="utf-8")
    assert "整体支撑方案 A/B/C 比选" in source
    assert "CandidateScheme3D" in source
    assert "整体采用方案" in source
    assert "墙—围檩—支撑全局矩阵与换撑刚度明细" in source
    assert "未激活 / —" in source
    assert "完整计算推荐" in source
    assert "decisionScore" in source


def test_full_candidate_comparison_adds_decision_rank_and_recommendation() -> None:
    rows = [
        {"schemeLabel": "A", "rank": 1, "score": 80, "maxSupportAxialForce": 5000, "maxDisplacement": 2.5, "maxWallMoment": 260, "maxWallShear": 200, "maxWaleMoment": 100000, "maxWaleDeflection": 0.08, "supportCount": 39, "columnCount": 9, "maxSpanLength": 24, "excessiveDirectStrutCount": 2, "failCount": 0, "warningCount": 4, "manualReviewCount": 0},
        {"schemeLabel": "B", "rank": 2, "score": 70, "maxSupportAxialForce": 6500, "maxDisplacement": 3.5, "maxWallMoment": 350, "maxWallShear": 220, "maxWaleMoment": 120000, "maxWaleDeflection": 0.5, "supportCount": 42, "columnCount": 4, "maxSpanLength": 30, "excessiveDirectStrutCount": 10, "failCount": 0, "warningCount": 6, "manualReviewCount": 0},
        {"schemeLabel": "C", "rank": 3, "score": 60, "maxSupportAxialForce": 4500, "maxDisplacement": 2.0, "maxWallMoment": 230, "maxWallShear": 190, "maxWaleMoment": 150000, "maxWaleDeflection": 0.12, "supportCount": 46, "columnCount": 14, "maxSpanLength": 28, "excessiveDirectStrutCount": 15, "failCount": 0, "warningCount": 4, "manualReviewCount": 0},
    ]
    _rank_full_candidate_calculations(rows)
    assert sorted(int(row["decisionRank"]) for row in rows) == [1, 2, 3]
    recommended = [row for row in rows if row["recommendedByFullCalculation"]]
    assert len(recommended) == 1
    assert float(recommended[0]["decisionScore"]) > 0
    assert "完整计算综合排名" in str(recommended[0]["decisionReason"])
