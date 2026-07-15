from __future__ import annotations

from collections import Counter

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig, plan_shape_diagnostics
from app.storage.artifact_store import ProjectArtifactStore, externalize_project_payload, rehydrate_project_payload
from app.version import SOFTWARE_VERSION


def _surface(size: int = 82) -> dict:
    xs = [float(index) for index in range(size)]
    ys = [float(index) for index in range(size)]
    return {
        "stratumCode": "S1",
        "surfaceType": "top",
        "confidence": "high",
        "grid": {
            "xValues": xs,
            "yValues": ys,
            "zValues": [[100.0 - 0.1 * x - 0.2 * y for x in xs] for y in ys],
        },
    }


def test_idw_full_surface_is_externalized_but_workspace_preview_survives(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    payload = {
        "id": "idw-preview-project",
        "name": "IDW preview",
        "geologicalModel": {"surfaces": [_surface()], "surfacePreviews": [], "volumes": [], "warnings": []},
        "advancedEngineering": {},
    }
    store = ProjectArtifactStore(tmp_path / "artifacts")
    compact = externalize_project_payload(payload, store)
    model = compact["geologicalModel"]
    assert model["surfaces"] == []
    assert len(model["surfacePreviews"]) == 1
    preview = model["surfacePreviews"][0]["grid"]
    assert len(preview["xValues"]) <= 36
    assert len(preview["yValues"]) <= 36
    assert preview["xValues"][0] == 0.0
    assert preview["xValues"][-1] == 81.0
    full = rehydrate_project_payload(compact, store)
    assert len(full["geologicalModel"]["surfaces"][0]["grid"]["xValues"]) == 82


def test_existing_surface_artifact_backfills_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    store = ProjectArtifactStore(tmp_path / "artifacts")
    ref = store.write_json("old-project", "geology-surfaces", [_surface()])
    ref["storageKey"] = "geology:surfaces"
    payload = {
        "id": "old-project",
        "geologicalModel": {"surfaces": [], "volumes": [], "warnings": []},
        "advancedEngineering": {"artifactStorage": {"artifacts": [ref]}},
    }
    compact = externalize_project_payload(payload, store)
    assert compact["geologicalModel"]["surfacePreviews"]


def _stepped_strip_project() -> Project:
    raw = [
        (0, 0), (15, 0), (15, 5), (70, 5), (70, 0), (90, 0),
        (90, 5), (175, 5), (175, 0), (190, 0), (190, 30),
        (175, 30), (175, 25), (90, 25), (90, 30), (70, 30),
        (70, 25), (15, 25), (15, 30), (0, 30),
    ]
    points = [Point2D(x=x, y=y) for x, y in raw]
    excavation = make_excavation_model(
        "stepped-strip",
        Polyline2D(points=points, closed=True),
        0.0,
        -16.0,
    )
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="hybrid_diagonal"),
    )
    return Project(name="stepped-strip", excavation=excavation, retainingSystem=retaining)


def test_elongated_stepped_strip_uses_adaptive_stations_and_terminal_braces() -> None:
    project = _stepped_strip_project()
    diagnostics = plan_shape_diagnostics(list(project.excavation.outline.points))
    quality = evaluate_support_layout_quality(project)
    counts = Counter(item.support_role for item in project.retaining_system.supports)
    assert diagnostics["archetype"] == "elongated_stepped_strip"
    assert diagnostics["corridorProfile"]["singleCorridor"] is True
    assert counts["corner_diagonal"] >= 4 * 2 * 3
    assert quality.metrics["supportStationClusterCount"] == 0
    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["supportToSupportTerminalCount"] == 0
    assert quality.metrics["waleSupportBayFailCount"] == 0
    # The old vertex-pair transition logic produced about 47 stations per level.
    assert max(quality.metrics["mainSupportCountByLevel"].values()) <= 34


def test_v333_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 33, 0)
