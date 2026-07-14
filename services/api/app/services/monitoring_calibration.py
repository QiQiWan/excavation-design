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


def _parse_timestamp(value: str) -> datetime | None:
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _monitoring_thresholds(project: Project, record_type: str, object_id: str | None, object_code: str | None) -> dict[str, Any]:
    depth_m = abs(float(project.excavation.bottom_elevation - project.excavation.top_elevation)) if project.excavation else 15.0
    settings = project.design_settings
    project_defined = settings.monitoring_threshold_source == "project_defined"
    if record_type == "wall_displacement":
        auto_alarm = max(15.0, min(60.0, depth_m * 1000.0 / 500.0))
        alarm = float(settings.monitoring_wall_displacement_alarm_mm) if project_defined and settings.monitoring_wall_displacement_alarm_mm is not None else auto_alarm
        warning = float(settings.monitoring_wall_displacement_warning_mm) if project_defined and settings.monitoring_wall_displacement_warning_mm is not None else alarm * 0.70
        return {"unit": "mm", "watch": round(min(warning * 0.75, alarm * 0.50), 3), "warning": round(warning, 3), "alarm": round(alarm, 3), "basis": "project monitoring plan" if project_defined else "project screening threshold H/500 with 15-60 mm bounds", "source": settings.monitoring_threshold_source}
    if record_type == "settlement":
        auto_alarm = max(15.0, min(50.0, depth_m * 1000.0 / 750.0))
        alarm = float(settings.monitoring_settlement_alarm_mm) if project_defined and settings.monitoring_settlement_alarm_mm is not None else auto_alarm
        warning = float(settings.monitoring_settlement_warning_mm) if project_defined and settings.monitoring_settlement_warning_mm is not None else alarm * 0.70
        return {"unit": "mm", "watch": round(min(warning * 0.75, alarm * 0.50), 3), "warning": round(warning, 3), "alarm": round(alarm, 3), "basis": "project monitoring plan" if project_defined else "project screening threshold H/750 with 15-50 mm bounds", "source": settings.monitoring_threshold_source}
    if record_type == "support_axial_force":
        reference = None
        if project.retaining_system:
            for support in project.retaining_system.supports:
                if (object_id and object_id == support.id) or (object_code and object_code == support.code):
                    reference = abs(float(support.effective_axial_force_standard or support.raw_axial_force_standard_envelope or support.design_axial_force or 0.0))
                    break
        if reference and reference > 1e-6:
            warning_ratio = max(0.0, float(settings.monitoring_support_force_warning_ratio))
            alarm_ratio = max(warning_ratio, float(settings.monitoring_support_force_alarm_ratio))
            return {"unit": "kN", "watch": round(reference * min(0.70, warning_ratio * 0.80), 3), "warning": round(reference * warning_ratio, 3), "alarm": round(reference * alarm_ratio, 3), "reference": round(reference, 3), "basis": "project ratios to current calculated standard axial-force envelope", "source": settings.monitoring_threshold_source}
        return {"unit": "kN", "watch": None, "warning": None, "alarm": None, "basis": "missing matched support calculation envelope", "source": settings.monitoring_threshold_source}
    if record_type == "groundwater":
        design = float(settings.groundwater_level)
        warning_offset = max(0.0, float(settings.monitoring_groundwater_warning_offset_m))
        alarm_offset = max(warning_offset, float(settings.monitoring_groundwater_alarm_offset_m))
        return {"unit": "m", "watch": round(design + warning_offset * 0.60, 3), "warning": round(design + warning_offset, 3), "alarm": round(design + alarm_offset, 3), "reference": design, "basis": "project offsets above design groundwater elevation", "source": settings.monitoring_threshold_source}
    return {"unit": "", "watch": None, "warning": None, "alarm": None, "basis": "unsupported monitoring type", "source": settings.monitoring_threshold_source}

def _monitoring_level(value: float, thresholds: dict[str, Any]) -> str:
    alarm = thresholds.get("alarm")
    warning = thresholds.get("warning")
    watch = thresholds.get("watch")
    if alarm is None:
        return "manual_review"
    if value >= float(alarm):
        return "alarm"
    if warning is not None and value >= float(warning):
        return "warning"
    if watch is not None and value >= float(watch):
        return "watch"
    return "normal"


def monitoring_control_summary(project: Project) -> dict[str, Any]:
    """Build a deterministic monitoring-control and digital-twin snapshot.

    Thresholds are project-level screening values. They are intentionally
    identified as non-statutory and must be replaced or approved by the project
    monitoring plan before construction issue.
    """
    from collections import defaultdict
    from datetime import datetime, timezone

    quality_counts = {"verified": 0, "provisional": 0, "rejected": 0}
    grouped: dict[tuple[str, str], list[tuple[datetime, MonitoringRecord, float]]] = defaultdict(list)
    invalid_timestamp_count = 0
    for record in project.monitoring_records:
        quality_counts[record.quality] = quality_counts.get(record.quality, 0) + 1
        if record.quality == "rejected":
            continue
        timestamp = _parse_timestamp(record.timestamp)
        if timestamp is None:
            invalid_timestamp_count += 1
            continue
        key = (record.record_type, str(record.object_id or record.object_code or "project"))
        grouped[key].append((timestamp, record, _normalized_measured_value(record)))

    series: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    severity_rank = {"normal": 0, "watch": 1, "warning": 2, "alarm": 3, "manual_review": 1}
    for (record_type, object_key), items in grouped.items():
        items.sort(key=lambda row: row[0])
        latest_time, latest_record, latest_value = items[-1]
        previous_value = items[-2][2] if len(items) > 1 else None
        rate_per_day = 0.0
        if len(items) > 1:
            previous_time = items[-2][0]
            elapsed_days = max((latest_time - previous_time).total_seconds() / 86400.0, 1.0 / 1440.0)
            rate_per_day = (latest_value - float(previous_value)) / elapsed_days
        projection_hours = max(1.0, min(168.0, float(project.design_settings.monitoring_projection_hours)))
        projected_24h = latest_value + rate_per_day * projection_hours / 24.0
        thresholds = _monitoring_thresholds(project, record_type, latest_record.object_id, latest_record.object_code)
        current_value_for_level = abs(latest_value) if record_type != "groundwater" else latest_value
        projected_value_for_level = abs(projected_24h) if record_type != "groundwater" else projected_24h
        current_level = _monitoring_level(current_value_for_level, thresholds)
        projected_level = _monitoring_level(projected_value_for_level, thresholds)
        governing_level = projected_level if severity_rank.get(projected_level, 0) > severity_rank.get(current_level, 0) else current_level
        predicted = None
        ratio = None
        if record_type == "wall_displacement":
            predicted = _predicted_wall_displacement(project, latest_record)
        elif record_type == "support_axial_force":
            predicted = _predicted_support_force(project, latest_record)
        elif record_type == "settlement":
            latest_calc = project.calculation_results[-1] if project.calculation_results else None
            predicted = 0.35 * abs(float(latest_calc.governing_values.max_displacement or 0.0)) if latest_calc else None
        elif record_type == "groundwater":
            predicted = float(project.design_settings.groundwater_level)
        if predicted is not None and abs(float(predicted)) > 1e-9:
            ratio = abs(latest_value) / abs(float(predicted)) if record_type != "groundwater" else latest_value - float(predicted)

        row = {
            "recordType": record_type,
            "objectKey": object_key,
            "objectId": latest_record.object_id,
            "objectCode": latest_record.object_code,
            "stageId": latest_record.stage_id,
            "sampleCount": len(items),
            "latestTimestamp": latest_time.isoformat(),
            "latestValue": round(latest_value, 6),
            "previousValue": round(float(previous_value), 6) if previous_value is not None else None,
            "ratePerDay": round(rate_per_day, 6),
            "projectionHours": projection_hours,
            "projected24h": round(projected_24h, 6),
            "unit": thresholds.get("unit"),
            "thresholds": thresholds,
            "currentLevel": current_level,
            "projectedLevel": projected_level,
            "governingLevel": governing_level,
            "calculatedReference": round(float(predicted), 6) if predicted is not None else None,
            "monitoringCalculationRatioOrOffset": round(float(ratio), 6) if ratio is not None else None,
            "quality": latest_record.quality,
        }
        series.append(row)
        if governing_level in {"watch", "warning", "alarm", "manual_review"}:
            alerts.append({
                "alertId": f"MON-{record_type.upper()}-{object_key}",
                "level": governing_level,
                "recordType": record_type,
                "objectKey": object_key,
                "latestValue": row["latestValue"],
                "projected24h": row["projected24h"],
                "unit": row["unit"],
                "thresholds": thresholds,
                "recommendedAction": {
                    "alarm": "立即复核监测点、暂停相关高风险工序并启动项目应急响应。",
                    "warning": "加密监测频率，复核施工工况、支撑轴力与地下水控制。",
                    "watch": "保持关注并核对趋势，必要时增加人工复测。",
                    "manual_review": "缺少可匹配的设计控制值，由专业工程师设定项目阈值。",
                }.get(governing_level, "复核监测数据。"),
            })

    series.sort(key=lambda row: (-severity_rank.get(str(row["governingLevel"]), 0), str(row["recordType"]), str(row["objectKey"])))
    alerts.sort(key=lambda row: (-severity_rank.get(str(row["level"]), 0), str(row["alertId"])))
    level_counts = {level: sum(row["governingLevel"] == level for row in series) for level in ("alarm", "warning", "watch", "manual_review", "normal")}
    highest_level = next((level for level in ("alarm", "warning", "watch", "manual_review", "normal") if level_counts.get(level, 0)), "normal")
    latest_calc = project.calculation_results[-1] if project.calculation_results else None
    latest_calibration = project.calibration_runs[-1] if project.calibration_runs else None
    return {
        "projectId": project.id,
        "recordCount": len(project.monitoring_records),
        "verifiedRecordCount": quality_counts.get("verified", 0),
        "provisionalRecordCount": quality_counts.get("provisional", 0),
        "rejectedRecordCount": quality_counts.get("rejected", 0),
        "invalidTimestampCount": invalid_timestamp_count,
        "alertsEvaluated": bool(series),
        "highestLevel": highest_level,
        "alertCount": len(alerts),
        "summary": {"seriesCount": len(series), "levelCounts": level_counts, "qualityCounts": quality_counts},
        "alerts": alerts,
        "series": series,
        "digitalTwin": {
            "snapshotAt": datetime.now(timezone.utc).isoformat(),
            "calculationResultId": latest_calc.id if latest_calc else None,
            "calculationCurrent": bool(latest_calc),
            "calibrationRunId": latest_calibration.id if latest_calibration else None,
            "calibrationApplied": bool(latest_calibration and latest_calibration.applied),
            "requiresRecalculation": bool(latest_calibration and latest_calibration.applied and not project.calculation_results),
            "state": highest_level,
            "observedObjectCount": len(series),
        },
        "thresholdPolicy": {
            "type": project.design_settings.monitoring_threshold_source,
            "statutory": False,
            "projectionHours": max(1.0, min(168.0, float(project.design_settings.monitoring_projection_hours))),
            "message": ("当前采用项目定义阈值；系统仍要求专业工程师确认其与监测方案、设计文件和应急预案一致。" if project.design_settings.monitoring_threshold_source == "project_defined" else "默认阈值仅用于系统筛查，应由项目监测方案、设计文件和专业工程师确认后替换或批准。"),
        },
    }
