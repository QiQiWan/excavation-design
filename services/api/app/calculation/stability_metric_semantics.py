from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class StabilityMetricSemantic:
    metric_id: str
    label: str
    metric_type: str
    direction: str


_SAFETY_METRICS: tuple[tuple[str, StabilityMetricSemantic], ...] = (
    ("BASE-HEAVE", StabilityMetricSemantic("base_heave", "坑底隆起", "safety_factor", "larger_is_better")),
    ("CONFINED-WATER-UPLIFT", StabilityMetricSemantic("confined_uplift", "承压水突涌", "safety_factor", "larger_is_better")),
    ("SEEPAGE-STABILITY", StabilityMetricSemantic("seepage", "渗流稳定", "safety_factor", "larger_is_better")),
    ("OVERALL-STABILITY", StabilityMetricSemantic("overall", "整体稳定", "safety_factor", "larger_is_better")),
    ("EMBEDMENT-STABILITY", StabilityMetricSemantic("embedment", "嵌固稳定", "safety_factor", "larger_is_better")),
)

_RISK_METRICS: tuple[tuple[str, StabilityMetricSemantic], ...] = (
    ("LAYERED-SEEPAGE-GRADIENT", StabilityMetricSemantic("layered_seepage", "分层渗透风险", "risk_ratio", "smaller_is_better")),
    ("DEWATERING-STAGE", StabilityMetricSemantic("dewatering", "降水阶段控制", "risk_ratio", "smaller_is_better")),
)

_QUALITY_METRICS: tuple[tuple[str, StabilityMetricSemantic], ...] = (
    ("WEAK-UNDERLYING-LAYER", StabilityMetricSemantic("weak_layer", "软弱下卧层", "quality_index", "larger_is_better")),
)


def _rule_id(check: dict[str, Any]) -> str:
    return str(check.get("ruleId", check.get("rule_id", ""))).upper()


def calculated_value(check: dict[str, Any]) -> float | None:
    value = check.get("calculatedValue", check.get("calculated_value"))
    return float(value) if isinstance(value, (int, float)) else None


def limit_value(check: dict[str, Any]) -> float | None:
    value = check.get("limitValue", check.get("limit_value"))
    return float(value) if isinstance(value, (int, float)) else None


def classify_stability_metric(check_or_rule_id: dict[str, Any] | str) -> StabilityMetricSemantic | None:
    rid = _rule_id(check_or_rule_id) if isinstance(check_or_rule_id, dict) else str(check_or_rule_id).upper()
    # Risk/control ratios must be classified before generic SEEPAGE/WATER tokens.
    for token, semantic in _RISK_METRICS:
        if token in rid:
            return semantic
    for token, semantic in _QUALITY_METRICS:
        if token in rid:
            return semantic
    for token, semantic in _SAFETY_METRICS:
        if token in rid:
            return semantic
    return None


def normalized_utilization(check: dict[str, Any], semantic: StabilityMetricSemantic | None = None) -> float | None:
    semantic = semantic or classify_stability_metric(check)
    value = calculated_value(check)
    limit = limit_value(check)
    if semantic is None or value is None or limit is None or abs(limit) <= 1.0e-12:
        return None
    if semantic.direction == "larger_is_better":
        return limit / max(value, 1.0e-12)
    return value / limit


def reserve_ratio(check: dict[str, Any], semantic: StabilityMetricSemantic | None = None) -> float | None:
    semantic = semantic or classify_stability_metric(check)
    value = calculated_value(check)
    limit = limit_value(check)
    if semantic is None or value is None or limit is None or abs(limit) <= 1.0e-12:
        return None
    if semantic.direction == "larger_is_better":
        return value / limit
    if value <= 1.0e-12:
        return None
    return limit / value


def stability_metric_rows(checks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for check in checks:
        semantic = classify_stability_metric(check)
        if semantic is None:
            continue
        value = calculated_value(check)
        limit = limit_value(check)
        rows.append({
            "metricId": semantic.metric_id,
            "label": semantic.label,
            "metricType": semantic.metric_type,
            "direction": semantic.direction,
            "ruleId": str(check.get("ruleId", check.get("rule_id", ""))),
            "value": value,
            "limit": limit,
            "utilization": normalized_utilization(check, semantic),
            "reserveRatio": reserve_ratio(check, semantic),
            "status": str(check.get("status", "manual_review")),
        })
    return rows


def select_controlling(rows: Iterable[dict[str, Any]], metric_type: str) -> dict[str, Any] | None:
    candidates = [
        row for row in rows
        if row.get("metricType") == metric_type and isinstance(row.get("utilization"), (int, float))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: float(row["utilization"]))


def minimum_safety_factor(checks: Iterable[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for check in checks:
        semantic = classify_stability_metric(check)
        value = calculated_value(check)
        if semantic and semantic.metric_type == "safety_factor" and value is not None and value < 900.0:
            values.append(value)
    return min(values) if values else None
