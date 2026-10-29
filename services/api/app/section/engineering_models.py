"""Explicit optional engineering context; never infer absent project approvals.

Kept independent of section.models so a versioned SectionInput may import this.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class EngineeringInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', allow_inf_nan=False)


class AxialStageCase(EngineeringInput):
    stage_id: str = Field(min_length=1, max_length=80)
    axial_force_design_kn: float
    axial_force_basis: str = Field(min_length=5, max_length=1000)
    second_order_assessment: Literal['not_required_by_project_assessment', 'required', 'unknown']
    second_order_basis: str = Field(min_length=5, max_length=1000)


class CircularShearInput(EngineeringInput):
    concrete_tensile_design_strength_mpa: float = Field(gt=0, le=10)
    concrete_strength_coefficient_beta_c: float = Field(ge=0.8, le=1)
    coefficient_basis: str = Field(min_length=5, max_length=1000)
    stirrup_design_strength_mpa: float = Field(gt=0, le=1000)
    effective_hoop_area_mm2: float = Field(gt=0, le=20000)
    hoop_area_basis: str = Field(min_length=10, max_length=1000)
    loading_case: Literal['general_bending_nonprestressed', 'concentrated_load', 'other']
    axial_force_design_kn: float
    seismic_case: Literal['nonseismic', 'seismic', 'unknown']
    applicability_basis: str = Field(min_length=10, max_length=1000)


class DetailingInput(EngineeringInput):
    hoop_arrangement: Literal['external_tangent_closed_circular', 'other', 'unknown']
    minimum_clear_cover_mm: float = Field(gt=0, le=200)
    minimum_clear_spacing_mm: float = Field(gt=0, le=300)
    maximum_stirrup_spacing_mm: float = Field(gt=0, le=1000)
    minimum_stirrup_diameter_mm: float = Field(gt=0, le=50)
    criterion_basis: str = Field(min_length=10, max_length=1000)


class AnchorageInput(EngineeringInput):
    bar_surface: Literal['deformed', 'plain']
    seismic_case: Literal['nonseismic', 'seismic', 'unknown']
    concrete_tensile_design_strength_mpa_capped_c60: float = Field(gt=0, le=2.04)
    material_basis: str = Field(min_length=10, max_length=1000)
    epoxy_coated: StrictBool
    construction_disturbed: StrictBool
    top_available_length_mm: float = Field(ge=0, le=20000)
    bottom_available_length_mm: float = Field(ge=0, le=20000)
    transverse_diameter_mm: float = Field(gt=0, le=50)
    transverse_spacing_mm: float = Field(gt=0, le=1000)
    applicability_basis: str = Field(min_length=10, max_length=1000)


class LapInput(EngineeringInput):
    arrangement: Literal['continuous_no_lap', 'tension_tied_lap', 'mechanical', 'welded', 'unknown']
    arrangement_basis: str = Field(min_length=10, max_length=1000)
    seismic_case: Literal['nonseismic', 'seismic', 'unknown']
    available_length_mm: float | None = Field(default=None, ge=0, le=20000)
    joint_area_ratio: float | None = Field(default=None, gt=0, le=1)
    transverse_diameter_mm: float | None = Field(default=None, gt=0, le=50)
    transverse_spacing_mm: float | None = Field(default=None, gt=0, le=1000)


class ServiceStageCase(EngineeringInput):
    stage_id: str = Field(min_length=1, max_length=80)
    axial_force_service_kn: float
    characteristic_effect_scale: float = Field(gt=0, le=3)
    combination_basis: str = Field(min_length=10, max_length=1000)


class ServiceInput(EngineeringInput):
    cases: tuple[ServiceStageCase, ...] = Field(min_length=1, max_length=50)
    concrete_elastic_modulus_mpa: float = Field(gt=1000, le=100000)
    long_term_basis: str = Field(min_length=10, max_length=1000)
    crack_width_limit_mm: float = Field(gt=0, le=2)
    limit_basis: str = Field(min_length=10, max_length=1000)

    @model_validator(mode='after')
    def unique_cases(self):
        if len({c.stage_id for c in self.cases}) != len(self.cases):
            raise ValueError('SLS阶段不得重复；每阶段只接受一个显式组合比例')
        return self


class EngineeringDesign(EngineeringInput):
    nm_cases: tuple[AxialStageCase, ...] | None = Field(default=None, min_length=1, max_length=50)
    shear: CircularShearInput | None = None
    detailing: DetailingInput | None = None
    anchorage: AnchorageInput | None = None
    lap: LapInput | None = None
    service: ServiceInput | None = None

    @model_validator(mode='after')
    def unique_cases(self):
        if self.nm_cases is not None and len({c.stage_id for c in self.nm_cases}) != len(self.nm_cases):
            raise ValueError('轴力阶段不得重复；每阶段只接受一个显式全桩恒定设计轴力')
        return self
