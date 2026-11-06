from __future__ import annotations

import math
from typing import Any

OBJECTIVE_KEYS = (
    "maxDisplacement",
    "maxSupportAxialForce",
    "maxWaleMoment",
    "materialIndex",
    "constructabilityRisk",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _material_index(item: dict[str, Any]) -> float:
    if item.get("materialIndex") is not None:
        return _number(item.get("materialIndex"))
    support_count = _number(item.get("supportCount"))
    column_count = _number(item.get("columnCount"))
    max_span = max(_number(item.get("maxSpanLength"), 1.0), 1.0)
    embedment_added = max(_number(item.get("wallEmbedmentAddedM")), 0.0)
    # Bounded comparative proxy. It ranks only the compared alternatives and is
    # never presented as a bill of quantities.
    return support_count * max_span + column_count * 12.0 + embedment_added * 80.0


def _constructability_risk(item: dict[str, Any]) -> float:
    return (
        _number(item.get("warningCount")) * 0.5
        + _number(item.get("manualReviewCount")) * 0.75
        + _number(item.get("excessiveDirectStrutCount")) * 2.0
        + _number(item.get("crossingCount")) * 10.0
        + _number(item.get("obstacleConflictCount")) * 10.0
        + _number(item.get("maxSpanLength")) / 20.0
    )


def objective_vector(item: dict[str, Any]) -> dict[str, float]:
    return {
        "maxDisplacement": max(_number(item.get("maxDisplacement")), 0.0),
        "maxSupportAxialForce": max(_number(item.get("maxSupportAxialForce")), 0.0),
        "maxWaleMoment": max(_number(item.get("maxWaleMoment")), 0.0),
        "materialIndex": max(_material_index(item), 0.0),
        "constructabilityRisk": max(_constructability_risk(item), 0.0),
    }


def _dominates(a: dict[str, float], b: dict[str, float], tol: float = 1.0e-9) -> bool:
    no_worse = all(a[key] <= b[key] + tol for key in OBJECTIVE_KEYS)
    strictly_better = any(a[key] < b[key] - tol for key in OBJECTIVE_KEYS)
    return no_worse and strictly_better


def apply_pareto_ranking(outputs: list[dict[str, Any]]) -> None:
    valid = [row for row in outputs if not row.get("error")]
    if not valid:
        return
    vectors = [objective_vector(row) for row in valid]
    remaining = set(range(len(valid)))
    rank = 1
    while remaining:
        front = []
        for i in sorted(remaining):
            if not any(_dominates(vectors[j], vectors[i]) for j in remaining if j != i):
                front.append(i)
        if not front:
            front = [min(remaining)]
        for i in front:
            row = valid[i]
            dominated_by = [
                str(valid[j].get("candidateId") or valid[j].get("schemeLabel") or j)
                for j in range(len(valid)) if j != i and _dominates(vectors[j], vectors[i])
            ]
            row["paretoRank"] = rank
            row["paretoFront"] = rank == 1
            row["paretoObjectives"] = {key: round(value, 6) for key, value in vectors[i].items()}
            row["paretoDominatedBy"] = dominated_by
            row["materialIndex"] = round(vectors[i]["materialIndex"], 4)
            row["constructabilityRisk"] = round(vectors[i]["constructabilityRisk"], 4)
        remaining.difference_update(front)
        rank += 1


def pareto_summary(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    apply_pareto_ranking(outputs)
    valid = [row for row in outputs if not row.get("error")]
    front = [row for row in valid if int(row.get("paretoRank") or 999) == 1]
    return {
        "method": "non-dominated sorting on calculated displacement, support force, wale moment, material proxy and constructability risk",
        "candidateCount": len(valid),
        "frontCount": len(front),
        "frontCandidateIds": [row.get("candidateId") for row in front],
        "boundary": "materialIndex is a comparative proxy; formal quantities require detailed schedules",
    }
