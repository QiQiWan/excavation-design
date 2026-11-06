from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from app.schemas.domain import Project, SupportLayoutOptimizationCandidate
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION

SUPPORT_CANDIDATE_SOURCE_SCHEMA = "3.66-concave-transfer-source-v1"


def _round(value: Any, digits: int = 5) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [_round(item, digits) for item in value]
    if isinstance(value, dict):
        return {str(key): _round(item, digits) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    return value


def _outline_payload(outline: Any) -> dict[str, Any] | None:
    if not outline:
        return None
    return {
        "closed": bool(getattr(outline, "closed", True)),
        "points": [
            [round(float(point.x), 5), round(float(point.y), 5)]
            for point in (getattr(outline, "points", None) or [])
        ],
    }


def support_candidate_source_payload(project: Project) -> dict[str, Any]:
    excavation = project.excavation
    retaining = project.retaining_system
    excavation_payload: dict[str, Any] | None = None
    if excavation:
        excavation_payload = {
            "outline": _outline_payload(excavation.outline),
            "topElevation": round(float(excavation.top_elevation), 5),
            "bottomElevation": round(float(excavation.bottom_elevation), 5),
            "supportAxisOffset": getattr(excavation, "support_axis_offset", None),
            "basementWallOffset": getattr(excavation, "basement_wall_offset", None),
            "localPits": [
                _round(item.model_dump(mode="json", by_alias=True))
                for item in (getattr(excavation, "local_pits", None) or [])
            ],
            "obstacles": [
                {
                    "id": obstacle.id,
                    "type": obstacle.obstacle_type,
                    "active": bool(obstacle.active),
                    "optimizationLocked": bool(getattr(obstacle, "optimization_locked", False)),
                    "outline": _outline_payload(obstacle.outline),
                }
                for obstacle in (getattr(excavation, "obstacles", None) or [])
            ],
        }
    wall_payload = []
    if retaining:
        wall_payload = [
            {
                "segmentId": wall.segment_id,
                "thickness": round(float(wall.thickness), 5),
                "topElevation": round(float(wall.top_elevation), 5),
                "bottomElevation": round(float(wall.bottom_elevation), 5),
                "concreteGrade": wall.concrete_grade,
                "rebarGrade": wall.rebar_grade,
            }
            for wall in sorted(retaining.diaphragm_walls or [], key=lambda item: (str(item.segment_id), str(item.id)))
        ]
    return {
        "schema": SUPPORT_CANDIDATE_SOURCE_SCHEMA,
        "projectId": project.id,
        "unitSystem": _round(project.unit_system.model_dump(mode="json", by_alias=True)),
        "coordinateSystem": _round(project.coordinate_system.model_dump(mode="json", by_alias=True)),
        "excavation": excavation_payload,
        "diaphragmWalls": wall_payload,
        "designSettings": _round(project.design_settings.model_dump(mode="json", by_alias=True)),
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
    }


def support_candidate_source_hash(project: Project) -> str:
    raw = json.dumps(
        support_candidate_source_payload(project),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def stamp_candidate_source(
    candidate: SupportLayoutOptimizationCandidate,
    project: Project,
    *,
    source_hash: str | None = None,
) -> str:
    source_hash = source_hash or support_candidate_source_hash(project)
    candidate.variable_summary = dict(candidate.variable_summary or {})
    candidate.variable_summary["candidateSourceHash"] = source_hash
    candidate.variable_summary["candidateSourceSchema"] = SUPPORT_CANDIDATE_SOURCE_SCHEMA
    candidate.variable_summary["candidateSourceCurrent"] = True
    return source_hash


def candidate_source_hash(candidate: SupportLayoutOptimizationCandidate) -> str | None:
    value = (candidate.variable_summary or {}).get("candidateSourceHash")
    return str(value) if value else None


def candidate_is_current(project: Project, candidate: SupportLayoutOptimizationCandidate) -> bool:
    stored = candidate_source_hash(candidate)
    return bool(stored and stored == support_candidate_source_hash(project))


def candidate_set_state(
    project: Project,
    candidates: Iterable[SupportLayoutOptimizationCandidate],
) -> dict[str, Any]:
    rows = list(candidates)
    current_hash = support_candidate_source_hash(project)
    current_rows = [item for item in rows if candidate_source_hash(item) == current_hash]
    stale_rows = [item for item in rows if candidate_source_hash(item) != current_hash]
    formal_rows = [
        item for item in current_rows
        if bool((item.hard_constraints or {}).get("passed"))
        and (item.variable_summary or {}).get("formalSchemeEligible", True) is not False
    ]
    controlled_rows = [
        item for item in current_rows
        if str((item.variable_summary or {}).get("capabilityOutcome") or "") == "controlled_block"
        or not bool((item.hard_constraints or {}).get("passed"))
    ]
    if not rows:
        state = "not_generated"
    elif not current_rows:
        state = "stale"
    elif formal_rows:
        state = "formal_ready"
    else:
        state = "diagnostic_only"
    reasons: list[str] = []
    if stale_rows:
        reasons.append("candidate_source_hash_mismatch")
    if current_rows and not formal_rows:
        reasons.append("no_formal_candidate")
    if len(formal_rows) < 2:
        reasons.append("insufficient_formal_candidates_for_comparison")
    return {
        "state": state,
        "currentSourceHash": current_hash,
        "candidateCount": len(rows),
        "currentCandidateCount": len(current_rows),
        "staleCandidateCount": len(stale_rows),
        "formalCandidateCount": len(formal_rows),
        "controlledCandidateCount": len(controlled_rows),
        "comparisonAllowed": len(formal_rows) >= 2,
        "adoptionAllowed": len(formal_rows) >= 1,
        "reasonCodes": list(dict.fromkeys(reasons)),
    }


def archive_and_clear_stale_candidates(
    project: Project,
    *,
    reason: str,
    archive_limit: int = 8,
) -> dict[str, Any]:
    retaining = project.retaining_system
    if not retaining or not retaining.support_layout_repair:
        return {"archivedCandidateCount": 0, "reason": reason}
    repair = retaining.support_layout_repair
    candidates = list(repair.candidates or [])
    if not candidates:
        repair.candidate_state = "not_generated"
        repair.comparison_eligibility = candidate_set_state(project, [])
        repair.candidate_source_hash = support_candidate_source_hash(project)
        return {"archivedCandidateCount": 0, "reason": reason}

    advanced = dict(project.advanced_engineering or {})
    archive = list(advanced.get("staleSupportCandidateArchive") or [])
    archive.append({
        "reason": reason,
        "candidateSourceHash": repair.candidate_source_hash,
        "candidateCount": len(candidates),
        "selectedCandidateId": repair.selected_candidate_id,
        "bestCandidateId": repair.best_candidate_id,
        "candidates": [
            {
                "id": item.id,
                "rank": item.rank,
                "score": item.score,
                "topologyFamily": (item.variable_summary or {}).get("topologyFamily"),
                "transferSystemTemplate": (item.variable_summary or {}).get("transferSystemTemplate"),
                "supportCount": item.support_count,
                "columnCount": item.column_count,
                "candidateSourceHash": candidate_source_hash(item),
            }
            for item in candidates[:8]
        ],
    })
    if archive_limit > 0:
        archive = archive[-archive_limit:]
    advanced["staleSupportCandidateArchive"] = archive
    advanced["supportCandidateState"] = {
        "state": "stale_cleared",
        "reason": reason,
        "archivedCandidateCount": len(candidates),
        "currentSourceHash": support_candidate_source_hash(project),
    }
    project.advanced_engineering = advanced

    repair.candidates = []
    repair.candidate_full_calculations = []
    repair.candidate_count = 0
    repair.best_candidate_id = None
    repair.selected_candidate_id = None
    repair.candidate_state = "not_generated"
    repair.formal_candidate_count = 0
    repair.controlled_candidate_count = 0
    repair.stale_candidate_count = len(candidates)
    repair.candidate_source_hash = support_candidate_source_hash(project)
    repair.comparison_eligibility = candidate_set_state(project, [])
    repair.summary = f"候选来源已失效并归档 {len(candidates)} 个旧候选：{reason}。请按当前轮廓重新生成。"
    retaining.layout_summary = dict(retaining.layout_summary or {})
    retaining.layout_summary.pop("candidateFullCalculationComparison", None)
    retaining.layout_summary["supportCandidateState"] = dict(advanced["supportCandidateState"])
    return {"archivedCandidateCount": len(candidates), "reason": reason}
