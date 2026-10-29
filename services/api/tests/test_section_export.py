"""Export boundary regressions: physics identity, measurable geometry and missing data."""
from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import json
from pathlib import Path
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import ezdxf
from lxml import etree
from pypdf import PdfReader
import pytest

from app.section.models import freeze_section, verification_example
from app.section.service import analyze_section


def _exporter():
    try:
        module = importlib.import_module('app.section.export')
    except ModuleNotFoundError:
        pytest.fail('The frozen section minimum package exporter is not implemented')
    return module.build_minimum_package


@pytest.fixture
def frozen_pair(monkeypatch):
    # Deployment must render on a host without any separately installed CJK font.
    monkeypatch.delenv('PITGUARD_CJK_FONT_PATH', raising=False)
    data = verification_example()
    data['section_id'] = 'VERIFY.PILE-01'
    data['reinforcement']['bar_count'] = None
    data['reinforcement']['bar_diameter_mm'] = None
    snapshot = freeze_section(data)
    return snapshot, analyze_section(snapshot)


def _unpacked(path: Path) -> Path:
    return path.with_suffix('')


@pytest.mark.filterwarnings('error:Glyph.*missing')
def test_package_retains_one_frozen_source_and_measurable_a3_geometry(frozen_pair, tmp_path):
    """Catches replacing true pile geometry/scale, standard-design mixups and invented bars."""
    snapshot, result = frozen_pair
    original = copy.deepcopy(result)
    package = _exporter()(snapshot, result, tmp_path)
    root = _unpacked(package)
    assert package.is_file() and root.is_dir()
    assert result == original
    with zipfile.ZipFile(package) as archive:
        assert {'calculation.docx', 'section_A3.pdf', 'section.dxf', 'envelopes_A3.pdf',
            'envelopes.csv', 'reinforcement.csv', 'manifest.json', 'frozen_section.json',
            'section_result.json', 'SHA256SUMS.txt'} <= set(archive.namelist())
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['snapshotHash'] == snapshot.snapshot_hash
    assert manifest['resultHash'] == result['resultHash']
    with (root / 'envelopes.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    point = max(result['envelope']['points'], key=lambda p: abs(p['mMaxKnM']))
    row = next(r for r in rows if r['basis'] == 'characteristic' and float(r['depth_m']) == point['depthM'] and r['side'] == point['side'])
    assert float(row['moment_max_kn_m']) == point['mMaxKnM']
    assert row['moment_max_stage_id'] == point['mMaxStageId']
    assert row['result_hash'] == result['resultHash']
    design = next(r for r in rows if r['basis'] == 'design' and float(r['depth_m']) == point['depthM'] and r['side'] == point['side'])
    assert float(design['moment_max_kn_m']) == point['mdMaxKnM']
    assert design['displacement_max_mm'] == ''  # displacement is not factored design data
    with (root / 'stage_results.csv').open(encoding='utf-8-sig', newline='') as handle:
        stage_rows = list(csv.DictReader(handle))
    assert {row['stage_id'] for row in stage_rows} == {'S0', 'S1', 'S2', 'S3', 'S4', 'S5'}
    assert {row['side'] for row in stage_rows} == {p['side'] for s in result['stages'] for p in s['points']}
    with (root / 'reinforcement.csv').open(encoding='utf-8-sig', newline='') as handle:
        rebar = next(csv.DictReader(handle))
    assert rebar['provided_area_mm2'] == '' and rebar['status'] == 'missing_provided_reinforcement'
    doc = ezdxf.readfile(root / 'section.dxf')
    assert doc.header['$INSUNITS'] == 4
    pile = list(doc.modelspace().query('LWPOLYLINE[layer=="SV_PILE"]'))[0]
    xy = list(pile.get_points('xy'))
    assert max(p[0] for p in xy) - min(p[0] for p in xy) == pytest.approx(700.0)
    assert max(p[1] for p in xy) - min(p[1] for p in xy) == pytest.approx(10000.0)
    assert any(abs(float(d.get_measurement())) == pytest.approx(10000.0) for d in doc.modelspace().query('DIMENSION'))
    layout = doc.layouts.get('A3-SECTION')
    viewport = list(layout.query('VIEWPORT[layer=="SV_VIEWPORT"]'))[0]
    assert viewport.dxf.view_height / viewport.dxf.height == pytest.approx(100.0)
    assert any('1:100' in entity.dxf.text for entity in layout.query('TEXT'))
    page = PdfReader(root / 'section_A3.pdf').pages[0]
    assert float(page.mediabox.width) == pytest.approx(420 / 25.4 * 72, abs=0.02)
    assert float(page.mediabox.height) == pytest.approx(297 / 25.4 * 72, abs=0.02)
    pdf_text = page.extract_text()
    assert '剖面验证' in pdf_text
    assert '完整哈希和来源见' in pdf_text
    assert '工程结论待复核' in pdf_text
    with zipfile.ZipFile(root / 'calculation.docx') as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
        styles = etree.fromstring(archive.read('word/styles.xml'))
    namespaces = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    assert len(xml.findall('.//m:oMath', namespaces)) >= 5
    assert xml.find('.//m:f', namespaces) is not None
    assert not styles.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr')
    assert '30,000,000' in ''.join(xml.itertext())  # integer-valued E must not lose trailing zeroes
    for line in (root / 'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_tampered_result_is_rejected_before_any_artifact(frozen_pair, tmp_path):
    snapshot, result = frozen_pair
    result['stages'][-1]['points'][1]['momentCharacteristicKnM'] = 9999.0
    with pytest.raises(ValueError, match='[Hh]ash|哈希'):
        _exporter()(snapshot, result, tmp_path)
    assert not list(tmp_path.iterdir())


def test_failed_analysis_exports_failure_evidence_without_curves_or_rebar(frozen_pair, tmp_path):
    data = verification_example()
    for index, stage in enumerate(data['stages'][1:], 1):
        stage['excavation_depth_m'] = 9.0 if index <= 2 else 9.5
        stage['water_inside_depth_m'] = stage['excavation_depth_m'] + .5
    snapshot = freeze_section(data)
    result = analyze_section(snapshot)
    assert result['status'] == 'failed'
    package = _exporter()(snapshot, result, tmp_path)
    root = _unpacked(package)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['analysisStatus'] == 'failed'
    assert manifest['curvesGenerated'] is False
    with (root / 'reinforcement.csv').open(encoding='utf-8-sig', newline='') as handle:
        assert list(csv.DictReader(handle)) == []
    assert not (root / 'envelopes_A3.pdf').exists()


def test_long_pile_has_actual_dimensions_and_one_paper_space_ratio(tmp_path):
    from app.section.render_helper import build_scene, export_dxf
    data = verification_example()
    data['pile']['length_m'] = 80.0
    data['soil_layers'][-1]['bottom_m'] = 80.0
    result = {'snapshotHash': 'a'*64, 'resultHash': 'b'*64}
    scene = build_scene(data, result)
    export_dxf(data, result, scene, tmp_path/'long.dxf')
    drawing = ezdxf.readfile(tmp_path/'long.dxf')
    layout = drawing.layouts.get('A3-SECTION')
    viewport = list(layout.query('VIEWPORT[layer=="SV_VIEWPORT"]'))[0]
    assert viewport.dxf.view_height/viewport.dxf.height == pytest.approx(500.0)
    assert any('1:500' in entity.dxf.text for entity in layout.query('TEXT'))
    assert any(abs(float(d.get_measurement())) == pytest.approx(80000.0) for d in drawing.modelspace().query('DIMENSION'))


def test_simultaneous_downloads_keep_distinct_complete_packages(frozen_pair, tmp_path, monkeypatch):
    from app.section import service
    snapshot, result = frozen_pair
    original_validate = service.validate_result
    ready = Barrier(2)

    def validate_together(snapshot, result):
        original_validate(snapshot, result)
        ready.wait(timeout=20)

    monkeypatch.setattr(service, 'validate_result', validate_together)
    export = _exporter()
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = [workers.submit(export, snapshot, result, tmp_path) for _ in range(2)]
        packages = [future.result(timeout=30) for future in pending]
    assert len(set(packages)) == 2
    for package in packages:
        with zipfile.ZipFile(package) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['resultHash'] == result['resultHash']
        assert (_unpacked(package)/'manifest.json').is_file()


def test_report_includes_worked_hand_checks_of_the_actual_frozen_section(frozen_pair):
    from docx import Document
    from app.section.benchmarks import run_hand_checks, same_snapshot_hand_checks
    from app.section.export import _comparison_details

    snapshot, result = frozen_pair
    same = same_snapshot_hand_checks(snapshot, result)
    hand = run_hand_checks()
    hand['sameSnapshot'] = same
    comparisons = {'hand': hand}
    original = copy.deepcopy(comparisons)
    document = Document()
    _comparison_details(document, comparisons)
    text = ''.join(document._element.itertext())
    assert '同一冻结剖面手工核对' in text
    assert 'F = 10 kN' in text
    assert same['snapshotHash'][:16] in text and same['discreteInputHash'][:16] in text
    assert len(same['checks']) == 6
    assert all(row['name'] in text for row in same['checks'])
    assert '坑内' in text and '坑外' in text and '分段有效应力' in text
    for row in same['soilPressureWorkedRows']:
        # Quadrature depths need enough displayed precision for a reader to
        # reproduce the printed gamma-prime times segment-length products.
        assert f"{row['depthM']:.8f}".rstrip('0').rstrip('.') in text
        for segment in row['verticalStressSegments']:
            assert f"{segment['effectiveUnitWeightKnM3']:.5g}" in text
            assert f"{segment['contributionKpa']:.5g}" in text
        for key in ('sigmaKpa', 'p0Kpa', 'activeKpa', 'passiveKpa'):
            assert f"{row['expected'][key]:.5g}" in text
    assert '全阶段平衡' in text and 'comparison.csv' in text
    assert comparisons == original
