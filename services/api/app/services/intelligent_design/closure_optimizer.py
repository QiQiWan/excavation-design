from __future__ import annotations
from typing import Any, Callable

class ClosureOptimizer:
    """验算-优化-再验算闭环控制器。"""

    def run(self, state: dict[str, Any], evaluator: Callable[[dict], dict] | None = None):
        history=[]
        current=state
        for i in range(3):
            result = evaluator(current) if evaluator else current.get("calculation", {})
            history.append({"round": i, "result": result})
            if self._qualified(result):
                return {"status": "qualified", "round": i, "history": history, "design": current}
            current=self._apply(current, result)
        return {"status":"optimization_limit_reached", "history":history, "design":current}

    def _qualified(self, result):
        sf=result.get("safety_factor")
        return isinstance(sf,(int,float)) and sf >= result.get("limit",1.3)

    def _apply(self, design, result):
        new=dict(design)
        sf=result.get("safety_factor",0)
        if sf < 1.3:
            new["support_stiffness_factor"] = new.get("support_stiffness_factor",1.0)*1.15
            new["optimization_round"] = new.get("optimization_round",0)+1
        return new
