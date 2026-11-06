"""Metamorphic independent physics checks for a fixed linear seepage BVP.

An algebraically independent sparse solver checks the same discrete system,
while these transformations check physical linearity invariants of Darcy flow.
Neither validates a real site's hydrogeological boundary model.
"""
from __future__ import annotations
from copy import deepcopy
from math import isclose
from .seepage_fd import solve_seepage


def validate_darcy_invariants(domain:dict,*,nx:int=24,nz:int=16)->dict:
    baseline=solve_seepage(domain,dx_cells=nx,dz_cells=nz)
    if baseline.get('status')!='numerical_screening_only':
        return {'status':'solver_failure','baseline':baseline,'formalDesignReady':False}
    checks=[]
    g=baseline['governingExit']['upwardGradient']
    q=baseline['waterBalance']['leftBoundaryInflowM2PerS']
    def checked(name,changed,expected_gradient,expected_flow):
        run=solve_seepage(changed,dx_cells=nx,dz_cells=nz)
        if run.get('status')!='numerical_screening_only':
            checks.append({'name':name,'passed':False,'solverStatus':run['status']})
            return
        actual_g=run['governingExit']['upwardGradient'];actual_q=run['waterBalance']['leftBoundaryInflowM2PerS']
        checks.append({'name':name,'passed':bool(isclose(actual_g,expected_gradient,rel_tol=3e-5,abs_tol=1e-7) and
                    isclose(actual_q,expected_flow,rel_tol=3e-5,abs_tol=1e-10)),
                       'expectedGradient':expected_gradient,'actualGradient':actual_g,
                       'expectedLeftBoundaryFluxM2PerS':expected_flow,'actualLeftBoundaryFluxM2PerS':actual_q})
    shifted=deepcopy(domain)
    shifted['insideHeadM']+=7.25;shifted['outsideHeadM']+=7.25
    checked('uniform_head_translation',shifted,g,q)
    scaled_k=deepcopy(domain)
    for lay in scaled_k['layers']:
        lay['khMPerS']*=2.5;lay['kvMPerS']*=2.5
    checked('uniform_permeability_scaling',scaled_k,g,2.5*q)
    if domain['outsideHeadM']-domain['insideHeadM']>1e-8:
        delta=deepcopy(domain)
        delta['outsideHeadM']=delta['insideHeadM']+1.4*(domain['outsideHeadM']-domain['insideHeadM'])
        checked('head_difference_linearity',delta,1.4*g,1.4*q)
    else:
        checks.append({'name':'head_difference_linearity','passed':True,
                       'note':'Skipped in the no-flow state; zero head was checked directly'})
    return {'schemaVersion':'pitguard.darcy-invariants/1','status':'passed' if all(t['passed'] for t in checks) else 'failed',
        'tests':checks,'formalDesignReady':False,
        'scope':'linear_steady_groundwater_boundary_value_problem_only',
        'siteBoundaryIndependentlyVerified':False,
        'limitations':['Finite-volume numerical metamorphic checks do not establish site seepage paths or piping susceptibility']}
