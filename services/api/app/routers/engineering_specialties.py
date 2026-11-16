"""M5/M7 field-case receipt and M3 isolated geometry preview.

No API here can approve, adopt, replace or issue a design result.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Query
from pydantic import ValidationError
from app.engineering.single_braced_pile.real_project.field_case_intake import (
    MAX_ARCHIVE_BYTES, blank_case_template, intake_field_case, FieldCase)
from app.engineering.single_braced_pile.repository import Repository
from app.services.m3_plan_topology import M3TopologyInput, plan_topology_candidates
from app.services.m3_spatial_frame import M3FrameInput, evaluate_m3_spatial

router = APIRouter(prefix='/api/engineering/specialties',tags=['engineering-specialties'])

@router.get('/m5-m7/luoyu-case-template')
def get_luoyu_case_template():
    return {'template':blank_case_template(),
      'instructions':['Supply original signed design report, drawing, geology, native five-stage arrays, node details, independent calculation and method qualifications as raw document files',
                      'Use actual SHA-256 of raw file bytes in case.json documents, and document each action and resistance with matching source digests',
                      'The intake performs byte integrity and limited arithmetic checks. Full engineering acceptance remains disabled'],
      'schema':FieldCase.model_json_schema(),'formalConstructionIssue':False}

@router.post('/m5-m7/field-case-intake')
async def inspect_field_case(request:Request, archive:UploadFile=File(...),result_hash:str|None=Query(default=None,pattern=r'^[a-f0-9]{64}$')):
    # Checker role is enforced by the global auth middleware and here as a
    # defence-in-depth check. No upload is saved as an approved design source.
    identity=getattr(request.state,'pitguard_identity',None)
    if identity is None or getattr(identity,'role',None) not in ('checker','admin'):
        raise HTTPException(403,detail='Original engineering dossier intake requires checker/admin')
    content=await archive.read(MAX_ARCHIVE_BYTES+1)
    if len(content)>MAX_ARCHIVE_BYTES:
        raise HTTPException(413,detail='Field evidence ZIP over byte budget')
    try:
        sealed=Repository().load(result_hash) if result_hash else None
        return intake_field_case(content,frozen_result=sealed)
    except FileNotFoundError as exc:
        raise HTTPException(404,detail='Frozen result not found') from exc
    except (ValueError,KeyError,ValidationError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.get('/m3/topology-schema')
def m3_topology_schema():
    return {'schema':M3TopologyInput.model_json_schema(),
            'purpose':'geometry_precheck_only','formalConstructionIssue':False}

@router.post('/m3/topology-preview')
def m3_topology_preview(body:M3TopologyInput):
    try:return plan_topology_candidates(body)
    except ValueError as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.get('/m3/spatial-schema')
def m3_spatial_schema():
    return {'schema':M3FrameInput.model_json_schema(),
            'purpose':'reference_3d_staged_structure_only',
            'reviewGate':'no_automatic_adoption',
            'formalConstructionIssue':False}

@router.post('/m3/spatial-compare')
def m3_spatial_compare(body:M3FrameInput):
    try:return evaluate_m3_spatial(body)
    except ValueError as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.post('/m3/agent-bounded-search')
def m3_agent_bounded_search(body:M3FrameInput):
    # L1-only, immutable alternatives. A5/A6 one-strut SQLite jobs are NOT
    # reused to hold unrelated 3D results without an explicit storage contract.
    try:return evaluate_m3_spatial(body,optimizer=True)
    except ValueError as exc:
        raise HTTPException(422,detail=str(exc)) from exc
