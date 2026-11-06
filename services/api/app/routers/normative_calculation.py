"""Normative engineering calculation toolbox and controlled agent API."""
from __future__ import annotations
from fastapi import APIRouter
from app.section.norms.registry import catalog
from app.section.norms.candidates import candidate_catalog
from app.section.norms.contracts import CalcRequest
from app.section.norms.engine import calculate
from app.section.norms.agent import AgentTask, run_task

router=APIRouter(prefix='/api/sections/norms',tags=['normative-calculation-agent'])

@router.get('/catalog')
def get_catalog() -> dict:
    return catalog()

@router.get('/candidates')
def get_candidates() -> dict:
    return candidate_catalog()

@router.post('/calculate')
def calculate_norms(body:CalcRequest) -> dict:
    return calculate(body)

@router.post('/agent')
def normative_agent(body:AgentTask) -> dict:
    return run_task(body)

@router.post('/audit-frozen')
def audit_legacy_frozen(body: dict) -> dict:
    from app.section.models import SectionInput
    from app.section.norms.audit import inspect_frozen_section
    return inspect_frozen_section(SectionInput.model_validate(body))
