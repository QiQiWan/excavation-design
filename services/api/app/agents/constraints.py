"""V3.88.3 A6: strict, non-promotional multi-constraint feasibility vectors.

'pass' is reserved for checks actually performed with finite inputs; missing
methods remain incomplete even if all available numerical checks pass.
"""
from __future__ import annotations
from .normative_contract import build_check_contract


def evaluate_constraints(result:dict)->dict:
    contract=build_check_contract(result)
    vector=[]
    for item in contract['projectAndScreeningChecks']:
        vector.append({'id':item['id'],'kind':item['type'],'state':item['assessment'],
                       'sourcePointer':item['sourcePointer'],
                       'actual':item.get('actual',item.get('providedAreaMm2')),
                       'limit':item.get('limit',item.get('requiredAreaMm2')),
                       'numericallyTested':item['assessment'] in {'pass','fail'}})
    for item in contract['normativeChecks']:
        vector.append({'id':'normative.'+item['checkId'],'kind':'normative',
                       'state':item['assessment'],'sourcePointer':item['resultPointer'],
                       'governingStageId':item['governingStageId'],
                       'methodClause':item['methodClause'],
                       'numericallyTested':item['assessment'] in {'pass','fail'}})
    failures=sorted(x['id'] for x in vector if x['state']=='fail')
    missing=sorted(x['id'] for x in vector if x['state'] not in {'pass','fail'})
    known=sum(x['numericallyTested'] for x in vector)
    solver_ok=result.get('analysisStatus')=='complete'
    if not solver_ok:failures.append('solver.analysis_incomplete')
    # Scope remains incomplete when upstream methods or source authentication
    # are unknown. No optimization score can turn that into a compliance PASS.
    physical_known=all(x['assessment']=='pass' for x in contract['projectAndScreeningChecks'])
    numerical_feasible=bool(solver_ok and not failures and known>0 and physical_known)
    return {'schemaVersion':'pitguard.a6-constraint-vector/3.88.3',
            'resultHash':result['resultHash'],'snapshotHash':result['snapshotHash'],
            'constraints':vector,'explicitFailures':failures,'unverifiedRequirements':missing,
            'knownConstraintsTested':known,'physicalProjectAndScreeningChecksAllObserved':physical_known,
            'numericallyFeasibleWithinTestedScope':numerical_feasible,
            'allApplicableNormativeChecksVerified':bool(contract['allNormativeRequirementsVerified'] and not failures and not missing),
            'originalSourceBytesVerified':False,'engineeringQualified':False,
            'formalConstructionIssue':False}
