"""Stage-specific one-sided elastic soil reaction + beam / strut solver.

Normative mapping: outside prescribed active earth pressure with pile spacing,
inside reaction ps=ps0+ks*u with effective soil reaction width b0; total
embedded PS<=EP, never local clipping masquerading as code compliance.
Hydrostatic water pressure is applied ONCE independently.
"""
from __future__ import annotations
from hashlib import sha256
import json
from math import ceil,pi,sqrt
import numpy as np
from app.section.solver import solve_stages
from .contracts import Section
from .pressure import pressure_state,vertical_effective,width
from .preflight import preflight
from .engineering_checks import normative_coverage
from .normative.actions import combinations,run_adverse_branches,explicit_action_combinations
from .normative.reference_review import assess_real_project_comparability

METHOD_VERSION='jgjt120_one_strut_elastic/1.2+soft_heave_heterogeneous_seepage_explicit_actions' 

def canonical_hash(data):
    return sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def nodes_for(model):
    pts={0.,model.pile.length_m,model.strut.depth_m}
    for x in model.layers:pts.update((x.top_m,x.bottom_m))
    for x in model.stages:pts.add(x.excavation_depth_m)
    anchors=sorted(z for z in pts if 0<=z<=model.pile.length_m)
    out=[anchors[0]]
    for a,b in zip(anchors,anchors[1:]):
        n=ceil((b-a)/model.mesh_max_m-1e-10)
        out.extend(round(a+(b-a)*i/n,10) for i in range(1,n+1))
    if len(out)>251:raise ValueError('网格过密，超过单剖面在线计算限制')
    return out

def _layer(model,z):
    for layer in model.layers:
        if layer.top_m<=z<layer.bottom_m:return layer
    return model.layers[-1]

def discrete_model(model:Section, pressure_factors=None):
    nodes=nodes_for(model);b0=width(model.pile.diameter_m,model.pile.spacing_m)
    f=pressure_factors or {'earth':1.,'permanent':1.,'variable':1.,'water':1.}
    if any((key not in f or not 0<=f[key]<=3) for key in ('earth','permanent','variable','water')):
        raise ValueError('Each action factor must be explicit and within 0..3')
    gauss=((-sqrt(3/5),5/9),(0.,8/9),(sqrt(3/5),5/9))
    stations=[]
    for e,(a,b) in enumerate(zip(nodes,nodes[1:])):
        for i,(x,w) in enumerate(gauss):
            z=(a+b)/2+x*(b-a)/2
            for side,shape in ((0,(1-x)/2),(1,(1+x)/2)):
                stations.append((f'{e}:{i}:{side}',e+side,z,w*(b-a)/2*shape))
    stages=[];support_node=nodes.index(round(model.strut.depth_m,10))
    for step in model.stages:
        springs=[];forces=[0.]*len(nodes)
        for key,node,z,weight in stations:
            layer=_layer(model,z)
            sigma_out=vertical_effective(model.layers,z,step.water_outside_depth_m,0,model.water_unit_weight_kn_m3)+step.surcharge_kpa
            out=pressure_state(layer,sigma_out)
            sigma_in=vertical_effective(model.layers,z,step.water_inside_depth_m,step.excavation_depth_m,model.water_unit_weight_kn_m3)
            inside=pressure_state(layer,sigma_in)
            # Separate variable action by marginal characteristic pressure. When all
            # factors are one, this reproduces the old clipped Rankine pressure exactly.
            # Soil reactions retain characteristic constitutive stiffness; no second
            # independent passive pressure is subtracted from the wall equilibrium.
            base_sigma=vertical_effective(model.layers,z,step.water_outside_depth_m,0,model.water_unit_weight_kn_m3)
            earth=pressure_state(layer,base_sigma)['active']
            if step.surcharge_permanent_kpa is None or step.surcharge_variable_kpa is None:
                perm=step.surcharge_kpa;var=0.
            else:perm=step.surcharge_permanent_kpa;var=step.surcharge_variable_kpa
            with_perm=pressure_state(layer,base_sigma+perm)['active']
            with_all=pressure_state(layer,base_sigma+perm+var)['active']
            outside_soil=(f['earth']*earth+f['permanent']*(with_perm-earth)
                          +f['variable']*(with_all-with_perm))
            if step.event=='initial':
                outside_soil=pressure_state(layer,base_sigma)['p0']
            if z>0:
                p=outside_soil*model.pile.spacing_m*weight
                springs.append({'id':'outside:'+key,'node':node,'depthM':z,'layerId':layer.id,
                    'weightM':weight,'side':'outside','sign':-1,'p0Kn':p,
                    'kKnPerM':0.,'lowerKn':p,'upperKn':p})
                forces[node]+=f['water']*model.water_unit_weight_kn_m3*max(0,z-step.water_outside_depth_m)*model.pile.spacing_m*weight
            if z>step.excavation_depth_m:
                p0=inside['p0']*b0*weight
                spring_k=layer.m_kn_m4*(z-step.excavation_depth_m)*b0*weight
                springs.append({'id':'inside:'+key,'node':node,'depthM':z,'layerId':layer.id,
                    'weightM':weight,'side':'inside','sign':1,'p0Kn':p0,
                    'kKnPerM':spring_k,'lowerKn':0.,'upperKn':1e9,
                    'passiveReferenceKn':inside['passive']*b0*weight})
                forces[node]-=f['water']*model.water_unit_weight_kn_m3*max(0,z-step.water_inside_depth_m)*model.pile.spacing_m*weight
            # On the excavated portion there is no inside water (input gate disallows ponding).
        installed=[]
        if step.event=='install_strut':
            # Input stiffness is per strut; wall length tributary per pile: spacing / strut spacing.
            effective=model.strut.stiffness_kn_m*model.pile.spacing_m/model.strut.spacing_along_wall_m
            installed=[{'id':model.strut.id,'node':support_node,'kind':'elastic',
                        'stiffnessKnPerM':effective,'compressionOnly':False,'preloadKn':0.}]
        stages.append({'id':step.id,'name':step.id,'event':step.event,'externalForcesKn':forces,
                       'soilSprings':springs,'installSupports':installed,'removeSupports':[],
                       'excavationDepthM':step.excavation_depth_m})
    EI=model.pile.elastic_modulus_kpa*model.pile.inertia_factor*pi*model.pile.diameter_m**4/64
    return {'schemaVersion':'pitguard.one-strut-discrete/1','nodesM':nodes,'eiKnM2':EI,
            'toeBoundary':model.pile.toe_boundary,'stages':stages,'forceBasis':'single_pile',
            'b0M':b0,'widthEarthM':model.pile.spacing_m,
            'pressureBasis':'effective_separated','pressureFactors':f,'methodVersion':METHOD_VERSION}

def _sections(step,nodes,solved):
    u=solved['nodalDisplacementM'];forces=solved['elementEndForces']
    pts=[]
    for e,(lo,hi) in enumerate(zip(nodes,nodes[1:])):
        v1,m1,_,m2=forces[e]
        pts.append({'depthM':lo,'side':'below','uMm':u[e]*1000,'momentKnM':-m1,'shearKn':v1})
        pts.append({'depthM':hi,'side':'above','uMm':u[e+1]*1000,'momentKnM':m2,'shearKn':v1})
    return pts

def calculate(payload:dict):
    mdl=Section.model_validate(payload)
    pre=preflight(payload)
    frozen=mdl.model_dump(mode='json')
    snapshot_hash=canonical_hash(frozen)
    disc=discrete_model(mdl)
    solver=solve_stages(disc)
    stage_results=[];issues=[dict(x) for x in pre['sourceIssues']]
    all_ok=solver['status']=='success'
    for source,calc in zip(disc['stages'],solver['stages']):
        success=calc['status']=='success';points=_sections(source,disc['nodesM'],calc) if success else []
        inside=[x for x in calc.get('soilStates',[]) if x['side']=='inside']
        ps=sum(x['pressureKn'] for x in inside)
        ep=sum(x['passiveReferenceKn'] for x in inside)
        ratio=ps/ep if ep>0 else None
        passive_ok=ps<=ep+1e-7*max(1,ep)
        if not passive_ok:
            issues.append({'code':'PASSIVE_RESULTANT_LIMIT','field':source['id'],'message':
                           f'坑内土反力合力{ps:.2f}kN超过被动参考合力{ep:.2f}kN；应增加嵌固深度并重算，当前阶段不可通过'})
        if not success:
            issues.append({'code':'SOLVER_FAILURE','field':source['id'],'message':calc.get('failureReason')})
        stage_results.append({'id':source['id'],'event':source['event'],'status':calc['status'],
              'failureReason':calc.get('failureReason'),'excavationDepthM':source['excavationDepthM'],
              'uMm':max((abs(x['uMm']) for x in points),default=None),
              'maxAbsMomentKnM':max((abs(x['momentKnM']) for x in points),default=None),
              'maxAbsShearKn':max((abs(x['shearKn']) for x in points),default=None),
              'points':points,'soilPressurePoints':[{'side':x['side'],'depthM':x['depthM'],
                   'pressureKn':x['pressureKn'],'weightM':x['weightM']} for x in calc.get('soilStates',[])],
              'strut': [{'id':x['id'],'reactionKn':x['reactionKn'],
                         'installDisplacementMm':1000*x['referenceDisplacementM']} for x in calc.get('supportStates',[])],
              'passiveResultantKn':ps if success else None,
              'passiveReferenceKn':ep if success else None,
              'passiveLimitSatisfied':passive_ok if success else None,
              'equilibrium':calc.get('equilibrium'),'solverResidual':calc.get('residualInfKn')})
        all_ok=all_ok and success and passive_ok
    complete=all_ok and all(x['points'] for x in stage_results)
    # No partial envelope or reinforcement if an actual load-bearing stage failed.
    pts=[(s['id'],p) for s in stage_results for p in s['points']] if complete else []
    gov={}
    for key in ('uMm','momentKnM','shearKn'):
        if pts:
            identity,point=max(pts,key=lambda q:abs(q[1][key]))
            gov[key]={'stageId':identity,'depthM':point['depthM'],
                      'side':point['side'],'value':point[key]}
        else:gov[key]=None
    displacement=max((abs(x[1]['uMm']) for x in pts),default=None)
    displacement_ok=displacement is not None and displacement<=mdl.displacement_limit_mm
    if complete and not displacement_ok:
        issues.append({'code':'DISPLACEMENT_OVER_LIMIT','field':'displacement_limit_mm',
                       'message':f'最大位移{displacement:.2f}mm超过控制值{mdl.displacement_limit_mm:.2f}mm'})
    # Engineering core uses the governed moment demand only; never infer axial loads or rebar capacity.
    demand=abs(gov['momentKnM']['value'])*mdl.design_effect_factor*mdl.gamma0 if complete else None
    area_provided=mdl.cage.bar_count*pi*mdl.cage.bar_diameter_mm**2/4
    area_min=pi*(mdl.pile.diameter_m*1000)**2/4*mdl.cage.minimum_ratio
    cage_geo=mdl.pile.diameter_m*500-mdl.cage.bar_axis_cover_mm
    stirrup_cover=mdl.cage.bar_axis_cover_mm-mdl.cage.bar_diameter_mm/2-mdl.cage.stirrup_diameter_mm
    rebar={'status':'demand_preview_only' if complete else 'not_calculated',
           'governingMomentStageId':gov['momentKnM']['stageId'] if complete else None,
           'governingShearStageId':gov['shearKn']['stageId'] if complete else None,
           'momentDesignKnM':demand,'gamma0':mdl.gamma0,'designEffectFactor':mdl.design_effect_factor,'shearDesignKn':abs(gov['shearKn']['value'])*mdl.design_effect_factor*mdl.gamma0 if complete else None,
           'providedAreaMm2':area_provided,'minimumAreaMm2':area_min,
           'barCount':mdl.cage.bar_count,'barDiameterMm':mdl.cage.bar_diameter_mm,
           'outerStirrupClearCoverMm':stirrup_cover,'minimumAreaMet':area_provided>=area_min,
           'fullRebarVerification':False,
           'unverified':['N-M','V_Rd','裂缝','锚固与接头','箍筋构造','压顶梁及支撑体系验算']}
    if cage_geo<=mdl.cage.bar_diameter_mm/2 or stirrup_cover<0:
        issues.append({'code':'INVALID_CAGE_GEOMETRY','field':'cage','message':'钢筋笼实体超出混凝土截面'})
    issues.extend([{'code':'ACTION_COMBINATION_REQUIRES_REVIEW','field':'design_effect_factor',
                    'message':'本模块将输入的作用系数与γ0各施加一次；完整标准组合及基本组合生成尚未集成，需独立校核'},
                   {'code':'STABILITY_NOT_INTEGRATED','field':'JGJ120_4_2_STABILITY',
                    'message':'整体稳定、抗倾覆、抗隆起和渗透稳定尚未全套集成；不可正式发行'},
                   {'code':'REBAR_NOT_FULLY_VERIFIED','field':'cage',
                    'message':'仅生成受力需求和给定钢筋信息，缺轴弯、抗剪、裂缝、锚固及构造验算'}])
    recommendations=[]
    if any(x['code']=='PASSIVE_RESULTANT_LIMIT' for x in issues):
        recommendations.append({'field':'pile.length_m','type':'design_change_proposal',
                                'text':'候选：增加墙趾嵌固长度并完成稳定及抗力合力重新验算；无自动数值修改'})
    if complete and not displacement_ok:
        recommendations.append({'field':'pile.inertia_factor/strut.stiffness_kn_m',
                                'type':'design_change_proposal',
                                'text':'候选：提高经结构计算确认的 EI 或支撑刚度、调整支撑标高；所有阶段重算'})
    if not pre['engineeringEvidenceComplete']:
        recommendations.append({'field':'sources','type':'missing_evidence',
                                'text':'按参数清单补充勘察、材料与构件计算依据，逐项确认后另存设计快照'})
    action_results=combinations(mdl,stage_results)
    action_results['explicit']=explicit_action_combinations(mdl)
    adverse=run_adverse_branches(mdl,stage_results) if complete and mdl.adverse_cases else []
    action_results['constructionAdverse']=adverse
    # Each adverse branch solves all stages independently. Preserve both
    # individual engineering envelopes and their governing source identities.
    design_peak={'designMomentKnM':demand,'stageId':rebar['governingMomentStageId'],
                 'scenario':'standard','designShearKn':rebar['shearDesignKn']}
    if complete:
        for c in adverse:
            if c['status']!='calculated':
                issues.append({'code':'ADVERSE_SCENARIO_NOT_SOLVED','field':c['case'],
                               'message':c.get('reason','不利工况未完成')})
                continue
            p=c['characteristicEffects']['momentKnM'];v=c['characteristicEffects']['shearKn']
            if p is not None:
                m=abs(p['signedValue'])*mdl.design_effect_factor*mdl.gamma0
                if m>design_peak['designMomentKnM']:
                    design_peak={'designMomentKnM':m,'stageId':p['stageId'],
                          'scenario':c['case'],'designShearKn':abs(v['signedValue'])*mdl.design_effect_factor*mdl.gamma0 if v else None}
    action_results['governingDesignMoment']=design_peak
    if complete and design_peak['designMomentKnM'] is not None:
        rebar['momentDesignKnM']=design_peak['designMomentKnM']
        rebar['governingMomentStageId']=design_peak['stageId']
        rebar['governingMomentScenario']=design_peak['scenario']
        # Shear and moment can govern in different stages or scenarios.
        maximum_shear=abs(gov['shearKn']['value'])*mdl.design_effect_factor*mdl.gamma0
        controlling_shear={'valueKn':maximum_shear,'stageId':gov['shearKn']['stageId'],'scenario':'standard'}
        for adverse_case in adverse:
            if adverse_case['status']!='calculated':continue
            v=adverse_case['characteristicEffects'].get('shearKn')
            if v:
                scaled=abs(v['signedValue'])*mdl.design_effect_factor*mdl.gamma0
                if scaled>controlling_shear['valueKn']:
                    controlling_shear={'valueKn':scaled,'stageId':v['stageId'],'scenario':adverse_case['case']}
        rebar['shearDesignKn']=controlling_shear['valueKn']
        rebar['governingShearStageId']=controlling_shear['stageId']
        rebar['governingShearScenario']=controlling_shear['scenario']
        action_results['governingDesignShear']=controlling_shear
    # When explicit ULS cases exist they supersede the legacy single-factor
    # design demands. The original characteristic stage arrays remain sealed.
    explicit=action_results['explicit']
    if explicit['status']=='calculated_scope_limited':
        mexp=explicit['governing'].get('momentKnM')
        vexp=explicit['governing'].get('shearKn')
        if mexp:
            rebar['momentDesignKnM']=abs(mexp['designValue'])
            rebar['governingMomentStageId']=mexp['stageId']
            rebar['governingMomentScenario']=mexp['case']
        if vexp:
            rebar['shearDesignKn']=abs(vexp['designValue'])
            rebar['governingShearStageId']=vexp['stageId']
            rebar['governingShearScenario']=vexp['case']
        rebar['designActionBasis']='explicit_factored_load_re_solve_no_legacy_factor'
    coverage=normative_coverage(mdl,stage_results,demand,action_results)
    for code in coverage['blockingCodes']:
        issues.append({'code':'NORMATIVE_'+code.upper().replace('.','_'),
                       'field':code,'message':f'规范验算 {code} 尚未完成或当前条件未满足：参见 normativeCoverage'})
    out={'schemaVersion':'pitguard.one-strut-result/1','methodVersion':METHOD_VERSION,
         'snapshotHash':snapshot_hash,'discreteHash':canonical_hash(disc),
         'projectId':mdl.project_id,'sectionId':mdl.section_id,'snapshot':frozen,
         'preflight':pre,'solverStatus':solver['status'],
         'analysisStatus':'complete' if complete else 'blocked',
         'engineeringStatus':'review_required', 'formalIssueReady':False,
         'methodAssumptions':[
            '仅平地面有效应力分算，朗肯主动压力；坑内初始反力ps0与m(z-h)u',
            '土反力作用宽度b0，坑外主动土压力采用单桩中心距',
            '嵌固段Psk<=Epk采用合力审查；超出则阻断，不局部截断',
            '支撑无预力，保留安装参考位移；完整施工历史还需工程验证'],
         'mesh':{'nodesM':disc['nodesM'],'nodeCount':len(disc['nodesM']),
                 'eiKnM2':disc['eiKnM2'],'b0M':disc['b0M'],'earthWidthM':disc['widthEarthM']},
         'stages':stage_results,'governing':gov,
         'checks':{'displacement':{'valueMm':displacement,'limitMm':mdl.displacement_limit_mm,
                                 'satisfied':displacement_ok if complete else None},
                   'passiveResultant':{'satisfied':all(s['passiveLimitSatisfied'] is True for s in stage_results) if complete else None}},
         'reinforcement':rebar,'actionCombinations':action_results,'normativeCoverage':coverage,
         'historicalReferenceComparison':assess_real_project_comparability(mdl) if mdl.section_id=='3-3' else None,'issues':issues,'recommendations':recommendations,
         'humanReviewRequired':True}
    # Source of truth for immutable outputs: digest all published data excluding digest field.
    out['resultHash']=canonical_hash(out)
    return out
