from __future__ import annotations

import math
import os
import gc
import hashlib

from app.services.runtime_diagnostics import append_event, memory_event
from statistics import mean, pstdev
from typing import Any

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Project, RetainingSystem, SupportElement, SupportLayoutOptimizationCandidate, Point2D
from app.services import design_service
from app.services import support_layout as layout_mod
from app.services.support_deep_design import evaluate_support_deep_design
from app.services.support_candidate_contract import stamp_candidate_source, support_candidate_source_hash

SUPPORT_CANDIDATE_CONTRACT_VERSION = "3.66-concave-transfer-search-v1"

OBJECTIVE_WEIGHTS: dict[str, float] = {
    "spacingDeviation": 20.0,
    "spanLength": 16.0,
    "obstacleConflict": 34.0,
    # Plan cleanliness is a principal objective.  Proper same-level crossings
    # remain a hard constraint; the high weight also keeps diagnostic/infeasible
    # candidates at the bottom and makes the design intent explicit to the UI.
    "supportCrossing": 80.0,
    # Legal T/Y/X nodes are sometimes unavoidable, but fewer internal junctions
    # produce a clearer load path, simpler nodes, and cleaner construction plans.
    "junctionComplexity": 64.0,
    # Multiple braces/struts converging to the same retaining-wall or wale node
    # are explicitly minimized because they create congested bearing plates,
    # local wale force peaks and difficult reinforcement detailing.
    "wallJunctionComplexity": 72.0,
    "columnCount": 7.0,
    "muckPathContinuity": 8.0,
    "axialPeakProxy": 11.0,
    "symmetry": 10.0,
    "endpointValidity": 18.0,
    "replacementContinuity": 8.0,
    "memberUtilization": 30.0,
    "bucklingRisk": 26.0,
    "constructionEffects": 14.0,
    "materialVolume": 8.0,
    "nodeReadiness": 16.0,
    "loadPathRedundancy": 12.0,
    "forceUniformity": 14.0,
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
        weights["bucklingRisk"] = max(weights["bucklingRisk"], 28.0)
    elif preset == "low_axial_force":
        weights["axialPeakProxy"] = max(weights["axialPeakProxy"], 28.0)
        weights["spanLength"] = max(weights["spanLength"], 22.0)
        weights["memberUtilization"] = max(weights["memberUtilization"], 38.0)
        weights["constructionEffects"] = max(weights["constructionEffects"], 20.0)
    elif preset == "muck_path_priority":
        weights["muckPathContinuity"] = max(weights["muckPathContinuity"], 30.0)
        weights["obstacleConflict"] = max(weights["obstacleConflict"], 44.0)
    elif preset == "clean_support_layout":
        weights["supportCrossing"] = 80.0
        weights["junctionComplexity"] = 80.0
        weights["wallJunctionComplexity"] = 80.0
        weights["symmetry"] = max(weights["symmetry"], 18.0)
        weights["spanLength"] = max(weights["spanLength"], 18.0)
        weights["memberUtilization"] = max(weights["memberUtilization"], 30.0)
        weights["bucklingRisk"] = max(weights["bucklingRisk"], 26.0)
    elif preset == "balanced":
        pass
    return weights

HARD_CONSTRAINT_LABELS = [
    "support_no_crossing",
    "support_no_muck_ramp_protected_crossing",
    "support_endpoints_on_wale_or_ring",
    "support_no_support_to_support_terminal",
    "support_station_minimum_separation",
    "temporary_columns_outside_obstacles",
    "replacement_path_continuity",
    "support_member_preliminary_stability",
    "support_construction_effect_envelope",
]
SOFT_OBJECTIVE_LABELS = [
    "minimum_plan_intersection_and_junction_count",
    "minimum_support_station_clustering",
    "minimum_wall_connection_convergence_count",
    "spacing_close_to_3_6m",
    "short_span_length",
    "reasonable_column_count",
    "low_axial_peak_proxy",
    "continuous_muck_path",
    "plan_symmetry",
    "low_member_interaction_utilization",
    "low_buckling_risk",
    "low_construction_effect_ratio",
    "low_material_volume",
    "node_detailing_readiness",
    "load_path_redundancy",
    "balanced_support_force_distribution",
]

TARGET_SPACING_VALUES = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
COLUMN_MAX_SPAN_VALUES = [12.0, 15.0, 18.0]
TOPOLOGY_STRATEGIES = ["direct_grid", "hybrid_diagonal"]
POSITION_PATTERNS: list[tuple[str, float]] = [
    ("as_generated", 0.0),
    ("global_shift_plus_1p0", 1.0),
    ("global_shift_minus_1p0", -1.0),
    ("symmetric_expand_1p0", 1.0),
    ("symmetric_compress_1p0", 1.0),
    ("alternating_escape_1p5", 1.5),
    ("center_gap_1p5", 1.5),
]


def _available_topology_strategies(project: Project) -> list[str]:
    """Select solver-compatible support families from the V3.28 shape taxonomy.

    Axial wall-to-wall grids and closed ring/radial systems are supported.
    Orthogonal concave plans receive a zoned-direct preliminary candidate; if
    junction transfer cannot be closed without a frame node, the candidate is
    retained as a single controlled-block diagnosis rather than multiplied into
    cosmetic A/B/C variants.
    """
    if not project.excavation or not project.excavation.outline.points:
        return ["direct_grid"]
    has_center_island = any(
        getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
        for item in (project.excavation.obstacles or [])
    )
    diag = layout_mod.plan_shape_diagnostics(
        list(project.excavation.outline.points),
        local_pit_count=len(project.excavation.local_pits or []),
        has_center_island=has_center_island,
    )
    families = [str(item) for item in (diag.get("supportedTopologyFamilies") or [])]
    supported = [item for item in families if item in {"direct_grid", "hybrid_diagonal", "ring_radial", "zoned_direct"}]
    return supported or ["direct_grid"]


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
    if not project.excavation or not project.excavation.outline.points or not system.supports:
        return 0.5
    axes = layout_mod._plan_axes(list(project.excavation.outline.points))
    mains = [s for s in system.supports if s.level_index == 1 and s.support_role == "main_strut"]
    if len(mains) <= 1:
        return 0.5
    vals: list[float] = []
    for support in mains:
        midpoint = Point2D(x=(support.start.x + support.end.x) / 2.0, y=(support.start.y + support.end.y) / 2.0)
        vals.append(layout_mod._local_coordinates(midpoint, axes).x)
    vals.sort()
    centre = 0.5 * (axes.long_min + axes.long_max)
    vals = [value - centre for value in vals]
    scale = max(max(abs(value) for value in vals), 1.0)
    mismatch = sum(abs(left + right) / scale for left, right in zip(vals, reversed(vals)))
    return max(0.0, min(1.0, 1.0 - mismatch / max(len(vals), 1)))

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


def _point_distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(float(a.x) - float(b.x), float(a.y) - float(b.y))


def _endpoint_is_supported_ty_node(system: RetainingSystem, support: SupportElement, endpoint: Point2D, tol: float = 0.03) -> bool:
    """Return True when an internal endpoint forms a supported T/Y node.

    A non-ring secondary or diagonal member is allowed to terminate at the
    interior of a same-level support, provided a temporary column exists at
    that node and its service record connects both members.  This is a node,
    not a crossing, and must be accepted by optimizer hard constraints.
    """
    connected: list[SupportElement] = []
    for other in system.supports:
        if other.code == support.code or int(other.level_index) != int(support.level_index):
            continue
        if other.support_role == "ring_strut":
            continue
        if layout_mod._point_on_segment(endpoint, other.start, other.end, tol=tol):
            connected.append(other)
    if not connected:
        return False

    for column in system.columns:
        if _point_distance(column.location, endpoint) > max(tol, 0.05):
            continue
        served = set(column.support_codes or [])
        if support.code not in served:
            continue
        if any(other.code in served for other in connected):
            return True
    return False


def _endpoint_is_valid(system: RetainingSystem, support: SupportElement, endpoint: Point2D, face_code: str | None) -> bool:
    # Non-ring supports in the current analysis model must terminate on a wall
    # or wale face. A temporary column supplies vertical/out-of-plane restraint;
    # it does not turn an axial strut into an in-plane transfer beam capable of
    # receiving a transverse force at mid-span.
    return bool(face_code)


def _endpoint_validity_penalty(system: RetainingSystem) -> float:
    endpoints = 0
    missing = 0
    for support in system.supports:
        if support.support_role == "ring_strut":
            continue
        endpoints += 2
        if not _endpoint_is_valid(system, support, support.start, support.start_face_code):
            missing += 1
        if not _endpoint_is_valid(system, support, support.end, support.end_face_code):
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


def _hard_constraints(project: Project, quality_metrics: dict[str, Any], system: RetainingSystem, deep_design: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint_missing = _endpoint_validity_penalty(system)
    obstacle_count = int(quality_metrics.get("obstacleConflictCount", 0) or 0)
    crossing_count = int(quality_metrics.get("supportCrossingCount", 0) or 0)
    outside_count = int(quality_metrics.get("supportOutsideExcavationCount", 0) or 0)
    wale_fail_count = int(quality_metrics.get("waleSupportBayFailCount", 0) or 0)
    station_cluster_count = int(quality_metrics.get("supportStationClusterCount", 0) or 0)
    corner_parallelism_issues = int(quality_metrics.get("cornerBraceParallelismIssueCount", 0) or 0)
    corner_endpoint_congestion = int(quality_metrics.get("cornerBraceEndpointCongestionCount", 0) or 0)
    support_to_support_terminals = int(quality_metrics.get("supportToSupportTerminalCount", 0) or 0)
    unsupported_internal_endpoints = int(quality_metrics.get("unsupportedInternalEndpointCount", 0) or 0)
    repl_penalty = _replacement_path_penalty(project)
    deep_design = deep_design or {}
    deep_metrics = dict(deep_design.get("metrics") or {})
    deep_required = bool(getattr(project.design_settings, "support_deep_design_required_for_candidate", True))
    deep_hard_pass = bool(deep_design.get("hardPass", True)) if deep_required else True
    shape_diagnostics = layout_mod.plan_shape_diagnostics(
        list(project.excavation.outline.points) if project.excavation else [],
        local_pit_count=len(project.excavation.local_pits or []) if project.excavation else 0,
        has_center_island=any(
            getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
            for item in (project.excavation.obstacles or [])
        ) if project.excavation else False,
    )
    transfer_required = str(shape_diagnostics.get("capability") or "").startswith("zoned_")
    transfer_audit = dict((system.layout_summary or {}).get("transferSystem") or {})
    transfer_system_present = (
        not transfer_required
        or bool(transfer_audit.get("calculationReady"))
        or bool(shape_diagnostics.get("hasCenterIsland"))
    )
    col_obstacle_hits = 0
    obstacles = layout_mod._active_obstacle_polygons(getattr(project.excavation, "obstacles", []) if project.excavation else [])
    for col in system.columns:
        if not layout_mod._point_avoids_obstacles(col.location, obstacles):
            col_obstacle_hits += 1
    passed = (
        crossing_count == 0
        and obstacle_count == 0
        and outside_count == 0
        and wale_fail_count == 0
        and station_cluster_count == 0
        and corner_parallelism_issues == 0
        and corner_endpoint_congestion == 0
        and support_to_support_terminals == 0
        and unsupported_internal_endpoints == 0
        and endpoint_missing == 0
        and col_obstacle_hits == 0
        and repl_penalty < 0.75
        and transfer_system_present
        and deep_hard_pass
    )
    return {
        "passed": passed,
        "supportNoCrossing": crossing_count == 0,
        "supportNoObstacleConflict": obstacle_count == 0,
        "supportInsideExcavation": outside_count == 0,
        "waleSupportBayWithinHardLimit": wale_fail_count == 0,
        "supportStationsMeetMinimumSeparation": station_cluster_count == 0,
        "cornerBracesAreParallelFamilies": corner_parallelism_issues == 0,
        "cornerBraceWallNodesAreIndependent": corner_endpoint_congestion == 0,
        "endpointsOnWaleOrRingNodes": endpoint_missing == 0,
        "endpointsOnWaleRingOrSupportedTYNodes": endpoint_missing == 0,  # compatibility alias; T/Y is no longer accepted
        "supportNoSupportToSupportTerminal": support_to_support_terminals == 0,
        "supportNoUnsupportedInternalEndpoint": unsupported_internal_endpoints == 0,
        "temporaryColumnsOutsideObstacles": col_obstacle_hits == 0,
        "replacementPathContinuity": repl_penalty < 0.75,
        "shapeTransferSystemComplete": transfer_system_present,
        "shapeTransferSystemRequired": transfer_required,
        "shapeTransferSystemTemplate": transfer_audit.get("templateId"),
        "shapeTransferSystemOfficialIssueReady": bool(transfer_audit.get("officialIssueReady")) if transfer_required else True,
        "shapeTransferSystemAudit": transfer_audit,
        "supportDeepDesignHardPass": deep_hard_pass,
        "supportDeepDesignRequired": deep_required,
        "supportMemberScreeningFailCount": int(deep_metrics.get("memberFailCount", 0) or 0),
        "supportMaximumInteractionUtilization": float(deep_metrics.get("maximumInteractionUtilization", 0.0) or 0.0),
        "supportMaximumSlenderness": float(deep_metrics.get("maximumSlenderness", 0.0) or 0.0),
        "shapeArchetype": shape_diagnostics.get("archetype"),
        "shapePrimarySystem": shape_diagnostics.get("primarySystem"),
        "supportCrossingCount": crossing_count,
        "obstacleConflictCount": obstacle_count,
        "supportOutsideExcavationCount": outside_count,
        "waleSupportBayFailCount": wale_fail_count,
        "supportStationClusterCount": station_cluster_count,
        "cornerBraceParallelismIssueCount": corner_parallelism_issues,
        "cornerBraceEndpointCongestionCount": corner_endpoint_congestion,
        "supportToSupportTerminalCount": support_to_support_terminals,
        "unsupportedInternalEndpointCount": unsupported_internal_endpoints,
        "missingEndpointRatio": round(endpoint_missing, 4),
        "columnObstacleHitCount": col_obstacle_hits,
        "replacementPathPenalty": round(repl_penalty, 4),
    }


def _objective_terms(project: Project, system: RetainingSystem, target_spacing: float, issue_metrics: dict[str, Any], deep_design: dict[str, Any] | None = None) -> dict[str, float]:
    bay = _bay_spacings(system)
    spans = _span_lengths(system)
    spacing_deviation = mean([abs(v - 5.0) / 5.0 for v in bay]) if bay else 1.0
    bay_cv = (pstdev(bay) / mean(bay)) if bay and mean(bay) > 1e-9 and len(bay) > 1 else 0.0
    span_penalty = min(max(spans) / 36.0, 2.0) if spans else 1.0
    muck_score = _muck_path_continuity_score(project, system, issue_metrics)
    symmetry = _symmetry_score(system, project)
    axial_proxy = _axial_peak_proxy(system, bay, spans, target_spacing)
    support_count = max(len(system.supports), 1)
    internal_junctions = float(issue_metrics.get("internalJunctionCount", 0) or 0)
    high_degree_junctions = float(issue_metrics.get("highDegreeJunctionCount", 0) or 0)
    wall_junctions = float(issue_metrics.get("wallJunctionCount", 0) or 0)
    high_degree_wall_junctions = float(issue_metrics.get("highDegreeWallJunctionCount", 0) or 0)
    projected_crossings = float(issue_metrics.get("projectedCrossLevelIntersectionCount", 0) or 0)
    junction_complexity = (
        internal_junctions
        + 1.5 * high_degree_junctions
        + 0.20 * projected_crossings
    ) / support_count
    corner_family_issues = float(issue_metrics.get("cornerBraceParallelismIssueCount", 0) or 0) + float(issue_metrics.get("cornerBraceEndpointCongestionCount", 0) or 0)
    wall_junction_complexity = (wall_junctions + 2.0 * high_degree_wall_junctions + 3.0 * corner_family_issues) / support_count
    deep_metrics = dict((deep_design or {}).get("metrics") or {})
    target_util = max(float(getattr(project.design_settings, "support_target_utilization", 0.85)), 0.1)
    max_util = float(deep_metrics.get("maximumInteractionUtilization", 0.0) or 0.0)
    slender_limit = max(float(getattr(project.design_settings, "support_screening_slenderness_limit", 150.0)), 1.0)
    max_slenderness = float(deep_metrics.get("maximumSlenderness", 0.0) or 0.0)
    effect_ratio = float(deep_metrics.get("maximumConstructionEffectRatio", 0.0) or 0.0)
    volume = float(deep_metrics.get("supportMaterialVolumeM3", 0.0) or 0.0)
    node_count = max(int(deep_metrics.get("supportNodeCount", 0) or 0), 1)
    node_unchecked = float(deep_metrics.get("supportNodeUncheckedCount", 0) or 0)
    single_pairs = float(deep_metrics.get("singleMemberWallPairCount", 0) or 0)
    force_cv = float(deep_metrics.get("maximumSupportForceCoefficientOfVariation", 0.0) or 0.0)
    force_peak_ratio = float(deep_metrics.get("maximumSupportForcePeakToMeanRatio", 1.0) or 1.0)
    return {
        "spacingDeviation": round(max(0.0, spacing_deviation + bay_cv * 0.5), 4),
        "spanLength": round(max(0.0, span_penalty), 4),
        "obstacleConflict": round(float(issue_metrics.get("obstacleConflictCount", 0) or 0), 4),
        "supportCrossing": round(float(issue_metrics.get("supportCrossingCount", 0) or 0), 4),
        "junctionComplexity": round(max(0.0, junction_complexity), 4),
        "wallJunctionComplexity": round(max(0.0, wall_junction_complexity), 4),
        "columnCount": round(max(0.0, _column_count_penalty(system)), 4),
        "muckPathContinuity": round(max(0.0, 1.0 - muck_score), 4),
        "axialPeakProxy": round(max(0.0, axial_proxy), 4),
        "symmetry": round(max(0.0, 1.0 - symmetry), 4),
        "endpointValidity": round(_endpoint_validity_penalty(system), 4),
        "replacementContinuity": round(_replacement_path_penalty(project), 4),
        "memberUtilization": round(max(0.0, max_util / target_util), 4),
        "bucklingRisk": round(max(0.0, max_slenderness / slender_limit), 4),
        "constructionEffects": round(max(0.0, effect_ratio), 4),
        "materialVolume": round(max(0.0, volume / max(support_count * 20.0, 1.0)), 4),
        "nodeReadiness": round(max(0.0, node_unchecked / node_count), 4),
        "loadPathRedundancy": round(max(0.0, single_pairs / support_count), 4),
        "forceUniformity": round(max(0.0, force_cv + max(0.0, force_peak_ratio - 1.0) * 0.5), 4),
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


def _cleanliness_sort_key(candidate: SupportLayoutOptimizationCandidate) -> tuple:
    """Lexicographic plan-cleanliness priority used before aggregate score."""
    metrics = candidate.metrics or {}
    auxiliary_count = int(metrics.get("secondaryGridSupportCount", 0) or 0) + int(metrics.get("cornerDiagonalCount", 0) or 0)
    return (
        int(metrics.get("supportToSupportTerminalCount", 0) or 0),
        int(metrics.get("unsupportedInternalEndpointCount", 0) or 0),
        float(metrics.get("supportCrossingCount", candidate.crossing_count) or 0.0),
        int(metrics.get("supportStationClusterCount", 0) or 0),
        int(metrics.get("supportMemberScreeningFailCount", 0) or 0),
        int(metrics.get("cornerBraceParallelismIssueCount", 0) or 0),
        int(metrics.get("cornerBraceEndpointCongestionCount", 0) or 0),
        int(metrics.get("highDegreeWallJunctionCount", 0) or 0),
        int(metrics.get("wallJunctionCount", 0) or 0),
        int(metrics.get("totalHighDegreeJunctionCount", metrics.get("highDegreeJunctionCount", 0)) or 0),
        int(metrics.get("totalJunctionCount", metrics.get("internalJunctionCount", 0)) or 0),
        float(metrics.get("planIntersectionComplexity", 0.0) or 0.0),
        float(metrics.get("supportMaximumInteractionUtilization", 0.0) or 0.0),
        float(metrics.get("supportMaximumSlenderness", 0.0) or 0.0),
        auxiliary_count,
        int(candidate.support_count or 0),
    )


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
    transfer_beams = [
        {
            "id": beam.id, "code": beam.code, "role": beam.beam_role, "elevation": beam.elevation,
            "supportLevel": beam.support_level,
            "points": [_point_payload(point) for point in beam.axis.points],
        }
        for beam in (system.ring_beams or [])
        if str(getattr(beam, "code", "")).startswith(("TR-", "TF-", "TB-"))
        or str(getattr(beam, "beam_role", "")).startswith("transfer_")
    ]
    transfer_audit = dict((system.layout_summary or {}).get("transferSystem") or {})
    return {
        "outline": outline, "supports": supports, "columns": columns, "obstacles": obstacles,
        "transferBeams": transfer_beams, "transferZones": list(transfer_audit.get("transferZones") or []),
        "zoneGraph": dict(transfer_audit.get("zoneGraph") or {}),
        "lockSummary": _lock_summary(project), "supportElevations": elevations,
        "layoutSummary": dict(system.layout_summary or {}),
    }


def _delta_geometry(adjustments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "changedSupportCount": len(adjustments),
        "adjustments": adjustments[:20],
    }


def _candidate_seed_system(project: Project) -> RetainingSystem:
    """Build a clean geometry-only retaining-system seed.

    Historical projects may carry tens or hundreds of megabytes in
    ``layout_summary.autoRepair``, candidate calculations and rebar caches. A
    deep copy of the whole retaining system for every trial multiplied that
    payload by the search count and was the main cause of 12-18 GB workers.
    Candidate generation only needs the wall/crown geometry and lock registry;
    all supports, wales, nodes and columns are regenerated.
    """
    source = project.retaining_system
    if source is None:
        return RetainingSystem()
    return RetainingSystem(
        id=source.id,
        type=source.type,
        diaphragm_walls=[item.model_copy(deep=True) for item in source.diaphragm_walls],
        crown_beams=[item.model_copy(deep=True) for item in source.crown_beams],
        wale_beams=[],
        ring_beams=[],
        supports=[],
        support_nodes=[],
        columns=[],
        layout_summary={},
        optimization_locks=[dict(item) for item in source.optimization_locks],
        support_layout_repair=None,
        rebar_design_scheme={},
        replacement_path=[],
        warnings=[],
    )


def _candidate_trial_project(project: Project) -> Project:
    trial = project.model_copy(deep=False)
    trial.calculation_results = []
    trial.calculation_cases = []
    trial.retaining_system = _candidate_seed_system(project)
    # Large presentation/audit caches are irrelevant to geometry search. Keep the
    # shared project object immutable and expose an empty trial-only dictionary.
    trial.advanced_engineering = {}
    return trial

def build_support_system_from_candidate(
    project: Project,
    target_spacing: float,
    column_span: float,
    pattern: str = "as_generated",
    amplitude: float = 0.0,
    topology_strategy: str = "balanced_grid",
    concave_transfer_template: str = "none",
) -> tuple[RetainingSystem | None, list[dict[str, Any]]]:
    if not project.excavation:
        return None, []
    trial_project = _candidate_trial_project(project)
    config = design_service.support_layout_config_from_settings(
        project.design_settings,
        topology_strategy=topology_strategy,
        target_spacing=target_spacing,
        column_span=column_span,
        concave_transfer_template=concave_transfer_template,
    )
    trial_project.retaining_system = design_service.auto_supports(trial_project.excavation, trial_project.retaining_system, layout_config=config)
    if getattr(project, "retaining_system", None):
        trial_project.retaining_system.optimization_locks = list(project.retaining_system.optimization_locks or [])
    _apply_locked_supports(project, trial_project.retaining_system)
    adjustments = _shift_main_support_positions(trial_project, trial_project.retaining_system, pattern, amplitude, column_max_span=column_span)
    adjustments.extend(_apply_endpoint_locks(project, trial_project.retaining_system, column_max_span=column_span))
    return trial_project.retaining_system, adjustments


def _geometry_fingerprint(system: RetainingSystem, precision: float = 0.25) -> tuple[tuple[int, int, int, int, int, int], ...]:
    """Return a physical load-path fingerprint for supports, transfer segments and columns.

    The fingerprint includes every transfer-beam segment and temporary-column
    position. Earlier endpoint-only fingerprints could collapse two chord paths
    with the same end nodes, while support-only fingerprints could collapse
    layouts with materially different column service spans.
    """
    def q(value: float) -> int:
        return int(round(float(value) / precision))

    role_codes = {
        "main_strut": 1,
        "corner_diagonal": 2,
        "secondary_strut": 3,
        "ring_strut": 4,
        "radial_strut": 5,
        "transfer_strut": 6,
    }
    beam_role_codes = {
        "transfer_ring_beam": 70,
        "transfer_frame_beam": 71,
        "transfer_brace": 72,
        "partition_beam": 73,
        "ring_beam": 74,
    }
    rows: list[tuple[int, int, int, int, int, int]] = []
    for item in system.supports:
        start = (q(item.start.x), q(item.start.y))
        end = (q(item.end.x), q(item.end.y))
        if end < start:
            start, end = end, start
        rows.append((
            int(item.level_index),
            int(role_codes.get(str(item.support_role or "main_strut"), 99)),
            start[0], start[1], end[0], end[1],
        ))
    for beam in system.ring_beams or []:
        role = str(getattr(beam, "beam_role", "") or "")
        if not (
            str(getattr(beam, "code", "")).startswith(("TR-", "TF-", "TB-"))
            or role.startswith("transfer_")
            or role in beam_role_codes
        ) or len(beam.axis.points) < 2:
            continue
        role_code = int(beam_role_codes.get(role, 79))
        for start_point, end_point in zip(beam.axis.points, beam.axis.points[1:]):
            start = (q(start_point.x), q(start_point.y))
            end = (q(end_point.x), q(end_point.y))
            if end < start:
                start, end = end, start
            rows.append((int(beam.support_level or 0), role_code, start[0], start[1], end[0], end[1]))
    for column in system.columns or []:
        point = (q(column.location.x), q(column.location.y))
        rows.append((0, 90, point[0], point[1], point[0], point[1]))
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
        f"{hard_text}。优化首先压低非法穿越、墙上多杆汇交和内部 T/Y/X 汇交节点数量，再比较间距偏差、跨长、障碍冲突、立柱数量、出土路径、轴力峰值代理和对称性。"
    )


def _progressive_search_values(minimum: float, maximum: float, preferred: float, *, count: int = 3) -> list[float]:
    low = float(min(minimum, maximum))
    high = float(max(minimum, maximum))
    pref = max(low, min(high, float(preferred)))
    if count >= 5:
        # A 0.5 m change can alter the integer number and phase of support bays
        # on a stepped outline.  Sampling only preferred/minimum/maximum skipped
        # these transition solutions: both coarse endpoints could fail while an
        # intermediate layout satisfied station separation and wale-bay limits.
        # Keep the refinement values immediately after the preferred value so a
        # bounded/core search reaches them before exhausting its trial budget.
        def rounded(value: float) -> float:
            return round(value * 2.0) / 2.0

        local_values: list[float] = []
        for value in (pref, pref - 0.5, pref + 0.5):
            item = rounded(max(low, min(high, value)))
            if item not in local_values:
                local_values.append(item)
        endpoints = [
            item
            for item in (rounded(low), rounded(high))
            if item not in local_values
        ]
        # If the preferred value sits on a boundary, one of the ±0.5 m values
        # collapses onto it. Fill the freed slot with the next closest half-metre
        # value before jumping to the far endpoint.
        local_slot_count = max(0, count - len(local_values) - len(endpoints))
        step = 1.0
        while local_slot_count > 0 and step <= (high - low) + 0.5:
            added = False
            for value in (pref - step, pref + step):
                if value < low - 1.0e-9 or value > high + 1.0e-9:
                    continue
                item = rounded(value)
                if item in local_values or item in endpoints:
                    continue
                local_values.append(item)
                local_slot_count -= 1
                added = True
                if local_slot_count <= 0:
                    break
            if not added and pref - step < low and pref + step > high:
                break
            step += 0.5
        return (local_values + endpoints)[:max(1, count)]

    values = [pref]
    if count >= 2:
        values.extend([low, high])
    if count >= 4:
        values.append((low + high) / 2.0)
    output: list[float] = []
    for value in values:
        rounded = round(value * 2.0) / 2.0
        if rounded not in output:
            output.append(rounded)
    return output[:max(1, count)]


def optimize_support_layout_candidates(
    project: Project,
    max_candidates: int = 5,
    objective_weights: dict[str, float] | None = None,
    preset: str | None = None,
    topology_family: str | None = None,
    search_config: dict[str, Any] | None = None,
    progress_callback: Any | None = None,
) -> tuple[RetainingSystem | None, list[SupportLayoutOptimizationCandidate]]:
    if not project.excavation:
        return None, []
    weights = preset_objective_weights(preset, objective_weights)
    locked = _locked_supports(project)
    locked_ids = [s.id for s in locked]
    candidates: list[tuple[SupportLayoutOptimizationCandidate, RetainingSystem]] = []
    shape = layout_mod.plan_shape_diagnostics(
        list(project.excavation.outline.points),
        local_pit_count=len(project.excavation.local_pits or []),
        has_center_island=any(getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True) for item in project.excavation.obstacles or []),
    )
    constrained_shape = int(shape.get("concaveVertexCount") or 0) > 0 or bool(shape.get("nearSquarePlan"))
    elongated_shape = bool(shape.get("slenderPlan")) or str(shape.get("archetype") or "") in {
        "elongated_stepped_strip", "elongated_convex_polygon"
    }
    search = dict(search_config or {})
    source_hash = support_candidate_source_hash(project)
    enable_concave_transfer_templates = bool(search.get("enableConcaveTransferTemplates"))
    from app.services.support_transfer_system import DEFAULT_TRANSFER_TEMPLATES, transfer_template_ids, transfer_topology_class
    allowed_transfer_templates = transfer_template_ids()
    configured_transfer_templates = [
        str(item) for item in (search.get("concaveTransferTemplates") or DEFAULT_TRANSFER_TEMPLATES)
        if str(item) in allowed_transfer_templates
    ]
    core_mode = bool(search.get("coreMode"))
    require_diverse_schemes = bool(search.get("requireDiverseSchemes"))
    # Long strip pits need at least one denser and one wider-bay direct-path
    # alternative to create a material A/B/C comparison. Keeping the old
    # 4.5--5.5 m window made every trial round to the same support stations.
    default_min = 4.0 if elongated_shape else 4.5 if constrained_shape else 4.0
    default_max = 6.5 if elongated_shape else 5.5 if constrained_shape else 6.0
    default_preferred = 5.0
    spacing_min = max(2.0, min(10.0, float(search.get("spacingMinM", default_min))))
    spacing_max = max(spacing_min, min(12.0, float(search.get("spacingMaxM", default_max))))
    spacing_preferred = max(spacing_min, min(spacing_max, float(search.get("preferredSpacingM", default_preferred))))
    # Irregular/elongated plans are discontinuous with respect to target bay
    # spacing because line counts are integers and transition-zone repairs snap
    # to wall stations.  Use the available bounded budget for half-metre
    # refinement instead of testing only the two extremes.  This closes the
    # observed 4.0/5.0 m controlled block where 4.5 m is feasible.
    spacing_value_count = 5 if (require_diverse_schemes or constrained_shape or elongated_shape) else 3
    spacing_values = _progressive_search_values(
        spacing_min,
        spacing_max,
        spacing_preferred,
        count=spacing_value_count,
    )
    column_max = max(8.0, min(30.0, float(search.get("columnSpanMaxM", 18.0))))
    column_min = max(6.0, min(column_max, float(search.get("columnSpanMinM", 15.0 if constrained_shape else 12.0))))
    column_values = _progressive_search_values(column_min, column_max, column_max, count=3 if require_diverse_schemes else 2)
    available_strategies = _available_topology_strategies(project)
    if topology_family:
        requested_family = str(topology_family).strip()
        available_strategies = [item for item in available_strategies if item == requested_family]
        if not available_strategies:
            return None, []
    # Candidate generation is an engineering search, not an unbounded geometry
    # fuzzer. Complex imported outlines previously multiplied topology, spacing,
    # column and line-shift combinations until the worker exhausted CPU/RAM.
    default_trial_limit = "12" if core_mode and require_diverse_schemes else "9" if core_mode else "24"
    environment_value = os.getenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT")
    environment_trial_limit = int(environment_value or default_trial_limit)
    configured_trial_limit = int(search.get("maxTrials") or environment_trial_limit)
    if environment_value:
        configured_trial_limit = min(configured_trial_limit, environment_trial_limit)
    if require_diverse_schemes and core_mode:
        configured_trial_limit = max(configured_trial_limit, 12)
    # A diverse comparison does not require an unbounded 48+ geometry sweep.
    # The prior lower-bound silently overrode the runtime memory policy and the
    # task-manager cap whenever a legacy progressive configuration requested
    # ``requireDiverseSchemes``.  Respect the explicit/environment budget and
    # let geometry fingerprints decide whether the bounded trials are distinct.
    if require_diverse_schemes and not core_mode:
        configured_trial_limit = max(configured_trial_limit, min(18, environment_trial_limit))
    max_trials = max(3, min(18 if core_mode else 72, configured_trial_limit))
    default_element_limit = "800" if core_mode else "2400"
    configured_elements = int(search.get("maxSupportElements") or os.getenv("PITGUARD_MAX_SUPPORT_ELEMENTS", default_element_limit))
    max_support_elements = max(100, min(4000 if core_mode else 12000, configured_elements))
    candidate_pool_limit = max(3, min(6 if core_mode else 18, int(search.get("candidatePoolLimit") or os.getenv("PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT", str(max_candidates * 2 if core_mode else min(max_trials, 18))))))
    trial_count = 0
    for topology_strategy in available_strategies:
        for target_spacing in spacing_values:
            for column_span in column_values:
                if core_mode:
                    # The second bounded pattern is only admitted for diverse
                    # comparisons. It changes actual support stations and is
                    # subsequently filtered by the geometry fingerprint.
                    strategy_patterns = POSITION_PATTERNS[:2] if require_diverse_schemes and topology_strategy not in {"ring_radial", "zoned_direct"} else POSITION_PATTERNS[:1]
                else:
                    strategy_patterns = POSITION_PATTERNS[:1] if topology_strategy in {"ring_radial", "zoned_direct"} else POSITION_PATTERNS[:3] if topology_strategy != "direct_grid" else POSITION_PATTERNS[:2]
                for pattern, amplitude in strategy_patterns:
                    if trial_count >= max_trials:
                        continue
                    trial_count += 1
                    if callable(progress_callback):
                        progress_callback(trial_count, max_trials, topology_strategy)
                    # V2.6.0: avoid deep-copying historical calculation results and large
                    # report payloads for every candidate.  Candidate generation only needs
                    # geometry, settings and the current retaining system.
                    trial_project = _candidate_trial_project(project)
                    memory_event(
                        "candidate-search",
                        "trial-start",
                        trialIndex=trial_count,
                        trialLimit=max_trials,
                        topologyFamily=topology_strategy,
                        targetSpacingM=target_spacing,
                        columnSpanM=column_span,
                        positionPattern=pattern,
                        candidatePoolSize=len(candidates),
                    )
                    transfer_template = "none"
                    if topology_strategy == "zoned_direct" and enable_concave_transfer_templates and configured_transfer_templates:
                        transfer_template = configured_transfer_templates[(trial_count - 1) % len(configured_transfer_templates)]
                    layout_config = design_service.support_layout_config_from_settings(
                        project.design_settings,
                        topology_strategy=topology_strategy,
                        target_spacing=target_spacing,
                        column_span=column_span,
                        concave_transfer_template=transfer_template,
                    )
                    trial_project.retaining_system = design_service.auto_supports(
                        trial_project.excavation,
                        trial_project.retaining_system,
                        layout_config=layout_config,
                    )
                    if len(trial_project.retaining_system.supports or []) > max_support_elements:
                        # Reject malformed/over-detailed candidates before repair,
                        # quality graph construction and geometry serialization.
                        memory_event(
                            "candidate-search",
                            "trial-rejected-element-limit",
                            trialIndex=trial_count,
                            supportCount=len(trial_project.retaining_system.supports or []),
                            maxSupportElements=max_support_elements,
                            candidatePoolSize=len(candidates),
                        )
                        del trial_project
                        gc.collect()
                        continue
                    if getattr(project, "retaining_system", None):
                        trial_project.retaining_system.optimization_locks = list(project.retaining_system.optimization_locks or [])
                    locked_count = _apply_locked_supports(project, trial_project.retaining_system, column_max_span=column_span)
                    adjustments = _shift_main_support_positions(trial_project, trial_project.retaining_system, pattern, amplitude, column_max_span=column_span)
                    adjustments.extend(_apply_endpoint_locks(project, trial_project.retaining_system, column_max_span=column_span))
                    # Score the constructible topology that will actually be calculated.
                    # Raw candidate lines can leave concave return walls unsupported or
                    # create excessive wale bays; ranking those raw geometries produced
                    # zero-score A/B/C cards that disagreed with the adopted design.
                    concave_preflight = layout_mod.repair_concave_return_supports(trial_project, layout_config)
                    wale_preflight = layout_mod.repair_wale_support_bays(trial_project, layout_config)
                    quality = evaluate_support_layout_quality(trial_project)
                    metrics = dict(quality.metrics or {})
                    deep_design = evaluate_support_deep_design(trial_project, trial_project.retaining_system, include_members=False)
                    deep_metrics = dict(deep_design.get("metrics") or {})
                    metrics.update({
                        "supportMemberScreeningFailCount": int(deep_metrics.get("memberFailCount", 0) or 0),
                        "supportMemberScreeningWarningCount": int(deep_metrics.get("memberWarningCount", 0) or 0),
                        "supportMaximumInteractionUtilization": float(deep_metrics.get("maximumInteractionUtilization", 0.0) or 0.0),
                        "supportMaximumSlenderness": float(deep_metrics.get("maximumSlenderness", 0.0) or 0.0),
                        "supportMaximumEffectiveUnbracedLengthM": float(deep_metrics.get("maximumEffectiveUnbracedLengthM", 0.0) or 0.0),
                        "supportMaximumConstructionEffectRatio": float(deep_metrics.get("maximumConstructionEffectRatio", 0.0) or 0.0),
                        "supportMaximumForceCoefficientOfVariation": float(deep_metrics.get("maximumSupportForceCoefficientOfVariation", 0.0) or 0.0),
                        "supportMaximumForcePeakToMeanRatio": float(deep_metrics.get("maximumSupportForcePeakToMeanRatio", 1.0) or 1.0),
                        "supportMaterialVolumeM3": float(deep_metrics.get("supportMaterialVolumeM3", 0.0) or 0.0),
                        "supportNodeUncheckedCount": int(deep_metrics.get("supportNodeUncheckedCount", 0) or 0),
                        "supportSingleMemberWallPairCount": int(deep_metrics.get("singleMemberWallPairCount", 0) or 0),
                    })
                    quality_fail_categories = sorted({
                        str(issue.category)
                        for issue in quality.issues
                        if issue.severity == "fail"
                    })
                    quality_warning_categories = sorted({
                        str(issue.category)
                        for issue in quality.issues
                        if issue.severity in {"warning", "manual_review"}
                    })
                    deep_member_fail_count = int(deep_metrics.get("memberFailCount", 0) or 0)
                    fail_count = sum(1 for i in quality.issues if i.severity == "fail") + deep_member_fail_count
                    warning_count = sum(1 for i in quality.issues if i.severity == "warning") + int(deep_metrics.get("memberWarningCount", 0) or 0)
                    terms = _objective_terms(trial_project, trial_project.retaining_system, target_spacing, metrics, deep_design)
                    hard = _hard_constraints(trial_project, metrics, trial_project.retaining_system, deep_design)
                    hard["qualityFailCount"] = int(fail_count)
                    hard["qualityFailCategories"] = quality_fail_categories
                    hard["qualityWarningCategories"] = quality_warning_categories
                    hard["deepMemberFailCount"] = deep_member_fail_count
                    hard["passed"] = bool(hard.get("passed")) and fail_count == 0
                    hard_failure_keys = sorted(
                        key
                        for key, value in hard.items()
                        if key != "passed"
                        and not key.endswith("Required")
                        and isinstance(value, bool)
                        and value is False
                    )
                    blocking_categories = list(quality_fail_categories)
                    if deep_member_fail_count:
                        blocking_categories.append("support_member_screening")
                    if bool(hard.get("shapeTransferSystemRequired")) and not bool(hard.get("shapeTransferSystemComplete")):
                        blocking_categories.append("shape_transfer_system")
                    hard["hardFailureKeys"] = hard_failure_keys
                    hard["blockingCategories"] = sorted(set(blocking_categories))
                    spans = _span_lengths(trial_project.retaining_system)
                    bay = _bay_spacings(trial_project.retaining_system)
                    score = _candidate_score(quality.score, terms, hard, fail_count, warning_count, weights)
                    fingerprint = _geometry_fingerprint(trial_project.retaining_system)
                    difference_score = _geometry_difference_score(adjustments, len(trial_project.retaining_system.supports))
                    candidate = SupportLayoutOptimizationCandidate(
                        id=_candidate_id(target_spacing, column_span, pattern, amplitude, f"{topology_strategy}-{transfer_template}" if transfer_template != "none" else topology_strategy),
                        score=score,
                        status=quality.status,
                        target_spacing=target_spacing,
                        column_max_span=column_span,
                        objective_terms=terms,
                        soft_objectives={
                            "minimumPlanIntersectionCount": 1.0 - min(1.0, terms.get("junctionComplexity", 1.0)),
                            "minimumWallJunctionCount": 1.0 - min(1.0, terms.get("wallJunctionComplexity", 1.0)),
                            "spacingCloseTo3To6m": 1.0 - min(1.0, terms.get("spacingDeviation", 1.0)),
                            "shortSpanLength": 1.0 - min(1.0, terms.get("spanLength", 1.0) / 2.0),
                            "reasonableColumnCount": 1.0 - min(1.0, terms.get("columnCount", 1.0)),
                            "lowAxialPeakProxy": 1.0 - min(1.0, terms.get("axialPeakProxy", 1.0)),
                            "continuousMuckPath": 1.0 - min(1.0, terms.get("muckPathContinuity", 1.0)),
                            "planSymmetry": 1.0 - min(1.0, terms.get("symmetry", 1.0)),
                            "memberUtilization": 1.0 - min(1.0, terms.get("memberUtilization", 1.0)),
                            "bucklingResistance": 1.0 - min(1.0, terms.get("bucklingRisk", 1.0)),
                            "constructionEffectControl": 1.0 - min(1.0, terms.get("constructionEffects", 1.0)),
                            "nodeDetailingReadiness": 1.0 - min(1.0, terms.get("nodeReadiness", 1.0)),
                            "loadPathRedundancy": 1.0 - min(1.0, terms.get("loadPathRedundancy", 1.0)),
                            "forceUniformity": 1.0 - min(1.0, terms.get("forceUniformity", 1.0)),
                        },
                        hard_constraints=hard,
                        variable_summary={
                            "variableType": "whole_scheme_topology_and_line_position",
                            "candidateContractVersion": SUPPORT_CANDIDATE_CONTRACT_VERSION,
                            "topologyFamily": topology_strategy,
                            "transferSystemTemplate": transfer_template,
                            "transferTopologyClass": transfer_topology_class(transfer_template),
                            "schemeFamily": (
                                f"{topology_strategy}:{transfer_topology_class(transfer_template)}"
                                if transfer_template != "none" else topology_strategy
                            ),
                            "transferSystemAudit": dict((trial_project.retaining_system.layout_summary or {}).get("transferSystem") or {}),
                            "schemeLabel": (
                                dict((trial_project.retaining_system.layout_summary or {}).get("transferSystem") or {}).get("templateLabel")
                                if transfer_template != "none"
                                else {"hybrid_diagonal": "转角墙—墙斜撑+对撑混合", "bidirectional_grid": "近方形双向框架", "direct_grid": "传统直对撑", "ring_radial": "闭合内环梁+径向支撑", "zoned_direct": "异形分区墙—墙对撑"}.get(topology_strategy, topology_strategy)
                            ),
                            "positionPattern": pattern,
                            "lineOffsetAmplitude": amplitude,
                            "adjustedLineCount": len(adjustments),
                            "targetSpacing": target_spacing,
                            "columnMaxSpan": column_span,
                            "progressiveSearchConfig": {
                                "spacingMinM": spacing_min,
                                "spacingMaxM": spacing_max,
                                "preferredSpacingM": spacing_preferred,
                                "columnSpanMinM": column_min,
                                "columnSpanMaxM": column_max,
                                "maxTrials": max_trials,
                                "requireDiverseSchemes": require_diverse_schemes,
                            },
                            "lockedSupportCount": locked_count,
                            "lockedSupportIds": locked_ids,
                            "lockSummary": _lock_summary(project),
                            "geometryFingerprint": ";".join(["-".join(map(str, row)) for row in fingerprint[:80]]),
                            "geometryDifferenceScore": difference_score,
                            "materiallyDifferent": difference_score >= 0.03 or pattern == "as_generated",
                            "deepDesignScreening": {
                                "status": deep_design.get("status"),
                                "summary": deep_design.get("summary"),
                                "metrics": deep_metrics,
                                "governingMembers": deep_design.get("governingMembers", [])[:8],
                                "designActions": list(deep_design.get("designActions", []))[:12],
                            },
                            "strengthTopologyPreflight": {
                                "concaveReturnRepair": concave_preflight,
                                "waleSupportBayRepair": wale_preflight,
                                "addedSupportCount": int(concave_preflight.get("addedSupportCount", 0) or 0) + int(wale_preflight.get("addedSupportCount", 0) or 0),
                            },
                            "topologyQualification": {
                                "qualityStatus": quality.status,
                                "qualityScore": float(quality.score or 0.0),
                                "failCount": int(fail_count),
                                "warningCount": int(warning_count),
                                "blockingCategories": list(hard.get("blockingCategories") or []),
                                "hardFailureKeys": list(hard.get("hardFailureKeys") or []),
                                "controlMetrics": {
                                    "supportCrossingCount": int(metrics.get("supportCrossingCount", 0) or 0),
                                    "supportOutsideExcavationCount": int(metrics.get("supportOutsideExcavationCount", 0) or 0),
                                    "waleSupportBayFailCount": int(metrics.get("waleSupportBayFailCount", 0) or 0),
                                    "supportStationClusterCount": int(metrics.get("supportStationClusterCount", 0) or 0),
                                    "supportToSupportTerminalCount": int(metrics.get("supportToSupportTerminalCount", 0) or 0),
                                    "unsupportedInternalEndpointCount": int(metrics.get("unsupportedInternalEndpointCount", 0) or 0),
                                    "cornerBraceParallelismIssueCount": int(metrics.get("cornerBraceParallelismIssueCount", 0) or 0),
                                    "cornerBraceEndpointCongestionCount": int(metrics.get("cornerBraceEndpointCongestionCount", 0) or 0),
                                    "supportMemberScreeningFailCount": deep_member_fail_count,
                                },
                            },
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
                        junction_count=int(metrics.get("totalJunctionCount", metrics.get("internalJunctionCount", 0)) or 0),
                        high_degree_junction_count=int(metrics.get("totalHighDegreeJunctionCount", metrics.get("highDegreeJunctionCount", 0)) or 0),
                        wall_junction_count=int(metrics.get("wallJunctionCount", 0) or 0),
                        plan_intersection_complexity=round(float(metrics.get("planIntersectionComplexity", 0.0) or 0.0), 4),
                        obstacle_conflict_count=int(metrics.get("obstacleConflictCount", 0) or 0),
                        axial_peak_proxy=round(_axial_peak_proxy(trial_project.retaining_system, bay, spans, target_spacing), 3) if spans else None,
                        symmetry_score=round(_symmetry_score(trial_project.retaining_system, trial_project), 3),
                        muck_path_continuity_score=round(_muck_path_continuity_score(trial_project, trial_project.retaining_system, metrics), 3),
                        export_readiness=_export_readiness(quality.status, hard, metrics),
                        constructability_note=(
                            {"hybrid_diagonal": "转角影响区采用直接落在相邻围檩/围护墙上的墙—墙角撑，并删除冲突对撑；", "bidirectional_grid": "仅用于近方形宽大基坑的双向框架；次向构件在专用节点终止并按框架受力复核；", "direct_grid": "传统短跨直对撑，构造直观；", "ring_radial": "闭合内环梁承接外围径向支撑，保持大面积出土空间；", "zoned_direct": "按识别出的走廊/翼缘区分区布置墙—墙对撑，转接区必须通过环梁、分隔墙或显式框架复核；"}.get(topology_strategy, "")
                            + _candidate_note(target_spacing, column_span, pattern, terms, hard)
                        ),
                    )
                    stamp_candidate_source(candidate, project, source_hash=source_hash)
                    candidate.variable_summary["formalSchemeEligible"] = bool(candidate.hard_constraints.get("passed"))
                    # Snapped support stations can make different spacing/shift
                    # variables produce the same physical load path. Keep only
                    # the best-scoring representative immediately instead of
                    # retaining duplicate systems until final ranking.
                    duplicate_index = next((
                        index for index, (_, existing_system) in enumerate(candidates)
                        if _geometry_fingerprint(existing_system) == fingerprint
                    ), None)
                    if duplicate_index is not None:
                        existing_candidate, _ = candidates[duplicate_index]
                        new_key = (
                            not candidate.hard_constraints.get("passed", False),
                            int(candidate.fail_count or 0),
                            _status_rank(candidate.status),
                            -float(candidate.score or 0.0),
                            int(candidate.support_count or 0),
                            int(candidate.column_count or 0),
                        )
                        old_key = (
                            not existing_candidate.hard_constraints.get("passed", False),
                            int(existing_candidate.fail_count or 0),
                            _status_rank(existing_candidate.status),
                            -float(existing_candidate.score or 0.0),
                            int(existing_candidate.support_count or 0),
                            int(existing_candidate.column_count or 0),
                        )
                        replaced = new_key < old_key
                        if replaced:
                            candidates[duplicate_index] = (candidate, trial_project.retaining_system)
                        memory_event(
                            "candidate-geometry", "candidate-rejected",
                            projectId=project.id,
                            reason="identical_geometry_pre_pool_replaced" if replaced else "identical_geometry_pre_pool",
                            topologyFamily=(candidate.variable_summary or {}).get("topologyFamily"),
                            supportCount=candidate.support_count,
                            columnCount=candidate.column_count,
                        )
                        del trial_project
                        if trial_count % 2 == 0:
                            gc.collect()
                        continue
                    candidates.append((candidate, trial_project.retaining_system))
                    memory_event(
                        "candidate-search",
                        "trial-complete",
                        trialIndex=trial_count,
                        candidateId=candidate.id,
                        score=candidate.score,
                        hardPassed=bool(candidate.hard_constraints.get("passed")),
                        qualityStatus=quality.status,
                        qualityScore=float(quality.score or 0.0),
                        failCount=int(fail_count),
                        warningCount=int(warning_count),
                        blockingCategories=list(hard.get("blockingCategories") or []),
                        hardFailureKeys=list(hard.get("hardFailureKeys") or []),
                        controlMetrics=(candidate.variable_summary.get("topologyQualification") or {}).get("controlMetrics", {}),
                        supportCount=candidate.support_count,
                        columnCount=candidate.column_count,
                        candidatePoolSize=len(candidates),
                        candidatePoolLimit=candidate_pool_limit,
                    )
                    if len(candidates) > candidate_pool_limit:
                        # Keep a bounded set of complete retaining-system objects.
                        # Previously all trial systems remained alive until the end
                        # of the search, which multiplied memory by the trial count.
                        candidates.sort(key=lambda item: (
                            not item[0].hard_constraints.get("passed", False),
                            int(item[0].fail_count or 0),
                            _status_rank(item[0].status),
                            -float(item[0].score or 0.0),
                            int(item[0].support_count or 0),
                            int(item[0].column_count or 0),
                        ))
                        family_best: dict[str, tuple[SupportLayoutOptimizationCandidate, RetainingSystem]] = {}
                        for pair in candidates:
                            family = str((pair[0].variable_summary or {}).get("schemeFamily") or (pair[0].variable_summary or {}).get("topologyFamily") or "unknown")
                            family_best.setdefault(family, pair)
                        keep = list(family_best.values())
                        selected_ids = {id(pair[0]) for pair in keep}
                        for pair in candidates:
                            if len(keep) >= candidate_pool_limit:
                                break
                            if id(pair[0]) not in selected_ids:
                                keep.append(pair)
                                selected_ids.add(id(pair[0]))
                        candidates[:] = keep[:candidate_pool_limit]
                    # Trial-only objects are intentionally released at every
                    # boundary. CPython may retain arenas, but the process-level
                    # hard cap and one-task worker guarantee final reclamation.
                    del trial_project
                    if trial_count % 2 == 0:
                        gc.collect()
    memory_event(
        "candidate-search",
        "search-ranking",
        trialCount=trial_count,
        candidatePoolSize=len(candidates),
        candidatePoolLimit=candidate_pool_limit,
    )
    # Feasible candidates first, then score, geometry diversity, then fewer supports/columns for constructability.
    candidates.sort(key=lambda item: (
        not item[0].hard_constraints.get("passed", False),
        int(item[0].fail_count or 0),
        _status_rank(item[0].status),
        *_cleanliness_sort_key(item[0]),
        -item[0].score,
        -float(item[0].variable_summary.get("geometryDifferenceScore", 0.0)),
        item[0].support_count,
        item[0].column_count,
    ))
    ranked: list[SupportLayoutOptimizationCandidate] = []
    ranked_systems: list[RetainingSystem] = []
    seen: set[tuple] = set()
    selected_system: RetainingSystem | None = None

    def _role_histogram(system: RetainingSystem) -> tuple[int, int, int, int]:
        roles = [str(item.support_role or "main_strut") for item in system.supports]
        return (
            roles.count("main_strut"),
            roles.count("corner_diagonal"),
            roles.count("secondary_strut"),
            len(roles) - roles.count("main_strut") - roles.count("corner_diagonal") - roles.count("secondary_strut"),
        )

    def _angle_histogram(system: RetainingSystem) -> tuple[int, int, int, int]:
        bins = [0, 0, 0, 0]
        for item in system.supports:
            dx = float(item.end.x) - float(item.start.x)
            dy = float(item.end.y) - float(item.start.y)
            angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
            angle = min(angle, 180.0 - angle)
            index = 0 if angle < 15.0 else 1 if angle < 40.0 else 2 if angle < 70.0 else 3
            bins[index] += 1
        return tuple(bins)  # type: ignore[return-value]

    def _structural_signature(candidate: SupportLayoutOptimizationCandidate, system: RetainingSystem) -> tuple[Any, ...]:
        # The topology-family label is intentionally excluded. Earlier versions
        # treated a changed label as structural diversity even when every support
        # line was identical, which produced cosmetic A/B/C alternatives.
        return (
            int(candidate.support_count or 0),
            int(candidate.column_count or 0),
            int(round(float(candidate.max_bay_spacing or 0.0) * 10.0)),
            int(round(float(candidate.max_span_length or 0.0) * 10.0)),
            _role_histogram(system),
            _angle_histogram(system),
            len([
                beam for beam in (system.ring_beams or [])
                if str(getattr(beam, "code", "")).startswith(("TR-", "TF-", "TB-"))
                or str(getattr(beam, "beam_role", "")).startswith("transfer_")
            ]),
            str(((system.layout_summary or {}).get("transferSystem") or {}).get("templateId") or "none"),
        )

    def _fingerprint_distance(left: RetainingSystem, right: RetainingSystem) -> float:
        a = set(_geometry_fingerprint(left))
        b = set(_geometry_fingerprint(right))
        if not a and not b:
            return 0.0
        return round(len(a.symmetric_difference(b)) / max(len(a.union(b)), 1), 4)

    structural_seen: set[tuple[Any, ...]] = set()

    def add_candidate(candidate: SupportLayoutOptimizationCandidate, system: RetainingSystem, *, force: bool = False) -> bool:
        nonlocal selected_system
        key = _geometry_fingerprint(system)
        structural_key = _structural_signature(candidate, system)
        if len(ranked) >= max_candidates:
            return False
        if key in seen:
            append_event(
                "candidate-geometry", "candidate-rejected",
                projectId=project.id, reason="identical_geometry", topologyFamily=(candidate.variable_summary or {}).get("topologyFamily"),
                supportCount=candidate.support_count, columnCount=candidate.column_count,
            )
            return False
        pairwise = [_fingerprint_distance(system, prior) for prior in ranked_systems]
        minimum_geometry_delta = min(pairwise) if pairwise else 1.0
        has_structural_delta = not structural_seen or structural_key not in structural_seen
        declared_difference = float((candidate.variable_summary or {}).get("geometryDifferenceScore", 0.0) or 0.0)
        # A valid comparison candidate must change the actual force-path geometry
        # or a material structural quantity. Family labels and line-shift metadata
        # alone cannot force a duplicate into the A/B/C set.
        if ranked and not force and not has_structural_delta and minimum_geometry_delta < 0.10 and declared_difference < 0.12:
            append_event(
                "candidate-geometry", "candidate-rejected",
                projectId=project.id, reason="cosmetic_difference", topologyFamily=(candidate.variable_summary or {}).get("topologyFamily"),
                geometryDelta=minimum_geometry_delta, declaredDifference=declared_difference,
                supportCount=candidate.support_count, columnCount=candidate.column_count,
            )
            return False
        seen.add(key)
        structural_seen.add(structural_key)
        candidate.rank = len(ranked) + 1
        candidate.variable_summary = dict(candidate.variable_summary or {})
        candidate.variable_summary["minimumGeometryDeltaToSelected"] = round(minimum_geometry_delta, 4) if ranked else 1.0
        fingerprint_text = ";".join("-".join(map(str, row)) for row in key)
        candidate.variable_summary["actualGeometrySignature"] = {
            "supportCount": int(candidate.support_count or 0),
            "columnCount": int(candidate.column_count or 0),
            "roleHistogram": list(_role_histogram(system)),
            "angleHistogram": list(_angle_histogram(system)),
            "fingerprintSize": len(key),
            "fingerprintHash": hashlib.sha256(fingerprint_text.encode("utf-8")).hexdigest()[:16],
        }
        ranked.append(candidate)
        ranked_systems.append(system)
        append_event(
            "candidate-geometry", "candidate-accepted",
            projectId=project.id, rank=candidate.rank, candidateId=candidate.id,
            topologyFamily=candidate.variable_summary.get("topologyFamily"), geometryDelta=minimum_geometry_delta,
            supportCount=candidate.support_count, columnCount=candidate.column_count,
            roleHistogram=list(_role_histogram(system)), angleHistogram=list(_angle_histogram(system)),
        )
        if selected_system is None:
            selected_system = system
        return True

    feasible_pairs = [pair for pair in candidates if pair[0].hard_constraints.get("passed", False) and pair[0].score >= 1.0]
    if not feasible_pairs and candidates:
        # Retain up to three *actually different* diagnostic alternatives. This
        # helps the engineer see whether the controlled block is caused by bay
        # density, terminal bracing or column service span. Every card remains
        # explicitly non-adoptable as a formal scheme until hard constraints pass.
        for diagnostic, diagnostic_system in candidates:
            diagnostic.variable_summary = dict(diagnostic.variable_summary or {})
            diagnostic.variable_summary["capabilityOutcome"] = "controlled_block"
            diagnostic.variable_summary["formalSchemeEligible"] = False
            diagnostic.variable_summary["shapeDiagnostics"] = shape
            diagnostic.variable_summary["alternativeSystemRecommendations"] = [
                "环梁/环撑体系",
                "中心岛法或分区施工",
                "具有平面内弯剪刚度和节点构造的显式双向框架",
            ]
            blocking_text = "、".join(
                map(str, (diagnostic.hard_constraints or {}).get("blockingCategories") or [])
            )
            diagnostic.constructability_note = (
                "当前轴压墙—墙构件模型尚未通过全部硬约束。该卡片仅用于比较真实几何差异和定位受控阻断，"
                "不得作为正式采用方案；请调整结构体系或设计约束后重新优化。"
                + (f" 当前控制类别：{blocking_text}。" if blocking_text else "")
            )
            add_candidate(diagnostic, diagnostic_system, force=not ranked)
            if len(ranked) >= min(max_candidates, 3):
                break
        memory_event(
            "candidate-search",
            "search-complete",
            trialCount=trial_count,
            rankedCandidateCount=len(ranked),
            retainedSystemCount=1 if selected_system is not None else 0,
            controlledBlock=True,
            distinctGeometryCount=len({str(_geometry_fingerprint(item)) for item in ranked_systems}),
            candidateIds=[item.id for item in ranked],
            blockingCandidates=[
                {
                    "candidateId": item.id,
                    "targetSpacingM": item.target_spacing,
                    "columnSpanM": item.column_max_span,
                    "blockingCategories": list((item.hard_constraints or {}).get("blockingCategories") or []),
                    "hardFailureKeys": list((item.hard_constraints or {}).get("hardFailureKeys") or []),
                    "controlMetrics": dict(((item.variable_summary or {}).get("topologyQualification") or {}).get("controlMetrics") or {}),
                }
                for item in ranked[:3]
            ],
        )
        return selected_system, ranked

    # Select one feasible representative from each topology family first.  The
    # operator compares complete A/B/C schemes rather than confirming wall faces
    # one by one.  Remaining slots are filled by score and spacing diversity.
    family_best: list[tuple[SupportLayoutOptimizationCandidate, RetainingSystem]] = []
    scheme_families = list(dict.fromkeys(
        str((pair[0].variable_summary or {}).get("schemeFamily") or (pair[0].variable_summary or {}).get("topologyFamily") or "unknown")
        for pair in candidates
    ))
    for family in scheme_families:
        item = next((
            pair for pair in candidates
            if pair[0].hard_constraints.get("passed", False)
            and str((pair[0].variable_summary or {}).get("schemeFamily") or (pair[0].variable_summary or {}).get("topologyFamily") or "unknown") == family
        ), None)
        if item:
            family_best.append(item)
    family_best.sort(key=lambda item: (int(item[0].fail_count or 0), _status_rank(item[0].status), *_cleanliness_sort_key(item[0]), -item[0].score))
    for candidate, system in family_best:
        add_candidate(candidate, system, force=not ranked)
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
        if add_candidate(candidate, system):
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
        # When at least one feasible scheme exists, failed trial geometries are
        # diagnostic evidence only and must not be mixed into the formal A/B/C
        # list. This also prevents a failed candidate from being selected merely
        # because it was the last displayed card.
        if candidate.score < 1.0 or not candidate.hard_constraints.get("passed", False):
            continue
        add_candidate(candidate, system)
        if len(ranked) >= max_candidates:
            break
    for idx, candidate in enumerate(ranked, start=1):
        candidate.rank = idx
        candidate.variable_summary = dict(candidate.variable_summary or {})
        candidate.variable_summary["diversityBasis"] = {
            "topologyFamily": candidate.variable_summary.get("topologyFamily"),
            "targetSpacingM": candidate.target_spacing,
            "columnSpanM": candidate.column_max_span,
            "supportCount": candidate.support_count,
            "columnCount": candidate.column_count,
            "positionPattern": candidate.variable_summary.get("positionPattern"),
        }
    memory_event(
        "candidate-search",
        "search-complete",
        trialCount=trial_count,
        rankedCandidateCount=len(ranked),
        retainedSystemCount=1 if selected_system is not None else 0,
        controlledBlock=False,
        distinctGeometryCount=len({str(_geometry_fingerprint(item)) for item in ranked_systems}),
        candidateIds=[item.id for item in ranked],
    )
    return selected_system, ranked
