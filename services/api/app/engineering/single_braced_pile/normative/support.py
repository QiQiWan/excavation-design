"""JGJ120-2012 4.9: strut force transfer and bounded member checks.

A wall-section reaction is not yet a global bracing frame solution. Unknown
node, temperature, settlement, bracing layout or end reaction blocks approval.
"""
from __future__ import annotations
from math import cos,radians
from .stability import _result


def support_checks(model,stages,action):
    reactions=[]
    for stage in stages:
        for support in stage['strut']:
            reactions.append({'stageId':stage['id'],'supportId':support['id'],
                    'signedReactionPerPileKn':support['reactionKn'],
                    'installationReferenceMm':support['installDisplacementMm']})
    member=model.support_member
    if not member:
        return {'status':'missing_input','method':'JGJ 120-2012 4.9',
             'stageReactions':reactions,'requiredInputs':['support_member'],
             'reason':'只有单桩等效支点反力；缺少实际支撑平面、材料截面和节点'}
    if not reactions:return {'status':'not_calculated','method':'JGJ120-2012 4.9','stageReactions':reactions}
    import math
    angle=cos(radians(member.angle_to_wall_normal_deg))
    gamma=model.design_effect_factor*model.gamma0
    lines=[]
    explicit=action.get('explicit',{}) if isinstance(action,dict) else {}
    official_cases=[c for c in explicit.get('cases',[]) if c['kind'] in ('ULS_basic','construction_adverse')
                     and c['status']=='calculated'] if explicit.get('status')=='calculated_scope_limited' else []
    if official_cases:
        for c in official_cases:
            for r in c['reactions']:
                if r['supportId']!=model.strut.id:continue
                normal=r['signedReactionKn']*member.tributary_wall_width_m/model.pile.spacing_m
                axial=normal/angle
                lines.append({'stageId':r['stageId'],'supportId':r['supportId'],
                    'combinationName':c['name'],'characteristicNormalKn':normal,
                    'characteristicAxialKn':axial,
                    'designAxialKn':axial*c['gammaImportance'],
                    'designFromLoadFactoredResolve':True,
                    'installationReferenceMm':r['referenceDisplacementMm']})
    else:
        for item in reactions:
            normal=item['signedReactionPerPileKn']*member.tributary_wall_width_m/model.pile.spacing_m
            axial=normal/angle
            lines.append({**item,'combinationName':None,'characteristicNormalKn':normal,
                          'characteristicAxialKn':axial,'designAxialKn':axial*gamma,
                          'designFromLoadFactoredResolve':False})
    if not lines:
        return {'status':'missing_input','method':'JGJ 120-2012 4.9',
                'requiredInputs':['valid_stage_brace_reactions']}
    control=max(lines,key=lambda v:abs(v['designAxialKn']))
    result={'method':'JGJ 120-2012 4.9.5-4.9.8','status':'review_required',
            'forceTransferBasis':'ULS separately solved reaction x tributary width / pile spacing / cos(angle); legacy fallback flagged'
              if official_cases else 'legacy single-factor reaction x tributary width / pile spacing / cos(angle)',
            'usesLoadFactoredULSReactions':bool(official_cases),
            'stageReactions':lines,'governing':control,'checkItems':[],
            'limitations':['未提供另一端支撑受力、平面刚度和完整节点体系，反力只能作为该支撑端的输入',
                           '须另算支撑自重、温差、施工平台荷载、差异沉降和预加力（若适用）']}
    if member.kind=='concrete':
        result['checkItems'].append(_result('member_strength','4.9.7','not_calculated',
             requiredInputs=['concrete_strut_N-M-V_section_and_second_order'],
             reason='混凝土内支撑需按混凝土构件轴压、偏压、受弯、受剪全套验算'))
    else:
        N=abs(control['designAxialKn']);L=member.member_length_m*1000
        minimum_e=max(L/1000,40.)
        actual_e=member.design_eccentricity_mm
        result['requiredMinimumEccentricityMm']=minimum_e
        if None in (member.section_area_mm2,member.section_modulus_mm3,
                    member.material_design_strength_mpa,member.stability_factor):
            result['checkItems'].append(_result('member_strength','4.9.7','missing_input',
                 requiredInputs=['section_area_mm2','section_modulus_mm3','material_design_strength_mpa','stability_factor']))
        elif not member.stability_factor_basis or not member.section_basis or actual_e is None:
            result['checkItems'].append(_result('member_strength','4.9.7','missing_input',
                 requiredInputs=['stability_factor_basis','section_basis','design_eccentricity_mm']))
        else:
            e=max(minimum_e,actual_e)
            A=member.section_area_mm2;W=member.section_modulus_mm3;f=member.material_design_strength_mpa
            sigma=N*1000/A+N*1000*e/W
            stability=N*1000/(member.stability_factor*A*f)
            stress_ratio=sigma/f
            result['checkItems'].append(_result('member_strength','4.9.7','fail' if stress_ratio>1 or stability>1 else 'screening_only',
                 axialDesignKn=N,combinedStressMpa=sigma,stressRatio=stress_ratio,
                 stabilityUtilization=stability,eccentricityMm=e,
                 reason='轴压＋偏心应力及提供的稳定系数初筛；钢构件整体局部稳定与连接计算未闭合'))
    if member.connection_capacity_kn is not None and member.connection_basis:
        cap=member.connection_capacity_kn
        result['checkItems'].append(_result('connection_capacity','4.9.7',
             'pass' if abs(control['designAxialKn'])<=cap else 'fail',
             axialDemandKn=abs(control['designAxialKn']),capacityKn=cap,
             capacityBasis=member.connection_basis,
             limitations=['仅依据经确认的连接设计承载力比较；实际传力及其他分量仍须核定']))
    else:
        result['checkItems'].append(_result('connection_capacity','4.9.7','missing_input',
               requiredInputs=['connection_capacity_kn','connection_basis']))
    # Transfer to the opposite support seat is an explicit engineering fact.
    # An equal/opposite check at one end is necessary but does not establish
    # a complete spatial brace-frame equilibrium.
    opposite={(x.stage_id,x.combination_name):x for x in member.opposite_end_reactions}
    if not opposite or not member.other_end_boundary_basis:
        result['checkItems'].append(_result('opposite_end_equilibrium','4.9.5','missing_input',
            requiredInputs=['support_member.opposite_end_reactions','support_member.other_end_boundary_basis'],
            reason='单侧支撑反力不能证明对侧传力及支撑体系平衡'))
    else:
        balances=[]
        for row in lines:
            other=opposite.get((row['stageId'],row.get('combinationName')))
            # An SLS/characteristic reaction from the opposite end cannot be
            # silently matched against a different load-factored ULS solution.
            if other is None:
                balances.append({'stageId':row['stageId'],
                'combinationName':row.get('combinationName'),'status':'missing_input'})
                continue
            mismatch=abs(row['characteristicNormalKn']+other.normal_kn)
            tolerance=max(.02*max(abs(row['characteristicNormalKn']),abs(other.normal_kn)),.5)
            balances.append({'stageId':row['stageId'],
                'combinationName':row.get('combinationName'),
                'thisEndNormalKn':row['characteristicNormalKn'],'otherEndNormalKn':other.normal_kn,
                'imbalanceKn':mismatch,'allowedDeviationKn':tolerance,
                'status':'pass' if mismatch<=tolerance else 'fail',
                'otherEndSource':other.source_basis})
        outcome='fail' if any(b['status']=='fail' for b in balances) else (
           'missing_input' if any(b['status']=='missing_input' for b in balances) else 'screening_only')
        result['checkItems'].append(_result('opposite_end_equilibrium','4.9.5',outcome,
              stageChecks=balances,reason='两端名义水平力校核；未包含中间节点、水平连系杆与竖向作用'))
    # Euler limits are intentionally kept as an explicit screening item. Local
    # plate buckling, imperfections, bracing points and structural combinations
    # require GB 50017 / GB 50010 design inputs separately.
    buckling=[member.elastic_modulus_mpa,member.i_horizontal_mm4,member.i_vertical_mm4,
              member.effective_length_horizontal_m,member.effective_length_vertical_m]
    if member.kind=='steel' and all(x is not None for x in buckling):
        import math
        E,ix,iy,lx,ly=buckling
        crit_x=math.pi**2*E*ix/(1000*(lx*1000)**2)
        crit_y=math.pi**2*E*iy/(1000*(ly*1000)**2)
        utilization=abs(control['designAxialKn'])/min(crit_x,crit_y)
        result['checkItems'].append(_result('brace_euler_stability','4.9.8',
           'fail' if utilization>=1 else 'screening_only',
           demandAxialKn=abs(control['designAxialKn']),eulerCriticalKn=min(crit_x,crit_y),
           eulerCriticalHorizontalKn=crit_x,eulerCriticalVerticalKn=crit_y,
           utilization=utilization,reason='Euler弹性分岔初筛，不能代替钢构件稳定设计系数和局部稳定'))
    elif member.kind=='steel':
        result['checkItems'].append(_result('brace_euler_stability','4.9.8','missing_input',
           requiredInputs=['elastic_modulus_mpa','i_horizontal_mm4','i_vertical_mm4',
                           'effective_length_horizontal_m','effective_length_vertical_m']))
    if any(x['status']=='fail' for x in result['checkItems']):
        result['status']='fail'
        result['reason']='杆件初筛或连接承载力已超限，必须调整并重新验算'
    return result
