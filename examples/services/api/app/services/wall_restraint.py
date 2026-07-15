from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.schemas.domain import MaterialDefinition, Point2D, SectionDefinition, SupportElement


def _distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))


def _endpoint_on_face(support: SupportElement, face_code: str) -> Point2D | None:
    if support.start_face_code == face_code:
        return support.start_wall_connection or support.start
    if support.end_face_code == face_code:
        return support.end_wall_connection or support.end
    return None


def _segment_index(excavation: Any, face_code: str) -> int | None:
    for index, item in enumerate(getattr(excavation, "segments", []) or []):
        if str(getattr(item, "name", "")) == str(face_code):
            return index
    return None


def _proxy_support(
    *,
    segment: Any,
    level_index: int,
    elevation: float,
    base_support: SupportElement,
    equivalent_span: float,
    evidence: str,
) -> SupportElement:
    midpoint = getattr(segment, "midpoint", None) or Point2D(
        x=(float(segment.start.x) + float(segment.end.x)) / 2.0,
        y=(float(segment.start.y) + float(segment.end.y)) / 2.0,
    )
    outward = getattr(segment, "outward_normal", None)
    nx = -float(getattr(outward, "x", 0.0) or 0.0)
    ny = -float(getattr(outward, "y", 0.0) or 0.0)
    norm = math.hypot(nx, ny)
    if norm <= 1.0e-9:
        dx = float(segment.end.x) - float(segment.start.x)
        dy = float(segment.end.y) - float(segment.start.y)
        length = max(math.hypot(dx, dy), 1.0e-9)
        nx, ny = -dy / length, dx / length
    else:
        nx, ny = nx / norm, ny / norm
    span = max(5.0, min(float(equivalent_span), 30.0))
    end = Point2D(x=round(float(midpoint.x) + nx * span, 4), y=round(float(midpoint.y) + ny * span, 4))
    section = SectionDefinition.model_validate(base_support.section.model_dump(mode="json", by_alias=True))
    material = MaterialDefinition.model_validate(base_support.material.model_dump(mode="json", by_alias=True))
    return SupportElement(
        id=f"corner-transfer-{segment.name}-L{level_index}",
        code=f"CT-{segment.name}-L{level_index}",
        level_index=int(level_index),
        elevation=float(elevation),
        start=Point2D(x=round(float(midpoint.x), 4), y=round(float(midpoint.y), 4)),
        end=end,
        support_role="corner_diagonal",
        layout_note="短回墙通过两端连续围檩及相邻支撑形成等效角部传力约束；该构件仅用于分析，不进入工程量。",
        span_length=span,
        bay_spacing=float(getattr(segment, "length", 0.0) or 0.0),
        start_face_code=str(segment.name),
        end_face_code=None,
        start_wall_connection=Point2D(x=round(float(midpoint.x), 4), y=round(float(midpoint.y), 4)),
        end_wall_connection=None,
        force_distribution_note=evidence,
        section_type=base_support.section_type,
        section=section,
        material=material,
        reinforcement=[],
        topology_family="hybrid_diagonal",
        professional_review_required=True,
    )


def build_effective_wall_restraints(
    excavation: Any,
    segment: Any,
    active_supports: list[SupportElement],
    *,
    target_spacing_m: float = 5.0,
    short_return_limit_m: float | None = None,
) -> tuple[list[SupportElement], dict[str, Any]]:
    """Build traceable analytical restraints for short stepped/return wall faces.

    A short wall between two directly supported adjacent faces is part of a corner
    transfer zone.  Treating it as an isolated unsupported vertical strip creates
    false wall moments and displacements.  This routine adds one reduced-stiffness
    analytical restraint per active level only when both ends have a nearby,
    continuous load path.  Proxies are local analysis objects and are never added
    to the permanent retaining-system member list or quantities.
    """
    face_code = str(getattr(segment, "name", ""))
    direct = [s for s in active_supports if face_code in {str(s.start_face_code or ""), str(s.end_face_code or "")}]
    active_levels = sorted({int(s.level_index) for s in active_supports})
    direct_levels = {int(s.level_index) for s in direct}
    index = _segment_index(excavation, face_code)
    length = float(getattr(segment, "length", 0.0) or 0.0)
    limit = float(short_return_limit_m if short_return_limit_m is not None else max(6.0, 1.25 * float(target_spacing_m)))
    audit: dict[str, Any] = {
        "faceCode": face_code,
        "segmentLengthM": round(length, 3),
        "directSupportCount": len(direct),
        "directSupportLevels": sorted(direct_levels),
        "activeLevels": active_levels,
        "analyticalTransferLevels": [],
        "unresolvedLevels": [],
        "status": "pass" if direct else "manual_review",
        "method": "direct support endpoints",
        "evidence": [],
    }
    if not active_levels or index is None:
        if not direct:
            audit["status"] = "fail"
            audit["unresolvedLevels"] = active_levels
            audit["method"] = "no active support/load-path data"
        return [], audit
    if length > limit:
        missing = [level for level in active_levels if level not in direct_levels]
        if missing:
            audit["status"] = "fail"
            audit["unresolvedLevels"] = missing
            audit["method"] = "direct support required for non-short wall face"
        return [], audit

    segments = list(getattr(excavation, "segments", []) or [])
    previous = segments[(index - 1) % len(segments)]
    following = segments[(index + 1) % len(segments)]
    previous_code = str(previous.name)
    following_code = str(following.name)
    start_corner = segment.start
    end_corner = segment.end
    threshold = max(8.0, 1.6 * float(target_spacing_m))
    by_level: dict[int, list[SupportElement]] = defaultdict(list)
    for support in active_supports:
        by_level[int(support.level_index)].append(support)

    proxies: list[SupportElement] = []
    for level in active_levels:
        if level in direct_levels:
            continue
        level_supports = by_level[level]
        prev_candidates: list[tuple[float, SupportElement]] = []
        next_candidates: list[tuple[float, SupportElement]] = []
        for support in level_supports:
            p = _endpoint_on_face(support, previous_code)
            if p is not None:
                prev_candidates.append((_distance(p, start_corner), support))
            p = _endpoint_on_face(support, following_code)
            if p is not None:
                next_candidates.append((_distance(p, end_corner), support))
        prev_hit = min(prev_candidates, key=lambda item: item[0]) if prev_candidates else None
        next_hit = min(next_candidates, key=lambda item: item[0]) if next_candidates else None
        if not prev_hit or not next_hit or prev_hit[0] > threshold or next_hit[0] > threshold:
            audit["unresolvedLevels"].append(level)
            continue
        base = prev_hit[1] if float(prev_hit[1].span_length or 1.0) <= float(next_hit[1].span_length or 1.0) else next_hit[1]
        span = 0.5 * (float(prev_hit[1].span_length or 8.0) + float(next_hit[1].span_length or 8.0))
        evidence = (
            f"{face_code} 长度 {length:.2f}m；L{level} 两端分别由 {prev_hit[1].code}、{next_hit[1].code} "
            f"经连续围檩传力，端点距转角 {prev_hit[0]:.2f}m/{next_hit[0]:.2f}m；按 0.55 角色刚度折减建立分析约束。"
        )
        proxy = _proxy_support(
            segment=segment,
            level_index=level,
            elevation=float(base.elevation),
            base_support=base,
            equivalent_span=span,
            evidence=evidence,
        )
        proxies.append(proxy)
        audit["analyticalTransferLevels"].append(level)
        audit["evidence"].append(evidence)

    if audit["unresolvedLevels"]:
        audit["status"] = "fail"
        audit["method"] = "partial corner-transfer path; direct/local support required"
    elif proxies:
        audit["status"] = "pass"
        audit["method"] = "short-return corner transfer through continuous wales and adjacent supports"
    elif direct:
        audit["status"] = "pass"
    else:
        audit["status"] = "fail"
        audit["unresolvedLevels"] = active_levels
    return proxies, audit
