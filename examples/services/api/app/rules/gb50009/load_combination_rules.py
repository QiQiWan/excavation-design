from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

LOAD_COMBINATION_RULE = DesignRule(
    rule_id="GB50009-LOAD-COMB-001",
    standard_name="GB 50009-2012 建筑结构荷载规范",
    standard_version="2012",
    clause_reference="荷载分类与组合章节（软件仅实现通用组合系数接口）",
    name="承载能力极限状态作用效应设计值",
    description="将标准值作用效应乘以支护结构重要性系数和综合分项系数；默认综合分项系数不小于 1.25。",
    severity="warning",
    applicable_to=["SupportElement", "DiaphragmWallPanel", "StageCalculationResult"],
)


def importance_factor(safety_grade: str) -> float:
    if "一" in safety_grade:
        return 1.10
    if "三" in safety_grade:
        return 0.90
    return 1.00


def design_effect_standard_to_uls(effect_standard: float, safety_grade: str = "二级", combined_partial_factor: float = 1.25) -> float:
    gamma0 = importance_factor(safety_grade)
    gamma_f = max(combined_partial_factor, 1.25)
    return effect_standard * gamma0 * gamma_f


def check_design_effect_available(object_id: str, object_type: str, standard_value: float | None, design_value: float | None) -> CheckResult:
    if standard_value is None or design_value is None:
        return CheckResult(
            rule_id=LOAD_COMBINATION_RULE.rule_id,
            object_id=object_id,
            object_type=object_type,
            status="manual_review",
            message="缺少标准值或设计值，无法追溯作用组合。",
            clause_reference=LOAD_COMBINATION_RULE.clause_reference,
        )
    return CheckResult(
        rule_id=LOAD_COMBINATION_RULE.rule_id,
        object_id=object_id,
        object_type=object_type,
        status="pass",
        calculated_value=design_value,
        limit_value=standard_value,
        unit="kN or kN·m",
        message="已按当前规则库生成承载能力极限状态设计值；仍需人工确认荷载组合与工况。",
        clause_reference=LOAD_COMBINATION_RULE.clause_reference,
    )
