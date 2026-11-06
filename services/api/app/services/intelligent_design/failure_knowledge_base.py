from __future__ import annotations

FAILURE_KB = {
    "SUPPORT_FORCE_HIGH": {
        "diagnosis": "支撑承载能力不足，通常由支撑体系刚度不足或荷载传递路径不合理导致",
        "causes": [
            "support_spacing_large",
            "wale_stiffness_low",
            "corner_bracing_inefficient"
        ],
        "actions": [
            {"action": "add_support_layer", "priority": 1, "expected": "降低单根支撑轴力"},
            {"action": "increase_wale_stiffness", "priority": 2, "expected": "改善荷载分配"},
            {"action": "optimize_corner_bracing", "priority": 3, "expected": "改善角部传力"}
        ]
    },
    "WALL_DISP_HIGH": {
        "diagnosis": "围护体系整体刚度不足",
        "causes": ["wall_stiffness_low", "support_activation_late"],
        "actions": [
            {"action": "increase_wall_section", "priority": 1, "expected": "降低墙体变形"},
            {"action": "increase_support_stiffness", "priority": 2, "expected": "提高侧向约束"}
        ]
    },
    "TOPOLOGY_FAILED": {
        "diagnosis": "结构拓扑无法形成连续受力体系",
        "causes": ["open_boundary", "floating_support_node", "invalid_connection"],
        "actions": [
            {"action": "repair_support_node", "priority": 1, "expected": "恢复连接关系"},
            {"action": "regenerate_corner_bracing", "priority": 2, "expected": "恢复角部传力"}
        ]
    }
}


def query_failure(code: str):
    return FAILURE_KB.get(code, {})
