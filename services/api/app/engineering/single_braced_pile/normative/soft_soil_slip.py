"""JGJ 120-2012 4.2.5 supported circular-slice mechanism.

The circle is centred on the lowest installed support and passes through the
retaining member toe.  Per metre of wall, an outside-soil slice of width dx
has a vertical column from the support datum z_s to the circular slip surface;
q_s is the overburden above z_s plus surface surcharge.  The strength on the
circular slip surface is selected from the soil layer at that depth.

This model is a strip-method equilibrium implementation. Water/total-stress
consistency and excavation-side geometry must be checked before it can be used
as a normative decision. Never silently substitute effective for undrained
strength or replace a layer with an engineering guess.
"""
from __future__ import annotations
from math import asin, atan2, cos, pi, sin, sqrt, tan, radians, isfinite


def _layer(layers, z):
    return next((l for l in layers if l.top_m <= z < l.bottom_m), layers[-1])


def _natural_overburden(layers, bottom):
    return sum(max(0., min(l.bottom_m, bottom)-l.top_m)*l.gamma_natural_kn_m3
               for l in layers if l.top_m < bottom)


def _slice_weight(layers, start, end):
    return sum(max(0., min(l.bottom_m, end)-max(start, l.top_m))*l.gamma_natural_kn_m3
               for l in layers if l.bottom_m > start and l.top_m < end)


def circular_slice_check(model, stage, *, n_slices=512):
    from .stability import _result
    h = stage.excavation_depth_m
    if h <= 0:
        return _result('soft_soil_heave', '4.2.5', 'not_applicable', stageId=stage.id)
    if stage.event == 'initial':
        return _result('soft_soil_heave', '4.2.5', 'not_applicable', stageId=stage.id)
    toe = model.pile.length_m
    support = model.strut.depth_m
    radius = toe - support
    if support >= h or radius <= 0 or n_slices < 32 or n_slices > 4096:
        return _result('soft_soil_heave', '4.2.5', 'outside_method_scope', stageId=stage.id,
                       reason='需已安装的最低支点及其下部嵌固段，且土条数须在规定范围')
    if not model.safety_grade:
        return _result('soft_soil_heave', '4.2.5', 'missing_input', stageId=stage.id,
                       requiredInputs=['safety_grade'])
    active_layers = [l for l in model.layers if l.bottom_m > h and l.top_m < toe]
    if any(l.soil_group is None or (l.soil_group == 'clay' and l.consistency in (None, 'unknown'))
           for l in active_layers):
        return _result('soft_soil_heave', '4.2.5', 'missing_input', stageId=stage.id,
                       requiredInputs=['soil_group_and_clay_consistency_below_base'])
    if not any(l.soil_group == 'clay' and l.consistency == 'soft' for l in active_layers):
        deeper_soft=[l.id for l in model.layers if l.top_m >= toe-1e-8 and
                     l.soil_group=='clay' and l.consistency=='soft']
        if deeper_soft:
            return _result('soft_soil_heave','4.2.5','outside_method_scope',stageId=stage.id,
                affectedLayers=deeper_soft,
                reason='墙趾以下存在软土；现有固定墙趾圆弧未覆盖此弱层，需追加更深软层隆起及整体稳定专项审查')
        return _result('soft_soil_heave', '4.2.5', 'not_applicable', stageId=stage.id,
                       reason='已核定开挖面以下至墙趾范围无软土且未见更深软层')
    if any(l.soil_group == 'rock' for l in active_layers):
        return _result('soft_soil_heave', '4.2.5', 'outside_method_scope', stageId=stage.id,
                       reason='圆弧穿过岩层，需岩体强度与结构面专门方法')
    if model.layers[-1].bottom_m < toe - 1e-8:
        return _result('soft_soil_heave', '4.2.5', 'missing_input', stageId=stage.id,
                       requiredInputs=['layers_down_to_pile_toe'])
    if stage.water_outside_depth_m < toe and any(l.heave_strength_basis != 'total' for l in active_layers):
        return _result('soft_soil_heave', '4.2.5', 'outside_method_scope', stageId=stage.id,
                       reason='含水软土土条法需要统一强度与孔压口径；现有有效/总应力混合输入不可直接采用天然重度公式')
    all_arc_layers = [l for l in model.layers if l.bottom_m > support and l.top_m < toe]
    missing = [l.id for l in all_arc_layers if l.heave_c_kpa is None or l.heave_phi_deg is None
               or l.heave_strength_basis is None]
    if missing:
        return _result('soft_soil_heave', '4.2.5', 'missing_input', stageId=stage.id,
                       requiredInputs=[f'layer[{i}].heave_c_kpa/heave_phi_deg/heave_strength_basis' for i in missing])
    if any(l.heave_strength_basis not in ('total', 'effective') for l in all_arc_layers):
        raise ValueError('Unexpected strength-basis value')
    if any(l.heave_strength_basis == 'effective' for l in all_arc_layers) and stage.water_outside_depth_m < toe:
        return _result('soft_soil_heave', '4.2.5', 'outside_method_scope', stageId=stage.id,
                       reason='未完成孔压沿滑弧有效应力项，含水层不得以天然重度直接乘有效摩擦角')
    if not isfinite(stage.surcharge_kpa) or stage.surcharge_kpa < 0:
        raise ValueError('Surface surcharge must be finite and nonnegative')

    # Deterministic midpoint strips; use exact circular arc length for cohesion
    # so that near-horizontal tangent at x=R cannot blow up l=b/cos(theta).
    q = stage.surcharge_kpa + _natural_overburden(model.layers, support)
    slices = []
    resisting, driving = 0., 0.
    for i in range(n_slices):
        xa = radius*i/n_slices
        xb = radius*(i+1)/n_slices
        xm = (xa+xb)/2
        dx = xb-xa
        arc = support + sqrt(max(0., radius*radius-xm*xm))
        lay = _layer(model.layers, min(arc, toe-1e-9))
        theta = asin(xm/radius)
        W = dx*(q + _slice_weight(model.layers, support, arc))
        lj = radius*(asin(xb/radius)-asin(xa/radius))
        shear = lay.heave_c_kpa*lj + W*cos(theta)*tan(radians(lay.heave_phi_deg))
        drive = W*sin(theta)
        resisting += shear
        driving += drive
        if not all(isfinite(x) for x in (shear, drive, lj, W)):
            raise ValueError('Non-finite circular slip slice')
        if i % max(1, n_slices//16) == 0:
            slices.append({'slice':i, 'xM':xm, 'slipDepthM':arc, 'layerId':lay.id,
                           'weightAndOverburdenKnPerM':W, 'thetaDeg':theta*180/pi})
    if driving <= 1e-12:
        return _result('soft_soil_heave','4.2.5','outside_method_scope',stageId=stage.id,
                       reason='土条滑动力非正；不应用无穷安全系数替代稳定检查')
    req = {1:2.2, 2:1.9, 3:1.7}[model.safety_grade]
    K = resisting/driving
    return _result('soft_soil_heave','4.2.5', 'pass' if K>=req else 'fail',
                   stageId=stage.id, safetyFactor=K, requiredFactor=req,
                   resistingForcePerM=resisting, drivingForcePerM=driving,
                   radiusM=radius, circleCenterDepthM=support, sliceCount=n_slices,
                   integration='midpoint_weights_exact_cohesive_arc',
                   stressBasis='natural_weight_with_explicit_shear_strength',
                   sampleSlices=slices,
                   limitations=['滑弧以最下道支点为圆心并通过墙趾；适用于给定平地面坑外土条几何',
                      '分层圆弧强度在每条滑弧段所属土层取值；需另核对软土触发深度和水力条件',
                      '此法不提供变形、支点反力、渗流或滑移面之外的稳定判断'])
