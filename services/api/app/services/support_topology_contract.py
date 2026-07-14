from __future__ import annotations

import hashlib
import json

from app.schemas.domain import Project


def support_topology_hash(project: Project) -> str:
    supports = project.retaining_system.supports if project.retaining_system else []
    payload = [
        {
            "id": support.id,
            "code": support.code,
            "level": int(support.level_index),
            "elevation": round(float(support.elevation), 4),
            "startFace": support.start_face_code,
            "endFace": support.end_face_code,
            "start": [round(float(support.start.x), 4), round(float(support.start.y), 4)],
            "end": [round(float(support.end.x), 4), round(float(support.end.y), 4)],
        }
        for support in sorted(supports, key=lambda item: (int(item.level_index), item.code, item.id))
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
