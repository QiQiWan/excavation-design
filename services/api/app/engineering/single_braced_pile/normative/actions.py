"""Separate service, characteristic stability, ULS and adverse-stage effects.

For JGJ120-2012 3.1.5-3.1.7: stability uses characteristic earth actions;
structural design effect = gamma_0 * gamma_F * characteristic response.
Changing stage water/surcharge requires a fresh nonlinear solve, not scaling u.
"""
from __future__ import annotations
from math import isfinite
from ..contracts import Section
from .stability import _result


def _peak(stages,key):
    return max(({'stageId':s['id'],'depthM':p['depthM'],'side':p['side'],
                 'signedValue':p[key],'absoluteValue':abs(p[key])}
                for s in stages if s['status']=='success' for p in s['points']),
               key=lambda r:r['absoluteValue'],default=None)


def combinations(model,stages):
    gamma=model.design_effect_factor;g0=model.gamma0;grade=model.safety_grade
    mandatory_g0={1:1.1,2:1.,3:.9}
    if not grade:state='missing_input'
    elif gamma<1.25-1e-10 or g0<mandatory_g0[grade]-1e-10:state='fail'
    else:state='calculated_scope_limited'
    standard={key:_peak(stages,key) for key in ('uMm','momentKnM','shearKn')}
    basic={key: ({**p,'designValue':p['signedValue']*gamma*g0,'factorGammaF':gamma,
                       'factorGamma0':g0} if p else None)
           for key,p in standard.items() if key!='uMm'}
    return {'method':'JGJ120-2012 3.1.5-3.1.7 integrated temporary action factor',
       'status':state,'requiredInputs':['safety_grade'] if grade is None else [],
       'characteristicStandard':standard,'basicCombination':basic,
       'geotechnicalStabilityBasis':'characteristic_loads_no_gammaF',
       'serviceabilityBasis':'characteristic_displacement_without_gammaF',
       'constructionAdverse':[],
       'limitations':['基本组合按规程临时支护综合分项系数对标准效应转换；非线性位移不可按系数放大',
         '温度、施工设备、差异沉降及不同时出现的作用需要独立定义工况和构件传力',
         '经项目明确的多种不利工况均须单独求解，再形成跨工况包络']}


def run_adverse_branches(model,baseline):
    """Rerun entire history for every user-declared, bounded adverse scenario."""
    from ..analysis import discrete_model,_sections
    from app.section.solver import solve_stages
    outcome=[]
    for scenario in model.adverse_cases:
        changes=[];active=False
        for stage in model.stages:
            if stage.id==scenario.from_stage_id:active=True
            if not active:changes.append(stage);continue
            out=stage.water_outside_depth_m-scenario.outside_water_raise_m
            inside=stage.water_inside_depth_m+scenario.inside_water_lower_m
            if out<0:
                outcome.append({'case':scenario.name,'status':'invalid_adverse_input',
                    'reason':'外水位高于地面，当前模块需要单独的地表积水作用模型'});break
            if inside>model.pile.length_m:
                outcome.append({'case':scenario.name,'status':'invalid_adverse_input',
                    'reason':'降低后的坑内水位超出计算深度'});break
            changes.append(stage.model_copy(update={
                'water_outside_depth_m':out,'water_inside_depth_m':inside,
                'surcharge_kpa':stage.surcharge_kpa+scenario.additional_surcharge_kpa}))
        else:
            try:
                source=model.model_dump(mode='json')
                # A prior 'no groundwater hazard' judgement cannot automatically
                # carry over to a raised exterior water level or lowered pit water.
                if (source['groundwater_check']['mode']=='no_trigger' and
                        (scenario.outside_water_raise_m>0 or scenario.inside_water_lower_m>0)):
                    source['groundwater_check']={'mode':'unknown'}
                case_model=Section.model_validate({**source,
                        'stages':[x.model_dump(mode='json') for x in changes], 'adverse_cases':[]})
                discrete=discrete_model(case_model);solved=solve_stages(discrete)
                stages=[]
                for stage,calc in zip(discrete['stages'],solved['stages']):
                    stages.append({'id':stage['id'],'status':calc['status'],
                         'points':_sections(stage,discrete['nodesM'],calc) if calc['status']=='success' else []})
                good=solved['status']=='success' and len(stages)==len(discrete['stages'])
                from .stability import (embedment_single_strut,heave_prandtl,
                        soft_soil_additional,minimal_toe_depth)
                from .groundwater import groundwater_checks
                governing_stage=case_model.stages[-1]
                stability_checks=[embedment_single_strut(case_model,governing_stage),
                     heave_prandtl(case_model,governing_stage),
                     soft_soil_additional(case_model,governing_stage),
                     minimal_toe_depth(case_model,governing_stage),
                     *groundwater_checks(case_model,governing_stage)] if good else []
                outcome.append({'case':scenario.name,'status':'calculated' if good else 'solver_failure',
                    'stabilityChecks':stability_checks,
                    'rationale':scenario.rationale,'fromStageId':scenario.from_stage_id,
                    'changedLoads':{'additionalSurchargeKpa':scenario.additional_surcharge_kpa,
                          'outsideWaterRaiseM':scenario.outside_water_raise_m,
                          'insideWaterLowerM':scenario.inside_water_lower_m},
                    'characteristicEffects':{key:_peak(stages,key) if good else None
                         for key in ('uMm','momentKnM','shearKn')},
                    'failureReason':None if good else '重求解失败，禁止使用未完成包络'})
            except (ValueError,RuntimeError) as exc:
                outcome.append({'case':scenario.name,'status':'solver_failure','reason':str(exc)})
    return outcome


def explicit_action_combinations(model):
    """Independently re-solve the staged nonlinear system under explicit action factors.

    Characteristics are NOT multiplied after the solve. Earth self-weight,
    permanent/variable surcharge increments and hydrostatic water are tracked
    separately before solving. Each result has its own controlling point and
    the support reaction computed from that same scenario and stage.

    Factors must be confirmed outside this function using project/standard
    selection. Without a complete matrix no formal ULS/SLS coverage is claimed.
    """
    if not model.load_combinations:
        return {'status':'missing_input','requiredInputs':['load_combinations'],
          'reason':'只有单一历史综合系数，尚未拆分 G、Q、水与 SLS/ULS 的作用组合',
          'cases':[],'governing':{}}
    from ..analysis import discrete_model,_sections
    from app.section.solver import solve_stages
    cases=[]
    for rule in model.load_combinations:
        factors={'earth':rule.gamma_earth,
                 'permanent':rule.gamma_permanent_surcharge,
                 'variable':rule.gamma_variable_surcharge,
                 'water':rule.gamma_water}
        mesh=discrete_model(model,pressure_factors=factors)
        solve=solve_stages(mesh)
        stages=[]
        for src,output in zip(mesh['stages'],solve['stages']):
            if output['status']!='success':
                stages.append({'id':src['id'],'status':output['status'],
                     'failureReason':output.get('failureReason'),'points':[],'supports':[]})
                continue
            pts=_sections(src,mesh['nodesM'],output)
            stages.append({'id':src['id'],'status':'success','points':pts,
                'supports':[{'supportId':s['id'],'signedReactionKn':s['reactionKn'],
                    'referenceDisplacementMm':1000*s['referenceDisplacementM']}
                    for s in output.get('supportStates',[])]})
        complete=solve['status']=='success' and len(stages)==len(model.stages) and all(s['status']=='success' for s in stages)
        peak={key:_peak(stages,key) if complete else None for key in ('uMm','momentKnM','shearKn')}
        peak={key:({**v,'designValue':v['signedValue']*rule.gamma_importance} if v and rule.kind in
                    ('ULS_basic','construction_adverse') else v) for key,v in peak.items()}
        reactions=[{'stageId':s['id'],**r,'designAxialReactionKn':r['signedReactionKn']*rule.gamma_importance}
                   for s in stages for r in s['supports']]
        cases.append({'name':rule.name,'kind':rule.kind,
            'status':'calculated' if complete else 'solver_failure',
            'factors':factors,'gammaImportance':rule.gamma_importance,
            'actionBasis':rule.action_basis,'combinationBasis':rule.combination_basis,
            'governing':peak,'reactions':reactions,
            'stages':stages,'resultScope':'nonlinear_wall_soil_stage_response',
            'isProjectCodeVerified':False})
    # A design must explicitly include ULS and SLS. The names and factors are
    # supplied and audited, never picked silently by this engineering adapter.
    observed={c['kind'] for c in cases if c['status']=='calculated'}
    complete=all(c['status']=='calculated' for c in cases)
    must={'ULS_basic','SLS_standard'}
    has_full=must.issubset(observed) and complete
    uls=[c for c in cases if c['status']=='calculated' and c['kind'] in ('ULS_basic','construction_adverse')]
    peak={}
    for key in ('momentKnM','shearKn'):
        pool=[{'case':c['name'],**c['governing'][key]} for c in uls if c['governing'][key]]
        peak[key]=max(pool,key=lambda v:abs(v['designValue']),default=None)
    return {'status':'calculated_scope_limited' if has_full else 'missing_input',
            'requiredInputs':[] if has_full else ['ULS_basic and SLS_standard fully solved'],
            'cases':cases,'governing':peak,
            'SLS_caseCount':sum(c['kind'].startswith('SLS') for c in cases),
            'ULS_caseCount':len(uls),'designEffectBasis':'factored_loads_re_solved_then_gamma_importance_once',
            'limitations':['本作用矩阵仅纳入已登记的土侧压力、地面永久/可变超载及静水压力',
                 '温差、支撑自重、施工机械、差异沉降和单独局部构件荷载仍须显式组合',
                 '组合输入须由设计人员核定；本模块不会自动声称覆盖GB55003要求的所有不利情形']}
