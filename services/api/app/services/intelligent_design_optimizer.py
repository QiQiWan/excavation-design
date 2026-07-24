from __future__ import annotations

import copy
import gc
import math
from collections import Counter
from typing import Any, Callable

from app.schemas.domain import CalculationCase, CalculationResult, Project
from app.services.intelligent_design_closure import apply_intervention_action, run_intelligent_design_closure


_SEARCH_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "id": "current_balanced",
        "label": "当前体系闭环",
        "strategy": "balanced",
        "seedPasses": 0,
        "wallIncrementM": 0.0,
        "beamIncrementM": 0.0,
        "supportFirst": False,
        "topologyMode": "none",
    },
    {
        "id": "economic_zoned",
        "label": "经济分区增强",
        "strategy": "economic_zoned",
        "seedPasses": 1,
        "wallIncrementM": 0.05,
        "beamIncrementM": 0.05,
        "supportFirst": False,
        "topologyMode": "none",
    },
    {
        "id": "stiffness_first",
        "label": "整体刚度优先",
        "strategy": "stiffness_first",
        "seedPasses": 2,
        "wallIncrementM": 0.15,
        "beamIncrementM": 0.15,
        "supportFirst": True,
        "topologyMode": "none",
    },
    {
        "id": "section_first",
        "label": "控制截面增强",
        "strategy": "section_first",
        "seedPasses": 2,
        "wallIncrementM": 0.10,
        "beamIncrementM": 0.15,
        "supportFirst": True,
        "topologyMode": "none",
    },
    {
        "id": "support_densified",
        "label": "平面支撑加密",
        "strategy": "stiffness_first",
        "seedPasses": 1,
        "wallIncrementM": 0.05,
        "beamIncrementM": 0.10,
        "supportFirst": True,
        "topologyMode": "densify",
    },
    {
        "id": "extra_support_level",
        "label": "增设控制支撑层",
        "strategy": "stiffness_first",
        "seedPasses": 1,
        "wallIncrementM": 0.10,
        "beamIncrementM": 0.10,
        "supportFirst": True,
        "topologyMode": "add_level",
    },
    {
        "id": "combined_system_upgrade",
        "label": "支撑层与截面联合增强",
        "strategy": "section_first",
        "seedPasses": 2,
        "wallIncrementM": 0.15,
        "beamIncrementM": 0.15,
        "supportFirst": True,
        "topologyMode": "add_level_densify",
    },
)


def _latest_closure(project: Project) -> dict[str, Any]:
    if not project.calculation_results:
        return {}
    latest = project.calculation_results[-1]
    return dict((latest.design_iteration_summary or {}).get("intelligentDesignClosure") or {})


def _search_trial(project: Project) -> Project:
    trial = project.model_copy(deep=True)
    trial.calculation_results = []
    trial.advanced_engineering = copy.deepcopy(project.advanced_engineering or {})
    trial.advanced_engineering.pop("calculationOptimizationSearch", None)
    return trial


def _polyline_length(polyline: Any) -> float:
    points = list(getattr(polyline, "points", []) or [])
    if len(points) < 2:
        return 0.0
    return sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(points[:-1], points[1:]))


def _member_quantities(project: Project) -> dict[str, float]:
    ret = project.retaining_system
    if ret is None:
        return {"wallConcreteM3": 0.0, "beamConcreteM3": 0.0, "supportIndex": 0.0, "totalIndex": 0.0}
    wall_volume = 0.0
    for wall in ret.diaphragm_walls:
        height = max(float(wall.top_elevation) - float(wall.bottom_elevation), 0.0)
        wall_volume += _polyline_length(wall.axis) * max(float(wall.thickness), 0.0) * height
    beam_volume = 0.0
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        beam_volume += (
            _polyline_length(beam.axis)
            * max(float(beam.section.width or 0.0), 0.0)
            * max(float(beam.section.height or 0.0), 0.0)
        )
    support_index = 0.0
    for support in ret.supports:
        length = float(support.span_length or math.hypot(support.end.x - support.start.x, support.end.y - support.start.y))
        if support.section_type == "steel_pipe":
            diameter = max(float(support.section.diameter or 0.0), 0.0)
            thickness = max(float(support.section.wall_thickness or 0.0), 0.0)
            area = math.pi * max(diameter * thickness - thickness * thickness, 0.0)
        else:
            area = max(float(support.section.width or 0.0), 0.0) * max(float(support.section.height or 0.0), 0.0)
        support_index += length * area
    return {
        "wallConcreteM3": round(wall_volume, 4),
        "beamConcreteM3": round(beam_volume, 4),
        "supportIndex": round(support_index, 4),
        "totalIndex": round(wall_volume + beam_volume + support_index, 4),
    }


def _closure_deficit(closure: dict[str, Any]) -> float:
    deficit = 0.0
    for row in list(closure.get("remainingChecks") or []):
        factor = row.get("safetyFactor")
        target = row.get("targetSafetyFactor")
        if factor is None or target is None:
            continue
        deficit += max(float(target) - float(factor), 0.0)
    return round(deficit, 6)


def _result_displacement(result: CalculationResult) -> float:
    governing = result.governing_values
    return float(getattr(governing, "max_displacement", 0.0) or 0.0) if governing else 0.0


def _quality_vector(closure: dict[str, Any], result: CalculationResult, quantity_delta: float) -> tuple[float, ...]:
    return (
        0.0 if bool(closure.get("calculationClosed")) else 1.0,
        0.0 if bool(closure.get("structuralClosed")) else 1.0,
        float(int(closure.get("hardFailCount") or 0)),
        float(int(closure.get("structuralFailCount") or 0)),
        float(int(closure.get("quantitativeOpenCount") or 0)),
        _closure_deficit(closure),
        _result_displacement(result),
        max(quantity_delta, 0.0),
    )


def _scalar_score(vector: tuple[float, ...]) -> float:
    weights = (1.0e10, 1.0e9, 1.0e7, 1.0e6, 1.0e5, 1.0e4, 10.0, 1.0)
    return round(sum(a * b for a, b in zip(vector, weights)), 6)


def _seed_actions(project: Project, profile: dict[str, Any], source_options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ret = project.retaining_system
    if ret is None:
        return []
    walls = {wall.id: wall for wall in ret.diaphragm_walls}
    beams = {beam.id: beam for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]}
    actions: list[dict[str, Any]] = []
    passes = max(0, min(int(profile.get("seedPasses") or 0), 3))
    wall_increment = float(profile.get("wallIncrementM") or 0.10)
    beam_increment = float(profile.get("beamIncrementM") or 0.10)

    if profile.get("supportFirst"):
        try:
            actions.append({"profileSeed": True, **apply_intervention_action(project, "optimize-supports")})
        except ValueError:
            pass

    for _ in range(passes):
        applied_ids: set[str] = set()
        for option in source_options:
            action_id = str(option.get("actionId") or "")
            if not action_id or action_id in applied_ids or option.get("automaticAllowed") is False:
                continue
            try:
                if action_id.startswith("strengthen-wall:"):
                    object_id = action_id.split(":", 1)[1]
                    wall = walls.get(object_id)
                    if wall is None:
                        continue
                    applied = apply_intervention_action(project, action_id, min(2.40, float(wall.thickness) + wall_increment))
                elif action_id.startswith("strengthen-beam:"):
                    object_id = action_id.split(":", 1)[1]
                    beam = beams.get(object_id)
                    if beam is None:
                        continue
                    applied = apply_intervention_action(project, action_id, {
                        "widthM": float(beam.section.width or 0.8) + beam_increment,
                        "heightM": float(beam.section.height or 0.8) + beam_increment,
                    })
                elif action_id.startswith("deepen-wall:"):
                    applied = apply_intervention_action(project, action_id)
                elif action_id == "optimize-supports" and not profile.get("supportFirst"):
                    applied = apply_intervention_action(project, action_id)
                else:
                    continue
            except ValueError:
                continue
            applied_ids.add(action_id)
            actions.append({"profileSeed": True, **applied})
    return actions


def _support_depths(project: Project) -> list[float]:
    if project.excavation is None or project.retaining_system is None:
        return []
    top = float(project.excavation.top_elevation)
    by_level: dict[int, list[float]] = {}
    for support in project.retaining_system.supports:
        by_level.setdefault(int(support.level_index), []).append(top - float(support.elevation))
    return sorted(round(sum(values) / len(values), 3) for values in by_level.values() if values)


def _normalize_pathological_vertical_levels(project: Project) -> dict[str, Any]:
    """Repair legacy per-member level drift before expensive search.

    The automatic engine supports at most six vertical levels. More levels in a
    non-locked automatic scheme indicate corrupted persistence or millimetric
    drift. Regenerate the same topology family on clustered level depths.
    """
    if project.excavation is None or project.retaining_system is None:
        return {"changed": False, "reason": "missing excavation or retaining system"}
    before = _support_depths(project)
    if len(before) <= 6:
        return {"changed": False, "reason": "level count within automatic boundary", "beforeDepthsM": before}
    if project.retaining_system.optimization_locks or any(
        support.optimization_locked for support in project.retaining_system.supports
    ):
        return {
            "changed": False,
            "manualRequired": True,
            "reason": "pathological level count is locked by the designer",
            "beforeDepthsM": before,
        }
    from app.services.design_service import (
        _sanitize_support_level_depths,
        auto_supports,
        support_layout_config_from_settings,
    )
    from app.services.workflow_v381 import repair_design_control_support_references

    excavation_depth = float(project.excavation.top_elevation) - float(project.excavation.bottom_elevation)
    normalized, audit = _sanitize_support_level_depths(before, excavation_depth)
    families = Counter(str(row.topology_family or "direct_grid") for row in project.retaining_system.supports)
    family = families.most_common(1)[0][0] if families else "direct_grid"
    strategy = family if family in {"direct_grid", "hybrid_diagonal", "bidirectional_grid", "ring_radial", "zoned_direct"} else "balanced_grid"
    project.design_settings.support_level_depths_m = list(normalized)
    config = support_layout_config_from_settings(
        project.design_settings,
        topology_strategy=strategy,
        target_spacing=float(project.design_settings.default_support_spacing or 5.0),
    )
    before_count = len(project.retaining_system.supports)
    project.retaining_system = auto_supports(project.excavation, project.retaining_system, config)
    project.design_settings.support_level_depths_m = _support_depths(project)
    repair = repair_design_control_support_references(project, allow_standard_transfer_rebuild=True)
    return {
        "changed": True,
        "actionId": "normalize-pathological-support-levels",
        "reason": "legacy support elevations produced more than six automatic levels",
        "beforeDepthsM": before,
        "afterDepthsM": _support_depths(project),
        "beforeSupportCount": before_count,
        "afterSupportCount": len(project.retaining_system.supports),
        "topologyFamily": strategy,
        "sanitization": audit,
        "designControlRepair": repair,
    }


def _insert_support_depth(project: Project) -> float | None:
    if project.excavation is None:
        return None
    depth = max(float(project.excavation.top_elevation) - float(project.excavation.bottom_elevation), 0.0)
    current = _support_depths(project)
    if len(current) >= 6 or depth < 5.0:
        return None
    lower_bound = 1.5
    upper_bound = max(lower_bound, depth - 0.75)
    anchors = [0.0, *current, depth]
    gaps = sorted(
        ((anchors[index + 1] - anchors[index], anchors[index], anchors[index + 1]) for index in range(len(anchors) - 1)),
        reverse=True,
    )
    for gap, left, right in gaps:
        if gap < 3.0:
            continue
        candidate = round(max(lower_bound, min(upper_bound, (left + right) / 2.0)), 3)
        if all(abs(candidate - item) >= 1.5 for item in current):
            return candidate
    return None


def _apply_topology_profile(project: Project, profile: dict[str, Any]) -> list[dict[str, Any]]:
    mode = str(profile.get("topologyMode") or "none")
    if mode == "none" or project.excavation is None or project.retaining_system is None:
        return []
    from app.services.design_service import auto_supports, support_layout_config_from_settings
    from app.services.workflow_v381 import repair_design_control_support_references

    before_count = len(project.retaining_system.supports)
    before_levels = _support_depths(project)
    current_spacing = float(project.design_settings.default_support_spacing or 5.0)
    target_spacing = current_spacing
    depths = list(before_levels)
    if mode in {"densify", "add_level_densify"}:
        target_spacing = max(3.0, round(current_spacing * (0.80 if mode == "add_level_densify" else 0.84), 2))
    inserted = None
    if mode in {"add_level", "add_level_densify"}:
        inserted = _insert_support_depth(project)
        if inserted is not None:
            depths = sorted({*depths, inserted})
    project.design_settings.default_support_spacing = target_spacing
    project.design_settings.support_level_depths_m = depths
    config = support_layout_config_from_settings(
        project.design_settings,
        topology_strategy="balanced_grid",
        target_spacing=target_spacing,
    )
    project.retaining_system = auto_supports(project.excavation, project.retaining_system, config)
    project.design_settings.support_level_depths_m = _support_depths(project)
    stage_repair = repair_design_control_support_references(
        project,
        allow_standard_transfer_rebuild=True,
    )
    return [{
        "profileSeed": True,
        "actionId": f"topology:{mode}",
        "objectCode": "support-system",
        "changed": before_count != len(project.retaining_system.supports) or before_levels != _support_depths(project),
        "before": {"supportCount": before_count, "levelDepthsM": before_levels, "spacingM": current_spacing},
        "after": {
            "supportCount": len(project.retaining_system.supports),
            "levelDepthsM": _support_depths(project),
            "spacingM": target_spacing,
            "insertedLevelDepthM": inserted,
        },
        "designControlRepair": stage_repair,
    }]


def _case_for_trial(trial: Project, fallback: CalculationCase | None) -> tuple[CalculationCase | None, dict[str, Any]]:
    from app.services.construction_stages import select_calculation_case_for_run
    from app.services.workflow_v381 import repair_design_control_support_references
    from app.calculation.engine import build_default_construction_cases

    repair = repair_design_control_support_references(
        trial,
        allow_standard_transfer_rebuild=True,
    )
    try:
        case, selection = select_calculation_case_for_run(trial)
        selection = dict(selection)
        selection["supportReferenceRepair"] = repair
        return case, selection
    except ValueError as exc:
        # A frozen/user-owned transfer path may remain formally unresolved.
        # Candidate evaluation must still use a current-topology screening case
        # so the optimizer can improve the structural system and return useful
        # evidence instead of producing zero evaluated candidates.
        generated = build_default_construction_cases(trial)[0]
        return generated, {
            "source": "current_topology_transfer_screening",
            "preserved": False,
            "caseId": generated.id,
            "supportReferenceRepair": repair,
            "formalTransferReviewRequired": bool(repair.get("manualRequired")),
            "formalStageError": str(exc),
        }


def _remaining_reason_codes(closure: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for row in list(closure.get("remainingChecks") or []):
        code = str(row.get("ruleId") or row.get("code") or row.get("category") or "UNRESOLVED_CHECK")
        if code not in codes:
            codes.append(code)
    for row in list(closure.get("remainingReviewItems") or []):
        code = str(row.get("code") or row.get("ruleId") or "MANUAL_REVIEW_REQUIRED")
        if code not in codes:
            codes.append(code)
    return codes


def run_bounded_optimization_search(
    project: Project,
    calculation_case: CalculationCase | None = None,
    *,
    max_candidates: int = 7,
    max_iterations: int = 6,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], None] | None = None,
) -> tuple[CalculationResult, dict[str, Any]]:
    """Run a deterministic adaptive search across member and support-system changes."""
    vertical_level_normalization = _normalize_pathological_vertical_levels(project)
    profiles = list(_SEARCH_PROFILES[: max(2, min(int(max_candidates or 7), len(_SEARCH_PROFILES)))])
    baseline_quantity = _member_quantities(project)
    source_options = list(_latest_closure(project).get("interventionOptions") or [])
    candidates: list[dict[str, Any]] = []
    best_overall_trial: Project | None = None
    best_overall_id: str | None = None
    best_overall_vector: tuple[float, ...] | None = None
    best_feasible_trial: Project | None = None
    best_feasible_id: str | None = None
    best_feasible_vector: tuple[float, ...] | None = None

    for index, profile in enumerate(profiles, start=1):
        if cancel_callback is not None:
            cancel_callback()
        candidate_id = f"opt-{profile['id']}-{index}"
        if progress_callback is not None:
            progress_callback(index - 1, len(profiles), {
                "candidateId": candidate_id,
                "profileId": profile["id"],
                "profileLabel": profile["label"],
                "status": "running",
                "candidateProgress": 0.08,
            })
        trial = _search_trial(project)
        try:
            topology_actions = _apply_topology_profile(trial, profile)
            if progress_callback is not None:
                progress_callback(index - 1, len(profiles), {
                    "candidateId": candidate_id,
                    "profileId": profile["id"],
                    "profileLabel": profile["label"],
                    "status": "running",
                    "candidateProgress": 0.30,
                })
            seeds = topology_actions + _seed_actions(trial, profile, source_options)
            trial_case, stage_selection = _case_for_trial(trial, calculation_case)
            if progress_callback is not None:
                progress_callback(index - 1, len(profiles), {
                    "candidateId": candidate_id,
                    "profileId": profile["id"],
                    "profileLabel": profile["label"],
                    "status": "running",
                    "candidateProgress": 0.48,
                })
            result, closure = run_intelligent_design_closure(
                trial,
                trial_case,
                auto_repair=True,
                strategy=str(profile["strategy"]),
                max_iterations=max_iterations,
            )
            quantities = _member_quantities(trial)
            quantity_delta = float(quantities["totalIndex"]) - float(baseline_quantity["totalIndex"])
            vector = _quality_vector(closure, result, quantity_delta)
            row = {
                "candidateId": candidate_id,
                "profileId": profile["id"],
                "profileLabel": profile["label"],
                "strategy": profile["strategy"],
                "topologyMode": profile.get("topologyMode"),
                "status": "evaluated",
                "score": _scalar_score(vector),
                "qualityVector": list(vector),
                "calculationClosed": bool(closure.get("calculationClosed")),
                "structuralClosed": bool(closure.get("structuralClosed")),
                "hardFailCount": int(closure.get("hardFailCount") or 0),
                "structuralFailCount": int(closure.get("structuralFailCount") or 0),
                "quantitativeOpenCount": int(closure.get("quantitativeOpenCount") or 0),
                "safetyDeficit": _closure_deficit(closure),
                "maxDisplacement": _result_displacement(result),
                "quantityProxy": quantities,
                "quantityDelta": round(quantity_delta, 4),
                "seedActions": seeds,
                "closureActions": [
                    action
                    for round_row in list(closure.get("history") or [])
                    for action in list(round_row.get("actions") or [])
                ],
                "executedIterations": int(closure.get("executedIterations") or 0),
                "remainingReasonCodes": _remaining_reason_codes(closure),
                "stageSelection": stage_selection,
            }
            candidates.append(row)
            if best_overall_vector is None or vector < best_overall_vector:
                best_overall_vector = vector
                best_overall_id = candidate_id
                best_overall_trial = trial
            if bool(closure.get("calculationClosed")) and (best_feasible_vector is None or vector < best_feasible_vector):
                best_feasible_vector = vector
                best_feasible_id = candidate_id
                best_feasible_trial = trial
            if trial is best_overall_trial or trial is best_feasible_trial:
                trial = None
        except Exception as exc:
            candidates.append({
                "candidateId": candidate_id,
                "profileId": profile["id"],
                "profileLabel": profile["label"],
                "strategy": profile["strategy"],
                "topologyMode": profile.get("topologyMode"),
                "status": "failed",
                "error": str(exc),
            })
        finally:
            if progress_callback is not None:
                progress_callback(index, len(profiles), candidates[-1])
            if trial is not None:
                del trial
            gc.collect()

    evaluated = [row for row in candidates if row.get("status") == "evaluated"]
    if cancel_callback is not None:
        cancel_callback()
    if not evaluated:
        raise ValueError("所有自动优化候选均未完成计算，请查看任务日志并复核工程输入。")

    feasible = [row for row in evaluated if row.get("calculationClosed")]
    ranked = sorted(evaluated, key=lambda row: tuple(row.get("qualityVector") or [float("inf")]))
    if feasible:
        selected_candidate_id = best_feasible_id
        selected_trial = best_feasible_trial
    else:
        selected_candidate_id = best_overall_id
        selected_trial = best_overall_trial
    if selected_trial is None or selected_candidate_id is None:
        raise ValueError("自动优化候选完成，但无法保留选定方案快照。")
    selected_profile = next(row for row in evaluated if row.get("candidateId") == selected_candidate_id)

    project.retaining_system = copy.deepcopy(selected_trial.retaining_system)
    project.design_control_stages = copy.deepcopy(selected_trial.design_control_stages)
    project.design_settings.default_support_spacing = float(selected_trial.design_settings.default_support_spacing)
    project.design_settings.support_level_depths_m = list(selected_trial.design_settings.support_level_depths_m)
    from app.services.workflow_v381 import repair_design_control_support_references
    from app.services.construction_stages import select_calculation_case_for_run
    stage_repair = repair_design_control_support_references(
        project,
        allow_standard_transfer_rebuild=True,
    )
    try:
        final_case, final_stage_selection = select_calculation_case_for_run(project)
    except ValueError as exc:
        from app.calculation.engine import build_default_construction_cases
        final_case = build_default_construction_cases(project)[0]
        final_stage_selection = {
            "source": "current_topology_transfer_screening",
            "preserved": False,
            "caseId": final_case.id,
            "formalTransferReviewRequired": bool(stage_repair.get("manualRequired")),
            "formalStageError": str(exc),
        }
    final_result, final_closure = run_intelligent_design_closure(
        project,
        final_case,
        auto_repair=True,
        strategy=str(selected_profile.get("strategy") or "balanced"),
        max_iterations=max_iterations,
    )
    final_quantity = _member_quantities(project)
    final_vector = _quality_vector(
        final_closure,
        final_result,
        float(final_quantity["totalIndex"]) - float(baseline_quantity["totalIndex"]),
    )
    rank_by_id = {row["candidateId"]: idx + 1 for idx, row in enumerate(ranked)}
    for row in candidates:
        row["rank"] = rank_by_id.get(row.get("candidateId"))
        row["selected"] = row.get("candidateId") == selected_candidate_id
    best_overall_trial = None
    best_feasible_trial = None
    del selected_trial
    gc.collect()

    calculation_closed = bool(final_closure.get("calculationClosed"))
    formal_transfer_review_required = bool(
        stage_repair.get("manualRequired")
        or final_stage_selection.get("formalTransferReviewRequired")
    )
    closed = bool(calculation_closed and not formal_transfer_review_required)
    outcome_status = (
        "closed"
        if closed
        else "calculated_pending_transfer_review"
        if calculation_closed
        else "cannot_close"
    )
    reason_codes = _remaining_reason_codes(final_closure)
    if formal_transfer_review_required and "DESIGN_CONTROL_TRANSFER_PATH_REVIEW" not in reason_codes:
        reason_codes.append("DESIGN_CONTROL_TRANSFER_PATH_REVIEW")
    summary = {
        "version": "3.87.7-transfer-path-auto-recovery-v1",
        "status": outcome_status,
        "closureOutcome": {
            "status": outcome_status,
            "closed": closed,
            "calculationClosed": calculation_closed,
            "formalTransferReviewRequired": formal_transfer_review_required,
            "reasonCodes": reason_codes,
            "message": (
                "已获得满足当前计算闸门和正式施工阶段合同的闭合方案。"
                if closed
                else "结构筛查计算已闭合，但冻结或用户自定义的换撑路径仍需确认。"
                if calculation_closed
                else "在当前自动优化边界内无法计算闭合；系统已采用最优改进方案并列出剩余控制项。"
            ),
        },
        "method": "有界自适应体系—截面联合搜索",
        "algorithm": "bounded adaptive system-and-section search",
        "objectivePriority": [
            "calculation closure", "structural closure", "hard failures", "structural failures",
            "quantitative reserve gaps", "safety-factor deficit", "maximum displacement", "material growth proxy",
        ],
        "candidateCount": len(candidates),
        "evaluatedCandidateCount": len(evaluated),
        "feasibleCandidateCount": len(feasible),
        "selectedCandidateId": selected_candidate_id,
        "selectedProfile": selected_profile.get("profileLabel"),
        "selectedStrategy": selected_profile.get("strategy"),
        "selectedScore": _scalar_score(final_vector),
        "selectedQualityVector": list(final_vector),
        "bestAvailableApplied": not closed,
        "verticalLevelNormalization": vertical_level_normalization,
        "baselineQuantityProxy": baseline_quantity,
        "finalQuantityProxy": final_quantity,
        "candidates": candidates,
        "designControlSupportRepair": stage_repair,
        "finalStageSelection": final_stage_selection,
        "finalClosure": {
            key: final_closure.get(key)
            for key in (
                "status", "strategy", "executedIterations", "calculationClosed", "structuralClosed",
                "hardFailCount", "structuralFailCount", "quantitativeOpenCount", "reviewCount",
                "reserveShortfallCount", "remainingChecks", "remainingReviewItems", "interventionOptions",
            )
        },
        "engineeringBoundary": "自动搜索可调整墙/梁/支撑截面、平面支撑间距并在最大层数约束内增设一道控制支撑层；支撑ID变化优先按持久化层级语义重绑，标准自下而上换撑序列可自动重建；荷载、土层、水位、测量坐标和冻结的专项换撑决策保持不变。",
    }
    final_result.design_iteration_summary = dict(final_result.design_iteration_summary or {})
    final_result.design_iteration_summary["optimizationSearch"] = summary
    final_result.report_diagram_data = dict(final_result.report_diagram_data or {})
    final_result.report_diagram_data["optimizationSearch"] = summary
    final_result.optimization_actions = list(final_result.optimization_actions or []) + [
        action
        for row in candidates if row.get("selected")
        for action in list(row.get("seedActions") or []) + list(row.get("closureActions") or [])
    ]
    project.advanced_engineering = dict(project.advanced_engineering or {})
    project.advanced_engineering["calculationOptimizationSearch"] = summary
    return final_result, summary
