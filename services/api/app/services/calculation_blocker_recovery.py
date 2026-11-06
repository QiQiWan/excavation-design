from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.schemas.domain import Project
from app.services.calculation_state import invalidate_calculation_state
from app.services.design_qualification import build_design_qualification
from app.services.design_service import auto_diaphragm_wall, auto_supports, support_layout_config_from_settings
from app.services.excavation_service import close_polyline, generate_excavation_segments, validate_outline
from app.geology.model_builder import ensure_geological_model_covers_excavation

Progress = Callable[[int, str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculation_blockers(qualification: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(gate)
        for gate in list(qualification.get("gates") or [])
        if "calculation" in list(gate.get("blocks") or [])
    ]


def build_resolution_plan(qualification: dict[str, Any]) -> dict[str, Any]:
    blockers = calculation_blockers(qualification)
    rows: list[dict[str, Any]] = []
    for gate in blockers:
        code = str(gate.get("code") or "Q-UNKNOWN")
        evidence = dict(gate.get("evidence") or {})
        if code == "Q-GEOMETRY":
            rows.append({
                "code": code,
                "title": "围护几何一致性修复",
                "mode": "automatic_then_review",
                "automaticAction": "根据闭合基坑轮廓重建墙段映射，并在支撑缺失时生成基础支撑体系。",
                "manualAction": "若轮廓自交、点数不足或墙段具有人工专项含义，请回到工程输入修正轮廓后重试。",
                "targetStage": "input",
                "evidence": evidence,
            })
        elif code == "Q-COORD-GEO":
            coordinate = dict(evidence.get("coordinateAlignment") or {})
            geology = dict(evidence.get("geologyCoverage") or {})
            rows.append({
                "code": code,
                "title": "坐标与地质覆盖修复",
                "mode": "automatic_geology_manual_coordinates",
                "automaticAction": "在钻孔和基坑坐标已可用时重建/外扩地质设计域。",
                "manualAction": (
                    "系统不会自动平移测量坐标。请核对 m/mm、坐标原点、旋转和平移关系后确认。"
                    if str(coordinate.get("status")) == "fail"
                    else "复核钻孔与基坑控制点；警告状态不阻断计算，但会阻断正式发行。"
                ),
                "targetStage": "input",
                "evidence": {"coordinate": coordinate, "geology": geology},
            })
        elif code == "Q-TOPOLOGY":
            rows.append({
                "code": code,
                "title": "支撑传力拓扑修复",
                "mode": "automatic_candidate_regeneration",
                "automaticAction": "规范化墙/围檩支承语义，重新生成有限数量的合格候选并采用通过硬约束的方案。",
                "manualAction": "若所有候选仍受控阻断，请切换体系、补充显式转接构件或锁定人工节点。",
                "targetStage": "scheme",
                "evidence": evidence,
            })
        else:
            rows.append({
                "code": code,
                "title": str(gate.get("title") or "计算前置条件"),
                "mode": "manual_review",
                "automaticAction": None,
                "manualAction": str(gate.get("recommendedAction") or gate.get("message") or "请人工复核。"),
                "targetStage": "calculation",
                "evidence": evidence,
            })
    return {
        "status": "blocked" if blockers else "ready",
        "blockerCount": len(blockers),
        "blockerCodes": [str(item.get("code") or "") for item in blockers],
        "actions": rows,
        "generatedAt": _now(),
    }


def apply_safe_input_recovery(
    project: Project,
    qualification: dict[str, Any],
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Apply only deterministic, auditable repairs before calculation.

    Coordinate transforms and specialist structural-system decisions are never
    guessed.  The method is deliberately bounded: it may rebuild the wall map,
    create a missing baseline support system, and rebuild geological coverage.
    """
    blockers = calculation_blockers(qualification)
    codes = {str(item.get("code") or "") for item in blockers}
    actions: list[dict[str, Any]] = []
    changed = False

    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    if "Q-GEOMETRY" in codes:
        excavation = project.excavation
        if excavation is None:
            actions.append({"code": "Q-GEOMETRY", "status": "manual_required", "message": "缺少基坑轮廓，无法自动重建墙段。"})
        else:
            errors = validate_outline(excavation.outline, excavation.top_elevation, excavation.bottom_elevation)
            recoverable_errors = [item for item in errors if "必须闭合" in item]
            hard_errors = [item for item in errors if item not in recoverable_errors]
            if recoverable_errors and not hard_errors:
                excavation.outline = close_polyline(excavation.outline)
                errors = validate_outline(excavation.outline, excavation.top_elevation, excavation.bottom_elevation)
                hard_errors = list(errors)
            if hard_errors:
                actions.append({
                    "code": "Q-GEOMETRY",
                    "status": "manual_required",
                    "message": "；".join(hard_errors[:8]),
                })
            else:
                report(7, "根据闭合轮廓重建围护墙段映射")
                excavation.segments = generate_excavation_segments(excavation.outline)
                project.retaining_system = auto_diaphragm_wall(excavation, project.retaining_system, project.design_settings)
                if not list(project.retaining_system.supports or []):
                    config = support_layout_config_from_settings(project.design_settings)
                    project.retaining_system = auto_supports(excavation, project.retaining_system, config)
                changed = True
                actions.append({
                    "code": "Q-GEOMETRY",
                    "status": "applied",
                    "message": f"已重建 {len(excavation.segments)} 个轮廓段和 {len(project.retaining_system.diaphragm_walls or [])} 个墙段。",
                })

    if "Q-COORD-GEO" in codes:
        evidence = next((dict(item.get("evidence") or {}) for item in blockers if item.get("code") == "Q-COORD-GEO"), {})
        coordinate = dict(evidence.get("coordinateAlignment") or {})
        geology = dict(evidence.get("geologyCoverage") or {})
        coordinate_failed = str(coordinate.get("status") or "") == "fail"
        if coordinate_failed:
            actions.append({
                "code": "Q-COORD-GEO",
                "status": "manual_required",
                "message": "检测到坐标尺度或转换错误风险；为避免错误平移，系统不会自动修改测量坐标。",
                "suggestedTranslation": coordinate.get("suggestedTranslation"),
            })
        elif project.boreholes and project.excavation:
            report(13, "重建或外扩地质设计域")
            rebuilt = ensure_geological_model_covers_excavation(project)
            changed = changed or rebuilt
            actions.append({
                "code": "Q-COORD-GEO",
                "status": "applied" if rebuilt else "checked",
                "message": "已按当前钻孔与基坑范围检查地质设计域。" if not rebuilt else "已重建/外扩地质模型以覆盖围护结构和影响区。",
                "previousStatus": geology.get("status"),
            })
        else:
            actions.append({
                "code": "Q-COORD-GEO",
                "status": "manual_required",
                "message": "缺少钻孔或基坑轮廓，无法自动建立地质覆盖。",
            })

    if changed:
        invalidate_calculation_state(
            project,
            reason="safe pre-calculation recovery changed geometry or geological design inputs",
            rebuild_cases=bool(project.excavation and project.retaining_system),
            invalidate_candidates=False,
        )
    return {"changed": changed, "actions": actions, "attemptedAt": _now()}


def persist_recovery_state(project: Project, *, before: dict[str, Any], after: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    state = {
        "status": "ready" if bool(after.get("calculationAllowed")) else "needs_intervention",
        "before": build_resolution_plan(before),
        "after": build_resolution_plan(after),
        "automaticRecovery": recovery,
        "updatedAt": _now(),
    }
    advanced = dict(project.advanced_engineering or {})
    history = list(advanced.get("calculationBlockerRecoveryHistory") or [])
    history.append(state)
    advanced["calculationBlockerRecovery"] = state
    advanced["calculationBlockerRecoveryHistory"] = history[-20:]
    project.advanced_engineering = advanced
    return state
