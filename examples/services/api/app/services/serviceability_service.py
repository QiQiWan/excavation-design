from __future__ import annotations

from typing import Any

from app.rules.gb50010.detailing_rules import crack_width_limit_mm, estimate_crack_width_mm
from app.schemas.domain import Project
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme


def _status(width: float, limit: float) -> str:
    if width > 1.25 * limit:
        return "fail"
    if width > limit:
        return "warning"
    return "pass"


def evaluate_long_term_serviceability(project: Project, mode: str = "balanced") -> dict[str, Any]:
    settings = project.design_settings
    scheme = build_rebar_design_scheme(project, mode=mode)
    creep = max(float(settings.creep_coefficient), 0.0)
    shrinkage = max(float(settings.shrinkage_strain), 0.0)
    sustained = min(max(float(settings.sustained_load_ratio), 0.1), 1.0)
    temperature = max(float(settings.temperature_range_c), 0.0)
    humidity = min(max(float(settings.relative_humidity), 0.2), 1.0)
    limit = crack_width_limit_mm(settings.environment_grade)
    long_term_factor = 1.0 + 0.22 * creep + 0.10 * (shrinkage / 0.00025) + 0.04 * (temperature / 20.0) + 0.05 * max(0.0, (0.75 - humidity) / 0.25)
    displacement_factor = 1.0 + 0.35 * creep * sustained
    rows: list[dict[str, Any]] = []
    counts = {"pass": 0, "warning": 0, "fail": 0, "manual_review": 0}
    for zone in scheme.get("wallZones", []):
        for face in zone.get("faces", []):
            demand = abs(float(face.get("momentDesignKnMPerM") or 0.0))
            utilization = max(float(face.get("utilization") or 0.0), 0.05)
            capacity = demand / utilization if demand > 0 else 1.0
            quasi_moment = demand * sustained
            short_width = estimate_crack_width_mm(
                quasi_moment,
                max(capacity, 1.0),
                float(face.get("barSpacingMm") or 200.0),
                float(face.get("barDiameterMm") or 20.0),
                "HRB400",
            )
            width = round(short_width * long_term_factor, 3)
            status = _status(width, limit)
            counts[status] += 1
            rows.append({
                "objectType": "wall_zone",
                "objectId": zone.get("zoneId"),
                "hostCode": zone.get("hostCode"),
                "face": face.get("face"),
                "topElevation": zone.get("topElevation"),
                "bottomElevation": zone.get("bottomElevation"),
                "quasiPermanentMomentKnMPerM": round(quasi_moment, 3),
                "estimatedCrackWidthMm": width,
                "limitMm": limit,
                "utilization": round(width / max(limit, 1e-9), 3),
                "longTermFactor": round(long_term_factor, 3),
                "status": status,
                "drawingRefs": zone.get("drawingRefs", []),
                "recommendedAction": "减小钢筋间距、提高配筋率或增加局部抗裂钢筋" if status != "pass" else "保持当前构造并结合监测复核",
            })
    latest = project.calculation_results[-1] if project.calculation_results else None
    max_short_displacement = float(latest.governing_values.max_displacement or 0.0) if latest else 0.0
    long_displacement = max_short_displacement * displacement_factor
    displacement_limit = (project.excavation.depth / max(settings.displacement_limit_ratio or 500.0, 1.0) * 1000.0) if project.excavation else None
    displacement_status = "manual_review"
    if displacement_limit is not None:
        displacement_status = "pass" if long_displacement <= displacement_limit else "warning" if long_displacement <= 1.2 * displacement_limit else "fail"
        counts[displacement_status] += 1
    overall = "fail" if counts["fail"] else "warning" if counts["warning"] or counts["manual_review"] else "pass"
    return {
        "status": overall,
        "summary": {
            "checkCount": len(rows) + (1 if displacement_limit is not None else 0),
            "counts": counts,
            "maxEstimatedCrackWidthMm": max((r["estimatedCrackWidthMm"] for r in rows), default=0.0),
            "crackWidthLimitMm": limit,
            "shortTermDisplacementMm": round(max_short_displacement, 3),
            "longTermDisplacementMm": round(long_displacement, 3),
            "displacementLimitMm": round(displacement_limit, 3) if displacement_limit is not None else None,
            "displacementStatus": displacement_status,
            "creepCoefficient": creep,
            "shrinkageStrain": shrinkage,
            "sustainedLoadRatio": sustained,
            "temperatureRangeC": temperature,
            "serviceLifeYears": settings.service_life_years,
        },
        "wallZoneChecks": rows,
        "method": "transparent long-term serviceability screening using quasi-permanent demand, creep/shrinkage/temperature amplification and existing crack-width screen",
        "boundary": "正式设计仍需按项目荷载组合、龄期、湿度、收缩徐变模型、施工缝和地下环境等级完成规范复核。",
    }
