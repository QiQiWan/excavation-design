from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class Diagnosis:
    code: str
    level: str
    title: str
    cause: str
    impact: str
    repair_actions: list[str]

class DesignDiagnosisEngine:
    """V3.88 diagnostic layer: converts calculation failures into repair tasks."""

    RULES = {
        "support_force_exceed": Diagnosis(
            "SUPPORT_FORCE_HIGH", "L2", "支撑轴力超限",
            "支撑体系刚度不足或间距过大",
            "可能导致构件承载力不足",
            ["增加一道支撑", "提高围檩刚度", "优化角撑布置"]
        ),
        "wall_displacement_exceed": Diagnosis(
            "WALL_DISP_HIGH", "L2", "围护墙位移超限",
            "墙体刚度不足或约束不足",
            "影响基坑变形控制",
            ["增加墙厚", "增加支撑刚度", "调整施工阶段"]
        ),
        "topology_failed": Diagnosis(
            "TOPOLOGY_FAILED", "L3", "支撑拓扑无法建立",
            "支撑节点与墙线或围檩连接关系异常",
            "无法形成可靠计算模型",
            ["自动修复节点", "重新生成角撑", "检查墙体闭合"]
        ),
    }

    def diagnose(self, checks: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for key, value in checks.items():
            if value in (False, None, "failed", "FAIL") and key in self.RULES:
                result.append(asdict(self.RULES[key]))
        return result

    def build_resolution(self, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "issues": diagnostics,
            "next_action": "AUTO_REPAIR_AVAILABLE" if diagnostics else "NO_ACTION_REQUIRED",
            "workflow": ["diagnose", "repair", "recalculate", "verify"]
        }
