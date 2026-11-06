"""HTTP-safe bounded adapter for the independent P4 two-manifest ZIP auditor."""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from .dossier_signature import audit_dossier

MAX_HTTP_DOSSIER_BYTES = 50_000_000


def verify_review_package(content: bytes) -> dict:
    if not isinstance(content,bytes) or not content or len(content)>MAX_HTTP_DOSSIER_BYTES:
        raise ValueError('P1-P4 ZIP must be non-empty and at most 50 MB in HTTP intake')
    with TemporaryDirectory(prefix='pitguard_p4_verification_') as td:
        source = Path(td)/'received_work_copy.zip'
        source.write_bytes(content)
        audit = audit_dossier(source)
    # The name is transport-dependent and not a document identity.
    return {**audit, 'schemaVersion':'pitguard.review-archive-integrity/2',
            'status':'integrity_verified_engineering_unqualified',
            'signatureVerified':False,'independentSourceBytesAuthentication':False,
            'engineeringComplianceVerified':False,'formalConstructionIssue':False,
            'nextActions':['Verify the detached signature against a separately trusted public key offline',
                           'Complete P1-P3 engineering checks and independent designer/checker/approver review']}
