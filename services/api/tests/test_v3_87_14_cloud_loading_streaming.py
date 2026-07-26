from __future__ import annotations

import json
from pathlib import Path

from app.routers.projects import get_project
from app.routers.rebar import _compact_rebar_scheme_for_read
from app.schemas.domain import Project
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def test_rebar_summary_prioritizes_problems_and_bounds_checks() -> None:
    checks = [
        {"checkId": f"pass-{index}", "status": "pass", "hostCode": f"P-{index}"}
        for index in range(1000)
    ] + [
        {"checkId": f"fail-{index}", "status": "fail", "hostCode": f"F-{index}"}
        for index in range(5)
    ]
    scheme = {
        "checks": checks,
        "wallZones": [{"zoneId": "W-1"}],
        "supportSchemes": [{"hostId": "S-1"}],
        "beamNodeSchemes": [],
        "constructability": {"checks": checks[:100], "summary": {"checkCount": 100}},
    }
    compact = _compact_rebar_scheme_for_read(scheme, detail="summary", check_limit=60)
    assert len(compact["checks"]) == 60
    assert [row["status"] for row in compact["checks"][:5]] == ["fail"] * 5
    assert compact["listSummary"]["checks"] == {"total": 1005, "returned": 60, "truncated": True}
    assert len(compact["constructability"]["checks"]) == 24


def test_shell_profile_removes_large_rebar_review_arrays_without_mutating_workspace(tmp_path: Path) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = Project(name="Cloud shell")
    raw = project.model_dump(mode="json", by_alias=True)
    raw["retainingSystem"] = {
        "id": "ret-1",
        "type": "diaphragm_wall_with_internal_bracing",
        "rebarDesignScheme": {
            "status": "warning",
            "summary": {"checkCount": 2000},
            "checks": [{"checkId": f"C-{index}", "status": "pass", "message": "x" * 200} for index in range(2000)],
            "constructability": {
                "checks": [{"checkId": f"K-{index}", "status": "pass", "message": "y" * 200} for index in range(500)],
            },
        },
    }
    store.upsert(raw)
    repo = ProjectRepository(store)

    workspace_response = get_project(project.id, profile="workspace", result_history_limit=1, repo=repo)
    shell_response = get_project(project.id, profile="shell", result_history_limit=1, repo=repo)
    workspace = json.loads(workspace_response.body)
    shell = json.loads(shell_response.body)

    assert len(workspace["retainingSystem"]["rebarDesignScheme"]["checks"]) == 2000
    assert shell["retainingSystem"]["rebarDesignScheme"]["checks"] == []
    assert shell["retainingSystem"]["rebarDesignScheme"]["constructability"]["checks"] == []
    assert len(shell_response.body) < len(workspace_response.body) / 5
    assert shell_response.headers["x-pitguard-project-profile"] == "shell"
    assert int(shell_response.headers["x-pitguard-response-uncompressed-bytes"]) == len(shell_response.body)
