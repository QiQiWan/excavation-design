"""V3.88.3 deterministic nondominated sorting over explicitly labelled proxies.

Proxy for support stiffness is *not a monetary cost*, and nominally lower steel
area is not a real bill of materials. Incomplete constraints never qualify a
candidate for engineering approval. Pareto ranking is for side-by-side review.
"""
from __future__ import annotations
from math import pi,isfinite
from .search_rules import changed_fields
from .constraints import evaluate_constraints


def _finite(v):return type(v) in (int,float) and isfinite(v)


def objectives(candidate:dict,baseline:dict)->dict:
    snap=candidate['snapshot'];orig=baseline['snapshot']
    c=(candidate.get('checks') or {}).get('displacement') or {}
    mm=c.get('valueMm');lim=c.get('limitMm')
    if not _finite(mm) or not _finite(lim) or lim<=0:
        u=None
    else:u=float(mm)/float(lim)
    cage=snap['cage'];base=orig['cage']
    area=cage['bar_count']*pi*(cage['bar_diameter_mm']**2)/4
    initial_area=base['bar_count']*pi*(base['bar_diameter_mm']**2)/4
    # Geometric quantities, comparable only for the same frozen baseline.
    pile_length=snap['pile']['length_m']/orig['pile']['length_m']
    pile_concrete_volume=(snap['pile']['diameter_m']/orig['pile']['diameter_m'])**2*pile_length
    longitudinal_area=area/initial_area
    stirrup_ratio=base['stirrup_spacing_mm']/cage['stirrup_spacing_mm']
    support_stiffness=snap['strut']['stiffness_kn_m']/orig['strut']['stiffness_kn_m']
    # Discrete design effort proxy; do not combine independently dimensioned
    # material types into an asserted cost or carbon calculation.
    changed=changed_fields(orig,snap)
    intervention=len(changed)+sum(abs(v['after']-v['before'])/max(abs(v['before']),1.)
                                  for v in changed.values())
    constraints=evaluate_constraints(candidate)
    fabrication=((candidate.get('normativeCoverage') or {}).get('requiredChecks') or {}).get('concrete_circular_cage_fabrication') or {}
    quantity=((fabrication.get('result') or {}).get('quantities') or {})
    theoretical_kg=quantity.get('theoreticalTotalKg')
    if not _finite(theoretical_kg):theoretical_kg=None
    return {'schemaVersion':'pitguard.a6-objectives/3.88.6',
      'resultHash':candidate['resultHash'],
      'values':{'displacementDemandRatio':u,
                'normalizedChangeEffort':round(intervention,9),
                'normalizedRebarArea':round(longitudinal_area,9),
                'normalizedPileLength':round(pile_length,9),
                'normalizedPileConcreteVolume':round(pile_concrete_volume,9),
                'normalizedStirrupDensity':round(stirrup_ratio,9),
                'normalizedSupportStiffness':round(support_stiffness,9),
                'theoreticalCageSteelKg':theoretical_kg},
      'paretoKeys':['displacementDemandRatio','normalizedChangeEffort'],
      'materialAndStiffnessProxiesOnly':True,'theoreticalSteelRequiresDeclaredFabricationPlan':True,
      'notActualCostOrCarbon':True,
      'constraintFailures':constraints['explicitFailures'],
      'constraintUnknowns':constraints['unverifiedRequirements'],
      'numericallyFeasibleWithinTestedScope':constraints['numericallyFeasibleWithinTestedScope'],
      'engineeringQualified':False,'formalConstructionIssue':False}


def _dominates(a:dict,b:dict)->bool:
    x=a['values'];y=b['values']
    if any(x[k] is None or y[k] is None for k in a['paretoKeys']):return False
    return all(x[k]<=y[k]+1e-9 for k in a['paretoKeys']) and any(x[k]<y[k]-1e-9 for k in a['paretoKeys'])


def pareto_report(rows:list[dict],baseline:dict,repository)->dict:
    """Compute fronts from immutable results; never trust mutable candidate metrics."""
    evaluated=[]
    for row in rows:
        if row.get('status')!='calculated' or not row.get('resultHash'):continue
        result=repository.load(row['resultHash'])
        if result['projectId']!=baseline['projectId'] or result['sectionId']!=baseline['sectionId']:
            raise ValueError('Pareto result provenance mismatch')
        obj=objectives(result,baseline)
        evaluated.append({'candidateId':row['id'],'roundIndex':row['roundIndex'],
                          'resultHash':row['resultHash'],'objectives':obj})
    # Compare within each feasibility class. A numerically infeasible candidate
    # must not outrank feasible one simply by having a smaller modification.
    def cls(x):
        o=x['objectives'];return (0 if o['numericallyFeasibleWithinTestedScope'] else 1,
                                  len(o['constraintFailures']),len(o['constraintUnknowns']))
    remaining=sorted(evaluated,key=lambda x:(cls(x),x['candidateId']))
    fronts=[]
    for group_key in sorted(set(cls(x) for x in remaining)):
        part=[x for x in remaining if cls(x)==group_key]
        rank=1
        while part:
            front=[p for p in part if not any(q is not p and _dominates(q['objectives'],p['objectives']) for q in part)]
            if not front:raise RuntimeError('Pareto sorting cycle')
            for x in front:x['paretoRank']=rank
            fronts.append({'feasibilityClass':group_key,'rank':rank,
                           'candidateIds':[x['candidateId'] for x in front]})
            taken={x['candidateId'] for x in front}
            part=[x for x in part if x['candidateId'] not in taken]
            rank+=1
    return {'schemaVersion':'pitguard.a6-pareto/3.88.3',
            'sourceResultHash':baseline['resultHash'],
            'candidateCount':len(evaluated),'candidates':evaluated,'fronts':fronts,
            'rankingIsEngineeringRecommendation':False,
            'realCostDataAvailable':False,'formalConstructionIssue':False}
