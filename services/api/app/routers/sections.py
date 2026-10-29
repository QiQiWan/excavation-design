"""Small isolated workflow for a frozen, auditable 2D pile section."""
from __future__ import annotations

import asyncio
from threading import BoundedSemaphore

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.section.models import SectionInput, freeze_section, verification_example
from app.section.storage import SectionStore, get_section_store


router = APIRouter(prefix='/api/sections', tags=['frozen-section-validation'])
_capacity = BoundedSemaphore(2)


@router.get('/example')
def example() -> dict:
    return verification_example()


@router.get('/example-v2')
def example_v2() -> dict:
    from app.section.examples import verification_example_v2
    return verification_example_v2()


@router.get('/reference/a3-3')
def a3_reference(store: SectionStore = Depends(get_section_store)) -> dict:
    from app.section.reference_workflow import a3_reference_assets
    data = a3_reference_assets()
    store.save_reference_contract(data['contract'])
    store.save_reference_curves(data['curveTemplate'])
    return data


@router.post('/reference/contracts')
def save_reference_contract(body: dict, store: SectionStore = Depends(get_section_store)) -> dict:
    from app.section.reference_contract import freeze_reference_contract, assess_reference_contract
    try:
        contract = freeze_reference_contract(body)
        store.save_reference_contract(contract)
        return {'contract': contract, 'assessment': assess_reference_contract(contract)}
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/reference/curves')
def save_reference_curves(body: dict, store: SectionStore = Depends(get_section_store)) -> dict:
    from app.section.reference_curves import freeze_curve_bundle
    try:
        bundle = freeze_curve_bundle(body)
        store.save_reference_curves(bundle)
        return bundle
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ReferenceComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contract_hash: str = Field(pattern='^[0-9a-f]{64}$')
    curve_bundle_hash: str = Field(pattern='^[0-9a-f]{64}$')


@router.post('/freeze')
def freeze(body: SectionInput, store: SectionStore = Depends(get_section_store)) -> dict:
    snapshot = freeze_section(body)
    store.save_snapshot(snapshot)
    return snapshot.to_record()


def _load_snapshot(store: SectionStore, identity: str):
    try:
        return store.load_snapshot(identity)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='冻结剖面输入不存在') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/snapshots/{snapshot_hash}')
def read_snapshot(snapshot_hash: str, store: SectionStore = Depends(get_section_store)) -> dict:
    return _load_snapshot(store, snapshot_hash).to_record()


@router.get('/snapshots/{snapshot_hash}/latest-result')
def latest_result(snapshot_hash: str, store: SectionStore = Depends(get_section_store)) -> dict:
    _load_snapshot(store, snapshot_hash)
    try:
        return store.load_latest_result(snapshot_hash)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='该冻结输入尚无已保存结果；计算可能仍在执行，请稍后恢复') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/snapshots/{snapshot_hash}/analyze')
async def analyze(snapshot_hash: str, store: SectionStore = Depends(get_section_store)) -> dict:
    snapshot = _load_snapshot(store, snapshot_hash)
    def calculate():
        if not _capacity.acquire(blocking=False):
            raise HTTPException(status_code=429, detail='已有两个剖面分析正在运行，请稍后重试')
        try:
            from app.section.service import analyze_section
            from app.section.benchmarks import attach_comparisons
            result = attach_comparisons(snapshot, analyze_section(snapshot))
            store.save_result(snapshot, result)
            return result
        finally:
            _capacity.release()
    try:
        # Keep health/status requests responsive. The optional MPI/native
        # comparator is itself isolated in a child process.
        return await asyncio.to_thread(calculate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/results/{result_hash}')
def read_result(result_hash: str, store: SectionStore = Depends(get_section_store)) -> dict:
    try:
        return store.load_result(result_hash)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='剖面计算结果不存在') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/results/{result_hash}/curves')
def result_curves(result_hash: str, store: SectionStore = Depends(get_section_store)) -> dict:
    from app.section.reference_curves import section_curve_bundle
    result = read_result(result_hash, store)
    return section_curve_bundle(_load_snapshot(store, result['snapshotHash']), result)


@router.post('/results/{result_hash}/compare-reference')
def compare_reference(result_hash: str, body: ReferenceComparisonRequest,
                      store: SectionStore = Depends(get_section_store)) -> dict:
    from app.section.reference_workflow import make_reference_comparison
    result = read_result(result_hash, store)
    snapshot = _load_snapshot(store, result['snapshotHash'])
    try:
        contract = store.load_reference_contract(body.contract_hash)
        curves = store.load_reference_curves(body.curve_bundle_hash)
        record = make_reference_comparison(snapshot, result, contract, curves)
        store.save_reference_comparison(record)
        return record
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='待比较条件或曲线未保存，请先导入') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/results/{result_hash}/package')
async def package(result_hash: str, store: SectionStore = Depends(get_section_store)) -> FileResponse:
    result = read_result(result_hash, store)
    snapshot = _load_snapshot(store, result['snapshotHash'])
    from app.section.export import build_minimum_package
    try:
        reference = store.reference_supplement(snapshot, result)
        output = await asyncio.to_thread(build_minimum_package, snapshot, result, store.export_directory(result_hash), reference=reference)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(output, media_type='application/zip', filename=f'section-{result_hash[:12]}.zip')
