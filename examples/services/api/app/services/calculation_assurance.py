from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from app.geometry.consistency import geometry_consistency_summary
from app.schemas.domain import CalculationCase, CalculationResult, Project
from app.services.support_topology_contract import support_topology_hash
from app.version import ALGORITHM_VERSION, EXPORT_SCHEMA_VERSION, RULE_SET_VERSION, SOFTWARE_VERSION


_REQUIRED_CHECK_FIELDS = ("ruleId", "objectId", "status", "message", "clauseReference")

_DERIVED_RETAINING_KEYS = {
    "designResults", "designResult", "internalForceResults", "reinforcement",
    "bearingPlate", "checkStatus", "designNote", "foundationDesign",
    "effectiveAxialForceStandard", "designAxialForce", "rawAxialForceStandardEnvelope",
    "forceReconciliationStatus", "forceReconciliationNote", "thermalAxialForce",
    "gapClosureForce", "eccentricityMoment", "constructionEffectNote",
    "sectionOptimizationStatus", "sectionOptimizationNote", "lifecycleNote",
    "preloadProtocolStatus", "supportLayoutRepair", "rebarDesignScheme",
    "layoutSummary", "warnings", "designAxialForce", "designMoment", "designShear",
}


def _strip_derived_retaining(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_derived_retaining(item) for key, item in value.items() if key not in _DERIVED_RETAINING_KEYS}
    if isinstance(value, list):
        return [_strip_derived_retaining(item) for item in value]
    return value



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _solver_runtime_manifest() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "numpy": np.__version__,
        "system": platform.system(),
        "machine": platform.machine(),
        "floatBits": 64,
        "numericThreads": {
            key: os.environ.get(key)
            for key in ("PITGUARD_NUMERIC_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
            if os.environ.get(key) is not None
        },
        "byteOrder": sys.byteorder,
    }


def _final_contract_id(input_contract_id: str, adopted_design_hash: str, runtime: dict[str, Any]) -> str:
    return f"calc-contract-{_canonical_hash({
        'inputContractId': input_contract_id,
        'adoptedDesignSnapshotHash': adopted_design_hash,
        'algorithmVersion': ALGORITHM_VERSION,
        'ruleSetVersion': RULE_SET_VERSION,
        'solverRuntime': runtime,
    })[:20]}"


def _issue(
    code: str,
    title: str,
    status: str,
    message: str,
    *,
    evidence: Any = None,
    action: str = "",
    object_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "status": status,
        "message": message,
        "evidence": evidence,
        "requiredAction": action,
        "objectId": object_id,
    }


def _check_row(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "ruleId": f"PITGUARD-INDUSTRIAL-{issue['code']}",
        "objectId": issue.get("objectId") or "calculation-basis",
        "objectType": "IndustrialCalculationBasis",
        "status": issue["status"],
        "calculatedValue": issue.get("evidence"),
        "limitValue": None,
        "unit": "quality gate",
        "message": issue["message"] + ((" 建议：" + issue["requiredAction"]) if issue.get("requiredAction") else ""),
        "clauseReference": "PitGuard industrial calculation assurance gate; project code checks remain independent",
    }


_NON_CALCULATION_DESIGN_SETTING_KEYS = {
    # UI, monitoring and release-policy fields do not change structural demand,
    # stiffness, geometry or the construction-stage solver.  Keeping them out of
    # the immutable calculation snapshot prevents an export/display preference
    # from invalidating a technically current result.
    "autoCenterExcavationOnGeology",
    "defaultWorkspaceMode",
    "monitoringCalibrationEnabled",
    "monitoringThresholdSource",
    "monitoringWallDisplacementWarningMm",
    "monitoringWallDisplacementAlarmMm",
    "monitoringSettlementWarningMm",
    "monitoringSettlementAlarmMm",
    "monitoringSupportForceWarningRatio",
    "monitoringSupportForceAlarmRatio",
    "monitoringGroundwaterWarningOffsetM",
    "monitoringGroundwaterAlarmOffsetM",
    "monitoringProjectionHours",
    "requireFormalApprovalForConstruction",
    "reinforcementFullGeometryMaxBars",
    "reinforcementVisualizationDensityM",
    "rebarCageGridMaxLinesPerFace",
}


def _calculation_design_settings_payload(project: Project) -> dict[str, Any]:
    settings = project.design_settings.model_dump(mode="json", by_alias=True)
    return {
        key: value
        for key, value in settings.items()
        if key not in _NON_CALCULATION_DESIGN_SETTING_KEYS
    }


def calculation_input_payload(project: Project, case: CalculationCase) -> dict[str, Any]:
    """Build a deterministic, calculation-only snapshot.

    Review actions, exported files, monitoring history and old results are
    intentionally excluded.  Any field that can change loads, geometry,
    stiffness, construction sequence or rule selection remains included.
    """
    calibration = dict((project.advanced_engineering or {}).get("calibrationFactors") or {})
    return {
        "projectId": project.id,
        "unitSystem": project.unit_system.model_dump(mode="json", by_alias=True),
        "coordinateSystem": project.coordinate_system.model_dump(mode="json", by_alias=True),
        "designSettings": _calculation_design_settings_payload(project),
        "boreholes": [row.model_dump(mode="json", by_alias=True) for row in project.boreholes],
        "strata": [row.model_dump(mode="json", by_alias=True) for row in project.strata],
        "geologicalModel": project.geological_model.model_dump(mode="json", by_alias=True) if project.geological_model else None,
        "excavation": project.excavation.model_dump(mode="json", by_alias=True) if project.excavation else None,
        "retainingSystem": _strip_derived_retaining(project.retaining_system.model_dump(mode="json", by_alias=True)) if project.retaining_system else None,
        "calculationCase": case.model_dump(mode="json", by_alias=True),
        "calibrationFactors": calibration,
    }


def build_calculation_contract(project: Project, case: CalculationCase) -> dict[str, Any]:
    payload = calculation_input_payload(project, case)
    input_hash = _canonical_hash(payload)
    case_payload = case.model_dump(mode="json", by_alias=True)
    case_hash = _canonical_hash(case_payload)
    geometry = geometry_consistency_summary(project)
    geometry_hash = _canonical_hash({
        "excavation": payload.get("excavation"),
        "retainingSystem": payload.get("retainingSystem"),
        "geometryConsistency": geometry,
    })
    topology_hash = support_topology_hash(project) if project.retaining_system else None
    runtime = _solver_runtime_manifest()
    contract_seed = {
        "inputSnapshotHash": input_hash,
        "caseHash": case_hash,
        "geometryHash": geometry_hash,
        "supportTopologyHash": topology_hash,
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "solverRuntime": runtime,
    }
    input_contract_id = f"calc-contract-input-{_canonical_hash(contract_seed)[:20]}"
    return {
        "contractId": input_contract_id,
        "inputContractId": input_contract_id,
        **contract_seed,
        "stageCount": len(case.stages),
        "segmentCount": len(project.excavation.segments) if project.excavation else 0,
        "createdAt": _now(),
    }


def audit_calculation_inputs(project: Project, case: CalculationCase) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    unit = project.unit_system
    si_ready = unit.length == "m" and unit.force == "kN" and unit.stress == "kPa" and unit.angle == "degree"
    issues.append(_issue(
        "INPUT-UNITS",
        "计算量纲基线",
        "pass" if si_ready else "fail",
        "计算内核输入采用 m-kN-kPa-degree 工程单位。" if si_ready else "项目单位与计算内核的工程单位基线不一致，当前版本未证明全部输入已完成显式换算。",
        evidence=unit.model_dump(mode="json", by_alias=True),
        action="将项目切换到 m、kN、kPa、degree 后重新计算，或补充经测试的单位换算层。",
        object_id=project.id,
    ))

    excavation = project.excavation
    if excavation is None:
        issues.append(_issue("INPUT-EXCAVATION", "基坑几何", "fail", "缺少基坑开挖模型。", action="建立闭合开挖轮廓和开挖标高。", object_id=project.id))
    else:
        depth_valid = math.isfinite(excavation.depth) and excavation.depth > 0 and excavation.top_elevation > excavation.bottom_elevation
        closed = bool(excavation.outline.closed and len(excavation.outline.points) >= 3 and len(excavation.segments) >= 3)
        issues.append(_issue(
            "INPUT-EXCAVATION",
            "基坑几何",
            "pass" if depth_valid and closed else "fail",
            "基坑轮廓闭合且标高方向有效。" if depth_valid and closed else "基坑轮廓未闭合、边数不足或开挖标高方向无效。",
            evidence={"depthM": excavation.depth, "segmentCount": len(excavation.segments), "closed": excavation.outline.closed},
            action="修复轮廓闭合、重复点、短边和顶底标高后重建围护体系。",
            object_id=excavation.id,
        ))

    retaining = project.retaining_system
    if retaining is None:
        issues.append(_issue("INPUT-RETAINING", "围护体系", "fail", "缺少围护体系。", action="生成围护墙、围檩、支撑、立柱及施工槽段。", object_id=project.id))
    else:
        bad_walls = [wall.panel_code for wall in retaining.diaphragm_walls if wall.thickness <= 0 or wall.top_elevation <= wall.bottom_elevation or not wall.concrete_grade or not wall.rebar_grade]
        bad_supports = [support.code for support in retaining.supports if support.elevation is None or support.start == support.end or not support.section or not support.material]
        status = "fail" if bad_walls or bad_supports or not retaining.diaphragm_walls or not retaining.supports else "pass"
        issues.append(_issue(
            "INPUT-RETAINING",
            "围护构件参数",
            status,
            "围护墙、支撑和材料参数具备计算条件。" if status == "pass" else "围护体系存在零尺寸、无效标高、空材料或缺失主要构件。",
            evidence={"wallCount": len(retaining.diaphragm_walls), "supportCount": len(retaining.supports), "badWalls": bad_walls[:20], "badSupports": bad_supports[:20]},
            action="修复无效构件参数并重新生成施工阶段。",
            object_id=retaining.id,
        ))

    geology_ready = bool(project.strata and project.geological_model)
    low_confidence = [stratum.code for stratum in project.strata if str(stratum.confidence).lower() not in {"high", "verified", "measured"}]
    geology_status = "pass" if geology_ready and not low_confidence else ("warning" if geology_ready else "fail")
    issues.append(_issue(
        "INPUT-GEOLOGY",
        "地质与参数依据",
        geology_status,
        "地层与空间模型已建立。" if geology_ready else "缺少地层参数或地质空间模型。",
        evidence={"boreholeCount": len(project.boreholes), "stratumCount": len(project.strata), "lowConfidenceStrata": low_confidence[:20]},
        action="补齐钻孔、地下水、参数来源和低置信度区域的专项复核。",
        object_id=project.id,
    ))

    support_ids = {item.id for item in retaining.supports} if retaining else set()
    support_by_id = {item.id: item for item in retaining.supports} if retaining else {}
    stage_ids: set[str] = set()
    duplicate_ids: list[str] = []
    invalid_support_refs: dict[str, list[str]] = {}
    elevation_reversals: list[str] = []
    invalid_stage_values: list[str] = []
    active_deactivated_overlap: dict[str, list[str]] = {}
    premature_support_activation: dict[str, list[str]] = {}
    invalid_deactivation_sequence: dict[str, list[str]] = {}
    reactivated_supports: dict[str, list[str]] = {}
    support_level_mismatch: dict[str, dict[str, list[int]]] = {}
    previous_elevation: float | None = None
    previous_active: set[str] = set()
    historically_deactivated: set[str] = set()
    top = excavation.top_elevation if excavation else None
    bottom = excavation.bottom_elevation if excavation else None
    for stage in case.stages:
        if stage.id in stage_ids:
            duplicate_ids.append(stage.id)
        stage_ids.add(stage.id)
        active = set(stage.active_support_ids)
        deactivated = set(stage.deactivated_support_ids)
        refs = active | deactivated
        stale = sorted(refs - support_ids)
        if stale:
            invalid_support_refs[stage.id] = stale[:30]
        overlap = sorted(active & deactivated)
        if overlap:
            active_deactivated_overlap[stage.id] = overlap[:30]
        invalid_remove = sorted(deactivated - previous_active)
        if invalid_remove:
            invalid_deactivation_sequence[stage.id] = invalid_remove[:30]
        reactivated = sorted(active & historically_deactivated)
        if reactivated:
            reactivated_supports[stage.id] = reactivated[:30]
        elevation = float(stage.excavation_elevation)
        if not math.isfinite(elevation) or not math.isfinite(float(stage.surcharge)) or stage.surcharge < 0:
            invalid_stage_values.append(stage.id)
        for water_value in (stage.groundwater_level_inside, stage.groundwater_level_outside):
            if water_value is not None and not math.isfinite(float(water_value)):
                invalid_stage_values.append(stage.id)
        if top is not None and bottom is not None and not (bottom - 1e-6 <= elevation <= top + 1e-6):
            invalid_stage_values.append(stage.id)
        if previous_elevation is not None and elevation > previous_elevation + 1e-6:
            elevation_reversals.append(stage.id)
        premature = sorted(
            support_id for support_id in active
            if support_id in support_by_id and float(support_by_id[support_id].elevation) < elevation - 1e-6
        )
        if premature:
            premature_support_activation[stage.id] = premature[:30]
        derived_levels = sorted({int(support_by_id[support_id].level_index) for support_id in active if support_id in support_by_id})
        declared_levels = sorted({int(level) for level in stage.active_support_levels})
        if declared_levels != derived_levels:
            support_level_mismatch[stage.id] = {"declared": declared_levels, "derived": derived_levels}
        previous_elevation = elevation
        previous_active = active
        historically_deactivated.update(deactivated)
    stage_hard_fail = bool(
        not case.stages or duplicate_ids or invalid_support_refs or elevation_reversals or invalid_stage_values
        or active_deactivated_overlap or premature_support_activation or invalid_deactivation_sequence
        or reactivated_supports
    )
    stage_status = "fail" if stage_hard_fail else "warning" if support_level_mismatch else "pass"
    issues.append(_issue(
        "INPUT-STAGES",
        "施工阶段完整性",
        stage_status,
        "施工阶段标高单调、构件引用有效、激活时机合理且工况值有限。" if stage_status == "pass" else ("支撑层级索引与活动构件ID不一致，计算仍以构件ID为准并要求复核。" if stage_status == "warning" else "施工阶段存在重复编号、标高回跳、提前激活、错误拆撑、无效构件引用或异常荷载。"),
        evidence={
            "stageCount": len(case.stages),
            "duplicateStageIds": duplicate_ids,
            "invalidSupportReferences": invalid_support_refs,
            "elevationReversals": elevation_reversals,
            "invalidStageValues": sorted(set(invalid_stage_values)),
            "activeDeactivatedOverlap": active_deactivated_overlap,
            "prematureSupportActivation": premature_support_activation,
            "invalidDeactivationSequence": invalid_deactivation_sequence,
            "reactivatedSupports": reactivated_supports,
            "supportLevelMismatch": support_level_mismatch,
        },
        action="按开挖—支撑安装—换撑—拆撑顺序重建工况，并同步当前构件ID。",
        object_id=case.id,
    ))

    current_topology = support_topology_hash(project) if retaining else None
    stored_topology = case.support_topology_hash
    topology_current = bool(current_topology and stored_topology == current_topology)
    issues.append(_issue(
        "INPUT-TOPOLOGY-CONTRACT",
        "工况拓扑合同",
        "pass" if topology_current else "fail",
        "工况支撑拓扑哈希与当前模型一致。" if topology_current else "施工工况与当前支撑拓扑哈希不一致。",
        evidence={"caseTopologyHash": stored_topology, "currentTopologyHash": current_topology},
        action="同步施工工况后重新计算，禁止复用旧拓扑结果。",
        object_id=case.id,
    ))

    geometry = geometry_consistency_summary(project)
    geometry_pass = str(geometry.get("status")) == "pass" or bool(geometry.get("consistent"))
    issues.append(_issue(
        "INPUT-GEOMETRY-CONSISTENCY",
        "计算几何一致性",
        "pass" if geometry_pass else "fail",
        "计算、三维和图纸几何源一致。" if geometry_pass else "几何一致性检查未通过。",
        evidence=geometry,
        action="重新生成围护、支撑、施工槽段和图纸几何，禁止混用旧对象。",
        object_id=project.id,
    ))

    fail_count = sum(row["status"] == "fail" for row in issues)
    warning_count = sum(row["status"] in {"warning", "manual_review"} for row in issues)
    return {
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "failCount": fail_count,
        "warningCount": warning_count,
        "issues": issues,
        "checks": [_check_row(row) for row in issues],
        "auditedAt": _now(),
    }


def _finite_numbers(values: Iterable[Any]) -> bool:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return bool(numbers) and all(math.isfinite(value) for value in numbers)


def _relative_difference(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    a = abs(float(a))
    b = abs(float(b))
    denominator = max(a, b, 1e-9)
    return abs(a - b) / denominator


def assess_calculation_result(
    project: Project,
    case: CalculationCase,
    result: CalculationResult,
    *,
    input_audit: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_audit = input_audit or audit_calculation_inputs(project, case)
    contract = contract or build_calculation_contract(project, case)
    issues: list[dict[str, Any]] = []

    expected_pairs = {(stage.id, segment.id) for stage in case.stages for segment in (project.excavation.segments if project.excavation else [])}
    actual_pairs = {(row.stage_id, row.segment_id) for row in result.stage_results}
    missing_pairs = sorted(expected_pairs - actual_pairs)
    duplicate_count = len(result.stage_results) - len(actual_pairs)
    stage_complete = not missing_pairs and duplicate_count == 0 and bool(expected_pairs)
    issues.append(_issue(
        "RESULT-STAGE-COVERAGE",
        "阶段—墙段结果覆盖",
        "pass" if stage_complete else "fail",
        "每个施工阶段和开挖墙段均有唯一结果。" if stage_complete else "阶段结果缺失或重复，控制包络不完整。",
        evidence={"expectedCount": len(expected_pairs), "actualCount": len(result.stage_results), "missingPairs": missing_pairs[:30], "duplicateCount": duplicate_count},
        action="修复异常阶段并重新执行全量计算，禁止使用不完整包络。",
        object_id=case.id,
    ))

    condition_values: list[float] = []
    residual_values: list[float] = []
    original_residual_values: list[float] = []
    fallback_rows: list[dict[str, Any]] = []
    support_reconciliation_warning = 0
    support_reconciliation_manual = 0
    wall_cross_checks: list[dict[str, Any]] = []
    for row in result.stage_results:
        coupled = row.global_coupled_result
        if coupled:
            if coupled.condition_number is not None:
                condition_values.append(float(coupled.condition_number))
            numerical = dict(coupled.equilibrium_diagnostics or {})
            residual = numerical.get("relativeResidual")
            original = numerical.get("originalRelativeResidual")
            if isinstance(residual, (int, float)):
                residual_values.append(float(residual))
            if isinstance(original, (int, float)):
                original_residual_values.append(float(original))
            if coupled.fallback:
                fallback_rows.append({"stageId": row.stage_id, "segmentId": row.segment_id, "reason": coupled.reason})
            if row.wall_internal_force:
                # Global matrix translations are stored in metres, while the
                # wall-on-elastic-foundation solver reports millimetres.
                global_displacement_mm = float(coupled.max_wall_displacement or 0.0) * 1000.0
                wall_displacement_mm = float(row.wall_internal_force.max_displacement or 0.0)
                difference = _relative_difference(global_displacement_mm, wall_displacement_mm)
                if difference is not None:
                    wall_cross_checks.append({
                        "stageId": row.stage_id,
                        "segmentId": row.segment_id,
                        "globalDisplacementMm": global_displacement_mm,
                        "wallSolverDisplacementMm": wall_displacement_mm,
                        "relativeDifference": difference,
                    })
        for force in row.support_forces:
            status = str(force.force_reconciliation_status or "")
            support_reconciliation_warning += int(status == "warning")
            support_reconciliation_manual += int(status == "manual_review")

    max_condition = max(condition_values, default=None)
    max_residual = max(residual_values, default=None)
    max_original_residual = max(original_residual_values, default=None)
    condition_review_limit = float(getattr(project.design_settings, "maximum_matrix_condition_number", 1.0e12) or 1.0e12)
    condition_fail_limit = condition_review_limit * 100.0
    condition_warning_limit = condition_review_limit / 100.0
    residual_warning_limit = float(getattr(project.design_settings, "maximum_equilibrium_relative_residual", 1.0e-8) or 1.0e-8)
    residual_fail_limit = residual_warning_limit * 100.0
    numerical_status = "pass"
    if max_condition is None or max_residual is None:
        numerical_status = "manual_review"
    if max_condition is not None and max_condition > condition_fail_limit:
        numerical_status = "fail"
    elif max_condition is not None and max_condition > condition_review_limit and numerical_status != "fail":
        numerical_status = "manual_review"
    elif max_condition is not None and max_condition > condition_warning_limit and numerical_status == "pass":
        numerical_status = "warning"
    if max_residual is not None and max_residual > residual_fail_limit:
        numerical_status = "fail"
    elif max_residual is not None and max_residual > residual_warning_limit and numerical_status == "pass":
        numerical_status = "warning"
    if fallback_rows and numerical_status == "pass":
        numerical_status = "manual_review"
    issues.append(_issue(
        "RESULT-NUMERICAL-QUALITY",
        "数值收敛与病态控制",
        numerical_status,
        "矩阵条件数、平衡残差和求解路径已统一审计。",
        evidence={
            "maxConditionNumber": max_condition,
            "maxRelativeResidual": max_residual,
            "maxOriginalRelativeResidual": max_original_residual,
            "fallbackCount": len(fallback_rows),
            "fallbackRows": fallback_rows[:20],
            "conditionWarningLimit": condition_warning_limit,
            "conditionReviewLimit": condition_review_limit,
            "conditionFailLimit": condition_fail_limit,
            "residualWarningLimit": residual_warning_limit,
            "residualFailLimit": residual_fail_limit,
        },
        action="复核刚度尺度、约束、连接、正则化和荷载路径；严重病态或残差超限时结果不得用于设计。",
        object_id=result.id,
    ))

    finite_governing = _finite_numbers(result.governing_values.model_dump().values())
    finite_stages = all(
        _finite_numbers([
            row.pressure_profile.points[0].total_pressure if row.pressure_profile.points else 0.0,
            row.wall_internal_force.max_moment if row.wall_internal_force else 0.0,
            row.wall_internal_force.max_shear if row.wall_internal_force else 0.0,
            row.wall_internal_force.max_displacement if row.wall_internal_force else 0.0,
        ])
        for row in result.stage_results
    ) if result.stage_results else False
    issues.append(_issue(
        "RESULT-FINITE",
        "结果有限性",
        "pass" if finite_governing and finite_stages else "fail",
        "控制结果与阶段结果均为有限数值。" if finite_governing and finite_stages else "结果中存在空值、无穷值或非数值状态。",
        evidence={"governingFinite": finite_governing, "stageFinite": finite_stages},
        action="检查输入量纲、材料参数、边界条件和求解器异常。",
        object_id=result.id,
    ))

    max_wall_difference = max((row["relativeDifference"] for row in wall_cross_checks), default=0.0)
    warning_ratio = float(getattr(project.design_settings, "independent_check_warning_ratio", 0.25) or 0.25)
    fail_ratio = float(getattr(project.design_settings, "independent_check_fail_ratio", 0.50) or 0.50)
    assurance_level = str(getattr(project.design_settings, "calculation_assurance_level", "engineering") or "engineering")
    has_reference_path = bool(wall_cross_checks or any(row.support_forces for row in result.stage_results))
    cross_status = "pass"
    if bool(getattr(project.design_settings, "require_independent_calculation_check", True)) and not has_reference_path:
        cross_status = "fail"
    elif max_wall_difference > fail_ratio:
        # At engineering level a large difference is an explicit independent
        # review item.  It becomes a hard failure only when the project declares
        # that this run is intended for official issue.
        cross_status = "fail" if assurance_level == "official_issue" else "manual_review"
    elif max_wall_difference > warning_ratio or support_reconciliation_warning or support_reconciliation_manual:
        cross_status = "manual_review"
    issues.append(_issue(
        "RESULT-INDEPENDENT-CHECK",
        "独立计算路径复核",
        cross_status,
        "全局耦合解与墙体弹性地基梁、连续围檩参考解进行了差异对账。",
        evidence={
            "maxWallDisplacementRelativeDifference": round(max_wall_difference, 6),
            "wallCrossChecks": sorted(wall_cross_checks, key=lambda item: item["relativeDifference"], reverse=True)[:20],
            "supportReconciliationWarningCount": support_reconciliation_warning,
            "supportReconciliationManualReviewCount": support_reconciliation_manual,
            "warningRatio": warning_ratio,
            "failRatio": fail_ratio,
            "assuranceLevel": assurance_level,
        },
        action="差异过大时复核荷载分带、墙体支点、围檩连续性、支撑刚度和单位换算。",
        object_id=result.id,
    ))

    trace_rows = list(result.checks or [])
    complete_trace = 0
    missing_trace: list[dict[str, Any]] = []
    for check in trace_rows:
        missing = [field for field in _REQUIRED_CHECK_FIELDS if not check.get(field)]
        if not missing:
            complete_trace += 1
        elif len(missing_trace) < 30:
            missing_trace.append({"ruleId": check.get("ruleId"), "objectId": check.get("objectId"), "missing": missing})
    trace_coverage = complete_trace / max(len(trace_rows), 1)
    trace_status = "pass" if trace_coverage >= 0.98 else "warning" if trace_coverage >= 0.90 else "fail"
    issues.append(_issue(
        "RESULT-TRACEABILITY",
        "规范校核追溯覆盖",
        trace_status,
        f"校核记录追溯完整率为 {trace_coverage:.1%}。",
        evidence={"checkCount": len(trace_rows), "completeCount": complete_trace, "coverage": round(trace_coverage, 6), "missing": missing_trace},
        action="补齐规则ID、对象、状态、信息和条文/方法来源；正式发行要求不低于98%。",
        object_id=result.id,
    ))

    input_fail = int(input_audit.get("failCount") or 0)
    status_order = {"pass": 0, "warning": 1, "manual_review": 2, "fail": 3}
    overall = "pass"
    for issue in issues:
        if status_order.get(issue["status"], 2) > status_order.get(overall, 0):
            overall = issue["status"]
    if input_fail:
        overall = "fail"
    elif input_audit.get("status") == "warning" and overall == "pass":
        overall = "warning"

    result_core = result.model_dump(
        mode="json",
        by_alias=True,
        exclude={
            "id", "calculation_assurance", "result_hash", "formal_report_gate", "calculated_at",
            "delivery_readiness", "input_snapshot_hash", "adopted_design_snapshot_hash", "calculation_contract_id",
        },
    )
    for container_key in ("designIterationSummary", "reportDiagramData"):
        nested = result_core.get(container_key)
        if isinstance(nested, dict):
            for derived_key in (
                "industrialCalculationAssurance", "calculationContract", "formalReportGate",
                "p35FrozenCalculationInputSnapshot", "p36StageCoverageAndNumericalAssurance",
                "p37IndependentSolverReconciliation", "p38ImmutableCalculationContract",
            ):
                nested.pop(derived_key, None)
    stable_contract = {key: value for key, value in contract.items() if key not in {"createdAt", "adoptedAt"}}
    result_hash = _canonical_hash({"contract": stable_contract, "result": result_core})
    fail_count = sum(row["status"] == "fail" for row in issues) + input_fail
    warning_count = sum(row["status"] in {"warning", "manual_review"} for row in issues) + int(input_audit.get("warningCount") or 0)
    return {
        "status": overall,
        "eligibleForEngineeringUse": overall not in {"fail"},
        "eligibleForOfficialIssue": overall == "pass" and not input_fail,
        "contract": contract,
        "inputAudit": input_audit,
        "resultHash": result_hash,
        "stageCoverage": {"expected": len(expected_pairs), "actual": len(result.stage_results), "complete": stage_complete},
        "numericalQuality": issues[1]["evidence"],
        "independentCheck": issues[3]["evidence"],
        "traceability": issues[4]["evidence"],
        "failCount": fail_count,
        "warningCount": warning_count,
        "issues": issues,
        "assessedAt": _now(),
        "boundary": "该质量包证明输入冻结、阶段覆盖、数值质量和内部独立复核流程已执行；仍不能替代第三方软件对标、真实工程反演和注册工程师签审。",
    }


def apply_calculation_assurance(
    project: Project,
    case: CalculationCase,
    result: CalculationResult,
    *,
    input_audit: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> CalculationResult:
    contract = dict(contract or build_calculation_contract(project, case))
    input_contract_id = str(contract.get("inputContractId") or contract.get("contractId"))
    adopted_design_hash = _canonical_hash(calculation_input_payload(project, case))
    contract["inputContractId"] = input_contract_id
    contract["adoptedDesignSnapshotHash"] = adopted_design_hash
    contract["contractId"] = _final_contract_id(input_contract_id, adopted_design_hash, dict(contract.get("solverRuntime") or _solver_runtime_manifest()))
    contract["adoptedAt"] = _now()
    assurance = assess_calculation_result(project, case, result, input_audit=input_audit, contract=contract)
    contract_data = dict(assurance.get("contract") or {})
    result.input_snapshot_hash = contract_data.get("inputSnapshotHash")
    result.adopted_design_snapshot_hash = contract_data.get("adoptedDesignSnapshotHash")
    result.calculation_contract_id = contract_data.get("contractId")
    result.result_hash = assurance.get("resultHash")
    result.calculation_assurance = assurance
    result.design_iteration_summary = dict(result.design_iteration_summary or {})
    result.design_iteration_summary.update({
        "p35FrozenCalculationInputSnapshot": True,
        "p36StageCoverageAndNumericalAssurance": True,
        "p37IndependentSolverReconciliation": True,
        "p38ImmutableCalculationContract": True,
        "industrialCalculationAssurance": assurance,
        "calculationContract": contract_data,
    })
    result.report_diagram_data = dict(result.report_diagram_data or {})
    result.report_diagram_data["industrialCalculationAssurance"] = assurance
    return result


def verify_current_calculation_contract(project: Project, result: CalculationResult | None = None) -> dict[str, Any]:
    result = result or (project.calculation_results[-1] if project.calculation_results else None)
    if result is None:
        return {"current": False, "reason": "missing calculation result"}
    case_id = getattr(result, "case_id", None)
    if not case_id:
        iteration = dict(getattr(result, "design_iteration_summary", {}) or {})
        current_topology = support_topology_hash(project) if project.retaining_system else None
        stored_topology = getattr(result, "support_topology_hash", None)
        return {
            "current": bool(
                current_topology
                and stored_topology == current_topology
                and iteration.get("algorithmVersion") == ALGORITHM_VERSION
                and iteration.get("ruleSetVersion") == RULE_SET_VERSION
            ),
            "reason": "preview contract uses topology and version compatibility before immutable result is created",
            "storedSupportTopologyHash": stored_topology,
            "currentSupportTopologyHash": current_topology,
        }
    case = next((row for row in project.calculation_cases if row.id == case_id), None)
    if case is None:
        return {"current": False, "reason": "calculation case no longer exists", "resultId": getattr(result, "id", None)}
    current = build_calculation_contract(project, case)
    stored = dict((result.calculation_assurance or {}).get("contract") or (result.design_iteration_summary or {}).get("calculationContract") or {})
    stored_input_contract_id = str(stored.get("inputContractId") or "")
    current_adopted_hash = str(current.get("inputSnapshotHash") or "")
    expected_final_contract_id = _final_contract_id(
        stored_input_contract_id,
        current_adopted_hash,
        dict(stored.get("solverRuntime") or current.get("solverRuntime") or _solver_runtime_manifest()),
    ) if stored_input_contract_id and current_adopted_hash else None
    return {
        "current": bool(
            stored
            and (stored.get("adoptedDesignSnapshotHash") or result.adopted_design_snapshot_hash) == current_adopted_hash
            and result.calculation_contract_id == expected_final_contract_id
            and result.support_topology_hash == current.get("supportTopologyHash")
            and stored.get("caseHash") == current.get("caseHash")
            and stored.get("algorithmVersion") == ALGORITHM_VERSION
            and stored.get("ruleSetVersion") == RULE_SET_VERSION
            and stored.get("solverRuntime") == current.get("solverRuntime")
        ),
        "resultId": result.id,
        "storedContractId": result.calculation_contract_id,
        "currentContractId": expected_final_contract_id,
        "storedInputContractId": stored_input_contract_id,
        "currentInputContractId": current.get("inputContractId"),
        "solverRuntime": current.get("solverRuntime"),
        "storedInputSnapshotHash": result.input_snapshot_hash,
        "storedAdoptedDesignSnapshotHash": stored.get("adoptedDesignSnapshotHash") or result.adopted_design_snapshot_hash,
        "currentInputSnapshotHash": current.get("inputSnapshotHash"),
        "storedSupportTopologyHash": result.support_topology_hash,
        "currentSupportTopologyHash": current.get("supportTopologyHash"),
        "storedAlgorithmVersion": stored.get("algorithmVersion"),
        "currentAlgorithmVersion": ALGORITHM_VERSION,
        "storedRuleSetVersion": stored.get("ruleSetVersion"),
        "currentRuleSetVersion": RULE_SET_VERSION,
    }
