"""P-delta reference and scope evidence for a braced soil-supported pile.

Euler and amplification are ideal-column analytical checks. They cannot
replace a verified geometrically nonlinear soil-spring + staged brace model.
"""
from __future__ import annotations
from math import isfinite
from .second_order_screen import euler_ideal_column_critical_kn
from .beam_column_reference import beam_column_critical_load


def second_order_reference_cases(model)->dict:
    data=(model.engineering_design or {}).get('second_order_reference')
    if not data:
        return {'status':'missing_input','requiredInputs':['engineering_design.second_order_reference'],
                'cases':[],'formalDesignReady':False}
    from app.section.engineering_models import SecondOrderReference
    spec=SecondOrderReference.model_validate(data)
    stages={s.id for s in model.stages}
    actions={(x.combination_name,x.stage_id):x for x in model.matched_axial_actions}
    rows=[];missing=[];fail=False
    for c in spec.cases:
        p={'stageId':c.stage_id,'combination':c.combination_name,
           'loadBasis':c.load_basis,'boundaryBasis':c.boundary_basis,
           'soilSpringQualification':False}
        a=actions.get((c.combination_name,c.stage_id))
        if c.stage_id not in stages or a is None:
            p['status']='missing_input';missing.append(f'{c.stage_id}.stage_matched_axial');rows.append(p);continue
        if abs(a.axial_design_kn-c.axial_compression_kn)>max(.01,abs(a.axial_design_kn)*1e-8):
            p['status']='inconsistent_input';fail=True;rows.append(p);continue
        if a.axial_design_kn<=0:
            p['status']='outside_method_scope';p['reason']='Euler compression-only ideal reference';rows.append(p);continue
        ncr=euler_ideal_column_critical_kn(flexural_rigidity_kn_m2=c.ei_kn_m2,
                                         effective_length_m=c.effective_length_m)
        ratio=c.axial_compression_kn/ncr
        if c.uniform_foundation_kn_m2 is not None:
            if not c.foundation_basis:
                p['foundationStatus']='missing_input'
                missing.append('second_order_reference.foundation_basis')
            else:
                p['idealBeamColumnEigenReference']=beam_column_critical_load(
                    ei_kn_m2=c.ei_kn_m2,length_m=c.effective_length_m,
                    foundation_kn_m2=c.uniform_foundation_kn_m2,
                    end_condition=c.ideal_end_condition)
        p.update(criticalEulerKn=ncr,loadRatio=ratio,
                 idealAmplification=1/(1-ratio) if ratio<1 else None,
                 status='fail' if ratio>=1 else 'screening_only')
        fail |= ratio>=1
        rows.append(p)
    return {'schemaVersion':'pitguard.circular-second-order/3.88.5',
       'status':'fail' if fail else 'missing_input' if missing else 'screening_only',
       'cases':rows,'requiredInputs':sorted(set(missing)),
       'geometricNonlinearSoilSpringReanalysisComplete':False,
       'connectionAndEndRestraintVerified':False,
       'formalDesignReady':False,'formalConstructionIssue':False}
