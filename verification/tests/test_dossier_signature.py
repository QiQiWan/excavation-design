"""Deterministic adversarial tests for the PitGuard 3.87.28 P4 detached signature overlay.

They exercise archive contracts without pretending that a synthetic test case is
an original engineering design or a formal issue-ready calculation.
"""
from __future__ import annotations

import importlib.util
import json
import os
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'services/api/app/engineering/single_braced_pile/assurance/dossier_signature.py'
spec = importlib.util.spec_from_file_location('dossier_signature_test_module', PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

# The synthetic fixture reproduces the real immutable snapshot -> result ->
# review canonical SHA256 chain. No arbitrary identity strings are trusted.
def js(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')


def checksum(blob):
    return sha256(blob).hexdigest()

SNAPSHOT={'project_id':'synthetic','section_id':'3-3'}
RESULT={'snapshot':SNAPSHOT,'snapshotHash':checksum(js(SNAPSHOT)),
        'analysisStatus':'complete','projectId':'synthetic','sectionId':'3-3'}
RESULT_ID=checksum(js(RESULT))
REVIEW={'resultHash':RESULT_ID,'formalConstructionIssue':False,
        'scope':'preliminary_calculation_review_only','reviewer':'synthetic-checker'}
IDENTITY={'snapshotHash':RESULT['snapshotHash'],
          'resultHash':RESULT_ID,'reviewHash':checksum(js(REVIEW))}
PHASE_RECORD={'phase':'P1','resultHash':RESULT_ID,'snapshotHash':RESULT['snapshotHash'],
              'formalEngineeringApproval':False}
PHASE_HASH=checksum(js(PHASE_RECORD))


def js(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')


def checksum(blob):
    return sha256(blob).hexdigest()


def entries_manifest(items):
    return [{'name': name, 'bytes': len(blob), 'sha256': checksum(blob)}
            for name, blob in sorted(items.items())]


def make_bundle(tmp_path, *, edit_gate=None, edit_outer=None, edit_nested=None,
                edit_reviews=None, extra=None, corrupt_zip=False):
    path = tmp_path / 'P1_P4_test_work_copy.zip'
    original = {'calculation.docx': b'minimal synthetic review only',
                'frozen_input.json': js(SNAPSHOT),
                'sealed_result.json': js({**RESULT, 'resultHash': IDENTITY['resultHash']}),
                'review_record.json': js({**REVIEW, 'reviewHash': IDENTITY['reviewHash']})}
    nested = {'schemaVersion': 'pitguard.one-strut-delivery/1', **IDENTITY,
              'formalIssueReady': False, 'files': entries_manifest(original)}
    if edit_nested:
        edit_nested(nested)
    nested_bytes = js(nested)
    phases = [{'phase': p, 'name': p, 'assessmentStatus': 'unqualified',
               'outstanding': ['工程资料待校审'], 'releaseApproved': False}
              for p in ('P1', 'P2', 'P3', 'P4')]
    gate = {'schemaVersion': 'pitguard.engineering-P1-P4-gates/1', **IDENTITY,
            'formalConstructionIssue': False, 'phases': phases,
            'phaseReviewHashes': {'P1': PHASE_HASH}}
    if edit_gate:
        edit_gate(gate)
    reviews = [{**PHASE_RECORD, 'phaseReviewHash': PHASE_HASH}]
    if edit_reviews:
        edit_reviews(reviews)
    outer_files = {**original, 'original_export_manifest.json': nested_bytes,
                   'P1_P4_gate_index.json': js(gate), 'P1_P4_phase_reviews.json': js(reviews)}
    if extra:
        outer_files.update(extra)
    outer = {'schemaVersion': 'pitguard.P1-P4-review-bundle/1', **IDENTITY,
             'formalConstructionIssue': False,
             'previousManifestSha256': checksum(nested_bytes),
             'files': entries_manifest(outer_files)}
    if edit_outer:
        edit_outer(outer)
    outer_files['manifest.json'] = js(outer)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for name, value in sorted(outer_files.items()):
            z.writestr(name, value)
    if corrupt_zip:
        path.write_bytes(path.read_bytes()[:30])
    return path


@pytest.fixture
def keypair(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_SIGNING_PASSWORD', 'strong-internal-test-passphrase-2026')
    private, public = tmp_path / 'reviewer_private.pem', tmp_path / 'reviewer_public.pem'
    info = mod.generate_keypair(private, public)
    assert info['keyId'] and private.exists() and public.exists()
    return private, public


def test_valid_dual_manifest_archive_still_unqualified(tmp_path):
    archive = make_bundle(tmp_path)
    result = mod.audit_dossier(archive)
    assert result['fileCount'] == 8
    assert result['archiveSha256'] == checksum(archive.read_bytes())
    assert result['sourceFilesIndependentlyAuthenticated'] is False
    assert result['engineeringComplianceVerified'] is False
    assert result['formalConstructionIssue'] is False
    assert [x['phase'] for x in result['phases']] == ['P1', 'P2', 'P3', 'P4']


def test_keygen_encrypted_and_no_overwrite(keypair):
    private, public = keypair
    contents = private.read_bytes()
    assert b'ENCRYPTED PRIVATE KEY' in contents
    assert b'BEGIN PUBLIC KEY' in public.read_bytes()
    with pytest.raises(mod.DossierValidationError):
        mod.generate_keypair(private, public)
    assert contents == private.read_bytes()


def test_missing_password_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('PITGUARD_SIGNING_PASSWORD', raising=False)
    with pytest.raises(mod.DossierValidationError):
        mod.generate_keypair(tmp_path / 'a.pem', tmp_path / 'b.pem')


def test_signed_dossier_verifies_without_engineering_promotion(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    private, public = keypair
    record = mod.sign_dossier(archive, private)
    assert record['formalConstructionIssue'] is False
    outcome = mod.verify_detached_signature(archive, record['signaturePath'], public)
    assert outcome['signatureVerified'] is True
    assert outcome['trustedKeyId'] == record['keyId']
    assert outcome['status'] == 'archive_and_trusted_signer_verified_working_copy_only'
    assert outcome['formalConstructionIssue'] is False
    assert outcome['engineeringComplianceVerified'] is False
    assert outcome['signatureTimeIndependentlyAttested'] is False


def test_signing_cannot_overwrite_signature(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    first = mod.sign_dossier(archive, keypair[0])
    original = Path(first['signaturePath']).read_bytes()
    with pytest.raises(FileExistsError):
        mod.sign_dossier(archive, keypair[0])
    assert Path(first['signaturePath']).read_bytes() == original


def test_wrong_trusted_key_is_rejected(tmp_path, keypair, monkeypatch):
    archive = make_bundle(tmp_path)
    record = mod.sign_dossier(archive, keypair[0])
    other_priv, other_pub = tmp_path / 'other_private.pem', tmp_path / 'other_public.pem'
    mod.generate_keypair(other_priv, other_pub)
    with pytest.raises(mod.DossierValidationError, match='key identity'):
        mod.verify_detached_signature(archive, record['signaturePath'], other_pub)


def test_signed_record_tamper_is_rejected(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    record = mod.sign_dossier(archive, keypair[0])
    sign_path = Path(record['signaturePath'])
    x = json.loads(sign_path.read_text())
    x['signedAtUtc'] = '2026-11-07T12:00:00+00:00'
    sign_path.write_text(json.dumps(x))
    with pytest.raises(mod.DossierValidationError, match='signature is invalid'):
        mod.verify_detached_signature(archive, sign_path, keypair[1])


def test_signed_record_untrusted_extra_public_key_rejected(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    record = mod.sign_dossier(archive, keypair[0])
    path = Path(record['signaturePath'])
    data = json.loads(path.read_text())
    data['publicKeyPem'] = keypair[1].read_text()
    path.write_text(json.dumps(data))
    with pytest.raises(mod.DossierValidationError, match='exactly'):
        mod.verify_detached_signature(archive, path, keypair[1])


def test_archive_byte_mutation_detected(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    record = mod.sign_dossier(archive, keypair[0])
    with archive.open('ab') as out:
        out.write(b'junk')
    with pytest.raises(mod.DossierValidationError, match='archiveSha256|archiveBytes'):
        mod.verify_detached_signature(archive, record['signaturePath'], keypair[1])


@pytest.mark.parametrize('mutator,matching', [
    (lambda d: d.update(formalConstructionIssue=True), 'formal construction'),
    (lambda d: d.update(resultHash='0' * 64), 'identity'),
    (lambda d: d['files'].pop(), 'Outer manifest mismatch'),
    (lambda d: d['files'].append(dict(d['files'][0])), 'Duplicate'),
    (lambda d: d.update(previousManifestSha256='0' * 64), 'Original export manifest digest'),
])
def test_outer_manifest_adversarial_cases(tmp_path, mutator, matching):
    archive = make_bundle(tmp_path, edit_outer=mutator)
    with pytest.raises(mod.DossierValidationError, match=matching):
        mod.audit_dossier(archive)


@pytest.mark.parametrize('mutator,matching', [
    (lambda d: d.update(formalConstructionIssue=True), 'formal construction'),
    (lambda d: d.update(resultHash='0' * 64), 'identity'),
    (lambda d: d['phases'].pop(), 'P1-P4 exactly once'),
    (lambda d: d['phases'][0].update(releaseApproved=True), 'must not claim'),
])
def test_gate_index_adversarial_cases(tmp_path, mutator, matching):
    archive = make_bundle(tmp_path, edit_gate=mutator)
    with pytest.raises(mod.DossierValidationError, match=matching):
        mod.audit_dossier(archive)


def test_phase_review_wrong_result_hash_rejected(tmp_path):
    archive = make_bundle(tmp_path, edit_reviews=lambda r: r[0].update(resultHash='f' * 64))
    with pytest.raises(mod.DossierValidationError, match='another calculation'):
        mod.audit_dossier(archive)


def test_phase_review_formal_approval_rejected(tmp_path):
    archive = make_bundle(tmp_path, edit_reviews=lambda r: r[0].update(formalEngineeringApproval=True))
    with pytest.raises(mod.DossierValidationError, match='formal engineering'):
        mod.audit_dossier(archive)


def test_original_manifest_false_hash_rejected(tmp_path):
    def alter(m):
        m['files'][0]['sha256'] = '0' * 64
    archive = make_bundle(tmp_path, edit_nested=alter)
    with pytest.raises(mod.DossierValidationError, match='Original export file'):
        mod.audit_dossier(archive)


def test_original_manifest_cannot_approve_formal_issue(tmp_path):
    archive = make_bundle(tmp_path, edit_nested=lambda d: d.update(formalIssueReady=True))
    with pytest.raises(mod.DossierValidationError, match='Original export cannot'):
        mod.audit_dossier(archive)


def test_unlisted_extra_file_rejected(tmp_path):
    path = make_bundle(tmp_path)
    with ZipFile(path, 'a') as z:
        z.writestr('new_undocumented.pdf', b'unlisted')
    with pytest.raises(mod.DossierValidationError, match='Outer manifest mismatch'):
        mod.audit_dossier(path)


def test_zip_traversal_rejected(tmp_path):
    archive = make_bundle(tmp_path, extra={'../stolen.txt': b'oops'})
    with pytest.raises(mod.DossierValidationError, match='Unsafe'):
        mod.audit_dossier(archive)


def test_duplicate_zip_entries_rejected(tmp_path):
    archive = make_bundle(tmp_path)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with ZipFile(archive, 'a') as z:
            z.writestr('calculation.docx', b'wrong duplicate')
    with pytest.raises(mod.DossierValidationError, match='Duplicate ZIP entry'):
        mod.audit_dossier(archive)


def test_malformed_zip_rejected(tmp_path):
    archive = make_bundle(tmp_path, corrupt_zip=True)
    with pytest.raises(mod.DossierValidationError, match='Unreadable ZIP'):
        mod.audit_dossier(archive)


def test_reject_oversized_claimed_manifest_size(tmp_path):
    def alter(d):
        d['files'][0]['bytes'] = 500_000_000
    archive = make_bundle(tmp_path, edit_outer=alter)
    with pytest.raises(mod.DossierValidationError, match='Invalid file byte count'):
        mod.audit_dossier(archive)


def test_duplicate_json_object_key_rejected(tmp_path):
    path = make_bundle(tmp_path)
    # Replacing the manifest without updating the ZIP index will leave an outer
    # manifest containing duplicate top-level key names.
    with ZipFile(path, 'r') as z:
        data = {n: z.read(n) for n in z.namelist()}
    data['manifest.json'] = data['manifest.json'].replace(b'"resultHash":', b'"resultHash":"0", "resultHash":', 1)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for n, raw in data.items():
            z.writestr(n, raw)
    with pytest.raises(mod.DossierValidationError, match='Duplicate JSON key'):
        mod.audit_dossier(path)


def test_malformed_signature_base64_rejected(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    signed = mod.sign_dossier(archive, keypair[0])
    path = Path(signed['signaturePath'])
    sig = json.loads(path.read_text())
    sig['signatureBase64'] = '***invalid***'
    path.write_text(json.dumps(sig))
    with pytest.raises(mod.DossierValidationError, match='signature is invalid'):
        mod.verify_detached_signature(archive, path, keypair[1])


def test_signature_is_not_self_sufficient_without_trusted_key(tmp_path, keypair):
    archive = make_bundle(tmp_path)
    record = mod.sign_dossier(archive, keypair[0])
    with pytest.raises(mod.DossierValidationError, match='local regular files'):
        mod.verify_detached_signature(archive, record['signaturePath'], tmp_path / 'missing_public_key.pem')


def _rebuild_self_consistent_manifests(path, change):
    """Recompute *both* manifests to test cross-checks beyond self-certified hashes."""
    with ZipFile(path,'r') as z:
        files={name:z.read(name) for name in z.namelist()}
    change(files)
    original=json.loads(files['original_export_manifest.json'])
    original_members={row['name'] for row in original['files']}
    original['files']=entries_manifest({k:files[k] for k in original_members})
    files['original_export_manifest.json']=js(original)
    outer=json.loads(files['manifest.json'])
    outer['previousManifestSha256']=checksum(files['original_export_manifest.json'])
    outer['files']=entries_manifest({k:v for k,v in files.items() if k!='manifest.json'})
    files['manifest.json']=js(outer)
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        for k,v in sorted(files.items()):z.writestr(k,v)


def test_rehashed_dual_manifests_cannot_override_sealed_result_canonical_hash(tmp_path):
    path=make_bundle(tmp_path)
    def alter(files):
        data=json.loads(files['sealed_result.json']);data['analysisStatus']='fake_completed'
        files['sealed_result.json']=js(data)
    _rebuild_self_consistent_manifests(path,alter)
    with pytest.raises(mod.DossierValidationError,match='Sealed result canonical identity'):
        mod.audit_dossier(path)


def test_rehashed_manifests_cannot_override_snapshot_hash(tmp_path):
    path=make_bundle(tmp_path)
    _rebuild_self_consistent_manifests(path,lambda files:files.update({'frozen_input.json':js({'evil':'fake'})}))
    with pytest.raises(mod.DossierValidationError,match='Frozen input canonical'):
        mod.audit_dossier(path)


def test_rehashed_manifests_cannot_override_preliminary_review_hash(tmp_path):
    path=make_bundle(tmp_path)
    def alter(files):
        data=json.loads(files['review_record.json']);data['reviewer']='unsigned replacement'
        files['review_record.json']=js(data)
    _rebuild_self_consistent_manifests(path,alter)
    with pytest.raises(mod.DossierValidationError,match='Preliminary review canonical'):
        mod.audit_dossier(path)


def test_rehashed_manifests_cannot_remove_phase_review_record(tmp_path):
    path=make_bundle(tmp_path)
    _rebuild_self_consistent_manifests(path,lambda files:files.update({'P1_P4_phase_reviews.json':js([])}))
    with pytest.raises(mod.DossierValidationError,match='must match records exactly'):
        mod.audit_dossier(path)


def test_rehashed_manifests_cannot_override_phase_record_hash(tmp_path):
    path=make_bundle(tmp_path)
    def alter(files):
        data=json.loads(files['P1_P4_phase_reviews.json']);data[0]['reviewer']='unsigned update'
        files['P1_P4_phase_reviews.json']=js(data)
    _rebuild_self_consistent_manifests(path,alter)
    with pytest.raises(mod.DossierValidationError,match='Phase review canonical hash'):
        mod.audit_dossier(path)
