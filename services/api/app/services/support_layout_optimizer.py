from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Project, RetainingSystem, SupportElement, SupportLayoutOptimizationCandidate, Point2D
from app.services import design_service
from app.services import support_layout as layout_mod

OBJECTIVE_WEIGHTS: dict[str, float] = {
    "spacingDeviation": 20.0,
    "spanLength": 16.0,
    "obstacleConflict": 34.0,
    "supportCrossing": 40.0,
    "columnCount": 7.0,
    "muckPathContinuity": 8.0,
    "axialPeakProxy": 11.0,
    "symmetry": 10.0,
    "endpointValidity": 18.0,
    "replacementContinuity": 8.0,
}


def normalize_objective_weights(overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Return bounded objective weights.

    The UI may pass user preferences such as fewer columns, lower axial force,
    or better muck-out continuity.  We keep a bounded range so a single slider
    cannot fully suppress hard engineering concerns.
    """
    weights = dict(OBJECTIVE_WEIGHTS)
    for key, value in (overrides or {}).items():
        if key not in weights:
            continue
        try:
            weights[key] = max(0.0, min(80.0, float(value)))
        except (TypeError, ValueError):
            continue
    return weights


def preset_objective_weights(preset: str | None, overrides: dict[str, float] | None = None) -> dict[str, float]:
    weights = normalize_objective_weights(overrides)
    if preset == "fewer_columns":
        weights["columnCount"] = max(weights["columnCount"], 22.0)
        weights["spanLength"] = max(weights["spanLength"], 18.0)
    elif preset == "low_axial_force":
        weights["axialPeakProxy"] = max(weights["axialPeakProxy"], 28.0)
        weights["spanLength"] = max(weights["spanLength"], 22.0)
    elif preset == "muck_path_priority":
        weights["muckPathContinuity"] = max(weights["muckPathContinuity"], 30.0)
        weights["obstacleConflict"] = max(weights["obstacleConflict"], 44.0)
    elif preset == "balanced":
        pass
    return weights

HARD_CONSTRAINT_LABELS = [
    "support_no_crossing",
    "support_no_muck_ramp_protected_crossing",
    "support_endpoints_on_wale_or_ring_nodes",
    "temporary_columns_outside_obstacles",
    "replacement_path_continuity",
]
SOFT_OBJECTIVE_LABELS = [
    "spacing_close_to_3_6m",
    "short_span_length",
    "reasonable_column_count",
    "low_axial_peak_proxy",
    "continuous_muck_path",
    "plan_symmetry",
]

TARGET_SPACING_VALUES = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
COLUMN_MAX_SPAN_VALUES = [12.0, 15.0, 18.0]
TOPOLOGY_STRATEGIES = ["hybrid_diagonal", "bidirectional_grid", "direct_grid"]
POSITION_PATTERNS: list[tuple[str, float]] = [
    ("as_generated", 0.0),
    ("global_shift_plus_1p0", 1.0),
    ("global_shift_minus_1p0", -1.0),
    ("symmetric_expand_1p0", 1.0),
    ("symmetric_compress_1p0", 1.0),
    ("alternating_escape_1p5", 1.5),
    ("center_gap_1p5", 1.5),
]


def _status_rank(status: str) -> int:
    return {"pass": 0, "warning": 1, "manual_review": 2, "fail": 3}.get(status, 2)


def _bay_spacings(system: RetainingSystem) -> list[float]:
    return [float(s.bay_spacing) for s in system.supports if s.support_role == "main_strut" and s.bay_spacing]


def _span_lengths(system: RetainingSystem) -> list[float]:
    return [float(s.span_length) for s in system.supports if s.span_length]


def _bounds(project: Project) -> tuple[float, float, float, float] | None:
    if not project.excavation or not project.excavation.outline.points:
        return None
    pts = project.excavation.outline.points
    return min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts)


def _long_axis(project: Project) -> str:
    b = _bounds(project)
    if not b:
        return "x"
    x0, y0, x1, y1 = b
    return "x" if (x1 - x0) >= (y1 - y0) else "y"


def _retained_lock_records(project: Project) -> list[dict[str, Any]]:
    ret = getattr(project, "retaining_system", None)
    return list(getattr(ret, "optimization_locks", []) or []) if ret else []


def _locked_level_indices(project: Project) -> set[int]:
    levels: set[int] = set()
    for item in _retained_lock_records(project):
        target = str(item.get("targetType", item.get("target_type", "")))
        if target in {"support_level", "level"} and item.get("locked", True):
            raw = item.get("levelIndex", item.get("level_index"))
            try:
                levels.add(int(raw))
            except (TypeError, ValueError):
                continue
    return levels


def _obstacle_lock_ids(project: Project) -> set[str]:
    ids: set[str] = set()
    excavation = getattr(project, "excavation", None)
    if excavation:
        for obs in getattr(excavation, "obstacles", []) or []:
            if getattr(obs, "optimization_locked", False):
                ids.add(obs.id)
    for item in _retained_lock_records(project):
        target = str(item.get("targetType", item.get("target_type", "")))
        if target in {"obstacle_boundary", "muck_path_boundary", "obstacle"} and item.get("locked", True):
            raw = item.get("obstacleId", item.get("obstacle_id"))
            if raw:
                ids.add(str(raw))
    return ids


def _support_is_fully_locked(project: Project, support: SupportElement) -> bool:
    return bool(getattr(support, "optimization_locked", False)) or int(support.level_index) in _locked_level_indices(project)


def _support_lock_signature(support: SupportElement) -> dict[str, Any]:
    return {
        "line": bool(getattr(support, "optimization_locked", False)),
        "start": bool(getattr(support, "optimization_locked_start", False)),
        "end": bool(getattr(support, "optimization_locked_end", False)),
    }


def _nearest_original_support(project: Project, support: SupportElement) -> SupportElement | None:
    ret = getattr(project, "retaining_system", None)
    if not ret:
        return None
    support_mid = ((support.start.x + support.end.x) / 2.0, (support.start.y + support.end.y) / 2.0)
    best: SupportElement | None = None
    best_dist = 1e18
    for old in ret.supports:
        if old.support_role != support.support_role or int(old.level_index) != int(support.level_index):
            continue
        old_mid = ((old.start.x + old.end.x) / 2.0, (old.start.y + old.end.y) / 2.0)
        dist = math.hypot(old_mid[0] - support_mid[0], old_mid[1] - support_mid[1])
        if dist < best_dist:
            best = old
            best_dist = dist
    return best


def _apply_endpoint_locks(project: Project, system: RetainingSystem, column_max_span: float = layout_mod.COLUMN_MAX_UNBRACED_SPAN_M) -> list[dict[str, Any]]:
    adjustments: list[dict[str, Any]] = []
    for support in system.supports:
        if _support_is_fully_locked(project, support):
            continue
        old = _nearest_original_support(project, support)
        if not old:
            continue
        lock_start = bool(getattr(old, "optimization_locked_start", False))
        lock_end = bool(getattr(old, "optimization_locked_end", False))
        if not lock_start and not lock_end:
            continue
        before = {"start": support.start.model_dump(mode="json"), "end": support.end.model_dump(mode="json")}
        if lock_start:
            support.start = old.start.model_copy(deep=True)
            support.start_face_code = old.start_face_code
        if lock_end:
            support.end = old.end.model_copy(deep=True)
            support.end_face_code = old.end_face_code
        support.span_length = round(layout_mod._distance(support.start, support.end), 3)
        support.optimization_locked_start = lock_start
        support.optimization_locked_end = lock_end
        support.optimization_lock_reason = old.optimization_lock_reason
        support.layout_note = (support.layout_note or "") + " V2.0.9 局部端点锁定：保留原方案已锁定端点。"
        adjustments.append({
            "supportId": support.id,
            "supportCode": support.code,
            "pattern": "endpoint_lock",
            "lockType": "endpoint",
            "lockedStart": lock_start,
            "lockedEnd": lock_end,
            "before": before,
            "after": {"start": support.start.model_dump(mode="json"), "end": support.end.model_dump(mode="json")},
            "endpointConstraint": "operator_locked_endpoint_preserved",
        })
    if adjustments and project.excavation:
        temp_lines = [layout_mod.SupportLayoutLine(s.support_role, s.start, s.end, float(s.span_length or 0.0), s.bay_spacing, s.layout_note or "") for s in system.supports]
        layout_mod._attach_faces(temp_lines, project.excavation)
        for line, support in zip(temp_lines, system.supports):
            if not getattr(support, "optimization_locked_start", False):
                support.start_face_code = line.start_face_code
            if not getattr(support, "optimization_locked_end", False):
                support.end_face_code = line.end_face_code
        layout_mod._assign_tributary_widths(system.supports, project.excavation)
        system.columns = layout_mod.make_column_elements(project.excavation, system.supports, max_unbraced_span_m=column_max_span)
        system.support_nodes = layout_mod.make_support_wale_nodes(system.supports, system.wale_beams)
    return adjustments


def _lock_summary(project: Project) -> dict[str, Any]:
    ret = getattr(project, "retaining_system", None)
    supports = ret.supports if ret else []
    return {
        "supportLineCount": len([s for s in supports if getattr(s, "optimization_locked", False)]),
        "endpointCount": len([s for s in supports if getattr(s, "optimization_locked_start", False) or getattr(s, "optimization_locked_end", False)]),
        "supportLevelCount": len(_locked_level_indices(project)),
        "obstacleBoundaryCount": len(_obstacle_lock_ids(project)),
        "levelIndices": sorted(_locked_level_indices(project)),
        "obstacleIds": sorted(_obstacle_lock_ids(project)),
    }


def _symmetry_score(system: RetainingSystem, project: Project) -> float:
    b = _bounds(project)
    if not b or not system.supports:
        return 0.5
    min_x, min_y, max_x, max_y = b
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    mains = [s for s in system.supports if s.level_index == 1 and s.support_role == "main_strut"]
    if len(mains) <= 1:
        return 0.5
    axis = _long_axis(project)
    vals = sorted(((s.start.x + s.end.x) / 2.0 - cx) if axis == "x" else ((s.start.y + s.end.y) / 2.0 - cy) for s in mains)
    scale = max(max(abs(v) for v in vals), 1.0)
    mismatch = 0.0
    pairs = 0
    for a, bval in zip(vals, reversed(vals)):
        mismatch += abs(a + bval) / scale
        pairs += 1
    return max(0.0, min(1.0, 1.0 - mismatch / max(pairs, 1)))


def _muck_path_continuity_score(project: Project, system: RetainingSystem, issue_metrics: dict[str, Any]) -> float:
    obstacles = getattr(project.excavation, "obstacles", []) if project.excavation else []
    muck = [o for o in obstacles if getattr(o, "active", True) and getattr(o, "obstacle_type", "") == "muck_out_opening"]
    if not muck:
        # Missing logistics data: do not fail optimization, but report reduced confidence.
        return 0.70
    conflict = float(issue_metrics.get("obstacleConflictCount", 0) or 0)
    if conflict > 0:
        return 0.0
    # Prefer layouts that keep at least one support bay in the opening zone clear.
    return 1.0


def _axial_peak_proxy(system: RetainingSystem, bay: list[float], spans: list[float], target_spacing: float) -> float:
    if not spans:
        return 1.0
    max_bay = max(bay or [target_spacing])
    # Larger span * bay usually leads to larger tributary load / support reaction.
    return max(spans) * max_bay / 180.0


def _column_count_penalty(system: RetainingSystem) -> float:
    support_count = max(len(system.supports), 1)
    density = len(system.columns) / support_count
    return abs(density - 0.22) / 0.35


def _endpoint_validity_penalty(system: RetainingSystem) -> float:
    endpoints = 0
    missing = 0
    for s in system.supports:
        if s.support_role == "ring_strut":
            continue
        endpoints += 2
        if not s.start_face_code:
            missing += 1
        if not s.end_face_code:
            missing += 1
    return missing / max(endpoints, 1)


def _replacement_path_penalty(project: Project) -> float:
    ret = project.retaining_system
    if not ret or not ret.replacement_path:
        return 1.0
    supports = ret.supports or []
    if not supports:
        return 1.0
    stage_levels = {int(s.level_index) for s in supports}
    path_levels = set()
    for item in ret.replacement_path:
        text = str(item)
        for level in stage_levels:
            if f"{level}" in text or f"L{level}" in text:
                path_levels.add(level)
    missing_ratio = 1.0 - len(path_levels) / max(len(stage_levels), 1)
    return max(0.0, min(1.0, missing_ratio))


def _hard_constraints(project: Project, quality_metrics: dict[str, Any], system: RetainingSystem) -> dict[str, Any]:
    endpoint_missing = _endpoint_validity_penalty(system)
    obstacle_count = int(quality_metrics.get("obstacleConflictCount", 0) or 0)
    crossing_count = int(quality_metrics.get("supportCrossingCount", 0) or 0)
    repl_penalty = _replacement_path_penalty(project)
    col_obstacle_hits = 0
    obstacles = layout_mod._active_obstacle_polygons(getattr(project.excavation, "obstacles", []) if project.excavation else [])
    for col in system.columns:
        if not layout_mod._point_avoids_obstacles(col.location, obstacles):
            col_obstacle_hits += 1
    passed = crossing_count == 0 and obstacle_count == 0 and endpoint_missing == 0 and col_obstacle_hits == 0 and repl_penalty < 0.75
    return {
        "passed": passed,
        "supportNoCrossing": crossing_count == 0,
        "supportNoObstacleConflict": obstacle_count == 0,
        "endpointsOnWaleOrRingNodes": endpoint_missing == 0,
        "temporaryColumnsOutsideObstacles": col_obstacle_hits == 0,
        "replacementPathContinuity": repl_penalty < 0.75,
        "supportCrossingCount": crossing_count,
        "obstacleConflictCount": obstacle_count,
        "missingEndpointRatio": round(endpoint_missing, 4),
        "columnObstacleHitCount": col_obstacle_hits,
        "replacementPathPenalty": round(repl_penalty, 4),
    }


def _objective_terms(project: Project, system: RetainingSystem, target_spacing: float, issue_metrics: dict[str, Any]) -> dict[str, float]:
    bay = _bay_spacings(system)
    spans = _span_lengths(system)
    spacing_deviation = mean([abs(v - 5.0) / 5.0 for v in bay]) if bay else 1.0
    bay_cv = (pstdev(bay) / mean(bay)) if bay and mean(bay) > 1e-9 and len(bay) > 1 else 0.0
    span_penalty = min(max(spans) / 36.0, 2.0) if spans else 1.0
    muck_score = _muck_path_continuity_score(project, system, issue_metrics)
    symmetry = _symmetry_score(system, project)
    axial_proxy = _axial_peak_proxy(system, bay, spans, target_spacing)
    return {
        "spacingDeviation": round(max(0.0, spacing_deviation + bay_cv * 0.5), 4),
        "spanLength": round(max(0.0, span_penalty), 4),
        "obstacleConflict": round(float(issue_metrics.get("obstacleConflictCount", 0) or 0), 4),
        "supportCrossing": round(float(issue_metrics.get("supportCrossingCount", 0) or 0), 4),
        "columnCount": round(max(0.0, _column_count_penalty(system)), 4),
        "muckPathContinuity": round(max(0.0, 1.0 - muck_score), 4),
        "axialPeakProxy": round(max(0.0, axial_proxy), 4),
        "symmetry": round(max(0.0, 1.0 - symmetry), 4),
        "endpointValidity": round(_endpoint_validity_penalty(system), 4),
        "replacementContinuity": round(_replacement_path_penalty(project), 4),
    }


def _candidate_score(quality_score: float, terms: dict[str, float], hard: dict[str, Any], fail_count: int, warning_count: int, objective_weights: dict[str, float] | None = None) -> float:
    weights = normalize_objective_weights(objective_weights)
    penalty = sum(weights[k] * min(float(terms.get(k, 0.0)), 3.0) for k in weights)
    penalty += fail_count * 35.0 + warning_count * 1.5
    if not hard.get("passed"):
        # Hard constraints do not necessarily make the candidate unusable for
        # diagnosis, but they must rank below feasible candidates.
        penalty += 65.0
    score = 0.52 * quality_score + 0.48 * max(0.0, 100.0 - penalty)
    return round(max(0.0, min(100.0, score)), 2)


def _line_position_index(system: RetainingSystem, project: Project) -> dict[tuple[int, str], list[SupportElement]]:
    axis = _long_axis(project)
    groups: dict[tuple[int, str], list[SupportElement]] = {}
    for level in sorted({s.level_index for s in system.supports if s.support_role == "main_strut"}):
        items = [s for s in system.supports if s.level_index == level and s.support_role == "main_strut"]
        items.sort(key=lambda s: ((s.start.x + s.end.x) / 2.0) if axis == "x" else ((s.start.y + s.end.y) / 2.0))
        groups[(level, axis)] = items
    return groups


def _pattern_offset(pattern: str, amplitude: float, index: int, count: int) -> float:
    if amplitude == 0.0 or count <= 1:
        return 0.0
    mid = (count - 1) / 2.0
    rel = index - mid
    if pattern.startswith("global_shift"):
        return amplitude
    if pattern.startswith("symmetric_expand"):
        return abs(amplitude) if rel >= 0 else -abs(amplitude)
    if pattern.startswith("symmetric_compress"):
        return -abs(amplitude) if rel >= 0 else abs(amplitude)
    if pattern.startswith("alternating_escape") or pattern.startswith("obstacle_escape"):
        return abs(amplitude) if index % 2 == 0 else -abs(amplitude)
    if pattern.startswith("center_gap"):
        # Open a larger central working bay by pushing the two lines closest to
        # the centre outward; outer lines remain almost unchanged.
        if abs(rel) <= 0.75:
            return abs(amplitude) if rel >= 0 else -abs(amplitude)
        return 0.35 * abs(amplitude) if rel > 0 else -0.35 * abs(amplitude)
    return 0.0


def _shift_main_support_positions(project: Project, system: RetainingSystem, pattern: str, amplitude: float, column_max_span: float = layout_mod.COLUMN_MAX_UNBRACED_SPAN_M) -> list[dict[str, Any]]:
    if not project.excavation or amplitude == 0.0:
        return []
    points = layout_mod._dedup_points(list(project.excavation.outline.points))
    obstacles = layout_mod._active_obstacle_polygons(getattr(project.excavation, "obstacles", []))
    min_x, min_y, max_x, max_y, span_x, span_y = layout_mod._bounds(points)
    long_is_x = span_x >= span_y
    adjustments: list[dict[str, Any]] = []
    groups = _line_position_index(system, project)
    for (_level, _axis), items in groups.items():
        for idx, support in enumerate(items):
            if _support_is_fully_locked(project, support):
                continue
            before_mid = (support.start.x + support.end.x) / 2.0 if long_is_x else (support.start.y + support.end.y) / 2.0
            offset = _pattern_offset(pattern, amplitude, idx, len(items))
            if abs(offset) <= 1e-9:
                continue
            if long_is_x:
                segments, used, shifted = layout_mod._find_viable_main_line(points=points, obstacles=obstacles, long_is_x=True, base_coord=before_mid + offset, min_coord=min_x, max_coord=max_x, span=span_x)
            else:
                segments, used, shifted = layout_mod._find_viable_main_line(points=points, obstacles=obstacles, long_is_x=False, base_coord=before_mid + offset, min_coord=min_y, max_coord=max_y, span=span_y)
            if not segments:
                continue
            # Pick segment closest to old support midpoint so concave pits keep the same bay branch.
            old_cx, old_cy = (support.start.x + support.end.x) / 2.0, (support.start.y + support.end.y) / 2.0
            start, end = min(segments, key=lambda pair: math.hypot(((pair[0].x + pair[1].x) / 2.0) - old_cx, ((pair[0].y + pair[1].y) / 2.0) - old_cy))
            old = {"start": support.start.model_dump(mode="json"), "end": support.end.model_dump(mode="json")}
            support.start = start
            support.end = end
            support.span_length = round(layout_mod._distance(start, end), 3)
            support.layout_note = (support.layout_note or "") + f" V2.0.7 约束优化器按 {pattern} 调整支撑线位置，变量位移 {used - before_mid:+.2f}m。"
            adjustments.append({
                "supportId": support.id,
                "supportCode": support.code,
                "pattern": pattern,
                "requestedOffset": round(offset, 3),
                "actualOffset": round(used - before_mid, 3),
                "before": old,
                "after": {"start": support.start.model_dump(mode="json"), "end": support.end.model_dump(mode="json")},
                "endpointConstraint": "snapped_to_outline_wale_scanline",
            })
    # Reattach endpoints to wall faces, rebuild tributary widths, columns and nodes.
    layout_mod._attach_faces([layout_mod.SupportLayoutLine(s.support_role, s.start, s.end, float(s.span_length or 0.0), s.bay_spacing, s.layout_note or "") for s in []], project.excavation)  # no-op guard for private import stability
    temp_lines = []
    for s in system.supports:
        line = layout_mod.SupportLayoutLine(s.support_role, s.start, s.end, float(s.span_length or 0.0), s.bay_spacing, s.layout_note or "")
        temp_lines.append(line)
    layout_mod._attach_faces(temp_lines, project.excavation)
    by_code = {s.code: s for s in system.supports}
    for line, support in zip(temp_lines, system.supports):
        support.start_face_code = line.start_face_code
        support.end_face_code = line.end_face_code
    layout_mod._assign_tributary_widths(system.supports, project.excavation)
    system.columns = layout_mod.make_column_elements(project.excavation, system.supports, max_unbraced_span_m=column_max_span)
    system.support_nodes = layout_mod.make_support_wale_nodes(system.supports, system.wale_beams)
    return adjustments




def _candidate_id(target_spacing: float, column_span: float, pattern: str, amplitude: float, topology_strategy: str = "balanced_grid") -> str:
    pattern_key = pattern.replace(".", "p").replace("-", "m")
    topology_key = topology_strategy.replace("_", "-")
    return f"slopt-{topology_key}-t{str(target_spacing).replace('.', 'p')}-c{str(column_span).replace('.', 'p')}-{pattern_key}-{str(amplitude).replace('.', 'p').replace('-', 'm')}"


def _locked_supports(project: Project) -> list[SupportElement]:
    ret = getattr(project, "retaining_system", None)
    if not ret:
        return []
    locked_levels = _locked_level_indices(project)
    locked: list[SupportElement] = []
    for support in ret.supports:
        if getattr(support, "optimization_locked", False) or int(support.level_index) in locked_levels:
            item = support.model_copy(deep=True)
            if int(item.level_index) in locked_levels:
                item.optimization_locked = True
                item.optimization_lock_reason = item.optimization_lock_reason or "operator locked support level"
            locked.append(item)
    return locked


def _apply_locked_supports(project: Project, system: RetainingSystem, column_max_span: float = layout_mod.COLUMN_MAX_UNBRACED_SPAN_M) -> int:
    locked = _locked_supports(project)
    if not locked:
        return 0
    retained: list[SupportElement] = []
    for locked_item in locked:
        locked_mid = ((locked_item.start.x + locked_item.end.x) / 2.0, (locked_item.start.y + locked_item.end.y) / 2.0)
        best_index = None
        best_dist = 1e18
        for idx, trial in enumerate(system.supports):
            if trial.support_role != locked_item.support_role or trial.level_index != locked_item.level_index:
                continue
            trial_mid = ((trial.start.x + trial.end.x) / 2.0, (trial.start.y + trial.end.y) / 2.0)
            dist = math.hypot(trial_mid[0] - locked_mid[0], trial_mid[1] - locked_mid[1])
            if dist < best_dist:
                best_dist = dist
                best_index = idx
        if best_index is not None:
            system.supports.pop(best_index)
        retained.append(locked_item)
    system.supports.extend(retained)
    system.supports.sort(key=lambda s: (s.level_index, s.support_role, s.code))
    if project.excavation:
        temp_lines = [layout_mod.SupportLayoutLine(s.support_role, s.start, s.end, float(s.span_length or 0.0), s.bay_spacing, s.layout_note or "") for s in system.supports]
        layout_mod._attach_faces(temp_lines, project.excavation)
        for line, support in zip(temp_lines, system.supports):
            support.start_face_code = line.start_face_code
            support.end_face_code = line.end_face_code
        layout_mod._assign_tributary_widths(system.supports, project.excavation)
        system.columns = layout_mod.make_column_elements(project.excavation, system.supports, max_unbraced_span_m=column_max_span)
        system.support_nodes = layout_mod.make_support_wale_nodes(system.supports, system.wale_beams)
    return len(retained)


def _point_payload(point: Point2D) -> dict[str, float]:
    return {"x": round(float(point.x), 4), "y": round(float(point.y), 4)}


def _plan_geometry(project: Project, system: RetainingSystem, adjustments: list[dict[str, Any]]) -> dict[str, Any]:
    changed_ids = {str(a.get("supportId")) for a in adjustments if a.get("supportId")}
    excavation = getattr(project, "excavation", None)
    outline = []
    obstacles = []
    if excavation and getattr(excavation, "outline", None):
        outline = [_point_payload(p) for p in excavation.outline.points]
        for obs in getattr(excavation, "obstacles", []) or []:
            if not getattr(obs, "active", True):
                continue
            opt = getattr(obs, "outline", None)
            pts = [_point_payload(p) for p in opt.points] if opt and getattr(opt, "points", None) else []
            obstacles.append({"id": obs.id, "name": obs.name, "type": obs.obstacle_type, "points": pts})
    supports = []
    for spt in system.supports:
        supports.append({
            "id": spt.id, "code": spt.code, "role": spt.support_role, "levelIndex": spt.level_index,
            "start": _point_payload(spt.start), "end": _point_payload(spt.end),
            "wallConnectionStart": _point_payload(spt.start_wall_connection) if getattr(spt, "start_wall_connection", None) else None,
            "wallConnectionEnd": _point_payload(spt.end_wall_connection) if getattr(spt, "end_wall_connection", None) else None,
            "spanLength": spt.span_length, "baySpacing": spt.bay_spacing,
            "elevation": spt.elevation, "centerlineOffsetM": getattr(spt, "centerline_offset_m", None),
            "topologyFamily": getattr(spt, "topology_family", "direct_grid"),
            "locked": bool(getattr(spt, "optimization_locked", False)),
            "lockState": _support_lock_signature(spt),
            "changed": spt.id in changed_ids,
        })
    columns = [{"id": c.id, "code": c.code, "location": _point_payload(c.location)} for c in system.columns]
    elevations = sorted({float(s.elevation) for s in system.supports}, reverse=True)
    return {"outline": outline, "supports": supports, "columns": columns, "obstacles": obstacles, "lockSummary": _lock_summary(project), "supportElevations": elevations, "layoutSummary": dict(system.layout_summary or {})}


def _delta_geometry(adjustments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "changedSupportCount": len(adjustments),
        "adjustments": adjustments[:20],
    }


def build_support_system_from_candidate(project: Project, target_spacing: float, column_span: float, pattern: str = "as_generated", amplitude: float = 0.0, topology_strategy: str = "balanced_grid") -> tuple[RetainingSystem | None, list[dict[str, Any]]]:
    if not project.excavation:
        return None, []
    trial_project = project.model_copy(deep=True)
    config = design_service.support_layout_config_from_settings(
        project.design_settings,
        topology_strategy=topology_strategy,
        target_spacing=target_spacing,
        column_span=column_span,
    )
    trial_project.retaining_system = design_service.auto_supports(trial_project.excavation, trial_project.retaining_system, layout_config=config)
    if getattr(project, "retaining_system", None):
        trial_project.retaining_system.optimization_locks = list(project.retaining_system.optimization_locks or [])
    _apply_locked_supports(project, trial_project.retaining_system)
    adjustments = _shift_main_support_positions(trial_project, trial_project.retaining_system, pattern, amplitude, column_max_span=column_span)
    adjustments.extend(_apply_endpoint_locks(project, trial_project.retaining_system, column_max_span=column_span))
    return trial_project.retaining_system, adjustments


def _geometry_fingerprint(system: RetainingSystem, precision: float = 0.25) -> tuple[tuple[int, int, int, int, int], ...]:
    def q(value: float) -> int:
        return int(round(float(value) / precision))
    rows: list[tuple[int, int, int, int, int]] = []
    for s in system.supports:
        if s.support_role != "main_strut":
            continue
        rows.append((int(s.level_index), q(s.start.x), q(s.start.y), q(s.end.x), q(s.end.y)))
    return tuple(sorted(rows))


def _geometry_difference_score(adjustments: list[dict[str, Any]], support_count: int) -> float:
    if not adjustments or support_count <= 0:
        return 0.0
    total = 0.0
    moved = 0
    for item in adjustments:
        before = item.get("before") or {}
        after = item.get("after") or {}
        bs, be = before.get("start"), before.get("end")
        aas, ae = after.get("start"), after.get("end")
        if not (bs and be and aas and ae):
            continue
        bmx = (float(bs.get("x", 0.0)) + float(be.get("x", 0.0))) / 2.0
        bmy = (float(bs.get("y", 0.0)) + float(be.get("y", 0.0))) / 2.0
        amx = (float(aas.get("x", 0.0)) + float(ae.get("x", 0.0))) / 2.0
        amy = (float(aas.get("y", 0.0)) + float(ae.get("y", 0.0))) / 2.0
        dist = math.hypot(amx - bmx, amy - bmy)
        if dist > 0.05:
            moved += 1
            total += dist
    return round((moved / max(support_count, 1)) * 0.55 + min(total / max(support_count, 1) / 2.0, 1.0) * 0.45, 4)

def _export_readiness(candidate_status: str, hard: dict[str, Any], quality_metrics: dict[str, Any]) -> dict[str, Any]:
    feasible = bool(hard.get("passed")) and candidate_status != "fail"
    return {
        "calculationReady": feasible,
        "ifcReady": feasible,
        "reportReady": feasible,
        "blockingReason": None if feasible else "存在支撑交叉、障碍冲突、端点未吸附或换撑路径不连续等硬约束问题。",
        "supportCrossingCount": quality_metrics.get("supportCrossingCount", 0),
        "obstacleConflictCount": quality_metrics.get("obstacleConflictCount", 0),
    }


def _candidate_note(target_spacing: float, column_max_span: float, pattern: str, terms: dict[str, float], hard: dict[str, Any]) -> str:
    hard_text = "硬约束满足" if hard.get("passed") else "硬约束未完全满足"
    return (
        f"目标分仓 {target_spacing:.1f}m，立柱最大服务跨 {column_max_span:.1f}m，支撑线变量策略 {pattern}；"
        f"{hard_text}。目标函数包含间距偏差、跨长、障碍冲突、交叉、立柱数量、出土路径、轴力峰值代理和对称性。"
    )


def optimize_support_layout_candidates(project: Project, max_candidates: int = 5, objective_weights: dict[str, float] | None = None, preset: str | None = None) -> tuple[RetainingSystem | None, list[SupportLayoutOptimizationCandidate]]:
    if not project.excavation:
        return None, []
    weights = preset_objective_weights(preset, objective_weights)
    locked = _locked_supports(project)
    locked_ids = [s.id for s in locked]
    candidates: list[tuple[SupportLayoutOptimizationCandidate, RetainingSystem]] = []
    spacing_values = [4.0, 5.0, 6.0]
    column_values = [12.0, 18.0]
    for topology_strategy in TOPOLOGY_STRATEGIES:
        for target_spacing in spacing_values:
            for column_span in column_values:
                strategy_patterns = POSITION_PATTERNS[:3] if topology_strategy != "direct_grid" else POSITION_PATTERNS[:2]
                for pattern, amplitude in strategy_patterns:
                    # V2.6.0: avoid deep-copying historical calculation results and large
                    # report payloads for every candidate.  Candidate generation only needs
                    # geometry, settings and the current retaining system.
                    trial_project = project.model_copy(deep=False)
                    trial_project.calculation_results = []
                    trial_project.calculation_cases = []
                    trial_project.retaining_system = project.retaining_system.model_copy(deep=True) if project.retaining_system else None
                    layout_config = design_service.support_layout_config_from_settings(
                        project.design_settings,
                        topology_strategy=topology_strategy,
                        target_spacing=target_spacing,
                        column_span=column_span,
                    )
                    trial_project.retaining_system = design_service.auto_supports(
                        trial_project.excavation,
                        trial_project.retaining_system,
                        layout_config=layout_config,
                    )
                    if getattr(project, "retaining_system", None):
                        trial_project.retaining_system.optimization_locks = list(project.retaining_system.optimization_locks or [])
                    locked_count = _apply_locked_supports(project, trial_project.retaining_system, column_max_span=column_span)
                    adjustments = _shift_main_support_positions(trial_project, trial_project.retaining_system, pattern, amplitude, column_max_span=column_span)
                    adjustments.extend(_apply_endpoint_locks(project, trial_project.retaining_system, column_max_span=column_span))
                    quality = evaluate_support_layout_quality(trial_project)
                    metrics = dict(quality.metrics or {})
                    fail_count = sum(1 for i in quality.issues if i.severity == "fail")
                    warning_count = sum(1 for i in quality.issues if i.severity == "warning")
                    terms = _objective_terms(trial_project, trial_project.retaining_system, target_spacing, metrics)
                    hard = _hard_constraints(trial_project, metrics, trial_project.retaining_system)
                    spans = _span_lengths(trial_project.retaining_system)
                    bay = _bay_spacings(trial_project.retaining_system)
                    score = _candidate_score(quality.score, terms, hard, fail_count, warning_count, weights)
                    fingerprint = _geometry_fingerprint(trial_project.retaining_system)
                    difference_score = _geometry_difference_score(adjustments, len(trial_project.retaining_system.supports))
                    candidate = SupportLayoutOptimizationCandidate(
                        id=_candidate_id(target_spacing, column_span, pattern, amplitude, topology_strategy),
                        score=score,
                        status=quality.status,
                        target_spacing=target_spacing,
                        column_max_span=column_span,
                        objective_terms=terms,
                        soft_objectives={
                            "spacingCloseTo3To6m": 1.0 - min(1.0, terms.get("spacingDeviation", 1.0)),
                            "shortSpanLength": 1.0 - min(1.0, terms.get("spanLength", 1.0) / 2.0),
                            "reasonableColumnCount": 1.0 - min(1.0, terms.get("columnCount", 1.0)),
                            "lowAxialPeakProxy": 1.0 - min(1.0, terms.get("axialPeakProxy", 1.0)),
                            "continuousMuckPath": 1.0 - min(1.0, terms.get("muckPathContinuity", 1.0)),
                            "planSymmetry": 1.0 - min(1.0, terms.get("symmetry", 1.0)),
                        },
                        hard_constraints=hard,
                        variable_summary={
                            "variableType": "whole_scheme_topology_and_line_position",
                            "topologyFamily": topology_strategy,
                            "schemeLabel": {"hybrid_diagonal": "斜撑+短对撑混合", "bidirectional_grid": "双向网格", "direct_grid": "传统直对撑"}.get(topology_strategy, topology_strategy),
                            "positionPattern": pattern,
                            "lineOffsetAmplitude": amplitude,
                            "adjustedLineCount": len(adjustments),
                            "targetSpacing": target_spacing,
                            "columnMaxSpan": column_span,
                            "lockedSupportCount": locked_count,
                            "lockedSupportIds": locked_ids,
                            "lockSummary": _lock_summary(project),
                            "geometryFingerprint": ";".join(["-".join(map(str, row)) for row in fingerprint[:80]]),
                            "geometryDifferenceScore": difference_score,
                            "materiallyDifferent": difference_score >= 0.03 or pattern == "as_generated",
                        },
                        line_adjustments=adjustments[:30],
                        plan_geometry=_plan_geometry(trial_project, trial_project.retaining_system, adjustments),
                        delta_geometry=_delta_geometry(adjustments),
                        weight_summary={"weights": weights, "preset": preset or "custom", "lockedSupportCount": locked_count, "lockedSupportIds": locked_ids, "lockSummary": _lock_summary(project)},
                        metrics=metrics,
                        issue_count=len(quality.issues),
                        fail_count=fail_count,
                        warning_count=warning_count,
                        support_count=len(trial_project.retaining_system.supports),
                        column_count=len(trial_project.retaining_system.columns),
                        max_span_length=round(max(spans), 3) if spans else None,
                        max_bay_spacing=round(max(bay), 3) if bay else None,
                        crossing_count=int(metrics.get("supportCrossingCount", 0) or 0),
                        obstacle_conflict_count=int(metrics.get("obstacleConflictCount", 0) or 0),
                        axial_peak_proxy=round(_axial_peak_proxy(trial_project.retaining_system, bay, spans, target_spacing), 3) if spans else None,
                        symmetry_score=round(_symmetry_score(trial_project.retaining_system, trial_project), 3),
                        muck_path_continuity_score=round(_muck_path_continuity_score(trial_project, trial_project.retaining_system, metrics), 3),
                        export_readiness=_export_readiness(quality.status, hard, metrics),
                        constructability_note=(
                            {"hybrid_diagonal": "角部采用短斜撑并减少靠角超长对撑；", "bidirectional_grid": "双向网格直接约束长边与回墙；", "direct_grid": "传统短跨直对撑，构造直观；"}.get(topology_strategy, "")
                            + _candidate_note(target_spacing, column_span, pattern, terms, hard)
                        ),
                    )
                    candidates.append((candidate, trial_project.retaining_system))
    # Feasible candidates first, then score, geometry diversity, then fewer supports/columns for constructability.
    candidates.sort(key=lambda item: (not item[0].hard_constraints.get("passed", False), -item[0].score, -float(item[0].variable_summary.get("geometryDifferenceScore", 0.0)), _status_rank(item[0].status), item[0].support_count, item[0].column_count))
    ranked: list[SupportLayoutOptimizationCandidate] = []
    seen: set[tuple] = set()
    selected_system: RetainingSystem | None = None

    def _structural_signature(candidate: SupportLayoutOptimizationCandidate) -> tuple[str, int, int, int, int]:
        return (
            str((candidate.variable_summary or {}).get("topologyFamily", "unknown")),
            int(candidate.support_count or 0),
            int(candidate.column_count or 0),
            int(round(float(candidate.max_bay_spacing or 0.0) * 10.0)),
            int(round(float(candidate.max_span_length or 0.0) * 10.0)),
        )

    structural_seen: set[tuple[str, int, int, int, int]] = set()

    def add_candidate(candidate: SupportLayoutOptimizationCandidate, system: RetainingSystem, *, force: bool = False) -> bool:
        nonlocal selected_system
        key = _geometry_fingerprint(system)
        structural_key = _structural_signature(candidate)
        if len(ranked) >= max_candidates:
            return False
        if key in seen and structural_key in structural_seen:
            return False
        # Operators should not compare three cosmetic 1 m shifts that have the
        # same support count, same column count and same force path.  The first
        # item is the recommended baseline; later items must either be a true
        # count/spacing alternative or have a clear geometric displacement.
        difference = float(candidate.variable_summary.get("geometryDifferenceScore", 0.0) or 0.0)
        has_structural_delta = not structural_seen or structural_key not in structural_seen
        if ranked and not force and not has_structural_delta and difference < 0.12:
            return False
        seen.add(key)
        structural_seen.add(structural_key)
        candidate.rank = len(ranked) + 1
        ranked.append(candidate)
        if selected_system is None:
            selected_system = system.model_copy(deep=True)
        return True

    # Select one feasible representative from each topology family first.  The
    # operator compares complete A/B/C schemes rather than confirming wall faces
    # one by one.  Remaining slots are filled by score and spacing diversity.
    family_best: list[tuple[SupportLayoutOptimizationCandidate, RetainingSystem]] = []
    for family in TOPOLOGY_STRATEGIES:
        item = next((pair for pair in candidates if pair[0].hard_constraints.get("passed", False) and str((pair[0].variable_summary or {}).get("topologyFamily")) == family), None)
        if item:
            family_best.append(item)
    family_best.sort(key=lambda item: -item[0].score)
    for candidate, system in family_best:
        add_candidate(candidate, system, force=True)
        if len(ranked) >= min(max_candidates, 3):
            break
    if not ranked and candidates:
        add_candidate(candidates[0][0], candidates[0][1], force=True)

    used_spacing_buckets: set[int] = set()
    used_column_buckets: set[int] = set()
    if ranked:
        used_spacing_buckets.add(int(round(ranked[0].target_spacing * 10.0)))
        used_column_buckets.add(int(round(ranked[0].column_max_span * 10.0)))

    for candidate, system in candidates:
        if candidate.score < 1.0 or not candidate.hard_constraints.get("passed", False):
            continue
        spacing_bucket = int(round(candidate.target_spacing * 10.0))
        column_bucket = int(round(candidate.column_max_span * 10.0))
        if spacing_bucket in used_spacing_buckets:
            continue
        if add_candidate(candidate, system, force=True):
            used_spacing_buckets.add(spacing_bucket)
            used_column_buckets.add(column_bucket)
        if len(ranked) >= max_candidates:
            break

    for candidate, system in candidates:
        if candidate.score < 1.0 or not candidate.hard_constraints.get("passed", False):
            continue
        column_bucket = int(round(candidate.column_max_span * 10.0))
        if column_bucket in used_column_buckets:
            continue
        if add_candidate(candidate, system):
            used_column_buckets.add(column_bucket)
        if len(ranked) >= max_candidates:
            break

    used_patterns = {c.variable_summary.get("positionPattern") for c in ranked if c.variable_summary}
    for candidate, system in candidates:
        pattern = candidate.variable_summary.get("positionPattern")
        if pattern in used_patterns:
            continue
        if candidate.score < 1.0 or not candidate.hard_constraints.get("passed", False):
            continue
        if add_candidate(candidate, system):
            used_patterns.add(pattern)
        if len(ranked) >= max_candidates:
            break

    for candidate, system in candidates:
        add_candidate(candidate, system)
        if len(ranked) >= max_candidates:
            break
    for idx, candidate in enumerate(ranked, start=1):
        candidate.rank = idx
    return selected_system, ranked
