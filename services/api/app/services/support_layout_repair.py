from __future__ import annotations

from typing import Any

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Project, SupportLayoutRepairSummary, QualityGateIssue
from app.services.design_service import auto_supports, support_layout_config_from_settings
from app.services.support_layout_optimizer import OBJECTIVE_WEIGHTS, build_support_system_from_candidate, normalize_objective_weights, optimize_support_layout_candidates
from app.services.calculation_state import invalidate_calculation_state
from app.services.wall_embedment_design import auto_design_wall_embedment


REPAIRABLE_CATEGORIES = {
    "support_spacing",
    "support_span",
    "wale_support_bay",
    "support_crossing",
    "support_outside_excavation",
    "obstacle_clearance",
    "temporary_column",
    "replacement_path",
    "support_station_cluster",
    "corner_brace_fan_geometry",
    "corner_brace_wall_node_congestion",
    "support_to_support_terminal",
    "unsupported_internal_endpoint",
}


def _issue_counts(issues: list[QualityGateIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.category] = counts.get(issue.category, 0) + 1
    return counts


def _repair_priority_score(quality: Any) -> float:
    """Return a 0-100 score for topology-repair progress.

    The general quality score intentionally gives every failed check the same
    deduction.  Repair selection has a different contract: illegal same-level
    member crossings are the primary topology objective, followed by members
    leaving the excavation and other hard constructability failures.  Keeping
    this score separate prevents a successful crossing removal from appearing
    as a regression merely because the regenerated scheme exposes additional
    lower-priority warnings.  The original quality score is retained alongside
    this value for engineering review.
    """
    metrics = dict(getattr(quality, "metrics", {}) or {})
    crossing_count = int(metrics.get("supportCrossingCount", 0) or 0)
    outside_count = int(metrics.get("supportOutsideExcavationCount", 0) or 0)
    internal_junction_count = int(metrics.get("internalJunctionCount", 0) or 0)
    high_degree_junction_count = int(metrics.get("highDegreeJunctionCount", 0) or 0)

    fail_issues = [issue for issue in quality.issues if issue.severity == "fail"]
    warning_count = sum(issue.severity == "warning" for issue in quality.issues)
    manual_count = sum(issue.severity == "manual_review" for issue in quality.issues)
    excluded = {"support_crossing", "support_outside_excavation"}
    other_hard_count = sum(issue.category not in excluded for issue in fail_issues)

    # Lexicographic intent encoded in a bounded score: one illegal crossing
    # outweighs the aggregate lower-priority deductions.  Junctions remain soft
    # constructability penalties because valid T/Y nodes can be structurally
    # necessary for concave and near-square excavations.
    deduction = (
        min(60.0, 60.0 * crossing_count)
        + min(18.0, 18.0 * outside_count)
        + min(14.0, 3.5 * other_hard_count)
        + min(4.0, 0.5 * warning_count + 1.5 * manual_count)
        + min(2.0, 0.10 * internal_junction_count + 0.25 * high_degree_junction_count)
    )
    return round(max(0.0, 100.0 - deduction), 1)


def _current_lock_summary(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    supports = ret.supports if ret else []
    obstacles = project.excavation.obstacles if project.excavation else []
    level_indices = sorted({int(item.get("levelIndex", item.get("level_index"))) for item in (ret.optimization_locks if ret else []) if str(item.get("targetType", item.get("target_type", ""))) == "support_level" and item.get("locked", True) and item.get("levelIndex", item.get("level_index")) is not None})
    obstacle_ids = [o.id for o in obstacles if getattr(o, "optimization_locked", False)]
    line_ids = [s.id for s in supports if getattr(s, "optimization_locked", False)]
    start_ids = [s.id for s in supports if getattr(s, "optimization_locked_start", False)]
    end_ids = [s.id for s in supports if getattr(s, "optimization_locked_end", False)]
    return {
        "supportLineCount": len(line_ids),
        "endpointCount": len(set(start_ids + end_ids)),
        "startEndpointCount": len(start_ids),
        "endEndpointCount": len(end_ids),
        "supportLevelCount": len(level_indices),
        "obstacleBoundaryCount": len(obstacle_ids),
        "supportLineIds": line_ids,
        "startEndpointIds": start_ids,
        "endEndpointIds": end_ids,
        "levelIndices": level_indices,
        "obstacleIds": obstacle_ids,
        "summary": f"整线 {len(line_ids)} 条，端点 {len(set(start_ids + end_ids))} 条，支撑层 {len(level_indices)} 层，出土/障碍边界 {len(obstacle_ids)} 个。",
    }


def _set_lock_record(project: Project, item: dict[str, Any], locked: bool, reason: str | None) -> None:
    if not project.retaining_system:
        return
    target_type = str(item.get("targetType", item.get("target_type", "support_line")))
    record = dict(item)
    record["targetType"] = target_type
    record["locked"] = bool(locked)
    if reason:
        record["reason"] = reason
    # Keep only the latest state for the same logical target.
    def key(v: dict[str, Any]) -> tuple[Any, ...]:
        return (
            v.get("targetType", v.get("target_type")),
            v.get("supportId", v.get("support_id")),
            v.get("endpoint"),
            v.get("levelIndex", v.get("level_index")),
            v.get("obstacleId", v.get("obstacle_id")),
        )
    existing = [r for r in project.retaining_system.optimization_locks if key(r) != key(record)]
    if locked:
        existing.append(record)
    project.retaining_system.optimization_locks = existing


def _clear_all_locks(project: Project) -> None:
    if project.retaining_system:
        for support in project.retaining_system.supports:
            support.optimization_locked = False
            support.optimization_locked_start = False
            support.optimization_locked_end = False
            support.optimization_lock_reason = None
        project.retaining_system.optimization_locks = []
    if project.excavation:
        for obstacle in project.excavation.obstacles or []:
            obstacle.optimization_locked = False
            obstacle.optimization_lock_reason = None


def _apply_lock_item(project: Project, item: dict[str, Any], locked: bool, reason: str | None) -> int:
    if not project.retaining_system:
        return 0
    target_type = str(item.get("targetType", item.get("target_type", "support_line")))
    support_id = item.get("supportId", item.get("support_id"))
    endpoint = str(item.get("endpoint", "")).lower()
    level_index = item.get("levelIndex", item.get("level_index"))
    obstacle_id = item.get("obstacleId", item.get("obstacle_id"))
    changed = 0

    if target_type in {"support_line", "line"} and support_id:
        for support in project.retaining_system.supports:
            if support.id == support_id or support.code == support_id:
                support.optimization_locked = locked
                support.optimization_lock_reason = reason if locked else None
                changed += 1
        _set_lock_record(project, {"targetType": "support_line", "supportId": support_id}, locked, reason)
        return changed

    if target_type in {"support_endpoint", "support_start", "support_end", "endpoint"} and support_id:
        if target_type == "support_start":
            endpoint = "start"
        elif target_type == "support_end":
            endpoint = "end"
        for support in project.retaining_system.supports:
            if support.id == support_id or support.code == support_id:
                if endpoint in {"start", "both", ""}:
                    support.optimization_locked_start = locked
                if endpoint in {"end", "both", ""}:
                    support.optimization_locked_end = locked
                support.optimization_lock_reason = reason if locked else support.optimization_lock_reason
                changed += 1
        _set_lock_record(project, {"targetType": "support_endpoint", "supportId": support_id, "endpoint": endpoint or "both"}, locked, reason)
        return changed

    if target_type in {"support_level", "level"} and level_index is not None:
        try:
            level = int(level_index)
        except (TypeError, ValueError):
            return 0
        for support in project.retaining_system.supports:
            if int(support.level_index) == level:
                support.optimization_locked = locked
                support.optimization_lock_reason = reason if locked else None
                changed += 1
        _set_lock_record(project, {"targetType": "support_level", "levelIndex": level}, locked, reason)
        return changed

    if target_type in {"obstacle_boundary", "muck_path_boundary", "obstacle"} and obstacle_id and project.excavation:
        for obstacle in project.excavation.obstacles or []:
            if obstacle.id == obstacle_id or obstacle.name == obstacle_id:
                obstacle.optimization_locked = locked
                obstacle.optimization_lock_reason = reason if locked else None
                changed += 1
        _set_lock_record(project, {"targetType": "obstacle_boundary", "obstacleId": obstacle_id}, locked, reason)
        return changed
    return changed


def auto_repair_support_layout(project: Project, objective_weights: dict[str, float] | None = None, preset: str | None = None) -> SupportLayoutRepairSummary:
    """Optimize and repair the support layout using an explicit objective function.

    V2.0.9 keeps the V2.0.8 candidate search and adds local operator locks.
    Whole support lines, individual endpoints, support levels, and obstacle / muck-out
    boundaries can be fixed before optimization so the search respects site logistics
    and manual engineering decisions.
    """
    if not project.excavation:
        return SupportLayoutRepairSummary(status="manual_review", summary="缺少基坑轮廓，无法自动修复支撑布置。")
    before = evaluate_support_layout_quality(project)
    before_counts = _issue_counts(before.issues)
    actions: list[dict] = []

    old_support_count = len(project.retaining_system.supports) if project.retaining_system else 0
    old_column_count = len(project.retaining_system.columns) if project.retaining_system else 0

    existing = project.retaining_system.support_layout_repair if project.retaining_system else None
    if (
        existing
        and existing.candidates
        and old_support_count > 0
        and before.status not in {"fail", "manual_review"}
        and not objective_weights
        and not preset
    ):
        # V2.6.0: normal calculation should not repeatedly re-enumerate every
        # support candidate.  Large projects with many stored calculation results
        # can spend minutes in deep copies during candidate search.  Reuse the
        # accepted/recent repair summary unless the user explicitly requests a new
        # optimization pass.
        existing.score_after = _repair_priority_score(before)
        existing.raw_quality_score_after = before.score
        existing.status = before.status
        existing.unresolved_issues = before.issues
        existing.summary = before.summary
        return existing

    weights = normalize_objective_weights(objective_weights)
    lock_summary = _current_lock_summary(project)
    best_system, candidates = optimize_support_layout_candidates(project, objective_weights=weights, preset=preset)
    if best_system is not None and candidates:
        project.retaining_system = best_system
        actions.append({
            "action": "objective_function_support_layout_optimization",
            "description": "枚举 3.5-6.0m 主对撑分仓与 12-18m 立柱服务跨，在整线/端点/层/出土边界局部锁定约束下，按非法穿越、内部汇交节点、高度汇交节点和综合工程性能的分层优先级采用最优方案。",
            "candidateCount": len(candidates),
            "bestCandidateId": candidates[0].id,
            "bestCandidateScore": candidates[0].score,
            "bestTargetSpacing": candidates[0].target_spacing,
            "bestColumnMaxSpan": candidates[0].column_max_span,
            "objectiveWeights": weights,
            "objectivePreset": preset or "custom",
            "lockSummary": lock_summary,
        })
    else:
        should_regenerate = (
            not project.retaining_system
            or before.status in {"fail", "manual_review"}
            or any(issue.category in REPAIRABLE_CATEGORIES for issue in before.issues)
        )
        if should_regenerate:
            project.retaining_system = auto_supports(project.excavation, project.retaining_system, layout_config=support_layout_config_from_settings(project.design_settings))
            actions.append({
                "action": "fallback_regenerate_dense_bays_and_repair_layout",
                "description": "优化器未生成候选方案，退回 3-6m 分仓规则修复。",
            })

    after = evaluate_support_layout_quality(project)
    after_counts = _issue_counts(after.issues)
    unresolved = [i for i in after.issues if i.severity in {"fail", "warning", "manual_review"}]
    status = "pass" if not unresolved else "fail" if any(i.severity == "fail" for i in unresolved) else "warning"
    repair_score_before = _repair_priority_score(before)
    repair_score_after = _repair_priority_score(after)
    summary = (
        f"支撑布置约束优化与候选方案比选：修复优先级评分 {repair_score_before:.1f} -> {repair_score_after:.1f}；"
        f"原始质量评分 {before.score:.1f} -> {after.score:.1f}；问题数 {len(before.issues)} -> {len(after.issues)}。"
    )
    if candidates:
        pattern = candidates[0].variable_summary.get("positionPattern", "as_generated") if candidates[0].variable_summary else "as_generated"
        summary += (
            f" 已比选 {len(candidates)} 个约束优化候选方案，采用第 1 名：目标分仓 {candidates[0].target_spacing:.1f}m，"
            f"立柱服务跨 {candidates[0].column_max_span:.1f}m，支撑线变量策略 {pattern}，非法穿越 "
            f"{candidates[0].crossing_count} 处，内部汇交节点 {candidates[0].junction_count} 处。"
        )
    elif actions:
        summary += " 已执行规则修复兜底。"
    else:
        summary += " 未发现需要自动修复的支撑布置问题。"

    final_lock_summary = _current_lock_summary(project)
    repair = SupportLayoutRepairSummary(
        optimization_method="constrained support-line position optimizer with ranked alternatives and local locks",
        optimization_phase="V2.0.9 local locks, animated candidate delta, and candidate calculation comparison",
        hard_constraint_labels=[
            "支撑不得交叉",
            "支撑不得穿越出土口/坡道/保护区",
            "支撑端点必须落在围檩/环梁/节点上",
            "同族支撑站位满足最小净距且不得在折点处聚集",
            "立柱不得落入障碍区",
            "换撑路径不得中断",
        ],
        soft_objective_labels=[
            "平面交叉点与内部汇交节点尽可能少",
            "支撑站位避免局部过密与重复分仓",
            "支撑间距接近 3-6m",
            "支撑体系尽量对称",
            "支撑跨长尽量短",
            "立柱数量不过多",
            "轴力峰值不过大",
            "出土路径尽量连续",
        ],
        objective_weights=weights,
        candidate_count=len(candidates),
        best_candidate_id=candidates[0].id if candidates else None,
        selected_candidate_id=candidates[0].id if candidates else None,
        locked_support_ids=final_lock_summary.get("supportLineIds", []),
        lock_summary=final_lock_summary,
        candidates=candidates,
        status=status,
        score_before=repair_score_before,
        score_after=repair_score_after,
        raw_quality_score_before=before.score,
        raw_quality_score_after=after.score,
        actions=[
            *actions,
            {"action": "support_count_change", "oldSupportCount": old_support_count, "newSupportCount": len(project.retaining_system.supports) if project.retaining_system else 0, "oldColumnCount": old_column_count, "newColumnCount": len(project.retaining_system.columns) if project.retaining_system else 0},
            {"action": "issue_count_before", "counts": before_counts},
            {"action": "issue_count_after", "counts": after_counts},
            {"action": "repair_priority_score", "before": repair_score_before, "after": repair_score_after, "rawQualityBefore": before.score, "rawQualityAfter": after.score},
            {"action": "local_lock_summary", "counts": final_lock_summary},
        ],
        unresolved_issues=unresolved[:30],
        summary=summary,
    )
    if project.retaining_system:
        project.retaining_system.support_layout_repair = repair
        project.retaining_system.warnings = list(dict.fromkeys([*project.retaining_system.warnings, summary]))
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["autoRepair"] = repair.model_dump(mode="json", by_alias=True)
        project.retaining_system.layout_summary["supportOptimizationCandidates"] = [c.model_dump(mode="json", by_alias=True) for c in candidates]
    return repair


def adopt_support_layout_candidate(project: Project, candidate_id: str) -> SupportLayoutRepairSummary:
    """Adopt one ranked candidate and write it back to the project.

    Candidate ids are deterministic functions of spacing, column span, and line
    position pattern, so we can regenerate the candidate safely without storing
    a full shadow model in the repository.
    """
    if not project.excavation:
        return SupportLayoutRepairSummary(status="manual_review", summary="缺少基坑轮廓，无法采用支撑优化候选方案。")
    current_repair = project.retaining_system.support_layout_repair if project.retaining_system else None
    weights = dict(current_repair.objective_weights or OBJECTIVE_WEIGHTS) if current_repair else dict(OBJECTIVE_WEIGHTS)
    _best, candidates = optimize_support_layout_candidates(project, max_candidates=12, objective_weights=weights)
    selected = next((c for c in candidates if c.id == candidate_id), None)
    if selected is None:
        # Fall back to the visible top-five candidates in the existing result.
        existing = (current_repair.candidates if current_repair else []) or []
        selected = next((c for c in existing if c.id == candidate_id), None)
    if selected is None:
        return SupportLayoutRepairSummary(status="fail", summary=f"未找到候选方案 {candidate_id}，无法采用。")
    pattern = str((selected.variable_summary or {}).get("positionPattern", "as_generated"))
    amplitude = float((selected.variable_summary or {}).get("lineOffsetAmplitude", 0.0) or 0.0)
    topology_strategy = str((selected.variable_summary or {}).get("topologyFamily", "balanced_grid"))
    system, adjustments = build_support_system_from_candidate(project, selected.target_spacing, selected.column_max_span, pattern, amplitude, topology_strategy)
    if system is None:
        return SupportLayoutRepairSummary(status="fail", summary=f"候选方案 {candidate_id} 重建失败。")
    # Preserve lock registry and support-local lock flags from the current retained system.
    if project.retaining_system:
        system.optimization_locks = list(project.retaining_system.optimization_locks or [])
    project.retaining_system = system
    quality = evaluate_support_layout_quality(project)
    selected.delta_geometry = {"changedSupportCount": len(adjustments), "adjustments": adjustments[:20]}
    lock_summary = _current_lock_summary(project)
    repair = SupportLayoutRepairSummary(
        optimization_method="interactive candidate adoption after weighted constrained optimization",
        optimization_phase="V2.0.9 adopted support optimization candidate",
        hard_constraint_labels=[
            "支撑不得交叉", "支撑不得穿越出土口/坡道/保护区", "支撑端点必须落在围檩/环梁/节点上", "立柱不得落入障碍区", "换撑路径不得中断",
        ],
        soft_objective_labels=[
            "平面交叉点与内部汇交节点尽可能少", "支撑间距接近 3-6m", "支撑体系尽量对称", "支撑跨长尽量短", "立柱数量不过多", "轴力峰值不过大", "出土路径尽量连续",
        ],
        objective_weights=weights,
        candidate_count=len(candidates),
        best_candidate_id=candidates[0].id if candidates else selected.id,
        selected_candidate_id=selected.id,
        locked_support_ids=lock_summary.get("supportLineIds", []),
        lock_summary=lock_summary,
        candidates=candidates[:5],
        status=quality.status,
        score_before=_repair_priority_score(quality),
        score_after=_repair_priority_score(quality),
        raw_quality_score_before=quality.score,
        raw_quality_score_after=quality.score,
        actions=[{
            "action": "adopt_support_optimization_candidate",
            "candidateId": selected.id,
            "rank": selected.rank,
            "score": selected.score,
            "targetSpacing": selected.target_spacing,
            "columnMaxSpan": selected.column_max_span,
            "positionPattern": pattern,
            "topologyFamily": topology_strategy,
            "changedSupportCount": len(adjustments),
            "lockSummary": lock_summary,
        }],
        unresolved_issues=[i for i in quality.issues if i.severity in {"fail", "warning", "manual_review"}][:30],
        summary=(
            f"已采用支撑优化候选方案 {selected.id}：整体拓扑 {topology_strategy}，目标分仓 {selected.target_spacing:.1f}m，"
            f"立柱服务跨 {selected.column_max_span:.1f}m，线位策略 {pattern}，非法穿越 {selected.crossing_count} 处，"
            f"内部汇交节点 {selected.junction_count} 处。"
        ),
    )
    project.retaining_system.support_layout_repair = repair
    project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
    project.retaining_system.layout_summary["autoRepair"] = repair.model_dump(mode="json", by_alias=True)
    project.retaining_system.layout_summary["supportOptimizationCandidates"] = [c.model_dump(mode="json", by_alias=True) for c in repair.candidates]
    # Candidate adoption changes the support system but must not leave a known
    # global wall-toe failure unresolved.  Apply the same common-toe stability
    # preflight that the calculation engine uses, while respecting imported or
    # manually locked wall elevations.
    embedment = auto_design_wall_embedment(
        project,
        project.calculation_cases[-1] if project.calculation_cases else None,
        enabled=bool(getattr(project.design_settings, "auto_wall_embedment_design_enabled", True)),
    )
    repair.actions.append({
        "action": "wall_embedment_preflight_after_candidate_adoption",
        "status": embedment.get("status"),
        "changed": embedment.get("changed"),
        "beforeBottomElevationM": embedment.get("beforeBottomElevationM"),
        "afterBottomElevationM": embedment.get("afterBottomElevationM"),
        "beforeMinimumFactor": embedment.get("beforeMinimumFactor"),
        "afterMinimumFactor": embedment.get("afterMinimumFactor"),
    })
    invalidate_calculation_state(
        project,
        reason=f"adopted support candidate {selected.id}; retaining topology and member ids changed",
        rebuild_cases=True,
    )
    return repair


def set_support_optimization_locks(
    project: Project,
    support_ids: list[str] | None = None,
    locked: bool = True,
    reason: str | None = None,
    lock_items: list[dict[str, Any]] | None = None,
    level_indices: list[int] | None = None,
    obstacle_ids: list[str] | None = None,
    replace: bool = False,
) -> SupportLayoutRepairSummary:
    if not project.retaining_system:
        return SupportLayoutRepairSummary(status="manual_review", summary="尚未生成支撑体系，无法锁定支撑线。")
    if replace:
        _clear_all_locks(project)
    changed = 0
    for sid in support_ids or []:
        changed += _apply_lock_item(project, {"targetType": "support_line", "supportId": sid}, locked, reason)
    for level in level_indices or []:
        changed += _apply_lock_item(project, {"targetType": "support_level", "levelIndex": level}, locked, reason)
    for oid in obstacle_ids or []:
        changed += _apply_lock_item(project, {"targetType": "obstacle_boundary", "obstacleId": oid}, locked, reason)
    for item in lock_items or []:
        item_locked = bool(item.get("locked", locked))
        changed += _apply_lock_item(project, item, item_locked, item.get("reason") or reason)
    current = project.retaining_system.support_layout_repair
    lock_summary = _current_lock_summary(project)
    summary = f"已{'锁定' if locked else '解除锁定'}局部优化约束，影响 {changed} 个对象；当前锁定：{lock_summary['summary']}"
    repair = current or SupportLayoutRepairSummary(status="pass", summary=summary)
    repair.status = "pass"
    repair.summary = summary
    repair.locked_support_ids = lock_summary.get("supportLineIds", [])
    repair.lock_summary = lock_summary
    repair.actions = [*(repair.actions or []), {"action": "set_local_optimization_locks", "changed": changed, "replace": replace, "lockSummary": lock_summary}]
    project.retaining_system.support_layout_repair = repair
    project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
    project.retaining_system.layout_summary["localOptimizationLocks"] = lock_summary
    return repair
