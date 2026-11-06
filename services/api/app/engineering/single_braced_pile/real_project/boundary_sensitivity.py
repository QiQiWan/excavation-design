"""Reproducible parameter-identifiability checks, not original FRWS calibration.

Only independently specified model variants are compared. The output is
synthetic conditional sensitivity and cannot be used to choose original site
boundary conditions by matching a published extreme.
"""
from __future__ import annotations
from .luoyu_three_three import produce_luoyu_research_curves


def assess_boundary_identifiability(*,mesh_max_m:float=.25)->dict:
    variations={
      'toe_free':{'toe_boundary':'free'},
      'toe_pinned':{'toe_boundary':'pinned'},
      'toe_fixed':{'toe_boundary':'fixed'},
      'crown_half':{'toe_boundary':'free','crown_multiplier':.5},
      'crown_double':{'toe_boundary':'free','crown_multiplier':2.},
      'support_half':{'toe_boundary':'free','support_multiplier':.5},
      'support_double':{'toe_boundary':'free','support_multiplier':2.},
    }
    results={}
    for name,params in variations.items():
        calc=produce_luoyu_research_curves(mesh_max_m=mesh_max_m,**params)
        results[name]={'inputOverrides':params,'discreteHash':calc['discreteHash'],
           'solverStatus':calc['solverStatus'],
           'stages':[{k:st.get(k) for k in ('id','status','peakDisplacementMm','peakMomentKnM','peakShearKn')}
                    for st in calc['stages']]}
    complete=all(x['solverStatus']=='success' and all(y['status']=='success' for y in x['stages'])
                 for x in results.values())
    variability={}
    for stage in ('S1','S3','S5'):
        variants=[next(st for st in run['stages'] if st['id']==stage)
                  for run in results.values() if run['solverStatus']=='success']
        variability[stage]={k:{'minimum':min(v[k] for v in variants),
                                'maximum':max(v[k] for v in variants)}
                            for k in ('peakDisplacementMm','peakMomentKnM','peakShearKn')}
    return {'schemaVersion':'pitguard.luoyu-boundary-identifiability/1',
            'status':'calculated_scope_limited' if complete else 'solver_failure',
            'referenceProject':'Luoyu/3-3','modelStatus':'conditional_research_only',
            'sameConditionsAsOriginal':False,'isOriginalNativeNumericExport':False,
            'meshMaxM':mesh_max_m,'variants':results,'stageSensitivityRanges':variability,
            'recommendation':'Do not back-calculate original toe/crown/support boundary from moment maxima alone',
            'requiredOriginalEvidence':['pile_toe_boundary_measured_or_model_export',
               'crown_stiffness_MN_per_m2_to_per_pile_conversion',
               'opposite_wall_phase_stiffness_and_displacement',
               'slope_earth_pressure_method_and_stratigraphy',
               'S1_to_S5_original_nodal_pressure_u_M_V_and_support_reaction_arrays'],
            'formalSameConditionVerification':False}
