"""Scope-qualified GB/T 50010 6.3.14/6.3.15 tension-shear branch.

N<0 means tensile axial force in the PitGuard section convention.  This
module does not approve seismic, torsional, fatigue or undocumented support
load types. Force kN, stress MPa, all geometry in mm.
"""
from __future__ import annotations
from math import isfinite,pi


def eccentric_tension_circular_shear(*, diameter_mm:float, axial_tension_kn:float,
    shear_design_kn:float, concrete_fc_mpa:float, concrete_ft_mpa:float,
    stirrup_fyv_mpa:float, circular_hoop_area_mm2:float, spacing_mm:float,
    beta_c:float, loading_type:str, loading_basis:str,
    shear_span_ratio:float|None=None, seismic_case:str='nonseismic')->dict:
    args=(diameter_mm,axial_tension_kn,shear_design_kn,concrete_fc_mpa,
          concrete_ft_mpa,stirrup_fyv_mpa,circular_hoop_area_mm2,spacing_mm,beta_c)
    if any(type(x) not in (int,float) or not isfinite(x) for x in args):
        raise ValueError('Nonfinite or nonnumeric tension-shear input')
    if min(diameter_mm,concrete_fc_mpa,concrete_ft_mpa,stirrup_fyv_mpa,
           circular_hoop_area_mm2,spacing_mm,beta_c)<=0 or not .8<=beta_c<=1:
        raise ValueError('Invalid section or documented material values')
    if axial_tension_kn<=0:
        raise ValueError('Positive tensile force is required for this branch')
    if seismic_case!='nonseismic':
        return {'status':'outside_method_scope','reason':'seismic_or_unknown_load_case',
                'formalDesignReady':False}
    if not isinstance(loading_basis,str) or len(loading_basis.strip())<10:
        return {'status':'missing_input','requiredInputs':['documented_shear_loading_basis'],
                'formalDesignReady':False}
    if loading_type=='uniform_loading':
        if shear_span_ratio is not None:
            return {'status':'outside_method_scope','reason':'uniform_loading_requires_fixed_lambda_1_5',
                    'formalDesignReady':False}
        lam=1.5
    elif loading_type=='concentrated_load':
        if shear_span_ratio is None or not isfinite(shear_span_ratio) or shear_span_ratio<=0:
            return {'status':'missing_input','requiredInputs':['shear_span_ratio_a_over_h0'],
                    'formalDesignReady':False}
        lam=max(1.5,min(3.,shear_span_ratio))
    else:
        return {'status':'outside_method_scope','reason':'unqualified_loading_type',
                'formalDesignReady':False}
    r=diameter_mm/2.;b=1.76*r;h0=1.6*r
    v_concrete=1.75/(lam+1)*concrete_ft_mpa*b*h0/1000.
    v_steel=stirrup_fyv_mpa*circular_hoop_area_mm2/spacing_mm*h0/1000.
    # GB/T 50010 6.3.14: the right-hand-side is not taken below V_steel;
    # it also sets a minimum transverse reinforcement contribution.
    minimum_steel=.36*concrete_ft_mpa*b*h0/1000.
    section_limit=.25*beta_c*concrete_fc_mpa*b*h0/1000.
    if v_steel + 1e-10 < minimum_steel:
        return {'status':'fail','reason':'minimum_shear_reinforcement_not_met',
            'minimumHoopContributionKn':minimum_steel,'providedHoopContributionKn':v_steel,
            'method':'GB/T50010-2010(2024) 6.3.14/6.3.15',
            'engineeringQualified':False,'formalDesignReady':False}
    calculated=max(v_steel,v_concrete+v_steel-.2*axial_tension_kn)
    governing=min(calculated,section_limit)
    demand=abs(shear_design_kn)
    return {'schemaVersion':'pitguard.circular-tension-shear/3.88.5',
            'status':'pass' if demand<=governing+1e-10 else 'fail',
            'method':'GB/T50010-2010(2024) 6.3.14/6.3.15 + JGJ120 4.3.2',
            'loadingType':loading_type,'loadingBasis':loading_basis,'lambda':lam,
            'axialTensionKn':axial_tension_kn,'shearDesignKn':demand,
            'concreteContributionKn':v_concrete,'hoopContributionKn':v_steel,
            'axialTensionDeductionKn':.2*axial_tension_kn,
            'minimumHoopContributionKn':minimum_steel,
            'sectionLimitKn':section_limit,'governingCapacityKn':governing,
            'utilization':demand/governing if governing>0 else None,
            'methodScope':'nonseismic_documented_eccentric_tension_circle_only',
            'engineeringQualified':False,'formalDesignReady':False,
            'formalConstructionIssue':False}
