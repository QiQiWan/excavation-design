from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

GB50007_BEARING_RULE = DesignRule(
    rule_id="GB50007-2011-BEARING-SUBSET",
    standard_name="建筑地基基础设计规范 GB 50007",
    standard_version="2011",
    clause_reference="5.2 bearing pressure subset; foundation layout to verify",
    name="地基承载力基础底面压力子集",
    description="pk <= fa、偏心时 pkmax <= 1.2fa 的承载力验算辅助函数。",
    severity="mandatory",
    applicable_to=["ColumnElement", "SupportFoundation"],
)


def check_foundation_bearing_pressure(
    object_id: str,
    vertical_force_kN: float,
    foundation_self_weight_kN: float,
    area_m2: float,
    fa_kpa: float,
    pkmax_kpa: float | None = None,
) -> CheckResult:
    if area_m2 <= 0 or fa_kpa <= 0:
        return CheckResult(
            rule_id=GB50007_BEARING_RULE.rule_id,
            object_id=object_id,
            object_type="SupportFoundation",
            status="manual_review",
            message="缺少基础面积或地基承载力特征值，不能自动完成 GB 50007 承载力验算。",
            clause_reference=GB50007_BEARING_RULE.clause_reference,
        )
    pk = (vertical_force_kN + foundation_self_weight_kN) / area_m2
    limit = fa_kpa
    status = "pass" if pk <= limit and (pkmax_kpa is None or pkmax_kpa <= 1.2 * fa_kpa) else "fail"
    message = "基础底面平均压力子集验算。若存在偏心、软弱下卧层、沉降或抗浮问题，应按 GB 50007 完整复核。"
    if pkmax_kpa is not None and pkmax_kpa > 1.2 * fa_kpa:
        message += " 偏心最大压力超过 1.2fa。"
    return CheckResult(
        rule_id=GB50007_BEARING_RULE.rule_id,
        object_id=object_id,
        object_type="SupportFoundation",
        status=status,
        calculated_value=round(pk, 3),
        limit_value=round(limit, 3),
        unit="kPa",
        message=message,
        clause_reference=GB50007_BEARING_RULE.clause_reference,
    )
