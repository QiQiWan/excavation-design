"""Single-row pile + one-strut calculation, evidence and reviewed deliverables."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from io import BytesIO
from pathlib import Path
import json
from .preflight import preflight
from .repository import Repository
from .artifacts import export_bundle

router=APIRouter(prefix='/api/engineering/one-strut',tags=['one-strut-normative-workspace'])

class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    result_hash: str=Field(pattern='^[0-9a-f]{64}$')
    reviewer: str=Field(min_length=2,max_length=120)
    notes: str=Field(min_length=8,max_length=1000)
    acknowledged_codes: list[str]

class ExportInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    result_hash: str=Field(pattern='^[0-9a-f]{64}$')
    review_hash: str=Field(pattern='^[0-9a-f]{64}$')

@router.get('/example')
def example_input() -> dict:
    return json.loads((Path(__file__).parent / 'assets' / 'verification_example.json').read_text(encoding='utf-8'))

@router.post('/preflight')
def check_input(payload:dict)->dict:
    return preflight(payload)

@router.post('/calculate')
def calculate_input(payload:dict)->dict:
    try: return Repository().compute(payload)
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc

@router.get('/results/{result_hash}')
def get_result(result_hash:str)->dict:
    try: return Repository().load(result_hash)
    except (FileNotFoundError,ValueError,KeyError) as exc:
        raise HTTPException(status_code=404,detail='已保存结果不存在或数据完整性验证失败') from exc

@router.post('/review')
def review_result(body:ReviewInput, request:Request)->dict:
    try:
        identity=getattr(request.state, 'pitguard_identity', None)
        if identity is None or identity.role not in ('checker','admin'):
            raise HTTPException(status_code=403, detail='受限复核须由具备校审权限的账户操作')
        return Repository().approve_preliminary(body.result_hash,reviewer=f'{body.reviewer} [{identity.actor}]',
                    notes=body.notes,acknowledged_codes=body.acknowledged_codes)
    except (FileNotFoundError,ValueError) as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc

@router.post('/exports')
def export_result(body:ExportInput):
    store=Repository()
    try:
        result=store.load(body.result_hash)
        review=store.load_review(body.review_hash,body.result_hash)
        content,manifest=export_bundle(result,review)
    except (FileNotFoundError,ValueError) as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    return StreamingResponse(BytesIO(content),media_type='application/zip',headers={
        'Content-Disposition': f'attachment; filename="one_strut_{body.result_hash[:12]}_review.zip"',
        'X-PitGuard-Snapshot':result['snapshotHash'],'X-PitGuard-Review':review['reviewHash']})


@router.get('/research/luoyu-3-3')
def research_luoyu_3_3(
    mesh_max_m: float = Query(default=0.15, ge=0.10, le=0.4),
    elastic_modulus_mpa: float = Query(default=30000., ge=1000., le=100000.),
    inertia_factor: float = Query(default=1., gt=0., le=1.),
):
    """Research-only source-derived five-stage reconstruction.

    Separate from the project calculation and from checked design output.
    Incomplete original nodal arrays, support mapping and physical method
    prohibit same-condition validation and normative approval.
    """
    from .real_project.luoyu_three_three import produce_luoyu_research_curves
    try:
        return produce_luoyu_research_curves(
            mesh_max_m=mesh_max_m, elastic_modulus_mpa=elastic_modulus_mpa,
            inertia_factor=inertia_factor)
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@router.get('/research/luoyu-3-3/boundary-register')
def research_luoyu_boundaries():
    from .real_project.boundary_evidence import luoyu_boundary_register
    return luoyu_boundary_register()

class CurveComparisonInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate: dict
    native_reference: dict

@router.post('/research/luoyu-3-3/compare-curves')
def research_luoyu_compare(body:CurveComparisonInput):
    from .real_project.boundary_evidence import compare_complete_curves
    return compare_complete_curves(body.candidate,body.native_reference)

# Register evidence-review endpoints under the existing one-strut workspace.
# They do not mutate a design snapshot or authorize construction issue.
from .assurance.router import router as assurance_router
router.include_router(assurance_router)
