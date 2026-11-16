"""Independent numerical equilibrium audit of the existing circular RC fiber law.

Independent integration: composite Simpson slices (not production Gauss
quadrature) and independent bracket / bisection. Engineering suitability of
material constitutive laws and JGJ120 Appendix B remains a separate gate.
Units: N kN, M kN*m, stresses MPa, all section dimensions mm.
"""
from __future__ import annotations
from math import isfinite, pi, sin
import numpy as np


def _material(m):
    cage=m.cage
    names=('concrete_peak_strain','concrete_ultimate_strain','steel_modulus_mpa','bar_start_angle_deg')
    if any(getattr(cage,n) is None for n in names):
        raise ValueError('missing_explicit_concrete_or_steel_strain_law')
    if cage.concrete_ultimate_strain <= cage.concrete_peak_strain:
        raise ValueError('concrete_ultimate_strain must exceed peak strain')
    R=m.pile.diameter_m*500
    if cage.bar_axis_cover_mm>=R or cage.bar_axis_cover_mm<=cage.bar_diameter_mm/2:
        raise ValueError('invalid_circular_cage_radius')
    if 2*(R-cage.bar_axis_cover_mm)*sin(pi/cage.bar_count)<=cage.bar_diameter_mm:
        raise ValueError('invalid_bar_clear_spacing')
    return cage,R


def independent_circular_nm(model, axial_kn:float, *,sign:int=1, slices:int=1024)->dict:
    """Cross-check the production N-M *section* law by independent Simpson sums.

    Extended tensile and deep-compression domains are reported as exploratory
    numerical branches only. This method does NOT approve eccentric tension,
    column slenderness, ductility, confinement, joints, or seismic design.
    """
    if not isfinite(axial_kn) or sign not in (-1,1) or slices<128 or slices%2:
        raise ValueError('invalid_NM_audit_configuration')
    cage,R=_material(model)
    height=2*R
    dy=height/slices
    y=-R+np.arange(slices+1)*dy
    arc=2*np.sqrt(np.maximum(0.,R*R-y*y))
    weights=np.full(slices+1,2.0)
    weights[1:-1:2]=4.0
    weights[0]=weights[-1]=1.
    dA=arc*weights*(dy/3)
    rs=R-cage.bar_axis_cover_mm
    theta=np.deg2rad(cage.bar_start_angle_deg)+np.arange(cage.bar_count)*2*pi/cage.bar_count
    bar_y=sign*rs*np.cos(theta)
    Abar=pi*cage.bar_diameter_mm**2/4
    peak=cage.concrete_peak_strain;ultimate=cage.concrete_ultimate_strain
    Es=cage.steel_modulus_mpa;fc=cage.concrete_design_mpa;fy=cage.steel_design_mpa

    def concrete_stress(strain):
        q=np.clip(strain/peak,0.,1.)
        return fc*(2*q-q*q)

    def equilibrium(c):
        depth=R-y
        eps=ultimate*(1.-depth/c)
        f=concrete_stress(eps)*dA
        bs=ultimate*(1.-(R-bar_y)/c)
        steel=np.clip(Es*bs,-fy,fy)
        net=(steel-concrete_stress(bs))*Abar
        return (float(np.sum(f)+np.sum(net))/1000.,
                float(np.dot(f,y)+np.dot(net,bar_y))/1e6)

    target=float(axial_kn)
    lo=max(1e-8, R*1e-10);hi=R*100000.
    lower,_=equilibrium(lo);upper,_=equilibrium(hi)
    if target<=lower+1e-6 or target>=upper-1e-6:
        return {'schemaVersion':'pitguard.circular-independent/1',
                'status':'outside_numerical_equilibrium_range',
                'requiredAxialKn':target,'minimumSupportedAxialKn':lower,
                'maximumSupportedAxialKn':upper,'capacityKnM':None,
                'formalDesignReady':False}
    # For a strongly nonlinear compressive law, bracket by subdivision and
    # choose the smallest positive neutral-axis root when multiple roots exist.
    # This preserves a conservative exploratory branch without asserting the
    # global ultimate N-M surface.
    grid=np.geomspace(lo,hi,220)
    hits=[];prior_c=grid[0];prior_n=lower-target
    for nxt in grid[1:]:
        nn=equilibrium(float(nxt))[0]-target
        if prior_n*nn<=0:hits.append((float(prior_c),float(nxt)))
        prior_c,prior_n=nxt,nn
    if not hits:
        return {'schemaVersion':'pitguard.circular-independent/1',
            'status':'root_not_bracketed','capacityKnM':None,'formalDesignReady':False}
    values=[]
    for a,b in hits:
        fa=equilibrium(a)[0]-target
        for _ in range(72):
            mid=(a+b)/2
            fm=equilibrium(mid)[0]-target
            if fa*fm<=0:b=mid
            else:a=mid;fa=fm
        c=(a+b)/2
        n,moment=equilibrium(c)
        values.append((abs(moment),c,n,moment))
    cap,c,n,moment=min(values,key=lambda row:row[0])
    method_scope=('within_original_compression_neutral_axis' if axial_kn>=0 and c<=height
                  else 'extended_strain_branch_unqualified')
    return {'schemaVersion':'pitguard.circular-independent/1',
        'status':'numerical_equilibrium_verified' if abs(n-target)<max(0.05,1e-5*abs(target)) else 'equilibrium_residual_too_large',
        'method':'independent_composite_simpson_fiber_equilibrium',
        'requiredAxialKn':target,'axialResidualKn':n-target,
        'capacityKnM':cap,'signedMomentKnM':moment,
        'neutralAxisMm':c,'sectionDiameterMm':height,'numericalBranches':len(values),
        'numericalMethodScope':method_scope,'slices':slices,
        'constitutiveLawIndependentlyValidated':False,
        'formalDesignReady':False,
        'limitations':['Same input material stress-strain assumptions as production model',
                       'Different quadrature and root solver verify discretization, not the physical constitutive law',
                       'Tension/deep compression and second-order effects remain outside verified JGJ120 design scope']}


def crosscheck_circular_nm(model, axial_kn, primary_capacity, sign=1, tolerance=.03):
    independent=independent_circular_nm(model,axial_kn,sign=sign)
    actual=independent.get('capacityKnM')
    value=primary_capacity.get('capacityKnM')
    if (primary_capacity.get('status')=='calculated' and value is not None
            and actual is not None and independent['numericalMethodScope']=='within_original_compression_neutral_axis'):
        discrepancy=abs(actual-value)/max(abs(value),1e-8)
        status='numerically_agreed_method_scope_pending' if discrepancy<=tolerance else 'numerical_disagreement'
    else:
        discrepancy=None
        status='outside_primary_method_scope'
    return {'schemaVersion':'pitguard.circular-independent-crosscheck/1',
            'status':status,'primaryCapacityKnM':value,'independentCapacityKnM':actual,
            'relativeDifference':discrepancy,'allowableDifference':tolerance,
            'independent':independent,'engineeringQualified':False,'formalConstructionIssue':False}
