from __future__ import annotations

import json
from pathlib import Path

from app.schemas.domain import Project
from app.storage.database import CANDIDATE_PREVIEW_SCHEMA, SQLiteProjectStore, _compact_candidate_plan_geometry


def _geometry() -> dict:
    return {
        "outline": [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 10}, {"x": 0, "y": 10}],
        "supports": [{"id": "RS-1", "role": "ring_strut", "start": {"x": 0, "y": 5}, "end": {"x": 5, "y": 5}}],
        "columns": [{"id": "C-1", "location": {"x": 10, "y": 5}}],
        "transferBeams": [{"id": "TR-1", "code": "TR-1", "role": "transfer_ring_beam", "points": [{"x": 5, "y": 3}, {"x": 15, "y": 3}, {"x": 15, "y": 7}]}],
        "transferZones": [{"id": "TZ-1", "zoneType": "concave_transfer", "outline": [{"x": 5, "y": 3}, {"x": 15, "y": 3}, {"x": 15, "y": 7}, {"x": 5, "y": 7}]}],
        "obstacles": [{"id": "OBS-1", "points": [{"x": 8, "y": 4}, {"x": 9, "y": 4}, {"x": 9, "y": 5}]}],
    }


def test_candidate_preview_v3_retains_complete_transfer_load_path() -> None:
    compact = _compact_candidate_plan_geometry(_geometry())
    assert compact["previewSchema"] == CANDIDATE_PREVIEW_SCHEMA == "candidate-plan-v3"
    assert compact["supports"][0]["role"] == "ring_strut"
    assert compact["transferBeams"][0]["role"] == "transfer_ring_beam"
    assert len(compact["transferBeams"][0]["points"]) == 3
    assert compact["transferZones"][0]["outline"]
    assert compact["obstacles"][0]["points"]


def test_candidate_preview_cache_rebuilds_legacy_v1_rows(tmp_path: Path) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = Project(name="preview-v3-rebuild")
    raw = project.model_dump(mode="json", by_alias=True)
    raw["retainingSystem"] = {
        "id": "RET-1",
        "type": "diaphragm_wall_with_internal_bracing",
        "diaphragmWalls": [], "crownBeams": [], "waleBeams": [], "ringBeams": [],
        "supports": [], "columns": [], "supportNodes": [], "warnings": [], "replacementPath": [],
        "supportLayoutRepair": {"candidates": [{"id": "candidate-1", "rank": 1, "planGeometry": _geometry()}]},
    }
    store.upsert(raw)
    legacy = {"outline": _geometry()["outline"], "supports": _geometry()["supports"], "columns": [], "previewSchema": "candidate-plan-v1"}
    with store._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO project_candidate_previews(project_id,candidate_id,candidate_rank,plan_geometry,updated_at) VALUES(?,?,?,?,datetime('now'))",
            (project.id, "candidate-1", 1, json.dumps(legacy)),
        )
        conn.commit()
    bundle = store.get_candidate_preview_bundle(project.id)
    assert bundle["source"] != "preview_cache"
    geometry = bundle["previews"][0]["planGeometry"]
    assert geometry["previewSchema"] == "candidate-plan-v3"
    assert geometry["transferBeams"]
    assert geometry["transferZones"]
