from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable

from app.schemas.domain import CalculationResult, GeologicalLayer, Project

DEFAULT_SCREENING_SPRING_KN_M2 = 12_000.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def representative_horizontal_spring(
    soil_profile: Iterable[GeologicalLayer] | None,
    *,
    excavation_depth_m: float,
    allow_default: bool = True,
) -> dict[str, Any]:
    """Return a traceable representative horizontal subgrade modulus.

    Priority is explicit horizontal subgrade modulus, followed by a transparent
    elastic-modulus-derived screening value. A fixed default is retained only
    for preliminary calculation and is explicitly marked as non-formal.
    """
    explicit: list[float] = []
    derived: list[float] = []
    layer_rows: list[dict[str, Any]] = []
    depth = max(float(excavation_depth_m), 1.0)
    for layer in soil_profile or []:
        parameters = getattr(layer, "parameters", None)
        if parameters is None:
            continue
        m = _finite(getattr(parameters, "horizontal_subgrade_modulus", None))
        e = _finite(getattr(parameters, "elastic_modulus", None))
        nu = min(max(_finite(getattr(parameters, "poisson_ratio", None), 0.30), 0.05), 0.49)
        if m > 0.0:
            explicit.append(m)
            source = "explicit_horizontal_subgrade_modulus"
            value = m
        elif e > 0.0:
            # E is stored in MPa-like engineering values in imported strata.
            # Convert to kN/m2 and reduce to an equivalent lateral spring over
            # a characteristic excavation depth. This is a design screening
            # relationship and remains marked as empirical.
            value = max(1_000.0, min(120_000.0, e * 1000.0 / max((1.0 + nu) * depth, 1.0)))
            derived.append(value)
            source = "derived_empirical"
        else:
            value = 0.0
            source = "missing"
        layer_rows.append({
            "stratumCode": getattr(layer, "stratum_code", None),
            "topElevationM": getattr(layer, "top_elevation", None),
            "bottomElevationM": getattr(layer, "bottom_elevation", None),
            "valueKnM2": round(value, 3) if value else None,
            "source": source,
        })
    if explicit:
        value = median(explicit)
        source = "explicit_horizontal_subgrade_modulus"
        formal = True
    elif derived:
        value = median(derived)
        source = "derived_empirical"
        formal = False
    elif allow_default:
        value = DEFAULT_SCREENING_SPRING_KN_M2
        source = "default_screening"
        formal = False
    else:
        value = 0.0
        source = "missing"
        formal = False
    return {
        "valueKnM2": round(float(value), 3),
        "source": source,
        "formalUseAllowed": formal,
        "layerValues": layer_rows,
        "explicitCount": len(explicit),
        "derivedCount": len(derived),
    }


def _layer_at_elevation(project: Project, elevation: float):
    for stratum in project.strata:
        # Project-level strata do not carry elevations. Prefer representative
        # geological layers from excavation segments when available.
        pass
    if project.excavation:
        for segment in project.excavation.segments:
            section = segment.representative_section
            if not section:
                continue
            for layer in section.layers:
                if float(layer.bottom_elevation) - 1e-9 <= elevation <= float(layer.top_elevation) + 1e-9:
                    return layer
    return None


def _mobilization(displacement_m: float, layer: Any) -> dict[str, float]:
    parameters = getattr(layer, "parameters", None)
    phi = _finite(getattr(parameters, "friction_angle", None), 25.0)
    c = _finite(getattr(parameters, "cohesion", None), 0.0)
    e = _finite(getattr(parameters, "elastic_modulus", None), 10.0)
    # Characteristic displacement for lateral resistance mobilization. The
    # bounded relation is used as a nonlinear spring diagnostic, not as a code
    # limit. Softer/cohesive soils mobilize at larger displacement.
    u50 = min(max(0.0025 + 0.00012 * max(30.0 - phi, 0.0) + 0.00003 * c + 0.00002 * max(20.0 - e, 0.0), 0.002), 0.02)
    ratio = abs(displacement_m) / max(u50, 1e-9)
    mobilized = ratio / (1.0 + ratio)
    tangent_ratio = 1.0 / (1.0 + ratio) ** 2
    return {"u50M": u50, "mobilizedRatio": mobilized, "tangentStiffnessRatio": tangent_ratio}


def build_nonlinear_geotechnical_assurance(project: Project, result: CalculationResult) -> dict[str, Any]:
    stage_rows: list[dict[str, Any]] = []
    fallback_sources: set[str] = set()
    max_mobilized = 0.0
    min_tangent = 1.0
    for stage in result.stage_results or []:
        force = stage.wall_internal_force
        if force is None or not force.points:
            continue
        points: list[dict[str, Any]] = []
        for point in force.points:
            elevation = _finite(getattr(point, "elevation", None))
            displacement_m = abs(_finite(getattr(point, "displacement", None))) / 1000.0
            layer = _layer_at_elevation(project, elevation)
            mobility = _mobilization(displacement_m, layer)
            max_mobilized = max(max_mobilized, mobility["mobilizedRatio"])
            min_tangent = min(min_tangent, mobility["tangentStiffnessRatio"])
            points.append({
                "elevationM": round(elevation, 3),
                "displacementMm": round(displacement_m * 1000.0, 4),
                "mobilizedResistanceRatio": round(mobility["mobilizedRatio"], 4),
                "tangentStiffnessRatio": round(mobility["tangentStiffnessRatio"], 4),
                "u50Mm": round(mobility["u50M"] * 1000.0, 3),
                "stratumCode": getattr(layer, "stratum_code", None),
            })
        coupled = stage.global_coupled_result
        coupled_data = coupled.model_dump(mode="json", by_alias=True) if coupled else {}
        source = str(coupled_data.get("soilSpringSource") or "unknown")
        if source in {"default_screening", "derived_empirical", "unknown"}:
            fallback_sources.add(source)
        stage_rows.append({
            "stageId": stage.stage_id,
            "segmentId": stage.segment_id,
            "soilSpringSource": source,
            "soilSpringKnM2": coupled_data.get("soilSpringKnM2"),
            "maximumMobilizedResistanceRatio": max((row["mobilizedResistanceRatio"] for row in points), default=0.0),
            "minimumTangentStiffnessRatio": min((row["tangentStiffnessRatio"] for row in points), default=1.0),
            "points": points[::max(1, len(points) // 20)] if len(points) > 24 else points,
        })

    gv = result.governing_values
    base_disp = abs(_finite(gv.max_displacement))
    base_force = abs(_finite(gv.max_support_axial_force))
    groundwater = float(project.design_settings.groundwater_level)
    uncertainty_cases = [
        {"case": "soil_stiffness_low", "soilStiffnessFactor": 0.70, "waterRiseM": 0.0, "displacementFactor": 1.30, "supportForceFactor": 1.10},
        {"case": "soil_stiffness_high", "soilStiffnessFactor": 1.30, "waterRiseM": 0.0, "displacementFactor": 0.82, "supportForceFactor": 0.94},
        {"case": "water_level_rise_0p5m", "soilStiffnessFactor": 1.0, "waterRiseM": 0.5, "displacementFactor": 1.08, "supportForceFactor": 1.06},
        {"case": "water_level_rise_1p0m", "soilStiffnessFactor": 1.0, "waterRiseM": 1.0, "displacementFactor": 1.16, "supportForceFactor": 1.12},
    ]
    for row in uncertainty_cases:
        row["projectedMaxDisplacementMm"] = round(base_disp * row["displacementFactor"], 3)
        row["projectedMaxSupportForceKn"] = round(base_force * row["supportForceFactor"], 3)
        row["groundwaterElevationM"] = round(groundwater + row["waterRiseM"], 3)
        row["method"] = "bounded local sensitivity projection; requires rerun for formal use"

    missing_parameters = []
    if not project.strata:
        missing_parameters.append("project.strata")
    if not project.boreholes:
        missing_parameters.append("project.boreholes")
    for stratum in project.strata:
        for name in ("unit_weight", "cohesion", "friction_angle", "elastic_modulus"):
            if getattr(stratum.parameters, name, None) is None:
                missing_parameters.append(f"{stratum.code}.{name}")
    if missing_parameters or "default_screening" in fallback_sources:
        status = "fail" if project.design_settings.formal_issue_strict_mode else "warning"
    elif fallback_sources:
        status = "warning"
    else:
        status = "pass"
    return {
        "schema": "pitguard-nonlinear-geotechnical-assurance-v1",
        "analysisMode": "displacement-mobilized nonlinear spring diagnostic with groundwater sensitivity",
        "requestedLevel": project.design_settings.geotechnical_analysis_level,
        "status": status,
        "stageSegmentCount": len(stage_rows),
        "maximumMobilizedResistanceRatio": round(max_mobilized, 4),
        "minimumTangentStiffnessRatio": round(min_tangent, 4),
        "fallbackSources": sorted(fallback_sources),
        "missingCriticalParameters": missing_parameters[:100],
        "uncertaintyCases": uncertainty_cases,
        "stageSegments": stage_rows,
        "formalUseAllowed": status == "pass" and bool(project.strata) and bool(project.boreholes) and project.design_settings.geotechnical_analysis_level != "screening",
        "boundary": "非线性弹簧诊断用于识别刚度退化和参数敏感性；土体塑性、固结、接触与二维/三维渗流仍需高级有限元或专项分析。",
    }
