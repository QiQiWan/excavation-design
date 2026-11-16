"""A separate, fail-closed API for standardized engineering evidence reviews."""
from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request,Query,File,Form,UploadFile
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
from .field_hydrology import (ObservationReviewInput, reconcile_observations,
                              PumpingTestInterpretationInput, interpret_thiem)
from .circular_review import (CrackCrosscheckInput, crosscheck_crack_reports,
                              EulerSecondOrderScreenInput, screen_euler_second_order)
from .dossier_verify import verify_review_package, MAX_HTTP_DOSSIER_BYTES
from ..real_project.native_numeric_import import (import_native_numeric,
                  compare_native_with_candidate, make_blank_numeric_template, MAX_ARCHIVE_BYTES)

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
       'unified':UnifiedAuditInput.model_json_schema(),
       'hydraulicFieldObservations':ObservationReviewInput.model_json_schema(),
       'pumpingTestThiem':PumpingTestInterpretationInput.model_json_schema(),
       'circularCrackCrosscheck':CrackCrosscheckInput.model_json_schema(),
       'pileEulerReferenceScreen':EulerSecondOrderScreenInput.model_json_schema()}}

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


@router.get('/luoyu-3-3/boundary-sensitivity')
def get_luoyu_boundary_sensitivity(
    mesh_max_m:float=Query(default=.25,ge=.1,le=.5)
):
    """Conditional study of toe/crown/support alternatives. No project certification."""
    from ..real_project.boundary_sensitivity import assess_boundary_identifiability
    try:
        return assess_boundary_identifiability(mesh_max_m=mesh_max_m)
    except (ValueError,KeyError,TypeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc



async def _bounded_upload(upload: UploadFile, limit: int) -> bytes:
    data = bytearray()
    try:
        while True:
            chunk = await upload.read(min(1024 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise HTTPException(413, detail='Uploaded engineering file exceeds the permitted size')
        return bytes(data)
    finally:
        await upload.close()


@router.post('/hydraulic/observations/validate')
def validate_field_observations(payload: ObservationReviewInput):
    return reconcile_observations(payload)


@router.post('/hydraulic/pumping-test/interpret')
def interpret_field_pumping_test(payload: PumpingTestInterpretationInput):
    return interpret_thiem(payload)


@router.post('/circular/crack-crosscheck')
def validate_circular_crack_reports(payload: CrackCrosscheckInput):
    try:
        return crosscheck_crack_reports(payload, Repository().load(payload.result_hash))
    except FileNotFoundError as exc:
        raise HTTPException(404, detail='Frozen result missing') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post('/circular/second-order/reference-screen')
def reference_second_order_screen(payload: EulerSecondOrderScreenInput):
    return screen_euler_second_order(payload)


@router.get('/luoyu-3-3/native-numeric/blank-template')
def download_native_numeric_template():
    payload = make_blank_numeric_template()
    return StreamingResponse(BytesIO(payload), media_type='application/zip', headers={
        'Content-Disposition':'attachment; filename="luoyu_3_3_native_numeric_blank.zip"',
        'X-PitGuard-Formal-Issue':'false'})


@router.post('/luoyu-3-3/native-numeric/validate')
async def validate_native_numeric_upload(upload: UploadFile=File(...)):
    content = await _bounded_upload(upload, MAX_ARCHIVE_BYTES)
    try:
        result = import_native_numeric(content)
        # Keep payload small in the browser response. Re-parse the same ZIP
        # when an actual comparison is requested; no unreviewed evidence is saved.
        result.pop('nativeReference')
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post('/luoyu-3-3/native-numeric/compare')
async def compare_native_numeric_upload(upload: UploadFile=File(...), candidate_json: str=Form(...)):
    content = await _bounded_upload(upload, MAX_ARCHIVE_BYTES)
    try:
        if len(candidate_json)>2_000_000:
            raise ValueError('Candidate JSON exceeds the permitted size')
        def reject_nonfinite(word):
            raise ValueError('Nonfinite candidate numeric values are prohibited: '+word)
        candidate = json.loads(candidate_json,parse_constant=reject_nonfinite)
        return compare_native_with_candidate(content, candidate)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post('/acceptance/verify-package')
async def verify_review_zip_upload(upload: UploadFile=File(...)):
    content=await _bounded_upload(upload, MAX_HTTP_DOSSIER_BYTES)
    try:
        return verify_review_package(content)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc

# V3.89.6: locked Luoyu 3-3 evidence benchmark; all uploads require an
# authenticated checker and never create formal construction approval.
from ..real_project.luoyu_fixed_acceptance import (
    requirement_catalog as _luoyu_fixed_catalog,
    blank_template as _luoyu_fixed_template,
    save_benchmark as _luoyu_fixed_save,
    read_benchmark as _luoyu_fixed_read,
    MAX_UPLOAD_BYTES as _LUOYU_MAX_BENCHMARK,
    declared_frozen_result_hash as _luoyu_fixed_claimed_result,
)


def _luoyu_checker(request: Request):
    actor = getattr(request.state, 'pitguard_identity', None)
    if actor is None or getattr(actor, 'role', None) not in ('checker', 'admin'):
        raise HTTPException(403, detail='Luoyu acceptance submissions require a signed-in engineering checker')
    return str(actor.actor)


def _luoyu_benchmark_store() -> Path:
    import os
    custom = os.environ.get('PITGUARD_LUOYU_ACCEPTANCE_STORE')
    return Path(custom) if custom else Repository().root / 'luoyu_3_3_acceptance'


@router.post('/luoyu-3-3/fixed-acceptance/original-report/audit')
async def luoyu_original_report_audit(request: Request, upload: UploadFile = File(...)):
    _luoyu_checker(request)
    from ..real_project.original_report_pdf import read_design_report_pdf, MAX_PDF_BYTES
    content = await _bounded_upload(upload, MAX_PDF_BYTES)
    try:
        return read_design_report_pdf(content)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/normative-beam')
def luoyu_normative_beam_audit(request: Request,
                              mesh_max_m:float=Query(default=.2,ge=.10,le=.4),
                              toe_boundary:str=Query(default='free',pattern='^(free|pinned|fixed)$')):
    # Read-only 1-D normative research review, never project design adoption.
    _luoyu_checker(request)
    from ..real_project.normative_elastic_beam import run_luoyu_normative_beam_recheck
    try:
        return run_luoyu_normative_beam_recheck(mesh_max_m=mesh_max_m,toe_boundary=toe_boundary)
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.post('/luoyu-3-3/fixed-acceptance/normative-beam/original-pdf')
async def luoyu_normative_beam_with_pdf(request: Request, upload: UploadFile=File(...),
                                        mesh_max_m:float=Query(default=.2,ge=.10,le=.4),
                                        toe_boundary:str=Query(default='free',pattern='^(free|pinned|fixed)$')):
    _luoyu_checker(request)
    from ..real_project.original_report_pdf import read_design_report_pdf, MAX_PDF_BYTES
    from ..real_project.normative_elastic_beam import run_luoyu_normative_beam_recheck
    content=await _bounded_upload(upload,MAX_PDF_BYTES)
    try:
        reference=read_design_report_pdf(content)
        return run_luoyu_normative_beam_recheck(
            mesh_max_m=mesh_max_m,toe_boundary=toe_boundary,original_report=reference)
    except (ValueError,TypeError,RuntimeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/physics-review')
def luoyu_physics_research_review(request: Request,
                                 trial_scope: str=Query(default='quick', pattern='^(none|quick)$')):
    """Research-only; full hypothesis sweep is intentionally CLI-only."""
    _luoyu_checker(request)
    from ..real_project.physics_diagnostics import run_luoyu_physics_review
    try:
        return run_luoyu_physics_review(trial_scope=trial_scope)
    except (ValueError, RuntimeError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post('/luoyu-3-3/fixed-acceptance/physics-review/original-pdf')
async def luoyu_physics_research_with_pdf(request: Request, upload: UploadFile=File(...),
                                         trial_scope: str=Query(default='quick',pattern='^(none|quick)$')):
    _luoyu_checker(request)
    from ..real_project.original_report_pdf import read_design_report_pdf, MAX_PDF_BYTES
    from ..real_project.physics_diagnostics import run_luoyu_physics_review
    content=await _bounded_upload(upload,MAX_PDF_BYTES)
    try:
        reference=read_design_report_pdf(content)
        return run_luoyu_physics_review(original_report=reference,trial_scope=trial_scope)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/physical-methods-38910')
def luoyu_advanced_normative_methods(request: Request,
                                   scope: str=Query(default='quick',pattern='^(none|quick)$')):
    """Read-only, checked JGJ pressure and 4.2/Annex C method review."""
    _luoyu_checker(request)
    from ..real_project.physics_review_38910 import run_luoyu_38910_review
    try:
        return run_luoyu_38910_review(scope=scope)
    except (ValueError,TypeError,RuntimeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.post('/luoyu-3-3/fixed-acceptance/physical-methods-38910/original-pdf')
async def luoyu_advanced_normative_with_pdf(request: Request,upload: UploadFile=File(...),
                                             scope: str=Query(default='quick',pattern='^(none|quick)$')):
    _luoyu_checker(request)
    from ..real_project.original_report_pdf import read_design_report_pdf,MAX_PDF_BYTES
    from ..real_project.physics_review_38910 import run_luoyu_38910_review
    content=await _bounded_upload(upload,MAX_PDF_BYTES)
    try:
        reference=read_design_report_pdf(content)
        return run_luoyu_38910_review(original_report=reference,scope=scope)
    except (ValueError,TypeError,RuntimeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/requirements')
def luoyu_fixed_acceptance_requirements():
    return _luoyu_fixed_catalog()


@router.get('/luoyu-3-3/fixed-acceptance/blank-template')
def luoyu_fixed_acceptance_blank():
    return StreamingResponse(BytesIO(_luoyu_fixed_template()), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="luoyu_3_3_acceptance_blank.zip"',
        'X-PitGuard-Formal-Issue': 'false'})


@router.post('/luoyu-3-3/fixed-acceptance/records')
async def luoyu_fixed_acceptance_record(request: Request, upload: UploadFile = File(...)):
    checker = _luoyu_checker(request)
    raw = await _bounded_upload(upload, _LUOYU_MAX_BENCHMARK)
    try:
        declared = _luoyu_fixed_claimed_result(raw)
        frozen = Repository().load(declared) if declared is not None else None
        return _luoyu_fixed_save(raw, _luoyu_benchmark_store(), checker, frozen_result=frozen)
    except FileNotFoundError as exc:
        raise HTTPException(422, detail='Claimed frozen result was not found in immutable repository') from exc
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/records/{benchmark_id}')
def luoyu_fixed_acceptance_get(benchmark_id: str, request: Request):
    _luoyu_checker(request)
    try:
        return _luoyu_fixed_read(benchmark_id, _luoyu_benchmark_store())
    except FileNotFoundError as exc:
        raise HTTPException(404, detail='Acceptance record not found') from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


# V3.89.11: evidence-aware field physical parameters and stability review.
# No route writes adopted design changes or construction issue approvals.
@router.get('/luoyu-3-3/fixed-acceptance/field-physical-38911')
def luoyu_field_physical_review(request: Request,
                              scope: str=Query(default='none',pattern='^(none|single)$')):
    _luoyu_checker(request)
    from ..real_project.physical_review_38911 import run_luoyu_38911_review
    try:
        return run_luoyu_38911_review(scope=scope)
    except (ValueError,RuntimeError,TypeError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc


@router.get('/luoyu-3-3/fixed-acceptance/field-physical-38911/source-template')
def luoyu_field_physical_template(request: Request):
    _luoyu_checker(request)
    from ..real_project.field_evidence_38911 import blank_case
    return blank_case()


@router.post('/luoyu-3-3/fixed-acceptance/field-physical-38911/evidence')
async def luoyu_field_physical_evidence(request: Request,
                  upload: UploadFile=File(...), original_pdf: UploadFile|None=File(None),
                  scope: str=Query(default='single',pattern='^(none|single)$')):
    _luoyu_checker(request)
    from ..real_project.field_evidence_38911 import MAX_BYTES
    from ..real_project.original_report_pdf import read_design_report_pdf,MAX_PDF_BYTES
    from ..real_project.physical_review_38911 import run_luoyu_38911_review
    content=await _bounded_upload(upload,MAX_BYTES)
    original=None
    if original_pdf is not None:
        original=read_design_report_pdf(await _bounded_upload(original_pdf,MAX_PDF_BYTES))
    try:
        return run_luoyu_38911_review(evidence_zip=content,
            original_report=original,scope=scope)
    except (ValueError,RuntimeError,TypeError,KeyError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc
