"""JGJ120 and GB55003 equation tools, source-aware preflight, deterministic run.

No generative text is used for forces, coefficients, selections or pass/fail.
Values proposed from catalogs are never silently filled into inputs.
"""
from __future__ import annotations

from datetime import date
from math import isfinite, radians, sin, sqrt, tan, pi
from hashlib import sha256
import json

from .contracts import CalcRequest, QuantityInput
from .registry import method, SOURCES
from .units import convert, FIELDS, CANONICAL

META = {'schemaVersion': 'pitguard.norm-calc-result/1',
        'codeScope': 'individually verified clauses; excludes full pit design, slope, seepage and section resistance'}


def _normalized(request: CalcRequest):
    vals, approvals, problems = {}, {}, []
    for name, obj in request.inputs.items():
        if isinstance(obj, QuantityInput):
            if name not in FIELDS:
                problems.append({'code':'UNKNOWN_PARAMETER', 'field':name, 'message':'未登记的量纲字段，不参与求解'})
                continue
            try:
                expected_dimension = FIELDS[name][0]
                if name in ('design_effect', 'design_resistance') and request.inputs.get('effect_type') in ('moment', 'torsion'):
                    expected_dimension = 'moment'
                val = convert(obj.value, obj.unit, expected_dimension); vals[name] = val['value']
                approvals[name] = {
                    **val, 'sourceType':obj.source_type, 'sourceRef':obj.source_ref,
                    'approval':obj.approval, 'approvedBy':obj.approved_by,
                    'ready':obj.approval=='approved' and obj.source_type!='synthetic_test',
                }
                if obj.approval!='approved':
                    problems.append({'code':'PARAMETER_NOT_CONFIRMED','field':name,
                                     'message':'已保留数值供审阅；正式工程验算不采纳未确认参数'})
                elif obj.source_type=='synthetic_test':
                    problems.append({'code':'SYNTHETIC_PARAMETER','field':name,
                                     'message':'合成验证例只用于算法测试，不可认定正式工程'})
            except ValueError as exc:
                problems.append({'code':'UNIT_ERROR','field':name,'message':str(exc)})
        elif name in ('stress_mode', 'strength_basis', 'pile_section', 'effect_type', 'stress_side') and isinstance(obj,str):
            vals[name]=obj
        elif name == 'stages' and isinstance(obj,list):
            vals[name]=obj
        else:
            problems.append({'code':'INVALID_PARAMETER_FORM','field':name,'message':'该字段必须为合法量纲值或明确枚举'})
    return vals, approvals, problems


def _check_math(values, required):
    missing=[n for n in required if n not in values]
    if missing:
        raise ValueError('缺少必需的工程输入: '+', '.join(missing))


def _pressures(values):
    _check_math(values, ['sigma_total','pore_pressure','cohesion','phi','stress_mode','strength_basis'])
    st, u, c, phi = (values[x] for x in ('sigma_total','pore_pressure','cohesion','phi'))
    mode=values['stress_mode']
    if mode not in ('effective_separated','total_combined'):
        raise ValueError('仅支持effective_separated或total_combined；与参数试验口径必须一致')
    if (mode=='effective_separated' and values['strength_basis']!='effective') or (mode=='total_combined' and values['strength_basis']!='total'):
        raise ValueError('土水分合算方式与黏聚力/内摩擦角试验口径不匹配；请提供同口径指标')
    if st<0 or u<0 or u>st+1e-10 or c<0 or not 0<=phi<45:
        raise ValueError('竖向应力、孔压或强度指标超出本方法适用域')
    ka=tan(pi/4-radians(phi)/2)**2
    kp=1/ka
    if mode=='effective_separated':
        sigma=st-u
        raw_active=ka*sigma-2*c*sqrt(ka)+u
        pa=max(0,raw_active)
        pp=kp*sigma+2*c*sqrt(kp)+u
        p0=ka*sigma+u # 4.1.4 initial on excavated side, no cohesion reduction
    else:
        raw_active=ka*st-2*c*sqrt(ka)
        pa=max(0,raw_active)
        pp=kp*st+2*c*sqrt(kp)
        p0=ka*st
    return {'activePressureKpa':pa,'passivePressureKpa':pp,'ka':ka,'kp':kp,
            'initialSoilReactionKpa':p0,'effectiveStressKpa':st-u,
            'mode':mode,'formula':'JGJ120-2012 3.4.2 (water term only for separated mode)',
            'unit':'kPa', 'includesPorePressure':mode=='effective_separated',
            'requiresAdditionalReview':raw_active<0,
            'tensionCrackWaterReview':'主动计算压力截为零；需单独核查张裂、裂隙进水及附加水压力情景' if raw_active<0 else ''}


def _pile_width(values):
    _check_math(values,['pile_width','pile_spacing','pile_section'])
    d,s=values['pile_width'],values['pile_spacing']
    shape=values['pile_section']
    if not 0<d<=5 or not d<=s<=10:
        raise ValueError('桩宽、桩距必须为物理有效值；咬合桩不能套用单排非咬合计算宽度')
    if shape=='circular':
        unbounded=.9*(1.5*d+.5) if d<=1 else .9*(d+1)
    elif shape in ('rectangular','h_section'):
        unbounded=1.5*d+.5 if d<=1 else d+1
    else:
        raise ValueError('pile_section仅支持circular/rectangular/h_section')
    return {'b0M':min(s,unbounded),'unlimitedB0M':unbounded,'earthPressureWidthM':s,
            'soilReactionWidthM':min(s,unbounded), 'wasCappedBySpacing':unbounded>s,
            'source':'JGJ120-2012 4.1.3(3), 4.1.7', 'unit':'m'}


def _m_estimate(values):
    """JGJ120-2012 4.1.6: m(MN/m4)=(0.2*phi**2-phi+c)/vb(mm).

    Returns a proposal only. The user has to verify layer, parameters, and vb.
    """
    _check_math(values, ['cohesion','phi','vb'])
    c, phi, vb_m = (values[k] for k in ('cohesion','phi','vb'))
    if c < 0 or not 0 <= phi < 45 or vb_m <= 0 or vb_m > 2:
        raise ValueError('土强度参数或坑底位移不符合4.1.6经验公式输入范围')
    vb_mm = vb_m * 1000
    effective_vb = max(10, vb_mm)
    numerator = .2*phi**2-phi+c
    if numerator <= 0:
        raise ValueError('经验公式计算所得m<=0，不能强行修正为正值；需要试验或地区经验')
    m_mn = numerator / effective_vb
    return {'candidateMKnPerM4':m_mn * 1000,
            'candidateMMnPerM4':m_mn, 'vbActualMm':vb_mm,
            'vbUsedMm':effective_vb, 'formulaNumerator':numerator,
            'approvalRequired':True,'isAutomaticDesignParameter':False,
            'source':'JGJ120-2012 4.1.6',
            'equation':'m(MN/m4)=(0.2*phi^2-phi+c)/max(vb,10mm)'}


def _kh(values):
    _check_math(values,['m','depth','excavation_depth'])
    m,z,h=values['m'],values['depth'],values['excavation_depth']
    if m<=0 or z<h or h<0:
        raise ValueError('m必须为正；4.1.5 仅适用于坑内嵌固段 z>=h')
    return {'khKnPerM3':m*(z-h), 'depthBelowExcavationM':z-h,
            'source':'JGJ120-2012 4.1.5', 'equation':'kh=m*(z-h)'}


def _soil_reaction(values):
    bounds=_pressures(values)
    stiff=_kh(values)
    _check_math(values,['displacement','stress_side'])
    if values['stress_side'] != 'excavation_inside':
        raise ValueError('土反力公式仅适用于坑内嵌固段，应明确stress_side=excavation_inside')
    v=values['displacement']
    if abs(v)>2:
        raise ValueError('位移超过2m，请核对输入单位；本局部方法不允许大变形外推')
    raw=bounds['initialSoilReactionKpa']+stiff['khKnPerM3']*v
    cap=bounds['passivePressureKpa']
    if raw<0:
        raise ValueError('当前输入使坑内土反力为负；需检查脱开/阶段模型，不能擅自改为零')
    return {'initialSoilReactionKpa':bounds['initialSoilReactionKpa'],
            'rawReactionKpa':raw, 'pointPassiveReferenceKpa':cap,
            'pointExceedsPassiveReference':raw>cap,
            'requiresEmbeddedSegmentForceCheck':True,
            'khKnPerM3':stiff['khKnPerM3'],
            'equation':'ps=ps0+m*(z-h)*v; integrated P_sk <= E_pk',
            'source':'JGJ120-2012 4.1.4-4.1.5',
            'disclaimer':'条文4.1.4的上限为嵌固段合力P_sk<=E_pk，单点压力不能替代合力检查，未在此自动截断点压力'}


def _uls(values):
    _check_math(values,['gamma0','effect_type','design_effect','design_resistance'])
    g,s,r=values['gamma0'],values['design_effect'],values['design_resistance']
    effect_type=values['effect_type']
    if effect_type not in ('axial', 'shear', 'moment','torsion'):
        raise ValueError('作用类型必须为axial、shear、moment、torsion之一')
    unit = 'kN.m' if effect_type in ('moment','torsion') else 'kN'
    if g<=0 or s<0 or r<=0:
        raise ValueError('γ0>0、Sd>=0且Rd>0；抗力必须来自构件计算，不能由内力反推')
    return {'designDemand':g*s,'designResistance':r,'unit':unit, 'effectType':effect_type,'utilization':g*s/r,
            'satisfied':g*s<=r+1e-10,'source':'GB55003-2021 7.2.1','equation':'gamma0*Sd <= Rd'}


def _sls(values):
    _check_math(values,['service_effect','service_limit'])
    s,limit=values['service_effect'],values['service_limit']
    if s<0 or limit<=0:
        raise ValueError('位移/沉降效应应取绝对值；控制限值必须大于0')
    return {'demandMm':s*1000,'limitMm':limit*1000,'utilization':s/limit,
            'satisfied':s<=limit+1e-10,'source':'GB55003-2021 7.2.2','equation':'S_service <= C'}


def _stages(values):
    _check_math(values,['stages'])
    stages=values['stages']; errors=[]; ids=set(); installed=set(); ever=set(); last_h=-1
    has_final=False; has_excavate=False
    for i,stage in enumerate(stages):
        if not isinstance(stage,dict):
            errors.append(f'第{i+1}项必须为阶段对象'); continue
        sid,event,h=stage.get('id'),stage.get('event'),stage.get('excavationDepthM')
        if not sid or sid in ids: errors.append(f'阶段{i+1}身份缺失或重复')
        ids.add(sid)
        if not isinstance(h,(float,int)) or isinstance(h,bool) or not isfinite(h) or h<0:
            errors.append(f'{sid}:开挖深度无效'); continue
        if h<last_h: errors.append(f'{sid}:开挖深度倒退，回填需要独立方法')
        if i==0 and (event!='initial' or h!=0): errors.append('首个事件应为无开挖初始状态')
        if event not in ('initial','excavate','install','remove','change_load','change_water','backfill'):
            errors.append(f'{sid}:未知或未实现的施工事件 {event}')
        if event=='excavate': has_excavate=True
        if stage.get('isFinalDesignState') is True: has_final=True
        add=stage.get('installSupportIds') or []
        rem=stage.get('removeSupportIds') or []
        if not isinstance(add,list) or not isinstance(rem,list) or any(not isinstance(x,str) or not x for x in add+rem):
            errors.append(f'{sid}:支撑引用必须是非空字符串列表'); continue
        if event=='install' and not add: errors.append(f'{sid}:安装事件没有对应支撑')
        if event=='remove' and not rem: errors.append(f'{sid}:拆除事件没有对应支撑')
        if event=='excavate' and h<=last_h: errors.append(f'{sid}:开挖事件应使坑底加深')
        if add and event!='install': errors.append(f'{sid}:安装操作必须独立事件')
        if rem and event!='remove': errors.append(f'{sid}:拆除操作必须独立事件')
        if any(item in installed or item in ever for item in add): errors.append(f'{sid}:支撑重复安装')
        if any(item not in installed for item in rem): errors.append(f'{sid}:拆除未安装支撑')
        for item in add: installed.add(item); ever.add(item)
        for item in rem: installed.discard(item)
        last_h=h
    if not has_excavate: errors.append('缺少开挖设计工况')
    if not has_final: errors.append('缺少明确标记isFinalDesignState的最终控制工况')
    return {'complete':not errors,'missingEvents':errors,'stageCount':len(stages),
            'source':'JGJ120-2012 4.1.2',
            'warning':'工况覆盖检查不计算墙体内力；拆撑阶段需单独验算替代结构'}

EXEC = {'JGJ120_4_1_6_M_ESTIMATE':_m_estimate, 'JGJ120_3_4_2_RANKINE':_pressures,'JGJ120_4_1_7_PILE_WIDTH':_pile_width,
        'JGJ120_4_1_5_SOIL_KH':_kh,'JGJ120_4_1_4_SOIL_REACTION':_soil_reaction,
        'GB55003_7_2_1_ULS':_uls,'GB55003_7_2_2_SLS':_sls,'JGJ120_4_1_2_STAGES':_stages}


def calculate(request:CalcRequest) -> dict:
    values, provenance, issues=_normalized(request)
    results=[]
    if '待确认' in request.project_region or '未知' in request.project_region:
        issues.append({'code':'UNCONFIRMED_REGION','message':'项目地区未确认；当前公式可预览，地方标准适用性未完成'})
    if '浙江' in request.project_region or request.project_region == 'Zhejiang':
        issues.append({'code':'LOCAL_STANDARD_REVIEW_REQUIRED','message':'浙江项目尚未完成DB33/T1096-2014逐条适用性和地方验算；当前单项公式仅供审核'})
    elif request.project_region not in ('待确认地区','未知'):
        issues.append({'code':'LOCAL_STANDARD_SCOPE_REVIEW_REQUIRED','message':'必须完成工程所在地适用的地方标准、项目类别与规范版本核查；当前单项公式仅供审核'})
    if request.design_date < SOURCES['GB55003-2021']['effective']:
        issues.append({'code':'DESIGN_DATE_CONFLICT','message':'当前规范组合不适合该设计日期，请另选历史版本并作适用性审核'})
    if request.design_date > date.today().isoformat():
        issues.append({'code':'FUTURE_DESIGN_DATE','message':'设计日期在未来，请确认版本及适用性'})
    for identifier in request.method_ids:
        try:
            spec=method(identifier)
        except ValueError as exc:
            results.append({'methodId':identifier,'status':'blocked','reason':str(exc)}); continue
        if identifier not in EXEC:
            results.append({'methodId':identifier,'status':'not_implemented',
                            'reason':'模块尚未完成规范公式、适用性、独立算例与成果链；不可调用旧近似充当规范结果',
                            'standard':spec['standard'],'clause':spec['clause']}); continue
        missing=[f for f in spec['required'] if f not in values]
        if missing:
            results.append({'methodId':identifier,'status':'missing_parameters','missing':missing,
                            'standard':spec['standard'],'clause':spec['clause']}); continue
        try:
            output=EXEC[identifier](values)
            used=[f for f in spec['required'] if f in provenance]
            confirmed=all(provenance[f]['ready'] for f in used)
            if identifier=='JGJ120_4_1_6_M_ESTIMATE':
                issues.append({'code':'M_ESTIMATE_REVIEW_REQUIRED','field':'m',
                               'message':'由c、φ、vb经验公式得到的m为候选值；须经工程师确认并按正式来源重新输入'})
                confirmed=False
            if output.get('requiresAdditionalReview'):
                issues.append({'code':'METHOD_REQUIRES_ADDITIONAL_REVIEW','field':identifier,
                               'message':'当前公式存在张裂或特殊物理情景，需要完成附加作用复核'})
            satisfied=output.get('satisfied', output.get('complete', True))
            if not satisfied:
                issues.append({'code':'ENGINEERING_CHECK_FAILED','field':identifier,
                               'message':f'{spec["label"]}已计算但未满足其验算条件，必须整改或重新校审'})
            results.append({'methodId':identifier,'status':'calculated','dataStatus':
                            'confirmed_inputs' if confirmed else 'preview_only',
                            'checkSatisfied':satisfied,
                            'source':spec['standard'],'clause':spec['clause'], 'scope':spec['scope'],
                            'inputsUsed':used,'output':output})
        except (ValueError,ArithmeticError) as exc:
            results.append({'methodId':identifier,'status':'invalid_domain','reason':str(exc),
                            'standard':spec['standard'],'clause':spec['clause']})
    normalized={'fields':provenance,'otherFields':{k:v for k,v in values.items() if k not in provenance}}
    identity=sha256(json.dumps(request.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    has_fail=any(item['status']!='calculated' for item in results)
    can_use=not has_fail and not issues and all(item.get('dataStatus')=='confirmed_inputs' for item in results)
    return {**META,'requestHash':identity,'projectId':request.project_id, 'task':request.task,
            'methodResults':results,'parameterProvenance':normalized,'issues':issues,
            'allRequestedMethodsCalculated':not has_fail,
            'previewOnly':not can_use,'allChecksSatisfied':not has_fail and not any(r.get('checkSatisfied') is False for r in results), 'wholePitDesignReady':False,
            'formalIssueReady':False,
            'nextAction':('完成缺项与来源确认' if has_fail or issues else '继续执行适用的全阶段受力、稳定和构件验算后人工校审')}
