from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import CalculationCase, Project, now_iso
from app.services.support_topology_contract import support_topology_hash


STAGE_INPUT_GUIDE: list[dict[str, Any]] = [
    {
        "field": "excavationElevation", "label": "阶段开挖标高", "source": "基坑轮廓、施工组织设计",
        "location": "基坑轮廓 / 施工阶段编辑器", "provider": "基坑设计与施工组织",
        "designStageAvailable": True, "action": "按每道支撑下方工作面或分区开挖控制标高录入。",
    },
    {
        "field": "activeSupportIds", "label": "本阶段已激活支撑", "source": "围护结构与支撑安装顺序",
        "location": "围护结构 / 施工阶段编辑器", "provider": "支护结构设计",
        "designStageAvailable": True, "action": "勾选本阶段已经安装并形成传力的支撑构件。",
    },
    {
        "field": "deactivatedSupportIds", "label": "拆除或退出工作的支撑", "source": "换撑及拆撑专项方案",
        "location": "施工阶段编辑器", "provider": "支护设计与施工专项",
        "designStageAvailable": True, "action": "仅在楼板/换撑达到设计条件后勾选退出工作的支撑。",
    },
    {
        "field": "groundwaterLevelOutside", "label": "坑外水位", "source": "勘察水位及设计水位",
        "location": "项目设置 → 基本设计控制 / 施工阶段编辑器", "provider": "勘察与岩土设计",
        "designStageAvailable": True, "action": "先设置项目设计水位；有阶段性回升或降深时在阶段中覆盖。",
    },
    {
        "field": "groundwaterLevelInside", "label": "坑内控制水位", "source": "降水专项设计",
        "location": "项目设置 → 基本设计控制 / 施工阶段编辑器", "provider": "降水设计",
        "designStageAvailable": True, "action": "按降水分期填写坑内控制水位。",
    },
    {
        "field": "surcharge", "label": "阶段地面超载", "source": "总平面、交通及堆载控制",
        "location": "项目设置 → 基本设计控制 / 施工阶段编辑器", "provider": "总图与基坑设计",
        "designStageAvailable": True, "action": "填写该阶段实际允许的坑边堆载与施工荷载。",
    },
    {
        "field": "replacementAction", "label": "换撑/拆撑生效条件", "source": "地下结构与换撑专项方案",
        "location": "项目设置 → 高级设计控制 / 施工阶段编辑器", "provider": "结构设计与施工专项",
        "designStageAvailable": True, "action": "说明楼板强度、连接和传力验收条件，不得只填写拆除时间。",
    },
]


def _issue(
    code: str,
    severity: str,
    message: str,
    action: str,
    *,
    stage_id: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "stageId": stage_id,
        "field": field,
        "message": message,
        "action": action,
    }


def normalize_user_calculation_case(project: Project, case: CalculationCase) -> CalculationCase:
    normalized = case.model_copy(deep=True)
    topology = support_topology_hash(project)
    support_by_id = {
        support.id: support
        for support in (project.retaining_system.supports if project.retaining_system else [])
    }
    normalized.source = "user_defined"
    normalized.locked = True
    normalized.support_topology_hash = topology
    normalized.updated_at = now_iso()
    normalized.revision = max(1, int(normalized.revision or 1))
    for stage in normalized.stages:
        stage.support_topology_hash = topology
        stage.active_support_ids = list(dict.fromkeys(stage.active_support_ids))
        stage.deactivated_support_ids = list(dict.fromkeys(stage.deactivated_support_ids))
        stage.active_support_levels = sorted({
            int(support_by_id[item].level_index)
            for item in stage.active_support_ids
            if item in support_by_id
        })
        if stage.stage_type in {"replacement", "support_removal"}:
            stage.transferred_support_levels = sorted({
                int(support_by_id[item].level_index)
                for item in stage.deactivated_support_ids
                if item in support_by_id
            })
    return normalized


def validate_calculation_case(project: Project, case: CalculationCase) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    excavation = project.excavation
    supports = project.retaining_system.supports if project.retaining_system else []
    support_by_id = {support.id: support for support in supports}
    valid_support_ids = {support.id for support in supports}
    topology = support_topology_hash(project) if project.retaining_system else None

    if excavation is None:
        issues.append(_issue("STAGE_EXCAVATION_MISSING", "fail", "缺少基坑轮廓和顶底标高。", "先完成基坑轮廓。"))
    if not case.stages:
        issues.append(_issue("STAGE_LIST_EMPTY", "fail", "施工阶段列表为空。", "生成推荐阶段或至少新增一个开挖阶段。"))

    stage_ids: set[str] = set()
    previous_elevation: float | None = None
    active_ever: set[str] = set()
    reaches_bottom = False
    has_final_stage = False
    for index, stage in enumerate(case.stages):
        label = stage.name or f"阶段 {index + 1}"
        if stage.id in stage_ids:
            issues.append(_issue("STAGE_ID_DUPLICATE", "fail", f"施工阶段编号重复：{stage.id}。", "为每个阶段生成唯一编号。", stage_id=stage.id))
        stage_ids.add(stage.id)
        elevation = float(stage.excavation_elevation)
        if not math.isfinite(elevation):
            issues.append(_issue("STAGE_ELEVATION_INVALID", "fail", f"{label} 的开挖标高不是有限数值。", "填写有效开挖标高。", stage_id=stage.id, field="excavationElevation"))
            continue
        if excavation:
            if elevation > float(excavation.top_elevation) + 1e-6 or elevation < float(excavation.bottom_elevation) - 1e-6:
                issues.append(_issue(
                    "STAGE_ELEVATION_OUTSIDE_EXCAVATION", "fail",
                    f"{label} 的开挖标高 {elevation:.3f}m 超出坑顶至坑底范围。",
                    "将标高调整到基坑设计开挖范围内。", stage_id=stage.id, field="excavationElevation",
                ))
            if abs(elevation - float(excavation.bottom_elevation)) <= 1e-4:
                reaches_bottom = True
        if previous_elevation is not None and elevation > previous_elevation + 1e-6:
            issues.append(_issue(
                "STAGE_ELEVATION_REBOUND", "fail", f"{label} 的开挖标高高于前一阶段。",
                "按施工顺序调整阶段，开挖标高应单调下降；回填或反向工况应单独建立专项算例。",
                stage_id=stage.id, field="excavationElevation",
            ))
        previous_elevation = elevation

        active = list(stage.active_support_ids)
        deactivated = list(stage.deactivated_support_ids)
        if stage.stage_type == "final":
            has_final_stage = True
        stale = sorted((set(active) | set(deactivated)) - valid_support_ids)
        if stale:
            issues.append(_issue(
                "STAGE_SUPPORT_REFERENCE_STALE", "fail", f"{label} 引用了 {len(stale)} 个已失效支撑ID。",
                "在当前支撑清单中重新选择激活/拆除构件。", stage_id=stage.id, field="activeSupportIds",
            ))
        overlap = sorted(set(active) & set(deactivated))
        if overlap:
            issues.append(_issue(
                "STAGE_SUPPORT_ACTIVE_AND_REMOVED", "fail", f"{label} 同时激活并拆除了 {len(overlap)} 根支撑。",
                "同一阶段内支撑只能处于激活或退出状态之一。", stage_id=stage.id,
            ))
        if stage.stage_type in {"replacement", "support_removal"} and not deactivated:
            issues.append(_issue(
                "STAGE_REMOVAL_TARGET_MISSING", "fail", f"{label} 为换撑/拆撑阶段，但未选择退出工作的支撑。",
                "选择拆除构件并填写换撑生效条件。", stage_id=stage.id, field="deactivatedSupportIds",
            ))
        removed_before_activation = sorted(set(deactivated) - active_ever)
        if removed_before_activation:
            issues.append(_issue(
                "STAGE_REMOVAL_BEFORE_ACTIVATION", "fail",
                f"{label} 退出了 {len(removed_before_activation)} 根此前未形成传力的支撑。",
                "补齐前序安装阶段，或从本阶段退出清单中移除这些构件。",
                stage_id=stage.id, field="deactivatedSupportIds",
            ))
        newly_active = set(active) - active_ever
        not_exposed = sorted(
            support_id for support_id in newly_active
            if support_id in support_by_id and float(support_by_id[support_id].elevation) < elevation - 1e-6
        )
        if not_exposed:
            issues.append(_issue(
                "STAGE_SUPPORT_NOT_EXPOSED", "fail",
                f"{label} 激活了 {len(not_exposed)} 根尚未随工作面开挖暴露的支撑。",
                "将安装阶段调整到支撑标高以下的工作面，或取消提前激活。",
                stage_id=stage.id, field="activeSupportIds",
            ))
        if stage.stage_type == "replacement" and not str(stage.replacement_action or "").strip():
            issues.append(_issue(
                "STAGE_REPLACEMENT_CONDITION_MISSING", "warning", f"{label} 未说明换撑生效条件。",
                "补充楼板强度、连接完成和传力验收条件。", stage_id=stage.id, field="replacementAction",
            ))
        for field, value in (
            ("groundwaterLevelInside", stage.groundwater_level_inside),
            ("groundwaterLevelOutside", stage.groundwater_level_outside),
        ):
            if value is not None and not math.isfinite(float(value)):
                issues.append(_issue("STAGE_WATER_LEVEL_INVALID", "fail", f"{label} 的水位无效。", "填写有限水位值。", stage_id=stage.id, field=field))
        if not math.isfinite(float(stage.surcharge)) or float(stage.surcharge) < 0:
            issues.append(_issue("STAGE_SURCHARGE_INVALID", "fail", f"{label} 的地面超载无效。", "填写不小于 0 的阶段超载。", stage_id=stage.id, field="surcharge"))
        active_ever.update(active)

    if excavation and case.stages and not reaches_bottom:
        issues.append(_issue(
            "STAGE_FINAL_EXCAVATION_MISSING", "fail", "施工阶段没有到达设计坑底。",
            "增加开挖至坑底的最终阶段。", field="excavationElevation",
        ))
    elif case.stages and not has_final_stage:
        issues.append(_issue(
            "STAGE_FINAL_TYPE_MISSING", "warning", "已有阶段到达设计坑底，但没有明确的最终开挖/使用校核阶段。",
            "将坑底控制阶段标记为“最终开挖与使用校核”。", field="stageType",
        ))
    missing_supports = sorted(valid_support_ids - active_ever)
    if supports and missing_supports:
        issues.append(_issue(
            "STAGE_SUPPORT_NEVER_ACTIVE", "warning", f"有 {len(missing_supports)} 根当前支撑从未在任何阶段激活。",
            "确认这些构件是否属于实际方案；需要参与受力时加入相应阶段。", field="activeSupportIds",
        ))
    if case.support_topology_hash and topology and case.support_topology_hash != topology:
        issues.append(_issue(
            "STAGE_TOPOLOGY_STALE", "fail", "施工阶段绑定的支撑拓扑与当前方案不一致。",
            "重新打开阶段编辑器，在当前构件清单中确认并保存。",
        ))

    fail_count = sum(item["severity"] == "fail" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    return {
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "valid": fail_count == 0,
        "failCount": fail_count,
        "warningCount": warning_count,
        "stageCount": len(case.stages),
        "issues": issues,
        "currentSupportTopologyHash": topology,
        "caseSupportTopologyHash": case.support_topology_hash,
    }


def select_calculation_case_for_run(project: Project) -> tuple[CalculationCase, dict[str, Any]]:
    from app.calculation.engine import build_default_construction_cases

    existing = project.calculation_cases[-1] if project.calculation_cases else None
    if existing and (existing.source == "user_defined" or existing.locked):
        validation = validate_calculation_case(project, existing)
        if not validation["valid"]:
            messages = "；".join(str(item["message"]) for item in validation["issues"] if item["severity"] == "fail")
            raise ValueError("用户施工阶段校验未通过：" + messages[:1200])
        return existing, {
            "source": "user_defined", "preserved": True, "caseId": existing.id,
            "stageCount": len(existing.stages), "validation": validation,
        }

    generated = build_default_construction_cases(project)[0]
    return generated, {
        "source": "auto_default", "preserved": False, "caseId": generated.id,
        "stageCount": len(generated.stages), "validation": validate_calculation_case(project, generated),
    }


def build_construction_stage_workspace(project: Project) -> dict[str, Any]:
    from app.calculation.engine import build_default_construction_cases

    saved = bool(project.calculation_cases)
    case = project.calculation_cases[-1] if saved else None
    if case is None and project.excavation and project.retaining_system:
        case = build_default_construction_cases(project)[0]
    validation = validate_calculation_case(project, case) if case else {
        "status": "fail", "valid": False, "failCount": 1, "warningCount": 0, "stageCount": 0,
        "issues": [_issue("STAGE_PREREQUISITE_MISSING", "fail", "缺少基坑或围护体系，无法生成施工阶段。", "先完成基坑轮廓和围护结构。")],
    }
    supports = project.retaining_system.supports if project.retaining_system else []
    return {
        "projectId": project.id,
        "saved": saved,
        "case": case.model_dump(mode="json", by_alias=True) if case else None,
        "summary": {
            "source": case.source if case else "missing",
            "locked": bool(case.locked) if case else False,
            "stageCount": len(case.stages) if case else 0,
            "supportCount": len(supports),
            "validationStatus": validation["status"],
            "failCount": validation["failCount"],
            "warningCount": validation["warningCount"],
        },
        "validation": validation,
        "supportOptions": [
            {
                "id": support.id, "code": support.code, "levelIndex": int(support.level_index),
                "elevation": float(support.elevation), "role": support.support_role,
            }
            for support in sorted(supports, key=lambda item: (int(item.level_index), item.code))
        ],
        "inputGuide": STAGE_INPUT_GUIDE,
    }
