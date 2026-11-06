"""Dimension-safe engineering units. Canonical engineering display: m, kPa,
 kN, kN/m, kN·m, kN/m³, kN/m⁴, degrees and mm (reinforcement).
"""
from __future__ import annotations

from math import isfinite

# Unit -> (physical dimension, factor to canonical unit); input alternatives
# are intentionally restricted to approved explicit conversions.
UNITS = {
    'm': ('length', 1), 'mm': ('length', .001), 'cm': ('length', .01),
    'kPa': ('stress', 1), 'MPa': ('stress', 1000), 'GPa': ('stress', 1_000_000),
    'kN': ('force', 1), 'N': ('force', .001),
    'kN/m': ('line_force', 1), 'N/mm': ('line_force', 1),
    'kN.m': ('moment', 1), 'kN*m': ('moment', 1), 'N.mm': ('moment', 1e-6),
    'kN/m3': ('unit_weight', 1), 'N/mm3': ('unit_weight', 1e6),
    'kN/m4': ('m_coefficient', 1), 'MPa/m2': ('m_coefficient', 1000),
    'kN/m3_per_m': ('m_coefficient', 1),
    'kN/m3_reaction': ('kh_coefficient', 1), 'MPa/m': ('kh_coefficient', 1000),
    'kN/m2_spring': ('support_stiffness_line', 1), 'MN/m2': ('support_stiffness_line', 1000),
    'kN/m_spring': ('support_stiffness_single', 1), 'MN/m': ('support_stiffness_single', 1000),
    'deg': ('angle', 1), 'degree': ('angle', 1),
    'rad': ('angle_radians', 1),
    'dimensionless': ('ratio', 1), '%': ('ratio', .01),
}
CANONICAL = {
    'length': 'm', 'stress': 'kPa', 'force': 'kN', 'line_force':'kN/m', 'moment':'kN.m',
    'unit_weight': 'kN/m3', 'm_coefficient':'kN/m4', 'kh_coefficient':'kN/m3_reaction',
    'support_stiffness_line':'kN/m2_spring', 'support_stiffness_single':'kN/m_spring',
    'angle':'deg', 'angle_radians':'rad', 'ratio':'dimensionless',
}


def convert(value: float, unit: str, expected_dimension: str) -> dict:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError('参数值必须为有限数值')
    spec = UNITS.get(unit)
    if spec is None:
        raise ValueError(f'未知单位 {unit}；请明确选择单位，禁止默默猜测')
    dimension, multiplier = spec
    if dimension != expected_dimension:
        raise ValueError(f'物理量维度不匹配：{unit}属于{dimension}，本字段要求{expected_dimension}')
    normalized = value * multiplier
    if not isfinite(normalized):
        raise ValueError('单位换算后数值溢出')
    return {'value': normalized, 'unit': CANONICAL[dimension], 'dimension': dimension,
            'original': {'value': value, 'unit': unit}, 'conversionFactor': multiplier}


# Field -> dimension, reviewed use of parameter
FIELDS = {
    'sigma_total': ('stress', '岩土竖向总应力'),
    'pore_pressure': ('stress', '当前点孔隙水压力'),
    'cohesion': ('stress', '与水土分合算方式一致的c/cʹ'),
    'phi': ('angle', '与水土分合算方式一致的内摩擦角'),
    'vb': ('length', '坑底水平位移vb：小于等于10mm时规范允许取10mm，不得假定实际位移'),
    'm': ('m_coefficient', '水平反力系数比例m，需现场试验/地区经验/审核公式来源'),
    'depth': ('length', '从统一地面基准向下的深度z'),
    'excavation_depth': ('length', '同一基准的坑底深度h'),
    'displacement': ('length', '压缩坑内土体方向为正的局部位移v'),
    'pile_width': ('length', '圆桩直径或矩形桩宽度'),
    'pile_spacing': ('length', '排桩中心距'),
    'gamma0': ('ratio', '支护结构重要性系数'),
    'design_effect': ('force', '与抗力同单位的基本组合效应'),
    'design_resistance': ('force', '独立按材料/构件方法计算的设计抗力'),
    'service_effect': ('length', '标准组合的位移/沉降效应'),
    'service_limit': ('length', '工程批准的位移/沉降限值'),
}
