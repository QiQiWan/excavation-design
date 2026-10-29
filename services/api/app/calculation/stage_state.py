"""Explicit, reproducible construction-stage boundary states.

These are independent linear snapshots. They do not claim transfer of plastic
soil history, installation displacement or prestress between solves.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.schemas.domain import CalculationCase, Project


def build_stage_states(project: Project, case: CalculationCase) -> dict[str, dict[str, Any]]:
    if not project.excavation or not project.retaining_system:
        raise ValueError("阶段计算缺少基坑或围护结构。")
    top = float(project.excavation.top_elevation)
    final_depth = top - float(project.excavation.bottom_elevation)
    supports = {str(s.id): s for s in project.retaining_system.supports}
    previous_active: set[str] = set()
    installed: set[str] = set()
    removed: set[str] = set()
    slab_active = False
    slab_depth: float | None = None
    slab_stage_id: str | None = None
    previous_depth = 0.0
    previous_id: str | None = None
    states: dict[str, dict[str, Any]] = {}
    for index, stage in enumerate(case.stages):
        if stage.id in states:
            raise ValueError(f"阶段编号重复：{stage.id}。")
        depth = top - float(stage.excavation_elevation)
        if not math.isfinite(depth) or depth < -1e-8 or depth > final_depth + 1e-8:
            raise ValueError(f"阶段 {stage.name} 开挖深度不在设计范围内。")
        depth = max(depth, 0.0)  # A true zero remains zero.
        if depth + 1e-8 < previous_depth:
            raise ValueError(f"阶段 {stage.name} 开挖深度回退；回填须建立独立工况。")
        active = {str(s) for s in stage.active_support_ids}
        deactivated = {str(s) for s in stage.deactivated_support_ids}
        if (active | deactivated) - set(supports):
            raise ValueError(f"阶段 {stage.name} 包含失效支撑引用，复算不会重新映射。")
        if active & deactivated:
            raise ValueError(f"阶段 {stage.name} 同时激活和拆除同一支撑。")
        if active & removed:
            raise ValueError(f"阶段 {stage.name} 重新激活已拆支撑；需明确重装事件。")
        if deactivated - installed:
            raise ValueError(f"阶段 {stage.name} 拆除尚未安装的支撑。")
        lost = previous_active - active - deactivated
        if lost:
            raise ValueError(f"阶段 {stage.name} 有支撑无拆除事件即退出，请补齐拆撑阶段。")
        newly_active = active - installed
        if any(float(supports[s].elevation) < float(stage.excavation_elevation) - 1e-8 for s in newly_active):
            raise ValueError(f"阶段 {stage.name} 激活了尚未开挖暴露的支撑。")
        assumptions: list[str] = []
        if newly_active and (index == 0 or depth > previous_depth + 1e-8):
            assumptions.append("缺少同深度安装前开挖控制状态；本阶段不覆盖安装前悬臂控制工况。")
        if newly_active and stage.stage_type != "support_installation":
            assumptions.append("新增支撑由显式激活清单进入本阶段，尚未单列安装事件。")
        if stage.stage_type in {"bottom_slab", "replacement", "support_removal"} and not slab_active:
            explicit_elevation = getattr(stage, "slab_activation_elevation", None)
            if stage.stage_type != "bottom_slab" and explicit_elevation is None:
                raise ValueError(f"阶段 {stage.name} 缺少前序楼板生效事件或明确换撑标高，不能自动恢复已拆支撑刚度。")
            slab_depth = top - float(explicit_elevation) if explicit_elevation is not None else depth
            if slab_depth < 0.0 or slab_depth > depth + 1e-8:
                raise ValueError(f"阶段 {stage.name} 楼板生效标高超出已开挖范围。")
            slab_active = True
            slab_stage_id = stage.id
            if explicit_elevation is None:
                assumptions.append("楼板约束标高未单独给定，按首次生效阶段开挖面取值，须由设计人确认。")
        installed.update(active)
        removed.update(deactivated)
        state = {
            "schema": "pitguard-stage-state-v1",
            "stageId": stage.id, "sequenceIndex": index, "previousStageId": previous_id,
            "stageType": stage.stage_type, "excavationDepthM": depth,
            "activeSupportIds": sorted(active), "newlyInstalledSupportIds": sorted(newly_active),
            "deactivatedSupportIds": sorted(deactivated), "removedSupportIds": sorted(removed),
            "inactiveSupportIds": sorted(set(supports) - active),
            "transferredSupportLevels": sorted(set(stage.transferred_support_levels)),
            "removedSupportStiffnessReused": False,
            "slabActive": slab_active, "slabActivationDepthM": slab_depth,
            "slabActivationStageId": slab_stage_id,
            "analysisType": "independent_linear_stage_snapshots",
            "historyTransfer": "not_implemented",
            "assumptions": assumptions,
        }
        state["stateHash"] = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        states[stage.id] = state
        previous_active, previous_depth, previous_id = active, depth, stage.id
    return states


def replacement_slab_properties(project: Project, segment_length: float, state: dict[str, Any]) -> dict[str, Any]:
    settings = project.design_settings
    return {
        "active": bool(state["slabActive"]),
        "activationDepthM": state["slabActivationDepthM"],
        "effectiveWidthM": settings.replacement_slab_effective_width_m,
        "thicknessM": settings.replacement_slab_thickness_m,
        "elasticModulusMpa": settings.replacement_slab_elastic_modulus_mpa,
        "connectionReduction": settings.replacement_connection_reduction,
        "transferLengthM": float(segment_length),
    }
