"""Full FastAPI route and actual original 3.87.27 numerical export integration."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.engineering.single_braced_pile.repository import Repository
from app.engineering.single_braced_pile.assurance.workflow import UnifiedAuditInput
from app.engineering.single_braced_pile.assurance.dossier import export_assurance_dossier
from app.engineering.single_braced_pile.assurance.dossier_verify import verify_review_package
from app.engineering.single_braced_pile.assurance.dossier_signature import (
    generate_keypair, sign_dossier, verify_detached_signature)
from app.engineering.single_braced_pile.real_project.native_numeric_import import make_blank_numeric_template
from test_38728_p1_p2_p3_api import water_request, observation,valid_zip


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('PITGUARD_API_KEYS', raising=False)
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    return TestClient(app)


def test_new_route_registration_and_schema(client):
    url='/api/engineering/one-strut/assurance'
    resp=client.get(url+'/schemas')
    assert resp.status_code==200,resp.text[:200]
    assert {'hydraulicFieldObservations','pumpingTestThiem','circularCrackCrosscheck','pileEulerReferenceScreen'}<=set(resp.json()['schemas'])
    template=client.get(url+'/luoyu-3-3/native-numeric/blank-template')
    assert template.status_code==200
    assert template.headers['content-type'].startswith('application/zip')
    assert template.content==make_blank_numeric_template()


def test_p1_observation_api(client):
    body=water_request([observation('inside',0,1),observation('outside',0,2)]).model_dump(mode='json')
    r=client.post('/api/engineering/one-strut/assurance/hydraulic/observations/validate',json=body)
    assert r.status_code==200,r.text[:300]
    assert r.json()['pairedCount']==1
    assert r.json()['formalDesignReady'] is False


def test_p2_second_order_api(client):
    r=client.post('/api/engineering/one-strut/assurance/circular/second-order/reference-screen',json={
        'project_id':'synthetic','section_id':'3-3', 'axial_compression_kn':100,
        'flexural_rigidity_kn_m2':1000,'effective_length_m':10,
        'effective_length_basis':'theoretical steel pile column', 'governing_stage_id':'S1'})
    assert r.status_code==200,r.text[:200]
    assert r.json()['formalDesignReady'] is False


def test_native_upload_api(client):
    blob=valid_zip(active_brace=True)
    r=client.post('/api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/validate',
                  files={'upload':('evidence.zip',blob,'application/zip')})
    assert r.status_code==200,r.text[:300]
    obj=r.json()
    assert obj['stationCounts']['S5']['brace']==1
    assert 'nativeReference' not in obj
    assert obj['originalSourceBytesIndependentlyAuthenticated'] is False


def test_native_compare_api(client):
    from app.engineering.single_braced_pile.real_project.native_numeric_import import import_native_numeric
    blob=valid_zip()
    cand={'stages':import_native_numeric(blob)['nativeReference']['stages']}
    r=client.post('/api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/compare',
                  files={'upload':('native.zip',blob,'application/zip')},
                  data={'candidate_json':json.dumps(cand)})
    assert r.status_code==200,r.text[:300]
    assert r.json()['assessmentStatus']=='all_channels_compared_source_peer_review_pending'
    assert r.json()['formalSameConditionVerification'] is False


def test_native_upload_malformed_error_is_422(client):
    r=client.post('/api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/validate',
                  files={'upload':('native.zip',b'xxx','application/zip')})
    assert r.status_code==422


def test_native_upload_limit_returns_413(client):
    r=client.post('/api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/validate',
                  files={'upload':('native.zip',b'x'*16_000_001,'application/zip')})
    assert r.status_code==413


@pytest.fixture
def real_generated_review(tmp_path):
    example_path=Path(__file__).resolve().parents[2] / 'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    example=json.loads(example_path.read_text(encoding='utf-8'))
    repository=Repository(root=tmp_path/'store')
    result=repository.compute(example)
    assert result['analysisStatus']=='complete'
    review=repository.approve_preliminary(result['resultHash'],reviewer='Integration checker',
        notes='This signed example is a synthetic regression case, not an engineering approval.',
        acknowledged_codes=[issue['code'] for issue in result['issues']])
    unified=UnifiedAuditInput.model_validate({
        'result_hash':result['resultHash'],
        'circular':{'project_id':result['projectId'],'section_id':result['sectionId'],
            'result_hash':result['resultHash'],'evidence':{'project_id':result['projectId'],
                 'section_id':result['sectionId'],'reference_version':'synthetic','records':[]}}})
    blob,manifest=export_assurance_dossier(unified,result,review)
    return blob,manifest,result


def test_actual_3_87_27_solver_to_dual_manifest_audit(real_generated_review):
    blob, manifest,result=real_generated_review
    assert len(manifest['files'])==17
    audit=verify_review_package(blob)
    assert audit['fileCount']==18
    assert audit['snapshotHash']==result['snapshotHash']
    assert audit['resultHash']==result['resultHash']
    assert audit['formalConstructionIssue'] is False
    assert audit['engineeringComplianceVerified'] is False


def test_real_export_http_integrity_endpoint(client,real_generated_review):
    blob,_,_=real_generated_review
    r=client.post('/api/engineering/one-strut/assurance/acceptance/verify-package',
                  files={'upload':('review.zip',blob,'application/zip')})
    assert r.status_code==200,r.text[:300]
    assert r.json()['status']=='integrity_verified_engineering_unqualified'
    assert r.json()['signatureVerified'] is False


def test_signed_actual_generated_review_with_separate_trusted_key(real_generated_review,tmp_path,monkeypatch):
    blob,_,result=real_generated_review
    archive=tmp_path/'review.zip';archive.write_bytes(blob)
    monkeypatch.setenv('PITGUARD_SIGNING_PASSWORD','synthetic-test-only-passphrase-123456')
    private=tmp_path/'private.pem';public=tmp_path/'public.pem'
    pair=generate_keypair(private,public)
    signed=sign_dossier(archive,private)
    assert signed['archiveSha256']==sha256(blob).hexdigest()
    verify=verify_detached_signature(archive,signed['signaturePath'],public)
    assert verify['signatureVerified'] is True
    assert verify['resultHash']==result['resultHash']
    assert verify['formalConstructionIssue'] is False
    assert verify['engineeringComplianceVerified'] is False


def test_auth_middleware_prohibits_unauthorized_new_upload(client,monkeypatch):
    monkeypatch.setenv('PITGUARD_API_KEYS',json.dumps({'admin-test-key':'admin'}))
    path='/api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/validate'
    rejected=client.post(path,files={'upload':('file.zip',valid_zip(),'application/zip')})
    assert rejected.status_code==401
    allowed=client.post(path,headers={'X-PitGuard-Key':'admin-test-key'},
                        files={'upload':('file.zip',valid_zip(),'application/zip')})
    assert allowed.status_code==200


def test_native_packing_cli_library_rehashes_and_checks(tmp_path):
    import importlib.util
    from io import BytesIO
    from zipfile import ZipFile
    path=Path(__file__).resolve().parents[2]/'scripts/pack-luoyu-native.py'
    spec=importlib.util.spec_from_file_location('native_packer_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'filled';source.mkdir()
    with ZipFile(BytesIO(valid_zip())) as z:
        for entry in z.namelist(): (source/entry).write_bytes(z.read(entry))
    metadata=json.loads((source/'metadata.json').read_text())
    metadata['fileDigests']={name:'0'*64 for name in metadata['fileDigests']}
    (source/'metadata.json').write_text(json.dumps(metadata))
    blob,audit=module.pack_native_directory(source)
    assert audit['stationCounts']['S1']['curve']==2
    assert audit['archiveSha256']==sha256(blob).hexdigest()


def test_native_packer_does_not_turn_empty_template_into_fake_results(tmp_path):
    import importlib.util
    from io import BytesIO
    from zipfile import ZipFile
    path=Path(__file__).resolve().parents[2]/'scripts/pack-luoyu-native.py'
    spec=importlib.util.spec_from_file_location('native_empty_packer_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'blank';source.mkdir()
    with ZipFile(BytesIO(make_blank_numeric_template())) as z:
        for entry in z.namelist(): (source/entry).write_bytes(z.read(entry))
    with pytest.raises(ValueError,match='All numeric channels'):
        module.pack_native_directory(source)
