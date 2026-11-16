"""Cross-domain N-M interaction reference with explicit qualification gates.

The comparison uses the original production Gauss solver and a separately
implemented Simpson equilibrium solver. They share material assumptions:
this is a numerical benchmark, never independent code-method certification.
"""
from __future__ import annotations
from math import isfinite
from .circular_independent import independent_circular_nm


def benchmark_nm_envelope(model, axial_kn_values:tuple[float,...]=(-300.,0.,150.,600.,1500.,2500.),
                          tolerance:float=.03)->dict:
    from app.section.engineering_rebar import circular_nm_capacity
    if not axial_kn_values or len(axial_kn_values)>25 or not 0<tolerance<=.15:
        raise ValueError('Invalid benchmark sampling')
    if any(type(a) not in (int,float) or not isfinite(a) for a in axial_kn_values):
        raise ValueError('N-M benchmark loads must be finite')
    c=model.cage
    if any(getattr(c,k) is None for k in ('concrete_peak_strain','concrete_ultimate_strain',
                                        'steel_modulus_mpa','bar_start_angle_deg')):
        return {'status':'missing_input','requiredInputs':['explicit_material_law'],
                'cases':[],'formalDesignReady':False}
    proof={'pile':{'diameter_m':model.pile.diameter_m},'reinforcement':{
        'bar_count':c.bar_count,'bar_diameter_mm':c.bar_diameter_mm,
        'bar_axis_cover_mm':c.bar_axis_cover_mm,'bar_start_angle_deg':c.bar_start_angle_deg,
        'concrete_design_strength_mpa':c.concrete_design_mpa,
        'steel_design_strength_mpa':c.steel_design_mpa,
        'concrete_peak_strain':c.concrete_peak_strain,
        'concrete_ultimate_strain':c.concrete_ultimate_strain,
        'steel_modulus_mpa':c.steel_modulus_mpa}}
    rows=[]
    for axial in sorted(set(axial_kn_values)):
        for sign in (-1,1):
            primary=circular_nm_capacity(proof,axial,sign=sign)
            independent=independent_circular_nm(model,axial,sign=sign,slices=1024)
            a=primary.get('capacityKnM');b=independent.get('capacityKnM')
            qualified=bool(a is not None and b is not None and
                           primary.get('status')=='calculated' and
                           independent.get('numericalMethodScope')=='within_original_compression_neutral_axis')
            difference=abs(a-b)/max(abs(a),1e-8) if qualified else None
            state=('numerical_disagreement' if difference is not None and difference>tolerance
                   else 'numerically_agreed_method_scope_pending' if qualified
                   else 'outside_primary_method_scope')
            rows.append({'axialDesignKn':axial,'momentSign':sign,'primaryStatus':primary.get('status'),
                         'primaryCapacityKnM':a,'simpsonCapacityKnM':b,
                         'independentAxialResidualKn':independent.get('axialResidualKn'),
                         'relativeDifference':difference,'assessment':state,
                         'numericalScope':independent.get('numericalMethodScope')})
    bad=any(x['assessment']=='numerical_disagreement' for x in rows)
    qualified_count=sum(x['assessment']=='numerically_agreed_method_scope_pending' for x in rows)
    return {'schemaVersion':'pitguard.circular-nm-benchmark/3.88.5',
       'status':'fail' if bad else 'calculated_scope_limited' if qualified_count else 'outside_method_scope',
       'cases':rows,'numericScopeAgreementCount':qualified_count,
       'unqualifiedDomainCount':len(rows)-qualified_count,
       'independentMaterialLawCertified':False,'JGJ120AppendixBEquivalentCertified':False,
       'axialTensionEngineeringCertified':False,'deepCompressionEngineeringCertified':False,
       'methodBasis':'JGJ120-2012 4.3.2 Appendix B; GB/T50010-2010(2024) Appendix E (comparison)',
       'formalDesignReady':False,'formalConstructionIssue':False}
