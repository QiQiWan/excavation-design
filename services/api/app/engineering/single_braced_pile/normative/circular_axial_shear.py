"""Circular section eccentric-compression shear, scoped to GB/T 50010 6.3.12/6.3.15.

The formula can be verified at a station independently, but is not a signed
construction design, a crack-width check or a replacement for detailing.
All values are force kN, length mm and stress MPa. Axial compression positive.
"""
from __future__ import annotations
from math import pi, isfinite


def eccentric_compression_circular_shear(*, diameter_mm: float,
        axial_compression_kn: float, shear_design_kn: float,
        concrete_fc_mpa: float, concrete_ft_mpa: float,
        stirrup_fyv_mpa: float, circular_hoop_area_mm2: float,
        spacing_mm: float, beta_c: float,
        loading_type: str, loading_basis: str,
        shear_span_ratio: float | None = None) -> dict:
    """JGJ120 4.3.2 + GB/T50010 6.3.15 -> 6.3.12/6.3.1.

    Uniform loading in a non-frame eccentric-compression pile uses lambda=1.5.
    A concentrated load requires the sourced a/h0 ratio, clamped [1.5,3].
    Axial force may never be obtained by dividing horizontal support reaction.
    """
    fields=(diameter_mm, axial_compression_kn, shear_design_kn,concrete_fc_mpa,
            concrete_ft_mpa,stirrup_fyv_mpa,circular_hoop_area_mm2,spacing_mm,beta_c)
    if not all(isfinite(v) for v in fields):
        raise ValueError('Nonfinite material, force or section input')
    if min(diameter_mm,concrete_fc_mpa,concrete_ft_mpa,stirrup_fyv_mpa,
           circular_hoop_area_mm2,spacing_mm,beta_c)<=0:
        raise ValueError('Positive circular-section material and hoop dimensions required')
    if not (0.8 <= beta_c <= 1.0):
        raise ValueError('Concrete strength coefficient must be justified')
    if axial_compression_kn < 0:
        return {'status':'outside_method_scope','reason':'Axial tension requires GB/T50010 6.3.14 separately',
                'formalDesignReady':False}
    if len(loading_basis.strip())<10:
        return {'status':'missing_input','requiredInputs':['axial_shear_loading_basis'],
                'formalDesignReady':False}
    if loading_type=='uniform_loading':
        lam=1.5
        if shear_span_ratio is not None:
            return {'status':'outside_method_scope','reason':'Uniform loading uses lambda=1.5, not an arbitrary override',
                    'formalDesignReady':False}
    elif loading_type=='concentrated_load':
        if shear_span_ratio is None or not isfinite(shear_span_ratio) or shear_span_ratio<=0:
            return {'status':'missing_input','requiredInputs':['shear_span_ratio_a_over_h0'],
                    'formalDesignReady':False}
        lam=min(3.,max(1.5,shear_span_ratio))
    else:
        return {'status':'outside_method_scope','reason':'Loading type not qualified for this circular-pile branch',
                'formalDesignReady':False}
    r=diameter_mm/2; b=1.76*r; h0=1.6*r; area=pi*r*r
    ncap_kn=.3*concrete_fc_mpa*area/1000.
    n_used_kn=min(axial_compression_kn,ncap_kn)
    concrete_part=1.75/(lam+1)*concrete_ft_mpa*b*h0/1000.
    hoop_part=stirrup_fyv_mpa*circular_hoop_area_mm2*h0/spacing_mm/1000.
    axial_part=.07*n_used_kn
    shear_capacity_kn=concrete_part+hoop_part+axial_part
    section_limit_kn=.25*beta_c*concrete_fc_mpa*b*h0/1000.
    v=abs(shear_design_kn)
    ratio=v/min(shear_capacity_kn,section_limit_kn)
    return {'schemaVersion':'pitguard.circular-axial-shear/1',
            'status':'pass' if ratio<=1 else 'fail',
            'formalDesignReady':False,
            'method':'GB/T50010-2010(2024) 6.3.15 -> 6.3.12 and 6.3.1; JGJ120-2012 4.3.2',
            'loadingType':loading_type,'loadingBasis':loading_basis,
            'shearSpanRatio':lam,'axialCompressionKn':axial_compression_kn,
            'axialCompressionUsedKn':n_used_kn,'axialCompressionCapKn':ncap_kn,
            'circularRadiusMm':r,'equivalentWidthMm':b,'equivalentEffectiveDepthMm':h0,
            'concreteContributionKn':concrete_part,'hoopContributionKn':hoop_part,
            'axialContributionKn':axial_part,'shearCapacityKn':shear_capacity_kn,
            'sectionLimitKn':section_limit_kn,'governingCapacityKn':min(shear_capacity_kn,section_limit_kn),
            'demandShearKn':v,'utilization':ratio,
            'axialForceProvenance':'must_be_stage_and_combination_matched',
            'limitations':['Engineering load type and shear span require independent confirmation',
                'Concentrated reaction shear and support sections must be checked on both sides',
                'No automatic approval of seismic design, torsion or anchorage']}
