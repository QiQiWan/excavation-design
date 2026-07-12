from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon

from app.compliance.assurance import evaluate_project_assurance
from app.schemas.domain import ExcavationModel, Point2D, Polyline2D, Project, RetainingSystem, SupportLayoutOptimizationCandidate
from app.services import candidate_result_cache, crane_logistics
from app.services.detailing_geometry import apply_bar_geometry_patches, apply_embedded_item_patches
from app.services.unit_registry import infer_unit_key, unit_registry


def _project() -> Project:
    excavation = ExcavationModel(
        name="v3.10",
        outline=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=30, y=0), Point2D(x=30, y=20), Point2D(x=0, y=20)], closed=True),
        top_elevation=0,
        bottom_elevation=-12,
        depth=12,
    )
    return Project(name="v3.10", excavation=excavation, retaining_system=RetainingSystem())


def test_coordination_geometry_patch_modifies_real_bar_and_embedded_item() -> None:
    project = _project()
    project.advanced_engineering["detailGeometryPatches"] = {
        "PATCH-1": {
            "patchId": "PATCH-1", "issueId": "ISSUE-1", "action": "rebar_reroute", "applied": True,
            "embeddedItemId": "EMB-1", "targetEmbeddedItem": {"itemId": "EMB-1", "center": {"x": 5.0, "y": 0.0, "z": -3.0}, "size": {"x": 0.5, "y": 0.5, "z": 0.5}},
            "geometryDelta": {"affectedBarGroupIds": ["BG-1"], "offsetVectorM": [0.0, 0.18, 0.0], "transitionLengthM": 0.8},
        },
        "PATCH-2": {
            "patchId": "PATCH-2", "issueId": "ISSUE-2", "action": "embedded_shift", "applied": True,
            "embeddedItemId": "EMB-1", "geometryDelta": {"shiftVectorM": [0.25, 0.0, 0.0]},
        },
    }
    bars = [{
        "barId": "BAR-1", "groupId": "BG-1", "points": [{"x": 0, "y": 0, "z": -3}, {"x": 10, "y": 0, "z": -3}],
        "centerlineLengthM": 10.0, "cutLengthM": 10.0, "unitWeightKgPerM": 2.47, "weightKg": 24.7,
    }]
    bar_result = apply_bar_geometry_patches(project, bars)
    assert bar_result["summary"]["modifiedBarCount"] == 1
    assert len(bar_result["bars"][0]["points"]) > 2
    assert bar_result["bars"][0]["centerlineLengthM"] > 10.0
    embedded = apply_embedded_item_patches(project, [{"itemId": "EMB-1", "center": {"x": 5.0, "y": 0.0, "z": -3.0}}])
    assert embedded["embeddedItems"][0]["center"]["x"] == 5.25


def test_candidate_cache_uses_input_hash_and_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(candidate_result_cache, "_CACHE_DIR", tmp_path)
    project = _project()
    candidate = SupportLayoutOptimizationCandidate(rank=1, score=88.0, target_spacing=5.0, column_max_span=10.0)
    digest = candidate_result_cache.candidate_input_hash(project, candidate)
    assert len(digest) == 64
    candidate_result_cache.put_cached_candidate_result(digest, {"candidateId": candidate.id, "decisionScore": 91.2})
    cached = candidate_result_cache.get_cached_candidate_result(digest)
    assert cached and cached["decisionScore"] == 91.2
    assert candidate_result_cache.cache_stats()["entryCount"] == 1


def test_astar_site_route_avoids_exclusion_zone() -> None:
    boundary = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
    exclusion = [("building", Polygon([(12, 4), (18, 4), (18, 16), (12, 16)]))]
    roads = [Polygon([(0, 8), (30, 8), (30, 12), (0, 12)])]
    route = crane_logistics._astar_route((2, 10), (28, 10), boundary, exclusion, roads, step=1.0)
    assert route["found"] is True
    assert route["line"].intersects(exclusion[0][1]) is False
    assert route["lengthM"] > 26.0
    assert len(route["coordinates"]) >= 3


def test_assurance_keeps_acceptance_matrix_and_adds_all_module_review() -> None:
    result = evaluate_project_assurance(_project())
    assert len(result["acceptanceMatrix"]) >= 12
    assert len(result["moduleCompletionReview"]) == 12
    assert {row["id"] for row in result["moduleCompletionReview"]} == {f"M{i:02d}" for i in range(1, 13)}
    assert 0 <= result["moduleOverallCompleteness"] <= 100
    assert any(row["name"] == "A/B/C方案优化" for row in result["moduleCompletionReview"])


def test_unit_registry_is_generated_from_canonical_package_and_infers_fields() -> None:
    registry = unit_registry()
    assert registry["source"].endswith("packages/units/engineering-units.json")
    assert infer_unit_key("slabReplacementStiffness") == "stiffness"
    assert infer_unit_key("excavationElevation") == "elevation"
