"""Circular-pile serviceability: supported uncracked tensile checks only.

GB/T 50010 7.1.1 and JGJ 94 3.1.6 permit crack-control grades with
specific tensile-stress criteria. Circular cracked-section width requires a
separately validated project method, so this module never fabricates w_max.
"""
from __future__ import annotations
from math import pi


def circular_tension_control(*, diameter_mm: float, moment_knm: float,
                             axial_compression_kn: float, concrete_ftk_mpa: float,
                             grade: int, combination_kind: str,
                             limit_mm: float | None = None) -> dict:
    if diameter_mm<=0 or concrete_ftk_mpa<=0 or grade not in (1,2,3):
        raise ValueError('Explicit physical section, tensile strength and crack class required')
    if combination_kind not in ('SLS_standard','SLS_quasi_permanent'):
        raise ValueError('Crack checks must use an explicit SLS combination')
    area=pi*diameter_mm**2/4
    inertia=pi*diameter_mm**4/64
    sigma_t=max(0.,abs(moment_knm)*1e6*(diameter_mm/2)/inertia
                -axial_compression_kn*1000/area)
    mcr=(concrete_ftk_mpa+axial_compression_kn*1000/area)*inertia/(diameter_mm/2)/1e6
    result={'method':'uncracked_circular_gross_section_linear_stress_v1',
      'basis':'GB/T 50010-2010(2024) 7.1.1 / JGJ94-2008 3.1.6; gross section elastic applicability',
      'crackClass':grade,'combination':combination_kind,'maximumTensileStressMpa':sigma_t,
      'concreteTensileCharacteristicMpa':concrete_ftk_mpa,'momentCrackingEstimateKnM':mcr,
      'crackWidthMm':None,'isFormalWidthCalculated':False,
      'formalDesignReady':False,'requiredInputs':[],
      'limitations':['gross concrete section has no flexural cracked-history or shrinkage restraint',
        'class III flexural crack width has no validated circular-section method in this module']}
    if grade==1 and combination_kind=='SLS_standard':
        result.update(status='pass_limited_tension_check' if sigma_t<=1e-9 else 'fail',allowedTensileStressMpa=0.)
    elif grade==2 and combination_kind=='SLS_standard':
        result.update(status='pass_limited_tension_check' if sigma_t<=concrete_ftk_mpa else 'fail',
                      allowedTensileStressMpa=concrete_ftk_mpa)
    elif grade==2 and combination_kind=='SLS_quasi_permanent':
        result.update(status='pass_limited_tension_check' if sigma_t<=1e-9 else 'fail',allowedTensileStressMpa=0.)
    elif grade==3:
        result.update(status='width_method_required',declaredLimitMm=limit_mm,
                      requiredInputs=['verified_circular_crack_width_method','sustained_actions','shrinkage_and_restraint_assessment'])
    else:
        result.update(status='not_required_for_this_class_combination')
    return result


def crack_matrix(model, explicit:dict) -> dict:
    config=model.crack_control
    if config is None:
        return {'status':'missing_input','requiredInputs':['crack_control'],
                'formalDesignReady':False,'cases':[]}
    if explicit.get('status')!='calculated_scope_limited':
        return {'status':'missing_input','requiredInputs':['complete_SLS_combinations'],
                'formalDesignReady':False,'cases':[]}
    actions={(x.combination_name,x.stage_id):x for x in model.service_axial_actions}
    rows=[];missing=[]
    for combo in explicit['cases']:
        if combo['kind'] not in ('SLS_standard','SLS_quasi_permanent') or combo['status']!='calculated':continue
        for stage in combo['stages']:
            if stage['status']!='success':continue
            axial=actions.get((combo['name'],stage['id']))
            if axial is None:
                missing.append(f'service_axial_actions:{combo["name"]}:{stage["id"]}')
                continue
            maxpt=max(stage['points'],key=lambda p:abs(p['momentKnM']),default=None)
            if not maxpt:continue
            r=circular_tension_control(diameter_mm=model.pile.diameter_m*1000,
                 moment_knm=maxpt['momentKnM']*combo['gammaImportance'],
                 axial_compression_kn=axial.axial_service_kn,
                 concrete_ftk_mpa=config.concrete_tensile_standard_mpa,
                 grade=config.grade,combination_kind=combo['kind'],limit_mm=config.width_limit_mm)
            rows.append({**r,'combinationName':combo['name'],'stageId':stage['id'],
               'depthM':maxpt['depthM'],'side':maxpt['side'],
               'axialActionBasis':axial.source_basis,'parameterBasis':config.material_basis})
    observed={(r['combination'],r['stageId']) for r in rows}
    if config.grade==2 and not any(r['combination']=='SLS_quasi_permanent' for r in rows):
        missing.append('SLS_quasi_permanent_combination')
    if not rows:missing.append('SLS_case_matched_axial_actions')
    fail=any(r['status']=='fail' for r in rows)
    return {'status':'fail' if fail else ('missing_input' if missing else
            'width_method_required' if config.grade==3 else 'calculated_scope_limited'),
        'cases':rows,'requiredInputs':sorted(set(missing)),'formalDesignReady':False,
        'limitations':['class I/II gross-section stress checks are limited to confirmed stress and section assumptions',
                       'class III requires independent validated circular cracking width method']}
