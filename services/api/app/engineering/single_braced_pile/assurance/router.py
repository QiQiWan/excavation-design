"""A separate, fail-closed API for standardized engineering evidence reviews."""
from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request
from pathlib import Path
import json
from io import BytesIO
from fastapi.responses import StreamingResponse
from pydantic import Field
from .hydraulic import HydraulicAuditInput,audit_hydraulic
from .circular import CircularAuditInput,audit_circular
from .luoyu import LuoyuAuditInput,audit_luoyu,luoyu_template
from ..repository import Repository
from .workflow import UnifiedAuditInput,audit_unified
from .quality_protocol import acceptance_plan
from .dossier import export_assurance_dossier
from .model import Strict

router=APIRouter(prefix='/assurance',tags=['engineering-assurance'])

@router.get('/catalog')
def method_catalog():
    return json.loads((Path(__file__).parent/'assets'/'method_catalog.json').read_text(encoding='utf-8'))

@router.get('/schemas')
def schema_catalog():
    return {'version':'pitguard.assurance-schemas/1','schemas':{
       'hydraulic':HydraulicAuditInput.model_json_schema(),
       'circular':CircularAuditInput.model_json_schema(),
       'luoyu':LuoyuAuditInput.model_json_schema(),
       'unified':UnifiedAuditInput.model_json_schema()}}

@router.post('/hydraulic')
def audit_hydraulic_model(payload:HydraulicAuditInput):
    try:return audit_hydraulic(payload)
    except (ValueError,KeyError,TypeError,AssertionError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.post('/circular')
def audit_circular_model(payload:CircularAuditInput):
    try:return audit_circular(payload,Repository().load(payload.result_hash))
    except FileNotFoundError as exc:raise HTTPException(404,detail='Frozen result missing') from exc
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,detail=str(exc)) from exc

@router.get('/luoyu-3-3/template')
def get_luoyu_template():
    return luoyu_template()

@router.post('/luoyu-3-3')
def audit_luoyu_model(payload:LuoyuAuditInput):
    try:return audit_luoyu(payload)
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,detail=str(exc)) from exc

@router.post('/workflow')
def audit_engineering_workflow(payload:UnifiedAuditInput):
    try:
        return audit_unified(payload,Repository().load(payload.result_hash))
    except FileNotFoundError as exc:
        raise HTTPException(404,detail='Frozen result missing') from exc
    except (ValueError,KeyError,TypeError,AssertionError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


class DossierInput(Strict):
    unified:UnifiedAuditInput
    review_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    phase_review_hashes:tuple[str,...]=Field(default=(),max_length=4)

class PhaseReviewInput(Strict):
    result_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    phase:str=Field(pattern=r'^P[1-4]$')
    method_revision:str=Field(min_length=2,max_length=140)
    notes:str=Field(min_length=12,max_length=1400)
    decision:str=Field(pattern=r'^(acknowledged_for_working_copy|returned_for_revision)$')
    source_refs:tuple[str,...]=Field(default=(),max_length=16)
    independent_verification_ref:str|None=None
    negative_case_ref:str|None=None

@router.post('/acceptance/review-phase')
def record_phase_review(body:PhaseReviewInput,request:Request):
    identity=getattr(request.state,'pitguard_identity',None)
    if identity is None or getattr(identity,'role',None) not in ('checker','admin'):
        raise HTTPException(403,detail='Engineering phase review requires checker/admin role')
    try:
        return Repository().save_phase_review(body.result_hash,phase=body.phase,
              reviewer=str(identity.actor),method_revision=body.method_revision,
              notes=body.notes,decision=body.decision,source_refs=body.source_refs,
              independent_verification_ref=body.independent_verification_ref,
              negative_case_ref=body.negative_case_ref)
    except (ValueError,FileNotFoundError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/acceptance/methods')
def acceptance_method_manifest():
    from .quality_protocol import PHASES
    return {'schemaVersion':'pitguard.engineering-phases/1',
            'phases':PHASES,'formalConstructionIssue':False}

@router.post('/acceptance/plan')
def review_acceptance_plan(body:UnifiedAuditInput):
    try:return acceptance_plan(body,Repository().load(body.result_hash))
    except FileNotFoundError as exc:raise HTTPException(404,detail='Frozen result missing') from exc
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,detail=str(exc)) from exc

@router.post('/acceptance/dossier')
def issue_review_dossier(body:DossierInput,request:Request):
    actor=getattr(request.state,'pitguard_identity',None)
    if actor is None or getattr(actor,'role',None) not in ('checker','admin'):
        raise HTTPException(403,detail='Only a signed-in checker may export a reviewed working dossier')
    repo=Repository()
    try:
        result=repo.load(body.unified.result_hash)
        review=repo.load_review(body.review_hash,body.unified.result_hash)
        phase_reviews=tuple(repo.load_phase_review(x,body.unified.result_hash) for x in body.phase_review_hashes)
        content,manifest=export_assurance_dossier(body.unified,result,review,phase_reviews=phase_reviews)
    except FileNotFoundError as exc:raise HTTPException(404,detail='Frozen result or review missing') from exc
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,detail=str(exc)) from exc
    return StreamingResponse(BytesIO(content),media_type='application/zip',headers={
        'Content-Disposition':f'attachment; filename="P1_P4_{result["resultHash"][:12]}_review.zip"',
        'X-PitGuard-Result':result['resultHash'],'X-PitGuard-Review':review['reviewHash'],
        'X-PitGuard-Formal-Issue':'false'})
