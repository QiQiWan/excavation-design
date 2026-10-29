from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isclose, pi, sin
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.section.engineering_models import EngineeringDesign
from app.section.method_models import MethodExtensions


SECTION_SCHEMA = 'pitguard.frozen-section/1'
SECTION_ALGORITHM = 'two-sided-epp-section/1.0'
SECTION_SCHEMA_V2 = 'pitguard.frozen-section/2'
SECTION_ALGORITHM_V2 = 'two-sided-epp-section/2.0'


def algorithm_for(data: dict) -> str:
    return SECTION_ALGORITHM if data['schema_version'] == SECTION_SCHEMA else SECTION_ALGORITHM_V2


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', allow_inf_nan=False)


class Datum(FrozenModel):
    label: str = Field(min_length=1, max_length=200)
    wall_top_elevation_m: float
    elevation_system: Literal['local', 'project_relative', 'absolute']


class Pile(FrozenModel):
    diameter_m: float = Field(gt=0.1, le=3)
    spacing_m: float = Field(gt=0.1, le=6)
    length_m: float = Field(gt=1, le=80)
    elastic_modulus_kpa: float = Field(gt=1e5, le=1e9)
    inertia_factor: float = Field(gt=0, le=1)
    toe_boundary: Literal['free', 'fixed', 'pinned']

    @model_validator(mode='after')
    def physical(self):
        if self.spacing_m < self.diameter_m:
            raise ValueError('本排桩模型要求桩距不小于桩径；咬合桩需另定截面与传力模型')
        return self


class SoilLayer(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    top_m: float = Field(ge=0)
    bottom_m: float = Field(gt=0)
    gamma_unsat_kn_m3: float = Field(gt=0, le=35)
    gamma_sat_kn_m3: float = Field(gt=0, le=35)
    cohesion_kpa: float = Field(ge=0, le=1000)
    phi_deg: float = Field(ge=0, lt=50)
    k0: float = Field(gt=0, le=3)
    m_inside_kn_m4: float = Field(gt=0, le=1e7)
    m_outside_kn_m4: float = Field(gt=0, le=1e7)

    @model_validator(mode='after')
    def ordered(self):
        if self.bottom_m <= self.top_m:
            raise ValueError('土层底深度必须大于顶深度')
        if self.gamma_sat_kn_m3 < self.gamma_unsat_kn_m3:
            raise ValueError('饱和重度不得小于非饱和重度')
        return self


class SectionSupport(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    depth_m: float = Field(ge=0)
    kind: Literal['elastic', 'rigid']
    stiffness_kn_per_m: float | None
    compression_only: bool
    preload_kn: float

    @model_validator(mode='after')
    def supported(self):
        if self.preload_kn != 0:
            raise ValueError('暂只接受零预力安装；锁定预加力需独立张拉与锁定事件')
        if self.kind == 'elastic' and (self.stiffness_kn_per_m is None or self.stiffness_kn_per_m <= 0):
            raise ValueError('弹性支点必须提供单桩横向刚度 kN/m')
        if self.kind == 'rigid' and (self.stiffness_kn_per_m is not None or self.compression_only):
            raise ValueError('刚性支点采用双向精确位移约束，不能用任意大刚度或只受压标志')
        return self


class SectionStage(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    event: Literal['initial', 'excavate', 'install', 'remove', 'water_or_load', 'soil_reset']
    excavation_depth_m: float = Field(ge=0)
    water_inside_depth_m: float = Field(ge=0)
    water_outside_depth_m: float = Field(ge=0)
    surcharge_kpa: float = Field(ge=0, le=1000)
    install_support_ids: tuple[str, ...]
    remove_support_ids: tuple[str, ...]


class AnalysisSettings(FrozenModel):
    soil_method: Literal['two_sided_elastoplastic_winkler_v1']
    pressure_method: Literal['effective_rankine_bounds_k0_v1', 'explicit_rankine_stress_modes_v2']
    max_element_size_m: float = Field(ge=0.02, le=2)
    water_unit_weight_kn_m3: float = Field(gt=9, lt=11)
    design_effect_factor: float = Field(ge=1, le=3)
    design_effect_factor_basis: str = Field(min_length=10, max_length=500)


class ReinforcementInput(FrozenModel):
    longitudinal_extent: Literal['full_length']
    bar_start_angle_deg: float = Field(ge=0, lt=360)
    bar_count: int | None = Field(ge=6, le=120)
    bar_diameter_mm: float | None = Field(ge=8, le=50)
    bar_axis_cover_mm: float = Field(gt=0, le=150)
    concrete_design_strength_mpa: float = Field(gt=0, le=100)
    concrete_peak_strain: float = Field(gt=0, le=0.01)
    concrete_ultimate_strain: float = Field(gt=0, le=0.02)
    steel_design_strength_mpa: float = Field(gt=0, le=1000)
    steel_modulus_mpa: float = Field(gt=1e4, le=3e5)
    minimum_longitudinal_ratio: float = Field(gt=0, le=0.04)
    minimum_ratio_basis: str = Field(min_length=5, max_length=500)
    stirrup_diameter_mm: float | None = Field(ge=4, le=20)
    stirrup_spacing_mm: float | None = Field(ge=40, le=500)
    axial_force_kn: Literal[0.0]

    @model_validator(mode='after')
    def complete_pairs(self):
        if (self.bar_count is None) != (self.bar_diameter_mm is None):
            raise ValueError('给定纵筋数量和直径必须同时填写或同时缺失')
        if (self.stirrup_diameter_mm is None) != (self.stirrup_spacing_mm is None):
            raise ValueError('给定箍筋直径和间距必须同时填写或同时缺失')
        if self.concrete_ultimate_strain < self.concrete_peak_strain:
            raise ValueError('混凝土极限应变不得小于峰值应变')
        return self


class Evidence(FrozenModel):
    parameter: str
    kind: Literal['source_fact', 'explicit_assumption', 'verification_definition']
    source: str
    source_sha256: str | None
    page: int | None = Field(ge=1)
    note: str


class SectionInput(FrozenModel):
    schema_version: Literal['pitguard.frozen-section/1', 'pitguard.frozen-section/2']
    section_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    purpose: Literal['verification_fixture', 'project_reconstruction', 'project_review']
    source_project_id: str | None
    source_revision: str | None
    comparison_reference: Literal['uploaded_A3_3'] | None
    datum: Datum
    pile: Pile
    soil_effective_width_m: float = Field(gt=0, le=10)
    soil_layers: tuple[SoilLayer, ...] = Field(min_length=1, max_length=30)
    supports: tuple[SectionSupport, ...] = Field(max_length=20)
    stages: tuple[SectionStage, ...] = Field(min_length=2, max_length=50)
    analysis: AnalysisSettings
    reinforcement: ReinforcementInput
    evidence: tuple[Evidence, ...] = Field(max_length=200)
    limitations: tuple[str, ...] = Field(max_length=50)
    method_extensions: MethodExtensions | None = None
    engineering_design: EngineeringDesign | None = None
    reference_contract_hash: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')

    @model_serializer(mode='wrap')
    def versioned_dump(self, handler):
        # Keep v1 bytes and identities intact, including direct model_dump calls.
        data = handler(self)
        if self.schema_version == SECTION_SCHEMA:
            for key in ('method_extensions', 'engineering_design', 'reference_contract_hash'):
                data.pop(key, None)
        return data

    @model_validator(mode='after')
    def stage_path_and_geometry(self):
        if self.schema_version == SECTION_SCHEMA:
            if any(x is not None for x in (self.method_extensions, self.engineering_design, self.reference_contract_hash)):
                raise ValueError('v1快照不得启用v2方法、工程检查或条件合同')
            if self.analysis.pressure_method != 'effective_rankine_bounds_k0_v1' or any(s.event == 'soil_reset' for s in self.stages):
                raise ValueError('v1快照只接受原有压力方法与事件')
        else:
            if self.method_extensions is None or self.analysis.pressure_method != 'explicit_rankine_stress_modes_v2':
                raise ValueError('v2必须完整声明显式压力、邻载及阶段历史选项')
            self.method_extensions.validate_section(self)
        length = self.pile.length_m
        if length / self.analysis.max_element_size_m > 350:
            raise ValueError('本轻量剖面通道最多接受约350个梁单元；请提高网格尺寸或使用离线分析')
        if self.reinforcement.bar_axis_cover_mm >= self.pile.diameter_m * 500:
            raise ValueError('钢筋中心至边缘距离必须小于桩半径')
        if self.reinforcement.bar_count is not None:
            bar = self.reinforcement.bar_diameter_mm
            assert bar is not None
            if self.reinforcement.bar_axis_cover_mm < bar / 2:
                raise ValueError('钢筋实体超出圆形桩截面；钢筋中心至边缘距离不得小于半径')
            circle_radius = self.pile.diameter_m * 500 - self.reinforcement.bar_axis_cover_mm
            if 2 * circle_radius * sin(pi / self.reinforcement.bar_count) < bar:
                raise ValueError('相邻纵向钢筋实体重叠；圆截面离散钢筋模型不成立')
        for attribute in ('soil_layers', 'supports', 'stages'):
            ids = [x.id for x in getattr(self, attribute)]
            if len(set(ids)) != len(ids):
                raise ValueError(f'{attribute} 的身份标识必须唯一')
        previous_bottom = 0.0
        for layer in self.soil_layers:
            if not isclose(layer.top_m, previous_bottom, abs_tol=1e-8):
                raise ValueError('土层必须从模型地面连续覆盖，不允许空隙或重叠')
            if layer.gamma_sat_kn_m3 <= self.analysis.water_unit_weight_kn_m3:
                raise ValueError('有效应力计算要求饱和重度大于水重度')
            previous_bottom = layer.bottom_m
        if previous_bottom < length:
            raise ValueError('土层必须覆盖桩底')
        support_by_id = {s.id: s for s in self.supports}
        for support in self.supports:
            if support.depth_m >= length:
                raise ValueError('支撑必须位于桩长范围内，不能与桩端约束重叠')
        active: set[str] = set()
        ever_installed: set[str] = set()
        previous: SectionStage | None = None
        for index, stage in enumerate(self.stages):
            if stage.excavation_depth_m >= length:
                raise ValueError('开挖深度必须小于桩长')
            if stage.water_inside_depth_m < stage.excavation_depth_m:
                raise ValueError('本模型不支持坑内地面以上积水；请单独定义积水压力')
            if len(set(stage.install_support_ids)) != len(stage.install_support_ids) or len(set(stage.remove_support_ids)) != len(stage.remove_support_ids):
                raise ValueError('单阶段的支点操作不得重复')
            if index == 0:
                if (stage.event != 'initial' or stage.excavation_depth_m != 0 or
                    stage.surcharge_kpa != 0 or stage.install_support_ids or stage.remove_support_ids or
                    stage.water_inside_depth_m != stage.water_outside_depth_m):
                    raise ValueError('首阶段必须为无开挖、无附加荷载、两侧同水位的初始平衡状态')
            else:
                assert previous is not None
                if stage.event == 'initial' or stage.excavation_depth_m < previous.excavation_depth_m:
                    raise ValueError('施工阶段不可重复初态或回填；本路径仅允许单调开挖')
                if stage.event == 'excavate' and stage.excavation_depth_m <= previous.excavation_depth_m:
                    raise ValueError('开挖事件必须增加开挖深度')
                if stage.event != 'excavate' and stage.excavation_depth_m != previous.excavation_depth_m:
                    raise ValueError('开挖和支撑操作必须拆分为独立事件')
                if stage.event in {'install', 'remove', 'soil_reset'}:
                    for field in ('water_inside_depth_m', 'water_outside_depth_m', 'surcharge_kpa'):
                        if getattr(stage, field) != getattr(previous, field):
                            raise ValueError('安装、拆除或单独重置土体时不得同时改变水位或地面荷载')
            if bool(stage.install_support_ids) != (stage.event == 'install'):
                raise ValueError('安装事件必须且只能包含安装清单')
            if bool(stage.remove_support_ids) != (stage.event == 'remove'):
                raise ValueError('拆除事件必须且只能包含拆除清单')
            for support_id in stage.install_support_ids:
                if support_id not in support_by_id or support_id in ever_installed:
                    raise ValueError('支撑身份不存在或已安装过；重新安装必须采用新身份')
                if support_by_id[support_id].depth_m > stage.excavation_depth_m:
                    raise ValueError('不能在尚未开挖到的位置安装支撑')
                if any(isclose(support_by_id[old].depth_m, support_by_id[support_id].depth_m, abs_tol=1e-8) for old in active):
                    raise ValueError('同一深度不能重复激活支点')
                active.add(support_id)
                ever_installed.add(support_id)
            for support_id in stage.remove_support_ids:
                if support_id not in active:
                    raise ValueError('只能拆除当前激活的支撑')
                active.remove(support_id)
            previous = stage
        if ever_installed != set(support_by_id):
            raise ValueError('每个定义的支撑必须在阶段清单中明确安装')
        return self


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def content_hash(value) -> str:
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _analysis_hash(data: dict) -> str:
    engineering = {key: value for key, value in data.items() if key not in {
        'section_id', 'name', 'purpose', 'source_project_id', 'source_revision', 'evidence', 'limitations',
    }}
    return content_hash({'algorithm': algorithm_for(data), 'engineering': engineering})


@dataclass(frozen=True, slots=True)
class FrozenSectionSnapshot:
    canonical_input: str
    snapshot_hash: str
    analysis_hash: str

    def payload(self) -> dict:
        value = json.loads(self.canonical_input)
        if content_hash(value) != self.snapshot_hash or _analysis_hash(value) != self.analysis_hash:
            raise ValueError('Frozen snapshot hash mismatch / 冻结快照哈希不一致')
        return value

    def validated_input(self) -> SectionInput:
        return SectionInput.model_validate(self.payload())

    def to_record(self) -> dict:
        return {'schemaVersion': self.payload()['schema_version'], 'snapshotHash': self.snapshot_hash,
                'analysisHash': self.analysis_hash, 'input': self.payload()}

    @classmethod
    def from_record(cls, record: dict) -> 'FrozenSectionSnapshot':
        if (set(record) != {'schemaVersion', 'snapshotHash', 'analysisHash', 'input'} or
                record['schemaVersion'] not in (SECTION_SCHEMA, SECTION_SCHEMA_V2) or
                record['schemaVersion'] != record['input'].get('schema_version')):
            raise ValueError('Unsupported frozen snapshot record')
        snapshot = freeze_section(record['input'])
        if snapshot.snapshot_hash != record['snapshotHash'] or snapshot.analysis_hash != record['analysisHash']:
            raise ValueError('Frozen snapshot hash mismatch / 冻结快照哈希不一致')
        return snapshot


def freeze_section(value: dict | SectionInput) -> FrozenSectionSnapshot:
    model = value if isinstance(value, SectionInput) else SectionInput.model_validate(value)
    data = model.model_dump(mode='json')
    return FrozenSectionSnapshot(canonical_json(data), content_hash(data), _analysis_hash(data))


def verification_example() -> dict:
    """Complete synthetic case. Values are explicit test definitions, never project defaults."""
    stages = [
        ('S0', '初始地应力平衡', 'initial', 0.0, 1.5, [], []),
        ('S1', '开挖至2.05米', 'excavate', 2.05, 2.55, [], []),
        ('S2', '安装第一道支撑', 'install', 2.05, 2.55, ['STRUT-1'], []),
        ('S3', '开挖至4.9米', 'excavate', 4.9, 5.4, [], []),
        ('S4', '安装刚性换撑支点', 'install', 4.9, 5.4, ['REPLACEMENT'], []),
        ('S5', '拆除第一道支撑', 'remove', 4.9, 5.4, [], ['STRUT-1']),
    ]
    return {
        'schema_version': SECTION_SCHEMA,
        'section_id': 'VERIFY-PILE-01', 'name': '二维排桩阶段分析验证例',
        'purpose': 'verification_fixture', 'source_project_id': None, 'source_revision': None,
        'comparison_reference': 'uploaded_A3_3',
        'datum': {'label': '验证模型地面 z=0', 'wall_top_elevation_m': 0.0, 'elevation_system': 'local'},
        'pile': {'diameter_m': 0.7, 'spacing_m': 0.9, 'length_m': 10.0,
                 'elastic_modulus_kpa': 30_000_000.0, 'inertia_factor': 1.0, 'toe_boundary': 'free'},
        'soil_effective_width_m': 0.9,
        'soil_layers': [
            {'id': 'L1', 'name': '验证砂土层一', 'top_m': 0, 'bottom_m': 4,
             'gamma_unsat_kn_m3': 18, 'gamma_sat_kn_m3': 20, 'cohesion_kpa': 0,
             'phi_deg': 30, 'k0': 0.5, 'm_inside_kn_m4': 8000, 'm_outside_kn_m4': 8000},
            {'id': 'L2', 'name': '验证砂土层二', 'top_m': 4, 'bottom_m': 20,
             'gamma_unsat_kn_m3': 19, 'gamma_sat_kn_m3': 21, 'cohesion_kpa': 0,
             'phi_deg': 34, 'k0': 0.44, 'm_inside_kn_m4': 15000, 'm_outside_kn_m4': 15000},
        ],
        'supports': [
            {'id': 'STRUT-1', 'depth_m': 1.625, 'kind': 'elastic', 'stiffness_kn_per_m': 29070,
             'compression_only': True, 'preload_kn': 0},
            {'id': 'REPLACEMENT', 'depth_m': 4.6, 'kind': 'rigid', 'stiffness_kn_per_m': None,
             'compression_only': False, 'preload_kn': 0},
        ],
        'stages': [{'id': key, 'name': name, 'event': event, 'excavation_depth_m': depth,
                    'water_inside_depth_m': water, 'water_outside_depth_m': 1.5,
                    'surcharge_kpa': 0, 'install_support_ids': installs, 'remove_support_ids': removes}
                   for key, name, event, depth, water, installs, removes in stages],
        'analysis': {'soil_method': 'two_sided_elastoplastic_winkler_v1',
                     'pressure_method': 'effective_rankine_bounds_k0_v1',
                     'max_element_size_m': 0.125, 'water_unit_weight_kn_m3': 9.81,
                     'design_effect_factor': 1.25,
                     'design_effect_factor_basis': '验证例显式采用1.25的特征内力效应放大系数，不代表任何实际项目规范取值'},
        'reinforcement': {'longitudinal_extent': 'full_length', 'bar_start_angle_deg': 0.0,
                          'bar_count': 16, 'bar_diameter_mm': 18, 'bar_axis_cover_mm': 59,
                          'concrete_design_strength_mpa': 14.3, 'concrete_peak_strain': 0.002,
                          'concrete_ultimate_strain': 0.0033, 'steel_design_strength_mpa': 360,
                          'steel_modulus_mpa': 200000, 'minimum_longitudinal_ratio': 0.006,
                          'minimum_ratio_basis': '本验证例明示的最小纵筋率，用于验证成果链，实际工程须确认采用依据',
                          'stirrup_diameter_mm': 8, 'stirrup_spacing_mm': 125, 'axial_force_kn': 0.0},
        'evidence': [{'parameter': 'all_inputs', 'kind': 'verification_definition',
                      'source': 'PitGuard冻结剖面解析与独立求解验证集', 'source_sha256': None, 'page': None,
                      'note': '完整合成算例；阶段顺序参考A3-3事件结构，其土层、边界和几何不冒充原项目。'}],
        'limitations': ['这是完整定义的合成验证例，不能作为原A3-3项目重建模型。',
                        '平地垂直排桩，分层有效应力与静水压力；不含坡台、有限宽邻载、渗流及土水合算。'],
    }
