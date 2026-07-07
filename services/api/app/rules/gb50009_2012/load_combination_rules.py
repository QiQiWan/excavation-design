from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

SURCHARGE_RULE = DesignRule(
    rule_id="GB50009-2012-SURCHARGE-TRACE",
    standard_name="GB50009-2012 建筑结构荷载规范",
    standard_version="2012",
    clause_reference="3.1",
    name="地面超载输入与荷载分类复核",
    description="检查基坑周边地面施工/车辆/材料超载是否作为可变荷载输入并进入侧向压力计算。",
    severity="warning",
    applicable_to=["DesignSettings", "CalculationCase"],
)

LOAD_FACTOR_RULE = DesignRule(
    rule_id="GB50009-2012-LOAD-COMBINATION-TRACE",
    standard_name="GB50009-2012 建筑结构荷载规范",
    standard_version="2012",
    clause_reference="3.2/3.3",
    name="承载能力极限状态荷载组合系数记录",
    description="记录程序用于墙体内力设计值的侧向压力放大系数，作为审查追溯字段。",
    severity="warning",
    applicable_to=["CalculationResult"],
)


def check_surcharge_defined(object_id: str, surcharge: float | None) -> CheckResult:
    if surcharge is None:
        return CheckResult(
            rule_id=SURCHARGE_RULE.rule_id,
            object_id=object_id,
            object_type="DesignSettings",
            status="fail",
            message="未输入地面超载；基坑周边施工材料、设备和车辆荷载应进入水平荷载计算。",
            clause_reference=SURCHARGE_RULE.clause_reference,
        )
    status = "pass" if surcharge >= 0 else "fail"
    return CheckResult(
        rule_id=SURCHARGE_RULE.rule_id,
        object_id=object_id,
        object_type="DesignSettings",
        status=status,
        calculated_value=surcharge,
        limit_value=0.0,
        unit="kPa",
        message=f"地面超载 q={surcharge:.2f}kPa 已进入土压力竖向应力计算；请结合施工堆载、车辆荷载和周边建构筑物荷载复核取值。",
        clause_reference=SURCHARGE_RULE.clause_reference,
    )


def load_combination_trace(object_id: str, factor: float) -> CheckResult:
    status = "pass" if factor >= 1.0 else "fail"
    return CheckResult(
        rule_id=LOAD_FACTOR_RULE.rule_id,
        object_id=object_id,
        object_type="CalculationResult",
        status=status,
        calculated_value=factor,
        limit_value=1.0,
        unit="-",
        message=f"墙体内力设计值采用侧向作用组合放大系数 {factor:.2f}；正式设计需按工程结构可靠性与荷载组合条件复核。",
        clause_reference=LOAD_FACTOR_RULE.clause_reference,
    )
