"""Versioned, bounded I/O for a rule-grounded engineering calculation agent."""
from __future__ import annotations

from typing import Literal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)


class QuantityInput(Strict):
    value: float
    unit: str = Field(min_length=1, max_length=32)
    source_type: Literal['survey', 'geotechnical_report', 'design_confirmed', 'standard_table',
                         'supplier_certified', 'engineering_derived', 'unconfirmed_candidate', 'synthetic_test']
    source_ref: str = Field(min_length=3, max_length=500)
    approval: Literal['approved', 'pending', 'rejected']
    approved_by: str | None = Field(default=None, max_length=120)
    detail: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def source_decision(self):
        if self.approval == 'approved' and not self.approved_by:
            raise ValueError('经确认参数必须注明确认人/可追溯的审核身份')
        if self.source_type == 'unconfirmed_candidate' and self.approval == 'approved':
            raise ValueError('候选值需先转换为设计确认来源及出处，不能直接标记已批准')
        return self


class CalcRequest(Strict):
    schema_version: Literal['pitguard.norm-calc-request/1']
    project_id: str = Field(min_length=1, max_length=100)
    project_region: str = Field(min_length=2, max_length=100)
    design_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    basis: Literal['GB55003-2021+JGJ120-2012']
    task: Literal['analyze_methods', 'calculate', 'review_parameters']
    method_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    inputs: dict[str, QuantityInput | str | list[dict]] = Field(default_factory=dict, max_length=80)
    scope_note: str = Field(default='', max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def uniqueness(self):
        try: date.fromisoformat(self.design_date)
        except ValueError: raise ValueError('design_date必须为真实有效日期')
        if len(set(self.method_ids)) != len(self.method_ids):
            raise ValueError('重复方法ID将造成重复求解，请去重')
        return self
