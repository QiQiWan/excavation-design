from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from app.schemas.domain import Project, SupportLayoutOptimizationCandidate
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION

_CACHE_DIR = Path(__file__).resolve().parents[2] / "runtime_cache" / "candidate-results"
_LOCK = RLock()


def candidate_input_hash(project: Project, candidate: SupportLayoutOptimizationCandidate) -> str:
    payload = {
        "projectId": project.id,
        "excavation": project.excavation.model_dump(mode="json", by_alias=True) if project.excavation else None,
        "strata": [item.model_dump(mode="json", by_alias=True) for item in project.strata],
        "designSettings": project.design_settings.model_dump(mode="json", by_alias=True),
        "calibrationFactors": (project.advanced_engineering or {}).get("calibrationFactors") or {},
        "candidate": {
            "id": candidate.id,
            "targetSpacing": candidate.target_spacing,
            "columnMaxSpan": candidate.column_max_span,
            "variableSummary": candidate.variable_summary,
            "planGeometry": candidate.plan_geometry,
        },
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_cached_candidate_result(input_hash: str) -> dict[str, Any] | None:
    path = _CACHE_DIR / f"{input_hash}.json"
    with _LOCK:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if data.get("inputHash") != input_hash:
        return None
    result = data.get("result")
    return result if isinstance(result, dict) else None


def put_cached_candidate_result(input_hash: str, result: dict[str, Any]) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{input_hash}.json"
    payload = {"inputHash": input_hash, "result": result}
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return path


def cache_stats() -> dict[str, Any]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = list(_CACHE_DIR.glob("*.json"))
    return {"cacheDirectory": str(_CACHE_DIR), "entryCount": len(files), "sizeBytes": sum(path.stat().st_size for path in files)}
