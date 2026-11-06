from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.schemas.domain import Project


def _point_tuple(point: Any) -> tuple[float, float]:
    return (round(float(point.x), 6), round(float(point.y), 6))


def _canonical_ring(points: list[Any]) -> list[tuple[float, float]]:
    ring = [_point_tuple(point) for point in points]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if not ring:
        return []
    variants: list[list[tuple[float, float]]] = []
    for seq in (ring, list(reversed(ring))):
        for index in range(len(seq)):
            variants.append(seq[index:] + seq[:index])
    return min(variants)


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def excavation_geometry_payload(project: Project) -> dict[str, Any]:
    excavation = project.excavation
    if excavation is None:
        return {"available": False}
    return {
        "available": True,
        "outline": _canonical_ring(list(excavation.outline.points)),
        "closed": bool(excavation.outline.closed),
        "topElevation": round(float(excavation.top_elevation), 6),
        "bottomElevation": round(float(excavation.bottom_elevation), 6),
        "segments": [
            {
                "id": segment.id,
                "name": segment.name,
                "start": _point_tuple(segment.start),
                "end": _point_tuple(segment.end),
            }
            for segment in excavation.segments
        ],
    }


def wall_geometry_payload(project: Project) -> dict[str, Any]:
    system = project.retaining_system
    if system is None:
        return {"available": False, "walls": []}
    walls = []
    for wall in system.diaphragm_walls:
        points = [_point_tuple(point) for point in wall.axis.points]
        walls.append({
            "id": wall.id,
            "segmentId": wall.segment_id,
            "faceSegmentIds": sorted(wall.face_segment_ids or []),
            "axis": points,
            "topElevation": round(float(wall.top_elevation), 6),
            "bottomElevation": round(float(wall.bottom_elevation), 6),
            "thickness": round(float(wall.thickness), 6),
        })
    walls.sort(key=lambda row: (row["segmentId"], row["id"]))
    return {"available": True, "walls": walls}


def geometry_consistency_summary(project: Project) -> dict[str, Any]:
    excavation_payload = excavation_geometry_payload(project)
    wall_payload = wall_geometry_payload(project)
    excavation_segments = {row["id"] for row in excavation_payload.get("segments", [])}
    wall_segments: set[str] = set()
    for wall in wall_payload.get("walls", []):
        wall_segments.add(str(wall["segmentId"]))
        wall_segments.update(str(item) for item in wall.get("faceSegmentIds", []))
    missing_wall_segments = sorted(excavation_segments - wall_segments)
    orphan_wall_segments = sorted(wall_segments - excavation_segments)
    outline_closed = bool(excavation_payload.get("closed"))
    consistent = outline_closed and not missing_wall_segments and not orphan_wall_segments
    return {
        "consistent": consistent,
        "outlineClosed": outline_closed,
        "excavationGeometryHash": _hash(excavation_payload),
        "wallGeometryHash": _hash(wall_payload),
        "missingWallSegments": missing_wall_segments,
        "orphanWallSegments": orphan_wall_segments,
        "wallCount": len(wall_payload.get("walls", [])),
        "segmentCount": len(excavation_payload.get("segments", [])),
    }
