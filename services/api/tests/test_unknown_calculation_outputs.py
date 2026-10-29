"""Real failed solver outputs must stay unknown through result enrichment."""
import json
from types import SimpleNamespace
import numpy as np
import pytest
from app.calculation import global_coupled as gc, wall_internal_force as wf
from app.calculation.result_enrichment import enrich_calculation_result
from app.schemas.domain import (CalculationCase, CalculationResult, ConstructionStage, GlobalCoupledSystemResult, ExcavationModel, Polyline2D, RetainingSystem,
                               Point2D, PressurePoint, PressureProfile, Project, StageCalculationResult, WallInternalForceResult)

@pytest.fixture
def failed_result(monkeypatch):
    pressure=PressureProfile(points=[PressurePoint(depth=z,elevation=-z,earth_pressure=6*z,water_pressure=0,total_pressure=6*z) for z in (0.,5.,10.,15.)])
    def fail_matrix(*args,**kwargs): return None, {'blocked':True,'message':'injected singular matrix','conditionGrade':{'grade':'blocked','status':'fail'}}
    monkeypatch.setattr(gc,'solve_scaled_symmetric',fail_matrix)
    coupled=gc.solve_global_wall_wale_support_system(pressure_profile=pressure,segment=SimpleNamespace(start=Point2D(x=0,y=0),end=Point2D(x=10,y=0),length=10.),face_code='F1',active_supports=[],top_elevation=0.,excavation_elevation=-10.,wall_bottom_elevation=-15.,wall_thickness=.8,concrete_grade='C35',soil_profile=[],stage_id='stage')
    def fail_wall(*args,**kwargs): raise np.linalg.LinAlgError('injected singular matrix')
    monkeypatch.setattr(wf.np.linalg,'solve',fail_wall)
    wall=wf.analyze_wall_on_elastic_foundation([],[],10.,-100.,-100.,0.,0.,-15.,.8,'C35',pressure_profile=pressure)
    stage=ConstructionStage(id='stage',name='excavation',excavation_elevation=-10.)
    row=StageCalculationResult(stage_id=stage.id,segment_id='segment',pressure_profile=pressure,
        global_coupled_result=GlobalCoupledSystemResult.model_validate(coupled),
        wall_internal_force=WallInternalForceResult(segment_id='segment',stage_id=stage.id,max_moment=wall['maxMoment'],max_shear=wall['maxShear'],max_displacement=wall['maxDisplacement'],method=wall['method']))
    project=Project(name='unknown results',retaining_system=RetainingSystem(),excavation=ExcavationModel(outline=Polyline2D(points=[]),top_elevation=0.,bottom_elevation=-10.,depth=10.)); case=CalculationCase(name='case',stages=[stage]); result=CalculationResult(project_id=project.id,case_id=case.id,stage_results=[row])
    return project,case,result


def test_failed_wall_displacement_remains_unknown_in_catalog(failed_result):
    project,case,result=failed_result; enrich_calculation_result(project,case,result)
    stage=result.result_catalog['stageMatrix'][0]; envelope=result.result_catalog['wallEnvelopes'][0]
    assert stage['maximumWallDisplacementMm'] is None
    assert stage['status']!='pass'
    assert envelope['maximumDisplacementMm'] is None
    assert envelope['displacementStageId'] is None
    assert envelope['displacementStatus']=='unavailable'


def test_failed_wall_object_does_not_count_as_complete(failed_result):
    project,case,result=failed_result; enrich_calculation_result(project,case,result)
    wall=next(row for row in result.result_completeness['domains'] if row['domainId']=='wall_results')
    assert wall['status']!='pass'
    assert wall['coveragePercent']==0.


def test_missing_numerical_metrics_are_not_perfect_zero(failed_result):
    project,case,result=failed_result
    result.stage_results[0].global_coupled_result.equilibrium_diagnostics={}
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['maximumRelativeResidual'] is None
    assert result.numerical_health['maximumScaledConditionNumber'] is None
    assert result.numerical_health['status']!='pass'


def test_no_global_system_has_no_numerical_pass(failed_result):
    project,case,result=failed_result; result.stage_results[0].global_coupled_result=None
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['status']!='pass'
    assert result.numerical_health['maximumRelativeResidual'] is None


def test_mixed_wall_envelope_cannot_hide_unknown_stage(failed_result):
    project,case,result=failed_result; known=result.stage_results[0].model_copy(deep=True)
    known.stage_id='known'; known.wall_internal_force.max_displacement=12.
    result.stage_results.append(known); enrich_calculation_result(project,case,result)
    envelope=result.result_catalog['wallEnvelopes'][0]
    assert envelope['maximumDisplacementMm'] is None
    assert envelope['maximumKnownDisplacementMm']==12.
    assert envelope['displacementStatus']=='partial'


def test_catalog_is_json_finite_when_stage_metadata_missing(failed_result):
    project,case,result=failed_result; case.stages=[]
    enrich_calculation_result(project,case,result)
    json.dumps(result.result_catalog,allow_nan=False)

def test_missing_wall_object_cannot_disappear_from_envelope(failed_result):
    project,case,result=failed_result; known=result.stage_results[0].model_copy(deep=True)
    known.stage_id='known'; known.wall_internal_force.max_displacement=12.
    result.stage_results[0].wall_internal_force=None
    result.stage_results.append(known); enrich_calculation_result(project,case,result)
    envelope=result.result_catalog['wallEnvelopes'][0]
    assert envelope['maximumDisplacementMm'] is None
    assert envelope['displacementStatus']=='partial'


def test_real_zero_displacement_remains_available(failed_result):
    project,case,result=failed_result; result.stage_results[0].wall_internal_force.max_displacement=0.
    enrich_calculation_result(project,case,result)
    assert result.result_catalog['wallEnvelopes'][0]['maximumDisplacementMm']==0.
    assert result.result_catalog['wallEnvelopes'][0]['displacementStatus']=='complete'


def test_serviceability_cannot_pass_unknown_displacement(failed_result):
    from app.services.serviceability_service import evaluate_long_term_serviceability
    project,case,result=failed_result; project.calculation_results=[result]
    report=evaluate_long_term_serviceability(project)
    assert report['summary']['shortTermDisplacementMm'] is None
    assert report['summary']['longTermDisplacementMm'] is None
    assert report['summary']['displacementStatus']=='manual_review'
    assert report['status']!='pass'


def test_calculation_diagnostics_keep_unknown_wall_envelope(failed_result):
    from app.services.calculation_diagnostics import _wall_envelopes
    project,case,result=failed_result
    row=_wall_envelopes(project,result.stage_results)[0]
    assert row['maxDisplacementMm'] is None
    assert row['governingStageId'] is None


def test_geotechnical_projection_and_mobilization_require_displacement(failed_result):
    from app.calculation.nonlinear_geotechnical import build_nonlinear_geotechnical_assurance
    from app.schemas.domain import WallInternalForcePoint
    project,case,result=failed_result
    result.stage_results[0].wall_internal_force.points=[WallInternalForcePoint(depth=5.,elevation=-5.,shear=1.,moment=1.,displacement=None)]
    report=build_nonlinear_geotechnical_assurance(project,result)
    assert all(row['projectedMaxDisplacementMm'] is None for row in report['uncertaintyCases'])
    assert report['maximumMobilizedResistanceRatio'] is None
    assert report['minimumTangentStiffnessRatio'] is None
    point=report['stageSegments'][0]['points'][0]
    assert point['displacementMm'] is None
    assert point['mobilizedResistanceRatio'] is None
    assert point['tangentStiffnessRatio'] is None
    assert report['status']!='pass'
    assert report['formalUseAllowed'] is False


def test_trace_unknown_displacement_has_no_invented_capacity(failed_result):
    from app.services.calculation_trace import build_calculation_trace
    project,case,result=failed_result; project.calculation_results=[result]
    report=build_calculation_trace(project)
    entry=next(row for row in report['entries'] if row['category']=='wall_deformation')
    assert entry['demandValue'] is None
    assert entry['capacityValue'] is None
    assert entry['utilization'] is None
    assert entry['status']=='manual_review'


def test_diagnostics_cannot_claim_stiffness_pass_with_unknown_wall(failed_result):
    from app.services.calculation_diagnostics import build_calculation_diagnostics
    project,case,result=failed_result
    report=build_calculation_diagnostics(project,case,result.stage_results,[])
    assert report['status']!='pass'
    assert report['strengthDesignLoop']['stiffnessStatus']!='pass'


def _set_solved_standard_and_design(row):
    """Use real domain objects with the explicit evidence emitted by stage_solver."""
    solved=row.global_coupled_result.model_copy(deep=True,update={
        'fallback':False,'reason':None,'ill_conditioned_blocked':False,
        'max_wall_displacement':.001,'condition_number':10.,'scaled_condition_number':10.,
        'condition_grade':{'grade':'good','status':'pass'},'numerical_gate':{'blocked':False},
        'equilibrium_diagnostics':{'relativeResidual':0.},
    })
    row.global_coupled_result=solved
    row.design_global_coupled_result=solved.model_copy(deep=True)
    row.wall_internal_force.max_displacement=1.
    row.wall_internal_force.max_moment_design=2.
    row.wall_internal_force.max_shear_design=2.
    row.coupled_system_result={'solveEvidence':{'wallSolvers':{
        basis:{'status':'solved','fallback':False,'maximumDisplacementMm':1.}
        for basis in ('standard','design')
    }}}


def test_design_global_failure_cannot_hide_behind_standard_numerical_pass(failed_result):
    project,case,result=failed_result; row=result.stage_results[0]
    failed=row.global_coupled_result.model_copy(deep=True)
    _set_solved_standard_and_design(row)
    row.design_global_coupled_result=failed
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['status']!='pass'
    stage=result.result_catalog['stageMatrix'][0]
    assert stage['status']!='pass'
    assert result.numerical_health['basisAvailability']['standard']['status']=='pass'
    assert result.numerical_health['basisAvailability']['design']['status']!='pass'
    assert stage['basisAvailability']['design']['status']!='pass'
    assert result.numerical_health['summaryBasis']=='standard'
    assert result.numerical_health['maximumScaledConditionNumber']==10.


def test_design_fd_unavailable_blocks_generic_health_and_stage_status(failed_result):
    project,case,result=failed_result; row=result.stage_results[0]
    _set_solved_standard_and_design(row)
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['status']=='pass'
    row.wall_internal_force.max_moment_design=None
    row.wall_internal_force.max_shear_design=None
    row.coupled_system_result['solveEvidence']['wallSolvers']['design']={
        'status':'unavailable','fallback':True,'maximumDisplacementMm':None,
    }
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['status']!='pass'
    stage=result.result_catalog['stageMatrix'][0]
    assert stage['status']!='pass'
    assert stage['basisAvailability']['standard']['status']=='pass'
    assert stage['basisAvailability']['design']['unavailableWallSolverCount']==1

@pytest.mark.parametrize('basis',['standard','design'])
def test_solver_numerical_warning_survives_basis_health_summary(failed_result,basis):
    project,case,result=failed_result; row=result.stage_results[0]
    _set_solved_standard_and_design(row)
    coupled=row.global_coupled_result if basis=='standard' else row.design_global_coupled_result
    coupled.numerical_gate={'status':'warning','blocked':False,'regularized':True}
    coupled.equilibrium_diagnostics={'relativeResidual':1e-7}
    enrich_calculation_result(project,case,result)
    assert result.numerical_health['basisAvailability'][basis]['status']=='warning'
    assert result.numerical_health['status']=='warning'
    assert result.result_catalog['stageMatrix'][0]['status']=='warning'
