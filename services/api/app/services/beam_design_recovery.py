from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.calculation.engine import _design_crown_beams, _design_wale_beams, _summary
from app.rules.jgj120_2012.retaining_wall_rules import importance_factor
from app.schemas.domain import Project
from app.services.runtime_diagnostics import append_event


def _check_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("ruleId") or ""), str(row.get("objectId") or "")


def _merge_checks(existing: list[dict[str, Any]], added: list[dict[str, Any]]) -> list[dict[str, Any]]:
    replacement_keys = {_check_key(row) for row in added}
    target_objects = {str(row.get("objectId") or "") for row in added}
    output = [
        dict(row)
        for row in existing
        if _check_key(dict(row)) not in replacement_keys
        and not (
            str(dict(row).get("ruleId") or "") == "PITGUARD-CROWN-BEAM-STAGE-EVIDENCE"
            and str(dict(row).get("objectId") or "") in target_objects
        )
    ]
    output.extend(dict(row) for row in added)
    return output


def recover_missing_beam_designs(project: Project) -> dict[str, Any]:
    """Complete missing crown/wale member records from current stage evidence.

    This is a derived-output recovery. Existing sections are kept unchanged so
    the immutable calculation input contract stays current. A geometry-only
    wale without a direct reaction receives the same-level worst envelope and
    remains a professional-review item. If the existing section fails that
    conservative envelope, the failure remains visible and must be strengthened
    and recalculated rather than being hidden.
    """
    ret = project.retaining_system
    latest = project.calculation_results[-1] if project.calculation_results else None
    if ret is None or latest is None:
        return {
            "status": "not_available",
            "recoveredCount": 0,
            "unresolvedCount": 0,
            "message": "缺少围护体系或当前施工阶段计算结果，无法补齐梁设计记录。",
        }

    crown_missing = {beam.code for beam in ret.crown_beams if beam.design_result is None}
    wale_missing = {
        beam.code
        for beam in [*ret.wale_beams, *(ret.ring_beams or [])]
        if beam.design_result is None
    }
    before = sorted(crown_missing | wale_missing)
    if not before:
        return {
            "status": "not_needed",
            "recoveredCount": 0,
            "unresolvedCount": 0,
            "message": "冠梁、围檩和环梁均已有当前设计结果。",
        }

    stages = list(latest.stage_results or [])
    gamma0 = importance_factor(project.design_settings.safety_grade)
    wale_results = [row for stage in stages for row in list(stage.wale_beam_results or [])]
    added: list[dict[str, Any]] = []
    if wale_missing and wale_results:
        added.extend(_design_wale_beams(
            project,
            wale_results,
            gamma0,
            beam_codes=wale_missing,
            allow_section_resize=False,
        ))
    if crown_missing and stages:
        added.extend(_design_crown_beams(
            project,
            stages,
            gamma0,
            beam_codes=crown_missing,
            allow_section_resize=False,
        ))

    after = sorted(
        beam.code
        for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]
        if beam.design_result is None
    )
    recovered = [code for code in before if code not in after]
    substituted = sorted(
        beam.code
        for beam in [*ret.wale_beams, *(ret.ring_beams or [])]
        if beam.code in recovered
        and beam.design_result is not None
        and "同一道支撑" in str(beam.design_result.method or "")
    )
    hard_failures = sorted(
        beam.code
        for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]
        if beam.code in recovered
        and beam.design_result is not None
        and str(beam.design_result.check_status) == "fail"
    )

    if added:
        latest.checks = _merge_checks(list(latest.checks or []), added)
        latest.check_summary = _summary(list(latest.checks or []))
        latest.report_diagram_data = dict(latest.report_diagram_data or {})
        latest.report_diagram_data["checkSummary"] = dict(latest.check_summary or {})
        if stages:
            stages[-1].checks = _merge_checks(list(stages[-1].checks or []), added)
        latest.design_iteration_summary = dict(latest.design_iteration_summary or {})

    status = "complete" if not after and not hard_failures else "requires_strengthening" if hard_failures else "incomplete"
    record = {
        "version": "3.60-beam-design-recovery-v1",
        "status": status,
        "calculationResultId": latest.id,
        "requestedCount": len(before),
        "recoveredCount": len(recovered),
        "sameLevelEnvelopeCount": len(substituted),
        "unresolvedCount": len(after),
        "hardFailureCount": len(hard_failures),
        "recoveredObjects": recovered[:120],
        "sameLevelEnvelopeObjects": substituted[:120],
        "unresolvedObjects": after[:120],
        "hardFailureObjects": hard_failures[:120],
        "calculatedAt": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"已从当前施工阶段证据补齐 {len(recovered)} 根梁，其中 {len(substituted)} 根采用同层最不利围檩包络并保留专业复核标识。"
            if recovered else "当前施工阶段证据不足，未能补齐缺失梁设计结果。"
        ),
    }
    latest.design_iteration_summary["beamDesignRecovery"] = record
    project.advanced_engineering = dict(project.advanced_engineering or {})
    project.advanced_engineering["beamDesignRecovery"] = record
    append_event("rebar-contract", "beam-design-recovered", projectId=project.id, **record)
    return record

