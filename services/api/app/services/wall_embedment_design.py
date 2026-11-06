from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.geology.section import extract_representative_section
from app.rules.jgj120_2012.retaining_wall_rules import (
    check_embedment_stability,
    required_embedment_factor,
)
from app.schemas.domain import CalculationCase, Project
from app.services.engineering_templates import safety_targets


@dataclass(frozen=True)
class _StageContext:
    excavation_depth_m: float
    groundwater_outside_elevation_m: float
    groundwater_inside_elevation_m: float | None
    surcharge_kpa: float
    stage_id: str | None
    stage_name: str | None


def _governing_stage_context(project: Project, case: CalculationCase | None) -> _StageContext:
    if project.excavation is None:
        raise ValueError("Project has no excavation")
    top = float(project.excavation.top_elevation)
    final_depth = max(0.0, top - float(project.excavation.bottom_elevation))
    stages = list(case.stages) if case is not None else []
    if not stages:
        return _StageContext(
            excavation_depth_m=final_depth,
            groundwater_outside_elevation_m=float(project.design_settings.groundwater_level),
            groundwater_inside_elevation_m=project.design_settings.groundwater_level_inside,
            surcharge_kpa=float(project.design_settings.surcharge),
            stage_id=None,
            stage_name="design final excavation",
        )

    def key(stage) -> tuple[float, float]:
        depth = min(final_depth, max(0.0, top - float(stage.excavation_elevation)))
        return depth, float(stage.surcharge or 0.0)

    stage = max(stages, key=key)
    stage_depth = min(final_depth, max(0.0, top - float(stage.excavation_elevation)))
    if stage_depth <= 1.0e-9:
        stage_depth = final_depth
    gw_out = (
        float(stage.groundwater_level_outside)
        if stage.groundwater_level_outside is not None
        else float(project.design_settings.groundwater_level)
    )
    gw_in = (
        float(stage.groundwater_level_inside)
        if stage.groundwater_level_inside is not None
        else project.design_settings.groundwater_level_inside
    )
    return _StageContext(
        excavation_depth_m=stage_depth,
        groundwater_outside_elevation_m=gw_out,
        groundwater_inside_elevation_m=gw_in,
        surcharge_kpa=float(stage.surcharge or project.design_settings.surcharge),
        stage_id=stage.id,
        stage_name=stage.name,
    )


def _geology_bottom_elevation(project: Project) -> float | None:
    values: list[float] = []
    for borehole in project.boreholes:
        values.extend(float(layer.bottom_elevation) for layer in borehole.layers)
    for stratum in project.strata:
        bottom = getattr(stratum, "bottom_elevation", None)
        if bottom is not None:
            values.append(float(bottom))
    return min(values) if values else None


def _evaluate(
    project: Project,
    case: CalculationCase | None,
    proposed_common_bottom: float | None = None,
) -> tuple[list[dict[str, Any]], _StageContext]:
    if project.excavation is None or project.retaining_system is None:
        return [], _governing_stage_context(project, case)
    context = _governing_stage_context(project, case)
    top = float(project.excavation.top_elevation)
    walls_by_segment = {wall.segment_id: wall for wall in project.retaining_system.diaphragm_walls}
    rows: list[dict[str, Any]] = []
    for segment in project.excavation.segments:
        wall = walls_by_segment.get(segment.id)
        if wall is None:
            continue
        locked = bool(getattr(wall, "bottom_elevation_locked", False))
        bottom = float(wall.bottom_elevation)
        if proposed_common_bottom is not None and not locked:
            # Automatic design is allowed to deepen a wall, never to shorten it.
            bottom = min(bottom, float(proposed_common_bottom))
        section = extract_representative_section(project, segment.id)
        check, trace = check_embedment_stability(
            object_id=wall.id,
            soil_profile=section.layers,
            excavation_depth=context.excavation_depth_m,
            wall_bottom_elevation=bottom,
            top_elevation=top,
            groundwater_level_outside=context.groundwater_outside_elevation_m,
            groundwater_level_inside=context.groundwater_inside_elevation_m,
            surcharge=context.surcharge_kpa,
            safety_grade=project.design_settings.safety_grade,
        )
        rows.append({
            "segmentId": segment.id,
            "segmentCode": segment.name,
            "wallId": wall.id,
            "wallCode": wall.panel_code,
            "bottomElevationM": round(bottom, 3),
            "embedmentDepthM": round(float(trace.get("embedmentDepthM") or 0.0), 3),
            "factor": float(check.calculated_value or 0.0),
            "limit": float(check.limit_value or 0.0),
            "status": check.status,
            "locked": locked,
            "source": str(getattr(wall, "bottom_elevation_source", "unknown") or "unknown"),
            "activeMomentKnMPerM": trace.get("activeMomentKnMPerM"),
            "netPassiveMomentKnMPerM": trace.get("netPassiveMomentKnMPerM"),
        })
    return rows, context


def auto_design_wall_embedment(
    project: Project,
    case: CalculationCase | None = None,
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Deepen the retaining wall toe when the embedment screening fails.

    The design is performed on a common wall-toe elevation so a continuous wall
    does not acquire arbitrary panel-by-panel steps. Imported/manual locked toe
    elevations are preserved. The function never shortens an existing wall.
    """
    if project.excavation is None or project.retaining_system is None:
        return {"status": "manual_review", "changed": False, "message": "缺少基坑或围护墙，无法执行嵌固深度设计。"}
    walls = list(project.retaining_system.diaphragm_walls)
    if not walls:
        return {"status": "manual_review", "changed": False, "message": "尚未生成地下连续墙。"}

    settings = project.design_settings
    if enabled is None:
        enabled = bool(getattr(settings, "auto_wall_embedment_design_enabled", True))
    limit = required_embedment_factor(settings.safety_grade)
    margin = max(0.0, float(getattr(settings, "wall_embedment_safety_margin", 0.05) or 0.0))
    reserve_ratio = max(1.0, float(safety_targets(project).get("embedment", 1.0)))
    target = max(limit + margin, limit * reserve_ratio)
    increment = max(0.05, float(getattr(settings, "wall_embedment_search_increment_m", 0.25) or 0.25))
    max_additional = max(0.0, float(getattr(settings, "wall_embedment_max_additional_depth_m", 20.0) or 20.0))

    before_rows, context = _evaluate(project, case)
    before_min = min((float(row["factor"]) for row in before_rows), default=0.0)
    before_bottoms = [float(wall.bottom_elevation) for wall in walls]
    common_before = min(before_bottoms)
    governing_before = min(before_rows, key=lambda row: float(row["factor"])) if before_rows else None

    audit: dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "pass" if before_min >= target else "fail",
        "codeStatus": "pass" if before_min >= limit else "fail",
        "changed": False,
        "ruleId": "JGJ120-2012-4.2-EMBEDMENT-STABILITY-SCREEN",
        "method": "common wall-toe search using the existing Rankine net-passive moment screening",
        "screeningLimit": round(limit, 3),
        "designTarget": round(target, 3),
        "projectReserveRatio": round(reserve_ratio, 3),
        "searchIncrementM": round(increment, 3),
        "beforeBottomElevationM": round(common_before, 3),
        "afterBottomElevationM": round(common_before, 3),
        "addedEmbedmentM": 0.0,
        "beforeMinimumFactor": round(before_min, 3),
        "afterMinimumFactor": round(before_min, 3),
        "governingSegmentBefore": governing_before,
        "governingSegmentAfter": governing_before,
        "stageId": context.stage_id,
        "stageName": context.stage_name,
        "excavationDepthM": round(context.excavation_depth_m, 3),
        "groundwaterOutsideElevationM": round(context.groundwater_outside_elevation_m, 3),
        "groundwaterInsideElevationM": context.groundwater_inside_elevation_m,
        "surchargeKPa": round(context.surcharge_kpa, 3),
        "lockedWallCount": sum(bool(getattr(wall, "bottom_elevation_locked", False)) for wall in walls),
        "rowsBefore": before_rows,
    }
    if before_min >= target:
        audit["message"] = "当前墙趾标高同时满足规范限值和项目储备目标，无需调整。"
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["wallEmbedmentDesign"] = audit
        return audit
    if not enabled:
        audit["message"] = "嵌固稳定筛查未通过，且自动墙趾设计已关闭。"
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["wallEmbedmentDesign"] = audit
        return audit

    locked_failures = [row for row in before_rows if row["locked"] and float(row["factor"]) < target]
    if locked_failures:
        audit["status"] = "fail"
        audit["lockedFailureCount"] = len(locked_failures)
        audit["message"] = "存在锁定墙趾未达到项目嵌固储备目标；系统不会覆盖人工/导入控制值，请在墙段长度优化中解除锁定、指定分区墙长或提交专业复核。"
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["wallEmbedmentDesign"] = audit
        return audit

    geology_bottom = _geology_bottom_elevation(project)
    deepest_by_iteration = common_before - max_additional
    deepest_allowed = deepest_by_iteration
    if geology_bottom is not None:
        # Keep the wall toe inside the represented geological column.
        deepest_allowed = max(deepest_by_iteration, float(geology_bottom) + 0.5)
    audit["geologyBottomElevationM"] = round(geology_bottom, 3) if geology_bottom is not None else None
    audit["deepestAllowedBottomElevationM"] = round(deepest_allowed, 3)

    candidate = common_before
    selected: float | None = None
    selected_rows: list[dict[str, Any]] = before_rows
    iteration_count = 0
    while candidate - increment >= deepest_allowed - 1.0e-9:
        candidate = round(candidate - increment, 6)
        iteration_count += 1
        rows, _ = _evaluate(project, case, proposed_common_bottom=candidate)
        selected_rows = rows
        if rows and min(float(row["factor"]) for row in rows) >= target:
            selected = candidate
            break
    if selected is None:
        selected = deepest_allowed
        selected_rows, _ = _evaluate(project, case, proposed_common_bottom=selected)

    original_bottom_by_id = {wall.id: float(wall.bottom_elevation) for wall in walls}
    for wall in walls:
        if bool(getattr(wall, "bottom_elevation_locked", False)):
            continue
        proposed = min(float(wall.bottom_elevation), float(selected))
        if proposed < float(wall.bottom_elevation) - 1.0e-9:
            if getattr(wall, "source_bottom_elevation", None) is None:
                wall.source_bottom_elevation = float(wall.bottom_elevation)
            wall.bottom_elevation = round(proposed, 3)
            wall.bottom_elevation_source = "auto_stability"
            if wall.design_results is not None:
                wall.design_results.notes = list(wall.design_results.notes or [])
                note = f"墙趾标高由 {original_bottom_by_id[wall.id]:.3f}m 自动加深至 {wall.bottom_elevation:.3f}m，以闭合嵌固稳定筛查。"
                if note not in wall.design_results.notes:
                    wall.design_results.notes.append(note)

    after_rows, _ = _evaluate(project, case)
    after_min = min((float(row["factor"]) for row in after_rows), default=0.0)
    common_after = min(float(wall.bottom_elevation) for wall in walls)
    governing_after = min(after_rows, key=lambda row: float(row["factor"])) if after_rows else None
    changed = any(abs(float(wall.bottom_elevation) - original_bottom_by_id[wall.id]) > 1.0e-9 for wall in walls)
    status = "pass" if after_min >= target else "fail"
    audit.update({
        "status": status,
        "codeStatus": "pass" if after_min >= limit else "fail",
        "changed": changed,
        "iterationCount": iteration_count,
        "afterBottomElevationM": round(common_after, 3),
        "addedEmbedmentM": round(max(0.0, common_before - common_after), 3),
        "afterMinimumFactor": round(after_min, 3),
        "governingSegmentAfter": governing_after,
        "rowsAfter": after_rows,
        "message": (
            f"墙趾已统一加深 {max(0.0, common_before - common_after):.2f}m，最小嵌固筛查系数由 {before_min:.3f} 提高至 {after_min:.3f}。"
            if changed and status == "pass"
            else (
                "当前地质深度或自动加深上限内仍不能满足嵌固稳定筛查，保留硬失败。"
                if status == "fail"
                else "现有较深墙趾已满足嵌固稳定筛查。"
            )
        ),
    })
    project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
    project.retaining_system.layout_summary["wallEmbedmentDesign"] = audit
    project.retaining_system.layout_summary.setdefault("designNotes", [])
    if changed:
        note = str(audit["message"])
        if note not in project.retaining_system.layout_summary["designNotes"]:
            project.retaining_system.layout_summary["designNotes"].append(note)
    return audit
