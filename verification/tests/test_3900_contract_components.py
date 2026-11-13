"""计算合同的分块哈希：不一致时必须指出是哪个输入块变了。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import calculation_assurance as ca


class _Case:
    id = 'case-1'
    stages = []

    def model_dump(self, **kwargs):
        return {'id': self.id, 'stages': []}


def _project():
    return SimpleNamespace(
        id='p-1', excavation=SimpleNamespace(segments=[]),
        retaining_system=None, calculation_cases=[_Case()],
    )


def _stub(monkeypatch, payload):
    monkeypatch.setattr(ca, 'calculation_input_payload', lambda project, case: payload)
    monkeypatch.setattr(ca, 'geometry_consistency_summary', lambda project: {})
    monkeypatch.setattr(ca, 'support_topology_hash', lambda project: None)
    monkeypatch.setattr(ca, '_solver_runtime_manifest', lambda: {'python': '3.13'})


def test_contract_records_one_hash_per_input_block(monkeypatch):
    _stub(monkeypatch, {'designSettings': {'surcharge': 20}, 'strata': [{'id': 'S1'}]})
    contract = ca.build_calculation_contract(_project(), _Case())

    assert set(contract['inputComponentHashes']) == {'designSettings', 'strata'}
    assert contract['inputComponentHashes']['designSettings'] == ca._canonical_hash(
        {'surcharge': 20})


def test_verify_names_the_changed_input_block(monkeypatch):
    _stub(monkeypatch, {'designSettings': {'surcharge': 20}, 'strata': [{'id': 'S1'}]})
    project = _project()
    contract = ca.build_calculation_contract(project, _Case())
    result = SimpleNamespace(
        id='calc-1', case_id='case-1',
        calculation_assurance={'contract': contract},
        adopted_design_snapshot_hash=contract['inputSnapshotHash'],
        calculation_contract_id='calc-contract-demo',
        support_topology_hash=None,
        input_snapshot_hash=contract['inputSnapshotHash'],
    )

    # 只改荷载，不动地层：合同应指出 designSettings 变了。
    _stub(monkeypatch, {'designSettings': {'surcharge': 25}, 'strata': [{'id': 'S1'}]})
    out = ca.verify_current_calculation_contract(project, result)

    assert out['inputComponentMismatches'] == ['designSettings']
    assert '输入快照变化字段：designSettings' in out['reason']


def test_verify_reports_no_changed_block_when_inputs_match(monkeypatch):
    payload = {'designSettings': {'surcharge': 20}, 'strata': [{'id': 'S1'}]}
    _stub(monkeypatch, payload)
    project = _project()
    contract = ca.build_calculation_contract(project, _Case())
    result = SimpleNamespace(
        id='calc-1', case_id='case-1',
        calculation_assurance={'contract': contract},
        adopted_design_snapshot_hash=contract['inputSnapshotHash'],
        calculation_contract_id='calc-contract-demo',
        support_topology_hash=None,
        input_snapshot_hash=contract['inputSnapshotHash'],
    )

    out = ca.verify_current_calculation_contract(project, result)
    assert out['inputComponentMismatches'] == []


def test_derived_geology_geometry_is_excluded_from_the_hash():
    # 真实回归：计算进程持有完整 surfaces，保存后工作区只留 previews，
    # 两者曾让合同每次算完就「变旧」。
    full = {'surfaces': [{'id': 's1'}], 'surface_previews': [],
            'volumes': [{'id': 'v1'}], 'vtu_mesh': {'points': 1},
            'coverage_audit': {'status': 'pass'}}
    preview = {'surfaces': [], 'surface_previews': [{'id': 's1'}],
               'volumes': [], 'vtu_mesh': None,
               'coverage_audit': {'status': 'pass'}}
    stripped = ca._strip_derived_geology(full)
    assert stripped == {'coverage_audit': {'status': 'pass'}}
    assert ca._canonical_hash(stripped) == ca._canonical_hash(ca._strip_derived_geology(preview))
