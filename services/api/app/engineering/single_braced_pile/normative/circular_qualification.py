"""V3.88.4 independent section-equilibrium and shear arithmetic crosschecks.

Produces explicit numeric agreement only. Mechanical and normative scope
qualification is separate and remains unverified until reviewed.
"""
from __future__ import annotations
from math import pi
from .circular_independent import crosscheck_circular_nm


def independently_review_matched_sections(model,matched)->dict:
    rows=matched.get('cases') or []
    observed={};audit=[];required=[]
    if not rows:required.append('synchronized_ULS_stage_depth_N_M_V')
    for row in rows:
        if row.get('nmCapacityKnM') is None:continue
        axial=row.get('axialDesignKn');sign=1 if row['momentDesignKnM']>=0 else -1
        key=(axial,sign)
        if key not in observed:
            try:
                observed[key]=crosscheck_circular_nm(model,axial,
                    {'status':'calculated','capacityKnM':row['nmCapacityKnM']},sign=sign)
            except (ValueError,ArithmeticError) as exc:
                observed[key]={'status':'independent_solver_failed','reason':str(exc)}
        entry=observed[key]
        audit.append({'stageId':row['stageId'],'combination':row['combination'],
            'depthM':row['depthM'],'side':row['side'],
            'axialDesignKn':axial,'momentDesignKnM':row['momentDesignKnM'],
            'primaryNominalCapacityKnM':row['nmCapacityKnM'],
            'independent':entry,'resultStageMatched':True})
    failed=any(r['independent']['status'] in {'numerical_disagreement','independent_solver_failed'} for r in audit)
    agreed=any(r['independent']['status']=='numerically_agreed_method_scope_pending' for r in audit)
    if not agreed and rows:
        required.append('capacity_in_primary_validated_NM_scope')
    if matched.get('requiredInputs'):
        required.extend(matched['requiredInputs'])
    return {'schemaVersion':'pitguard.a5-independent-nm/3.88.4',
        'status':'fail' if failed else ('calculated_scope_limited' if agreed and not required else 'missing_input'),
        'resultCount':len(audit),'independentMethod':'Simpson circle slices + independent bracketing',
        'cases':audit,'requiredInputs':sorted(set(required)),
        'materialLawIndependentlyQualified':False,
        'generalAxialTensionAndDeepCompressionDesignQualified':False,
        'engineeringQualified':False,'formalDesignReady':False,
        'formalConstructionIssue':False}


def shear_arithmetic_crosscheck(model,matched)->dict:
    """Independently recompute N=0 and eccentric compression shear arithmetic.

    A declared branch and actual primary capacity must be available. Axial
    tension, seismic and unknown concentrated-force spacing stay unverified.
    """
    rows=matched.get('cases') or []
    shear=(model.engineering_design or {}).get('shear') or {}
    checked=[];missing=[]
    r=model.pile.diameter_m*500
    width=1.76*r;depth=1.6*r
    cage=model.cage
    for row in rows:
        if row.get('shearCapacityKn') is None or row.get('shearStatus') not in ('pass','fail'):
            continue
        axial=row['axialDesignKn']
        A=shear.get('effective_hoop_area_mm2')
        if A is None or axial is None:
            missing.append('shear.effective_hoop_area_mm2');continue
        lam=1.5
        if axial!=0:
            kind=shear.get('axial_shear_loading_case')
            if kind=='concentrated_load':
                value=shear.get('shear_span_ratio_a_over_h0')
                if value is None:
                    missing.append('shear_span_ratio_a_over_h0');continue
                lam=min(3.,max(1.5,value))
            elif kind!='uniform_loading':
                missing.append('shear.axial_shear_loading_case');continue
        fc=cage.concrete_design_mpa;ft=shear.get('concrete_tensile_design_strength_mpa');fy=shear.get('stirrup_design_strength_mpa');beta=shear.get('concrete_strength_coefficient_beta_c')
        if any(x is None for x in (ft,fy,beta)):
            missing.append('shear.material_strengths');continue
        vc=(.7 if axial==0 else 1.75/(lam+1))*ft*width*depth/1000
        vs=fy*A*depth/cage.stirrup_spacing_mm/1000
        ceiling=.25*beta*fc*width*depth/1000
        if axial<0:
            if vs+.0000000001 < .36*ft*width*depth/1000:
                missing.append('minimum_tension_shear_hoop_contribution');continue
            nominal=max(vs,vc+vs-.2*(-axial))
        else:
            nterm=0 if axial==0 else .07*min(axial,.3*fc*pi*r*r/1000)
            nominal=vc+vs+nterm
        capacity=min(nominal,ceiling)
        discrepancy=abs(capacity-row['shearCapacityKn'])/max(1e-8,capacity)
        checked.append({'combination':row['combination'],'stageId':row['stageId'],
           'depthM':row['depthM'],'side':row['side'],
           'independentCapacityKn':capacity,'primaryCapacityKn':row['shearCapacityKn'],
           'relativeDifference':discrepancy,'status':'numerically_agreed' if discrepancy<=1e-9 else 'numerical_disagreement'})
    return {'schemaVersion':'pitguard.a5-independent-shear/3.88.4',
      'status':'fail' if any(r['status']=='numerical_disagreement' for r in checked)
        else 'calculated_scope_limited' if checked else 'missing_input',
      'cases':checked,'requiredInputs':sorted(set(missing)) if checked else ['stage_matched_supported_shear_case'],
      'engineeringQualified':False,'formalDesignReady':False,'formalConstructionIssue':False}
