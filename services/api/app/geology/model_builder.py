from __future__ import annotations

from collections import defaultdict
from typing import Iterable

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


def _model_bounds(model: GeologicalModel | None) -> Bounds | None:
    if model is None or not model.surfaces:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for surface in model.surfaces:
        xs.extend(surface.grid.x_values)
        ys.extend(surface.grid.y_values)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _contains_bounds(outer: Bounds, inner: Bounds, tol: float = 1e-6) -> bool:
    return outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol


def build_geological_model_from_boreholes(boreholes: list[Borehole], grid_size: float = 10.0, required_bounds: Bounds | None = None) -> GeologicalModel:
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
    if required_bounds and bounds != natural_bounds:
        warnings.append(
            "地质模型范围已自动外扩至覆盖基坑围护结构及施工影响区；外扩区域采用边界钻孔 IDW 外推，正式工程应补充勘察或人工确认。"
        )
    surfaces: list[GeologicalSurface] = []
    for code in sorted(bottom_points.keys()):
        confidence = "high" if len(bottom_points[code]) == len(boreholes) and not required_bounds else "medium"
        surfaces.append(
            GeologicalSurface(
                stratum_code=code,
                surface_type="top",
                grid=interpolate_surface_idw(top_points[code], bounds, grid_size),
                confidence=confidence,
            )
        )
        surfaces.append(
            GeologicalSurface(
                stratum_code=code,
                surface_type="bottom",
                grid=interpolate_surface_idw(bottom_points[code], bounds, grid_size),
                confidence=confidence,
            )
        )

    volumes = [{"stratumCode": code, "representation": "between top/bottom IDW surfaces", "confidence": "IDW-with-optional-extension"} for code in sorted(bottom_points.keys())]
    return GeologicalModel(surfaces=surfaces, volumes=volumes, warnings=warnings)


def ensure_geological_model_covers_excavation(project: Project, grid_size: float = 10.0, padding: float = 10.0) -> bool:
    """Extend/rebuild the geological model when the excavation exceeds its XY range.

    Returns True when the model was generated or regenerated.  The function keeps
    any imported VTU mesh in place, because the IDW geological surfaces are the
    fallback used by design-section extraction and visualization.
    """
    required = _bounds_from_excavation(project, padding=padding)
    if required is None:
        return False
    current = _model_bounds(project.geological_model)
    if current is not None and _contains_bounds(current, required):
        return False
    if not project.boreholes:
        if project.geological_model:
            project.geological_model.warnings = list(dict.fromkeys(project.geological_model.warnings + ["基坑范围超出地质模型，但缺少钻孔，无法自动外扩地质面。"] ))
        return False
    old_mesh = project.geological_model.vtu_mesh if project.geological_model else None
    project.geological_model = build_geological_model_from_boreholes(project.boreholes, grid_size=grid_size, required_bounds=required)
    project.geological_model.vtu_mesh = old_mesh
    return True
