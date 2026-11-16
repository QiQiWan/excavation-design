"""Combine immutable M3 candidate studies with independent code review facts.

Read-only diagnostic matrix. Does not mutate design selection or reinterpret
unqualified source documents as formally checked construction designs.
"""
from __future__ import annotations


def build_review_matrix(candidates:list[dict], method_reviews:list[dict])->dict:
    by_id={}
    for record in method_reviews:
        idx=record['candidateIndex']
        by_id.setdefault(idx,{}).setdefault(record['kind'],[]).append(record)
    rows=[]
    for candidate in candidates:
        result=candidate['result']
        idx=candidate['candidateIndex'];reviews=by_id.get(idx,{})
        norm=reviews.get('normative',[])
        comp=reviews.get('component',[])
        latest_n=norm[-1]['report'] if norm else None
        latest_c=comp[-1]['report'] if comp else None
        pressure_difference=bool(latest_n and any(r['status']=='numeric_difference' for r in latest_n['profiles']))
        pressure_missing=bool(latest_n and latest_n['missingStageFaceCases'])
        exceeded=bool(latest_c and latest_c['screenViolations'])
        capacity_missing=bool(latest_c and latest_c['missingChecks'])
        reasons=[]
        if latest_n is None:reasons.append('NORMATIVE_PRESSURE_REVIEW_NOT_SUBMITTED')
        if latest_c is None:reasons.append('MEMBER_NODE_CAPACITY_REVIEW_NOT_SUBMITTED')
        if pressure_difference:reasons.append('RANKINE_PRESSURE_NUMERIC_DISAGREEMENT')
        if pressure_missing:reasons.append('INCOMPLETE_STAGE_FACE_REVIEW')
        if exceeded:reasons.append('MEMBER_OR_NODE_CAPACITY_SCREEN_EXCEEDED')
        if capacity_missing:reasons.append('MEMBER_OR_NODE_CAPACITY_SCREEN_MISSING')
        reasons+=['DESIGN_COMBINATIONS_AND_CODE_METHODS_NOT_FULLY_QUALIFIED',
                  'AUTHENTIC_ENGINEERING_EVIDENCE_AND_SIGNATURES_MISSING']
        rows.append({'candidateIndex':idx,'candidateId':candidate['candidateId'],
            'computed':result.get('status')=='nonlinear_contact_reference_solved_only',
            'normativeReviewStatus':latest_n['status'] if latest_n else 'missing',
            'componentReviewStatus':latest_c['status'] if latest_c else 'missing',
            'numericPressureDifference':pressure_difference,'capacityScreenExceeded':exceeded,
            'diagnosticIssues':sorted(set(reasons)),'readyForFormalDesign':False,
            'formalConstructionIssue':False})
    return {'schemaVersion':'pitguard.m3-review-matrix/3.89.5','candidates':rows,
        'automaticAdoptionAllowed':False,'engineeringQualified':False,
        'formalConstructionIssue':False}
