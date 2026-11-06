"""Strict geometric, geological, stage and approval inputs; SI-derived canonical engineering units."""
from __future__ import annotations
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

class Source(Strict):
    path: str = Field(min_length=3)
    origin: Literal['geotechnical_report','project_design','survey','standard_table','calculation','example']
    reference: str = Field(min_length=4)
    state: Literal['confirmed','candidate','rejected']
    approved_by: str | None = None
    @model_validator(mode='after')
    def approval(self):
        if self.state == 'confirmed' and not self.approved_by:
            raise ValueError('确认参数必须有工程师或审核人员身份')
        if self.origin == 'example' and self.state == 'confirmed':
            raise ValueError('示例参数不能声明为项目勘察事实')
        return self

class Pile(Strict):
    diameter_m: float = Field(gt=.15, le=2.5)
    spacing_m: float = Field(gt=.15, le=5)
    length_m: float = Field(gt=2, le=50)
    elastic_modulus_kpa: float = Field(gt=1e6, le=1e9)
    inertia_factor: float = Field(gt=0, le=1)
    toe_boundary: Literal['free','pinned','fixed'] = 'free'
    @model_validator(mode='after')
    def check_spacing(self):
        if self.spacing_m < self.diameter_m:
            raise ValueError('本计算限于非咬合单排圆桩，桩距应不小于桩径')
        return self

class Layer(Strict):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    top_m: float = Field(ge=0)
    bottom_m: float = Field(gt=0)
    gamma_natural_kn_m3: float = Field(gt=0, le=30)
    gamma_sat_kn_m3: float = Field(gt=9.81, le=35)
    c_effective_kpa: float = Field(ge=0, le=500)
    phi_effective_deg: float = Field(ge=0, lt=48)
    m_kn_m4: float = Field(gt=0, le=2e6)
    soil_group: Literal['sand','gravel','silt','clay','rock'] | None = None
    consistency: Literal['soft','not_soft','unknown'] | None = None
    heave_strength_basis: Literal['effective','total'] | None = None
    heave_c_kpa: float | None = Field(default=None, ge=0, le=500)
    heave_phi_deg: float | None = Field(default=None, ge=0, lt=48)

class Strut(Strict):
    id: str = Field(min_length=1)
    depth_m: float = Field(ge=0)
    stiffness_kn_m: float = Field(gt=0, le=1e10)
    spacing_along_wall_m: float = Field(gt=0, le=30)
    # No preload, no unilateral disengagement in this verified scope.

class Stage(Strict):
    id: str = Field(min_length=1)
    event: Literal['initial','excavate','install_strut','change_water','change_surcharge']
    excavation_depth_m: float = Field(ge=0)
    water_outside_depth_m: float = Field(ge=0)
    water_inside_depth_m: float = Field(ge=0)
    surcharge_kpa: float = Field(ge=0, le=150)
    surcharge_permanent_kpa: float | None = Field(default=None, ge=0, le=150)
    surcharge_variable_kpa: float | None = Field(default=None, ge=0, le=150)

class Cage(Strict):
    bar_count: int = Field(ge=6,le=80)
    bar_diameter_mm: float = Field(ge=8,le=40)
    bar_axis_cover_mm: float = Field(gt=0,le=150)
    minimum_ratio: float = Field(gt=0,lt=.04)
    concrete_design_mpa: float = Field(gt=5,le=50)
    steel_design_mpa: float = Field(gt=100,le=600)
    stirrup_diameter_mm: float = Field(ge=4,le=20)
    stirrup_spacing_mm: float = Field(ge=50,le=400)
    concrete_peak_strain: float | None = Field(default=None, gt=0, le=.02)
    concrete_ultimate_strain: float | None = Field(default=None, gt=0, le=.02)
    steel_modulus_mpa: float | None = Field(default=None, gt=100000, le=250000)
    bar_start_angle_deg: float | None = Field(default=None, ge=0, lt=360)

class HydraulicLayer(Strict):
    top_m: float = Field(ge=0)
    bottom_m: float = Field(gt=0)
    kh_m_s: float = Field(gt=0, le=.1)
    kv_m_s: float = Field(gt=0, le=.1)
    gamma_sub_kn_m3: float = Field(gt=0, le=25)

class SeepageNumerical(Strict):
    width_inside_m: float = Field(gt=1, le=200)
    width_outside_m: float = Field(gt=1, le=200)
    depth_below_pit_m: float = Field(gt=1, le=100)
    curtain_embedment_below_pit_m: float = Field(gt=0, le=50)
    outside_total_head_m: float
    inside_total_head_m: float
    head_basis: str = Field(min_length=8)
    layers: tuple[HydraulicLayer,...] = Field(min_length=1,max_length=20)
    far_field_boundary_verified: bool = False
    bottom_boundary_verified: bool = False
    hydraulic_evidence: str | None = None
    mesh_refinement_limit: float = Field(default=.10, gt=0, le=.5)

class GroundwaterCheck(Strict):
    # Unknown is intentionally distinct from explicitly investigated absence.
    mode: Literal['unknown','no_trigger','confined','hanging_curtain','both'] = 'unknown'
    assessment_basis: str | None = None
    confined_aquifer_top_depth_m: float | None = Field(default=None, ge=0, le=100)
    confined_head_difference_m: float | None = Field(default=None, ge=0, le=100)
    hanging_curtain_toe_depth_m: float | None = Field(default=None, ge=0, le=100)
    aquifer_top_or_water_depth_m: float | None = Field(default=None, ge=0, le=100)
    submerged_unit_weight_kn_m3: float | None = Field(default=None, gt=0, le=25)
    homogeneous_aquifer: bool | None = None
    numerical_seepage: SeepageNumerical | None = None

class CrackControl(Strict):
    grade: Literal[1,2,3]
    concrete_tensile_standard_mpa: float = Field(gt=0,le=12)
    material_basis: str = Field(min_length=10)
    width_limit_mm: float | None = Field(default=None,gt=0,le=1.)
    @model_validator(mode='after')
    def need_width_limit(self):
        if self.grade == 3 and self.width_limit_mm is None:
            raise ValueError('三级裂缝控制必须有明确裂缝宽度限值，数值方法另行验证')
        return self

class ServiceAxialAction(Strict):
    combination_name: str = Field(min_length=2)
    stage_id: str = Field(min_length=1)
    axial_service_kn: float
    source_basis: str = Field(min_length=10)

class DesignCase(Strict):
    name: str = Field(min_length=2, max_length=80)
    from_stage_id: str = Field(min_length=1)
    additional_surcharge_kpa: float = Field(default=0, ge=0, le=150)
    outside_water_raise_m: float = Field(default=0, ge=0, le=30)
    inside_water_lower_m: float = Field(default=0, ge=0, le=30)
    rationale: str = Field(min_length=8, max_length=600)

class LoadCombination(Strict):
    name: str = Field(min_length=2, max_length=90)
    kind: Literal['ULS_basic','SLS_standard','SLS_quasi_permanent','construction_adverse']
    gamma_earth: float = Field(ge=0, le=3)
    gamma_permanent_surcharge: float = Field(ge=0, le=3)
    gamma_variable_surcharge: float = Field(ge=0, le=3)
    gamma_water: float = Field(ge=0, le=3)
    gamma_importance: float = Field(ge=.9, le=2)
    action_basis: str = Field(min_length=8)
    combination_basis: str = Field(min_length=8)

class MatchedAxialAction(Strict):
    combination_name: str = Field(min_length=2)
    stage_id: str = Field(min_length=1)
    axial_design_kn: float
    source_basis: str = Field(min_length=10)
    second_order_assessment: Literal['not_required_by_project_assessment','required','unknown']
    second_order_basis: str = Field(min_length=10)

class OppositeEndReaction(Strict):
    stage_id: str = Field(min_length=1)
    combination_name: str | None = None
    normal_kn: float
    source_basis: str = Field(min_length=10)

class SupportMember(Strict):
    kind: Literal['steel','concrete']
    angle_to_wall_normal_deg: float = Field(default=0, ge=0, lt=80)
    member_length_m: float = Field(gt=0, le=150)
    tributary_wall_width_m: float = Field(gt=0, le=30)
    section_area_mm2: float | None = Field(default=None, gt=0)
    section_modulus_mm3: float | None = Field(default=None, gt=0)
    material_design_strength_mpa: float | None = Field(default=None, gt=0)
    stability_factor: float | None = Field(default=None, gt=0, le=1)
    stability_factor_basis: str | None = None
    connection_capacity_kn: float | None = Field(default=None, gt=0)
    connection_basis: str | None = None
    design_eccentricity_mm: float | None = Field(default=None, ge=0)
    section_basis: str | None = None
    opposite_end_reactions: tuple[OppositeEndReaction,...] = ()
    other_end_boundary_basis: str | None = None
    elastic_modulus_mpa: float | None = Field(default=None, gt=0, le=3e5)
    i_horizontal_mm4: float | None = Field(default=None, gt=0)
    i_vertical_mm4: float | None = Field(default=None, gt=0)
    effective_length_horizontal_m: float | None = Field(default=None, gt=0)
    effective_length_vertical_m: float | None = Field(default=None, gt=0)
    local_stability_basis: str | None = None

class Section(Strict):
    schema_version: Literal['pitguard.one-strut/1'] = 'pitguard.one-strut/1'
    project_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    project_region: str = Field(min_length=2)
    design_date: str
    purpose: Literal['project_review','verification_example']
    pile: Pile
    layers: tuple[Layer,...] = Field(min_length=1,max_length=12)
    strut: Strut
    stages: tuple[Stage,...] = Field(min_length=3,max_length=15)
    cage: Cage
    sources: tuple[Source,...] = ()
    water_unit_weight_kn_m3: float = Field(default=9.81,ge=9.5,le=10.5)
    mesh_max_m: float = Field(default=.25,ge=.05,le=1.)
    displacement_limit_mm: float = Field(gt=0, le=500)
    gamma0: float = Field(ge=.9,le=2)
    design_effect_factor: float = Field(ge=1,le=2)
    safety_grade: Literal[1,2,3] | None = None
    groundwater_check: GroundwaterCheck = Field(default_factory=GroundwaterCheck)
    support_member: SupportMember | None = None
    adverse_cases: tuple[DesignCase,...] = Field(default=(), max_length=8)
    load_combinations: tuple[LoadCombination,...] = Field(default=(), max_length=12)
    matched_axial_actions: tuple[MatchedAxialAction,...] = Field(default=(), max_length=150)
    crack_control: CrackControl | None = None
    service_axial_actions: tuple[ServiceAxialAction,...] = Field(default=(), max_length=150)
    engineering_design: dict | None = None
    method: Literal['jgjt120_rankine_elastic_strut_limited_v1'] = 'jgjt120_rankine_elastic_strut_limited_v1'

    @model_validator(mode='after')
    def cross_checks(self):
        try: date.fromisoformat(self.design_date)
        except ValueError: raise ValueError('设计日期不合法')
        layers=sorted(self.layers,key=lambda x:x.top_m)
        if [x.id for x in layers] != [x.id for x in self.layers]: raise ValueError('土层必须按深度排序')
        prev=0.
        for layer in layers:
            if abs(layer.top_m-prev)>1e-8 or layer.bottom_m<=layer.top_m:
                raise ValueError('土层不能有重叠或缺失')
            if layer.gamma_sat_kn_m3<=self.water_unit_weight_kn_m3:
                raise ValueError('饱和重度必须高于水重度，核对含水率和单位')
            prev=layer.bottom_m
        if prev+1e-8<self.pile.length_m: raise ValueError('土层必须覆盖墙趾深度')
        if self.strut.depth_m>=self.pile.length_m: raise ValueError('支撑超出桩身范围')
        if any(s.excavation_depth_m>=self.pile.length_m for s in self.stages):
            raise ValueError('开挖面必须位于桩底以上')
        if any(s.water_inside_depth_m<s.excavation_depth_m for s in self.stages):
            raise ValueError('坑内水位高于坑底积水需要独立水压模型')
        if self.stages[0].event!='initial' or self.stages[0].excavation_depth_m!=0:
            raise ValueError('首阶段应为零开挖初始平衡')
        installed=False; previous=0.; seen=set(); have_excavation=False
        for index,s in enumerate(self.stages):
            if s.id in seen: raise ValueError('工况编号重复')
            seen.add(s.id)
            if s.excavation_depth_m<previous: raise ValueError('本模块不支持回填卸载')
            if s.event=='initial' and index!=0: raise ValueError('不允许重新初始化阶段')
            if s.event=='excavate':
                if s.excavation_depth_m<=previous: raise ValueError('开挖事件深度未增加')
                have_excavation=True
            elif s.excavation_depth_m!=previous: raise ValueError('安装、换水及改变超载不能同时改变开挖深度')
            if s.event=='install_strut':
                if installed: raise ValueError('本模块只允许安装一道支撑')
                if s.excavation_depth_m<self.strut.depth_m: raise ValueError('尚未开挖暴露支撑位置')
                installed=True
            previous=s.excavation_depth_m
        if not installed or not have_excavation or not any(s.event=='excavate' for s in self.stages[1:]):
            raise ValueError('缺少支撑安装或开挖事件')
        if self.stages[-1].event=='install_strut':
            raise ValueError('最终阶段不得停留在安装瞬间')
        ids={x.id for x in self.stages}
        for case in self.adverse_cases:
            if case.from_stage_id not in ids:
                raise ValueError('不利工况起点必须属于已定义施工阶段')
        if len({c.name for c in self.adverse_cases})!=len(self.adverse_cases):
            raise ValueError('不利工况名称不得重复')
        combo_ids={c.name for c in self.load_combinations}
        if len({(a.combination_name,a.stage_id) for a in self.matched_axial_actions})!=len(self.matched_axial_actions):
            raise ValueError('同一组合和阶段不能重复设置桩轴力')
        if any(a.combination_name not in combo_ids or a.stage_id not in seen for a in self.matched_axial_actions):
            raise ValueError('轴力必须绑定已定义的作用组合和施工阶段')
        if len({(a.combination_name,a.stage_id) for a in self.service_axial_actions})!=len(self.service_axial_actions):
            raise ValueError('同一SLS组合和阶段不能重复设置轴力')
        if any(a.combination_name not in combo_ids or a.stage_id not in seen for a in self.service_axial_actions):
            raise ValueError('SLS轴力必须绑定已有组合和阶段')
        kind_map={c.name:c.kind for c in self.load_combinations}
        if any(kind_map.get(a.combination_name) not in ('SLS_standard','SLS_quasi_permanent')
               for a in self.service_axial_actions):
            raise ValueError('SLS轴力不能绑定设计基本组合或施工不利组合')
        if self.load_combinations:
            names=[x.name for x in self.load_combinations]
            if len(set(names))!=len(names):raise ValueError('作用组合名称必须唯一')
            for st in self.stages:
                if st.surcharge_permanent_kpa is None or st.surcharge_variable_kpa is None:
                    raise ValueError('使用显式组合时，必须拆分每个阶段的永久、可变地面超载')
                if abs(st.surcharge_permanent_kpa+st.surcharge_variable_kpa-st.surcharge_kpa)>1e-7:
                    raise ValueError('永久、可变地面超载之和必须等于特征超载')
        if self.groundwater_check.mode == 'no_trigger' and not self.groundwater_check.assessment_basis:
            raise ValueError('无水力稳定触发条件必须有水文调查依据')
        if self.pile.length_m/self.mesh_max_m>240: raise ValueError('节点数量超出本工作台限制')
        return self

MUST_CONFIRM=(
    'pile.diameter_m','pile.spacing_m','pile.length_m','pile.elastic_modulus_kpa',
    'pile.inertia_factor','strut.stiffness_kn_m','strut.depth_m',
    'displacement_limit_mm','design_effect_factor','gamma0','safety_grade',
    'cage.minimum_ratio','cage.concrete_design_mpa','cage.steel_design_mpa',
)
def required_sources(model: Section)->list[str]:
    fields=list(MUST_CONFIRM)
    if model.groundwater_check.mode != 'unknown':
        fields.extend(['groundwater_check.mode','groundwater_check.assessment_basis'])
    if model.support_member:
        fields.extend(['support_member.member_length_m','support_member.tributary_wall_width_m',
                       'support_member.angle_to_wall_normal_deg'])
    for i,layer in enumerate(model.layers):
        names=['top_m','bottom_m','gamma_natural_kn_m3','gamma_sat_kn_m3',
               'c_effective_kpa','phi_effective_deg','m_kn_m4','soil_group','heave_strength_basis']
        if layer.soil_group=='clay':names.append('consistency')
        if layer.heave_strength_basis=='total':names.extend(['heave_c_kpa','heave_phi_deg'])
        fields.extend(f'layers.{i}.{name}' for name in names)
    return fields
