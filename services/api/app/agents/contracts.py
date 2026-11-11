"""Strict public contracts for the V3.88 research/work-copy Agent pilot."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

SHA256 = r'^[0-9a-f]{64}$'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class StartRun(Strict):
    result_hash: str = Field(pattern=SHA256)
    max_candidates: int = Field(default=3, ge=1, le=5)
    model_mode: Literal['off', 'optional'] = 'off'
    client_request_id: str | None = Field(default=None, min_length=8, max_length=96, pattern=r'^[a-zA-Z0-9_.-]+$')
    conversation_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class RunDecision(Strict):
    candidate_id: str | None = Field(default=None, pattern=SHA256)
    decision: Literal['adopt_for_working_copy', 'reject']
    expected_active_snapshot_hash: str = Field(pattern=SHA256)
    acknowledgement: bool
    comment: str = Field(min_length=12, max_length=1000)


class OperatorNote(Strict):
    note: str = Field(min_length=12, max_length=1000)


class ConversationCreate(Strict):
    """Opening a session with the engineering assistant."""

    project_id: str = Field(min_length=1, max_length=100)
    title: str = Field(default='新会话', min_length=1, max_length=120)


class OrchestratorDispatch(Strict):
    """Ask the orchestrator to start the next ready stage, if any.

    The orchestrator re-reads project state on every call; a caller cannot
    force a stage that its gates have not opened.
    """

    project_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    result_hash: str | None = Field(default=None, pattern=SHA256)
    max_candidates: int = Field(default=3, ge=1, le=5)
    client_request_id: str | None = Field(default=None, min_length=8, max_length=96,
                                          pattern=r'^[a-zA-Z0-9_.-]+$')


class MessageCreate(Strict):
    """A designer's turn.

    The public API accepts engineer turns only. Assistant, system and tool
    turns are written server-side by the runtime, so a client can never
    forge a message that appears to come from the assistant.
    """

    role: Literal['user'] = 'user'
    content: str = Field(min_length=1, max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInvoke(Strict):
    """One Agent tool call. Parameter shapes are enforced by the tool registry."""

    projectId: str | None = Field(default=None, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


class SuperTrigger(Strict):
    """A Router-level environment event (Trigger). Fast Path validates the shape."""

    projectId: str = Field(min_length=1, max_length=100)
    kind: Literal['user_message', 'document_uploaded', 'design_input_changed',
                  'calculation_failed', 'run_completed', 'review_requested']
    payload: dict[str, Any] = Field(default_factory=dict)
    conversationId: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    threadId: str | None = Field(default=None, min_length=1, max_length=100)
    acceptMode: Literal['observe', 'auto'] = 'observe'


class SuperDispatch(Strict):
    """Execute one accepted Ticket; the Mandate cannot be widened here."""

    text: str | None = Field(default=None, max_length=2000)
    resultHash: str | None = Field(default=None, pattern=SHA256)
    requestedTools: list[str] = Field(default_factory=list)
