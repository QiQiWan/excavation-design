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
    axial_shear_loading_case: Literal['uniform_loading','concentrated_load'] | None = None
    axial_shear_loading_basis: str | None = Field(default=None, max_length=1000)
    shear_span_ratio_a_over_h0: float | None = Field(default=None, gt=0, le=100)


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


class CircularWidthReferenceCase(EngineeringInput):
    stage_id: str = Field(min_length=1,max_length=80)
    depth_m: float = Field(ge=0,le=100)
    side: Literal['above','below']
    frequent_effect_scale: float = Field(gt=0,le=3)
    frequent_moment_knm: float
    axial_frequent_kn: float = Field(gt=0)
    effective_length_m: float = Field(gt=0,le=60)
    long_term_effect_ratio: float = Field(ge=0,le=1)
    combination_basis: str = Field(min_length=12,max_length=1000)


class CircularWidthReference(EngineeringInput):
    method: Literal['JTG3362-2018_bridge_comparison_only']
    steel_surface: Literal['deformed','plain','epoxy_deformed']
    geometry_basis: str = Field(min_length=12,max_length=1000)
    project_width_limit_mm: float = Field(gt=0,le=2)
    cases: tuple[CircularWidthReferenceCase,...] = Field(min_length=1,max_length=60)
    @model_validator(mode='after')
    def unique_stations(self):
        names=[(c.stage_id,c.depth_m,c.side) for c in self.cases]
        if len(names)!=len(set(names)):
            raise ValueError('Repeated circular reference stage/depth/side station')
        return self


class CageConstruction(EngineeringInput):
    minimum_clear_bar_spacing_mm: float = Field(gt=0,le=300)
    minimum_hoop_cover_mm: float = Field(gt=0,le=200)
    minimum_hoop_diameter_mm: float = Field(gt=0,le=40)
    maximum_middle_pitch_mm: float = Field(gt=0,le=600)
    maximum_end_pitch_mm: float = Field(gt=0,le=400)
    actual_top_end_pitch_mm: float = Field(gt=0,le=400)
    actual_bottom_end_pitch_mm: float = Field(gt=0,le=400)
    actual_top_end_zone_length_mm: float = Field(gt=0,le=10000)
    actual_bottom_end_zone_length_mm: float = Field(gt=0,le=10000)
    required_top_end_zone_length_mm: float = Field(gt=0,le=10000)
    required_bottom_end_zone_length_mm: float = Field(gt=0,le=10000)
    criteria_basis: str = Field(min_length=12,max_length=1000)
    fabrication_basis: str = Field(min_length=12,max_length=1000)


class SecondOrderReferenceCase(EngineeringInput):
    stage_id: str = Field(min_length=1,max_length=80)
    combination_name: str = Field(min_length=2,max_length=90)
    axial_compression_kn: float
    ei_kn_m2: float = Field(gt=0,le=1e11)
    effective_length_m: float = Field(gt=0,le=150)
    boundary_basis: str = Field(min_length=15,max_length=1000)
    load_basis: str = Field(min_length=15,max_length=1000)
    uniform_foundation_kn_m2: float | None = Field(default=None,ge=0,le=1e10)
    foundation_basis: str | None = Field(default=None,min_length=15,max_length=1000)
    ideal_end_condition: Literal['pinned_pinned','fixed_pinned','fixed_fixed'] = 'pinned_pinned'


class SecondOrderReference(EngineeringInput):
    cases: tuple[SecondOrderReferenceCase,...] = Field(min_length=1,max_length=30)
    @model_validator(mode='after')
    def unique(self):
        names=[(c.stage_id,c.combination_name) for c in self.cases]
        if len(names)!=len(set(names)):
            raise ValueError('Duplicate second-order stage/combination')
        return self


class CircularCrackProjectCase(EngineeringInput):
    stage_id: str = Field(min_length=1,max_length=80)
    combination_name: str = Field(min_length=2,max_length=90)
    depth_m: float = Field(ge=0,le=150)
    side: Literal['above','below']
    service_effect_scale: float = Field(gt=0,le=3)
    service_axial_kn: float
    service_moment_knm: float
    reported_width_mm: float = Field(ge=0,le=5)
    independent_width_mm: float = Field(ge=0,le=5)
    independent_tolerance_mm: float = Field(gt=0,le=.1)
    report_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    checker_identity: str = Field(min_length=4,max_length=100)


class CircularCrackProjectReview(EngineeringInput):
    method_basis: str = Field(min_length=25,max_length=2000)
    method_document_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    project_width_limit_mm: float = Field(gt=0,le=2)
    cases: tuple[CircularCrackProjectCase,...] = Field(min_length=1,max_length=60)
    @model_validator(mode='after')
    def unique(self):
        names=[(c.stage_id,c.combination_name,c.depth_m,c.side) for c in self.cases]
        if len(names)!=len(set(names)):
            raise ValueError('Duplicate crack review station')
        return self


class BarSplice(EngineeringInput):
    bar_index: int = Field(ge=0,le=79)
    station_mm: float = Field(ge=0,le=50000)
    method: Literal['mechanical','welded','lap']
    source_reference: str = Field(min_length=12,max_length=600)


class CageFabrication(EngineeringInput):
    segment_lengths_mm: tuple[float,...] = Field(min_length=1,max_length=30)
    max_transport_length_mm: float = Field(gt=0,le=50000)
    max_bar_stock_length_mm: float = Field(gt=0,le=50000)
    max_lift_segment_mass_kg: float = Field(gt=0,le=100000)
    steel_density_kg_m3: float = Field(ge=7500,le=8200)
    required_top_anchorage_mm: float = Field(ge=0,le=20000)
    required_bottom_anchorage_mm: float = Field(ge=0,le=20000)
    available_top_anchorage_mm: float = Field(ge=0,le=20000)
    available_bottom_anchorage_mm: float = Field(ge=0,le=20000)
    available_node_clearance_mm: float = Field(ge=0,le=20000)
    required_node_clearance_mm: float = Field(ge=0,le=20000)
    minimum_splice_stagger_mm: float = Field(gt=0,le=2000)
    minimum_splice_end_distance_mm: float = Field(ge=0,le=10000)
    splice_interface_window_mm: float = Field(gt=0,le=3000)
    max_same_station_fraction: float = Field(gt=0,le=1)
    connection_method: Literal['continuous_no_splices','mechanical','welded','lap']
    splices: tuple[BarSplice,...] = Field(default=(),max_length=1000)
    criteria_basis: str = Field(min_length=15,max_length=2000)
    fabrication_basis: str = Field(min_length=15,max_length=2000)
    node_basis: str = Field(min_length=15,max_length=2000)
    connection_basis: str = Field(min_length=15,max_length=2000)

    @model_validator(mode='after')
    def lengths_and_connections(self):
        if any(v<=0 for v in self.segment_lengths_mm):
            raise ValueError('All cage segments must be positive')
        if self.connection_method=='continuous_no_splices' and len(self.segment_lengths_mm)>1:
            raise ValueError('Multiple segments must declare a connection method')
        if self.connection_method=='continuous_no_splices' and self.splices:
            raise ValueError('Unplanned bar splices in continuous cage')
        return self


class EngineeringDesign(EngineeringInput):
    nm_cases: tuple[AxialStageCase, ...] | None = Field(default=None, min_length=1, max_length=50)
    shear: CircularShearInput | None = None
    detailing: DetailingInput | None = None
    anchorage: AnchorageInput | None = None
    lap: LapInput | None = None
    service: ServiceInput | None = None
    circular_width_reference: CircularWidthReference | None = None
    cage_construction: CageConstruction | None = None
    second_order_reference: SecondOrderReference | None = None
    circular_crack_project_review: CircularCrackProjectReview | None = None
    nm_benchmark_axial_kn: tuple[float,...] | None = Field(default=None,min_length=1,max_length=25)
    cage_fabrication: CageFabrication | None = None

    @model_validator(mode='after')
    def unique_cases(self):
        if self.nm_cases is not None and len({c.stage_id for c in self.nm_cases}) != len(self.nm_cases):
            raise ValueError('轴力阶段不得重复；每阶段只接受一个显式全桩恒定设计轴力')
        return self
