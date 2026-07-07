from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

WALL_THICKNESS_RULE = DesignRule(
    rule_id="ENT-PRELIM-DW-THK-001",
    standard_name="Enterprise Preliminary Design Rules",
    standard_version="0.1",
    clause_reference=None,
    name="地下连续墙厚度初选",
    description="按开挖深度区间给出 MVP 墙厚初选值。非规范最终设计。",
    severity="recommendation",
    applicable_to=["DiaphragmWallPanel"],
)

SUPPORT_LEVEL_RULE = DesignRule(
    rule_id="ENT-PRELIM-SUP-LVL-001",
    standard_name="Enterprise Preliminary Design Rules",
    standard_version="0.1",
    clause_reference=None,
    name="水平支撑道数初选",
    description="按开挖深度区间和竖向间距约束给出 V1.1 支撑道数建议。非规范最终设计。",
    severity="recommendation",
    applicable_to=["SupportElement"],
)


def select_wall_thickness(excavation_depth: float) -> tuple[float, list[str]]:
    warnings: list[str] = []
    if excavation_depth <= 6:
        return 0.6, warnings
    if excavation_depth <= 10:
        return 0.8, warnings
    if excavation_depth <= 15:
        return 1.0, warnings
    if excavation_depth <= 20:
        return 1.2, warnings
    warnings.append("开挖深度大于 20m，墙厚初选仅给出 1.2m 下限建议，应进行专项设计和专业复核。")
    return 1.2, warnings


def select_embedment_depth(excavation_depth: float) -> float:
    # Conservative engineering-v0.2 initial value used before staged stability checks.
    # The previous MVP 0.35H lower bound is kept as a documented minimum concept,
    # but automatic design now starts from a deeper wall to improve embedment and
    # seepage/heave screening for the bundled full-flow example.
    return max(0.75 * excavation_depth, 6.0)


def select_support_count(excavation_depth: float) -> tuple[int, list[str]]:
    warnings: list[str] = []
    if excavation_depth <= 5:
        return 1, ["H<=5m 时可为 0~1 道支撑，MVP 默认给 1 道以便形成可计算模型。"]
    if excavation_depth <= 8:
        return 1, warnings
    if excavation_depth <= 12:
        return 3, warnings
    if excavation_depth <= 16:
        return 3, warnings
    if excavation_depth <= 20:
        return 4, warnings
    warnings.append("开挖深度大于 20m，需要专项设计；MVP 默认给 5 道支撑建议。")
    return 5, warnings


def support_elevations(top_elevation: float, bottom_elevation: float) -> tuple[list[float], list[str]]:
    depth = top_elevation - bottom_elevation
    count, warnings = select_support_count(depth)
    if count <= 0:
        return [], warnings
    first_depth = 1.5
    min_space_above_bottom = 2.0
    usable_depth = max(depth - first_depth - min_space_above_bottom, 0.0)
    if count == 1:
        elevations = [top_elevation - min(first_depth, max(depth - min_space_above_bottom, 0.5))]
    else:
        spacing = usable_depth / (count - 1) if count > 1 else 0.0
        if spacing < 3.0:
            warnings.append("按默认支撑道数布置时支撑竖向间距小于 3.0m，需人工调整。")
        if spacing > 5.0:
            warnings.append("按默认支撑道数布置时支撑竖向间距大于 5.0m，需人工调整。")
        elevations = [top_elevation - first_depth - i * spacing for i in range(count)]
        if elevations[-1] <= bottom_elevation + min_space_above_bottom:
            warnings.append("最下一道支撑与坑底施工空间不足，需人工复核。")
    return [round(e, 3) for e in elevations], warnings


def preliminary_manual_review_check(object_id: str, object_type: str, message: str) -> CheckResult:
    return CheckResult(
        rule_id="ENT-PRELIM-MANUAL-REVIEW",
        object_id=object_id,
        object_type=object_type,
        status="manual_review",
        message=message,
    )
