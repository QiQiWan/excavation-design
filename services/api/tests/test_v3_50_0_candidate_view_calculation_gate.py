from __future__ import annotations

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.support_layout_optimizer import SUPPORT_CANDIDATE_CONTRACT_VERSION
from app.services.support_layout_repair import auto_repair_support_layout
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.tasks.manager import TaskManager


def _stepped_project() -> Project:
    points = [
        (-115, -14), (-99, -14), (-99, -12), (-39, -12), (-39, -16.5),
        (-13, -16.5), (-13, -13), (98, -13), (98, -14.5), (115, -14.5),
        (115, 14.5), (98, 14.5), (98, 13), (-13, 13), (-13, 16.5),
        (-39, 16.5), (-39, 12), (-99, 12), (-99, 14), (-115, 14),
    ]
    excavation = make_excavation_model(
        "harvest-lake-v350",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in points], closed=True),
        0.0,
        -16.6,
    )
    project = Project(name="harvest-lake-v350", excavation=excavation)
    project.design_settings.design_basis_confirmed = True
    project.design_settings.bearing_capacity_kpa = 220.0
    project.retaining_system = auto_diaphragm_wall(excavation)
    return project


def test_candidates_record_current_geometry_contract(monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_PRODUCT_MODE", "core")
    monkeypatch.setenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12")
    project = _stepped_project()
    repair = auto_repair_support_layout(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={"requireDiverseSchemes": True, "coreMode": True, "maxTrials": 12},
    )
    assert repair.selected_candidate_id
    assert repair.candidates
    assert all(
        (candidate.variable_summary or {}).get("candidateContractVersion") == SUPPORT_CANDIDATE_CONTRACT_VERSION
        for candidate in repair.candidates
    )


def test_calculation_gate_recovers_legacy_diagnostic_candidates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PITGUARD_PRODUCT_MODE", "core")
    monkeypatch.setenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12")
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "v350.sqlite3"))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")

    project = _stepped_project()
    repair = auto_repair_support_layout(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={"requireDiverseSchemes": True, "coreMode": True, "maxTrials": 12},
    )
    assert repair.candidates and repair.selected_candidate_id

    # Reproduce the persisted V3.48 state: visible diagnostic cards, no adopted
    # support system and no candidate contract marker.
    for candidate in repair.candidates:
        candidate.variable_summary = dict(candidate.variable_summary or {})
        candidate.variable_summary.pop("candidateContractVersion", None)
        candidate.variable_summary["capabilityOutcome"] = "controlled_block"
        candidate.variable_summary["formalSchemeEligible"] = False
        candidate.hard_constraints = dict(candidate.hard_constraints or {})
        candidate.hard_constraints["passed"] = False
    repair.selected_candidate_id = None
    project.retaining_system.supports = []
    project.retaining_system.columns = []

    store = SQLiteProjectStore(tmp_path / "v350.sqlite3")
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store)
    loaded = repo.require(project.id)
    manager = TaskManager()

    recovery = manager._attempt_legacy_topology_recovery(repo, loaded)
    assert recovery["recovered"] is True
    assert loaded.retaining_system is not None
    assert len(loaded.retaining_system.supports) > 0
    assert loaded.retaining_system.support_layout_repair is not None
    assert loaded.retaining_system.support_layout_repair.selected_candidate_id
    assert all(
        (candidate.variable_summary or {}).get("candidateContractVersion") == SUPPORT_CANDIDATE_CONTRACT_VERSION
        for candidate in loaded.retaining_system.support_layout_repair.candidates
    )
