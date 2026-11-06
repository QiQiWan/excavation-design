from __future__ import annotations

from app.rules.base import CheckResult, DesignRule

FOUNDATION_GENERAL_SAFETY_RULE = DesignRule(
    rule_id="GB55003-2021-FOUNDATION-GENERAL-001",
    standard_name="GB 55003-2021 建筑与市政地基基础通用规范",
    standard_version="2021",
    clause_reference="基坑工程功能性要求（软件记录为强制性通用要求）",
    name="基坑工程安全功能声明",
    description="基坑工程应保证支护结构、周边建构筑物、地下管线、道路和主体地下结构施工空间安全。",
    severity="mandatory",
    applicable_to=["Project", "ExcavationModel", "RetainingSystem"],
)


def check_foundation_general_requirements(project_id: str, has_geology: bool, has_excavation: bool, has_retaining_system: bool) -> list[CheckResult]:
    checks: list[CheckResult] = []
    inputs = [
        ("geologicalModel", has_geology, "已建立地质模型。", "缺少地质模型，不能形成可靠的设计剖面。"),
        ("excavation", has_excavation, "已定义基坑轮廓与开挖深度。", "缺少基坑轮廓与开挖深度。"),
        ("retainingSystem", has_retaining_system, "已生成围护结构。", "缺少围护结构方案。"),
    ]
    for suffix, ok, pass_msg, fail_msg in inputs:
        checks.append(
            CheckResult(
                rule_id=f"{FOUNDATION_GENERAL_SAFETY_RULE.rule_id}-{suffix}",
                object_id=project_id,
                object_type="Project",
                status="pass" if ok else "fail",
                message=pass_msg if ok else fail_msg,
                clause_reference=FOUNDATION_GENERAL_SAFETY_RULE.clause_reference,
            )
        )
    checks.append(
        CheckResult(
            rule_id=FOUNDATION_GENERAL_SAFETY_RULE.rule_id,
            object_id=project_id,
            object_type="Project",
            status="manual_review",
            message="周边建构筑物、地下管线、道路、市政设施和施工安全边界需由专业人员根据现场资料复核。",
            clause_reference=FOUNDATION_GENERAL_SAFETY_RULE.clause_reference,
        )
    )
    return checks
