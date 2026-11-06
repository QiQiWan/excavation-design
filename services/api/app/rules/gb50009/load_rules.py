from __future__ import annotations

from app.rules.base import CheckResult, DesignRule
from app.schemas.domain import Project

GB50009_SURCHARGE_RULE = DesignRule(
    rule_id="GB50009-2012-CONSTRUCTION-SURCHARGE",
    standard_name="GB 50009",
    standard_version="2012",
    clause_reference="3, 5",
    name="基坑周边施工与道路荷载取值记录",
    description="基坑周边施工材料、设备、道路车辆及既有建筑荷载应作为附加荷载进入土压力计算；当前采用项目 surcharge 字段。",
    severity="mandatory",
    applicable_to=["DesignSettings", "PressureProfile"],
)


def check_surcharge_record(project: Project) -> CheckResult:
    q = project.design_settings.surcharge
    status = "pass" if q >= 0 else "fail"
    message = f"地面超载取值 q={q} kPa，已进入侧向土压力计算；正式设计应根据现场堆载、车辆、塔吊/泵车等施工荷载复核。"
    return CheckResult(
        rule_id=GB50009_SURCHARGE_RULE.rule_id,
        object_id=project.id,
        object_type="Project",
        status=status,
        calculated_value=q,
        limit_value=0.0,
        unit="kPa",
        message=message,
        clause_reference="GB 50009-2012; load representative value to be confirmed by designer",
    )
