from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.runtime_diagnostics import append_event


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_STATUS_RANK = {"pass": 0, "skipped": 0, "warning": 1, "manual_review": 2, "fail": 3}


@dataclass
class CalculationExecutionTrace:
    """Small, serialization-friendly phase ledger for one calculation run.

    The solver remains synchronous, but every major engineering phase records a
    duration, status and bounded metrics.  This makes partial completion,
    bottlenecks and blocking phases visible without retaining large matrices.
    """

    run_id: str
    input_contract_id: str | None = None
    started_at: str = field(default_factory=_now_iso)
    _started_perf: float = field(default_factory=time.perf_counter)
    _phase_started_perf: float = field(default_factory=time.perf_counter)
    _phases: list[dict[str, Any]] = field(default_factory=list)

    def finish_phase(
        self,
        phase_id: str,
        label: str,
        *,
        status: str = "pass",
        message: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        now = time.perf_counter()
        phase = {
            "phaseId": phase_id,
            "label": label,
            "status": status,
            "durationSeconds": round(max(0.0, now - self._phase_started_perf), 4),
            "message": message,
            "metrics": dict(metrics or {}),
        }
        self._phases.append(phase)
        append_event(
            "calculation-execution",
            "phase_completed",
            runId=self.run_id,
            inputContractId=self.input_contract_id,
            **phase,
        )
        self._phase_started_perf = now

    def to_dict(self, *, transaction_status: str = "committed") -> dict[str, Any]:
        statuses = [str(item.get("status") or "pass") for item in self._phases]
        worst = max(statuses, key=lambda item: _STATUS_RANK.get(item, 2), default="pass")
        delivery_phase_ids = {"quality_delivery_gate", "result_evidence_freeze"}
        engineering_statuses = [
            str(item.get("status") or "pass") for item in self._phases
            if str(item.get("phaseId") or "") not in delivery_phase_ids
        ]
        delivery_statuses = [
            str(item.get("status") or "pass") for item in self._phases
            if str(item.get("phaseId") or "") in delivery_phase_ids
        ]
        engineering_worst = max(engineering_statuses, key=lambda item: _STATUS_RANK.get(item, 2), default="pass")
        delivery_worst = max(delivery_statuses, key=lambda item: _STATUS_RANK.get(item, 2), default="pass")
        total_duration = round(max(0.0, time.perf_counter() - self._started_perf), 4)
        bottleneck = max(self._phases, key=lambda item: float(item.get("durationSeconds") or 0.0), default=None)
        warning_count = sum(item in {"warning", "manual_review"} for item in statuses)
        fail_count = sum(item == "fail" for item in statuses)
        return {
            "schema": "pitguard-calculation-execution-v2",
            "runId": self.run_id,
            "inputContractId": self.input_contract_id,
            "status": (
                "completed_with_engineering_blocks" if engineering_worst == "fail"
                else "completed_with_engineering_review" if engineering_worst in {"warning", "manual_review"}
                else "completed_with_delivery_blocks" if delivery_worst == "fail"
                else "completed_with_delivery_review" if delivery_worst in {"warning", "manual_review"}
                else "completed"
            ),
            "engineeringStatus": engineering_worst,
            "deliveryStatus": delivery_worst,
            "worstPhaseStatus": worst,
            "transactionStatus": transaction_status,
            "startedAt": self.started_at,
            "completedAt": _now_iso(),
            "totalDurationSeconds": total_duration,
            "phaseCount": len(self._phases),
            "passPhaseCount": len(self._phases) - warning_count - fail_count,
            "warningPhaseCount": warning_count,
            "failPhaseCount": fail_count,
            "bottleneckPhase": ({
                "phaseId": bottleneck.get("phaseId"),
                "label": bottleneck.get("label"),
                "durationSeconds": bottleneck.get("durationSeconds"),
                "durationSharePercent": round(100.0 * float(bottleneck.get("durationSeconds") or 0.0) / max(total_duration, 1.0e-9), 1),
            } if bottleneck else None),
            "phases": list(self._phases),
        }
