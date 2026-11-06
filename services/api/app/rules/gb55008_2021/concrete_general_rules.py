from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

CONCRETE_GENERAL_RULE = DesignRule(
    rule_id="GB55008-2021-CONCRETE-GENERAL-001",
    standard_name="GB 55008-2021 混凝土结构通用规范",
    standard_version="2021",
    clause_reference="混凝土结构工程通用强制性要求",
    name="混凝土结构承载力、正常使用和耐久性设计声明",
    description="混凝土构件应满足承载能力、正常使用和耐久性要求。软件实现材料/承载力初步诊断，正式设计仍需人工复核。",
    severity="mandatory",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)


def check_concrete_grade(object_id: str, object_type: str, concrete_grade: str) -> CheckResult:
    # Conservative minimum used for reinforced concrete members in this software prototype.
    min_grade = 25
    try:
        grade_num = int(concrete_grade.upper().replace("C", ""))
    except ValueError:
        return CheckResult(
            rule_id=CONCRETE_GENERAL_RULE.rule_id,
            object_id=object_id,
            object_type=object_type,
            status="manual_review",
            message=f"无法解析混凝土强度等级 {concrete_grade}，需人工复核。",
            clause_reference=CONCRETE_GENERAL_RULE.clause_reference,
        )
    status = "pass" if grade_num >= min_grade else "fail"
    return CheckResult(
        rule_id=CONCRETE_GENERAL_RULE.rule_id,
        object_id=object_id,
        object_type=object_type,
        status=status,
        calculated_value=grade_num,
        limit_value=min_grade,
        unit="C-grade",
        message=("混凝土强度等级满足软件默认下限。" if status == "pass" else "混凝土强度等级低于软件默认下限。"),
        clause_reference=CONCRETE_GENERAL_RULE.clause_reference,
    )
