"""Reuse the existing independently exercised circular-NM, V and detailing tools.

The adapter does not invent axial loads, material strains, crack rules or node
anchorage. All stage cases must belong to the same frozen analysis.
"""
from __future__ import annotations
from app.section.engineering_rebar import check_engineering_rebar
from app.section.engineering_models import EngineeringDesign
from .circular_axial_shear import eccentric_compression_circular_shear


def circular_checks(model,stages,combination):
    if model.engineering_design is None:
        return {'status':'missing_input','requiredInputs':['engineering_design'],
               'formalDesignReady':False,'reason':'需要逐阶段轴力、材料应变曲线、抗剪与构造依据；当前仅可提供需求面积'}
    engineering=EngineeringDesign.model_validate(model.engineering_design).model_dump(mode='json')
    cage=model.cage
    needs_material=any(engineering.get(x) is not None for x in ('nm_cases','service'))
    missing=[key for key in ('concrete_peak_strain','concrete_ultimate_strain','steel_modulus_mpa','bar_start_angle_deg')
             if getattr(cage,key) is None] if needs_material else []
    if missing:
        return {'status':'missing_input','requiredInputs':['cage.'+x for x in missing],
                'formalDesignReady':False,'reason':'圆桩应变协调需要实证材料本构参数'}
    r={'bar_count':cage.bar_count,'bar_diameter_mm':cage.bar_diameter_mm,
       'bar_axis_cover_mm':cage.bar_axis_cover_mm,'bar_start_angle_deg':cage.bar_start_angle_deg or 0.,
       'concrete_design_strength_mpa':cage.concrete_design_mpa,
       'steel_design_strength_mpa':cage.steel_design_mpa,
       'concrete_peak_strain':cage.concrete_peak_strain,
       'concrete_ultimate_strain':cage.concrete_ultimate_strain,
       'steel_modulus_mpa':cage.steel_modulus_mpa,
       'stirrup_diameter_mm':cage.stirrup_diameter_mm,
       'stirrup_spacing_mm':cage.stirrup_spacing_mm}
    adapter={'pile':{'diameter_m':model.pile.diameter_m},'reinforcement':r,
             'analysis':{'design_effect_factor':model.design_effect_factor*model.gamma0}}
    normalized=[]
    for s in stages:
        normalized.append({'id':s['id'],'status':s['status'],
             'points':[{'depthM':p['depthM'],'side':p['side'],
                 'momentCharacteristicKnM':p['momentKnM'],'shearCharacteristicKn':p['shearKn']}
                       for p in s['points']]})
    shear=combination['characteristicStandard']['shearKn']
    envelope={'complete':all(s['status']=='success' for s in stages),
       'governing':{'shearAbs':{'valueKn':shear['signedValue'],'stageId':shear['stageId'],
                              'depthM':shear['depthM'],'side':shear['side']}} if shear else {}}
    result=check_engineering_rebar(adapter,envelope,engineering,normalized)
    if model.adverse_cases:
        result['limitations'].append('不利工况尚未提供相应的N-M-V同步构件组，不能将标准工况验算结果推广到不利工况')
        result['formalDesignReady']=False
        result['unverifiedChecks'].append('adverse_cases_stage_matched_N_M_V')
    return result


def synchronized_combination_sections(model, explicit):
    """Simultaneously evaluate stage/depth-matched N-M-V for explicit ULS.

    The N-M branch uses the existing strain compatibility integrator. A
    supported circular shear clause is evaluated with the same V and
    matching axial force. Nonzero axial compression requires documented
    uniform or concentrated loading per GB/T 50010 6.3.12/6.3.15; unsupported
    types remain blocked. No unrelated stage peaks are combined.
    """
    from app.section.engineering_rebar import circular_nm_capacity
    import math
    if explicit.get('status') != 'calculated_scope_limited':
        return {'status':'missing_input','requiredInputs':['complete_explicit_ULS_and_SLS_combinations'],
                'cases':[],'formalDesignReady':False}
    cage=model.cage
    proof={'pile':{'diameter_m':model.pile.diameter_m},
      'reinforcement':{
        'bar_count':cage.bar_count,'bar_diameter_mm':cage.bar_diameter_mm,
        'bar_axis_cover_mm':cage.bar_axis_cover_mm,'bar_start_angle_deg':cage.bar_start_angle_deg or 0.,
        'concrete_design_strength_mpa':cage.concrete_design_mpa,
        'steel_design_strength_mpa':cage.steel_design_mpa,
        'concrete_peak_strain':cage.concrete_peak_strain,
        'concrete_ultimate_strain':cage.concrete_ultimate_strain,
        'steel_modulus_mpa':cage.steel_modulus_mpa}}
    complete_material=all(getattr(cage,x) is not None for x in
        ('concrete_peak_strain','concrete_ultimate_strain','steel_modulus_mpa','bar_start_angle_deg'))
    axial={(x.combination_name,x.stage_id):x for x in model.matched_axial_actions}
    shear_input=(model.engineering_design or {}).get('shear')
    rows=[];missing=[];capacities={}
    for case in explicit['cases']:
        if case['kind'] not in ('ULS_basic','construction_adverse'):continue
        for stage in case['stages']:
            if stage['status']!='success':
                missing.append(f'{case["name"]}:{stage["id"]}:solver');continue
            action=axial.get((case['name'],stage['id']))
            if action is None:missing.append(f'{case["name"]}:{stage["id"]}:design_axial_force')
            for pt in stage['points']:
                M=pt['momentKnM']*case['gammaImportance']
                V=pt['shearKn']*case['gammaImportance']
                row={'combination':case['name'],'stageId':stage['id'],
                     'depthM':pt['depthM'],'side':pt['side'],'momentDesignKnM':M,
                     'shearDesignKn':V,'axialDesignKn':action.axial_design_kn if action else None,
                     'axialActionBasis':action.source_basis if action else None,
                     'status':'missing_input','nmCapacityKnM':None,'nmUtilization':None,
                     'shearCapacityKn':None,'shearUtilization':None,'shearStatus':'missing_input'}
                if action and complete_material and action.second_order_assessment=='not_required_by_project_assessment':
                    key=(action.axial_design_kn,1 if M>=0 else -1)
                    if key not in capacities:
                        capacities[key]=circular_nm_capacity(proof,*key[:1],sign=key[1])
                    cap=capacities[key]
                    row['nmCapacityKnM']=cap['capacityKnM']
                    if cap['capacityKnM'] is not None and cap['capacityKnM']>0:
                        row['nmUtilization']=abs(M)/cap['capacityKnM']
                        row['status']='pass' if row['nmUtilization']<=1 else 'fail'
                    else: row['status']='outside_method_scope'
                elif action and action.second_order_assessment!='not_required_by_project_assessment':
                    row['status']='outside_method_scope'
                elif action and not complete_material:missing.append('cage.material_strain_laws')
                if shear_input is None:
                    missing.append('engineering_design.shear')
                elif action is None:
                    row['shearStatus']='missing_input'
                elif shear_input.get('seismic_case')!='nonseismic':
                    row['shearStatus']='outside_method_scope'
                    row['shearScopeReason']='Seismic shear branch not validated'
                elif cage.stirrup_spacing_mm and cage.stirrup_diameter_mm:
                    A=shear_input['effective_hoop_area_mm2']
                    maximum=math.pi*cage.stirrup_diameter_mm**2/2
                    if A>maximum*(1+1e-9):
                        row['shearStatus']='outside_method_scope'
                        missing.append('shear.effective_hoop_area_mm2_consistency')
                    elif action.axial_design_kn<0:
                        row['shearStatus']='outside_method_scope'
                        row['shearScopeReason']='Axial tension requires a separately justified 6.3.14 check'
                    elif action.axial_design_kn>0 and shear_input.get('axial_shear_loading_case') is None:
                        row['shearStatus']='outside_method_scope'
                        row['shearScopeReason']='Axial compression needs explicitly documented loading type and lambda'
                        missing.append('shear.axial_shear_loading_case')
                    elif (shear_input['axial_force_design_kn'] not in (0,action.axial_design_kn)):
                        row['shearStatus']='outside_method_scope'
                        row['shearScopeReason']='Conflicting axial force declarations'
                    elif action.axial_design_kn==0 and shear_input['loading_case']=='general_bending_nonprestressed':
                        r=model.pile.diameter_m*500;b=1.76*r;h=1.6*r
                        ft=shear_input['concrete_tensile_design_strength_mpa']
                        fy=shear_input['stirrup_design_strength_mpa']
                        strength=.7*ft*b*h/1000+fy*A*h/cage.stirrup_spacing_mm/1000
                        section_limit=.25*shear_input['concrete_strength_coefficient_beta_c']*cage.concrete_design_mpa*b*h/1000
                        capacity=min(strength,section_limit)
                        row['shearCapacityKn']=capacity
                        row['shearUtilization']=abs(V)/capacity if capacity>0 else None
                        row['shearStatus']='pass' if abs(V)<=capacity else 'fail'
                        row['shearMethod']='GB/T50010 6.3.15 -> 6.3.4, N=0'
                    elif action.axial_design_kn>0:
                        loading=shear_input.get('axial_shear_loading_case')
                        if shear_input.get('loading_case')=='concentrated_load' and loading!='concentrated_load':
                            row['shearStatus']='outside_method_scope'
                            row['shearScopeReason']='Concentrated load requires the matching lambda basis'
                        elif shear_input.get('loading_case')=='other':
                            row['shearStatus']='outside_method_scope'
                        else:
                            checked=eccentric_compression_circular_shear(
                                diameter_mm=model.pile.diameter_m*1000,
                                axial_compression_kn=action.axial_design_kn,
                                shear_design_kn=V,concrete_fc_mpa=cage.concrete_design_mpa,
                                concrete_ft_mpa=shear_input['concrete_tensile_design_strength_mpa'],
                                stirrup_fyv_mpa=shear_input['stirrup_design_strength_mpa'],
                                circular_hoop_area_mm2=A,spacing_mm=cage.stirrup_spacing_mm,
                                beta_c=shear_input['concrete_strength_coefficient_beta_c'],
                                loading_type=loading,
                                loading_basis=shear_input.get('axial_shear_loading_basis') or '',
                                shear_span_ratio=shear_input.get('shear_span_ratio_a_over_h0'))
                            row['axialShearVerification']=checked
                            row['shearStatus']=checked['status']
                            row['shearCapacityKn']=checked.get('governingCapacityKn')
                            row['shearUtilization']=checked.get('utilization')
                            row['shearMethod']=checked.get('method')
                            if checked['status']=='missing_input':
                                missing.extend(checked['requiredInputs'])
                            elif checked['status']=='outside_method_scope':
                                row['shearScopeReason']=checked['reason']
                    else:
                        row['shearStatus']='outside_method_scope'
                        row['shearScopeReason']='Unsupported N=0 load branch'

                rows.append(row)
    nm=max((r for r in rows if r['nmUtilization'] is not None),
           key=lambda r:r['nmUtilization'],default=None)
    shear=max((r for r in rows if r['shearUtilization'] is not None),
              key=lambda r:r['shearUtilization'],default=None)
    failure=any(x['status']=='fail' or x['shearStatus']=='fail' for x in rows)
    outside=any(x['status']=='outside_method_scope' or x['shearStatus'] in ('outside_method_scope','screening_only') for x in rows)
    return {'status':'fail' if failure else ('missing_input' if missing else
            ('outside_method_scope' if outside else 'calculated_scope_limited')),
        'cases':rows,'requiredInputs':sorted(set(missing)),
        'governingNM':nm,'governingShear':shear,'formalDesignReady':False,
        'limitations':['轴力必须提供与组合、阶段对应的设计值，不能由支撑水平反力推断',
           '同一截面取同一阶段、组合、深度及受力侧的 N-M-V，分别计算抗弯与抗剪利用率',
           '符合GB/T50010 6.3.12/6.3.15时可验算正轴压N的斜截面；受力类型、剪跨比、截面限值及轴力来源必须明确，轴拉和其他类型仍阻断',
           '裂缝、二阶效应、节点与施工详图仍须经过独立验证']}
