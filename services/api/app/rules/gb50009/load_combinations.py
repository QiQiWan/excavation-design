from __future__ import annotations

from typing import Any

from app.rules.base import CheckResult, DesignRule

GB50009_BASIC_COMBINATION_RULE = DesignRule(
    rule_id="GB50009-2012-BASIC-COMBINATION-SUBSET",
    standard_name="建筑结构荷载规范 GB 50009",
    standard_version="2012",
    clause_reference="load classification and basic combination subset; final project combination to verify",
    name="永久/可变作用基本组合子集",
    description="用于把土压力、水压力和地面超载形成结构内力设计值的基本组合辅助函数。",
    severity="mandatory",
    applicable_to=["CalculationResult", "SupportElement", "DiaphragmWallPanel"],
)


def basic_combination(permanent: float = 0.0, variable: float = 0.0, gamma_g: float = 1.35, gamma_q: float = 1.40, psi: float = 1.0) -> float:
    return gamma_g * permanent + gamma_q * psi * variable


def combination_record(permanent: float, variable: float, gamma_g: float = 1.35, gamma_q: float = 1.40, psi: float = 1.0) -> dict[str, Any]:
    return {
        "ruleId": GB50009_BASIC_COMBINATION_RULE.rule_id,
        "permanent": permanent,
        "variable": variable,
        "gammaG": gamma_g,
        "gammaQ": gamma_q,
        "psi": psi,
        "designValue": round(basic_combination(permanent, variable, gamma_g, gamma_q, psi), 3),
        "note": "组合系数为软件默认值；正式设计应按项目作用分类和审查要求确认。",
    }


def check_combination_documented(object_id: str, combination: dict[str, Any]) -> CheckResult:
    required = ["gammaG", "gammaQ", "psi", "designValue"]
    complete = all(key in combination and combination.get(key) is not None for key in required)
    return CheckResult(
        rule_id=GB50009_BASIC_COMBINATION_RULE.rule_id,
        object_id=object_id,
        object_type="CalculationResult",
        status="pass" if complete else "manual_review",
        calculated_value=combination.get("designValue"),
        limit_value=None,
        unit="kN/kPa-derived",
        message=(
            "荷载组合参数已完整记录并进入设计值计算；正式报审前仍应由专业人员确认作用分类和分项/组合系数。"
            if complete
            else "缺少荷载组合参数，不能形成可追溯设计值。"
        ),
        clause_reference=GB50009_BASIC_COMBINATION_RULE.clause_reference,
        formula="Sd = gammaG * Gk + gammaQ * psi * Qk",
    )
