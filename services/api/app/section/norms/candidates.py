"""Explicit optional parameter suggestions, never automatic engineering inputs.

A material standard property is only usable when the material grade is
confirmed. Geotechnical case statistics are not transferred across projects.
"""
from __future__ import annotations

CANDIDATES = [
    {'parameter':'elastic_modulus','label':'C30混凝土弹性模量参考值', 'value':30000, 'unit':'MPa',
     'materialGrade':'C30', 'sourceClass':'standard_table',
     'source':'GB/T50010-2010(2024) 4.1.5 表4.1.5',
     'applicability':'确认采用C30混凝土、非特殊骨料且材料标准表适用；桩的EI折减需另定',
     'approval':'candidate_only'},
    {'parameter':'elastic_modulus','label':'C35混凝土弹性模量参考值', 'value':31500, 'unit':'MPa',
     'materialGrade':'C35', 'sourceClass':'standard_table',
     'source':'GB/T50010-2010(2024) 4.1.5 表4.1.5',
     'applicability':'材料等级已确认且试验优先；不能代替设计刚度折减',
     'approval':'candidate_only'},
    {'parameter':'steel_design_strength','label':'HRB400钢筋抗拉强度设计值参考', 'value':360, 'unit':'MPa',
     'materialGrade':'HRB400', 'sourceClass':'standard_table',
     'source':'GB/T50010-2010(2024) 第4.2节；使用前核对实际强度/钢筋类别及修订表',
     'applicability':'钢筋实际牌号为HRB400，相关构件与应力状态允许此取值',
     'approval':'candidate_only'},
    {'parameter':'water_unit_weight','label':'淡水容重计算常数', 'value':9.81, 'unit':'kN/m3',
     'sourceClass':'physics_reference', 'source':'常规工程重力计算参考值 ρ≈1000kg/m³、g≈9.81m/s²',
     'applicability':'淡水与常规重力场；特殊盐度/温度按项目修正', 'approval':'candidate_only'},
    {'parameter':'k0','label':'砂土Jaky公式估计静止侧压力系数', 'formula':'K0≈1−sin(φʹ)',
     'unit':'dimensionless','sourceClass':'empirical_model',
     'source':'Jaky经验关系，非无条件规范指定值',
     'applicability':'正常固结、排水砂土的初步选项；超固结土等不适用',
     'approval':'candidate_only'},
    {'parameter':'soil_gamma','label':'2020年螺屿村参考工程填土重度', 'value':18.0, 'unit':'kN/m3',
     'sourceClass':'historical_project', 'source':'上传螺屿村支护计算书 PDF 物理第9页土层参数表，填土经验值',
     'applicability':'仅用于该历史资料整理和示例对照；其他工程须勘察或确认',
     'approval':'candidate_only'},
    {'parameter':'m','label':'2020年螺屿村1-1剖面填土m参考', 'value':4.0, 'unit':'MPa/m2',
     'sourceClass':'historical_project','source':'上传螺屿村计算书 PDF 物理第22页，1-1剖面填土m',
     'applicability':'该剖面历史取值；不可替代新项目水平荷载试验和地区经验',
     'approval':'candidate_only'},
]
REQUIRED_PROJECT = [
    {'parameter':'location_and_design_date','why':'地区标准和版本适用性','source':'项目文件'},
    {'parameter':'soil_strata_c_phi_gamma','why':'土层分布与强度/重度直接决定压力与稳定','source':'勘察/试验'},
    {'parameter':'groundwater_layers','why':'潜水、承压水和渗流条件会改变水压力及失稳模式','source':'水文勘察/观测'},
    {'parameter':'pile_geometry_and_grade','why':'控制EI、作用宽度和构件抗力','source':'设计人确认'},
    {'parameter':'soil_m_and_basis','why':'JGJ120 4.1.5-4.1.6 的m需注明依据','source':'水平载荷试验/地区经验/规范经验公式'},
    {'parameter':'support_stiffness_and_stage','why':'决定支点力、安装位移及控制阶段','source':'支撑结构分析/设计人'},
    {'parameter':'design_load_combinations','why':'承载能力与正常使用极限状态需分别组合','source':'项目设计基准'},
    {'parameter':'allowable_deformations','why':'位移与沉降限值随保护对象确定','source':'环境调查及设计控制'},
]


def candidate_catalog() -> dict:
    return {'schemaVersion':'pitguard.norm-candidates/1','candidates':[dict(x) for x in CANDIDATES],
            'requiredProjectInformation':[dict(x) for x in REQUIRED_PROJECT],
            'policy':'候选只供选择、筛查及人工确认；历史经验不自动输入；没有明确证据的设计值一律保留缺项'}
