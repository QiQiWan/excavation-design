"""Building-excavation circular-crack method and evidence intake.

GB/T50010 7.1.2 ordinary equations do not explicitly cover circular RC
sections. An independent method review and authenticated input report are
necessary. We check frozen stage and SLS effect consistency; never fabricate
an approved project formula or re-label a bridge reference as code compliance.
"""
from __future__ import annotations
from hashlib import sha256


def project_circular_crack_review(model, stage_results, explicit_actions=None)->dict:
    raw=(model.engineering_design or {}).get('circular_crack_project_review')
    if not raw:
        return {'status':'missing_input','requiredInputs':['engineering_design.circular_crack_project_review'],
                'cases':[],'formalDesignReady':False}
    from app.section.engineering_models import CircularCrackProjectReview
    spec=CircularCrackProjectReview.model_validate(raw)
    stages={s['id']:s for s in stage_results}
    explicit={(c.get('name'),s.get('id')):(c,s)
        for c in (explicit_actions or {}).get('cases',[])
        for s in c.get('stages',[]) if isinstance(s,dict)}
    declared={c.name:c for c in model.load_combinations}
    axial={(a.combination_name,a.stage_id):a for a in model.service_axial_actions}
    results=[];required=[];has_failure=False
    for case in spec.cases:
        row={'stageId':case.stage_id,'depthM':case.depth_m,'side':case.side,
             'combination':case.combination_name,'reportSha256':case.report_sha256,
             'reportedWidthMm':case.reported_width_mm,
             'independentWidthMm':case.independent_width_mm,
             'methodDocumentSha256':spec.method_document_sha256,
             'projectMethodAcceptedByAuthorizedChecker':False,
             'rawSourceBytesIndependentlyVerified':False}
        stage=stages.get(case.stage_id)
        combo=declared.get(case.combination_name)
        a=axial.get((case.combination_name,case.stage_id))
        pts=[p for p in (stage or {}).get('points',[]) if
             p['side']==case.side and abs(p['depthM']-case.depth_m)<1e-8]
        combined=explicit.get((case.combination_name,case.stage_id))
        project_case,combined_stage=combined if combined else ({},{})
        sls_pts=[p for p in combined_stage.get('points',[]) if
                 p['side']==case.side and abs(p['depthM']-case.depth_m)<1e-8]
        if (not stage or stage.get('status')!='success' or len(pts)!=1 or
            not combo or not a or combo.kind not in ('SLS_standard','SLS_quasi_permanent') or
            not combined or project_case.get('status')!='calculated' or
            combined_stage.get('status')!='success' or len(sls_pts)!=1):
            row['status']='missing_input'
            required.append('exact_solver_resolved_SLS_case_stage_station_axial_moment')
            results.append(row);continue
        expected=sls_pts[0]['momentKnM']
        raw_scaled=pts[0]['momentKnM']*case.service_effect_scale
        if (abs(expected-case.service_moment_knm)>max(.001,abs(expected)*1e-7)
            or abs(raw_scaled-expected)>max(.001,abs(expected)*1e-7)
            or abs(a.axial_service_kn-case.service_axial_kn)>max(.001,abs(a.axial_service_kn)*1e-7)):

            row['status']='inconsistent_input';has_failure=True;results.append(row);continue
        diff=abs(case.reported_width_mm-case.independent_width_mm)
        if diff>case.independent_tolerance_mm:
            row['status']='numerical_disagreement';has_failure=True
        elif case.reported_width_mm>spec.project_width_limit_mm:
            row['status']='reported_width_exceeds_project_limit';has_failure=True
        else:row['status']='external_numerical_agreement_review_pending'
        row['widthDifferenceMm']=diff
        row['withinDeclaredLimit']=case.reported_width_mm<=spec.project_width_limit_mm
        row['frozenMomentMatched']=True;row['frozenAxialMatched']=True
        results.append(row)
    return {'schemaVersion':'pitguard.circular-project-crack/3.88.5',
       'status':'fail' if has_failure else 'missing_input' if required else 'calculated_scope_limited',
       'methodBasis':spec.method_basis,'methodDocumentSha256':spec.method_document_sha256,
       'projectWidthLimitMm':spec.project_width_limit_mm,
       'cases':results,'requiredInputs':sorted(set(required)),
       'GB50010_7_1_2_CircularFormulaAutomaticallyApplicable':False,
       'originalSourceBytesIndependentlyVerified':False,
       'engineeringQualified':False,'formalDesignReady':False,'formalConstructionIssue':False}
