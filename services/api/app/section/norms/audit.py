"""Legacy frozen section preflight against the selected normative family.

This is a read-only compatibility audit; it NEVER rewrites immutable snapshots.
"""
from __future__ import annotations
from math import pi
from app.section.models import SectionInput


def inspect_frozen_section(value: SectionInput) -> dict:
    from .engine import _pile_width
    data=value.model_dump(mode='json')
    b0=_pile_width({'pile_width':value.pile.diameter_m,'pile_spacing':value.pile.spacing_m,
                    'pile_section':'circular'})
    problems=[]
    if abs(value.soil_effective_width_m-b0['b0M'])>1e-9:
        problems.append({'code':'WIDTH_NOT_JGJ120_4_1_7', 'severity':'blocking',
                         'detail':f"已冻结土宽 {value.soil_effective_width_m}m，按圆桩/间距规范计算 b0={b0['b0M']:.6g}m"})
    if value.analysis.soil_method!='jgj120_2012_elastic_support':
        problems.append({'code':'EXISTING_SOIL_METHOD_NOT_NORMATIVE_ADAPTER', 'severity':'blocking',
                         'detail':'当前双侧弹塑性土弹簧包含独立土体历史，不能等同于JGJ120-2012第4.1.3~4.1.5条单侧坑内反力模型'})
    if value.analysis.design_effect_factor!=1.0:
        problems.append({'code':'GLOBAL_MULTIPLIER_NOT_DESIGN_COMBINATION', 'severity':'blocking',
                         'detail':'将内力统一乘系数不能代替按作用基本组合与标准组合分别求解'})
    if any(x.id not in {r.parameter for r in value.evidence} for x in value.soil_layers):
        problems.append({'code':'SOIL_PARAMETER_PROVENANCE_INSUFFICIENT','severity':'review',
                         'detail':'当前证据数组没有逐层强度/重度/m的源字段签审，不得自动推断为已确认'})
    if value.purpose=='verification_fixture':
        problems.append({'code':'SYNTHETIC_FIXTURE','severity':'blocking',
                         'detail':'合成算例仅可验证计算代码，不可认定为真实工程'})
    if value.pile.toe_boundary=='fixed':
        problems.append({'code':'TOE_FIXED_CONDITION_REQUIRE_CONFIRMATION','severity':'review',
                         'detail':'桩端固定需有可验证的嵌入坚硬地层条件或边界模型依据'})
    if value.analysis.pressure_method not in ('effective_rankine_bounds_k0_v1','explicit_rankine_stress_modes_v2'):
        problems.append({'code':'UNEXPECTED_PRESSURE_METHOD','severity':'blocking',
                         'detail':'压力方法未在当前兼容性审查中登记'})
    return {'schemaVersion':'pitguard.norm-section-audit/1','sectionId':value.section_id,
            'readOnly':True,'standardBasis':'GB55003-2021 + JGJ120-2012',
            'existingSoilMethod':value.analysis.soil_method,'normativeReactionWidth':b0,
            'existingReactionWidthM':value.soil_effective_width_m,
            'currentEiKnM2':value.pile.elastic_modulus_kpa*pi*value.pile.diameter_m**4/64*value.pile.inertia_factor,
            'issues':problems,'safeToClaimFullJGJ120Compliance':False,
            'recommendation':'继续使用旧路径验证历史数据；正式结构分析另实施4.1.3~4.1.10单侧土反力模型并逐项对照独立算例。'}
