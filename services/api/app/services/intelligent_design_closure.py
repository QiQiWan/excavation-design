from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import CalculationCase, CalculationResult, Project
from app.services.core_engineering_presentation import build_verification_distribution
from app.services.support_deep_design import optimize_support_deep_design


STRUCTURAL_CATEGORIES = {"strength", "stiffness", "stability", "hydraulic"}


def _verification_for_result(project: Project, result: CalculationResult) -> dict[str, Any]:
    """Build the normal verification view without making a trial result history."""
    project.calculation_results.append(result)
    try:
        return build_verification_distribution(project)
    finally:
        if project.calculation_results and project.calculation_results[-1] is result:
            project.calculation_results.pop()


def _expanded_result_records(distribution: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one verification row per physical object.

    The verification screen intentionally collapses a catalogue rule to its
    controlling row.  The design feedback loop must not use that collapsed
    view, otherwise only the first controlling wall/beam is strengthened while
    the remaining object rows stay just below the project reserve target.
    """
    expanded: list[dict[str, Any]] = []
    for parent in distribution.get("records") or []:
        object_rows = list(parent.get("objectResults") or [])
        if not object_rows:
            expanded.append(dict(parent))
            continue
        for raw in object_rows:
            row = dict(raw)
            for key in (
                "ruleId", "label", "category", "scope", "targetSafetyFactor",
                "standard", "implementationState", "evidenceState",
            ):
                if row.get(key) is None:
                    row[key] = parent.get(key)
            expanded.append(row)
    return expanded


def _quantitative_open_records(distribution: dict[str, Any]) -> list[dict[str, Any]]:
    controlling: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in _expanded_result_records(distribution):
        row = dict(raw)
        if str(row.get("category") or "") not in STRUCTURAL_CATEGORIES:
            continue
        if str(row.get("evidenceState") or "calculated") != "calculated":
            continue
        factor = row.get("safetyFactor")
        target = row.get("targetSafetyFactor")
        if factor is None or target is None:
            continue
        raw_rule = str(row.get("rawRuleId") or row.get("ruleId") or "").upper()
        if any(token in raw_rule for token in (
            "LIFECYCLE-PATH", "CONSTRUCTION-EFFECTS", "DETAILING-READINESS",
            "COORDINATION", "QUALITY-", "ASSURANCE", "COVERAGE",
        )):
            continue
        original_status = str(row.get("originalStatus") or "")
        if original_status in {"manual_review", "preliminary", "not_applicable"}:
            continue
        # A warning emitted by the calculation engine is a professional review
        # item (for example a weak-layer screen or a construction-effect
        # reminder), not a numerical section-sizing target.  Rows that were
        # originally ``pass`` but were promoted to ``warning`` only because
        # they miss the project's reserve target remain eligible above.
        if original_status == "warning":
            continue
        if float(factor) + 1.0e-4 >= float(target):
            continue
        key = (str(row.get("ruleId") or row.get("rawRuleId") or row.get("label")), str(row.get("objectId") or row.get("wallId") or "system"))
        previous = controlling.get(key)
        if previous is None or float(row.get("safetyFactor") or 99.0) < float(previous.get("safetyFactor") or 99.0):
            controlling[key] = row
    return sorted(
        controlling.values(),
        key=lambda row: (float(row.get("safetyFactor") or 99.0), str(row.get("objectCode") or ""), str(row.get("label") or "")),
    )


def _assessment(project: Project, result: CalculationResult) -> dict[str, Any]:
    distribution = _verification_for_result(project, result)
    expanded = _expanded_result_records(distribution)
    open_records = _quantitative_open_records(distribution)
    hard_records = [row for row in expanded if str(row.get("status") or "") == "fail"]
    review_records: list[dict[str, Any]] = []
    review_seen: set[tuple[str, str]] = set()
    for row in expanded:
        status = str(row.get("status") or "")
        original_status = str(row.get("originalStatus") or "")
        if status not in {"warning", "manual_review", "preliminary"}:
            continue
        # Catalogue rows without ``rawRuleId`` describe completeness/readiness
        # elsewhere in the full matrix.  They are not emitted findings from
        # this calculation and must not inflate the intelligent-loop counter.
        if not row.get("rawRuleId"):
            continue
        # Reserve shortfalls converted from a passing calculation are handled
        # by ``open_records``.  Only genuine engine/professional review states
        # belong in this non-blocking lane.
        if original_status not in {"warning", "manual_review", "preliminary"}:
            continue
        key = (
            str(row.get("ruleId") or row.get("rawRuleId") or row.get("label") or "review"),
            str(row.get("objectId") or row.get("wallId") or "system"),
        )
        if key not in review_seen:
            review_seen.add(key)
            review_records.append(row)
    structural_fails = [
        row for row in hard_records
        if str(row.get("category") or "") in STRUCTURAL_CATEGORIES and str(row.get("status") or "") == "fail"
    ]
    hard_fail_count = int((result.check_summary or {}).get("fail") or 0)
    return {
        "distribution": distribution,
        "openRecords": open_records,
        "hardRecords": hard_records,
        "reviewRecords": review_records,
        "quantitativeOpenCount": len(open_records),
        "reviewCount": len(review_records),
        "reserveShortfallCount": sum(float(row.get("safetyFactor") or 0.0) >= 1.0 for row in open_records),
        "structuralFailCount": len(structural_fails),
        "hardFailCount": hard_fail_count,
        "structuralClosed": not open_records and not structural_fails,
        "calculationClosed": not open_records and hard_fail_count == 0,
    }


def _round_up(value: float, increment: float) -> float:
    return round(math.ceil((value - 1.0e-9) / increment) * increment, 3)


def _strengthen_for_records(project: Project, records: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    ret = project.retaining_system
    if ret is None:
        return []
    walls = {wall.id: wall for wall in ret.diaphragm_walls}
    beams = {beam.id: beam for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]}
    supports = {support.id: support for support in ret.supports}
    actions: list[dict[str, Any]] = []

    wall_governing: dict[str, dict[str, Any]] = {}
    beam_governing: dict[str, dict[str, Any]] = {}
    support_required = False
    for row in records:
        object_id = str(row.get("objectId") or row.get("wallId") or "")
        scope = str(row.get("scope") or "")
        rule_text = " ".join(str(row.get(key) or "") for key in ("ruleId", "rawRuleId", "label")).lower()
        # Toe length, groundwater and global soil stability are not silently
        # changed here.  Their data and specialist choices become interaction
        # cards after the automatic member loop.
        if object_id in walls and not any(token in rule_text for token in ("embedment", "heave", "seepage", "uplift", "global", "weak", "下卧")):
            previous = wall_governing.get(object_id)
            if previous is None or float(row.get("safetyFactor") or 99.0) < float(previous.get("safetyFactor") or 99.0):
                wall_governing[object_id] = row
        elif object_id in beams or scope in {"wale", "crown_beam"} and object_id in beams:
            previous = beam_governing.get(object_id)
            if previous is None or float(row.get("safetyFactor") or 99.0) < float(previous.get("safetyFactor") or 99.0):
                beam_governing[object_id] = row
        elif object_id in supports or scope == "support":
            support_required = True

    increment = 0.15 if strategy == "stiffness_first" else 0.05 if strategy == "economic_zoned" else 0.10
    for wall_id, row in wall_governing.items():
        wall = walls[wall_id]
        before = float(wall.thickness)
        factor = max(float(row.get("safetyFactor") or 0.1), 0.1)
        target = max(float(row.get("targetSafetyFactor") or 1.1), 1.0)
        scale = min(1.25, max(1.03, (target / factor) ** (0.40 if str(row.get("category")) == "stiffness" else 0.25)))
        after = min(2.40, _round_up(max(before + increment, before * scale), 0.05))
        if after <= before + 1.0e-9:
            continue
        wall.thickness = after
        actions.append({
            "action": "increase_wall_thickness", "objectId": wall.id, "objectCode": wall.panel_code,
            "before": before, "after": after, "unit": "m", "category": row.get("category"),
            "reason": f"{row.get('label')} 的安全系数 {factor:.3f} 低于目标 {target:.3f}",
            "automaticBoundary": "只增厚控制墙段，不改土层、水位、荷载和已确认施工顺序",
        })

    for beam_id, row in beam_governing.items():
        beam = beams[beam_id]
        before_w = float(beam.section.width or 0.8)
        before_h = float(beam.section.height or 0.8)
        if str(row.get("category")) == "stiffness" or strategy == "stiffness_first":
            after_w = min(3.0, _round_up(before_w + 0.05, 0.05))
            after_h = min(2.6, _round_up(before_h + 0.15, 0.05))
        else:
            after_w = min(3.0, _round_up(before_w + 0.10, 0.05))
            after_h = min(2.6, _round_up(before_h + 0.10, 0.05))
        if after_w <= before_w + 1.0e-9 and after_h <= before_h + 1.0e-9:
            continue
        beam.section.width = after_w
        beam.section.height = after_h
        role = "冠梁" if beam.beam_role == "crown_beam" else "围檩"
        beam.section.name = f"{int(round(after_w * 1000))}x{int(round(after_h * 1000))} 钢筋混凝土{role}"
        actions.append({
            "action": "increase_beam_section", "objectId": beam.id, "objectCode": beam.code,
            "before": {"widthM": before_w, "heightM": before_h},
            "after": {"widthM": after_w, "heightM": after_h},
            "category": row.get("category"),
            "reason": f"{row.get('label')} 未达到项目储备目标",
        })

    if support_required:
        deep = optimize_support_deep_design(project, max_iterations=2)
        changed = list(deep.get("changedSupportIds") or [])
        actions.append({
            "action": "optimize_support_sections", "objectId": ret.id, "objectCode": "水平支撑体系",
            "changedSupportCount": len(changed), "changedSupportIds": changed[:40],
            "reason": "支撑轴压、稳定或施工偏心组合未达到项目储备目标",
            "status": deep.get("status"),
        })
    return actions


def _remaining_options(project: Project, assessment: dict[str, Any]) -> list[dict[str, Any]]:
    ret = project.retaining_system
    options: list[dict[str, Any]] = []
    walls = {wall.id: wall for wall in (ret.diaphragm_walls if ret else [])}
    beams = {beam.id: beam for beam in ([*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])] if ret else [])}
    supports = {support.id: support for support in (ret.supports if ret else [])}
    candidate_rows: list[dict[str, Any]] = list(assessment.get("openRecords") or [])
    known = {(str(row.get("ruleId")), str(row.get("objectId") or row.get("wallId") or "")) for row in candidate_rows}
    candidate_rows.extend(
        row for row in (assessment.get("hardRecords") or [])
        if (str(row.get("ruleId")), str(row.get("objectId") or row.get("wallId") or "")) not in known
    )
    known.update(
        (str(row.get("ruleId")), str(row.get("objectId") or row.get("wallId") or ""))
        for row in candidate_rows
    )
    candidate_rows.extend(
        row for row in (assessment.get("reviewRecords") or [])
        if (str(row.get("ruleId")), str(row.get("objectId") or row.get("wallId") or "")) not in known
    )
    for row in candidate_rows[:400]:
        object_id = str(row.get("objectId") or row.get("wallId") or "")
        factor = float(row.get("safetyFactor") or 0.0)
        target = float(row.get("targetSafetyFactor") or 1.0)
        rule_text = " ".join(str(row.get(key) or "") for key in ("ruleId", "rawRuleId", "label")).lower()
        base = {
            "objectId": object_id or None,
            "objectCode": row.get("objectCode") or row.get("wallCode") or "整体",
            "check": row.get("label"), "currentSafetyFactor": factor, "targetSafetyFactor": target,
            "reason": (
                f"当前安全系数 {factor:.3f}，目标 {target:.3f}，差额 {max(target - factor, 0.0):.3f}"
                if row.get("safetyFactor") is not None else str(row.get("message") or "该硬校核尚未闭合")
            ),
        }
        if any(token in rule_text for token in ("construction-effects", "lifecycle-path", "preload", "replacement")):
            options.append({
                **base, "objectId": None,
                "objectCode": "同类支撑构件",
                "actionId": f"manual:construction-review:{row.get('ruleId')}",
                "label": "确认施工阶段效应与拆换撑传力路径",
                "automaticAllowed": False,
                "targetPanel": "工程输入 → 施工阶段",
                "instruction": "核对各阶段开挖标高、支撑启用/退出、预加轴力、温度作用及楼板换撑时点；确认后重新计算，专业复核提醒不会再被误判为截面承载力不足。",
            })
        elif any(token in rule_text for token in ("weak", "下卧")):
            options.append({
                **base, "actionId": f"manual:weak-layer:{object_id or 'system'}", "label": "复核软弱下卧层并选择坑底加固方案",
                "automaticAllowed": False, "targetPanel": "工程输入 → 地质分层 / 围护方案 → 坑底加固",
                "instruction": "补齐软弱层厚度、抗剪强度和承载参数；由工程师选择加深墙趾、裙边/抽条加固、满堂加固或专项地基处理后再计算，系统不会用增厚围护墙代替该判断。",
            })
        elif object_id in walls and any(token in rule_text for token in ("embedment", "heave", "seepage", "uplift", "global")):
            wall = walls[object_id]
            options.append({
                **base, "actionId": f"deepen-wall:{object_id}", "label": f"加深 {wall.panel_code} 墙趾并复算",
                "automaticAllowed": not bool(wall.bottom_elevation_locked),
                "proposedValue": round(float(wall.bottom_elevation) - 0.50, 2), "unit": "m",
                "targetPanel": "围护方案 → 各墙段设计长度 / 墙趾分区",
                "instruction": "优先选择连续分区墙长；人工/导入锁定墙趾必须先由工程师解除锁定。",
            })
        elif object_id in walls:
            wall = walls[object_id]
            options.append({
                **base, "actionId": f"strengthen-wall:{object_id}", "label": f"增厚 {wall.panel_code} 并重新配筋",
                "automaticAllowed": True, "proposedValue": round(float(wall.thickness) + 0.10, 2), "unit": "m",
                "targetPanel": "围护方案 → 墙段参数",
                "instruction": "只强化控制墙段；也可选择“经济分区”让各墙段采用不同厚度和设计长度。",
            })
        elif object_id in beams:
            beam = beams[object_id]
            options.append({
                **base, "actionId": f"strengthen-beam:{object_id}", "label": f"增大 {beam.code} 截面",
                "automaticAllowed": True,
                "proposedValue": {"widthM": round(float(beam.section.width or 0.8) + 0.10, 2), "heightM": round(float(beam.section.height or 0.8) + 0.10, 2)},
                "targetPanel": "围护方案 → 梁构件参数",
                "instruction": "应用后自动重算刚度、内力、受弯、受剪与配筋。",
            })
        elif object_id in supports or str(row.get("scope")) == "support":
            options.append({
                **base, "actionId": "optimize-supports", "label": "优化控制水平支撑截面与立柱间距",
                "automaticAllowed": True, "targetPanel": "围护方案 → 支撑深化",
                "instruction": "保持已确认拓扑，优先增大控制支撑截面并缩短无侧向支承长度。",
            })
        else:
            options.append({
                **base, "actionId": f"manual:{row.get('ruleId')}", "label": "补充专项资料或人工确认设计假定",
                "automaticAllowed": False, "targetPanel": "工程输入 / 施工阶段 / 专项复核",
                "instruction": str(row.get("message") or "按验算项补齐地质、水位、施工阶段或专项计算资料后重算。"),
            })

    # Always expose two different, auditable ways forward when automatic
    # convergence is difficult.
    has_automatic_gap = bool(assessment.get("openRecords")) or any(
        str(row.get("category") or "") in STRUCTURAL_CATEGORIES
        for row in (assessment.get("hardRecords") or [])
    )
    if has_automatic_gap:
        options.extend([
            {
                "actionId": "run-strategy:stiffness_first", "label": "按刚度优先策略继续自动闭环",
                "automaticAllowed": True, "targetPanel": "计算验算 → 智能闭环",
                "instruction": "优先增大构件高度和墙厚，适用于位移、挠度或稳定控制，材料用量通常较高。",
            },
            {
                "actionId": "run-strategy:economic_zoned", "label": "按经济分区策略继续自动闭环",
                "automaticAllowed": True, "targetPanel": "计算验算 → 智能闭环",
                "instruction": "按控制墙段/梁段局部强化，并保留不同墙长、墙厚候选，需复核施工缝和防水连续性。",
            },
        ])
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        action_id = str(option.get("actionId") or "")
        if action_id and action_id not in seen:
            seen.add(action_id)
            deduped.append(option)
    return deduped[:20]


def apply_intervention_action(project: Project, action_id: str, value: Any = None) -> dict[str, Any]:
    ret = project.retaining_system
    if ret is None:
        raise ValueError("项目尚未生成围护体系。")
    walls = {wall.id: wall for wall in ret.diaphragm_walls}
    beams = {beam.id: beam for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]}
    if action_id.startswith("strengthen-wall:"):
        object_id = action_id.split(":", 1)[1]
        wall = walls.get(object_id)
        if wall is None:
            raise ValueError("未找到需要补强的墙段。")
        before = float(wall.thickness)
        minimum_next = _round_up(before + 0.10, 0.05)
        proposed = float(value) if value is not None else minimum_next
        after = min(2.40, _round_up(max(minimum_next, proposed), 0.05))
        if after <= before + 1.0e-9:
            raise ValueError(f"{wall.panel_code} 墙厚已达到自动优化上限 {before:.2f} m，需改用支撑体系、材料或专项双筋设计。")
        wall.thickness = after
        return {
            "actionId": action_id, "objectId": wall.id, "objectCode": wall.panel_code,
            "before": before, "after": wall.thickness, "delta": round(wall.thickness - before, 3),
            "unit": "m", "changed": True, "valuePolicy": "relative_from_current",
        }
    if action_id.startswith("deepen-wall:"):
        object_id = action_id.split(":", 1)[1]
        wall = walls.get(object_id)
        if wall is None:
            raise ValueError("未找到需要加深的墙段。")
        if wall.bottom_elevation_locked:
            raise ValueError("该墙趾为人工/导入锁定值，请先在墙段设计长度界面解除锁定。")
        before = float(wall.bottom_elevation)
        minimum_next = before - 0.50
        proposed = float(value) if value is not None else minimum_next
        after = round(min(minimum_next, proposed), 3)
        if after >= before - 1.0e-9:
            raise ValueError(f"{wall.panel_code} 墙趾没有产生有效加深。")
        wall.bottom_elevation = after
        wall.bottom_elevation_source = "auto_stability"
        wall.toe_profile_status = "local"
        return {
            "actionId": action_id, "objectId": wall.id, "objectCode": wall.panel_code,
            "before": before, "after": wall.bottom_elevation,
            "delta": round(wall.bottom_elevation - before, 3), "unit": "m",
            "changed": True, "valuePolicy": "relative_from_current",
        }
    if action_id.startswith("strengthen-beam:"):
        object_id = action_id.split(":", 1)[1]
        beam = beams.get(object_id)
        if beam is None:
            raise ValueError("未找到需要补强的梁构件。")
        before = {"widthM": float(beam.section.width or 0.8), "heightM": float(beam.section.height or 0.8)}
        proposed = value if isinstance(value, dict) else {}
        next_width = _round_up(before["widthM"] + 0.10, 0.05)
        next_height = _round_up(before["heightM"] + 0.10, 0.05)
        after_width = min(3.0, _round_up(max(next_width, float(proposed.get("widthM") or next_width)), 0.05))
        after_height = min(2.6, _round_up(max(next_height, float(proposed.get("heightM") or next_height)), 0.05))
        if after_width <= before["widthM"] + 1.0e-9 and after_height <= before["heightM"] + 1.0e-9:
            raise ValueError(f"{beam.code} 截面已达到自动优化上限，需调整体系或材料。")
        beam.section.width = after_width
        beam.section.height = after_height
        return {
            "actionId": action_id, "objectId": beam.id, "objectCode": beam.code,
            "before": before, "after": {"widthM": beam.section.width, "heightM": beam.section.height},
            "changed": True, "valuePolicy": "relative_from_current",
        }
    if action_id == "optimize-supports":
        deep = optimize_support_deep_design(project, max_iterations=3)
        changed_ids = list(deep.get("changedSupportIds") or [])
        if not changed_ids and str(deep.get("status") or "") in {"pass", "closed"}:
            raise ValueError("当前支撑截面深化已无可继续的单调升级项。")
        return {
            "actionId": action_id, "status": deep.get("status"),
            "changedSupportIds": changed_ids, "changedSupportCount": len(changed_ids),
            "changed": bool(changed_ids), "valuePolicy": "relative_from_current",
        }
    if action_id.startswith("apply-wall-length:"):
        from app.services.wall_vertical_length_optimizer import apply_wall_vertical_length_candidate
        candidate_id = action_id.split(":", 1)[1]
        return apply_wall_vertical_length_candidate(project, candidate_id, mode="balanced")
    if action_id.startswith("run-strategy:"):
        return {"actionId": action_id, "strategy": action_id.split(":", 1)[1], "message": "已选择新的自动闭环策略。"}
    raise ValueError("该建议属于人工/专项处理，不能由系统静默修改设计输入。")


def run_intelligent_design_closure(
    project: Project,
    calculation_case: CalculationCase | None = None,
    *,
    auto_repair: bool = True,
    strategy: str | None = None,
    max_iterations: int | None = None,
) -> tuple[CalculationResult, dict[str, Any]]:
    """Run a bounded designer/calculator feedback loop on the adopted scheme."""
    from app.calculation.engine import run_calculation

    strategy = str(strategy or project.design_settings.intelligent_closure_strategy or "balanced")
    if strategy not in {"balanced", "stiffness_first", "section_first", "economic_zoned"}:
        strategy = "balanced"
    limit = max(1, min(int(max_iterations or project.design_settings.max_intelligent_closure_iterations or 5), 8))
    history: list[dict[str, Any]] = []
    final_result: CalculationResult | None = None
    final_assessment: dict[str, Any] = {}
    for iteration in range(1, limit + 1):
        result = run_calculation(project, calculation_case, auto_repair=auto_repair, include_candidate_comparison=False)
        assessment = _assessment(project, result)
        round_row = {
            "iteration": iteration,
            "resultId": result.id,
            "hardFailCount": assessment["hardFailCount"],
            "structuralFailCount": assessment["structuralFailCount"],
            "quantitativeOpenBefore": assessment["quantitativeOpenCount"],
            "reserveShortfallBefore": assessment["reserveShortfallCount"],
            "governingOpenChecks": [
                {
                    "ruleId": row.get("ruleId"), "label": row.get("label"), "objectId": row.get("objectId"),
                    "objectCode": row.get("objectCode") or row.get("wallCode"),
                    "safetyFactor": row.get("safetyFactor"), "targetSafetyFactor": row.get("targetSafetyFactor"),
                }
                for row in assessment["openRecords"][:12]
            ],
            "actions": [],
        }
        final_result = result
        final_assessment = assessment
        if assessment["calculationClosed"] or iteration >= limit:
            history.append(round_row)
            break
        actionable: list[dict[str, Any]] = list(assessment["openRecords"])
        known = {
            (str(row.get("ruleId") or row.get("rawRuleId")), str(row.get("objectId") or row.get("wallId") or ""))
            for row in actionable
        }
        actionable.extend(
            row for row in assessment["hardRecords"]
            if str(row.get("category") or "") in STRUCTURAL_CATEGORIES
            and (str(row.get("ruleId") or row.get("rawRuleId")), str(row.get("objectId") or row.get("wallId") or "")) not in known
        )
        actions = _strengthen_for_records(project, actionable, strategy)
        round_row["actions"] = actions
        round_row["changedObjectCount"] = len({str(item.get("objectId")) for item in actions if item.get("objectId")})
        history.append(round_row)
        if not actions:
            break

    if final_result is None:
        raise ValueError("智能设计闭环未生成计算结果。")

    options = _remaining_options(project, final_assessment)
    full_closed = bool(final_assessment.get("calculationClosed"))
    structural_closed = bool(final_assessment.get("structuralClosed"))
    review_groups: dict[str, dict[str, Any]] = {}
    for row in (final_assessment.get("reviewRecords") or []):
        rule_id = str(row.get("ruleId") or row.get("rawRuleId") or row.get("label") or "专业复核")
        group = review_groups.setdefault(rule_id, {
            "ruleId": rule_id,
            "label": row.get("label"),
            "category": row.get("category"),
            "objectCount": 0,
            "objectCodes": [],
            "message": row.get("message"),
        })
        group["objectCount"] = int(group["objectCount"]) + 1
        code = str(row.get("objectCode") or row.get("wallCode") or "整体")
        if code not in group["objectCodes"] and len(group["objectCodes"]) < 8:
            group["objectCodes"].append(code)
    review_group_rows = list(review_groups.values())
    summary = {
        "version": "3.55-intelligent-design-closure-v1",
        "status": (
            "closed_with_review"
            if full_closed and int(final_assessment.get("reviewCount") or 0) > 0
            else "closed"
            if full_closed
            else "structural_closed_with_review"
            if structural_closed
            else "needs_intervention"
        ),
        "strategy": strategy,
        "automatic": True,
        "maximumIterations": limit,
        "executedIterations": len(history),
        "converged": full_closed,
        "structuralClosed": structural_closed,
        "calculationClosed": full_closed,
        "hardFailCount": int(final_assessment.get("hardFailCount") or 0),
        "structuralFailCount": int(final_assessment.get("structuralFailCount") or 0),
        "quantitativeOpenCount": int(final_assessment.get("quantitativeOpenCount") or 0),
        "reviewCount": int(final_assessment.get("reviewCount") or 0),
        "reviewGroupCount": len(review_group_rows),
        "reserveShortfallCount": int(final_assessment.get("reserveShortfallCount") or 0),
        "automaticInterventionCount": sum(len(list(item.get("actions") or [])) for item in history),
        "appliedInterventionCount": 0,
        "history": history,
        "remainingChecks": [
            {
                "ruleId": row.get("ruleId"), "label": row.get("label"), "objectId": row.get("objectId"),
                "objectCode": row.get("objectCode") or row.get("wallCode"), "category": row.get("category"),
                "safetyFactor": row.get("safetyFactor"), "targetSafetyFactor": row.get("targetSafetyFactor"),
                "message": row.get("message"),
            }
            for row in (final_assessment.get("openRecords") or [])[:30]
        ],
        "remainingReviewItems": [
            {
                "ruleId": row.get("ruleId"), "label": row.get("label"), "objectId": row.get("objectId"),
                "objectCode": row.get("objectCode") or row.get("wallCode"), "category": row.get("category"),
                "status": row.get("status"), "message": row.get("message"),
            }
            for row in (final_assessment.get("reviewRecords") or [])[:30]
        ],
        "reviewGroups": review_group_rows,
        "interventionOptions": options,
        "manualWorkflow": [
            "先查看控制对象、安全系数与目标值，不再只显示“待处理”。",
            "可直接应用推荐的墙厚、梁截面、支撑优化或局部墙趾加深，并自动重新计算。",
            "涉及地质、水位、降水和锁定施工顺序时，系统只指出填写位置，不会静默改假定。",
            "结构数值闭合后，锚固、接头、碰撞和审签作为构造复核继续进入 P3，不再误报为结构未闭合。",
        ],
        "engineeringBoundary": "自动闭环仅执行有上限、单调增强且可追溯的设计动作；不自动降低荷载、不提高土体参数、不改变已锁定施工阶段，也不替代注册工程师签审。",
    }
    final_result.design_iteration_summary = dict(final_result.design_iteration_summary or {})
    final_result.design_iteration_summary["intelligentDesignClosure"] = summary
    final_result.report_diagram_data = dict(final_result.report_diagram_data or {})
    final_result.report_diagram_data["intelligentDesignClosure"] = summary
    final_result.optimization_actions = list(final_result.optimization_actions or []) + [
        action for item in history for action in (item.get("actions") or [])
    ]
    if ret := project.retaining_system:
        ret.layout_summary = dict(ret.layout_summary or {})
        ret.layout_summary["intelligentDesignClosure"] = {
            key: summary[key] for key in (
                "version", "status", "strategy", "executedIterations", "structuralClosed",
                "calculationClosed", "quantitativeOpenCount", "reserveShortfallCount", "reviewCount", "reviewGroupCount",
            )
        }
    return final_result, summary
