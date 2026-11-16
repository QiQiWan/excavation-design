"""Strict public contracts for the V3.88 research/work-copy Agent pilot."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SHA256 = r'^[0-9a-f]{64}$'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class StartRun(Strict):
    result_hash: str = Field(pattern=SHA256)
    max_candidates: int = Field(default=3, ge=1, le=5)
    max_rounds: int = Field(default=1, ge=1, le=3)
    max_evaluations: int | None = Field(default=None, ge=1, le=15)
    max_runtime_seconds: int = Field(default=900, ge=10, le=7200)
    focus: Literal['auto','support','reinforcement','embedment','diagnostics','coupled','section'] = 'auto'
    model_mode: Literal['off', 'optional'] = 'off'
    client_request_id: str | None = Field(default=None, min_length=8, max_length=96, pattern=r'^[a-zA-Z0-9_.-]+$')


class RunDecision(Strict):
    candidate_id: str | None = Field(default=None, pattern=SHA256)
    decision: Literal['adopt_for_working_copy', 'reject']
    expected_active_snapshot_hash: str = Field(pattern=SHA256)
    acknowledgement: bool
    comment: str = Field(min_length=12, max_length=1000)


class OperatorNote(Strict):
    note: str = Field(min_length=12, max_length=1000)


class CandidateEvidenceReview(Strict):
    field_path: str = Field(min_length=4,max_length=100,pattern=r'^[a-z_]+\.[a-z_]+$')
    source_relative_path: str = Field(min_length=3,max_length=350)
    source_sha256: str = Field(pattern=SHA256)
    declared_value: float | int
    source_reference: str = Field(min_length=12,max_length=350)
    method_basis: str = Field(min_length=20,max_length=900)
