from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

FOUNDATION_REVIEW_RULE = DesignRule(
    rule_id="GB50007-2011-FOUNDATION-MANUAL",
    standard_name="GB50007-2011 建筑地基基础设计规范",
    standard_version="2011",
    clause_reference="5.3",
    name="地基承载力、变形和周边基础影响复核",
    description="检查是否具备地基承载力、变形和周边建构筑物基础影响复核所需输入。",
    severity="warning",
    applicable_to=["Project", "ExcavationModel"],
)


def foundation_manual_review(object_id: str, borehole_count: int, has_geology_model: bool) -> CheckResult:
    if borehole_count > 0 and has_geology_model:
        status = "manual_review"
        message = "已有勘察剖面和地质模型，但软件尚未建立周边基础、沉降/变形和地基承载力完整模型；需按地基基础规范结合周边建构筑物专项复核。"
    else:
        status = "fail"
        message = "缺少勘察剖面或地质模型，不能进行地基基础相关复核。"
    return CheckResult(
        rule_id=FOUNDATION_REVIEW_RULE.rule_id,
        object_id=object_id,
        object_type="Project",
        status=status,
        message=message,
        clause_reference=FOUNDATION_REVIEW_RULE.clause_reference,
    )
