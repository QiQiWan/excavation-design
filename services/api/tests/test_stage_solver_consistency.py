"""Real stage solves protecting the load/spring hand-off contract."""
from types import SimpleNamespace

import pytest

from app.calculation import stage_solver
from app.calculation.stage_loads import build_stage_loads
from app.calculation.stage_state import build_stage_states
from app.schemas.domain import (CalculationCase, ConstructionStage, DesignSettings,
    ExcavationSegment, GeologicalLayer, MaterialDefinition, Point2D,
    SectionDefinition, SoilParameters, SupportElement)


def fixture():
    layers = [GeologicalLayer(stratum_code="S", stratum_name="synthetic", top_elevation=0,
        bottom_elevation=-30, thickness=30, parameters=SoilParameters(unit_weight=18,
        saturated_unit_weight=20, cohesion=0, friction_angle=30, horizontal_subgrade_modulus=8000))]
    support = SupportElement(id="s", code="S1", level_index=1, elevation=-2,
        start=Point2D(x=5, y=0), end=Point2D(x=5, y=20), span_length=20,
        start_face_code="F1", end_face_code="F2", section=SectionDefinition(width=.8,height=.8),
        material=MaterialDefinition(name="Concrete",grade="C35"))
    segment = ExcavationSegment(id="face",name="F1",start=Point2D(x=0,y=0),end=Point2D(x=10,y=0),
        length=10,outward_normal=Point2D(x=0,y=-1),midpoint=Point2D(x=5,y=0),chainage=0)
    settings = DesignSettings(load_gamma_g=1.5,load_gamma_q=1.8,load_psi=.5,importance_factor=1.2)
    project = SimpleNamespace(excavation=SimpleNamespace(top_elevation=0.,bottom_elevation=-10.),
        design_settings=settings,retaining_system=SimpleNamespace(supports=[support],wale_beams=[]))
    stage = ConstructionStage(id="stage",name="test",excavation_elevation=-10,
        active_support_ids=[support.id],stage_type="support_installation",surcharge=30)
    case = CalculationCase(name="case",stages=[stage])
    state = build_stage_states(project,case)[stage.id]
    wall = SimpleNamespace(thickness=.8,bottom_elevation=-16.,concrete_grade="C35")
    section = SimpleNamespace(layers=layers)
    loads = build_stage_loads(layers,10.,-30.,-30.,30.,0.,16.,settings,step=.5)
    return dict(project=project,segment=segment,stage=stage,section=section,wall=wall,
        loads=loads,state=state,segment_supports=[support],support_stiffness_factor=1.7)


def test_stage_solver_hands_final_global_springs_to_wall_without_refactoring():
    kwargs = fixture()
    result = stage_solver.solve_stage_response(**kwargs)
    assert not result["global"].fallback
    global_spring = result["global"].support_reactions[0].spring_stiffness
    wall_spring = result["wall_raw"]["supportReactions"][0]["springStiffness"]
    assert wall_spring * kwargs["segment"].length == pytest.approx(global_spring,abs=.02)
    design_global_spring = result["global_design"].support_reactions[0].spring_stiffness
    design_wall_spring = result["wall_design_raw"]["supportReactions"][0]["springStiffness"]
    assert design_wall_spring * kwargs["segment"].length == pytest.approx(design_global_spring,abs=.02)


def test_stage_solver_consumes_factored_load_once_and_returns_actual_design_response(monkeypatch):
    kwargs = fixture()
    calls = []
    real_wall_solver = stage_solver.analyze_wall_on_elastic_foundation

    def observe_real_solve(**parameters):
        raw = real_wall_solver(**parameters)
        calls.append((parameters["pressure_profile"],raw))
        return raw

    monkeypatch.setattr(stage_solver,"analyze_wall_on_elastic_foundation",observe_real_solve)
    result = stage_solver.solve_stage_response(**kwargs)
    assert len(calls)==2
    # z=5 dry: standard=18*5/3+30/3=40 kPa;
    # design=1.2*(1.5*30+1.8*.5*10)=64.8 kPa.
    assert calls[0][0].points[10].total_pressure == pytest.approx(40)
    assert calls[1][0].points[10].total_pressure == pytest.approx(64.8)
    assert calls[0][0].model_dump() == kwargs["loads"].standard_profile.model_dump()
    assert calls[1][0].model_dump() == kwargs["loads"].design_profile.model_dump()
    assert result["wall"].max_moment_design == abs(calls[1][1]["maxMoment"])
    assert result["wall"].max_shear_design == abs(calls[1][1]["maxShear"])
    assert result["wall"].max_displacement == abs(calls[0][1]["maxDisplacement"])
    assert result["wall"].load_combination_factor is None


def test_stage_solver_longterm_factor_reaches_wall_ei():
    baseline=stage_solver.solve_stage_response(**fixture(),long_term_stiffness_factor=1.)
    reduced=stage_solver.solve_stage_response(**fixture(),long_term_stiffness_factor=.6)
    assert reduced["wall_raw"]["EI"] == pytest.approx(.6*baseline["wall_raw"]["EI"],abs=.002)
    assert reduced["wall"].max_displacement != baseline["wall"].max_displacement


def test_fd_fallback_without_elevation_preserves_unknown_displacement(monkeypatch):
    import numpy as np
    from app.calculation import wall_internal_force
    real_stage_wall = stage_solver.analyze_wall_on_elastic_foundation
    real_numpy_solve = np.linalg.solve
    def fail_fd_only(**kwargs):
        def fail(*args, **kw):
            raise np.linalg.LinAlgError("injected FD failure")
        monkeypatch.setattr(wall_internal_force.np.linalg, "solve", fail)
        try:
            return real_stage_wall(**kwargs)
        finally:
            monkeypatch.setattr(wall_internal_force.np.linalg, "solve", real_numpy_solve)
    monkeypatch.setattr(stage_solver, "analyze_wall_on_elastic_foundation", fail_fd_only)
    result = stage_solver.solve_stage_response(**fixture())
    assert result["wall"].max_displacement is None
    assert all(point.displacement is None for point in result["wall"].points)


def test_design_fd_failure_cannot_publish_simplified_envelope_as_solved_design(monkeypatch):
    import numpy as np
    from app.calculation import wall_internal_force
    real_wall = stage_solver.analyze_wall_on_elastic_foundation
    real_solve = np.linalg.solve
    call_count = 0

    def fail_only_design_wall(**parameters):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return real_wall(**parameters)
        def failure(*args, **kwargs):
            raise np.linalg.LinAlgError("injected design FD failure")
        monkeypatch.setattr(wall_internal_force.np.linalg, "solve", failure)
        try:
            return real_wall(**parameters)
        finally:
            monkeypatch.setattr(wall_internal_force.np.linalg, "solve", real_solve)

    monkeypatch.setattr(stage_solver, "analyze_wall_on_elastic_foundation", fail_only_design_wall)
    result = stage_solver.solve_stage_response(**fixture())
    assert not result["global"].fallback
    assert not result["global_design"].fallback
    assert result["wall"].max_displacement is not None
    assert result["wall_design_raw"]["maxMoment"] > 0  # A diagnostic proxy exists.
    assert result["wall"].max_moment_design is None
    assert result["wall"].max_shear_design is None
    assert result["wall"].design_points == []
    assert result["evidence"]["wallSolvers"]["design"]["status"] == "unavailable"
    assert result["evidence"]["wallSolvers"]["standard"]["status"] == "solved"
