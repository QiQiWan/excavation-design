"""Synthetic mechanical regressions; these are not design certification."""
from types import SimpleNamespace
import math
import numpy as np
import pytest
from app.schemas.domain import GeologicalLayer, SoilParameters, Point2D, SupportElement, SectionDefinition, MaterialDefinition, PressureProfile, PressurePoint
from app.calculation import global_coupled as gc, wall_internal_force as wf
from app.rules.jgj120_2012.retaining_wall_rules import check_embedment_stability, _integrate_moment
from app.rules.jgj120_2012.stability_rules import check_overall_stability_circular_search

def soil():
    return [GeologicalLayer(stratum_code='S',stratum_name='synthetic',top_elevation=0.,bottom_elevation=-50.,thickness=50.,parameters=SoilParameters(unit_weight=18.,saturated_unit_weight=20.,cohesion=0.,friction_angle=30.,horizontal_subgrade_modulus=8000.))]
def support():
    return SupportElement(id='s',code='S1',level_index=1,elevation=-2.,start=Point2D(x=5,y=0),end=Point2D(x=5,y=20),span_length=20.,start_face_code='F1',end_face_code='F2',section=SectionDefinition(width=.8,height=.8),material=MaterialDefinition(name='Concrete',grade='C35'))
def segment(length=10.):
    return SimpleNamespace(start=Point2D(x=0,y=0),end=Point2D(x=length,y=0),length=length)
def profile():
    return PressureProfile(points=[PressurePoint(depth=float(z),elevation=-float(z),earth_pressure=6*z,water_pressure=0.,total_pressure=6*z) for z in np.arange(0,16.001,.25)])
def wall_kwargs():
    return dict(soil_profile=soil(),supports=[support()],excavation_depth=10.,groundwater_level_outside=-100.,groundwater_level_inside=-100.,surcharge=0.,top_elevation=0.,wall_bottom_elevation=-16.,wall_thickness=.8,concrete_grade='C35',segment=segment())
def global_kwargs():
    return dict(pressure_profile=profile(),segment=segment(),face_code='F1',active_supports=[support()],top_elevation=0.,excavation_elevation=-10.,wall_bottom_elevation=-16.,wall_thickness=.8,concrete_grade='C35',soil_profile=soil(),stage_id='stage',stage_type='excavation')
def capture_solver(monkeypatch):
    captured=[]; original=gc.solve_scaled_symmetric
    def capture(k,f,**kw):
        result=original(k,f,**kw); captured.append((k.copy(),f.copy(),result[0])); return result
    monkeypatch.setattr(gc,'solve_scaled_symmetric',capture)
    return captured

def test_interior_override_changes_wall_response():
    a=wf.analyze_wall_on_elastic_foundation(**wall_kwargs(),support_stiffness_overrides_kn_per_m={'s':1e3})
    b=wf.analyze_wall_on_elastic_foundation(**wall_kwargs(),support_stiffness_overrides_kn_per_m={'s':1e9})
    assert a['points']!=b['points']
    assert a['maxDisplacement']!=b['maxDisplacement']
def test_zero_override_is_no_restraint():
    a=wf.analyze_wall_on_elastic_foundation(**wall_kwargs(),support_stiffness_overrides_kn_per_m={'s':0.})
    kw=wall_kwargs(); kw['supports']=[]
    b=wf.analyze_wall_on_elastic_foundation(**kw)
    assert a['points']==b['points']
    assert a['supportReactions'][0]['springStiffness']==0.
def test_supplied_pressure_is_consumed_without_rebuild(monkeypatch):
    def forbidden(**kw): raise AssertionError('supplied pressure must not be rebuilt')
    monkeypatch.setattr(wf,'calculate_lateral_pressure_profile',forbidden)
    result=wf.analyze_wall_on_elastic_foundation(**wall_kwargs(),pressure_profile=profile())
    assert result['points'][20]['netPressure']==pytest.approx(6*result['points'][20]['depth'])
def test_wall_solve_failure_retains_unknown_displacement(monkeypatch):
    def fail(*args): raise np.linalg.LinAlgError('test singularity')
    monkeypatch.setattr(wf.np.linalg,'solve',fail)
    result=wf.analyze_wall_on_elastic_foundation(**wall_kwargs())
    assert result['maxDisplacement'] is None
    assert all(p['displacementMm'] is None for p in result['points'])
def test_spatial_soil_factor_changes_wall_response():
    a=gc.solve_global_wall_wale_support_system(**global_kwargs(),soil_modulus_factor=.25)
    b=gc.solve_global_wall_wale_support_system(**global_kwargs(),soil_modulus_factor=4.)
    assert a['wallDisplacementProfile']!=b['wallDisplacementProfile']
def test_full_face_wall_load_and_stiffness_scale_together():
    a_kw=global_kwargs(); a_kw.update(active_supports=[],segment=segment(1.))
    b_kw=global_kwargs(); b_kw.update(active_supports=[],segment=segment(10.))
    a=gc.solve_global_wall_wale_support_system(**a_kw); b=gc.solve_global_wall_wale_support_system(**b_kw)
    assert a['maxWallDisplacement']==pytest.approx(b['maxWallDisplacement'],rel=1e-5)
def test_returned_spatial_dofs_still_satisfy_assembled_matrix(monkeypatch):
    captured=capture_solver(monkeypatch); result=gc.solve_global_wall_wale_support_system(**global_kwargs()); k,f,u=captured[-1]
    assert np.linalg.norm(k@u-f)/np.linalg.norm(f)<1e-8
    assert all(d['verticalReaction']==0. for d in result['columnVerticalDofs'])
    assert all(d['loadRepresented'] is False for d in result['columnVerticalDofs'])
def test_spatial_normal_stiffness_is_not_projected_twice(monkeypatch):
    captured=capture_solver(monkeypatch); kw=global_kwargs(); s=support(); s.end=Point2D(x=20,y=20); kw['active_supports']=[s]
    result=gc.solve_global_wall_wale_support_system(**kw); k,_,_=captured[-1]; dofs=result['dofs']
    wi=next(d['index'] for d in dofs if d['dofType']=='wale_horizontal'); si=next(d['index'] for d in dofs if d['dofType']=='support_axial')
    assert -k[wi,si]==pytest.approx(result['supportReactions'][0]['springStiffness'],abs=.001)
def test_final_excavation_does_not_activate_replacement_slab():
    props=dict(effectiveWidthM=10.,thicknessM=.4,elasticModulusMpa=30000.,connectionReduction=.7)
    assert gc._replacement_slab_state('final',10.,props)['status']=='not_active'
    assert gc._replacement_slab_state('excavation',10.,dict(props,active=True,activationDepthM=8.))['status']=='active'
def test_slab_stiffness_added_once_to_wall(monkeypatch):
    captured=capture_solver(monkeypatch); kw=global_kwargs()
    s2=support().model_copy(update={'id':'s2','code':'S2','start':Point2D(x=8,y=0),'end':Point2D(x=8,y=20)})
    kw['active_supports']=[support(),s2]; gc.solve_global_wall_wale_support_system(**kw); k0=captured[-1][0]
    props=dict(effectiveWidthM=10.,thicknessM=.4,elasticModulusMpa=30000.,connectionReduction=.7,activationDepthM=10.)
    kw.update(stage_type='bottom_slab',replacement_slab_properties=props)
    result=gc.solve_global_wall_wale_support_system(**kw); diff=captured[-1][0]-k0
    assert np.trace(diff)==pytest.approx(result['slabReplacementStiffness'])
    changed=np.flatnonzero(np.diag(diff)>1e-5); assert len(changed)==1
    assert result['dofs'][changed[0]]['dofType']=='wall_horizontal'
def test_embedment_passive_overburden_starts_at_pit_bottom():
    check,trace=check_embedment_stability('w',soil(),10.,-14.,0.,-100.,-100.,0.,'二级'); active=profile()
    passive=PressureProfile(points=[p.model_copy(update={'total_pressure':54*max(p.depth-10,0)}) for p in active.points])
    _,am=_integrate_moment(active,0.,10.,14.); _,rm=_integrate_moment(passive,10.,14.,14.,net_with=active)
    assert trace['netPassiveMomentKnMPerM']==pytest.approx(rm,abs=.001)
    assert check.calculated_value==pytest.approx(rm/am,abs=.001)
    assert check.status!='pass'
def test_overall_screen_does_not_raise_raw_factor_or_certify():
    result=check_overall_stability_circular_search('e',soil(),10.,4.,0.,-10.,0.,'二级',20.); candidates=[]
    for radius in [11.,12.5,15.,17.5,20.,25.,30.]:
        for offset in [7.,10.,15.,20.,25.]:
            theta=2*math.asin(min(min(2*radius*.92,math.hypot(offset,14.))/(2*radius),.98)); angle=max(theta/2,math.radians(8)); weight=18*.5*offset*12
            candidates.append((weight*math.cos(angle)*math.tan(math.pi/6)+.15*18*16)/(weight*math.sin(angle)))
    assert result.calculated_value==pytest.approx(min(candidates),abs=.001)
    assert result.status!='pass'
    assert 'unverified' in result.formula.lower()

@pytest.mark.parametrize('stiffness',[0.,1e3,1e9])
def test_wall_assembly_and_recovery_use_identical_stiffness(monkeypatch,stiffness):
    matrices=[]; original=wf.np.linalg.solve
    def capture(k,f):
        matrices.append(k.copy()); return original(k,f)
    monkeypatch.setattr(wf.np.linalg,'solve',capture)
    result=wf.analyze_wall_on_elastic_foundation(**wall_kwargs(),support_stiffness_overrides_kn_per_m={'s':stiffness})
    kw=wall_kwargs(); kw['supports']=[]; wf.analyze_wall_on_elastic_foundation(**kw)
    dz=result['meshStep']; idx=round(2./dz)
    assert (matrices[0][idx,idx]-matrices[1][idx,idx])*dz==pytest.approx(result['supportReactions'][0]['springStiffness'],abs=.001)


def test_serialized_spatial_dofs_satisfy_matrix(monkeypatch):
    captured=capture_solver(monkeypatch); result=gc.solve_global_wall_wale_support_system(**global_kwargs()); k,f,_=captured[-1]
    delivered=np.array([d['value'] for d in result['dofs']])
    assert np.linalg.norm(k@delivered-f)/np.linalg.norm(f)<1e-8


def test_failed_global_solve_keeps_unknown_displacements(monkeypatch):
    def fail(k,f,**kw): return None, {'blocked':True,'message':'test singularity'}
    monkeypatch.setattr(gc,'solve_scaled_symmetric',fail)
    result=gc.solve_global_wall_wale_support_system(**global_kwargs())
    assert result['fallback'] is True
    assert result['maxWallDisplacement'] is None
    assert all(p['horizontalDisplacement'] is None for p in result['wallDisplacementProfile'])
    assert all(p['value'] is None for p in result['dofs'])


def test_slab_added_once_in_condensed_branch(monkeypatch):
    monkeypatch.setattr(gc,'MAX_SPATIAL_SUPPORT_NODES',0)
    captured=capture_solver(monkeypatch); kw=global_kwargs(); gc.solve_global_wall_wale_support_system(**kw); k0=captured[-1][0]
    kw.update(stage_type='bottom_slab',replacement_slab_properties=dict(effectiveWidthM=10.,thicknessM=.4,elasticModulusMpa=30000.,connectionReduction=.7,activationDepthM=10.))
    result=gc.solve_global_wall_wale_support_system(**kw)
    assert np.trace(captured[-1][0]-k0)==pytest.approx(result['slabReplacementStiffness'])

def test_explicit_slab_restraint_changes_fd_response_at_its_depth():
    kw=wall_kwargs(); kw['supports']=[]
    baseline=wf.analyze_wall_on_elastic_foundation(**kw)
    result=wf.analyze_wall_on_elastic_foundation(**kw,lateral_restraints=[{'id':'slab','depth':10.,'stiffnessKnPerM':8.4e6}])
    assert result['points']!=baseline['points']
    reaction=result['supportReactions'][0]
    assert reaction['restraintSource']=='explicit_lateral_restraint'
    assert reaction['springStiffness']==pytest.approx(8.4e5)
    assert reaction['appliedDepth']==10.

@pytest.mark.parametrize('elevation',[-.25,-15.75,-16.])
def test_support_at_boundary_stencil_is_actually_assembled(elevation):
    kw=wall_kwargs(); kw['supports']=[support().model_copy(update={'elevation':elevation})]
    a=wf.analyze_wall_on_elastic_foundation(**kw,support_stiffness_overrides_kn_per_m={'s':0.})
    b=wf.analyze_wall_on_elastic_foundation(**kw,support_stiffness_overrides_kn_per_m={'s':1e9})
    assert a['points']!=b['points']

def test_assumed_slab_elevation_diagnostic_survives_spatial_handoff():
    kw=global_kwargs(); kw.update(stage_type='bottom_slab',replacement_slab_properties=dict(effectiveWidthM=10.,thicknessM=.4,elasticModulusMpa=30000.,connectionReduction=.7))
    result=gc.solve_global_wall_wale_support_system(**kw)
    assert result['slabReplacementComponents']['assumedExcavationBottom'] is True

def test_embedment_consumes_shared_components_without_rebuild(monkeypatch):
    from app.rules.jgj120_2012 import retaining_wall_rules as rw
    def forbidden(*args,**kw): raise AssertionError('provided pressure must not be rebuilt')
    monkeypatch.setattr(rw,'calculate_jgj120_lateral_pressure_profile',forbidden)
    frozen=profile().model_copy(update={'points':[
        p.model_copy(update={'active_earth_pressure':9*p.depth,'passive_earth_pressure':54*max(p.depth-10,0),
                             'outside_water_pressure':3*p.depth,'inside_water_pressure':2*max(p.depth-10,0),
                             'total_pressure':0.}) for p in profile().points]})
    _,trace=rw.check_embedment_stability('w',soil(),10.,-14.,0.,-100.,-100.,0.,'二级',pressure_profile=frozen)
    outward=frozen.model_copy(update={'points':[p.model_copy(update={'total_pressure':12*p.depth}) for p in frozen.points]})
    passive=frozen.model_copy(update={'points':[p.model_copy(update={'total_pressure':56*max(p.depth-10,0)}) for p in frozen.points]})
    _,am=_integrate_moment(outward,0.,10.,14.); _,rm=_integrate_moment(passive,10.,14.,14.,net_with=outward)
    assert trace['activeMomentKnMPerM']==pytest.approx(am,abs=.001)
    assert trace['netPassiveMomentKnMPerM']==pytest.approx(rm,abs=.001)

def test_zero_excavation_embedment_has_no_fabricated_factor():
    check,trace=check_embedment_stability('w',soil(),0.,-14.,0.,-100.,-100.,0.,'二级')
    assert check.calculated_value is None
    assert check.status=='manual_review'
    assert trace['applicability']=='not_applicable'

def test_effective_override_does_not_receive_support_or_transfer_factor_twice():
    kw=wall_kwargs(); kw.update(supports=[],transferred_supports=[support()],support_stiffness_factor=2.,transfer_stiffness_factor=1.5)
    result=wf.analyze_wall_on_elastic_foundation(**kw,support_stiffness_overrides_kn_per_m={'s':1e6})
    assert result['supportReactions'][0]['springStiffness']==pytest.approx(1e5)

def test_fd_wall_and_long_term_factors_are_separately_applied():
    kw=wall_kwargs(); kw['supports']=[]
    result=wf.analyze_wall_on_elastic_foundation(**kw,wall_stiffness_factor=.25,long_term_stiffness_factor=.6)
    baseline=wf.analyze_wall_on_elastic_foundation(**kw,wall_stiffness_factor=.25)
    assert result['maxDisplacement']!=baseline['maxDisplacement']
