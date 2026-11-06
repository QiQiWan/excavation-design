#!/usr/bin/env python3
"""Verify all source bytes from a sealed release manifest (not an authenticity signature)."""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json,sys
ROOT=Path(__file__).resolve().parents[1]


def verify():
    manifest=json.loads((ROOT/'RELEASE_SOURCE_MANIFEST.json').read_text(encoding='utf8'))
    if manifest.get('version')!='3.88.0' or manifest.get('formalConstructionIssue') is not False:
        raise ValueError('wrong release identity or formal issue claim')
    exempt={'RELEASE_SOURCE_MANIFEST.json','SOURCE_SHA256SUMS.txt'}
    expected={row['path']:row for row in manifest['files']}
    if len(expected)!=len(manifest['files']):raise ValueError('duplicate manifest path')
    actual={str(p.relative_to(ROOT)).replace('\\','/') for p in ROOT.rglob('*')
            if p.is_file() and not any(part in {'__pycache__','.pytest_cache','node_modules','dist','.venv','.git','verification-output'} for part in p.relative_to(ROOT).parts)
            and p.suffix not in ('.pyc','.pyo') and str(p.relative_to(ROOT)).replace('\\','/') not in exempt}
    if set(expected)!=actual:raise ValueError(f'manifest closure mismatch: missing={sorted(set(expected)-actual)[:5]}, extra={sorted(actual-set(expected))[:5]}')
    for rel,row in expected.items():
        p=ROOT/rel
        raw=p.read_bytes()
        if len(raw)!=row['bytes'] or sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError('file integrity mismatch: '+rel)
    if manifest['sourceFileCount']!=len(expected):raise ValueError('file count mismatch')
    print(f"[PitGuard] V3.88.0 source closure verified: {len(expected)} files")
    return len(expected)


if __name__=='__main__':
    try:verify()
    except (ValueError,KeyError,FileNotFoundError) as exc:
        print('[PitGuard] SOURCE INTEGRITY FAILURE:',exc,file=sys.stderr)
        raise SystemExit(1)
