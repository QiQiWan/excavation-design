from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class RuleModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class DesignRule(RuleModel):
    rule_id: str
    standard_name: str
    standard_version: str
    clause_reference: str | None = None
    name: str
    description: str
    severity: Literal["mandatory", "warning", "recommendation"]
    applicable_to: list[str]


class CheckResult(RuleModel):
    rule_id: str
    object_id: str
    object_type: str
    status: Literal["pass", "fail", "warning", "not_applicable", "manual_review"]
    calculated_value: float | None = None
    limit_value: float | None = None
    unit: str | None = None
    message: str
    clause_reference: str | None = None
    standard_name: str | None = None
    standard_version: str | None = None
    name: str | None = None
    formula: str | None = None
    review_required: bool = True
