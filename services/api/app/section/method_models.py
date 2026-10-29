"""Explicit v2 choices; these describe PitGuard methods, never inferred FRWS options."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MethodModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', allow_inf_nan=False)


class SurfaceLoad(MethodModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal['strip', 'rectangle']
    q_kpa: float = Field(gt=0, le=1000)
    near_m: float = Field(ge=0, le=1000)
    far_m: float = Field(gt=0, le=2000)
    along_start_m: float | None = Field(ge=-2000, le=2000)
    along_end_m: float | None = Field(ge=-2000, le=2000)
    basis: str = Field(min_length=10, max_length=1000)

    @model_validator(mode='after')
    def domain(self):
        if self.far_m <= self.near_m:
            raise ValueError('有限邻载远边距必须大于近边距，均自本剖面墙轴线起算')
        if self.kind == 'strip' and (self.along_start_m is not None or self.along_end_m is not None):
            raise ValueError('条带沿墙方向无限；有限长度须选择rectangle并明确相对剖面位置')
        if self.kind == 'rectangle' and (self.along_start_m is None or self.along_end_m is None or
                                          self.along_end_m <= self.along_start_m):
            raise ValueError('矩形邻载必须明确沿墙起止位置，不得默认剖面位于荷载中点')
        return self


class LayerStressMode(MethodModel):
    layer_id: str = Field(min_length=1, max_length=80)
    mode: Literal['effective_separated', 'total_combined']
    parameter_basis: Literal['effective', 'total']
    basis: str = Field(min_length=10, max_length=1000)

    @model_validator(mode='after')
    def consistent(self):
        if self.parameter_basis != ('effective' if self.mode == 'effective_separated' else 'total'):
            raise ValueError('土水合分算选择必须与该层c、phi和K0的应力口径一致')
        return self


class StageMethodOptions(MethodModel):
    stage_id: str = Field(min_length=1, max_length=80)
    active_load_ids: tuple[str, ...] = Field(max_length=20)
    soil_history_action: Literal['carry_plastic_history_v1', 'reset_plastic_offset_to_zero_v1',
                                 'rebase_soil_at_current_displacement_v1']
    history_basis: str = Field(min_length=10, max_length=1000)


class MethodExtensions(MethodModel):
    schema_version: Literal['pitguard.method-extensions/1']
    geometry_basis: Literal['flat_wall_top_surface']
    m_law: Literal['k_h=m*z']
    m_depth_reference: Literal['current_side_surface']
    m_basis: str = Field(min_length=10, max_length=1000)
    surface_load_method: Literal['boussinesq_vertical_rankine_k_mapping_v1']
    surface_loads: tuple[SurfaceLoad, ...] = Field(max_length=20)
    layer_stress_modes: tuple[LayerStressMode, ...] = Field(min_length=1, max_length=30)
    stage_options: tuple[StageMethodOptions, ...] = Field(min_length=2, max_length=50)

    def validate_section(self, section):
        for records, key in ((self.surface_loads, 'id'), (self.layer_stress_modes, 'layer_id'),
                             (self.stage_options, 'stage_id')):
            identities = [getattr(record, key) for record in records]
            if len(identities) != len(set(identities)):
                raise ValueError('方法扩展中的层、阶段和荷载身份不得重复')
        if [r.layer_id for r in self.layer_stress_modes] != [r.id for r in section.soil_layers]:
            raise ValueError('必须按土层顺序完整指定各层应力口径')
        if [r.stage_id for r in self.stage_options] != [r.id for r in section.stages]:
            raise ValueError('必须按施工顺序完整指定各阶段方法选项')
        known = {r.id for r in self.surface_loads}
        previous = None
        for stage, options in zip(section.stages, self.stage_options):
            active = set(options.active_load_ids)
            if len(active) != len(options.active_load_ids) or not active <= known:
                raise ValueError('阶段邻载清单包含重复或未定义荷载')
            carry = options.soil_history_action == 'carry_plastic_history_v1'
            if stage.event == 'initial' and (active or not carry):
                raise ValueError('初始平衡不得含新增有限邻载或重置历史')
            if stage.event in {'install', 'remove'} and (previous is None or not carry or active != set(previous.active_load_ids)):
                raise ValueError('支撑安装/拆除必须与邻载变化和土体重置分开')
            if stage.event == 'soil_reset' and (previous is None or carry or active != set(previous.active_load_ids)):
                raise ValueError('soil_reset须明确非继承规则，且不能同时改变邻载')
            previous = options
