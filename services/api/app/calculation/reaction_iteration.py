from __future__ import annotations

import math
from typing import Any

from app.calculation.stage_loads import build_stage_loads
from app.calculation.stage_state import build_stage_states
from app.calculation.stage_solver import solve_stage_response
from app.services.wall_restraint import build_effective_wall_restraints
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


def _displacement_deltas(current_m: dict[str, float], previous_m: dict[str, float]) -> tuple[float, float]:
    """Relative displacement change with a 1 mm floor; absolute change in mm."""
    current_mm = {key: value * 1000.0 for key, value in current_m.items()}
    previous_mm = {key: value * 1000.0 for key, value in previous_m.items()}
    return _max_relative_delta(current_mm, previous_mm), _max_absolute_delta(current_mm, previous_mm)


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
    stage_load_records: dict | None = None,
    stage_states: dict | None = None,
) -> dict[str, Any]:
    """Iterate wall/wale reactions with transfer-frame far-end stiffness.

    Each iteration obtains the tangent stiffness of the transfer frame at every
    radial support, combines it in series with the support/wale spring, resolves
    the wall–wale system, and feeds the updated support reactions back into the
    transfer frame.  The method is deliberately explicit and records every
    residual so a reviewer can judge convergence.
    """
    states = stage_states if stage_states is not None else build_stage_states(project, case)
    load_records = stage_load_records if stage_load_records is not None else {}
    stage_by_id = {str(stage.id): stage for stage in case.stages}
    segment_by_id = {str(segment.id): segment for segment in project.excavation.segments}
    wall_by_segment = {str(wall.segment_id): wall for wall in project.retaining_system.diaphragm_walls}
    supports_by_id = {str(support.id): support for support in project.retaining_system.supports}
    force_maps = _stage_force_maps(stage_results)
    previous_displacements = {
        f"{result.stage_id}:{result.segment_id}": float(result.global_coupled_result.max_wall_displacement)
        for result in stage_results
        if result.global_coupled_result and result.global_coupled_result.max_wall_displacement is not None
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
                "schema": "pitguard-wall-wale-transfer-iteration-v2",
                "status": "fail",
                "converged": False,
                "iterationCount": iteration,
                "failedStageIds": failed_stage_ids,
                "history": history,
                "stageAnalyses": stage_analyses,
                "message": "转接框架在反力迭代中触发数值阻断，迭代结果不得用于设计。",
            }

        current_displacements: dict[str, float] = {}
        updated_maps: dict[str, dict[str, float]] = {stage_id: {} for stage_id in force_maps}
        solved_maps: dict[str, dict[str, float]] = {stage_id: {} for stage_id in force_maps}
        for result in stage_results:
            stage_id, segment_id = str(result.stage_id), str(result.segment_id)
            stage, segment = stage_by_id[stage_id], segment_by_id[segment_id]
            wall = wall_by_segment[segment_id]
            state = states[stage_id]
            section = extract_representative_section(project, segment_id)
            top = float(project.excavation.top_elevation)
            loads = load_records.get((stage_id, segment_id))
            if loads is None:
                gw_out = (stage.groundwater_level_outside if stage.groundwater_level_outside is not None else project.design_settings.groundwater_level) + groundwater_offset_m
                gw_in = stage.groundwater_level_inside
                if gw_in is None:
                    gw_in = project.design_settings.groundwater_level_inside
                if gw_in is None:
                    gw_in = gw_out
                loads = build_stage_loads(section.layers, state["excavationDepthM"], gw_out, gw_in,
                    stage.surcharge, top, top-wall.bottom_elevation, project.design_settings)
                load_records[(stage_id, segment_id)] = loads
            active = [supports_by_id[sid] for sid in state["activeSupportIds"]]
            segment_supports = [s for s in active if segment.name in {s.start_face_code, s.end_face_code}]
            corners, restraint_audit = build_effective_wall_restraints(project.excavation, segment, active,
                target_spacing_m=float(project.design_settings.default_support_spacing or 5.0))
            far_end = endpoint_stiffness_by_stage.get(stage_id, {})
            solved = solve_stage_response(project, segment, stage, section, wall, loads, state,
                segment_supports=segment_supports, corner_transfer_supports=corners,
                wall_stiffness_factor=wall_stiffness_factor, soil_modulus_factor=soil_modulus_factor,
                support_stiffness_factor=support_stiffness_factor, long_term_stiffness_factor=long_term_stiffness_factor,
                far_end_stiffness=far_end, iteration=iteration)
            result.pressure_profile, result.design_pressure_profile = loads.standard_profile, loads.design_profile
            result.load_evidence = {**loads.evidence, "consumers": solved["evidence"]}
            result.stage_state, result.solve_id = state, solved["solve_id"]
            result.global_coupled_result, result.design_global_coupled_result = solved["global"], solved["global_design"]
            result.wall_internal_force = solved["wall"]
            result.support_forces, result.wale_beam_results = solved["forces"], solved["wales"]
            result.coupled_system_result.update(solveEvidence=solved["evidence"], wallRestraintAudit=restraint_audit)
            result.coupled_system_result["wallWaleTransferIteration"] = {
                "iteration": iteration, "farEndSupportCount": len(far_end),
                "maximumScaledConditionNumber": solved["global"].scaled_condition_number,
                "blocked": solved["global"].ill_conditioned_blocked,
            }
            result.wall_internal_force_placeholder = {
                "status": "calculated" if solved["wall"].max_displacement is not None else "partial",
                "coupledIteration": iteration, "solveId": solved["solve_id"], "loadRecordId": loads.load_id,
                "maxMoment": solved["wall"].max_moment, "maxShear": solved["wall"].max_shear,
                "maxDisplacement": solved["wall"].max_displacement,
                "maxMomentDesign": solved["wall"].max_moment_design, "maxShearDesign": solved["wall"].max_shear_design,
                "supportReactions": solved["wall_raw"].get("supportReactions", []),
                "points": solved["wall_raw"].get("points", []),
            }
            displacement = solved["global"].max_wall_displacement
            if displacement is None or solved["global"].fallback or solved["global_design"].fallback or any(
                evidence["status"] != "solved" for evidence in solved["evidence"]["wallSolvers"].values()
            ):
                return {"schema": "pitguard-wall-wale-transfer-iteration-v2", "status": "fail", "converged": False,
                    "iterationCount": iteration, "history": history, "failedStageIds": [stage_id],
                    "message": "最终标准或设计作用求解不可用，未按零位移/零反力判为收敛。"}
            current_displacements[f"{stage_id}:{segment_id}"] = float(displacement)
            for force in result.support_forces:
                sid = str(force.support_id)
                value = float(force.axial_force)
                solved_maps[stage_id][sid] = max(solved_maps[stage_id].get(sid, 0.0), value)
        # Relaxation is an iteration variable only. Published results always
        # retain the actual reactions of the last solved displacement vector.
        for stage_id, values in solved_maps.items():
            for sid, value in values.items():
                old = previous_force_maps.get(stage_id, {}).get(sid, value)
                updated_maps[stage_id][sid] = (1-current_relaxation)*old + current_relaxation*value

        force_delta = max(
            (_max_relative_delta(solved_maps.get(stage_id, {}), previous_force_maps.get(stage_id, {})) for stage_id in updated_maps),
            default=0.0,
        )
        force_absolute_delta = max(
            (_max_absolute_delta(solved_maps.get(stage_id, {}), previous_force_maps.get(stage_id, {})) for stage_id in updated_maps),
            default=0.0,
        )
        displacement_delta, displacement_absolute_delta = _displacement_deltas(current_displacements, previous_displacements)
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
            force_maps = solved_maps
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
