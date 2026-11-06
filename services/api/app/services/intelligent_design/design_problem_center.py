from __future__ import annotations
from .failure_knowledge_base import query_failure


def build_problem_center(diagnostics):
    items=[]
    for item in diagnostics:
        kb=query_failure(item.get("code"))
        items.append({
            **item,
            "engineering_diagnosis": kb.get("diagnosis", "需要进一步分析"),
            "recommended_actions": kb.get("actions", [])
        })
    return {
        "summary": {
            "total": len(items),
            "blocking": len([x for x in items if x.get("level")=="L3"]),
            "warning": len([x for x in items if x.get("level")=="L2"])
        },
        "issues": items,
        "workflow": ["diagnose", "repair", "recalculate", "verify"]
    }
