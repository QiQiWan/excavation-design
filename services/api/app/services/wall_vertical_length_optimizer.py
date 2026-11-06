from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Literal

from app.geology.section import extract_representative_section
from app.rules.jgj120_2012.retaining_wall_rules import check_embedment_stability, required_embedment_factor
from app.schemas.domain import CalculationCase, Project
from app.services.wall_embedment_design import _geology_bottom_elevation, _governing_stage_context

WallLengthMode = Literal["conservative", "balanced", "economic"]

_MODE_MARGIN = {"conservative": 0.15, "balanced": 0.08, "economic": 0.03}


def _mode(value: str | None) -> WallLengthMode:
    return value if value in _MODE_MARGIN else "balanced"  # type: ignore[return-value]


def _wall_plan_length(wall: Any) -> float:
    pts = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    if len(pts) < 2:
        return max(float(getattr(wall, "design_length", 0.0) or 0.0), 0.0)
    return sum(math.hypot(float(b.x - a.x), float(b.y - a.y)) for a, b in zip(pts[:-1], pts[1:]))


def _case(project: Project) -> CalculationCase | None:
    return project.calculation_cases[-1] if project.calculation_cases else None


def _evaluate_wall(project: Project, segment_id: str, wall: Any, bottom: float, case: CalculationCase | None) -> dict[str, Any]:
    if project.excavation is None:
        raise ValueError("Project has no excavation")
    context = _governing_stage_context(project, case)
    section = extract_representative_section(project, segment_id)
    check, trace = check_embedment_stability(
        object_id=wall.id,
        soil_profile=section.layers,
        excavation_depth=context.excavation_depth_m,
        wall_bottom_elevation=float(bottom),
        top_elevation=float(project.excavation.top_elevation),
        groundwater_level_outside=context.groundwater_outside_elevation_m,
        groundwater_level_inside=context.groundwater_inside_elevation_m,
        surcharge=context.surcharge_kpa,
        safety_grade=project.design_settings.safety_grade,
    )
    return {
        "factor": float(check.calculated_value or 0.0),
        "limit": float(check.limit_value or 0.0),
        "status": str(check.status),
        "embedmentDepthM": round(float(trace.get("embedmentDepthM") or 0.0), 3),
        "activeMomentKnMPerM": trace.get("activeMomentKnMPerM"),
        "netPassiveMomentKnMPerM": trace.get("netPassiveMomentKnMPerM"),
    }


def _shallowest_safe_bottom(
    project: Project,
    segment_id: str,
    wall: Any,
    target: float,
    increment: float,
    case: CalculationCase | None,
    *,
    respect_lock: bool = True,
) -> tuple[float, dict[str, Any], int]:
    current = float(wall.bottom_elevation)
    evaluation = _evaluate_wall(project, segment_id, wall, current, case)
    if evaluation["factor"] < target:
        return current, evaluation, 0
    excavation_bottom = float(project.excavation.bottom_elevation) if project.excavation else current
    minimum_embedment = max(3.0, 0.35 * max(float(project.excavation.top_elevation - project.excavation.bottom_elevation), 1.0)) if project.excavation else 3.0
    shallow_limit = excavation_bottom - minimum_embedment
    source = str(getattr(wall, "bottom_elevation_source", "unknown") or "unknown")
    locked = bool(getattr(wall, "bottom_elevation_locked", False)) or source in {"imported", "manual"}
    if locked and respect_lock:
        return current, evaluation, 0
    selected = current
    selected_eval = evaluation
    iterations = 0
    candidate = current
    while candidate + increment <= shallow_limit + 1e-9:
        candidate = round(candidate + increment, 6)
        trial = _evaluate_wall(project, segment_id, wall, candidate, case)
        iterations += 1
        if float(trial["factor"]) + 1e-9 < target:
            break
        selected = candidate
        selected_eval = trial
    return selected, selected_eval, iterations


def _merge_short_runs(rows: list[dict[str, Any]], min_run: float, max_step: float, max_zones: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    runs: list[dict[str, Any]] = []
    for row in rows:
        level = round(float(row["requiredBottomElevationM"]), 3)
        if runs and abs(float(runs[-1]["bottomElevationM"]) - level) <= 0.05:
            runs[-1]["rows"].append(row)
            runs[-1]["planLengthM"] += float(row["planLengthM"])
        else:
            runs.append({"bottomElevationM": level, "rows": [row], "planLengthM": float(row["planLengthM"])})
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for idx, run in enumerate(list(runs)):
            if float(run["planLengthM"]) >= min_run:
                continue
            neighbor_idx = 1 if idx == 0 else len(runs) - 2 if idx == len(runs) - 1 else idx - 1 if abs(float(runs[idx - 1]["bottomElevationM"]) - float(run["bottomElevationM"])) <= abs(float(runs[idx + 1]["bottomElevationM"]) - float(run["bottomElevationM"])) else idx + 1
            neighbor = runs[neighbor_idx]
            # A merged zone adopts the deeper toe to preserve safety.
            neighbor["bottomElevationM"] = min(float(neighbor["bottomElevationM"]), float(run["bottomElevationM"]))
            neighbor["rows"] = (neighbor["rows"] + run["rows"]) if neighbor_idx < idx else (run["rows"] + neighbor["rows"])
            neighbor["planLengthM"] += float(run["planLengthM"])
            runs.pop(idx)
            changed = True
            break
    while len(runs) > max_zones and len(runs) > 1:
        best_idx = min(range(len(runs) - 1), key=lambda i: abs(float(runs[i]["bottomElevationM"]) - float(runs[i + 1]["bottomElevationM"])))
        a, b = runs[best_idx], runs[best_idx + 1]
        merged = {
            "bottomElevationM": min(float(a["bottomElevationM"]), float(b["bottomElevationM"])),
            "rows": a["rows"] + b["rows"],
            "planLengthM": float(a["planLengthM"]) + float(b["planLengthM"]),
        }
        runs[best_idx:best_idx + 2] = [merged]
    for idx in range(1, len(runs)):
        previous = float(runs[idx - 1]["bottomElevationM"])
        current = float(runs[idx]["bottomElevationM"])
        if abs(current - previous) > max_step:
            # Keep the deeper level when the proposed step exceeds the project limit.
            common = min(current, previous)
            runs[idx - 1]["bottomElevationM"] = common
            runs[idx]["bottomElevationM"] = common
    zones: list[dict[str, Any]] = []
    for index, run in enumerate(runs, start=1):
        zone_rows = list(run["rows"])
        zones.append({
            "zoneId": f"WVZ-{index:02d}",
            "bottomElevationM": round(float(run["bottomElevationM"]), 3),
            "planLengthM": round(float(run["planLengthM"]), 3),
            "wallIds": [str(item["wallId"]) for item in zone_rows],
            "wallCodes": [str(item["wallCode"]) for item in zone_rows],
            "segmentIds": [str(item["segmentId"]) for item in zone_rows],
            "minimumFactor": round(min(float(item["optimizedFactor"]) for item in zone_rows), 3),
        })
    return zones


def analyze_wall_vertical_length(project: Project, mode: str = "balanced") -> dict[str, Any]:
    selected_mode = _mode(mode)
    ret = project.retaining_system
    if project.excavation is None or ret is None or not ret.diaphragm_walls:
        return {"projectId": project.id, "status": "manual_review", "message": "缺少基坑或地下连续墙，无法进行竖向墙长优化。", "candidates": []}
    case = _case(project)
    settings = project.design_settings
    limit = required_embedment_factor(settings.safety_grade)
    configured_margin = max(0.0, float(getattr(settings, "wall_vertical_length_target_margin", 0.08) or 0.08))
    target = limit + max(configured_margin, _MODE_MARGIN[selected_mode])
    increment = max(0.1, float(getattr(settings, "wall_embedment_search_increment_m", 0.25) or 0.25))
    walls_by_segment = {wall.segment_id: wall for wall in ret.diaphragm_walls}
    rows: list[dict[str, Any]] = []
    for segment in project.excavation.segments:
        wall = walls_by_segment.get(segment.id)
        if wall is None:
            continue
        current_eval = _evaluate_wall(project, segment.id, wall, float(wall.bottom_elevation), case)
        allow_reference_optimization = bool(getattr(settings, "wall_toe_allow_imported_reference_optimization", False))
        optimized_bottom, optimized_eval, iterations = _shallowest_safe_bottom(
            project, segment.id, wall, target, increment, case,
            respect_lock=not allow_reference_optimization,
        )
        plan_length = _wall_plan_length(wall)
        current_vertical = float(wall.top_elevation) - float(wall.bottom_elevation)
        optimized_vertical = float(wall.top_elevation) - float(optimized_bottom)
        source = str(getattr(wall, "bottom_elevation_source", "unknown") or "unknown")
        locked = bool(getattr(wall, "bottom_elevation_locked", False)) or source in {"imported", "manual"}
        rows.append({
            "segmentId": segment.id,
            "wallId": wall.id,
            "wallCode": wall.panel_code,
            "faceCode": wall.design_face_code or segment.id,
            "planLengthM": round(plan_length, 3),
            "thicknessM": float(wall.thickness),
            "topElevationM": float(wall.top_elevation),
            "currentBottomElevationM": float(wall.bottom_elevation),
            "requiredBottomElevationM": round(float(optimized_bottom), 3),
            "currentVerticalLengthM": round(current_vertical, 3),
            "optimizedVerticalLengthM": round(optimized_vertical, 3),
            "currentFactor": round(float(current_eval["factor"]), 3),
            "optimizedFactor": round(float(optimized_eval["factor"]), 3),
            "targetFactor": round(target, 3),
            "locked": locked,
            "source": source,
            "searchIterations": iterations,
            "potentialShorteningM": round(max(0.0, float(optimized_bottom) - float(wall.bottom_elevation)), 3),
            "currentConcreteVolumeM3": round(plan_length * float(wall.thickness) * current_vertical, 3),
            "optimizedConcreteVolumeM3": round(plan_length * float(wall.thickness) * optimized_vertical, 3),
        })
    common_bottom = min(float(item["requiredBottomElevationM"]) for item in rows) if rows else 0.0
    common_zones = [{
        "zoneId": "WVZ-COMMON",
        "bottomElevationM": round(common_bottom, 3),
        "planLengthM": round(sum(float(item["planLengthM"]) for item in rows), 3),
        "wallIds": [str(item["wallId"]) for item in rows],
        "wallCodes": [str(item["wallCode"]) for item in rows],
        "segmentIds": [str(item["segmentId"]) for item in rows],
        "minimumFactor": round(min(float(item["optimizedFactor"]) for item in rows), 3) if rows else None,
    }]
    zoned = _merge_short_runs(
        rows,
        min_run=max(5.0, float(getattr(settings, "wall_vertical_zone_min_run_m", 20.0) or 20.0)),
        max_step=max(0.5, float(getattr(settings, "wall_vertical_zone_max_step_m", 2.0) or 2.0)),
        max_zones=max(1, int(getattr(settings, "wall_vertical_max_zone_count", 3) or 3)),
    )
    current_volume = sum(float(item["currentConcreteVolumeM3"]) for item in rows)

    def candidate(candidate_id: str, label: str, zones: list[dict[str, Any]], allowed: bool) -> dict[str, Any]:
        zone_by_wall = {wall_id: zone for zone in zones for wall_id in zone["wallIds"]}
        optimized_volume = 0.0
        minimum_factor = 999.0
        locked_conflicts = 0
        factor_cache: dict[tuple[str, float], float] = {}
        for row in rows:
            zone = zone_by_wall.get(str(row["wallId"]))
            bottom = float(zone["bottomElevationM"]) if zone else float(row["currentBottomElevationM"])
            if bool(row["locked"]) and bottom > float(row["currentBottomElevationM"]) + 1e-9:
                locked_conflicts += 1
                bottom = float(row["currentBottomElevationM"])
            vertical = float(row["topElevationM"]) - bottom
            optimized_volume += float(row["planLengthM"]) * float(row["thicknessM"]) * vertical
            key = (str(row["wallId"]), round(bottom, 3))
            if key not in factor_cache:
                wall = walls_by_segment.get(str(row["segmentId"]))
                factor_cache[key] = float(_evaluate_wall(project, str(row["segmentId"]), wall, bottom, case)["factor"]) if wall is not None else 0.0
            minimum_factor = min(minimum_factor, factor_cache[key])
        saving = max(0.0, current_volume - optimized_volume)
        step_count = max(0, len(zones) - 1)
        complexity_penalty = step_count * 8.0 + locked_conflicts * 25.0
        score = 100.0 + saving / max(current_volume, 1.0) * 100.0 - complexity_penalty
        return {
            "candidateId": candidate_id,
            "label": label,
            "zones": zones,
            "zoneCount": len(zones),
            "currentConcreteVolumeM3": round(current_volume, 2),
            "optimizedConcreteVolumeM3": round(optimized_volume, 2),
            "estimatedConcreteSavingM3": round(saving, 2),
            "estimatedSavingRatio": round(saving / max(current_volume, 1.0), 4),
            "minimumScreeningFactor": round(minimum_factor if minimum_factor < 999 else 0.0, 3),
            "lockedConflictCount": locked_conflicts,
            "constructabilityPenalty": round(complexity_penalty, 2),
            "score": round(score, 2),
            "status": "candidate" if allowed and not locked_conflicts else "manual_review",
            "professionalReviewRequired": True,
        }

    required_bottoms = [float(row["requiredBottomElevationM"]) for row in rows]
    toe_spread = (max(required_bottoms) - min(required_bottoms)) if required_bottoms else 0.0
    minimum_variation = 0.75
    design_mode = str(getattr(settings, "wall_toe_design_mode", "uniform") or "uniform")
    variation_supported = toe_spread >= minimum_variation and len(zoned) > 1
    variation_reason = (
        f"局部稳定筛查要求的墙趾高差为 {toe_spread:.3f} m，达到分区墙趾触发值 {minimum_variation:.2f} m。"
        if variation_supported else
        f"局部稳定筛查要求的墙趾高差仅 {toe_spread:.3f} m，未达到分区墙趾触发值 {minimum_variation:.2f} m；保持统一墙趾更利于成槽、防水与钢筋笼标准化。"
    )

    candidates = [candidate("WVL-COMMON", "连续墙统一墙趾", common_zones, True)]
    if len(zoned) > 1:
        zoned_candidate = candidate("WVL-ZONED", "分区统一墙趾", zoned, selected_mode != "conservative" and design_mode in {"zoned", "local"} and variation_supported)
        if not variation_supported:
            zoned_candidate["status"] = "manual_review"
            zoned_candidate["rejectionReason"] = variation_reason
        candidates.append(zoned_candidate)
    current_candidate = candidate(
        "WVL-KEEP",
        "保持当前墙趾",
        [{
            "zoneId": f"WVZ-KEEP-{idx:02d}",
            "bottomElevationM": float(row["currentBottomElevationM"]),
            "planLengthM": float(row["planLengthM"]),
            "wallIds": [str(row["wallId"])],
            "wallCodes": [str(row["wallCode"])],
            "segmentIds": [str(row["segmentId"])],
            "minimumFactor": float(row["currentFactor"]),
        } for idx, row in enumerate(rows, start=1)],
        True,
    )
    candidates.append(current_candidate)
    recommended = max((item for item in candidates if item["status"] == "candidate"), key=lambda item: float(item["score"]), default=current_candidate)
    return {
        "projectId": project.id,
        "status": "warning" if any(float(row["potentialShorteningM"]) > 0.0 for row in rows) else "pass",
        "mode": selected_mode,
        "method": "segment screening + evidence-driven continuous construction-zone consolidation; imported/manual wall toes are reference-checked only when enabled and are never shortened automatically",
        "designMode": design_mode,
        "variationEvidence": {"requiredToeSpreadM": round(toe_spread, 3), "minimumVariationM": minimum_variation, "variationSupported": variation_supported, "reason": variation_reason},
        "screeningLimit": round(limit, 3),
        "designTarget": round(target, 3),
        "geologyBottomElevationM": _geology_bottom_elevation(project),
        "rows": rows,
        "candidates": candidates,
        "recommendedCandidateId": recommended["candidateId"],
        "summary": {
            "wallCount": len(rows),
            "lockedWallCount": sum(1 for row in rows if row["locked"]),
            "potentialShorteningWallCount": sum(1 for row in rows if float(row["potentialShorteningM"]) > 0.0),
            "currentConcreteVolumeM3": round(current_volume, 2),
            "recommendedConcreteVolumeM3": recommended["optimizedConcreteVolumeM3"],
            "estimatedConcreteSavingM3": recommended["estimatedConcreteSavingM3"],
            "recommendedZoneCount": recommended["zoneCount"],
            "requiredToeSpreadM": round(toe_spread, 3),
            "variationSupported": variation_supported,
        },
        "engineeringBoundary": "连续地下墙优先采用统一墙趾；只有节约量足够、分区连续、台阶位于槽段/转角且防水与施工可控时才采用分区墙趾。",
    }


def apply_wall_vertical_length_candidate(project: Project, candidate_id: str, mode: str = "balanced") -> dict[str, Any]:
    analysis = analyze_wall_vertical_length(project, mode=mode)
    candidate = next((item for item in analysis.get("candidates", []) if item.get("candidateId") == candidate_id), None)
    if candidate is None:
        raise ValueError(f"Candidate not found: {candidate_id}")
    if candidate.get("status") != "candidate":
        raise ValueError("Candidate requires professional review and cannot be auto-applied")
    ret = project.retaining_system
    if ret is None:
        raise ValueError("Project has no retaining system")
    wall_by_id = {wall.id: wall for wall in ret.diaphragm_walls}
    changes: list[dict[str, Any]] = []
    for zone in candidate.get("zones", []):
        bottom = float(zone["bottomElevationM"])
        for wall_id in zone.get("wallIds", []):
            wall = wall_by_id.get(str(wall_id))
            if wall is None:
                continue
            source = str(getattr(wall, "bottom_elevation_source", "unknown") or "unknown")
            if bool(getattr(wall, "bottom_elevation_locked", False)) or source in {"imported", "manual"}:
                continue
            before = float(wall.bottom_elevation)
            wall.bottom_elevation = round(bottom, 3)
            wall.bottom_elevation_source = "auto_stability"
            wall.toe_zone_id = str(zone.get("zoneId") or "")
            wall.toe_profile_status = "zoned" if len(candidate.get("zones", [])) > 1 else "uniform"
            changes.append({"wallId": wall.id, "wallCode": wall.panel_code, "beforeBottomElevationM": before, "afterBottomElevationM": wall.bottom_elevation, "zoneId": zone.get("zoneId")})
    ret.layout_summary = dict(ret.layout_summary or {})
    ret.layout_summary["wallVerticalLengthOptimization"] = {
        "candidateId": candidate_id,
        "mode": mode,
        "changes": changes,
        "recomputeRequired": True,
        "professionalReviewRequired": True,
        "analysisSnapshot": analysis,
    }
    project.calculation_results = []
    project.advanced_engineering = dict(project.advanced_engineering or {})
    project.advanced_engineering["calculationState"] = {
        "requiresRecalculation": True,
        "reason": "围护墙竖向设计长度/墙趾标高已优化",
    }
    return {
        "projectId": project.id,
        "candidateId": candidate_id,
        "changedWallCount": len(changes),
        "changes": changes,
        "recomputeRequired": True,
        "message": "已写入围护墙竖向长度候选。必须重新运行分阶段计算、稳定校核和配筋设计。",
    }
