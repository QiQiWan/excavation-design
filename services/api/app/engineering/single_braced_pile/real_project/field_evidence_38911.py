"""Content-verified input register for Luoyu 3-3 field evidence.

Content hashes authenticate bytes inside the submitted ZIP, **not** an
engineer's signature, test laboratory, survey calibration or the truth of a
self-declared number. No automatic construction approval follows from upload.
The 2020 design-report geometry is kept separate from field measurements.
"""
from __future__ import annotations
import hashlib
import io
import json
import re
import stat
import zipfile
from math import isfinite
from pathlib import PurePosixPath

SCHEMA = 'pitguard.luoyu-3-3-field-evidence/3.89.11'
MAX_BYTES = 24 * 1024 * 1024
ROLES = ('site_geometry','rock_socket_tests','opposite_wall_response',
         'groundwater_investigation','fixed_pivot_slip_geometry')
ROLE_LABELS = {
  'site_geometry':'现场测量地形及周边荷载位置',
  'rock_socket_tests':'岩层嵌固与侧向/转动刚度试验或计算依据',
  'opposite_wall_response':'斜撑另一端墙体分阶段刚度与自由位移',
  'groundwater_investigation':'各阶段水位、承压含水层及帷幕地质水文依据',
  'fixed_pivot_slip_geometry':'最低支点圆弧软土条分几何与试验强度',
}


def finite(value, name, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value,(int,float)) or not isfinite(value):
        raise ValueError(f'{name} must be a finite numeric value')
    val = float(value)
    if lower is not None and val < lower or upper is not None and val > upper:
        raise ValueError(f'{name} outside method boundary')
    return val


def _safe_name(name: str) -> bool:
    if not isinstance(name,str) or '\\' in name or '\x00' in name:
        return False
    p=PurePosixPath(name)
    return bool(name and not name.startswith('/') and p.parts
                and all(x not in ('','.', '..') for x in p.parts)
                and str(p)==name and len(name)<=180)


def decode_evidence_zip(content: bytes|None) -> dict:
    """Validate *actual file bytes* and extract allow-listed JSON role sources."""
    if content is None:
        return {'schemaVersion':SCHEMA,'uploaded':False,'documents':{},'roles':{},
                'blockedRoles':list(ROLES),'status':'blocked_missing_field_evidence'}
    if not isinstance(content,bytes) or not 0<len(content)<=MAX_BYTES:
        raise ValueError('Evidence ZIP exceeds size limit or is empty')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            entries=z.infolist()
            if len(entries)>16:
                raise ValueError('Evidence ZIP contains too many entries')
            if len({x.filename for x in entries})!=len(entries):
                raise ValueError('Evidence ZIP contains duplicate filenames')
            for info in entries:
                if not _safe_name(info.filename) or info.is_dir():
                    raise ValueError('Evidence filenames must be safe files under documents/')
                if (info.external_attr >>16) & 0o170000 == stat.S_IFLNK:
                    raise ValueError('Symbolic links are forbidden in evidence ZIP')
                if info.file_size>MAX_BYTES or info.compress_size>MAX_BYTES:
                    raise ValueError('Evidence member exceeds bounded size')
            if sum(x.file_size for x in entries)>MAX_BYTES:
                raise ValueError('Evidence ZIP uncompressed bytes exceed bounded size')
            files={x.filename:z.read(x.filename) for x in entries}
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError('Evidence archive is invalid') from exc
    if 'case.json' not in files:
        raise ValueError('Evidence archive requires case.json')
    try:
        case=json.loads(files['case.json'].decode('utf-8'))
    except (UnicodeError,ValueError,TypeError) as exc:
        raise ValueError('Evidence case.json requires UTF-8 JSON') from exc
    if not isinstance(case,dict) or case.get('schemaVersion')!=SCHEMA:
        raise ValueError('Evidence schema version mismatch')
    from ..normative.reference_review import luoyu_three_three
    reference=luoyu_three_three()
    if (case.get('project')!=reference['project'] or case.get('section')!='3-3'
            or case.get('pileLengthM')!=6.6):
        raise ValueError('Evidence project, section or pile version mismatch (short pile excluded)')
    declarations=case.get('documents',[])
    if not isinstance(declarations,list) or len(declarations)>len(ROLES):
        raise ValueError('Evidence role declarations must be a bounded array')
    raw_attachments=case.get('attachments',[])
    if not isinstance(raw_attachments,list) or len(raw_attachments)>len(ROLES)*2:
        raise ValueError('Original source attachments must be a bounded array')
    roles={}; documents={}; names={'case.json'}
    for decl in declarations:
        if not isinstance(decl,dict) or set(decl)!={'role','path','sha256'}:
            raise ValueError('Evidence item needs only role, path and sha256')
        role,path,digest=decl['role'],decl['path'],decl['sha256']
        if role not in ROLES or role in roles or not _safe_name(path) or not path.startswith('documents/'):
            raise ValueError('Evidence role or path is invalid or repeated')
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
            raise ValueError('Evidence SHA-256 requires lowercase 64-character hex')
        if path not in files or path in names or not path.endswith('.json'):
            raise ValueError('Role must reference a unique present .json source file')
        names.add(path)
        raw=files[path]
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('Evidence SHA-256 does not match the file bytes: '+role)
        try: decoded=json.loads(raw.decode('utf-8'))
        except (ValueError,UnicodeError) as exc:raise ValueError('Role source is not UTF-8 JSON: '+role) from exc
        if not isinstance(decoded,dict) or decoded.get('role')!=role or decoded.get('project')!=reference['project'] or decoded.get('section')!='3-3':
            raise ValueError('Role source has wrong project / role / section identity: '+role)
        if decoded.get('sourceQuality') not in {'original_field_record','engineer_calculation','synthetic_test'}:
            raise ValueError('Source must declare original_field_record / engineer_calculation / synthetic_test')
        if not isinstance(decoded.get('data'),dict):
            raise ValueError('Source requires typed data object')
        roles[role]=decoded
        documents[role]={'path':path,'sha256':digest,'sizeBytes':len(raw),
           'declaredSourceQuality':decoded['sourceQuality'],
           'bytesAuthenticated':True,'engineeringAuthorityVerified':False}
    # Supporting drawings, surveys, laboratory sheets and hydro logs may be
    # attached as original bytes. Their hashes prove only ZIP consistency.
    # Keep these separate from interpreted JSON numbers and from signatures.
    allowed_suffixes={'.pdf','.csv','.json','.txt','.png','.jpg','.jpeg','.dwg','.dxf','.xlsx'}
    for item in raw_attachments:
        if not isinstance(item,dict) or set(item)!={'role','path','sha256'}:
            raise ValueError('Original attachment must declare role, path and SHA-256')
        role,path,digest=item['role'],item['path'],item['sha256']
        if role not in roles or not _safe_name(path) or not path.startswith('originals/'):
            raise ValueError('Original attachment role or path not registered')
        if (path not in files or path in names or PurePosixPath(path).suffix.lower() not in allowed_suffixes
                or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest)):
            raise ValueError('Original attachment filename/hash invalid or duplicate')
        payload=files[path]
        if hashlib.sha256(payload).hexdigest()!=digest:
            raise ValueError('Original attachment SHA-256 mismatches source bytes')
        names.add(path)
        documents[role].setdefault('originalByteAttachments',[]).append({
            'path':path,'sizeBytes':len(payload),'sha256':digest,
            'bytesAuthenticated':True,'engineeringAuthorshipVerified':False})
    for role in documents:
        documents[role]['originalByteAttachmentCount']=len(documents[role].get('originalByteAttachments',[]))
    if set(files)!=names:
        raise ValueError('Every evidence file must be registered, unregistered files forbidden')
    return {'schemaVersion':SCHEMA,'uploaded':True,'archiveSha256':hashlib.sha256(content).hexdigest(),
      'documents':documents,'roles':roles,'blockedRoles':[r for r in ROLES if r not in roles],
      'status':'self_declared_documents_hash_checked_engineering_authorship_unverified',
      'engineeringQualified':False}


def blank_case() -> dict:
    from ..normative.reference_review import luoyu_three_three
    r=luoyu_three_three()
    return {'schemaVersion':SCHEMA,'project':r['project'],'section':'3-3',
      'pileLengthM':6.6,'documents':[],'attachments':[],
      'requiredRoles':[{ 'role':r,'description':ROLE_LABELS[r]} for r in ROLES],
      'note':'Add source JSON under documents/, with matching project/section/role/sourceQuality/data; calculate SHA-256 of original bytes. Optional attachments must be under originals/ with role/path/sha256. The empty case is expected to be blocked.'}
