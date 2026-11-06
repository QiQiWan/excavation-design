"""Server-side raw-byte SHA256 checks for source evidence.

A client SHA string alone is NOT evidence that the corresponding document was
read or authenticated. Only files inside the explicitly configured evidence
root may be checked. A hash match is file integrity, not professional approval.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import re


def verify_raw_source_file(*,source_root:Path,relative_path:str,expected_sha256:str,
                           maximum_size_bytes:int=400_000_000)->dict:
    if not re.fullmatch('[a-f0-9]{64}',expected_sha256):
        raise ValueError('Expected source SHA256 must be 64 hexadecimal characters')
    root=Path(source_root).resolve(strict=True)
    if not relative_path or '\\' in relative_path or Path(relative_path).is_absolute():
        raise ValueError('Source path must be relative and local')
    target=(root/relative_path).resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError('Source path must stay within the evidence root')
    if not target.is_file():
        return {'status':'source_file_missing','sourceName':relative_path,
                'hashMatched':False,'fileBytesActuallyRead':False}
    if target.stat().st_size>maximum_size_bytes:
        raise ValueError('Source file size exceeds maximum evidence limit')
    dig=sha256()
    with target.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            dig.update(chunk)
    observed=dig.hexdigest()
    return {'status':'byte_hash_confirmed' if observed==expected_sha256 else 'hash_mismatch',
            'sourceName':relative_path,'bytes':target.stat().st_size,
            'declaredSha256':expected_sha256,'actualSha256':observed,
            'hashMatched':observed==expected_sha256,'fileBytesActuallyRead':True,
            'doesNotCertifyGeotechnicalInterpretation':True}
