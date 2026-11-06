from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable
import gc
import hashlib
import json
import re

from app.schemas.domain import (
    CalculationCase,
    ConstructionPlanStage,
    ConstructionStage,
    DesignControlStage,
    DesignScenario,
    DeviationEvent,
    FieldExecutionSnapshot,
    Project,
)
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.review_workflow import review_status
from app.services.support_topology_contract import support_topology_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(
    code: str,
    label: str,
    status: str,
    *,
    responsibility: str,
    affects: str,
    action: str,
    blocking: bool = False,
    detail: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "blocking": bool(blocking),
        "responsibility": responsibility,
        "affects": affects,
        "action": action,
        "detail": detail,
    }



def design_control_signature(stages: list[DesignControlStage]) -> str:
    """Hash designer-controlled content while ignoring approval-only metadata.

    Approval/freeze changes are workflow decisions. They must not invalidate a
    numerically current calculation when elevations, supports, water, surcharge
    and scenario-defining limits are unchanged.
    """
    rows: list[dict[str, Any]] = []
    ignored = {
        "data_status",
        "created_at",
        "updated_at",
        "source_calculation_case_id",
        "source_stage_id",
        "design_scenario_ids",
    }
    for stage in stages:
        payload = stage.model_dump(mode="json", by_alias=False)
        rows.append({key: value for key, value in payload.items() if key not in ignored})
    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def invalidate_design_scenario_results(project: Project, *, reason: str) -> dict[str, Any]:
    """Invalidate only derived scenario execution/envelope evidence."""
    advanced = dict(project.advanced_engineering or {})
    cleared: list[str] = []
    for key in ("designScenarioExecution", "designScenarioEnvelope"):
        if key in advanced:
            advanced.pop(key, None)
            cleared.append(key)
    advanced["designScenarioState"] = {
        "status": "invalidated",
        "reason": reason,
        "requiresExecution": True,
        "clearedKeys": cleared,
        "invalidatedAt": _now(),
    }
    project.advanced_engineering = advanced
    return dict(advanced["designScenarioState"])

def migrate_legacy_stages(project: Project, *, force: bool = False) -> dict[str, Any]:
    """Create designer-owned control stages from the latest legacy calculation case.

    The migration is idempotent. It never interprets legacy source-document,
    approval or observed values as field facts; those fields remain in the
    legacy numerical case for audit only.
    """
    if project.design_control_stages and not force:
        return {
            "migrated": False,
            "reason": "already_present",
            "stageCount": len(project.design_control_stages),
        }
    case = project.calculation_cases[-1] if project.calculation_cases else None
    if case is None:
        try:
            from app.calculation.engine import build_default_construction_cases

            cases = build_default_construction_cases(project)
            case = cases[-1] if cases else None
        except (ValueError, TypeError):
            case = None
    if case is None:
        project.design_control_stages = []
        return {"migrated": False, "reason": "no_stage_source", "stageCount": 0}

    stages: list[DesignControlStage] = []
    excavation_top = float(project.excavation.top_elevation if project.excavation else 0.0)
    previous = excavation_top
    settings = project.design_settings
    for row in case.stages:
        lower = min(float(previous), float(row.excavation_elevation))
        upper = max(float(previous), float(row.excavation_elevation))
        preload_target = None
        preload_values = [
            float(support.preload)
            for support in (project.retaining_system.supports if project.retaining_system else [])
            if support.id in set(row.active_support_ids) and support.preload is not None
        ]
        if preload_values:
            preload_target = sum(preload_values) / len(preload_values)
        support_by_id = {
            support.id: support
            for support in (project.retaining_system.supports if project.retaining_system else [])
        }
        inactive_levels = sorted({
            int(support_by_id[item].level_index)
            for item in row.deactivated_support_ids
            if item in support_by_id
        })
        if not inactive_levels and row.stage_type in {"replacement", "support_removal"}:
            # Legacy generated cases retain semantic transferred levels even if
            # their support IDs later become stale. Preserve the current-stage
            # removal level when it can be recovered from zone/name metadata.
            match = re.search(r"(?:replace|remove)-L(\d+)", str(row.zone or ""), re.IGNORECASE)
            if not match:
                match = re.search(r"第\s*(\d+)\s*道", str(row.name or ""))
            if match:
                inactive_levels = [int(match.group(1))]
        stage = DesignControlStage(
            name=row.name,
            excavation_elevation_lower=lower,
            excavation_elevation_upper=upper,
            required_support_ids=list(row.active_support_ids),
            permitted_inactive_support_ids=list(row.deactivated_support_ids),
            required_support_levels=sorted({int(v) for v in row.active_support_levels}),
            permitted_inactive_support_levels=inactive_levels,
            replacement_action=row.replacement_action,
            transfer_path_status=(
                "mapped" if row.stage_type in {"replacement", "support_removal"} else "not_applicable"
            ),
            transfer_path_source=(
                "legacy_calculation_case_semantics" if row.stage_type in {"replacement", "support_removal"} else None
            ),
            groundwater_level_limit=row.groundwater_level_inside
            if row.groundwater_level_inside is not None
            else settings.groundwater_level_inside,
            surcharge_limit=float(row.surcharge),
            preload_target=preload_target,
            preload_lower=(0.8 * preload_target) if preload_target is not None else None,
            preload_upper=(1.1 * preload_target) if preload_target is not None else None,
            overexcavation_limit=float(settings.overexcavation_depth_m),
            stiffness_reduction_limit=0.85,
            hold_points=["达到控制标高前完成必需支撑安装与验收"],
            source_calculation_case_id=case.id,
            source_stage_id=row.id,
            stage_type=row.stage_type,
            zone=row.zone,
            data_status="approved" if row.data_status == "verified" else "draft",
        )
        stages.append(stage)
        previous = float(row.excavation_elevation)
    project.design_control_stages = stages
    project.advanced_engineering["designControlStageMigration"] = {
        "schema": "pitguard-design-control-stage-migration-v1",
        "sourceCaseId": case.id,
        "stageCount": len(stages),
        "migratedAt": _now(),
        "boundary": "迁移结果表示设计计算控制工况，不表示施工单位计划或现场实际状态。",
    }
    return {"migrated": True, "sourceCaseId": case.id, "stageCount": len(stages)}




def _support_levels(project: Project) -> tuple[dict[int, list[Any]], dict[str, Any]]:
    supports = list(project.retaining_system.supports if project.retaining_system else [])
    by_level: dict[int, list[Any]] = {}
    by_id: dict[str, Any] = {}
    for support in supports:
        level = int(support.level_index)
        by_level.setdefault(level, []).append(support)
        by_id[support.id] = support
    for rows in by_level.values():
        rows.sort(key=lambda row: (str(row.code), str(row.id)))
    return by_level, by_id


def _find_source_stage(project: Project, stage: DesignControlStage) -> tuple[CalculationCase | None, ConstructionStage | None, int | None]:
    preferred: list[CalculationCase] = []
    fallback: list[CalculationCase] = []
    for case in project.calculation_cases:
        if stage.source_calculation_case_id and case.id == stage.source_calculation_case_id:
            preferred.append(case)
        else:
            fallback.append(case)
    for case in [*preferred, *fallback]:
        for index, row in enumerate(case.stages):
            if stage.source_stage_id and row.id == stage.source_stage_id:
                return case, row, index
        for index, row in enumerate(case.stages):
            if row.name == stage.name and row.stage_type == stage.stage_type:
                return case, row, index
    return None, None, None


def _stage_level_hint(stage: DesignControlStage, source: ConstructionStage | None = None) -> int | None:
    values = [
        str(stage.zone or ""),
        str(stage.name or ""),
        str(getattr(source, "zone", "") or ""),
        str(getattr(source, "name", "") or ""),
    ]
    for value in values:
        match = re.search(r"(?:replace|remove)-L(\d+)", value, re.IGNORECASE)
        if not match:
            match = re.search(r"第\s*(\d+)\s*道", value)
        if match:
            return int(match.group(1))
    return None


def _standard_replacement_path_available(project: Project) -> bool:
    path = list(project.retaining_system.replacement_path if project.retaining_system else [])
    actions = {str(row.get("action") or "") for row in path if isinstance(row, dict)}
    if "replace_from_lowest_level" in actions and (
        "bottom_slab_cast" in actions or "final_support_removal" in actions
    ):
        return True

    # Early V3.87 workspaces could lose retainingSystem.replacementPath during
    # compaction while preserving software-generated replace-Ln stages. Treat
    # that semantic sequence as a legacy declaration only when every transfer
    # stage remains software-managed and non-frozen. This recovers identity
    # bindings without inventing a user-owned or specialist transfer decision.
    transfer = [
        row for row in (project.design_control_stages or [])
        if row.stage_type in {"replacement", "support_removal"}
    ]
    return bool(transfer) and all(_auto_managed_transfer_stage(row) for row in transfer) and all(
        row.permitted_inactive_support_levels or _stage_level_hint(row) is not None
        for row in transfer
    )


def _auto_managed_transfer_stage(stage: DesignControlStage) -> bool:
    return bool(
        stage.source_calculation_case_id
        or stage.source_stage_id
        or stage.transfer_path_source
        or re.search(r"(?:replace|remove)-L\d+", str(stage.zone or ""), re.IGNORECASE)
        or "换撑拆除" in str(stage.name or "")
    ) and stage.data_status != "frozen"


def _rebuild_standard_transfer_stages(project: Project) -> dict[str, Any]:
    """Rebuild the canonical bottom-up replacement sequence for current IDs.

    This is permitted only for software-managed, non-frozen stages and the
    standard replacement path already declared on the retaining system. The
    operation changes identity bindings, not the load/water/excavation basis.
    """
    stages = list(project.design_control_stages or [])
    transfer = [row for row in stages if row.stage_type in {"replacement", "support_removal"}]
    if not transfer or not _standard_replacement_path_available(project):
        return {"changed": False, "reason": "standard replacement path unavailable"}
    if not all(_auto_managed_transfer_stage(row) for row in transfer):
        return {"changed": False, "reason": "transfer stages are frozen or user-owned"}
    by_level, _by_id = _support_levels(project)
    levels = sorted(by_level)
    if not levels or project.excavation is None:
        return {"changed": False, "reason": "current support levels or excavation are missing"}

    non_transfer = [row for row in stages if row.stage_type not in {"replacement", "support_removal"}]
    template = transfer[0]
    bottom = float(project.excavation.bottom_elevation)
    remaining = set(levels)
    rebuilt: list[DesignControlStage] = []
    for level in sorted(levels, reverse=True):
        remaining.discard(level)
        required_levels = sorted(remaining)
        required_ids = [support.id for item in required_levels for support in by_level[item]]
        inactive_ids = [support.id for support in by_level[level]]
        rebuilt.append(
            DesignControlStage(
                name=f"换撑拆除：永久结构达到条件后拆除第 {level} 道水平支撑",
                design_basis_revision_id=template.design_basis_revision_id,
                excavation_elevation_lower=bottom,
                excavation_elevation_upper=bottom,
                required_support_ids=required_ids,
                permitted_inactive_support_ids=inactive_ids,
                required_support_levels=required_levels,
                permitted_inactive_support_levels=[level],
                replacement_action=(
                    template.replacement_action
                    or "地下室楼板或换撑构件达到设计强度、连接完成并经传力验收后，自下而上分级拆除支撑"
                ),
                transfer_path_status="mapped",
                transfer_path_source="standard_bottom_up_path_rebuilt_for_current_topology",
                groundwater_level_limit=template.groundwater_level_limit,
                surcharge_limit=template.surcharge_limit,
                preload_target=template.preload_target,
                preload_lower=template.preload_lower,
                preload_upper=template.preload_upper,
                overexcavation_limit=template.overexcavation_limit,
                stiffness_reduction_limit=template.stiffness_reduction_limit,
                hold_points=list(dict.fromkeys([
                    *list(template.hold_points or []),
                    "永久结构达到设计强度、连接完成并经传力验收后方可拆撑",
                ])),
                stage_type="replacement",
                zone=f"replace-L{level}",
                revision=max(1, int(template.revision or 1)) + 1,
                data_status="approved" if all(row.data_status == "approved" for row in transfer) else "draft",
                value_type=template.value_type,
            )
        )
    project.design_control_stages = non_transfer + rebuilt
    return {
        "changed": True,
        "reason": "standard bottom-up transfer sequence rebuilt",
        "removedStageCount": len(transfer),
        "rebuiltStageCount": len(rebuilt),
        "supportLevels": levels,
    }


def repair_design_control_support_references(
    project: Project,
    *,
    allow_standard_transfer_rebuild: bool = True,
) -> dict[str, Any]:
    """Repair stale support IDs using persistent support-level semantics.

    V3.87.7 resolves the former false dead-end where a topology regeneration
    invalidated only support IDs in otherwise unchanged replacement stages.
    Exact source-stage level metadata is preferred. A canonical bottom-up
    transfer sequence may be rebuilt only when the project already declares
    that standard path and the affected stages are software-managed/non-frozen.
    """
    stages = list(project.design_control_stages or [])
    by_level, support_by_id = _support_levels(project)
    supports = [support for rows in by_level.values() for support in rows]
    if not stages or not supports:
        return {
            "changed": False,
            "manualRequired": False,
            "stageCount": len(stages),
            "reason": "missing design-control stages or current supports",
            "details": [],
            "manualItems": [],
        }

    valid_ids = set(support_by_id)
    ordered_supports = sorted(
        supports,
        key=lambda row: (-float(row.elevation), int(getattr(row, "level_index", 0)), str(row.id)),
    )
    details: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    changed = False
    tolerance = 1.0e-5

    for stage in stages:
        stale_required = sorted(set(stage.required_support_ids) - valid_ids)
        stale_inactive = sorted(set(stage.permitted_inactive_support_ids) - valid_ids)
        semantic_missing = (
            stage.stage_type in {"replacement", "support_removal"}
            and not stage.required_support_levels
            and not stage.permitted_inactive_support_levels
        )
        if not stale_required and not stale_inactive and not semantic_missing:
            continue
        before_required = list(stage.required_support_ids)
        before_inactive = list(stage.permitted_inactive_support_ids)
        before_required_levels = list(stage.required_support_levels)
        before_inactive_levels = list(stage.permitted_inactive_support_levels)

        if stage.stage_type in {"replacement", "support_removal"}:
            source_case, source_stage, source_index = _find_source_stage(project, stage)
            required_levels = sorted({int(value) for value in stage.required_support_levels})
            inactive_levels = sorted({int(value) for value in stage.permitted_inactive_support_levels})
            method = "stored_design_control_levels" if required_levels or inactive_levels else None
            confidence = "high" if method else "unknown"

            if source_stage is not None:
                if not required_levels and source_stage.active_support_levels:
                    required_levels = sorted({int(value) for value in source_stage.active_support_levels})
                if not inactive_levels:
                    source_by_id = {
                        support.id: support
                        for support in (project.retaining_system.supports if project.retaining_system else [])
                    }
                    inactive_levels = sorted({
                        int(source_by_id[item].level_index)
                        for item in source_stage.deactivated_support_ids
                        if item in source_by_id
                    })
                if not inactive_levels:
                    hint = _stage_level_hint(stage, source_stage)
                    if hint is not None:
                        inactive_levels = [hint]
                if not inactive_levels and source_stage.transferred_support_levels:
                    current = {int(value) for value in source_stage.transferred_support_levels}
                    previous: set[int] = set()
                    if source_case is not None and source_index is not None and source_index > 0:
                        previous = {
                            int(value)
                            for value in source_case.stages[source_index - 1].transferred_support_levels
                        }
                    delta = sorted(current - previous)
                    inactive_levels = delta or ([max(current)] if current else [])
                method = "source_calculation_stage_levels"
                confidence = "high"

            if not inactive_levels:
                hint = _stage_level_hint(stage, source_stage)
                if hint is not None:
                    inactive_levels = [hint]
                    method = "stage_zone_or_name_level"
                    confidence = "medium"

            current_levels = set(by_level)
            missing_levels = sorted((set(required_levels) | set(inactive_levels)) - current_levels)
            can_infer_active = bool(inactive_levels) and _standard_replacement_path_available(project)
            if not required_levels and can_infer_active:
                # Standard path removes supports from the lowest level upward.
                # At remove-Lk, all current levels above k remain active.
                remove_level = min(inactive_levels)
                required_levels = sorted(level for level in current_levels if level < remove_level)
                method = method or "standard_bottom_up_sequence"
                confidence = "medium"

            safe_medium = (
                confidence == "medium"
                and stage.data_status != "frozen"
                and _standard_replacement_path_available(project)
                and project.excavation is not None
                and abs(float(stage.excavation_elevation_lower) - float(project.excavation.bottom_elevation)) <= 0.75
            )
            safe = confidence == "high" or safe_medium
            if safe and not missing_levels and inactive_levels:
                remapped_required = [support.id for level in required_levels for support in by_level[level]]
                remapped_inactive = [support.id for level in inactive_levels for support in by_level[level]]
                if set(remapped_required) & set(remapped_inactive):
                    safe = False
                else:
                    stage.required_support_ids = list(dict.fromkeys(remapped_required))
                    stage.permitted_inactive_support_ids = list(dict.fromkeys(remapped_inactive))
                    stage.required_support_levels = required_levels
                    stage.permitted_inactive_support_levels = inactive_levels
                    stage.transfer_path_status = "mapped"
                    stage.transfer_path_source = method
                    if not stage.replacement_action:
                        stage.replacement_action = (
                            getattr(source_stage, "replacement_action", None)
                            or "永久结构达到设计强度、连接完成并经传力验收后分级拆除支撑"
                        )
                    stage.revision = max(1, int(stage.revision or 1)) + 1
                    stage.updated_at = _now()
                    changed = True
                    details.append({
                        "stageId": stage.id,
                        "stageName": stage.name,
                        "stageType": stage.stage_type,
                        "repairKind": "transfer_path_semantic_remap",
                        "method": method,
                        "confidence": confidence,
                        "beforeRequiredCount": len(before_required),
                        "afterRequiredCount": len(stage.required_support_ids),
                        "beforeInactiveCount": len(before_inactive),
                        "afterInactiveCount": len(stage.permitted_inactive_support_ids),
                        "requiredSupportLevels": required_levels,
                        "inactiveSupportLevels": inactive_levels,
                        "removedStaleRequiredCount": len(stale_required),
                        "removedStaleInactiveCount": len(stale_inactive),
                    })
                    continue

            manual.append({
                "stageId": stage.id,
                "stageName": stage.name,
                "stageType": stage.stage_type,
                "staleRequiredCount": len(stale_required),
                "staleInactiveCount": len(stale_inactive),
                "requiredSupportLevels": required_levels,
                "inactiveSupportLevels": inactive_levels,
                "missingCurrentLevels": missing_levels,
                "dataStatus": stage.data_status,
                "reasonCode": (
                    "TRANSFER_STAGE_FROZEN" if stage.data_status == "frozen"
                    else "TRANSFER_LEVEL_SEMANTICS_MISSING" if not inactive_levels
                    else "TRANSFER_LEVEL_NOT_IN_CURRENT_TOPOLOGY" if missing_levels
                    else "TRANSFER_PATH_AMBIGUOUS"
                ),
                "message": "换撑/拆撑阶段无法在不改变设计意图的前提下映射到当前支撑层级。",
                "action": "确认该阶段退出的支撑层及永久结构生效条件，或采用系统推荐的标准自下而上换撑序列。",
            })
            stage.transfer_path_status = "manual_review"
            continue

        excavation_lower = float(stage.excavation_elevation_lower)
        if stage.stage_type in {"bottom_slab", "final"}:
            remapped_required = [row.id for row in ordered_supports]
        else:
            remapped_required = [
                row.id for row in ordered_supports
                if float(row.elevation) >= excavation_lower - tolerance
            ]
        stage.required_support_ids = list(dict.fromkeys(remapped_required))
        stage.permitted_inactive_support_ids = [
            item for item in dict.fromkeys(before_inactive) if item in valid_ids
        ]
        stage.required_support_levels = sorted({
            int(support_by_id[item].level_index)
            for item in stage.required_support_ids
            if item in support_by_id
        })
        stage.permitted_inactive_support_levels = sorted({
            int(support_by_id[item].level_index)
            for item in stage.permitted_inactive_support_ids
            if item in support_by_id
        })
        stage.revision = max(1, int(stage.revision or 1)) + 1
        stage.updated_at = _now()
        changed = True
        details.append({
            "stageId": stage.id,
            "stageName": stage.name,
            "stageType": stage.stage_type,
            "repairKind": "elevation_semantic_remap",
            "excavationElevationLower": excavation_lower,
            "beforeRequiredCount": len(before_required),
            "afterRequiredCount": len(stage.required_support_ids),
            "removedStaleRequiredCount": len(stale_required),
            "removedStaleInactiveCount": len(stale_inactive),
            "beforeRequiredLevels": before_required_levels,
            "afterRequiredLevels": stage.required_support_levels,
            "beforeInactiveLevels": before_inactive_levels,
            "afterInactiveLevels": stage.permitted_inactive_support_levels,
        })

    rebuild: dict[str, Any] | None = None
    transfer_stages = [
        row for row in (project.design_control_stages or [])
        if row.stage_type in {"replacement", "support_removal"}
    ]
    covered_transfer_levels = {
        int(level)
        for row in transfer_stages
        for level in row.permitted_inactive_support_levels
    }
    topology_level_mismatch = bool(
        transfer_stages
        and set(by_level) != covered_transfer_levels
        and _standard_replacement_path_available(project)
        and all(_auto_managed_transfer_stage(row) for row in transfer_stages)
    )
    if allow_standard_transfer_rebuild and (manual or topology_level_mismatch):
        rebuild = _rebuild_standard_transfer_stages(project)
        if rebuild.get("changed"):
            changed = True
            details.append({
                "repairKind": "standard_transfer_sequence_rebuild",
                "topologyLevelMismatch": topology_level_mismatch,
                "coveredTransferLevelsBefore": sorted(covered_transfer_levels),
                **rebuild,
            })
            manual = []

    if changed or manual:
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["designControlSupportReferenceRepair"] = {
            "schema": "pitguard-design-control-support-reference-repair-v2",
            "changed": changed,
            "manualRequired": bool(manual),
            "automaticStageCount": len([row for row in details if row.get("stageId")]),
            "automaticTransferStageCount": len([
                row for row in details if row.get("repairKind") == "transfer_path_semantic_remap"
            ]),
            "transferSequenceRebuilt": bool(rebuild and rebuild.get("changed")),
            "manualStageCount": len(manual),
            "currentSupportCount": len(supports),
            "currentSupportLevels": sorted(by_level),
            "details": details,
            "manualItems": manual,
            "updatedAt": _now(),
            "boundary": (
                "优先按持久化支撑层级语义重绑构件ID；仅对已声明的标准自下而上换撑路径和非冻结软件管理阶段重建序列。"
            ),
        }
    return {
        "changed": changed,
        "manualRequired": bool(manual),
        "stageCount": len(project.design_control_stages or []),
        "automaticStageCount": len([row for row in details if row.get("stageId")]),
        "automaticTransferStageCount": len([
            row for row in details if row.get("repairKind") == "transfer_path_semantic_remap"
        ]),
        "transferSequenceRebuilt": bool(rebuild and rebuild.get("changed")),
        "manualStageCount": len(manual),
        "details": details,
        "manualItems": manual,
    }

def validate_design_control_stages(project: Project) -> dict[str, Any]:
    stages = list(project.design_control_stages or [])
    issues: list[dict[str, Any]] = []
    valid_support_ids = {row.id for row in (project.retaining_system.supports if project.retaining_system else [])}
    previous_lower: float | None = None
    for stage in stages:
        if stage.excavation_elevation_lower > stage.excavation_elevation_upper:
            issues.append({"code": "DCS_ELEVATION_RANGE_INVALID", "stageId": stage.id, "severity": "fail", "message": "开挖控制标高下限高于上限。"})
        unknown = sorted(set(stage.required_support_ids) - valid_support_ids)
        if unknown:
            issues.append({"code": "DCS_SUPPORT_NOT_CURRENT", "stageId": stage.id, "severity": "fail", "message": f"存在 {len(unknown)} 根不属于当前方案的必需支撑。", "objects": unknown})
        if stage.preload_lower is not None and stage.preload_upper is not None and stage.preload_lower > stage.preload_upper:
            issues.append({"code": "DCS_PRELOAD_RANGE_INVALID", "stageId": stage.id, "severity": "fail", "message": "预加轴力允许下限高于上限。"})
        if previous_lower is not None and stage.excavation_elevation_lower > previous_lower + 1e-6:
            issues.append({"code": "DCS_STAGE_ORDER", "stageId": stage.id, "severity": "warning", "message": "后续工况开挖控制标高高于前一工况，请确认顺序。"})
        previous_lower = stage.excavation_elevation_lower
    fail_count = sum(row["severity"] == "fail" for row in issues)
    warning_count = sum(row["severity"] == "warning" for row in issues)
    return {
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "valid": bool(stages) and fail_count == 0,
        "stageCount": len(stages),
        "failCount": fail_count,
        "warningCount": warning_count,
        "issues": issues,
        "semanticType": "design_control_stage",
    }


def synchronize_design_control_case(project: Project) -> tuple[CalculationCase | None, dict[str, Any]]:
    """Synchronize numerical ConstructionStage records from design controls."""
    if not project.design_control_stages:
        migrate_legacy_stages(project)
    validation = validate_design_control_stages(project)
    if not validation["valid"]:
        return None, {"synchronized": False, "validation": validation}
    topology = support_topology_hash(project) if project.retaining_system else None
    support_by_id = {
        support.id: support
        for support in (project.retaining_system.supports if project.retaining_system else [])
    }
    stages: list[ConstructionStage] = []
    transferred_levels_seen: set[int] = set()
    for stage in project.design_control_stages:
        excavation_elevation = float(stage.excavation_elevation_lower)
        active_levels = sorted({
            *[int(value) for value in stage.required_support_levels],
            *[
                int(support_by_id[item].level_index)
                for item in stage.required_support_ids
                if item in support_by_id
            ],
        })
        inactive_levels = sorted({
            *[int(value) for value in stage.permitted_inactive_support_levels],
            *[
                int(support_by_id[item].level_index)
                for item in stage.permitted_inactive_support_ids
                if item in support_by_id
            ],
        })
        if stage.stage_type in {"replacement", "support_removal"}:
            transferred_levels_seen.update(inactive_levels)
        stages.append(
            ConstructionStage(
                id=stage.source_stage_id or stage.id,
                name=stage.name,
                excavation_elevation=excavation_elevation,
                active_support_ids=list(stage.required_support_ids),
                deactivated_support_ids=list(stage.permitted_inactive_support_ids),
                active_support_levels=active_levels,
                transferred_support_levels=sorted(transferred_levels_seen),
                support_topology_hash=topology,
                stage_type=stage.stage_type,
                zone=stage.zone,
                replacement_action=stage.replacement_action,
                groundwater_level_inside=stage.groundwater_level_limit,
                groundwater_level_outside=project.design_settings.groundwater_level,
                surcharge=float(stage.surcharge_limit if stage.surcharge_limit is not None else project.design_settings.surcharge),
                approved_by="design_control_stage",
                approved_at=stage.updated_at,
                data_status="verified" if stage.data_status in {"approved", "frozen"} else "draft",
            )
        )
    existing = next((row for row in project.calculation_cases if row.source == "synchronized" and row.name == "设计控制工况计算"), None)
    revision = int(existing.revision + 1) if existing else 1
    case = CalculationCase(
        id=existing.id if existing else None,
        name="设计控制工况计算",
        stages=stages,
        support_topology_hash=topology,
        synchronization_note="由 V3.78 设计控制工况同步；不包含施工计划日期或现场实测状态。",
        source="synchronized",
        locked=True,
        revision=revision,
        created_at=existing.created_at if existing else _now(),
        updated_at=_now(),
    ) if existing else CalculationCase(
        name="设计控制工况计算",
        stages=stages,
        support_topology_hash=topology,
        synchronization_note="由 V3.78 设计控制工况同步；不包含施工计划日期或现场实测状态。",
        source="synchronized",
        locked=True,
        revision=revision,
    )
    project.calculation_cases = [row for row in project.calculation_cases if row.id != case.id and not (row.source == "synchronized" and row.name == case.name)] + [case]
    return case, {"synchronized": True, "caseId": case.id, "stageCount": len(stages), "validation": validation}


def generate_design_scenarios(project: Project, *, replace_auto: bool = True) -> dict[str, Any]:
    if not project.design_control_stages:
        migrate_legacy_stages(project)
    existing_user = [row for row in project.design_scenarios if row.source == "user_defined"]
    existing_auto = [] if replace_auto else [row for row in project.design_scenarios if row.source == "auto_generated"]
    scenarios: list[DesignScenario] = list(existing_auto)
    settings = project.design_settings
    for stage in project.design_control_stages:
        base_overrides = {
            "excavationElevation": stage.excavation_elevation_lower,
            "activeSupportIds": list(stage.required_support_ids),
            "groundwaterLevel": stage.groundwater_level_limit,
            "surcharge": stage.surcharge_limit,
        }
        rows = [
            ("BASELINE", "基准设计工况", "baseline", {}, ["按设计推荐支撑激活和控制标高"]),
            ("DELAYED_SUPPORT", "支撑安装滞后", "support_delay", {"delayedSupportCount": 1}, ["最不利一组拟安装支撑延迟生效"]),
            ("LOCAL_OVEREXCAVATION", "局部超挖", "overexcavation", {"overexcavationDepthM": stage.overexcavation_limit or settings.overexcavation_depth_m}, ["局部开挖低于设计控制标高"]),
            ("PRELOAD_LOW", "预加轴力不足", "preload", {"preloadFactor": 0.8}, ["预加轴力取目标值的 80%"]),
            ("PRELOAD_HIGH", "预加轴力偏高", "preload", {"preloadFactor": 1.1}, ["预加轴力取目标值的 110%"]),
            ("HIGH_GROUNDWATER", "高水位", "groundwater", {"groundwaterRiseM": settings.dewatering_failure_rise_m}, ["水位升至设计控制上限或批准不利值"]),
            ("TEMP_SURCHARGE", "临时施工超载", "surcharge", {"surchargeFactor": 1.25}, ["坑边施工荷载按设计限值的 1.25 倍筛查"]),
            ("SUPPORT_STIFFNESS_REDUCTION", "支撑刚度折减", "stiffness", {"supportStiffnessFactor": stage.stiffness_reduction_limit or 0.85}, ["考虑连接间隙、安装偏差和温度效应"]),
            ("CRITICAL_SUPPORT_DEGRADATION", "关键支撑异常", "member_anomaly", {"criticalSupportStiffnessFactor": 0.5}, ["关键支撑刚度降低至 50% 的鲁棒性筛查"]),
        ]
        stage_ids: list[str] = []
        for suffix, label, category, override, assumptions in rows:
            scenario = DesignScenario(
                code=f"{stage.id}:{suffix}",
                name=f"{stage.name} · {label}",
                stage_id=stage.id,
                category=category,
                parameter_overrides={**base_overrides, **override},
                assumptions=assumptions,
                source="auto_generated",
                approval_status="approved" if category == "baseline" else "draft",
            )
            scenarios.append(scenario)
            stage_ids.append(scenario.id)
        stage.design_scenario_ids = stage_ids
        stage.updated_at = _now()
    project.design_scenarios = scenarios + existing_user
    project.advanced_engineering["designScenarioSuite"] = {
        "schema": "pitguard-design-scenario-suite-v1",
        "scenarioCount": len(project.design_scenarios),
        "stageCount": len(project.design_control_stages),
        "generatedAt": _now(),
        "boundary": "自动情景用于设计包络和鲁棒性筛查；项目批准范围优先于软件建议范围。",
    }
    return project.advanced_engineering["designScenarioSuite"]


def set_design_scenario_approval(
    project: Project,
    scenario_ids: list[str],
    *,
    approval_status: str = "approved",
    enabled: bool | None = None,
) -> dict[str, Any]:
    if approval_status not in {"draft", "approved", "rejected"}:
        raise ValueError("Unsupported design scenario approval status")
    selected = set(scenario_ids)
    updated: list[str] = []
    for scenario in project.design_scenarios:
        if scenario.id not in selected:
            continue
        scenario.approval_status = approval_status
        if enabled is not None:
            scenario.enabled = bool(enabled)
        updated.append(scenario.id)
    missing = sorted(selected - set(updated))
    result = {
        "schema": "pitguard-design-scenario-approval-v1",
        "approvalStatus": approval_status,
        "enabled": enabled,
        "updatedScenarioIds": updated,
        "missingScenarioIds": missing,
        "approvedCount": sum(row.enabled and row.approval_status == "approved" for row in project.design_scenarios),
        "updatedAt": _now(),
    }
    project.advanced_engineering["designScenarioApproval"] = result
    return result


ScenarioProgress = Callable[[int, str], None]


def _design_scenario_hash(project: Project, scenario: DesignScenario, assumptions: dict[str, Any]) -> str:
    payload = {
        "projectId": project.id,
        "scenarioId": scenario.id,
        "scenarioCode": scenario.code,
        "stageId": scenario.stage_id,
        "overrides": scenario.parameter_overrides,
        "assumptions": assumptions,
        "supportTopologyHash": support_topology_hash(project) if project.retaining_system else None,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _scenario_execution_seed(project: Project) -> Project:
    """Bound a formal scenario clone without mutating the designer's project."""
    seed = project.model_copy(deep=True)
    seed.calculation_results = []
    if seed.retaining_system is not None:
        repair = seed.retaining_system.support_layout_repair
        if repair is not None:
            repair.candidates = []
            repair.candidate_full_calculations = []
        seed.retaining_system.rebar_design_scheme = None
    keep_advanced: dict[str, Any] = {}
    for key in (
        "calibrationFactors",
        "designControlStageMigration",
        "designScenarioSuite",
        "designScenarioApproval",
        "detailGeometryPatches",
        "detailingOverrides",
    ):
        if key in (seed.advanced_engineering or {}):
            keep_advanced[key] = deepcopy(seed.advanced_engineering[key])
    seed.advanced_engineering = keep_advanced
    return seed


def apply_design_scenario(project: Project, scenario: DesignScenario) -> tuple[CalculationCase, dict[str, Any]]:
    """Apply one approved V3.79 scenario to a disposable project clone.

    The operation mutates only the supplied clone. Stage-local changes remain
    stage-local where the current solver supports them. Support-stiffness
    reduction is represented through the existing global calibration factor and
    is explicitly reported as a conservative whole-system approximation.
    """
    case, sync = synchronize_design_control_case(project)
    if case is None:
        raise ValueError(f"设计控制工况无法同步：{sync.get('validation')}")
    design = next((row for row in project.design_control_stages if row.id == scenario.stage_id), None)
    if design is None:
        raise ValueError(f"情景未绑定当前设计控制工况：{scenario.code}")
    stage_index = next((index for index, row in enumerate(case.stages) if row.id in {design.source_stage_id, design.id}), None)
    if stage_index is None:
        raise ValueError(f"计算案例中未找到情景控制工况：{scenario.code}")
    stage = case.stages[stage_index]
    overrides = dict(scenario.parameter_overrides or {})
    assumptions: dict[str, Any] = {
        "scenarioId": scenario.id,
        "scenarioCode": scenario.code,
        "category": scenario.category,
        "designControlStageId": design.id,
        "calculationStageId": stage.id,
        "parameterOverrides": overrides,
    }
    active_ids = list(stage.active_support_ids)
    support_by_id = {row.id: row for row in (project.retaining_system.supports if project.retaining_system else [])}

    if scenario.category == "baseline":
        assumptions["method"] = "synchronized design-control baseline"
    elif scenario.category == "support_delay":
        previous = set(case.stages[stage_index - 1].active_support_ids) if stage_index > 0 else set()
        newly_activated = sorted(set(active_ids) - previous)
        candidates = newly_activated or sorted(active_ids)
        count = max(1, min(int(overrides.get("delayedSupportCount") or 1), len(candidates))) if candidates else 0
        delayed = candidates[-count:] if count else []
        stage.active_support_ids = [item for item in active_ids if item not in set(delayed)]
        stage.deactivated_support_ids = sorted(set(stage.deactivated_support_ids) | set(delayed))
        assumptions.update({"method": "stage-local support activation delay", "delayedSupportIds": delayed})
    elif scenario.category == "overexcavation":
        depth = max(0.0, float(overrides.get("overexcavationDepthM") or design.overexcavation_limit or 0.0))
        before = float(stage.excavation_elevation)
        stage.excavation_elevation = before - depth
        if project.excavation and stage_index == len(case.stages) - 1:
            original_bottom = float(project.excavation.bottom_elevation)
            project.excavation.bottom_elevation = min(original_bottom, stage.excavation_elevation)
            project.excavation.depth = float(project.excavation.top_elevation - project.excavation.bottom_elevation)
            assumptions["projectBottomElevationBefore"] = original_bottom
            assumptions["projectBottomElevationAfter"] = project.excavation.bottom_elevation
        assumptions.update({"method": "stage-local excavation elevation perturbation", "excavationElevationBefore": before, "excavationElevationAfter": stage.excavation_elevation, "overexcavationDepthM": depth})
    elif scenario.category == "preload":
        factor = max(0.0, float(overrides.get("preloadFactor") or 1.0))
        changed: dict[str, float] = {}
        for support_id in active_ids:
            support = support_by_id.get(support_id)
            if support is None:
                continue
            base = support.preload if support.preload is not None else design.preload_target
            if base is not None:
                support.preload = float(base) * factor
                changed[support_id] = support.preload
            if support.preload_ratio is not None:
                support.preload_ratio = float(support.preload_ratio) * factor
        assumptions.update({"method": "active-support preload perturbation", "preloadFactor": factor, "changedSupportPreloads": changed})
    elif scenario.category == "groundwater":
        rise = max(0.0, float(overrides.get("groundwaterRiseM") or 0.0))
        outside = float(project.design_settings.groundwater_level)
        base = stage.groundwater_level_inside
        if base is None:
            base = design.groundwater_level_limit
        if base is None:
            base = project.design_settings.groundwater_level_inside
        if base is None:
            base = float(stage.excavation_elevation) - 0.5
        stage.groundwater_level_inside = min(outside, float(base) + rise)
        assumptions.update({"method": "stage-local inside-water-level rise", "groundwaterLevelBefore": base, "groundwaterLevelAfter": stage.groundwater_level_inside, "riseM": rise})
    elif scenario.category == "surcharge":
        factor = max(0.0, float(overrides.get("surchargeFactor") or 1.0))
        before = float(stage.surcharge)
        stage.surcharge = before * factor
        assumptions.update({"method": "stage-local surcharge perturbation", "surchargeBefore": before, "surchargeAfter": stage.surcharge, "surchargeFactor": factor})
    elif scenario.category == "stiffness":
        factor = max(0.05, min(2.0, float(overrides.get("supportStiffnessFactor") or 1.0)))
        calibration = dict(project.advanced_engineering.get("calibrationFactors") or {})
        before = float(calibration.get("supportStiffnessFactor") or 1.0)
        calibration["supportStiffnessFactor"] = before * factor
        project.advanced_engineering["calibrationFactors"] = calibration
        assumptions.update({"method": "whole-system support stiffness calibration factor", "supportStiffnessFactorBefore": before, "supportStiffnessFactorAfter": calibration["supportStiffnessFactor"], "scope": "global_conservative_proxy"})
    elif scenario.category == "member_anomaly":
        factor = max(0.05, min(1.0, float(overrides.get("criticalSupportStiffnessFactor") or 0.5)))
        candidates = [support_by_id[item] for item in active_ids if item in support_by_id]
        if not candidates:
            raise ValueError(f"情景 {scenario.code} 没有可降刚度的活动支撑。")
        support = max(candidates, key=lambda row: float(row.span_length or 0.0))
        default_e = 32_500_000.0 if support.section_type == "rc_rectangular" else 200_000_000.0
        before = float(support.material.elastic_modulus or default_e)
        support.material.elastic_modulus = before * factor
        assumptions.update({"method": "critical-member elastic-modulus degradation", "criticalSupportId": support.id, "elasticModulusBefore": before, "elasticModulusAfter": support.material.elastic_modulus, "stiffnessFactor": factor})
    else:
        raise ValueError(f"当前计算内核不支持情景类型：{scenario.category}")

    assumptions["scenarioInputHash"] = _design_scenario_hash(project, scenario, assumptions)
    project.advanced_engineering["activeDesignScenario"] = assumptions
    return case, assumptions


def _compact_design_scenario_result(scenario: DesignScenario, result: Any, assumptions: dict[str, Any]) -> dict[str, Any]:
    stability = result.stability_detailed_result
    governing = result.governing_values
    return {
        "scenarioId": scenario.id,
        "scenarioCode": scenario.code,
        "scenarioName": scenario.name,
        "stageId": scenario.stage_id,
        "category": scenario.category,
        "status": "fail" if int((result.check_summary or {}).get("fail", 0) or 0) else "warning" if int((result.check_summary or {}).get("warning", 0) or 0) else "pass",
        "calculationResultId": result.id,
        "caseId": result.case_id,
        "scenarioInputHash": assumptions.get("scenarioInputHash"),
        "assumptions": assumptions,
        "maxWallDisplacement": getattr(governing, "max_displacement", None),
        "maxSupportAxialForce": getattr(governing, "max_support_axial_force", None),
        "maxWallMoment": getattr(governing, "max_wall_moment", None),
        "maxWallShear": getattr(governing, "max_wall_shear", None),
        "minSafetyFactor": getattr(stability, "min_safety_factor", None) if stability else None,
        "checkSummary": dict(result.check_summary or {}),
        "calculatedAt": result.calculated_at,
        "evidenceLevel": "formal_design_scenario_rerun",
    }


def run_design_scenario_suite(
    project: Project,
    scenario_ids: list[str] | None = None,
    *,
    max_scenarios: int = 12,
    progress: ScenarioProgress | None = None,
) -> dict[str, Any]:
    """Run approved V3.79 scenarios on isolated project clones."""
    from app.calculation.engine import run_calculation

    selected_ids = set(scenario_ids or [])
    selected = [
        row for row in project.design_scenarios
        if row.enabled and row.approval_status == "approved" and row.category != "baseline"
        and (not selected_ids or row.id in selected_ids)
    ]
    if selected_ids:
        missing = sorted(selected_ids - {row.id for row in selected})
        if missing:
            raise ValueError(f"部分情景不存在、未启用或未批准：{', '.join(missing[:8])}")
    limit = max(1, min(int(max_scenarios), 50))
    skipped = selected[limit:]
    selected = selected[:limit]
    if not selected:
        raise ValueError("没有已批准且启用的非基准设计情景。")

    seed = _scenario_execution_seed(project)
    summaries: list[dict[str, Any]] = []
    full_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total = len(selected)
    for index, selected_scenario in enumerate(selected, start=1):
        if progress:
            progress(int(8 + (index - 1) / max(total, 1) * 78), f"设计包络正式复算 {index}/{total}：{selected_scenario.name}")
        trial = seed.model_copy(deep=True)
        scenario = next(row for row in trial.design_scenarios if row.id == selected_scenario.id)
        try:
            case, assumptions = apply_design_scenario(trial, scenario)
            result = run_calculation(trial, case, auto_repair=False, include_candidate_comparison=False)
            summary = _compact_design_scenario_result(scenario, result, assumptions)
            summaries.append(summary)
            full_results.append({
                "scenarioId": scenario.id,
                "scenarioCode": scenario.code,
                "assumptions": assumptions,
                "calculationResult": result.model_dump(mode="json", by_alias=True),
            })
        except Exception as exc:
            errors.append({
                "scenarioId": selected_scenario.id,
                "scenarioCode": selected_scenario.code,
                "scenarioName": selected_scenario.name,
                "status": "fail",
                "error": str(exc),
                "evidenceLevel": "formal_design_scenario_rerun_failed",
            })
        finally:
            del trial
            gc.collect()
    del seed
    gc.collect()
    return {
        "schema": "pitguard-design-scenario-execution-v1",
        "method": "isolated staged rerun for each approved design scenario",
        "requestedScenarioIds": [row.id for row in selected],
        "skippedScenarioIds": [row.id for row in skipped],
        "summaries": summaries,
        "errors": errors,
        "fullResults": full_results,
        "summary": {
            "requestedCount": len(selected),
            "completedCount": len(summaries),
            "failedExecutionCount": len(errors),
            "skippedByLimitCount": len(skipped),
            "failCount": sum(row.get("status") == "fail" for row in summaries),
            "warningCount": sum(row.get("status") == "warning" for row in summaries),
        },
        "boundary": "每个情景均在隔离项目副本中重新运行施工阶段计算；全局支撑刚度折减使用当前内核的校准因子表达，并在假定中显式标记。",
    }


def build_scenario_envelope(project: Project) -> dict[str, Any]:
    """Build a transparent envelope ledger from available formal/scenario results.

    The service does not fabricate member results. If formal scenario reruns are
    unavailable it returns a readiness ledger and explicitly marks the envelope
    as pending.
    """
    advanced = dict(project.advanced_engineering or {})
    design_suite = dict(advanced.get("designScenarioExecution") or {})
    legacy_suite = dict(advanced.get("formalAdverseScenarioSuite") or {})
    raw_rows = (
        list(design_suite.get("summaries") or design_suite.get("scenarios") or [])
        + list(legacy_suite.get("summaries") or legacy_suite.get("scenarios") or [])
    )
    formal_rows = [row for row in raw_rows if isinstance(row, dict)]
    latest = project.calculation_results[-1] if project.calculation_results else None
    baseline = {
        "maxWallDisplacement": getattr(latest.governing_values, "max_displacement", None) if latest else None,
        "maxSupportAxialForce": getattr(latest.governing_values, "max_support_axial_force", None) if latest else None,
        "maxWallMoment": getattr(latest.governing_values, "max_wall_moment", None) if latest else None,
        "minSafetyFactor": (latest.stability_detailed_result.min_safety_factor if latest and latest.stability_detailed_result else None),
    }
    candidates: list[dict[str, Any]] = []
    if latest:
        candidates.append({"scenarioCode": "BASELINE", "source": "latest_calculation", **baseline})
    for row in formal_rows:
        result = dict(row.get("resultSummary") or row.get("summary") or {})
        merged = {**row, **result}
        candidates.append({
            "scenarioCode": merged.get("scenarioCode") or merged.get("code"),
            "source": merged.get("evidenceLevel") or merged.get("source") or "formal_scenario_rerun",
            "maxWallDisplacement": merged.get("maxWallDisplacement") if merged.get("maxWallDisplacement") is not None else merged.get("maxWallDisplacementMm") if merged.get("maxWallDisplacementMm") is not None else merged.get("maxDisplacement"),
            "maxSupportAxialForce": merged.get("maxSupportAxialForce") if merged.get("maxSupportAxialForce") is not None else merged.get("maxSupportForceKn") if merged.get("maxSupportForceKn") is not None else merged.get("maxSupportForce"),
            "maxWallMoment": merged.get("maxWallMoment") if merged.get("maxWallMoment") is not None else merged.get("maxWallMomentKnM"),
            "minSafetyFactor": merged.get("minSafetyFactor") if merged.get("minSafetyFactor") is not None else merged.get("minimumSafetyFactor"),
        })
    def control(key: str, direction: str = "max") -> dict[str, Any] | None:
        valid = [row for row in candidates if row.get(key) is not None]
        if not valid:
            return None
        fn = max if direction == "max" else min
        value = fn(float(row[key]) for row in valid)
        row = next(row for row in valid if abs(float(row[key]) - value) <= 1e-12)
        return {"value": value, "controllingScenarioCode": row.get("scenarioCode"), "source": row.get("source")}
    envelope = {
        "maxWallDisplacement": control("maxWallDisplacement"),
        "maxSupportAxialForce": control("maxSupportAxialForce"),
        "maxWallMoment": control("maxWallMoment"),
        "minSafetyFactor": control("minSafetyFactor", "min"),
    }
    approved_scenarios = [row for row in project.design_scenarios if row.enabled and row.approval_status == "approved"]
    formal_codes = {
        str(row.get("scenarioCode") or row.get("code"))
        for row in formal_rows
        if row.get("status") in {"pass", "warning", "success", "fail"}
    }
    pending = [row.code for row in approved_scenarios if row.category != "baseline" and row.code not in formal_codes]
    result = {
        "schema": "pitguard-design-envelope-v1",
        "status": "pass" if candidates and not pending else "warning" if candidates else "missing",
        "candidateResultCount": len(candidates),
        "approvedScenarioCount": len(approved_scenarios),
        "pendingFormalScenarioCodes": pending,
        "envelope": envelope,
        "generatedAt": _now(),
        "boundary": "只对真实完成的基准计算和正式情景复算取包络；未计算情景不会以放大系数伪造构件结果。",
    }
    project.advanced_engineering["designScenarioEnvelope"] = result
    return result


def evaluate_construction_plan_stage(project: Project, plan: ConstructionPlanStage) -> dict[str, Any]:
    design = next((row for row in project.design_control_stages if row.id == plan.design_control_stage_id), None)
    if design is None:
        return {"status": "fail", "grade": "E", "withinDesignEnvelope": False, "issues": [{"code": "PLAN_STAGE_UNBOUND", "severity": "critical", "message": "施工计划没有绑定当前设计控制工况。"}]}
    issues: list[dict[str, Any]] = []
    grade_rank = 0
    def add(code: str, severity: str, message: str, rank: int, **extra: Any) -> None:
        nonlocal grade_rank
        grade_rank = max(grade_rank, rank)
        issues.append({"code": code, "severity": severity, "message": message, **extra})
    if plan.planned_excavation_elevation is not None:
        value = float(plan.planned_excavation_elevation)
        tolerance = float(design.overexcavation_limit or 0.0)
        if value < float(design.excavation_elevation_lower) - tolerance:
            add("PLAN_EXCAVATION_PROHIBITED", "critical", "计划开挖标高超出设计允许域。", 4, designLimit=design.excavation_elevation_lower, plannedValue=value)
        elif value < float(design.excavation_elevation_lower):
            add("PLAN_EXCAVATION_RECALC", "major", "计划存在允许超挖范围内的偏差，需要增量复算确认。", 2, designLimit=design.excavation_elevation_lower, plannedValue=value)
        elif tolerance > 0 and value > float(design.excavation_elevation_lower) + 1.0e-6 and value <= float(design.excavation_elevation_lower) + 0.1 * tolerance:
            add("PLAN_EXCAVATION_NEAR_LIMIT", "warning", "计划开挖标高接近设计控制边界。", 1, designLimit=design.excavation_elevation_lower, plannedValue=value)
        elif value > float(design.excavation_elevation_upper) + 1e-6:
            add("PLAN_STAGE_MISMATCH", "warning", "计划标高高于当前控制工况范围，可能对应其他阶段。", 1, plannedValue=value)
    current_support_ids = {row.id for row in (project.retaining_system.supports if project.retaining_system else [])}
    unknown_supports = sorted(set(plan.planned_support_ids) - current_support_ids)
    if unknown_supports:
        add("PLAN_SUPPORT_SUBSTITUTION", "major", "计划包含当前设计方案之外的替代支撑，改变主要传力体系。", 3, affectedMemberIds=unknown_supports)
    missing_supports = sorted(set(design.required_support_ids) - set(plan.planned_support_ids))
    if missing_supports:
        add("PLAN_REQUIRED_SUPPORT_MISSING", "critical", f"计划缺少 {len(missing_supports)} 根设计要求的支撑。", 4, affectedMemberIds=missing_supports)
    for support_id, value in plan.planned_preloads.items():
        if design.preload_lower is not None and float(value) < float(design.preload_lower):
            add("PLAN_PRELOAD_LOW", "major", "计划预加轴力低于设计允许下限。", 2, affectedMemberIds=[support_id], plannedValue=value, designLimit=design.preload_lower)
        if design.preload_upper is not None and float(value) > float(design.preload_upper):
            add("PLAN_PRELOAD_HIGH", "major", "计划预加轴力高于设计允许上限。", 2, affectedMemberIds=[support_id], plannedValue=value, designLimit=design.preload_upper)
    if plan.planned_groundwater_level is not None and design.groundwater_level_limit is not None:
        if float(plan.planned_groundwater_level) > float(design.groundwater_level_limit):
            add("PLAN_WATER_LEVEL_HIGH", "major", "计划控制水位高于设计上限。", 2, plannedValue=plan.planned_groundwater_level, designLimit=design.groundwater_level_limit)
        elif abs(float(plan.planned_groundwater_level) - float(design.groundwater_level_limit)) <= 0.2:
            add("PLAN_WATER_LEVEL_NEAR_LIMIT", "warning", "计划控制水位接近设计上限。", 1, plannedValue=plan.planned_groundwater_level, designLimit=design.groundwater_level_limit)
    if plan.planned_surcharge is not None and design.surcharge_limit is not None:
        ratio = float(plan.planned_surcharge) / max(float(design.surcharge_limit), 1e-9)
        if ratio > 1.0:
            add("PLAN_SURCHARGE_HIGH", "critical" if ratio > 1.25 else "major", "计划坑边荷载高于设计上限。", 4 if ratio > 1.25 else 2, plannedValue=plan.planned_surcharge, designLimit=design.surcharge_limit)
        elif ratio >= 0.9:
            add("PLAN_SURCHARGE_NEAR_LIMIT", "warning", "计划坑边荷载接近设计上限。", 1, plannedValue=plan.planned_surcharge, designLimit=design.surcharge_limit)
    grades = ["A", "B", "C", "D", "E"]
    grade = grades[min(grade_rank, 4)]
    status = "fail" if grade in {"D", "E"} else "warning" if grade in {"B", "C"} else "pass"
    return {
        "schema": "pitguard-construction-plan-compliance-v1",
        "status": status,
        "grade": grade,
        "withinDesignEnvelope": grade in {"A", "B"},
        "requiresDesignerReview": grade in {"C", "D", "E"},
        "requiresRecalculation": grade in {"C", "D"},
        "prohibited": grade == "E",
        "issueCount": len(issues),
        "issues": issues,
        "responsibility": "施工单位提交计划；设计单位仅处理超出设计允许域的事项。",
    }


def assess_field_snapshot(project: Project, snapshot: FieldExecutionSnapshot, *, persist: bool = True) -> dict[str, Any]:
    plan = next((row for row in project.construction_plan_stages if row.id == snapshot.construction_plan_stage_id), None)
    if plan is None:
        return {"status": "fail", "withinPlan": False, "withinDesignEnvelope": False, "events": [], "message": "现场快照未绑定施工计划阶段。"}
    design = next((row for row in project.design_control_stages if row.id == plan.design_control_stage_id), None)
    if design is None:
        return {"status": "fail", "withinPlan": False, "withinDesignEnvelope": False, "events": [], "message": "施工计划未绑定当前设计控制工况。"}
    events: list[DeviationEvent] = []
    def event(kind: str, *, design_limit: float | None, planned: float | None, observed: float | None, severity: str, hold: bool, recalc: bool, response: bool, member_ids: list[str] | None = None) -> None:
        events.append(DeviationEvent(
            deviation_type=kind,
            design_control_stage_id=design.id,
            construction_plan_stage_id=plan.id,
            field_snapshot_id=snapshot.id,
            affected_stage_ids=[design.id],
            affected_member_ids=member_ids or [],
            design_limit=design_limit,
            planned_value=planned,
            observed_value=observed,
            severity=severity,
            work_hold_required=hold,
            recalculation_required=recalc,
            designer_response_required=response,
            responsible_party="construction_unit" if kind != "monitoring_alarm" else "monitoring_unit",
        ))
    if snapshot.actual_excavation_elevation is not None:
        observed = float(snapshot.actual_excavation_elevation)
        planned = float(plan.planned_excavation_elevation) if plan.planned_excavation_elevation is not None else None
        if planned is not None and abs(observed - planned) > 0.05:
            event("excavation_vs_plan", design_limit=design.excavation_elevation_lower, planned=planned, observed=observed, severity="warning", hold=False, recalc=False, response=False)
        hard_lower = float(design.excavation_elevation_lower) - float(design.overexcavation_limit or 0.0)
        if observed < hard_lower:
            event("excavation_outside_design", design_limit=hard_lower, planned=planned, observed=observed, severity="critical", hold=True, recalc=True, response=True)
        elif observed < float(design.excavation_elevation_lower):
            event("overexcavation_within_tolerance", design_limit=design.excavation_elevation_lower, planned=planned, observed=observed, severity="major", hold=True, recalc=True, response=True)
    missing = sorted(set(design.required_support_ids) - set(snapshot.active_support_ids))
    if missing:
        event("required_support_not_active", design_limit=None, planned=None, observed=None, severity="critical", hold=True, recalc=True, response=True, member_ids=missing)
    for support_id, observed in snapshot.measured_preloads.items():
        planned = plan.planned_preloads.get(support_id)
        if design.preload_lower is not None and float(observed) < float(design.preload_lower):
            event("preload_below_design", design_limit=design.preload_lower, planned=planned, observed=observed, severity="major", hold=True, recalc=True, response=True, member_ids=[support_id])
        if design.preload_upper is not None and float(observed) > float(design.preload_upper):
            event("preload_above_design", design_limit=design.preload_upper, planned=planned, observed=observed, severity="major", hold=True, recalc=True, response=True, member_ids=[support_id])
    for _point, observed in snapshot.groundwater_levels.items():
        if design.groundwater_level_limit is not None and float(observed) > float(design.groundwater_level_limit):
            event("groundwater_above_design", design_limit=design.groundwater_level_limit, planned=plan.planned_groundwater_level, observed=observed, severity="critical", hold=True, recalc=True, response=True)
    if persist:
        # Reassessment of the same immutable field snapshot replaces its prior
        # generated events, preventing duplicate alarms after retries.
        project.deviation_events = [row for row in project.deviation_events if row.field_snapshot_id != snapshot.id]
        project.deviation_events.extend(events)
    within_design = not any(row.severity in {"major", "critical"} for row in events)
    within_plan = not any(row.deviation_type.endswith("vs_plan") for row in events)
    return {
        "schema": "pitguard-field-deviation-assessment-v1",
        "status": "fail" if any(row.severity == "critical" for row in events) else "warning" if events else "pass",
        "withinPlan": within_plan,
        "withinDesignEnvelope": within_design,
        "designerReviewRequired": any(row.designer_response_required for row in events),
        "recalculationRequired": any(row.recalculation_required for row in events),
        "workHoldRecommended": any(row.work_hold_required for row in events),
        "events": [row.model_dump(mode="json", by_alias=True) for row in events],
    }


def _latest_calculation_status(project: Project) -> tuple[Any, dict[str, Any]]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest is None:
        return None, {"current": False, "reason": "missing"}
    try:
        contract = verify_current_calculation_contract(project, latest)
    except Exception as exc:  # keep workflow reporting resilient
        contract = {"current": False, "reason": str(exc)}
    return latest, contract


def evaluate_design_issue_gate(project: Project) -> dict[str, Any]:
    if not project.design_control_stages:
        migrate_legacy_stages(project)
    validation = validate_design_control_stages(project)
    latest, contract = _latest_calculation_status(project)
    review = review_status(project)
    items: list[dict[str, Any]] = []
    items.append(_item("DESIGN_BASIS", "设计基准已确认", "pass" if project.design_settings.design_basis_confirmed else "fail", responsibility="设计单位", affects="设计发行", action="确认项目等级、规范、荷载组合和控制参数。", blocking=not project.design_settings.design_basis_confirmed))
    geo_ok = bool(project.boreholes and project.strata and project.geological_model)
    items.append(_item("DESIGN_SOURCE_DATA", "地质、水文地质和周边资料可用于设计", "pass" if geo_ok else "fail", responsibility="建设单位/勘察单位提供，设计单位核验适用性", affects="设计计算与设计发行", action="补充并核验钻孔、地层、水位和周边环境资料。", blocking=not geo_ok))
    geometry_ok = bool(project.excavation and project.retaining_system)
    items.append(_item("DESIGN_GEOMETRY", "基坑轮廓与围护体系完整", "pass" if geometry_ok else "fail", responsibility="设计单位", affects="设计计算", action="完成闭合轮廓、围护墙和支撑方案。", blocking=not geometry_ok))
    items.append(_item("DESIGN_CONTROL_STAGES", "设计控制工况有效", validation["status"], responsibility="设计单位", affects="设计计算", action="修复开挖控制标高、支撑激活和水位/荷载边界。", blocking=not validation["valid"], detail=validation))
    controls_approved = bool(project.design_control_stages) and all(row.data_status in {"approved", "frozen"} for row in project.design_control_stages)
    items.append(_item("DESIGN_CONTROL_APPROVAL", "设计控制工况已批准或冻结", "pass" if controls_approved else "fail", responsibility="设计单位", affects="设计计算与设计发行", action="完成设计控制工况校核，并将其状态更新为已批准或已冻结。", blocking=not controls_approved))
    calc_ok = bool(latest and int((latest.check_summary or {}).get("fail", 0) or 0) == 0 and contract.get("current"))
    items.append(_item("DESIGN_CALCULATION", "当前设计计算无硬性失败且合同有效", "pass" if calc_ok else "fail", responsibility="设计单位", affects="设计发行", action="按当前设计控制工况重新计算并关闭 fail。", blocking=not calc_ok, detail=contract))
    review_ok = bool(review.get("approvalValid") and review.get("registeredStructuralApproverValid"))
    items.append(_item("DESIGN_REVIEW", "设计校审与批准完成", "pass" if review_ok else "warning", responsibility="设计/校核/审核/批准人员", affects="正式设计发行", action="完成设计、校核、审核和批准。", blocking=not review_ok, detail=review))
    control_requirements_ok = bool(project.design_control_stages and all(row.hold_points for row in project.design_control_stages))
    items.append(_item("DESIGN_CONTROL_REQUIREMENTS", "施工控制条件已由设计明确", "pass" if control_requirements_ok else "warning", responsibility="设计单位", affects="设计发行与施工方案编制", action="为各控制工况补充支撑、水位、荷载和进入下一工况条件。", blocking=not control_requirements_ok))
    blockers = [row for row in items if row["blocking"]]
    readiness = round(100.0 * sum(row["status"] == "pass" for row in items) / max(len(items), 1), 1)
    return {
        "schema": "pitguard-design-issue-gate-v1",
        "status": "fail" if blockers else "warning" if any(row["status"] == "warning" for row in items) else "pass",
        "eligible": not blockers,
        "readiness": readiness,
        "items": items,
        "blockingCodes": [row["code"] for row in blockers],
        "explicitExclusions": ["专项施工方案", "专家论证", "实际开挖状态", "实测预加轴力", "现场验收", "施工期监测实测值"],
        "boundary": "设计发行仅由设计责任范围内的资料、计算、控制条件和校审决定。",
    }


def _workflow_evidence(project: Project) -> dict[str, Any]:
    return dict((project.advanced_engineering or {}).get("statutoryWorkflowEvidence") or {})


def evaluate_construction_preparation_gate(project: Project) -> dict[str, Any]:
    design_gate = evaluate_design_issue_gate(project)
    evidence = _workflow_evidence(project)
    plans = list(project.construction_plan_stages or [])
    compliance = [evaluate_construction_plan_stage(project, row) for row in plans]
    hazard = str(project.design_settings.hazardous_work_classification or "unclassified")
    hazardous = hazard in {"hazardous", "large_scale_hazardous"}
    large = hazard == "large_scale_hazardous"
    def verified(code: str) -> bool:
        row = evidence.get(code) or {}
        return bool(row.get("status") == "verified" and row.get("artifactCurrent"))
    items = [
        _item("DESIGN_ISSUED", "设计文件满足发行条件", "pass" if design_gate["eligible"] else "fail", responsibility="设计单位", affects="施工准备", action="完成设计发行门禁。", blocking=not design_gate["eligible"]),
        _item("CONSTRUCTION_PLAN", "施工计划阶段已提交并与设计控制工况绑定", "pass" if plans else "fail", responsibility="施工单位", affects="施工准备", action="提交施工计划阶段和专项方案版本。", blocking=not plans),
        _item("CONSTRUCTION_PLAN_APPROVAL", "施工计划阶段已批准", "pass" if plans and all(row.approval_status == "approved" for row in plans) else "fail", responsibility="施工单位/监理单位", affects="施工准备", action="完成施工计划与专项方案审批后再申请现场阶段放行。", blocking=not plans or not all(row.approval_status == "approved" for row in plans)),
        _item("PLAN_COMPLIANCE", "施工计划未违反设计允许域", "pass" if compliance and all(row["grade"] in {"A", "B"} for row in compliance) else "fail" if any(row["grade"] in {"D", "E"} for row in compliance) else "warning", responsibility="施工单位提交，设计单位处理 C/D/E 级偏差", affects="施工准备", action="B 级可带提示进入审批；C 级完成增量复算后方可放行；D/E 级调整计划或发起设计变更。", blocking=not compliance or any(row["grade"] in {"C", "D", "E"} for row in compliance), detail=compliance),
    ]
    requirements = [
        ("special_construction_plan", "专项施工方案", hazardous, "施工单位"),
        ("supervision_review", "总监理工程师审查", hazardous, "监理单位"),
        ("expert_review_report", "专家论证及意见闭环", large and project.design_settings.require_expert_review_for_large_hazard, "施工单位组织/专家组"),
        ("monitoring_plan", "监测方案", hazardous, "建设单位委托监测单位"),
        ("emergency_plan", "应急预案", hazardous, "施工单位"),
    ]
    for code, label, required, party in requirements:
        ok = verified(code)
        status = "pass" if ok else "fail" if required else "not_applicable"
        items.append(_item(code.upper(), label, status, responsibility=party, affects="施工准备", action=f"由{party}提交并完成审批。", blocking=required and not ok))
    blockers = [row for row in items if row["blocking"]]
    return {
        "schema": "pitguard-construction-preparation-gate-v1",
        "status": "fail" if blockers else "warning" if any(row["status"] == "warning" for row in items) else "pass",
        "eligible": not blockers,
        "readiness": round(100.0 * sum(row["status"] in {"pass", "not_applicable"} for row in items) / max(len(items), 1), 1),
        "items": items,
        "blockingCodes": [row["code"] for row in blockers],
        "boundary": "施工准备状态不会反向作废已发行设计；超出设计允许域时触发设计复核或变更。",
    }


def evaluate_field_release_gate(project: Project, construction_plan_stage_id: str | None = None) -> dict[str, Any]:
    construction_gate = evaluate_construction_preparation_gate(project)
    plans = [row for row in project.construction_plan_stages if construction_plan_stage_id is None or row.id == construction_plan_stage_id]
    plan = plans[-1] if plans else None
    snapshots = [row for row in project.field_execution_snapshots if plan and row.construction_plan_stage_id == plan.id]
    snapshot = snapshots[-1] if snapshots else None
    assessment = assess_field_snapshot(project, snapshot, persist=False) if snapshot else None
    snapshot_verified = bool(snapshot and snapshot.quality == "verified")
    evidence = _workflow_evidence(project)
    acceptance = evidence.get("stage_acceptance") or {}
    acceptance_ok = bool(acceptance.get("status") == "verified" and acceptance.get("artifactCurrent"))
    items = [
        _item("CONSTRUCTION_PREPARATION", "施工准备门禁通过", "pass" if construction_gate["eligible"] else "fail", responsibility="施工/监理/建设相关责任方", affects="现场阶段放行", action="完成施工准备条件。", blocking=not construction_gate["eligible"]),
        _item("FIELD_SNAPSHOT", "当前现场状态已由责任方提交", "pass" if snapshot else "fail", responsibility="施工单位/监理单位", affects="现场阶段放行", action="提交实际开挖、支撑、水位和强度状态。", blocking=not snapshot),
        _item("FIELD_SNAPSHOT_QUALITY", "现场快照已核验", "pass" if snapshot_verified else "fail", responsibility="施工单位/监理单位", affects="现场阶段放行", action="临时或被拒绝的数据只能用于预警，核验后方可作为阶段放行证据。", blocking=not snapshot_verified),
        _item("FIELD_WITHIN_DESIGN", "现场状态位于设计允许域", "pass" if assessment and assessment["withinDesignEnvelope"] else "fail", responsibility="施工单位执行，设计单位处理超域偏差", affects="下一阶段放行", action="超域时暂停相关工序并发起复核。", blocking=not assessment or not assessment["withinDesignEnvelope"], detail=assessment),
        _item("STAGE_ACCEPTANCE", "阶段验收记录完成", "pass" if acceptance_ok else "fail", responsibility="施工单位/监理单位", affects="下一道工序", action="完成危大工程阶段验收及签字。", blocking=not acceptance_ok),
    ]
    blockers = [row for row in items if row["blocking"]]
    return {
        "schema": "pitguard-field-stage-release-gate-v1",
        "status": "fail" if blockers else "pass",
        "eligible": not blockers,
        "readiness": round(100.0 * sum(row["status"] == "pass" for row in items) / max(len(items), 1), 1),
        "items": items,
        "blockingCodes": [row["code"] for row in blockers],
        "planStageId": plan.id if plan else None,
        "fieldSnapshotId": snapshot.id if snapshot else None,
        "boundary": "现场数据只影响施工阶段放行和偏差处置，不作为设计文件首次发行的前置条件。",
    }


def workflow_overview(project: Project) -> dict[str, Any]:
    if not project.design_control_stages:
        migrate_legacy_stages(project)
    design = evaluate_design_issue_gate(project)
    construction = evaluate_construction_preparation_gate(project)
    field = evaluate_field_release_gate(project)
    open_deviations = [row for row in project.deviation_events if row.status not in {"accepted", "closed"}]
    result = {
        "schema": "pitguard-business-workflow-v381",
        "designIssue": design,
        "constructionPreparation": construction,
        "fieldExecution": field,
        "counts": {
            "designControlStages": len(project.design_control_stages),
            "designScenarios": len(project.design_scenarios),
            "constructionPlanStages": len(project.construction_plan_stages),
            "fieldSnapshots": len(project.field_execution_snapshots),
            "openDeviationEvents": len(open_deviations),
        },
        "responsibilityBoundary": [
            {"domain": "design", "owner": "设计单位", "output": "设计控制工况、允许域、计算、校审和设计成果"},
            {"domain": "construction", "owner": "施工/监理/专家等责任方", "output": "专项方案、施工计划及施工准备审批"},
            {"domain": "field", "owner": "施工/监理/监测单位", "output": "现场状态、监测、验收和偏差证据"},
        ],
        "evaluatedAt": _now(),
    }
    project.advanced_engineering["businessWorkflowV381"] = result
    return result
