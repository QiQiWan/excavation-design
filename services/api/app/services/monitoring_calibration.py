from __future__ import annotations

import math
from statistics import median
from typing import Any

from app.schemas.domain import CalibrationRun, MonitoringRecord, Project


def _normalized_measured_value(record: MonitoringRecord) -> float:
    unit = record.unit.strip().lower().replace(" ", "")
    value = float(record.measured_value)
    if record.record_type in {"wall_displacement", "settlement"}:
        if unit in {"m", "meter", "metre"}:
            return value * 1000.0
        if unit in {"cm"}:
            return value * 10.0
        return value
    if record.record_type == "support_axial_force":
        if unit in {"n"}:
            return value / 1000.0
        if unit in {"mn"}:
            return value * 1000.0
        return value
    if record.record_type == "groundwater":
        if unit in {"mm"}:
            return value / 1000.0
        if unit in {"cm"}:
            return value / 100.0
        return value
    return value


def _matching_wall_segment_ids(project: Project, record: MonitoringRecord) -> set[str]:
    if record.object_id:
        return {record.object_id}
    if not record.object_code or not project.retaining_system:
        return set()
    code = record.object_code.strip().casefold()
    result: set[str] = set()
    for wall in project.retaining_system.diaphragm_walls:
        aliases = {wall.id, wall.panel_code, wall.design_face_code or ""}
        if any(str(alias).strip().casefold() == code for alias in aliases):
            result.add(wall.id)
            if wall.design_face_code:
                result.add(wall.design_face_code)
    return result


def _predicted_wall_displacement(project: Project, record: MonitoringRecord) -> float | None:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if not latest:
        return None
    segment_ids = _matching_wall_segment_ids(project, record)
    candidates: list[tuple[float, float]] = []
    for stage in latest.stage_results:
        if record.stage_id and stage.stage_id != record.stage_id:
            continue
        force = stage.wall_internal_force
        if not force:
            continue
        if segment_ids and stage.segment_id not in segment_ids:
            continue
        for point in force.points:
            value = abs(float(point.displacement or 0.0))
            delta = abs(float(point.elevation) - record.elevation) if record.elevation is not None else 0.0
            candidates.append((delta, value))
    if candidates:
        if record.elevation is None:
            return max(value for _delta, value in candidates)
        minimum_delta = min(delta for delta, _value in candidates)
        # Multiple construction stages can have a sample at the same elevation.
        # Use the governing displacement among the closest elevation samples,
        # rather than whichever stage happened to be encountered first.
        nearest = [value for delta, value in candidates if delta <= minimum_delta + 0.15]
        return max(nearest) if nearest else max(candidates, key=lambda item: (-item[0], item[1]))[1]
    return float(latest.governing_values.max_displacement or 0.0)


def _predicted_support_force(project: Project, record: MonitoringRecord) -> float | None:
    ret = project.retaining_system
    if not ret:
        return None
    for support in ret.supports:
        if record.object_id == support.id or record.object_code == support.code:
            # Field monitoring represents the effective in-service axial force,
            # so compare against the standard envelope before ULS amplification.
            return abs(float(support.effective_axial_force_standard or support.raw_axial_force_standard_envelope or support.design_axial_force or 0.0))
    return None


def _ratios(project: Project) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
    out = {"wall": [], "support": [], "groundwater": [], "settlement": []}
    rejected: list[dict[str, Any]] = []
    for record in project.monitoring_records:
        if record.quality == "rejected":
            continue
        measured = _normalized_measured_value(record)
        ratio: float | None = None
        if record.record_type == "wall_displacement":
            predicted = _predicted_wall_displacement(project, record)
            if predicted and predicted > 1e-6:
                ratio = abs(measured) / predicted
                target = "wall"
            else:
                target = "wall"
        elif record.record_type == "support_axial_force":
            predicted = _predicted_support_force(project, record)
            if predicted and predicted > 1e-6:
                ratio = abs(measured) / predicted
                target = "support"
            else:
                target = "support"
        elif record.record_type == "groundwater":
            out["groundwater"].append(measured - project.design_settings.groundwater_level)
            continue
        elif record.record_type == "settlement":
            latest = project.calculation_results[-1] if project.calculation_results else None
            predicted = 0.35 * abs(float(latest.governing_values.max_displacement or 0.0)) if latest else None
            if predicted and predicted > 1e-6:
                ratio = abs(measured) / predicted
                target = "settlement"
            else:
                target = "settlement"
        else:
            continue
        if ratio is None:
            rejected.append({"recordId": record.id, "reason": "no matching calculation prediction"})
        elif ratio < 0.1 or ratio > 10.0:
            rejected.append({"recordId": record.id, "reason": "monitoring/calculation ratio outside 0.1-10.0", "ratio": ratio})
        else:
            out[target].append(ratio)
    return out, rejected

def calibrate_from_monitoring(project: Project, apply: bool = False) -> CalibrationRun:
    ratios, rejected_samples = _ratios(project)
    wall_ratio = median(ratios["wall"]) if ratios["wall"] else 1.0
    support_ratio = median(ratios["support"]) if ratios["support"] else 1.0
    groundwater_offset = median(ratios["groundwater"]) if ratios["groundwater"] else 0.0
    settlement_ratio = median(ratios["settlement"]) if ratios["settlement"] else wall_ratio
    combined_ground_ratio = median([wall_ratio, settlement_ratio])
    soil_factor = min(max(1.0 / max(combined_ground_ratio, 0.25), 0.45), 1.8)
    support_factor = min(max(1.0 / max(support_ratio, 0.25), 0.55), 1.6)
    wall_factor = min(max(1.0 / math.sqrt(max(wall_ratio, 0.25)), 0.65), 1.4)
    sample_count = sum(len(v) for v in ratios.values())
    confidence = "high" if sample_count >= 12 and ratios["wall"] and ratios["support"] else "medium" if sample_count >= 5 else "low"
    deviations = [abs(v - 1.0) for group in (ratios["wall"], ratios["support"], ratios["settlement"]) for v in group]
    before = sum(d*d for d in deviations) / max(len(deviations), 1)
    after_terms = []
    for value in ratios["wall"]:
        after_terms.append(abs(value * soil_factor * wall_factor - 1.0))
    for value in ratios["support"]:
        after_terms.append(abs(value * support_factor - 1.0))
    for value in ratios["settlement"]:
        after_terms.append(abs(value * soil_factor - 1.0))
    after = sum(d*d for d in after_terms) / max(len(after_terms), 1)
    status = "pass" if sample_count >= 5 and after <= before else "warning" if sample_count else "manual_review"
    run = CalibrationRun(
        status=status, sample_count=sample_count, wall_stiffness_factor=round(wall_factor, 3), support_stiffness_factor=round(support_factor, 3),
        soil_modulus_factor=round(soil_factor, 3), groundwater_offset_m=round(groundwater_offset, 3), objective_before=round(before, 5), objective_after=round(after, 5),
        confidence=confidence, applied=apply,
        diagnostics={
            "ratios": ratios, "rejectedSamples": rejected_samples,
            "unitBasis": {"wall_displacement": "mm", "settlement": "mm", "support_axial_force": "kN-standard-envelope", "groundwater": "m-elevation"},
            "message": "采用监测/计算比的稳健中位数反演土体、墙体和支撑有效刚度修正系数；异常量纲或无法匹配的样本不参与反演。",
        },
    )
    if apply:
        previous = dict(project.advanced_engineering.get("calibrationFactors") or {})
        project.advanced_engineering["calibrationFactors"] = {
            "wallStiffnessFactor": run.wall_stiffness_factor, "supportStiffnessFactor": run.support_stiffness_factor,
            "soilModulusFactor": run.soil_modulus_factor, "groundwaterOffsetM": run.groundwater_offset_m,
            "calibrationRunId": run.id,
            "previousFactors": previous,
            "appliedAt": run.created_at,
            "method": "robust median monitoring inversion",
        }
        project.calculation_results = []
        project.messages.append("监测反演系数已应用，原计算结果已失效，请重新计算。")
    project.calibration_runs.append(run)
    return run


def monitoring_summary(project: Project) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in project.monitoring_records:
        counts[item.record_type] = counts.get(item.record_type, 0) + 1
    latest = project.calibration_runs[-1].model_dump(mode="json", by_alias=True) if project.calibration_runs else None
    return {"recordCount": len(project.monitoring_records), "counts": counts, "latestCalibration": latest, "requiresRecalculation": bool(latest and latest.get("applied") and not project.calculation_results)}
