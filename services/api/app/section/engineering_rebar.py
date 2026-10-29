"""Bounded circular-section checks with explicit engineering context.

No approval of a construction design is made here. A checked equation and a
provided criterion retain separate provenance; absent facts remain unknown.
"""
from __future__ import annotations
from functools import lru_cache
from math import pi, sin, radians, isfinite
import numpy as np
from app.section.engineering_models import EngineeringDesign


SOURCE_ROOT = 'https://gf.cabr-fire.com/m/'
SOURCES = {
    'shear': {'standard': 'GB/T 50010-2010 (2024)', 'clause': '6.3.1, 6.3.4, 6.3.15', 'url': SOURCE_ROOT+'article-12606.htm'},
    'anchorage': {'standard': 'GB/T 50010-2010 (2024)', 'clause': '8.3.1, 8.3.2', 'url': SOURCE_ROOT+'article-12617.htm'},
    'lap': {'standard': 'GB/T 50010-2010 (2024)', 'clause': '8.4', 'url': SOURCE_ROOT+'article-12618.htm'},
    'crack': {'standard': 'GB/T 50010-2010 (2024)', 'clause': '7.1.2 scope; 7.1.3 stress assumptions', 'url': SOURCE_ROOT+'article-12612.htm'},
}
CHECK_GROUPS = {'axial_flexural_interaction':'nm_cases', 'shear':'shear', 'minimum_cover':'detailing',
                'bar_spacing':'detailing', 'stirrup_detailing':'detailing', 'anchorage':'anchorage',
                'anchorage_confinement':'anchorage', 'lap':'lap', 'service_stress':'service', 'crack_width':'service'}


@lru_cache(maxsize=4)
def _gauss(n):
    x,w=np.polynomial.legendre.leggauss(n)
    x.flags.writeable=w.flags.writeable=False
    return x,w


def _geometry(model):
    r=model['reinforcement']; radius=model['pile']['diameter_m']*500
    if r.get('bar_count') is None or r.get('bar_diameter_mm') is None:
        raise ValueError('reinforcement.bar_count / reinforcement.bar_diameter_mm')
    count=r['bar_count'];d=r['bar_diameter_mm'];axis=r['bar_axis_cover_mm'];rs=radius-axis
    area=count*pi*d*d/4
    if count<6 or rs<=0 or axis<d/2 or 2*rs*sin(pi/count)<d or area>.04*pi*radius**2:
        raise ValueError('Given circular cage is outside the validated geometry/4% numerical range')
    return r,radius,rs,area


def _ultimate_forces(model, sign=1, integration_points=384):
    r,R,rs,area=_geometry(model)
    if sign not in (-1,1) or integration_points<16:
        raise ValueError('Invalid bending orientation or quadrature order')
    x,w=_gauss(integration_points)
    by=sign*rs*np.cos(np.arange(r['bar_count'])*2*pi/r['bar_count']+radians(r['bar_start_angle_deg']))
    ab=area/r['bar_count']
    def stress(e):
        q=np.clip(e/r['concrete_peak_strain'],0,1)
        return r['concrete_design_strength_mpa']*(2*q-q*q)
    def evaluate(c):
        # Split at the material-law corner; do not integrate a fictitious
        # rectangular section or double-count displaced concrete.
        transition=max(0,c*(1-r['concrete_peak_strain']/r['concrete_ultimate_strain']))
        F=M=0.
        for lo,hi in ((0.,min(c,transition)),(min(c,transition),min(2*R,c))):
            if hi<=lo: continue
            depth=(hi+lo)/2+(hi-lo)/2*x;y=R-depth
            da=2*np.sqrt(np.maximum(0,R*R-y*y))*w*(hi-lo)/2
            f=stress(r['concrete_ultimate_strain']*(1-depth/c))*da
            F+=float(np.sum(f));M+=float(np.dot(f,y))
        e=r['concrete_ultimate_strain']*(1-(R-by)/c)
        steel=np.clip(r['steel_modulus_mpa']*e,-r['steel_design_strength_mpa'],r['steel_design_strength_mpa'])
        net=(steel-stress(e))*ab
        return F+float(np.sum(net)), M+float(np.dot(net,by)), float(np.min(e)),float(np.max(e))
    return evaluate,R


def circular_nm_capacity(model: dict, axial_force_design_kn: float, *, sign: int=1,
                         integration_points: int=384) -> dict:
    """Partial N-M branch only: concrete compression depth <= diameter.

    N is positive in compression. Same explicit material laws as the v1
    mechanical pure-bending calculation; no slenderness or seismic resistance
    factors, confinement enhancement, concrete tension or steel rupture model.
    """
    if not isfinite(axial_force_design_kn): raise ValueError('Axial force must be finite')
    forces,R=_ultimate_forces(model,sign,integration_points)
    upper=forces(2*R)[0]/1000
    base={'method':'circular_discrete_bars_nm_partial_v1','bendingSign':sign,
          'axialForceDesignKn':axial_force_design_kn,'maximumSupportedAxialKn':upper,
          'minimumSupportedAxialKn':0.,'capacityKnM':None,'neutralAxisMm':None,'axialResidualKn':None,
          'status':'outside_method_scope'}
    if axial_force_design_kn<0 or axial_force_design_kn>upper: return base
    lo,hi=R*1e-8,2*R; target=axial_force_design_kn*1000
    if forces(lo)[0]>target: return base
    for _ in range(80):
        mid=(lo+hi)/2
        if forces(mid)[0]>target: hi=mid
        else:lo=mid
    c=(lo+hi)/2;N,M,e_min,e_max=forces(c)
    base.update(status='calculated',capacityKnM=abs(M)/1e6,neutralAxisMm=c,
                axialResidualKn=(N-target)/1000,minimumSteelStrain=e_min,maximumSteelStrain=e_max,
                integrationPointsPerSegment=integration_points)
    return base


def circular_service_stress(model: dict, moment_service_knm: float, concrete_modulus_mpa: float) -> dict:
    """N=0 cracked elastic circular section; stress is not a crack width."""
    r,R,rs,area=_geometry(model)
    if not isfinite(moment_service_knm) or not isfinite(concrete_modulus_mpa) or concrete_modulus_mpa<=0:
        raise ValueError('Service actions/modulus must be finite and modulus positive')
    if moment_service_knm==0:
        return {'neutralAxisMm':None,'maximumTensileSteelStressMpa':0.,'maximumCompressiveConcreteStressMpa':0.,
                'axialResidualKn':0.,'momentResidualKnM':0.,'barStressesMpa':[0.]*r['bar_count']}
    sign=1 if moment_service_knm>0 else -1
    by=sign*rs*np.cos(np.arange(r['bar_count'])*2*pi/r['bar_count']+radians(r['bar_start_angle_deg']))
    ab=area/r['bar_count'];x,w=_gauss(256);Es=r['steel_modulus_mpa'];Ec=concrete_modulus_mpa
    def unit_forces(c):
        depth=c/2*(x+1);y=R-depth
        da=2*np.sqrt(np.maximum(0,R*R-y*y))*w*c/2
        e=c-depth;f=Ec*e*da
        be=c-(R-by)
        net=(Es*be-Ec*np.maximum(be,0))*ab
        return float(np.sum(f)+np.sum(net)),float(np.dot(f,y)+np.dot(net,by)),be
    lo,hi=0.,2*R
    for _ in range(80):
        mid=(lo+hi)/2
        if unit_forces(mid)[0]>0:hi=mid
        else:lo=mid
    c=(lo+hi)/2;F,M,be=unit_forces(c);k=abs(moment_service_knm)*1e6/M
    stresses=Es*be*k
    return {'neutralAxisMm':c,'maximumTensileSteelStressMpa':max(0.,-float(np.min(stresses))),
            'maximumCompressiveConcreteStressMpa':Ec*c*k,'axialResidualKn':F*k/1000,
            'momentResidualKnM':(M*k-abs(moment_service_knm)*1e6)/1e6,
            'barStressesMpa':[float(v) for v in stresses]}



def circular_service_nm_stress(model: dict, axial_force_service_kn: float,
                               moment_service_knm: float,
                               concrete_modulus_mpa: float) -> dict:
    """Cracked-linear circular-section *stress* equilibrium for paired N and M.

    Positive N is compression. Concrete tension is neglected; concrete
    compression and steel are elastic. Steel area displaces compressive
    concrete (without losing the steel contribution).  A two-component
    semismooth force-equilibrium Newton iteration is used. This is not a
    GB/T 50010 7.1.2 circular crack-width model and cannot approve cracking.
    """
    r,R,rs,area=_geometry(model)
    if not all(isfinite(v) for v in (axial_force_service_kn, moment_service_knm, concrete_modulus_mpa)):
        raise ValueError('Service N, M and E must be finite')
    if concrete_modulus_mpa<=0:raise ValueError('Concrete elastic modulus must be positive')
    Es=r['steel_modulus_mpa']; ratio=Es/concrete_modulus_mpa
    pos=rs*np.cos(np.arange(r['bar_count'])*2*pi/r['bar_count']+radians(r['bar_start_angle_deg']))
    steel_area=area/r['bar_count']
    x,w=_gauss(256)
    y=R*x
    da=2*R*np.sqrt(np.maximum(0.,R*R-y*y))*w
    scaled=y/R; barscaled=pos/R
    nt=1000*axial_force_service_kn; mt=1e6*moment_service_knm
    gross_area=pi*R*R; gross_i=pi*R**4/4
    a=nt/(gross_area+ratio*area)
    b=(mt*R)/(gross_i+ratio*steel_area*float(np.dot(pos,pos)))

    def eval_state(a,b, derivative=True):
        concrete=a+b*scaled
        steel=a+b*barscaled
        cplus=np.maximum(concrete,0.)
        steel_net=ratio*steel-np.maximum(steel,0.)
        f=float(np.dot(cplus,da)+steel_area*np.sum(steel_net))
        m=float(np.dot(cplus*y,da)+steel_area*np.dot(steel_net,pos))
        if not derivative:return f,m,concrete,steel
        cc=(concrete>0).astype(float)
        ss=ratio-(steel>0).astype(float)
        na=float(np.dot(cc,da)+steel_area*np.sum(ss))
        nb=float(np.dot(cc*scaled,da)+steel_area*np.dot(ss,barscaled))
        ma=float(np.dot(cc*y,da)+steel_area*np.dot(ss,pos))
        mb=float(np.dot(cc*y*scaled,da)+steel_area*np.dot(ss*barscaled,pos))
        return f,m,na,nb,ma,mb

    scale=max(1.,abs(nt),abs(mt)/R)
    solved=False
    for iteration in range(60):
        f,m,na,nb,ma,mb=eval_state(a,b)
        residual=max(abs(f-nt),abs(m-mt)/R)/scale
        if residual<1e-10:
            solved=True;break
        det=na*mb-nb*ma
        if abs(det)<1e-18:
            break
        df=nt-f;dm=mt-m
        da_step=(df*mb-nb*dm)/det
        db_step=(na*dm-df*ma)/det
        fraction=1.
        for _ in range(30):
            aa=a+fraction*da_step; bb=b+fraction*db_step
            ff,mm,*_=eval_state(aa,bb,False)
            if max(abs(ff-nt),abs(mm-mt)/R)/scale < residual:
                a=aa;b=bb;break
            fraction*=.5
        else:
            break
    f,m,concrete,steel=eval_state(a,b,False)
    resn=(f-nt)/1000;resm=(m-mt)/1e6
    neutral_axis=(R+R*a/b) if abs(b)>1e-12 else None
    steel_stresses=(ratio*steel)
    result={'method':'circular_elastic_cracked_nm_stress_v1',
            'status':'calculated' if solved else 'solver_failure',
            'axialForceServiceKn':axial_force_service_kn,
            'momentServiceKnM':moment_service_knm,
            'maximumTensileSteelStressMpa':max(0.,-float(np.min(steel_stresses))),
            'maximumCompressiveConcreteStressMpa':max(0.,float(np.max(concrete))),
            'neutralAxisDepthMm':float(neutral_axis) if neutral_axis is not None else None,
            'axialResidualKn':resn,'momentResidualKnM':resm,
            'barStressesMpa':[float(v) for v in steel_stresses],
            'concreteTensionNeglected':True,
            'crackWidthMm':None,'crackWidthStatus':'outside_method_scope',
            'limitations':['服务阶段N和M需属于同一荷载组合及同一施工阶段',
                           '混凝土受拉区不计强度，受压区与钢筋按弹性计算；长期徐变收缩与裂缝间距不在当前方法内',
                           'GB/T 50010 7.1.2列举的普通截面裂缝公式不得直接外推为圆桩裂缝宽度']}
    return result

def _check(id,method,*,basis=None,scope=''):
    return {'id':id,'method':method,'status':'missing_input','demand':None,'capacity':None,'utilization':None,
            'units':None,'details':{},'requiredInputs':[],'basis':basis or [],'scope':scope,
            'limitations':[],'governingCase':None}


def _missing(c,*fields):
    c['status']='missing_input';c['requiredInputs'].extend(fields)
    return c


def _status(rows):
    states={row['status'] for row in rows}
    for s in ('fail','outside_method_scope','missing_input','not_calculated'):
        if s in states:return s
    return 'pass'


def _extrema(stage):
    points=stage.get('points') or []
    if not points:return []
    low=min(points,key=lambda p:p['momentCharacteristicKnM']);high=max(points,key=lambda p:p['momentCharacteristicKnM'])
    return [low] if low==high else [low,high]


def _stage_map(stages):
    if not stages:return {}
    if len({s['id'] for s in stages})!=len(stages):raise ValueError('Duplicate stage result identity')
    return {s['id']:s for s in stages}


def check_engineering_rebar(model: dict, envelope: dict, design: dict | None,
                            stages: list[dict] | None=None) -> dict:
    """Consume sealed actions; caller verifies sealing before this pure function.

    Numeric unknowns remain null. Every pass has a local scope. A missing
    design group never adopts an affirmative seismic/environment assumption.
    """
    d=EngineeringDesign.model_validate(design or {}).model_dump(mode='json')
    checks={id:_check(id, 'not_selected') for id in CHECK_GROUPS}
    result={'method':'circular_pile_engineering_checks_v1','status':'review_required','formalDesignReady':False,
            'checks':list(checks.values()),'requiredInputs':[],'unverifiedChecks':[],
            'limitations':['仅适用于各项列明范围；局部通过不代表完整规范符合或可签发施工图。',
                           'N为显式阶段全桩恒定截面作用；不由横向支点反力推导，不计算轴力引起的二阶效应。',
                           '抗震、耐久性选类、搭接布置、节点传力、裂缝宽度及施工质量仍须逐项取得依据。']}
    if not envelope.get('complete'):
        for c in checks.values():c['status']='not_calculated'
        result['status']='not_calculated';result['unverifiedChecks']=list(checks)
        return result
    for id,group in CHECK_GROUPS.items():
        if d[group] is None:_missing(checks[id],f'engineering_design.{group}')
    r=model['reinforcement']; factor=model['analysis']['design_effect_factor'];stage_map=_stage_map(stages)
    try:_,R,rs,area=_geometry(model); geometry_ok=True
    except ValueError as exc:
        geometry_ok=False
        for id in checks:
            if d[CHECK_GROUPS[id]] is not None:
                _missing(checks[id],str(exc))
    if geometry_ok:
        _nm(checks['axial_flexural_interaction'],model,d['nm_cases'],stage_map,factor)
        _detailing(checks,r,R,rs,d['detailing'])
        _anchorage(checks,r,d['anchorage'],d['detailing'])
        _lap(checks['lap'],r,d['lap'],d['anchorage'],d['detailing'])
        _service(checks,model,d['service'],stage_map)
    # Shear needs concrete geometry and hoops but not longitudinal steel.
    _shear(checks['shear'],model,envelope,d['shear'],d['nm_cases'],stage_map)
    for c in checks.values():
        result['requiredInputs'].extend(c['requiredInputs'])
        if c['status'] not in ('pass','calculated'):result['unverifiedChecks'].append(c['id'])
    result['requiredInputs']=sorted(set(result['requiredInputs']))
    if any(c['status']=='fail' for c in checks.values()):result['status']='engineering_fail'
    return result


def _nm(c,model,cases,stage_map,factor):
    if cases is None:return
    c.update(method='circular_discrete_bars_nm_partial_v1',scope='显式材料曲线、N≥0且中和轴深度≤D的截面承载力分支；不含二阶效应',
             basis=[{'standard':'explicit frozen material laws','clause':'plane sections and force equilibrium','url':None}])
    if not stage_map:_missing(c,'stages');return
    rows=[];covered={x['stage_id'] for x in cases};missing=set(stage_map)-covered
    for s in sorted(missing):c['requiredInputs'].append(f'engineering_design.nm_cases[{s}]')
    for case in cases:
        stage=stage_map.get(case['stage_id'])
        if stage is None:
            c['requiredInputs'].append(f'stages[{case["stage_id"]}]');continue
        if stage.get('status')!='success' or not stage.get('points'):
            c['requiredInputs'].append(f'stages[{case["stage_id"]}].successful_points');continue
        for p in _extrema(stage):
            M=p['momentCharacteristicKnM']*factor
            row={'stageId':case['stage_id'],'depthM':p['depthM'],'side':p.get('side'),
                 'momentCharacteristicKnM':p['momentCharacteristicKnM'],'momentDesignKnM':M,
                 'designEffectFactor':factor,'axialForceDesignKn':case['axial_force_design_kn'],
                 'axialForceBasis':case['axial_force_basis'],'secondOrderAssessment':case['second_order_assessment'],
                 'secondOrderBasis':case['second_order_basis'],'capacityKnM':None,'axialResidualKn':None,
                 'status':'outside_method_scope','utilization':None}
            if case['second_order_assessment']=='not_required_by_project_assessment':
                cap=circular_nm_capacity(model,case['axial_force_design_kn'],sign=1 if M>=0 else -1)
                row.update(cap)
                if cap['capacityKnM'] is not None:
                    row['utilization']=abs(M)/cap['capacityKnM'] if cap['capacityKnM']>0 else None
                    row['status']='pass' if abs(M)<=cap['capacityKnM'] else 'fail'
            rows.append(row)
    c['details']={'cases':rows,'coverageComplete':not c['requiredInputs'],'demandSource':'sealed stage characteristic M × design_effect_factor once'}
    c['status']=_status(rows) if rows else 'missing_input'
    if c['requiredInputs'] and c['status']=='pass':c['status']='missing_input'
    valid=[x for x in rows if x['utilization'] is not None]
    if valid:
        g=max(valid,key=lambda x:x['utilization']);c.update(demand=abs(g['momentDesignKnM']),capacity=g['capacityKnM'],
            utilization=g['utilization'],units='kN·m',governingCase=g)
    c['limitations']=['未评价钢筋断裂应变、延性、全受压应变域、轴拉、稳定或抗震；缺阶段轴力时不能把全路径标为通过。']


def _shear(c,model,envelope,shear,nm_cases,stage_map):
    if shear is None:return
    c.update(method='gb50010_6_3_15_circular_shear_n0_v1',basis=[SOURCES['shear']],
             scope='非抗震、非预应力、无扭转、N=0一般受弯圆截面；有效箍筋面积解释由显式输入给定')
    if shear['axial_force_design_kn']!=0 or shear['seismic_case']!='nonseismic' or shear['loading_case']!='general_bending_nonprestressed':
        c['status']='outside_method_scope';return
    # A contradictory stage axial input invalidates an all-stage N=0 shear declaration.
    if nm_cases and any(x['axial_force_design_kn']!=0 for x in nm_cases):
        c['status']='outside_method_scope';c['limitations']=['阶段轴力非零，与包络抗剪N=0适用声明冲突。'];return
    r=model['reinforcement'];s=r.get('stirrup_spacing_mm');dia=r.get('stirrup_diameter_mm')
    if s is None or dia is None:_missing(c,'reinforcement.stirrup_spacing_mm','reinforcement.stirrup_diameter_mm');return
    R=model['pile']['diameter_m']*500;b=1.76*R;h=1.6*R
    ft=shear['concrete_tensile_design_strength_mpa'];fy=shear['stirrup_design_strength_mpa'];As=shear['effective_hoop_area_mm2']
    maximum_area=2*pi*dia**2/4
    if As>maximum_area*(1+1e-9):
        c.update(status='outside_method_scope',details={'suppliedEffectiveHoopAreaMm2':As,
            'maximumTwoLegAreaMm2':maximum_area,'givenStirrupDiameterMm':dia},
            limitations=['有效箍筋面积超过给定单个圆形闭箍两肢几何面积；不能凭任意面积检算给定箍筋。'])
        return
    vc=.7*ft*b*h/1000;vs=fy*As*h/s/1000
    limit=.25*shear['concrete_strength_coefficient_beta_c']*r['concrete_design_strength_mpa']*b*h/1000
    g=envelope['governing']['shearAbs'];factor=model['analysis']['design_effect_factor'];V=abs(g['valueKn'])*factor
    capacity=min(limit,vc+vs)
    c.update(status='pass' if V<=capacity else 'fail',demand=V,capacity=capacity,utilization=V/capacity,units='kN',
        governingCase={'stageId':g['stageId'],'depthM':g['depthM'],'side':g.get('side'),
                       'signedCharacteristicValue':g['valueKn'],'designEffectFactor':factor},
        details={'equivalentWidthMm':b,'equivalentDepthMm':h,'concreteContributionKn':vc,'stirrupContributionKn':vs,
                 'sectionLimitKn':limit,'providedEffectiveHoopAreaMm2':As,'providedAreaPerLengthMm2PerM':As/s*1000,
                 'strengthRequiredAreaPerLengthMm2PerM':max(0,(V-vc)*1000/(fy*h))*1000,
                 'hoopAreaBasis':shear['hoop_area_basis'],'applicabilityBasis':shear['applicability_basis'],
                 'coefficientBasis':shear['coefficient_basis'],'stirrupSpacingMm':s,'stirrupDiameterMm':dia,
                 'sectionLimitPass':V<=limit,'concreteAndHoopCapacityKn':vc+vs,
                 'axialConditionSource':'stage_axial_cases' if nm_cases else 'independent_explicit_zero_axial_assessment',
                 'axialStageCoverageConfirmed':bool(nm_cases and stage_map and {x['stage_id'] for x in nm_cases}==set(stage_map))})
    c['limitations']=['本项不代表最小配箍率、箍筋闭合/锚固、局部加密区或构造间距通过；须另项核实。']


def _detailing(checks,r,R,rs,d):
    if d is None:return
    diameter=r['bar_diameter_mm'];hoop=r.get('stirrup_diameter_mm');pitch=r.get('stirrup_spacing_mm')
    for name in ('minimum_cover','bar_spacing','stirrup_detailing'):
        checks[name].update(method='exact_cage_geometry_supplied_criteria_v1',scope='几何量与显式项目判据比较；不自动选取暴露环境或灌注工艺规范限值',
            basis=[{'standard':'supplied project criteria','clause':d['criterion_basis'],'url':None}])
    clear=r['bar_axis_cover_mm']-diameter/2
    c=checks['minimum_cover'];c['details']={'barAxisToSurfaceMm':r['bar_axis_cover_mm'],'longitudinalClearCoverMm':clear,'outerHoopClearCoverMm':None}
    if d['hoop_arrangement']!='external_tangent_closed_circular':c['status']='outside_method_scope'
    elif hoop is None:_missing(c,'reinforcement.stirrup_diameter_mm')
    else:
        actual=clear-hoop;c['details']['outerHoopClearCoverMm']=actual
        c.update(status='pass' if actual>=d['minimum_clear_cover_mm'] else 'fail',demand=d['minimum_clear_cover_mm'],capacity=actual,units='mm')
    spacing=2*rs*sin(pi/r['bar_count'])-diameter;c=checks['bar_spacing']
    c.update(status='pass' if spacing>=d['minimum_clear_spacing_mm'] else 'fail',demand=d['minimum_clear_spacing_mm'],capacity=spacing,units='mm',
             details={'barCentroidRadiusMm':rs,'clearChordSpacingMm':spacing,'diameterMm':diameter,'barCount':r['bar_count']})
    c=checks['stirrup_detailing']
    if hoop is None or pitch is None:_missing(c,'reinforcement.stirrup_diameter_mm','reinforcement.stirrup_spacing_mm')
    elif d['hoop_arrangement']!='external_tangent_closed_circular':c['status']='outside_method_scope'
    else:c.update(status='pass' if hoop>=d['minimum_stirrup_diameter_mm'] and pitch<=d['maximum_stirrup_spacing_mm'] else 'fail',
        details={'providedDiameterMm':hoop,'minimumDiameterMm':d['minimum_stirrup_diameter_mm'],'providedSpacingMm':pitch,
                 'maximumSpacingMm':d['maximum_stirrup_spacing_mm'],'hoopArrangement':d['hoop_arrangement']},units='mm')
    c['limitations']=['未检查闭合端头、螺旋箍倾角、焊接、笼体加劲、导管空间或各节点加密区。']


def _anchorage(checks,r,a,d):
    if a is None:return
    c=checks['anchorage'];cc=checks['anchorage_confinement']
    for x in (c,cc):x.update(method='gb50010_straight_deformed_tension_anchorage_v1',basis=[SOURCES['anchorage']],
        scope='非抗震普通带肋钢筋直线受拉锚固；显式节点适用性声明，不代替梁柱节点专门条文')
    if a['bar_surface']!='deformed' or a['seismic_case']!='nonseismic':
        c['status']=cc['status']='outside_method_scope';return
    dia=r['bar_diameter_mm'];ft=a['concrete_tensile_design_strength_mpa_capped_c60']
    basic=.14*r['steel_design_strength_mpa']/ft*dia
    factors={'diameter':1.1 if dia>25 else 1.,'epoxy':1.25 if a['epoxy_coated'] else 1.,'constructionDisturbance':1.1 if a['construction_disturbed'] else 1.}
    correction=float(np.prod(list(factors.values())));required=max(200.,basic*max(.6,correction))
    ends=[{'end':end,'providedLengthMm':a[f'{end}_available_length_mm'],'requiredLengthMm':required,
           'status':'pass' if a[f'{end}_available_length_mm']>=required else 'fail'} for end in ('top','bottom')]
    c['limitations']=['ft为显式锚固连接区混凝土值；top/bottom长度为实际钢筋从相应锚固起算截面起的可用长度，不等同桩长或桩入土深度。', '无超配筋或大保护层折减；不验证钢筋钩头、节点几何和实际粘结质量。']
    c.update(status=_status(ends),demand=required,capacity=min(x['providedLengthMm'] for x in ends),units='mm',
        details={'basicLengthMm':basic,'requiredLengthMm':required,'correctionFactors':factors,'totalCorrectionFactor':correction,
                 'ends':ends,'materialBasis':a['material_basis'],'applicabilityBasis':a['applicability_basis']})
    if d is None:_missing(cc,'engineering_design.detailing.hoop_arrangement');return
    if d['hoop_arrangement']!='external_tangent_closed_circular':cc['status']='outside_method_scope';return
    # Cover of the ANCHORED longitudinal bar, not the outer hoop, controls 5d.
    cover=r['bar_axis_cover_mm']-dia/2
    required_zone=cover<=5*dia
    min_d=dia/4;max_s=min(5*dia,100.)
    cc.update(status='pass' if not required_zone or (a['transverse_diameter_mm']>=min_d and a['transverse_spacing_mm']<=max_s) else 'fail',
        units='mm',details={'longitudinalClearCoverMm':cover,'confinementRequiredBy5dCondition':required_zone,
            'minimumDiameterMm':min_d if required_zone else None,'maximumSpacingMm':max_s if required_zone else None,
            'providedDiameterMm':a['transverse_diameter_mm'],'providedSpacingMm':a['transverse_spacing_mm']})
    cc['limitations']=['此处横向钢筋是显式锚固区输入，不把全长箍筋间距自动当局部实际配筋。']


def _lap(c,r,lap,a,d):
    if lap is None:return
    c.update(method='explicit_lap_arrangement_review_v1',basis=[SOURCES['lap']],scope='显式连接方式记录；未获验证的连接细节不判通过')
    c['details']={'arrangement':lap['arrangement'],'arrangementBasis':lap['arrangement_basis']}
    if lap['arrangement']=='continuous_no_lap':
        c['status']='pass';c['limitations']=['仅核对显式无接头布置声明；不校验运输、分节、焊接或实际施工一致性。']
    elif lap['arrangement']=='tension_tied_lap' and lap['seismic_case']=='nonseismic':
        c['method']='gb50010_tension_lap_length_partial_v1'
        required_fields=('available_length_mm','joint_area_ratio','transverse_diameter_mm','transverse_spacing_mm')
        missing=[f'engineering_design.lap.{f}' for f in required_fields if lap[f] is None]
        if a is None:missing.append('engineering_design.anchorage')
        if d is None:missing.append('engineering_design.detailing.hoop_arrangement')
        if missing:_missing(c,*missing);return
        if a['bar_surface']!='deformed' or a['seismic_case']!='nonseismic' or d['hoop_arrangement']!='external_tangent_closed_circular':
            c['status']='outside_method_scope';return
        dia=r['bar_diameter_mm'];ratio=lap['joint_area_ratio']
        factor=1.2 if ratio<=.25 else (1.2+(ratio-.25)*.8 if ratio<=.5 else 1.4+(ratio-.5)*.4)
        anchor=.14*r['steel_design_strength_mpa']/a['concrete_tensile_design_strength_mpa_capped_c60']*dia
        anchor*= (1.1 if dia>25 else 1.)*(1.25 if a['epoxy_coated'] else 1.)*(1.1 if a['construction_disturbed'] else 1.)
        anchor=max(200.,anchor);required=max(300.,factor*anchor)
        length_ok=lap['available_length_mm']>=required
        conf_ok=lap['transverse_diameter_mm']>=dia/4 and lap['transverse_spacing_mm']<=min(5*dia,100.)
        c.update(status='outside_method_scope' if length_ok and conf_ok else 'fail',demand=required,
            capacity=lap['available_length_mm'],units='mm')
        c['details'].update(jointAreaRatio=ratio,lapLengthFactor=factor,requiredLengthMm=required,
            providedLengthMm=lap['available_length_mm'],connectionZoneLengthMm=1.3*lap['available_length_mm'],
            lengthStatus='pass' if length_ok else 'fail',confinementStatus='pass' if conf_ok else 'fail',
            minimumTransverseDiameterMm=dia/4,maximumTransverseSpacingMm=min(5*dia,100.),
            providedTransverseDiameterMm=lap['transverse_diameter_mm'],providedTransverseSpacingMm=lap['transverse_spacing_mm'])
        c['limitations']=['已算8.4.4长度及8.4.6局部横筋；仍未验证接头纵向位置、同区段百分率、构件类别上限或轴拉/小偏心受拉禁用条件。',
            '搭接采用显式锚固组的同一接头混凝土与修正条件；若搭接位于不同混凝土或不同扰动条件处，本项不适用。']
    else:
        c['status']='outside_method_scope'
        c['limitations']=['搭接百分率、连接区段、节点位置及连接型式尚需专门检查，未用通用长度代替完整搭接设计。']


def _service(checks,model,s,stage_map):
    if s is None:return
    c=checks['service_stress'];crack=checks['crack_width']
    for x in (c,crack):x.update(basis=[SOURCES['crack']])
    c.update(method='circular_cracked_elastic_stress_nm_v1',scope='圆形开裂弹性截面同工况N-M应力分析；不评价裂缝宽度或长期应力重分布')
    crack.update(method='circular_crack_width_not_substantiated',status='outside_method_scope',
        scope='GB50010 7.1.2普通矩形/T/I公式不直接认定适用于周边均匀配筋圆桩',
        details={'declaredLimitMm':s['crack_width_limit_mm'],'limitBasis':s['limit_basis']},
        limitations=['缺少经过核验的适用圆桩裂缝模型；开裂钢筋应力不能代替裂缝宽度结果。'])
    if not stage_map:_missing(c,'stages');return
    rows=[]
    required_stage_ids=list(stage_map)
    provided_stage_ids={case['stage_id'] for case in s['cases']}
    missing_stage_ids=[stage_id for stage_id in required_stage_ids if stage_id not in provided_stage_ids]
    for stage_id in missing_stage_ids:
        c['requiredInputs'].append(f'engineering_design.service.cases[{stage_id}]')
    for case in s['cases']:
        stage=stage_map.get(case['stage_id'])
        if stage is None or stage.get('status')!='success' or not stage.get('points'):
            c['requiredInputs'].append(f'stages[{case["stage_id"]}].successful_points');continue
        for p in _extrema(stage):
            M=p['momentCharacteristicKnM']*case['characteristic_effect_scale']
            row={'stageId':case['stage_id'],'depthM':p['depthM'],'side':p.get('side'),'momentServiceKnM':M,
                'momentCharacteristicKnM':p['momentCharacteristicKnM'],'characteristicEffectScale':case['characteristic_effect_scale'],
                'axialForceServiceKn':case['axial_force_service_kn'],'combinationBasis':case['combination_basis'],
                'maximumTensileSteelStressMpa':None,'status':'outside_method_scope'}
            # The pair N-M belongs to the current SLS case, stage and section.
            row.update(circular_service_nm_stress(model,case['axial_force_service_kn'],
                M,s['concrete_elastic_modulus_mpa']))
            if row['status']=='calculated' and row['maximumTensileSteelStressMpa']>=model['reinforcement']['steel_design_strength_mpa']:
                row['status']='outside_method_scope'
                row['limitation']='弹性钢筋应力达到设计强度，服务阶段线弹性假定超出适用范围。'
            rows.append(row)
    c['details']={'cases':rows,'longTermBasis':s['long_term_basis'],'concreteElasticModulusMpa':s['concrete_elastic_modulus_mpa'],
                  'requiredStageIds':required_stage_ids,'missingStageIds':missing_stage_ids,
                  'coverageComplete':not c['requiredInputs']}
    c['status']='missing_input' if c['requiredInputs'] or not rows else ('outside_method_scope' if any(x['status']=='outside_method_scope' for x in rows) else 'calculated')
    c['limitations']=['应力采用显式SLS比例×该阶段特征弯矩；不自动使用ULS系数或从设计值倒推SLS。']
