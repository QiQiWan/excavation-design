from __future__ import annotations

import hashlib
import json

from app.schemas.domain import Project


def support_topology_hash(project: Project) -> str:
    system = project.retaining_system
    supports = system.supports if system else []
    beams = system.ring_beams if system else []
    payload = {
        "supports": [
            {
                "id": support.id,
                "code": support.code,
                "role": support.support_role,
                "loadPathClass": getattr(support, "load_path_class", None),
                "topologyFamily": getattr(support, "topology_family", None),
                "transferSystemId": getattr(support, "transfer_system_id", None),
                "transferZoneId": getattr(support, "transfer_zone_id", None),
                "startNodeId": getattr(support, "start_node_id", None),
                "endNodeId": getattr(support, "end_node_id", None),
                "loadPathId": getattr(support, "load_path_id", None),
                "level": int(support.level_index),
                "elevation": round(float(support.elevation), 4),
                "startFace": support.start_face_code,
                "endFace": support.end_face_code,
                "start": [round(float(support.start.x), 4), round(float(support.start.y), 4)],
                "end": [round(float(support.end.x), 4), round(float(support.end.y), 4)],
                "section": support.section.model_dump(mode="json", by_alias=True),
            }
            for support in sorted(supports, key=lambda item: (int(item.level_index), item.code, item.id))
        ],
        "ringAndTransferBeams": [
            {
                "id": beam.id,
                "code": beam.code,
                "role": beam.beam_role,
                "transferSystemId": getattr(beam, "transfer_system_id", None),
                "transferZoneId": getattr(beam, "transfer_zone_id", None),
                "startNodeId": getattr(beam, "start_node_id", None),
                "endNodeId": getattr(beam, "end_node_id", None),
                "loadPathId": getattr(beam, "load_path_id", None),
                "level": beam.support_level,
                "elevation": round(float(beam.elevation), 4),
                "axis": [
                    [round(float(point.x), 4), round(float(point.y), 4)]
                    for point in beam.axis.points
                ],
                "section": beam.section.model_dump(mode="json", by_alias=True),
            }
            for beam in sorted(beams, key=lambda item: (int(item.support_level or 0), item.code, item.id))
        ],
        "transferSystem": {
            key: value
            for key, value in dict((system.layout_summary or {}).get("transferSystem") or {}).items()
            if key in {
                "templateId", "modelClass", "topologyClass", "transferSystemId",
                "junctionCount", "coveredJunctionCount", "beamCount", "radialSupportCount",
                "requiredFaceCount", "faceCoverageComplete", "ringClosed",
            }
        } if system else {},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
