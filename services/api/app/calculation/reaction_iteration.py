from __future__ import annotations

import math
from typing import Any

from app.calculation.global_coupled import solve_global_wall_wale_support_system
from app.calculation.planar_transfer_frame import analyze_transfer_frame_system
from app.calculation.wall_internal_force import analyze_wall_on_elastic_foundation
from app.geology.section import extract_representative_section
from app.schemas.domain import (
    CalculationCase,
    GlobalCoupledSystemResult,
    Project,
    StageCalculationResult,
    WallInternalForcePoint,
    WallInternalForceResult,
)


def _wall_force_result(segment_id: str, stage_id: str, raw: dict[str, Any], gamma0: float) -> WallInternalForceResult:
    points_raw = list(raw.get("points") or [])
    stride = max(1, len(points_raw) // 80) if points_raw else 1
    points = [
        WallInternalForcePoint(
            depth=float(item.get("depth") or 0.0),
            elevation=float(item.get("elevation") or -float(item.get("depth") or 0.0)),
            shear=float(item.get("shear") or 0.0),
            moment=float(item.get("moment") or 0.0),
            displacement=item.get("displacementMm"),
        )
        for item in points_raw[::stride]
    ]
    moment = abs(float(raw.get("maxMoment") or 0.0))
    shear = abs(float(raw.get("maxShear") or 0.0))
    displacement = raw.get("maxDisplacement")
    return WallInternalForceResult(
        segment_id=segment_id,
        stage_id=stage_id,
        points=points,
        max_moment=round(moment, 3),
        max_shear=round(shear, 3),
        max_displacement=round(abs(float(displacement)), 3) if displacement is not None else None,
        max_moment_design=round(moment * gamma0 * 1.25, 3),
        max_shear_design=round(shear * gamma0 * 1.25, 3),
        importance_factor=gamma0,
        load_combination_factor=1.25,
        method=str(raw.get("method") or "coupled wall-wale-transfer iteration"),
        warnings=[str(item) for item in raw.get("warnings") or []],
    )


def _stage_force_maps(stage_results: list[StageCalculationResult]) -> dict[str, dict[str, float]]:
    maps: dict[str, dict[str, float]] = {}
    for result in stage_results:
        target = maps.setdefault(str(result.stage_id), {})
        for force in result.support_forces:
            target[str(force.support_id)] = max(
                float(target.get(str(force.support_id), 0.0)),
                float(force.axial_force or 0.0),
            )
    return maps


def _max_relative_delta(current: dict[str, float], previous: dict[str, float]) -> float:
    keys = set(current) | set(previous)
    return max(
        (abs(float(current.get(key, 0.0)) - float(previous.get(key, 0.0))) / max(abs(float(previous.get(key, 0.0))), 1.0)) for key in keys
    ) if keys else 0.0


def _max_absolute_delta(current: dict[str, float], previous: dict[str, float]) -> float:
    keys = set(current) | set(previous)
    return max((abs(float(current.get(key, 0.0)) - float(previous.get(key, 0.0))) for key in keys), default=0.0)


def _nested_relative_delta(current: dict[str, dict[str, float]], previous: dict[str, dict[str, float]]) -> float:
    return max(
        (_max_relative_delta(current.get(key, {}), previous.get(key, {})) for key in set(current) | set(previous)),
        default=0.0,
    )


def iterate_wall_wale_transfer_reactions(
    project: Project,
    case: CalculationCase,
    stage_results: list[StageCalculationResult],
    *,
    gamma0: float,
    wall_stiffness_factor: float = 1.0,
    soil_modulus_factor: float = 1.0,
    support_stiffness_factor: float = 1.0,
    long_term_stiffness_factor: float = 1.0,
    groundwater_offset_m: float = 0.0,
    max_iterations: int = 8,
    force_tolerance: float = 0.02,
    displacement_tolerance: float = 0.01,
    relaxation: float = 1.0,
) -> dict[str, Any]:
    """Iterate wall/wale reactions with transfer-frame far-end stiffness.

    Each iteration obtains the tangent stiffness of the transfer frame at every
    radial support, combines it in series with the support/wale spring, resolves
    the wall–wale system, and feeds the updated support reactions back into the
    transfer frame.  The method is deliberately explicit and records every
    residual so a reviewer can judge convergence.
    """
    stage_by_id = {str(stage.id): stage for stage in case.stages}
    segment_by_id = {str(segment.id): segment for segment in project.excavation.segments}
    wall_by_segment = {str(wall.segment_id): wall for wall in project.retaining_system.diaphragm_walls}
    supports_by_id = {str(support.id): support for support in project.retaining_system.supports}
    force_maps = _stage_force_maps(stage_results)
    previous_displacements = {
        f"{result.stage_id}:{result.segment_id}": float(result.global_coupled_result.max_wall_displacement if result.global_coupled_result else 0.0)
        for result in stage_results
    }
    history: list[dict[str, Any]] = []
    stage_analyses: dict[str, dict[str, Any]] = {}
    current_relaxation = min(1.0, max(0.15, float(relaxation)))
    relaxation_history: list[float] = []
    two_iterations_back: dict[str, dict[str, float]] | None = None
    previous_force_delta: float | None = None
    previous_displacement_delta: float | None = None
    stagnation_count = 0
    oscillation_detected = False

    for iteration in range(1, max_iterations + 1):
        previous_force_maps = {stage_id: dict(values) for stage_id, values in force_maps.items()}
        endpoint_stiffness_by_stage: dict[str, dict[str, float]] = {}
        failed_stage_ids: list[str] = []
        for stage_id, force_map in force_maps.items():
            analysis = analyze_transfer_frame_system(
                project.retaining_system,
                support_force_overrides=force_map,
                stage_id=stage_id,
                stage_name=getattr(stage_by_id.get(stage_id), "name", None),
                run_sensitivity=False,
                allow_screening_regularization=False,
            )
            stage_analyses[stage_id] = analysis
            if analysis.get("status") == "fail":
                failed_stage_ids.append(stage_id)
                continue
            endpoint_stiffness_by_stage[stage_id] = {
                str(support_id): float(item.get("stiffnessKnPerM") or 0.0)
                for support_id, item in (analysis.get("endpointStiffness") or {}).items()
                if item.get("status") in {"pass", "warning"} and float(item.get("stiffnessKnPerM") or 0.0) > 0.0
            }
        if failed_stage_ids:
            return {
                "schema": "pitguard-wall-wale-transfer-iteration-v1",
                "status": "fail",
                "converged": False,
                "iterationCount": iteration,
                "failedStageIds": failed_stage_ids,
                "history": history,
                "stageAnalyses": stage_analyses,
                "message": "转接框架在反力迭代中触发数值阻断，迭代结果不得用于设计。",
            }

        current_displacements: dict[str, float] = {}
        updated_maps: dict[str, dict[str, float]] = {stage_id: dict(values) for stage_id, values in force_maps.items()}
        for result in stage_results:
            stage_id = str(result.stage_id)
            segment_id = str(result.segment_id)
            stage = stage_by_id.get(stage_id)
            segment = segment_by_id.get(segment_id)
            if stage is None or segment is None:
                continue
            wall = wall_by_segment.get(segment_id)
            section = extract_representative_section(project, segment_id)
            top = float(project.excavation.top_elevation)
            bottom = float(project.excavation.bottom_elevation)
            final_depth = top - bottom
            stage_depth = min(final_depth, max(0.0, top - float(stage.excavation_elevation))) or final_depth
            active_ids = {str(item) for item in stage.active_support_ids or []} - {str(item) for item in stage.deactivated_support_ids or []}
            transferred_levels = {int(level) for level in stage.transferred_support_levels or []}
            active_supports = [
                support for support in project.retaining_system.supports
                if (
                    str(support.id) in active_ids
                    or int(support.level_index or 0) in transferred_levels
                )
                and segment.name in {support.start_face_code, support.end_face_code}
            ]
            if not active_supports:
                continue
            far_end = endpoint_stiffness_by_stage.get(stage_id, {})
            raw_global = solve_global_wall_wale_support_system(
                pressure_profile=result.pressure_profile,
                segment=segment,
                face_code=segment.name,
                active_supports=active_supports,
                top_elevation=top,
                excavation_elevation=top - stage_depth,
                wall_bottom_elevation=float(wall.bottom_elevation if wall else bottom - max(4.0, 0.35 * final_depth)),
                wall_thickness=float(wall.thickness if wall else 1.0),
                concrete_grade=str(wall.concrete_grade if wall else "C35"),
                soil_profile=section.layers,
                stage_id=stage_id,
                stage_type=stage.stage_type,
                wall_stiffness_factor=wall_stiffness_factor,
                soil_modulus_factor=soil_modulus_factor,
                support_stiffness_factor=support_stiffness_factor,
                replacement_slab_properties={
                    "effectiveWidthM": project.design_settings.replacement_slab_effective_width_m,
                    "thicknessM": project.design_settings.replacement_slab_thickness_m,
                    "elasticModulusMpa": project.design_settings.replacement_slab_elastic_modulus_mpa,
                    "connectionReduction": project.design_settings.replacement_connection_reduction,
                    "transferLengthM": float(segment.length),
                },
                wale_stiffness_factor=float(project.design_settings.wale_cracked_stiffness_factor),
                joint_translational_factor=float(project.design_settings.joint_translational_stiffness_factor),
                joint_rotational_factor=float(project.design_settings.joint_rotational_stiffness_factor),
                rigid_zone_length_factor=float(project.design_settings.rigid_zone_length_factor),
                initial_imperfection_ratio=float(project.design_settings.initial_imperfection_ratio),
                long_term_stiffness_factor=long_term_stiffness_factor,
                support_far_end_stiffness_by_id=far_end,
            )
            coupled = GlobalCoupledSystemResult(**raw_global)
            result.global_coupled_result = coupled
            result.coupled_system_result["wallWaleTransferIteration"] = {
                "iteration": iteration,
                "farEndSupportCount": len(far_end),
                "maximumScaledConditionNumber": raw_global.get("scaledConditionNumber"),
                "conditionGrade": raw_global.get("conditionGrade"),
                "blocked": raw_global.get("illConditionedBlocked"),
            }
            current_displacements[f"{stage_id}:{segment_id}"] = float(coupled.max_wall_displacement or 0.0)
            effective_springs = {
                str(reaction.support_id): float(reaction.spring_stiffness)
                for reaction in coupled.support_reactions
                if float(reaction.spring_stiffness or 0.0) > 0.0
            }
            gw_out = (stage.groundwater_level_outside if stage.groundwater_level_outside is not None else project.design_settings.groundwater_level) + groundwater_offset_m
            gw_in = stage.groundwater_level_inside if stage.groundwater_level_inside is not None else project.design_settings.groundwater_level
            raw_wall = analyze_wall_on_elastic_foundation(
                soil_profile=section.layers,
                supports=[support for support in active_supports if str(support.id) in active_ids],
                excavation_depth=stage_depth,
                groundwater_level_outside=gw_out,
                groundwater_level_inside=gw_in,
                surcharge=stage.surcharge,
                top_elevation=top,
                wall_bottom_elevation=float(wall.bottom_elevation if wall else bottom - max(4.0, 0.35 * final_depth)),
                wall_thickness=float(wall.thickness if wall else 1.0),
                concrete_grade=str(wall.concrete_grade if wall else "C35"),
                segment=segment,
                transferred_supports=[support for support in active_supports if str(support.id) not in active_ids],
                wall_stiffness_factor=wall_stiffness_factor,
                soil_modulus_factor=soil_modulus_factor,
                support_stiffness_factor=support_stiffness_factor,
                support_stiffness_overrides_kn_per_m=effective_springs,
            )
            result.wall_internal_force = _wall_force_result(segment_id, stage_id, raw_wall, gamma0)
            result.wall_internal_force_placeholder.update({
                "coupledIteration": iteration,
                "maxMoment": raw_wall.get("maxMoment"),
                "maxShear": raw_wall.get("maxShear"),
                "maxDisplacement": raw_wall.get("maxDisplacement"),
                "supportStiffnessOverrideCount": len(effective_springs),
            })
            reaction_force = {
                str(reaction.support_id): float(reaction.axial_force or 0.0)
                for reaction in coupled.support_reactions
                if str(reaction.support_id) in supports_by_id
            }
            target_map = updated_maps.setdefault(stage_id, {})
            for support_id, new_force in reaction_force.items():
                old_force = float(target_map.get(support_id, new_force) or new_force)
                relaxed = (1.0 - current_relaxation) * old_force + current_relaxation * new_force
                target_map[support_id] = max(relaxed, 0.0)
                for force in result.support_forces:
                    if str(force.support_id) == support_id:
                        force.axial_force = round(max(relaxed, 0.0), 3)
                        if force.axial_force_design is not None:
                            force.axial_force_design = round(max(float(force.axial_force_design), relaxed * gamma0 * 1.25), 3)
                        force.distribution_method = "wall-wale-transfer fixed-point iteration"
                        force.distribution_note = f"第 {iteration} 次迭代，松弛系数 {current_relaxation:.2f}，远端框架刚度已进入串联支承。"

        force_delta = max(
            (_max_relative_delta(updated_maps.get(stage_id, {}), previous_force_maps.get(stage_id, {})) for stage_id in updated_maps),
            default=0.0,
        )
        force_absolute_delta = max(
            (_max_absolute_delta(updated_maps.get(stage_id, {}), previous_force_maps.get(stage_id, {})) for stage_id in updated_maps),
            default=0.0,
        )
        displacement_delta = _max_relative_delta(current_displacements, previous_displacements)
        displacement_absolute_delta = _max_absolute_delta(current_displacements, previous_displacements)
        cycle_delta = _nested_relative_delta(updated_maps, two_iterations_back) if two_iterations_back is not None else math.inf
        cycle_detected = bool(
            two_iterations_back is not None
            and cycle_delta <= max(force_tolerance * 0.5, 1.0e-4)
            and force_delta > force_tolerance
        )
        oscillation_detected = oscillation_detected or cycle_detected
        relaxation_history.append(round(current_relaxation, 6))
        history.append({
            "iteration": iteration,
            "maximumForceRelativeChange": float(f"{force_delta:.6e}"),
            "maximumForceAbsoluteChangeKn": round(force_absolute_delta, 6),
            "maximumDisplacementRelativeChange": float(f"{displacement_delta:.6e}"),
            "maximumDisplacementAbsoluteChangeMm": round(displacement_absolute_delta, 6),
            "relaxation": round(current_relaxation, 6),
            "twoCycleRelativeChange": None if not math.isfinite(cycle_delta) else float(f"{cycle_delta:.6e}"),
            "oscillationDetected": cycle_detected,
            "stageCount": len(stage_analyses),
        })
        converged = force_delta <= force_tolerance and displacement_delta <= displacement_tolerance
        if converged:
            force_maps = updated_maps
            convergence_quality = "strong" if force_delta <= force_tolerance * 0.25 and displacement_delta <= displacement_tolerance * 0.25 else "acceptable"
            return {
                "schema": "pitguard-wall-wale-transfer-iteration-v2",
                "status": "pass",
                "converged": True,
                "convergenceQuality": convergence_quality,
                "iterationCount": iteration,
                "forceTolerance": force_tolerance,
                "displacementTolerance": displacement_tolerance,
                "initialRelaxation": relaxation,
                "finalRelaxation": current_relaxation,
                "relaxationHistory": relaxation_history,
                "oscillationDetected": oscillation_detected,
                "stagnationDetected": False,
                "finalForceRelativeResidual": float(history[-1]["maximumForceRelativeChange"]),
                "finalForceAbsoluteResidualKn": float(history[-1]["maximumForceAbsoluteChangeKn"]),
                "finalDisplacementRelativeResidual": float(history[-1]["maximumDisplacementRelativeChange"]),
                "finalDisplacementAbsoluteResidualMm": float(history[-1]["maximumDisplacementAbsoluteChangeMm"]),
                "history": history,
                "finalForceMaps": force_maps,
                "stageAnalyses": stage_analyses,
                "message": "墙—围檩—转接框架反力与位移自适应松弛迭代收敛。",
            }

        residual_worsened = (
            previous_force_delta is not None
            and previous_displacement_delta is not None
            and (force_delta > previous_force_delta * 1.20 or displacement_delta > previous_displacement_delta * 1.20)
        )
        residual_improved = (
            previous_force_delta is not None
            and previous_displacement_delta is not None
            and force_delta < previous_force_delta * 0.70
            and displacement_delta <= max(previous_displacement_delta * 0.90, displacement_tolerance)
        )
        if cycle_detected or residual_worsened:
            current_relaxation = max(0.15, current_relaxation * 0.5)
        elif residual_improved:
            current_relaxation = min(1.0, current_relaxation * 1.15)

        if previous_force_delta is not None and previous_displacement_delta is not None:
            force_progress = abs(previous_force_delta - force_delta) / max(previous_force_delta, 1.0e-12)
            displacement_progress = abs(previous_displacement_delta - displacement_delta) / max(previous_displacement_delta, 1.0e-12)
            stagnation_count = stagnation_count + 1 if force_progress < 0.02 and displacement_progress < 0.02 else 0
        if stagnation_count >= 3 and current_relaxation <= 0.2:
            return {
                "schema": "pitguard-wall-wale-transfer-iteration-v2",
                "status": "fail",
                "converged": False,
                "stagnationDetected": True,
                "oscillationDetected": oscillation_detected,
                "iterationCount": iteration,
                "forceTolerance": force_tolerance,
                "displacementTolerance": displacement_tolerance,
                "initialRelaxation": relaxation,
                "finalRelaxation": current_relaxation,
                "relaxationHistory": relaxation_history,
                "history": history,
                "finalForceMaps": updated_maps,
                "stageAnalyses": stage_analyses,
                "message": "反力迭代残差持续停滞，已提前阻断，避免以伪收敛结果进入设计。",
            }
        two_iterations_back = previous_force_maps
        force_maps = updated_maps
        previous_displacements = current_displacements
        previous_force_delta = force_delta
        previous_displacement_delta = displacement_delta

    return {
        "schema": "pitguard-wall-wale-transfer-iteration-v2",
        "status": "fail",
        "converged": False,
        "iterationCount": max_iterations,
        "forceTolerance": force_tolerance,
        "displacementTolerance": displacement_tolerance,
        "initialRelaxation": relaxation,
        "finalRelaxation": current_relaxation,
        "relaxationHistory": relaxation_history,
        "oscillationDetected": oscillation_detected,
        "stagnationDetected": stagnation_count >= 3,
        "finalForceRelativeResidual": float(history[-1]["maximumForceRelativeChange"]) if history else None,
        "finalForceAbsoluteResidualKn": float(history[-1]["maximumForceAbsoluteChangeKn"]) if history else None,
        "finalDisplacementRelativeResidual": float(history[-1]["maximumDisplacementRelativeChange"]) if history else None,
        "finalDisplacementAbsoluteResidualMm": float(history[-1]["maximumDisplacementAbsoluteChangeMm"]) if history else None,
        "history": history,
        "finalForceMaps": force_maps,
        "stageAnalyses": stage_analyses,
        "message": "墙—围檩—转接框架反力迭代未在最大迭代次数内收敛，已阻断正式设计资格。",
    }
