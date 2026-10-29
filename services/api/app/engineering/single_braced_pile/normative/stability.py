"""Versioned JGJ 120-2012 4.2.2, 4.2.4 and 4.2.7 calculations.

All moment integrals are characteristic-load / per metre of wall. No structural
load factor or per-pile width is applied to an earth-stability safety ratio.
This module intentionally refuses to guess soil constitutive strength types.
"""
from __future__ import annotations
from math import pi,exp,tan,radians, isfinite
from . import __name__ as _module
from ..pressure import pressure_state, vertical_effective

_NODES=(-.8611363115940526,-.3399810435848563,.3399810435848563,.8611363115940526)
_WEIGHTS=(.3478548451374538,.6521451548625461,.6521451548625461,.3478548451374538)


def _result(code, clause, status, **kwargs):
    return {'code':code,'standard':'JGJ 120-2012','clause':clause,'status':status,**kwargs}


def _layer(layers,z):
    return next((l for l in layers if l.top_m<=z<l.bottom_m),layers[-1])


def _integrate(ends,func):
    value=0.
    for a,b in zip(ends,ends[1:]):
        if b<=a:continue
        mid=(a+b)/2; half=(b-a)/2
        for x,w in zip(_NODES,_WEIGHTS):value+=w*half*func(mid+half*x)
    return value


def _breakpoints(model,stage,end):
    candidates=[0.,end,stage.excavation_depth_m,model.strut.depth_m,
                stage.water_inside_depth_m,stage.water_outside_depth_m]
    candidates.extend(v for layer in model.layers for v in (layer.top_m,layer.bottom_m))
    return sorted({v for v in candidates if 0<=v<=end})


def embedment_single_strut(model,stage):
    """JGJ 120 4.2.2: E_pk * z_p2 / (E_ak * z_a2).

    Signed moments about the strut preserve above-strut active pressure direction.
    Water is counted once in the separated-stress case. Ground surface is flat.
    """
    h=stage.excavation_depth_m;toe=model.pile.length_m;s=model.strut.depth_m
    if h<=s or stage.event=='initial':
        return _result('embedment','4.2.2','not_applicable',stageId=stage.id)
    if not model.safety_grade:
        return _result('embedment','4.2.2','missing_input',stageId=stage.id,requiredInputs=['safety_grade'])
    if any(l.soil_group not in ('sand','gravel','silt','clay') for l in model.layers if l.top_m<toe):
        return _result('embedment','4.2.2','outside_method_scope',stageId=stage.id,
                       reason='土压力计算域包含未声明土类或岩层；需要专项土压力方法')
    bounds=_breakpoints(model,stage,toe)
    def pa(z):
        lay=_layer(model.layers,z)
        sig=vertical_effective(model.layers,z,stage.water_outside_depth_m,0.,model.water_unit_weight_kn_m3)
        p=pressure_state(lay,sig+stage.surcharge_kpa)['active']
        return p + model.water_unit_weight_kn_m3*max(0,z-stage.water_outside_depth_m)
    def pp(z):
        if z<=h:return 0.
        lay=_layer(model.layers,z)
        sig=vertical_effective(model.layers,z,stage.water_inside_depth_m,h,model.water_unit_weight_kn_m3)
        return pressure_state(lay,sig)['passive']+model.water_unit_weight_kn_m3*max(0,z-stage.water_inside_depth_m)
    ea=_integrate(bounds,pa);ep=_integrate(bounds,pp)
    # JGJ 4.2.2 uses resultant moment arms to the support: signed accounting
    # detects when above-support pressures change the resultant location.
    ma=_integrate(bounds,lambda z:pa(z)*(z-s))
    mp=_integrate(bounds,lambda z:pp(z)*(z-s))
    if ma<=0 or mp<=0:return _result('embedment','4.2.2','outside_method_scope',stageId=stage.id,
      reason='支点附近土压力合力矩不满足单向踢脚机制，须独立建立稳定模型', activeMomentKnMPerM=ma,passiveMomentKnMPerM=mp)
    factor=mp/ma;required={1:1.25,2:1.20,3:1.15}[model.safety_grade]
    return _result('embedment','4.2.2','pass' if factor>=required else 'fail',stageId=stage.id,
       requiredFactor=required,safetyFactor=factor,activeResultantKnPerM=ea,passiveResultantKnPerM=ep,
       activeMomentKnMPerM=ma,passiveMomentKnMPerM=mp,
       activeLeverArmM=ma/ea if ea>0 else None,passiveLeverArmM=mp/ep if ep>0 else None,
       pressureBasis='characteristic_Rankine_effective_plus_water_once',
       limitations=['水平地面、均布荷载、平面朗肯土压力、无土拱与附加侧压力',
                    '黏性土张裂缝积水、渗流及地方规范的附加要求应单独验证'])


def _heave_unit_weight(l,depth,water,water_gamma):
    # JGJ 4.2.4: submerged unit weight in granular/silty soil below water;
    # clay uses natural unit weight with appropriate total-stress strength.
    if l.soil_group in ('sand','silt','gravel'):
        return l.gamma_sat_kn_m3-water_gamma if depth>=water else l.gamma_natural_kn_m3
    if l.soil_group=='clay':return l.gamma_natural_kn_m3
    raise ValueError('soil type not assessed')


def _average_gamma(model,start,end,water):
    if end<=start:return None
    breaks=sorted({start,end,*[x for l in model.layers for x in (l.top_m,l.bottom_m)],water})
    breaks=[v for v in breaks if start<=v<=end]
    total=_integrate(breaks,lambda z:_heave_unit_weight(_layer(model.layers,z),z,water,model.water_unit_weight_kn_m3))
    return total/(end-start)


def _heave_strength(layer):
    if layer.soil_group in ('sand','silt','gravel') and layer.heave_strength_basis=='effective':
        return layer.heave_c_kpa if layer.heave_c_kpa is not None else layer.c_effective_kpa,\
               layer.heave_phi_deg if layer.heave_phi_deg is not None else layer.phi_effective_deg
    if layer.soil_group=='clay' and layer.heave_strength_basis=='total':
        if layer.heave_c_kpa is not None and layer.heave_phi_deg is not None:
            return layer.heave_c_kpa,layer.heave_phi_deg
    return None


def heave_prandtl(model,stage):
    """JGJ 120 4.2.4-1: K=(gamma_m2*D*Nq+c*Nc)/(gamma_m1*(h+D)+q)."""
    h=stage.excavation_depth_m;toe=model.pile.length_m
    if h<=0:return _result('basal_heave','4.2.4','not_applicable',stageId=stage.id)
    if not model.safety_grade:return _result('basal_heave','4.2.4','missing_input',stageId=stage.id,requiredInputs=['safety_grade'])
    if not all(l.soil_group in ('sand','silt','gravel','clay') for l in model.layers if l.top_m<toe+1e-6):
        return _result('basal_heave','4.2.4','outside_method_scope',stageId=stage.id,
                       reason='未说明土类或存在岩层，Prandtl 土体模型不适用')
    # Mandatory weak underlying layers: evaluate every below-toe layer boundary
    # that has a measured soil-strength basis; unknown strength blocks closure.
    candidates=[toe,*[l.top_m for l in model.layers if l.top_m>toe+1e-8]]
    if model.layers[-1].bottom_m<=toe+1e-7:
        return _result('basal_heave','4.2.4','missing_input',stageId=stage.id,
                       requiredInputs=['layers_below_pile_toe'])
    rows=[]
    for depth in candidates:
        layer=_layer(model.layers,min(depth+1e-6,model.layers[-1].bottom_m-1e-6))
        strength=_heave_strength(layer)
        if not strength:
            return _result('basal_heave','4.2.4','missing_input',stageId=stage.id,
                     requiredInputs=[f'layers[{layer.id}].soil_group/heave_strength_basis/heave_c_kpa/heave_phi_deg'],
                     checkedLevels=rows)
        try:
            g1=_average_gamma(model,0,depth,stage.water_outside_depth_m)
            g2=_average_gamma(model,h,depth,stage.water_inside_depth_m)
        except ValueError:
            return _result('basal_heave','4.2.4','outside_method_scope',stageId=stage.id,reason='土类与重度计算口径不匹配')
        c,phi=strength;tanp=tan(radians(phi));nq=exp(pi*tanp)*tan(pi/4+radians(phi)/2)**2
        nc=(nq-1)/tanp if abs(tanp)>1e-10 else pi+2
        D=depth-h
        denominator=g1*depth+stage.surcharge_kpa
        if denominator<=0:
            return _result('basal_heave','4.2.4','outside_method_scope',stageId=stage.id,reason='分母非正，需独立建模')
        factor=(g2*D*nq+c*nc)/denominator
        rows.append({'controlDepthM':depth,'layerId':layer.id,'cKpa':c,'phiDeg':phi,'gammaOutsideKnM3':g1,
                     'gammaInsideKnM3':g2,'embeddedDepthM':D,'Nq':nq,'Nc':nc,'factor':factor,
                     'strengthBasis':layer.heave_strength_basis})
    governing=min(rows,key=lambda x:x['factor'])
    req={1:1.8,2:1.6,3:1.4}[model.safety_grade]
    return _result('basal_heave','4.2.4','pass' if governing['factor']>=req else 'fail',stageId=stage.id,
       requiredFactor=req,safetyFactor=governing['factor'],governing=governing,checkedLevels=rows,
       limitations=['软弱下卧层按各层顶标高另算','4.2.5 软土绕支点圆弧隆起需要独立检查','不适用于岩石、复杂地形及未经核准的总应力参数'])


def minimal_toe_depth(model,stage):
    h=stage.excavation_depth_m
    if not model.safety_grade:return _result('minimum_embedment','4.2.7','missing_input',requiredInputs=['safety_grade'])
    if h<=0:return _result('minimum_embedment','4.2.7','not_applicable',stageId=stage.id)
    actual=model.pile.length_m-h;minimum=.3*h
    return _result('minimum_embedment','4.2.7','pass' if actual>=minimum else 'fail',stageId=stage.id,
            actualM=actual,recommendedMinimumM=minimum,ratio=actual/minimum,
            classification='not_less_than_recommended; not a replacement for 4.2.2-4.2.6')


def soft_soil_additional(model,stage):
    """4.2.5: pass through the independently tested circular-slice implementation."""
    from .soft_soil_slip import circular_slice_check
    return circular_slice_check(model,stage)
