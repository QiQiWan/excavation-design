"""JGJ120-2012 stability ledger for the 2020 Luoyu 3-3 historical case.

All numbers here are independent *conditional* engineering calculations or
rechecks of printed equations. A non-verified geotechnical input, water boundary,
active/passive pressure method or structural boundary ALWAYS blocks adoption.
The contract intentionally makes uncovered clauses visible.
"""
from __future__ import annotations

from math import exp, isfinite, pi, radians, tan, sqrt
from typing import Any

STABILITY_SCHEMA = 'pitguard.luoyu-stability-ledger/3.89.9'
_REQUIRED_THRESHOLD = {'I': 1.25, 'II': 1.20, 'III': 1.15}
_HEAVE_THRESHOLD = {'I': 1.8, 'II': 1.6, 'III': 1.4}


def finite_number(value: Any, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite number')
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{name} must be a finite number') from exc
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f'{name} outside allowed physical range')
    return result


def capacity_factors(phi_deg: float) -> tuple[float, float]:
    """JGJ120-2012 4.2.4 (Nq, Nc); stable phi=0 limit Nc=pi+2."""
    phi = finite_number(phi_deg, 'soil phi')
    if not 0 <= phi <= 45:
        raise ValueError('phi outside independently reviewed pressure-method range')
    angle = radians(phi)
    if abs(angle) < 1e-12:
        return (1., pi + 2.)
    nq = tan(pi / 4 + angle / 2)**2 * exp(pi*tan(angle))
    return nq, (nq-1.)/tan(angle)


def layer_at_site(facts: dict, site_z: float) -> dict:
    position = 0.
    layers = facts['soilLayersOriginalGround']
    for i, layer in enumerate(layers):
        position += float(layer['thicknessM'])
        if site_z < position-1e-10 or i == len(layers)-1:
            return layer
    raise ValueError('Empty geological column')


def soil_overburden(facts: dict, lo: float, hi: float) -> float:
    """Unadjusted reported design gamma integral, kPa; wet-unit-weight unresolved."""
    if hi <= lo: return 0.
    total, pos = 0., 0.
    for layer in facts['soilLayersOriginalGround']:
        new_pos = pos + float(layer['thicknessM'])
        thickness = max(0., min(hi, new_pos) - max(lo, pos))
        total += thickness * float(layer['gammaNaturalKnM3'])
        pos = new_pos
    if hi > pos + 1e-8: raise ValueError('Depth exceeds soil profile')
    return total


def basal_heave_reference(facts: dict, excavation_depth_m: float, *, surcharge_kpa: float = 0.) -> dict:
    """4.2.4 numerical reference with EXPLICIT uncalibrated natural-gamma assumption.

    Formula: [gamma_m2 D Nq + c Nc]/[gamma_m1 (h+D) + q0].
    Must not be used as a ULS conclusion without groundwater, strength and
    loading verification, and additional weak underlying layer search.
    """
    h = finite_number(excavation_depth_m,'excavation depth',0.)
    q = finite_number(surcharge_kpa,'uniform surcharge',0.)
    toe = float(facts['pileTopOriginalGroundDepthM'])+float(facts['pileLengthM'])
    if h <= 0 or h >= toe:
        raise ValueError('Excavation depth must be between ground and pile toe')
    d = toe-h
    layer = layer_at_site(facts,toe + 1e-5)
    c = float(layer['cKpa']); phi=float(layer['phiDeg'])
    nq,nc=capacity_factors(phi)
    # gamma averages are derived from original printed NATURAL weights only.
    # Do not call this a verified saturated/effective stress calculation.
    total_out=soil_overburden(facts,0.,toe)
    total_in=soil_overburden(facts,h,toe)
    driving=total_out+q
    resisting=total_in*nq+c*nc
    k=resisting/driving if driving>1e-12 else None
    return {'clause':'JGJ120-2012/4.2.4','numericalStatus':'calculated_research_candidate',
        'engineeringStatus':'blocked_unverified_soil_water_and_loads',
        'siteExcavationDepthM':h,'toeSiteDepthM':toe,'embedmentM':d,
        'toeSoilLayer':layer['name'],'cKpa':c,'phiDeg':phi,
        'Nq':nq,'Nc':nc,'gammaOutMeanNaturalKnM3':total_out/toe,
        'gammaInsideMeanNaturalKnM3':total_in/d,'surchargeAssumedKpa':q,
        'resistingKpa':resisting,'drivingKpa':driving,'factorConditional':k,
        'referenceThresholdGradeIII':1.4,'numericallyAboveThreshold':k is not None and k>=1.4,
        'methodDetails':'4.2.4 basal-heave reference; saturated weights, pore pressure and deeper weak layers unverified'}


def stage_embedment_inventory(facts: dict, stage: dict, *, supported: bool, grade: str='III') -> dict:
    h = float(stage['siteExcavationDepthM'])
    toe = float(facts['pileTopOriginalGroundDepthM'])+float(facts['pileLengthM'])
    d = toe-h
    if d < 0 or h <= 0: raise ValueError('Excavation geometrically incompatible with the retaining pile')
    ratio=d/h
    minimum_ratio=.3 if supported else .8
    return {'clause':'JGJ120-2012/4.2.7','stageId':stage['id'],
        'supportState':'single_support_or_replacement' if supported else 'cantilever_before_strut',
        'embeddedDepthM':d,'excavationDepthM':h,'embedmentToExcavationRatio':ratio,
        'recommendedMinimumRatio':minimum_ratio,'minimumGeometrySatisfied':ratio>=minimum_ratio,
        'engineeringStatus':'partial_geometry_only_toe_strength_not_verified',
        'stabilityLimitFrom4_2_2Or4_2_1':'missing_independent_pressure_resultants'}


def printed_arithmetic_check() -> list[dict]:
    """Check ONLY arithmetic visibly printed on 2020 PDF physical pages 35-36.

    These ratios do not independently search slip surfaces or confirm soil
    parameters. Source is an engineering report, not numeric stage arrays.
    """
    values=[
      ('early_outer_slope_circle',120.2,95.6,1.26,35,'reported_global_slide'),
      ('S1_circle',1138.8,311.5,3.66,35,'reported_global_slide'),
      ('S3_circle',903.6,440.7,2.05,35,'reported_global_slide'),
      ('S1_embedment_moment',666.4*1.487,288.4*2.752,1.25,35,'reported_toe_stability'),
      ('S3_embedment_moment',302.7*5.56,288.4*3.523,1.66,35,'reported_toe_stability'),
      ('S3_basal_heave',53.3*18.4+50*30.14,136.3,18.25,36,'reported_basal_heave'),
    ]
    checks=[]
    for identity,num,den,printed,page,kind in values:
        actual=num/den
        checks.append({'id':identity,'type':kind,'sourcePhysicalPage':page,
           'printedFactor':printed,'recalculatedFromPrintedOperands':actual,
           'roundingAgrees':abs(round(actual,2)-printed)<=.01,
           'status':'reported_equation_arithmetic_only',
           'provesDesignMethodOrGeometry':False})
    return checks


def hydraulic_gradient_screen(*, head_difference_m:float|None=None,
                              seepage_path_m:float|None=None,
                              submerged_gamma_kn_m3:float|None=None,
                              water_gamma_kn_m3:float=9.81)->dict:
    """Optional i=dh/L, icr=gamma'/gammaw SCREEN ONLY, Annex C incomplete."""
    if any(x is None for x in (head_difference_m,seepage_path_m,submerged_gamma_kn_m3)):
        return {'clause':'JGJ120-2012/4.2.6-and-Appendix-C',
            'status':'blocked_missing_hydraulic_gradient_path_aquifer_and_material',
            'gradient':None,'criticalGradient':None,'factor':None,
            'formalWaterSafetyPassed':False}
    dh=finite_number(head_difference_m,'head difference',0.)
    length=finite_number(seepage_path_m,'seepage path',0.)
    unit=finite_number(submerged_gamma_kn_m3,'submerged unit weight',0.)
    gw=finite_number(water_gamma_kn_m3,'water unit weight',1e-8)
    if length<=0 or unit<=0: raise ValueError('Positive seepage path and submerged unit weight required')
    i=dh/length
    icr=unit/gw
    return {'clause':'JGJ120-2012/4.2.6-and-Appendix-C',
        'status':'calculated_critical_gradient_screen_only',
        'gradient':i,'criticalGradient':icr,'factor':icr/i if i>0 else None,
        'formalWaterSafetyPassed':False,
        'methodQualification':'critical-gradient check does not substitute for Appendix C seepage-network and uplift verification'}


def stability_portfolio(facts: dict, stages:list[dict], *,
                        reported_soil_basis:str='2020_design_report_only')->dict:
    if [x['id'] for x in stages] != ['S1','S2','S3','S4','S5']:
        raise ValueError('Only five-stage 3-3 registered sequence allowed')
    from .embedment_reference import moment_reference
    rows=[]
    for stage in stages:
        support=stage['id'] in {'S2','S3','S4','S5'}
        embedded=stage_embedment_inventory(facts,stage,supported=support)
        heave=basal_heave_reference(facts,stage['siteExcavationDepthM'])
        if stage['id'] in {'S2','S3','S5'}:
            support_depth=(float(facts['siteSupportDepthM'])
                 if stage['id']!='S5' else float(facts['siteReplacementLevelM']))
            moment=moment_reference(facts,excavation_depth=stage['siteExcavationDepthM'],
                 support_site_depth=support_depth)
        else:
            moment={'clause':'JGJ120-2012/4.2.1-4.2.2',
                'engineeringStatus':'blocked_non_single_support_mechanism',
                'independentEngineeringStabilityQualified':False}
        rows.append({'stageId':stage['id'],'embedmentMinimum':embedded,
            'basalHeave':heave,'toeMomentEquilibrium':moment,
            'hydraulic':hydraulic_gradient_screen(),
            'overallCircularSlip':{'clause':'JGJ120-2012/4.2.3-and_report_local_requirement',
                'status':'blocked_missing_independently_verified_slip_slices',
                'independentStabilityFactor':None},
            'bottomSoftLayerRotation':{'clause':'JGJ120-2012/4.2.5',
                 'status':'requires_slices_and_local_soil_profile' if stage['siteExcavationDepthM']>=4.9
                         else 'assess_soft_layer_presence_per_stage',
                 'independentStabilityFactor':None},
            'engineeringQualified':False})
    return {'schemaVersion':STABILITY_SCHEMA,
        'status':'partial_numeric_checks_with_unclosed_methods',
        'soilAndWaterEvidence':reported_soil_basis,
        'stageResults':rows,'printedArithmeticCrossChecks':printed_arithmetic_check(),
        'methodRegister':[
          {'clause':'4.2.1/4.2.2','category':'embedment_moment','status':'blocked_pressure_and_boundaries'},
          {'clause':'4.2.3','category':'overall_slide','status':'blocked_independent_slice_search'},
          {'clause':'4.2.4','category':'basal_heave','status':'conditional_reference_only'},
          {'clause':'4.2.5','category':'bottom_soft_layer_rotation','status':'requires_applicability_and_slices'},
          {'clause':'4.2.6+Annex C','category':'seepage_and_uplift','status':'blocked_hydraulic_evidence'},
          {'clause':'4.2.7','category':'minimum_embedded_ratio','status':'geometric_numerical_screen_only'},
        ],
        'numberOfUnclosedMethodCategories':5,
        'completedFullStabilityQualification':False,
        'engineeringQualified':False,
        'formalConstructionIssue':False}
