"""Authenticated immutable M3 physical-study Agent endpoints (no adoption)."""
from __future__ import annotations
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from app.services.access_control import ROLE_RANK
from app.services.m3_physical_evidence import MAX_BUNDLE_BYTES, M3PhysicalStudy
from app.services.m3_reference_artifacts import make_candidate_work_bundle
from .store import ConflictError
from .m3_runtime import SpatialAgentStore, SpatialAgentRuntime

router = APIRouter(prefix='/api/agents/m3',tags=['m3-spatial-agent-v3892'])


def _identity(request):
    identity=getattr(request.state,'pitguard_identity',None)
    if identity is None:
        raise HTTPException(401,detail='Authentication required')
    return identity


def _run(request,run_id):
    identity=_identity(request)
    try:run=SpatialAgentStore().get(run_id)
    except KeyError as exc:raise HTTPException(404,detail='M3 study not found') from exc
    if run['actor']!=identity.actor and ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='Cannot read another engineer M3 study')
    return run


@router.get('/schema')
def m3_physical_schema():
    return {'schema':M3PhysicalStudy.model_json_schema(),
        'purpose':'source_bytes_verified_depth_distributed_reference_only',
        'readOnlyDesign':True,'formalConstructionIssue':False}


@router.post('/runs',status_code=201)
async def create_m3_study(request:Request,bundle:UploadFile=File(...),
    optimizer:bool=Query(default=True),
    client_request_id:str|None=Query(default=None,min_length=8,max_length=96,pattern=r'^[A-Za-z0-9_.-]+$')):
    identity=_identity(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='M3 candidate study requires designer permission')
    raw=await bundle.read(MAX_BUNDLE_BYTES+1)
    if len(raw)>MAX_BUNDLE_BYTES:
        raise HTTPException(413,detail='M3 evidence bundle exceeds size limit')
    try:
        return SpatialAgentStore().create(raw,identity.actor,client_request_id,optimizer=optimizer)
    except ConflictError as exc:raise HTTPException(409,detail=str(exc)) from exc
    except (ValueError,KeyError) as exc:raise HTTPException(422,detail=str(exc)) from exc


@router.get('/runs')
def list_m3_studies(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    identity=_identity(request)
    rows=SpatialAgentStore().list(project_id)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        rows=[row for row in rows if row['actor']==identity.actor]
    return {'runs':rows,'formalConstructionIssue':False}


@router.get('/runs/{run_id}')
def get_m3_study(run_id:str,request:Request):
    return _run(request,run_id)


@router.get('/runs/{run_id}/events')
def m3_study_events(run_id:str,request:Request):
    _run(request,run_id)
    return {'events':SpatialAgentStore().events(run_id),'formalConstructionIssue':False}


@router.get('/runs/{run_id}/candidates')
def m3_study_candidates(run_id:str,request:Request):
    _run(request,run_id)
    return {'candidates':SpatialAgentStore().candidates(run_id),'formalConstructionIssue':False}


@router.get('/runs/{run_id}/report')
def m3_study_report(run_id:str,request:Request):
    _run(request,run_id)
    return SpatialAgentStore().review_summary(run_id)


@router.post('/runs/{run_id}/advance')
def m3_study_advance(run_id:str,request:Request):
    _run(request,run_id)
    if ROLE_RANK.get(_identity(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='M3 runtime execution requires designer permission')
    try:
        return SpatialAgentRuntime().process_one(run_id) or SpatialAgentStore().get(run_id)
    except ConflictError as exc:raise HTTPException(409,detail=str(exc)) from exc
    except (ValueError,FileNotFoundError) as exc:raise HTTPException(422,detail=str(exc)) from exc


@router.post('/runs/{run_id}/{control}')
def m3_study_control(run_id:str,control:str,request:Request):
    _run(request,run_id)
    if control not in ('pause','resume','cancel'):
        raise HTTPException(404,detail='M3 Agent control not supported')
    if ROLE_RANK.get(_identity(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='M3 Agent control requires designer permission')
    try:return SpatialAgentStore().change_state(run_id,control)
    except ConflictError as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.get('/runs/{run_id}/m7-reviews')
def list_m7_node_reviews(run_id:str,request:Request):
    _run(request,run_id)
    return {'reviews':SpatialAgentStore().node_reviews(run_id), 'formalConstructionIssue':False}


@router.post('/runs/{run_id}/m7-reviews/{candidate_index}')
async def compare_independent_m7_node_actions(run_id:str,candidate_index:int,request:Request,
                                              independent_report:UploadFile=File(...)):
    _run(request,run_id)
    identity=_identity(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='M7 independent numerical comparison requires checker permission')
    raw=await independent_report.read(2_000_001)
    if len(raw)>2_000_000:
        raise HTTPException(413,detail='M7 independent report over 2 MB')
    try:
        return SpatialAgentStore().record_node_review(run_id,candidate_index,identity.actor,raw)
    except (ValueError,KeyError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/runs/{run_id}/work-bundles/{candidate_index}')
def m3_candidate_reference_work_bundle(run_id:str,candidate_index:int,request:Request):
    run=_run(request,run_id)
    store=SpatialAgentStore()
    rows=store.candidates(run_id)
    matching=[row for row in rows if row['candidateIndex']==candidate_index]
    if not matching:raise HTTPException(404,detail='M3 candidate result missing')
    try:
        study,audit=store.load_blob(run)
        bundle=make_candidate_work_bundle(matching[0]['result'],audit)
    except ValueError as exc:
        raise HTTPException(422,detail=str(exc)) from exc
    return Response(content=bundle,media_type='application/zip',headers={
        'Content-Disposition':f'attachment; filename="PitGuard_M3_Reference_{run_id[:12]}_{candidate_index}.zip"',
        'X-PitGuard-Formal-Issue':'false'})


@router.get('/runs/{run_id}/method-reviews')
def m3_list_method_reviews(run_id:str,request:Request,candidate_index:int|None=None):
    _run(request,run_id)
    return {'reviews':SpatialAgentStore().method_reviews(run_id,candidate_index),
            'engineeringQualified':False,'formalConstructionIssue':False}


@router.post('/runs/{run_id}/method-reviews/{kind}/{candidate_index}')
async def m3_submit_method_review(run_id:str,kind:str,candidate_index:int,request:Request,
                                   evidence:UploadFile=File(...)):
    _run(request,run_id)
    identity=_identity(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='Independent normative/component review requires checker permission')
    if kind not in ('normative','component'):
        raise HTTPException(404,detail='Unknown independent method review')
    raw=await evidence.read(6_000_001)
    if len(raw)>6_000_000:
        raise HTTPException(413,detail='Review archive exceeds 6 MB')
    from pydantic import ValidationError
    try:
        return SpatialAgentStore().record_method_review(run_id,candidate_index,identity.actor,raw,kind)
    except (ValueError,KeyError,ValidationError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/runs/{run_id}/review-bundles/{candidate_index}')
def m3_candidate_review_package(run_id:str,candidate_index:int,request:Request):
    run=_run(request,run_id)
    store=SpatialAgentStore()
    rows=store.candidates(run_id)
    matched=[r for r in rows if r['candidateIndex']==candidate_index]
    if not matched:
        raise HTTPException(404,detail='Frozen M3 candidate missing')
    from app.services.m3_review_artifacts import make_m3_review_package
    try:
        _,audit=store.load_blob(run)
        method_reviews=store.method_reviews(run_id,candidate_index)
        norm=[r for r in method_reviews if r['kind']=='normative']
        component=[r for r in method_reviews if r['kind']=='component']
        body=make_m3_review_package(matched[0]['result'],audit,norm,component)
    except (ValueError,KeyError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc
    return Response(content=body,media_type='application/zip',headers={
        'Content-Disposition':f'attachment; filename="PitGuard_M3_Review_{run_id[:12]}_{candidate_index}.zip"',
        'X-PitGuard-Formal-Issue':'false'})


@router.get('/runs/{run_id}/review-matrix')
def m3_candidate_review_matrix(run_id:str,request:Request):
    _run(request,run_id)
    from app.services.m3_review_matrix import build_review_matrix
    store=SpatialAgentStore()
    return build_review_matrix(store.candidates(run_id),store.method_reviews(run_id))
