from __future__ import annotations

import math
from typing import Any, Iterable

from app.schemas.domain import Point2D, Project

Bounds = tuple[float, float, float, float]


def _point_bounds(points: Iterable[Point2D]) -> Bounds | None:
    values = list(points)
    if not values:
        return None
    xs = [float(point.x) for point in values]
    ys = [float(point.y) for point in values]
    return min(xs), min(ys), max(xs), max(ys)


def _borehole_bounds(project: Project) -> Bounds | None:
    if not project.boreholes:
        return None
    xs = [float(item.x) for item in project.boreholes]
    ys = [float(item.y) for item in project.boreholes]
    return min(xs), min(ys), max(xs), max(ys)


def _size(bounds: Bounds) -> tuple[float, float]:
    return max(0.0, bounds[2] - bounds[0]), max(0.0, bounds[3] - bounds[1])


def _center(bounds: Bounds) -> tuple[float, float]:
    return (bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0


def _intersection_area(a: Bounds, b: Bounds) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def _area(bounds: Bounds) -> float:
    width, height = _size(bounds)
    return width * height


def _as_dict(bounds: Bounds | None) -> dict[str, float] | None:
    if bounds is None:
        return None
    return {
        "minX": round(bounds[0], 4),
        "minY": round(bounds[1], 4),
        "maxX": round(bounds[2], 4),
        "maxY": round(bounds[3], 4),
    }


def audit_project_coordinate_alignment(project: Project) -> dict[str, Any]:
    """Audit whether geotechnical and excavation plan coordinates are plausibly aligned.

    The check deliberately avoids assuming a particular excavation shape.  It
    compares coordinate envelopes, centre offsets, scale ratios and explicit
    placement metadata.  It never applies a translation automatically; the
    proposed translation is an auditable preview that requires confirmation.
    """
    excavation = project.excavation
    excavation_bounds = _point_bounds(excavation.outline.points) if excavation else None
    borehole_bounds = _borehole_bounds(project)
    coordinate_system = project.coordinate_system

    if excavation_bounds is None:
        return {
            "status": "manual_review",
            "aligned": False,
            "message": "尚未定义基坑平面，无法进行地质与围护坐标一致性检查。",
            "excavationBounds": None,
            "boreholeBounds": _as_dict(borehole_bounds),
            "requiresConfirmation": True,
        }
    if borehole_bounds is None:
        return {
            "status": "warning",
            "aligned": False,
            "message": "缺少钻孔平面坐标，无法验证地质数据与基坑轮廓是否处于同一坐标系。",
            "excavationBounds": _as_dict(excavation_bounds),
            "boreholeBounds": None,
            "requiresConfirmation": True,
        }

    excavation_width, excavation_height = _size(excavation_bounds)
    borehole_width, borehole_height = _size(borehole_bounds)
    exc_center = _center(excavation_bounds)
    bore_center = _center(borehole_bounds)
    center_distance = math.hypot(bore_center[0] - exc_center[0], bore_center[1] - exc_center[1])
    reference_span = max(excavation_width, excavation_height, borehole_width, borehole_height, 1.0)
    normalized_center_offset = center_distance / reference_span
    overlap = _intersection_area(excavation_bounds, borehole_bounds)
    overlap_ratio = overlap / max(min(_area(excavation_bounds), _area(borehole_bounds)), 1e-9)

    excavation_span = max(excavation_width, excavation_height, 1e-9)
    borehole_span = max(borehole_width, borehole_height, 1e-9)
    scale_ratio = max(excavation_span / borehole_span, borehole_span / excavation_span)
    explicit_placement = bool(getattr(excavation, "explicit_placement", False))
    centered_on_geology = bool(getattr(excavation, "centered_on_geology", False))
    declared_transform = (
        coordinate_system.type != "local"
        or abs(float(coordinate_system.origin_x)) > 1e-9
        or abs(float(coordinate_system.origin_y)) > 1e-9
    )

    severe_scale_risk = scale_ratio >= 100.0
    no_overlap = overlap_ratio <= 1e-6
    large_offset = normalized_center_offset > 0.75
    moderate_offset = normalized_center_offset > 0.30

    if severe_scale_risk:
        status = "fail"
        aligned = False
        message = "基坑与钻孔平面尺度相差超过两个数量级，可能存在 m/mm 单位或坐标转换错误。"
    elif no_overlap and large_offset and not (explicit_placement or centered_on_geology or declared_transform):
        status = "manual_review"
        aligned = False
        message = "钻孔与基坑平面包围盒无交叠且中心偏移较大，需确认坐标原点、平移或旋转关系。"
    elif no_overlap and not (explicit_placement or centered_on_geology):
        status = "warning"
        aligned = False
        message = "钻孔与基坑平面包围盒无交叠；地质外推可以覆盖计算域，但坐标关系仍需确认。"
    elif moderate_offset and overlap_ratio < 0.20:
        status = "warning"
        aligned = False
        message = "钻孔与基坑仅局部交叠，建议复核控制点和坐标转换参数。"
    else:
        status = "pass"
        aligned = True
        message = "钻孔与基坑平面坐标范围具有合理交叠，未发现明显的平移或尺度异常。"

    translation = {
        "dx": round(bore_center[0] - exc_center[0], 4),
        "dy": round(bore_center[1] - exc_center[1], 4),
        "method": "align_excavation_center_to_borehole_center",
        "automaticApplicationAllowed": False,
    }
    return {
        "status": status,
        "aligned": aligned,
        "message": message,
        "coordinateSystemType": coordinate_system.type,
        "declaredOrigin": {
            "x": float(coordinate_system.origin_x),
            "y": float(coordinate_system.origin_y),
            "z": float(coordinate_system.origin_z),
        },
        "excavationBounds": _as_dict(excavation_bounds),
        "boreholeBounds": _as_dict(borehole_bounds),
        "excavationCenter": {"x": round(exc_center[0], 4), "y": round(exc_center[1], 4)},
        "boreholeCenter": {"x": round(bore_center[0], 4), "y": round(bore_center[1], 4)},
        "centerOffsetM": round(center_distance, 4),
        "normalizedCenterOffset": round(normalized_center_offset, 4),
        "overlapRatio": round(overlap_ratio, 4),
        "scaleRatio": round(scale_ratio, 4),
        "explicitPlacement": explicit_placement,
        "centeredOnGeology": centered_on_geology,
        "declaredCoordinateTransform": declared_transform,
        "suggestedTranslation": translation,
        "requiresConfirmation": status in {"fail", "manual_review", "warning"},
        "evidenceBoundary": "该检查只验证平面包围盒、尺度和显式坐标元数据；正式工程仍应使用测量控制点或统一坐标转换文件复核。",
    }
