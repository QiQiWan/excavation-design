from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class DomainModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class UnitSystem(DomainModel):
    length: Literal["m", "mm"] = "m"
    force: Literal["kN", "N"] = "kN"
    stress: Literal["kPa", "MPa", "Pa"] = "kPa"
    angle: Literal["degree", "radian"] = "degree"


class CoordinateSystem(DomainModel):
    type: Literal["local", "projected", "geographic"] = "local"
    origin_x: float = 0.0
    origin_y: float = 0.0
    origin_z: float = 0.0
    epsg: str | None = None
    elevation_datum: str | None = None


class DesignSettings(DomainModel):
    safety_grade: str = "二级"
    environment_grade: str = "一般"
    groundwater_level: float = -1.5
    groundwater_level_inside: float | None = None
    confined_water_head_elevation: float | None = None
    surcharge: float = 20.0
    minimum_segment_length: float = 0.5
    rule_set: str = "jgj120_gb50010_engineering_v1_0"
    pressure_method: Literal["active", "at_rest"] = "active"
    water_soil_method: Literal["separate", "combined"] = "separate"
    displacement_limit_ratio: float | None = None
    auto_center_excavation_on_geology: bool = True
    default_support_spacing: float = 5.0
    # Optional project-defined support depths below excavation top. Empty means enterprise auto-layout.
    support_level_depths_m: list[float] = Field(default_factory=list)
    service_life_years: int = 50
    relative_humidity: float = 0.75
    sustained_load_ratio: float = 0.65
    creep_coefficient: float = 1.6
    shrinkage_strain: float = 0.00025
    temperature_range_c: float = 20.0
    seismic_grade: str = "non_seismic_temporary"
    monitoring_calibration_enabled: bool = True
    require_formal_approval_for_construction: bool = False
    support_wall_clearance_m: float = 1.0
    max_direct_strut_span_m: float = 24.0
    max_wale_support_bay_m: float = 7.5
    hard_max_wale_support_bay_m: float = 9.0
    auto_strength_design_enabled: bool = True
    max_design_iterations: int = 3
    # Wall-toe design is a separate strength/stability loop.  It is enabled by
    # default because support-topology repair cannot close an embedment failure.
    auto_wall_embedment_design_enabled: bool = True
    wall_embedment_safety_margin: float = 0.05
    wall_embedment_search_increment_m: float = 0.25
    wall_embedment_max_additional_depth_m: float = 20.0
    diagonal_brace_min_wall_length_m: float = 18.0
    # Corner braces are wall-to-wall compression members located within a local
    # corner influence zone. They must not terminate on another horizontal strut.
    corner_diagonal_min_offset_m: float = 3.5
    corner_diagonal_max_offset_m: float = 8.0
    corner_diagonal_max_wall_fraction: float = 0.30
    prefer_diagonal_braces: bool = True
    # Wale-bay repair normally uses direct wall-to-wall V/corner braces.
    # Support-to-support T/Y repair nodes are disabled unless the engineer
    # explicitly selects a grid/frame topology that is modelled for them.
    allow_wale_repair_t_y_nodes: bool = False
    replacement_slab_effective_width_m: float = 6.0
    replacement_slab_thickness_m: float = 0.25
    replacement_slab_elastic_modulus_mpa: float = 30000.0
    replacement_connection_reduction: float = 0.65
    default_workspace_mode: Literal["compact", "professional"] = "compact"
    # Geological model domain control.  The design model must cover the retaining
    # wall and a surrounding influence buffer; values outside the borehole trust
    # domain are extrapolated conservatively and flagged for review.
    geology_minimum_plan_buffer_m: float = 10.0
    geology_depth_buffer_ratio: float = 0.5
    geology_max_extrapolation_distance_m: float = 60.0
    auto_extend_geology_to_design_domain: bool = True
    # Expert-design orchestration. Support topology, vertical wall length and
    # reinforcement zoning are reviewed as one coupled design problem.
    expert_design_enabled: bool = True
    wall_vertical_length_optimization_enabled: bool = True
    wall_vertical_zone_min_run_m: float = 20.0
    wall_vertical_zone_max_step_m: float = 2.0
    wall_vertical_max_zone_count: int = 3
    wall_vertical_length_target_margin: float = 0.08
    reinforcement_plan_zoning_enabled: bool = True
    reinforcement_corner_zone_length_m: float = 3.0
    reinforcement_support_node_zone_half_length_m: float = 1.8
    reinforcement_full_geometry_max_bars: int = 60000
    reinforcement_visualization_density_m: float = 4.0
    # Design-institute style reserve and anti-downgrade rules for wall cages.
    # These are project design preferences and remain subject to crack-width,
    # joint, lifting and local-node verification.
    wall_rebar_target_utilization: float = 0.88
    wall_rebar_no_downgrade_existing: bool = True
    wall_rebar_default_max_main_spacing_mm: float = 180.0
    wall_rebar_long_wall_threshold_m: float = 40.0
    wall_rebar_long_wall_max_main_spacing_mm: float = 150.0
    # V3.20 design-institute workflow controls.  The support family is selected
    # explicitly before line positioning; elongated pits default to direct short-
    # span struts and near-square pits require an explicit frame/ring decision.
    support_layout_family: Literal["auto", "direct_strut", "direct_with_corner", "bidirectional_frame", "ring_radial"] = "auto"
    support_transition_zone_spacing_factor: float = 0.72
    support_transition_zone_influence_m: float = 8.0
    support_min_station_separation_m: float = 2.8
    # A calculation wall can contain several construction panels / reinforcement
    # cages.  Panelization is preserved through IFC, CAD and detailing exports.
    wall_panel_target_length_m: float = 6.0
    wall_panel_min_length_m: float = 3.0
    wall_panel_max_length_m: float = 7.0
    wall_toe_design_mode: Literal["uniform", "zoned", "local"] = "uniform"
    wall_toe_allow_imported_reference_optimization: bool = False
    rebar_cage_grid_max_lines_per_face: int = 140


class Point2D(DomainModel):
    x: float
    y: float


class Polyline2D(DomainModel):
    points: list[Point2D]
    closed: bool = True


class GroundwaterRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("gw"))
    water_level: float
    description: str | None = None


class BoreholeLayer(DomainModel):
    id: str = Field(default_factory=lambda: new_id("bhl"))
    stratum_code: str
    stratum_name: str
    top_depth: float
    bottom_depth: float
    top_elevation: float
    bottom_elevation: float
    description: str | None = None


class SoilParameters(DomainModel):
    unit_weight: float | None = None
    saturated_unit_weight: float | None = None
    effective_unit_weight: float | None = None
    cohesion: float | None = None
    friction_angle: float | None = None
    elastic_modulus: float | None = None
    poisson_ratio: float | None = None
    compression_modulus: float | None = None
    permeability_x: float | None = None
    permeability_y: float | None = None
    permeability_z: float | None = None
    k0: float | None = None
    horizontal_subgrade_modulus: float | None = None


class Stratum(DomainModel):
    id: str = Field(default_factory=lambda: new_id("stratum"))
    code: str
    name: str
    color: str | None = None
    parameters: SoilParameters = Field(default_factory=SoilParameters)
    parameter_source: Literal["test", "empirical", "manual", "imported"] = "imported"
    confidence: Literal["high", "medium", "low"] = "medium"


class Borehole(DomainModel):
    id: str = Field(default_factory=lambda: new_id("bh"))
    code: str
    x: float
    y: float
    collar_elevation: float
    depth: float
    layers: list[BoreholeLayer] = Field(default_factory=list)
    water_levels: list[GroundwaterRecord] = Field(default_factory=list)
    source_file: str | None = None


class SurfaceGrid(DomainModel):
    x_values: list[float]
    y_values: list[float]
    z_values: list[list[float]]


class GeologicalSurface(DomainModel):
    stratum_code: str
    surface_type: Literal["top", "bottom"]
    grid: SurfaceGrid
    confidence: Literal["high", "medium", "low"] = "medium"


class GeologicalModel(DomainModel):
    surfaces: list[GeologicalSurface] = Field(default_factory=list)
    volumes: list[dict[str, Any]] = Field(default_factory=list)
    vtu_mesh: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    coverage_audit: dict[str, Any] = Field(default_factory=dict)


class GeologicalLayer(DomainModel):
    stratum_code: str
    stratum_name: str
    top_elevation: float
    bottom_elevation: float
    thickness: float
    parameters: SoilParameters = Field(default_factory=SoilParameters)


class GeologicalSection(DomainModel):
    segment_id: str
    section_name: str
    top_elevation: float
    bottom_elevation: float
    layers: list[GeologicalLayer] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExcavationSegment(DomainModel):
    id: str = Field(default_factory=lambda: new_id("seg"))
    name: str
    start: Point2D
    end: Point2D
    length: float
    outward_normal: Point2D
    midpoint: Point2D
    chainage: float
    representative_section: GeologicalSection | None = None


class LocalPit(DomainModel):
    id: str = Field(default_factory=lambda: new_id("localpit"))
    name: str
    outline: Polyline2D | None = None
    bottom_elevation: float | None = None


class ConstructionObstacle(DomainModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    name: str
    obstacle_type: Literal["basement_column_grid", "ramp", "muck_out_opening", "protected_zone", "center_island", "manual"] = "manual"
    outline: Polyline2D | None = None
    center: Point2D | None = None
    width: float | None = None
    length: float | None = None
    clearance: float = 1.0
    active: bool = True
    note: str | None = None
    optimization_locked: bool = False
    optimization_lock_reason: str | None = None


class ExcavationModel(DomainModel):
    id: str = Field(default_factory=lambda: new_id("exc"))
    name: str = "Main excavation"
    outline: Polyline2D
    top_elevation: float
    bottom_elevation: float
    depth: float
    segments: list[ExcavationSegment] = Field(default_factory=list)
    local_pits: list[LocalPit] = Field(default_factory=list)
    obstacles: list[ConstructionObstacle] = Field(default_factory=list)
    drawing_layers: list[dict[str, Any]] = Field(default_factory=list)
    support_axis_offset: float | None = None
    basement_wall_offset: float | None = None
    explicit_placement: bool = False
    centered_on_geology: bool = False
    placement_note: str | None = None
    area: float | None = None
    perimeter: float | None = None
    warnings: list[str] = Field(default_factory=list)


class SectionDefinition(DomainModel):
    width: float | None = None
    height: float | None = None
    diameter: float | None = None
    wall_thickness: float | None = None
    name: str | None = None


class MaterialDefinition(DomainModel):
    name: str
    grade: str
    elastic_modulus: float | None = None


class ReinforcementGroup(DomainModel):
    id: str = Field(default_factory=lambda: new_id("rebar"))
    name: str
    bar_type: Literal["longitudinal", "stirrup", "distribution", "tie", "additional"]
    diameter: float
    spacing: float | None = None
    count: int | None = None
    grade: str
    location_description: str
    area_per_meter: float | None = None
    required_area_per_meter: float | None = None
    check_status: Literal["preliminary", "pass", "fail", "warning", "manual_review"] = "manual_review"


class WallDesignResult(DomainModel):
    max_moment: float | None = None
    max_shear: float | None = None
    max_displacement: float | None = None
    max_moment_design: float | None = None
    max_shear_design: float | None = None
    required_reinforcement_area: float | None = None
    provided_reinforcement_area: float | None = None
    moment_capacity: float | None = None
    shear_capacity: float | None = None
    rebar_diameter: float | None = None
    rebar_spacing: float | None = None
    governing_rule_ids: list[str] = Field(default_factory=list)
    formula_trace: list[str] = Field(default_factory=list)
    check_status: Literal["preliminary", "manual_review", "pass", "fail", "warning"] = "manual_review"
    method: str | None = None
    notes: list[str] = Field(default_factory=list)


class DiaphragmWallPanel(DomainModel):
    id: str = Field(default_factory=lambda: new_id("dw"))
    segment_id: str
    panel_code: str
    axis: Polyline2D
    design_face_code: str | None = None
    design_length: float | None = None
    face_segment_ids: list[str] = Field(default_factory=list)
    thickness: float
    top_elevation: float
    bottom_elevation: float
    bottom_elevation_source: Literal[
        "unknown", "enterprise_initial", "imported", "manual", "auto_stability"
    ] = "unknown"
    bottom_elevation_locked: bool = False
    source_bottom_elevation: float | None = None
    toe_zone_id: str | None = None
    toe_profile_status: Literal["uniform", "zoned", "local", "reference_locked"] = "uniform"
    construction_panels: list[dict[str, Any]] = Field(default_factory=list)
    concrete_grade: str = "C35"
    rebar_grade: str = "HRB400"
    reinforcement: list[ReinforcementGroup] = Field(default_factory=list)
    design_results: WallDesignResult | None = None
    professional_review_required: bool = True


class WaleBeamInternalForcePoint(DomainModel):
    chainage: float
    shear: float
    moment: float
    deflection: float


class WaleBeamInternalForceResult(DomainModel):
    id: str = Field(default_factory=lambda: new_id("waleif"))
    wale_beam_code: str
    face_code: str
    level_index: int
    elevation: float
    stage_id: str | None = None
    pressure_line_load: float
    beam_length: float
    support_node_count: int
    points: list[WaleBeamInternalForcePoint] = Field(default_factory=list)
    max_moment: float = 0.0
    max_shear: float = 0.0
    max_deflection: float = 0.0
    max_moment_design: float | None = None
    max_shear_design: float | None = None
    method: str = "continuous Euler-Bernoulli wale beam with elastic strut supports"
    warnings: list[str] = Field(default_factory=list)




class WaleBeamEnvelopePoint(DomainModel):
    chainage: float
    max_positive_moment: float = 0.0
    max_negative_moment: float = 0.0
    max_abs_shear: float = 0.0
    max_abs_deflection: float = 0.0


class WaleBeamEnvelopeResult(DomainModel):
    id: str = Field(default_factory=lambda: new_id("waleenv"))
    wale_beam_code: str
    level_index: int | None = None
    face_code: str | None = None
    governing_stage_ids: list[str] = Field(default_factory=list)
    points: list[WaleBeamEnvelopePoint] = Field(default_factory=list)
    max_positive_moment: float = 0.0
    max_negative_moment: float = 0.0
    max_abs_shear: float = 0.0
    max_abs_deflection: float = 0.0
    diagram_note: str = "multi-stage envelope from wale continuous-beam internal-force points"

class WaleBeamDesignResult(DomainModel):
    id: str = Field(default_factory=lambda: new_id("waledesign"))
    wale_beam_code: str
    face_code: str | None = None
    level_index: int | None = None
    max_moment: float = 0.0
    max_shear: float = 0.0
    max_deflection: float = 0.0
    max_moment_design: float = 0.0
    max_shear_design: float = 0.0
    required_reinforcement_area: float | None = None
    provided_reinforcement_area: float | None = None
    moment_capacity: float | None = None
    shear_capacity: float | None = None
    main_bar_diameter: float | None = None
    main_bar_spacing: float | None = None
    stirrup_diameter: float | None = None
    stirrup_spacing: float | None = None
    node_additional_reinforcement_note: str | None = None
    deflection_limit: float | None = None
    deflection_ratio: float | None = None
    deflection_check_status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    optimized_width: float | None = None
    optimized_height: float | None = None
    optimization_history: list[dict[str, Any]] = Field(default_factory=list)
    local_bearing_spread_width: float | None = None
    local_bearing_spread_height: float | None = None
    wall_connection_note: str | None = None
    envelope: WaleBeamEnvelopeResult | None = None
    check_status: Literal["preliminary", "manual_review", "pass", "fail", "warning"] = "manual_review"
    method: str = "GB 50010 rectangular RC wale-beam subset with node reinforcement coordination"
    notes: list[str] = Field(default_factory=list)


class BeamElement(DomainModel):
    id: str = Field(default_factory=lambda: new_id("beam"))
    code: str
    axis: Polyline2D
    elevation: float
    section: SectionDefinition
    material: MaterialDefinition
    beam_role: Literal["crown_beam", "wale_beam", "ring_beam", "replacement_slab_edge", "manual"] = "manual"
    design_axial_force: float | None = None
    design_moment: float | None = None
    design_shear: float | None = None
    support_level: int | None = None
    internal_force_results: list[WaleBeamInternalForceResult] = Field(default_factory=list)
    design_result: WaleBeamDesignResult | None = None
    reinforcement: list[ReinforcementGroup] = Field(default_factory=list)
    professional_review_required: bool = True


class BearingPlateDesign(DomainModel):
    plate_width: float
    plate_height: float
    plate_thickness: float
    bearing_area: float
    bearing_stress: float | None = None
    bearing_capacity: float | None = None
    check_status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    design_note: str | None = None


class SupportWaleNode(DomainModel):
    id: str = Field(default_factory=lambda: new_id("node"))
    code: str
    support_id: str
    support_code: str
    level_index: int
    elevation: float
    location: Point2D
    face_code: str | None = None
    wale_beam_code: str | None = None
    node_type: Literal["strut_to_wale", "diagonal_to_wale", "ring_strut_to_ring", "manual"] = "strut_to_wale"
    bearing_plate: BearingPlateDesign | None = None
    reinforcement: list[ReinforcementGroup] = Field(default_factory=list)
    check_status: Literal["pass", "fail", "warning", "manual_review"] = "manual_review"
    design_note: str | None = None


class SupportElement(DomainModel):
    id: str = Field(default_factory=lambda: new_id("support"))
    code: str
    level_index: int
    elevation: float
    start: Point2D
    end: Point2D
    support_role: Literal["main_strut", "secondary_strut", "corner_diagonal", "ring_strut", "manual"] = "main_strut"
    layout_note: str | None = None
    span_length: float | None = None
    bay_spacing: float | None = None
    start_face_code: str | None = None
    end_face_code: str | None = None
    start_tributary_width: float | None = None
    end_tributary_width: float | None = None
    force_distribution_note: str | None = None
    section_type: Literal["rc_rectangular", "steel_pipe", "h_steel"] = "rc_rectangular"
    section: SectionDefinition = Field(default_factory=lambda: SectionDefinition(width=0.8, height=0.8, name="800x800 RC"))
    material: MaterialDefinition = Field(default_factory=lambda: MaterialDefinition(name="Concrete", grade="C35"))
    preload: float | None = None
    preload_ratio: float | None = None
    installation_stage_id: str | None = None
    temperature_delta_c: float | None = None
    thermal_axial_force: float | None = None
    gap_closure_force: float | None = None
    construction_deviation_mm: float | None = None
    eccentricity_moment: float | None = None
    effective_axial_force_standard: float | None = None
    design_axial_force: float | None = None
    construction_effect_note: str | None = None
    raw_axial_force_standard_envelope: float | None = None
    force_reconciliation_status: Literal["pass", "warning", "manual_review"] = "manual_review"
    force_reconciliation_note: str | None = None
    section_optimization_status: Literal["not_run", "pass", "section_upgraded", "topology_upgrade_required"] = "not_run"
    section_optimization_note: str | None = None
    lifecycle_note: str | None = None
    preload_stage_id: str | None = None
    removal_stage_id: str | None = None
    preload_protocol_status: Literal["pass", "warning", "manual_review"] = "manual_review"
    reinforcement: list[ReinforcementGroup] = Field(default_factory=list)
    professional_review_required: bool = True
    optimization_locked: bool = False
    optimization_locked_start: bool = False
    optimization_locked_end: bool = False
    optimization_lock_reason: str | None = None
    start_wall_connection: Point2D | None = None
    end_wall_connection: Point2D | None = None
    centerline_offset_m: float | None = None
    start_wall_clearance_m: float | None = None
    end_wall_clearance_m: float | None = None
    topology_family: Literal["direct_grid", "hybrid_diagonal", "bidirectional_grid", "ring_radial", "manual"] = "direct_grid"
    design_zone: str | None = None
    station_chainage_m: float | None = None
    local_clear_span_m: float | None = None
    placement_reason: str | None = None
    load_path_class: Literal["wall_to_wall", "wall_to_ring", "supported_frame_node", "manual"] = "wall_to_wall"


class FoundationDesign(DomainModel):
    id: str = Field(default_factory=lambda: new_id("fdn"))
    code: str
    foundation_type: Literal["temporary_spread_footing", "column_pile", "manual_review"] = "temporary_spread_footing"
    width: float
    length: float
    thickness: float = 1.2
    area: float
    concrete_unit_weight: float = 25.0
    foundation_self_weight: float
    vertical_force: float
    fa: float
    eccentricity_factor: float = 1.05
    average_pressure: float
    max_pressure: float
    pile_diameter: float | None = None
    pile_length: float | None = None
    pile_count: int | None = None
    pile_capacity: float | None = None
    pile_utilization: float | None = None
    pile_tip_elevation: float | None = None
    check_status: Literal["pass", "fail", "manual_review"] = "manual_review"
    design_note: str | None = None


class ColumnElement(DomainModel):
    id: str = Field(default_factory=lambda: new_id("col"))
    code: str
    location: Point2D
    top_elevation: float
    bottom_elevation: float
    section: SectionDefinition
    material: MaterialDefinition
    support_codes: list[str] = Field(default_factory=list)
    service_area_note: str | None = None
    foundation_design: FoundationDesign | None = None


class RetainingSystem(DomainModel):
    id: str = Field(default_factory=lambda: new_id("ret"))
    type: Literal["diaphragm_wall_with_internal_bracing"] = "diaphragm_wall_with_internal_bracing"
    diaphragm_walls: list[DiaphragmWallPanel] = Field(default_factory=list)
    crown_beams: list[BeamElement] = Field(default_factory=list)
    wale_beams: list[BeamElement] = Field(default_factory=list)
    ring_beams: list[BeamElement] = Field(default_factory=list)
    supports: list[SupportElement] = Field(default_factory=list)
    support_nodes: list[SupportWaleNode] = Field(default_factory=list)
    columns: list[ColumnElement] = Field(default_factory=list)
    layout_summary: dict[str, Any] = Field(default_factory=dict)
    optimization_locks: list[dict[str, Any]] = Field(default_factory=list)
    support_layout_repair: SupportLayoutRepairSummary | None = None
    rebar_design_scheme: dict[str, Any] = Field(default_factory=dict)
    replacement_path: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConstructionStage(DomainModel):
    id: str = Field(default_factory=lambda: new_id("stage"))
    name: str
    excavation_elevation: float
    active_support_ids: list[str] = Field(default_factory=list)
    deactivated_support_ids: list[str] = Field(default_factory=list)
    active_support_levels: list[int] = Field(default_factory=list)
    transferred_support_levels: list[int] = Field(default_factory=list)
    support_topology_hash: str | None = None
    stage_type: Literal["excavation", "support_installation", "bottom_slab", "replacement", "support_removal", "final"] = "excavation"
    zone: str | None = None
    replacement_action: str | None = None
    groundwater_level_inside: float | None = None
    groundwater_level_outside: float | None = None
    surcharge: float = 20.0


class CalculationCase(DomainModel):
    id: str = Field(default_factory=lambda: new_id("case"))
    name: str
    stages: list[ConstructionStage] = Field(default_factory=list)
    support_topology_hash: str | None = None
    synchronization_note: str | None = None
    created_at: str = Field(default_factory=now_iso)


class PressurePoint(DomainModel):
    depth: float
    elevation: float
    earth_pressure: float
    water_pressure: float
    total_pressure: float
    active_earth_pressure: float | None = None
    passive_earth_pressure: float | None = None
    outside_water_pressure: float | None = None
    inside_water_pressure: float | None = None
    vertical_stress_total: float | None = None
    vertical_stress_effective: float | None = None
    ka: float | None = None
    kp: float | None = None
    k0: float | None = None
    cohesion: float | None = None
    friction_angle: float | None = None
    stratum_code: str | None = None
    method: str | None = "JGJ120-Rankine-water-soil-separated"


class PressureProfile(DomainModel):
    points: list[PressurePoint]
    method: str = "JGJ120-2012 Rankine active/passive earth pressure and hydrostatic water pressure"
    standard_references: list[str] = Field(default_factory=lambda: ["JGJ120-2012 3.4.2", "JGJ120-2012 3.4.4"])
    warnings: list[str] = Field(default_factory=list)


class SupportForceResult(DomainModel):
    support_id: str | None = None
    level_index: int
    elevation: float
    tributary_top: float
    tributary_bottom: float
    axial_force: float
    axial_force_design: float | None = None
    importance_factor: float | None = None
    partial_factor: float | None = None
    unit: str = "kN"
    method: str = "support tributary-area integration of net lateral pressure"
    face_code: str | None = None
    support_endpoint: Literal["start", "end", "unknown"] = "unknown"
    wale_beam_code: str | None = None
    wale_chainage: float | None = None
    tributary_width: float | None = None
    continuous_beam_reaction: float | None = None
    reference_axial_force: float | None = None
    global_axial_force: float | None = None
    force_reconciliation_ratio: float | None = None
    force_reconciliation_status: Literal["pass", "warning", "manual_review"] | None = None
    elastic_support_stiffness: float | None = None
    normal_projection_factor: float | None = None
    beam_node_count: int | None = None
    distribution_method: str | None = None
    distribution_note: str | None = None
    preload_effect: float | None = None
    thermal_effect: float | None = None
    gap_effect: float | None = None
    eccentricity_effect: float | None = None
    effective_axial_force: float | None = None
    construction_effect_note: str | None = None


class WallInternalForcePoint(DomainModel):
    depth: float
    elevation: float
    shear: float
    moment: float
    displacement: float | None = None


class WallInternalForceResult(DomainModel):
    segment_id: str
    stage_id: str
    points: list[WallInternalForcePoint] = Field(default_factory=list)
    max_moment: float = 0.0
    max_shear: float = 0.0
    max_displacement: float | None = None
    max_moment_design: float | None = None
    max_shear_design: float | None = None
    importance_factor: float = 1.0
    load_combination_factor: float = 1.25
    method: str = "equivalent vertical beam from pressure profile; elastic-foundation solver reserved"
    warnings: list[str] = Field(default_factory=list)




class GlobalCoupledDof(DomainModel):
    index: int
    name: str
    value: float
    unit: str = "m"
    dof_type: str | None = None
    object_id: str | None = None
    stage_status: str | None = None


class GlobalCoupledSupportReaction(DomainModel):
    support_id: str
    support_code: str
    endpoint: str
    face_code: str
    level_index: int
    chainage: float
    depth: float
    node_displacement: float
    spring_stiffness: float
    node_reaction: float
    axial_force: float
    axial_deformation: float
    normal_projection_factor: float
    direction_cosine_x: float | None = None
    direction_cosine_y: float | None = None
    rigid_node_factor: float | None = None
    governing_source: str | None = None


class GlobalCoupledSystemResult(DomainModel):
    method: str
    stage_id: str | None = None
    face_code: str | None = None
    fallback: bool = False
    reason: str | None = None
    matrix_size: int = 0
    condition_number: float | None = None
    equilibrium_diagnostics: dict[str, Any] = Field(default_factory=dict)
    dof_summary: dict[str, Any] = Field(default_factory=dict)
    dofs: list[GlobalCoupledDof] = Field(default_factory=list)
    wall_displacement_profile: list[dict[str, Any]] = Field(default_factory=list)
    support_reactions: list[GlobalCoupledSupportReaction] = Field(default_factory=list)
    column_vertical_supports: list[dict[str, Any]] = Field(default_factory=list)
    max_wall_displacement: float = 0.0
    max_support_axial_force: float = 0.0
    # V2.0 spatial frame fields: wall/wale rotational DOFs, support axial DOFs, column vertical DOFs and slab replacement stiffness.
    model_dimension: str | None = None
    spatial_matrix_size: int | None = None
    spatial_condition_number: float | None = None
    spatial_dof_summary: dict[str, Any] = Field(default_factory=dict)
    wall_rotation_profile: list[dict[str, Any]] = Field(default_factory=list)
    wale_node_profile: list[dict[str, Any]] = Field(default_factory=list)
    support_axial_dofs: list[dict[str, Any]] = Field(default_factory=list)
    column_vertical_dofs: list[dict[str, Any]] = Field(default_factory=list)
    slab_replacement_stiffness: float | None = None
    slab_replacement_status: Literal["not_active", "active", "missing", "invalid"] | None = None
    slab_replacement_source: str | None = None
    slab_replacement_required: bool | None = None
    slab_replacement_components: dict[str, Any] = Field(default_factory=dict)
    rigid_node_zones: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)




class StabilityDetailedResult(DomainModel):
    method: str = "V2.0 reviewable stability calculation package"
    controlling_section_id: str | None = None
    controlling_section_name: str | None = None
    heave_factor: float | None = None
    confined_uplift_factor: float | None = None
    seepage_factor: float | None = None
    overall_stability_factor: float | None = None
    weak_layer_index: float | None = None
    min_safety_factor: float | None = None
    controlling_mode: str | None = None
    circular_slip_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    seepage_paths: list[dict[str, Any]] = Field(default_factory=list)
    drawdown_process: list[dict[str, Any]] = Field(default_factory=list)
    dewatering_wells: list[dict[str, Any]] = Field(default_factory=list)
    depressurization_wells: list[dict[str, Any]] = Field(default_factory=list)
    improvement_options: list[dict[str, Any]] = Field(default_factory=list)
    diagram_data: dict[str, Any] = Field(default_factory=dict)
    review_notes: list[str] = Field(default_factory=list)


class DrawingSheetResult(DomainModel):
    sheet_id: str
    title: str
    scale: str = "1:100"
    file_path: str | None = None
    sheet_type: str = "detail"
    model_objects: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)




class QualityGateIssue(DomainModel):
    id: str = Field(default_factory=lambda: new_id("qgi"))
    category: str
    severity: Literal["pass", "warning", "fail", "manual_review"] = "warning"
    object_id: str | None = None
    object_type: str | None = None
    message: str
    recommendation: str | None = None
    highlight_geometry: dict[str, Any] = Field(default_factory=dict)
    related_object_ids: list[str] = Field(default_factory=list)
    display_hint: str | None = None


class SupportLayoutQualitySummary(DomainModel):
    score: float = 0.0
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    summary: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    issues: list[QualityGateIssue] = Field(default_factory=list)
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    crossing_pairs: list[dict[str, Any]] = Field(default_factory=list)
    checked_at: str = Field(default_factory=now_iso)




class SupportLayoutOptimizationCandidate(DomainModel):
    id: str = Field(default_factory=lambda: new_id("slopt"))
    rank: int = 0
    score: float = 0.0
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    target_spacing: float = 5.0
    column_max_span: float = 18.0
    objective_terms: dict[str, float] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_objectives: dict[str, Any] = Field(default_factory=dict)
    variable_summary: dict[str, Any] = Field(default_factory=dict)
    line_adjustments: list[dict[str, Any]] = Field(default_factory=list)
    plan_geometry: dict[str, Any] = Field(default_factory=dict)
    delta_geometry: dict[str, Any] = Field(default_factory=dict)
    weight_summary: dict[str, Any] = Field(default_factory=dict)
    export_readiness: dict[str, Any] = Field(default_factory=dict)
    issue_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    support_count: int = 0
    column_count: int = 0
    max_span_length: float | None = None
    max_bay_spacing: float | None = None
    crossing_count: int = 0
    junction_count: int = 0
    high_degree_junction_count: int = 0
    plan_intersection_complexity: float = 0.0
    obstacle_conflict_count: int = 0
    axial_peak_proxy: float | None = None
    symmetry_score: float | None = None
    muck_path_continuity_score: float | None = None
    full_calculation: dict[str, Any] = Field(default_factory=dict)
    constructability_note: str | None = None

class SupportLayoutRepairSummary(DomainModel):
    optimization_method: str | None = None
    optimization_phase: str | None = None
    hard_constraint_labels: list[str] = Field(default_factory=list)
    soft_objective_labels: list[str] = Field(default_factory=list)
    objective_weights: dict[str, float] = Field(default_factory=dict)
    candidate_count: int = 0
    best_candidate_id: str | None = None
    selected_candidate_id: str | None = None
    locked_support_ids: list[str] = Field(default_factory=list)
    lock_summary: dict[str, Any] = Field(default_factory=dict)
    candidates: list[SupportLayoutOptimizationCandidate] = Field(default_factory=list)
    candidate_full_calculations: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    score_before: float | None = None
    score_after: float | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_issues: list[QualityGateIssue] = Field(default_factory=list)
    summary: str = ""
    checked_at: str = Field(default_factory=now_iso)


class IfcViewerProfileRisk(DomainModel):
    viewer: str
    status: Literal["pass", "warning", "fail", "manual_review"] = "pass"
    risk_level: Literal["low", "medium", "high"] = "low"
    score: float = 100.0
    risk_items: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class IfcCompatibilityCheckResult(DomainModel):
    score: float = 0.0
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    summary: str = ""
    file_path: str | None = None
    export_mode: str | None = None
    entity_counts: dict[str, int] = Field(default_factory=dict)
    raw_unicode_found: bool = False
    missing_references: list[str] = Field(default_factory=list)
    zero_dimension_count: int = 0
    invalid_placement_count: int = 0
    missing_material_association_count: int = 0
    missing_spatial_containment_count: int = 0
    viewer_profiles: list[IfcViewerProfileRisk] = Field(default_factory=list)
    issues: list[QualityGateIssue] = Field(default_factory=list)
    checked_at: str = Field(default_factory=now_iso)


class FormalReportGate(DomainModel):
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    allowed_for_official_issue: bool = False
    headline: str = ""
    blocking_items: list[QualityGateIssue] = Field(default_factory=list)
    warning_items: list[QualityGateIssue] = Field(default_factory=list)
    missing_items: list[QualityGateIssue] = Field(default_factory=list)
    checklist_sections: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=now_iso)

class DesignReviewSummary(DomainModel):
    strength_status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    stiffness_status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    stability_status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    strength_fail_count: int = 0
    stiffness_fail_count: int = 0
    stability_fail_count: int = 0
    strength_warning_count: int = 0
    stiffness_warning_count: int = 0
    stability_warning_count: int = 0
    max_strength_utilization: float | None = None
    max_stiffness_utilization: float | None = None
    min_stability_safety_factor: float | None = None
    notes: list[str] = Field(default_factory=list)

class StageCalculationResult(DomainModel):
    stage_id: str
    segment_id: str
    pressure_profile: PressureProfile
    support_forces: list[SupportForceResult] = Field(default_factory=list)
    wale_beam_results: list[WaleBeamInternalForceResult] = Field(default_factory=list)
    coupled_system_result: dict[str, Any] = Field(default_factory=dict)
    global_coupled_result: GlobalCoupledSystemResult | None = None
    wall_internal_force: WallInternalForceResult | None = None
    wall_internal_force_placeholder: dict[str, Any] = Field(default_factory=dict)
    stability_checks: list[dict[str, Any]] = Field(default_factory=list)
    rc_checks: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)


class GoverningValues(DomainModel):
    max_total_pressure: float = 0.0
    max_support_axial_force: float = 0.0
    max_wall_moment: float | None = None
    max_wall_shear: float | None = None
    max_displacement: float | None = None
    governing_check_status: str | None = None
    embedment_safety_factor_min: float | None = None
    heave_safety_factor_min: float | None = None
    # Legacy field retained for reading older project snapshots. Current layered seepage
    # screening returns a risk index (smaller is safer), not a safety factor.
    seepage_safety_factor_min: float | None = None
    seepage_risk_index_max: float | None = None
    strength_check_status: str | None = None
    stiffness_check_status: str | None = None
    stability_check_status: str | None = None


class CalculationResult(DomainModel):
    id: str = Field(default_factory=lambda: new_id("calc"))
    project_id: str
    case_id: str
    support_topology_hash: str | None = None
    stage_results: list[StageCalculationResult] = Field(default_factory=list)
    governing_values: GoverningValues = Field(default_factory=GoverningValues)
    warnings: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    check_summary: dict[str, int] = Field(default_factory=dict)
    design_iteration_summary: dict[str, Any] = Field(default_factory=dict)
    optimization_actions: list[dict[str, Any]] = Field(default_factory=list)
    report_diagram_data: dict[str, Any] = Field(default_factory=dict)
    design_review_summary: DesignReviewSummary | None = None
    stability_detailed_result: StabilityDetailedResult | None = None
    drawing_sheets: list[DrawingSheetResult] = Field(default_factory=list)
    support_layout_quality: SupportLayoutQualitySummary | None = None
    support_layout_repair: SupportLayoutRepairSummary | None = None
    ifc_compatibility: IfcCompatibilityCheckResult | None = None
    formal_report_gate: FormalReportGate | None = None
    standards: list[str] = Field(default_factory=lambda: [
        "JGJ 120-2012 建筑基坑支护技术规程",
        "GB 55003-2021 建筑与市政地基基础通用规范",
        "GB 55008-2021 混凝土结构通用规范",
        "GB 50010-2010（2024年局部修订）混凝土结构设计规范",
        "GB 50009-2012 建筑结构荷载规范",
        "GB 50007-2011 建筑地基基础设计规范",
    ])
    professional_review_required: bool = True
    calculated_at: str = Field(default_factory=now_iso)




class MonitoringRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("mon"))
    record_type: Literal["wall_displacement", "support_axial_force", "groundwater", "settlement"]
    object_id: str | None = None
    object_code: str | None = None
    stage_id: str | None = None
    timestamp: str = Field(default_factory=now_iso)
    measured_value: float
    unit: str
    elevation: float | None = None
    x: float | None = None
    y: float | None = None
    quality: Literal["verified", "provisional", "rejected"] = "verified"
    source: str = "manual"
    note: str | None = None


class CalibrationRun(DomainModel):
    id: str = Field(default_factory=lambda: new_id("calib"))
    status: Literal["pass", "warning", "fail", "manual_review"] = "manual_review"
    sample_count: int = 0
    wall_stiffness_factor: float = 1.0
    support_stiffness_factor: float = 1.0
    soil_modulus_factor: float = 1.0
    groundwater_offset_m: float = 0.0
    objective_before: float | None = None
    objective_after: float | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    applied: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class ReviewAction(DomainModel):
    id: str = Field(default_factory=lambda: new_id("review"))
    role: Literal["designer", "checker", "reviewer", "approver"]
    actor: str
    action: Literal["submit", "accept", "reject", "reopen", "approve"]
    comment: str | None = None
    snapshot_hash: str
    created_at: str = Field(default_factory=now_iso)


class ReviewWorkflow(DomainModel):
    status: Literal["draft", "submitted", "checked", "reviewed", "approved", "rejected", "stale"] = "draft"
    current_role: Literal["designer", "checker", "reviewer", "approver"] = "designer"
    approved_snapshot_hash: str | None = None
    actions: list[ReviewAction] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=lambda: ["designer", "checker", "reviewer", "approver"])
    updated_at: str = Field(default_factory=now_iso)


class DrawingRevision(DomainModel):
    id: str = Field(default_factory=lambda: new_id("rev"))
    revision: str = "A"
    description: str
    sheet_numbers: list[str] = Field(default_factory=list)
    author: str = "AI-DRAFT"
    snapshot_hash: str
    issue_status: Literal["review", "construction", "superseded"] = "review"
    created_at: str = Field(default_factory=now_iso)


class ProjectSummary(DomainModel):
    id: str
    name: str
    location: str | None = None
    created_at: str | None = None
    updated_at: str
    has_excavation: bool = False
    has_retaining_system: bool = False
    calculation_case_count: int = 0
    calculation_result_count: int = 0
    latest_calculation_id: str | None = None
    governing_status: str | None = None
    geometry_consistent: bool | None = None


class Project(DomainModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    name: str
    location: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    unit_system: UnitSystem = Field(default_factory=UnitSystem)
    coordinate_system: CoordinateSystem = Field(default_factory=CoordinateSystem)
    design_settings: DesignSettings = Field(default_factory=DesignSettings)
    boreholes: list[Borehole] = Field(default_factory=list)
    strata: list[Stratum] = Field(default_factory=list)
    geological_model: GeologicalModel | None = None
    excavation: ExcavationModel | None = None
    retaining_system: RetainingSystem | None = None
    calculation_cases: list[CalculationCase] = Field(default_factory=list)
    calculation_results: list[CalculationResult] = Field(default_factory=list)
    cad_template: dict[str, Any] = Field(default_factory=dict)
    drawing_rule_set: dict[str, Any] = Field(default_factory=dict)
    monitoring_records: list[MonitoringRecord] = Field(default_factory=list)
    calibration_runs: list[CalibrationRun] = Field(default_factory=list)
    review_workflow: ReviewWorkflow = Field(default_factory=ReviewWorkflow)
    drawing_revisions: list[DrawingRevision] = Field(default_factory=list)
    advanced_engineering: dict[str, Any] = Field(default_factory=dict)
    messages: list[str] = Field(default_factory=list)
