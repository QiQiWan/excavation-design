from __future__ import annotations

from typing import Any

from app.schemas.domain import Project


def invalidate_calculation_state(
    project: Project,
    *,
    reason: str,
    rebuild_cases: bool = True,
    archive_limit: int = 20,
) -> dict[str, Any]:
    """Invalidate results after geometry/topology changes without losing audit history.

    The active ``calculation_results`` list is consumed by the UI as the current
    design result.  Leaving a pre-change result there caused V3.14 to mix old
    Fail counts with a newly adopted support scheme.  Compact summaries are moved
    to the project audit archive and the active list is cleared.
    """
    previous = list(project.calculation_results or [])
    advanced = dict(project.advanced_engineering or {})
    archive = list(advanced.get("invalidatedCalculationArchive") or [])
    for result in previous:
        archive.append({
            "resultId": result.id,
            "caseId": result.case_id,
            "calculatedAt": result.calculated_at,
            "checkSummary": dict(result.check_summary or {}),
            "governingValues": result.governing_values.model_dump(mode="json", by_alias=True),
            "reason": reason,
        })
    if archive_limit > 0:
        archive = archive[-archive_limit:]
    advanced["invalidatedCalculationArchive"] = archive
    advanced["calculationState"] = {
        "status": "invalidated",
        "reason": reason,
        "invalidatedResultCount": len(previous),
        "requiresRecalculation": True,
    }
    project.advanced_engineering = advanced
    project.calculation_results = []

    if project.retaining_system and project.retaining_system.support_layout_repair:
        repair = project.retaining_system.support_layout_repair
        repair.candidate_full_calculations = []
        for candidate in repair.candidates or []:
            candidate.full_calculation = {}
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary.pop("candidateFullCalculationComparison", None)
        project.retaining_system.layout_summary["calculationInvalidation"] = dict(advanced["calculationState"])

    if rebuild_cases and project.excavation and project.retaining_system:
        from app.calculation.engine import build_default_construction_cases

        project.calculation_cases = build_default_construction_cases(project)
        advanced["calculationState"]["rebuiltCaseCount"] = len(project.calculation_cases)
    else:
        project.calculation_cases = []
        advanced["calculationState"]["rebuiltCaseCount"] = 0
    return dict(advanced["calculationState"])


def mark_calculation_state_current(project: Project, result_id: str) -> None:
    advanced = dict(project.advanced_engineering or {})
    advanced["calculationState"] = {
        "status": "current",
        "resultId": result_id,
        "requiresRecalculation": False,
    }
    project.advanced_engineering = advanced
