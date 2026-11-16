"""Independent Euler-Bernoulli FE geometric-stiffness buckling reference.

Not a project soil-pile P-Delta analysis: uniform Winkler foundation and ideal
end constraints only. Gives a reproducible eigenvalue benchmark with exact
Euler verification for zero-foundation pinned-pinned column.
"""
from __future__ import annotations
from math import isfinite,pi
import numpy as np


def beam_column_critical_load(*, ei_kn_m2:float,length_m:float,elements:int=32,
                              foundation_kn_m2:float=0.,end_condition:str='pinned_pinned')->dict:
    nums=(ei_kn_m2,length_m,foundation_kn_m2)
    if (any(type(x) not in (int,float) or not isfinite(x) for x in nums)
        or ei_kn_m2<=0 or length_m<=0 or foundation_kn_m2<0 or
        isinstance(elements,bool) or not isinstance(elements,int) or not 8<=elements<=100 or
        end_condition not in ('pinned_pinned','fixed_fixed','fixed_pinned')):
        raise ValueError('Invalid ideal beam-column eigenvalue reference inputs')
    le=length_m/elements
    k=np.zeros((2*(elements+1),2*(elements+1)))
    g=np.zeros_like(k)
    beam=ei_kn_m2/le**3*np.array([[12,6*le,-12,6*le],
                                  [6*le,4*le*le,-6*le,2*le*le],
                                  [-12,-6*le,12,-6*le],
                                  [6*le,2*le*le,-6*le,4*le*le]],float)
    geometric=1/(30*le)*np.array([[36,3*le,-36,3*le],
                                  [3*le,4*le*le,-3*le,-le*le],
                                  [-36,-3*le,36,-3*le],
                                  [3*le,-le*le,-3*le,4*le*le]],float)
    for i in range(elements):
        q=[2*i,2*i+1,2*i+2,2*i+3]
        k[np.ix_(q,q)]+=beam
        g[np.ix_(q,q)]+=geometric
        # Foundation energy \int k_w y^2 dx, with consistent Hermite beam mass.
        if foundation_kn_m2:
            foundation=foundation_kn_m2*le/420*np.array([
             [156,22*le,54,-13*le],
             [22*le,4*le*le,13*le,-3*le*le],
             [54,13*le,156,-22*le],
             [-13*le,-3*le*le,-22*le,4*le*le]],float)
            k[np.ix_(q,q)]+=foundation
    fixed={0,2*elements}
    if end_condition in ('fixed_fixed','fixed_pinned'):fixed.add(1)
    if end_condition=='fixed_fixed':fixed.add(2*elements+1)
    free=[i for i in range(len(k)) if i not in fixed]
    sub_k=k[np.ix_(free,free)];sub_g=g[np.ix_(free,free)]
    try:
        chol=np.linalg.cholesky(sub_k)
        A=np.linalg.solve(chol,sub_g)
        eigmax=float(np.linalg.eigvalsh(np.linalg.solve(chol,A.T).T).max())
    except np.linalg.LinAlgError as exc:
        raise ValueError('Singular ideal beam-column reference') from exc
    if eigmax<=0 or not isfinite(eigmax):raise ValueError('No positive buckling eigenvalue')
    load=1/eigmax
    return {'schemaVersion':'pitguard.ideal-beam-column/3.88.5',
        'status':'reference_calculated','method':'Hermite FE stiffness and geometric stiffness eigenvalue',
        'criticalAxialKn':load,'eulerPinnedPinnedKn':pi*pi*ei_kn_m2/length_m**2,
        'endCondition':end_condition,'uniformFoundationKnM2':foundation_kn_m2,
        'elements':elements,'soilStructureSystemQualified':False,
        'formalDesignReady':False,'formalConstructionIssue':False}
