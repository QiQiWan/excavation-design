from __future__ import annotations

from pathlib import Path

from app.calculation.engine import build_default_construction_cases
from app.routers.calculation import build_cases, update_construction_stages
from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project, StageCalculationResult
from app.services.calculation_state import invalidate_calculation_state, mark_calculation_state_current
from app.services.construction_stages import (
    build_construction_stage_workspace,
    normalize_user_calculation_case,
    select_calculation_case_for_run,
    validate_calculation_case,
)
from app.services.deepening_readiness import calculation_readiness
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def _project() -> Project:
    excavation = make_excavation_model(
        "v353",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=30, y=0),
                Point2D(x=30, y=20), Point2D(x=0, y=20),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    project = Project(name="v353", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True
    project.calculation_cases = build_default_construction_cases(project)
    return project


def _add_current_result(project: Project, *, stage_count: int = 1) -> CalculationResult:
    wall = project.retaining_system.diaphragm_walls[0]
    case = project.calculation_cases[-1]
    result = CalculationResult(
        projectId=project.id,
        caseId=case.id,
        stageResults=[StageCalculationResult(
            stageId=f"{case.stages[-1].id}-{index}",
            segmentId=wall.segment_id,
            pressureProfile={"points": []},
        ) for index in range(stage_count)],
        checkSummary={"pass": 1, "warning": 0, "fail": 0},
    )
    project.calculation_results.append(result)
    mark_calculation_state_current(project, result.id)
    return result


def _repo(tmp_path: Path, monkeypatch) -> ProjectRepository:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "api")
    return ProjectRepository(SQLiteProjectStore(tmp_path / "pitguard.sqlite3"))


def test_latest_stage_evidence_is_externalized_then_selectively_loaded(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    project = _project()
    _add_current_result(project)
    repo.save(project)

    compact = repo.require_workspace(project.id)
    assert compact.calculation_results[-1].stage_results == []
    assert compact.calculation_results[-1].stage_result_summary["actualCount"] == 1
    assert compact.calculation_results[-1].stage_result_summary["storageState"] == "externalized"

    hydrated = repo.require_workspace_with_latest_calculation(project.id)
    readiness = calculation_readiness(hydrated)
    assert len(hydrated.calculation_results[-1].stage_results) == 1
    assert readiness["stageEvidenceState"] == "loaded"
    assert readiness["valid"] is True
    assert readiness["missingData"] == []


def test_missing_stage_artifact_is_reported_as_system_evidence_not_design_input(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    project = _project()
    _add_current_result(project)
    repo.save(project)
    stage_ref = next(
        item for item in repo.store.list_artifacts(project.id)
        if str(item.get("storageKey") or "").startswith("calculation:") and ":stages:" in str(item.get("storageKey"))
    )
    (tmp_path / "artifacts" / stage_ref["relativePath"]).unlink()

    hydrated = repo.require_workspace_with_latest_calculation(project.id)
    readiness = calculation_readiness(hydrated)
    assert readiness["stageEvidenceState"] == "artifact_missing"
    missing = next(item for item in readiness["missingData"] if item["code"] == "STAGE_ARTIFACT_MISSING")
    assert missing["type"] == "external_evidence_missing"
    assert missing["designStageAvailable"] is False


def test_partially_loaded_stage_chunks_never_pass_the_reinforcement_gate(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("PITGUARD_STAGE_RESULT_CHUNK_SIZE", "25")
    project = _project()
    _add_current_result(project, stage_count=30)
    repo.save(project)
    stage_refs = [
        item for item in repo.store.list_artifacts(project.id)
        if str(item.get("storageKey") or "").startswith("calculation:") and ":stages:" in str(item.get("storageKey"))
    ]
    assert len(stage_refs) == 2
    (tmp_path / "artifacts" / stage_refs[-1]["relativePath"]).unlink()

    hydrated = repo.require_workspace_with_latest_calculation(project.id)
    readiness = calculation_readiness(hydrated)
    assert readiness["stageEvidenceState"] == "partial"
    assert readiness["stageResultCount"] == 25
    assert readiness["expectedStageResultCount"] == 30
    assert readiness["stageEvidenceComplete"] is False
    assert readiness["valid"] is False


def test_user_defined_stage_case_is_validated_locked_and_preserved_for_calculation() -> None:
    project = _project()
    case = project.calculation_cases[-1].model_copy(deep=True)
    case.name = "经施工组织确认的分步工况"
    normalized = normalize_user_calculation_case(project, case)
    project.calculation_cases = [normalized]

    selected, decision = select_calculation_case_for_run(project)
    assert selected.id == normalized.id
    assert selected.name == "经施工组织确认的分步工况"
    assert decision["preserved"] is True
    assert decision["validation"]["valid"] is True

    invalid = normalized.model_copy(deep=True)
    invalid.stages[-1].excavation_elevation = project.excavation.top_elevation + 1.0
    validation = validate_calculation_case(project, invalid)
    assert validation["valid"] is False
    assert any(item["code"] == "STAGE_ELEVATION_OUTSIDE_EXCAVATION" for item in validation["issues"])


def test_legacy_build_cases_action_does_not_overwrite_locked_project_stages(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    project = _project()
    locked = normalize_user_calculation_case(project, project.calculation_cases[-1])
    locked.name = "锁定施工顺序"
    project.calculation_cases = [locked]
    repo.save(project)

    returned = build_cases(project.id, repo)
    assert returned[-1].id == locked.id
    assert returned[-1].name == "锁定施工顺序"
    persisted = repo.require(project.id)
    assert persisted.calculation_cases[-1].id == locked.id


def test_update_stage_route_locks_case_and_invalidates_previous_result(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    project = _project()
    _add_current_result(project)
    case = project.calculation_cases[-1].model_copy(deep=True)
    case.name = "项目确认工况 R1"
    repo.save(project)

    response = update_construction_stages(project.id, case, repo)
    assert response["case"]["source"] == "user_defined"
    assert response["case"]["locked"] is True
    persisted = repo.require(project.id)
    assert persisted.calculation_results == []
    assert persisted.calculation_cases[-1].name == "项目确认工况 R1"
    assert persisted.advanced_engineering["calculationState"]["status"] == "invalidated"
    assert persisted.advanced_engineering["requiresRecalculation"] is True


def test_stage_editor_workspace_explains_exact_owner_location_and_design_availability() -> None:
    workspace = build_construction_stage_workspace(_project())
    assert workspace["summary"]["stageCount"] >= 1
    fields = {item["field"]: item for item in workspace["inputGuide"]}
    assert fields["excavationElevation"]["location"]
    assert fields["activeSupportIds"]["provider"] == "支护结构设计"
    assert fields["groundwaterLevelInside"]["designStageAvailable"] is True
    assert all(item["action"] for item in workspace["inputGuide"])


def test_invalidation_preserves_confirmed_case_and_successful_calculation_clears_both_stale_flags() -> None:
    project = _project()
    original_case_id = project.calculation_cases[-1].id
    invalidate_calculation_state(project, reason="施工阶段已更新", preserve_cases=True)
    assert project.calculation_cases[-1].id == original_case_id
    assert project.advanced_engineering["requiresRecalculation"] is True
    result = _add_current_result(project)
    mark_calculation_state_current(project, result.id)
    assert project.advanced_engineering["requiresRecalculation"] is False
    assert "invalidationReason" not in project.advanced_engineering
    assert project.advanced_engineering["calculationState"]["requiresRecalculation"] is False


def test_frontend_exposes_stage_editor_and_latest_evidence_api() -> None:
    root = Path(__file__).resolve().parents[3]
    editor = (root / "apps" / "web" / "src" / "components" / "ConstructionStageEditor.tsx").read_text(encoding="utf-8")
    client = (root / "apps" / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "这些资料在哪里补齐" in editor
    assert "保存并锁定项目阶段" in editor
    assert "construction-stages" in client
    assert "latest-evidence" in client
