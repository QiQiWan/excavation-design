from __future__ import annotations

from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project
from app.services.adverse_scenario_execution import _bounded_scenario_seed, apply_formal_scenario, scenario_catalog
from app.services.core_workspace import build_core_workspace_status
from app.services.design_basis import build_design_basis
from app.services.design_service import auto_diaphragm_wall
from app.services.enterprise_library import resolve_enterprise_library, validate_enterprise_library
from app.services.excavation_service import make_excavation_model
from app.services.p3_detailing_closure import build_p3_detailing_closure


def _project() -> Project:
    excavation = make_excavation_model(
        "v347",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=30, y=0), Point2D(x=30, y=18), Point2D(x=0, y=18)], closed=True),
        0.0,
        -10.0,
    )
    project = Project(name="v347", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True
    project.design_settings.bearing_capacity_kpa = 180
    return project


def test_enterprise_library_is_versioned_and_validated() -> None:
    project = _project()
    resolved = resolve_enterprise_library(project)
    validation = validate_enterprise_library(project)
    assert resolved["library"]["libraryId"] == "pitguard_default"
    assert resolved["library"]["libraryVersion"].startswith("2026.07")
    assert validation["status"] == "pass"
    assert validation["summary"]["nodeTemplateCount"] >= 2
    assert validation["summary"]["rebarCombinationCount"] >= 2


def test_design_basis_exposes_enterprise_standards_and_impact_inputs() -> None:
    basis = build_design_basis(_project())
    assert basis["enterprise"]["validation"]["status"] == "pass"
    assert basis["enterprise"]["standardTemplate"]["safetyTargets"]["overall_stability"] >= 1.25
    assert basis["summary"]["enterpriseLibraryId"] == "pitguard_default"


def test_formal_adverse_scenario_modifies_a_bounded_calculation_seed() -> None:
    project = _project()
    project.design_settings.groundwater_level = -1.0
    project.design_settings.groundwater_level_inside = -8.0
    project.retaining_system.layout_summary = {"candidateSchemes": [{"large": "payload"}], "small": 1}
    seed = _bounded_scenario_seed(project)
    assumptions = apply_formal_scenario(seed, "DEWATERING_FAILURE")
    assert assumptions["groundwaterLevelInsideAfter"] > assumptions["groundwaterLevelInsideBefore"]
    assert "candidateSchemes" not in seed.retaining_system.layout_summary
    assert assumptions["scenarioInputHash"]
    codes = {row["code"] for row in scenario_catalog(project)}
    assert {"DEWATERING_FAILURE", "OVEREXCAVATION", "LOCAL_SEEPAGE"}.issubset(codes)


def test_core_status_exposes_p3_resources_without_heavy_artifacts() -> None:
    project = _project()
    project.advanced_engineering["formalAdverseScenarioSuite"] = {"summary": {"scenarioCount": 2}, "summaries": []}
    project.advanced_engineering["p3DetailingClosure"] = {"status": "warning", "summary": {"nodeSubmodelCount": 2}}
    status = build_core_workspace_status(project)
    assert status["formalAdverseScenarioSuite"]["summary"]["scenarioCount"] == 2
    assert status["p3DetailingClosure"]["summary"]["nodeSubmodelCount"] == 2
    assert status["enterpriseLibraryValidation"]["status"] == "pass"
    assert len(status["adverseScenarioCatalog"]) >= 5


def test_p3_detailing_closure_externalizes_full_payload_and_keeps_compact(monkeypatch) -> None:
    import app.services.p3_detailing_closure as service

    project = _project()
    project.calculation_results = [CalculationResult(projectId=project.id, caseId="case-1")]
    project.retaining_system.rebar_design_scheme = {"mode": "balanced", "wallZones": [], "supportSchemes": []}

    monkeypatch.setattr(service, "build_rebar_detailing", lambda *_args, **_kwargs: {
        "summary": {"barMarkCount": 5, "individualBarCount": 120},
        "designScheme": project.retaining_system.rebar_design_scheme,
        "deepDetailing": {"summary": {"couplerCount": 8}, "nodeHardware": {"embeddedItems": [{"id": "E1"}], "checks": []}, "embeddedItemCollisionChecks": []},
    })
    monkeypatch.setattr(service, "build_rebar_constructability", lambda *_args, **_kwargs: {"checks": [{"checkId": "C1", "category": "anchorage", "status": "warning", "message": "复核锚固"}]})
    monkeypatch.setattr(service, "evaluate_node_local_response", lambda *_args, **_kwargs: {"summary": {"nodeCount": 1, "nonlinearFERequiredCount": 1}, "nodes": [{"nodeId": "N1", "nodeCode": "N1", "supportCode": "", "designForceKn": 1000}]})
    monkeypatch.setattr(service, "build_node_submodels", lambda *_args, **_kwargs: {"summary": {"submodelCount": 1}, "submodels": [{"solverDeckFilename": "node_submodels/N1.inp"}]})
    monkeypatch.setattr(service, "build_calculix_input_deck", lambda _row: "*HEADING\nP3 NODE")
    monkeypatch.setattr(service, "evaluate_model_collisions", lambda *_args, **_kwargs: {"summary": {"hardCollisionCount": 0}, "collisions": []})
    monkeypatch.setattr(service, "build_coordination_optimization", lambda *_args, **_kwargs: {"issues": []})

    result = build_p3_detailing_closure(project)
    assert result["compact"]["summary"]["individualBarCount"] == 120
    assert result["compact"]["summary"]["nodeSubmodelCount"] == 1
    assert result["compact"]["status"] == "warning"
    assert result["full"]["solverDecks"]["node_submodels/N1.inp"].startswith("*HEADING")
