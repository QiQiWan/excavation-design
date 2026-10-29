"""One stage/face solve contract for both first solve and coupling iterations.

Service actions and design actions are solved independently. Wall, global and
continuous-wale outputs retain their separate model identities; sharing loads
does not turn these bounded linear models into a validated nonlinear model.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.calculation.global_coupled import solve_global_wall_wale_support_system
from app.calculation.stage_loads import StageLoadRecord
from app.calculation.stage_state import replacement_slab_properties
from app.calculation.support_forces import estimate_support_axial_forces
from app.calculation.wall_internal_force import analyze_wall_on_elastic_foundation
from app.schemas.domain import GlobalCoupledSystemResult, WallInternalForcePoint, WallInternalForceResult


def _wall_points(raw: dict[str, Any], top_elevation: float) -> list[WallInternalForcePoint]:
    # Full precision and all nodes are retained for envelope/provenance checks.
    return [WallInternalForcePoint(depth=p["depth"], elevation=p.get("elevation", top_elevation-p["depth"]),
        shear=p["shear"], moment=p["moment"], displacement=p.get("displacementMm"))
        for p in raw.get("points", [])]


def solve_stage_response(
    project, segment, stage, section, wall, loads: StageLoadRecord,
    state: dict[str, Any], *, segment_supports: list,
    corner_transfer_supports: list | None = None,
    wall_stiffness_factor: float = 1.0, soil_modulus_factor: float = 1.0,
    support_stiffness_factor: float = 1.0, long_term_stiffness_factor: float = 1.0,
    far_end_stiffness: dict[str, float] | None = None, iteration: int = 0,
) -> dict[str, Any]:
    corners = list(corner_transfer_supports or [])
    physical_ids = {s.id for s in segment_supports}
    if physical_ids - set(state["activeSupportIds"]) or physical_ids & set(state["removedSupportIds"]):
        raise ValueError("阶段求解包含未激活或已拆除支撑。")
    restraints = [*segment_supports, *corners]
    top = float(project.excavation.top_elevation)
    depth = float(state["excavationDepthM"])
    settings = project.design_settings
    profiles = {"standard": loads.standard_profile, "design": loads.design_profile}
    slab_properties = replacement_slab_properties(project, segment.length, state)
    stiffness_inputs = {
        "wall": {"thickness": wall.thickness, "bottom": wall.bottom_elevation, "concrete": wall.concrete_grade},
        "supports": [s.model_dump(mode="json", by_alias=True) for s in restraints],
        "waleBeams": [beam.model_dump(mode="json", by_alias=True) for beam in project.retaining_system.wale_beams],
        "segment": segment.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json", by_alias=True),
        "soil": [layer.model_dump(mode="json", by_alias=True) for layer in section.layers],
        "factors": [wall_stiffness_factor, soil_modulus_factor, support_stiffness_factor, long_term_stiffness_factor],
        "farEndStiffness": far_end_stiffness or {}, "slab": slab_properties,
    }
    stiffness_hash = hashlib.sha256(json.dumps(stiffness_inputs, sort_keys=True).encode()).hexdigest()
    solve_id = "solve-" + hashlib.sha256(json.dumps({"load": loads.load_id, "stage": state["stateHash"],
        "segment": segment.id, "stiffness": stiffness_hash, "iteration": iteration}, sort_keys=True).encode()).hexdigest()[:24]
    global_results = {}
    wall_results = {}
    reference_forces = {}
    wale_results = {}
    for basis, pressure in profiles.items():
        raw_global = solve_global_wall_wale_support_system(
            pressure_profile=pressure, segment=segment, face_code=segment.name, active_supports=restraints,
            top_elevation=top, excavation_elevation=top-depth, wall_bottom_elevation=wall.bottom_elevation,
            wall_thickness=wall.thickness, concrete_grade=wall.concrete_grade, soil_profile=section.layers,
            stage_id=stage.id, stage_type=stage.stage_type, wall_stiffness_factor=wall_stiffness_factor,
            soil_modulus_factor=soil_modulus_factor, support_stiffness_factor=support_stiffness_factor,
            replacement_slab_properties=slab_properties, wale_stiffness_factor=settings.wale_cracked_stiffness_factor,
            joint_translational_factor=settings.joint_translational_stiffness_factor,
            joint_rotational_factor=settings.joint_rotational_stiffness_factor,
            rigid_zone_length_factor=settings.rigid_zone_length_factor,
            initial_imperfection_ratio=settings.initial_imperfection_ratio,
            long_term_stiffness_factor=long_term_stiffness_factor,
            support_far_end_stiffness_by_id=far_end_stiffness,
        )
        global_result = GlobalCoupledSystemResult(**raw_global)
        global_results[basis] = global_result
        springs = {r.support_id: r.spring_stiffness for r in global_result.support_reactions if r.spring_stiffness > 0}
        lateral_restraints = []
        if global_result.slab_replacement_status == "active" and global_result.slab_replacement_stiffness:
            lateral_restraints.append({"id": "replacement-slab", "depth": state["slabActivationDepthM"],
                "stiffnessKnPerM": global_result.slab_replacement_stiffness})
        wall_results[basis] = analyze_wall_on_elastic_foundation(
            soil_profile=section.layers, supports=segment_supports, transferred_supports=corners,
            excavation_depth=depth, groundwater_level_outside=None, groundwater_level_inside=None,
            surcharge=stage.surcharge, top_elevation=top, wall_bottom_elevation=wall.bottom_elevation,
            wall_thickness=wall.thickness, concrete_grade=wall.concrete_grade, segment=segment,
            wall_stiffness_factor=wall_stiffness_factor, long_term_stiffness_factor=long_term_stiffness_factor,
            soil_modulus_factor=soil_modulus_factor,
            support_stiffness_factor=support_stiffness_factor, transfer_stiffness_factor=1.0,
            support_stiffness_overrides_kn_per_m=springs,
            pressure_method=settings.pressure_method, water_soil_method=settings.water_soil_method,
            lateral_restraints=lateral_restraints, pressure_profile=pressure,
        )
        collector: list = []
        reference_forces[basis] = estimate_support_axial_forces(
            pressure, restraints, segment.length, top, top-depth,
            safety_grade=settings.safety_grade, segment_name=segment.name, segment=segment,
            wale_beams=project.retaining_system.wale_beams, stage_id=stage.id,
            wale_result_collector=collector, apply_design_factor=False,
        )
        wale_results[basis] = collector
    standard_raw, design_raw = wall_results["standard"], wall_results["design"]
    wall_solver_evidence = {}
    for basis, raw in wall_results.items():
        required = [raw.get(key) for key in ("maxMoment", "maxShear", "maxDisplacement")]
        usable = bool(raw.get("points")) and all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in required
        ) and "fallback" not in str(raw.get("method", "")).lower()
        wall_solver_evidence[basis] = {
            "status": "solved" if usable else "unavailable",
            "method": raw.get("method"), "fallback": not usable,
            "maximumDisplacementMm": raw.get("maxDisplacement"),
            "warnings": list(raw.get("warnings", [])),
        }
    design_wall_usable = wall_solver_evidence["design"]["status"] == "solved"
    displacement = standard_raw.get("maxDisplacement")
    wall_result = WallInternalForceResult(
        segment_id=segment.id, stage_id=stage.id, points=_wall_points(standard_raw, top),
        design_points=_wall_points(design_raw, top) if design_wall_usable else [], load_record_id=loads.load_id, solve_id=solve_id,
        max_moment=abs(float(standard_raw["maxMoment"])), max_shear=abs(float(standard_raw["maxShear"])),
        max_displacement=abs(float(displacement)) if displacement is not None and math.isfinite(float(displacement)) else None,
        max_moment_design=abs(float(design_raw["maxMoment"])) if design_wall_usable else None,
        max_shear_design=abs(float(design_raw["maxShear"])) if design_wall_usable else None,
        importance_factor=settings.importance_factor, load_combination_factor=None,
        method=str(standard_raw.get("method")) + "; separate standard/design load solves",
        warnings=list(dict.fromkeys([*standard_raw.get("warnings", []), *design_raw.get("warnings", [])])),
    )
    standard_global, design_global = global_results["standard"], global_results["design"]
    std_reactions = {(r.support_id, r.endpoint): r for r in standard_global.support_reactions}
    des_reactions = {(r.support_id, r.endpoint): r for r in design_global.support_reactions}
    design_references = {(r.support_id, r.support_endpoint): r for r in reference_forces["design"]}
    physical_ids = {s.id for s in segment_supports}
    forces = [r for r in reference_forces["standard"] if r.support_id in physical_ids]
    for force in forces:
        key = (force.support_id, force.support_endpoint)
        reaction, design_reaction = std_reactions.get(key), des_reactions.get(key)
        reference = float(force.axial_force)
        force.reference_axial_force = reference
        usable = reaction is not None and design_reaction is not None and not standard_global.fallback and not design_global.fallback
        if usable:
            force.axial_force = max(float(reaction.axial_force), 0.0)
            force.axial_force_design = max(float(design_reaction.axial_force), 0.0)
            force.global_axial_force = force.axial_force
            force.force_reconciliation_ratio = force.axial_force / reference if reference > 1e-8 else None
            ratio = force.force_reconciliation_ratio
            force.force_reconciliation_status = "warning" if ratio is not None and (ratio > 3.0 or ratio < 0.2) else "pass"
            force.continuous_beam_reaction = reaction.node_reaction
            force.elastic_support_stiffness = reaction.spring_stiffness
            force.normal_projection_factor = reaction.normal_projection_factor
            force.distribution_method = "global_wall_wale_support_matrix; independent_continuous_wale_reference"
        else:
            reference_design = design_references.get(key)
            force.axial_force_design = reference_design.axial_force if reference_design else None
            force.force_reconciliation_status = "manual_review"
            force.distribution_method = "continuous_wale_reference_only; global_solution_unavailable"
        force.partial_factor = None
        force.importance_factor = settings.importance_factor
        force.distribution_note = f"荷载记录 {loads.load_id}；求解 {solve_id}；标准/设计作用分别求解，未对结果再乘统一系数。"
    design_wales = {(w.wale_beam_code, w.level_index): w for w in wale_results["design"]}
    for wale in wale_results["standard"]:
        paired = design_wales.get((wale.wale_beam_code, wale.level_index))
        if paired is not None:
            wale.max_moment_design = paired.max_moment
            wale.max_shear_design = paired.max_shear
            wale.design_points = paired.points
        wale.load_record_id = loads.load_id
        wale.solve_id = solve_id
    return {
        "wall": wall_result, "wall_raw": standard_raw, "wall_design_raw": design_raw,
        "global": standard_global, "global_design": design_global,
        "forces": forces, "wales": wale_results["standard"], "solve_id": solve_id,
        "evidence": {"solveId": solve_id, "loadRecordId": loads.load_id, "stiffnessHash": stiffness_hash,
            "loadProfileIds": loads.evidence["profile_ids"], "iteration": iteration,
            "wallSolvers": wall_solver_evidence,
            "pressureBasis": "frozen_standard_and_design", "designResponseFactoredAgain": False,
            "modelRelationship": "shared loads and final springs; separate wall/global/wale linear models"},
    }
