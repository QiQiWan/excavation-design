from __future__ import annotations

import asyncio
import importlib
import json

import pytest

from app.section.models import freeze_section, verification_example, SectionInput
from app.section.service import analyze_section


def modules():
    try:
        return importlib.import_module('app.section.storage'), importlib.import_module('app.routers.sections')
    except ModuleNotFoundError:
        pytest.fail('Immutable section API/storage is not implemented')


def test_content_addressed_storage_roundtrip_and_tamper_rejection(tmp_path):
    storage, _ = modules()
    store = storage.SectionStore(tmp_path)
    snapshot = freeze_section(verification_example())
    result = analyze_section(snapshot)
    store.save_snapshot(snapshot)
    store.save_result(snapshot, result)
    assert store.load_snapshot(snapshot.snapshot_hash) == snapshot
    assert store.load_result(result['resultHash']) == result
    record_path = tmp_path / 'snapshots' / f'{snapshot.snapshot_hash}.json'
    bad = json.loads(record_path.read_text())
    bad['input']['pile']['diameter_m'] += .1
    record_path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        store.load_snapshot(snapshot.snapshot_hash)


def test_api_freezes_explicit_input_and_cannot_export_a_caller_supplied_result(tmp_path):
    storage, router = modules()
    store = storage.SectionStore(tmp_path)
    record = router.freeze(SectionInput.model_validate(verification_example()), store)
    result = asyncio.run(router.analyze(record['snapshotHash'], store))
    assert result['snapshotHash'] == record['snapshotHash']
    assert result['comparisons']['hand']['status'] == 'passed'
    assert store.load_result(result['resultHash']) == result
    assert router.read_result(result['resultHash'], store)['resultHash'] == result['resultHash']
    # The package route exposes only a result ID and a server store; there is
    # no request-body parameter accepting caller-created diagrams/results.
    import inspect
    assert set(inspect.signature(router.package).parameters) == {'result_hash', 'store'}


@pytest.mark.parametrize('identity', ['../private', 'g'*64, '0'*63, '/tmp/file'])
def test_storage_id_cannot_be_used_as_a_path(tmp_path, identity):
    storage, _ = modules()
    with pytest.raises(ValueError):
        storage.SectionStore(tmp_path).load_snapshot(identity)


def test_completed_result_is_recoverable_from_snapshot_after_client_timeout(tmp_path):
    storage, router = modules()
    store = storage.SectionStore(tmp_path)
    snapshot = freeze_section(verification_example())
    store.save_snapshot(snapshot)
    assert hasattr(store, 'load_latest_result'), 'A timed-out browser needs a recovery path'
    with pytest.raises(FileNotFoundError):
        store.load_latest_result(snapshot.snapshot_hash)
    result = analyze_section(snapshot)
    store.save_result(snapshot, result)
    assert store.load_latest_result(snapshot.snapshot_hash)['resultHash'] == result['resultHash']
    from app.section.benchmarks import attach_comparisons
    later = attach_comparisons(snapshot, result, independent=False, mesh=False)
    store.save_result(snapshot, later)
    restored = router.latest_result(snapshot.snapshot_hash, store)
    assert restored['resultHash'] == later['resultHash']
    assert restored['snapshotHash'] == snapshot.snapshot_hash
