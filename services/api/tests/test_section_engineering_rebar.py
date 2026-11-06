"""Engineering checks: fixed hand calculations and independent angular integrals."""
from __future__ import annotations
import importlib
import math
from copy import deepcopy
import numpy as np
import pytest
from pydantic import ValidationError
from app.section.models import verification_example


def api():
    try:
        return importlib.import_module('app.section.engineering_rebar')
    except ModuleNotFoundError:
        pytest.fail('Engineering circular checks are not implemented')


def schema():
    try:
        return importlib.import_module('app.section.engineering_models').EngineeringDesign
    except ModuleNotFoundError:
        pytest.fail('Explicit engineering input schema is not implemented')


def stages():
    return [{'id': 'S1', 'status': 'success', 'points': [
        {'depthM': 1., 'side': 'below', 'momentCharacteristicKnM': 120., 'shearCharacteristicKn': 80.},
        {'depthM': 2., 'side': 'above', 'momentCharacteristicKnM': -90., 'shearCharacteristicKn': -120.6}]},
        {'id': 'S2', 'status': 'success', 'points': [
        {'depthM': 1., 'side': 'below', 'momentCharacteristicKnM': 30., 'shearCharacteristicKn': 40.}]}]


def envelope():
    return {'complete': True, 'governing': {'shearAbs': {'valueKn': -120.6, 'stageId': 'S1', 'depthM': 2., 'side': 'above'},
        'momentAbs': {'valueKnM': 120., 'stageId': 'S1', 'depthM': 1., 'side': 'below'}}}


def nm_cases():
    return [dict(stage_id=s, axial_force_design_kn=n, axial_force_basis='Synthetic explicit constant stage axial force',
                 second_order_assessment='not_required_by_project_assessment', second_order_basis='Synthetic short member test only')
            for s,n in [('S1',0.),('S2',300.)]]


def shear():
    return dict(concrete_tensile_design_strength_mpa=1.43, concrete_strength_coefficient_beta_c=1.,
        coefficient_basis='Synthetic C30 beta c = 1', stirrup_design_strength_mpa=270.,
        effective_hoop_area_mm2=100.53096491487338, hoop_area_basis='Two effective legs of one phi8 closed circular hoop',
        loading_case='general_bending_nonprestressed', axial_force_design_kn=0., seismic_case='nonseismic',
        applicability_basis='Synthetic pure bending without torsion or prestress')


def detailing():
    return dict(hoop_arrangement='external_tangent_closed_circular', minimum_clear_cover_mm=50.,
        minimum_clear_spacing_mm=75., maximum_stirrup_spacing_mm=150., minimum_stirrup_diameter_mm=6.,
        criterion_basis='Explicit synthetic project geometric criteria')


def anchorage():
    return dict(bar_surface='deformed', seismic_case='nonseismic',
        concrete_tensile_design_strength_mpa_capped_c60=1.43, material_basis='Synthetic C30 joint concrete tensile design value',
        epoxy_coated=False, construction_disturbed=False, top_available_length_mm=600., bottom_available_length_mm=650.,
        transverse_diameter_mm=8., transverse_spacing_mm=128., applicability_basis='Straight bar in nonseismic beam/column-like anchorage zone; not a beam-column node')


def model():
    m=verification_example()
    m['reinforcement']['stirrup_spacing_mm']=128.
    return m


def report(design=None, stage_data=None):
    return api().check_engineering_rebar(model(),envelope(),design,stages() if stage_data is None else stage_data)


def by_id(r):
    return {c['id']:c for c in r['checks']}


def test_missing_engineering_context_does_not_confirm_project_defaults():
    r=report()
    assert r['formalDesignReady'] is False
    assert r['status']=='review_required'
    assert all(c['status']=='missing_input' for c in r['checks'])
    assert 'engineering_design.nm_cases' in r['requiredInputs']


def test_strict_schema_forbids_free_moment_override_nonfinite_and_duplicate_stage():
    cls=schema()
    d=cls.model_validate({'nm_cases':nm_cases()})
    assert d.nm_cases[0].axial_force_design_kn==0
    for bad in ({'nm_cases':[dict(nm_cases()[0],moment_design_knm=1)]},
                {'shear':dict(shear(),effective_hoop_area_mm2=float('nan'))},
                {'nm_cases':[nm_cases()[0],nm_cases()[0]]}):
        with pytest.raises(ValidationError): cls.model_validate(bad)


def test_circular_shear_matches_independent_numeric_hand_calculation():
    c=by_id(report({'shear':shear()}))['shear']
    assert c['status']=='pass'
    assert c['demand']==pytest.approx(150.75)
    assert c['details']['equivalentWidthMm']==616
    assert c['details']['equivalentDepthMm']==560
    assert c['details']['concreteContributionKn']==pytest.approx(345.30496)
    assert c['details']['stirrupContributionKn']==pytest.approx(118.75220230569417)
    assert c['details']['sectionLimitKn']==pytest.approx(1233.232)
    assert c['governingCase']['stageId']=='S1'
    assert c['governingCase']['signedCharacteristicValue']==-120.6


def test_shear_crushing_limit_cannot_be_overcome_by_adding_hoops():
    e=envelope(); e['governing']['shearAbs']['valueKn']=2000.
    s=dict(shear(),effective_hoop_area_mm2=2*math.pi*20**2/4)
    m=model();m['reinforcement']['stirrup_diameter_mm']=20.;m['reinforcement']['stirrup_spacing_mm']=40.
    c=by_id(api().check_engineering_rebar(m,e,{'shear':s},stages()))['shear']
    assert c['status']=='fail'
    assert c['capacity']==pytest.approx(1233.232)


def test_cover_is_outer_hoop_cover_and_spacing_is_chord_not_arc():
    checks=by_id(report({'detailing':detailing()}))
    c=checks['minimum_cover']
    assert c['status']=='fail'
    assert c['details']['longitudinalClearCoverMm']==50
    assert c['details']['outerHoopClearCoverMm']==42
    assert checks['bar_spacing']['details']['clearChordSpacingMm']==pytest.approx(95.54256741338664)
    assert checks['bar_spacing']['status']=='pass'
    assert checks['stirrup_detailing']['status']=='pass'


def test_anchorage_length_and_zone_confinement_remain_distinct_failures():
    c=by_id(report({'anchorage':anchorage(),'detailing':detailing()}))
    assert c['anchorage']['details']['basicLengthMm']==pytest.approx(634.4055944055945)
    assert c['anchorage']['status']=='fail'
    assert c['anchorage']['details']['ends'][0]['status']=='fail'
    assert c['anchorage']['details']['ends'][1]['status']=='pass'
    assert c['anchorage_confinement']['status']=='fail'
    assert c['anchorage_confinement']['details']['maximumSpacingMm']==90


def test_anchorage_corrections_are_applied_and_missing_detail_is_unknown():
    a=dict(anchorage(),epoxy_coated=True,construction_disturbed=True)
    c=by_id(report({'anchorage':a}))
    assert c['anchorage']['details']['requiredLengthMm']==pytest.approx(872.3076923076924)
    assert c['anchorage_confinement']['status']=='missing_input'


def test_nm_uses_both_signed_demands_and_axial_from_corresponding_stage_only():
    r=report({'nm_cases':nm_cases()}); c=by_id(r)['axial_flexural_interaction']
    rows=c['details']['cases']
    assert {(x['stageId'],x['momentDesignKnM'],x['axialForceDesignKn']) for x in rows}=={('S1',150.,0.),('S1',-112.5,0.),('S2',37.5,300.)}
    assert all(abs(x['axialResidualKn'])<1e-5 for x in rows)
    assert c['status']=='pass'


def test_nm_missing_stage_coverage_and_outside_compression_domain_are_not_passes():
    c=by_id(report({'nm_cases':nm_cases()[:1]}))['axial_flexural_interaction']
    assert c['status']=='missing_input'
    assert 'engineering_design.nm_cases[S2]' in c['requiredInputs']
    cases=nm_cases();cases[1]['axial_force_design_kn']=100000.
    c=by_id(report({'nm_cases':cases}))['axial_flexural_interaction']
    assert c['status']=='outside_method_scope'
    assert any(x['capacityKnM'] is None for x in c['details']['cases'])


def test_nm_zero_agrees_existing_validated_pure_bending_and_nonzero_independent_integral():
    from scipy.integrate import quad
    from scipy.optimize import brentq
    from app.section.models import freeze_section
    from app.section.reinforcement import circular_capacity
    m=model();actual=api().circular_nm_capacity(m,0.,sign=1)
    expected=circular_capacity(freeze_section(m).validated_input())
    assert actual['capacityKnM']==pytest.approx(expected['capacityKnM'],rel=1e-7)
    # Independent angular integration: dA=2 R^2 cos(theta)^2 dtheta.
    # Different coordinate, adaptive quadrature and Brent root.
    R=350.;rs=291.;As=math.pi*18**2/4; steel_y=rs*np.cos(np.arange(16)*2*math.pi/16)
    def force(c):
        def stress(y):
            e=.0033*(1-(R-y)/c); q=min(1,max(0,e/.002));return 14.3*(2*q-q*q)
        th0=math.asin(max(-1,1-c/R))
        f=quad(lambda t:stress(R*math.sin(t))*2*R*R*math.cos(t)**2,th0,math.pi/2,epsabs=.001,limit=200)[0]
        moment=quad(lambda t:stress(R*math.sin(t))*2*R**3*math.cos(t)**2*math.sin(t),th0,math.pi/2,epsabs=.01,limit=200)[0]
        for y in steel_y:
            steel=max(-360,min(360,200000*.0033*(1-(R-y)/c)))
            net=(steel-stress(y))*As;f+=net;moment+=net*y
        return f,moment
    c=brentq(lambda x:force(x)[0]-300000,1.,700.)
    got=api().circular_nm_capacity(m,300.,sign=1)
    assert got['neutralAxisMm']==pytest.approx(c,abs=.0001)
    assert got['capacityKnM']==pytest.approx(force(c)[1]/1e6,rel=1e-6)


def test_crack_width_is_never_passed_from_rectangle_or_missing_sls():
    r=report({'service':{'cases':[{'stage_id':'S1','axial_force_service_kn':0.,'characteristic_effect_scale':1.,'combination_basis':'Synthetic quasi-permanent combination explicit scale'}],
        'concrete_elastic_modulus_mpa':30000.,'long_term_basis':'Synthetic elastic instantaneous stress only', 'crack_width_limit_mm':.2,'limit_basis':'Synthetic test limit'}})
    c=by_id(r)
    assert c['crack_width']['status']=='outside_method_scope'
    assert c['crack_width']['capacity'] is None
    assert c['service_stress']['details']['cases'][0]['maximumTensileSteelStressMpa']>0
    assert 'crack_width' in r['unverifiedChecks']


def test_incomplete_envelope_never_generates_engineering_pass():
    r=api().check_engineering_rebar(model(),{'complete':False},{'shear':shear()},stages())
    assert r['status']=='not_calculated'
    assert all(c['status']=='not_calculated' for c in r['checks'])


def test_effective_hoop_area_cannot_exceed_two_given_circular_legs():
    c=by_id(report({'shear':dict(shear(),effective_hoop_area_mm2=101.)}))['shear']
    assert c['status']=='outside_method_scope'
    assert c['capacity'] is None
    assert c['details']['maximumTwoLegAreaMm2']==pytest.approx(100.53096491487338)


def test_nonzero_stage_axial_case_invalidates_full_envelope_zero_axial_shear():
    c=by_id(report({'shear':shear(),'nm_cases':nm_cases()}))['shear']
    assert c['status']=='outside_method_scope'
    assert c['capacity'] is None


def test_standalone_zero_axial_shear_does_not_claim_nm_coverage():
    c=by_id(report({'shear':shear()}))['shear']
    assert c['details']['axialConditionSource']=='independent_explicit_zero_axial_assessment'
    assert c['details']['axialStageCoverageConfirmed'] is False


def test_service_linear_stresses_double_with_explicit_sls_moment():
    a=api().circular_service_stress(model(),100.,30000.)
    b=api().circular_service_stress(model(),200.,30000.)
    assert b['maximumTensileSteelStressMpa']==pytest.approx(2*a['maximumTensileSteelStressMpa'])
    assert abs(a['axialResidualKn'])<1e-8
    assert abs(a['momentResidualKnM'])<1e-8
    # Independent transformed-section solution using direct sampled circular
    # strips and a scalar Brent root; no production quadrature or neutral axis.
    from scipy.optimize import brentq
    R=350.;ys=np.linspace(-R,R,100001);dy=ys[1]-ys[0]
    da=2*np.sqrt(np.maximum(0,R*R-ys*ys))*dy
    by=291*np.cos(np.arange(16)*2*np.pi/16);ab=np.pi*18**2/4
    def residual(c):
        ec=np.maximum(ys-(R-c),0)
        return 30000*np.dot(ec,da)+ab*np.sum(200000*(by-(R-c))-30000*np.maximum(by-(R-c),0))
    c=brentq(residual,1.,699.)
    ec=np.maximum(ys-(R-c),0)
    stiffness=30000*np.dot(ec*ys,da)+ab*np.dot(200000*(by-(R-c))-30000*np.maximum(by-(R-c),0),by)
    stress=200000*(by-(R-c))*100e6/stiffness
    assert a['neutralAxisMm']==pytest.approx(c,abs=2e-5)
    assert a['maximumTensileSteelStressMpa']==pytest.approx(-min(stress),rel=1e-6)


def test_verified_lap_length_is_numeric_but_layout_is_not_invented():
    lap={'arrangement':'tension_tied_lap','arrangement_basis':'Synthetic ordinary nonseismic flexural tension splice assessment',
         'seismic_case':'nonseismic','available_length_mm':1000.,'joint_area_ratio':.5,
         'transverse_diameter_mm':8.,'transverse_spacing_mm':90.}
    c=by_id(report({'lap':lap,'anchorage':anchorage(),'detailing':detailing()}))['lap']
    assert c['details']['lapLengthFactor']==1.4
    assert c['details']['requiredLengthMm']==pytest.approx(888.167832167832)
    assert c['details']['lengthStatus']=='pass'
    assert c['details']['confinementStatus']=='pass'
    assert c['status']=='outside_method_scope'  # no longitudinal splice locations supplied
    lap['available_length_mm']=800.;lap['transverse_spacing_mm']=128.
    c=by_id(report({'lap':lap,'anchorage':anchorage(),'detailing':detailing()}))['lap']
    assert c['status']=='fail'
    assert c['details']['lengthStatus']=='fail'
    assert c['details']['confinementStatus']=='fail'


def test_second_order_required_and_axial_tension_never_yield_capacity():
    cases=nm_cases();cases[0]['second_order_assessment']='required';cases[1]['axial_force_design_kn']=-10.
    c=by_id(report({'nm_cases':cases}))['axial_flexural_interaction']
    assert c['status']=='outside_method_scope'
    assert all(x['capacityKnM'] is None for x in c['details']['cases'])


def test_anchor_context_and_missing_hoop_fields_stay_explicit():
    m=model();m['reinforcement']['stirrup_diameter_mm']=None;m['reinforcement']['stirrup_spacing_mm']=None
    r=api().check_engineering_rebar(m,envelope(),{'shear':shear(),'detailing':detailing()},stages())
    c=by_id(r)
    assert c['minimum_cover']['status']=='missing_input'
    assert c['minimum_cover']['details']['outerHoopClearCoverMm'] is None
    assert c['shear']['status']=='missing_input'


def _review_service(cases):
    return {'cases':[{'stage_id':stage_id,'axial_force_service_kn':0.,'characteristic_effect_scale':1.,
                      'combination_basis':'Explicit synthetic stage service combination'} for stage_id in cases],
            'concrete_elastic_modulus_mpa':30000.,'long_term_basis':'Instantaneous elastic stress assessment only',
            'crack_width_limit_mm':.2,'limit_basis':'Explicit synthetic project crack criterion'}


def test_service_omitted_stage_retains_partial_stresses_but_reports_incomplete_coverage():
    stage_data=stages()
    stage_data[1]['points'][0]['momentCharacteristicKnM']=1000.
    r=report({'service':_review_service(['S1'])},stage_data)
    c=by_id(r)['service_stress']
    assert c['status']=='missing_input'
    assert 'engineering_design.service.cases[S2]' in c['requiredInputs']
    assert 'engineering_design.service.cases[S2]' in r['requiredInputs']
    assert 'service_stress' in r['unverifiedChecks']
    assert c['details']['requiredStageIds']==['S1','S2']
    assert c['details']['missingStageIds']==['S2']
    assert c['details']['coverageComplete'] is False
    assert c['details']['cases']
    assert all(row['stageId']=='S1' and row['maximumTensileSteelStressMpa']>0 for row in c['details']['cases'])
    assert by_id(r)['crack_width']['status']=='outside_method_scope'


def test_service_complete_stage_coverage_remains_calculated_not_passed():
    r=report({'service':_review_service(['S1','S2'])})
    c=by_id(r)['service_stress']
    assert c['status']=='calculated'
    assert c['requiredInputs']==[]
    assert c['details']['requiredStageIds']==['S1','S2']
    assert c['details']['missingStageIds']==[]
    assert c['details']['coverageComplete'] is True
    assert {row['stageId'] for row in c['details']['cases']}=={'S1','S2'}
    assert by_id(r)['crack_width']['status']=='outside_method_scope'
    assert r['formalDesignReady'] is False
