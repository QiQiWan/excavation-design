from copy import deepcopy
import csv
import json
import zipfile

import pytest

from app.section.examples import verification_example_v2
from app.section.models import freeze_section
from app.section.service import analyze_section
from app.section.reference_workflow import a3_reference_assets, make_reference_comparison, reference_supplement


@pytest.mark.filterwarnings('error:Glyph.*missing')
def test_v2_export_preserves_engineering_failures_full_pressure_states_and_reference_gaps(tmp_path):
    from app.section.export import build_minimum_package
    intake = a3_reference_assets()
    data = verification_example_v2()
    data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data); result = analyze_section(snapshot)
    comparison = make_reference_comparison(snapshot, result, intake['contract'], intake['curveTemplate'])
    supplement = reference_supplement(snapshot, result, intake['contract'], intake['curveTemplate'], comparison)
    original = deepcopy(result)
    path = build_minimum_package(snapshot, result, tmp_path, reference=supplement)
    assert result == original
    folder = path.with_suffix('')
    with zipfile.ZipFile(path) as archive:
        assert {'engineering_checks.csv', 'engineering_checks.json', 'soil_pressure_states.csv',
                'section_curves.json', 'stage_pressures.pdf', 'rebar_geometry.pdf',
                'rebar_geometry.dxf', 'reference_contract.json', 'reference_comparison.json',
                'reference_curves.json', 'reference_parameters.csv'} <= set(archive.namelist())
    manifest = json.loads((folder/'manifest.json').read_text())
    assert manifest['referenceSupplementHash'] == supplement['supplementHash']
    assert manifest['engineeringCheckStatus'] == 'engineering_fail'
    assert manifest['formalConstructionIssue'] is False
    assert manifest['font']['docxEmbedding']['sourceSha256'] == manifest['font']['sourceSha256']
    assert manifest['font']['docxEmbedding']['subsetted'] is False
    with (folder/'engineering_checks.csv').open(encoding='utf-8-sig') as handle:
        checks = {r['check_id']:r for r in csv.DictReader(handle)}
    assert checks['minimum_cover']['status'] == 'fail'
    assert checks['crack_width']['capacity'] == ''
    assert checks['crack_width']['status'] == 'outside_method_scope'
    assert all(r['result_hash'] == result['resultHash'] for r in checks.values())
    import ezdxf
    cage = ezdxf.readfile(folder/'rebar_geometry.dxf')
    assert len(cage.modelspace().query('CIRCLE[layer=="REBAR_LONGITUDINAL"]')) == 16
    assert max(c.dxf.radius for c in cage.modelspace().query('CIRCLE[layer=="PILE_OUTLINE"]')) == 350
    with zipfile.ZipFile(folder/'calculation.docx') as archive:
        from lxml import etree
        xml = etree.fromstring(archive.read('word/document.xml'))
        text = ''.join(xml.itertext())
    ns = {'m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    powers = xml.xpath('//m:sSup[m:e//m:t[contains(text(),"x²")]]/m:sup//m:t/text()', namespaces=ns)
    assert powers == ['5/2']
    assert '工程配筋分项检查' in text
    assert '42' in text and '634.406' in text
    assert 'Φ8@125' in text and '间距≤90' in text and '全长连续无搭接' in text
    assert '原项目条件与全曲线' in text
    assert '尚未取得原程序节点曲线' in text


def test_export_rejects_reference_from_a_different_result_before_writing(tmp_path):
    from app.section.export import build_minimum_package
    intake = a3_reference_assets(); data = verification_example_v2()
    data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot=freeze_section(data);result=analyze_section(snapshot)
    supplement=reference_supplement(snapshot,result,intake['contract'])
    supplement['resultHash']='f'*64
    with pytest.raises(ValueError):
        build_minimum_package(snapshot,result,tmp_path,reference=supplement)
    assert not list(tmp_path.iterdir())


def test_reference_document_uses_current_contract_values_without_hardcoded_a3_datum():
    from docx import Document
    from app.section.review_export import append_reference_document
    doc = Document()
    reference = {'contract': {'contractHash': 'a'*64, 'parameters': []},
                 'resultHash': 'b'*64, 'assessment': {'counts': {}, 'missingConditions': []}}
    append_reference_document(doc, reference, lambda *a: None, str)
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert '1.3 m' not in text
    assert '-0.8 m' not in text
