from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import CalculationResult, Project
from app.services.engineering_templates import safety_targets


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _demand_capacity_ratio(check: dict[str, Any]) -> float:
    calculated = _number(check.get("calculatedValue"))
    limit = _number(check.get("limitValue"))
    if calculated is None or limit is None or limit <= 1.0e-9:
        return 1.15
    rule = str(check.get("ruleId") or "").upper()
    # Stability checks normally express resistance/action as a safety factor;
    # section checks express demand and capacity.  Both are converted to a
    # demand/capacity ratio where values above one require strengthening.
    if any(token in rule for token in ("STABILITY", "HEAVE", "UPLIFT", "EMBEDMENT")):
        return max(1.0, limit / max(calculated, 1.0e-9))
    return max(1.0, calculated / limit)


_DESIGN_RULE_TOKENS = (
    "FLEXURE", "SHEAR", "AXIAL", "CAPACITY", "DEFORMATION", "DEFLECTION",
    "CRACK", "REBAR", "STABILITY", "HEAVE", "UPLIFT", "SEEPAGE",
    "EMBEDMENT", "PIPING", "GLOBAL",
)
_NON_AUTOMATIC_DETAIL_TOKENS = (
    "ANCHOR", "LAP", "DETAIL", "CONSTRUCTION", "COMBINATION", "MATRIX",
    "RECONCILIATION", "NODE-DETAILING",
)


def _is_design_numeric_check(check: dict[str, Any]) -> bool:
    rule = str(check.get("ruleId") or "").upper()
    if not any(token in rule for token in _DESIGN_RULE_TOKENS):
        return False
    if any(token in rule for token in _NON_AUTOMATIC_DETAIL_TOKENS):
        return False
    return _number(check.get("calculatedValue")) is not None and _number(check.get("limitValue")) is not None


def _check_safety_factor(check: dict[str, Any]) -> float | None:
    utilization = _number(check.get("utilization"))
    if utilization is not None and utilization > 1.0e-9:
        return 1.0 / utilization
    calculated = _number(check.get("calculatedValue"))
    limit = _number(check.get("limitValue"))
    if calculated is None or limit is None or calculated <= 1.0e-9 or limit <= 1.0e-9:
        return None
    rule = str(check.get("ruleId") or "").upper()
    unit = str(check.get("unit") or "").lower()
    minimum_direction = (
        "MINREBAR" in rule
        or (
            any(token in rule for token in ("EMBEDMENT", "HEAVE", "UPLIFT", "SEEPAGE", "PIPING", "GLOBAL-STABILITY", "OVERALL-STABILITY"))
            and "utilization" not in unit
        )
    )
    return calculated / limit if minimum_direction else limit / calculated


def _target_for_check(project: Project, check: dict[str, Any]) -> float:
    targets = safety_targets(project)
    rule = str(check.get("ruleId") or "").upper()
    object_type = str(check.get("objectType") or "").upper()
    if any(token in rule for token in ("DEFORMATION", "DEFLECTION", "CRACK")):
        key = "stiffness"
    elif ("SUPPORT" in object_type or "SUPPORT" in rule) and any(token in rule for token in ("STABILITY", "SLENDER")):
        key = "support_stability"
    elif "COLUMN" in object_type and "STABILITY" in rule:
        key = "column_stability"
    elif "EMBEDMENT" in rule:
        key = "embedment"
    elif "HEAVE" in rule:
        key = "base_heave"
    elif "SEEPAGE" in rule or "PIPING" in rule:
        key = "seepage"
    elif "UPLIFT" in rule:
        key = "confined_uplift"
    elif "GLOBAL" in rule or "OVERALL" in rule:
        key = "overall_stability"
    else:
        key = "strength"
    return max(1.0, float(targets.get(key, 1.0) or 1.0))


def _feedback_checks(project: Project, result: CalculationResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in result.checks or []:
        check = dict(source)
        hard_fail = str(check.get("status") or "") == "fail"
        safety_factor = _check_safety_factor(check) if _is_design_numeric_check(check) else None
        target = _target_for_check(project, check) if safety_factor is not None else 1.0
        reserve_shortfall = safety_factor is not None and safety_factor + 1.0e-6 < target
        if not hard_fail and not reserve_shortfall:
            continue
        check["_hardFail"] = hard_fail
        check["_reserveShortfall"] = reserve_shortfall
        check["_safetyFactor"] = safety_factor
        check["_targetSafetyFactor"] = target
        check["_designRatio"] = max(1.0, target / max(safety_factor or 0.0, 1.0e-9)) if safety_factor is not None else _demand_capacity_ratio(check)
        rows.append(check)
    rows.sort(key=lambda item: (-float(item.get("_designRatio") or 1.0), str(item.get("ruleId") or "")))
    return rows


def evaluate_safety_design_feedback(project: Project, result: CalculationResult) -> dict[str, Any]:
    rows = _feedback_checks(project, result)
    hard_fail_count = sum(bool(item.get("_hardFail")) for item in rows)
    reserve_shortfall_count = sum(bool(item.get("_reserveShortfall")) for item in rows)
    return {
        "hardFailCount": hard_fail_count,
        "reserveShortfallCount": reserve_shortfall_count,
        "candidateCount": len(rows),
        "closed": hard_fail_count == 0 and reserve_shortfall_count == 0,
    }


def _round_up(value: float, step: float) -> float:
    return round(math.ceil((value - 1.0e-9) / step) * step, 6)


def apply_safety_design_feedback(
    project: Project,
    result: CalculationResult,
    *,
    iteration: int,
) -> dict[str, Any]:
    """Apply bounded section changes without changing support IDs or stages.

    This is the safe inner loop after a designer has confirmed construction
    stages.  It can strengthen wall, wale and support sections because those
    changes preserve the confirmed construction sequence.  Topology changes,
    missing inputs and specialist checks are deliberately returned as manual
    actions instead of being silently rewritten.
    """
    retaining = project.retaining_system
    if retaining is None:
        return {"iteration": iteration, "changed": False, "actions": [], "remaining": ["缺少围护结构体系。"]}

    candidates = _feedback_checks(project, result)
    failed = [item for item in candidates if bool(item.get("_hardFail"))]
    walls = {wall.id: wall for wall in retaining.diaphragm_walls}
    supports = {support.id: support for support in retaining.supports}
    beams = {beam.id: beam for beam in [*retaining.wale_beams, *(getattr(retaining, "ring_beams", []) or [])]}
    actions: list[dict[str, Any]] = []
    remaining: list[str] = []
    handled: set[tuple[str, str]] = set()
    adjusted_objects: set[str] = set()

    for check in candidates:
        rule = str(check.get("ruleId") or "UNKNOWN").upper()
        object_id = str(check.get("governingObjectId") or check.get("objectId") or "")
        key = (rule, object_id)
        if key in handled:
            continue
        handled.add(key)
        ratio = float(check.get("_designRatio") or _demand_capacity_ratio(check))

        wall = walls.get(object_id)
        if wall is not None and "MINREBAR" in rule:
            remaining.append(f"{wall.panel_code} 的项目储备配筋已达到自动钢筋组合边界，请人工调整主筋直径/间距并复核净距。")
            continue
        if wall is not None and any(token in rule for token in ("FLEXURE", "SHEAR", "DEFORMATION", "DEFLECTION", "CRACK", "REBAR")):
            if object_id in adjusted_objects:
                continue
            adjusted_objects.add(object_id)
            before = float(wall.thickness)
            # A 100 mm step is auditable and avoids oscillating on tiny changes.
            # The per-iteration increase is bounded; subsequent calculations
            # decide whether another step is actually required.
            factor = min(1.22, max(1.08, ratio ** (1.0 / 3.0)))
            after = min(1.80, _round_up(before * factor, 0.10))
            if after > before + 1.0e-9:
                wall.thickness = after
                actions.append({
                    "type": "wall_section_strengthening",
                    "label": "控制墙段截面补强",
                    "objectId": wall.id,
                    "objectCode": wall.panel_code,
                    "ruleId": rule,
                    "before": {"thicknessM": before},
                    "after": {"thicknessM": after},
                    "reason": f"该墙段校核需求/承载能力比约 {ratio:.2f}，墙厚增加后重新计算内力与配筋。",
                })
            else:
                remaining.append(f"{wall.panel_code} 已达到自动墙厚上限，需调整支撑体系或专项复核。")
            continue

        support = supports.get(object_id)
        if support is not None and any(token in rule for token in ("AXIAL", "STABILITY", "SLENDER", "CAPACITY")):
            if object_id in adjusted_objects:
                continue
            adjusted_objects.add(object_id)
            if support.section_type == "rc_rectangular":
                before_w = float(support.section.width or 0.8)
                before_h = float(support.section.height or 0.8)
                factor = min(1.18, max(1.06, math.sqrt(ratio / 0.88)))
                after_w = min(2.50, _round_up(before_w * factor, 0.05))
                after_h = min(2.50, _round_up(before_h * factor, 0.05))
                if after_w > before_w + 1.0e-9 or after_h > before_h + 1.0e-9:
                    support.section.width = after_w
                    support.section.height = after_h
                    support.section.name = f"{int(after_w * 1000)}×{int(after_h * 1000)} 钢筋混凝土支撑"
                    actions.append({
                        "type": "support_section_strengthening", "label": "支撑截面补强",
                        "objectId": support.id, "objectCode": support.code, "ruleId": rule,
                        "before": {"widthM": before_w, "heightM": before_h},
                        "after": {"widthM": after_w, "heightM": after_h},
                        "reason": f"轴压或稳定校核需求/承载能力比约 {ratio:.2f}。",
                    })
                else:
                    remaining.append(f"{support.code} 已达到自动截面上限，需缩短计算长度、增设可靠侧向支承或专项复核。")
            elif support.section_type == "steel_pipe":
                before_d = float(support.section.diameter or 0.609)
                before_t = float(support.section.wall_thickness or 0.016)
                after_d = min(1.50, _round_up(before_d * min(1.15, max(1.05, math.sqrt(ratio))), 0.025))
                after_t = min(0.050, _round_up(before_t * min(1.18, max(1.06, ratio)), 0.002))
                if after_d > before_d + 1.0e-9 or after_t > before_t + 1.0e-9:
                    support.section.diameter = after_d
                    support.section.wall_thickness = after_t
                    support.section.name = f"钢管 Φ{int(after_d * 1000)}×{int(after_t * 1000)}"
                    actions.append({
                        "type": "support_section_strengthening", "label": "钢支撑截面补强",
                        "objectId": support.id, "objectCode": support.code, "ruleId": rule,
                        "before": {"diameterM": before_d, "wallThicknessM": before_t},
                        "after": {"diameterM": after_d, "wallThicknessM": after_t},
                        "reason": f"轴压或稳定校核需求/承载能力比约 {ratio:.2f}。",
                    })
                else:
                    remaining.append(f"{support.code} 已达到自动钢管截面上限，需调整体系或专项复核。")
            else:
                remaining.append(f"{support.code} 的截面类型需要人工选型。")
            continue

        beam = beams.get(object_id)
        if beam is not None and any(token in rule for token in ("WALE", "FLEXURE", "SHEAR", "DEFLECTION")):
            if object_id in adjusted_objects:
                continue
            adjusted_objects.add(object_id)
            before_w = float(beam.section.width or 0.9)
            before_h = float(beam.section.height or 0.8)
            factor = min(1.20, max(1.06, ratio ** (1.0 / 3.0)))
            after_w = min(3.50, _round_up(before_w * factor, 0.10))
            after_h = min(2.80, _round_up(before_h * factor, 0.10))
            if after_w > before_w + 1.0e-9 or after_h > before_h + 1.0e-9:
                beam.section.width = after_w
                beam.section.height = after_h
                beam.section.name = f"{int(after_w * 1000)}×{int(after_h * 1000)} 钢筋混凝土围檩"
                actions.append({
                    "type": "wale_section_strengthening", "label": "围檩截面补强",
                    "objectId": beam.id, "objectCode": beam.code, "ruleId": rule,
                    "before": {"widthM": before_w, "heightM": before_h},
                    "after": {"widthM": after_w, "heightM": after_h},
                    "reason": f"围檩强度或刚度需求/承载能力比约 {ratio:.2f}。",
                })
            else:
                remaining.append(f"{beam.code} 已达到自动围檩截面上限，需缩短支点间距或专项复核。")
            continue

        if any(token in rule for token in ("TOPOLOGY", "CROSSING", "SUPPORT_BAY", "LOAD_PATH", "TEMPORARY_COLUMN")):
            remaining.append("施工阶段已锁定，涉及构件增删或传力路径的失败不会静默修改；请回到围护方案调整并重新确认施工阶段。")
        elif any(token in rule for token in ("EMBEDMENT", "HEAVE", "UPLIFT", "SEEPAGE", "STABILITY")):
            remaining.append("稳定或水控制在自动墙趾设计后仍未闭合；请核对地层、水位和墙趾锁定值，必要时采用分区墙趾或专项加固。")
        else:
            remaining.append(str(check.get("message") or f"{rule} 需要专业复核。"))

    # Deduplicate engineer-facing manual actions while retaining all section
    # changes for traceability.
    remaining = list(dict.fromkeys(item for item in remaining if item))
    return {
        "iteration": iteration,
        "changed": bool(actions),
        "failCountBefore": len(failed),
        "reserveShortfallCountBefore": sum(bool(item.get("_reserveShortfall")) for item in candidates),
        "designCandidateCount": len(candidates),
        "actions": actions,
        "changedObjectCount": len({str(item.get("objectId")) for item in actions}),
        "remaining": remaining,
        "message": (
            f"第 {iteration} 轮根据控制校核自动补强 {len(actions)} 个构件，需重新计算确认。"
            if actions else
            "当前未闭合项不属于可安全自动调整的截面项，已转为明确的人工处理建议。"
        ),
    }
