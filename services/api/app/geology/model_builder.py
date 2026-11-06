from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable, Any

from app.geology.idw import interpolate_surface_idw
from app.schemas.domain import Borehole, GeologicalModel, GeologicalSurface, Project
from app.services.excavation_service import _unique_polygon_points

Bounds = tuple[float, float, float, float]


def _bounds_from_boreholes(boreholes: list[Borehole], padding: float = 10.0) -> Bounds:
    xs = [b.x for b in boreholes]
    ys = [b.y for b in boreholes]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if abs(max_x - min_x) < 1e-9:
        min_x -= padding
        max_x += padding
    else:
        span = max_x - min_x
        min_x -= max(padding, span * 0.1)
        max_x += max(padding, span * 0.1)
    if abs(max_y - min_y) < 1e-9:
        min_y -= padding
        max_y += padding
    else:
        span = max_y - min_y
        min_y -= max(padding, span * 0.1)
        max_y += max(padding, span * 0.1)
    return min_x, min_y, max_x, max_y


def _merge_bounds(*bounds_values: Bounds | None) -> Bounds:
    values = [b for b in bounds_values if b is not None]
    if not values:
        raise ValueError("No bounds to merge")
    return (
        min(b[0] for b in values),
        min(b[1] for b in values),
        max(b[2] for b in values),
        max(b[3] for b in values),
    )


def _bounds_from_excavation(project: Project, padding: float = 10.0) -> Bounds | None:
    if project.excavation is None:
        return None
    pts = _unique_polygon_points(project.excavation.outline)
    if not pts:
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding


def _bounds_from_retaining_system(project: Project, padding: float = 0.0) -> Bounds | None:
    """Return the XY envelope of walls, supports and columns.

    Imported or manually edited retaining systems may no longer coincide exactly
    with the excavation outline.  The geological design domain therefore follows
    the actual structural geometry rather than assuming the outline is sufficient.
    """
    system = project.retaining_system
    if system is None:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for support in system.supports or []:
        xs.extend([float(support.start.x), float(support.end.x)])
        ys.extend([float(support.start.y), float(support.end.y)])
        for point in (support.start_wall_connection, support.end_wall_connection):
            if point is not None:
                xs.append(float(point.x))
                ys.append(float(point.y))
    for column in system.columns or []:
        xs.append(float(column.location.x))
        ys.append(float(column.location.y))
    # Diaphragm-wall objects are associated with excavation segment ids.  The
    # segment geometry is the authoritative plan position when explicit wall
    # centreline coordinates are not stored on the member itself.
    if project.excavation:
        wall_segment_ids = {str(wall.segment_id) for wall in system.diaphragm_walls or []}
        for segment in project.excavation.segments or []:
            if not wall_segment_ids or str(segment.id) in wall_segment_ids or str(segment.name) in wall_segment_ids:
                xs.extend([float(segment.start.x), float(segment.end.x)])
                ys.extend([float(segment.start.y), float(segment.end.y)])
    if not xs or not ys:
        return None
    return min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding


def _max_bounds_extension(outer: Bounds, inner: Bounds) -> float:
    """Maximum plan distance by which ``outer`` extends beyond ``inner``."""
    return max(
        0.0,
        float(inner[0] - outer[0]),
        float(inner[1] - outer[1]),
        float(outer[2] - inner[2]),
        float(outer[3] - inner[3]),
    )


def required_geological_design_bounds(project: Project) -> Bounds | None:
    """Design-domain bounds covering excavation, retaining geometry and influence zone."""
    excavation = project.excavation
    if excavation is None:
        return None
    settings = project.design_settings
    depth = abs(float(excavation.top_elevation) - float(excavation.bottom_elevation))
    minimum = max(0.0, float(getattr(settings, "geology_minimum_plan_buffer_m", 10.0) or 10.0))
    ratio = max(0.0, float(getattr(settings, "geology_depth_buffer_ratio", 0.5) or 0.5))
    influence_buffer = max(minimum, depth * ratio)
    excavation_bounds = _bounds_from_excavation(project, padding=influence_buffer)
    structure_bounds = _bounds_from_retaining_system(project, padding=influence_buffer)
    if excavation_bounds is None and structure_bounds is None:
        return None
    return _merge_bounds(excavation_bounds, structure_bounds)


def _model_bounds(model: GeologicalModel | None) -> Bounds | None:
    if model is None:
        return None
    surfaces = list(model.surfaces or model.surface_previews or [])
    if not surfaces:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for surface in surfaces:
        xs.extend(surface.grid.x_values)
        ys.extend(surface.grid.y_values)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _contains_bounds(outer: Bounds, inner: Bounds, tol: float = 1e-6) -> bool:
    return outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol


def build_geological_model_from_boreholes(
    boreholes: list[Borehole],
    grid_size: float = 10.0,
    required_bounds: Bounds | None = None,
    *,
    max_extrapolation_distance_m: float | None = None,
) -> GeologicalModel:
    if not boreholes:
        raise ValueError("No boreholes available for geological model generation")

    bottom_points: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    top_points: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    warnings: list[str] = []

    for borehole in boreholes:
        seen: set[str] = set()
        for layer in borehole.layers:
            top_points[layer.stratum_code].append((borehole.x, borehole.y, layer.top_elevation))
            bottom_points[layer.stratum_code].append((borehole.x, borehole.y, layer.bottom_elevation))
            seen.add(layer.stratum_code)
        missing = {layer.stratum_code for other in boreholes for layer in other.layers} - seen
        for code in missing:
            warnings.append(f"钻孔 {borehole.code} 缺失地层 {code}，IDW 插值结果低置信度。")

    natural_bounds = _bounds_from_boreholes(boreholes)
    bounds = _merge_bounds(natural_bounds, required_bounds)
    extension_distance = _max_bounds_extension(bounds, natural_bounds)
    if required_bounds and bounds != natural_bounds:
        warnings.append(
            "地质模型范围已自动外扩至覆盖围护结构及施工影响区；外扩区域采用钻孔可信边界夹持的 IDW 外推，正式工程应结合补充勘察或人工确认。"
        )
    limit = float(max_extrapolation_distance_m) if max_extrapolation_distance_m is not None else None
    if limit is not None and extension_distance > limit + 1e-9:
        warnings.append(
            f"地质模型最大平面外推距离 {extension_distance:.2f}m 超过项目限值 {limit:.2f}m；该区域不得直接作为正式设计参数依据。"
        )
    surfaces: list[GeologicalSurface] = []
    for code in sorted(bottom_points.keys()):
        confidence = "high" if len(bottom_points[code]) == len(boreholes) and not required_bounds else "medium"
        surfaces.append(
            GeologicalSurface(
                stratum_code=code,
                surface_type="top",
                grid=interpolate_surface_idw(top_points[code], bounds, grid_size, trusted_bounds=natural_bounds),
                confidence="low" if extension_distance > 0 else confidence,
            )
        )
        surfaces.append(
            GeologicalSurface(
                stratum_code=code,
                surface_type="bottom",
                grid=interpolate_surface_idw(bottom_points[code], bounds, grid_size, trusted_bounds=natural_bounds),
                confidence="low" if extension_distance > 0 else confidence,
            )
        )

    volumes = [{"stratumCode": code, "representation": "between top/bottom IDW surfaces", "confidence": "IDW-with-optional-extension"} for code in sorted(bottom_points.keys())]
    extrapolation_status = (
        "manual_review" if limit is not None and extension_distance > limit + 1e-9
        else ("warning" if extension_distance > 0 else "pass")
    )
    coverage_audit = {
        "status": "warning" if extension_distance > 0 else "pass",
        "coverageStatus": "pass",
        "extrapolationStatus": extrapolation_status,
        "boreholeTrustBounds": {"minX": natural_bounds[0], "minY": natural_bounds[1], "maxX": natural_bounds[2], "maxY": natural_bounds[3]},
        "modelBounds": {"minX": bounds[0], "minY": bounds[1], "maxX": bounds[2], "maxY": bounds[3]},
        "requiredBounds": ({"minX": required_bounds[0], "minY": required_bounds[1], "maxX": required_bounds[2], "maxY": required_bounds[3]} if required_bounds else None),
        "autoExtended": bool(required_bounds and bounds != natural_bounds),
        "maximumExtrapolationDistanceM": round(extension_distance, 3),
        "maximumAllowedExtrapolationDistanceM": limit,
        "extrapolationMethod": "IDW inside trust bounds; nearest trusted-boundary clamping outside",
        "gridSizeM": float(grid_size),
        "boreholeCount": len(boreholes),
    }
    return GeologicalModel(surfaces=surfaces, volumes=volumes, warnings=warnings, coverage_audit=coverage_audit)


def ensure_geological_model_covers_excavation(project: Project, grid_size: float = 10.0, padding: float | None = None) -> bool:
    """Extend/rebuild the geological model when the excavation exceeds its XY range.

    Returns True when the model was generated or regenerated.  The function keeps
    any imported VTU mesh in place, because the IDW geological surfaces are the
    fallback used by design-section extraction and visualization.
    """
    if not bool(getattr(project.design_settings, "auto_extend_geology_to_design_domain", True)):
        return False
    if padding is not None:
        required = _bounds_from_excavation(project, padding=padding)
    else:
        required = required_geological_design_bounds(project)
    if required is None:
        return False
    current = _model_bounds(project.geological_model)
    if current is not None and _contains_bounds(current, required):
        if project.geological_model is not None:
            audit = dict(project.geological_model.coverage_audit or {})
            auto_extended = bool(audit.get("autoExtended"))
            audit.update({
                # Recompute from current geometry instead of preserving a stale
                # fail written by an earlier algorithm version.
                "status": "warning" if auto_extended else "pass",
                "coverageStatus": "pass",
                "extrapolationStatus": audit.get("extrapolationStatus") or ("warning" if auto_extended else "pass"),
                "requiredBounds": {"minX": required[0], "minY": required[1], "maxX": required[2], "maxY": required[3]},
                "designDomainCovered": True,
            })
            project.geological_model.coverage_audit = audit
        return False
    if not project.boreholes:
        if project.geological_model:
            project.geological_model.warnings = list(dict.fromkeys(project.geological_model.warnings + ["基坑范围超出地质模型，但缺少钻孔，无法自动外扩地质面。"] ))
        return False
    old_mesh = project.geological_model.vtu_mesh if project.geological_model else None
    project.geological_model = build_geological_model_from_boreholes(
        project.boreholes,
        grid_size=grid_size,
        required_bounds=required,
        max_extrapolation_distance_m=float(getattr(project.design_settings, "geology_max_extrapolation_distance_m", 60.0) or 60.0),
    )
    project.geological_model.vtu_mesh = old_mesh
    project.geological_model.coverage_audit["designDomainCovered"] = True
    return True


def geological_coverage_audit(project: Project) -> dict[str, Any]:
    required = required_geological_design_bounds(project)
    current = _model_bounds(project.geological_model)
    if required is None:
        return {"status": "manual_review", "message": "尚未定义基坑几何，无法确定地质设计域。"}
    if current is None:
        return {
            "status": "fail",
            "message": "尚未生成可用于设计剖面提取的地质面。",
            "requiredBounds": {"minX": required[0], "minY": required[1], "maxX": required[2], "maxY": required[3]},
            "designDomainCovered": False,
        }
    covered = _contains_bounds(current, required)
    audit = dict(project.geological_model.coverage_audit or {}) if project.geological_model else {}
    auto_extended = bool(audit.get("autoExtended"))
    extension = float(audit.get("maximumExtrapolationDistanceM") or 0.0)
    limit = audit.get("maximumAllowedExtrapolationDistanceM")
    limit_value = float(limit) if limit is not None else None
    extrapolation_status = (
        "manual_review" if limit_value is not None and extension > limit_value + 1e-9
        else ("warning" if extension > 0.0 or auto_extended else "pass")
    )
    audit.update({
        "status": ("warning" if auto_extended or extension > 0.0 else "pass") if covered else "fail",
        "coverageStatus": "pass" if covered else "fail",
        "extrapolationStatus": extrapolation_status,
        "designDomainCovered": covered,
        "modelBounds": {"minX": current[0], "minY": current[1], "maxX": current[2], "maxY": current[3]},
        "requiredBounds": {"minX": required[0], "minY": required[1], "maxX": required[2], "maxY": required[3]},
    })
    if not covered:
        audit["message"] = "围护结构或施工影响区超出当前地质模型平面范围。"
    elif audit.get("autoExtended"):
        audit["message"] = "地质模型已覆盖围护结构和施工影响区；外扩区域属于低置信度外推区。"
    else:
        audit["message"] = "地质模型平面范围覆盖围护结构和施工影响区。"
    return audit
