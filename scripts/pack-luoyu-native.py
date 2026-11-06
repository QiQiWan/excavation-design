#!/usr/bin/env python3
"""Create a strictly hashed Luoyu native ZIP from a filled template directory.

This tool verifies byte consistency only and does not claim FRWS authenticity.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/api'))
from app.engineering.single_braced_pile.real_project.native_numeric_import import (
    DATA_FILES, import_native_numeric)


def pack_native_directory(source:Path)->tuple[bytes,dict]:
    source=Path(source)
    if not source.is_dir():raise ValueError('Provide the directory containing the five native data files')
    content={}
    for name in (*DATA_FILES,'metadata.json'):
        file=source/name
        if not file.is_file() or file.is_symlink():
            raise ValueError('Missing or symlinked native data file: '+name)
        if file.stat().st_size>25_000_000:
            raise ValueError('Native input file exceeds 25 MB: '+name)
        content[name]=file.read_bytes()
    metadata=json.loads(content['metadata.json'].decode('utf-8-sig'))
    if not isinstance(metadata,dict):raise ValueError('metadata.json must be a JSON object')
    metadata['fileDigests']={name:sha256(content[name]).hexdigest() for name in DATA_FILES}
    content['metadata.json']=(json.dumps(metadata,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    stream=BytesIO()
    with ZipFile(stream,'w',ZIP_DEFLATED,compresslevel=8) as out:
        for name,data in sorted(content.items()):out.writestr(name,data)
    blob=stream.getvalue()
    audit=import_native_numeric(blob)
    audit.pop('nativeReference')
    return blob,audit


def main():
    parser=argparse.ArgumentParser(description='Pack and validate native Luoyu S1-S5 numeric data')
    parser.add_argument('--source',required=True,help='Unzipped and filled native template directory')
    parser.add_argument('--output',required=True,help='New ZIP output path; existing files are never replaced')
    args=parser.parse_args()
    try:
        target=Path(args.output)
        if target.exists():raise ValueError('Output already exists; use a new name to retain source history')
        blob,audit=pack_native_directory(Path(args.source))
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as out:out.write(blob)
    except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'blocked','error':str(exc),'formalSameConditionVerification':False},ensure_ascii=False),file=sys.stderr)
        return 2
    print(json.dumps({'status':'numeric_zip_created_requires_independent_review',
        'file':str(target),'bytes':len(blob),'archiveSha256':audit['archiveSha256'],
        'stationCounts':audit['stationCounts'],'formalSameConditionVerification':False},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
