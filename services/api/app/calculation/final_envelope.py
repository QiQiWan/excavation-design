"""Single aggregation point, called only after the final structural solves."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.schemas.domain import StageCalculationResult


def build_final_wall_envelopes(rows: list[StageCalculationResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[StageCalculationResult]] = {}
    for row in rows:
        grouped.setdefault(row.segment_id, []).append(row)
    envelopes = {}
    for segment_id, items in grouped.items():
        standard: dict[str, float | None] = {}
        design: dict[str, float | None] = {}
        governing: dict[str, str | None] = {}
        for destination, key, field, provenance in [
            (standard, "moment", "max_moment", "momentStageId"),
            (standard, "shear", "max_shear", "shearStageId"),
            (standard, "displacement", "max_displacement", "displacementStageId"),
            (design, "moment", "max_moment_design", "momentDesignStageId"),
            (design, "shear", "max_shear_design", "shearDesignStageId"),
        ]:
            values = [(row.stage_id, getattr(row.wall_internal_force, field, None)) for row in items]
            known = [(sid, abs(float(value))) for sid, value in values
                     if isinstance(value, (int, float)) and math.isfinite(float(value))]
            complete = len(known) == len(items)
            maximum = max(known, key=lambda pair: pair[1]) if known else (None, None)
            # A partial maximum cannot establish compliance for all stages.
            destination[key] = maximum[1] if complete else None
            governing[provenance] = maximum[0] if complete else None
        evidence = {
            "segmentId": segment_id, "standard": standard, "design": design,
            "governing": governing, "stageIds": [row.stage_id for row in items],
            "solveIds": [getattr(row, "solve_id", None) for row in items],
            "source": "final_stage_results_after_coupling",
        }
        evidence["envelopeHash"] = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        envelopes[segment_id] = evidence
    return envelopes
