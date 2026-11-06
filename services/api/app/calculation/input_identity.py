"""Semantic input identity separated from derived proposals and run metadata."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def full_design_input(project, case) -> dict[str, Any]:
    from app.services.calculation_assurance import calculation_input_payload
    value = calculation_input_payload(project, case)
    value["allCalculationCases"] = [row.model_dump(mode="json", by_alias=True) for row in project.calculation_cases]
    return value


def solver_configuration_hash(project, case) -> str:
    from app.services.calculation_assurance import calculation_input_payload
    value = calculation_input_payload(project, case)
    # Generated reinforcement is a demand proposal and is not used for stiffness
    # in the current fixed cracked-EI model. Supplied bars remain in full input identity.
    value.pop("reinforcementInputs", None)
    derived_only = {"analysisStatus", "spatialAnalysisStatus", "designTorsion",
        "designOutOfPlaneMoment", "designEccentricInPlaneMoment", "spatialDetailing"}
    def physics(item):
        if isinstance(item, dict):
            return {key: physics(child) for key, child in item.items() if key not in derived_only}
        if isinstance(item, list):
            return [physics(child) for child in item]
        return item
    return canonical_hash(physics(value))
