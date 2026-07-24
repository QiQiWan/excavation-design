from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.schemas.domain import CalculationCase, CalculationResult, Project, StageCalculationResult
from app.calculation.stability_metric_semantics import classify_stability_metric, normalized_utilization, select_controlling, stability_metric_rows


_STATUS_RANK = {"pass": 0, "not_applicable": 0, "warning": 1, "manual_review": 2, "missing": 3, "fail": 4}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _status_worst(values: list[str], default: str = "pass") -> str:
    clean = [str(item or default) for item in values]
    return max(clean, key=lambda item: _STATUS_RANK.get(item, 2), default=default)


def _check_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "warning": 0, "manual_review": 0, "fail": 0}
    for item in checks:
        status = str(item.get("status") or "manual_review")
        if status not in counts:
            status = "manual_review"
        counts[status] += 1
    return counts


def _stage_rows(case: CalculationCase, stage_results: list[StageCalculationResult]) -> list[dict[str, Any]]:
    by_stage: dict[str, list[StageCalculationResult]] = defaultdict(list)
    for row in stage_results:
        by_stage[str(row.stage_id)].append(row)
    stage_lookup = {str(stage.id): stage for stage in case.stages}
    rows: list[dict[str, Any]] = []
    for stage_id, segments in by_stage.items():
        stage = stage_lookup.get(stage_id)
        checks = [dict(item) for segment in segments for item in segment.checks]
        conditions: list[float] = []
        residuals: list[float] = []
        fallbacks = 0
        for segment in segments:
            global_result = segment.global_coupled_result
            if global_result:
                conditions.append(_finite(global_result.scaled_condition_number or global_result.condition_number))
                residuals.append(_finite((global_result.equilibrium_diagnostics or {}).get("relativeResidual")))
                fallbacks += int(bool(global_result.fallback))
        support_forces = [force for segment in segments for force in segment.support_forces]
        wale_results = [wale for segment in segments for wale in (segment.wale_beam_results or [])]
        wall_results = [segment.wall_internal_force for segment in segments if segment.wall_internal_force]
        pressures = [abs(_finite(point.total_pressure)) for segment in segments for point in segment.pressure_profile.points]
        metric_rows = stability_metric_rows(checks)
        safety_control = select_controlling(metric_rows, "safety_factor")
        risk_control = select_controlling(metric_rows, "risk_ratio")
        if risk_control and _finite(risk_control.get("utilization")) <= 0.0:
            risk_control = None
        counts = _check_counts(checks)
        row = {
            "stageId": stage_id,
            "stageName": str(getattr(stage, "name", stage_id)),
            "stageType": str(getattr(stage, "stage_type", "unknown")),
            "excavationElevationM": _finite(getattr(stage, "excavation_elevation", None), math.nan),
            "activeSupportCount": len(set(getattr(stage, "active_support_ids", []) or [])),
            "transferredSupportLevelCount": len(set(getattr(stage, "transferred_support_levels", []) or [])),
            "segmentCount": len(segments),
            "maximumPressureKpa": max(pressures, default=0.0),
            "maximumWallMomentKnmPerM": max((abs(_finite(item.max_moment)) for item in wall_results), default=0.0),
            "maximumWallShearKnPerM": max((abs(_finite(item.max_shear)) for item in wall_results), default=0.0),
            "maximumWallDisplacementMm": max((abs(_finite(item.max_displacement)) for item in wall_results), default=0.0),
            "maximumSupportForceKn": max((abs(_finite(item.axial_force_design or item.axial_force)) for item in support_forces), default=0.0),
            "maximumWaleMomentKnm": max((abs(_finite(item.max_moment_design or item.max_moment)) for item in wale_results), default=0.0),
            "maximumWaleShearKn": max((abs(_finite(item.max_shear_design or item.max_shear)) for item in wale_results), default=0.0),
            "maximumWaleDeflectionM": max((abs(_finite(item.max_deflection)) for item in wale_results), default=0.0),
            "maximumScaledConditionNumber": max(conditions, default=0.0),
            "maximumRelativeResidual": max(residuals, default=0.0),
            "fallbackCount": fallbacks,
            "minimumStabilityFactor": (safety_control or {}).get("value"),
            "controllingSafetyMode": (safety_control or {}).get("metricId"),
            "maximumStabilityRiskUtilization": (risk_control or {}).get("utilization"),
            "controllingStabilityRiskMode": (risk_control or {}).get("metricId"),
            "stabilityMetricSemantics": metric_rows,
            "checkCounts": counts,
            "status": "fail" if counts["fail"] else "manual_review" if counts["manual_review"] else "warning" if counts["warning"] else "pass",
        }
        rows.append(row)
    order = {str(stage.id): index for index, stage in enumerate(case.stages)}
    rows.sort(key=lambda item: order.get(str(item["stageId"]), 10**9))
    return rows


def _critical_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    maxima = {
        key: max((_finite(row.get(key)) for row in rows), default=1.0) or 1.0
        for key in (
            "maximumWallDisplacementMm", "maximumWallMomentKnmPerM", "maximumSupportForceKn",
            "maximumWaleMomentKnm", "maximumScaledConditionNumber", "maximumRelativeResidual",
        )
    }
    scored: list[dict[str, Any]] = []
    for row in rows:
        counts = dict(row.get("checkCounts") or {})
        components = {
            "wallDisplacement": _finite(row.get("maximumWallDisplacementMm")) / maxima["maximumWallDisplacementMm"],
            "wallMoment": _finite(row.get("maximumWallMomentKnmPerM")) / maxima["maximumWallMomentKnmPerM"],
            "supportForce": _finite(row.get("maximumSupportForceKn")) / maxima["maximumSupportForceKn"],
            "waleMoment": _finite(row.get("maximumWaleMomentKnm")) / maxima["maximumWaleMomentKnm"],
            "numericalCondition": math.log10(max(_finite(row.get("maximumScaledConditionNumber")), 1.0)) / max(math.log10(max(maxima["maximumScaledConditionNumber"], 10.0)), 1.0),
            "checkPenalty": min(1.0, 0.4 * int(counts.get("fail", 0)) + 0.08 * int(counts.get("warning", 0)) + 0.12 * int(counts.get("manual_review", 0))),
        }
        score = (
            0.24 * components["wallDisplacement"] + 0.18 * components["wallMoment"]
            + 0.18 * components["supportForce"] + 0.12 * components["waleMoment"]
            + 0.10 * components["numericalCondition"] + 0.18 * components["checkPenalty"]
        )
        reasons = [key for key, value in sorted(components.items(), key=lambda item: item[1], reverse=True) if value >= 0.70][:3]
        scored.append({
            "stageId": row["stageId"],
            "stageName": row["stageName"],
            "stageType": row["stageType"],
            "criticalityScore": round(score * 100.0, 2),
            "status": row["status"],
            "reasons": reasons,
            "metrics": {key: row.get(key) for key in (
                "maximumWallDisplacementMm", "maximumWallMomentKnmPerM", "maximumSupportForceKn",
                "maximumWaleMomentKnm", "minimumStabilityFactor", "maximumScaledConditionNumber",
            )},
        })
    return sorted(scored, key=lambda item: item["criticalityScore"], reverse=True)[:10]


def _support_envelopes(project: Project, rows: list[StageCalculationResult]) -> list[dict[str, Any]]:
    stage_map: dict[str, list[tuple[str, str, Any]]] = defaultdict(list)
    for stage in rows:
        for force in stage.support_forces:
            if force.support_id:
                stage_map[str(force.support_id)].append((str(stage.stage_id), str(stage.segment_id), force))
    supports = {str(item.id): item for item in project.retaining_system.supports}
    output: list[dict[str, Any]] = []
    for support_id, values in stage_map.items():
        governing = max(values, key=lambda item: abs(_finite(item[2].axial_force_design or item[2].axial_force)))
        force = governing[2]
        support = supports.get(support_id)
        output.append({
            "supportId": support_id,
            "supportCode": str(getattr(support, "code", support_id)),
            "levelIndex": int(getattr(support, "level_index", force.level_index) or 0),
            "role": str(getattr(support, "support_role", "unknown")),
            "governingStageId": governing[0],
            "governingSegmentId": governing[1],
            "maximumStandardForceKn": max(abs(_finite(item[2].axial_force)) for item in values),
            "maximumDesignForceKn": max(abs(_finite(item[2].axial_force_design or item[2].axial_force)) for item in values),
            "maximumEffectiveForceKn": max(abs(_finite(item[2].effective_axial_force or item[2].axial_force)) for item in values),
            "maximumReconciliationRatio": max((_finite(item[2].force_reconciliation_ratio) for item in values), default=0.0),
            "reconciliationStatus": _status_worst([str(item[2].force_reconciliation_status or "pass") for item in values]),
            "resultCount": len(values),
        })
    output.sort(key=lambda item: item["maximumDesignForceKn"], reverse=True)
    return output


def _wall_envelopes(project: Project, rows: list[StageCalculationResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[StageCalculationResult]] = defaultdict(list)
    for stage in rows:
        if stage.wall_internal_force:
            grouped[str(stage.segment_id)].append(stage)
    segments = {str(item.id): item for item in project.excavation.segments}
    output: list[dict[str, Any]] = []
    for segment_id, values in grouped.items():
        moment = max(values, key=lambda item: abs(_finite(item.wall_internal_force.max_moment)))
        shear = max(values, key=lambda item: abs(_finite(item.wall_internal_force.max_shear)))
        displacement = max(values, key=lambda item: abs(_finite(item.wall_internal_force.max_displacement)))
        segment = segments.get(segment_id)
        output.append({
            "segmentId": segment_id,
            "segmentName": str(getattr(segment, "name", segment_id)),
            "maximumMomentKnmPerM": abs(_finite(moment.wall_internal_force.max_moment)),
            "momentStageId": str(moment.stage_id),
            "maximumShearKnPerM": abs(_finite(shear.wall_internal_force.max_shear)),
            "shearStageId": str(shear.stage_id),
            "maximumDisplacementMm": abs(_finite(displacement.wall_internal_force.max_displacement)),
            "displacementStageId": str(displacement.stage_id),
            "stageCount": len(values),
        })
    output.sort(key=lambda item: item["maximumDisplacementMm"], reverse=True)
    return output


def _wale_envelopes(rows: list[StageCalculationResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for stage in rows:
        for item in stage.wale_beam_results or []:
            grouped[str(item.wale_beam_code)].append((str(stage.stage_id), item))
    output: list[dict[str, Any]] = []
    for code, values in grouped.items():
        governing_moment = max(values, key=lambda row: abs(_finite(row[1].max_moment_design or row[1].max_moment)))
        governing_shear = max(values, key=lambda row: abs(_finite(row[1].max_shear_design or row[1].max_shear)))
        governing_deflection = max(values, key=lambda row: abs(_finite(row[1].max_deflection)))
        item = governing_moment[1]
        output.append({
            "waleBeamCode": code,
            "faceCode": str(getattr(item, "face_code", "")),
            "levelIndex": int(getattr(item, "level_index", 0) or 0),
            "maximumMomentKnm": abs(_finite(governing_moment[1].max_moment)),
            "maximumDesignMomentKnm": abs(_finite(governing_moment[1].max_moment_design or governing_moment[1].max_moment)),
            "momentStageId": governing_moment[0],
            "maximumShearKn": abs(_finite(governing_shear[1].max_shear)),
            "maximumDesignShearKn": abs(_finite(governing_shear[1].max_shear_design or governing_shear[1].max_shear)),
            "shearStageId": governing_shear[0],
            "maximumDeflectionM": abs(_finite(governing_deflection[1].max_deflection)),
            "deflectionStageId": governing_deflection[0],
            "stageCount": len(values),
        })
    output.sort(key=lambda item: item["maximumDesignMomentKnm"], reverse=True)
    return output


def _column_foundation_envelopes(project: Project) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for column in project.retaining_system.columns or []:
        foundation = column.foundation_design
        output.append({
            "columnId": str(column.id),
            "columnCode": str(column.code),
            "topElevationM": _finite(column.top_elevation),
            "bottomElevationM": _finite(column.bottom_elevation),
            "supportedMemberCount": len(column.support_codes or []),
            "foundationType": str(getattr(foundation, "foundation_type", "missing")),
            "verticalForceKn": _finite(getattr(foundation, "vertical_force", None)),
            "maximumBearingPressureKpa": _finite(getattr(foundation, "max_pressure", None)),
            "allowableBearingPressureKpa": _finite(getattr(foundation, "fa", None)),
            "bearingUtilization": (
                _finite(getattr(foundation, "max_pressure", None)) / max(_finite(getattr(foundation, "fa", None)), 1.0e-9)
                if foundation else None
            ),
            "foundationStatus": str(getattr(foundation, "check_status", "missing")),
        })
    output.sort(key=lambda item: _finite(item.get("bearingUtilization")), reverse=True)
    return output


def _node_hotspots(project: Project) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in project.retaining_system.support_nodes or []:
        spatial = dict(node.spatial_detailing or {})
        plate = node.bearing_plate
        torsion = abs(_finite(spatial.get("torsionKnm") or spatial.get("maximumTorsionKnm")))
        out_of_plane = abs(_finite(spatial.get("outOfPlaneMomentKnm") or spatial.get("maximumOutOfPlaneMomentKnm")))
        eccentric = abs(_finite(spatial.get("inPlaneEccentricMomentKnm") or spatial.get("maximumInPlaneEccentricMomentKnm")))
        rotation = abs(_finite(spatial.get("maximumAbsoluteRotationRad") or spatial.get("jointRotationRad")))
        bearing_utilization = (
            _finite(getattr(plate, "bearing_stress", None)) / max(_finite(getattr(plate, "bearing_capacity", None)), 1.0e-9)
            if plate and getattr(plate, "bearing_capacity", None) else 0.0
        )
        score = max(
            min(1.0, rotation / 0.01) if rotation else 0.0,
            min(1.0, bearing_utilization),
            min(1.0, torsion / 1000.0),
            min(1.0, (out_of_plane + eccentric) / 500.0),
            1.0 if str(node.check_status) == "fail" else 0.7 if str(node.check_status) in {"warning", "manual_review"} else 0.0,
        )
        rows.append({
            "nodeId": str(node.id),
            "nodeCode": str(node.code),
            "supportCode": str(node.support_code),
            "levelIndex": int(node.level_index or 0),
            "nodeType": str(node.node_type),
            "checkStatus": str(node.check_status),
            "bearingUtilization": bearing_utilization,
            "maximumRotationRad": rotation,
            "torsionKnm": torsion,
            "outOfPlaneMomentKnm": out_of_plane,
            "eccentricInPlaneMomentKnm": eccentric,
            "criticalityScore": round(score * 100.0, 2),
        })
    rows.sort(key=lambda item: item["criticalityScore"], reverse=True)
    return rows[:50]


def _stability_modes(result: CalculationResult) -> list[dict[str, Any]]:
    detailed = result.stability_detailed_result
    if not detailed:
        return []
    definitions = [
        ("embedment", "嵌固稳定", "safety_factor", "larger_is_better", detailed.embedment_factor, detailed.embedment_limit),
        ("base_heave", "坑底隆起", "safety_factor", "larger_is_better", detailed.heave_factor, detailed.heave_limit),
        ("confined_uplift", "承压水突涌", "safety_factor", "larger_is_better", detailed.confined_uplift_factor, detailed.confined_uplift_limit),
        ("seepage", "渗流稳定", "safety_factor", "larger_is_better", detailed.seepage_factor, detailed.seepage_limit),
        ("overall", "整体稳定", "safety_factor", "larger_is_better", detailed.overall_stability_factor, detailed.overall_stability_limit),
        ("layered_seepage", "分层渗透风险", "risk_ratio", "smaller_is_better", detailed.layered_seepage_risk_index, detailed.layered_seepage_risk_limit),
        ("dewatering", "降水阶段控制", "risk_ratio", "smaller_is_better", detailed.dewatering_control_ratio, detailed.dewatering_control_limit),
        ("weak_layer", "软弱下卧层", "quality_index", "larger_is_better", detailed.weak_layer_index, detailed.weak_layer_limit),
    ]
    rows: list[dict[str, Any]] = []
    for mode_id, label, metric_type, direction, raw_value, raw_limit in definitions:
        available = raw_value is not None and math.isfinite(_finite(raw_value, math.nan))
        value = _finite(raw_value, math.nan)
        limit = _finite(raw_limit, math.nan)
        utilization = None
        reserve = None
        if available and math.isfinite(limit) and abs(limit) > 1.0e-12:
            if direction == "larger_is_better":
                utilization = limit / max(value, 1.0e-12)
                reserve = value / limit
            else:
                utilization = value / limit
                reserve = None if value <= 1.0e-12 else limit / value
        controlling = (
            metric_type == "safety_factor" and mode_id == detailed.controlling_safety_mode
        ) or (
            metric_type == "risk_ratio" and mode_id == detailed.controlling_risk_mode
        )
        rows.append({
            "modeId": mode_id,
            "label": label,
            "metricType": metric_type,
            "direction": direction,
            "value": value,
            "factor": value if metric_type == "safety_factor" else None,
            "limit": limit,
            "utilization": utilization,
            "reserveRatio": reserve,
            "available": available,
            "controlling": controlling,
            "controllingScope": "safety" if metric_type == "safety_factor" and controlling else "risk" if metric_type == "risk_ratio" and controlling else None,
        })
    return rows



def _check_ledger(checks: list[dict[str, Any]], statuses: set[str], limit: int = 100) -> list[dict[str, Any]]:
    rank = {"fail": 0, "manual_review": 1, "warning": 2, "pass": 3}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for check in checks:
        status = str(check.get("status") or "manual_review")
        if status not in statuses:
            continue
        rule_id = str(check.get("ruleId", check.get("rule_id", "")))
        object_id = str(check.get("objectId", check.get("object_id", "")))
        message = str(check.get("message") or "")
        key = (rule_id, object_id, status, message)
        if key in seen:
            continue
        seen.add(key)
        semantic = classify_stability_metric(check)
        calculated = check.get("calculatedValue", check.get("calculated_value"))
        limit_value = check.get("limitValue", check.get("limit_value"))
        utilization = normalized_utilization(check, semantic) if semantic else None
        if utilization is None and isinstance(calculated, (int, float)) and isinstance(limit_value, (int, float)) and abs(float(limit_value)) > 1.0e-12:
            utilization = abs(float(calculated) / float(limit_value))
        rows.append({
            "ruleId": rule_id,
            "objectId": object_id or None,
            "objectType": check.get("objectType", check.get("object_type")),
            "status": status,
            "calculatedValue": calculated,
            "limitValue": limit_value,
            "unit": check.get("unit"),
            "utilization": utilization,
            "metricType": semantic.metric_type if semantic else None,
            "direction": semantic.direction if semantic else None,
            "message": message,
            "clauseReference": check.get("clauseReference", check.get("clause_reference")),
        })
    rows.sort(key=lambda row: (rank.get(str(row["status"]), 9), -_finite(row.get("utilization")), str(row["ruleId"]), str(row.get("objectId") or "")))
    return rows[:limit]


def _rule_status_counts(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "warning": 0, "manual_review": 0, "fail": 0})
    for check in checks:
        rule_id = str(check.get("ruleId", check.get("rule_id", "unknown")))
        status = str(check.get("status") or "manual_review")
        if status not in grouped[rule_id]:
            status = "manual_review"
        grouped[rule_id][status] += 1
    rows = [{"ruleId": rule_id, **counts, "total": sum(counts.values())} for rule_id, counts in grouped.items()]
    rows.sort(key=lambda row: (-int(row["fail"]), -int(row["manual_review"]), -int(row["warning"]), str(row["ruleId"])))
    return rows

def _reinforcement_inventory(project: Project) -> dict[str, Any]:
    walls = [group for wall in project.retaining_system.diaphragm_walls for group in wall.reinforcement or []]
    beams = [group for beam in (project.retaining_system.wale_beams + project.retaining_system.ring_beams + project.retaining_system.crown_beams) for group in beam.reinforcement or []]
    supports = [group for support in project.retaining_system.supports for group in support.reinforcement or []]
    nodes = [group for node in project.retaining_system.support_nodes for group in node.reinforcement or []]
    all_groups = walls + beams + supports + nodes
    role_counts: dict[str, int] = defaultdict(int)
    for group in all_groups:
        role_counts[str(getattr(group, "bar_type", "unknown"))] += 1
    return {
        "wallGroupCount": len(walls),
        "beamGroupCount": len(beams),
        "supportGroupCount": len(supports),
        "nodeGroupCount": len(nodes),
        "totalGroupCount": len(all_groups),
        "roleCounts": dict(role_counts),
        "membersWithoutReinforcement": {
            "walls": sum(not wall.reinforcement for wall in project.retaining_system.diaphragm_walls),
            "beams": sum(not beam.reinforcement for beam in (project.retaining_system.wale_beams + project.retaining_system.ring_beams + project.retaining_system.crown_beams)),
            "supports": sum(not support.reinforcement for support in project.retaining_system.supports if support.section_type == "rc_rectangular"),
            "nodes": sum(not node.reinforcement for node in project.retaining_system.support_nodes),
        },
    }


def _numerical_health(project: Project, rows: list[StageCalculationResult]) -> dict[str, Any]:
    systems = [stage.global_coupled_result for stage in rows if stage.global_coupled_result]
    grades = defaultdict(int)
    for item in systems:
        grade = str((item.condition_grade or {}).get("grade") or (item.numerical_gate or {}).get("conditionGrade", {}).get("grade") or "unknown")
        grades[grade] += 1
    residuals = [_finite((item.equilibrium_diagnostics or {}).get("relativeResidual")) for item in systems]
    reaction = dict((project.advanced_engineering or {}).get("wallWaleTransferReactionIteration") or {})
    transfer = dict((project.advanced_engineering or {}).get("concaveTransferFrameAnalysis") or {})
    spatial = dict((project.advanced_engineering or {}).get("concaveTransferSpatialAnalysis") or {})
    fail_count = sum(bool(item.ill_conditioned_blocked) for item in systems)
    warning_count = sum(str((item.condition_grade or {}).get("status")) == "warning" for item in systems)
    status = "fail" if fail_count or reaction.get("status") == "fail" or transfer.get("status") == "fail" or spatial.get("status") == "fail" else "warning" if warning_count or reaction.get("status") == "warning" or transfer.get("status") == "warning" or spatial.get("status") == "warning" else "pass"
    return {
        "schema": "pitguard-numerical-health-v1",
        "status": status,
        "globalSystemCount": len(systems),
        "conditionGradeCounts": dict(grades),
        "maximumRawConditionNumber": max((_finite(item.raw_condition_number or item.condition_number) for item in systems), default=0.0),
        "maximumScaledConditionNumber": max((_finite(item.scaled_condition_number or item.condition_number) for item in systems), default=0.0),
        "maximumRelativeResidual": max(residuals, default=0.0),
        "fallbackCount": sum(bool(item.fallback) for item in systems),
        "blockedSystemCount": fail_count,
        "reactionIteration": {
            "status": reaction.get("status") or "not_required",
            "converged": reaction.get("converged"),
            "iterationCount": reaction.get("iterationCount"),
            "forceResidual": reaction.get("finalForceRelativeResidual"),
            "displacementResidual": reaction.get("finalDisplacementRelativeResidual"),
            "relaxationHistory": reaction.get("relaxationHistory") or [],
            "oscillationDetected": reaction.get("oscillationDetected", False),
        },
        "transferFrame": {
            "status": transfer.get("status") or "not_required",
            "maximumScaledConditionNumber": transfer.get("maximumScaledConditionNumber"),
            "maximumNodeStiffnessRatio": transfer.get("maximumNodeStiffnessRatio"),
            "maximumRelativeResidual": transfer.get("maximumRelativeResidual"),
            "sensitivity": transfer.get("sensitivity") or {},
        },
        "spatialNode": {
            "status": spatial.get("status") or "not_required",
            "maximumRotationRad": spatial.get("maximumJointRotationRad"),
            "maximumTorsionKnm": spatial.get("maximumTorsionKnm"),
            "maximumScaledConditionNumber": spatial.get("maximumScaledConditionNumber"),
            "maximumEquilibriumResidual": spatial.get("maximumEquilibriumResidual"),
        },
    }


def _domain(domain_id: str, label: str, status: str, coverage: float, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "domainId": domain_id,
        "label": label,
        "status": status,
        "coveragePercent": round(max(0.0, min(100.0, coverage)), 1),
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _completeness(project: Project, case: CalculationCase, result: CalculationResult, numerical: dict[str, Any]) -> dict[str, Any]:
    expected_stage_segments = max(1, len(case.stages) * len(project.excavation.segments))
    actual_stage_segments = len(result.stage_results)
    wall_count = sum(bool(item.wall_internal_force) for item in result.stage_results)
    transfer_required = bool(((project.retaining_system.layout_summary or {}).get("transferSystem") or {}).get("required"))
    transfer = dict((project.advanced_engineering or {}).get("concaveTransferFrameAnalysis") or {})
    detailing = dict((project.advanced_engineering or {}).get("concaveTransferAutoDetailing") or {})
    data = dict((project.advanced_engineering or {}).get("transferEngineeringDataAssurance") or {})
    formal = result.formal_report_gate
    geology = dict((result.design_iteration_summary or {}).get("geologyCoverage") or {})
    analysis_assurance = dict(result.analysis_assurance or {})
    geotechnical_assurance = dict(result.geotechnical_assurance or {})
    spatial_verification = dict(result.spatial_verification or {})
    verification_matrix = dict(result.verification_matrix or {})
    statutory_workflow = dict(result.statutory_workflow_assurance or {})
    domains = [
        _domain("input_contract", "输入快照与单位", "pass" if result.input_snapshot_hash and result.calculation_contract_id else "fail", 100.0 if result.input_snapshot_hash and result.calculation_contract_id else 0.0, "计算输入已冻结并生成不可变合同。" if result.input_snapshot_hash else "缺少不可变输入快照。"),
        _domain("geology", "地质与地下水", "pass" if geology.get("designDomainCovered") and len(project.boreholes) >= 3 else "warning" if project.strata else "fail", 100.0 if geology.get("designDomainCovered") and len(project.boreholes) >= 3 else 60.0 if project.strata else 0.0, str(geology.get("message") or "缺少地质覆盖证据。"), {"boreholeCount": len(project.boreholes), "stratumCount": len(project.strata)}),
        _domain("construction_stages", "施工阶段", "pass" if case.stages and all(getattr(stage, "support_topology_hash", None) for stage in case.stages) else "warning" if case.stages else "fail", 100.0 if case.stages and all(getattr(stage, "support_topology_hash", None) for stage in case.stages) else 70.0 if case.stages else 0.0, f"已计算 {len(case.stages)} 个施工阶段。", {"stageCount": len(case.stages)}),
        _domain("wall_results", "围护墙内力与位移", "pass" if wall_count == expected_stage_segments else "warning" if wall_count else "fail", 100.0 * wall_count / expected_stage_segments, f"获得 {wall_count}/{expected_stage_segments} 个阶段-墙段结果。"),
        _domain("support_wale_results", "支撑与围檩结果", "pass" if any(item.support_forces for item in result.stage_results) and any(item.wale_beam_results for item in result.stage_results) else "warning", 100.0 if any(item.support_forces for item in result.stage_results) and any(item.wale_beam_results for item in result.stage_results) else 60.0, "支撑轴力、节点反力和围檩内力已形成施工阶段包络。"),
        _domain("numerical_health", "数值稳定性", str(numerical.get("status") or "manual_review"), 100.0 if numerical.get("status") == "pass" else 75.0 if numerical.get("status") == "warning" else 0.0, "刚度条件、平衡残差、反力迭代和敏感性已统一评估。", numerical),
        _domain("analysis_assurance", "分析等级与参数来源", str(analysis_assurance.get("status") or "missing"), 100.0 if analysis_assurance.get("formalIssueEligible") else 65.0 if analysis_assurance else 0.0, "模型等级、参数来源、默认值和代理模型已形成保证包。" if analysis_assurance else "缺少分析保证包。", analysis_assurance),
        _domain("geotechnical_model", "非线性岩土与地下水", str(geotechnical_assurance.get("status") or "missing"), 100.0 if geotechnical_assurance.get("formalUseAllowed") else 65.0 if geotechnical_assurance else 0.0, "位移动员、切线刚度和水位敏感性已评估。" if geotechnical_assurance else "缺少岩土模型保证包。", geotechnical_assurance),
        _domain("spatial_verification", "六自由度空间验证", str(spatial_verification.get("status") or "not_applicable"), 100.0 if spatial_verification.get("status") in {"pass", "not_applicable"} else 70.0 if spatial_verification else 0.0, "空间杆系与平面模型差异已评估。" if spatial_verification else "当前体系未形成空间验证结果。", spatial_verification),
        _domain("verification_matrix", "验证矩阵与外部基准", str(verification_matrix.get("status") or "missing"), 100.0 if verification_matrix.get("status") == "pass" else 75.0 if verification_matrix.get("status") == "warning" else 0.0, "单元、组装、独立参考与外部软件证书已汇总。" if verification_matrix else "缺少验证矩阵。", verification_matrix),
        _domain("transfer_system", "异形转接体系", "not_applicable" if not transfer_required else str(transfer.get("status") or "missing"), 100.0 if not transfer_required or transfer.get("status") == "pass" else 75.0 if transfer.get("status") == "warning" else 0.0, "规则平面无需异形转接。" if not transfer_required else "异形转接框架已完成施工阶段分析。" if transfer else "缺少转接框架结果。"),
        _domain("strength_verification", "构件强度与稳定", str(result.design_review_summary.strength_status if result.design_review_summary else "missing"), 100.0 if result.design_review_summary and result.design_review_summary.strength_status == "pass" else 70.0 if result.design_review_summary and result.design_review_summary.strength_status in {"warning", "manual_review"} else 0.0, "墙、围檩、支撑、节点、立柱及基础的强度和构件稳定检查已汇总。"),
        _domain("deformation_verification", "变形与刚度", str(result.design_review_summary.stiffness_status if result.design_review_summary else "missing"), 100.0 if result.design_review_summary and result.design_review_summary.stiffness_status == "pass" else 70.0 if result.design_review_summary and result.design_review_summary.stiffness_status in {"warning", "manual_review"} else 0.0, "墙体位移、围檩挠度和联立体系刚度检查已汇总。"),
        _domain("stability", "岩土稳定与地下水", str(result.design_review_summary.stability_status if result.design_review_summary else "missing"), 100.0 if result.stability_detailed_result and result.design_review_summary and result.design_review_summary.stability_status == "pass" else 75.0 if result.stability_detailed_result else 0.0, "抗隆起、嵌固、整体稳定、承压水和渗流专项结果已按指标方向汇总。" if result.stability_detailed_result else "缺少稳定专项包。"),
        _domain("detailing", "构件与节点深化", "not_applicable" if not transfer_required else str(detailing.get("status") or "missing"), 100.0 if not transfer_required or detailing.get("status") == "pass" else 60.0 if detailing else 0.0, "构件配筋、抗扭、承压、加腋与锚固证据已生成。" if detailing else "缺少异形转接深化证据。"),
        _domain("drawings_ifc", "图纸与BIM", _status_worst([str(result.ifc_compatibility.status if result.ifc_compatibility else "missing"), "pass" if result.drawing_sheets else "missing"]), 100.0 if result.drawing_sheets and result.ifc_compatibility and result.ifc_compatibility.status == "pass" else 60.0 if result.drawing_sheets else 0.0, f"已生成 {len(result.drawing_sheets)} 张图纸成果。"),
        _domain("statutory_workflow", "责任分阶段法定流程", str(statutory_workflow.get("status") or "missing"), 100.0 if statutory_workflow.get("formalIssueEligible") else 45.0 if statutory_workflow else 0.0, "设计发行、施工准备和现场放行证据已按责任阶段分开汇总。" if statutory_workflow else "缺少法定流程保证包。", statutory_workflow),
        _domain("data_and_signoff", "真实资料与专业审签", "pass" if formal and formal.allowed_for_official_issue else "fail", 100.0 if formal and formal.allowed_for_official_issue else 25.0 if data else 0.0, "正式发行证据已闭合。" if formal and formal.allowed_for_official_issue else "真实资料、执业资格或正式发行闸门尚未闭合。", {"formalGateStatus": formal.status if formal else None, "formalIssueAllowed": formal.allowed_for_official_issue if formal else False}),
    ]
    engineering_domains = [item for item in domains if item["domainId"] != "data_and_signoff"]
    engineering_score = sum(float(item["coveragePercent"]) for item in engineering_domains) / max(len(engineering_domains), 1)
    formal_score = sum(float(item["coveragePercent"]) for item in domains) / max(len(domains), 1)

    # Completeness describes how many result domains exist. Readiness additionally respects
    # the engineering importance of each domain and cannot average away a critical block.
    weights = {
        "input_contract": 0.04, "geology": 0.09, "construction_stages": 0.04,
        "wall_results": 0.06, "support_wale_results": 0.06, "numerical_health": 0.06,
        "analysis_assurance": 0.07, "geotechnical_model": 0.07, "spatial_verification": 0.05,
        "verification_matrix": 0.04, "transfer_system": 0.05, "strength_verification": 0.09,
        "deformation_verification": 0.06, "stability": 0.08, "detailing": 0.07,
        "drawings_ifc": 0.04, "statutory_workflow": 0.03,
    }
    by_id = {str(item["domainId"]): item for item in domains}
    engineering_readiness = sum(
        float(by_id[domain_id]["coveragePercent"]) * weight
        for domain_id, weight in weights.items() if domain_id in by_id
    )
    critical_caps = {
        "input_contract": 45.0, "geology": 65.0, "wall_results": 50.0,
        "support_wale_results": 55.0, "numerical_health": 50.0,
        "analysis_assurance": 50.0, "geotechnical_model": 50.0,
        "spatial_verification": 55.0, "verification_matrix": 60.0,
        "transfer_system": 55.0, "strength_verification": 55.0,
        "deformation_verification": 65.0, "stability": 60.0, "detailing": 70.0,
    }
    critical_blocks = [
        domain_id for domain_id, cap in critical_caps.items()
        if domain_id in by_id and str(by_id[domain_id]["status"]) in {"fail", "missing"}
    ]
    for domain_id in critical_blocks:
        engineering_readiness = min(engineering_readiness, critical_caps[domain_id])
    formal_readiness = min(engineering_readiness, formal_score)
    if not (formal and formal.allowed_for_official_issue):
        formal_readiness = min(formal_readiness, 49.0)
    return {
        "schema": "pitguard-result-completeness-v2",
        "status": _status_worst([str(item["status"]) for item in domains]),
        "engineeringCompletenessPercent": round(engineering_score, 1),
        "formalIssueCompletenessPercent": round(formal_score, 1),
        "engineeringReadinessPercent": round(engineering_readiness, 1),
        "formalIssueReadinessPercent": round(formal_readiness, 1),
        "criticalBlockingDomains": critical_blocks,
        "readinessPolicy": {
            "method": "weighted domain readiness with critical-domain caps",
            "weights": weights,
            "criticalCaps": critical_caps,
            "formalGateCapWhenBlocked": 49.0,
        },
        "domainCount": len(domains),
        "passCount": sum(item["status"] in {"pass", "not_applicable"} for item in domains),
        "warningCount": sum(item["status"] in {"warning", "manual_review"} for item in domains),
        "failCount": sum(item["status"] in {"fail", "missing"} for item in domains),
        "domains": domains,
    }


def enrich_calculation_result(
    project: Project,
    case: CalculationCase,
    result: CalculationResult,
    *,
    execution: dict[str, Any] | None = None,
) -> CalculationResult:
    stage_rows = _stage_rows(case, result.stage_results)
    numerical = _numerical_health(project, result.stage_results)
    support_envelopes = _support_envelopes(project, result.stage_results)
    wall_envelopes = _wall_envelopes(project, result.stage_results)
    wale_envelopes = _wale_envelopes(result.stage_results)
    column_foundation_envelopes = _column_foundation_envelopes(project)
    node_hotspots = _node_hotspots(project)
    stability_modes = _stability_modes(result)
    reinforcement_inventory = _reinforcement_inventory(project)
    transfer = dict((project.advanced_engineering or {}).get("concaveTransferFrameAnalysis") or {})
    transfer_beam_envelopes = [
        {"beamCode": code, **dict(values or {})}
        for code, values in (transfer.get("beamEnvelope") or {}).items()
    ]
    catalog = {
        "schema": "pitguard-result-catalog-v3",
        "stageMatrix": stage_rows,
        "criticalStages": _critical_stages(stage_rows),
        "wallEnvelopes": wall_envelopes,
        "supportEnvelopes": support_envelopes,
        "waleEnvelopes": wale_envelopes,
        "transferBeamEnvelopes": transfer_beam_envelopes,
        "columnFoundationEnvelopes": column_foundation_envelopes,
        "nodeHotspots": node_hotspots,
        "stabilityModes": stability_modes,
        "blockingCheckLedger": _check_ledger(result.checks, {"fail"}),
        "warningCheckLedger": _check_ledger(result.checks, {"warning"}),
        "manualReviewLedger": _check_ledger(result.checks, {"manual_review"}),
        "ruleStatusCounts": _rule_status_counts(result.checks),
        "reinforcementInventory": reinforcement_inventory,
        "analysisAssurance": dict(result.analysis_assurance or {}),
        "geotechnicalAssurance": dict(result.geotechnical_assurance or {}),
        "spatialVerification": dict(result.spatial_verification or {}),
        "verificationMatrix": dict(result.verification_matrix or {}),
        "statutoryWorkflowAssurance": dict(result.statutory_workflow_assurance or {}),
        "uncertaintyCases": list((result.geotechnical_assurance or {}).get("uncertaintyCases") or []),
        "counts": {
            "stageRows": len(stage_rows),
            "criticalStages": min(10, len(stage_rows)),
            "wallEnvelopes": len(wall_envelopes),
            "supportEnvelopes": len(support_envelopes),
            "waleEnvelopes": len(wale_envelopes),
            "transferBeamEnvelopes": len(transfer_beam_envelopes),
            "columnFoundationEnvelopes": len(column_foundation_envelopes),
            "nodeHotspots": len(node_hotspots),
            "stabilityModes": len(stability_modes),
            "blockingChecks": len(_check_ledger(result.checks, {"fail"})),
            "warningChecks": len(_check_ledger(result.checks, {"warning"})),
            "manualReviewChecks": len(_check_ledger(result.checks, {"manual_review"})),
            "uncertaintyCases": len((result.geotechnical_assurance or {}).get("uncertaintyCases") or []),
            "analysisDomains": len((result.analysis_assurance or {}).get("domains") or []),
            "statutoryRequirements": len((result.statutory_workflow_assurance or {}).get("requirements") or []),
        },
    }
    result.calculation_execution = dict(execution or {})
    result.numerical_health = numerical
    result.result_catalog = catalog
    result.result_completeness = _completeness(project, case, result, numerical)
    result.stage_result_summary = {
        **dict(result.stage_result_summary or {}),
        "stageCount": len(case.stages),
        "stageSegmentResultCount": len(result.stage_results),
        "criticalStageCount": len(catalog["criticalStages"]),
        "wallEnvelopeCount": len(wall_envelopes),
        "supportEnvelopeCount": len(support_envelopes),
        "waleEnvelopeCount": len(wale_envelopes),
        "nodeHotspotCount": len(node_hotspots),
        "columnFoundationEnvelopeCount": len(column_foundation_envelopes),
        "resultDomainCount": result.result_completeness.get("domainCount"),
        "engineeringCompletenessPercent": result.result_completeness.get("engineeringCompletenessPercent"),
        "formalIssueCompletenessPercent": result.result_completeness.get("formalIssueCompletenessPercent"),
        "engineeringReadinessPercent": result.result_completeness.get("engineeringReadinessPercent"),
        "formalIssueReadinessPercent": result.result_completeness.get("formalIssueReadinessPercent"),
    }
    result.report_diagram_data = {
        **dict(result.report_diagram_data or {}),
        "calculationExecution": result.calculation_execution,
        "numericalHealth": numerical,
        "resultCompleteness": result.result_completeness,
        "resultCatalog": catalog,
        "stageMatrix": stage_rows,
        "criticalStages": catalog["criticalStages"],
    }
    return result
